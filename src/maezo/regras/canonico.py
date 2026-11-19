"""JSON canônico da chancela de regras (ADR-0080, PROPOSTO; blueprint §3.3).

Mesmo perfil do subconjunto RFC 8785 que a repo já usa no portal humano
(`maezo.portal.engine.profile.canonicalize`): chaves ordenadas por unidades de código UTF-16, sem
espaços, strings com `ensure_ascii=False`, recusa de surrogates soltos. Um teste prova que, para todo
valor sem número, os bytes são IDÊNTICOS aos do perfil do portal.

A única extensão é necessária e estreita: valores de política (`teto_duro_brl: 300`) são inteiros, e o
perfil do portal proíbe números. Aqui um inteiro é aceito se estiver na faixa segura do I-JSON
(|n| ≤ 2^53 − 1), serializado em decimal — exatamente o que o RFC 8785 produz para esses números.
Ponto flutuante é RECUSADO (falha fechada): a serialização ES6 de float é fonte clássica de hash
divergente entre linguagens, e nenhum valor coberto hoje é fracionário.

Biblioteca pura (só stdlib): não importa o portal para não puxar o pacote dele para o CI.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

#: Maior inteiro exatamente representável em IEEE 754 binary64 (I-JSON, RFC 7493 §2.2).
INTEIRO_SEGURO_MAX: Final[int] = 2**53 - 1
_PROFUNDIDADE_MAX: Final[int] = 32
_SURROGATE: Final[re.Pattern[str]] = re.compile("[\ud800-\udfff]")


class CanonicoError(ValueError):
    """Valor fora do perfil canônico (a mensagem nunca carrega o conteúdo)."""


def _string(valor: str) -> str:
    if _SURROGATE.search(valor) is not None:
        raise CanonicoError("escalar Unicode invalido (surrogate solto)")
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def canonicalizar(valor: Any) -> bytes:
    """Bytes canônicos (UTF-8) de `valor`: null, bool, str, int seguro, lista e mapa de chave str."""

    def codificar(item: Any, profundidade: int) -> str:
        if profundidade > _PROFUNDIDADE_MAX:
            raise CanonicoError("profundidade maxima excedida")
        if item is None:
            return "null"
        if type(item) is bool:
            return "true" if item else "false"
        if type(item) is str:
            return _string(item)
        if type(item) is int:
            if abs(item) > INTEIRO_SEGURO_MAX:
                raise CanonicoError("inteiro fora da faixa segura do I-JSON")
            return str(item)
        if type(item) is float:
            raise CanonicoError("ponto flutuante nao e' aceito no hash de regra")
        if type(item) in (list, tuple):
            return "[" + ",".join(codificar(x, profundidade + 1) for x in item) + "]"
        if type(item) is dict:
            if not all(type(k) is str for k in item):
                raise CanonicoError("chave de objeto JSON tem de ser str")
            chaves = sorted(item, key=lambda k: k.encode("utf-16-be"))
            return (
                "{" + ",".join(_string(k) + ":" + codificar(item[k], profundidade + 1) for k in chaves) + "}"
            )
        raise CanonicoError(f"tipo nao canonico: {type(item).__name__}")

    return codificar(valor, 0).encode("utf-8")


def sha256_hex(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def sha256_canonico(valor: Any) -> str:
    """sha256 (hex minúsculo) dos bytes canônicos de `valor`."""
    return sha256_hex(canonicalizar(valor))


def normalizar_fim_de_linha(dados: bytes) -> bytes:
    """CRLF -> LF, como a DL-0148 (`check_aprovacao_de_regras.sha256_normalizado`): o checkout
    Windows (`core.autocrlf=true`) e o do CI Linux têm de dar o MESMO hash."""
    return dados.replace(b"\r\n", b"\n")


__all__ = [
    "INTEIRO_SEGURO_MAX",
    "CanonicoError",
    "canonicalizar",
    "normalizar_fim_de_linha",
    "sha256_canonico",
    "sha256_hex",
]
