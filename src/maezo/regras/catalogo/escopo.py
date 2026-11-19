"""`spec/regras/escopo.yaml`: processos no portão e o que cada um cobre (blueprint §3.2, RF-07, RF-47).

    schema: maezo-regras-escopo.v1
    processos:
      - processo: SP-OP-REEMBOLSO-001
        portao: observar            # observar | exigir — mudar e' FLIP revisado por dono qualificado
        desde_pr: null              # PR que ligou o portao (null enquanto `observar` sem flip)
        artefatos_cobertos:
          - {arquivo: <yaml>, caminho: [...]}   # toda folha sob o caminho precisa de regra
          - {arquivo: <dmn>}                     # todo <rule> de toda decisionTable precisa de regra
          - {arquivo: <bpmn>, elemento: <id>}    # o elemento precisa de regra
          - {arquivo: <py>, nome: <CONSTANTE>}   # a constante precisa de regra
          - {arquivo: <md>, secao: "<cabecalho>"}# a secao precisa de regra

Cobertura (`check_regras_cobertura.py`): cada UNIDADE coberta tem de aparecer na `fonte` de alguma
regra do catálogo do MESMO processo. Regra nova sem texto de negócio não entra (RF-07): a chave nova
sob um caminho coberto deixa o CI vermelho até ganhar entrada no catálogo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree as ET

from maezo.platform.xml_safe import DtdRefusedError, parse_xml_without_dtd
from maezo.regras.canonico import normalizar_fim_de_linha
from maezo.regras.catalogo.carregar import CatalogoYamlError, carregar_yaml, carregar_yaml_texto
from maezo.regras.catalogo.extratores import FonteError, resolver_arquivo
from maezo.regras.catalogo.repositorio import ARQUIVO_ESCOPO
from maezo.regras.catalogo.schema import Catalogo

SCHEMA_ESCOPO: Final[str] = "maezo-regras-escopo.v1"
PORTOES: Final[frozenset[str]] = frozenset({"observar", "exigir"})
_FORMAS: Final[tuple[frozenset[str], ...]] = (
    frozenset({"arquivo", "caminho"}),
    frozenset({"arquivo"}),
    frozenset({"arquivo", "elemento"}),
    frozenset({"arquivo", "nome"}),
    frozenset({"arquivo", "secao"}),
)


@dataclass(frozen=True, slots=True)
class ProcessoNoEscopo:
    processo: str
    portao: str
    desde_pr: int | None
    artefatos_cobertos: tuple[Mapping[str, Any], ...]


#: Uma unidade coberta: (arquivo, chave de localização) — a mesma forma que a fonte da regra produz.
Unidade = tuple[str, str, tuple[Any, ...]]


def validar_escopo(dados: Any) -> tuple[list[ProcessoNoEscopo], list[str]]:
    achados: list[str] = []
    if not isinstance(dados, dict) or set(dados) != {"schema", "processos"}:
        return [], [f"{ARQUIVO_ESCOPO}: tem de ser mapa com `schema` e `processos`"]
    if dados["schema"] != SCHEMA_ESCOPO:
        achados.append(f"{ARQUIVO_ESCOPO}: `schema` tem de ser {SCHEMA_ESCOPO!r}")
    if not isinstance(dados["processos"], list):
        return [], [*achados, f"{ARQUIVO_ESCOPO}: `processos` tem de ser lista"]
    saida: list[ProcessoNoEscopo] = []
    vistos: set[str] = set()
    for i, item in enumerate(dados["processos"], start=1):
        onde = f"{ARQUIVO_ESCOPO}: processos[{i}]"
        if not isinstance(item, dict) or set(item) != {
            "processo",
            "portao",
            "desde_pr",
            "artefatos_cobertos",
        }:
            achados.append(f"{onde}: chaves tem de ser processo, portao, desde_pr, artefatos_cobertos")
            continue
        processo = item["processo"]
        if not isinstance(processo, str) or processo in vistos:
            achados.append(f"{onde}: `processo` ausente ou repetido")
            continue
        vistos.add(processo)
        if item["portao"] not in PORTOES:
            achados.append(f"{onde}: `portao` tem de ser observar ou exigir")
        desde = item["desde_pr"]
        if desde is not None and (type(desde) is not int or desde < 1):
            achados.append(f"{onde}: `desde_pr` numero de PR ou null")
        if item["portao"] == "exigir" and desde is None:
            achados.append(f"{onde}: portao `exigir` exige `desde_pr` (o PR do flip)")
        cobertos = item["artefatos_cobertos"]
        if not isinstance(cobertos, list) or not cobertos:
            achados.append(f"{onde}: `artefatos_cobertos` lista nao vazia")
            continue
        for j, art in enumerate(cobertos, start=1):
            if not isinstance(art, dict) or frozenset(art) not in _FORMAS:
                achados.append(
                    f"{onde}.artefatos_cobertos[{j}]: forma fora de {[sorted(f) for f in _FORMAS]}"
                )
        saida.append(
            ProcessoNoEscopo(
                processo=processo,
                portao=str(item["portao"]),
                desde_pr=desde if type(desde) is int else None,
                artefatos_cobertos=tuple(a for a in cobertos if isinstance(a, dict)),
            )
        )
    return saida, achados


def carregar_escopo(raiz: Path) -> tuple[list[ProcessoNoEscopo], list[str]]:
    caminho = raiz / ARQUIVO_ESCOPO
    if not caminho.is_file():
        return [], [f"{ARQUIVO_ESCOPO} nao existe"]
    try:
        return validar_escopo(carregar_yaml(caminho))
    except CatalogoYamlError as exc:
        return [], [f"{ARQUIVO_ESCOPO}: {exc}"]


def _folhas(valor: Any, prefixo: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    if isinstance(valor, dict) and valor:
        saida: list[tuple[Any, ...]] = []
        for chave in valor:
            saida += _folhas(valor[chave], (*prefixo, chave))
        return saida
    return [prefixo]


def unidades(raiz: Path, artefato: Mapping[str, Any]) -> list[Unidade]:
    """As unidades cobertas por um item de `artefatos_cobertos`. `FonteError` se não resolve."""
    arquivo = str(artefato["arquivo"])
    caminho = resolver_arquivo(raiz, arquivo)
    if "caminho" in artefato:
        bruto = normalizar_fim_de_linha(caminho.read_bytes()).decode("utf-8")
        try:
            dados = carregar_yaml_texto(bruto)
        except CatalogoYamlError as exc:
            raise FonteError(f"{arquivo}: YAML recusado ({exc})") from exc
        atual = dados
        for passo in artefato["caminho"]:
            if not isinstance(atual, dict) or passo not in atual:
                raise FonteError(f"{arquivo}: caminho {artefato['caminho']!r} nao resolve")
            atual = atual[passo]
        return [(arquivo, "caminho", folha) for folha in _folhas(atual, tuple(artefato["caminho"]))]
    if set(artefato) == {"arquivo"}:
        if not arquivo.endswith(".dmn"):
            raise FonteError(f"{arquivo}: arquivo inteiro so' e' aceito para .dmn (uma unidade por <rule>)")
        try:
            arvore = parse_xml_without_dtd(normalizar_fim_de_linha(caminho.read_bytes()))
        except (DtdRefusedError, ET.ParseError) as exc:
            raise FonteError(f"{arquivo}: XML recusado ({type(exc).__name__})") from exc
        regras = [
            el.get("id", "")
            for tabela in arvore.iter()
            if tabela.tag.rsplit("}", 1)[-1] == "decisionTable"
            for el in tabela
            if el.tag.rsplit("}", 1)[-1] == "rule"
        ]
        return [(arquivo, "regra", (rid,)) for rid in regras]
    (chave,) = set(artefato) - {"arquivo"}
    return [(arquivo, chave, (artefato[chave],))]


def unidades_das_fontes(catalogo: Catalogo) -> set[Unidade]:
    saida: set[Unidade] = set()
    for regra in catalogo.regras:
        for fonte in regra.fonte:
            arquivo = str(fonte["arquivo"])
            for chave in ("caminho", "regra", "elemento", "nome", "secao"):
                if chave in fonte:
                    valor = fonte[chave]
                    saida.add((arquivo, chave, tuple(valor) if isinstance(valor, list) else (valor,)))
    return saida


def _coberta(unidade: Unidade, das_fontes: set[Unidade]) -> bool:
    if unidade in das_fontes:
        return True
    arquivo, chave, loc = unidade
    if chave != "caminho":
        return False
    # Fonte num ancestral da folha cobre a folha (o valor do ancestral inclui a folha).
    return any(a == arquivo and c == "caminho" and loc[: len(f)] == f for a, c, f in das_fontes)


def verificar_cobertura(
    raiz: Path, escopo: Sequence[ProcessoNoEscopo], catalogos: Sequence[Catalogo]
) -> list[str]:
    por_processo = {c.processo: c for c in catalogos}
    achados: list[str] = []
    for proc in escopo:
        catalogo = por_processo.get(proc.processo)
        if catalogo is None:
            achados.append(
                f"{proc.processo}: esta' em {ARQUIVO_ESCOPO} sem catalogo valido em spec/regras/catalogo/"
            )
            continue
        das_fontes = unidades_das_fontes(catalogo)
        for art in proc.artefatos_cobertos:
            try:
                lista = unidades(raiz, art)
            except FonteError as exc:
                achados.append(f"{proc.processo}: artefato coberto irresolvivel — {exc}")
                continue
            if not lista:
                achados.append(f"{proc.processo}: {art['arquivo']} coberto sem nenhuma unidade (vacuo)")
            for unidade in lista:
                if not _coberta(unidade, das_fontes):
                    arquivo, chave, loc = unidade
                    achados.append(
                        f"{proc.processo}: {arquivo} {chave}={list(loc)!r} esta' no escopo e "
                        f"NAO tem regra no "
                        f"catalogo — regra nova sem texto de negocio nao entra (RF-07)."
                    )
    return achados


__all__ = [
    "PORTOES",
    "SCHEMA_ESCOPO",
    "ProcessoNoEscopo",
    "carregar_escopo",
    "unidades",
    "unidades_das_fontes",
    "validar_escopo",
    "verificar_cobertura",
]
