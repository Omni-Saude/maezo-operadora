"""The AMH durable inbox — DBA-gated, inert until ratified (MZO-060, ADR-0037 XRD-10).

**What this module is.** The typed repository over the `amh_inbox` table created by
`platform/migrations/versions/0007_amh_inbox.py`, plus the fail-closed loader for the human
ratification artifact that gates it. It is the durable-settlement half of the answer to a question
`maezo.adapters.amh` recorded and refused to fake: ADR-0037 XRD-10 says "offsets Kafka nunca
representam conclusao de negocio", and `maezo.ports.work_items.WorkItemSource.ack` may return
success ONLY on DURABLE settlement — so the thing that licences an `ack` is a committed row here,
never a committed offset.

**Nothing consumes it. That is deliberate, and it is enforced twice.**

1. *The consumer does not exist.* MZO-050b (the `AmhEventConsumer`) is separately gated and
   `maezo.adapters.amh` still ships no `consumer.py`; nothing in `worker_runtime` or any other
   composition root imports this module.
2. *And if it did, it still would not run.* `build_amh_inbox_repository` REFUSES to construct
   anything until `spec/policies/amh/inbox-ratification.yaml` says a DBA approved the schema. The
   refusal is not a flag a caller can pass around: `PostgresAmhInbox` cannot be constructed
   without an `InboxRatification`, and `InboxRatification.__post_init__` refuses to exist unless
   `ratificado` is the boolean `true` and `dba_review` is `APPROVED`. An unratified inbox
   therefore cannot report settlement success, because no object able to report it can be built.

That is the same refusal shape as `maezo.platform.lifecycle.legal_bases_matrix`, which refuses its
own `UNRATIFIED-*.template.yaml` rather than treating placeholder markers as data — a precedent
this module follows down to the `REASON_*` taxonomy and the "no default, no fallback, no partial
load" posture.

**Where the guarantee actually lives, stated precisely.** `InboxRatification` checks the two HUMAN
switches and nothing else — it is a dataclass, so a caller CAN hand-build one without reading any
artifact at all. What binds an approval to a file and to specific DDL is
`build_amh_inbox_repository` -> `load_inbox_ratification`: that is the digest-binding path, and the
artifact on disk is the switch. `PostgresAmhInbox.__init__` is the SECOND lock and re-checks all
four properties (the two switches plus `migration_revision` and `migration_sha256`), so a
hand-built or forged ratification naming other DDL yields a refusal rather than a working
repository. Before that re-check existed, `InboxRatification(migration_revision="0001",
migration_sha256="deadbeef", ...)` built a repository that recorded and settled rows on a live
server.

**Activation requires NO code change.** Ratifying is a YAML edit plus applying migration 0007. The
digest binding is what keeps that from being a loophole: a ratified artifact must carry the sha256
of the migration file it approved, so a ratification cannot silently carry over to DDL the reviewer
never saw.

**A typing consequence MZO-050b inherits, recorded here rather than discovered there.** The
`AmhInbox` Protocol below lives under `maezo.platform`, and `maezo.adapters.amh` is fenced against
importing any `maezo.*` outside `maezo.ports` and its own package
(`tests/unit/adapters/amh/test_adapter_purity.py`, `_ALLOWED_MAEZO_PREFIXES`). The consumer can
therefore NEVER write `from maezo.platform.integrations.amh_inbox import AmhInbox` — not even under
`if TYPE_CHECKING`, since that fence walks the AST and sees guarded imports too. Composition-root
INJECTION still works, because `Protocol` is structural: the root builds a `PostgresAmhInbox` and
hands it to the consumer, which annotates the collaborator with a shape it is allowed to name. If
MZO-050b wants the annotation to name this exact surface, the follow-up is to declare the Protocol
port-side under `maezo/ports/` and have this class satisfy it — never to relax the fence.

**PHI posture.** `_stored_row` is the ONE function that projects a `CanonicalEnvelope` onto stored
columns. It fills 18 columns: 17 of them from 16 of the envelope's 28 fields (`source_position`
contributes two, `kind` and `value`), and the 18th (`inbox_stream`) from the `stream` ARGUMENT, not
from the envelope. FIVE envelope fields are deliberately never read —
`protected_source_record_ref`, `portable_subject_ref`, `amh_mpi_ref`, `beneficiary_ref`,
`consent_decision_ref`. The event body is not a sixth: `CanonicalEnvelope` has no `payload` field
and this schema has no payload column, so no event body is persisted at all. That is a boundary
decision: the AMH is the lake of record (ADR-0019) and the surviving ADR-0013 principle is
consume-not-duplicate, so a payload column here would make this repository a second copy of record
of AMH-owned data. `payload_hash` is kept instead — a non-reversible digest that lets a replay
carrying MUTATED content be DETECTED (`InboxRecordOutcome.CONFLICT`) instead of silently
deduplicated. Quarantine referrals carry only a closed `PortFailureReason` token and a pinned topic
NAME, never a payload or an upstream error body (ADR-0037 immutable prohibition #5, and
`WorkItemSource.nack`'s own contract).

**Honest results only (DL-0038).** Every operation reports what it OBSERVED. `record` distinguishes
a fresh dedup row from a consistent duplicate from a key collision carrying different content;
`mark_settled` distinguishes a settlement it applied from one that was already there from a refusal
to overwrite the opposite terminal state; `mark_quarantined` returns the reason ACTUALLY STORED, so
a second `nack` carrying a DIVERGENT reason is detectable instead of silently dropped. There is no
"assume it worked" branch anywhere in this file — and the one branch that turned out to be
defensive rather than reachable (`InboxRecordOutcome.CONCURRENT`) says so in its own docstring
instead of claiming a race it does not have.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog
import yaml

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.ports.envelope import CanonicalEnvelope
from maezo.ports.errors import PortFailureReason

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Ratification artifact — location
# ---------------------------------------------------------------------------

#: Env override for the ratification artifact path. Authoritative when set: pointing it at a
#: missing file fails closed rather than falling back to a default, mirroring
#: `MAEZO_AMH_CONTRACT_PIN` (`maezo.adapters.amh.contract`) and `MAEZO_SPEC_DIR`
#: (`maezo.agents.resolve_spec_dir`). An explicit-but-wrong override is a configuration error, and
#: silently loading a DIFFERENT ratification than the operator named is the failure this prevents.
INBOX_RATIFICATION_PATH_ENV: Final[str] = "MAEZO_AMH_INBOX_RATIFICATION"

#: Repo-relative location of the artifact.
INBOX_RATIFICATION_RELATIVE_PATH: Final[str] = "spec/policies/amh/inbox-ratification.yaml"

#: The alembic revision this module's SQL speaks. A ratification for any other revision is refused.
INBOX_MIGRATION_REVISION: Final[str] = "0007"

#: Filename of that migration, resolved package-relative so it works from a wheel as well as a
#: checkout (the migrations live INSIDE `src/maezo`, so they ship with the package).
INBOX_MIGRATION_FILENAME: Final[str] = "0007_amh_inbox.py"

#: Cap on any externally-sourced string echoed into a refusal message. The path is env-overridable,
#: so a misconfigured override could otherwise paste an arbitrary file's strings into a log line
#: unbounded (the bound `maezo.adapters.amh.contract` adopted for the same reason).
MAX_ECHOED_CHARS: Final[int] = 200

# ---------------------------------------------------------------------------
# Ratification artifact — schema
# ---------------------------------------------------------------------------

REQUIRED_RATIFICATION_FIELDS: Final[tuple[str, ...]] = (
    "status",
    "ratificado",
    "dba_review",
    "dba_reviewer",
    "dba_review_date",
    "evidence_ref",
    "notes",
    "migration_revision",
    "migration_sha256",
    "review_packet",
)

#: The literal values a RATIFIED artifact must carry. Compared by identity for `ratificado`
#: (`is True`), never by equality: `1 == True` in Python, so an `==` check would accept
#: `ratificado: 1` as a human's ratification.
RATIFIED_STATUS: Final[str] = "RATIFIED"
APPROVED_DBA_REVIEW: Final[str] = "APPROVED"

#: Markers that identify a value as an unfilled placeholder. A half-filled ratification is not a
#: ratification — the loader refuses if ANY string value still carries one of these.
PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = ("PENDING", "PLACEHOLDER", "TODO", "TBD", "XXX")

# Precise, distinguishable refusal reasons. Callers (and the DBA packet) use these to tell "the
# artifact is absent" apart from "present but a draft" apart from "ratified for DIFFERENT DDL".
REASON_PATH_NOT_SET = "path_not_set"
REASON_FILE_NOT_FOUND = "file_not_found"
REASON_UNREADABLE = "unreadable"
REASON_INVALID_ENCODING = "invalid_encoding"
REASON_INVALID_YAML = "invalid_yaml"
REASON_INVALID_SCHEMA = "invalid_schema"
REASON_PLACEHOLDER_VALUE = "placeholder_value"
REASON_NOT_RATIFIED = "not_ratified"
REASON_DBA_REVIEW_PENDING = "dba_review_pending"
REASON_MIGRATION_REVISION_MISMATCH = "migration_revision_mismatch"
REASON_MIGRATION_FILE_NOT_FOUND = "migration_file_not_found"
REASON_MIGRATION_DIGEST_MISMATCH = "migration_digest_mismatch"
REASON_INVALID_ENVELOPE = "invalid_envelope"
REASON_UNRATIFIED_REPOSITORY = "unratified_repository"


class AmhInboxUnavailableError(RuntimeError):
    """Fail-closed sentinel: no usable, DBA-ratified AMH inbox is available.

    Raised for EVERY failure mode of the loader, the path resolver, the repository factory and the
    envelope projection. There is no fallback artifact, no partial load and no degraded repository.
    `reason` is one of the `REASON_*` constants; `detail` is an operator-facing, bounded, non-PHI
    explanation.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"amh inbox unavailable ({reason}): {detail}")


def _bounded(value: str) -> str:
    """Bound an externally-sourced string before it reaches a log line or a refusal message."""
    if len(value) <= MAX_ECHOED_CHARS:
        return value
    return f"{value[:MAX_ECHOED_CHARS]}...[truncated {len(value) - MAX_ECHOED_CHARS} chars]"


def _fail(reason: str, detail: str) -> AmhInboxUnavailableError:
    logger.error("amh_inbox_unavailable", reason=reason, detail=detail)
    return AmhInboxUnavailableError(reason, detail)


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxRatification:
    """A LOADED, VALIDATED, DBA-ratified authorisation to operate the AMH inbox.

    This type is the capability. `PostgresAmhInbox` takes one and cannot be built without it, and
    `__post_init__` refuses to construct an instance that does not carry a real ratification — so
    there is no such thing as an `InboxRatification` object that says "not ratified yet". A caller
    cannot fabricate a permissive one by passing different constructor arguments; the only way to
    obtain one is `load_inbox_ratification` reading a file a DBA edited.
    """

    status: str
    ratificado: bool
    dba_review: str
    dba_reviewer: str
    dba_review_date: str
    evidence_ref: str
    notes: str
    migration_revision: str
    migration_sha256: str
    review_packet: str

    def __post_init__(self) -> None:
        # `is not True`, not `!= True`: `1 == True`, and a ratification is a human's explicit
        # boolean, never a truthy accident.
        if self.ratificado is not True:
            raise _fail(
                REASON_NOT_RATIFIED,
                f"ratificado is {self.ratificado!r}, not the boolean true — an InboxRatification "
                "may only exist for an artifact a DBA actually ratified",
            )
        if self.status != RATIFIED_STATUS:
            raise _fail(
                REASON_NOT_RATIFIED,
                f"status is {_bounded(str(self.status))!r}, expected {RATIFIED_STATUS!r}",
            )
        if self.dba_review != APPROVED_DBA_REVIEW:
            raise _fail(
                REASON_DBA_REVIEW_PENDING,
                f"dba_review is {_bounded(str(self.dba_review))!r}, expected {APPROVED_DBA_REVIEW!r}",
            )


# ---------------------------------------------------------------------------
# Ratification artifact — resolution and loading
# ---------------------------------------------------------------------------


def _repo_root_candidate() -> Path:
    """`src/maezo/platform/integrations/ -> repo root` (a source checkout)."""
    return Path(__file__).resolve().parents[4]


def _package_root() -> Path:
    """`src/maezo/platform/integrations/ -> src/maezo` (also the installed package root)."""
    return Path(__file__).resolve().parents[2]


def _default_ratification_candidates() -> tuple[Path, ...]:
    """Repo checkout first, then the package-adjacent copy an installed wheel ships.

    The wheel force-include for `spec/policies/amh` (`pyproject.toml`) is what puts the second
    candidate on disk in a container. Without it a packaged deployment would find NO artifact and
    fail closed forever — the same deploy-bricking shape the AMH contract pin already hit and
    fixed (MZO-050a).
    """
    relative = Path(INBOX_RATIFICATION_RELATIVE_PATH)
    return (
        _repo_root_candidate() / relative,
        _package_root() / relative,
    )


def _is_existing_file(path: Path) -> bool:
    """`Path.is_file()` without letting a hostile path raise out of a boolean question."""
    try:
        return path.is_file()
    except OSError:
        return False


def resolve_ratification_path() -> Path:
    """Resolve the ratification artifact, honouring `MAEZO_AMH_INBOX_RATIFICATION`.

    Fail-closed in both directions: an env override pointing at a missing file raises immediately
    (no fallback), and an unresolvable default raises rather than returning a path that does not
    exist — the message names every candidate tried, which is what an operator debugging a
    container needs.

    Raises:
        AmhInboxUnavailableError: the override is unresolvable/missing, or no candidate exists.
    """
    override = os.environ.get(INBOX_RATIFICATION_PATH_ENV)
    if override:
        # Broad guard, deliberately: this is three stdlib calls on an operator-supplied string and
        # contains none of this module's logic, so it cannot mask a bug of ours — while its raise
        # set genuinely is not enumerable (`expanduser()` raises RuntimeError for `~nosuchuser`,
        # `resolve()` raises ValueError for an embedded NUL and OSError for an overlong component).
        try:
            path = Path(override).expanduser().resolve()
        except Exception as exc:
            raise _fail(
                REASON_FILE_NOT_FOUND,
                f"${INBOX_RATIFICATION_PATH_ENV} is not resolvable to a filesystem path "
                f"({type(exc).__name__}: {_bounded(str(exc))})",
            ) from exc
        if not _is_existing_file(path):
            raise _fail(
                REASON_FILE_NOT_FOUND,
                f"ratification artifact not found at {path} (resolved from ${INBOX_RATIFICATION_PATH_ENV})",
            )
        return path

    candidates = _default_ratification_candidates()
    for candidate in candidates:
        if _is_existing_file(candidate):
            return candidate

    raise _fail(
        REASON_PATH_NOT_SET,
        "ratification artifact not found at any default candidate: "
        + ", ".join(str(c) for c in candidates)
        + f" — set ${INBOX_RATIFICATION_PATH_ENV} to {INBOX_RATIFICATION_RELATIVE_PATH}",
    )


def migration_file_path() -> Path:
    """Absolute path of the migration this module's SQL speaks (checkout or wheel)."""
    return _package_root() / "platform" / "migrations" / "versions" / INBOX_MIGRATION_FILENAME


def migration_digest() -> str:
    """sha256 (lowercase hex) of the migration file. The value a DBA writes into the artifact.

    Raises:
        AmhInboxUnavailableError: the migration file is absent or unreadable.
    """
    path = migration_file_path()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _fail(
            REASON_MIGRATION_FILE_NOT_FOUND,
            f"could not read migration {path}: {_bounded(str(exc))}",
        ) from exc


def _looks_like_placeholder(value: str) -> str | None:
    """Return the placeholder marker a value still carries, or None."""
    upper = value.upper()
    for marker in PLACEHOLDER_MARKERS:
        if marker in upper:
            return marker
    return None


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail(REASON_UNREADABLE, f"could not read {path}: {_bounded(str(exc))}") from exc
    except UnicodeDecodeError as exc:
        # NOT covered by OSError: UnicodeDecodeError subclasses ValueError. Without this explicit
        # catch a non-UTF-8 artifact would escape as a raw traceback instead of a typed refusal.
        raise _fail(
            REASON_INVALID_ENCODING,
            f"{path} is not valid UTF-8 ({exc.encoding} decode failed at byte offset "
            f"{exc.start}: {_bounded(exc.reason)}) — re-encode the artifact as UTF-8",
        ) from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise _fail(REASON_INVALID_YAML, f"malformed YAML in {path}: {_bounded(str(exc))}") from exc

    if not isinstance(data, dict):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{path}: root must be a mapping, got {type(data).__name__}",
        )
    return data


def load_inbox_ratification(path: str | Path | None = None) -> InboxRatification:
    """Load and validate the DBA ratification artifact. FAILS CLOSED.

    Path resolution: the explicit `path` argument, else `resolve_ratification_path()`.

    Every failure mode — an absent/unreadable/mis-encoded/malformed file, a schema violation, a
    value still carrying a placeholder marker, `ratificado` that is not the boolean `true`, a
    `dba_review` that is not `APPROVED`, a ratification naming a DIFFERENT migration revision, or
    one whose recorded digest does not match the migration file on disk — raises
    `AmhInboxUnavailableError` with a precise `reason`. Nothing here invents, defaults or infers a
    human value: the only successful outcome is a ratification built entirely from what a DBA
    explicitly wrote to the file.

    The DIGEST check is the reason ratification cannot silently carry over: it binds the human's
    approval to the exact DDL bytes reviewed, so editing migration 0007 after ratification returns
    the inbox to a refusing state until a DBA reviews and re-digests it.

    Raises:
        AmhInboxUnavailableError: always, on any failure mode above.
    """
    artifact_path = Path(path) if path is not None else resolve_ratification_path()
    if not _is_existing_file(artifact_path):
        raise _fail(REASON_FILE_NOT_FOUND, f"ratification artifact not found at {artifact_path}")

    data = _read_yaml_mapping(artifact_path)

    missing = [field for field in REQUIRED_RATIFICATION_FIELDS if field not in data]
    if missing:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{artifact_path}: missing required field(s): {missing}",
        )

    # The boolean field is checked before the string sweep so a pure DRAFT artifact reports the
    # reason a reader expects (`not_ratified`) rather than tripping first on one of its own
    # placeholder strings.
    ratificado = data["ratificado"]
    if ratificado is not True:
        raise _fail(
            REASON_NOT_RATIFIED,
            f"{artifact_path}: ratificado is {ratificado!r}, not the boolean true — this artifact "
            "is a DRAFT and the AMH inbox stays inert until a DBA ratifies it",
        )

    values: dict[str, str] = {}
    for field in REQUIRED_RATIFICATION_FIELDS:
        if field == "ratificado":
            continue
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{artifact_path}: {field} must be a non-empty string, got {type(value).__name__}",
            )
        marker = _looks_like_placeholder(value)
        if marker is not None:
            raise _fail(
                REASON_PLACEHOLDER_VALUE,
                f"{artifact_path}: {field} still carries the placeholder marker {marker!r} "
                f"({_bounded(value)!r}) — a half-filled ratification is not a ratification",
            )
        values[field] = value

    if values["migration_revision"] != INBOX_MIGRATION_REVISION:
        raise _fail(
            REASON_MIGRATION_REVISION_MISMATCH,
            f"{artifact_path}: migration_revision is {values['migration_revision']!r}, but this "
            f"repository speaks revision {INBOX_MIGRATION_REVISION!r}",
        )

    actual_digest = migration_digest()
    if values["migration_sha256"].strip().lower() != actual_digest:
        raise _fail(
            REASON_MIGRATION_DIGEST_MISMATCH,
            f"{artifact_path}: migration_sha256 is {_bounded(values['migration_sha256'])!r} but "
            f"{migration_file_path()} hashes to {actual_digest!r} — the ratification covers DDL "
            "that is no longer on disk; a DBA must review the current migration and re-digest it",
        )

    ratification = InboxRatification(
        status=values["status"],
        ratificado=True,
        dba_review=values["dba_review"],
        dba_reviewer=values["dba_reviewer"],
        dba_review_date=values["dba_review_date"],
        evidence_ref=values["evidence_ref"],
        notes=values["notes"],
        migration_revision=values["migration_revision"],
        migration_sha256=actual_digest,
        review_packet=values["review_packet"],
    )
    logger.info(
        "amh_inbox_ratification_loaded",
        path=str(artifact_path),
        migration_revision=ratification.migration_revision,
        reviewer=ratification.dba_reviewer,
    )
    return ratification


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


class InboxStream(StrEnum):
    """Which port a row settles. Payer-core vocabulary, NOT a wire value — binding this to pinned
    topic names would make a contract major bump a schema change."""

    WORK_ITEM = "work_item"
    CONSENT = "consent"


class InboxStatus(StrEnum):
    """The lifecycle states of `amh_inbox.status`. SETTLED and QUARANTINED are terminal."""

    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    SETTLED = "SETTLED"
    QUARANTINED = "QUARANTINED"


class InboxRecordOutcome(StrEnum):
    """What `record` OBSERVED. Four outcomes because there are four distinguishable states."""

    RECORDED = "recorded"
    """A fresh dedup row was committed. The caller owns processing this event."""

    DUPLICATE = "duplicate"
    """The dedup key was already present, with the SAME tenant and payload hash. A legitimate
    redelivery; the caller must not re-process, and `redelivery_count` was incremented."""

    CONFLICT = "conflict"
    """The dedup key was present but with a DIFFERENT `amh_tenant` or `payload_hash` — the same
    event identity carrying different content or tenancy. Deterministic and unretryable: the
    caller must quarantine (`PortFailureReason.CONTRACT_VIOLATION`). No state was mutated."""

    CONCURRENT = "concurrent"
    """DEFENSIVE. The dedup row could not be read back after `ON CONFLICT DO NOTHING` declined to
    insert it.

    This is deliberately NOT described as the in-flight-writer race it was originally written for,
    because that race resolves elsewhere. Measured on PostgreSQL 16.14: an `INSERT ... ON CONFLICT
    DO NOTHING` whose conflict is another transaction's UNCOMMITTED insert does not return
    immediately — Postgres speculative insertion BLOCKS on that transaction's xid until it ends
    (observed: ~4.4s of wait against a writer holding for 5s). If the other writer commits, the
    follow-up `SELECT` — a fresh snapshot at READ COMMITTED — sees the committed row and the
    outcome is `DUPLICATE`/`CONFLICT`; if it rolls back, this call's own insert proceeds and the
    outcome is `RECORDED`. So at READ COMMITTED (asyncpg's default, and this repository's) that
    race never lands here. Raising the isolation level does not land here either: at REPEATABLE
    READ and SERIALIZABLE the INSERT itself raises `could not serialize access due to concurrent
    update` rather than silently declining (`ExecCheckTupleVisible`).

    What is left reachable is a row that vanished BETWEEN the two statements — i.e. a `DELETE` by a
    writer that is not this repository (this repository issues none; a future MZO-130 pruner would
    be one). The branch is kept because the alternative is guessing, and because an isolation-level
    or SQL change that DID make the state reachable would then fail honestly instead of silently.
    Retrying the same delivery later is the caller's correct response."""


class InboxSettleOutcome(StrEnum):
    """What a lifecycle transition OBSERVED."""

    APPLIED = "applied"
    """The transition was applied by THIS call."""

    ALREADY = "already"
    """The row was already in the requested state. Idempotent replay — `ack`/`nack` are required to
    be idempotent per delivery handle, and this is what makes that true durably."""

    REFUSED_TERMINAL = "refused_terminal"
    """The row is in the OTHER terminal state (settling a quarantined row, or quarantining a
    settled one). Refused: terminal states are not rewritten."""

    NOT_FOUND = "not_found"
    """No row exists for the dedup key. Never silently created — a settlement for an event that was
    never recorded is a caller bug, not something to paper over with an upsert."""


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxRecordResult:
    """The result of `record`: what happened, and the row's state afterwards."""

    outcome: InboxRecordOutcome
    status: InboxStatus | None = None
    redelivery_count: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxQuarantineResult:
    """The result of `mark_quarantined`: what happened, AND the referral actually on the row.

    `InboxSettleOutcome` alone cannot answer the question a second `nack` raises. A row that is
    already `QUARANTINED` returns `ALREADY` whatever reason the caller passed, so a caller
    quarantining for `TIMEOUT` a row already quarantined as `CONTRACT_VIOLATION` would have its
    reason silently dropped and be told the operation succeeded. Returning the STORED reason makes
    that divergence detectable by the caller instead of invisible.

    `stored_reason` is the raw column value (the DDL CHECK constrains it to the `PortFailureReason`
    value set, so it parses — but this type reports what was read, it does not coerce it).
    """

    outcome: InboxSettleOutcome
    requested_reason: PortFailureReason
    stored_reason: str | None = None
    stored_topic: str | None = None

    @property
    def reason_diverged(self) -> bool:
        """True iff the row was ALREADY quarantined under a reason different from the one asked for."""
        return (
            self.outcome is InboxSettleOutcome.ALREADY
            and self.stored_reason is not None
            and self.stored_reason != str(self.requested_reason)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingInboxEntry:
    """One unsettled row from `pending`. Carries no subject reference and no payload."""

    contract_manifest_digest: str
    event_id: str
    amh_tenant: str
    inbox_stream: InboxStream
    event_type: str
    status: InboxStatus
    received_at: datetime
    redelivery_count: int


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

#: Columns `record` writes, in bind order. `status`, `received_at`, `last_seen_at` and
#: `redelivery_count` take their DDL defaults. The FIVE subject-bearing envelope fields are absent
#: here and must stay absent, and there is no payload column at all —
#: `tests/unit/platform/integrations/test_amh_inbox.py` pins both.
_INSERT_COLUMNS: Final[tuple[str, ...]] = (
    "contract_manifest_digest",
    "event_id",
    "amh_tenant",
    "legal_entity",
    "inbox_stream",
    "event_type",
    "canonical_schema_version",
    "idempotency_key",
    "source_product",
    "source_entity",
    "source_position_kind",
    "source_position_value",
    "payload_hash",
    "replay_count",
    "occurred_at",
    "ingested_at",
    "correlation_id",
    "trace_id",
)

_INSERT_SQL: Final[str] = (
    f"INSERT INTO amh_inbox ({', '.join(_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(_INSERT_COLUMNS) + 1))}) "
    "ON CONFLICT (contract_manifest_digest, event_id) DO NOTHING "
    "RETURNING status, redelivery_count"
)

# Read-and-lock the conflicting row so the consistency check and the redelivery increment cannot
# interleave with another writer's transition.
_SELECT_FOR_UPDATE_SQL: Final[str] = (
    "SELECT status, amh_tenant, payload_hash, redelivery_count FROM amh_inbox "
    "WHERE contract_manifest_digest = $1 AND event_id = $2 FOR UPDATE"
)

_BUMP_REDELIVERY_SQL: Final[str] = (
    "UPDATE amh_inbox SET redelivery_count = redelivery_count + 1, last_seen_at = now() "
    "WHERE contract_manifest_digest = $1 AND event_id = $2 "
    "RETURNING status, redelivery_count"
)

_SELECT_STATUS_SQL: Final[str] = (
    "SELECT status FROM amh_inbox WHERE contract_manifest_digest = $1 AND event_id = $2"
)

_EXISTS_SQL: Final[str] = "SELECT 1 FROM amh_inbox WHERE contract_manifest_digest = $1 AND event_id = $2"

# Conditional transitions. The `WHERE status IN (...)` clause is what makes the lifecycle
# monotonic without a trigger: a terminal row simply does not match, and the zero-row result is
# then disambiguated by reading the current status.
_MARK_PROCESSED_SQL: Final[str] = (
    "UPDATE amh_inbox SET status = 'PROCESSED', processed_at = now(), last_seen_at = now() "
    "WHERE contract_manifest_digest = $1 AND event_id = $2 AND status = 'RECEIVED' "
    "RETURNING status"
)

_MARK_SETTLED_SQL: Final[str] = (
    "UPDATE amh_inbox SET status = 'SETTLED', settled_at = now(), last_seen_at = now() "
    "WHERE contract_manifest_digest = $1 AND event_id = $2 "
    "AND status IN ('RECEIVED', 'PROCESSED') "
    "RETURNING status"
)

_MARK_QUARANTINED_SQL: Final[str] = (
    "UPDATE amh_inbox SET status = 'QUARANTINED', quarantined_at = now(), last_seen_at = now(), "
    "quarantine_reason = $3, quarantine_topic = $4 "
    "WHERE contract_manifest_digest = $1 AND event_id = $2 "
    "AND status IN ('RECEIVED', 'PROCESSED') "
    "RETURNING status, quarantine_reason, quarantine_topic"
)

# Read the referral a zero-row quarantine UPDATE did NOT overwrite, so the caller can see that its
# reason was not the one stored rather than being told `ALREADY` and nothing else.
_SELECT_QUARANTINE_STATE_SQL: Final[str] = (
    "SELECT status, quarantine_reason, quarantine_topic FROM amh_inbox "
    "WHERE contract_manifest_digest = $1 AND event_id = $2"
)

_PENDING_SQL: Final[str] = (
    "SELECT contract_manifest_digest, event_id, amh_tenant, inbox_stream, event_type, status, "
    "received_at, redelivery_count FROM amh_inbox "
    "WHERE amh_tenant = $1 AND status IN ('RECEIVED', 'PROCESSED') "
    "ORDER BY received_at ASC LIMIT $2"
)


# ---------------------------------------------------------------------------
# Envelope projection (pure — the ONE place PHI exclusion is enforced)
# ---------------------------------------------------------------------------


def _require_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.{field} must be a non-empty string, got {type(value).__name__}",
        )
    return value


def _require_aware(field: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.{field} must be a datetime, got {type(value).__name__}",
        )
    # A naive datetime bound to `timestamptz` is silently interpreted in the session's TimeZone,
    # shifting the instant by whatever the server happens to be set to. Refusing is the only honest
    # option: the wrong instant on a settlement record is unrecoverable.
    #
    # The guard is `Exception` deliberately: `utcoffset()` calls into a caller-supplied `tzinfo`,
    # whose raise set is not enumerable (a hostile or buggy tzinfo may raise anything at all), while
    # this function's declared contract is `AmhInboxUnavailableError`. The body is one stdlib call
    # and none of this module's logic, so the broad catch cannot mask a bug of ours — the same
    # reasoning `maezo.adapters.amh.mapping` reached after four foreign-exception escapes.
    try:
        offset = value.utcoffset()
    except Exception as exc:
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.{field}.tzinfo raised on utcoffset() ({type(exc).__name__}: {_bounded(str(exc))})",
        ) from exc
    if offset is None:
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.{field} is timezone-naive; a naive value bound to timestamptz silently "
            "adopts the server's TimeZone and records the wrong instant",
        )
    return value


def _stored_row(envelope: CanonicalEnvelope, stream: InboxStream) -> tuple[object, ...]:
    """Project a canonical envelope onto the 18 stored columns, in `_INSERT_COLUMNS` order.

    THE PHI FENCE. This is the only function in the repository that reads envelope fields. It reads
    16 of the envelope's 28 — all non-subject-linkable — and fills 17 columns with them
    (`source_position` supplies both `source_position_kind` and `source_position_value`); the 18th
    column, `inbox_stream`, comes from the `stream` argument rather than from the envelope.

    The FIVE subject-bearing fields — `protected_source_record_ref`, `portable_subject_ref`,
    `amh_mpi_ref`, `beneficiary_ref`, `consent_decision_ref` — are never read, so they cannot reach
    a bind parameter, a log line or a stored row. The event body is not a sixth exclusion:
    `CanonicalEnvelope` has no `payload` field and `amh_inbox` has no payload column.

    Pure and DB-free, so the fence is testable without Postgres — including with a planted
    sentinel in every excluded field.
    """
    return (
        _require_text("contract_manifest_digest", envelope.contract_manifest_digest),
        _require_text("event_id", envelope.event_id),
        _require_text("amh_tenant", envelope.amh_tenant),
        _require_text("legal_entity", envelope.legal_entity),
        str(stream),
        _require_text("event_type", envelope.event_type),
        _require_text("canonical_schema_version", envelope.canonical_schema_version),
        _require_text("idempotency_key", envelope.idempotency_key),
        _require_text("source_product", envelope.source_product),
        _require_text("source_entity", envelope.source_entity),
        _require_text("source_position.kind", envelope.source_position.kind),
        _require_text("source_position.value", envelope.source_position.value),
        _require_text("payload_hash", envelope.payload_hash),
        _require_replay_count(envelope.replay_count),
        _require_aware("occurred_at", envelope.occurred_at),
        _require_aware("ingested_at", envelope.ingested_at),
        _require_text("correlation_id", envelope.correlation_id),
        _require_text("trace_id", envelope.trace_id),
    )


def _require_replay_count(value: object) -> int:
    # `bool` is a subclass of `int`; `replay_count: True` would otherwise bind as 1 and record a
    # replay count nobody sent.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.replay_count must be an int, got {type(value).__name__}",
        )
    if value < 0:
        raise _fail(
            REASON_INVALID_ENVELOPE,
            f"envelope.replay_count must be >= 0, got {value}",
        )
    return value


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class AmhInbox(Protocol):
    """The durable-settlement surface the future MZO-050b consumer will call.

    Structural (`typing.Protocol`), so a consumer depends on the SHAPE and an in-memory fake or a
    replay harness satisfies it without inheriting anything. Every method reports what it OBSERVED
    — none of them has an "assume it worked" branch.

    Structural is not a stylistic preference here, it is the only thing that can work: this
    Protocol lives under `maezo.platform`, which `maezo.adapters.amh` is fenced from importing, so
    the MZO-050b consumer can be HANDED an implementation by a composition root but can never name
    this type. See the module docstring for the port-side follow-up.
    """

    async def record(self, envelope: CanonicalEnvelope, *, stream: InboxStream) -> InboxRecordResult: ...

    async def is_duplicate(self, *, contract_manifest_digest: str, event_id: str) -> bool: ...

    async def mark_processed(self, *, contract_manifest_digest: str, event_id: str) -> InboxSettleOutcome: ...

    async def mark_settled(self, *, contract_manifest_digest: str, event_id: str) -> InboxSettleOutcome: ...

    async def mark_quarantined(
        self,
        *,
        contract_manifest_digest: str,
        event_id: str,
        reason: PortFailureReason,
        quarantine_topic: str | None = None,
    ) -> InboxQuarantineResult: ...

    async def pending(self, *, amh_tenant: str, limit: int = 100) -> tuple[PendingInboxEntry, ...]: ...


class PostgresAmhInbox:
    """Durable AMH inbox over Postgres (satisfies `AmhInbox`). REQUIRES a DBA ratification.

    Lazy asyncpg pool, `search_path` pinned to the tenant's schema on EVERY acquire — `setup=`,
    not `init=`, because asyncpg RESETs all session state on connection release, so an `init`-only
    search_path silently reverts on the second use of a reused connection (the defect
    `PostgresAuditSink` fixed and `PostgresIdempotencyStore` inherited).

    The `ratification` argument is a required, keyword-only capability, not a flag: there is no
    constructor signature that produces a working repository without one, and `InboxRatification`
    itself cannot exist with `ratificado` false or `dba_review` unapproved.

    `__init__` is the SECOND lock, and it re-checks FOUR properties, not two. The two human
    switches catch an instance forged past `__post_init__` (e.g. by `object.__setattr__` on the
    frozen dataclass). The `migration_revision` and `migration_sha256` re-checks catch the other
    half: `InboxRatification` is a dataclass, so those two fields are only ever validated against
    the migration on disk by `load_inbox_ratification` — a caller who builds the dataclass DIRECTLY
    (a plausible test double, a hand-rolled composition root) supplies whatever revision and digest
    it likes and never touches the artifact. That was live-provable: before this re-check,
    `InboxRatification(migration_revision="0001", migration_sha256="deadbeef", ...)` produced a
    repository that recorded and settled rows against real Postgres. Re-checking here makes the
    digest binding a property of the REPOSITORY rather than of one route into it — the artifact
    remains the switch, and `build_amh_inbox_repository` remains the only path that binds an
    approval to a FILE.
    """

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        ratification: InboxRatification,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        if ratification.ratificado is not True or ratification.dba_review != APPROVED_DBA_REVIEW:
            raise _fail(
                REASON_UNRATIFIED_REPOSITORY,
                "PostgresAmhInbox was handed a ratification that does not carry a DBA approval "
                f"(ratificado={ratification.ratificado!r}, "
                f"dba_review={_bounded(str(ratification.dba_review))!r})",
            )
        if ratification.migration_revision != INBOX_MIGRATION_REVISION:
            raise _fail(
                REASON_MIGRATION_REVISION_MISMATCH,
                "PostgresAmhInbox was handed a ratification for migration revision "
                f"{_bounded(str(ratification.migration_revision))!r}, but this repository speaks "
                f"revision {INBOX_MIGRATION_REVISION!r}",
            )
        # Recomputed from disk on every construction, deliberately: the value carried on the
        # dataclass is only as trustworthy as whoever built it, while this one is the DDL the
        # process would actually be operating against.
        actual_digest = migration_digest()
        if str(ratification.migration_sha256).strip().lower() != actual_digest:
            raise _fail(
                REASON_MIGRATION_DIGEST_MISMATCH,
                "PostgresAmhInbox was handed a ratification whose migration_sha256 is "
                f"{_bounded(str(ratification.migration_sha256))!r}, but {migration_file_path()} "
                f"hashes to {actual_digest!r} — the approval does not cover the DDL on disk",
            )
        # Validates the tenant as a schema identifier (anti-injection) — the same rule the audit
        # sink, the checkpointer and the A2A idempotency store use. This string is interpolated
        # into `SET search_path`.
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool
        self._ratification = ratification

    @property
    def ratification(self) -> InboxRatification:
        """The ratification this repository operates under (for audit/diagnostics)."""
        return self._ratification

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                # D4-02 / R-108 (OWNER-DECISIONS-REGISTER, opcao B): min_size/max_size abaixo sao
                # DEFAULT DELIBERADO, nao esquecimento. O dono decidiu MANTER estes valores e
                # documenta-los em vez de expor min/max por ambiente em values.yaml, adiando essa
                # exposicao para depois de SC-05 — sem a aritmetica agregada de conexoes (pools x
                # processos x replicas x tenants vs max_connections do Aurora) que SC-05 produz,
                # expor knobs transferiria para quem preencher o valor o risco de estourar o teto do
                # Aurora. Ha' QUATRO sitios com este MESMO default nesta arvore — a2a/idempotency.py,
                # a2a/outbox.py, platform/integrations/amh_inbox.py, gateway/audit_postgres.py —
                # mude os quatro juntos se mudar aqui. REABRE quando SC-05 landar (ver
                # docs/review-queue.md, secao "D4-02 — pools asyncpg sem tuning por ambiente").
                min_size=1,
                max_size=10,
                setup=self._set_search_path,
            )
        return self._pool

    async def _set_search_path(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by `schema_for_tenant()` in `__init__` (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def record(self, envelope: CanonicalEnvelope, *, stream: InboxStream) -> InboxRecordResult:
        """Idempotently record an inbound envelope's dedup row.

        Returns `RECORDED` for a fresh row, `DUPLICATE` for a consistent redelivery (whose
        `redelivery_count` this call increments), `CONFLICT` when the dedup key already carries a
        different `amh_tenant` or `payload_hash` (no state mutated — the caller must quarantine),
        and `CONCURRENT` only in the defensive case described on `InboxRecordOutcome.CONCURRENT`.

        **Contention, stated rather than left to be discovered.** A conflicting insert that is
        still UNCOMMITTED does not make this call return quickly: Postgres speculative insertion
        BLOCKS until the other transaction commits or rolls back, and this pool sets no
        `lock_timeout` and no `command_timeout`, so the wait is unbounded and is bounded in
        practice only by the other replica's transaction. Whether to cap it is the DBA's call —
        see decision D-6 in `docs/reviews/mzo-060-dba-review-packet.md`.
        """
        params = _stored_row(envelope, stream)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchrow(_INSERT_SQL, *params)
            if inserted is not None:
                return InboxRecordResult(
                    outcome=InboxRecordOutcome.RECORDED,
                    status=InboxStatus(inserted["status"]),
                    redelivery_count=int(inserted["redelivery_count"]),
                )

            existing = await conn.fetchrow(
                _SELECT_FOR_UPDATE_SQL, envelope.contract_manifest_digest, envelope.event_id
            )
            if existing is None:
                # DEFENSIVE, not the in-flight race this used to claim: at READ COMMITTED the
                # INSERT above has already WAITED out any conflicting transaction, and this SELECT
                # takes a fresh snapshot, so a concurrent writer resolves as DUPLICATE/CONFLICT or
                # as RECORDED. Reaching here means the row went away between the two statements —
                # a DELETE by something that is not this repository. Reported, never guessed at.
                logger.warning(
                    "amh_inbox_record_concurrent",
                    contract_manifest_digest=envelope.contract_manifest_digest,
                    tenant=self._tenant,
                )
                return InboxRecordResult(outcome=InboxRecordOutcome.CONCURRENT)

            if (
                existing["amh_tenant"] != envelope.amh_tenant
                or existing["payload_hash"] != envelope.payload_hash
            ):
                # Same event identity, different content or tenancy. Deterministic and
                # unretryable; nothing is mutated, so the row stays exactly as first recorded.
                logger.error(
                    "amh_inbox_record_conflict",
                    contract_manifest_digest=envelope.contract_manifest_digest,
                    tenant=self._tenant,
                    tenant_matches=existing["amh_tenant"] == envelope.amh_tenant,
                    payload_hash_matches=existing["payload_hash"] == envelope.payload_hash,
                )
                return InboxRecordResult(
                    outcome=InboxRecordOutcome.CONFLICT,
                    status=InboxStatus(existing["status"]),
                    redelivery_count=int(existing["redelivery_count"]),
                )

            bumped = await conn.fetchrow(
                _BUMP_REDELIVERY_SQL, envelope.contract_manifest_digest, envelope.event_id
            )
            if bumped is None:  # pragma: no cover - the row is locked FOR UPDATE above
                return InboxRecordResult(outcome=InboxRecordOutcome.CONCURRENT)
            return InboxRecordResult(
                outcome=InboxRecordOutcome.DUPLICATE,
                status=InboxStatus(bumped["status"]),
                redelivery_count=int(bumped["redelivery_count"]),
            )

    async def is_duplicate(self, *, contract_manifest_digest: str, event_id: str) -> bool:
        """True iff a dedup row already exists for `(contract_manifest_digest, event_id)`."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            found = await conn.fetchval(_EXISTS_SQL, contract_manifest_digest, event_id)
        return found is not None

    async def _transition(
        self,
        sql: str,
        *,
        contract_manifest_digest: str,
        event_id: str,
        target: InboxStatus,
    ) -> InboxSettleOutcome:
        """Apply a conditional transition and DISAMBIGUATE a zero-row result honestly.

        Only for the no-extra-parameter transitions (`mark_processed`, `mark_settled`).
        `mark_quarantined` binds a reason and topic and reports the STORED referral, so it has its
        own body rather than threading extra parameters through here.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            updated = await conn.fetchrow(sql, contract_manifest_digest, event_id)
            if updated is not None:
                return InboxSettleOutcome.APPLIED
            current = await conn.fetchval(_SELECT_STATUS_SQL, contract_manifest_digest, event_id)
        if current is None:
            return InboxSettleOutcome.NOT_FOUND
        if current == str(target):
            return InboxSettleOutcome.ALREADY
        return InboxSettleOutcome.REFUSED_TERMINAL

    async def mark_processed(self, *, contract_manifest_digest: str, event_id: str) -> InboxSettleOutcome:
        """RECEIVED -> PROCESSED. Not a settlement: it does NOT licence `ack`."""
        return await self._transition(
            _MARK_PROCESSED_SQL,
            contract_manifest_digest=contract_manifest_digest,
            event_id=event_id,
            target=InboxStatus.PROCESSED,
        )

    async def mark_settled(self, *, contract_manifest_digest: str, event_id: str) -> InboxSettleOutcome:
        """RECEIVED|PROCESSED -> SETTLED. THE durable settlement that licences `ack` to report ok.

        `ALREADY` on a second call is what makes `ack` idempotent per delivery handle; a quarantined
        row returns `REFUSED_TERMINAL` and is never rewritten into a settlement.
        """
        return await self._transition(
            _MARK_SETTLED_SQL,
            contract_manifest_digest=contract_manifest_digest,
            event_id=event_id,
            target=InboxStatus.SETTLED,
        )

    async def mark_quarantined(
        self,
        *,
        contract_manifest_digest: str,
        event_id: str,
        reason: PortFailureReason,
        quarantine_topic: str | None = None,
    ) -> InboxQuarantineResult:
        """RECEIVED|PROCESSED -> QUARANTINED, recording the referral AND reporting what is stored.

        `reason` is a closed `PortFailureReason` token and `quarantine_topic` is the pinned
        quarantine topic NAME the caller routed to. Nothing else is recorded: no payload, no
        upstream error body, no subject reference (ADR-0037 immutable prohibition #5).

        Terminal rows are never rewritten, so a row already `QUARANTINED` yields `ALREADY` and this
        call's `reason` is DISCARDED. That is correct — quarantine is terminal — but reporting only
        `ALREADY` would hide it, so the result also carries the reason and topic actually on the
        row (`InboxQuarantineResult.reason_diverged` is the one-line test). A divergence means two
        different `nack`s classified the same event differently; it is logged here and is the
        caller's to reconcile, not this repository's to resolve by overwriting.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            updated = await conn.fetchrow(
                _MARK_QUARANTINED_SQL,
                contract_manifest_digest,
                event_id,
                str(reason),
                quarantine_topic,
            )
            if updated is not None:
                return InboxQuarantineResult(
                    outcome=InboxSettleOutcome.APPLIED,
                    requested_reason=reason,
                    stored_reason=updated["quarantine_reason"],
                    stored_topic=updated["quarantine_topic"],
                )
            current = await conn.fetchrow(_SELECT_QUARANTINE_STATE_SQL, contract_manifest_digest, event_id)

        if current is None:
            return InboxQuarantineResult(outcome=InboxSettleOutcome.NOT_FOUND, requested_reason=reason)
        if current["status"] != str(InboxStatus.QUARANTINED):
            # The other terminal state (SETTLED). Its CHECK constraints keep the quarantine columns
            # NULL, so there is nothing stored to report — only the refusal.
            return InboxQuarantineResult(
                outcome=InboxSettleOutcome.REFUSED_TERMINAL,
                requested_reason=reason,
                stored_reason=current["quarantine_reason"],
                stored_topic=current["quarantine_topic"],
            )

        result = InboxQuarantineResult(
            outcome=InboxSettleOutcome.ALREADY,
            requested_reason=reason,
            stored_reason=current["quarantine_reason"],
            stored_topic=current["quarantine_topic"],
        )
        if result.reason_diverged:
            logger.warning(
                "amh_inbox_quarantine_reason_divergent",
                contract_manifest_digest=contract_manifest_digest,
                tenant=self._tenant,
                stored_reason=result.stored_reason,
                requested_reason=str(reason),
            )
        return result

    async def pending(self, *, amh_tenant: str, limit: int = 100) -> tuple[PendingInboxEntry, ...]:
        """Oldest-first scan of rows that are recorded but NOT terminal, for one contract tenant."""
        if limit <= 0:
            raise _fail(REASON_INVALID_ENVELOPE, f"pending(limit=) must be positive, got {limit}")
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PENDING_SQL, amh_tenant, limit)
        return tuple(
            PendingInboxEntry(
                contract_manifest_digest=row["contract_manifest_digest"],
                event_id=row["event_id"],
                amh_tenant=row["amh_tenant"],
                inbox_stream=InboxStream(row["inbox_stream"]),
                event_type=row["event_type"],
                status=InboxStatus(row["status"]),
                received_at=row["received_at"],
                redelivery_count=int(row["redelivery_count"]),
            )
            for row in rows
        )

    async def aclose(self) -> None:
        """Close the pool (if this repository opened it)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def build_amh_inbox_repository(
    *,
    dsn: str,
    tenant: str,
    ratification_path: str | Path | None = None,
    pool: asyncpg.Pool | None = None,
) -> PostgresAmhInbox:
    """THE sanctioned way to obtain an AMH inbox. Refuses unless a DBA ratified the schema.

    Loads the ratification artifact FIRST and lets its refusal propagate — so a caller that never
    checks anything still cannot get a repository out of an unratified deployment. Flipping the
    artifact to `ratificado: true` + `dba_review: APPROVED` (with the matching migration digest)
    activates this factory with NO code change and no redeploy of new code.

    Raises:
        AmhInboxUnavailableError: the artifact is absent, malformed, still a draft, still carries
            placeholders, or ratifies a different migration/digest than the one on disk.
        ValueError: `tenant` is not a valid Postgres schema identifier (`schema_for_tenant`).
    """
    ratification = load_inbox_ratification(ratification_path)
    return PostgresAmhInbox(dsn=dsn, tenant=tenant, ratification=ratification, pool=pool)


__all__ = [
    "APPROVED_DBA_REVIEW",
    "INBOX_MIGRATION_FILENAME",
    "INBOX_MIGRATION_REVISION",
    "INBOX_RATIFICATION_PATH_ENV",
    "INBOX_RATIFICATION_RELATIVE_PATH",
    "MAX_ECHOED_CHARS",
    "PLACEHOLDER_MARKERS",
    "RATIFIED_STATUS",
    "REASON_DBA_REVIEW_PENDING",
    "REASON_FILE_NOT_FOUND",
    "REASON_INVALID_ENCODING",
    "REASON_INVALID_ENVELOPE",
    "REASON_INVALID_SCHEMA",
    "REASON_INVALID_YAML",
    "REASON_MIGRATION_DIGEST_MISMATCH",
    "REASON_MIGRATION_FILE_NOT_FOUND",
    "REASON_MIGRATION_REVISION_MISMATCH",
    "REASON_NOT_RATIFIED",
    "REASON_PATH_NOT_SET",
    "REASON_PLACEHOLDER_VALUE",
    "REASON_UNRATIFIED_REPOSITORY",
    "REASON_UNREADABLE",
    "REQUIRED_RATIFICATION_FIELDS",
    "AmhInbox",
    "AmhInboxUnavailableError",
    "InboxQuarantineResult",
    "InboxRatification",
    "InboxRecordOutcome",
    "InboxRecordResult",
    "InboxSettleOutcome",
    "InboxStatus",
    "InboxStream",
    "PendingInboxEntry",
    "PostgresAmhInbox",
    "build_amh_inbox_repository",
    "load_inbox_ratification",
    "migration_digest",
    "migration_file_path",
    "resolve_ratification_path",
]
