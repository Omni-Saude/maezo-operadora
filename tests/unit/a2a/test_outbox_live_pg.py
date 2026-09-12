"""LIVE-Postgres proof for the A2A fact outbox — the claims the fake-conn suite cannot make.

Tier 2 (`@pytest.mark.integration`), placed HERE rather than under `tests/integration/` for the
same reason `test_idempotency_store.py` and `test_a2a_edge_live_pg.py` are: this needs a REAL
Postgres and explicitly NOT the CIB Seven engine, and `tests/integration/`'s package-wide autouse
`_skip_if_engine_unreachable` fixture would gate it on an engine it does not use. SKIPS LOUDLY
(never silently) when no server is reachable — deliberately not an `xfail`, which would start
FAILING the moment Postgres becomes reachable and the tests legitimately pass.

**The DDL under test is the REAL migration.** `_outbox_ddl()` extracts the `op.execute(...)` SQL
from `0008_a2a_fact_outbox.py` and runs THAT — no hand-copied CREATE TABLE. `test_idempotency_
store.py` duplicates its DDL (and says so); duplicating it here would mean a schema change could
pass this suite while breaking the migration, which is precisely the failure a live proof exists
to catch.

What this proves that no fake can:

 1. **Transactional atomicity, both directions.** With a caller's connection enlisted
    (`outbox_transaction`), a ROLLBACK leaves ZERO fact rows and a COMMIT leaves EXACTLY ONE. This
    is the "outbox" in transactional outbox, and it is real SQL rollback semantics, not a mock.
 2. **At-least-once by LEASE EXPIRY.** A row claimed and never marked is re-claimed once its lease
    passes — with the SAME `dedup_key` and an incremented `attempts` — which is what makes a crash
    between publish and mark cost a duplicate rather than a fact.
 3. **The lease guard is real.** A worker whose lease was stolen cannot seal the row.
 4. **The CHECK constraints actually reject.** The biconditional lifecycle checks are asserted by
    trying to write the invalid rows and being refused, not by reading the DDL text (that half is
    `tests/unit/platform/test_migration_0008_a2a_fact_outbox.py`).
 5. **`dedup_key` is genuinely non-unique.** Two rows carrying the same key are ACCEPTED, so a
    legitimate re-emission can never raise out of `DelegationDispatcher.delegate`.
 6. **End to end**: `drain_once` over the real table, real claim/mark SQL, recording publisher.

R1 verifier: `docker compose --profile core up -d postgres` (or any local server, via
`MAEZO_TEST_DATABASE_URL`) and re-run this file. The FULL CIB Seven live proof is a train-close
activity, not this leg's.

DSN DEFAULT (gap LIVE-SUITES-SILENT-SKIP-AUDIT, 2026-09-04). `MAEZO_TEST_DATABASE_URL` still wins;
the fallback is now `postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5433}/maezo` instead
of a hardcoded `localhost:5433` — byte-for-byte `tests/integration/conftest.py::_audit_pg_dsn`'s
convention, i.e. the Postgres `docker-compose.yml` actually publishes
(`ports: ["${MAEZO_PG_HOST_PORT:-5433}:5432"]`) and the one CI's integration/chaos lanes serve
(both pin `MAEZO_PG_HOST_PORT=5432`). This file's old default was already reachable against the
local compose stack, so — unlike the five other `tests/unit/**/*_live_pg.py` suites, which
defaulted to ports nobody ever served — it was not silently skipping locally; honouring
`MAEZO_PG_HOST_PORT` is what makes it reachable on a runner that publishes the SAME compose
Postgres on 5432, so a CI job that adds the service does not have to special-case this file.
A default nobody serves is a silent skip, not a proof.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.a2a.facts import DelegationFactKind, build_fact
from maezo.a2a.outbox import (
    ENQUEUE_SQL,
    PostgresFactOutbox,
    PostgresOutboxFactProducer,
    outbox_transaction,
)
from maezo.a2a.outbox_relay import drain_once
from maezo.gateway.audit_postgres import normalize_dsn

pytestmark = pytest.mark.integration

_MIGRATION = (
    Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions/0008_a2a_fact_outbox.py"
)


def _outbox_ddl() -> list[str]:
    """The REAL `upgrade()` SQL from migration 0008 — extracted, never re-typed."""
    source = _MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    statements = re.findall(r'op\.execute\("""(.*?)"""\)', upgrade, re.DOTALL)
    assert statements, "non-vacuity: migration 0008 must contain executable DDL"
    return statements


def _default_test_dsn() -> str:
    """`MAEZO_TEST_DATABASE_URL` wins; otherwise the compose Postgres (gap LIVE-SUITES-SILENT-SKIP-AUDIT).

    Mirrors `tests/integration/conftest.py::_audit_pg_dsn` exactly, so the default is a server
    that actually exists in both environments that run tests: the local compose stack
    (`${MAEZO_PG_HOST_PORT:-5433}`) and a CI job that pins `MAEZO_PG_HOST_PORT=5432`.
    """
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — "
            "a2a_fact_outbox live tests SKIPPED (visible, not silent). Start it with "
            "`docker compose --profile core up -d postgres` and re-run this file."
        )
    return dsn


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = f"ob{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        for statement in _outbox_ddl():
            await conn.execute(statement)
    finally:
        await conn.close()
    yield tenant_id
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


def _fact(
    kind: DelegationFactKind = DelegationFactKind.REQUESTED, task_id: str = "t1", *, tenant: str = "amh"
) -> bytes:
    return build_fact(
        kind,
        task_id=task_id,
        task_type="authorization.analyze",
        tenant=tenant,
        origin="helena",
        target="rafael",
        delegation_chain=("helena", "rafael"),
    ).to_value()


async def _row_count(dsn: str, schema: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema}"')
        count = await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox")
    finally:
        await conn.close()
    return int(count)


class _RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        self.sent.append((topic, value, key))


# ---------------------------------------------------------------------------
# 1. Transactional atomicity — both directions
# ---------------------------------------------------------------------------


async def test_a_rollback_of_the_enlisting_transaction_leaves_no_fact_row(
    pg_dsn: str, tenant_schema: str
) -> None:
    """The effect rolls back => the fact row does not exist. Real SQL rollback, real table."""
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        with pytest.raises(RuntimeError, match="the caller's effect failed"):
            async with conn.transaction(), outbox_transaction(conn):
                await outbox.enqueue("agents.events.delegation.requested", _fact(tenant=tenant_schema))
                # Non-vacuity: the row IS visible inside the transaction before the rollback.
                assert await conn.fetchval("SELECT count(*) FROM a2a_fact_outbox") == 1
                raise RuntimeError("the caller's effect failed")
    finally:
        await conn.close()
        await outbox.aclose()

    assert await _row_count(pg_dsn, tenant_schema) == 0


async def test_a_commit_of_the_enlisting_transaction_leaves_exactly_one_fact_row(
    pg_dsn: str, tenant_schema: str
) -> None:
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        async with conn.transaction(), outbox_transaction(conn):
            await outbox.enqueue("agents.events.delegation.requested", _fact(tenant=tenant_schema))
    finally:
        await conn.close()
        await outbox.aclose()

    assert await _row_count(pg_dsn, tenant_schema) == 1


async def test_without_enlistment_the_row_commits_on_its_own(pg_dsn: str, tenant_schema: str) -> None:
    """The posture the live composition roots actually use today: the outbox's own
    single-statement transaction. Durable, and NOT atomic with anything else."""
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    try:
        await outbox.enqueue("agents.events.delegation.requested", _fact())
    finally:
        await outbox.aclose()
    assert await _row_count(pg_dsn, tenant_schema) == 1


# ---------------------------------------------------------------------------
# 2-3. At-least-once by lease expiry; the lease guard
# ---------------------------------------------------------------------------


async def test_an_unmarked_claim_is_reclaimed_after_its_lease_expires(
    pg_dsn: str, tenant_schema: str
) -> None:
    """The crash window, against real SQL: claim, never mark (the process died), wait out the
    lease, and the row comes back with the SAME dedup_key and an incremented attempts."""
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    try:
        await outbox.enqueue("agents.events.delegation.requested", _fact())

        first = await outbox.claim_batch(claimed_by="w1", claim_ttl_s=0.2)
        assert len(first) == 1 and first[0].attempts == 1

        # Still leased: another worker must not steal it.
        assert await outbox.claim_batch(claimed_by="w2", claim_ttl_s=0.2) == []

        await asyncio.sleep(0.35)
        second = await outbox.claim_batch(claimed_by="w2", claim_ttl_s=5.0)
        assert len(second) == 1
        assert second[0].dedup_key == first[0].dedup_key == "amh:a2a:delegate:t1:requested"
        assert second[0].payload == first[0].payload
        assert second[0].attempts == 2
    finally:
        await outbox.aclose()


async def test_a_previous_owner_cannot_seal_a_stolen_row(pg_dsn: str, tenant_schema: str) -> None:
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    try:
        row_id = await outbox.enqueue("agents.events.delegation.requested", _fact())
        await outbox.claim_batch(claimed_by="w1", claim_ttl_s=0.2)
        await asyncio.sleep(0.35)
        await outbox.claim_batch(claimed_by="w2", claim_ttl_s=5.0)

        assert await outbox.mark_delivered([row_id], claimed_by="w1") == 0
        assert (await outbox.counts_by_status()).get("claimed") == 1
        assert await outbox.mark_delivered([row_id], claimed_by="w2") == 1
        assert (await outbox.counts_by_status()).get("delivered") == 1
    finally:
        await outbox.aclose()


async def test_mark_failed_releases_the_row_and_records_the_error(pg_dsn: str, tenant_schema: str) -> None:
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        row_id = await outbox.enqueue("agents.events.delegation.requested", _fact())
        await outbox.claim_batch(claimed_by="w1", claim_ttl_s=30.0)
        assert await outbox.mark_failed([row_id], claimed_by="w1", error="ConnectionError: down") == 1

        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        row = await conn.fetchrow(
            "SELECT status, attempts, last_error, claim_expires_at FROM a2a_fact_outbox WHERE id = $1",
            row_id,
        )
        assert row["status"] == "pending"
        assert row["attempts"] == 1  # not decremented — a stuck row shows a growing attempt count
        assert row["last_error"] == "ConnectionError: down"
        assert row["claim_expires_at"] is None  # the lease CHECK is biconditional
    finally:
        await conn.close()
        await outbox.aclose()


# ---------------------------------------------------------------------------
# 4. The CHECK constraints actually reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "columns", "values"),
    [
        ("delivered without a timestamp", "status", "'delivered'"),
        ("claimed without a lease", "status", "'claimed'"),
        ("unknown status", "status", "'sent'"),
        ("negative attempts", "attempts", "-1"),
    ],
)
async def test_the_lifecycle_checks_reject_invalid_rows(
    pg_dsn: str, tenant_schema: str, label: str, columns: str, values: str
) -> None:
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO a2a_fact_outbox "
                f"(tenant, dedup_key, topic, payload, {columns}) VALUES ('amh','k','t','\\x7b7d', {values})"
            )
    finally:
        await conn.close()


async def test_a_delivered_timestamp_on_a_pending_row_is_rejected(pg_dsn: str, tenant_schema: str) -> None:
    """The BICONDITIONAL half a one-directional check would let through: a row an operator reads
    as delivered while the relay reads it as pending."""
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO a2a_fact_outbox (tenant, dedup_key, topic, payload, delivered_at) "
                "VALUES ('amh','k','t','\\x7b7d', now())"
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 5. dedup_key is genuinely non-unique
# ---------------------------------------------------------------------------


async def test_two_rows_may_carry_the_same_dedup_key(pg_dsn: str, tenant_schema: str) -> None:
    """A UNIQUE constraint here would raise `UniqueViolationError` out of `FactProducer.emit`, i.e.
    out of `DelegationDispatcher.delegate` — dropping a DELEGATION to protect a duplicate FACT."""
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    try:
        first = await outbox.enqueue("agents.events.delegation.requested", _fact())
        second = await outbox.enqueue("agents.events.delegation.requested", _fact())
        assert first != second
        assert await _row_count(pg_dsn, tenant_schema) == 2
    finally:
        await outbox.aclose()


# ---------------------------------------------------------------------------
# 6. End to end through the real producer + relay
# ---------------------------------------------------------------------------


async def test_the_producer_and_relay_round_trip_through_real_postgres(
    pg_dsn: str, tenant_schema: str
) -> None:
    """`PostgresOutboxFactProducer.send` -> real row -> `drain_once` -> published + delivered."""
    outbox = PostgresFactOutbox(dsn=pg_dsn, tenant=tenant_schema)
    producer = PostgresOutboxFactProducer(outbox)
    publisher = _RecordingPublisher()
    try:
        value = _fact(DelegationFactKind.COMPLETED, "t-e2e")
        await producer.send("agents.events.delegation.completed", value, key=b"amh")

        report = await drain_once(outbox, publisher, claimed_by="w1")
        assert (report.claimed, report.delivered, report.sealed) == (1, 1, 1)
        assert publisher.sent == [("agents.events.delegation.completed", value, b"amh")]
        assert await outbox.counts_by_status() == {"delivered": 1}

        # A second sweep finds nothing: delivered rows leave the drain scan.
        assert (await drain_once(outbox, publisher, claimed_by="w1")).claimed == 0
    finally:
        await outbox.aclose()


async def test_the_enqueue_sql_constant_is_the_one_that_actually_runs(
    pg_dsn: str, tenant_schema: str
) -> None:
    """Non-vacuity for the fake-conn suite's SQL assertions: the exact `ENQUEUE_SQL` string it
    pins is executable against the real DDL."""
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        row_id = await conn.fetchval(ENQUEUE_SQL, "amh", "k", "t", "amh", b"{}")
        assert row_id is not None
    finally:
        await conn.close()
