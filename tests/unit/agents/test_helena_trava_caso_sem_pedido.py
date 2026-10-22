"""TRAVA DE SAÍDA `caso_so_com_sim` (ADR-0066; regra do dono): pedido de pessoa só abre caso com "sim" a uma
oferta ou pedido EXPLÍCITO de pessoa. Mensagens sintéticas."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _starts


async def _depois_da_oferta(mensagem: str, respostas: list[str] | None = None) -> tuple[Any, Any, Any]:
    grafo, whatsapp, cib, _fonte = _grafo_e_estado({}, respostas or [])
    estado = _entrada(mensagem) | {"coleta_pendente": ca.PEND_EQUIPE, "coleta_contexto": ""}
    resultado = await grafo.ainvoke(estado)
    return resultado, whatsapp, cib


@pytest.mark.parametrize("mensagem", ["beleza, valeu", "ok, obrigada", "blz"])
async def test_cortesia_depois_da_oferta_nao_abre_caso(mensagem: str) -> None:
    _resultado, whatsapp, cib = await _depois_da_oferta(mensagem)
    assert _starts(cib) == 0, "cortesia nao e' sim: nenhum caso"
    assert whatsapp.enviados[-1] in (ca.TEXTO_DE_NADA, ca.TEXTO_COMBINADO)


@pytest.mark.parametrize("mensagem", ["sim", "sim, pode passar", "quero sim"])
async def test_sim_a_oferta_continua_abrindo_o_caso(mensagem: str) -> None:
    _resultado, _whatsapp, cib = await _depois_da_oferta(mensagem)
    assert _starts(cib) == 1


async def test_pedido_explicito_continua_abrindo_o_caso() -> None:
    grafo, _whatsapp, cib, _fonte = _grafo_e_estado({}, [_classify(intent="human_request")])
    await grafo.ainvoke(_entrada("quero falar com um atendente"))
    assert _starts(cib) == 1


async def test_modelo_que_le_pedido_de_pessoa_em_cortesia_nao_abre_caso() -> None:
    grafo, whatsapp, cib, _fonte = _grafo_e_estado({}, [_classify(intent="human_request")])
    await grafo.ainvoke(_entrada("valeu, era isso"))
    assert _starts(cib) == 0 and whatsapp.enviados[-1].startswith("Por nada!")


async def test_com_caso_aberto_a_pergunta_do_prazo_responde_pelo_caso_aberto() -> None:
    """dono_v1, 22/10/2026: "Quanto tempo demora?" depois do pedido de atendente nao vira cortesia: o caso ja'
    existe, a trava nao abre caso novo e o start reaproveita o aberto (texto do prazo)."""
    from tests.unit.agents.test_helena_rodada_3_simulador import _caso_aberto, _grafo

    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _modelo, _cib = _grafo([_classify(intent="human_request")], cib=cib)
    await grafo.ainvoke(_entrada("Quanto tempo demora?"))
    assert whatsapp.enviados and whatsapp.enviados[-1] not in (ca.TEXTO_DE_NADA, ca.TEXTO_COMBINADO)
    assert _starts(cib) == antes, "nenhum caso novo"
