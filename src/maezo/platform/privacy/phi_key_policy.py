"""Ratification gate for the PHI-in-business-keys remediation flag (DL-0043 leg (c)).

THE PROBLEM THIS FRAMES (it does not, by itself, solve it). `matricula_beneficiario` is declared
in `PHI_PROCESS_VARS` (`maezo.tools.workers.phi_vars`) — the house already classifies it as Zona
PHI — and is nonetheless minted RAW into two business keys when a plan has no contract number
(individual/familiar plans, i.e. exactly the natural-person population):

    CANCEL-{tenant}-{numero_contrato OR matricula_beneficiario}   (workers/inadimplencia.py)
    INAD-{tenant}-{numero_contrato OR matricula_beneficiario}     (agents/fernando/graph.py)

That key then egresses unredacted: structlog lines, an engine process variable, the Kafka payload
`_business_key`, the Kafka MESSAGE KEY, the notifications-mirror allowlist, and the durable
Postgres start-dedup key. The house's own correct precedent is `PROG-...-{beneficiario_pseudo_id}`
(agents/valentina/graph.py).

WHY A FLAG AND NOT A FIX. Changing the identity format changes what is written into DURABLE
stores (engine business keys, the dedup table, Kafka partitioning, Cockpit search). That is an
owner decision with an LGPD leg, an operations leg, and a migration leg — none of which
engineering can ratify on the owner's behalf. So this loader exists to make the decision a DATA
change (flip four fields in the manifest; no Python change, no redeploy) and to keep the default
INERT until that act happens.

THE RULE. The declared `modo` takes effect ONLY when the manifest is fully ratified:
`status: RATIFICADO` AND `ratificacao.ratificado is True` AND non-blank `revisor` AND non-blank
`ratificado_em`. Anything else — DRAFT, a partial ratification, a missing file, malformed YAML,
an `unratified: true` template marker, an unknown mode token, a DUPLICATED top-level key —
resolves to `PhiKeyMode.OFF`, which is today's behavior byte-for-byte. A DRAFT manifest declaring
`modo: pseudo_keys` is therefore `off`; the loader never promotes a draft.

PRECEDENT MIRRORED. `maezo.tools.workers.auth_criteria.load_criteria_sources` (itself mirroring
`maezo.platform.lifecycle.legal_bases_matrix.load_retention_matrix`): same `unratified: true`
root guard, same "all accountability fields or nothing" rule, same SWALLOW-don't-raise failure
mode. Swallowing is the right posture here for the same reason it is there — a raise would turn a
governance-config problem into a worker incident, and an incident STALLS a beneficiary's case.
Here the argument is even simpler: the fail-closed direction for this flag is "do nothing", and
doing nothing is exactly what `off` means.

NOTHING IS RATIFIED TODAY. The shipped manifest is `status: DRAFT`, `modo: off`,
`ratificacao.ratificado: false` — the honest recorded state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

#: Narrow env override for the manifest path — a test-pinning hook and an operator escape hatch,
#: mirroring `auth_criteria.MANIFEST_PATH_ENV`. Unset resolves through the single T0.3 mechanism.
PHI_KEY_POLICY_PATH_ENV = "MAEZO_PHI_KEY_POLICY_PATH"

#: `status:` literal that opens the gate. Compared case-insensitively after strip, but nothing
#: other than this token counts — an empty/absent/unknown status is DRAFT by default.
_RATIFIED_STATUS = "RATIFICADO"

_RATIFICATION_BLOCK = "ratificacao"
_RATIFIED_FLAG = "ratificado"
_RATIFIED_BY = "revisor"
_RATIFIED_AT = "ratificado_em"


class PhiKeyMode(StrEnum):
    """The three declarable remediation modes. `OFF` is the only one reachable without a human.

    OFF
        Today's behavior, byte-identical. No scrubber installed, no key format change, no Kafka
        message-key change. The ONLY thing that runs is the content-free shadow counter.
    SCRUB_ONLY
        Closes the egress paths WITHOUT touching any persisted identity (no migration): structlog
        scrubbing of key-bearing field names + `matricula_beneficiario`; `_business_key` dropped
        from the notifications-mirror allowlist; Kafka MESSAGE KEY pseudonymized for the
        CANCEL/INAD families only (a documented partitioning change — see the manifest).
    PSEUDO_KEYS
        SCRUB_ONLY plus: new mints use `beneficiario_pseudo_id` (the valentina precedent) instead
        of `matricula_beneficiario`.

        THE DUAL-READ IS PARTIAL, AND NAMING IT PRECISELY IS THE POINT. Exactly ONE consumer
        dual-reads today: the CANCEL anti-dupla-terminacao correlation guard
        (`inadimplencia._query_ja_em_rescisao_cancel` via `base.contract_business_key_forms`).
        The START side does NOT: `transport.start_process_idempotent` takes a SINGLE business key
        for both `find_active_instance` and `start_dedup_key`, so an instance live under the
        legacy matricula form would be invisible to a start keyed on the pseudo form — a double
        start of SP-OP-INADIMPLENCIA-001 / SP-OP-CANCEL-001, plus a second audit row because the
        dedup key moved with the mint. `beneficiario_pseudo_id` is also absent from
        `inadimplencia._HANDOFF_CARRY_KEYS`, so the handoff payload does not even carry the new
        anchor forward. These are recorded as UNMET pre-requisites in the manifest's
        `pre_requisitos_pseudo_keys` block, and `MODE IS NOT RATIFIABLE` until they are closed.
        Ratifying this mode as if the dual-read were universal is the failure this docstring
        exists to prevent.
    """

    OFF = "off"
    SCRUB_ONLY = "scrub_only"
    PSEUDO_KEYS = "pseudo_keys"


@dataclass(frozen=True)
class PhiKeyPolicy:
    """Immutable, already-resolved view of the remediation manifest.

    Attributes:
        modo: the EFFECTIVE mode — what the code must actually do. Never anything but `OFF`
            unless the manifest is fully ratified.
        declarado: the mode the manifest DECLARES. Differs from `modo` exactly when a DRAFT (or
            otherwise unratified) manifest declares something other than `off`; kept so the
            shadow telemetry and the ops log can say "someone staged pseudo_keys but nobody
            ratified it" instead of silently reporting `off`.
        ratificado: whether the manifest carried a complete, accountable ratification.
        motivo: bounded token explaining why `modo` was demoted to `OFF` (empty when it was not).
            Bounded by construction — safe on a log line, never carries file content.
    """

    modo: PhiKeyMode
    declarado: PhiKeyMode
    ratificado: bool
    motivo: str = ""

    @property
    def scrubbing_enabled(self) -> bool:
        """True iff the log/mirror/Kafka-key scrubbing of `scrub_only` is in force."""
        return self.modo in (PhiKeyMode.SCRUB_ONLY, PhiKeyMode.PSEUDO_KEYS)

    @property
    def pseudo_keys_enabled(self) -> bool:
        """True iff new mints must use `beneficiario_pseudo_id` and guards must dual-read."""
        return self.modo is PhiKeyMode.PSEUDO_KEYS


#: The value EVERY failure mode and every unratified manifest resolves to.
_OFF_POLICY = PhiKeyPolicy(modo=PhiKeyMode.OFF, declarado=PhiKeyMode.OFF, ratificado=False)


def _manifest_default_path() -> str:
    """`<spec>/policies/privacy/phi-business-key-remediation.yaml` via the T0.3 mechanism."""
    from maezo.agents import resolve_spec_dir  # noqa: PLC0415 — lazy: avoids an import cycle

    return str(resolve_spec_dir() / "policies" / "privacy" / "phi-business-key-remediation.yaml")


def _off(reason: str, detail: str, *, declarado: PhiKeyMode = PhiKeyMode.OFF) -> PhiKeyPolicy:
    """Log the demotion and return an OFF policy. NEVER raises."""
    logger.warning("phi_key_policy_inactive", reason=reason, detail=detail, declarado=declarado.value)
    return PhiKeyPolicy(modo=PhiKeyMode.OFF, declarado=declarado, ratificado=False, motivo=reason)


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_mode(raw: Any) -> PhiKeyMode | None:
    """Parse a `modo:` token. Returns None for anything that is not one of the three literals.

    THE YAML 1.1 TRAP, handled explicitly: PyYAML resolves a BARE `off` to the boolean `False`
    (and `on`/`yes`/`no` likewise). So `modo: off` — the most natural way for a human to write the
    default, and the way this file's own documentation reads — never arrives here as the string
    `"off"`. Mapping `False` to `OFF` is therefore not leniency, it is the correct reading of the
    document; refusing it would make the shipped manifest look malformed and bury a real problem
    under a warning nobody would trust. `True` (i.e. a bare `on`) is NOT a mode and is refused —
    which fails to `OFF` anyway, so the asymmetry costs nothing.

    The shipped artifact quotes the value (`modo: "off"`) so the intent is unambiguous to a human
    reader too; this branch exists so an operator who unquotes it does not silently change meaning.
    """
    if raw is False:
        return PhiKeyMode.OFF
    if not isinstance(raw, str):
        return None
    try:
        return PhiKeyMode(raw.strip().lower())
    except ValueError:
        return None


def _is_ratified(data: dict[str, Any]) -> tuple[bool, str]:
    """True iff status + all three accountability fields are present and valid.

    Mirrors `auth_criteria._entry_is_ratified` exactly, including the `is True` pin: a string
    `"true"`, a `1`, or any other truthy value is REFUSED. A partial ratification (flag flipped,
    reviewer left null) is an unfinished edit, not a ratification, and must not activate anything.
    """
    status = data.get("status")
    if not (isinstance(status, str) and status.strip().upper() == _RATIFIED_STATUS):
        return False, "status_nao_ratificado"

    block = data.get(_RATIFICATION_BLOCK)
    if not isinstance(block, dict):
        return False, "ratificacao_ausente"
    if block.get(_RATIFIED_FLAG) is not True:
        return False, "ratificado_nao_true"
    if any(not _is_nonblank_str(block.get(field)) for field in (_RATIFIED_BY, _RATIFIED_AT)):
        return False, "ratificacao_incompleta"
    return True, ""


def _duplicate_top_level_keys(raw_text: str) -> tuple[str, ...]:
    """Top-level keys declared MORE THAN ONCE, in document order. Empty tuple when clean.

    WHY THIS GATE EXISTS. `yaml.safe_load` resolves a duplicate mapping key by silently keeping
    the LAST occurrence. On THIS artifact that is a forgery shape that needs no forger: a file
    whose visible top reads `status: DRAFT` / `modo: "off"` can carry a second `status:
    RATIFICADO` / `modo: pseudo_keys` further down and really activate the mode, while a human
    reading top-down — and a reviewer diffing only the block they were pointed at — sees the
    inert declaration. The reverse order is just as bad: it would silently DEACTIVATE a
    ratification the reviewer believes they granted. Either way the document does not say what it
    appears to say, so it is refused WHOLESALE (-> OFF), in both orders. Refusing is safe by
    construction: the fail-closed direction of this flag is "do nothing".

    Uses `yaml.compose` — the node graph, which PRESERVES duplicates — rather than a
    `SafeLoader.construct_mapping` override, so the check is scoped exactly to this loader and to
    the document ROOT. Nested duplicates inside `ratificacao`/`escopo` are not this gate's
    business (none of them can flip the mode), and no other YAML consumer in the repo changes
    behaviour. Malformed YAML returns `()` here: `_parse`'s own `safe_load` already reported it
    as `invalid_yaml`, and this function must never be the thing that raises.
    """
    try:
        root = yaml.compose(raw_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return ()
    if not isinstance(root, yaml.MappingNode):
        return ()
    seen: set[str] = set()
    duplicated: list[str] = []
    for key_node, _value_node in root.value:
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        key = str(key_node.value)
        if key in seen and key not in duplicated:
            duplicated.append(key)
        seen.add(key)
    return tuple(duplicated)


def _parse(raw_text: str, manifest_path: Path) -> PhiKeyPolicy:
    """Parse an already-read manifest. NEVER raises; demotes to OFF instead."""
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return _off("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")

    if not isinstance(data, dict):
        return _off("invalid_schema", f"{manifest_path}: root must be a mapping")

    # Duplicate-key guard (see `_duplicate_top_level_keys`): a document that reads one way and
    # loads another is refused before ANY field of it is trusted.
    duplicated = _duplicate_top_level_keys(raw_text)
    if duplicated:
        return _off(
            "chave_duplicada",
            f"{manifest_path}: top-level key(s) declared more than once: {sorted(duplicated)}",
        )

    # Template guard, mirrored from `load_retention_matrix`/`load_criteria_sources`: a copy marked
    # `unratified: true` is refused WHOLESALE, so pointing the path at a placeholder also fails
    # to the inert direction instead of half-activating.
    if data.get("unratified") is True:
        return _off("unratified_template", f"{manifest_path} is marked 'unratified: true'")

    declarado = _parse_mode(data.get("modo"))
    if declarado is None:
        return _off("modo_desconhecido", f"{manifest_path}: 'modo' must be one of off|scrub_only|pseudo_keys")

    ratified, why_not = _is_ratified(data)
    if not ratified:
        if declarado is PhiKeyMode.OFF:
            # The shipped state: a DRAFT manifest that also declares `off`. There is no
            # discrepancy to report — warning here would train operators to ignore this event,
            # which must keep meaning "someone staged a mode that is not in force".
            logger.debug("phi_key_policy_inert", path=str(manifest_path), reason=why_not)
            return PhiKeyPolicy(
                modo=PhiKeyMode.OFF, declarado=PhiKeyMode.OFF, ratificado=False, motivo=why_not
            )
        # THE forgery-shape case: a DRAFT manifest declaring `modo: pseudo_keys` lands here and
        # resolves to OFF. `declarado` preserves what was staged so ops can see it.
        return _off(
            why_not,
            f"{manifest_path}: declared modo={declarado.value} is NOT in force",
            declarado=declarado,
        )

    logger.info(
        "phi_key_policy_active",
        path=str(manifest_path),
        modo=declarado.value,
        revisor=str(data[_RATIFICATION_BLOCK][_RATIFIED_BY]),
        ratificado_em=str(data[_RATIFICATION_BLOCK][_RATIFIED_AT]),
    )
    return PhiKeyPolicy(modo=declarado, declarado=declarado, ratificado=True)


def load_phi_key_policy(path: str | Path | None = None) -> PhiKeyPolicy:
    """Load the remediation manifest. FAILS TO `OFF` — never raises, never self-activates.

    Path resolution: explicit `path` > `MAEZO_PHI_KEY_POLICY_PATH` >
    `<spec>/policies/privacy/phi-business-key-remediation.yaml` (via `resolve_spec_dir`).

    Every failure mode (unresolvable spec dir, missing file, non-file path, unreadable, non-UTF-8,
    malformed YAML, wrong schema, DUPLICATE top-level key, unknown `modo`, `unratified: true`,
    DRAFT status, incomplete ratification) returns an OFF policy plus one `warning` log line.
    """
    raw_path = path if path is not None else os.environ.get(PHI_KEY_POLICY_PATH_ENV)
    if not raw_path:
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 — an unresolvable spec/ activates nothing
            return _off("path_unresolved", f"could not resolve the default manifest path: {exc}")

    manifest_path = Path(raw_path)
    if not manifest_path.is_file():
        return _off("file_not_found", f"no readable manifest file at {manifest_path}")

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # NOT covered by OSError (UnicodeDecodeError subclasses ValueError) — the same explicit
        # catch `legal_bases_matrix.py` documents for cp1252 files with accented characters.
        return _off("invalid_encoding", f"{manifest_path} is not valid UTF-8: {exc}")
    except OSError as exc:
        return _off("unreadable", f"could not read {manifest_path}: {exc}")

    return _parse(raw_text, manifest_path)


@lru_cache(maxsize=8)
def _load_cached(resolved_path: str | None) -> PhiKeyPolicy:
    return load_phi_key_policy(resolved_path)


def phi_key_policy(path: str | Path | None = None) -> PhiKeyPolicy:
    """Cached `load_phi_key_policy` — the accessor hot paths (key mints, log processors) use.

    The manifest is static governance config, so caching avoids re-reading YAML per external task
    (same reasoning, and the same restart-to-refresh trade-off, as `auth_criteria.criteria_sources`
    and `ceilings._load_matrix_cached`). Activating therefore takes effect on the next daemon
    restart — acceptable for an artifact whose whole point is a deliberate human act, and inert
    in the interim.
    """
    return _load_cached(str(path) if path is not None else None)


def reset_phi_key_policy_cache() -> None:
    """Clear the `phi_key_policy` cache. Tests only — production refreshes by restarting."""
    _load_cached.cache_clear()
