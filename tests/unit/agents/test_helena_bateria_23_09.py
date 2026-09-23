"""Bateria do diretor de 23/09/2026 na imagem `79cb6ab3`: o caso `A2`.

`A2` — "quem e voce?" e "voce e uma pessoa ou um robo?" sairam `human_request` e abriram
SP-OP-ESCALATION-001 (`solicitacao_humano`, P3, `atendimento-humano`, 4h). A Helena responde essa
pergunta por obrigacao de transparencia; abrir fila humana para ela e' trabalho sem pedido.

O outro divergente da mesma bateria, `E3` (agendamento), NAO esta aqui porque nao era defeito da
Helena: o log do receptor mostra `route=schedule`, `motivo_categoria=solicitacao_humano`,
`desfecho=escalado_humano`, e o motor registrou `escalation_routing` -> `atendimento-humano` / P3 /
PT4H — 4,5 s depois do inicio do processo. O leitor da bateria esperava 2,5 s fixos e concluiu
"sem rota". O conserto dele e' no leitor, nao no grafo.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import HelenaGraph, HelenaState, _pergunta_de_identidade_sem_pedido
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _InferenciaEmOrdem:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


def _classify_json(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return json.dumps(base)


def _graph(respostas: list[str]) -> tuple[HelenaGraph, FakeCibSevenTransport]:
    cibseven = FakeCibSevenTransport()
    graph = HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaEmOrdem(respostas)),
        dmn=FakeDmnTransport(),  # nada registrado: consultar tabela aqui seria erro alto e claro
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )
    return graph, cibseven


def _estado(mensagem: str) -> HelenaState:
    return {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:bateria23",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-23",
        "message_body": mensagem,
    }


async def _rota(graph: HelenaGraph, mensagem: str) -> tuple[str, dict[str, Any]]:
    corrente: dict[str, Any] = dict(_estado(mensagem))
    corrente.update(await graph.receive(cast(HelenaState, corrente)))
    corrente.update(await graph.classify(cast(HelenaState, corrente)))
    return HelenaGraph._route(cast(HelenaState, corrente)), corrente


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mensagem", ["quem e voce?", "voce e uma pessoa ou um robo?", "Com quem estou falando?"]
)
async def test_a2_pergunta_de_identidade_classificada_como_pedido_nao_abre_fila(mensagem: str) -> None:
    """O modelo errou do jeito que errou no ar (`human_request`); a rota nao pode depender disso."""
    graph, _ = _graph([_classify_json(intent="human_request")])

    destino, estado = await _rota(graph, mensagem)

    assert destino == "inform"
    assert estado["intent"] == "information"
    assert not estado.get("escalation_motivo")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mensagem",
    [
        "voce e um robo? quero falar com uma pessoa",
        "quero falar com um atendente",
        "me passa pra alguem por favor",
        "voce e humana? me liga",
    ],
)
async def test_pedido_explicito_de_humano_continua_escalando(mensagem: str) -> None:
    """O lado que a cerca protege: quem PEDE humano continua recebendo. O pedido vence a pergunta."""
    graph, _ = _graph([_classify_json(intent="human_request")])

    destino, estado = await _rota(graph, mensagem)

    assert destino == "escalate"
    assert estado["escalation_motivo"] == "solicitacao_humano"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Quem é você?", True),
        ("vc é robô?", True),
        ("isso é uma IA?", True),
        # "atendente" esta' no padrao de PEDIDO de proposito: na duvida, protege quem quer humano.
        ("você é atendente?", False),
        ("quem pode me atender?", False),
        ("quem e o medico responsavel?", False),
        ("estou com dor de cabeca", False),
    ],
)
def test_a_cerca_e_estreita_na_identidade_e_larga_no_pedido(texto: str, esperado: bool) -> None:
    assert _pergunta_de_identidade_sem_pedido(texto) is esperado
