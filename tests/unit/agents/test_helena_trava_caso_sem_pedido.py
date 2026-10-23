"""TRAVA DE SAÍDA `caso_so_com_sim` (ADR-0066; regra do dono): pedido de pessoa só abre caso com "sim" a uma
oferta ou pedido EXPLÍCITO de pessoa. Mensagens sintéticas.

Desde a retirada da cobertura (DL-0154) a oferta da equipe aqui e' a de uma resposta comum da Helena, e o
classificador le' o pedido de pessoa (`human_request`) no contexto."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.habilidades.conversa import leitura_curta as ca
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _grafo, _starts

_OFERTA = "Se quiser, passo para a nossa equipe ver o seu caso. Quer?"


async def _depois_da_oferta(mensagem: str) -> tuple[Any, Any, Any]:
    # O modelo: a classificacao e, se o turno for redigido, o rascunho de cortesia.
    grafo, whatsapp, _modelo, cib = _grafo([_classify(intent="human_request"), "Combinado!"])
    resultado = await grafo.ainvoke(_entrada(mensagem, falas_da_helena=[_OFERTA]))
    return resultado, whatsapp, cib


@pytest.mark.parametrize("mensagem", ["beleza, valeu", "ok, obrigada", "blz"])
async def test_cortesia_depois_da_oferta_nao_abre_caso(mensagem: str) -> None:
    _resultado, whatsapp, cib = await _depois_da_oferta(mensagem)
    assert _starts(cib) == 0, "cortesia nao e' sim: nenhum caso"
    assert whatsapp.enviados and "192" not in whatsapp.enviados[-1]


# O "sim" a oferta da equipe lido NO CONTEXTO (inclusive "pode ser", "pode passar pra equipe sim") e' do
# roteador v7 (etapa 7 do Plano de ajustes v2): a unica oferta com leitor proprio de "sim" era a da cobertura,
# que saiu (DL-0154).


async def test_pedido_explicito_continua_abrindo_o_caso() -> None:
    grafo, _whatsapp, _modelo, cib = _grafo([_classify(intent="human_request")])
    await grafo.ainvoke(_entrada("quero falar com um atendente"))
    assert _starts(cib) == 1


async def test_modelo_que_le_pedido_de_pessoa_em_cortesia_nao_abre_caso() -> None:
    grafo, whatsapp, _modelo, cib = _grafo([_classify(intent="human_request")])
    await grafo.ainvoke(_entrada("valeu, era isso"))
    assert _starts(cib) == 0 and whatsapp.enviados[-1].startswith("Por nada!")


async def test_com_caso_aberto_a_pergunta_do_prazo_responde_pelo_caso_aberto() -> None:
    """dono_v1, 22/10/2026: "Quanto tempo demora?" depois do pedido de atendente nao vira cortesia: o caso ja'
    existe, a trava nao abre caso novo e o start reaproveita o aberto (texto do prazo)."""
    from tests.unit.agents.test_helena_rodada_3_simulador import _caso_aberto

    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _modelo, _cib = _grafo([_classify(intent="human_request")], cib=cib)
    await grafo.ainvoke(_entrada("Quanto tempo demora?"))
    assert whatsapp.enviados and whatsapp.enviados[-1] not in (ca.TEXTO_DE_NADA, ca.TEXTO_COMBINADO)
    assert _starts(cib) == antes, "nenhum caso novo"
