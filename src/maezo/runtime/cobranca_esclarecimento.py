"""PERGUNTA DE ESCLARECIMENTO sobre a resposta anterior do Lucas (teste real do dono, 12/10/2026).

O defeito: o Lucas respondeu "Voce pagou a mensalidade de julho em 29/07"; a pessoa perguntou "De qual
ano?" e o turno foi lido SOZINHO — `consulta_valores` sem mes —, entao o Lucas respondeu o mes mais
recente (outubro). A pessoa insistiu ("Voce disse que paguei em 29/07, mas de que ano?") e o
classificador leu `contestacao`, que sempre escala: um caso P2 "o beneficiario contesta uma cobranca"
para quem so' queria saber o ano.

A correcao e' deterministica, no mesmo desenho do foco (`cobranca_foco.py`) e do ano da competencia
(`competencia.py`): um lexico PEQUENO, sobre o texto da pessoa, decide se a mensagem e' uma pergunta
SOBRE A RESPOSTA ANTERIOR. Quando e', e o Lucas esta' com a conversa, o despachante reaproveita o
ASSUNTO e o MES daquela resposta (as colunas `lucas_cobranca_subtipo`/`lucas_competencia` do roteador).

Contestacao continua exigindo DISCORDANCIA EXPLICITA: qualquer termo de discordancia no texto
desliga o esclarecimento, e a mensagem segue o caminho de sempre (o Lucas escala).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: Perguntas sobre a resposta anterior, sobre o texto sem acento e minusculo.
_ESCLARECIMENTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:qual|que)\s+(?:e\s+o\s+|seria\s+o\s+|foi\s+o\s+|o\s+)?(?:ano|mes)\b"
    r"|\bcomo assim\b"
    r"|\bnao entendi\b"
    r"|\bde quando\b"
)

#: Discordancia explicita: com qualquer um destes, NAO e' esclarecimento (vale a contestacao).
_DISCORDANCIA: Final[re.Pattern[str]] = re.compile(
    r"errad|indevid|nao reconhec|discord|nao concordo|(?:cobr|pag)\w* a mais\b|absurd|contest|duplicad"
    r"|nao (?:fiz|devo|usei)\b|reclama"
)

#: Subtipos que um esclarecimento pode REAPROVEITAR: so' os informativos. Um esclarecimento nunca
#: leva a conversa para contestacao, cobranca recebida ou cancelamento.
SUBTIPOS_REAPROVEITAVEIS: Final[frozenset[str]] = frozenset(
    {"consulta_valores", "vencimento", "confirmacao_pagamento", "boleto_2via"}
)

#: Subtipos que o modelo leu e que um esclarecimento NAO sobrescreve: pedido de cancelamento e aviso
#: de cobranca/atraso continuam no caminho deles, mesmo com "como assim" no texto.
SUBTIPOS_QUE_NAO_SE_SOBRESCREVEM: Final[frozenset[str]] = frozenset({"cancelamento", "cobranca_recebida"})

_ESPACOS: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", plano)


def e_pergunta_de_esclarecimento(texto: object) -> bool:
    """`True` quando o texto pergunta sobre a resposta anterior SEM discordar dela."""
    if not isinstance(texto, str):
        return False
    plano = _normalizar(texto)
    return bool(_ESCLARECIMENTO.search(plano)) and not _DISCORDANCIA.search(plano)


__all__ = [
    "SUBTIPOS_QUE_NAO_SE_SOBRESCREVEM",
    "SUBTIPOS_REAPROVEITAVEIS",
    "e_pergunta_de_esclarecimento",
]
