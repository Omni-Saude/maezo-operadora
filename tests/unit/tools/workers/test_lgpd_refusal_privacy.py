"""R-181 successors: terminal, inert LGPD refusal across worker and harness.

Unit transport doubles observe calls only; real engine acceptance is a separate gate.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import structlog.testing

from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerFailureError,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import ExecuteRequestWorker, register_lgpd_workers

_REJECTED = "sentinela paciente teste relato clinico recusado"
_BAD_TYPES: list[Any] = [None, False, 1, 1.5, [], [_REJECTED], {}, {"tipo": _REJECTED}, _REJECTED]
_BAD_DECISIONS: list[Any] = [None, False, 1, [], [_REJECTED], {"decisao": _REJECTED}, _REJECTED]


def _variables(**extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "titular_pseudo_id": "pseudo-test",
        "tipo_requisicao": "eliminacao",
        "human_approved": True,
        "decisao_dsr": "EXECUTAR_E_ENVIAR",
        "fundamentacao_legal": _REJECTED,
        "detalhes_requisicao": _REJECTED,
        **extra,
    }


@pytest.mark.parametrize("tipo", _BAD_TYPES)
def test_malformed_type_is_terminal_typed_refusal_without_echo(tipo: Any) -> None:
    variables = _variables(tipo_requisicao=tipo)
    before = copy.deepcopy(variables)
    with structlog.testing.capture_logs() as logs, pytest.raises(WorkerFailureError) as raised:
        ExecuteRequestWorker().run(variables)
    assert raised.value.retries_left == 0
    assert "fora do dominio" in str(raised.value)
    assert _REJECTED not in str(raised.value)
    assert _REJECTED not in json.dumps(logs, default=str)
    assert logs and any("recusado" in entry["event"] for entry in logs)
    assert not any(entry["event"] == "worker_completed" for entry in logs)
    assert variables == before


@pytest.mark.parametrize("decision", _BAD_DECISIONS)
@pytest.mark.parametrize("approved", [True, False])
def test_rejected_decision_and_type_do_not_echo_before_any_guard(decision: Any, approved: bool) -> None:
    variables = _variables(tipo_requisicao=_REJECTED, decisao_dsr=decision, human_approved=approved)
    with structlog.testing.capture_logs() as logs, pytest.raises(WorkerFailureError) as raised:
        ExecuteRequestWorker().run(variables)
    assert raised.value.retries_left == 0
    assert logs and _REJECTED not in json.dumps(logs, default=str)
    assert _REJECTED not in str(raised.value)


@pytest.mark.parametrize("tipo", _BAD_TYPES + ["eliminacao", "correcao", "portabilidade"])
async def test_harness_refuses_without_complete_response_or_mutating_variables(tipo: Any) -> None:
    variables = _variables(tipo_requisicao=tipo)
    before = copy.deepcopy(variables)
    task = ExternalTask(
        task_id="task-test",
        topic="operadora.lgpd.execute_request",
        process_instance_id="process-test",
        business_key="DSR-amh-pseudo-test",
        worker_id="worker-test",
        variables=variables,
        retries=3,
    )
    transport = FakeWorkerTransport()
    audit = FakeAuditSink()
    kafka = FakeKafkaPublisher()
    harness = WorkerHarness(transport, worker_id="worker-test", tenant="amh", audit_sink=audit)
    register_lgpd_workers(harness, kafka)
    with structlog.testing.capture_logs() as logs:
        await harness._handle(task)
    assert transport.completed == []
    assert transport.bpmn_errors == []
    assert len(transport.failures) == 1
    assert transport.failures[0][0] == task.task_id
    assert transport.failures[0][2:] == (0, 0)
    assert "NAO esta implementada" in transport.failures[0][1]
    assert _REJECTED not in transport.failures[0][1]
    assert kafka.published == []
    assert variables == before
    assert _REJECTED not in json.dumps(logs, default=str)
    assert not any(entry["event"] == "worker_completed" for entry in logs)
    # Technical WorkerFailureError writes only the incident; no execution audit is fabricated.
    assert audit.emitted == []
