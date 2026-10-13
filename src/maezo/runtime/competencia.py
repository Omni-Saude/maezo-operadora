"""A COMPETENCIA de cobranca (`AAAA-MM`): UMA definicao para o repositorio inteiro.

Mora em `runtime/`, e nao num agente nem na plataforma, porque quatro lugares a validam e eles
estao dos dois lados da fronteira: o classify da Helena (`agents/helena/graph.py`), a fonte de
cobranca do Lucas (`agents/lucas/fonte_cobranca.py`), o handoff (`platform/webhooks/whatsapp/
lucas_turno.py`) e o roteador (`.../roteamento.py`, que espelha o CHECK da migration 0017).
`agents/` nao importa `platform/`, e um agente nao importa o outro; `runtime/` todos ja' importam.
Quatro copias da mesma regex eram quatro chances de uma aceitar o que a outra recusa.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final

#: `AAAA-MM`, mes 01..12. O mesmo padrao do CHECK `lucas_competencia` da migration 0017.
COMPETENCIA_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: America/Sao_Paulo sem horario de verao desde 2019: UTC-3 fixo (mesma escolha de
#: `agents/lucas/graph.py` e `platform/testchannel/resultados.py`), sem depender de `tzdata`.
FUSO_BRASILIA: Final[timezone] = timezone(timedelta(hours=-3))

#: Ano EXPLICITO no texto do beneficiario: quatro digitos 19xx/20xx, isolados ("julho de 2024", "07/2024").
_ANO_EXPLICITO: Final[re.Pattern[str]] = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
#: As duas formas relativas tratadas (lexico deliberadamente minimo, texto ja' sem acento e minusculo).
_ANO_PASSADO: Final[re.Pattern[str]] = re.compile(r"\bano passado\b")
_ESTE_ANO: Final[re.Pattern[str]] = re.compile(r"\b(?:d?este|d?esse) ano\b")
#: Pistas de que a pergunta e' sobre a mensalidade QUE VEM (ou a mais recente), nao sobre uma ja' passada
#: (simulador, 13/10/2026: "Novembro ta' lancado?" em outubro virou novembro de 2025, ja' paga).
_PISTA_DO_PROXIMO: Final[re.Pattern[str]] = re.compile(
    r"\blancad|\bgerad|\bno sistema\b|\bproxim|\bvai vencer\b|\bvence\b|\bvencimento\b|\bja saiu\b"
    r"|\bja veio\b|\bja chegou\b|\bemitid|\bultim|\bmais recente\b|\bja tem\b|\bja ta\b|\bja esta\b"
)


def competencia_valida(valor: object) -> bool:
    """`valor` e' uma competencia `AAAA-MM`? Recusa tudo que nao for `str` (valor de modelo ou de
    entrada nao validada pode ser lista, numero, dict)."""
    return isinstance(valor, str) and COMPETENCIA_RE.match(valor) is not None


def hoje_em_brasilia(agora: datetime) -> date:
    """A data de `agora` no fuso do beneficiario (`agora` sem fuso e' lido como UTC)."""
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    return agora.astimezone(FUSO_BRASILIA).date()


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def resolver_ano_da_competencia(competencia: str | None, texto: object, hoje: date) -> str | None:
    """O ANO da competencia decidido pelo TEXTO do beneficiario, nunca pelo modelo (10/10/2026).

    Teste real do dono: "qual foi o valor da mensalidade de julho?" em 10/10/2026 saiu `2024-07` — o
    modelo do classify inventou o ano que a pessoa nao disse. Daqui em diante o MES vem do modelo
    (ja' validado como `AAAA-MM`) e o ANO e':

    - o ano dito no texto (4 digitos 19xx/20xx), quando ha' um so' — ou o do modelo, se ele e' um dos
      anos ditos;
    - "ano passado" -> o ano anterior ao de hoje; "este/deste/esse/desse ano" -> o ano de hoje;
    - sem ano no texto: a ocorrencia MAIS RECENTE do mes que nao esta' no futuro em relacao a `hoje`
      (hoje 10/10/2026: julho -> 2026-07, outubro -> 2026-10, novembro -> 2025-11);
    - sem ano, mas com PISTA de mensalidade que vem ou mais recente ("ja' ta' lancado?", "quando vence",
      "proxima", "ultima" — `_PISTA_DO_PROXIMO`): o mes deste ano, ou o do ano que vem quando ele e' o
      seguinte a dezembro (hoje 13/10/2026: novembro -> 2026-11; hoje 10/12/2026: janeiro -> 2027-01).

    Competencia ausente ou fora da forma volta como veio: quem valida e recusa e' o chamador.
    """
    if competencia is None or not competencia_valida(competencia):
        return competencia
    ano_do_modelo, mes_texto = competencia.split("-")
    mes = int(mes_texto)
    normalizado = _sem_acento(texto) if isinstance(texto, str) else ""
    anos_ditos = set(_ANO_EXPLICITO.findall(normalizado))
    if anos_ditos:
        if ano_do_modelo in anos_ditos or len(anos_ditos) > 1:
            return competencia
        return f"{anos_ditos.pop()}-{mes_texto}"
    if _ANO_PASSADO.search(normalizado):
        return f"{hoje.year - 1:04d}-{mes_texto}"
    if _ESTE_ANO.search(normalizado):
        return f"{hoje.year:04d}-{mes_texto}"
    if _PISTA_DO_PROXIMO.search(normalizado):
        ano = hoje.year + 1 if hoje.month == 12 and mes == 1 else hoje.year
        return f"{ano:04d}-{mes_texto}"
    ano = hoje.year if mes <= hoje.month else hoje.year - 1
    return f"{ano:04d}-{mes_texto}"


_NOMES_DOS_MESES: Final[dict[str, int]] = {
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
_NOME_DE_MES: Final[re.Pattern[str]] = re.compile(r"\b(" + "|".join(_NOMES_DOS_MESES) + r")\b")


def mes_unico_do_texto(texto: object) -> int | None:
    """O numero do mes quando o texto nomeia UM so' mes ("a de julho foi paga?"); `None` sem mes, com
    mais de um ("de julho a setembro") ou com texto que nao e' `str`."""
    if not isinstance(texto, str):
        return None
    meses = {_NOMES_DOS_MESES[m] for m in _NOME_DE_MES.findall(_sem_acento(texto))}
    return meses.pop() if len(meses) == 1 else None


__all__ = [
    "COMPETENCIA_RE",
    "FUSO_BRASILIA",
    "competencia_valida",
    "hoje_em_brasilia",
    "mes_unico_do_texto",
    "resolver_ano_da_competencia",
]
