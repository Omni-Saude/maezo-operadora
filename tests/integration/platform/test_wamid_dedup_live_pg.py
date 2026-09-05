"""LIVE-Postgres proof of the `wamid` dedup registry (gaps `WEBHOOK-WAMID-DEDUP` /
`DRIVER-IDEMPOTENCY-ORPHAN-TABLE`; owner decisions R-071/R-073).

Tier 2 (`@pytest.mark.integration`). Runs the REAL alembic chain (`0001..0010`) against a real
Postgres in a throwaway per-run schema and then exercises
`platform.driver_idempotency.PostgresDriverIdempotencyRegistry` — the SAME SQL constants the
production path uses, never a copy — so the branches a unit double cannot reach are actually
executed:

1.  the repurpose DDL of migration `0010` really applies (column, CHECK, partial index, comments);
2.  claim -> seal -> a redelivery is refused; release -> a redelivery is allowed;
3.  the EXPIRY branch: a TTL that has passed makes the key claimable again;
4.  the LEASE branch: an abandoned `pending` claim (a receiver killed mid-turn) is re-claimable,
    while a fresh one is not — the difference between recovering a lost message and answering the
    beneficiary twice;
5.  CONCURRENCY: N simultaneous claims of the same key, exactly one wins (this is the property the
    whole design rests on and the one a fake dict cannot prove);
6.  PHI: no row in the table carries a raw phone number or a raw wamid.

Skips LOUDLY (never errors, never fakes) when no Postgres is reachable. `MAEZO_TEST_DATABASE_URL`
wins; the fallback is the compose stack (gap LIVE-SUITES-SILENT-SKIP-AUDIT: a default nobody
serves is a silent skip, not a proof).

    docker compose up -d postgres
    MAEZO_TEST_DATABASE_URL=postgresql://maezo:maezo@localhost:5433/maezo \\
      uv run pytest tests/integration/platform/test_wamid_dedup_live_pg.py -v
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import pytest

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.platform.driver_idempotency import (
    DEDUP_TABLE,
    STATUS_PENDING,
    STATUS_PROCESSED,
    PostgresDriverIdempotencyRegistry,
)

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PHONE = "5511999999999"
_WAMID = "wamid.HBgNNTUxMTk5OTk5OTk5ORUCABIYFDNBMEE1NkY3RUUxQjA5RkQyNkE5AA=="


def _dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


def _alembic_config(dsn: str, tenant_id: str) -> Any:
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "src" / "maezo" / "platform" / "migrations"))
    async_dsn = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg.set_main_option("sqlalchemy.url", async_dsn)
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant_id}"])  # env.py: -x tenant=<id>
    return cfg


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _dsn()
    if not asyncio.run(_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL) — see the module docstring for the bring-up command."
        )
    return dsn


@pytest.fixture(scope="module")
def tenant_schema(pg_dsn: str) -> Iterator[str]:
    """A throwaway per-run schema with the WHOLE migration chain applied (0001..head)."""
    from alembic import command

    tenant_id = f"wamid{uuid.uuid4().hex[:12]}"  # [a-z][a-z0-9_]* per schema_for_tenant

    async def _create() -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    asyncio.run(_create())
    command.upgrade(_alembic_config(pg_dsn, tenant_id), "head")
    yield tenant_id

    async def _drop() -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_drop())


@pytest.fixture
async def registry(
    pg_dsn: str, tenant_schema: str
) -> Any:  # AsyncIterator[PostgresDriverIdempotencyRegistry]
    store = PostgresDriverIdempotencyRegistry(dsn=pg_dsn, tenant=tenant_schema, lease_s=1.0)
    yield store
    await store.aclose()


def _key(suffix: str) -> str:
    return f"wa:inbound:test:hk1_{suffix}"


async def _row(pg_dsn: str, schema: str, key: str) -> Any:
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema}"')
        return await conn.fetchrow(
            f"SELECT key, tenant, status, expires_at FROM {DEDUP_TABLE} WHERE key = $1", key
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. The repurpose DDL really applied
# ---------------------------------------------------------------------------


async def test_migration_0010_applied_the_repurpose_ddl(pg_dsn: str, tenant_schema: str) -> None:
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        column = await conn.fetchrow(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 AND column_name = 'status'",
            tenant_schema,
            DEDUP_TABLE,
        )
        assert column is not None, "migration 0010 did not add driver_idempotency.status"
        assert column["is_nullable"] == "NO"
        assert "processed" in column["column_default"]

        constraint = await conn.fetchval(
            "SELECT conname FROM pg_constraint WHERE conname = 'ck_driver_idempotency_status'"
        )
        assert constraint is not None

        indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
            tenant_schema,
            DEDUP_TABLE,
        )
        names = {row["indexname"] for row in indexes}
        assert "ix_driver_idempotency_pending" in names
        assert "ix_driver_idempotency_expires" in names

        # The repurpose declaration a DBA can actually read (R-073's actual ask).
        comment = await conn.fetchval(
            "SELECT obj_description($1::regclass, 'pg_class')", f'"{tenant_schema}".{DEDUP_TABLE}'
        )
        assert comment is not None and "WEBHOOK-WAMID-DEDUP" in comment
    finally:
        await conn.close()


async def test_the_status_check_refuses_a_vocabulary_the_code_never_writes(
    pg_dsn: str, tenant_schema: str
) -> None:
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                f"INSERT INTO {DEDUP_TABLE} (key, tenant, expires_at, status) "
                "VALUES ('bad', 't', now() + interval '1 hour', 'done')"
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 2-4. The claim protocol against a real database
# ---------------------------------------------------------------------------


async def test_a_second_claim_of_a_sealed_key_is_refused(
    registry: PostgresDriverIdempotencyRegistry, pg_dsn: str, tenant_schema: str
) -> None:
    key = _key("sealed")
    assert await registry.claim(key, ttl_s=3600) is True
    await registry.mark_processed(key)

    assert await registry.claim(key, ttl_s=3600) is False, "a redelivery must be suppressed"
    row = await _row(pg_dsn, tenant_schema, key)
    assert row["status"] == STATUS_PROCESSED


async def test_a_released_claim_lets_the_redelivery_run(
    registry: PostgresDriverIdempotencyRegistry, pg_dsn: str, tenant_schema: str
) -> None:
    key = _key("released")
    assert await registry.claim(key, ttl_s=3600) is True
    await registry.release(key)

    assert await _row(pg_dsn, tenant_schema, key) is None
    assert await registry.claim(key, ttl_s=3600) is True, "a failed turn must get a real retry"


async def test_an_expired_window_makes_the_key_claimable_again(
    registry: PostgresDriverIdempotencyRegistry,
) -> None:
    """A dedup with no expiry would swallow a message the beneficiary legitimately re-sends."""
    key = _key("expired")
    assert await registry.claim(key, ttl_s=-1) is True  # already expired on arrival
    await registry.mark_processed(key)

    assert await registry.claim(key, ttl_s=3600) is True


async def test_a_fresh_in_flight_claim_is_not_stealable_but_an_abandoned_one_is(
    registry: PostgresDriverIdempotencyRegistry,
) -> None:
    """The lease branch — the difference between recovering a message lost to a killed receiver and
    answering the beneficiary twice. `lease_s=1.0` on this fixture keeps the wait honest."""
    key = _key("lease")
    assert await registry.claim(key, ttl_s=3600) is True
    assert await registry.claim(key, ttl_s=3600) is False, "an in-flight turn must not be stolen"

    await asyncio.sleep(1.2)  # the lease elapses; the claim now looks abandoned
    assert await registry.claim(key, ttl_s=3600) is True


async def test_sealing_a_key_another_replica_reclaimed_does_not_steal_it(
    registry: PostgresDriverIdempotencyRegistry, pg_dsn: str, tenant_schema: str
) -> None:
    """`mark_processed` is scoped to a `pending` row: a slow replica finishing late must not seal a
    claim that already belongs to somebody else's in-flight turn."""
    key = _key("late-seal")
    assert await registry.claim(key, ttl_s=3600) is True
    await registry.mark_processed(key)  # replica A seals
    await registry.mark_processed(key)  # replica B's late seal — a no-op, not a resurrection

    row = await _row(pg_dsn, tenant_schema, key)
    assert row["status"] == STATUS_PROCESSED


# ---------------------------------------------------------------------------
# 5. Concurrency — the property the whole design rests on
# ---------------------------------------------------------------------------


async def test_twenty_concurrent_claims_of_one_wamid_produce_exactly_one_winner(
    pg_dsn: str, tenant_schema: str
) -> None:
    """Two replicas receiving the same redelivery at the same instant is the REAL shape of this
    race — an in-memory window (option A, rejected by the owner) cannot see it at all."""
    key = _key("race")
    registries = [PostgresDriverIdempotencyRegistry(dsn=pg_dsn, tenant=tenant_schema) for _ in range(20)]
    try:
        results = await asyncio.gather(*(store.claim(key, ttl_s=3600) for store in registries))
    finally:
        await asyncio.gather(*(store.aclose() for store in registries))

    assert sum(1 for won in results if won) == 1, f"expected exactly one winner, got {results}"
    row = await _row(pg_dsn, tenant_schema, key)
    assert row["status"] == STATUS_PENDING


# ---------------------------------------------------------------------------
# 6. PHI
# ---------------------------------------------------------------------------


async def test_no_row_carries_a_raw_phone_number_or_a_raw_wamid(
    registry: PostgresDriverIdempotencyRegistry, pg_dsn: str, tenant_schema: str
) -> None:
    """The property that keeps the LGPD inventory's `SEM_COLUNA_DE_TITULAR` true after the
    repurpose. Written as a scan of the WHOLE table rather than of this test's own keys, so a
    future writer that stored a raw wamid fails here too."""
    from maezo.gateway.pseudonymizer import Pseudonymizer
    from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard

    guard = WhatsAppDedupGuard(
        registry=registry,
        pseudonymizer=Pseudonymizer.from_settings(
            phi_hmac_key="integration-test-key-not-a-secret",
            production=False,
            tenant_id=tenant_schema,
        ),
        tenant=tenant_schema,
    )
    assert await guard.claim_inbound(_WAMID) is True
    assert await guard.claim_inbound(_WAMID) is False

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        keys = [row["key"] for row in await conn.fetch(f"SELECT key FROM {DEDUP_TABLE}")]
    finally:
        await conn.close()

    assert keys, "non-vacuity: the scan must see the rows this test wrote"
    for key in keys:
        assert _PHONE not in key
        assert _WAMID not in key
        assert "wamid." not in key
