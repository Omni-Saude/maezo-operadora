"""Real-Postgres integration for the worker-daemon audit sink + readiness gate (T1.10 T-D).

Mirrors the ADR-0024 / T1.10 real-PG suite pattern (`tests/unit/gateway/test_audit_postgres.py`):
`@pytest.mark.integration`, a `pg_dsn` fixture that SKIPS LOUDLY when Postgres is unreachable (never
a silent pass — ADR-0011), and a per-test tenant schema created with the `audit_chain` +
`audit_emit_dedup` DDL (the same DDL `0002_audit_chain.py` / `0005_audit_emit_dedup.py` ship; the
migrations themselves are run end-to-end separately — see the PR VERIFY section).

NO CIB Seven engine is involved: this proves the T-D deliverables against live Postgres only —
`PostgresAuditSink.check_ready()`, the `audit_sink_ready` readiness gate flipping green, and the
emit path writing a verifiable `audit_chain` row (consuming T-A's `emit_once`).

Run for real:
  docker compose -p td_sink up -d postgres   # unique project, MAEZO_PG_HOST_PORT=5542
  MAEZO_TEST_DATABASE_URL=postgresql://maezo:maezo@localhost:5542/maezo \
    uv run pytest tests/unit/runtime/test_worker_runtime_audit_sink_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import (
    AuditPersistenceError,
    PostgresAuditSink,
    normalize_dsn,
    verify_chain,
)
from maezo.runtime.worker_runtime.service import (
    WorkerState,
    _probe_audit_sink,
    build_readiness_checks,
)
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

pytestmark = pytest.mark.integration


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


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
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — worker "
            "audit-sink DB-backed tests SKIPPED (visible, not silent). Start it with "
            "`docker compose -p td_sink up -d postgres` (MAEZO_PG_HOST_PORT=5542)."
        )
    return dsn


# Mirrors 0002_audit_chain.py / 0005_audit_emit_dedup.py upgrade() DDL exactly.
_AUDIT_CHAIN_DDL = """
    CREATE TABLE audit_chain (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        timestamp       timestamptz NOT NULL DEFAULT now(),
        tenant_id       text NOT NULL,
        agent_id        text NOT NULL,
        agent_version   text NOT NULL,
        action          text NOT NULL,
        decision        text NOT NULL,
        input_hash      text NOT NULL,
        decision_basis  jsonb NOT NULL DEFAULT '{}'::jsonb,
        dmn_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
        model_id        text,
        prompt_version  text,
        record_hash     text NOT NULL,
        prev_record_hash text,
        created_at      timestamptz NOT NULL DEFAULT now(),

        CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)
    )
"""
_AUDIT_EMIT_DEDUP_DDL = """
    CREATE TABLE audit_emit_dedup (
        tenant       text NOT NULL,
        dedup_key    text NOT NULL,
        record_hash  text NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),

        PRIMARY KEY (tenant, dedup_key)
    )
"""


async def _make_tenant_schema(dsn: str, *, with_table: bool = True) -> str:
    tenant_id = f"td{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        if with_table:
            await conn.execute(f'SET search_path TO "{tenant_id}"')
            await conn.execute(_AUDIT_CHAIN_DDL)
            await conn.execute(_AUDIT_EMIT_DEDUP_DDL)
    finally:
        await conn.close()
    return tenant_id


async def _drop_tenant_schema(dsn: str, tenant_id: str) -> None:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = await _make_tenant_schema(pg_dsn)
    yield tenant_id
    await _drop_tenant_schema(pg_dsn, tenant_id)


# ---------------------------------------------------------------------------
# check_ready — the bounded connectivity probe the readiness gate depends on
# ---------------------------------------------------------------------------


async def test_check_ready_passes_against_live_schema(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        await sink.check_ready()  # does not raise -> sink can durably audit
    finally:
        await sink.aclose()


async def test_check_ready_fails_closed_when_table_missing(pg_dsn: str) -> None:
    """A reachable server but a schema WITHOUT audit_chain -> check_ready RAISES (fail-closed):
    migrations-not-applied must keep the daemon out of the fetch rotation, not fake readiness."""
    tenant_id = await _make_tenant_schema(pg_dsn, with_table=False)
    try:
        sink = PostgresAuditSink(pg_dsn, tenant_id)
        try:
            with pytest.raises(AuditPersistenceError):
                await sink.check_ready()
        finally:
            await sink.aclose()
    finally:
        await _drop_tenant_schema(pg_dsn, tenant_id)


# ---------------------------------------------------------------------------
# audit_sink_ready readiness gate — flips GREEN against a live, migrated sink
# ---------------------------------------------------------------------------


async def test_audit_sink_ready_gate_flips_green(pg_dsn: str, tenant_schema: str) -> None:
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    settings = WorkerRuntimeSettings(TENANT_ID=tenant_schema, DATABASE_URL=pg_dsn)
    state = WorkerState(settings=settings, audit_sink=sink)
    try:
        assert await _probe_audit_sink(sink, settings.dep_connect_timeout_s) is True

        checks = {c.__name__: c for c in build_readiness_checks(state)}
        result = await checks["audit_sink_ready"]()
        assert result.healthy is True
    finally:
        await sink.aclose()


async def test_audit_sink_ready_gate_red_when_table_missing(pg_dsn: str) -> None:
    tenant_id = await _make_tenant_schema(pg_dsn, with_table=False)
    try:
        sink = PostgresAuditSink(pg_dsn, tenant_id)
        settings = WorkerRuntimeSettings(TENANT_ID=tenant_id, DATABASE_URL=pg_dsn)
        state = WorkerState(settings=settings, audit_sink=sink)
        try:
            checks = {c.__name__: c for c in build_readiness_checks(state)}
            result = await checks["audit_sink_ready"]()
            assert result.healthy is False
        finally:
            await sink.aclose()
    finally:
        await _drop_tenant_schema(pg_dsn, tenant_id)


# ---------------------------------------------------------------------------
# emit path — a live sink writes a verifiable audit_chain row (consumes T-A's emit_once)
# ---------------------------------------------------------------------------


async def test_emit_once_writes_row_and_chain_verifies(pg_dsn: str, tenant_schema: str) -> None:
    """The headline: a worker-daemon-constructed sink durably persists an effect via T-A's
    `emit_once`, the row is queryable, the chain verifies, and a re-delivered effect (same
    dedup_key) is deduped to the SAME record (no second link)."""
    sink = PostgresAuditSink(pg_dsn, tenant_schema)
    dedup_key = f"{tenant_schema}:task-abc123"
    try:
        record = AuditRecord(
            agent_id="operadora-worker",
            tenant_id=tenant_schema,
            agent_version="0.2.0",
            action="operadora.inadimplencia.handoff_rescisao",
            decision="ALLOW",
            details={"input_sha256": "deadbeef", "roteamento": "rescisao_handoff"},
        )
        first_hash = await sink.emit_once(record, dedup_key=dedup_key)
        assert first_hash

        # Re-delivery of the SAME effect -> deduped, no second chain link, same identity back.
        replay = AuditRecord(
            agent_id="operadora-worker",
            tenant_id=tenant_schema,
            agent_version="0.2.0",
            action="operadora.inadimplencia.handoff_rescisao",
            decision="ALLOW",
            details={"input_sha256": "deadbeef", "roteamento": "rescisao_handoff"},
        )
        replay_hash = await sink.emit_once(replay, dedup_key=dedup_key)
        assert replay_hash == first_hash
    finally:
        await sink.aclose()

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        rows = await conn.fetch("SELECT agent_id, action, record_hash FROM audit_chain")
    finally:
        await conn.close()

    assert len(rows) == 1, "exactly one durable row for one effect (dedup prevented a double)"
    assert rows[0]["agent_id"] == "operadora-worker"
    assert rows[0]["action"] == "operadora.inadimplencia.handoff_rescisao"

    result = await verify_chain(pg_dsn, tenant_schema)
    assert result.valid is True
    assert result.total_records == 1
    assert result.verified_records == 1
