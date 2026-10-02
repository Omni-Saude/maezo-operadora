"""Bateria de 01/10/2026 (B13): "vou abrir reclamacao na ANS" abre atendimento humano (DL-0065, interino).

Antes a mensagem recebia o texto fixo de fora do canal e a pessoa ficava sem ninguem. A rota usada e'
a que ja' existe (`solicitacao_humano`, P3): motivo e fila proprios (SP-OP-NIP-001) dependem do Diretor
e do regulatorio. O padrao exige o verbo de reclamacao E o orgao: citar a ANS sozinha nao escala.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import HelenaGraph, HelenaState, _ameaca_regulatoria
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AMEACAS = [
    "Vou abrir reclamação na ANS, ninguém resolve nada",
    "vou reclamar no Procon",
    "Vou denunciar esse plano na ouvidoria",
    "vou acionar a ANS contra voces",
    "Vou processar o plano na justiça",
    "a ANS vai receber minha reclamacao",
]
_NAO_SAO_AMEACA = [
    "O que é a ANS?",
    "Qual o prazo que a ANS define para autorização?",
    "o processo da ANS demora?",
    "Preciso da segunda via do boleto",
    "Meu processo de reembolso está em andamento",
    "Oi, bom dia",
]


@pytest.mark.parametrize("texto", _AMEACAS)
def test_ameaca_de_reclamacao_a_orgao_regulador_e_reconhecida(texto: str) -> None:
    assert _ameaca_regulatoria(texto), texto


@pytest.mark.parametrize("texto", _NAO_SAO_AMEACA)
def test_citar_o_orgao_ou_o_processo_sem_reclamar_nao_escala(texto: str) -> None:
    assert not _ameaca_regulatoria(texto), texto


class _Gravador:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classificacao(intent: str) -> str:
    return json.dumps(
        {
            "intent": intent,
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
        }
    )


async def _turno(intent: str, texto: str) -> dict[str, Any]:
    graph = HelenaGraph(
        inference=cast(InferenceProvider, _Gravador([_classificacao(intent)])),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:ameaca-regulatoria",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ANS",
        "message_body": texto,
    }
    return dict(await graph.compile_graph().compile().ainvoke(estado))


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["outside_channel", "information"])
async def test_a_ameaca_abre_atendimento_humano_qualquer_que_seja_a_intencao_do_modelo(intent: str) -> None:
    resultado = await _turno(intent, "Vou abrir reclamação na ANS, ninguém resolve nada")

    assert resultado["escalation_started"] is True
    assert resultado["escalation_motivo"] == "solicitacao_humano"


@pytest.mark.asyncio
async def test_citar_a_ans_sem_reclamar_continua_sem_atendimento() -> None:
    resultado = await _turno("outside_channel", "O que é a ANS?")

    assert not resultado.get("escalation_started")
