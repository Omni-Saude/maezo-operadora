"""MZO-1c — `UNIQUE` multi-match pelo MOTOR REAL (CIB Seven, ADR-0011/ADR-0028), nunca por mock.

O teste de unidade (`tests/unit/tools/workers/test_dmn_transport_unique_multimatch.py`) replica o
corpo de erro que o codigo do engine-rest produz; ESTE arquivo prova o que so o motor responde:

  1. a forma do erro no fio: HTTP 500, `type` `RestException` (ou a excecao crua), mensagem com
     `DMN-03001` e `'UNIQUE'` — a mesma que `dmn_transport._is_unique_multimatch` casa;
  2. o transporte vira isso em `DmnHitPolicyViolationError` (PERMANENTE) com a versao resolvida, e
     a mesma tabela fora da sobreposicao segue devolvendo uma linha;
  3. o caminho inteiro no harness real: a PRIMEIRA entrega ja abre incidente (retries=0, mesmo com
     orcamento de retry 3) e a cadeia ADR-0007 do Postgres do lane ganha a linha REFUSED.

A tabela e FIXTURE DE TESTE (`tests/fixtures/dmn/teste_rco_unique_sobreposicao.dmn`), nunca de
`spec/`: `rco_exemplo_sintetico` foi desenhada para NAO sobrepor (ADR-0075 secao c). Sem motor
alcancavel, o pacote inteiro PULA com motivo explicito (`tests/integration/conftest.py`).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import (
    ERR_DMN_HIT_POLICY_VIOLATION,
    CibSevenDmnTransport,
    DmnHitPolicyViolationError,
    evaluate_sync,
    first_row,
)
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    TopicSubscription,
    WorkerHarness,
)

from ..conftest import RUN_ID, deploy_process, list_incidents

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "dmn" / "teste_rco_unique_sobreposicao.dmn"
_KEY = "teste_rco_unique_sobreposicao"
_SOBREPOSTO = {"teste_valor": 45}  # casa as duas linhas (0..50 e 40..120)
_SO_A = {"teste_valor": 20}  # casa so a primeira


@pytest.fixture
async def fixture_deployed(engine_client: httpx.AsyncClient) -> str:
    """Envia a fixture ao motor (idempotente: duplicate-filtering) e devolve o id da definicao."""
    resp = await engine_client.post(
        "/deployment/create",
        data={
            "deployment-name": "mzo-1c-teste-unique-sobreposicao",
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files={_FIXTURE.name: (_FIXTURE.name, _FIXTURE.read_bytes(), "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")
    definition = await engine_client.get(f"/decision-definition/key/{_KEY}")
    definition.raise_for_status()
    return str(definition.json()["id"])


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


async def test_engine_wire_shape_is_what_the_transport_matches(
    engine_client: httpx.AsyncClient, fixture_deployed: str
) -> None:
    resp = await engine_client.post(
        f"/decision-definition/key/{_KEY}/evaluate",
        json={"variables": {"teste_valor": {"value": 45, "type": "Integer"}}},
    )
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["type"] in {"RestException", "DmnHitPolicyException"}, body
    assert "DMN-03001" in body["message"], body
    assert "'UNIQUE'" in body["message"], body


async def test_transport_raises_permanent_error_with_resolved_version(
    dmn: CibSevenDmnTransport, fixture_deployed: str
) -> None:
    with pytest.raises(DmnHitPolicyViolationError) as excinfo:
        await dmn.evaluate(_KEY, _SOBREPOSTO)
    assert excinfo.value.code == ERR_DMN_HIT_POLICY_VIOLATION
    assert excinfo.value.version is not None
    assert excinfo.value.version.id == fixture_deployed
    assert "FAIXA_" not in str(excinfo.value)  # o dump de regras do motor nunca vaza

    rows, version = await dmn.evaluate(_KEY, _SO_A)
    assert rows == [{"faixa": "FAIXA_A"}]
    assert version.id == fixture_deployed


def _entry(variables: dict[str, Any], *, dmn: CibSevenDmnTransport) -> dict[str, Any]:
    rows, _version = evaluate_sync(dmn, _KEY, {"teste_valor": variables["teste_valor"]})
    return dict(first_row(rows, _KEY))


async def _fetch_one(transport: CibSevenWorkerTransport, *, worker_id: str, topic: str) -> ExternalTask:
    deadline = asyncio.get_event_loop().time() + 15.0
    while asyncio.get_event_loop().time() < deadline:
        tasks = await transport.fetch_and_lock(
            worker_id, [TopicSubscription(topic, 30_000)], max_tasks=1, async_response_timeout_ms=500
        )
        if tasks:
            return tasks[0]
        await asyncio.sleep(0.5)
    raise AssertionError(f"no task fetched on topic {topic!r} within 15s")


async def test_harness_first_delivery_opens_incident_and_audits_refusal(
    engine_base_url: str,
    engine_client: httpx.AsyncClient,
    fixture_deployed: str,
    dmn: CibSevenDmnTransport,
    audit_pg: tuple[str, str],
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    process_key = f"mzo1c_it_unique_{RUN_ID}"
    topic = f"mzo1c.it.unique.{RUN_ID}"
    await deploy_process(
        engine_client, process_key=process_key, topic=topic, deployment_name=f"mzo1c-unique-{RUN_ID}"
    )
    # Iniciado com a entrada sobreposta como variavel de processo: o worker a le do task.
    started = await engine_client.post(
        f"/process-definition/key/{process_key}/start",
        json={
            "businessKey": "bk-mzo1c",
            "variables": {"teste_valor": {"value": _SOBREPOSTO["teste_valor"], "type": "Integer"}},
        },
    )
    if started.status_code >= 300:
        raise AssertionError(f"start-process failed: {started.status_code} {started.text}")
    process_instance_id = str(started.json()["id"])

    worker_id = f"it-worker-{RUN_ID}"
    transport = CibSevenWorkerTransport(engine_base_url)
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        max_retry_attempts=3,
        async_response_timeout_ms=2_000,
        audit_sink=audit_sink,
    )
    harness.register_worker(FunctionWorker(topic, functools.partial(_entry, dmn=dmn)))
    try:
        task = await _fetch_one(transport, worker_id=worker_id, topic=topic)
        await harness._handle(task)
    finally:
        await transport.close()

    # PRIMEIRA entrega -> incidente: nenhum retry consumido, caso na frente de um humano.
    incidents = await list_incidents(engine_client, process_instance_id)
    assert len(incidents) == 1, incidents
    assert ERR_DMN_HIT_POLICY_VIOLATION in (incidents[0].get("incidentMessage") or ""), incidents

    dsn, tenant_id = audit_pg
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        rows = await conn.fetch(
            f"SELECT decision, decision_basis::text AS basis, dmn_versions::text AS versions "
            f'FROM "{tenant_id}".audit_chain WHERE action = $1',
            topic,
        )
    finally:
        await conn.close()
    assert len(rows) == 1, rows
    assert rows[0]["decision"] == "REFUSED"
    assert ERR_DMN_HIT_POLICY_VIOLATION in rows[0]["basis"]
    assert _KEY in rows[0]["versions"] and fixture_deployed in rows[0]["versions"]
