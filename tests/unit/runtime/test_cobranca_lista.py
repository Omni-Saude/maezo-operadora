"""Teste real do dono (12/10/2026): o PEDIDO DE LISTA sai do texto por um lexico pequeno e deterministico."""

from __future__ import annotations

from datetime import date

import pytest

from maezo.runtime.cobranca_lista import (
    CHAVES_DA_LISTA,
    QUANTIDADE_MAXIMA,
    QUANTIDADE_PADRAO,
    PedidoDeLista,
    decidir_lista_da_cobranca,
    lista_do_handoff,
)

_HOJE = date(2026, 10, 12)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        # A pergunta do dono e a insistencia.
        ("Quais foram as últimas três mensalidades pagas por mim?", PedidoDeLista(3, situacao="paga")),
        ("Mas eu te perguntei das últimas três", PedidoDeLista(3)),
        # Numerais e digitos, nas duas ordens.
        ("as duas últimas", PedidoDeLista(2)),
        ("as 4 últimas mensalidades", PedidoDeLista(4)),
        ("últimos 6 pagamentos", PedidoDeLista(6, situacao="paga")),
        ("coparticipações dos últimos cinco meses", PedidoDeLista(5)),
        ("quanto paguei nos últimos seis meses?", PedidoDeLista(6, situacao="paga")),
        # Sem numero: o padrao.
        ("e nos últimos meses?", PedidoDeLista(QUANTIDADE_PADRAO)),
        ("meu histórico de pagamentos", PedidoDeLista(QUANTIDADE_PADRAO, situacao="paga")),
        # Teto.
        ("os últimos 20 pagamentos", PedidoDeLista(QUANTIDADE_MAXIMA, situacao="paga")),
        # Periodo: intervalo (ano pela regra de `competencia.py`) e ano inteiro.
        ("de julho a setembro", PedidoDeLista(3, de="2026-07", ate="2026-09")),
        ("entre novembro e fevereiro", PedidoDeLista(4, de="2025-11", ate="2026-02")),
        ("de julho de 2025 até março de 2026", PedidoDeLista(9, de="2025-07", ate="2026-03")),
        ("e as de 2026?", PedidoDeLista(12, de="2026-01", ate="2026-12")),
        ("mensalidades pagas de 2025", PedidoDeLista(12, de="2025-01", ate="2025-12", situacao="paga")),
        ("as últimas 3 de 2025", PedidoDeLista(3, de="2025-01", ate="2025-12")),
        # Todas em aberto.
        ("quais mensalidades estão em aberto?", PedidoDeLista(QUANTIDADE_MAXIMA, situacao="em_aberto")),
        ("tenho boletos pendentes?", PedidoDeLista(QUANTIDADE_MAXIMA, situacao="em_aberto")),
    ],
)
def test_pedido_de_lista(texto: str, esperado: PedidoDeLista) -> None:
    assert decidir_lista_da_cobranca(texto, _HOJE) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Qual o valor da minha última mensalidade?",
        "qual a última mensalidade paga?",
        "quanto foi a mensalidade de julho?",
        "a mensalidade de julho de 2025",
        "quanto paguei em 2025?",
        "o que está em aberto?",
        "De qual ano?",
        "pagamentos dos últimos 30 dias",
        "nos últimos 2 anos",
        "quando vence?",
        "de julho a julho",
    ],
)
def test_pergunta_de_um_mes_nao_e_lista(texto: str) -> None:
    assert decidir_lista_da_cobranca(texto, _HOJE) is None


def test_texto_que_nao_e_str_nao_e_lista() -> None:
    assert decidir_lista_da_cobranca(None, _HOJE) is None
    assert decidir_lista_da_cobranca(["ultimas tres"], _HOJE) is None


def test_ida_e_volta_pelo_handoff() -> None:
    pedido = PedidoDeLista(3, de="2026-07", ate="2026-09", situacao="paga")
    assert frozenset(pedido.como_handoff()) == CHAVES_DA_LISTA
    assert lista_do_handoff(pedido.como_handoff()) == pedido
    assert lista_do_handoff(None) is None


@pytest.mark.parametrize(
    "valor",
    [
        {"quantidade": 3},
        {"quantidade": 3, "de": None, "ate": None, "situacao": None, "texto": "x"},
        {"quantidade": 1, "de": None, "ate": None, "situacao": None},
        {"quantidade": 13, "de": None, "ate": None, "situacao": None},
        {"quantidade": True, "de": None, "ate": None, "situacao": None},
        {"quantidade": "3", "de": None, "ate": None, "situacao": None},
        {"quantidade": 3, "de": "2026-13", "ate": None, "situacao": None},
        {"quantidade": 3, "de": "2026-09", "ate": "2026-07", "situacao": None},
        {"quantidade": 3, "de": None, "ate": None, "situacao": "vencida"},
        "ultimas tres",
    ],
)
def test_forma_errada_e_recusada(valor: object) -> None:
    with pytest.raises(ValueError):
        lista_do_handoff(valor)
