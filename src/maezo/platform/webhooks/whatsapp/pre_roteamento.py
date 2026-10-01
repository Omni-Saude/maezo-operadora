"""Pre-roteamento DETERMINISTICO do numero unico (ADR-0062; plano `lucas-numero-unico.md` §2.3).

Roda em toda mensagem de texto, ANTES de qualquer LLM, e devolve dois booleanos:

* `pedido_humano` (P5) — a pessoa pediu para falar com alguem;
* `sinal_saude` (P2) — termo FORTE de saude ou risco.

A REGRA ESTRUTURAL que este modulo nao pode quebrar: a camada deterministica so' consegue PUXAR
para a Helena ou para um humano, nunca para o Lucas. Por isso ela so' produz sinais que BLOQUEIAM
(o handoff de cobranca) ou que ESCALAM (pedido de pessoa) — e um falso positivo vai sempre para o
lado seguro. Ela NAO decide triagem: quem decide e' a DMN da Helena.

Os lexicos moram em `spec/policies/roteamento/pre-roteamento.yaml` (versionado, `status: DRAFT`),
resolvido pelo MESMO `resolve_spec_dir()` de toda politica do repo. O carregamento e' fail-closed:
lista vazia, termo duplicado ou termo fora da forma normalizada recusam no boot, porque um lexico
que nao casa o que parece casar e' pior do que nenhum.

PHI: o texto da mensagem entra, so' dois booleanos e o NUMERO de termos casados saem. Nenhum termo
casado e nenhum trecho do texto e' devolvido, para que nada disso possa chegar a um log.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

#: Caminho relativo a `spec/`.
LEXICO_RELATIVO: Final[str] = "policies/roteamento/pre-roteamento.yaml"
STATUS_ACEITOS: Final[frozenset[str]] = frozenset({"DRAFT", "RATIFICADO"})

_NAO_PALAVRA = re.compile(r"[^0-9a-z]+")


class LexicoInvalidoError(ValueError):
    """O arquivo de lexicos nao serve: a mensagem diz o porque, nunca o texto de beneficiario."""


def normalizar(texto: str) -> str:
    """Sem acento (NFKD), caixa baixa, pontuacao vira espaco, espacos colapsados."""
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return _NAO_PALAVRA.sub(" ", sem_acento.casefold()).strip()


@dataclass(frozen=True, slots=True)
class SinaisLexicos:
    """O que o pre-roteamento viu. Sem texto, sem termo: so' o que pode ir para um log."""

    pedido_humano: bool
    sinal_saude: bool
    termos_pedido_humano: int
    termos_sinal_saude: int
    versao: str


@dataclass(frozen=True, slots=True)
class PreRoteamento:
    versao: str
    status: str
    pedido_humano_lexico: tuple[str, ...]
    sinal_saude_lexico: tuple[str, ...]

    def avaliar(self, texto: str) -> SinaisLexicos:
        alvo = f" {normalizar(texto)} "
        humano = sum(1 for termo in self.pedido_humano_lexico if f" {termo} " in alvo)
        saude = sum(1 for termo in self.sinal_saude_lexico if f" {termo} " in alvo)
        return SinaisLexicos(
            pedido_humano=humano > 0,
            sinal_saude=saude > 0,
            termos_pedido_humano=humano,
            termos_sinal_saude=saude,
            versao=self.versao,
        )


def _lista(dados: dict[str, Any], chave: str) -> tuple[str, ...]:
    bruto = dados.get(chave)
    if not isinstance(bruto, list) or not bruto:
        raise LexicoInvalidoError(f"pre-roteamento: `{chave}` ausente ou vazio")
    termos: list[str] = []
    for item in bruto:
        if not isinstance(item, str) or not item.strip():
            raise LexicoInvalidoError(f"pre-roteamento: `{chave}` tem item que nao e' texto")
        if normalizar(item) != item:
            raise LexicoInvalidoError(
                f"pre-roteamento: `{chave}` tem termo fora da forma normalizada ({item!r}) — "
                "ele nunca casaria o texto normalizado"
            )
        termos.append(item)
    if len(set(termos)) != len(termos):
        raise LexicoInvalidoError(f"pre-roteamento: `{chave}` tem termo duplicado")
    return tuple(termos)


def de_mapa(dados: Any) -> PreRoteamento:
    if not isinstance(dados, dict):
        raise LexicoInvalidoError("pre-roteamento: a raiz do arquivo nao e' um mapa")
    status = dados.get("status")
    if status not in STATUS_ACEITOS:
        raise LexicoInvalidoError(f"pre-roteamento: `status` deve ser um de {sorted(STATUS_ACEITOS)}")
    versao = dados.get("versao")
    if not isinstance(versao, str) or not versao.strip():
        raise LexicoInvalidoError("pre-roteamento: `versao` ausente")
    return PreRoteamento(
        versao=versao,
        status=str(status),
        pedido_humano_lexico=_lista(dados, "pedido_humano_lexico"),
        sinal_saude_lexico=_lista(dados, "sinal_saude_lexico"),
    )


def carregar(caminho: Path | None = None) -> PreRoteamento:
    """Carrega os lexicos; `caminho=None` resolve pelo `spec/` canonico (fail-closed)."""
    if caminho is None:
        from maezo.agents import resolve_spec_dir

        caminho = resolve_spec_dir() / LEXICO_RELATIVO
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LexicoInvalidoError(f"pre-roteamento: nao foi possivel ler ({type(exc).__name__})") from None
    return de_mapa(dados)
