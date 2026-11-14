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


#: SEGUIMENTO CURTO (rodada 11, 14/10/2026): com o Lucas ativo, estas perguntas continuam o assunto
#: financeiro. Alem do esclarecimento acima: "e a de outubro?", "quanto foi?", "e o vencimento?", "foi paga?".
_SEGUIMENTO: Final[re.Pattern[str]] = re.compile(
    r"\bquanto (?:foi|era|ficou|deu)\b|\bfoi (?:paga|pago|quitad[ao])\b|\be o vencimento\b"
    r"|\bvence quando\b|\bquando vence\b"
)
_E_A_DE: Final[re.Pattern[str]] = re.compile(r"^e (?:a|o|as|os|aquela|aquele)\b")
_MAX_PALAVRAS_DO_SEGUIMENTO: Final[int] = 7

#: SINAL DE CONSULTA DO PLANO: com qualquer um destes a mensagem NAO e' seguimento da cobranca — vai pelo
#: caminho normal (a consulta do plano e' da Helena).
_PISTA_DE_PLANO: Final[re.Pattern[str]] = re.compile(
    r"\bplano\b|\bcarencias?\b|\bcarteirinha\b|\bcobre\b|\bcobrem\b|\bcobertura\b|\bautoriza\w*"
    r"|\bdependentes?\b|\bsegmenta\w*|\bacomoda\w*|\bvigencia\b"
)
#: Pessoa ou quadro de saude na frase ("e a minha filha?", "e o bebe?"): nunca seguimento financeiro.
_PESSOA_OU_SAUDE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:filh[oa]s?|mae|pai|bebe|crianca|esposa|marido|dor|febre|sintoma\w*|passando mal|remedio)\b"
)


def e_seguimento_de_cobranca(texto: object) -> bool:
    """`True` quando a mensagem curta continua a conversa de cobranca (esclarecimento, "e a de <mes>?",
    "quanto foi?", "e o vencimento?", "foi paga?") SEM pista de consulta do plano, sem pessoa/saude e sem
    discordancia. Decide por TEXTO, nunca pelo modelo; o chamador exige o Lucas ativo."""
    if not isinstance(texto, str):
        return False
    plano = _normalizar(texto).strip(" ?!.,")
    if not plano or _PISTA_DE_PLANO.search(plano) or _PESSOA_OU_SAUDE.search(plano):
        return False
    if _DISCORDANCIA.search(plano):
        return False
    if _ESCLARECIMENTO.search(plano):
        return True
    if len(plano.split()) > _MAX_PALAVRAS_DO_SEGUIMENTO:
        return False
    return bool(_SEGUIMENTO.search(plano) or _E_A_DE.search(plano))


#: Mensagem de cobranca "limpa" (16/10/2026, `dono_v1` segmento 5): curta, sem pista de plano, sem pessoa ou
#: quadro de saude, sem discordancia (contestacao tem o caminho dela) e sem cancelamento.
_MAX_PALAVRAS_DA_COBRANCA_CURTA: Final[int] = 12
_CANCELAMENTO: Final[re.Pattern[str]] = re.compile(r"\bcancel\w*")


def e_mensagem_curta_sem_ruido_de_cobranca(texto: object) -> bool:
    """`True` quando a mensagem e' curta e NAO traz pista de plano, pessoa/saude,
    discordancia nem cancelamento.

    Nao diz que o assunto e' cobranca (isso e' do lexico de cobranca do chamador): so' afasta o que faria a
    mensagem ser outra coisa. Decide por TEXTO, nunca pelo modelo."""
    if not isinstance(texto, str):
        return False
    plano = _normalizar(texto).strip(" ?!.,")
    if not plano or len(plano.split()) > _MAX_PALAVRAS_DA_COBRANCA_CURTA:
        return False
    return not (
        _PISTA_DE_PLANO.search(plano)
        or _PESSOA_OU_SAUDE.search(plano)
        or _DISCORDANCIA.search(plano)
        or _CANCELAMENTO.search(plano)
    )


__all__ = [
    "SUBTIPOS_QUE_NAO_SE_SOBRESCREVEM",
    "SUBTIPOS_REAPROVEITAVEIS",
    "e_mensagem_curta_sem_ruido_de_cobranca",
    "e_pergunta_de_esclarecimento",
    "e_seguimento_de_cobranca",
]
