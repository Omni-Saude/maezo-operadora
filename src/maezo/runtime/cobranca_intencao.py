"""Intencoes de cobranca que abrem caso ou pedem confirmacao, decididas pelo TEXTO (rodada 12, 14/10/2026).

O Lucas nao ve' texto (ADR-0062). Decisao do dono (sessao de 14/10/2026): o Lucas responde os FATOS primeiro,
sempre, e so' abre caso humano para contestacao, pedido de negociacao/parcelamento, cancelamento, pedido
explicito de pessoa ou atraso real. Contestacao e pessoa ja' tem caminho proprio; aqui ficam os dois sinais
que faltavam, no mesmo desenho do foco (`cobranca_foco.py`): lexico pequeno e deterministico, sobre o texto
sem acento e minusculo, viajando no handoff como valor fechado.

  - NEGOCIACAO: "quero parcelar", "posso negociar a divida", "tem acordo?" — o Lucas nunca negocia
    (`PROMESSA_DE_CAPACIDADE_PROIBIDA`): abre o caso.
  - CONFIRMACAO do cancelamento: depois de o Lucas perguntar "Voce quer pedir o cancelamento do seu plano?",
    "sim, quero cancelar" confirma e "nao, deixa pra la" desiste. A pergunta pendente e' lembrada
    pelo roteador (referencia do ultimo texto enviado); sem ela, nenhuma confirmacao vale.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

Confirmacao = Literal["confirma", "nega"]
CONFIRMACOES: Final[frozenset[str]] = frozenset({"confirma", "nega"})

_NEGOCIACAO: Final[re.Pattern[str]] = re.compile(
    r"\bparcel(?:ar|o|amento|ei|as?)\b|\bnegocia(?:r|cao|ndo)\b|\brenegocia|\bacordo\b|\bdividir (?:em|a|o)\b"
    r"|\bpagar em vezes\b|\bdesconto (?:pra|para) pagar\b|\bquitar (?:a )?(?:divida|mensalidade)\b"
)

_NEGA: Final[re.Pattern[str]] = re.compile(
    r"\bnao (?:quero|vou|desejo|preciso|precisa|cancel\w*|e isso|era isso)\b|\bdesisti"
    r"|\bdeixa (?:pra la|quieto|assim)\b|\besquece\b|\bnem quero\b|\bmudei de ideia\b|^\s*nao\s*[.!]*\s*$"
)
_CONFIRMA: Final[re.Pattern[str]] = re.compile(
    r"\bsim\b|\bquero (?:sim|cancelar|pedir)\b|\bconfirmo\b|\bpode cancelar\b|\bcancela(?:r)? (?:sim|meu|o)\b"
    r"|\bisso mesmo\b|\bpedir (?:o )?cancelamento\b"
)

_ESPACOS: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", plano)


def pede_negociacao(texto: object) -> bool:
    """O texto pede para negociar/parcelar? `False` para o que nao for `str`."""
    return isinstance(texto, str) and bool(_NEGOCIACAO.search(_normalizar(texto)))


def decidir_confirmacao(texto: object) -> Confirmacao | None:
    """ "confirma" para um pedido afirmativo ("sim, quero cancelar"), "nega" para recusa ("nao quero,
    deixa pra la"), `None` sem nenhum dos dois. Negacao vence: "nao quero cancelar" nunca confirma."""
    if not isinstance(texto, str):
        return None
    plano = _normalizar(texto)
    if _NEGA.search(plano):
        return "nega"
    if _CONFIRMA.search(plano):
        return "confirma"
    return None


_MELHOR_NAO: Final[re.Pattern[str]] = re.compile(r"\bmelhor nao\b|\bnao quero mais\b")
_SIM: Final[re.Pattern[str]] = re.compile(r"\bsim\b")


def e_desistencia_do_cancelamento(texto: object) -> bool:
    """O texto DESISTE do cancelamento ("nao", "deixa pra la", "esquece", "melhor nao", "mudei de ideia",
    "nao quero mais")? Usado so' com a pergunta de confirmacao pendente (chave `MAEZO_CANCEL_PF_V1`). Um
    "sim" no texto nunca e' desistencia: segue o caminho de sempre."""
    if not isinstance(texto, str):
        return False
    plano = _normalizar(texto)
    if _SIM.search(plano):
        return False
    return decidir_confirmacao(texto) == "nega" or _MELHOR_NAO.search(plano) is not None


def confirmacao_valida(valor: object) -> bool:
    return isinstance(valor, str) and valor in CONFIRMACOES


__all__ = [
    "CONFIRMACOES",
    "Confirmacao",
    "confirmacao_valida",
    "decidir_confirmacao",
    "e_desistencia_do_cancelamento",
    "pede_negociacao",
]
