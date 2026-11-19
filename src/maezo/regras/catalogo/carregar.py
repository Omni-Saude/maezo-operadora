"""Leitura dos YAML da chancela de regras: `safe_load` com chave duplicada RECUSADA.

O PyYAML aceita chave repetida em silêncio e fica com a ÚLTIMA — num catálogo de regras isso é uma
aprovação ou um `aprovam` sobrescrito sem ninguém ver. Aqui a segunda ocorrência de uma chave no
mesmo mapa é erro (falha fechada). Merge key (`<<`) também é recusada: o catálogo é escrito à mão e
revisado linha a linha; uma âncora que injeta campos esconde o que está sendo aprovado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class CatalogoYamlError(ValueError):
    """YAML ilegível, com chave duplicada ou com merge key."""


class _CarregadorEstrito(yaml.SafeLoader):
    """SafeLoader que recusa chave duplicada e `<<`."""


def _mapa_estrito(loader: _CarregadorEstrito, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
    vistos: set[Any] = set()
    for chave_no, _valor_no in node.value:
        if chave_no.tag == "tag:yaml.org,2002:merge":
            raise ConstructorError(None, None, "merge key (<<) nao e' aceita", chave_no.start_mark)
        chave = loader.construct_object(chave_no, deep=deep)
        try:
            repetida = chave in vistos
        except TypeError:
            raise ConstructorError(None, None, "chave de mapa nao hashavel", chave_no.start_mark) from None
        if repetida:
            raise ConstructorError(None, None, f"chave duplicada: {chave!r}", chave_no.start_mark)
        vistos.add(chave)
    return loader.construct_mapping(node, deep=deep)


_CarregadorEstrito.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa_estrito)


def carregar_yaml_texto(texto: str) -> Any:
    """`yaml.load` estrito sobre texto."""
    try:
        return yaml.load(texto, Loader=_CarregadorEstrito)  # noqa: S506 - SafeLoader estrito
    except yaml.YAMLError as exc:
        raise CatalogoYamlError(str(exc)) from exc


def carregar_yaml(caminho: Path) -> Any:
    """Lê `caminho` (UTF-8) com o carregador estrito."""
    try:
        texto = caminho.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogoYamlError(f"{caminho}: ilegivel ({type(exc).__name__})") from exc
    return carregar_yaml_texto(texto)


__all__ = ["CatalogoYamlError", "carregar_yaml", "carregar_yaml_texto"]
