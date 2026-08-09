"""Per-layer erasure PLAN seam + an inert dry-run reporter (ADR-0029 dark build).

This module builds the SEAM and the DRY-RUN only. It never builds, holds or executes a
destructive statement, and it never invents a human decision. Two things live here:

1. **The structural enumeration** (`PERSISTENCE_LAYERS`): every persistence relation the
   custody/erasure design names, each carrying its migration citation, how a titular's rows
   are identified, and — where an honest one exists — a `SELECT count(*)` probe. The
   enumeration is a FACT about the schema, derived from migrations 0001-0007, and it is
   authored here rather than read from YAML so that a human editing the (CODEOWNERS-gated)
   plan artifact cannot introduce a statement this process would run. The plan and this
   enumeration must cover the SAME relations; drift fails a unit test in both directions.

2. **The fail-closed loader** (`load_erasure_plan`) for the DPO's decisions, and
   `dry_run()`, which reports WHAT WOULD BE TOUCHED per layer as COUNTS ONLY. No row
   contents, no titular reference and no bind values are ever returned or logged.

WHAT THIS MODULE DOES NOT DO, stated plainly because the omissions are the design:

- It executes nothing destructive. `ErasureRunMode.EXECUTE` is refused unconditionally by
  `assert_dry_run_only()`. While the plan is unratified the reason is `plan_unratified`;
  once a DPO ratifies it, the reason becomes `execution_mechanism_absent` — because
  `ErasureManager.erase()` still raises `ErasureNotImplementedError`
  (`src/maezo/platform/erasure.py:152`) and the two identity bridges below still do not
  exist. Ratification alone is therefore NEVER sufficient to cause an effect, which is the
  point: a governance act must not double as a destructive trigger.
- It decides nothing. Every `decisao_dpo`, `base_legal` and `retencao` is the DPO's, and an
  unratified plan yields the literal sentinel `DECISION_PENDING` in every finding rather
  than a default.
- It does not open a database connection, import a driver, or hold a DSN. Counting is done
  by a `counter` callable the CALLER supplies; with no counter, every countable layer
  reports `NOT_COUNTED_NO_COUNTER` and no count is fabricated.

THE TWO MISSING IDENTITY BRIDGES (the reason most layers cannot be counted at all):

- `titular_pseudo_id` -> `fhir_patient_id`. The LGPD DSR process carries a pseudonym
  (`src/maezo/tools/workers/lgpd.py:112`); the only two columns that identify a titular in
  the Alembic chain are `fhir_patient_id`. No mapping exists (`lgpd.py:293-294`).
- `thread_id` -> `fhir_patient_id`. The working layer is keyed by `thread_id`, not by
  patient (`src/maezo/platform/erasure.py:196-206`).

A dry-run given a `titular_pseudo_id` therefore reports `NOT_COUNTED_IDENTITY_BRIDGE_ABSENT`
for EVERY layer. That is not a degraded result — it is the true state of the system, and
reporting a count of 0 instead would assert "nothing to erase", which is the same class of
false success `ErasureManager` was made to refuse.

Placement: inside `maezo.platform.lifecycle` on purpose. That package's sources are scanned
by `tests/unit/platform/test_lifecycle.py`, which forbids any import naming the destructive
audit-chain path and forbids DELETE/UPDATE/INSERT/DROP/TRUNCATE/ALTER as a whole word in any
string literal in code — plus, scoped to this module specifically, a positive check that
every statement-shaped string constant (plain or `+`-concatenated) starts with exactly
`SELECT count(*)`. Living under that scanner is a structural guarantee that this module's SQL
surface stays `SELECT count(*)` and nothing else.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NoReturn

import structlog
import yaml

logger = structlog.get_logger(__name__)

# Env var carrying the path to a ratified plan. Unset is NOT an error: the shipped DRAFT is
# resolved instead, so the default outcome is the honest `not_ratified` refusal rather than a
# `path_not_set` one that hides the artifact's existence.
PLAN_PATH_ENV: Final = "MAEZO_ERASURE_PLAN_PATH"

# The dry-run CLI reads the titular reference from the ENVIRONMENT, never from argv:
# argv is visible in `ps` output and lands in shell history, and a subject reference is not
# something to leave in either. It stays optional — without it the CLI still reports the
# structure, the plan state and the refusal reasons.
SUBJECT_REF_ENV: Final = "MAEZO_ERASURE_DRY_RUN_SUBJECT_REF"

PLAN_RELPATH: Final = "spec/policies/retention/erasure-plan.template.yaml"

# The sentinel every finding carries while the DPO has not decided. Deliberately identical to
# the placeholder marker used in the artifact, so an unratified state is greppable end to end.
DECISION_PENDING: Final = "PENDENTE"

# Machine-detectable placeholder markers. `PENDENTE` leads because this artifact's vocabulary
# is Portuguese; the rest mirror `maezo.platform.integrations.amh_inbox.PLACEHOLDER_MARKERS`
# so one half-filled artifact cannot pass a check the other half would fail.
PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = (
    "PENDENTE",
    "PENDING",
    "PLACEHOLDER",
    "TODO",
    "TBD",
    "XXX",
)

REQUIRED_PLAN_FIELDS: Final[tuple[str, ...]] = (
    "status",
    "ratificado",
    "dpo_review",
    "dpo_reviewer",
    "dpo_review_date",
    "evidence_ref",
    "notes",
    "review_packet",
    "camadas",
)

REQUIRED_LAYER_FIELDS: Final[tuple[str, ...]] = (
    "camada",
    "tabela",
    "decisao_dpo",
    "base_legal",
    "retencao",
)

# The closed set of verdicts a DPO may record per relation. Free text is refused: a verdict
# nobody can enumerate is a verdict no downstream mechanism could ever honour.
DECISION_VOCABULARY: Final[tuple[str, ...]] = (
    "ELIMINAR",
    "ANONIMIZAR",
    "RETER_COM_BASE_LEGAL",
)

APPROVED_REVIEW: Final = "APPROVED"
RATIFICADO_STATUS: Final = "RATIFICADO"

# Precise, distinguishable refusal reasons — the packet and the CLI both cite them verbatim.
REASON_PATH_OVERRIDE_UNUSABLE: Final = "path_override_unusable"
REASON_FILE_NOT_FOUND: Final = "file_not_found"
REASON_UNREADABLE: Final = "unreadable"
REASON_INVALID_ENCODING: Final = "invalid_encoding"
REASON_INVALID_YAML: Final = "invalid_yaml"
REASON_INVALID_SCHEMA: Final = "invalid_schema"
REASON_EMPTY: Final = "empty"
REASON_NOT_RATIFIED: Final = "not_ratified"
REASON_DPO_REVIEW_PENDING: Final = "dpo_review_pending"
REASON_STATUS_NOT_RATIFICADO: Final = "status_not_ratificado"
REASON_PLACEHOLDER_VALUE: Final = "placeholder_value"
REASON_UNKNOWN_DECISION: Final = "unknown_decision"
REASON_LAYER_SET_MISMATCH: Final = "layer_set_mismatch"

# Refusal reasons for a non-dry mode. Two, and which one fires is itself information.
REASON_PLAN_UNRATIFIED: Final = "plan_unratified"
REASON_EXECUTION_MECHANISM_ABSENT: Final = "execution_mechanism_absent"


class ErasurePlanUnavailableError(RuntimeError):
    """Fail-closed sentinel: no usable, DPO-ratified per-layer erasure plan is available.

    Raised by `load_erasure_plan` for every failure mode. There is no fallback plan, no
    partial load and no defaulted decision. `reason` is one of the `REASON_*` constants;
    `detail` is a path-qualified explanation fit for an operator-facing message.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"erasure plan unavailable ({reason}): {detail}")


class ErasureExecutionRefusedError(RuntimeError):
    """Fail-closed sentinel: a non-dry run was requested and is refused, always.

    `reason` distinguishes the two independent blockers: `plan_unratified` (no DPO decision
    exists) and `execution_mechanism_absent` (a decision exists, but nothing can carry it
    out). Both are refusals; neither is recoverable by retrying.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"erasure execution refused ({reason}): {detail}")


class IdentityResolution(StrEnum):
    """How — or whether — a titular's rows can be identified in a given relation.

    A structural fact about the schema, not a decision. `SEM_COLUNA_DE_TITULAR` and
    `PONTE_AUSENTE` are deliberately distinct: the first says no column identifies the
    subject at all, the second says a column does but the reference the DSR process carries
    cannot reach it.
    """

    RESOLVIVEL = "RESOLVIVEL"
    PONTE_AUSENTE = "PONTE_AUSENTE"
    SEM_COLUNA_DE_TITULAR = "SEM_COLUNA_DE_TITULAR"
    SEM_REFERENCIA_TITULAR = "SEM_REFERENCIA_TITULAR"
    NAO_PROVISIONADA = "NAO_PROVISIONADA"
    RETIRADA = "RETIRADA"


class SubjectRefKind(StrEnum):
    """Which KIND of reference the caller holds. The distinction is load-bearing.

    `TITULAR_PSEUDO_ID` is what the LGPD DSR process actually carries; `FHIR_PATIENT_ID` is
    what the two subject-bearing columns are keyed on. No mapping between them exists, so a
    caller holding the former can count nothing — and the report says so per layer instead of
    silently returning zeroes.
    """

    FHIR_PATIENT_ID = "fhir_patient_id"
    TITULAR_PSEUDO_ID = "titular_pseudo_id"


class ErasureRunMode(StrEnum):
    """Run mode. Only `DRY_RUN` is reachable; `EXECUTE` exists to be refused by name."""

    DRY_RUN = "dry_run"
    EXECUTE = "execute"


class LayerFindingStatus(StrEnum):
    """Outcome for one relation in a dry-run report.

    Every non-`COUNTED` value states WHY no count exists. There is deliberately no value
    meaning "assume zero": an absent count and a count of zero are different facts, and
    collapsing them would let an unreachable layer read as an empty one.
    """

    COUNTED = "COUNTED"
    COUNT_FAILED = "COUNT_FAILED"
    NOT_COUNTED_NO_COUNTER = "NOT_COUNTED_NO_COUNTER"
    NOT_COUNTED_IDENTITY_BRIDGE_ABSENT = "NOT_COUNTED_IDENTITY_BRIDGE_ABSENT"
    NOT_COUNTED_NO_SUBJECT_COLUMN = "NOT_COUNTED_NO_SUBJECT_COLUMN"
    NOT_APPLICABLE_NO_SUBJECT_REF = "NOT_APPLICABLE_NO_SUBJECT_REF"
    NOT_APPLICABLE_NOT_PROVISIONED = "NOT_APPLICABLE_NOT_PROVISIONED"
    NOT_APPLICABLE_RETIRED = "NOT_APPLICABLE_RETIRED"


@dataclass(frozen=True)
class PersistenceLayer:
    """One persistent relation the custody/erasure design names.

    Attributes:
        camada: Layer family (trabalho / episodica / semantica / auditoria / custodia /
            registro_de_eliminacao / idempotencia / inbox_amh).
        tabela: Relation name, as the migration writes it.
        migracao: Citation for where the relation is created (or that nothing creates it).
        identificacao: How a titular's rows would be identified, in prose.
        resolucao: The `IdentityResolution` verdict for this relation.
        ordem: Logical order of operations. NOT a database constraint — the chain declares
            no foreign key anywhere, so ordering is a procedural commitment, reviewable
            rather than enforced.
        subject_column: The column a subject reference binds to, or None.
        count_statement: A `SELECT count(*)` probe with named parameters, or None when no
            honest per-titular count exists. Values are NEVER interpolated into this text.
    """

    camada: str
    tabela: str
    migracao: str
    identificacao: str
    resolucao: IdentityResolution
    ordem: int
    subject_column: str | None
    count_statement: str | None


# ---------------------------------------------------------------------------------------
# The enumeration. Derived from migrations 0001-0007; every entry cites its source. Held in
# CODE, not read from the artifact, so that editing the (human-owned) plan can never change
# which statement a probe would issue.
#
# Only two relations in the whole chain carry a subject column, and both are `fhir_patient_id`
# (`agent_memory` 0001:69, `erasure_log` 0004:67). Everything else is either keyed on
# something derived from the event, or provably holds no subject reference at all.
# ---------------------------------------------------------------------------------------
PERSISTENCE_LAYERS: Final[tuple[PersistenceLayer, ...]] = (
    PersistenceLayer(
        camada="trabalho",
        tabela="checkpoints",
        migracao="none — PostgresSaver.setup()/.asetup(), not Alembic (0006:14-23,25-35)",
        identificacao="thread_id + checkpoint_ns; no fhir_patient_id column (erasure.py:196-206)",
        resolucao=IdentityResolution.NAO_PROVISIONADA,
        ordem=1,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="trabalho",
        tabela="checkpoint_blobs",
        migracao="none — PostgresSaver.setup()/.asetup() (0006:19-21)",
        identificacao="(thread_id, checkpoint_ns, channel, version); PHI-bearing BYTEA, no subject column",
        resolucao=IdentityResolution.NAO_PROVISIONADA,
        ordem=2,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="trabalho",
        tabela="checkpoint_writes",
        migracao="none — PostgresSaver.setup()/.asetup() (0006:22-23)",
        identificacao="(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)",
        resolucao=IdentityResolution.NAO_PROVISIONADA,
        ordem=3,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="trabalho",
        tabela="checkpoint_migrations",
        migracao="none — PostgresSaver.setup()/.asetup() (0006:17)",
        identificacao="none — the saver's own schema-version bookkeeping",
        resolucao=IdentityResolution.SEM_REFERENCIA_TITULAR,
        ordem=4,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="trabalho",
        tabela="agent_checkpoints",
        migracao="0001:33-43 created; 0006:69 dropped",
        identificacao="(thread_id, checkpoint_ns, checkpoint_id)",
        resolucao=IdentityResolution.RETIRADA,
        ordem=5,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="trabalho",
        tabela="agent_checkpoint_writes",
        migracao="0001:48-58 created; 0006:68 dropped",
        identificacao="(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)",
        resolucao=IdentityResolution.RETIRADA,
        ordem=6,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="episodica",
        tabela="agent_memory",
        migracao="0001:63-75; partial subject index 0001:82-86",
        identificacao="tenant_id + fhir_patient_id (NULLABLE, 0001:69)",
        resolucao=IdentityResolution.PONTE_AUSENTE,
        ordem=7,
        subject_column="fhir_patient_id",
        count_statement=(
            "SELECT count(*) AS n FROM agent_memory "
            "WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref"
        ),
    ),
    PersistenceLayer(
        camada="semantica",
        tabela="agent_memory.embedding",
        migracao="0001:72 (vector(1536) column); pgvector extension 0001:28",
        identificacao="the same row as the episodic entry — not a separate relation",
        resolucao=IdentityResolution.PONTE_AUSENTE,
        ordem=8,
        subject_column="fhir_patient_id",
        count_statement=(
            "SELECT count(*) AS n FROM agent_memory "
            "WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref "
            "AND embedding IS NOT NULL"
        ),
    ),
    PersistenceLayer(
        camada="auditoria",
        tabela="audit_chain",
        migracao="0002:27-51; UNIQUE(prev_record_hash) 0002:44-50",
        identificacao="no subject column; any link lives inside decision_basis jsonb (0002:36)",
        resolucao=IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ordem=9,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="auditoria",
        tabela="audit_emit_dedup",
        migracao="0005:59-67",
        identificacao="(tenant, dedup_key); dedup_key derives from the event, not the subject",
        resolucao=IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ordem=10,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="custodia",
        tabela="custody_bundles",
        migracao="0004:31-45; indexes 0004:47-58",
        identificacao="evidence_refs jsonb (0004:36) — pseudonymized pointers (custody.py:11)",
        resolucao=IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ordem=11,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="registro_de_eliminacao",
        tabela="erasure_log",
        migracao="0004:64-79; (tenant_id, fhir_patient_id) index 0004:82-84",
        identificacao="tenant_id + fhir_patient_id (NOT NULL, 0004:67)",
        resolucao=IdentityResolution.PONTE_AUSENTE,
        ordem=12,
        subject_column="fhir_patient_id",
        count_statement=(
            "SELECT count(*) AS n FROM erasure_log "
            "WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref"
        ),
    ),
    PersistenceLayer(
        camada="idempotencia",
        tabela="a2a_idempotency",
        migracao="0003:32-45",
        identificacao="(task_id, tenant); result jsonb (0003:39) may carry derived content",
        resolucao=IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ordem=13,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="idempotencia",
        tabela="driver_idempotency",
        migracao="0003:61-67; expiry index 0003:69-72",
        identificacao="key text PRIMARY KEY (0003:62) — a business key, not a subject column",
        resolucao=IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ordem=14,
        subject_column=None,
        count_statement=None,
    ),
    PersistenceLayer(
        camada="inbox_amh",
        tabela="amh_inbox",
        migracao="0007:127-229",
        identificacao="none — five subject-bearing envelope fields excluded by construction",
        resolucao=IdentityResolution.SEM_REFERENCIA_TITULAR,
        ordem=15,
        subject_column=None,
        count_statement=None,
    ),
)

KNOWN_TABLES: Final[frozenset[str]] = frozenset(layer.tabela for layer in PERSISTENCE_LAYERS)


@dataclass(frozen=True)
class ErasureLayerDecision:
    """The DPO's ratified verdict for one relation. Carried, never invented."""

    camada: str
    tabela: str
    decisao_dpo: str
    base_legal: str
    retencao: str


@dataclass(frozen=True)
class ErasurePlan:
    """An immutable, loaded-and-validated, DPO-ratified per-layer erasure plan."""

    dpo_reviewer: str
    dpo_review_date: str
    evidence_ref: str
    review_packet: str
    notes: str
    decisions: MappingProxyType[str, ErasureLayerDecision]

    def get(self, tabela: str) -> ErasureLayerDecision | None:
        """Look up one relation's ratified decision, or None if absent."""
        return self.decisions.get(tabela)

    def __len__(self) -> int:
        return len(self.decisions)

    def __iter__(self) -> Iterator[ErasureLayerDecision]:
        return iter(self.decisions.values())


@dataclass(frozen=True)
class LayerFinding:
    """What a dry-run learned about one relation. Counts only — never row contents."""

    layer: PersistenceLayer
    status: LayerFindingStatus
    row_count: int | None
    decisao_dpo: str
    detail: str


@dataclass(frozen=True)
class ErasureDryRunReport:
    """A dry-run result. Carries no subject reference and no row contents, by construction.

    `subject_ref_kind` records WHICH kind of reference was supplied; the value itself is
    never stored, returned or logged — it only ever reaches a bind parameter.
    """

    subject_ref_kind: SubjectRefKind
    tenant_id: str
    plan_ratified: bool
    findings: tuple[LayerFinding, ...]

    @property
    def counted_rows(self) -> int:
        """Total rows across layers that produced a count. Layers without one add nothing."""
        return sum(f.row_count or 0 for f in self.findings if f.status is LayerFindingStatus.COUNTED)

    @property
    def uncounted_layers(self) -> tuple[str, ...]:
        """Relations for which no honest count could be produced, in enumeration order."""
        return tuple(f.layer.tabela for f in self.findings if f.status is not LayerFindingStatus.COUNTED)


def _fail(reason: str, detail: str) -> ErasurePlanUnavailableError:
    logger.error("erasure_plan_unavailable", reason=reason, detail=detail)
    return ErasurePlanUnavailableError(reason, detail)


def _looks_like_placeholder(value: str) -> str | None:
    """Return the placeholder marker a value still carries, or None."""
    upper = value.upper()
    for marker in PLACEHOLDER_MARKERS:
        if marker in upper:
            return marker
    return None


def _plan_candidates() -> tuple[Path, ...]:
    """Repo-relative then package-adjacent candidate locations for the plan artifact."""
    here = Path(__file__).resolve()
    # src/maezo/platform/lifecycle/erasure_plan.py -> parents[4] is the repo root.
    repo_relative = here.parents[4] / PLAN_RELPATH
    # An installed wheel would resolve it package-adjacent (maezo/spec/...). Kept so the
    # module works if `spec/policies/retention` is ever force-included; see the packet's
    # open decision on packaging, which is deliberately NOT taken here.
    package_adjacent = here.parents[2] / PLAN_RELPATH
    return (repo_relative, package_adjacent)


def resolve_plan_path() -> Path:
    """Resolve the plan artifact, honouring `MAEZO_ERASURE_PLAN_PATH`.

    Fail-closed in both directions: an override pointing at a missing file raises rather
    than falling back to the shipped DRAFT (silently reading a different artifact than the
    operator named would be worse than refusing), and an unresolvable default raises with
    every candidate named.

    Raises:
        ErasurePlanUnavailableError: the override is unusable/missing, or no candidate exists.
    """
    override = os.environ.get(PLAN_PATH_ENV)
    if override:
        # Broad guard, deliberately: three stdlib calls on an operator-supplied string, none
        # of which contain this module's logic, and whose raise set is genuinely not
        # enumerable (`expanduser()` raises RuntimeError for `~nosuchuser`; `resolve()` raises
        # ValueError for an embedded NUL and OSError for an overlong component).
        try:
            path = Path(override).expanduser().resolve()
        except Exception as exc:
            raise _fail(
                REASON_PATH_OVERRIDE_UNUSABLE,
                f"{PLAN_PATH_ENV}={override!r} could not be resolved to a path: {exc}",
            ) from exc
        if not path.is_file():
            raise _fail(
                REASON_FILE_NOT_FOUND,
                f"{PLAN_PATH_ENV} points at {path}, which is not an existing file",
            )
        return path

    candidates = _plan_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise _fail(
        REASON_FILE_NOT_FOUND,
        f"no erasure plan artifact found; tried {[str(c) for c in candidates]} "
        f"(set {PLAN_PATH_ENV} to point at a ratified one)",
    )


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail(REASON_UNREADABLE, f"could not read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        # NOT covered by OSError: UnicodeDecodeError subclasses ValueError. Without this
        # explicit catch a non-UTF-8 artifact (cp1252 from Windows tooling, with accented
        # words like "juridico") would escape as a raw traceback instead of a typed refusal.
        raise _fail(
            REASON_INVALID_ENCODING,
            f"{path} is not valid UTF-8 ({exc.encoding} failed at byte {exc.start}: "
            f"{exc.reason}) — re-encode the plan as UTF-8",
        ) from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise _fail(REASON_INVALID_YAML, f"malformed YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{path}: root must be a mapping, got {type(data).__name__}",
        )
    return data


def _require_clean_str(path: Path, where: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{path}: {where}{field} must be a non-empty string, got {type(value).__name__}",
        )
    marker = _looks_like_placeholder(value)
    if marker is not None:
        raise _fail(
            REASON_PLACEHOLDER_VALUE,
            f"{path}: {where}{field} still carries the placeholder marker {marker!r} — "
            "a half-filled plan is not a plan, and no value here may be inferred",
        )
    return value


def _parse_layers(path: Path, raw_layers: object) -> dict[str, ErasureLayerDecision]:
    if not isinstance(raw_layers, list):
        raise _fail(REASON_INVALID_SCHEMA, f"{path}: 'camadas' is missing or is not a list")
    if not raw_layers:
        raise _fail(REASON_EMPTY, f"{path}: 'camadas' is present but empty")

    decisions: dict[str, ErasureLayerDecision] = {}
    for index, raw_entry in enumerate(raw_layers):
        where = f"camadas[{index}]."
        if not isinstance(raw_entry, dict):
            raise _fail(REASON_INVALID_SCHEMA, f"{path}: camadas[{index}] is not a mapping")
        missing = [f for f in REQUIRED_LAYER_FIELDS if f not in raw_entry]
        if missing:
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{path}: camadas[{index}] missing required field(s): {missing}",
            )
        values = {f: _require_clean_str(path, where, f, raw_entry[f]) for f in REQUIRED_LAYER_FIELDS}

        decision = values["decisao_dpo"]
        if decision not in DECISION_VOCABULARY:
            raise _fail(
                REASON_UNKNOWN_DECISION,
                f"{path}: {where}decisao_dpo is {decision!r}, which is not one of "
                f"{list(DECISION_VOCABULARY)} — a verdict nobody can enumerate is not a verdict",
            )

        tabela = values["tabela"]
        if tabela in decisions:
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{path}: duplicate tabela {tabela!r} at camadas[{index}]",
            )
        decisions[tabela] = ErasureLayerDecision(
            camada=values["camada"],
            tabela=tabela,
            decisao_dpo=decision,
            base_legal=values["base_legal"],
            retencao=values["retencao"],
        )

    planned = frozenset(decisions)
    if planned != KNOWN_TABLES:
        raise _fail(
            REASON_LAYER_SET_MISMATCH,
            f"{path}: the plan covers a different relation set than this module enumerates. "
            f"Missing from the plan: {sorted(KNOWN_TABLES - planned)}; "
            f"unknown to this module: {sorted(planned - KNOWN_TABLES)}. A plan that does not "
            "cover exactly the known relations is not a plan",
        )
    return decisions


def load_erasure_plan(path: str | Path | None = None) -> ErasurePlan:
    """Load and validate the DPO's per-layer erasure plan from YAML. FAILS CLOSED.

    Path resolution: the explicit `path` argument, else `resolve_plan_path()`.

    Every failure mode — a missing/unreadable/mis-encoded/malformed artifact, a schema
    violation, `ratificado` that is not the boolean `true`, a `dpo_review` that is not
    `APPROVED`, a `status` that is not `RATIFICADO`, any value still carrying a placeholder
    marker, a verdict outside the closed vocabulary, or a relation set that differs from this
    module's enumeration — raises `ErasurePlanUnavailableError` with a precise `reason`.
    Nothing here defaults, infers or invents a human value: the only successful outcome is a
    plan built entirely from what a DPO explicitly wrote.

    Three switches gate ratification independently — `ratificado` (bool), `dpo_review`
    (`APPROVED`) and `status` (`RATIFICADO`) — deliberately redundant with each other. A DPO
    could otherwise leave `status: DRAFT` unedited while flipping the other two, and the
    artifact would read as ratified while its own headline field still said it was a draft.

    The layer-set equality check is what stops a ratification from silently carrying over: a
    migration that adds a relation makes the shipped plan incomplete, and the plan refuses
    until a DPO decides for the new relation too.

    Raises:
        ErasurePlanUnavailableError: always, on any failure mode above.
    """
    plan_path = Path(path) if path is not None else resolve_plan_path()
    if not plan_path.is_file():
        raise _fail(REASON_FILE_NOT_FOUND, f"erasure plan artifact not found at {plan_path}")

    data = _read_yaml_mapping(plan_path)

    missing = [f for f in REQUIRED_PLAN_FIELDS if f not in data]
    if missing:
        raise _fail(REASON_INVALID_SCHEMA, f"{plan_path}: missing required field(s): {missing}")

    # The two switch fields are checked BEFORE the placeholder sweep so a pure DRAFT reports
    # the reason a reader expects (`not_ratified`) instead of tripping on one of its own
    # placeholder strings first.
    ratificado = data["ratificado"]
    if ratificado is not True:
        raise _fail(
            REASON_NOT_RATIFIED,
            f"{plan_path}: ratificado is {ratificado!r}, not the boolean true — this artifact "
            "is a DRAFT and no per-layer erasure decision exists until a DPO ratifies it",
        )
    dpo_review = data["dpo_review"]
    if dpo_review != APPROVED_REVIEW:
        raise _fail(
            REASON_DPO_REVIEW_PENDING,
            f"{plan_path}: dpo_review is {dpo_review!r}, not {APPROVED_REVIEW!r}",
        )
    # A third, independent switch. Without this check `status` reached only
    # `_require_clean_str` below — a non-empty, non-placeholder string — so a plan left as
    # `status: DRAFT` while `ratificado: true` and `dpo_review: APPROVED` were both flipped
    # would load as ratified. `status` itself must say RATIFICADO too.
    status = data["status"]
    if status != RATIFICADO_STATUS:
        raise _fail(
            REASON_STATUS_NOT_RATIFICADO,
            f"{plan_path}: status is {status!r}, not {RATIFICADO_STATUS!r} — ratificado=true "
            f"and dpo_review={APPROVED_REVIEW!r} are not sufficient on their own; the status "
            "field itself must also read RATIFICADO or this artifact is still a DRAFT",
        )

    scalars = {
        f: _require_clean_str(plan_path, "", f, data[f])
        for f in ("status", "dpo_reviewer", "dpo_review_date", "evidence_ref", "notes", "review_packet")
    }
    decisions = _parse_layers(plan_path, data["camadas"])

    logger.info(
        "erasure_plan_loaded",
        path=str(plan_path),
        status=scalars["status"],
        relation_count=len(decisions),
    )
    return ErasurePlan(
        dpo_reviewer=scalars["dpo_reviewer"],
        dpo_review_date=scalars["dpo_review_date"],
        evidence_ref=scalars["evidence_ref"],
        review_packet=scalars["review_packet"],
        notes=scalars["notes"],
        decisions=MappingProxyType(decisions),
    )


def assert_dry_run_only(mode: ErasureRunMode, plan: ErasurePlan | None) -> None:
    """Refuse any non-dry mode. There is no argument, environment or plan that permits one.

    The refusal REASON depends on the plan, and that is the whole information content of this
    function: with no ratified plan the blocker is governance (`plan_unratified`); with one,
    the blocker is that nothing can carry the decision out (`execution_mechanism_absent`) —
    `ErasureManager.erase()` raises `ErasureNotImplementedError` (`erasure.py:152`) and
    neither identity bridge exists. Ratification is therefore never, on its own, sufficient
    to cause an effect.

    Args:
        mode: The requested mode.
        plan: A ratified plan, if one loaded.

    Raises:
        ErasureExecutionRefusedError: whenever `mode` is not `DRY_RUN`.
    """
    if mode is ErasureRunMode.DRY_RUN:
        return
    if plan is None:
        _refuse(
            REASON_PLAN_UNRATIFIED,
            f"mode={mode.value} requested, but no DPO-ratified per-layer erasure plan is "
            f"loaded ({PLAN_RELPATH} ships as a DRAFT). Only "
            f"{ErasureRunMode.DRY_RUN.value} is reachable",
        )
    _refuse(
        REASON_EXECUTION_MECHANISM_ABSENT,
        f"mode={mode.value} requested with a ratified plan present, and it is STILL refused: "
        "no execution mechanism exists (ErasureManager.erase() raises "
        "ErasureNotImplementedError; the titular_pseudo_id->fhir_patient_id and "
        "thread_id->fhir_patient_id bridges do not exist). Ratification is a governance act, "
        "never a trigger",
    )


def _refuse(reason: str, detail: str) -> NoReturn:
    logger.error("erasure_execution_refused", reason=reason, detail=detail)
    raise ErasureExecutionRefusedError(reason, detail)


def _static_status(layer: PersistenceLayer) -> LayerFindingStatus | None:
    """Statuses decided by the relation itself, before any reference or counter is involved."""
    if layer.resolucao is IdentityResolution.RETIRADA:
        return LayerFindingStatus.NOT_APPLICABLE_RETIRED
    if layer.resolucao is IdentityResolution.NAO_PROVISIONADA:
        return LayerFindingStatus.NOT_APPLICABLE_NOT_PROVISIONED
    if layer.resolucao is IdentityResolution.SEM_REFERENCIA_TITULAR:
        return LayerFindingStatus.NOT_APPLICABLE_NO_SUBJECT_REF
    if layer.resolucao is IdentityResolution.SEM_COLUNA_DE_TITULAR:
        return LayerFindingStatus.NOT_COUNTED_NO_SUBJECT_COLUMN
    return None


def _probe(
    layer: PersistenceLayer,
    *,
    subject_ref: str,
    subject_ref_kind: SubjectRefKind,
    tenant_id: str,
    counter: Callable[[str, Mapping[str, str]], int] | None,
) -> tuple[LayerFindingStatus, int | None, str]:
    static = _static_status(layer)
    if static is not None:
        return static, None, layer.identificacao

    if subject_ref_kind is not SubjectRefKind.FHIR_PATIENT_ID:
        return (
            LayerFindingStatus.NOT_COUNTED_IDENTITY_BRIDGE_ABSENT,
            None,
            f"reference is a {subject_ref_kind.value}; this relation is keyed on "
            f"{layer.subject_column} and no mapping between the two exists (lgpd.py:293-294)",
        )
    if layer.count_statement is None:
        return LayerFindingStatus.NOT_COUNTED_NO_SUBJECT_COLUMN, None, layer.identificacao
    if counter is None:
        return (
            LayerFindingStatus.NOT_COUNTED_NO_COUNTER,
            None,
            "no counter supplied; this module opens no connection of its own",
        )

    # Runtime belt-and-suspenders, alongside the static AST guard in test_lifecycle.py: even a
    # `PersistenceLayer` built outside `PERSISTENCE_LAYERS` (the `_layers` override seam exists
    # for exactly this in tests) cannot reach the counter with anything but a count probe.
    # An explicit `if/raise` on purpose, NOT `assert`: `assert` is stripped under `python -O`
    # (`__debug__` becomes False), which would silently defeat this exact guard in the highest-
    # risk scenario — GK N-1.
    if not layer.count_statement.startswith("SELECT count(*)"):
        raise ValueError(
            f"refusing to execute a non-SELECT-count statement for {layer.tabela!r}: "
            f"{layer.count_statement!r}"
        )
    params: Mapping[str, str] = {"tenant_id": tenant_id, "subject_ref": subject_ref}
    try:
        count = counter(layer.count_statement, params)
    except Exception as exc:  # noqa: BLE001 — a caller-supplied callable; its raise set is theirs
        # The message is bounded to the exception TYPE on purpose: a driver error can echo the
        # bind values, and a report that quotes them would carry the very reference this
        # report is built not to carry.
        return (
            LayerFindingStatus.COUNT_FAILED,
            None,
            f"counter raised {type(exc).__name__} (message withheld: it can echo bind values)",
        )
    return LayerFindingStatus.COUNTED, int(count), layer.identificacao


def dry_run(
    *,
    subject_ref: str,
    subject_ref_kind: SubjectRefKind,
    tenant_id: str,
    counter: Callable[[str, Mapping[str, str]], int] | None = None,
    plan: ErasurePlan | None = None,
    mode: ErasureRunMode = ErasureRunMode.DRY_RUN,
    _layers: Sequence[PersistenceLayer] = PERSISTENCE_LAYERS,
) -> ErasureDryRunReport:
    """Report WHAT WOULD BE TOUCHED per persistence layer, as counts only.

    Nothing is modified, and nothing destructive is built: the only statements this function
    can issue are the `SELECT count(*)` probes in `PERSISTENCE_LAYERS`, handed to a counter
    the CALLER supplies. `subject_ref` reaches a bind parameter and nothing else — it is
    never logged, never returned and never interpolated into SQL text.

    Args:
        subject_ref: The titular reference. Never stored or logged. Blank/whitespace-only is
            refused whenever `counter` is also supplied — see Raises.
        subject_ref_kind: Which kind of reference `subject_ref` is. A `TITULAR_PSEUDO_ID`
            yields `NOT_COUNTED_IDENTITY_BRIDGE_ABSENT` everywhere — the true state today.
        tenant_id: Tenant scope, bound as a parameter.
        counter: Executes one probe and returns its count. None means no counts are produced
            (and none are fabricated).
        plan: A ratified plan, if one loaded. Without it every finding carries
            `DECISION_PENDING`.
        mode: Must be `DRY_RUN`; anything else is refused before any probe runs.
        _layers: The relations to probe. Defaults to the full enumeration; leading underscore
            because this is a TEST seam, not a production parameter — overridable so a test can
            exercise one relation (or a deliberately poisoned one) without re-declaring the set.

    Returns:
        An `ErasureDryRunReport` with one finding per relation, in enumeration order.

    Raises:
        ErasureExecutionRefusedError: if `mode` is not `DRY_RUN`.
        ValueError: if `counter` is supplied and `subject_ref` is blank or whitespace-only. A
            count issued against an empty reference would come back as an ordinary `COUNTED`
            result — indistinguishable from a genuine zero — instead of surfacing the missing
            input it actually is; refusing up front keeps that false-success class out of the
            report entirely, on the same reasoning as `NOT_COUNTED_IDENTITY_BRIDGE_ABSENT`.
    """
    assert_dry_run_only(mode, plan)

    if counter is not None and not subject_ref.strip():
        raise ValueError(
            "counter supplied but subject_ref is blank/whitespace-only — refusing to probe: "
            "a count against an empty reference would read as a confirmed COUNTED result "
            "instead of the missing input it actually is"
        )

    findings: list[LayerFinding] = []
    for layer in sorted(_layers, key=lambda item: item.ordem):
        status, count, detail = _probe(
            layer,
            subject_ref=subject_ref,
            subject_ref_kind=subject_ref_kind,
            tenant_id=tenant_id,
            counter=counter,
        )
        decision = DECISION_PENDING
        if plan is not None:
            ratified = plan.get(layer.tabela)
            if ratified is not None:
                decision = ratified.decisao_dpo
        findings.append(
            LayerFinding(
                layer=layer,
                status=status,
                row_count=count,
                decisao_dpo=decision,
                detail=detail,
            )
        )

    report = ErasureDryRunReport(
        subject_ref_kind=subject_ref_kind,
        tenant_id=tenant_id,
        plan_ratified=plan is not None,
        findings=tuple(findings),
    )
    # Counts and statuses only. The subject reference is deliberately absent from this event.
    logger.info(
        "erasure_dry_run_completed",
        tenant_id=tenant_id,
        subject_ref_kind=subject_ref_kind.value,
        plan_ratified=report.plan_ratified,
        relation_count=len(report.findings),
        counted_rows=report.counted_rows,
        uncounted_layers=list(report.uncounted_layers),
    )
    return report


def render_report(report: ErasureDryRunReport, plan_state: str) -> str:
    """Render a dry-run report as operator-facing text. Counts and statuses only."""
    lines = [
        f"erasure dry-run — tenant={report.tenant_id} "
        f"subject_ref_kind={report.subject_ref_kind.value} plan={plan_state}",
        f"{'ordem':<6}{'camada':<24}{'tabela':<26}{'status':<38}{'linhas':<8}decisao_dpo",
    ]
    for finding in report.findings:
        count = "-" if finding.row_count is None else str(finding.row_count)
        lines.append(
            f"{finding.layer.ordem:<6}{finding.layer.camada:<24}{finding.layer.tabela:<26}"
            f"{finding.status.value:<38}{count:<8}{finding.decisao_dpo}"
        )
    lines.append(
        f"counted rows: {report.counted_rows}; relations without an honest count: "
        f"{len(report.uncounted_layers)}/{len(report.findings)}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Inert dry-run entrypoint: `python -m maezo.platform.lifecycle.erasure_plan`.

    Opens no database connection and supplies no counter, so every countable relation reports
    `NOT_COUNTED_NO_COUNTER`. What it DOES report is the full structural enumeration, the
    plan's state, and — per relation — the DPO decision or the `PENDENTE` sentinel. The
    titular reference, if any, is read from the environment rather than argv (argv shows up in
    `ps` and shell history).

    Returns:
        0 — this entrypoint only reports. It never has an effect to succeed or fail at.
    """
    parser = argparse.ArgumentParser(
        prog="maezo.platform.lifecycle.erasure_plan",
        description="Dry-run only: report what an erasure WOULD touch, per persistence layer.",
        # No abbreviations: `--subject-ref` must NOT silently resolve to `--subject-ref-kind`.
        # An operator reaching for a flag that would take the reference itself has to be told
        # the flag does not exist, not have their reference land in a different option.
        allow_abbrev=False,
    )
    parser.add_argument("--tenant", required=True, help="tenant scope, bound as a parameter")
    parser.add_argument(
        "--subject-ref-kind",
        choices=[kind.value for kind in SubjectRefKind],
        default=SubjectRefKind.TITULAR_PSEUDO_ID.value,
        help=f"which kind of reference {SUBJECT_REF_ENV} carries",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    plan: ErasurePlan | None
    try:
        loaded = load_erasure_plan()
    except ErasurePlanUnavailableError as exc:
        plan, plan_state = None, f"UNRATIFIED ({exc.reason})"
    else:
        plan = loaded
        plan_state = f"RATIFIED by {loaded.dpo_reviewer} on {loaded.dpo_review_date}"

    report = dry_run(
        subject_ref=os.environ.get(SUBJECT_REF_ENV, ""),
        subject_ref_kind=SubjectRefKind(args.subject_ref_kind),
        tenant_id=args.tenant,
        counter=None,
        plan=plan,
    )
    print(render_report(report, plan_state))  # noqa: T201 — operator-facing CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess in the test suite
    raise SystemExit(main())
