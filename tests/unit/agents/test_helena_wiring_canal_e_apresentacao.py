"""Bateria de 21/09 — os dois fios que ligam F6 e F7 ao grafo.

A frente de prompts entregou a cerca de canal (`motivo_de_canal_nao_confirmado`) e a instrucao
`apresentacao_ja_feita` no `response_prompt`, mas o `graph.py` estava travado para ela. Este arquivo
prova que os dois estao LIGADOS: a cerca de canal passa pelo unico chokepoint de saida
(`_respond_llm`), e o sinal de apresentacao nasce no envio, sobrevive ao `receive` do turno
seguinte e chega ao contexto do prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import (
    ERRO_RESPOSTA_RECUSADA,
    HelenaGraph,
    RespostaRecusadaError,
)
from maezo.agents.helena.prompts import RECUSA_CANAL_NAO_CONFIRMADO
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: O texto MEDIDO no caso E4 da bateria: canal sem dono, em cinco casos.
TEXTO_E4 = (
    "Este canal pode te orientar sobre como consultar o valor da mensalidade. Voce pode "
    "verificar pelo aplicativo do plano, no portal da operadora ou entrando em contato com a "
    "central de atendimento."
)
#: O mesmo conteudo, com os nomes que EXISTEM (definicao do dono, 21/09).
TEXTO_COM_NOME = (
    "O valor da mensalidade e a segunda via do boleto ficam no aplicativo Austa Clinicas e no "
    "portal do plano. Se preferir, a central de atendimento do plano tambem atende essa duvida."
)


class _InferenciaQueGuardaOPrompt:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self.texto


class _WhatsAppQueRegistra:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> None:
        self.enviados.append((to_hash, text))


def _graph(texto: str, whatsapp: Any = None) -> tuple[HelenaGraph, _InferenciaQueGuardaOPrompt]:
    inferencia = _InferenciaQueGuardaOPrompt(texto)
    graph = HelenaGraph(
        inference=inferencia,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
    )
    return graph, inferencia


def _estado(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_wiring-teste",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-TESTE-WIRING",
        "message_body": "quanto eu devo de mensalidade?",
        "intensidade": "desconhecida",
    }
    base.update(extra)
    return base


# --- F7: a cerca de canal esta' no chokepoint ---------------------------------------------------


async def test_o_texto_medido_no_e4_e_recusado_no_chokepoint_com_o_grupo_de_canal() -> None:
    graph, _ = _graph(TEXTO_E4)

    with pytest.raises(RespostaRecusadaError) as recusa:
        await graph._respond_llm(_estado(), "inform")

    assert recusa.value.grupo == RECUSA_CANAL_NAO_CONFIRMADO
    assert recusa.value.padrao == "aplicativo do plano"


@pytest.mark.parametrize("rota", ["inform", "escalate", "schedule"])
async def test_canal_nao_confirmado_e_recusado_em_toda_rota(rota: str) -> None:
    """Nao existe rota em que um canal inexistente seja verdade."""
    graph, _ = _graph("Consulte no app do convenio ou ligue 0800 para a operadora.")

    with pytest.raises(RespostaRecusadaError) as recusa:
        await graph._respond_llm(_estado(), rota)  # type: ignore[arg-type]

    assert recusa.value.grupo == RECUSA_CANAL_NAO_CONFIRMADO


async def test_o_nome_confirmado_passa_pelo_chokepoint() -> None:
    """Nao-vacuidade: a cerca reprova o nome errado, nao a mencao a canal."""
    graph, _ = _graph(TEXTO_COM_NOME)

    assert await graph._respond_llm(_estado(), "inform") == TEXTO_COM_NOME


async def test_inform_com_canal_inventado_vira_escalonamento_como_qualquer_recusa() -> None:
    """A recusa de canal entra pelo MESMO caminho das outras tres: `inform` barrado -> humano."""
    graph, _ = _graph(TEXTO_E4)

    saida = await graph.inform(_estado())

    assert saida["response_kind"] == "escalate"
    assert saida["error"] == ERRO_RESPOSTA_RECUSADA
    assert "aplicativo do plano" not in str(saida)


# --- F6: o cartao de apresentacao e' uma vez por conversa --------------------------------------


async def test_o_envio_bem_sucedido_acende_apresentacao_ja_feita() -> None:
    whatsapp = _WhatsAppQueRegistra()
    graph, _ = _graph("Oi! Sou Helena, navegadora de saude do plano.", whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text="Oi! Sou Helena, navegadora de saude do plano.", response_kind="inform")
    )

    assert saida["apresentacao_ja_feita"] is True
    assert len(whatsapp.enviados) == 1


async def test_receive_preserva_o_sinal_entre_turnos_e_nao_o_inventa() -> None:
    graph, _ = _graph("qualquer")

    com_sinal = await graph.receive(_estado(apresentacao_ja_feita=True))
    sem_sinal = await graph.receive(_estado())

    assert com_sinal["apresentacao_ja_feita"] is True
    assert sem_sinal["apresentacao_ja_feita"] is False


async def test_o_sinal_chega_ao_contexto_do_prompt_de_resposta() -> None:
    """A chave entra no contexto SO' quando e' `True` (21/09/2026, segunda rodada).

    As irmas (`population`, `memoria_a_confirmar`) sao condicionais, e o prompt inteiro e' escrito
    no idioma "QUANDO O CONTEXTO TROUXER X". Mandar `apresentacao_ja_feita: False` pedia ao modelo
    que interpretasse uma negacao explicita num texto que so' fala de presenca — ruido no unico
    lugar em que a AUSENCIA ja era a informacao.
    """
    graph, inferencia = _graph(TEXTO_COM_NOME)

    await graph._respond_llm(_estado(apresentacao_ja_feita=True), "inform")
    await graph._respond_llm(_estado(), "inform")

    assert "'apresentacao_ja_feita': True" in inferencia.prompts[0]
    # A forma com aspas simples e' o dict do contexto; o TEXTO do prompt cita o campo entre
    # backticks ("quando o contexto trouxer `apresentacao_ja_feita`"), e por isso continua ali.
    assert "'apresentacao_ja_feita'" not in inferencia.prompts[1]
