"""Audit Chain — hash-linked, append-only, anti-fork (ADR-0007, DL-0018).

Every effect on the world is recorded in a SHA-256 hash chain where each
record includes the previous record's hash. DL-0018 mandates the chain be
NON-PARTITIONED: UNIQUE(prev_record_hash) is enforced within the table (see
`platform/migrations/versions/0002_audit_chain.py`), ensuring a single,
unforgeable audit trail per schema (the platform is schema-per-tenant —
`platform/migrations/env.py` — so in practice this is one unforgeable trail
per tenant).

AuditRecord carries the ADR-0007 tuple: tenant_id, agent_id, agent_version,
action, decision, decision_basis ("details"), dmn_versions, model_id,
prompt_version, input_hash, timestamp, prev_hash.

T1.10: this module defines the in-memory reference sink (`AuditSink`, used by
tests and as a dev/fallback implementation). The durable Postgres-backed sink
lives in `maezo.gateway.audit_postgres.PostgresAuditSink` — it satisfies the
same record/hash contract defined here (chaining is owned by the record, not
the sink) so the two are interchangeable for anything that only needs
`emit()`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Sentinel previous-hash for the first record in a chain. A real (non-null,
# non-empty) sentinel — rather than SQL NULL — is used deliberately: the
# `audit_chain` table's anti-fork guard is `UNIQUE(prev_record_hash)`, and
# that column is nullable (see 0002_audit_chain.py). Postgres UNIQUE
# constraints treat NULL as distinct from every other NULL, so two
# concurrent genesis inserts (both prev_record_hash = NULL) would NOT
# violate the constraint — a silent fork at the very start of the chain.
# Using a fixed, real sentinel value closes that gap: a second genesis
# insert collides with the first under UNIQUE, exactly like every other
# fork attempt. See `audit_postgres.py` for the transactional advisory-lock
# guard that is the *primary* defense; this sentinel is belt-and-suspenders.
GENESIS_PREV_HASH: str = "0" * 64


def hash_input(payload: object) -> str:
    """SHA-256 of a canonical (sorted-key) JSON encoding of `payload`.

    PHI must never enter the audit chain raw. This is a convenience helper
    for callers that want to hash a raw tool-call input themselves before
    building an `AuditRecord`; `AuditRecord.details` is expected to already
    be safe-to-persist structured data (it is hashed again automatically —
    see `compute_input_hash` — to populate the `input_hash` column).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_jsonb(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a payload to the exact shape it will have after a Postgres jsonb round-trip.

    H1 fix (R1 verification cycle 1, T1.10): `record_hash` used to be computed from the
    in-memory object at write time, while `verify_chain()` recomputes it from the
    jsonb-ROUND-TRIPPED row. Postgres jsonb stores numbers as `numeric`, which normalizes some
    values differently than Python's `json` module — reproduced with `{"x": -0.0}`: stored as
    `0.0`, so recomputation produced a different hash and a CLEAN one-record chain reported
    tamper (valid=False). A tamper detector that false-alarms trains on-call to ignore it, so
    the fix is to hash what we store: every `AuditRecord` jsonb field is canonicalized through
    THIS function at construction, guaranteeing the invariant *bytes hashed at write == bytes
    recomputed at verify* for every value Postgres jsonb can round-trip.

    The normalization (each rule verified empirically against the compose `pgvector/pgvector:pg16`
    via `SELECT $1::jsonb::text` — see the H1-regression tests in test_audit_postgres.py):

    1. Structural: `json.loads(json.dumps(payload, sort_keys=True, default=str))` first, so the
       in-memory shape matches what `json.loads` on the stored text will produce (tuples→lists,
       non-str keys→str, non-JSON objects→their `default=str` form).
    2. `-0.0` → `0.0`: Postgres `numeric` has no signed zero.
    3. Floats whose `repr` uses exponent notation (only |v| ≥ 1e16; all such doubles are
       integral) → `int(Decimal(repr(v)))`: numeric prints them as plain digit strings with no
       fractional part, which `json.loads` returns as *int*. `Decimal(repr(v))` — not `int(v)` —
       because Postgres parses the shortest-repr STRING with decimal semantics: for `1e308`
       numeric stores exactly 10**308, while `int(1e308)` is the double's exact binary value
       (1000...1097906362944... — a different integer).
    4. Non-finite floats (NaN/±Infinity) raise ValueError (fail-closed): they are not valid JSON
       and Postgres would reject the INSERT anyway — rejecting at the AuditRecord boundary gives
       the caller a clear error instead of an opaque DB failure, and keeps the in-memory
       `AuditSink` (which has no DB to reject for it) from accepting a record the durable sink
       could never persist.

    Small-exponent floats (e.g. `1e-05`, `5e-324`) need no special-casing: numeric prints them as
    plain decimals with a fractional part, `json.loads` parses that back to the identical double,
    and `json.dumps` re-emits the identical shortest repr (verified empirically).

    Idempotent: a payload loaded back from a stored jsonb row passes through unchanged, so
    `verify_chain()`'s record reconstruction re-applies it harmlessly.
    """
    shaped = json.loads(json.dumps(payload, sort_keys=True, default=str))
    normalized: dict[str, Any] = _normalize_jsonb_value(shaped, path="$")
    return normalized


def _normalize_jsonb_value(value: Any, *, path: str) -> Any:
    """Recursive worker for `canonicalize_jsonb` (rules 2-4). `path` is for error messages."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"non-finite float ({value!r}) at {path} cannot be represented in the audit "
                "chain's jsonb columns (fail-closed at the AuditRecord boundary)"
            )
        if value == 0.0:
            return 0.0  # rule 2: numeric has no signed zero; -0.0 round-trips as 0.0
        if "e" in repr(value):
            # rule 3: exponent-form repr (|v| >= 1e16 → always integral as a double, OR
            # |v| < 1e-4 → never integral). Only the integral case round-trips as int.
            as_decimal = Decimal(repr(value))
            if as_decimal == as_decimal.to_integral_value():
                return int(as_decimal)
        return value
    if isinstance(value, dict):
        return {key: _normalize_jsonb_value(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_jsonb_value(item, path=f"{path}[{i}]") for i, item in enumerate(value)]
    return value


@dataclass
class AuditRecord:
    """A single entry in the audit hash chain (ADR-0007 tuple).

    Attributes:
        agent_id: Identifier of the agent that performed the action.
        tenant_id: Owning tenant (maps to the `tenant_id` column and to the
            schema-per-tenant advisory lock used by PostgresAuditSink).
        agent_version: Version of the emitting agent (non-repudiation —
            ADR-0007 requires "sob-qual-versao").
        action: The action being audited.
        decision: PEP decision (ALLOW/DENY/REQUIRE_HUMAN).
        details: Structured decision basis (must be JSON-serializable, no
            raw PHI). Persisted as the `decision_basis` jsonb column; also
            hashed (see `compute_input_hash`) into the `input_hash` column.
        timestamp: UTC timestamp of the event.
        dmn_versions: DMN table versions consulted for this decision.
            T1.5 (ADR-0028) closes the precondition this TODO used to name: the migrated
            workers (`pagto`, `inadimplencia`, `contas`, `recurso`, `credenciamento`,
            `adequacao`, `ans_submit`, `lgpd` — see `docs/adr/0028-dmn-evaluation-engine-side.md`
            §"Migration plan") no longer hand-fork decision-table rules in Python; they call the
            real engine via `maezo.tools.workers.dmn_transport.DmnTransport.evaluate(...)`,
            which returns a `DmnVersion(key, id, version, deployment_id)` alongside the result
            rows (fail-closed: never `"unknown"`, ADR-0028 §2). A caller building an
            `AuditRecord` for a DMN-driven decision populates this field with
            ``{key: dmn_version.to_audit_dict()}`` per table consulted — see
            `DmnVersion.to_audit_dict()` (`dmn_transport.py`) for the exact
            `{"version": ..., "id": ..., "deploymentId": ...}` shape, and
            `tests/integration/dmn/test_dmn_golden_parity.py` /
            `tests/unit/gateway/test_audit_dmn_versions.py` for a populated example evaluated
            against the live engine. STALE as of T1.5 (2026-07-17, PR #56, when this paragraph was
            written): "no production code path yet constructs an `AuditRecord` FROM a worker/agent
            decision" was true then but not since T1.10, one day later. Real production callers
            today: `WorkerHarness._build_audit_record`/`_emit_audit` (worker completion, T-C,
            `harness.py:1201`/`:1232`) and `_build_refusal_record`/`_audit_guard_refusal` (guard
            refusal, T-E, PR #131, `harness.py:1264`/`:1300`) — both fed by the real
            `collect_dmn_versions()` collector (`_audit_ctx.py`), so this field carries genuine DMN
            provenance for those records, not `{}`; `A2ADispatcher._audit_delegation`/
            `_audit_delegation_outcome` (A2A delegation, PR #156, `a2a/dispatcher.py:402`/`:483`);
            and `build_start_audit_record` (agent process-start provenance, T-C2,
            `mcp_cibseven/transport.py:514`, emitted at `:599`). A2A delegation records never set
            `dmn_versions` (no DMN table is consulted for a delegation decision) — for those records
            the `NOT NULL DEFAULT '{}'::jsonb` column default remains the correct, honest value; it
            is no longer, though, evidence that nothing calls `emit()` in production.
        model_id: LLM model identifier, when the action involved one.
        prompt_version: Prompt template version, when applicable.
        prev_hash: SHA-256 hash of the previous record in the chain.
            `GENESIS_PREV_HASH` for the first record of a chain.
        record_hash: SHA-256 hash of this record (computed after
            construction, and recomputed by the sink once the real
            `prev_hash` is known).
    """

    agent_id: str
    tenant_id: str
    agent_version: str
    action: str
    decision: str
    details: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    dmn_versions: dict[str, Any] = field(default_factory=dict)
    model_id: str | None = None
    prompt_version: str | None = None
    prev_hash: str = field(default=GENESIS_PREV_HASH)
    record_hash: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Canonicalize round-trip-sensitive fields, then compute the record hash.

        H1 fix (R1 verification cycle 1): the hash must be computed over the exact
        representation that survives storage-and-readback, otherwise `verify_chain()`
        false-alarms on clean chains (see `canonicalize_jsonb`). Two field classes need it:

        - jsonb fields (`details`, `dmn_versions`): normalized via `canonicalize_jsonb`.
        - `timestamp`: Postgres `timestamptz` stores an absolute instant and returns it in
          UTC — a record hashed with a non-UTC (or naive) timestamp would recompute
          differently after readback. Aware timestamps are normalized to UTC (identical
          instant, identical `isoformat()` after round-trip); naive ones are rejected
          (fail-closed — a naive timestamp's actual instant is ambiguous, and guessing a
          timezone here would fabricate audit evidence).
        """
        if self.timestamp.tzinfo is None:
            raise ValueError(
                "AuditRecord.timestamp must be timezone-aware (naive timestamps are ambiguous "
                "and would break hash verification after the timestamptz round-trip)"
            )
        self.timestamp = self.timestamp.astimezone(UTC)
        self.details = canonicalize_jsonb(self.details)
        self.dmn_versions = canonicalize_jsonb(self.dmn_versions)
        self.record_hash = self._compute_hash()

    def compute_input_hash(self) -> str:
        """SHA-256 of the canonical `details` payload (the `input_hash` column).

        Deterministic and pure — callers never set this directly; it is
        derived from `details` so the column can never silently drift from
        the content it is supposed to attest to.
        """
        return hash_input(self.details)

    def _compute_hash(self) -> str:
        """Compute SHA-256 hash of this record based on its content fields.

        The hash covers every persisted column except `record_hash` itself
        (that would be circular) and the server-generated `id`/`created_at`.
        """
        payload = json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "tenant_id": self.tenant_id,
                "agent_id": self.agent_id,
                "agent_version": self.agent_version,
                "action": self.action,
                "decision": self.decision,
                "details": self.details,
                "dmn_versions": self.dmn_versions,
                "model_id": self.model_id,
                "prompt_version": self.prompt_version,
                "input_hash": self.compute_input_hash(),
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditSink:
    """In-memory, append-only audit chain with hash-linked integrity.

    This is the reference implementation used by tests and as a dev
    fallback — it holds the entire chain in process memory, so it forks
    from genesis on every restart. It is NOT the production sink: use
    `maezo.gateway.audit_postgres.PostgresAuditSink` for durable,
    fail-closed persistence (T1.10). Both share the same `AuditRecord`
    chaining contract, so they are interchangeable behind an `emit()` call.

    DL-0018: The chain is NON-PARTITIONED — UNIQUE(prev_record_hash) is
    enforced within the table. In production, this is backed by Postgres
    with a UNIQUE constraint on prev_record_hash (schema-per-tenant).
    """

    def __init__(self) -> None:
        """Initialize an empty audit chain."""
        self._chain: list[AuditRecord] = []
        self._last_hash: str = GENESIS_PREV_HASH
        logger.info("audit_sink_initialized")

    @property
    def chain(self) -> list[AuditRecord]:
        """Return the current chain (for inspection/testing only)."""
        return self._chain

    def emit(self, record: AuditRecord) -> str:
        """Append a record to the chain and return its hash.

        Links the record to the previous record via prev_hash.
        The first record (genesis) has prev_hash = GENESIS_PREV_HASH.

        Args:
            record: The audit record to append.

        Returns:
            The SHA-256 hex digest of the appended record.
        """
        # Set the previous hash for chain linking
        record.prev_hash = self._last_hash

        # Recompute hash with the now-set prev_hash
        record.record_hash = record._compute_hash()

        self._chain.append(record)
        self._last_hash = record.record_hash

        logger.debug(
            "audit_record_emitted",
            tenant_id=record.tenant_id,
            agent_id=record.agent_id,
            action=record.action,
            decision=record.decision,
            record_hash=record.record_hash,
        )
        return record.record_hash

    def verify_chain(self) -> bool:
        """Verify the integrity of the entire chain.

        Recomputes hashes for each record and checks prev_hash links.
        Returns True if the chain is intact, False if tampering is detected.

        Returns:
            True if the chain has not been tampered with.
        """
        prev: str = GENESIS_PREV_HASH
        for i, record in enumerate(self._chain):
            # Check prev_hash link
            if record.prev_hash != prev:
                logger.error(
                    "audit_chain_tamper_detected_prev_hash_mismatch",
                    index=i,
                    expected_prev=prev,
                    actual_prev=record.prev_hash,
                )
                return False

            # Recompute the expected hash for this record
            expected_hash = record._compute_hash()
            if record.record_hash != expected_hash:
                logger.error(
                    "audit_chain_tamper_detected_hash_mismatch",
                    index=i,
                    expected=expected_hash,
                    actual=record.record_hash,
                )
                return False

            prev = record.record_hash

        return True
