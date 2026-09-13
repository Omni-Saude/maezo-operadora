"""A recusa de saida: o texto que viola a regra NAO chega ao beneficiario.

O QUE ACONTECEU, medido em 13/09/2026 pelo diretor no Canal de Teste, contra a imagem que ja
carregava `response-v3`. Cinco turnos, uma mae, uma crianca de 2 anos:

  turno 3  "Minha filha tem 2 anos, esta com o nariz escorrendo"
           -> "Como nao ha sinais de alerta na descricao, um profissional humano entrara em
              contato" + "Aguarde, pois logo alguem da equipe entrara em contato."
  turno 5  "Nao tem cedido com medicamentos, ela ja tomou antibiotico"
           -> "Como nao identificamos sinais de alerta na mensagem [...]"

Nos cinco turnos: `desfecho=resolvido_automatico`, `route=inform`, ZERO processos abertos. A
Helena prometeu, duas vezes, um contato humano que nao existia — nao ha processo, nao ha fila,
nao ha tarefa, ninguem ia ligar. E afirmou a ausencia de alerta duas vezes, com a proibicao do
`response-v3` valendo.

POR QUE ESTES TESTES EXISTEM SEPARADOS DOS GOLDEN. O golden roda com resposta GRAVADA: a canaria
dele examina a gravacao, nunca o texto que o modelo produz. Estes aqui exercitam o CAMINHO — o
rascunho e' barrado, o turno muda de rota, o processo abre. E' a diferenca entre pedir ao modelo
e impedir o modelo, que e' o achado inteiro deste documento.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import (
    ERRO_RESPOSTA_RECUSADA,
    RESPOSTA_HANDOFF_RECUSADA,
    HelenaGraph,
)
from maezo.agents.helena.prompts import motivo_de_recusa
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# --- Os textos REAIS do documento de 13/09/2026. Nao sao exemplos inventados. -------------------

TURNO_3 = (
    "Olá! Entendo que você está preocupada com o nariz escorrendo da sua filha. Este canal pode "
    "orientar e encaminhar para atendimento, se necessário. Como não há sinais de alerta na "
    "descrição, um profissional humano entrará em contato para avaliar melhor e ajudar com as "
    "próximas etapas. Aguarde, pois logo alguém da equipe entrará em contato."
)
TURNO_5 = (
    "Olá! Entendi que você está com uma preocupação. Como não identificamos sinais de alerta na "
    "mensagem, gostaria de entender um pouco melhor: você poderia descrever como está se "
    "sentindo ou quais sintomas tem observado?"
)
CONFORME = (
    "Olá! Este canal pode orientar sobre o plano, encaminhar para um profissional e ajudar com "
    "agendamento. Pode me contar um pouco mais sobre o que está acontecendo?"
)
#: Uma promessa de humano SEM negativa clinica — o caso que separa as duas cercas.
SO_PROMESSA = "Olá! Recebi sua mensagem e alguém da equipe entrará em contato com você em breve."


class _InferenciaFixa:
    """Devolve sempre o mesmo texto, para qualquer prompt. O que se testa aqui e' a CERCA."""

    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.chamadas = 0

    async def generate(self, prompt: str, **_: Any) -> str:
        self.chamadas += 1
        return self.texto


def _graph(texto: str, cibseven: FakeCibSevenTransport | None = None) -> HelenaGraph:
    return HelenaGraph(
        inference=_InferenciaFixa(texto),
        dmn=FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=None,
    )


def _estado(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:recusa-teste",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-TESTE-RECUSA",
        "message_body": "minha filha tem 2 anos e esta com o nariz escorrendo",
        "intensidade": "desconhecida",
    }
    base.update(extra)
    return base


# --- A funcao pura -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rotulo", "texto", "grupo"),
    [
        ("turno 3 do documento", TURNO_3, "negativa_clinica"),
        ("turno 5 do documento", TURNO_5, "negativa_clinica"),
        ("promessa sem negativa", SO_PROMESSA, "promessa_de_humano"),
    ],
)
def test_os_textos_reais_sao_recusados_na_rota_informativa(rotulo: str, texto: str, grupo: str) -> None:
    achado = motivo_de_recusa(texto, "inform")

    assert achado is not None, f"{rotulo} passou pela cerca"
    assert achado[0] == grupo


def test_texto_conforme_passa() -> None:
    """A outra metade: uma cerca que reprova tudo nao deixa escrever resposta nenhuma."""
    assert motivo_de_recusa(CONFORME, "inform") is None


def test_a_promessa_de_humano_e_permitida_na_rota_que_de_fato_escala() -> None:
    """O falso positivo que quebraria o caminho CERTO.

    Em `escalate` um humano foi mesmo acionado e o proprio prompt MANDA prometer. Uma cerca
    incondicional reprovaria exatamente a rota que funciona.
    """
    assert motivo_de_recusa(SO_PROMESSA, "escalate") is None
    assert motivo_de_recusa(SO_PROMESSA, "schedule") is None


def test_a_negativa_clinica_e_recusada_mesmo_na_rota_que_escala() -> None:
    """A negativa nao tem rota permitida: afirmar que a pessoa nao tem alerta e' parecer clinico
    em qualquer caminho, inclusive naquele em que um humano ja foi acionado."""
    achado = motivo_de_recusa(TURNO_3, "escalate")

    assert achado is not None
    assert achado[0] == "negativa_clinica"


def test_a_constante_de_handoff_nao_viola_a_propria_cerca() -> None:
    """Se o texto de contingencia violasse a lista, a recusa cairia num laco ou enviaria o que
    acabou de barrar. Vale nas duas rotas em que ela pode ser usada."""
    assert motivo_de_recusa(RESPOSTA_HANDOFF_RECUSADA, "escalate") is None
    assert motivo_de_recusa(RESPOSTA_HANDOFF_RECUSADA, "inform") is None


# --- O caminho no grafo ------------------------------------------------------------------------


async def test_inform_com_rascunho_recusado_vira_escalonamento_de_verdade() -> None:
    """O defeito 1 do documento, fechado: a Helena nao envia a promessa — ela a CUMPRE.

    Cinco turnos prometeram humano e abriram zero processos. Aqui o rascunho que prometia e'
    barrado, e o turno abre SP-OP-ESCALATION-001 em vez de responder.
    """
    cibseven = FakeCibSevenTransport()
    graph = _graph(TURNO_3, cibseven=cibseven)

    saida = await graph.inform(_estado())

    assert saida["response_kind"] == "escalate"
    assert saida["escalation_started"] is True
    assert saida["escalation_motivo"] == "falha_tecnica"
    assert saida["error"] == ERRO_RESPOSTA_RECUSADA
    # O texto barrado nao sobrevive em lugar nenhum da saida do turno.
    assert "não há sinais de alerta" not in str(saida)


async def test_o_texto_enviado_apos_a_recusa_e_a_constante_segura() -> None:
    """`_start_escalation` redige pelo mesmo `_respond_llm`; com a inferencia devolvendo sempre o
    texto proibido, ATE o rascunho de `escalate` e' barrado — e a saida vira a constante."""
    graph = _graph(TURNO_3)

    saida = await graph.inform(_estado())

    assert saida["response_text"] == RESPOSTA_HANDOFF_RECUSADA


async def test_o_erro_nao_carrega_o_texto_nem_o_padrao() -> None:
    """LUC-06/NEW-01: o campo de estado leva TOKEN DE CLASSE. O padrao exato vive no log e no
    contador, porque este `error` sobrevive para o sufixo do handoff no turno seguinte."""
    graph = _graph(TURNO_5)

    saida = await graph.inform(_estado())

    assert saida["error"] == ERRO_RESPOSTA_RECUSADA
    assert "sinais de alerta" not in saida["error"]
    assert "negativa_clinica" not in saida["error"]


async def test_rascunho_conforme_segue_pela_rota_informativa_sem_abrir_nada() -> None:
    """Nao-vacuidade do caminho: sem a violacao, `inform` continua sendo `inform`."""
    cibseven = FakeCibSevenTransport()
    graph = _graph(CONFORME, cibseven=cibseven)

    saida = await graph.inform(_estado())

    assert saida["response_kind"] == "inform"
    assert saida["response_text"] == CONFORME
    assert "escalation_started" not in saida
