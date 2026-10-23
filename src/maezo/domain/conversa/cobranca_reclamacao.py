"""O TIPO de uma reclamacao de cobranca, decidido pelo TEXTO do beneficiario (simulador, 13/10/2026).

O defeito: "FUI COBRADO DUAS VEZES NO MES PASSADO" escalou (certo) e, nas mensagens seguintes, com o caso ja'
com a equipe, o Lucas respondeu "Entendi. Para outra duvida sobre a mensalidade, e' so' me perguntar." —
como se a pessoa nao estivesse reclamando de nada. O Lucas nao ve' texto (ADR-0062): para reconhecer a
reclamacao ESPECIFICA ("sua reclamacao sobre a cobranca em dobro de setembro de 2026 ja' esta' com a nossa
equipe") ele recebe, no handoff, um enum fechado decidido aqui, pelo mesmo desenho do foco
(`cobranca_foco.py`): lexico pequeno, deterministico, sobre o texto sem acento e minusculo.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal, get_args

TipoDeReclamacao = Literal["em_dobro", "valor_errado", "nao_reconhece"]
TIPOS_DE_RECLAMACAO: Final[frozenset[str]] = frozenset(get_args(TipoDeReclamacao))

_LEXICO: Final[tuple[tuple[TipoDeReclamacao, re.Pattern[str]], ...]] = (
    (
        "em_dobro",
        re.compile(r"\bduas vezes\b|\bem dobro\b|\bduplicad|\bdobrad|\b2 vezes\b|\bcobrad[oa]s? de novo\b"),
    ),
    ("nao_reconhece", re.compile(r"\bnao reconhe|\bnao fiz\b|\bnao contratei\b|\bnao e minha\b")),
    ("valor_errado", re.compile(r"\berrad|\ba mais\b|\bmais caro\b|\bvalor (?:alto|maior)\b|\baument")),
)

_ESPACOS: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", plano)


def decidir_reclamacao(texto: object) -> TipoDeReclamacao | None:
    """O tipo da reclamacao, ou `None` quando nenhum termo casa (ou o texto nao e' `str`)."""
    if not isinstance(texto, str):
        return None
    plano = _normalizar(texto)
    for tipo, padrao in _LEXICO:
        if padrao.search(plano):
            return tipo
    return None


def reclamacao_valida(valor: object) -> bool:
    return isinstance(valor, str) and valor in TIPOS_DE_RECLAMACAO


__all__ = ["TIPOS_DE_RECLAMACAO", "TipoDeReclamacao", "decidir_reclamacao", "reclamacao_valida"]
