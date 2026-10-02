"""Bateria de 01/10/2026 (S1: A02, A03, A10): saudacao e despedida tem texto fixo.

O modelo, que redigia a resposta a "oi", "tudo bem?" e "obrigado, era so isso", reabria o cartao de
apresentacao a cada turno — em 6 de 6 execucoes — depois de ja' ter se apresentado, o que o
`response_prompt` proibe. Instrucao de prompt nao segura uma regra que depende de memoria da
conversa; `apresentacao_ja_feita` ja' e' mantido pelo grafo, entao a decisao passa a ser dele.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_DESPEDIDA,
    RESPOSTA_SAUDACAO_ABERTURA,
    RESPOSTA_SAUDACAO_CURTA,
    HelenaGraph,
    HelenaState,
    _e_despedida,
)
from maezo.agents.helena.prompts import menciona_encaminhamento, motivo_de_recusa
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _Gravador:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else "RASCUNHO-DO-MODELO"


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classify(intent: str) -> str:
    return json.dumps(
        {
            "intent": intent,
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
        }
    )


def _graph(gravador: _Gravador, whatsapp: _WhatsApp | None = None) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, gravador),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp or _WhatsApp(),
    )


def _estado(mensagem: str, **extra: Any) -> HelenaState:
    estado = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:saudacao",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-SD",
        "message_body": mensagem,
        "intent": "greeting",
    }
    estado.update(extra)
    return cast(HelenaState, estado)


@pytest.mark.asyncio
async def test_a_primeira_saudacao_recebe_o_cartao_de_abertura_sem_chamar_o_modelo() -> None:
    gravador = _Gravador([_classify("greeting")])
    whatsapp = _WhatsApp()
    estado = cast(
        HelenaState,
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:saudacao",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "PSEUDO-SD",
            "message_body": "Bom dia! Tudo bem com voce?",
        },
    )

    resultado = dict(await _graph(gravador, whatsapp).compile_graph().compile().ainvoke(estado))

    assert resultado["response_text"] == RESPOSTA_SAUDACAO_ABERTURA
    assert whatsapp.enviados == [RESPOSTA_SAUDACAO_ABERTURA]
    assert len(gravador.prompts) == 1, "so' a classificacao chama o modelo"
    assert not resultado.get("escalation_started")


@pytest.mark.asyncio
async def test_depois_de_se_apresentar_a_saudacao_e_curta_e_sem_o_cartao() -> None:
    saida = await _graph(_Gravador([])).inform(_estado("oii", apresentacao_ja_feita=True))

    assert saida["response_text"] == RESPOSTA_SAUDACAO_CURTA
    assert "Helena" not in saida["response_text"]


@pytest.mark.asyncio
async def test_o_cartao_de_abertura_acende_o_sinal_de_apresentacao_ja_feita() -> None:
    """O elo que faz a regra funcionar: o texto enviado traz "Helena", entao o turno seguinte sabe."""
    whatsapp = _WhatsApp()
    graph = _graph(_Gravador([_classify("greeting")]), whatsapp)
    estado = cast(
        HelenaState,
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:saudacao",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "PSEUDO-SD",
            "message_body": "oi",
        },
    )

    resultado = dict(await graph.compile_graph().compile().ainvoke(estado))

    assert resultado.get("apresentacao_ja_feita") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("intencao", ["greeting", "information"])
@pytest.mark.parametrize(
    "mensagem",
    [
        "Obrigado, era so isso",
        "obrigada!",
        "valeu, tchau",
        "muito obrigado pela ajuda",
        "ate mais, obrigado",
    ],
)
async def test_a_despedida_recebe_a_resposta_fixa_com_ou_sem_apresentacao(
    mensagem: str, intencao: str
) -> None:
    for ja_se_apresentou in (False, True):
        saida = await _graph(_Gravador([])).inform(
            _estado(mensagem, intent=intencao, apresentacao_ja_feita=ja_se_apresentou)
        )

        assert saida["response_text"] == RESPOSTA_DESPEDIDA, (mensagem, ja_se_apresentou)


@pytest.mark.parametrize(
    "mensagem",
    [
        "obrigado, mas agora estou com dor no peito",
        "obrigado, e me liga amanha",
        "ok",
        "tchau, quero cancelar meu plano",
        "",
        "muito obrigado pela ajuda que voce me deu na semana passada com tudo isso",
    ],
)
def test_so_e_despedida_a_mensagem_inteira_de_agradecimento_ou_adeus(mensagem: str) -> None:
    """Um falso positivo engoliria um pedido; por isso o vocabulario e' fechado e a mensagem inteira."""
    assert _e_despedida(mensagem) is False, mensagem


def test_os_textos_fixos_passam_nas_cercas_de_inform_sem_anunciar_encaminhamento() -> None:
    for texto in (RESPOSTA_SAUDACAO_ABERTURA, RESPOSTA_SAUDACAO_CURTA, RESPOSTA_DESPEDIDA):
        assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None, texto
        assert menciona_encaminhamento(texto) is False, texto


def test_o_cartao_de_abertura_nao_promete_o_que_o_dl_0052_retirou() -> None:
    """Desde o DL-0052 duvida administrativa recebe `RESPOSTA_FORA_DO_CANAL`; o cartao antigo
    ("posso te orientar sobre duvidas administrativas do plano") prometia o contrario."""
    assert "administrativ" not in RESPOSTA_SAUDACAO_ABERTURA.lower()
    assert "plano" not in RESPOSTA_SAUDACAO_ABERTURA.lower()
