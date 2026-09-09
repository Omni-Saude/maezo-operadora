"""Helena -> CIB Seven -> Kafka -> HITL, com dependencias reais (ADR-0011/0030).

O LLM e replay deterministico; engine, broker, produtor, consumidor e auditoria
Postgres sao reais. A falha primaria usa conexao TCP recusada em porta reservada
pelo proprio teste; nao derruba o broker nem fabrica uma excecao em um mock.
O supervisor usa outro produtor saudavel. A auditoria aqui e emit-before-complete,
como o harness: a publicacao acontece no handler ANTES da auditoria de conclusao.
Nenhuma conclusao clinica, assinatura humana ou entrega WhatsApp e simulada como fato.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from collections import Counter
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.agents.helena.graph import build
from maezo.platform.integrations.events_kafka_producer import AioKafkaEventsProducer
from maezo.runtime.worker_runtime.service import PRODUCTION_BPMN_ERROR_ALLOWLIST
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.escalation import (
    _GRUPO_SUPERVISAO,
    make_notify_team_handler,
    register_escalation_workers,
)
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import (
    AUDIT_AGENT_ID,
    AUDIT_DECISION_COMPLETE,
    CibSevenWorkerTransport,
    ExternalTask,
    WorkerHarness,
    _from_camunda_var,
)
from tests.integration.platform.test_events_kafka_producer_live import (
    _await_assigned,
    _await_topic_ready,
)
from tests.integration.processes.conftest import audit_rows_for_task_ids
from tests.integration.processes.engine_rest import EngineRest
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

from ._engine_helpers import (
    active_instances,
    candidate_groups,
    mapa_de_variavel_estruturada,
    process_variable_not_deserialized,
    process_variables,
    tasks_for_instance,
)
from .test_helena_escalation import _FakeInference, _FakeWhatsAppSender, _RaisingInference

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[3]
_BPMN = _ROOT / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
_REQUESTED = "agents.events.escalation.requested"
_NOTIFICATIONS = "operadora.notifications.internal"
_PUBLISH = "operadora.events.publish"
_PRIMARY = "operadora.escalation.notify_team"
_SUPERVISOR = "operadora.escalation.notify_supervisor"
_TOPICS = (_PUBLISH, _PRIMARY, _SUPERVISOR)


def _kafka_bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _assert_broker_records(
    records: list[Any],
    *,
    business_key: str,
    instance_id: str,
    tenant: str,
    conversation_id: str,
    route: dict[str, Any],
    primary_failed: bool,
) -> None:
    """Exige fatos do wire; um log, eco de status ou objeto de outro caso nao serve."""
    assert Counter(record.topic for record in records) == Counter({_REQUESTED: 1, _NOTIFICATIONS: 1})
    assert all(record.key == business_key.encode() for record in records)
    payloads = {record.topic: json.loads(record.value) for record in records}
    requested = payloads[_REQUESTED]
    assert requested["_business_key"] == business_key
    assert requested["_process_instance_id"] == instance_id
    assert requested["_worker_topic"] == _PUBLISH
    assert requested["tenant_id"] == tenant
    assert requested["conversation_id"] == conversation_id
    assert requested["source_agent_id"] == "helena"
    assert requested["motivo_categoria"] == "falha_tecnica"
    assert "severidade" in requested and requested["severidade"] is None
    notification = payloads[_NOTIFICATIONS]
    assert notification["tenant_id"] == tenant
    assert "severidade" in notification and notification["severidade"] is None
    assert notification["grupo_atendimento"] == route["grupo_atendimento"]
    assert notification["prioridade"] == route["prioridade"]
    if primary_failed:
        assert notification["type"] == "escalation.notify_supervisor"
        assert notification["alert_to"] == _GRUPO_SUPERVISAO
        assert notification["motivo_alerta"] == "notificacao_primaria_falhou"
    else:
        assert notification["type"] == "escalation.notify_team"
        assert notification["motivo_categoria"] == "falha_tecnica"
    for payload in payloads.values():
        assert {"message_body", "resumo_contexto", "beneficiario_pseudo_id"}.isdisjoint(payload)


async def _drain_instance(
    client: httpx.AsyncClient,
    harness: WorkerHarness,
    *,
    worker_id: str,
    business_key: str,
    instance_id: str,
) -> tuple[dict[str, Any], list[ExternalTask]]:
    """REST fetchAndLock escopado a businessKey, decodificador e harness de producao.

    Segue test_lgpd_execution_refusal, evitando consumir tarefas de outros casos
    deixadas na stack compartilhada. O transporte de complete/bpmnError e real.
    """
    handled: list[ExternalTask] = []
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        users = await tasks_for_instance(client, instance_id)
        if users:
            assert len(users) == 1
            assert users[0]["taskDefinitionKey"] == "UT_TratarEscalonamento"
            return users[0], handled
        response = await client.post(
            "/external-task/fetchAndLock",
            json={
                "workerId": worker_id,
                "maxTasks": 1,
                "asyncResponseTimeout": 500,
                "topics": [
                    {
                        "topicName": topic,
                        "lockDuration": 15000,
                        "businessKey": business_key,
                        "deserializeValues": False,
                    }
                    for topic in _TOPICS
                ],
            },
        )
        response.raise_for_status()
        for item in response.json():
            assert item["processInstanceId"] == instance_id
            assert item["businessKey"] == business_key
            task = ExternalTask(
                task_id=item["id"],
                topic=item["topicName"],
                process_instance_id=instance_id,
                business_key=business_key,
                worker_id=worker_id,
                variables={key: _from_camunda_var(value) for key, value in item["variables"].items()},
                retries=item.get("retries"),
            )
            handled.append(task)
            await harness._handle(task)
        incidents = await client.get("/incident", params={"processInstanceId": instance_id})
        incidents.raise_for_status()
        assert incidents.json() == [], "a falha modelada deve seguir pelo boundary, nunca incident"
    raise AssertionError("o caso nao alcancou UT_TratarEscalonamento no prazo da prova")


async def _consume_case(consumer: Any, business_key: str) -> list[Any]:
    records: list[Any] = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and len(records) < 2:
        batches = await consumer.getmany(timeout_ms=500)
        records.extend(
            record for batch in batches.values() for record in batch if record.key == business_key.encode()
        )
    # Todos os handlers ja terminaram; inclua qualquer registro adicional antes de
    # exigir cardinalidade exata, inclusive uma publicacao primaria indevida.
    batches = await consumer.getmany(timeout_ms=500)
    records.extend(
        record for batch in batches.values() for record in batch if record.key == business_key.encode()
    )
    return records


@pytest.mark.parametrize("classifier_failure", ["malformed_json", "provider_exception"])
@pytest.mark.parametrize("primary_failed", [False, True], ids=["primary_delivered", "fallback_delivered"])
async def test_classifier_failure_reaches_human_with_real_broker_delivery(
    engine_base_url: str,
    engine_client: httpx.AsyncClient,
    audit_sink: Any,
    audit_tenant: str,
    audit_pg: tuple[str, str],
    classifier_failure: str,
    primary_failed: bool,
) -> None:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    servers = _kafka_bootstrap_servers()
    for topic in (_REQUESTED, _NOTIFICATIONS):
        await _await_topic_ready(servers, topic)
    consumer = AIOKafkaConsumer(
        _REQUESTED,
        _NOTIFICATIONS,
        bootstrap_servers=servers,
        group_id=f"helena-broker-{uuid.uuid4().hex}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    healthy = AioKafkaEventsProducer(bootstrap_servers=servers)
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))  # reservado SEM listen: conexao realmente recusada, sem race de porta
    refused_endpoint = f"127.0.0.1:{reserved.getsockname()[1]}"
    raw_failed = AIOKafkaProducer(bootstrap_servers=refused_endpoint, request_timeout_ms=1000)
    failed = AioKafkaEventsProducer(raw_producer=raw_failed, connect_timeout_s=2, send_timeout_s=2)
    token = uuid.uuid4().hex
    conversation_id = f"wa:{audit_tenant}:helena-broker-{token}"
    business_key = f"ESC-{audit_tenant}-{conversation_id}"
    worker_id = f"helena-broker-{token}"
    transport = CibSevenWorkerTransport(engine_base_url, timeout=30)
    engine = EngineRest(engine_base_url)
    dmn = CibSevenDmnTransport(engine_base_url, timeout=30)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30)
    instance_id: str | None = None
    try:
        await consumer.start()
        partition_sets = [consumer.partitions_for_topic(topic) for topic in (_REQUESTED, _NOTIFICATIONS)]
        assert all(partition_sets), "os dois topicos devem ter particoes antes da assinatura"
        await _await_assigned(consumer, expected_partitions=sum(len(parts) for parts in partition_sets))
        await consumer.seek_to_end()  # cursor definido ANTES de criar a instancia
        harness = WorkerHarness(
            transport,
            worker_id=worker_id,
            tenant=audit_tenant,
            audit_sink=audit_sink,
            bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
        )
        register_events_workers(harness, healthy, tenant_id=audit_tenant)
        register_escalation_workers(harness, healthy)
        if primary_failed:
            # Mesmo handler de producao, somente sua conexao primaria e indisponivel.
            # O objeto real aiokafka produz KafkaConnectionError, nunca um raise de teste.
            harness.register(_PRIMARY, make_notify_team_handler(failed))
        assert set(harness.registered_topics) == set(_TOPICS)
        inference = (
            _RaisingInference()
            if classifier_failure == "provider_exception"
            else _FakeInference(
                [
                    "{",
                    "Classificador indisponivel; atendimento humano solicitado.",
                    "Um atendente humano continuara o atendimento.",
                ]
            )
        )
        graph = build(
            {
                "inference": inference,
                "dmn": dmn,
                "cibseven": cibseven,
                "audit_sink": audit_sink,
                "whatsapp": _FakeWhatsAppSender(),
            }
        )
        result = await graph.compile().ainvoke(
            {
                "tenant_id": audit_tenant,
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-test-{token}",
                "message_body": "Mensagem de teste.",
            }
        )
        assert result["next_kind"] == "escalate"
        assert result["escalation_motivo"] == "falha_tecnica"
        assert result["escalation_severidade"] is None
        assert result["escalation_started"] is True
        instances = await active_instances(engine_client, business_key)
        assert len(instances) == 1
        instance_id = instances[0]["id"]
        xml = await engine.definition_xml(instances[0]["definitionId"])
        assert xml.strip() == _BPMN.read_text().strip(), "a instancia deve usar o BPMN deste checkout"
        task, handled = await _drain_instance(
            engine_client, harness, worker_id=worker_id, business_key=business_key, instance_id=instance_id
        )
        expected_topics = [_PUBLISH, _PRIMARY] + ([_SUPERVISOR] if primary_failed else [])
        assert [item.topic for item in handled] == expected_topics
        assert await engine.incidents(instance_id) == []
        ended = await engine.activity_instances_ended(instance_id)
        assert "ST_PublishRequested" in ended and "BRT_RotearEscalonamento" in ended
        assert ("BE_FalhaNotificacao" in ended) is primary_failed
        assert ("ST_NotificarFallback" in ended) is primary_failed
        assert "BE_NotifFallbackFailed" not in ended
        assert "BE_PubReqFailed" not in ended
        variables = await process_variables(engine_client, instance_id)
        assert "severidade" in variables and variables["severidade"]["value"] is None
        assert variables["motivo_categoria"]["value"] == "falha_tecnica"
        assert variables["status"]["value"] == ("supervisor_notified" if primary_failed else "teams_notified")
        assert variables["event_published"]["value"] is True
        expected = evaluate(
            read_live_table(DMN_DIR / "escalation_routing.dmn"),
            {"motivo_categoria": "falha_tecnica", "severidade": None},
        )
        assert expected.regra == "r6"
        decisions = await engine.history_decision_instances(
            instance_id=instance_id, decision_key="escalation_routing"
        )
        assert len(decisions) == 1, "a instancia deve avaliar a DMN de roteamento exatamente uma vez"
        deployed_dmn = await engine_client.get(
            f"/decision-definition/{decisions[0]['decisionDefinitionId']}/xml"
        )
        deployed_dmn.raise_for_status()
        assert (
            deployed_dmn.json()["dmnXml"].strip() == (DMN_DIR / "escalation_routing.dmn").read_text().strip()
        )
        route = mapa_de_variavel_estruturada(variables["roteamento"], set(expected.saidas))
        if route is None:
            raw = await process_variable_not_deserialized(engine_client, instance_id, "roteamento")
            route = mapa_de_variavel_estruturada(raw, set(expected.saidas))
        assert route == expected.saidas
        assert await candidate_groups(engine_client, task["id"]) == {route["grupo_atendimento"]}
        records = await _consume_case(consumer, business_key)
        _assert_broker_records(
            records,
            business_key=business_key,
            instance_id=instance_id,
            tenant=audit_tenant,
            conversation_id=conversation_id,
            route=route,
            primary_failed=primary_failed,
        )
        # A cadeia e vinculada aos IDs reais das tarefas. Falha primaria nao e uma
        # conclusao e nao pode ganhar linha COMPLETE. O handler publica antes da auditoria;
        # a garantia do harness e auditoria antes de engine.complete.
        completed = [item for item in handled if not (primary_failed and item.topic == _PRIMARY)]
        rows = await audit_rows_for_task_ids(audit_pg[0], audit_tenant, [item.task_id for item in handled])
        assert Counter(rows) == Counter(
            (AUDIT_AGENT_ID, item.topic, AUDIT_DECISION_COMPLETE) for item in completed
        )
    finally:
        # Fechar todos os clientes mesmo se a limpeza da instancia/um cliente falhar.
        async with AsyncExitStack() as cleanup:
            cleanup.callback(reserved.close)
            cleanup.push_async_callback(raw_failed.stop)
            cleanup.push_async_callback(healthy.close)
            cleanup.push_async_callback(consumer.stop)
            cleanup.push_async_callback(transport.close)
            cleanup.push_async_callback(engine.aclose)
            cleanup.push_async_callback(dmn.close)
            cleanup.push_async_callback(cibseven.close)
            if instance_id is not None:
                response = await engine_client.delete(
                    f"/process-instance/{instance_id}", params={"skipCustomListeners": "true"}
                )
                response.raise_for_status()
