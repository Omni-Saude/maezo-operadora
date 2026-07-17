"""Unit tests for maezo.gateway.audit — Audit Chain (ADR-0007, DL-0018).

TDD London School: tests written BEFORE implementation.
"""

from maezo.gateway.audit import GENESIS_PREV_HASH, AuditRecord, AuditSink


def test_audit_chain_integrity() -> None:
    """Sequential emits must produce a valid hash chain where each record
    references the previous record's hash."""
    sink = AuditSink()

    r1_hash = sink.emit(
        AuditRecord(
            agent_id="helena",
            tenant_id="amh",
            agent_version="1.0.0",
            action="triagem_whatsapp",
            decision="ALLOW",
            details={"paciente": "pseudo-abc123"},
        )
    )

    r2_hash = sink.emit(
        AuditRecord(
            agent_id="rafael",
            tenant_id="amh",
            agent_version="1.0.0",
            action="aprovacao_auth_dmn_favoravel",
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
            tenant_id="amh",
            agent_version="1.0.0",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )
    sink.emit(
        AuditRecord(
            agent_id="rafael",
            tenant_id="amh",
            agent_version="1.0.0",
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
            tenant_id="amh",
            agent_version="1.0.0",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )

    assert sink.verify_chain()


def test_audit_record_prev_hash_genesis_sentinel() -> None:
    """The first record (genesis) must have prev_hash = GENESIS_PREV_HASH.

    A real sentinel (not SQL NULL) is used deliberately so that the
    `UNIQUE(prev_record_hash)` DB constraint also guards the very first
    record of a chain — see GENESIS_PREV_HASH's docstring in audit.py.
    """
    sink = AuditSink()

    sink.emit(
        AuditRecord(
            agent_id="helena",
            tenant_id="amh",
            agent_version="1.0.0",
            action="triagem",
            decision="ALLOW",
            details={},
        )
    )

    assert sink.chain[0].prev_hash == GENESIS_PREV_HASH


def test_audit_chain_prev_hash_unique() -> None:
    """DL-0018: UNIQUE(prev_record_hash) — each record must have a unique
    prev_hash (anti-fork: single chain per schema, not partitionable)."""
    sink = AuditSink()

    prev_hashes: set[str] = set()
    for i in range(5):
        sink.emit(
            AuditRecord(
                agent_id=f"agent-{i}",
                tenant_id="amh",
                agent_version="1.0.0",
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
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={"key": "value"},
    )

    assert record.agent_id == "helena"
    assert record.tenant_id == "amh"
    assert record.agent_version == "1.0.0"
    assert record.action == "triagem"
    assert record.decision == "ALLOW"
    assert record.details == {"key": "value"}
    assert isinstance(record.timestamp, dt.datetime)


def test_audit_record_dmn_versions_default_non_null() -> None:
    """TODO(T1.5): no caller can populate dmn_versions yet (workers don't call
    the DMN engine). The field must default to a non-null empty structure —
    never None/absent — so the DB column (NOT NULL DEFAULT '{}') is always
    satisfiable and no record is ever missing this column's provenance."""
    record = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={},
    )

    assert record.dmn_versions == {}
    assert record.dmn_versions is not None


def test_audit_record_input_hash_derived_from_details() -> None:
    """input_hash must be a deterministic hash of `details`, never settable
    directly (PHI must never enter the chain raw; only its hash may)."""
    r1 = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={"a": 1},
    )
    r2 = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={"a": 1},
    )
    r3 = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={"a": 2},
    )

    assert r1.compute_input_hash() == r2.compute_input_hash()
    assert r1.compute_input_hash() != r3.compute_input_hash()
