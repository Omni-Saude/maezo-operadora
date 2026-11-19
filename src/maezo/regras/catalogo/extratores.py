"""Extratores de fonte: o pedaço EXATO do artefato que uma regra aprova (blueprint §3.3).

Cada tipo de fonte tem um localizador fechado e devolve os bytes que entram no hash. Todo byte lido
passa por CRLF -> LF antes (DL-0148). Nenhum extrator importa o módulo apontado nem executa nada:
XML é lido sem DTD (`maezo.platform.xml_safe`), YAML com o carregador estrito, Python com `ast`.

Localizadores (chaves fechadas, validadas em `schema.py`):

  dmn_linha           {arquivo, regra}     C14N 2.0 do hitPolicy + todas as colunas (<input>/<output>)
                                           da decisionTable + o <rule id=regra>. Mudou coluna, todas
                                           as linhas da tabela revalidam (o significado depende delas).
  bpmn_elemento       {arquivo, elemento}  C14N 2.0 do elemento com aquele id.
  parametro_politica  {arquivo, caminho}   JSON canônico do valor no caminho YAML.
  python_constante    {arquivo, nome}      JSON canônico de `ast.literal_eval` da atribuição de módulo.
  clausula_documento  {arquivo, secao}     bytes do cabeçalho EXATO até o próximo cabeçalho de nível
                                           menor ou igual (fora de bloco de código).
  texto_beneficiario  {arquivo, caminho}   JSON canônico do TEXTO (str) no caminho YAML.

O digest de cada fonte amarra o LOCALIZADOR e o conteúdo (`fonte_sha256`): apontar a mesma regra para
outra chave com o mesmo valor também muda o hash — senão a aprovação de `teto = 0` valeria para
qualquer outro zero do arquivo.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final
from xml.etree import ElementTree as ET

from maezo.platform.xml_safe import DtdRefusedError, parse_xml_without_dtd
from maezo.regras.canonico import (
    CanonicoError,
    canonicalizar,
    normalizar_fim_de_linha,
    sha256_canonico,
    sha256_hex,
)
from maezo.regras.catalogo.carregar import CatalogoYamlError, carregar_yaml_texto

TIPOS_DE_FONTE: Final[tuple[str, ...]] = (
    "dmn_linha",
    "bpmn_elemento",
    "parametro_politica",
    "python_constante",
    "clausula_documento",
    "texto_beneficiario",
)

#: Chaves do localizador por tipo (conjunto fechado).
CHAVES_DO_LOCALIZADOR: Final[Mapping[str, frozenset[str]]] = {
    "dmn_linha": frozenset({"arquivo", "regra"}),
    "bpmn_elemento": frozenset({"arquivo", "elemento"}),
    "parametro_politica": frozenset({"arquivo", "caminho"}),
    "python_constante": frozenset({"arquivo", "nome"}),
    "clausula_documento": frozenset({"arquivo", "secao"}),
    "texto_beneficiario": frozenset({"arquivo", "caminho"}),
}

_CABECALHO: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})\s+\S")
_CERCA_DE_CODIGO: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}(```|~~~)")


class FonteError(ValueError):
    """Fonte irresolvível: arquivo, chave, elemento ou seção inexistente ou ambíguo."""


def _nome_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def resolver_arquivo(raiz: Path, arquivo: str) -> Path:
    """Caminho relativo POSIX dentro da raiz; nunca absoluto, nunca `..`."""
    posix = PurePosixPath(arquivo)
    if not arquivo or posix.is_absolute() or ".." in posix.parts or "\\" in arquivo or ":" in arquivo:
        raise FonteError(f"arquivo {arquivo!r}: tem de ser caminho relativo POSIX sem '..'")
    caminho = raiz.joinpath(*posix.parts)
    if not caminho.is_file():
        raise FonteError(f"arquivo {arquivo!r} nao existe")
    return caminho


def _ler(raiz: Path, arquivo: str) -> bytes:
    return normalizar_fim_de_linha(resolver_arquivo(raiz, arquivo).read_bytes())


def _c14n(elemento: ET.Element) -> bytes:
    texto = ET.canonicalize(ET.tostring(elemento, encoding="unicode"), strip_text=True)
    return texto.encode("utf-8")


def _xml(raiz: Path, arquivo: str) -> ET.Element:
    try:
        return parse_xml_without_dtd(_ler(raiz, arquivo))
    except (DtdRefusedError, ET.ParseError) as exc:
        raise FonteError(f"{arquivo}: XML recusado ({type(exc).__name__})") from exc


def extrair_dmn_linha(raiz: Path, arquivo: str, regra: str) -> bytes:
    arvore = _xml(raiz, arquivo)
    achadas: list[tuple[ET.Element, ET.Element]] = []
    for tabela in arvore.iter():
        if _nome_local(tabela.tag) != "decisionTable":
            continue
        for filho in tabela:
            if _nome_local(filho.tag) == "rule" and filho.get("id") == regra:
                achadas.append((tabela, filho))
    if len(achadas) != 1:
        raise FonteError(f"{arquivo}: <rule id={regra!r}> encontrada {len(achadas)} vez(es); precisa ser 1")
    tabela, linha = achadas[0]
    partes = [f"hitPolicy={tabela.get('hitPolicy', 'UNIQUE')}".encode()]
    partes += [_c14n(c) for c in tabela if _nome_local(c.tag) in ("input", "output")]
    partes.append(_c14n(linha))
    return b"\n".join(partes)


def extrair_bpmn_elemento(raiz: Path, arquivo: str, elemento: str) -> bytes:
    arvore = _xml(raiz, arquivo)
    achados = [el for el in arvore.iter() if el.get("id") == elemento]
    if len(achados) != 1:
        raise FonteError(
            f"{arquivo}: elemento id={elemento!r} encontrado {len(achados)} vez(es); precisa ser 1"
        )
    return _c14n(achados[0])


def _caminhar(dados: Any, caminho: Sequence[Any], arquivo: str) -> Any:
    atual = dados
    for passo in caminho:
        if isinstance(atual, Mapping) and isinstance(passo, str) and passo in atual:
            atual = atual[passo]
            continue
        if (
            isinstance(atual, list)
            and isinstance(passo, int)
            and type(passo) is int
            and 0 <= passo < len(atual)
        ):
            atual = atual[passo]
        else:
            raise FonteError(f"{arquivo}: caminho {list(caminho)!r} nao resolve (parou em {passo!r})")
    return atual


def _valor_yaml(raiz: Path, arquivo: str, caminho: Sequence[Any]) -> Any:
    try:
        dados = carregar_yaml_texto(_ler(raiz, arquivo).decode("utf-8"))
    except (CatalogoYamlError, UnicodeDecodeError) as exc:
        raise FonteError(f"{arquivo}: YAML recusado ({exc})") from exc
    return _caminhar(dados, caminho, arquivo)


def extrair_parametro_politica(raiz: Path, arquivo: str, caminho: Sequence[Any]) -> bytes:
    valor = _valor_yaml(raiz, arquivo, caminho)
    try:
        return canonicalizar(valor)
    except CanonicoError as exc:
        raise FonteError(f"{arquivo}: valor em {list(caminho)!r} fora do JSON canonico ({exc})") from exc


def extrair_texto_beneficiario(raiz: Path, arquivo: str, caminho: Sequence[Any]) -> bytes:
    valor = _valor_yaml(raiz, arquivo, caminho)
    if not isinstance(valor, str):
        raise FonteError(f"{arquivo}: {list(caminho)!r} nao e' texto")
    return canonicalizar(valor)


def extrair_python_constante(raiz: Path, arquivo: str, nome: str) -> bytes:
    try:
        modulo = ast.parse(_ler(raiz, arquivo).decode("utf-8"), filename=arquivo)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise FonteError(f"{arquivo}: Python ilegivel ({type(exc).__name__})") from exc
    valores: list[ast.expr] = []
    for no in modulo.body:
        if isinstance(no, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nome for t in no.targets):
            valores.append(no.value)
            continue
        if (
            isinstance(no, ast.AnnAssign)
            and isinstance(no.target, ast.Name)
            and no.target.id == nome
            and no.value is not None
        ):
            valores.append(no.value)
    if len(valores) != 1:
        raise FonteError(
            f"{arquivo}: constante de modulo {nome!r} atribuida {len(valores)} vez(es); precisa ser 1"
        )
    try:
        literal = ast.literal_eval(valores[0])
    except ValueError as exc:
        raise FonteError(f"{arquivo}: {nome} nao e' literal ({exc})") from exc
    try:
        return canonicalizar(literal)
    except CanonicoError as exc:
        raise FonteError(f"{arquivo}: {nome} fora do JSON canonico ({exc})") from exc


def extrair_clausula_documento(raiz: Path, arquivo: str, secao: str) -> bytes:
    """Do cabeçalho exato (linha inteira, ex.: `### 3.3 Teto por pedido`) ao próximo de nível ≤."""
    alvo = _CABECALHO.match(secao)
    if alvo is None:
        raise FonteError(f"secao {secao!r}: tem de ser a linha de cabecalho markdown inteira")
    nivel = len(alvo.group(1))
    try:
        linhas = _ler(raiz, arquivo).decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise FonteError(f"{arquivo}: nao e' UTF-8") from exc
    em_codigo = False
    inicio: list[int] = []
    cabecalhos: list[tuple[int, int]] = []  # (indice, nivel)
    for i, linha in enumerate(linhas):
        if _CERCA_DE_CODIGO.match(linha):
            em_codigo = not em_codigo
            continue
        if em_codigo:
            continue
        m = _CABECALHO.match(linha)
        if m is None:
            continue
        cabecalhos.append((i, len(m.group(1))))
        if linha.rstrip() == secao.rstrip():
            inicio.append(i)
    if len(inicio) != 1:
        raise FonteError(f"{arquivo}: cabecalho {secao!r} encontrado {len(inicio)} vez(es); precisa ser 1")
    fim = len(linhas)
    for indice, nivel_seguinte in cabecalhos:
        if indice > inicio[0] and nivel_seguinte <= nivel:
            fim = indice
            break
    trecho = "\n".join(linhas[inicio[0] : fim]).rstrip("\n") + "\n"
    return trecho.encode("utf-8")


def localizador(fonte: Mapping[str, Any], tipo: str) -> dict[str, Any]:
    """O localizador canônico (só as chaves do tipo) — entra no digest da fonte."""
    return {chave: fonte[chave] for chave in sorted(CHAVES_DO_LOCALIZADOR[tipo])}


def extrair(raiz: Path, tipo: str, fonte: Mapping[str, Any]) -> bytes:
    """Bytes da fonte. `fonte` já passou pela validação de forma de `schema.py`."""
    arquivo = str(fonte["arquivo"])
    if tipo == "dmn_linha":
        return extrair_dmn_linha(raiz, arquivo, str(fonte["regra"]))
    if tipo == "bpmn_elemento":
        return extrair_bpmn_elemento(raiz, arquivo, str(fonte["elemento"]))
    if tipo == "parametro_politica":
        return extrair_parametro_politica(raiz, arquivo, fonte["caminho"])
    if tipo == "python_constante":
        return extrair_python_constante(raiz, arquivo, str(fonte["nome"]))
    if tipo == "clausula_documento":
        return extrair_clausula_documento(raiz, arquivo, str(fonte["secao"]))
    if tipo == "texto_beneficiario":
        return extrair_texto_beneficiario(raiz, arquivo, fonte["caminho"])
    raise FonteError(f"tipo de fonte desconhecido: {tipo!r}")


def fonte_sha256(raiz: Path, tipo: str, fonte: Mapping[str, Any]) -> str:
    """Digest da fonte: JSON canônico de {tipo, localizador, sha256 dos bytes extraídos}."""
    conteudo = sha256_hex(extrair(raiz, tipo, fonte))
    return sha256_canonico(
        {"tipo": tipo, "localizador": localizador(fonte, tipo), "conteudo_sha256": conteudo}
    )


__all__ = [
    "CHAVES_DO_LOCALIZADOR",
    "TIPOS_DE_FONTE",
    "FonteError",
    "extrair",
    "extrair_bpmn_elemento",
    "extrair_clausula_documento",
    "extrair_dmn_linha",
    "extrair_parametro_politica",
    "extrair_python_constante",
    "extrair_texto_beneficiario",
    "fonte_sha256",
    "localizador",
    "resolver_arquivo",
]
