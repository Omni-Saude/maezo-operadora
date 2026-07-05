"""Audit Chain — hash-linked, append-only, anti-fork (ADR-0007, DL-0018).

Every effect on the world is recorded in a SHA-256 hash chain where each
record includes the previous record's hash. DL-0018 mandates the chain be
NON-PARTITIONED: UNIQUE(prev_record_hash) is enforced globally, ensuring
a single, unforgeable audit trail.

AuditRecord carries: timestamp, agent_id, action, decision, details, prev_hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class AuditRecord:
    """A single entry in the audit hash chain.

    Attributes:
        timestamp: UTC timestamp of the event.
        agent_id: Identifier of the agent that performed the action.
        action: The action being audited.
        decision: PEP decision (ALLOW/DENY/REQUIRE_HUMAN).
        details: Additional structured detail (must be JSON-serializable).
        prev_hash: SHA-256 hash of the previous record (None for genesis).
        record_hash: SHA-256 hash of this record (computed after construction).
    """

    agent_id: str
    action: str
    decision: str
    details: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    prev_hash: str | None = field(default=None)
    record_hash: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Compute the record hash after fields are set."""
        self.record_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA-256 hash of this record based on its content fields.

        The hash covers: timestamp, agent_id, action, decision, details, prev_hash.
        The record_hash itself is NOT included in the hash (would be circular).
        """
        payload = json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "agent_id": self.agent_id,
                "action": self.action,
                "decision": self.decision,
                "details": self.details,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditSink:
    """Append-only audit chain with hash-linked integrity.

    Each emit() appends a record to the in-memory chain, linking it to the
    previous record via prev_hash. The chain can be verified via verify_chain()
    to detect tampering.

    DL-0018: The chain is NON-PARTITIONED — UNIQUE(prev_record_hash) is
    enforced globally. In production, this is backed by Postgres with a
    UNIQUE constraint on prev_record_hash.
    """

    def __init__(self) -> None:
        """Initialize an empty audit chain."""
        self._chain: list[AuditRecord] = []
        self._last_hash: str | None = None
        logger.info("audit_sink_initialized")

    @property
    def chain(self) -> list[AuditRecord]:
        """Return the current chain (for inspection/testing only)."""
        return self._chain

    def emit(self, record: AuditRecord) -> str:
        """Append a record to the chain and return its hash.

        Links the record to the previous record via prev_hash.
        The first record (genesis) has prev_hash = None.

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
        prev: str | None = None
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
