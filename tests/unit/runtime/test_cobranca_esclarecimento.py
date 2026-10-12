"""Pergunta de esclarecimento sobre a resposta anterior do Lucas (teste real do dono, 12/10/2026)."""

from __future__ import annotations

import pytest

from maezo.runtime.cobranca_esclarecimento import e_pergunta_de_esclarecimento


@pytest.mark.parametrize(
    "texto",
    [
        "De qual ano?",
        "Você disse que paguei em 29/07, mas de que ano?",
        "qual o ano?",
        "Qual é o ano?",
        "de que mês?",
        "Como assim?",
        "Não entendi",
        "de quando é esse valor?",
    ],
)
def test_pergunta_sobre_a_resposta_anterior_e_esclarecimento(texto: str) -> None:
    assert e_pergunta_de_esclarecimento(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        "Qual ano? Esse valor está errado",
        "Como assim? Eu não reconheço essa cobrança",
        "Não entendi, me cobraram a mais",
        "de que ano? isso é cobrança indevida",
        "Como assim? Não concordo com o reajuste",
    ],
)
def test_com_discordancia_explicita_nao_e_esclarecimento(texto: str) -> None:
    assert e_pergunta_de_esclarecimento(texto) is False


@pytest.mark.parametrize(
    "texto",
    [
        "Quando paguei a mensalidade de julho?",
        "Quanto foi a coparticipação de maio?",
        "Me cobraram errado",
        "qual a mais recente?",
        "",
        None,
        42,
    ],
)
def test_pergunta_nova_nao_e_esclarecimento(texto: object) -> None:
    assert e_pergunta_de_esclarecimento(texto) is False
