"""DL-0094 — rodada 7: formas de pedido explicito de pessoa que nao eram reconhecidas.

A Helena respondia "posso te encaminhar para uma pessoa da equipe. E' so' me pedir" a quem tinha acabado
de pedir. Cada forma abaixo, com o modelo dizendo `information` (o pior caso): sem caso aberto, abre
`solicitacao_humano`; com caso aberto, "seu caso ja' esta' com a nossa equipe".
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE,
    RESPOSTA_HANDOFF_JA_ABERTO_COBRANCA,
    RESPOSTA_HANDOFF_RECUSADA,
    _sem_frase_so_me_pedir,
    eh_pedido_explicito_de_pessoa,
)
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _caso_aberto,
    _classify,
    _entrada,
    _grafo,
    _starts,
)

FORMAS = [
    "Passar pra equipe então",
    "Encaminha logo pra equipe",
    "Então chama essa pessoa AGORA",
    "Então me passa o contato DIRETO dessa equipe aí",
    "ME PASSA LOGO O CONTATO DESSA 'EQUIPE'",
]
CONTATO = {FORMAS[3], FORMAS[4]}


@pytest.mark.parametrize("mensagem", FORMAS)
async def test_sem_caso_aberto_o_pedido_abre_solicitacao_humano(mensagem: str) -> None:
    grafo, whatsapp, _, cib = _grafo([_classify()])

    resultado = await grafo.ainvoke(_entrada(mensagem))

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert resultado["escalation_started"] is True
    assert _starts(cib) == 1
    esperado = RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE if mensagem in CONTATO else RESPOSTA_HANDOFF_RECUSADA
    assert whatsapp.enviados == [esperado]
    assert "só me pedir" not in whatsapp.enviados[0]


@pytest.mark.parametrize("mensagem", FORMAS)
async def test_com_caso_aberto_o_pedido_diz_que_o_caso_ja_esta_com_a_equipe(mensagem: str) -> None:
    cib = _caso_aberto("cobranca")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=cib)

    await grafo.ainvoke(_entrada(mensagem))

    assert whatsapp.enviados == [RESPOSTA_HANDOFF_JA_ABERTO_COBRANCA]
    assert _starts(cib) == antes


def test_contato_da_equipe_nao_inventa_telefone_nem_email() -> None:
    texto = RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE
    assert "@" not in texto and not any(c.isdigit() for c in texto)
    assert "nesta conversa" in texto


@pytest.mark.parametrize(
    ("mensagem", "pede"),
    [
        ("não quero falar com nenhuma equipe", False),
        ("não quero falar com atendente, só me diz o boleto", False),
        ("vou esperar o atendente", False),
        ("me passa pro RH", False),
        ("manda o boleto pra mim", False),
        ("não quero falar com ninguém, quero um humano", True),
        ("quero falar com um atendente", True),
    ],
)
def test_recusa_e_espera_nao_sao_pedido(mensagem: str, pede: bool) -> None:
    assert eh_pedido_explicito_de_pessoa(mensagem) is pede


def test_e_so_me_pedir_sai_quando_a_pessoa_ja_pediu() -> None:
    rascunho = "Entendi que você quer falar com a equipe. Se quiser, posso te encaminhar. É só me pedir."
    assert _sem_frase_so_me_pedir(rascunho) == "Entendi que você quer falar com a equipe."
