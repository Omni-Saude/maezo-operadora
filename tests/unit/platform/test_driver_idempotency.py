"""`platform.driver_idempotency` — the durable dedup registry (gaps `WEBHOOK-WAMID-DEDUP` /
`DRIVER-IDEMPOTENCY-ORPHAN-TABLE`, owner decisions R-071/R-073).

No Postgres here: this suite pins the SQL's semantics as text plus the Python behaviour around
it (claim/seal/release return semantics, fail-closed translation, pool discipline). The live
round trip — atomicity, the expiry and lease branches actually firing, concurrent claims — is
`tests/integration/platform/test_wamid_dedup_live_pg.py`, which executes THESE constants rather
than a copy of them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from maezo.platform import driver_idempotency as di
from maezo.platform.driver_idempotency import (
    CLAIM_SQL,
    MARK_PROCESSED_SQL,
    RELEASE_SQL,
    DedupRegistryUnavailableError,
    PostgresDriverIdempotencyRegistry,
)

_DSN = "postgresql://u:p@localhost:5432/db"


class _FakeConn:
    def __init__(self, results: list[Any] | None = None, *, boom: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._results = results or []
        self._boom = boom

    async def execute(self, sql: str, *args: Any) -> None:
        self.calls.append((sql, args))

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if self._boom:
            raise RuntimeError("connection refused")
        self.calls.append((sql, args))
        return self._results.pop(0) if self._results else None


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.closed = False

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    async def close(self) -> None:
        self.closed = True


def _registry(conn: _FakeConn, **kwargs: Any) -> PostgresDriverIdempotencyRegistry:
    return PostgresDriverIdempotencyRegistry(dsn=_DSN, tenant="amh", pool=_FakePool(conn), **kwargs)


# ---------------------------------------------------------------------------
# SQL semantics (text-level, so a later edit that breaks one fails here first)
# ---------------------------------------------------------------------------


def test_the_claim_takes_over_an_expired_window_or_an_abandoned_in_flight_claim() -> None:
    """Both branches matter: without the first, dedup would be permanent (a message resent by the
    beneficiary a week later would be swallowed); without the second, a receiver killed mid-turn
    would suppress the redelivery of a message nobody ever answered."""
    assert "ON CONFLICT (key) DO UPDATE" in CLAIM_SQL
    assert "driver_idempotency.expires_at <= now()" in CLAIM_SQL
    assert "driver_idempotency.status = 'pending'" in CLAIM_SQL
    assert "driver_idempotency.created_at <= now() - make_interval" in CLAIM_SQL
    assert CLAIM_SQL.rstrip().endswith("RETURNING key")


def test_the_claim_writes_pending_never_processed() -> None:
    """A claim that inserted a terminal row would suppress the redelivery of work that never ran."""
    assert "'pending'" in CLAIM_SQL
    assert "'processed'" not in CLAIM_SQL


def test_sealing_only_ever_moves_pending_to_processed() -> None:
    """A late seal must not steal a claim another replica already re-claimed."""
    assert "SET status = 'processed'" in MARK_PROCESSED_SQL
    assert "AND status = 'pending'" in MARK_PROCESSED_SQL


def test_releasing_only_deletes_a_still_pending_row() -> None:
    """A `processed` row means the beneficiary WAS answered; deleting it would re-open the
    duplicate-reply window."""
    assert RELEASE_SQL.startswith("DELETE FROM driver_idempotency")
    assert "AND status = 'pending'" in RELEASE_SQL


def test_every_statement_is_scoped_by_tenant_as_well_as_key() -> None:
    for sql in (MARK_PROCESSED_SQL, RELEASE_SQL):
        assert "tenant = $2" in sql


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


async def test_claim_returns_true_when_the_row_was_won() -> None:
    conn = _FakeConn(["wa:inbound:amh:hk1_x"])
    assert await _registry(conn).claim("wa:inbound:amh:hk1_x") is True


async def test_claim_returns_false_when_the_key_is_already_claimed() -> None:
    conn = _FakeConn([None])
    assert await _registry(conn).claim("wa:inbound:amh:hk1_x") is False


async def test_claim_passes_the_ttl_and_the_lease_as_bound_parameters() -> None:
    """Never string-interpolated: the TTL comes from settings, and a formatted interval would be
    an injection surface as well as a drift surface."""
    conn = _FakeConn(["k"])
    registry = _registry(conn, lease_s=90.0)

    await registry.claim("k", ttl_s=3600.0)

    sql, args = conn.calls[0]
    assert sql == CLAIM_SQL
    assert args == ("k", "amh", 3600.0, 90.0)


@pytest.mark.parametrize(
    "operation", ["claim", "mark_processed", "release"], ids=["claim", "seal", "release"]
)
async def test_every_operation_fails_closed_on_a_database_error(operation: str) -> None:
    """The registry NEVER answers "no duplicate" because it could not look."""
    registry = _registry(_FakeConn(boom=True))
    with pytest.raises(DedupRegistryUnavailableError):
        await getattr(registry, operation)("k")


async def test_the_failure_message_does_not_swallow_the_cause() -> None:
    registry = _registry(_FakeConn(boom=True))
    with pytest.raises(DedupRegistryUnavailableError) as excinfo:
        await registry.claim("k")
    assert "RuntimeError" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


async def test_an_unsealed_claim_is_announced() -> None:
    """`mark_processed` finding no `pending` row means another replica re-claimed the key — not an
    error, but a lease-tuning fact operators need."""
    import structlog

    registry = _registry(_FakeConn([None]))
    with structlog.testing.capture_logs() as logs:
        await registry.mark_processed("k")
    assert any(entry["event"] == "dedup_claim_not_sealed" for entry in logs)


async def test_the_pool_pins_the_search_path_on_every_acquire_not_once_per_connection() -> None:
    """`init=` would pin it once per physical connection, and asyncpg RESETs session state on
    release — the second claim on a reused connection would look for the table in `public` (the
    #55 defect, ported from `PostgresAuditSink`)."""
    created = AsyncMock(return_value=_FakePool(_FakeConn(["k"])))
    registry = PostgresDriverIdempotencyRegistry(dsn=_DSN, tenant="amh")

    with patch.object(di.asyncpg, "create_pool", created):
        await registry.claim("k")

    kwargs = created.await_args.kwargs
    assert "setup" in kwargs
    assert "init" not in kwargs


async def test_an_injected_pool_is_not_closed_by_the_registry() -> None:
    """The pool's owner closes it; a registry that closed a borrowed pool would take down whatever
    else shares it."""
    pool = _FakePool(_FakeConn())
    registry = PostgresDriverIdempotencyRegistry(dsn=_DSN, tenant="amh", pool=pool)

    await registry.aclose()

    assert pool.closed is False


async def test_a_tenant_that_is_not_a_valid_schema_identifier_is_refused() -> None:
    """Anti-injection: the tenant becomes a `SET search_path TO "..."` literal."""
    with pytest.raises(Exception, match="(?i)tenant|schema"):
        PostgresDriverIdempotencyRegistry(dsn=_DSN, tenant='amh"; DROP SCHEMA public; --')
