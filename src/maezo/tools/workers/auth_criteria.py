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
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import structlog
import yaml

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


def _manifest_default_path() -> str:
    """`<spec>/processes/dmn/auth-criteria-ratification.yaml` via the single T0.3 mechanism."""
    return str(resolve_spec_dir() / "processes" / "dmn" / "auth-criteria-ratification.yaml")


@dataclass(frozen=True)
class CriteriaSources:
    """Immutable, already-validated view of the ratification manifest.

    Attributes:
        ratified: decision ids whose rule source a human has ratified (all three manifest
            fields present and valid). EMPTY when the manifest is absent/unusable — the
            fail-closed default.
        dut_criteria_by_ref: `dut_ref` TOKEN -> `dut_criteria_*` decision id. Routing plumbing
            declared by a human, never guessed in code (see the manifest's own comment).
    """

    ratified: frozenset[str]
    dut_criteria_by_ref: Mapping[str, str]

    def is_ratified(self, decision_id: str) -> bool:
        """True iff `decision_id`'s rule source is human-ratified. FAIL-CLOSED on anything else."""
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


def _parse(raw_text: str, manifest_path: Path) -> CriteriaSources:
    """Parse an already-read manifest into `CriteriaSources`. NEVER raises; refuses instead."""
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return _refuse("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")

    if not isinstance(data, dict):
        return _refuse(
            "invalid_schema",
            f"{manifest_path}: root must be a mapping, got {type(data).__name__}",
        )

    # Template guard, mirrored from `load_retention_matrix` (legal_bases_matrix.py): a copy of
    # the manifest marked `unratified: true` is refused WHOLESALE, so pointing the path at a
    # placeholder by mistake also fails closed instead of half-loading.
    if data.get("unratified") is True:
        return _refuse(
            "unratified_template",
            f"{manifest_path} is marked 'unratified: true' (placeholder template) — a "
            "human-ratified manifest must replace it; treating every source as NOT ratified",
        )

    raw_fontes = data.get("fontes")
    if raw_fontes is None:
        raw_fontes = {}
    if not isinstance(raw_fontes, dict):
        return _refuse("invalid_schema", f"{manifest_path}: 'fontes' must be a mapping")

    ratified = frozenset(
        decision_id
        for decision_id, entry in raw_fontes.items()
        if isinstance(decision_id, str) and _entry_is_ratified(decision_id, entry)
    )

    raw_map = data.get("mapeamento_dut_criteria")
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, dict):
        return _refuse(
            "invalid_schema", f"{manifest_path}: 'mapeamento_dut_criteria' must be a mapping"
        )
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
    )
    return CriteriaSources(ratified=ratified, dut_criteria_by_ref=MappingProxyType(dut_map))


def load_criteria_sources(path: str | Path | None = None) -> CriteriaSources:
    """Load the ratification manifest. FAILS CLOSED — never raises, never defaults to ratified.

    Path resolution: the explicit `path` argument > `MAEZO_AUTH_CRITERIA_MANIFEST_PATH` >
    `<spec>/processes/dmn/auth-criteria-ratification.yaml` (via `resolve_spec_dir`).

    Every failure mode (unresolvable spec dir, missing file, non-file path, unreadable,
    non-UTF-8, malformed YAML, wrong schema, `unratified: true` template marker) returns
    `CriteriaSources(frozenset(), {})` — nothing ratified, nothing mapped — plus one `error`
    log line. See the module docstring for why this swallows where the retention-matrix
    precedent raises.
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
