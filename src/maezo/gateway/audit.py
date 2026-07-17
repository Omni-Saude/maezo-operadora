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
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
            TODO(T1.5): no caller populates this yet — workers still
            re-implement DMN-like logic in Python instead of calling the
            DMN engine, so there is no versioned DMN evaluation to cite.
            Once T1.5 lands (workers call DMN, ADR-0012), wire the real
            versions through here. Until then this is an explicit
            non-null empty structure (`{}`), never a fabricated value —
            the `dmn_versions` column is `NOT NULL DEFAULT '{}'::jsonb`
            precisely to make "no DMN provenance yet" representable
            without lying about it.
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
        """Compute the record hash after fields are set."""
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
