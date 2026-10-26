"""REEMBOLSO FORA DO ESCOPO (avaliacao r5b de 25/10/2026). Modulo FOLHA (sem import do grafo).

"Se eu fizer a consulta particular da' pra pedir reembolso?" chegou a `conversa` (o roteador leu
`fora_do_escopo`), o classificador disse `outside_channel`, a habilidade fechou a intencao em
`information` e o MODELO redigiu "Sim, e' possivel pedir reembolso...". Reembolso esta' fora do escopo da
Helena (pausado): a resposta e' UM texto fixo do pacote (`reembolso_fora`) dizendo que este canal nao trata
reembolso e quais sao os canais oficiais, nunca confirmando que e' possivel. E a trava de saida
`sem_confirmar_reembolso` troca qualquer redacao que confirme o reembolso ("e' possivel pedir reembolso",
"voce tem direito a reembolso") por esse mesmo texto, venha de onde vier.
"""

from __future__ import annotations

import re
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar

CHAVE_REEMBOLSO_FORA: Final[str] = "reembolso_fora"
TEXTO_REEMBOLSO_FORA: Final[str] = (
    "Por aqui eu não trato de reembolso. Você consegue essa informação pelo aplicativo Austa Clínicas, pelo "
    "portal do plano ou pela central de atendimento do plano."
)
PADRAO: Final[dict[str, str]] = {CHAVE_REEMBOLSO_FORA: TEXTO_REEMBOLSO_FORA}

#: O assunto reembolso na mensagem da pessoa (texto normalizado, sem acento).
_REEMBOLSO: Final[re.Pattern[str]] = re.compile(r"\b(?:reembols\w*|ressarc\w*)\b")

#: A redacao que CONFIRMA o reembolso (texto normalizado). Negacao logo antes ("nao e' possivel") nao conta.
_CONFIRMA_REEMBOLSO: Final[re.Pattern[str]] = re.compile(
    r"(?<!nao )\b(?:e|eh|sera|seria)\s+possivel\s+"
    r"(?:(?:voce\s+)?(?:pedir|solicitar|ter|receber|conseguir)\s+)?"
    r"(?:o\s+|um\s+)?(?:reembols\w*|ressarc\w*)"
    r"|(?<!nao )\b(?:tem|tera|teria|possui)\s+direito\s+(?:a|ao)\s+(?:um\s+)?(?:reembols\w*|ressarc\w*)"
    r"|(?<!nao )\b(?:voce\s+)?(?:pode|podera|consegue|conseguira)\s+(?:pedir|solicitar|receber|ter)\s+"
    r"(?:o\s+|um\s+)?(?:reembols\w*|ressarc\w*)"
    r"|\b(?:sera|vai\s+ser)\s+reembolsad\w*"
)


def pergunta_de_reembolso(texto: object) -> bool:
    """A mensagem fala de reembolso (pedir, saber se tem direito, prazo, valor)?"""
    return isinstance(texto, str) and bool(_REEMBOLSO.search(normalizar(texto)))


def confirma_reembolso(texto: object) -> bool:
    """O texto CONFIRMA que da' para pedir/receber reembolso? (a trava de saida)"""
    return isinstance(texto, str) and bool(_CONFIRMA_REEMBOLSO.search(normalizar(texto)))


__all__ = [
    "CHAVE_REEMBOLSO_FORA",
    "PADRAO",
    "TEXTO_REEMBOLSO_FORA",
    "confirma_reembolso",
    "pergunta_de_reembolso",
]
