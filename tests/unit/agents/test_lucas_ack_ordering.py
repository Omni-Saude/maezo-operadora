"""LUC-05: o ACK ao beneficiario so sai DEPOIS que a escalacao existe de verdade.

O DEFEITO. `LucasGraph.escalate_human` enviava o ACK ("um atendente humano vai continuar") no
proprio no, ANTES de `start_process`. Se o start entao falhava, o `except CibSevenError` gravava
apenas `process_started=False` + `error`, o `desfecho` seguia `escalado_humano` e o grafo ia
calado ao terminal — com o beneficiario ja informado de que um humano assumiria e ZERO instancias
de SP-OP-ESCALATION-001 existindo. Sem retry, sem prazo (o SLA vive na instancia que nao nasceu) e
sem alerta.

A CORRECAO (ordem, nao mensagem). O envio migrou para um no proprio, `send_escalation_ack`,
alcancavel SO pelo ramo de sucesso da aresta condicional que sai de `start_process`. O replay e
seguro porque `start_process_idempotent` e idempotente por business key: uma retentativa do mesmo
caso reencontra a instancia viva (`ALREADY_ACTIVE`) em vez de abrir uma segunda.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import LucasGraph
from maezo.platform import observability
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink

_DESFECHO_ERRO = "erro_inicio_processo"


class _FakeInference:
    model_id = "claude-luc05-probe"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append(prompt)
        return "texto sintetico"


class _FakeDmn:
    async def evaluate(
        self, table: str, dmn_input: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        return ([{"roteamento": "ATENDIMENTO_HUMANO"}], DmnVersion("t", "id1", 1, "d1"))


class _RecordingWhatsApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"ok": True}


class _FailingStartTransport(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError(f"engine indisponivel (probe LUC-05): start de {process_key}")


def _graph(whatsapp: _RecordingWhatsApp, cibseven: FakeCibSevenTransport) -> LucasGraph:
    return LucasGraph(
        inference=_FakeInference(),
        dmn=_FakeDmn(),
        cibseven=cibseven,
        whatsapp=whatsapp,
        audit_sink=FakeStartAuditSink(),
    )


_ESTADO = {
    "tenant_id": "amh",
    "conversation_id": "wa:amh:luc05",
    "canal": "whatsapp",
    "beneficiario_pseudo_id": "PSEUDO-TESTE-LUC05",
    "to_hash": "hash-luc05",
    "business_key": "ESC-amh-luc05",
    "route": "escalate_human",
    "motivo_humano": "contestacao_cobranca",
    "motivo_categoria": "cobranca",
    "severidade": "leve",
    "grupo_humano": "atendimento-humano",
}


async def test_escalate_human_no_longer_sends_the_ack_itself() -> None:
    """O no de escalacao monta o dossie e MARCA o ACK como pendente — nao fala com ninguem."""
    whatsapp = _RecordingWhatsApp()
    saida = await _graph(whatsapp, FakeCibSevenTransport()).escalate_human(dict(_ESTADO))

    assert whatsapp.sent == [], "escalate_human ainda envia o ACK antes do start (LUC-05)"
    assert saida["mensagem_enviada"] is False
    assert saida["ack_pending"] is True
    assert saida["desfecho"] == "escalado_humano"


async def test_ack_is_sent_only_after_a_successful_start() -> None:
    whatsapp = _RecordingWhatsApp()
    graph = _graph(whatsapp, FakeCibSevenTransport())

    estado = {**_ESTADO, **await graph.escalate_human(dict(_ESTADO))}
    assert whatsapp.sent == []

    estado = {**estado, **await graph.start_process(estado)}
    assert estado["process_started"] is True

    saida = await graph.send_escalation_ack(estado)
    assert len(whatsapp.sent) == 1, "o ACK nao saiu apos um start bem-sucedido"
    assert saida["mensagem_enviada"] is True
    assert saida["ack_pending"] is False
    assert saida["desfecho"] == "escalado_humano"


async def test_failed_start_sends_nothing_and_ends_in_the_error_desfecho(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CibSevenError` => `whatsapp.send` NUNCA chamado, desfecho de erro, retry sinalizado."""
    contagem: list[int] = []
    monkeypatch.setattr(observability, "record_agent_error", lambda: contagem.append(1))

    whatsapp = _RecordingWhatsApp()
    graph = _graph(whatsapp, _FailingStartTransport())

    estado = {**_ESTADO, **await graph.escalate_human(dict(_ESTADO))}
    estado = {**estado, **await graph.start_process(estado)}
    assert estado["start_failed"] is True

    saida = await graph.notify_start_failure(estado)

    assert whatsapp.sent == [], (
        "o beneficiario foi informado de que um humano assumiria enquanto ZERO instancias de "
        "SP-OP-ESCALATION-001 existiam (o defeito LUC-05 inteiro)"
    )
    assert saida["desfecho"] == _DESFECHO_ERRO
    assert saida["mensagem_enviada"] is False
    assert saida["ack_pending"] is True
    assert saida["retryable_error"] is True
    assert contagem == [1]


async def test_ack_node_is_a_noop_on_the_informational_route() -> None:
    """A jornada `respond_member` nunca abre processo — e o no de ACK nao pode inventar um envio."""
    whatsapp = _RecordingWhatsApp()
    graph = _graph(whatsapp, FakeCibSevenTransport())
    saida = await graph.send_escalation_ack({**_ESTADO, "route": "respond_member", "process_started": False})
    assert whatsapp.sent == []
    assert saida == {}
