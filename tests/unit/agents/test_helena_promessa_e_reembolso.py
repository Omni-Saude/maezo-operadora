"""Avaliacao r5b de 25/10/2026 — promessa de encaminhar sem caso (B) e reembolso confirmado (C).

B. Depois de uma resposta do MODELO que oferecia a equipe ("Se quiser, eu te encaminho para um atendente"), o
   "quero, pode passar" recebeu outra redacao do modelo prometendo o encaminhamento, e nenhum caso abriu (o
   roteador caiu por tempo; o caminho de sempre leu o "sim" sem pedido explicito). Agora a oferta redigida
   pelo modelo conta como oferta pendente (o "sim" abre o caso) e a cerca TEXTO x FATO reconhece "vou te
   encaminhar" e "a equipe vai te responder" como promessa: sem caso no turno, sai o texto honesto.
C. "Da' pra pedir reembolso?" recebeu "Sim, e' possivel pedir reembolso" (redacao do modelo). Agora: o texto
   fixo do pacote (fora do escopo, canais oficiais) e a trava de saida contra confirmar reembolso.

Frases escritas para estes testes (nenhuma copiada do conjunto da avaliacao)."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena import graph as g
from maezo.agents.helena import travas_saida
from maezo.agents.helena.habilidades.atendimento_humano import oferta as of
from maezo.agents.helena.habilidades.atendimento_humano.textos import RESPOSTA_RECUSA_DE_EQUIPE
from maezo.agents.helena.habilidades.conversa import reembolso as rb
from maezo.agents.helena.prompts import motivo_de_recusa
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_escalar_por_repeticao import _estado, _grafo, _WhatsApp
from tests.unit.agents.test_helena_roteador_v7 import _ConversaAtiva
from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _texto

_OFERTAS_DO_MODELO = (
    "Sim, as regras estão no aplicativo. Se quiser, eu te encaminho para um atendente humano.",
    "Posso te encaminhar para uma pessoa da nossa equipe, se preferir.",
    "Isso fica com a central. Quer que eu te encaminhe para um atendente?",
)


def _com_fala(fala: str, mensagem: str) -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_promessa",
        "message_body": mensagem,
        "historico_conversa": [
            {"papel": "beneficiario", "texto": "uma pergunta", "em": "2026-10-25T19:00:00+00:00"},
            {"papel": "helena", "texto": fala, "em": "2026-10-25T19:00:00+00:00"},
        ],
    }


# =================================================================================================
# B1. A oferta redigida pelo modelo e' oferta pendente; o "sim" abre o caso
# =================================================================================================


@pytest.mark.parametrize("fala", _OFERTAS_DO_MODELO)
def test_oferta_redigida_pelo_modelo_conta_como_oferta_pendente(fala: str) -> None:
    assert of.oferta_pendente(_com_fala(fala, "quero")) is True


@pytest.mark.parametrize(
    "fala",
    [
        "Posso te encaminhar para um atendente. Quer saber também da sua carência?",
        "Você pode falar com um atendente pela central de atendimento do plano.",
        "O reembolso fica com a central. Posso ajudar em mais alguma coisa?",
    ],
)
def test_sem_oferta_ou_com_outra_pergunta_nao_ha_oferta_pendente(fala: str) -> None:
    """Controle negativo: outra pergunta na mesma fala (o "sim" podia ser a ela) ou nenhuma oferta."""
    assert of.oferta_pendente(_com_fala(fala, "sim")) is False


@pytest.mark.parametrize("sim", ["quero, pode passar", "pode sim", "quero sim, pode encaminhar"])
@pytest.mark.parametrize("fala", _OFERTAS_DO_MODELO)
def test_o_sim_a_oferta_do_modelo_abre_o_caso_sem_roteador(fala: str, sim: str) -> None:
    grafo = _grafo(FakeCibSevenTransport(), _WhatsApp())
    update = of.responder(grafo, _com_fala(fala, sim))
    assert update is not None
    assert update["next_kind"] == "escalate"
    assert update["escalation_motivo"] == "solicitacao_humano"


@pytest.mark.parametrize("nao", ["não precisa", "agora não", "não, obrigado"])
def test_o_nao_a_oferta_do_modelo_recebe_a_recusa_curta(nao: str) -> None:
    grafo = _grafo(FakeCibSevenTransport(), _WhatsApp())
    update = of.responder(grafo, _com_fala(_OFERTAS_DO_MODELO[0], nao))
    assert update is not None
    assert update.get("next_kind") != "escalate"
    assert update["response_text"] == RESPOSTA_RECUSA_DE_EQUIPE


@pytest.mark.parametrize("outra", ["ok", "valeu", "e a minha carteirinha?"])
def test_cortesia_ou_pedido_novo_depois_da_oferta_do_modelo_nao_abre_caso(outra: str) -> None:
    grafo = _grafo(FakeCibSevenTransport(), _WhatsApp())
    assert of.responder(grafo, _com_fala(_OFERTAS_DO_MODELO[0], outra)) is None


# =================================================================================================
# B2. A cerca TEXTO x FATO: promessa de encaminhar so' com caso
# =================================================================================================

_PROMESSAS = (
    "Vou te encaminhar para um atendente humano. Aguarde um momento.",
    "Certo, vou te passar para uma pessoa da equipe agora.",
    "Aguarde, que alguém da nossa equipe vai te responder por aqui.",
)


@pytest.mark.parametrize("promessa", _PROMESSAS)
def test_promessa_de_encaminhar_sem_caso_e_recusada_pela_cerca(promessa: str) -> None:
    assert motivo_de_recusa(promessa, "inform", start_aconteceu=False) is not None


@pytest.mark.parametrize(
    "honesto",
    [
        RESPOSTA_RECUSA_DE_EQUIPE,
        "Se quiser, eu te encaminho para um atendente humano.",
        "Você pode falar com a central de atendimento do plano.",
    ],
)
def test_recusa_oferta_e_orientacao_nao_sao_promessa(honesto: str) -> None:
    """Controle negativo: a recusa curta, a OFERTA e a orientacao dos canais passam."""
    assert motivo_de_recusa(honesto, "inform", start_aconteceu=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("promessa", _PROMESSAS)
async def test_promessa_sem_caso_no_turno_sai_como_o_texto_honesto(promessa: str) -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    await _grafo(cib, zap).respond(_estado((), texto=promessa))
    assert zap.enviados == [g.RESPOSTA_SEM_ENCAMINHAMENTO]
    assert sum(cib._generations.values()) == 0


# =================================================================================================
# C. Reembolso: fora do escopo, nunca confirmado
# =================================================================================================

_PERGUNTAS_DE_REEMBOLSO = (
    "se eu pagar a consulta no particular consigo reembolso?",
    "como faço pra pedir o reembolso do exame?",
    "o plano me reembolsa a consulta do psicólogo?",
)


@pytest.mark.parametrize("pergunta", _PERGUNTAS_DE_REEMBOLSO)
def test_pergunta_de_reembolso_e_reconhecida(pergunta: str) -> None:
    assert rb.pergunta_de_reembolso(pergunta) is True


@pytest.mark.parametrize("outra", ["quanto veio a mensalidade?", "o plano cobre fisioterapia?", "bom dia"])
def test_outro_assunto_nao_e_reembolso(outra: str) -> None:
    assert rb.pergunta_de_reembolso(outra) is False


@pytest.mark.parametrize(
    "confirma",
    [
        "Sim, é possível pedir reembolso. As regras estão no aplicativo.",
        "Você tem direito a reembolso dessa consulta.",
        "Você pode solicitar o reembolso pelo portal do plano.",
    ],
)
def test_a_trava_troca_a_confirmacao_de_reembolso(confirma: str) -> None:
    assert rb.confirma_reembolso(confirma) is True
    assert travas_saida.na_saida(confirma) == rb.TEXTO_REEMBOLSO_FORA


@pytest.mark.parametrize(
    "nao_confirma",
    [
        rb.TEXTO_REEMBOLSO_FORA,
        "Não é possível pedir reembolso por aqui; procure a central.",
        "O valor da sua mensalidade está no aplicativo Austa Clínicas.",
    ],
)
def test_texto_que_nao_confirma_reembolso_sai_igual(nao_confirma: str) -> None:
    assert rb.confirma_reembolso(nao_confirma) is False
    assert travas_saida.na_saida(nao_confirma) == nao_confirma


@pytest.mark.parametrize("habilidade", ["conversa", "plano", "cobertura"])
@pytest.mark.parametrize("pergunta", _PERGUNTAS_DE_REEMBOLSO)
async def test_reembolso_recebe_o_texto_fixo_qualquer_que_seja_a_habilidade(
    pergunta: str, habilidade: str
) -> None:
    conversa = _ConversaAtiva()
    resposta = await conversa.fala(pergunta, decisao_v7(habilidade))
    assert _texto(resposta) == rb.TEXTO_REEMBOLSO_FORA
    assert conversa.motivos == []


@pytest.mark.parametrize("pergunta", _PERGUNTAS_DE_REEMBOLSO)
async def test_reembolso_recebe_o_texto_fixo_sem_o_roteador(pergunta: str) -> None:
    conversa = _Conversa()
    resposta = await conversa.diz(pergunta)
    assert _texto(resposta) == rb.TEXTO_REEMBOLSO_FORA


def test_o_texto_do_reembolso_esta_no_pacote() -> None:
    from maezo.agents.helena.textos import PADRAO

    assert PADRAO[rb.CHAVE_REEMBOLSO_FORA] == rb.TEXTO_REEMBOLSO_FORA
