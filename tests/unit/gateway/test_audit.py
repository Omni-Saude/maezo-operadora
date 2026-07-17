"""Unit tests for maezo.gateway.audit — Audit Chain (ADR-0007, DL-0018).

TDD London School: tests written BEFORE implementation.
"""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from maezo.gateway.audit import GENESIS_PREV_HASH, AuditRecord, AuditSink, canonicalize_jsonb


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


# ---------------------------------------------------------------------------
# H1 regression (R1 verification cycle 1): jsonb round-trip canonicalization.
# The DB-backed end-to-end variants live in test_audit_postgres.py; these are
# the pure canonicalizer rules, testable without Postgres.
# ---------------------------------------------------------------------------


def _record(details: dict[str, object]) -> AuditRecord:
    return AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details=dict(details),
    )


def test_canonicalize_negative_zero_normalized() -> None:
    """H1 root cause: Postgres numeric has no signed zero, so -0.0 is stored as 0.0.
    Canonicalization must drop the sign BEFORE hashing so write-hash == verify-hash."""
    record = _record({"x": -0.0})
    assert record.details["x"] == 0.0
    assert math.copysign(1.0, record.details["x"]) == 1.0  # sign actually gone, not just ==0
    assert isinstance(record.details["x"], float)


def test_canonicalize_exponent_integral_floats_become_int() -> None:
    """Floats with exponent-form repr (|v| >= 1e16) are printed by Postgres numeric as plain
    digit strings, which json.loads returns as int. The int must follow the DECIMAL semantics
    of the shortest repr (what Postgres parses), not the double's exact binary value:
    numeric('1e+308') is exactly 10**308, while int(1e308) is a different integer."""
    record = _record({"a": 1e16, "b": -1e16, "c": 1.234e16, "d": 1e308, "e": 1.0000000000000002e16})
    assert record.details["a"] == 10**16 and isinstance(record.details["a"], int)
    assert record.details["b"] == -(10**16) and isinstance(record.details["b"], int)
    assert record.details["c"] == 12340000000000000 and isinstance(record.details["c"], int)
    assert record.details["d"] == 10**308 and isinstance(record.details["d"], int)
    assert record.details["d"] != int(1e308)  # decimal-of-repr, NOT binary value of the double
    assert record.details["e"] == 10000000000000002 and isinstance(record.details["e"], int)


def test_canonicalize_small_and_plain_floats_untouched() -> None:
    """Values that round-trip hash-stable through jsonb (verified empirically) pass through:
    small-exponent floats print as plain decimals with a fraction and load back to the same
    double; fixed-notation floats keep their '.0'/fraction via numeric's preserved scale."""
    record = _record({"a": 1e-05, "b": 5e-324, "c": 0.1, "d": 2.5, "e": 1.0, "f": 9.99e15})
    for key, expected in {"a": 1e-05, "b": 5e-324, "c": 0.1, "d": 2.5, "e": 1.0, "f": 9.99e15}.items():
        assert record.details[key] == expected
        assert isinstance(record.details[key], float)


def test_canonicalize_rejects_non_finite_floats() -> None:
    """NaN/±Inf are not valid JSON and Postgres would reject the INSERT — fail-closed at the
    AuditRecord boundary, with a path in the error, including deeply nested occurrences."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="non-finite float"):
            _record({"x": bad})
    with pytest.raises(ValueError, match=r"\$\.outer\[1\]\.inner"):
        _record({"outer": [{}, {"inner": float("nan")}]})


def test_canonicalize_recurses_and_is_idempotent() -> None:
    """Nested dicts/lists are normalized; canonicalizing an already-canonical payload is a
    no-op (verify_chain reconstructs records from stored rows, re-running canonicalization)."""
    payload: dict[str, object] = {
        "unicode_ключ_鍵": {"nested": [{"deep": [-0.0, 1e16, "café", None, True, False]}]},
        "mixed": [1, 1.5, "1", None],
    }
    once = canonicalize_jsonb(payload)
    assert once["unicode_ключ_鍵"]["nested"][0]["deep"][0] == 0.0
    assert once["unicode_ключ_鍵"]["nested"][0]["deep"][1] == 10**16
    assert isinstance(once["unicode_ключ_鍵"]["nested"][0]["deep"][1], int)
    assert canonicalize_jsonb(once) == once


def test_canonicalize_applies_to_dmn_versions_too() -> None:
    """dmn_versions is a jsonb column with the same round-trip exposure as decision_basis."""
    record = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={},
        dmn_versions={"tabela": -0.0},
    )
    assert math.copysign(1.0, record.dmn_versions["tabela"]) == 1.0


def test_audit_record_rejects_naive_timestamp() -> None:
    """A naive timestamp's actual instant is ambiguous — Postgres timestamptz returns UTC, so
    the hash would not recompute after readback. Fail-closed at construction."""
    with pytest.raises(ValueError, match="timezone-aware"):
        AuditRecord(
            agent_id="helena",
            tenant_id="amh",
            agent_version="1.0.0",
            action="triagem",
            decision="ALLOW",
            details={},
            timestamp=datetime(2026, 7, 17, 12, 0, 0),  # noqa: DTZ001 — the point of the test
        )


def test_audit_record_normalizes_timestamp_to_utc() -> None:
    """Aware non-UTC timestamps are the same instant Postgres will return in UTC — normalize
    at construction so isoformat() (a hash input) is identical before and after storage."""
    brt = timezone(timedelta(hours=-3))
    record = AuditRecord(
        agent_id="helena",
        tenant_id="amh",
        agent_version="1.0.0",
        action="triagem",
        decision="ALLOW",
        details={},
        timestamp=datetime(2026, 7, 17, 9, 30, 0, tzinfo=brt),
    )
    assert record.timestamp.tzinfo == UTC
    assert record.timestamp == datetime(2026, 7, 17, 12, 30, 0, tzinfo=UTC)
