"""R-044 (F-062, ADR-0020 item 6) contra o BPMN canonico de LGPD e a API real de incidentes.

Bandeira dark `lgpd_legal_hold_predicate` LIGADA no bootstrap. startInstructions posiciona o token
em ST_ExecutarRequisicao (isola esta fronteira; NAO prova a jornada inteira nem aprovacao humana
real). Prova: com hold ativo, ou sem registro configurado, a eliminacao aprovada vira incidente
`ERR_DSR_LEGAL_HOLD_NOT_HUMAN` (retries=0), o token NAO avanca para o envio, nada e publicado, e a
recusa fica gravada na cadeia ADR-0007 real (`REFUSED`). Registro e pseudonimo sinteticos.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.platform.lifecycle.legal_hold import REGISTRY_PATH_ENV
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    FakeKafkaPublisher,
    WorkerHarness,
    _from_camunda_var,
)
from maezo.tools.workers.lgpd import ERR_DSR_LEGAL_HOLD_NOT_HUMAN, register_lgpd_workers

from .conftest import CIBSEVEN_BASE_URL, audit_rows_for_task_ids
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration
_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn"
_PSEUDO = "pseudo-sintetico-hold-" + uuid.uuid4().hex[:8]


@pytest.mark.parametrize("registro", ["hold_ativo", "sem_registro"])
async def test_eliminacao_sob_legal_hold_vira_incidente_auditado_sem_avancar(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
    audit_pg: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registro: str,
) -> None:
    if registro == "hold_ativo":
        path = tmp_path / "holds.yaml"
        path.write_text(
            "schema_version: 1\nholds:\n  - hold_id: LH-SINT-IT-1\n"
            f"    tenant_id: {audit_tenant}\n    titular_pseudo_id: {_PSEUDO}\n"
            "    colocado_em: 2026-09-01\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(REGISTRY_PATH_ENV, str(path))
    else:
        monkeypatch.delenv(REGISTRY_PATH_ENV, raising=False)

    await engine.deploy(_BPMN, name="r044-lgpd-legal-hold")
    worker_id = "qa-legal-hold-" + uuid.uuid4().hex[:8]
    business_key = "DSR-test-" + uuid.uuid4().hex
    variables = {
        "tenant_id": audit_tenant,
        "titular_pseudo_id": _PSEUDO,
        "tipo_requisicao": "eliminacao",
        "human_approved": True,  # Entrada sintetica de fronteira, nunca alegacao de signoff humano.
        "decisao_dsr": "EXECUTAR_E_ENVIAR",
    }
    kafka = FakeKafkaPublisher()
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(transport, worker_id=worker_id, tenant=audit_tenant, audit_sink=audit_sink)
    register_lgpd_workers(harness, kafka, lgpd_legal_hold_predicate=True)
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
            task = ExternalTask(
                task_id=item["id"],
                topic=item["topicName"],
                process_instance_id=instance_id,
                business_key=business_key,
                worker_id=worker_id,
                variables={key: _from_camunda_var(value) for key, value in item["variables"].items()},
                retries=item.get("retries"),
            )
            await harness._handle(task)

            incidents = await engine.incidents(instance_id)
            assert len(incidents) == 1
            assert incidents[0]["incidentType"] == "failedExternalTask"
            assert incidents[0]["activityId"] == "ST_ExecutarRequisicao"
            # O harness prefixa o nome da classe e trunca o meio da mensagem: o codigo de guarda e
            # o inicio da recusa sao o que o engine preserva.
            assert incidents[0]["incidentMessage"].startswith(
                f"LegalHoldErasureDeferredError: {ERR_DSR_LEGAL_HOLD_NOT_HUMAN}: eliminacao DIFERIDA"
            )
            assert _PSEUDO not in json.dumps(incidents)
            tasks = await client.get("/external-task", params={"processInstanceId": instance_id})
            tasks.raise_for_status()
            assert [t["retries"] for t in tasks.json()] == [0]
            history = await engine.activity_instances_ended(instance_id)
            assert "ST_EnviarResposta" not in history
            assert "End_RequisicaoConcluida" not in history
            assert await engine.instance_is_active(instance_id)
            assert kafka.published == []
            dsn, tenant = audit_pg
            rows = await audit_rows_for_task_ids(dsn, tenant, [f"refuse:{task.task_id}"])
            assert rows == [("operadora-worker", "operadora.lgpd.execute_request", "REFUSED")]
        finally:
            await transport.close()
            if instance_id is not None:
                deleted = await client.delete(f"/process-instance/{instance_id}")
                deleted.raise_for_status()
