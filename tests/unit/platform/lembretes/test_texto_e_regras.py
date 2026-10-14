"""Texto fixo do lembrete (guia de tom) e regras puras de selecao e horario."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

import pytest

from maezo.platform.lembretes.regras import (
    MOTIVO_COBRANCA_DA_ADMINISTRADORA,
    MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA,
    MOTIVO_SEM_COMPETENCIA_NA_JANELA,
    dentro_do_horario,
    escolher_competencia,
)
from maezo.platform.lembretes.texto import (
    CONFIRMACAO_PARAR,
    CORPO_TEMPLATE_META,
    compor_lembrete,
    compor_teste,
    mes_e_ano,
)
from tests.support.lembrete_fakes import competencia, fatos

HOJE = date(2026, 10, 13)

_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


def test_texto_exato() -> None:
    m = compor_lembrete(competencia="2026-10", valor_total="320.25", vencimento=date(2026, 10, 16))
    assert m.texto == (
        "Sua mensalidade de outubro de 2026 (R$ 320,25) vence em 16/10. "
        "Para a 2ª via, acesse o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento. "
        "Para não receber lembretes, responda PARAR."
    )
    assert m.parametros_template == ("outubro de 2026", "320,25", "16/10")


def test_corpo_do_template_renderizado_e_igual_ao_texto_livre() -> None:
    m = compor_lembrete(competencia="2026-03", valor_total="1000.00", vencimento=date(2026, 3, 5))
    renderizado = CORPO_TEMPLATE_META
    for i, v in enumerate(m.parametros_template, start=1):
        renderizado = renderizado.replace(f"{{{{{i}}}}}", v)
    assert renderizado == m.texto


def test_mes_sempre_com_ano() -> None:
    assert mes_e_ano("2026-01") == "janeiro de 2026"
    assert mes_e_ano("2027-12") == "dezembro de 2027"


def test_tom_frases_curtas_sem_emoji_e_sem_nome_de_agente() -> None:
    texto = compor_lembrete(competencia="2026-10", valor_total="1.00", vencimento=HOJE).texto
    for frase in re.split(r"(?<=[.!?])\s+", texto):
        assert len(frase.split()) <= 20, frase
    assert len(_EMOJI.findall(texto)) <= 1
    for nome in ("Lucas", "Helena", "Fernando", "Maezo"):
        assert nome not in texto
    assert len(CONFIRMACAO_PARAR.split()) <= 20
    assert CONFIRMACAO_PARAR == "Você não receberá mais lembretes."


def test_texto_de_teste_e_claramente_marcado() -> None:
    m = compor_teste(competencia="2026-10", valor_total="123.45", vencimento=date(2026, 10, 16))
    assert m.texto.startswith("[TESTE] Sua mensalidade de outubro de 2026 (R$ 123,45)")
    assert m.parametros_template[0] == "[TESTE] outubro de 2026"


@pytest.mark.parametrize(
    ("pagador", "administradora", "motivo"),
    [
        ("pessoa_juridica", False, MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA),
        ("desconhecido", False, MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA),
        (None, False, MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA),
        ("pessoa_fisica", True, MOTIVO_COBRANCA_DA_ADMINISTRADORA),
        ("desconhecido", True, MOTIVO_COBRANCA_DA_ADMINISTRADORA),
    ],
)
def test_so_pessoa_fisica_recebe(pagador: str | None, administradora: bool, motivo: str) -> None:
    escolhida, m = escolher_competencia(
        fatos(competencia(), pagador=pagador, administradora=administradora), hoje=HOJE, dias_antecedencia=3
    )
    assert escolhida is None and m == motivo


def test_pessoa_fisica_com_competencia_em_aberto_no_prazo() -> None:
    escolhida, motivo = escolher_competencia(fatos(competencia()), hoje=HOJE, dias_antecedencia=3)
    assert motivo is None and escolhida is not None
    assert (escolhida.competencia, escolhida.valor_total, escolhida.vencimento) == (
        "2026-10",
        "320.25",
        date(2026, 10, 16),
    )


@pytest.mark.parametrize(
    ("kwargs", "dias", "enviaria"),
    [
        ({"vencimento": "2026-10-13"}, 3, True),  # vence hoje
        ({"vencimento": "2026-10-16"}, 3, True),  # no limite de N dias
        ({"vencimento": "2026-10-17"}, 3, False),  # um dia alem
        ({"vencimento": "2026-10-17"}, 4, True),  # N configuravel
        ({"vencimento": "2026-10-12"}, 3, False),  # ja venceu
        ({"situacao": "paga"}, 3, False),
        ({"situacao": "vencida"}, 3, False),
        ({"situacao": "cancelada"}, 3, False),
        ({"situacao": "sem_titulo"}, 3, False),
        ({"vencimento": None}, 3, False),
        ({"vencimento": "nao-e-data"}, 3, False),
        ({"valor_total": None}, 3, False),
    ],
)
def test_competencia_em_aberto_dentro_de_n_dias(kwargs: dict[str, Any], dias: int, enviaria: bool) -> None:
    escolhida, motivo = escolher_competencia(fatos(competencia(**kwargs)), hoje=HOJE, dias_antecedencia=dias)
    assert (escolhida is not None) is enviaria
    if not enviaria:
        assert motivo == MOTIVO_SEM_COMPETENCIA_NA_JANELA


def test_varias_competencias_escolhe_o_vencimento_mais_proximo() -> None:
    f = fatos(
        competencia("2026-11", vencimento="2026-10-16"),
        competencia("2026-10", vencimento="2026-10-14"),
        competencia("2026-09", situacao="paga", vencimento="2026-10-13"),
    )
    escolhida, _ = escolher_competencia(f, hoje=HOJE, dias_antecedencia=3)
    assert escolhida is not None and escolhida.competencia == "2026-10"


@pytest.mark.parametrize(
    ("utc", "esperado"),
    [
        (datetime(2026, 10, 13, 10, 59, tzinfo=UTC), False),  # 07:59 em Sao Paulo
        (datetime(2026, 10, 13, 11, 0, tzinfo=UTC), True),  # 08:00
        (datetime(2026, 10, 13, 22, 59, tzinfo=UTC), True),  # 19:59
        (datetime(2026, 10, 13, 23, 0, tzinfo=UTC), False),  # 20:00
        (datetime(2026, 10, 14, 2, 0, tzinfo=UTC), False),  # 23:00
    ],
)
def test_horario_de_silencio(utc: datetime, esperado: bool) -> None:
    assert dentro_do_horario(utc) is esperado
