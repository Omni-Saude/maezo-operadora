"""Lote T-E unico (ADR-0030 §4 / Tier-3; R-050, R-059 CRED, R-119) contra o engine real.

Para cada um dos quatro guards `*_NOT_HUMAN` habilitados em 2026-10-31, com o allowlist de
PRODUCAO (`PRODUCTION_BPMN_ERROR_ALLOWLIST`, nao uma lista de probe) e os workers reais registrados
pelo bootstrap da familia: `startBeforeActivity` posiciona o token na service task guardada (isola a
fronteira; NAO prova a jornada inteira nem a decisao humana real — as variaveis de decisao sao
entrada sintetica de fronteira, nunca alegacao de signoff), o worker RECUSA, e a prova e':

* a linha `REFUSED` gravada na cadeia ADR-0007 real (dedup `refuse:<task_id>`);
* o boundary modelado foi tomado e a instancia terminou no terminal NEUTRO;
* nenhum incidente aberto, nenhuma task completada (o efeito adverso NAO aconteceu), nada publicado.

Fecha, em engine, os nos BE_DecredNaoHumano / BE_CredDenialNaoHumano / BE_SuspensaoNaoHumano (e
seus End_*) listados como nunca executados em raw/flow_node_coverage.tsv (F-195), e o
BE_ManterNaoConfirmado com o allowlist de producao (F-087).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.runtime.worker_runtime.service import PRODUCTION_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.cancel import register_cancel_workers
from maezo.tools.workers.credenciamento import register_credenciamento_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    FakeKafkaPublisher,
    WorkerHarness,
    _from_camunda_var,
)
from maezo.tools.workers.inadimplencia import register_inadimplencia_workers

from .conftest import CIBSEVEN_BASE_URL, audit_rows_for_task_ids
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration
_BPMN_DIR = Path(__file__).resolve().parents[3] / "spec" / "processes" / "bpmn"

_CASOS: list[dict[str, Any]] = [
    {
        "code": "ERR_CANCEL_MANTER_NOT_HUMAN",
        "bpmn": "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn",
        "process_key": "SP-OP-CANCEL-001",
        "activity": "ST_ConfirmMaintainedDecision",
        "boundary": "BE_ManterNaoConfirmado",
        "end": "End_ManterNaoConfirmado",
        "topic": "operadora.cancel.confirm_maintained_decision",
        "register": register_cancel_workers,
        "variables": {"decisao_cancelamento": "RESCINDIR", "fundamentacao_contratual": "sintetica"},
    },
    {
        "code": "ERR_DECRED_NOT_HUMAN",
        "bpmn": "SP-OP-CRED-001_Descredenciamento.bpmn",
        "process_key": "SP-OP-CRED-001",
        "activity": "ST_RegisterDescredenciamento",
        "boundary": "BE_DecredNaoHumano",
        "end": "End_DecredBloqueadoNaoHumano",
        "topic": "operadora.cred.register_descredenciamento",
        "register": register_credenciamento_workers,
        "variables": {
            "prestador_id": "P-SINT-TE-1",
            "decisao_cred": "MANTER",  # != DESCREDENCIAR -> guard recusa
            "responsavel_id": "resp-sintetico",
            "fundamentacao": "sintetica",
            "referencia_regulatoria": "sintetica",
            "comprovacao_notificacao_previa": "sintetica",
        },
    },
    {
        "code": "ERR_CRED_DENIAL_NOT_HUMAN",
        "bpmn": "SP-OP-CRED-001_Descredenciamento.bpmn",
        "process_key": "SP-OP-CRED-001",
        "activity": "ST_RegisterCredDenial",
        "boundary": "BE_CredDenialNaoHumano",
        "end": "End_CredGuardBloqueadoNaoHumano",
        "topic": "operadora.cred.register_cred_denial",
        "register": register_credenciamento_workers,
        "variables": {
            "prestador_id": "P-SINT-TE-2",
            "decisao_cred": "APROVAR_CREDENCIAMENTO",  # != NEGAR_CREDENCIAMENTO -> guard recusa
            "responsavel_id": "resp-sintetico",
            "fundamentacao": "sintetica",
            "referencia_regulatoria": "sintetica",
        },
    },
    {
        "code": "ERR_CONTRACT_SUSPENSION_NOT_HUMAN",
        "bpmn": "SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn",
        "process_key": "SP-OP-INADIMPLENCIA-001",
        "activity": "ST_RegisterSuspension",
        "boundary": "BE_SuspensaoNaoHumano",
        "end": "End_SuspensaoBloqueadaNaoHumano",
        "topic": "operadora.inadimplencia.register_contract_suspension",
        "register": register_inadimplencia_workers,
        "variables": {
            "numero_contrato": "C-SINT-TE-1",
            "decisao_inadimplencia": "MANTER",  # != SUSPENDER -> guard recusa
            "responsavel_id": "resp-sintetico",
            "fundamentacao_contratual": "sintetica",
            "referencia_regulatoria": "sintetica",
            "comprovacao_notificacao_previa": "sintetica",
            "comprovacao_periodo_minimo": "sintetica",
            "ja_em_rescisao_cancel": False,
        },
    },
]


@pytest.mark.parametrize("caso", _CASOS, ids=[c["code"] for c in _CASOS])
async def test_guard_not_human_habilitado_audita_e_termina_no_terminal_neutro(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
    audit_pg: tuple[str, str],
    caso: dict[str, Any],
) -> None:
    code: str = caso["code"]
    assert code in PRODUCTION_BPMN_ERROR_ALLOWLIST  # premissa: e' o allowlist de producao que roteia

    await engine.deploy(_BPMN_DIR / caso["bpmn"], name=f"te-lote-{caso['process_key']}")
    worker_id = "qa-te-lote-" + uuid.uuid4().hex[:8]
    business_key = f"TE-LOTE-{uuid.uuid4().hex}"
    kafka = FakeKafkaPublisher()
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        audit_sink=audit_sink,
        bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
    )
    register: Callable[..., None] = caso["register"]
    register(harness, kafka)
    variables = {"tenant_id": audit_tenant, **caso["variables"]}
    instance_id: str | None = None
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20) as client:
        try:
            started = await client.post(
                f"/process-definition/key/{caso['process_key']}/start",
                json={
                    "businessKey": business_key,
                    "variables": EngineRest._to_camunda_vars(variables),
                    "startInstructions": [{"type": "startBeforeActivity", "activityId": caso["activity"]}],
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
                            "topicName": caso["topic"],
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
                variables={k: _from_camunda_var(v) for k, v in item["variables"].items()},
                retries=item.get("retries"),
            )
            await harness._handle(task)

            assert await engine.incidents(instance_id) == []
            history = await engine.activity_instances_ended(instance_id)
            assert caso["boundary"] in history, history
            assert caso["end"] in history, history
            assert not await engine.instance_is_active(instance_id)
            assert await engine.history_state(instance_id) == "COMPLETED"
            assert kafka.published == []
            dsn, tenant = audit_pg
            rows = await audit_rows_for_task_ids(dsn, tenant, [f"refuse:{task.task_id}"])
            assert rows == [("operadora-worker", caso["topic"], "REFUSED")]
            # nenhuma linha de COMPLETION para a task: o efeito adverso nao foi registrado
            assert await audit_rows_for_task_ids(dsn, tenant, [task.task_id]) == []
        finally:
            await transport.close()
            if instance_id is not None and await engine.instance_is_active(instance_id):
                deleted = await client.delete(f"/process-instance/{instance_id}")
                deleted.raise_for_status()
