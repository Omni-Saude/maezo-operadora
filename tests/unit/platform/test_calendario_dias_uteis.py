"""R-005 / F-018 (DEC-02) — calendario versionado de feriados + conversor de dias uteis.

O conversor so faz aritmetica de calendario: inicio + N dias uteis -> vencimento (fim do N-esimo
dia util, America/Sao_Paulo; o dia do inicio nao conta). Quantos dias uteis cada prazo tem vive
na DMN (AGENTS.md regra 5). Casos de borda: fim de semana, feriado nacional, feriado estadual,
virada de ano, Pascoa e dias moveis, ponto facultativo (conta como util), fuso sem horario de
verao, entradas invalidas, e mutacao da tabela de feriados (a tabela e o que decide).
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from maezo.platform.calendario import dias_uteis as du
from maezo.platform.calendario import feriados_br as fb
from maezo.platform.calendario.dias_uteis import (
    FUSO_NEGOCIO,
    duracao_iso_ate,
    eh_dia_util,
    feriados_do_ano,
    normalizar_uf,
    pascoa,
    vencimento_em_dias_uteis,
)

BRT = dt.timezone(dt.timedelta(hours=-3))


def _brt(ano: int, mes: int, dia: int, hora: int = 10) -> dt.datetime:
    return dt.datetime(ano, mes, dia, hora, 0, tzinfo=FUSO_NEGOCIO)


def _fim(ano: int, mes: int, dia: int) -> dt.datetime:
    return dt.datetime(ano, mes, dia, 23, 59, 59, tzinfo=FUSO_NEGOCIO)


# --- Pascoa e dias moveis ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ano", "esperado"),
    [
        (2019, dt.date(2019, 4, 21)),
        (2024, dt.date(2024, 3, 31)),
        (2025, dt.date(2025, 4, 20)),
        (2026, dt.date(2026, 4, 5)),
        (2027, dt.date(2027, 3, 28)),
        (2030, dt.date(2030, 4, 21)),
        (2038, dt.date(2038, 4, 25)),
    ],
)
def test_pascoa_gregoriana(ano: int, esperado: dt.date) -> None:
    assert pascoa(ano) == esperado


def test_dias_moveis_de_2026() -> None:
    feriados = feriados_do_ano(2026)
    assert dt.date(2026, 4, 3) not in feriados  # Sexta-feira da Paixao: municipal (Lei 9.093/1995 art. 2o)
    # Carnaval (16 e 17/2) e Corpus Christi (4/6) sao ponto facultativo: nao entram.
    assert {dt.date(2026, 2, 16), dt.date(2026, 2, 17), dt.date(2026, 6, 4)}.isdisjoint(feriados)


# --- casos de borda do conversor ------------------------------------------------------------------


def test_fim_de_semana_nao_conta() -> None:
    # sexta 6/11/2026 + 1 dia util -> segunda 9/11
    assert vencimento_em_dias_uteis(_brt(2026, 11, 6), 1) == _fim(2026, 11, 9)


def test_dia_do_inicio_nao_conta() -> None:
    # segunda 9/11/2026 + 5 -> segunda 16/11 (15/11 e domingo)
    assert vencimento_em_dias_uteis(_brt(2026, 11, 9), 5) == _fim(2026, 11, 16)


def test_feriado_nacional_nao_conta() -> None:
    # quinta 24/12/2026 + 1 -> 25 (Natal) nao conta -> segunda 28/12
    assert vencimento_em_dias_uteis(_brt(2026, 12, 24), 1) == _fim(2026, 12, 28)


def test_sexta_feira_da_paixao_conta_como_util() -> None:
    assert vencimento_em_dias_uteis(_brt(2026, 4, 2), 1) == _fim(2026, 4, 3)


def test_ponto_facultativo_conta_como_util() -> None:
    assert vencimento_em_dias_uteis(_brt(2026, 2, 13), 1) == _fim(2026, 2, 16)  # Carnaval
    assert vencimento_em_dias_uteis(_brt(2026, 6, 3), 1) == _fim(2026, 6, 4)  # Corpus Christi


def test_feriado_estadual_so_conta_na_uf() -> None:
    # quarta 8/7/2026 + 1: em SP o 9/7 e feriado (Lei estadual 9.497/1997)
    assert vencimento_em_dias_uteis(_brt(2026, 7, 8), 1, "SP") == _fim(2026, 7, 10)
    assert vencimento_em_dias_uteis(_brt(2026, 7, 8), 1, "sp ") == _fim(2026, 7, 10)
    assert vencimento_em_dias_uteis(_brt(2026, 7, 8), 1) == _fim(2026, 7, 9)
    # UF sem fonte estadual conferida: so nacional (vencimento nunca posterior ao legal)
    assert "RJ" in fb.UFS_PENDENTES_DE_FONTE
    assert vencimento_em_dias_uteis(_brt(2026, 7, 8), 1, "RJ") == _fim(2026, 7, 9)
    # UF invalida: so nacional
    assert vencimento_em_dias_uteis(_brt(2026, 7, 8), 1, "XX") == _fim(2026, 7, 9)


def test_virada_de_ano() -> None:
    # quarta 30/12/2026 + 3: 31 (1), 1/1/2027 feriado, fim de semana, 4/1 (2), 5/1 (3)
    assert vencimento_em_dias_uteis(_brt(2026, 12, 30), 3) == _fim(2027, 1, 5)


def test_consciencia_negra_so_a_partir_de_2024() -> None:
    assert eh_dia_util(dt.date(2023, 11, 20)) is True
    assert eh_dia_util(dt.date(2024, 11, 20)) is False
    assert vencimento_em_dias_uteis(_brt(2024, 11, 19), 1) == _fim(2024, 11, 21)


def test_prazo_pac_10_dias_uteis_atravessando_finados() -> None:
    # sexta 30/10/2026 + 10: 2/11 (Finados) nao conta; 3..6 (4), 9..13 (9), 16/11 (10)
    assert vencimento_em_dias_uteis(_brt(2026, 10, 30), 10) == _fim(2026, 11, 16)
    # + 14: 17, 18, 19 (13), 20/11 (Consciencia Negra) nao conta, 23/11 (14)
    assert vencimento_em_dias_uteis(_brt(2026, 10, 30), 14) == _fim(2026, 11, 23)


def test_fuso_de_negocio_sem_horario_de_verao() -> None:
    # 01:00 UTC de sexta 6/11 ainda e quinta 5/11 em Brasilia: a contagem parte do dia local.
    inicio_utc = dt.datetime(2026, 11, 6, 1, 0, tzinfo=dt.UTC)
    venc = vencimento_em_dias_uteis(inicio_utc, 1)
    assert venc == _fim(2026, 11, 6)
    # sem horario de verao (Decreto 9.772/2019): UTC-3 inclusive no antigo periodo de verao
    for mes in (1, 2, 11, 12):
        assert venc.replace(month=mes).utcoffset() == dt.timedelta(hours=-3)
    assert venc.isoformat() == "2026-11-06T23:59:59-03:00"


def test_entrada_em_outro_fuso_da_o_mesmo_vencimento() -> None:
    em_brt = vencimento_em_dias_uteis(dt.datetime(2026, 11, 9, 10, tzinfo=BRT), 5)
    em_utc = vencimento_em_dias_uteis(dt.datetime(2026, 11, 9, 13, tzinfo=dt.UTC), 5)
    assert em_brt == em_utc == _fim(2026, 11, 16)


@pytest.mark.parametrize("dias", [0, -1, 367, True, False, 5.0, "5", None])
def test_dias_uteis_invalidos_sao_recusados(dias: object) -> None:
    with pytest.raises(ValueError):
        vencimento_em_dias_uteis(_brt(2026, 11, 9), dias)  # type: ignore[arg-type]


def test_instante_sem_fuso_e_recusado() -> None:
    with pytest.raises(ValueError):
        vencimento_em_dias_uteis(dt.datetime(2026, 11, 9, 10), 5)


# --- duracao ISO lida pelo timer ------------------------------------------------------------------


def test_duracao_iso_arredonda_para_baixo() -> None:
    agora = dt.datetime(2026, 11, 9, 13, 0, 0, 900_000, tzinfo=dt.UTC)
    venc = _fim(2026, 11, 9)  # 02:59:59 UTC de 10/11 -> 13h59m58,1s depois
    assert duracao_iso_ate(agora, venc) == "PT50398S"


def test_duracao_iso_de_vencimento_passado_e_zero() -> None:
    assert duracao_iso_ate(_brt(2026, 11, 20), _fim(2026, 11, 9)) == "PT0S"


def test_duracao_iso_exige_fuso() -> None:
    with pytest.raises(ValueError):
        duracao_iso_ate(dt.datetime(2026, 11, 9), _fim(2026, 11, 9))


def test_normalizar_uf() -> None:
    assert normalizar_uf(" sp") == "SP"
    for lixo in ("XX", "SPX", "", None, 35, "S P"):
        assert normalizar_uf(lixo) is None


# --- o dado: proveniencia e versao ----------------------------------------------------------------


def test_toda_regra_tem_fonte_e_url_oficial() -> None:
    regras = list(fb.NACIONAIS) + [r for regras in fb.ESTADUAIS.values() for r in regras]
    for regra in regras:
        assert regra.fonte.strip(), regra
        assert re.fullmatch(r"https://www\.(planalto\.gov\.br|al\.sp\.gov\.br)/\S+", regra.url), regra
        assert regra.tipo in {"feriado", "feriado_municipal", "ponto_facultativo"}


def test_escopo_estadual_e_so_uf_valida_e_sem_municipal() -> None:
    assert len(fb.UFS) == 27
    assert set(fb.ESTADUAIS) <= fb.UFS
    assert fb.UFS - set(fb.ESTADUAIS) == fb.UFS_PENDENTES_DE_FONTE
    assert re.fullmatch(r"feriados-br-\d{4}\.\d{2}\.\d{2}-\d+", fb.CALENDARIO_VERSAO)


def test_feriados_nacionais_fixos_completos() -> None:
    fixos = {(r.mes, r.dia) for r in fb.NACIONAIS if isinstance(r, fb.RegraFixa) and r.tipo == "feriado"}
    assert fixos == {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}


# --- mutacao da tabela: o resultado vem do DADO, nao de constante escondida -----------------------


def test_mutacao_remover_o_natal_muda_o_vencimento(monkeypatch: pytest.MonkeyPatch) -> None:
    sem_natal = tuple(
        r for r in fb.NACIONAIS if not (isinstance(r, fb.RegraFixa) and (r.mes, r.dia) == (12, 25))
    )
    monkeypatch.setattr(du, "NACIONAIS", sem_natal)
    assert vencimento_em_dias_uteis(_brt(2026, 12, 24), 1) == _fim(2026, 12, 25)


def test_mutacao_ponto_facultativo_virar_feriado_muda_o_vencimento(monkeypatch: pytest.MonkeyPatch) -> None:
    mutada = tuple(
        fb.RegraPascoa(r.deslocamento, r.nome, "feriado", r.fonte, r.url)
        if isinstance(r, fb.RegraPascoa) and r.deslocamento == 60
        else r
        for r in fb.NACIONAIS
    )
    monkeypatch.setattr(du, "NACIONAIS", mutada)
    assert vencimento_em_dias_uteis(_brt(2026, 6, 3), 1) == _fim(2026, 6, 5)


def test_mutacao_nova_regra_estadual_passa_a_valer(monkeypatch: pytest.MonkeyPatch) -> None:
    regra = fb.RegraFixa(11, 10, "Feriado sintetico", "feriado", "teste", "https://www.planalto.gov.br/x")
    monkeypatch.setattr(du, "ESTADUAIS", {**fb.ESTADUAIS, "RJ": (regra,)})
    assert vencimento_em_dias_uteis(_brt(2026, 11, 9), 1, "RJ") == _fim(2026, 11, 11)
    assert vencimento_em_dias_uteis(_brt(2026, 11, 9), 1) == _fim(2026, 11, 10)


def test_mutacao_vigencia_da_regra_e_respeitada(monkeypatch: pytest.MonkeyPatch) -> None:
    mutada = tuple(
        fb.RegraFixa(r.mes, r.dia, r.nome, r.tipo, r.fonte, r.url, desde=2027)
        if isinstance(r, fb.RegraFixa) and (r.mes, r.dia) == (12, 25)
        else r
        for r in fb.NACIONAIS
    )
    monkeypatch.setattr(du, "NACIONAIS", mutada)
    assert eh_dia_util(dt.date(2026, 12, 25)) is True
    assert eh_dia_util(dt.date(2028, 12, 25)) is False  # segunda-feira, regra ja vigente
