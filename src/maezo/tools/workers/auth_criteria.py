"""Ratification gate for the SP-OP-AUTH-001 auto-approval criteria (GAP-AUTH-4).

THE PROBLEM THIS SOLVES. `operadora.auth.validate_auto_criteria` decides three of its four
criteria (tecnico / regulatorio / contratual) from DMN tables whose clinical, regulatory and
contractual content is SYNTHETIC and DRAFT (`docs/review-queue.md`, "Phase 1 — DUT/ROL/Carencia
DMN set"; every one of those tables says `status: DRAFT — conteudo SINTETICO` in its own
`<description>`). Wiring them naively would let synthetic rules grant real authorizations —
strictly WORSE than the unverified start-payload seeds they replace, because it would *look*
validated.

THE RULE. A criterion may contribute a PASS only if its rule source is RATIFIED in
`spec/processes/dmn/auth-criteria-ratification.yaml`. A DRAFT / unlisted / malformed source
yields `false` with a `*_FONTE_NAO_RATIFICADA` token, regardless of what the table computes.
Ratifying a table is a pure DATA change — flip three fields in the manifest; no Python changes,
no new deploy of this module.

THE SECOND RULE (M-3). The manifest's `criterios_nao_cobertos:` section declares FACTS the
automatic route nominally required and that NO criterion checks today — each naming, in
`bloqueia_ratificacao_de`, the source whose ratification it FORBIDS. That declaration is
ENFORCED HERE: a source named by any uncovered criterion can never count as ratified, even with
`ratificado: true` + `revisor` + `ratificado_em` all filled in. Without this, an SME doing
exactly the data-change activation the design promises (flipping `auth_criteria_contratual`)
would open automatic approval for an OUT-OF-NETWORK provider with no criterion objecting —
the same "absence of validation reads as an implicit PASS" pathology GAP-AUTH-4 closed, merely
deferred. The suppression is not a hardcoded constant: removing the `criterios_nao_cobertos`
entry (a reviewed edit to a CODEOWNERS-gated file, made once the missing source exists) lifts
it with no code change, so "ratificar = mudanca de DADOS" still holds in both directions.

PRECEDENT MIRRORED. `maezo.platform.lifecycle.legal_bases_matrix.load_retention_matrix` — the
DPO retention-matrix loader that REFUSES its own `unratified: true` placeholder template rather
than "succeeding" on placeholder data. Same posture here (including the `unratified: true` root
guard), with ONE deliberate divergence in the failure MODE:

    the retention loader RAISES; this loader SWALLOWS to "nothing is ratified".

That divergence is the same one `tools/workers/ceilings.py` already documents for
`CeilingResolver` versus the PEP factory: *"a PEP that cannot gate must not boot, but a worker
that cannot resolve a ceiling must still route safely to human review"*. A raise here would
become an external-task incident, and an incident STALLS a care-authorization request —
strictly worse for the beneficiary than routing it to a human auditor. Every failure mode
(absent file, unreadable, non-UTF-8, malformed YAML, wrong schema, template marker) therefore
resolves to an EMPTY `CriteriaSources`: zero ratified sources, zero dut_ref mappings, one
`error`-level structured log line. Nothing here can ever *open* an automatic approval; it can
only close one.

NOTHING IS RATIFIED TODAY. The shipped manifest lists all six sources with `ratificado: false`.
That is not a placeholder — it is the honest recorded state (`docs/review-queue.md`).
"""

from __future__ import annotations

import os
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import structlog
import yaml
from yaml.constructor import ConstructorError as _YamlConstructorError

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Narrow env override for the manifest path, retained as a test-pinning hook and as an
#: operator escape hatch. When unset the default resolves through the single T0.3 mechanism
#: (`resolve_spec_dir`) — the SAME resolution `ceilings.py` uses for the autonomy matrix, never
#: a second scheme.
MANIFEST_PATH_ENV = "MAEZO_AUTH_CRITERIA_MANIFEST_PATH"

#: Fields a `fontes:` entry must ALL carry for the source to count as ratified. `ratificado`
#: must be the boolean literal `True` (never truthy junk — the repo's fail-closed pin idiom,
#: mirroring `human_approved is True`); `revisor`/`ratificado_em` must be non-blank strings so a
#: ratification is an ACCOUNTABLE act (ADR-0007: who, and when), not an anonymous flag flip.
_RATIFIED_FLAG = "ratificado"
_RATIFIED_BY = "revisor"
_RATIFIED_AT = "ratificado_em"

#: The manifest section declaring facts NO criterion verifies today (`rede_credenciada` is the
#: shipped one), and the per-entry field naming the source each of them BLOCKS. Both names are
#: the manifest's own — this loader enforces the declaration the SME already reads there.
_UNCOVERED_SECTION = "criterios_nao_cobertos"
_BLOCKS_RATIFICATION_OF = "bloqueia_ratificacao_de"

#: The COMPLETE, closed set of top-level keys `_parse` understands (verified against the shipped
#: manifest: `version`, `fontes`, `mapeamento_dut_criteria`, `criterios_nao_cobertos` — nothing
#: else). GK-criteria minor-1: without this allowlist, a mistyped section name such as
#: `criterios_nao_cobertoss:` is not "an unknown key" to PyYAML at all — it is simply absent
#: under its correct name, so `data.get(_UNCOVERED_SECTION)` returns `None` and the whole
#: `criterios_nao_cobertos` block evaporates with NO error, exactly as if it had been
#: deliberately (and correctly) removed. Any key outside this set now refuses the manifest
#: WHOLESALE — the same `_refuse(...)` path an unknown `bloqueia_ratificacao_de` target already
#: takes — instead of the typo silently reading as "nothing to enforce here".
_KNOWN_TOP_LEVEL_KEYS = frozenset({"version", "fontes", "mapeamento_dut_criteria", _UNCOVERED_SECTION})


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` that RAISES on a duplicate mapping key instead of silently last-wins.

    GK-criteria minor-1's second half: stock PyYAML resolves a duplicate key (at ANY mapping
    depth — a second top-level `criterios_nao_cobertos:`, or a repeated decision id inside
    `fontes:`) by silently keeping the LAST occurrence and discarding every earlier one, with no
    warning. For this manifest that is fail-OPEN: a duplicated `criterios_nao_cobertos:` can
    make the M-3 suppression block vanish exactly as completely as a typo'd key does, and
    nothing above would ever see it happen.

    This loader closes that by raising `yaml.constructor.ConstructorError` (a `yaml.YAMLError`
    subclass) the moment a duplicate key is constructed. `_parse`'s existing
    `except yaml.YAMLError` catches it and refuses the manifest wholesale — the identical
    fail-closed outcome as any other malformed-YAML case, no new except clause required.

    SCOPED TO THIS LOADER ONLY: no other `yaml.safe_load`/`yaml.load` call in the repository is
    affected; this class is never registered as a replacement for `yaml.SafeLoader` generally.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise _YamlConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _manifest_default_path() -> str:
    """`<spec>/processes/dmn/auth-criteria-ratification.yaml` via the single T0.3 mechanism."""
    return str(resolve_spec_dir() / "processes" / "dmn" / "auth-criteria-ratification.yaml")


@dataclass(frozen=True)
class CriteriaSources:
    """Immutable, already-validated view of the ratification manifest.

    Attributes:
        ratified: decision ids whose manifest entry CLAIMS a complete, accountable ratification
            (all three fields present and valid). EMPTY when the manifest is absent/unusable —
            the fail-closed default. This is the raw claim, deliberately preserved even when
            suppressed: `is_ratified` is the authority, and an auditor is better served by
            "the SME did flip it AND we suppressed it" than by a silently dropped entry.
        dut_criteria_by_ref: `dut_ref` TOKEN -> `dut_criteria_*` decision id. Routing plumbing
            declared by a human, never guessed in code (see the manifest's own comment).
        blocked_by_uncovered: decision id -> the `criterios_nao_cobertos` entries that FORBID
            ratifying it. Populated from the manifest's own declaration; empty by default so a
            hand-built instance is never accidentally MORE permissive than a loaded one.
    """

    ratified: frozenset[str]
    dut_criteria_by_ref: Mapping[str, str]
    blocked_by_uncovered: Mapping[str, tuple[str, ...]] = MappingProxyType({})

    def is_ratified(self, decision_id: str) -> bool:
        """True iff `decision_id`'s rule source is human-ratified. FAIL-CLOSED on anything else.

        THE SINGLE ENFORCEMENT POINT for both rules (module docstring). A source named by an
        uncovered criterion is refused BEFORE the ratification claim is even consulted: no
        combination of manifest fields can make it pass while the block stands. Deliberately the
        only place this is checked — filtering `ratified` at parse time as well would make each
        layer individually mutable-without-effect, which is exactly the sort of redundancy that
        hides a neutralised gate.
        """
        if decision_id in self.blocked_by_uncovered:
            return False
        return decision_id in self.ratified

    def criteria_table_for(self, dut_ref: Any) -> str | None:
        """The `dut_criteria_*` decision id declared for `dut_ref`, or None (=> fail closed).

        `dut_rol_coverage` emits refs like ``"DUT-BARIATRICA-001 DRAFT/verify"``; the manifest is
        keyed by the leading TOKEN (`"DUT-BARIATRICA-001"`). A non-string, blank, or unmapped ref
        returns None, which the validator turns into `TECNICO_PROCEDIMENTO_NAO_MAPEADO`.
        """
        if not isinstance(dut_ref, str):
            return None
        token = dut_ref.strip().split(" ", 1)[0] if dut_ref.strip() else ""
        if not token:
            return None
        return self.dut_criteria_by_ref.get(token)


#: The fail-closed value every failure mode resolves to: nothing ratified, nothing mapped.
_EMPTY_SOURCES = CriteriaSources(ratified=frozenset(), dut_criteria_by_ref=MappingProxyType({}))


def _refuse(reason: str, detail: str) -> CriteriaSources:
    """Log the refusal and return the empty (fail-closed) sources. NEVER raises."""
    logger.error("auth_criteria_manifest_unavailable", reason=reason, detail=detail)
    return _EMPTY_SOURCES


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entry_is_ratified(decision_id: str, entry: Any) -> bool:
    """True iff `entry` carries a complete, accountable ratification. FAIL-CLOSED otherwise.

    Requires ALL THREE of: `ratificado is True` (the boolean literal — a string `"true"`, `1`,
    or any other truthy value is REFUSED), a non-blank `revisor`, and a non-blank
    `ratificado_em`. A partial ratification (flag flipped, reviewer left null) is not a
    ratification; it reads as an unfinished edit and must not open an automatic approval.
    """
    if not isinstance(entry, dict):
        logger.warning("auth_criteria_entry_malformed", decision_id=decision_id)
        return False
    if entry.get(_RATIFIED_FLAG) is not True:
        return False
    missing = [f for f in (_RATIFIED_BY, _RATIFIED_AT) if not _is_nonblank_str(entry.get(f))]
    if missing:
        logger.warning(
            "auth_criteria_ratification_incomplete",
            decision_id=decision_id,
            missing_fields=missing,
            detail="ratificado=true but the accountability fields are absent/blank — NOT ratified",
        )
        return False
    return True


def _blocked_sources(raw: Any, declared: frozenset[str]) -> Mapping[str, tuple[str, ...]] | None:
    """`criterios_nao_cobertos` -> {blocked decision id: (uncovered criterion names, ...)}.

    Returns None to mean "unusable — refuse the WHOLE manifest", never a partial reading. An
    entry this loader cannot understand is indistinguishable from an entry someone deleted, and
    those two must not resolve the same way: deletion is the reviewed act that LIFTS a block,
    so a malformed declaration has to fail toward suppression instead (`_EMPTY_SOURCES`).

    Every entry must therefore be a mapping naming, in `bloqueia_ratificacao_de`, one source (or
    a list of sources) that `fontes` actually declares. Three specific fail-closed choices:

      - a MISSING/blank `bloqueia_ratificacao_de` is refused rather than read as "blocks
        nothing": an uncovered criterion that names no source is precisely the inert declaration
        this enforcement exists to prevent.
      - an UNKNOWN target (a typo like `auth_criteria_contratuall`) is refused rather than
        ignored, because ignoring it would silently evaporate the block — fail-open by typo.

    WHAT THIS FUNCTION DOES NOT GUARD (GK-criteria minor-1, closed one level up instead): a typo
    in the SECTION'S OWN NAME (`criterios_nao_cobertoss:`) or a DUPLICATE `criterios_nao_cobertos:`
    key never reach this function at all — `raw` would simply be absent/`None`, indistinguishable
    from the section never having been written. Both are refused wholesale by `_parse` BEFORE it
    calls this function: an unknown key via `_KNOWN_TOP_LEVEL_KEYS`, a duplicate via the
    duplicate-key-raising `_DuplicateKeySafeLoader` (both scoped to this loader only). This
    function only ever sees a single, ALREADY-recognized `criterios_nao_cobertos` mapping — or
    its legitimate absence:

      - the section being ABSENT/null is fine and means "nothing is blocked": that IS the
        documented, still-UNguarded resolution path (remove the entry once the missing source
        exists — deliberately distinct from it vanishing BY ACCIDENT, which is what minor-1
        closed). The entry's continued PRESENCE in the shipped manifest is pinned separately by
        `tests/unit/sec/test_auto_approval_criteria_fence.py`.
    """
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, dict):
        return None

    blocked: dict[str, list[str]] = {}
    for name, entry in raw.items():
        if not _is_nonblank_str(name) or not isinstance(entry, dict):
            return None
        declared_target = entry.get(_BLOCKS_RATIFICATION_OF)
        targets = [declared_target] if _is_nonblank_str(declared_target) else declared_target
        if not isinstance(targets, list) or not targets:
            return None
        for target in targets:
            if not _is_nonblank_str(target) or target.strip() not in declared:
                return None
            blocked.setdefault(target.strip(), []).append(str(name).strip())
    return MappingProxyType({key: tuple(names) for key, names in blocked.items()})


def _parse(raw_text: str, manifest_path: Path) -> CriteriaSources:
    """Parse an already-read manifest into `CriteriaSources`. NEVER raises; refuses instead."""
    try:
        data = yaml.load(raw_text, Loader=_DuplicateKeySafeLoader)
    except yaml.YAMLError as exc:
        # Catches BOTH stock malformed YAML and `_DuplicateKeySafeLoader`'s duplicate-key raise
        # (GK-criteria minor-1) — a `yaml.constructor.ConstructorError` is a `yaml.YAMLError`.
        return _refuse("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")

    if not isinstance(data, dict):
        return _refuse(
            "invalid_schema",
            f"{manifest_path}: root must be a mapping, got {type(data).__name__}",
        )

    # Template guard, mirrored from `load_retention_matrix` (legal_bases_matrix.py): a copy of
    # the manifest marked `unratified: true` is refused WHOLESALE, so pointing the path at a
    # placeholder by mistake also fails closed instead of half-loading. Checked BEFORE the
    # unknown-top-level-key guard below so this specific, documented placeholder marker keeps
    # its own precise refusal reason rather than being masked by the generic one.
    if data.get("unratified") is True:
        return _refuse(
            "unratified_template",
            f"{manifest_path} is marked 'unratified: true' (placeholder template) — a "
            "human-ratified manifest must replace it; treating every source as NOT ratified",
        )

    # GK-criteria minor-1: a top-level key outside the closed set — a mistyped section name
    # (`criterios_nao_cobertoss:`) chief among them — is refused WHOLESALE rather than silently
    # ignored, for the same reason an unknown `bloqueia_ratificacao_de` target is refused below:
    # a declaration this loader cannot recognise is indistinguishable from one that was never
    # written, and the two must not resolve the same way.
    unknown_keys = sorted(str(k) for k in data if k not in _KNOWN_TOP_LEVEL_KEYS)
    if unknown_keys:
        return _refuse(
            "unknown_top_level_key",
            f"{manifest_path}: unrecognized top-level key(s) {unknown_keys} — refusing the "
            f"whole manifest (known keys: {sorted(_KNOWN_TOP_LEVEL_KEYS)}) rather than risk a "
            "mistyped or unexpected section silently vanishing every block it declares",
        )

    raw_fontes = data.get("fontes")
    if raw_fontes is None:
        raw_fontes = {}
    if not isinstance(raw_fontes, dict):
        return _refuse("invalid_schema", f"{manifest_path}: 'fontes' must be a mapping")

    blocked = _blocked_sources(
        data.get(_UNCOVERED_SECTION), frozenset(k for k in raw_fontes if isinstance(k, str))
    )
    if blocked is None:
        return _refuse(
            "uncovered_criteria_malformed",
            f"{manifest_path}: '{_UNCOVERED_SECTION}' is unusable — every entry must be a "
            f"mapping naming, in '{_BLOCKS_RATIFICATION_OF}', one or more sources declared "
            "under 'fontes'. A declaration this loader cannot read is treated as a block it "
            "cannot honour, so the manifest is refused wholesale (nothing ratified) rather "
            "than silently unblocking a source",
        )

    ratified = frozenset(
        decision_id
        for decision_id, entry in raw_fontes.items()
        if isinstance(decision_id, str) and _entry_is_ratified(decision_id, entry)
    )

    # TELEMETRY for the suppression (no PHI — every field here is a static config identifier).
    # Logged at ERROR because this exact state is the M-3 hazard caught in the act: a human
    # completed a ratification while the fact it depends on remains unverified by any criterion.
    # The per-request half of the evidence needs nothing extra: `is_ratified` returns False, so
    # `_gate_on_ratification` already records the would-be verdict as a `*_SOMBRA_*` token
    # alongside `*_FONTE_NAO_RATIFICADA`, exactly as for any other unratified source.
    for decision_id in sorted(d for d in ratified if d in blocked):
        logger.error(
            "auth_criteria_ratification_suppressed",
            decision_id=decision_id,
            criterios_nao_cobertos=list(blocked[decision_id]),
            detail=(
                "ratification suppressed by uncovered criterion — the manifest declares this "
                "source blocked, so it cannot contribute a PASS however complete its "
                "'ratificado/revisor/ratificado_em' fields are; supply the missing source and "
                f"remove the '{_UNCOVERED_SECTION}' entry to lift the block"
            ),
        )

    raw_map = data.get("mapeamento_dut_criteria")
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, dict):
        return _refuse("invalid_schema", f"{manifest_path}: 'mapeamento_dut_criteria' must be a mapping")
    dut_map = {
        str(ref).strip(): str(table).strip()
        for ref, table in raw_map.items()
        if _is_nonblank_str(ref) and _is_nonblank_str(table)
    }

    logger.info(
        "auth_criteria_manifest_loaded",
        path=str(manifest_path),
        ratified_count=len(ratified),
        ratified=sorted(ratified),
        dut_criteria_mappings=len(dut_map),
        blocked_by_uncovered=sorted(blocked),
    )
    return CriteriaSources(
        ratified=ratified,
        dut_criteria_by_ref=MappingProxyType(dut_map),
        blocked_by_uncovered=blocked,
    )


def load_criteria_sources(path: str | Path | None = None) -> CriteriaSources:
    """Load the ratification manifest. FAILS CLOSED — never raises, never defaults to ratified.

    Path resolution: the explicit `path` argument > `MAEZO_AUTH_CRITERIA_MANIFEST_PATH` >
    `<spec>/processes/dmn/auth-criteria-ratification.yaml` (via `resolve_spec_dir`).

    Every failure mode (unresolvable spec dir, missing file, non-file path, unreadable,
    non-UTF-8, malformed YAML, wrong schema, `unratified: true` template marker, an unreadable
    `criterios_nao_cobertos` declaration) returns `CriteriaSources(frozenset(), {})` — nothing
    ratified, nothing mapped — plus one `error` log line. See the module docstring for why this
    swallows where the retention-matrix precedent raises.
    """
    raw_path = path if path is not None else os.environ.get(MANIFEST_PATH_ENV)
    if not raw_path:
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable spec/ ratifies nothing
            return _refuse("path_unresolved", f"could not resolve the default manifest path: {exc}")

    manifest_path = Path(raw_path)
    if not manifest_path.is_file():
        return _refuse(
            "file_not_found", f"no readable manifest file at {manifest_path} — nothing is ratified"
        )

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # NOT covered by OSError (UnicodeDecodeError subclasses ValueError) — the same explicit
        # catch legal_bases_matrix.py documents for cp1252 files with accented characters.
        return _refuse("invalid_encoding", f"{manifest_path} is not valid UTF-8: {exc}")
    except OSError as exc:
        return _refuse("unreadable", f"could not read {manifest_path}: {exc}")

    return _parse(raw_text, manifest_path)


@lru_cache(maxsize=8)
def _load_cached(resolved_path: str | None) -> CriteriaSources:
    return load_criteria_sources(resolved_path)


def criteria_sources(path: str | Path | None = None) -> CriteriaSources:
    """Cached `load_criteria_sources` — the accessor workers use on the task hot path.

    The manifest is static governance config, so caching it avoids re-reading YAML on every
    external task (same reasoning, and the same restart-to-refresh trade-off, as
    `ceilings._load_matrix_cached`). A ratification therefore takes effect on the next daemon
    restart — acceptable for an artifact whose whole point is a deliberate human act, and
    strictly fail-closed in the interim.
    """
    return _load_cached(str(path) if path is not None else None)
