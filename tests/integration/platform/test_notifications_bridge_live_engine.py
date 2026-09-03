"""EB-4 LIVE proof (REAL engine + REAL Postgres audit chain): the reconciled bridge and the
un-stubbed in-flow worker BOTH start a real downstream SP-OP-RECURSO-001 instance through the
fenced `start_process_idempotent` chokepoint, and BOTH emit the ADR-0007 start record.

Distinct from `test_notifications_bridge_live_pg.py` (which stands the engine in with a
`FakeCibSevenTransport`, PG-only): this suite drives a REAL CIB Seven engine, so it proves the
full `event -> bridge/worker -> real downstream process instance STARTED (queried by business key)
-> audit row SELECTed` chain end-to-end. Skips LOUDLY (never fakes) when either the engine or
Postgres is unreachable, mirroring the parent integration conftest's could-not-verify posture.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest

from maezo.gateway.audit_postgres import (
    FreshSinkAuditEmitter,
    PostgresAuditSink,
    normalize_dsn,
    schema_for_tenant,
)
from maezo.platform.notification_bridge import (
    RECURSO_INTAKE_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.contas import start_recurso
from tests.integration.conftest import _apply_migrations, _engine_reachable, _pg_reachable

# ---------------------------------------------------------------------------
# m10 (gatekeeper R1 do PR-3) — ESTE ARQUIVO NAO RODA EM NENHUMA PERNA DE CI HOJE.
#
# Nao ha `pytestmark = pytest.mark.integration` aqui — e nenhum arquivo de
# `tests/integration/platform/` tem, e a convencao do diretorio e auto-skip pelas fixtures de
# alcancabilidade (`_engine_reachable`/`_pg_reachable`), nao marker. O efeito medido pelo
# gatekeeper: sob `pytest -m integration` estas suites saem como `deselected` (rc=5) e sob
# `-m "not integration"` elas sao coletadas mas SKIPPAM (sem stack). Resultado liquido: elas
# nunca executam de verdade.
#
# Isso importa em particular para `test_bridge_reconciled_event_starts_real_instance_and_audits`
# abaixo: e o UNICO teste que provaria a regra `agents.events.recurso.intake_recebido` iniciando
# uma instancia REAL de SP-OP-RECURSO-001 (ADR-0040 §3.1, OQ-R1).
#
# NAO corrigido aqui de proposito: a condicao e PRE-EXISTENTE (nao introduzida pelo PR-3) e vale
# para os quatro arquivos do diretorio, incluindo dois que este PR nao toca
# (`test_notifications_bridge_live_pg.py`, `test_events_kafka_producer_live.py`). Marcar so os
# dois que o PR-3 toca deixaria o diretorio com duas convencoes; a correcao e do harness e vai
# como item do orquestrador. Divulgado aqui, no ponto do defeito, e nao apenas no corpo do PR.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.integration

_REPO_RECURSO_BPMN = "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"


def _audit_dsn() -> str:
    import os

    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


@pytest.fixture
def live_tenant(engine_base_url: str) -> Iterator[tuple[str, str, str]]:
    """(dsn, tenant, engine_url) with a fresh migrated schema + RECURSO deployed. Skips loudly."""
    import asyncio
    import pathlib

    # This directory's conftest no-ops the parent engine gate (its Kafka suite needs no engine),
    # so THIS engine-driven suite must check reachability itself — skip loudly, never fake.
    if not _engine_reachable(engine_base_url):
        pytest.skip(f"COULD NOT VERIFY: CIB Seven engine unreachable at {engine_base_url}")
    dsn = _audit_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(f"COULD NOT VERIFY: Postgres unreachable at {dsn!r}")

    tenant = f"it_eb4_{uuid.uuid4().hex[:6]}"
    schema = schema_for_tenant(tenant)

    async def _create() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        finally:
            await conn.close()

    async def _drop() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create())
    _apply_migrations(dsn, tenant)

    # Deploy the REAL RECURSO-001 process so the fenced start has a definition to start.
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    with open(repo_root / _REPO_RECURSO_BPMN, "rb") as fh:
        bpmn = fh.read()
    with httpx.Client(base_url=engine_base_url, timeout=60.0) as c:
        resp = c.post(
            "/deployment/create",
            data={"deployment-name": f"eb4-live-{tenant}", "enable-duplicate-filtering": "true"},
            files={"SP-OP-RECURSO-001.bpmn": ("SP-OP-RECURSO-001.bpmn", bpmn, "text/xml")},
        )
        assert resp.status_code < 300, resp.text
    try:
        yield (dsn, tenant, engine_base_url)
    finally:
        asyncio.run(_drop())


async def _audit_count(dsn: str, tenant: str, action: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema_for_tenant(tenant)}"')
        count = await conn.fetchval("SELECT count(*) FROM audit_chain WHERE action = $1", action)
        return int(count)
    finally:
        await conn.close()


async def _engine_instance(engine_url: str, business_key: str) -> str | None:
    t = CibSevenHttpTransport(engine_url)
    try:
        inst = await t.find_active_instance(business_key)
        return inst.instance_id if inst else None
    finally:
        await t.close()


def test_worker_start_recurso_starts_real_instance_and_audits(
    live_tenant: tuple[str, str, str],
) -> None:
    """The un-stubbed in-flow worker starts a REAL RECURSO-001 instance + emits the audit row;
    a redelivery is idempotent (same instance, no second start).

    SYNC test: `start_recurso` drives its own `asyncio.run(start_process_idempotent(...))`, so it
    must NOT be called from inside a running event loop (the query helpers use `asyncio.run` too).
    """
    import asyncio

    dsn, tenant, engine_url = live_tenant
    suffix = uuid.uuid4().hex[:8]
    guia, glosa = f"GUIA-{suffix}", f"GLOSA-{suffix}"
    bk = f"RECURSO-{tenant}-{guia}-{glosa}"
    variables = {
        "tenant_id": tenant,
        "glosa_id": glosa,
        "numero_guia_tiss": guia,
        "glosa_type": "administrativa",
        "documentacao_anexa": True,
        "numero_lote_tiss": "LOTE-1",
    }

    def _run() -> dict[str, object]:
        return start_recurso(
            variables,
            engine=FreshClientCibSevenTransport(engine_url),
            audit_sink=FreshSinkAuditEmitter(dsn, tenant),
        )

    res = _run()
    assert res["recurso_business_key"] == bk
    assert res["recurso_already_existed"] is False
    assert asyncio.run(_engine_instance(engine_url, bk)) == res["recurso_instance_id"]
    assert asyncio.run(_audit_count(dsn, tenant, "start_process:SP-OP-RECURSO-001")) == 1

    # Idempotent redelivery: same instance, still exactly one durable chain link.
    res2 = _run()
    assert res2["recurso_instance_id"] == res["recurso_instance_id"]
    assert res2["recurso_already_existed"] is True
    assert asyncio.run(_audit_count(dsn, tenant, "start_process:SP-OP-RECURSO-001")) == 1


@pytest.mark.asyncio
async def test_bridge_reconciled_event_starts_real_instance_and_audits(
    live_tenant: tuple[str, str, str],
) -> None:
    """The intake bridge rule (agents.events.recurso.intake_recebido, ADR-0040 §3.1) starts a REAL
    RECURSO-001 instance through the fence with agent_id=notification_bridge; a payload without
    the business-key anchors starts nothing (negative).

    The event has no publisher in `main` (OQ-R1) — this proves the RULE works when one exists;
    the absence of a publisher is proved by
    `test_regra_intake_recurso_e_dormente_ate_o_adaptador_existir`."""
    dsn, tenant, engine_url = live_tenant
    suffix = uuid.uuid4().hex[:8]
    guia, glosa = f"GUIAB-{suffix}", f"GLOSAB-{suffix}"
    bk = f"RECURSO-{tenant}-{guia}-{glosa}"

    transport = CibSevenHttpTransport(engine_url)
    sink = PostgresAuditSink(dsn, tenant)
    try:
        bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, sink))
        # Positive: matching desfecho + anchors -> real start.
        pos = await bridge.on_event(
            RECURSO_INTAKE_EVENT,
            {
                "tenant_id": tenant,
                "numero_guia_tiss": guia,
                "glosa_id": glosa,
            },
        )
        triggered = [r for r in pos if r.handoff_triggered]
        assert len(triggered) == 1
        assert await _engine_instance(engine_url, bk) == triggered[0].process_instance_id

        # Negative: missing business-key anchor -> nothing starts, nothing audited for it.
        neg = await bridge.on_event(
            RECURSO_INTAKE_EVENT,
            {"tenant_id": tenant, "numero_guia_tiss": "X"},
        )
        assert not [r for r in neg if r.handoff_triggered]
    finally:
        await transport.close()
        await sink.aclose()

    # Exactly one durable start record for the positive case (agent_id notification_bridge).
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema_for_tenant(tenant)}"')
        rows = await conn.fetch(
            "SELECT agent_id FROM audit_chain WHERE action = 'start_process:SP-OP-RECURSO-001'"
        )
    finally:
        await conn.close()
    assert [r["agent_id"] for r in rows] == ["notification_bridge"]
