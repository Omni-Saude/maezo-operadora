"""R-181 successors against the real canonical LGPD BPMN and incident API.

startInstructions positions the token at ST_ExecutarRequisicao to isolate this boundary.
It does NOT prove the full DSR journey, identity review or a real human approval. No compile
or response stub is registered; an accidental complete is observable as a live send task.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    FakeKafkaPublisher,
    WorkerHarness,
    _from_camunda_var,
)
from maezo.tools.workers.lgpd import register_lgpd_workers

from .conftest import CIBSEVEN_BASE_URL
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration
_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn"
_RAW = "sentinela paciente teste dado recusado"


@pytest.mark.parametrize("tipo", [[_RAW], {"tipo": _RAW}, _RAW, "eliminacao"])
async def test_real_execution_task_refuses_without_advancing_or_echoing(
    engine: EngineRest, audit_sink: Any, audit_tenant: str, tipo: Any
) -> None:
    await engine.deploy(_BPMN, name="r6-lgpd-refusal-boundary")
    worker_id = "qa-refusal-" + uuid.uuid4().hex[:8]
    business_key = "DSR-test-" + uuid.uuid4().hex
    variables = {
        "tenant_id": audit_tenant,
        "titular_pseudo_id": "pseudo-test",
        "tipo_requisicao": tipo,
        "human_approved": True,  # Synthetic boundary input, never a claim of human signoff.
        "decisao_dsr": "EXECUTAR_E_ENVIAR",
    }
    kafka = FakeKafkaPublisher()
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(transport, worker_id=worker_id, tenant=audit_tenant, audit_sink=audit_sink)
    register_lgpd_workers(harness, kafka)
    instance_id: str | None = None
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20) as client:
        try:
            started = await client.post(
                "/process-definition/key/SP-OP-LGPD-DSR-001/start",
                json={
                    "businessKey": business_key,
                    "variables": EngineRest._to_camunda_vars(variables),
                    "startInstructions": [
                        {"type": "startBeforeActivity", "activityId": "ST_ExecutarRequisicao"}
                    ],
                },
            )
            started.raise_for_status()
            instance_id = started.json()["id"]
            # Real fetchAndLock, restricted to this unique business key. The production decoder
            # converts the engine's Json wire values; there is no in-memory engine substitute.
            fetched = await client.post(
                "/external-task/fetchAndLock",
                json={
                    "workerId": worker_id,
                    "maxTasks": 1,
                    "topics": [
                        {
                            "topicName": "operadora.lgpd.execute_request",
                            "lockDuration": 10000,
                            "businessKey": business_key,
                            "deserializeValues": False,
                        }
                    ],
                },
            )
            fetched.raise_for_status()
            items = fetched.json()
            assert len(items) == 1 and items[0]["processInstanceId"] == instance_id
            item = items[0]
            decoded = {key: _from_camunda_var(value) for key, value in item["variables"].items()}
            assert decoded["tipo_requisicao"] == tipo
            task = ExternalTask(
                task_id=item["id"],
                topic=item["topicName"],
                process_instance_id=instance_id,
                business_key=business_key,
                worker_id=worker_id,
                variables=decoded,
                retries=item.get("retries"),
            )
            await harness._handle(task)
            incidents = await engine.incidents(instance_id)
            assert len(incidents) == 1
            assert incidents[0]["incidentType"] == "failedExternalTask"
            assert incidents[0]["activityId"] == "ST_ExecutarRequisicao"
            assert "NAO esta implementada" in incidents[0]["incidentMessage"]
            assert _RAW not in json.dumps(incidents)
            tasks = await client.get("/external-task", params={"processInstanceId": instance_id})
            tasks.raise_for_status()
            assert len(tasks.json()) == 1
            assert tasks.json()[0]["id"] == task.task_id
            assert tasks.json()[0]["retries"] == 0
            history = await engine.activity_instances_ended(instance_id)
            assert "ST_EnviarResposta" not in history
            assert "End_RequisicaoConcluida" not in history
            assert await engine.instance_is_active(instance_id)
            assert kafka.published == []
            after = await client.get(
                f"/process-instance/{instance_id}/variables", params={"deserializeValues": "false"}
            )
            after.raise_for_status()
            assert {key: _from_camunda_var(value) for key, value in after.json().items()} == variables
        finally:
            await transport.close()
            if instance_id is not None:
                deleted = await client.delete(f"/process-instance/{instance_id}")
                deleted.raise_for_status()
