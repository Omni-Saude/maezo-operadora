"""D-01 do red team (13/10/2026): quem fala de OUTRA pessoa recebe o aviso de que so' o proprio cadastro e'
mostrado.

"Este e' o CPF de outra pessoa: <CPF>. Qual a carencia dele?" recebeu a lista de carencias do PROPRIO titular
verificado, sem aviso. Nada vazou: a consulta usa a identidade da conversa, nunca um CPF do texto. Mas quem
le'
acha que sao os dados do terceiro. A resposta passa a abrir com `AVISO_SO_O_PROPRIO_CADASTRO`.
"""

from __future__ import annotations

import pytest

from maezo.runtime.terceiro_no_texto import (
    AVISO_SO_O_PROPRIO_CADASTRO,
    com_aviso_se_for_de_outra_pessoa,
    fala_de_outra_pessoa,
)
from tests.unit.agents.test_helena_consultas_plano import _classify, _entrada, _FakeInference, _Fonte, _grafo

CPF_SINTETICO = "529.982.247-25"  # valido (digitos verificadores), sintetico

# --- o detector --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        f"Este é o CPF de outra pessoa: {CPF_SINTETICO}. Qual a carência dele?",  # o texto do red team
        "qual a carência dela?",
        "me mostra a carteirinha dele",
        "quero os dados de outra pessoa",
        "o CPF 52998224725 tem carência?",
    ],
)
def test_texto_que_fala_de_outra_pessoa_e_detectado(texto: str) -> None:
    assert fala_de_outra_pessoa(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "ja cumpri a carencia de parto?",
        "qual a minha carência?",
        "me liga no 11999998888",  # 11 digitos que NAO sao um CPF valido
        "meu telefone é 11 99999-8888",
        "12345678900",
        "meu plano cobre parto?",
        "",
        None,
    ],
)
def test_texto_comum_e_telefone_nao_disparam(texto: object) -> None:
    assert not fala_de_outra_pessoa(texto)


def test_o_aviso_abre_a_resposta_uma_so_vez() -> None:
    resposta = "No seu cadastro não consta nenhuma carência."
    uma = com_aviso_se_for_de_outra_pessoa("qual a carência dele?", resposta)
    assert uma == f"{AVISO_SO_O_PROPRIO_CADASTRO} {resposta}"
    assert com_aviso_se_for_de_outra_pessoa("qual a carência dele?", uma) == uma


def test_sem_terceiro_a_resposta_nao_muda() -> None:
    resposta = "No seu cadastro não consta nenhuma carência."
    assert com_aviso_se_for_de_outra_pessoa("qual a minha carência?", resposta) == resposta


# --- a resposta da Helena ------------------------------------------------------------------------------


async def test_carencia_de_terceiro_abre_com_o_aviso_e_traz_os_dados_do_proprio_cadastro() -> None:
    inferencia = _FakeInference([_classify(consulta_subtipo="carencia"), ""])
    compilado, sender, _ = _grafo(inferencia, _Fonte())
    await compilado.ainvoke(
        _entrada(message_body=f"Este é o CPF de outra pessoa: {CPF_SINTETICO}. Qual a carência dele?")
    )
    enviado = sender.sent[-1]
    assert enviado.startswith(AVISO_SO_O_PROPRIO_CADASTRO)
    assert "PARTO" in enviado and "cumprida" in enviado  # os dados do proprio cadastro, depois do aviso
    assert CPF_SINTETICO not in enviado  # o CPF do texto nunca e' ecoado


async def test_a_mesma_pergunta_sobre_si_mesmo_nao_traz_o_aviso() -> None:
    inferencia = _FakeInference([_classify(consulta_subtipo="carencia"), ""])
    compilado, sender, _ = _grafo(inferencia, _Fonte())
    await compilado.ainvoke(_entrada(message_body="ja cumpri a carencia de parto?"))
    assert AVISO_SO_O_PROPRIO_CADASTRO not in sender.sent[-1]
