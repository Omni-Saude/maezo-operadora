"""Unit tests for maezo.platform.retention — Retention Manager (ADR-0007).

TDD London School: tests written BEFORE implementation verification.
Tests 5-year regulatory retention policy with cutoff, expiry, and batch filtering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maezo.platform.retention import _RETENTION_DELETE_SQL, DEFAULT_RETENTION_YEARS, RetentionManager


def test_retention_policy_default_years() -> None:
    """RetentionManager must default to 5-year retention (regulatory minimum)."""
    manager = RetentionManager()
    assert manager.retention_years == 5
    assert manager.retention_period == timedelta(days=5 * 365)


def test_retention_policy_custom_years() -> None:
    """RetentionManager must accept custom retention years (>= 5)."""
    manager = RetentionManager(retention_years=7)
    assert manager.retention_years == 7
    assert manager.retention_period == timedelta(days=7 * 365)


def test_retention_policy_enforces_minimum() -> None:
    """RetentionManager must enforce minimum 5 years even when lower value given."""
    manager = RetentionManager(retention_years=2)
    # Should clamp to minimum of 5
    assert manager.retention_years == 5


def test_cutoff_timestamp_five_years_ago() -> None:
    """cutoff_timestamp must be approximately 5 years before the reference."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    cutoff = manager.cutoff_timestamp(reference=now)

    # Cutoff should be approximately 5 years before reference
    # Allow some flexibility for leap years (365 * 5 = 1825 days)
    delta = now - cutoff
    assert 1820 <= delta.days <= 1830  # ~5 years


def test_cutoff_timestamp_defaults_to_now() -> None:
    """cutoff_timestamp must use current UTC time when no reference given."""
    manager = RetentionManager()
    cutoff = manager.cutoff_timestamp()

    # Cutoff must be in the past
    assert cutoff < datetime.now(UTC)
    # Cutoff must be approximately 5 years ago
    delta = datetime.now(UTC) - cutoff
    assert 1820 <= delta.days <= 1831


def test_is_expired_old_record() -> None:
    """Records older than 5 years must be marked as expired."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, tzinfo=UTC)
    old_record = datetime(2020, 1, 1, tzinfo=UTC)  # > 5 years ago

    assert manager.is_expired(old_record, reference=now) is True


def test_is_expired_recent_record() -> None:
    """Records within 5 years must NOT be marked as expired."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, tzinfo=UTC)
    recent_record = datetime(2024, 1, 1, tzinfo=UTC)  # ~2.5 years ago

    assert manager.is_expired(recent_record, reference=now) is False


def test_is_expired_exactly_five_years() -> None:
    """Records exactly at the 5-year boundary must be considered NOT expired."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    exactly_five = now - timedelta(days=5 * 365)

    # Exact boundary: record at exactly 5 years is NOT older than cutoff
    # (cutoff is now - 5 years; record at that time is equal, not older)
    assert manager.is_expired(exactly_five, reference=now) is False


def test_is_expired_just_over_five_years() -> None:
    """Records just over 5 years must be expired."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    just_over = now - timedelta(days=5 * 365 + 1)

    assert manager.is_expired(just_over, reference=now) is True


def test_filter_expired_splits_records() -> None:
    """filter_expired must correctly split records into expired and retained."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, tzinfo=UTC)
    old_ts = datetime(2019, 1, 1, tzinfo=UTC)  # > 5 years ago
    recent_ts = datetime(2025, 1, 1, tzinfo=UTC)  # < 5 years ago

    records = [
        {"id": 1, "ts": old_ts, "action": "old_action"},
        {"id": 2, "ts": recent_ts, "action": "recent_action"},
        {"id": 3, "ts": old_ts, "action": "another_old"},
        {"id": 4, "ts": recent_ts, "action": "new_action"},
    ]

    expired, retained = manager.filter_expired(records, reference=now)

    assert len(expired) == 2
    assert len(retained) == 2
    assert all(r["id"] in {1, 3} for r in expired)
    assert all(r["id"] in {2, 4} for r in retained)


def test_filter_expired_empty_list() -> None:
    """filter_expired must handle empty record lists gracefully."""
    manager = RetentionManager()
    expired, retained = manager.filter_expired([])
    assert expired == []
    assert retained == []


def test_filter_expired_all_expired() -> None:
    """filter_expired must handle all records being expired."""
    manager = RetentionManager()

    now = datetime(2026, 7, 5, tzinfo=UTC)
    old_ts = datetime(2018, 1, 1, tzinfo=UTC)

    records = [{"id": i, "ts": old_ts} for i in range(5)]

    expired, retained = manager.filter_expired(records, reference=now)

    assert len(expired) == 5
    assert len(retained) == 0


def test_filter_expired_none_ts_retained() -> None:
    """Records with None timestamp must be retained (fail-safe)."""
    manager = RetentionManager()

    records = [
        {"id": 1, "ts": None},
        {"id": 2, "ts": datetime(2020, 1, 1, tzinfo=UTC)},
    ]

    now = datetime(2026, 7, 5, tzinfo=UTC)
    expired, retained = manager.filter_expired(records, reference=now)

    # Record with None ts (2) should be retained (+ the old one expired)
    assert len(expired) >= 1
    # Record with None ts should be in retained
    retained_ids = {r["id"] for r in retained}
    assert 1 in retained_ids


def test_retention_query_generates_sql() -> None:
    """retention_query devolve o DELETE literal com o cutoff como parametro ligado (R-110 / F-151)."""
    manager = RetentionManager(retention_years=5)
    reference_floor = manager.cutoff_timestamp()

    statement, params = manager.retention_query(table_name="audit_chain", ts_column="ts")

    assert statement == "DELETE FROM audit_chain WHERE ts < :cutoff"
    assert set(params) == {"cutoff"}
    cutoff = params["cutoff"]
    assert cutoff.tzinfo is not None
    assert reference_floor <= cutoff <= manager.cutoff_timestamp()
    # O valor nunca vira texto dentro do SQL (antes: literal ISO entre aspas).
    assert cutoff.isoformat() not in statement
    assert "'" not in statement


def test_retention_query_defaults_are_the_audit_chain_pair() -> None:
    statement, _ = RetentionManager().retention_query()
    assert statement == "DELETE FROM audit_chain WHERE ts < :cutoff"


@pytest.mark.parametrize(
    ("table_name", "ts_column"),
    [
        ("events", "created_at"),
        ("audit_chain", "created_at"),
        ("audit_chain; DROP TABLE audit_chain; --", "ts"),
        ("audit_chain", "ts OR 1=1"),
        ("AUDIT_CHAIN", "ts"),
    ],
)
def test_retention_query_refuses_pairs_outside_the_closed_map(table_name: str, ts_column: str) -> None:
    """Teste negativo: identificador fora de `_RETENTION_DELETE_SQL` e' recusado antes de existir SQL."""
    with pytest.raises(ValueError, match="mapa fechado"):
        RetentionManager().retention_query(table_name=table_name, ts_column=ts_column)


def test_retention_delete_map_is_closed_and_literal() -> None:
    """O mapa e' a unica fonte do SQL: chaves sao identificadores simples e cada valor so' liga `:cutoff`."""
    assert _RETENTION_DELETE_SQL == {("audit_chain", "ts"): "DELETE FROM audit_chain WHERE ts < :cutoff"}


def test_retention_years_constant() -> None:
    """DEFAULT_RETENTION_YEARS must be 5 (regulatory minimum per ADR-0007)."""
    assert DEFAULT_RETENTION_YEARS == 5
