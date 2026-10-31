"""Calendario versionado de feriados do Brasil (nacional + estadual) — DADO, nao regra de prazo.

R-005 / DEC-02 (decisao do juridico via dono, 2026-10-31): onde a norma diz "dias uteis", o
prazo e contado com um calendario de feriados VERSIONADO, nacional + o da UF do beneficiario.
Este modulo e so o dado (com a fonte de cada regra); a aritmetica fica em `dias_uteis.py`, e
QUANTOS dias uteis cada prazo tem continua na DMN/contrato (AGENTS.md regra 5).

Escopo e o que NAO entra:
- Nacional: os feriados civis de lei federal (Lei 662/1949 com a redacao da Lei 10.607/2002,
  Lei 6.802/1980, Lei 14.759/2023) e a Sexta-feira da Paixao, que a Lei 9.093/1995 art. 2o inclui
  entre os dias de guarda e que a portaria federal anual de feriados lista como feriado nacional.
- Estadual: SO a regra com fonte primaria conferida. Hoje: SP (Lei estadual 9.497/1997, 9 de
  julho, data magna — Lei 9.093/1995 art. 1o, II). As outras 26 UFs estao em
  `UFS_PENDENTES_DE_FONTE`: sem regra estadual, a conversao usa so o calendario nacional, o que
  da um vencimento IGUAL OU ANTERIOR ao legal (nunca posterior). Incluir uma UF e mudanca de
  DADO (nova regra com fonte + nova `CALENDARIO_VERSAO`), sem mudanca de codigo.
- Municipal: nunca. Nenhum feriado municipal e inventado aqui.
- Ponto facultativo (Carnaval segunda e terca, Corpus Christi): calculado de forma
  deterministica a partir da Pascoa, mas NAO e feriado — conta como dia util. Tratar ponto
  facultativo como dia nao util e decisao do juridico (seria mudar `tipo` aqui, com nova versao).

Qualquer mudanca neste arquivo exige subir `CALENDARIO_VERSAO`; a versao vai junto de cada
vencimento calculado (auditoria de qual calendario produziu o prazo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Versao do conjunto de regras abaixo. Subir a cada mudanca de dado.
CALENDARIO_VERSAO = "feriados-br-2026.10.31-1"

TipoDia = Literal["feriado", "ponto_facultativo"]

_LEI_662 = "https://www.planalto.gov.br/ccivil_03/leis/l0662.htm"
_LEI_6802 = "https://www.planalto.gov.br/ccivil_03/leis/l6802.htm"
_LEI_14759 = "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/L14759.htm"
_LEI_9093 = "https://www.planalto.gov.br/ccivil_03/leis/l9093.htm"
_NAO_FERIADO = "nao e feriado civil (Lei 9.093/1995 art. 1o); ponto facultativo na portaria federal anual"
_SP_LEI_9497 = "https://www.al.sp.gov.br/repositorio/legislacao/lei/1997/lei-9497-05.03.1997.html"


@dataclass(frozen=True)
class RegraFixa:
    """Dia fixo do ano (mes/dia), vigente de `desde` a `ate` (anos inclusivos; None = sem limite)."""

    mes: int
    dia: int
    nome: str
    tipo: TipoDia
    fonte: str
    url: str
    desde: int | None = None
    ate: int | None = None


@dataclass(frozen=True)
class RegraPascoa:
    """Dia movel: `deslocamento` dias a partir do domingo de Pascoa (gregoriana)."""

    deslocamento: int
    nome: str
    tipo: TipoDia
    fonte: str
    url: str
    desde: int | None = None
    ate: int | None = None


Regra = RegraFixa | RegraPascoa

NACIONAIS: tuple[Regra, ...] = (
    RegraFixa(
        1, 1, "Confraternizacao Universal", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662
    ),
    RegraFixa(4, 21, "Tiradentes", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662),
    RegraFixa(5, 1, "Dia do Trabalho", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662),
    RegraFixa(
        9, 7, "Independencia do Brasil", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662
    ),
    RegraFixa(10, 12, "Nossa Senhora Aparecida", "feriado", "Lei 6.802/1980 art. 1o", _LEI_6802),
    RegraFixa(11, 2, "Finados", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662),
    RegraFixa(
        11, 15, "Proclamacao da Republica", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662
    ),
    RegraFixa(
        11,
        20,
        "Dia Nacional de Zumbi e da Consciencia Negra",
        "feriado",
        "Lei 14.759/2023 art. 1o (em vigor desde 22/12/2023)",
        _LEI_14759,
        desde=2024,
    ),
    RegraFixa(12, 25, "Natal", "feriado", "Lei 662/1949 art. 1o (red. Lei 10.607/2002)", _LEI_662),
    RegraPascoa(
        -2,
        "Sexta-feira da Paixao",
        "feriado",
        "Lei 9.093/1995 art. 2o (dia de guarda; listado como feriado nacional na portaria federal anual)",
        _LEI_9093,
    ),
    RegraPascoa(-48, "Carnaval (segunda-feira)", "ponto_facultativo", _NAO_FERIADO, _LEI_9093),
    RegraPascoa(-47, "Carnaval (terca-feira)", "ponto_facultativo", _NAO_FERIADO, _LEI_9093),
    RegraPascoa(60, "Corpus Christi", "ponto_facultativo", _NAO_FERIADO, _LEI_9093),
)

#: Regras estaduais com fonte primaria conferida (texto da lei lido na fonte oficial).
ESTADUAIS: dict[str, tuple[Regra, ...]] = {
    "SP": (
        RegraFixa(
            7,
            9,
            "Revolucao Constitucionalista (data magna do Estado de Sao Paulo)",
            "feriado",
            "Lei estadual SP 9.497/1997 art. 1o (Lei 9.093/1995 art. 1o, II); conferida em 2026-10-31",
            _SP_LEI_9497,
        ),
    ),
}

#: As 27 unidades da federacao.
UFS: frozenset[str] = frozenset(
    {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
        "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    }
)  # fmt: skip

#: UFs cujas regras estaduais ainda nao tem fonte primaria conferida neste calendario.
UFS_PENDENTES_DE_FONTE: frozenset[str] = UFS - frozenset(ESTADUAIS)
