"""ASSUMIR DESARMA O ALERTA DE CIENCIA (pendencia 40, DL-0152).

O timer `BT_SlaAck` de SP-OP-ESCALATION-001 e' NAO interruptivo: dispara mesmo com o atendente ja'
tendo assumido `UT_TratarEscalonamento`, e o `notify_supervisor` alertava o supervisor de um caso que
ja' tinha dono. Agora, no caminho do SLA de CIENCIA (`motivo=sla_ack_breached`), o handler le o
`assignee` da task pelo seam `engine` e, com dono, registra `escalation.ciencia_dada` e NAO alerta.

  * assumida -> nao alerta (evento fechado "ciencia dada");
  * nao assumida -> alerta;
  * leitura falha (ou sem seam de leitura) -> alerta (lado seguro);
  * fallback de canal (`motivo_fallback=notificacao_primaria_falhou`) -> alerta SEMPRE, sem leitura.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import structlog

from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    CibSevenHttpTransport,
    FakeCibSevenTransport,
)
from maezo.tools.workers.escalation import (
    EVENTO_CIENCIA_DADA,
    EVENTO_SUPERVISOR_ALERTADO,
    make_notify_supervisor_handler,
    register_escalation_workers,
)
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher

_TOPICO = "operadora.escalation.notify_supervisor"
_INSTANCIA = "proc-ack-1"
_UT = "UT_TratarEscalonamento"


def _vars(**extra: Any) -> dict[str, Any]:
    variaveis: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "conv-1",
        "motivo_categoria": "red_flag_clinico",
        "severidade": "grave",
        "grupo_atendimento": "plantao-clinico",
        "prioridade": "P1",
    }
    variaveis.update(extra)
    return variaveis


def _ack() -> ExternalTask:
    return ExternalTask(
        task_id="t-1",
        topic=_TOPICO,
        process_instance_id=_INSTANCIA,
        business_key="ESC-amh-conv-1",
        worker_id="w-1",
        variables=_vars(motivo="sla_ack_breached"),
    )


def _fallback() -> ExternalTask:
    return ExternalTask(
        task_id="t-2",
        topic=_TOPICO,
        process_instance_id=_INSTANCIA,
        business_key="ESC-amh-conv-1",
        worker_id="w-1",
        variables=_vars(motivo_fallback="notificacao_primaria_falhou"),
    )


class _Motor(FakeCibSevenTransport):
    def __init__(self, *, falha: Exception | None = None) -> None:
        super().__init__()
        self.falha = falha
        self.leituras: list[tuple[str, str]] = []

    async def read_user_task_assignee(self, process_instance_id: str, task_definition_key: str) -> str | None:
        self.leituras.append((process_instance_id, task_definition_key))
        if self.falha is not None:
            raise self.falha
        return await super().read_user_task_assignee(process_instance_id, task_definition_key)


def _assumida(assignee: str | None = "atendente.ana") -> _Motor:
    motor = _Motor()
    motor.seed_user_task_assignee(_INSTANCIA, _UT, assignee)
    return motor


async def test_sla_de_ciencia_com_task_assumida_nao_alerta_o_supervisor() -> None:
    kafka = FakeKafkaPublisher()
    motor = _assumida()
    with structlog.testing.capture_logs() as logs:
        resultado = await make_notify_supervisor_handler(kafka, motor)(_ack())

    assert kafka.published == [], "ciencia dada: nenhum alerta ao supervisor"
    assert motor.leituras == [(_INSTANCIA, _UT)]
    assert resultado["status"] == "supervisor_nao_alertado_ciencia_dada"
    assert "alert_to" not in resultado
    eventos = [linha for linha in logs if linha["event"] == EVENTO_CIENCIA_DADA]
    assert eventos == [
        {"event": EVENTO_CIENCIA_DADA, "motivo_alerta": "sla_ack_breached", "log_level": "info"}
    ]
    assert not [linha for linha in logs if linha["event"] == EVENTO_SUPERVISOR_ALERTADO]


@pytest.mark.parametrize("assignee", [None, "", "   "], ids=["sem_dono", "vazio", "branco"])
async def test_sla_de_ciencia_com_task_nao_assumida_alerta(assignee: str | None) -> None:
    kafka = FakeKafkaPublisher()
    resultado = await make_notify_supervisor_handler(kafka, _assumida(assignee))(_ack())

    assert resultado["status"] == "supervisor_notified"
    [(_topico, carga, _chave)] = kafka.published
    assert carga["type"] == "escalation.notify_supervisor"
    assert carga["motivo_alerta"] == "sla_ack_breached"


async def test_sla_de_ciencia_sem_task_ativa_alerta() -> None:
    """Nada semeado: o motor nao tem a task ativa (ex.: instancia de outra chave) -> alerta."""
    kafka = FakeKafkaPublisher()
    resultado = await make_notify_supervisor_handler(kafka, _Motor())(_ack())

    assert resultado["status"] == "supervisor_notified"
    assert len(kafka.published) == 1


@pytest.mark.parametrize(
    "falha",
    [CibSevenError("motor fora"), httpx.ConnectError("recusada"), TypeError("bug de leitura")],
    ids=["cibseven", "rede", "bug"],
)
async def test_leitura_que_falha_alerta(falha: Exception) -> None:
    kafka = FakeKafkaPublisher()
    motor = _Motor(falha=falha)
    with structlog.testing.capture_logs() as logs:
        resultado = await make_notify_supervisor_handler(kafka, motor)(_ack())

    assert motor.leituras == [(_INSTANCIA, _UT)]
    assert resultado["status"] == "supervisor_notified"
    assert len(kafka.published) == 1
    [falhou] = [linha for linha in logs if linha["event"] == "escalation_ciencia_leitura_falhou"]
    assert falhou["error_type"] == type(falha).__name__
    assert str(falha) not in str(falhou), "so' o TIPO do erro vai ao log"


async def test_sem_seam_de_leitura_alerta_como_antes() -> None:
    kafka = FakeKafkaPublisher()
    resultado = await make_notify_supervisor_handler(kafka)(_ack())

    assert resultado["status"] == "supervisor_notified"
    assert len(kafka.published) == 1


async def test_fallback_de_canal_alerta_sempre_mesmo_com_task_assumida() -> None:
    kafka = FakeKafkaPublisher()
    motor = _assumida()
    resultado = await make_notify_supervisor_handler(kafka, motor)(_fallback())

    assert motor.leituras == [], "o fallback nao consulta o motor"
    assert resultado["status"] == "supervisor_notified"
    [(_topico, carga, _chave)] = kafka.published
    assert carga["motivo_alerta"] == "notificacao_primaria_falhou"


async def test_register_escalation_workers_entrega_o_seam_engine_ao_handler() -> None:
    """O composition root (`register_default_workers`) passa `engine=` a todos os bootstraps."""

    class _Harness:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {}

        def register(self, topic: str, handler: Any) -> None:
            self.handlers[topic] = handler

    harness = _Harness()
    kafka = FakeKafkaPublisher()
    register_escalation_workers(harness, kafka, engine=_assumida(), dmn=None)  # type: ignore[arg-type]

    resultado = await harness.handlers[_TOPICO](_ack())
    assert resultado["status"] == "supervisor_nao_alertado_ciencia_dada"
    assert kafka.published == []


# --- a leitura REAL do transporte HTTP (wire do motor via `httpx.MockTransport`) ---------------


def _transporte_http(
    resposta: httpx.Response | Exception,
) -> tuple[CibSevenHttpTransport, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def _responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    transporte = CibSevenHttpTransport("http://motor/engine-rest")
    transporte._client = httpx.AsyncClient(
        base_url="http://motor/engine-rest", transport=httpx.MockTransport(_responder)
    )
    return transporte, pedidos


async def test_transporte_http_le_o_assignee_da_task_ativa() -> None:
    transporte, pedidos = _transporte_http(
        httpx.Response(200, json=[{"id": "tk-1", "taskDefinitionKey": _UT, "assignee": "atendente.ana"}])
    )
    assert await transporte.read_user_task_assignee(_INSTANCIA, _UT) == "atendente.ana"
    [pedido] = pedidos
    assert pedido.method == "GET"
    assert pedido.url.path == "/engine-rest/task"
    assert dict(pedido.url.params) == {"processInstanceId": _INSTANCIA, "taskDefinitionKey": _UT}


@pytest.mark.parametrize("corpo", [[], [{"id": "tk-1", "assignee": None}]], ids=["sem_task", "sem_dono"])
async def test_transporte_http_sem_dono_devolve_none(corpo: list[dict[str, Any]]) -> None:
    transporte, _ = _transporte_http(httpx.Response(200, json=corpo))
    assert await transporte.read_user_task_assignee(_INSTANCIA, _UT) is None


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(500, json={"message": "x"}),
        httpx.Response(200, json={"nao": "lista"}),
        httpx.Response(200, content=b"<html>"),
        httpx.ConnectError("recusada"),
    ],
    ids=["http_500", "forma_errada", "nao_json", "rede"],
)
async def test_transporte_http_leitura_que_falha_levanta(resposta: httpx.Response | Exception) -> None:
    transporte, _ = _transporte_http(resposta)
    with pytest.raises(CibSevenError):
        await transporte.read_user_task_assignee(_INSTANCIA, _UT)


# --- o seam do DAEMON (`FreshClientCibSevenTransport`) delega a leitura ---------------------------


async def test_transporte_do_daemon_delega_a_leitura_ao_transporte_interno() -> None:
    from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

    interno = _assumida()
    daemon = FreshClientCibSevenTransport("http://motor/engine-rest", transport_factory=lambda: interno)
    kafka = FakeKafkaPublisher()

    assert await daemon.read_user_task_assignee(_INSTANCIA, _UT) == "atendente.ana"
    resultado = await make_notify_supervisor_handler(kafka, daemon)(_ack())
    assert resultado["status"] == "supervisor_nao_alertado_ciencia_dada"
    assert kafka.published == []


async def test_transporte_do_daemon_com_interno_sem_a_leitura_levanta_e_o_handler_alerta() -> None:
    from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

    class _InternoSemLeitura:
        async def close(self) -> None:
            return None

    daemon = FreshClientCibSevenTransport(
        "http://motor/engine-rest",
        transport_factory=lambda: _InternoSemLeitura(),  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(CibSevenError):
        await daemon.read_user_task_assignee(_INSTANCIA, _UT)
    kafka = FakeKafkaPublisher()
    resultado = await make_notify_supervisor_handler(kafka, daemon)(_ack())
    assert resultado["status"] == "supervisor_notified"
    assert len(kafka.published) == 1
