"""Bateria de 01/10/2026 (A04, A05): as duas frases de conformidade (F6) sobrevivem ao DL-0052.

O DL-0052 (#577) fez o classificador mandar "voce e uma pessoa ou um robo?" e "voce sabe quem eu
sou?" para `outside_channel`, e `inform` devolve o texto fixo de "fora do canal" SEM chamar o
modelo. A frase que o dono exigiu em 21/09 (dizer que e' um sistema automatizado) deixou de ser
dada, e nenhum teste pegou porque os de A2 (`test_helena_bateria_23_09.py`) forjam o classificador
em `human_request` e nunca leem o TEXTO enviado.

Estes testes rodam o TURNO COMPLETO, com o classificador gravado em cada intencao que ele pode
devolver, e comparam o texto que sai pelo WhatsApp com a constante do dono.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import RESPOSTA_FORA_DO_CANAL, HelenaGraph, HelenaState
from maezo.agents.helena.prompts import RESPOSTA_NAO_CONSIGO_IDENTIFICAR, RESPOSTA_SOU_ASSISTENTE_VIRTUAL
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
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
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


async def _turno(
    mensagem: str, **classificacao: Any
) -> tuple[dict[str, Any], _WhatsApp, FakeCibSevenTransport]:
    cibseven = FakeCibSevenTransport()
    whatsapp = _WhatsApp()
    graph = HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaEmOrdem([_classify_json(**classificacao)])),
        dmn=FakeDmnTransport(),  # nada registrado: consultar tabela aqui seria erro alto e claro
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
    )
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:identidade",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ID",
        "message_body": mensagem,
    }
    resultado = await graph.compile_graph().compile().ainvoke(estado)
    return dict(resultado), whatsapp, cibseven


# O classificador pode devolver qualquer uma destas para uma pergunta de identidade: antes do
# DL-0052 era `human_request` (corrigido em 23/09) ou `information`; depois, `outside_channel`.
_INTENCOES = ["outside_channel", "information", "greeting", "human_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("intencao", _INTENCOES)
@pytest.mark.parametrize(
    "mensagem",
    [
        "Você é uma pessoa ou um robô?",
        "voce e um robo?",
        "isso é automático?",
        "estou falando com uma pessoa?",
    ],
)
async def test_pergunta_se_e_robo_recebe_a_frase_do_dono_literal(mensagem: str, intencao: str) -> None:
    resultado, whatsapp, _ = await _turno(mensagem, intent=intencao)

    assert resultado["response_text"] == RESPOSTA_SOU_ASSISTENTE_VIRTUAL
    assert whatsapp.enviados == [RESPOSTA_SOU_ASSISTENTE_VIRTUAL]
    assert not resultado.get("escalation_started")


@pytest.mark.asyncio
@pytest.mark.parametrize("intencao", _INTENCOES)
@pytest.mark.parametrize(
    "mensagem",
    ["Você sabe quem eu sou?", "vc me conhece?", "sabe meu nome?"],
)
async def test_pergunta_se_a_conhece_recebe_a_frase_do_dono_literal(mensagem: str, intencao: str) -> None:
    resultado, whatsapp, _ = await _turno(mensagem, intent=intencao)

    assert resultado["response_text"] == RESPOSTA_NAO_CONSIGO_IDENTIFICAR
    assert whatsapp.enviados == [RESPOSTA_NAO_CONSIGO_IDENTIFICAR]
    assert not resultado.get("escalation_started")


@pytest.mark.asyncio
async def test_as_duas_perguntas_na_mesma_mensagem_recebem_as_duas_frases_em_ordem() -> None:
    resultado, _, _ = await _turno("você é um robô? e você sabe quem eu sou?", intent="outside_channel")

    assert (
        resultado["response_text"] == f"{RESPOSTA_SOU_ASSISTENTE_VIRTUAL} {RESPOSTA_NAO_CONSIGO_IDENTIFICAR}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mensagem",
    [
        "preciso da segunda via do boleto",
        # "quem e" solto NAO e' pergunta sobre a Helena: quem responde "sou um assistente virtual" a
        # esta pergunta responde errado.
        "quem é o titular do contrato?",
        "qual é o robô de atendimento do plano?",
    ],
)
async def test_assunto_que_nao_e_identidade_continua_com_o_texto_fixo_de_fora_do_canal(mensagem: str) -> None:
    resultado, _, _ = await _turno(mensagem, intent="outside_channel")

    assert resultado["response_text"] == RESPOSTA_FORA_DO_CANAL


@pytest.mark.asyncio
async def test_pedido_explicito_de_humano_vence_a_pergunta_de_identidade() -> None:
    """ "voce e um robo? quero falar com uma pessoa" continua `human_request`: o pedido vence."""
    resultado, _, _ = await _turno("você é um robô? quero falar com uma pessoa", intent="human_request")

    assert resultado["response_text"] != RESPOSTA_SOU_ASSISTENTE_VIRTUAL
    assert resultado.get("escalation_started") is True


@pytest.mark.asyncio
async def test_com_sintoma_a_triagem_vence_a_pergunta_de_identidade() -> None:
    """A saude vence sempre (DL-0052): com codigo de sintoma o caminho e' a tabela, nao a frase."""
    cibseven = FakeCibSevenTransport()
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    graph = HelenaGraph(
        inference=cast(
            InferenceProvider,
            _InferenciaEmOrdem(
                [
                    _classify_json(
                        intent="symptom",
                        population="adult",
                        sintoma_codigo="dor_toracica",
                        intensidade="grave",
                    ),
                    "resumo",
                    "um profissional humano vai continuar",
                ]
            ),
        ),
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:identidade2",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ID2",
        "message_body": "você é um robô? estou com dor no peito",
    }

    resultado = await graph.compile_graph().compile().ainvoke(estado)

    assert resultado.get("escalation_started") is True
    assert resultado["response_text"] != RESPOSTA_SOU_ASSISTENTE_VIRTUAL
