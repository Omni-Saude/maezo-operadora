"""DL-0131 — o estouro do PRAZO DE RESOLUCAO vira um fato contavel, sem identificador.

`BT_SlaResolucao` nao passa por `notify_supervisor`: vai a `ST_PublishResolBreach`
(`operadora.events.publish`, `event_topic=agents.events.escalation.sla_breached`,
`event_fase=resolucao`) e depois a `UT_SupervisorAssume`. O worker generico emite
`escalation.sla_resolucao_estourada` quando ESSA tarefa chega a ele — publique ou nao o evento.

A condicao de disparo e' conferida contra o BPMN (fonte), nao digitada aqui.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.tools.workers.events import (
    EVENTO_SLA_RESOLUCAO_ESTOURADA,
    SLA_BREACHED_TOPIC,
    SLA_FASE_RESOLUCAO,
    make_publish_event_handler,
)
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerBpmnError

_BPMN = (
    Path(__file__).resolve().parents[4]
    / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
)


def _bloco(activity_id: str) -> str:
    texto = _BPMN.read_text(encoding="utf-8")
    inicio = texto.index(f'id="{activity_id}"')
    return texto[inicio : texto.index("</bpmn:serviceTask>", inicio)]


def _input(bloco: str, nome: str) -> str:
    achado = re.search(rf'name="{nome}">([^<]+)<', bloco)
    assert achado is not None, nome
    return achado.group(1).strip()


def _vars_do_bpmn(activity_id: str) -> dict[str, Any]:
    bloco = _bloco(activity_id)
    variaveis: dict[str, Any] = {
        "event_topic": _input(bloco, "event_topic"),
        "event_fase": _input(bloco, "event_fase"),
        "event_payload_vars": _input(bloco, "event_payload_vars"),
        "tenant_id": "amh",
        "conversation_id": "conv-1",
        "motivo_categoria": "red_flag_clinico",
        "severidade": "grave",
    }
    return variaveis


def _task(activity_id: str) -> ExternalTask:
    return ExternalTask(
        task_id="t-1",
        topic="operadora.events.publish",
        process_instance_id="p-1",
        business_key="ESC-amh-conv-1",
        worker_id="w-1",
        variables=_vars_do_bpmn(activity_id),
        process_definition_key="SP-OP-ESCALATION-001",
        activity_id=activity_id,
    )


class _KafkaFora:
    async def publish(
        self, topic: str, value: dict[str, Any], *, key: str | None = None, best_effort: bool | None = None
    ) -> bool:
        raise RuntimeError("broker fora")


def _estouros(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == EVENTO_SLA_RESOLUCAO_ESTOURADA]


def test_a_condicao_de_disparo_e_a_do_bpmn() -> None:
    bloco = _bloco("ST_PublishResolBreach")
    assert _input(bloco, "event_topic") == SLA_BREACHED_TOPIC
    assert _input(bloco, "event_fase") == SLA_FASE_RESOLUCAO
    assert EVENTO_SLA_RESOLUCAO_ESTOURADA == "escalation.sla_resolucao_estourada"


async def test_publicacao_do_estouro_de_resolucao_emite_o_fato_fechado() -> None:
    with capture_logs() as logs:
        await make_publish_event_handler(FakeKafkaPublisher())(_task("ST_PublishResolBreach"))
    assert _estouros(logs) == [{"event": "escalation.sla_resolucao_estourada", "log_level": "warning"}]


async def test_o_fato_sai_mesmo_quando_a_publicacao_falha() -> None:
    """A falha da publicacao segue para `UT_SupervisorAssume` (BE_PubResolBreachFailed): o prazo
    estourou do mesmo jeito e tem de ser contado."""
    handler = make_publish_event_handler(_KafkaFora(), bpmn_error_topics=frozenset({SLA_BREACHED_TOPIC}))
    with capture_logs() as logs, pytest.raises(WorkerBpmnError):
        await handler(_task("ST_PublishResolBreach"))
    assert len(_estouros(logs)) == 1


async def test_o_fato_sai_sem_produtor() -> None:
    with capture_logs() as logs:
        await make_publish_event_handler(None)(_task("ST_PublishResolBreach"))
    assert len(_estouros(logs)) == 1


@pytest.mark.parametrize("activity_id", ["ST_PublishAckBreach", "ST_PublishRequested", "ST_PublishResolved"])
async def test_outras_publicacoes_nao_contam_como_estouro_de_resolucao(activity_id: str) -> None:
    tarefa = _task("ST_PublishResolBreach")
    bloco = _bloco(activity_id)
    variaveis = dict(tarefa.variables)
    variaveis["event_topic"] = _input(bloco, "event_topic")
    fase = re.search(r'name="event_fase">([^<]+)<', bloco)
    if fase is None:
        variaveis.pop("event_fase")
    else:
        variaveis["event_fase"] = fase.group(1).strip()
    variaveis["event_payload_vars"] = _input(bloco, "event_payload_vars")
    for nome in variaveis["event_payload_vars"].split(","):
        variaveis.setdefault(nome.strip(), "x")
    task = ExternalTask(
        task_id="t-2",
        topic="operadora.events.publish",
        process_instance_id="p-1",
        business_key="ESC-amh-conv-1",
        worker_id="w-1",
        variables=variaveis,
        process_definition_key="SP-OP-ESCALATION-001",
        activity_id=activity_id,
    )
    with capture_logs() as logs:
        await make_publish_event_handler(FakeKafkaPublisher())(task)
    assert _estouros(logs) == []
