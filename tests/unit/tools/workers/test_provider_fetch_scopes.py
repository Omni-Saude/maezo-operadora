"""PW1-A unit admission/wire regressions; real CIB acceptance is a separate gate.

HTTP responses here are synthetic transport fixtures, not receipts or an engine
qualification. The fixture returns the variables explicitly requested on the wire.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from maezo.gateway.audit import hash_input
from maezo.tools.workers import contas, credenciamento, recurso
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    TopicSealError,
    TopicSubscription,
    WorkerHarness,
)


def test_raw_scope_is_frozen_before_caller_mutates_its_list() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="scope-unit")
    caller_scope = ["tenant_id"]

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        return {}

    harness.register("operadora.scope.unit", handler, variables=caller_scope)
    caller_scope.append("cpf_beneficiario")

    assert harness._topic_variables["operadora.scope.unit"] == ("tenant_id",)


def test_worker_scope_survives_seal_and_idempotent_owner_registration() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="scope-unit")
    worker = FunctionWorker("operadora.scope.unit", lambda values: {})
    caller_scope = ["tenant_id", "responsavel_id"]
    harness.register_worker(worker, variables=caller_scope)
    caller_scope.clear()
    harness.seal_topic(worker.topic, FunctionWorker)

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        return {}

    with pytest.raises(TopicSealError):
        harness.register(worker.topic, handler, variables=[])
    assert harness._topic_variables[worker.topic] == ("tenant_id", "responsavel_id")
    assert harness.registry.get(worker.topic) is worker
    assert worker.topic in harness._worker_base_topics

    harness.register_worker(worker, variables=("tenant_id",))
    assert harness._topic_variables[worker.topic] == ("tenant_id",)
    assert harness._sealed_topics[worker.topic] is FunctionWorker
    assert worker.topic in harness._worker_base_topics


def test_raw_reregistration_replaces_scope_and_worker_metric_claim() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="scope-unit")
    worker = FunctionWorker("operadora.scope.unit", lambda values: {})
    harness.register_worker(worker, variables=("tenant_id",))

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        return {}

    harness.register(worker.topic, handler, variables=[])
    assert harness._topic_variables[worker.topic] == ()
    assert worker.topic not in harness._worker_base_topics
    assert harness._handler_names[worker.topic] != "FunctionWorker"


async def test_wire_keeps_unscoped_none_distinct_from_empty_and_frozen_scopes() -> None:
    captured: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=[])

    transport = CibSevenWorkerTransport("http://scope-unit/engine-rest")
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(
        base_url="http://scope-unit/engine-rest", transport=httpx.MockTransport(respond)
    )
    try:
        await transport.fetch_and_lock(
            "scope-unit",
            [
                TopicSubscription("legacy", 1000, None),
                TopicSubscription("no-inputs", 1000, ()),
                TopicSubscription("provider", 1000, ("tenant_id", "responsavel_id")),
            ],
            max_tasks=1,
            async_response_timeout_ms=0,
        )
    finally:
        await transport.close()
    assert captured[0]["topics"] == [
        {"topicName": "legacy", "lockDuration": 1000},
        {"topicName": "no-inputs", "lockDuration": 1000, "variables": []},
        {
            "topicName": "provider",
            "lockDuration": 1000,
            "variables": ["tenant_id", "responsavel_id"],
        },
    ]


async def test_wire_scope_excludes_phi_sentinel_from_handler_and_audit_tool_input() -> None:
    seen_inputs: list[dict[str, Any]] = []
    wire_requests: list[dict[str, Any]] = []
    source_variables = {
        "tenant_id": {"value": "tenant-synthetic", "type": "String"},
        "responsavel_id": {"value": "actor-synthetic", "type": "String"},
        "cpf_beneficiario": {"value": "PHI_SENTINEL_SYNTHETIC", "type": "String"},
    }

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        wire_requests.append(body)
        if request.url.path.endswith("/fetchAndLock"):
            subscription = body["topics"][0]
            requested = subscription.get("variables", source_variables)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "scope-task-synthetic",
                        "topicName": subscription["topicName"],
                        "processInstanceId": "scope-process-synthetic",
                        "businessKey": "CRED-tenant-synthetic-provider-synthetic",
                        "workerId": "scope-unit",
                        "variables": {key: source_variables[key] for key in requested},
                    }
                ],
            )
        return httpx.Response(204)

    def execute(values: dict[str, Any]) -> dict[str, Any]:
        seen_inputs.append(dict(values))
        return {"registered": True}

    transport = CibSevenWorkerTransport("http://scope-unit/engine-rest")
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(
        base_url="http://scope-unit/engine-rest", transport=httpx.MockTransport(respond)
    )
    audit = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="scope-unit", tenant="tenant-synthetic", audit_sink=audit)
    worker = FunctionWorker("operadora.scope.unit", execute)
    harness.register_worker(worker, variables=["tenant_id", "responsavel_id"])
    try:
        tasks = await transport.fetch_and_lock(
            "scope-unit",
            harness._topic_subscriptions(),
            max_tasks=1,
            async_response_timeout_ms=0,
        )
        await harness._handle(tasks[0])
    finally:
        await transport.close()

    assert seen_inputs == [{"tenant_id": "tenant-synthetic", "responsavel_id": "actor-synthetic"}]
    assert "cpf_beneficiario" not in wire_requests[0]["topics"][0]["variables"]
    assert audit.emitted[0][0].details["input_sha256"] == hash_input(seen_inputs[0])
    assert wire_requests[-1]["variables"] == {"registered": {"value": True, "type": "Boolean"}}


def _provider_harness() -> WorkerHarness:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="scope-unit")
    credenciamento.register_credenciamento_workers(harness)
    recurso.register_recurso_workers(harness)
    contas.register_contas_workers(harness)
    return harness


def test_every_provider_topic_including_raw_handlers_has_a_restrictive_scope() -> None:
    harness = _provider_harness()
    assert len(harness.registered_topics) == 32
    for topic in harness.registered_topics:
        scope = harness._topic_variables[topic]
        assert isinstance(scope, tuple), topic
        assert scope and len(scope) == len(set(scope)), topic
        assert "cpf_beneficiario" not in scope, topic
        assert "matricula_beneficiario" not in scope, topic
    # Unknown topics retain legacy behavior; there is no all-domain implicit scope.
    harness.register_worker(FunctionWorker("operadora.unrelated.unit", lambda values: {}))
    assert harness._topic_variables["operadora.unrelated.unit"] is None


@pytest.mark.parametrize(
    ("topic", "model"),
    [
        ("operadora.recurso.validate_recurso", recurso.RecursoInput),
        ("operadora.recurso.assess_eligibility", recurso.RecursoInput),
        ("operadora.recurso.assess_eligibility", recurso.RecursoValidationResult),
        ("operadora.recurso.analyze_request", recurso.RecursoInput),
        ("operadora.recurso.registrar_indeferimento", recurso.RecursoIndeferimentoInput),
        ("operadora.recurso.notify_sla_risk", recurso.NotifySlaRiskInput),
        ("operadora.recurso.escalate_ans_timeout", recurso.EscalateAnsTimeoutInput),
        ("operadora.recurso.comunicar_resposta", recurso.ComunicarRespostaInput),
        ("operadora.contas.identify_glosa", contas.GlosaInput),
        ("operadora.contas.analyze_reason", contas.GlosaInput),
        ("operadora.contas.calculate_impact", contas.GlosaIdentified),
        ("operadora.contas.prepare_triage_dossier", contas.GlosaInput),
        ("operadora.contas.prepare_triage_dossier", contas.GlosaIdentified),
        ("operadora.contas.registrar_glosa", contas.GlosaRegistroInput),
        ("operadora.contas.emitir_demonstrativo", contas.DemonstrativoInput),
    ],
)
def test_scope_retains_inputs_selected_by_existing_typed_boundary(topic: str, model: Any) -> None:
    scope = _provider_harness()._topic_variables[topic]
    assert scope is not None
    assert {field.name for field in dataclasses.fields(model)} <= set(scope)


def test_transitive_correlation_money_and_human_guard_inputs_are_not_dropped() -> None:
    scopes = _provider_harness()._topic_variables
    expected = {
        "operadora.cred.register_descredenciamento": {
            "decisao_cred",
            "responsavel_id",
            "fundamentacao",
            "referencia_regulatoria",
            "comprovacao_notificacao_previa",
            "tem_beneficiarios_vinculados",
            "plano_substituicao",
        },
        "operadora.cred.register_cred_denial": {
            "decisao_cred",
            "responsavel_id",
            "fundamentacao",
            "referencia_regulatoria",
        },
        "operadora.cred.prepare_dossier": {
            "tenant_id",
            "prestador_id",
            "protocolo_cred",
            "direcao",
            "especialidade",
            "regiao_saude",
            "origem_solicitacao",
            "data_solicitacao_iso",
            "tem_beneficiarios_vinculados",
        },
        "operadora.contas.start_fraude": {"numero_caso", "prestador_id", "tenant_id"},
        "operadora.contas.handoff_pagamento": {
            "tenant_id",
            "numero_lote_tiss",
            "prestador_id",
            "valor_apresentado_brl",
            "valor_liberado_brl",
            "fonte_valor",
            "lastro_origem",
            "analista_id",
            "data_vencimento",
        },
        "operadora.contas.emitir_demonstrativo": {"business_key", "tipo_comunicacao"},
        "operadora.recurso.handoff_pagamento": {
            "analista_id",
            "auditor_id",
            "valor_deferido_brl",
            "data_vencimento",
            "fonte_valor",
        },
        "operadora.recurso.escalate_ans_timeout": {"tenant_id", "event_topic_breach"},
    }
    for topic, required in expected.items():
        scope = scopes[topic]
        assert scope is not None and required <= set(scope), topic
