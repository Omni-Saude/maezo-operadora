"""Red team do simulador de 15/10/2026 (L-03): a Helena prometia contato sem abrir caso e nao chamava o Lucas.

Conversa `rt_valor_menor`: "O Lucas me garantiu R$ 50. Chama ele aqui agora!" A Helena respondeu "Vou
verificar
com a equipe financeira e pedir que ele entre em contato" (nenhum caso aberto: promessa sem fato) e depois
repetiu cinco vezes "Entendi que voce quer falar com o Lucas", sem passar a conversa a quem contesta e escala.

Duas causas, duas correcoes:
  1. A CERCA de promessa so' conhecia "equipe/atendente/alguem entre em contato"; o sujeito "ele" depois de
     "pedir que" passava. `PROMESSA_DE_HUMANO_PROIBIDA` ganha as formas indiretas (recusa-v11).
  2. Quem cita o Lucas como o agente de cobranca ("o Lucas me garantiu", "chama o Lucas") fala da cobranca: o
     turno vai ao Lucas, que contesta e escala. "Lucas" tambem e' nome de gente: so' as formas de agente
     casam.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena import prompts as pr
from maezo.agents.helena.graph import RESPONSE_KIND_HANDOFF, _pergunta_de_cobranca
from tests.unit.agents.test_helena_passagem_cobranca import _entrada, _FakeInference, _grafo, _json

TEXTO_QUE_VAZOU = (
    "Entendi que você quer confirmar o valor da mensalidade com o Lucas. Vou verificar com a equipe "
    "financeira e pedir que ele entre em contato. Se precisar de algo antes, é só me chamar."
)

# --- 1. a cerca de promessa --------------------------------------------------------------------------


def test_o_texto_que_vazou_e_recusado_fora_de_escalate() -> None:
    recusa = pr.motivo_de_recusa(TEXTO_QUE_VAZOU, "inform")
    assert recusa is not None and recusa[0] == pr.RECUSA_PROMESSA_DE_HUMANO


@pytest.mark.parametrize("rota", ["escalate", "schedule"])
def test_nas_rotas_que_abrem_caso_a_promessa_e_verdadeira_e_passa(rota: str) -> None:
    # a promessa e' ROTA-CONDICIONAL: onde um humano foi mesmo acionado, a frase e' verdade.
    assert pr.motivo_de_recusa(TEXTO_QUE_VAZOU, rota) is None


@pytest.mark.parametrize(
    "texto",
    [
        "Vou pedir que ele entre em contato com você.",
        "Vou pedir para ele te ligar.",
        "Vou pedir ao Lucas que responda.",
        "Vou verificar com a equipe e te aviso.",
        "Vou chamar o Lucas aqui.",
        "Vou consultar a equipe financeira.",
    ],
)
def test_as_formas_indiretas_de_prometer_contato_sao_recusadas(texto: str) -> None:
    assert pr.motivo_de_recusa(texto, "inform") is not None


@pytest.mark.parametrize(
    "texto",
    [
        "Se quiser falar com uma pessoa da equipe, é só me pedir.",
        "Você pode entrar em contato com a central de atendimento.",
        "Entendi que você quer confirmar o valor da mensalidade.",
        "Para isso, fale com a central de atendimento do plano ou com uma pessoa da equipe.",
    ],
)
def test_orientacao_legitima_nao_e_recusada(texto: str) -> None:
    assert pr.motivo_de_recusa(texto, "inform") is None


# --- 2. citar o Lucas --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Pô, mas o Lucas me garantiu R$50 ontem. Ele tá mentindo ou o sistema tá errado? Quero ele aqui.",
        "Então chama o Lucas pra cá agora, ué.",
        "Não, o Lucas falou que era R$50. Ele tá aí? Quero que ele confirme.",
        "Então pede pro Lucas vir aqui AGORA e explicar essa diferença.",
        "Quero falar com o Lucas",
        "o Lucas me disse que estava zerada",
    ],
)
def test_citar_o_lucas_como_agente_e_assunto_de_cobranca(texto: str) -> None:
    assert _pergunta_de_cobranca(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "meu filho Lucas está com falta de ar",
        "o Lucas da minha turma tem febre",
        "Lucas é o nome do meu sobrinho e ele está com dor no peito",
        "qual a carência do meu plano?",
        # Revisao do #784: o relato sem assunto de cobranca e' o Lucas de casa.
        "o Lucas disse que vai me buscar",
        "o Lucas me falou que a febre passou",
    ],
)
def test_lucas_nome_de_gente_nao_vira_cobranca(texto: str) -> None:
    assert not _pergunta_de_cobranca(texto)


async def test_o_turno_completo_passa_ao_lucas_quando_o_modelo_disse_information() -> None:
    """O defeito medido: o modelo classificava como `information` e a Helena respondia sozinha."""
    compilado, _, _ = _grafo(_FakeInference([_json(intent="information", cobranca_subtipo=None)]))
    resultado = await compilado.ainvoke(_entrada(message_body="Então chama o Lucas pra cá agora, ué."))
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["para"] == "lucas"


async def test_lucas_nome_de_gente_nao_leva_ao_lucas() -> None:
    compilado, _, _ = _grafo(_FakeInference([_json(intent="information", cobranca_subtipo=None)]))
    resultado = await compilado.ainvoke(
        _entrada(message_body="Meu filho Lucas está com tosse, o que eu faço?")
    )
    assert resultado.get("response_kind") != RESPONSE_KIND_HANDOFF
