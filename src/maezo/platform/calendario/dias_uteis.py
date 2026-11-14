"""Conversor deterministico de dias uteis (R-005 / F-018; DEC-02) — so aritmetica de calendario.

Entrada: um instante de inicio (com fuso), N dias uteis e, opcionalmente, a UF. Saida: o
instante de vencimento em America/Sao_Paulo. QUANTOS dias uteis um prazo tem NAO e decidido
aqui: o numero vem da DMN/contrato (AGENTS.md regra 5; ex.: `auth_sla.prazo_resposta_dias_uteis`
para a RN 623/2024 art. 12). O calendario de feriados e o dado versionado de `feriados_br`.

Convencao de contagem (a mesma do prazo processual, CPC art. 224): o dia do inicio NAO conta;
contam-se N dias uteis a partir do dia seguinte; o prazo vence no FIM (23:59:59) do N-esimo dia
util, no fuso America/Sao_Paulo. Dia util = segunda a sexta que nao e feriado nacional nem
feriado da UF informada (ponto facultativo conta como util — ver `feriados_br`).

O Brasil nao tem horario de verao desde 2019 (Decreto 9.772/2019); o fuso vem do banco IANA
(`zoneinfo`), e uma imagem sem `tzdata` cai no UTC-3 fixo, que e o mesmo deslocamento vigente.
"""

from __future__ import annotations

import datetime as dt
import re

from maezo.platform.calendario.feriados_br import (
    CALENDARIO_VERSAO,
    ESTADUAIS,
    NACIONAIS,
    UFS,
    Regra,
    RegraPascoa,
)

try:
    from zoneinfo import ZoneInfo

    FUSO_NEGOCIO: dt.tzinfo = ZoneInfo("America/Sao_Paulo")
except Exception:  # imagem slim sem banco de fusos: sem horario de verao desde 2019
    FUSO_NEGOCIO = dt.timezone(dt.timedelta(hours=-3), "America/Sao_Paulo")

#: Teto defensivo: nenhum prazo regulatorio de dias uteis chega perto disto.
MAX_DIAS_UTEIS = 366

_FIM_DO_DIA = dt.time(23, 59, 59)
_UF_RE = re.compile(r"[A-Z]{2}")


def pascoa(ano: int) -> dt.date:
    """Domingo de Pascoa no calendario gregoriano (algoritmo anonimo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    mes, dia = divmod(h + el - 7 * m + 114, 31)
    return dt.date(ano, mes, dia + 1)


def normalizar_uf(uf: object) -> str | None:
    """UF valida em caixa alta, ou None (ausente, branca, desconhecida ou de outro tipo)."""
    if not isinstance(uf, str):
        return None
    texto = uf.strip().upper()
    if _UF_RE.fullmatch(texto) is None or texto not in UFS:
        return None
    return texto


def _vigente(regra: Regra, ano: int) -> bool:
    return (regra.desde is None or ano >= regra.desde) and (regra.ate is None or ano <= regra.ate)


def _data_da_regra(regra: Regra, ano: int) -> dt.date:
    if isinstance(regra, RegraPascoa):
        return pascoa(ano) + dt.timedelta(days=regra.deslocamento)
    return dt.date(ano, regra.mes, regra.dia)


def feriados_do_ano(ano: int, uf: str | None = None) -> frozenset[dt.date]:
    """Datas NAO uteis por feriado (tipo `feriado`) no ano: nacionais + as da UF, se houver."""
    regras: tuple[Regra, ...] = NACIONAIS + ESTADUAIS.get(uf or "", ())
    return frozenset(
        _data_da_regra(regra, ano) for regra in regras if regra.tipo == "feriado" and _vigente(regra, ano)
    )


def eh_dia_util(dia: dt.date, uf: str | None = None) -> bool:
    """Segunda a sexta que nao e feriado nacional nem feriado da UF."""
    return dia.weekday() < 5 and dia not in feriados_do_ano(dia.year, normalizar_uf(uf))


def vencimento_em_dias_uteis(inicio: dt.datetime, dias_uteis: int, uf: str | None = None) -> dt.datetime:
    """Fim (23:59:59, America/Sao_Paulo) do N-esimo dia util contado a partir do dia seguinte ao inicio.

    `inicio` precisa ter fuso (um instante ingenuo e ambiguo e e recusado). `dias_uteis` e um
    inteiro em [1, MAX_DIAS_UTEIS] (bool e recusado). UF desconhecida ou ausente conta so os
    feriados nacionais.
    """
    if inicio.tzinfo is None or inicio.utcoffset() is None:
        raise ValueError("inicio precisa ter fuso horario (instante ingenuo e ambiguo)")
    if isinstance(dias_uteis, bool) or not isinstance(dias_uteis, int):
        raise ValueError(f"dias_uteis precisa ser inteiro, recebeu {type(dias_uteis).__name__}")
    if not 1 <= dias_uteis <= MAX_DIAS_UTEIS:
        raise ValueError(f"dias_uteis fora de [1, {MAX_DIAS_UTEIS}]: {dias_uteis}")
    uf_norm = normalizar_uf(uf)
    dia = inicio.astimezone(FUSO_NEGOCIO).date()
    restantes = dias_uteis
    while restantes:
        dia += dt.timedelta(days=1)
        if eh_dia_util(dia, uf_norm):
            restantes -= 1
    return dt.datetime.combine(dia, _FIM_DO_DIA, tzinfo=FUSO_NEGOCIO)


def duracao_iso_ate(agora: dt.datetime, vencimento: dt.datetime) -> str:
    """Duracao ISO 8601 (`PTnS`, segundos inteiros, arredondada para BAIXO) de `agora` ate `vencimento`.

    Vencimento ja passado vira `PT0S` (o timer dispara de imediato). Arredondar para baixo
    garante que o timer nunca vence depois do instante calculado.
    """
    if agora.tzinfo is None or vencimento.tzinfo is None:
        raise ValueError("agora e vencimento precisam ter fuso horario")
    segundos = int((vencimento - agora).total_seconds())
    return f"PT{max(segundos, 0)}S"


__all__ = [
    "CALENDARIO_VERSAO",
    "FUSO_NEGOCIO",
    "MAX_DIAS_UTEIS",
    "duracao_iso_ate",
    "eh_dia_util",
    "feriados_do_ano",
    "normalizar_uf",
    "pascoa",
    "vencimento_em_dias_uteis",
]
