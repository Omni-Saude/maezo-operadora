"""Unit tests for maezo.gateway.audit — Audit Chain (ADR-0007, DL-0018).

TDD London School: tests written BEFORE implementation.
"""

from maezo.gateway.audit import AuditRecord, AuditSink


def test_audit_chain_integrity() -> None:
    """Sequential emits must produce a valid hash chain where each record
    references the previous record's hash."""
    sink = AuditSink()

    r1_hash = sink.emit(
        AuditRecord(
            agent_id="helena",
            action="triage_and_routing",
            decision="ALLOW",
            details={"paciente": "pseudo-abc123"},
        )
    )

    r2_hash = sink.emit(
        AuditRecord(
            agent_id="rafael",
            action="authorization_approval",
            decision="ALLOW",
            details={"guia": "g-001"},
        )
    )

    # Each emit returns a hash
    assert r1_hash is not None
    assert r2_hash is not None
    assert r1_hash != r2_hash

    # Chain integrity: r2's prev_hash should equal r1_hash
    records = sink.chain
    assert len(records) >= 2
    assert records[1].prev_hash == r1_hash


def test_audit_chain_tamper_detection() -> None:
    """Modifying a record in the chain must invalidate subsequent hashes."""
    sink = AuditSink()

    sink.emit(
        AuditRecord(
            agent_id="helena",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )
    sink.emit(
        AuditRecord(
            agent_id="rafael",
            action="aprovacao",
            decision="ALLOW",
            details={},
        )
    )

    # Tamper: change a field on record 0
    sink.chain[0].action = "acao_alterada"

    # Recompute hashes - should detect tampering
    assert not sink.verify_chain()


def test_audit_chain_empty_chain() -> None:
    """An empty chain must verify successfully (vacuously true)."""
    sink = AuditSink()
    assert sink.verify_chain()


def test_audit_chain_single_record() -> None:
    """A single-record chain must verify successfully."""
    sink = AuditSink()

    sink.emit(
        AuditRecord(
            agent_id="helena",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )

    assert sink.verify_chain()


def test_audit_record_prev_hash_none_for_genesis() -> None:
    """The first record (genesis) must have prev_hash = None."""
    sink = AuditSink()

    sink.emit(
        AuditRecord(
            agent_id="helena",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )

    assert sink.chain[0].prev_hash is None


def test_audit_chain_prev_hash_unique() -> None:
    """DL-0018: UNIQUE(prev_record_hash) — each record must have a unique
    prev_hash (anti-fork: single global chain, not partitionable)."""
    sink = AuditSink()

    prev_hashes: set[str | None] = set()
    for i in range(5):
        sink.emit(
            AuditRecord(
                agent_id=f"agent-{i}",
                action=f"action-{i}",
                decision="ALLOW",
                details={},
            )
        )
        if i > 0:
            prev_hashes.add(sink.chain[i].prev_hash)

    # With 4 records after genesis, we should have 4 unique prev_hashes
    assert len(prev_hashes) == 4


def test_audit_record_fields() -> None:
    """AuditRecord must have all required fields."""
    import datetime as dt

    record = AuditRecord(
        agent_id="helena",
        action="triagem",
        decision="ALLOW",
        details={"key": "value"},
    )

    assert record.agent_id == "helena"
    assert record.action == "triagem"
    assert record.decision == "ALLOW"
    assert record.details == {"key": "value"}
    assert isinstance(record.timestamp, dt.datetime)
