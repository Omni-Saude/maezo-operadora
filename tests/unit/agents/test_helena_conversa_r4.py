"""Conversa simples (avaliacao r4 de 22/10/2026): cumprimento nao e' despedida e o polegar vale no contexto.

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador). Os textos de regra propria
da cobertura sairam com a cobertura (DL-0154)."""

from __future__ import annotations

import pytest

from maezo.agents.helena.habilidades.conversa import leitura_curta as ca
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


@pytest.mark.parametrize(
    ("texto", "curta"),
    [
        ("1", True),
        ("sim", True),
        ("ok.", True),
        ("Ana", True),
        ("eu ja disse que e pra mim", True),
        ("Quem são meus dependentes?", False),
        ("quero saber da minha carência", False),
        ("qual o telefone da central?", False),
    ],
)
def test_resposta_curta(texto: str, curta: bool) -> None:
    assert ca.resposta_curta(texto) is curta


def test_quero_alguma_coisa_nao_e_sim() -> None:
    assert ca.ler_resposta("quero saber da minha carencia") is None
    assert ca.ler_resposta("quero sim, por favor") == "sim"
