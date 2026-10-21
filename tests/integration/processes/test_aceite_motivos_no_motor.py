"""Cada motivo de escalonamento que o roteiro de aceite exige chega, no motor REAL, a
`UT_TratarEscalonamento` no grupo que a DMN `escalation_routing` decide.

Os motivos sao LIDOS de `maezo.qa.simulador.aceite.ROTEIROS` (`tests/support/aceite_motivos.py`),
nunca digitados; o rotulo `cancelamento` fica de fora porque abre SP-OP-CANCEL-001, nao este processo
(ver a cerca `tests/unit/qa/test_aceite_motivos_cobertos.py`). As severidades tambem vem do codigo:
`_MOTIVO_SEM_SEVERIDADE` -> so' `null` (o que Lucas/bridge mandam); os demais -> o dominio contratual
`_SEVERIDADES_CONTRATUAIS`. A forma das variaveis e' a do start canonico dos agentes
(`test_sp_op_escalation_001.start_escalation`), no tenant da execucao.

Real: motor CIB Seven (BPMN + DMN deste checkout, no tenant), workers de producao
(`register_events_workers` + `register_escalation_workers`) com o `AioKafkaEventsProducer` no broker
real, auditoria Postgres real. O grupo esperado e' a DMN viva avaliada com as MESMAS entradas.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from maezo.platform.integrations.events_kafka_producer import AioKafkaEventsProducer
from maezo.runtime.worker_runtime.service import PRODUCTION_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.escalation import (
    _MOTIVO_SEM_SEVERIDADE,
    _SEVERIDADES_CONTRATUAIS,
    register_escalation_workers,
)
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness
from tests.integration.agents._engine_helpers import candidate_groups
from tests.support.aceite_motivos import motivos_de_escalonamento
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table
from tests.support.engine_tenant_deploy import deploy_for_tenant
from tests.support.escalation_chain import drenar_por_chave, kafka_bootstrap_servers, tarefas_humanas

from .engine_rest import EngineRest

pytestmark = pytest.mark.integration


def _casos() -> list[tuple[str, str | None]]:
    casos: list[tuple[str, str | None]] = []
    for motivo in sorted(motivos_de_escalonamento()):
        severidades: list[str | None] = (
            [None] if motivo in _MOTIVO_SEM_SEVERIDADE else sorted(_SEVERIDADES_CONTRATUAIS)
        )
        casos.extend((motivo, severidade) for severidade in severidades)
    return casos


@pytest.fixture(scope="module", autouse=True)
def _escalation_do_tenant(engine_base_url: str, audit_tenant: str) -> None:
    deploy_for_tenant(engine_base_url, audit_tenant, "SP-OP-ESCALATION-001")


@pytest.fixture
async def harness(engine_base_url: str, audit_sink: Any, audit_tenant: str) -> AsyncIterator[WorkerHarness]:
    transporte = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers())
    h = WorkerHarness(
        transporte,
        worker_id=f"esc-aceite-{uuid.uuid4().hex[:8]}",
        tenant=audit_tenant,
        audit_sink=audit_sink,
        bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
    )
    register_events_workers(h, producer, tenant_id=audit_tenant)
    register_escalation_workers(h, producer)
    try:
        yield h
    finally:
        await producer.close()
        await transporte.close()


@pytest.mark.parametrize(("motivo", "severidade"), _casos(), ids=lambda v: str(v))
async def test_motivo_do_aceite_chega_a_tarefa_humana_no_grupo_da_dmn(
    engine_client: httpx.AsyncClient,
    harness: WorkerHarness,
    audit_tenant: str,
    motivo: str,
    severidade: str | None,
) -> None:
    conversa = f"conv-aceite-{uuid.uuid4().hex[:8]}"
    business_key = f"ESC-{audit_tenant}-{conversa}"
    variaveis = EngineRest._to_camunda_vars(
        {
            "tenant_id": audit_tenant,
            "source_agent_id": "lucas" if motivo == "cobranca" else "helena",
            "source_agent_version": "qa-1.0",
            "conversation_id": conversa,
            "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
            "canal": "whatsapp",
            "motivo_categoria": motivo,
            "severidade": severidade,
            "resumo_contexto": "Beneficiario Teste 001 — sintese sintetica para humano",
        }
    )
    resposta = await engine_client.post(
        f"/process-definition/key/SP-OP-ESCALATION-001/tenant-id/{audit_tenant}/start",
        json={"businessKey": business_key, "variables": variaveis},
    )
    resposta.raise_for_status()
    instance_id = str(resposta.json()["id"])
    try:

        async def _ha_tarefa() -> bool:
            return bool(await tarefas_humanas(engine_client, instance_id))

        await drenar_por_chave(
            engine_client,
            harness,
            worker_id=harness._worker_id,
            business_key=business_key,
            instance_id=instance_id,
            pronto=_ha_tarefa,
        )
        tarefas = await tarefas_humanas(engine_client, instance_id)
        assert [t["taskDefinitionKey"] for t in tarefas] == ["UT_TratarEscalonamento"]
        rota = evaluate(
            read_live_table(DMN_DIR / "escalation_routing.dmn"),
            {"motivo_categoria": motivo, "severidade": severidade},
        )
        assert await candidate_groups(engine_client, tarefas[0]["id"]) == {rota.saidas["grupo_atendimento"]}
    finally:
        apagar = await engine_client.delete(
            f"/process-instance/{instance_id}", params={"skipCustomListeners": "true"}
        )
        assert apagar.status_code in (204, 404)
