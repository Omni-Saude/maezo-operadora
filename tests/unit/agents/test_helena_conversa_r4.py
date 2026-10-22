"""Conversa simples e textos de regra propria (avaliacao r4 de 22/10/2026): cumprimento nao e' despedida, o
polegar vale no contexto, e o texto de regra propria nunca sai cortado no meio.

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _grafo, _Modelo


@pytest.mark.parametrize("fala", ["bom dia", "Boa tarde! tudo bem?", "boa noite", "Bom dia, Helena"])
async def test_cumprimento_recebe_cumprimento_nunca_despedida(fala: str) -> None:
    modelo = _Modelo([_decisao("conversa")], [])
    grafo, whatsapp, cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala))
    assert whatsapp.enviados[-1] == ca.TEXTO_SAUDACAO and _starts(cib) == 0


@pytest.mark.parametrize("fala", ["boa noite, obrigada", "bom dia, valeu", "boa tarde, tchau"])
def test_cumprimento_com_agradecimento_ou_despedida_continua_cortesia(fala: str) -> None:
    assert ca.resposta_de_cortesia(fala) in (ca.TEXTO_DE_NADA, ca.TEXTO_COMBINADO)


@pytest.mark.parametrize("polegar", ["👍", "👍🏻", "👌", "✅"])
async def test_polegar_sozinho_e_cortesia_sem_caso(polegar: str) -> None:
    modelo = _Modelo([_decisao("conversa")], [])
    grafo, whatsapp, cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(polegar))
    assert whatsapp.enviados[-1] in (ca.TEXTO_COMBINADO, ca.TEXTO_DE_NADA) and _starts(cib) == 0


@pytest.mark.parametrize("polegar", ["👍", "👍🏽", "👌"])
async def test_polegar_a_oferta_da_equipe_e_sim(polegar: str) -> None:
    modelo = _Modelo([_decisao("cobertura", responde=True)], [])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    contexto = ca.Contexto(termos=["qwxzk"], quem="titular", tentativas=2).codificar()
    await grafo.ainvoke(_entrada(polegar) | {"coleta_pendente": ca.PEND_EQUIPE, "coleta_contexto": contexto})
    assert _starts(cib) == 1


@pytest.mark.parametrize("tipo", ["estetica", "inseminacao", "experimental", "remedio", "material"])
def test_linha_de_regra_propria_nunca_corta_no_meio(tipo: str) -> None:
    linha = ca.frase_de_outro_item("Item", tipo)
    frase = linha.removeprefix("Item: ")
    assert frase.endswith(".") and not frase.endswith(" art.") and "(" not in frase.split(")")[-1]
    assert frase.count("(") == frase.count(")"), "parentese aberto = frase cortada no meio"
