"""Os TEXTOS fixos da Helena que moram no pacote de comportamento (ADR-0067 §4).

`spec/agents/helena/pacote/textos.yaml` (formato `helena-textos-v1`) traz, por chave, o texto que a Helena
manda. Trocar uma frase e' mudar o YAML: a esteira publica e o agente le' o pacote novo em ate' 60 s,
sem build e sem deploy. Sem pacote (teste, ambiente sem `MAEZO_PACOTES_URI`) vale o texto PADRAO do codigo,
que e' o mesmo aprovado.

Regras do validador (o pacote inteiro e' recusado se uma falhar; o agente fica com o ultimo pacote bom):
- so' chaves CONHECIDAS (as de `PADRAO`); chave desconhecida reprova;
- texto nao vazio, ate' 1200 caracteres;
- nenhum link (decisao do dono de 18/10/2026: a fonte e' citada so' pelo nome) — `http`, `www.` ou dominio;
- marcadores `{...}` so' os que o texto padrao da mesma chave ja' tem.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from typing import Final

from .habilidades.cobertura_fora import CHAVE_TEXTO as _CHAVE_COBERTURA
from .habilidades.cobertura_fora import TEXTO_COBERTURA_FORA

ARQUIVO_TEXTOS: Final[str] = "textos.yaml"
FORMATO_TEXTOS: Final[str] = "helena-textos-v1"
_TAMANHO_MAXIMO: Final[int] = 1200

#: Chave -> texto padrao (o aprovado). Texto novo no pacote entra AQUI primeiro (com o padrao e o teste).
PADRAO: Final[Mapping[str, str]] = {
    _CHAVE_COBERTURA: TEXTO_COBERTURA_FORA,
}

#: Link em qualquer forma: esquema, `www.` ou dominio com TLD comum (gov.br, anvisa.gov.br, site.com).
LINK: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bhttps?://\S+|\bwww\.\S+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|br|org|net|gov|saude|info|io)"
    r"(?:\.br)?\b(?:/\S*)?"
)


def _marcadores(texto: str) -> set[str]:
    return {nome for _, nome, _, _ in string.Formatter().parse(texto) if nome}


def erros_dos_textos(textos: object) -> list[str]:
    """Os motivos de recusa do mapa `textos` do pacote (nunca o conteudo do texto)."""
    if not isinstance(textos, Mapping):
        return ["textos tem de ser um mapeamento chave -> texto"]
    erros: list[str] = []
    desconhecidas = sorted(set(textos) - set(PADRAO))
    if desconhecidas:
        erros.append(f"textos: chaves desconhecidas {desconhecidas}")
    for chave, texto in textos.items():
        if chave not in PADRAO:
            continue
        if not isinstance(texto, str) or not texto.strip() or len(texto) > _TAMANHO_MAXIMO:
            erros.append(f"textos.{chave}: texto nao vazio de ate' {_TAMANHO_MAXIMO} caracteres")
            continue
        if LINK.search(texto):
            erros.append(f"textos.{chave}: sem link (a fonte vai so' pelo nome)")
        try:
            extras = _marcadores(texto) - _marcadores(PADRAO[chave])
        except ValueError:
            erros.append(f"textos.{chave}: chaves {{}} malformadas")
            continue
        if extras:
            erros.append(f"textos.{chave}: marcadores desconhecidos {sorted(extras)}")
    return erros


class TextosDaHelena:
    """O texto de cada chave: o do pacote, quando ha', senao o padrao do codigo."""

    def __init__(self, do_pacote: Mapping[str, str] | None = None) -> None:
        self._do_pacote = {k: v for k, v in (do_pacote or {}).items() if k in PADRAO and isinstance(v, str)}

    def __getitem__(self, chave: str) -> str:
        return self._do_pacote.get(chave) or PADRAO[chave]


__all__ = [
    "ARQUIVO_TEXTOS",
    "FORMATO_TEXTOS",
    "LINK",
    "PADRAO",
    "TextosDaHelena",
    "erros_dos_textos",
]
