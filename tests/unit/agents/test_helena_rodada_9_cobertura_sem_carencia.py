"""DL-0096 — rodada 9: o assunto anterior (carencia) nao pode vazar para a pergunta de cobertura.

O roteiro de aceite do dono em dev (13/10/2026, imagem 999610b7), passo 3: "Meu plano cobre parto?" logo
depois de "Estou em carencia?" recebeu "No seu cadastro nao consta nenhuma carencia..." e a frase de onde
confirmar, SEM a segmentacao. A causa: com o historico, o modelo devolveu `consulta_plano`/`carencia` para a
pergunta ("parto" e' tema de carencia), e o grafo confiou no subtipo do modelo. O subtipo de uma pergunta de
cobertura agora e' decidido pelo TEXTO.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    MODELO_SEGMENTACAO_DO_CADASTRO,
    RESPOSTA_COBERTURA_ESPECIFICA,
)
from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _grafo
from tests.unit.agents.test_helena_rodada_8_teste_real import SEGMENTACAO, SEM_CARENCIAS, _Fonte

#: O historico do roteiro: tipo do plano (passo 1) e carencia (passo 2), ambos com dados do plano.
HISTORICO_DO_ROTEIRO = [MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO, MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO]
SO_A_COBERTURA = (
    f"{MODELO_SEGMENTACAO_DO_CADASTRO.format(segmentacao=SEGMENTACAO)} {RESPOSTA_COBERTURA_ESPECIFICA}"
)


@pytest.mark.parametrize("subtipo_do_modelo", ["carencia", "carteirinha", "autorizacao", "elegibilidade"])
async def test_cobre_parto_depois_da_carencia_responde_so_a_cobertura(subtipo_do_modelo: str) -> None:
    fonte = _Fonte({"carencia": SEM_CARENCIAS})
    classificacao = _classify(intent="consulta_plano", consulta_subtipo=subtipo_do_modelo)
    grafo, whatsapp, _, cib = _grafo([classificacao], fonte=cast(Any, fonte))

    resultado = await grafo.ainvoke(_entrada("Meu plano cobre parto?", HISTORICO_DO_ROTEIRO))

    assert resultado["response_kind"] == "inform"
    assert fonte.chamadas == ["elegibilidade"], "a carencia nao e' consultada de novo"
    assert whatsapp.enviados == [SO_A_COBERTURA]
    assert "carência" not in whatsapp.enviados[0]
    assert "Obstetrícia" in whatsapp.enviados[0]
    assert sum(len(v) for v in cib._history.values()) == 0


async def test_o_mesmo_roteiro_com_o_modelo_dizendo_information_da_a_mesma_resposta() -> None:
    fonte = _Fonte({"carencia": SEM_CARENCIAS})
    grafo, whatsapp, _, _ = _grafo([_classify(intent="information")], fonte=cast(Any, fonte))

    await grafo.ainvoke(_entrada("Meu plano cobre parto?", HISTORICO_DO_ROTEIRO))

    assert fonte.chamadas == ["elegibilidade"]
    assert whatsapp.enviados == [SO_A_COBERTURA]


async def test_quando_a_mensagem_pede_a_carencia_junto_da_cobertura_a_carencia_continua() -> None:
    fonte = _Fonte({"carencia": SEM_CARENCIAS})
    classificacao = _classify(intent="consulta_plano", consulta_subtipo="carencia")
    grafo, whatsapp, _, _ = _grafo([classificacao], fonte=cast(Any, fonte))

    await grafo.ainvoke(_entrada("Meu plano cobre parto e tenho carência?", HISTORICO_DO_ROTEIRO))

    assert fonte.chamadas == ["carencia"]
    assert whatsapp.enviados[0].startswith("No seu cadastro não consta nenhuma carência")
    assert RESPOSTA_COBERTURA_ESPECIFICA in whatsapp.enviados[0]


def test_o_roteiro_inteiro_na_ordem_do_dono_so_muda_o_assunto_quando_a_pessoa_muda() -> None:
    """O contrato do subtipo, sem grafo: o texto decide."""
    from maezo.agents.helena.graph import _pergunta_de_cobertura, _pergunta_de_outro_fato_do_plano

    assert _pergunta_de_cobertura("Meu plano cobre parto?")
    assert not _pergunta_de_outro_fato_do_plano("Meu plano cobre parto?")
    assert _pergunta_de_outro_fato_do_plano("Meu plano cobre parto e tenho carência?")
