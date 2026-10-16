"""Mascaramento de tudo o que sai do processo do simulador (S3, log, prompt do juiz e da persona).

Tokens fixos, nunca hash do valor: o relatorio precisa mostrar QUE havia um CPF ali, nao permitir
reconhecer de quem era. O que e' mascarado:

  * CPF, com ou sem pontuacao, e qualquer sequencia de 11 digitos;
  * a data de nascimento do sujeito da conversa, em qualquer das formas que a pessoa digita;
  * as partes do nome do sujeito (3+ letras, sem diferenciar acento nem caixa);
  * rotulo de boleto (`****1234`, `final 1234`) e sequencias longas de digitos (linha digitavel);
  * telefone (inclusive o sintetico).

Valor em reais, competencia e data de vencimento NAO sao mascarados: sozinhos nao identificam
ninguem e sao exatamente o que o relatorio precisa mostrar para explicar uma falha do D2.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from .registro import Sujeito

TOKEN_CPF = "[CPF]"
TOKEN_NASCIMENTO = "[NASCIMENTO]"
TOKEN_NOME = "[NOME]"
TOKEN_BOLETO = "[BOLETO]"
TOKEN_NUMERO = "[NUMERO]"
TOKEN_TELEFONE = "[TELEFONE]"
# DL-0114: o codigo de barras do boleto (linha digitavel de 47 digitos ou 44 digitos) e' CREDENCIAL DE
# PAGAMENTO: nunca vai ao relatorio, ao transcript nem ao log do simulador.
TOKEN_CODIGO_DE_BARRAS = "[CODIGO-DE-BARRAS]"

_CODIGO_DE_BARRAS = re.compile(
    r"(?<!\d)(?:\d{5}[.\s]?\d{5}[.\s]+\d{5}[.\s]?\d{6}[.\s]+\d{5}[.\s]?\d{6}[.\s]+\d[.\s]+\d{14}|\d{44})(?!\d)"
)
# Rede de seguranca: 20+ digitos seguidos (com UM separador entre eles) nao sao CPF, telefone, valor nem data.
_DIGITOS_DE_CREDENCIAL = re.compile(r"(?<!\d)\d(?:[.\s-]?\d){19,}(?!\d)")
_CPF = re.compile(r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}(?!\d)")
_TELEFONE = re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)")
_BOLETO_ASTERISCO = re.compile(r"\*{2,}\s?\d{2,6}(?!\d)")
_BOLETO_FINAL = re.compile(r"(\bfinal:?\s+)\d{2,6}(?!\d)", re.IGNORECASE)
_DIGITOS_LONGOS = re.compile(r"\d[\d.\s-]{11,}\d")
#: Palavras curtas e comuns que, sendo parte de um nome ("de", "da", "dos"), nao sao mascaradas.
_PARTICULAS = frozenset({"de", "da", "do", "das", "dos", "e"})

_VARIANTES: dict[str, str] = {
    "a": "aáàâãä",
    "e": "eéèêë",
    "i": "iíìîï",
    "o": "oóòôõö",
    "u": "uúùûü",
    "c": "cç",
    "n": "nñ",
}


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


def _padrao_de_palavra(palavra: str) -> str:
    partes = []
    for letra in sem_acento(palavra):
        variantes = _VARIANTES.get(letra)
        partes.append(f"[{variantes}{variantes.upper()}]" if variantes else re.escape(letra))
    return r"(?<!\w)" + "".join(partes) + r"(?!\w)"


def _formas_da_data(sujeito: Sujeito) -> list[str]:
    d = sujeito.nascimento
    dia, mes, ano = f"{d.day:02d}", f"{d.month:02d}", f"{d.year:04d}"
    formas = {
        f"{dia}/{mes}/{ano}",
        f"{dia}-{mes}-{ano}",
        f"{dia}.{mes}.{ano}",
        f"{dia}{mes}{ano}",
        f"{ano}-{mes}-{dia}",
        f"{dia}/{mes}/{ano[2:]}",
        f"{d.day}/{d.month}/{ano}",
    }
    return sorted(formas, key=len, reverse=True)


class Mascarador:
    """Mascara um texto no contexto de UM sujeito (o da conversa) mais as regras gerais."""

    def __init__(self, sujeito: Sujeito | None = None) -> None:
        self._datas: list[re.Pattern[str]] = []
        self._nomes: list[re.Pattern[str]] = []
        if sujeito is not None:
            self._datas = [
                re.compile(r"(?<!\d)" + re.escape(f) + r"(?!\d)") for f in _formas_da_data(sujeito)
            ]
            partes = {p for p in re.split(r"\s+", sujeito.nome.strip()) if len(p) >= 3}
            partes -= {p for p in partes if sem_acento(p) in _PARTICULAS}
            self._nomes = [
                re.compile(_padrao_de_palavra(p), re.IGNORECASE)
                for p in sorted(partes, key=len, reverse=True)
            ]

    def texto(self, valor: str) -> str:
        saida = valor
        for padrao in self._datas:
            saida = padrao.sub(TOKEN_NASCIMENTO, saida)
        # O codigo de barras ANTES do CPF: o padrao do CPF morderia um pedaco da linha digitavel.
        saida = _CODIGO_DE_BARRAS.sub(TOKEN_CODIGO_DE_BARRAS, saida)
        saida = _DIGITOS_DE_CREDENCIAL.sub(TOKEN_CODIGO_DE_BARRAS, saida)
        saida = _BOLETO_ASTERISCO.sub(TOKEN_BOLETO, saida)
        saida = _BOLETO_FINAL.sub(lambda m: m.group(1) + TOKEN_BOLETO, saida)
        saida = _CPF.sub(TOKEN_CPF, saida)
        saida = _DIGITOS_LONGOS.sub(TOKEN_NUMERO, saida)
        saida = _TELEFONE.sub(TOKEN_TELEFONE, saida)
        for padrao in self._nomes:
            saida = padrao.sub(TOKEN_NOME, saida)
        return saida

    def objeto(self, valor: Any) -> Any:
        """Mascara recursivamente strings dentro de dicionarios/listas (fatos, resultados)."""
        if isinstance(valor, str):
            return self.texto(valor)
        if isinstance(valor, Mapping):
            return {str(k): self._campo(str(k), v) for k, v in valor.items()}
        if isinstance(valor, list | tuple):
            return [self.objeto(v) for v in valor]
        return valor

    def _campo(self, chave: str, valor: Any) -> Any:
        if chave in {"numero_boleto", "boleto"} and isinstance(valor, str) and valor:
            return TOKEN_BOLETO
        return self.objeto(valor)


def contem_dado_do_sujeito(texto: str, sujeito: Sujeito) -> list[str]:
    """Que dado do sujeito aparece no texto (`cpf`, `nascimento`, `nome`)? Insumo da checagem D9."""
    achados: list[str] = []
    digitos = re.sub(r"\D", "", texto)
    if sujeito.cpf and sujeito.cpf in digitos:
        achados.append("cpf")
    mascarador = Mascarador(sujeito)
    if any(p.search(texto) for p in mascarador._datas):
        achados.append("nascimento")
    if any(p.search(texto) for p in mascarador._nomes):
        achados.append("nome")
    return achados


def mascarar_todos(textos: Iterable[str], sujeito: Sujeito | None) -> list[str]:
    m = Mascarador(sujeito)
    return [m.texto(t) for t in textos]


__all__ = [
    "TOKEN_BOLETO",
    "TOKEN_CODIGO_DE_BARRAS",
    "TOKEN_CPF",
    "TOKEN_NASCIMENTO",
    "TOKEN_NOME",
    "TOKEN_NUMERO",
    "TOKEN_TELEFONE",
    "Mascarador",
    "contem_dado_do_sujeito",
    "mascarar_todos",
    "sem_acento",
]
