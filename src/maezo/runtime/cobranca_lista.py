"""PEDIDO DE LISTA numa pergunta de cobranca, decidido pelo TEXTO do beneficiario (dono, 12/10/2026).

O defeito: "Quais foram as ultimas tres mensalidades pagas por mim?" recebeu so' "outubro de 2026", e a
insistencia ("Mas eu te perguntei das ultimas tres") recebeu o mesmo mes de novo. O Lucas nao ve' texto
(ADR-0062), e o handoff so' levava UM mes. Decisao do dono: havendo o dado, o Lucas LISTA; faltando, diz o
que tem ("Encontrei so' 2 ...").

O desenho e' o do foco (`cobranca_foco.py`) e do ano da competencia (`competencia.py`): um lexico PEQUENO e
deterministico sobre o texto da pessoa decide QUANTOS meses, de QUE periodo e de QUE situacao. O resultado
viaja no handoff como um dicionario FECHADO de quatro chaves (`PedidoDeLista.como_handoff`) — numeros e
`AAAA-MM`, nunca o texto.

O que o lexico reconhece (texto sem acento e minusculo):
  - quantidade: "ultimas tres", "ultimos 3 meses", "as tres ultimas", "3 ultimos pagamentos";
    "ultimos meses"/"ultimas mensalidades"/"historico" sem numero -> `QUANTIDADE_PADRAO`;
  - periodo: "de julho a setembro", "entre julho e setembro" (ano como em `resolver_ano_da_competencia`);
    "as de 2026", "mensalidades de 2025" (ano inteiro);
  - situacao: "pagas"/"paguei"/"pagamentos" -> `paga`; "em aberto"/"abertas"/"pendentes"/"nao pagas" ->
    `em_aberto` ("todas em aberto", "quais estao em aberto" tambem pedem lista).
Um pedido de UM mes ("a ultima mensalidade", "a de julho") nao e' lista: `None`, o caminho de sempre.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from maezo.runtime.competencia import competencia_valida

SituacaoDaLista = Literal["paga", "em_aberto"]

#: "ultimos meses" sem numero: tres (decisao do dono, 12/10/2026).
QUANTIDADE_PADRAO: Final[int] = 3
#: Teto: a janela que a fonte AMH devolve (`fonte_cobranca_amh.JANELA_MESES`).
QUANTIDADE_MAXIMA: Final[int] = 12
#: As chaves do pedido no handoff, nem uma a mais, nem uma a menos.
CHAVES_DA_LISTA: Final[frozenset[str]] = frozenset({"quantidade", "de", "ate", "situacao"})
SITUACOES_DA_LISTA: Final[frozenset[str]] = frozenset({"paga", "em_aberto"})

_NUMERAIS: Final[dict[str, int]] = {
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "onze": 11,
    "doze": 12,
}
_MESES: Final[dict[str, int]] = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

#: Um numero de meses: digitos ou numeral, nunca seguido de outra unidade ("ultimos 30 dias" nao e' lista).
_N: Final[str] = r"(\d{1,2}|" + "|".join(_NUMERAIS) + r")(?!\s*(?:dias|semanas|anos)\b)"
_MES: Final[str] = r"(" + "|".join(_MESES) + r")"
_ANO: Final[str] = r"((?:19|20)\d{2})"
_SUBSTANTIVOS: Final[str] = "mensalidades|meses|pagamentos|boletos|coparticipacoes|parcelas|faturas|cobrancas"
_SUBSTANTIVO: Final[str] = r"(?:" + _SUBSTANTIVOS + r")"

_ULTIMAS_N: Final[re.Pattern[str]] = re.compile(r"\b(?:ultim[oa]s|anteriores)\s+" + _N + r"\b")
_N_ULTIMAS: Final[re.Pattern[str]] = re.compile(r"\b" + _N + r"\s+(?:ultim[oa]s|anteriores)\b")
_N_SUBSTANTIVO: Final[re.Pattern[str]] = re.compile(r"\b" + _N + r"\s+" + _SUBSTANTIVO + r"\b")
_ULTIMAS_SEM_N: Final[re.Pattern[str]] = re.compile(
    r"\bultim[oa]s\s+" + _SUBSTANTIVO + r"\b|\bhistorico\b|\bmeses anteriores\b"
)
_TEM_ULTIMAS: Final[re.Pattern[str]] = re.compile(r"\bultim[oa]s\b|\banteriores\b")

_INTERVALO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:de|desde|entre)\s+"
    + _MES
    + r"(?:\s+de\s+"
    + _ANO
    + r")?\s+(?:a|ate|e)\s+"
    + _MES
    + r"(?:\s+de\s+"
    + _ANO
    + r")?\b"
)
_ANO_INTEIRO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:as|os|todas|todos|pag[oa]s|"
    + _SUBSTANTIVOS
    + r")\s+(?:d[eo]|em)\s+(?:ano\s+de\s+)?"
    + _ANO
    + r"\b"
)
_DE_ANO: Final[re.Pattern[str]] = re.compile(r"\b(?:de|em)\s+(?:ano\s+de\s+)?" + _ANO + r"\b")
_NOME_DE_MES: Final[re.Pattern[str]] = re.compile(r"\b" + _MES + r"\b")

_EM_ABERTO: Final[re.Pattern[str]] = re.compile(
    r"\bem aberto\b|\babert[oa]s\b|\bpendentes\b|\bnao pag[oa]s\b"
)
_PEDE_TODAS_EM_ABERTO: Final[re.Pattern[str]] = re.compile(
    r"\btod[oa]s\b|\bquais\b|\bmensalidades\b|\bboletos\b|\bparcelas\b"
)
_PAGAS: Final[re.Pattern[str]] = re.compile(r"\bpag[oa]s\b|\bpaguei\b|\bpagamentos\b")

_ESPACOS: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PedidoDeLista:
    """Quantos meses (`quantidade`), de que periodo (`de`..`ate`, `AAAA-MM` inclusivos) e de que
    situacao. Nenhum campo carrega texto."""

    quantidade: int
    de: str | None = None
    ate: str | None = None
    situacao: SituacaoDaLista | None = None

    def __post_init__(self) -> None:
        if (
            type(self.quantidade) is not int
            or not 2 <= self.quantidade <= QUANTIDADE_MAXIMA
            or (self.de is not None and not competencia_valida(self.de))
            or (self.ate is not None and not competencia_valida(self.ate))
            or (self.de is not None and self.ate is not None and self.de > self.ate)
            or (self.situacao is not None and self.situacao not in SITUACOES_DA_LISTA)
        ):
            raise ValueError("cobranca_lista: pedido fora do dominio")

    def como_handoff(self) -> dict[str, Any]:
        return {"quantidade": self.quantidade, "de": self.de, "ate": self.ate, "situacao": self.situacao}

    @property
    def tem_periodo(self) -> bool:
        return self.de is not None or self.ate is not None


def lista_do_handoff(valor: object) -> PedidoDeLista | None:
    """O pedido de um valor de handoff/estado; `None` para ausente. Forma errada LEVANTA `ValueError`
    (o chamador decide: o handoff recusa, o grafo ignora)."""
    if valor is None:
        return None
    if not isinstance(valor, Mapping) or frozenset(valor) != CHAVES_DA_LISTA:
        raise ValueError("cobranca_lista: chaves fora do dominio")
    return PedidoDeLista(
        quantidade=valor["quantidade"], de=valor["de"], ate=valor["ate"], situacao=valor["situacao"]
    )


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", plano)


def _numero(bruto: str) -> int:
    return _NUMERAIS[bruto] if bruto in _NUMERAIS else int(bruto)


def _quantidade_dita(plano: str) -> int | None:
    for padrao in (_ULTIMAS_N, _N_ULTIMAS):
        achado = padrao.search(plano)
        if achado:
            return _numero(achado.group(1))
    if _TEM_ULTIMAS.search(plano):
        achado = _N_SUBSTANTIVO.search(plano)
        if achado:
            return _numero(achado.group(1))
    return None


def _meses_entre(de: str, ate: str) -> int:
    ano_de, mes_de = (int(p) for p in de.split("-"))
    ano_ate, mes_ate = (int(p) for p in ate.split("-"))
    return (ano_ate - ano_de) * 12 + (mes_ate - mes_de) + 1


def _intervalo(plano: str, hoje: date) -> tuple[str, str] | None:
    achado = _INTERVALO.search(plano)
    if achado is None:
        return None
    mes_de, ano_de, mes_ate, ano_ate = achado.groups()
    m_de, m_ate = _MESES[mes_de], _MESES[mes_ate]
    if ano_ate:
        a_ate = int(ano_ate)
    elif ano_de:
        a_ate = int(ano_de) if m_ate >= m_de else int(ano_de) + 1
    else:
        # A ocorrencia mais recente do mes final que nao esta' no futuro (a regra de `competencia.py`).
        a_ate = hoje.year if m_ate <= hoje.month else hoje.year - 1
    a_de = int(ano_de) if ano_de else (a_ate if m_de <= m_ate else a_ate - 1)
    de, ate = f"{a_de:04d}-{m_de:02d}", f"{a_ate:04d}-{m_ate:02d}"
    return (de, ate) if de <= ate else None


def decidir_lista_da_cobranca(texto: object, hoje: date) -> PedidoDeLista | None:
    """O pedido de lista do texto, ou `None` quando a pergunta e' de UM mes (ou o texto nao e' `str`)."""
    if not isinstance(texto, str):
        return None
    plano = _normalizar(texto)
    situacao: SituacaoDaLista | None = None
    if _EM_ABERTO.search(plano):
        situacao = "em_aberto"
    elif _PAGAS.search(plano):
        situacao = "paga"

    quantidade = _quantidade_dita(plano)
    if quantidade is None and _ULTIMAS_SEM_N.search(plano):
        quantidade = QUANTIDADE_PADRAO

    periodo = _intervalo(plano, hoje)
    if periodo is None and not _NOME_DE_MES.search(plano):
        # "as de 2026"; com quantidade ja' pedida, basta o ano ("as ultimas 3 de 2025").
        achado = _ANO_INTEIRO.search(plano) or (_DE_ANO.search(plano) if quantidade is not None else None)
        if achado:
            periodo = (f"{achado.group(1)}-01", f"{achado.group(1)}-12")
    if periodo is not None:
        no_periodo = _meses_entre(*periodo)
        quantidade = no_periodo if quantidade is None else min(quantidade, no_periodo)
    elif quantidade is None and situacao == "em_aberto" and _PEDE_TODAS_EM_ABERTO.search(plano):
        quantidade = QUANTIDADE_MAXIMA

    if quantidade is None or quantidade < 2:
        return None
    de, ate = periodo if periodo is not None else (None, None)
    return PedidoDeLista(quantidade=min(quantidade, QUANTIDADE_MAXIMA), de=de, ate=ate, situacao=situacao)


__all__ = [
    "CHAVES_DA_LISTA",
    "QUANTIDADE_MAXIMA",
    "QUANTIDADE_PADRAO",
    "PedidoDeLista",
    "SituacaoDaLista",
    "decidir_lista_da_cobranca",
    "lista_do_handoff",
]
