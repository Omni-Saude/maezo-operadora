"""Onde os arquivos da chancela moram na repo, e a leitura de todos os catálogos de uma vez."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Final

from maezo.regras.catalogo.carregar import CatalogoYamlError, carregar_yaml
from maezo.regras.catalogo.schema import Catalogo, validar_catalogo

PASTA_REGRAS: Final[str] = "spec/regras"
PASTA_CATALOGO: Final[str] = "spec/regras/catalogo"
ARQUIVO_ESCOPO: Final[str] = "spec/regras/escopo.yaml"
ARQUIVO_CONTROLE: Final[str] = "spec/regras/controle-de-alteracoes.yaml"
PASTA_RECIBOS: Final[str] = "spec/regras/recibos"


def arquivos_de_catalogo(raiz: Path) -> list[str]:
    """Caminhos relativos (POSIX) de `spec/regras/catalogo/*.yaml`, ordenados."""
    pasta = raiz / PASTA_CATALOGO
    if not pasta.is_dir():
        return []
    return [f"{PASTA_CATALOGO}/{p.name}" for p in sorted(pasta.glob("*.yaml"))]


def carregar_catalogos(raiz: Path, hoje: date | None = None) -> tuple[list[Catalogo], list[str]]:
    """Todos os catálogos válidos e os achados (catálogo com achado não entra na lista)."""
    catalogos: list[Catalogo] = []
    achados: list[str] = []
    for arquivo in arquivos_de_catalogo(raiz):
        try:
            dados = carregar_yaml(raiz / arquivo)
        except CatalogoYamlError as exc:
            achados.append(f"{arquivo}: {exc}")
            continue
        catalogo, erros = validar_catalogo(dados, arquivo=arquivo, raiz=raiz, hoje=hoje)
        achados.extend(erros)
        if catalogo is not None:
            catalogos.append(catalogo)
    return catalogos, achados


__all__ = [
    "ARQUIVO_CONTROLE",
    "ARQUIVO_ESCOPO",
    "PASTA_CATALOGO",
    "PASTA_RECIBOS",
    "PASTA_REGRAS",
    "arquivos_de_catalogo",
    "carregar_catalogos",
]
