"""LIVE-PG fenced-start audit-row proof (T2.6-EB3 TESTS section) — port 5647 by default.

Reuses the T2.6-7 fenced-start proof pattern
(`tests/integration/processes/test_sp_op_nip_001.py::
test_nip_handoff_end_to_end_starts_ans_submit_via_live_bridge`) but scoped to the AUDIT lane
ONLY, per this task's explicit boundary: "NO cibseven engine needed for the bridge-start unit
level (the start is mocked/fenced-proven); if you want a real process start, that needs
cibseven — scope to PG-level fenced-start proof + document." A `FakeCibSevenTransport` stands in
for the engine side (no real process is started — just as the unit suite already proves);
`PostgresAuditSink` is REAL, against a real Postgres with migrations 0001->0005 applied to a
per-run tenant schema. This proves the fenced starter
(`build_cibseven_process_starter` -> `start_process_idempotent`) durably persists the ADR-0007
audit row for a REAL bridge handoff (not a synthetic call site), and that a redelivery of the
SAME event is idempotent at the audit layer (`emit_once`'s exactly-once dedup — no second chain
link for a repeated business key).

Reachability convention (mirrors `tests/unit/gateway/test_audit_postgres.py` /
`tests/integration/conftest.py`'s own DSN resolution): `MAEZO_TEST_DATABASE_URL` wins; otherwise
`postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5647}/maezo` — 5647 (not the 5433
dev-default / 5432 CI-default) so this suite never collides with an already-running Postgres on
a shared dev machine. This directory's `conftest.py` already overrides the parent integration
suite's CIB-Seven-engine autouse gate (this suite needs Postgres only), so a missing broker/engine
elsewhere never blocks this file — only Postgres unreachability does, and that SKIPS LOUDLY
("COULD NOT VERIFY", ADR-0011 posture), never fakes a database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, schema_for_tenant
from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.integration.conftest import _apply_migrations, _pg_reachable

_DEFAULT_PORT = "5647"


def _pg_dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", _DEFAULT_PORT)
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


@pytest.fixture
def pg_tenant_schema() -> Iterator[tuple[str, str]]:
    """Per-test throwaway tenant schema with REAL migrations 0001->0005 applied. Skips LOUDLY
    when Postgres is unreachable (never fakes it).

    Deliberately a PLAIN (non-async) fixture using `asyncio.run()` per operation — mirrors
    `tests/integration/conftest.py`'s own `audit_pg` fixture shape exactly, and avoids nesting
    `asyncio.run()` inside pytest-asyncio's already-running per-test loop (`_pg_reachable`
    itself calls `asyncio.run()`).
    """
    dsn = _pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(
            f"COULD NOT VERIFY: no reachable Postgres at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL / MAEZO_PG_HOST_PORT). T2.6-EB3's LIVE-PG fenced-start "
            "proof needs a real Postgres with migrations 0001-0005 applied — bring one up, e.g.:\n"
            "  docker run -d -p 5647:5432 -e POSTGRES_USER=maezo -e POSTGRES_PASSWORD=maezo "
            "-e POSTGRES_DB=maezo pgvector/pgvector:pg16\n"
            "to run this for real."
        )

    tenant_id = f"eb3{uuid.uuid4().hex[:10]}"  # [a-z][a-z0-9]* — no cross-run dedup residue

    async def _create_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    async def _drop_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create_schema())
    _apply_migrations(dsn, tenant_id)

    yield dsn, tenant_id

    asyncio.run(_drop_schema())


async def _fetch_chain_rows(dsn: str, tenant_id: str) -> list[Any]:
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(f'SELECT action, agent_id FROM "{schema}".audit_chain ORDER BY id')
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_bridge_handoff_through_fenced_starter_emits_durable_audit_row(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """A REAL bridge handoff (CONTAS->RECURSO, the reconciled EB-4 `agents.events.contas.
    completed`/`desfecho=encaminhada_recurso` rule — this test previously drove the
    pre-reconciliation `contas.glosa_confirmed` literal, which `notification_bridge.py` no longer
    registers at all) through the fenced starter durably persists the ADR-0007 audit row BEFORE
    the (faked) engine effect — proven by querying `audit_chain` directly, not merely by
    `emit_once` not raising. The payload is a SYNTHETIC enriched one (carries `numero_guia_tiss`,
    the business-key anchor today's real minimal `event_payload_vars` does not yet emit — EB-4
    "arming" follow-up note in `notification_bridge.py`)."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    try:
        results = await bridge.on_event(
            event_type=CONTAS_COMPLETED_EVENT,
            payload={
                "tenant_id": tenant_id,
                "desfecho": "encaminhada_recurso",
                "glosa_id": "GLOSA-LIVE-001",
                "numero_guia_tiss": "GUIA-LIVE-001",
            },
        )
        assert results[0].handoff_triggered is True
        assert results[0].process_instance_id  # the fenced starter actually returned an instance

        rows = await _fetch_chain_rows(dsn, tenant_id)
        assert len(rows) == 1
        assert rows[0]["action"] == "start_process:SP-OP-RECURSO-001"
        assert rows[0]["agent_id"] == "notification_bridge"
    finally:
        await audit_sink.aclose()
        await transport.close()


@pytest.mark.asyncio
async def test_bridge_handoff_redelivery_is_idempotent_at_the_audit_layer(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """Two `on_event` calls for the identical event (a Kafka redelivery) converge on the SAME
    process_instance_id and write exactly ONE audit_chain row — `emit_once`'s exactly-once dedup
    holds for a genuine bridge call, not just the chokepoint's own isolated unit suite."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    payload = {
        "tenant_id": tenant_id,
        "desfecho": "encaminhada_recurso",
        "glosa_id": "GLOSA-LIVE-002",
        "numero_guia_tiss": "GUIA-LIVE-002",
    }
    try:
        first = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=dict(payload))
        second = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=dict(payload))

        assert first[0].handoff_triggered is True
        assert second[0].handoff_triggered is True
        assert first[0].process_instance_id == second[0].process_instance_id

        rows = await _fetch_chain_rows(dsn, tenant_id)
        assert len(rows) == 1, "a redelivered handoff must not write a second audit-chain link"
    finally:
        await audit_sink.aclose()
        await transport.close()
