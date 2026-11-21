"""Leitor do dicionario de entradas do pacote K1 do rco (`entradas.json`; contrato-k1.md §1 e §5).

O QUE ISTO E. O K1 publica, ao lado do manifesto, `entradas.json`: uma linha por entrada de DMN, com o
nome DMN, os nomes `snake` e Maezo, o tipo, o resumo do dominio de valores, a ORIGEM declarada
(`tiss` | `cadastro` | `fora_tiss` | `derivado`), a formula estruturada da entrada derivada
(`formula.formula` + `formula.termos`, so nas derivadas posteriores ao gate PROG-RCO-15), a politica de
nulo e a derivacao do lago (K3). O manifesto pina o arquivo pelo sha256 (`dicionario_entradas
.sha256_arquivo`); `maezo.platform.rco_manifesto.anexo_pinado` confere os bytes e este modulo le o
CONTEUDO. O montador de entradas (`maezo.tools.workers.rco_montador`) e o unico consumidor.

FAIL-CLOSED (como o leitor do manifesto):
- JSON estrito (sem chave repetida, sem NaN/Infinity) e campos EXATOS no topo, em cada entrada, na
  formula, em cada termo e na derivacao do lago: campo a mais ou a menos e erro;
- `versao` do dicionario so entre `VERSOES_SUPORTADAS`. Contrato §5: entrada nova muda a versao do
  dicionario e "o consumidor precisa alimenta-la antes de aceitar o pacote" — uma versao que este
  leitor nao conhece e recusada ate o montador ser revisto. A 1.2.0 (rco PROG-RCO-10/PROG-RCO-20,
  `docs/programa/k1-entradas-origem.yaml` em f77f96ac) publica a formula de 11 derivadas e acrescenta a
  cada entrada `valores_maezo` (de-para do consumidor, `null` sem de-para; `scripts/k1_entradas.py::linhas`):
  os campos exatos dependem da versao (`CAMPOS_ENTRADA_POR_VERSAO`). A 1.4.0 (rco d9b8adfa, K1 2.3.0,
  DL-0183) tem os campos da 1.2.0, acrescenta entradas (entre elas a derivada
  `urgenciaAposVinteQuatroHorasCobertura`) e o campo OPCIONAL `formula.regimes` (`derivacao.regimes` do rco:
  regime -> tipos de guia TISS e o termo de data; `sem_regime` sem termo e com `lacuna`); a 1.3.0 nao foi
  lida por nenhum pacote pinado aqui. A 1.5.0 (rco aed187d4, PR #174) tem os campos da 1.4.0 e publica o
  valor estruturado de todo termo `parametro` (exatamente um de `valor` com `unidade`, `referencia`,
  `valor_por_contrato` ou `lacuna`; `scripts/k1_entradas.py::erros_parametro`) e o campo OPCIONAL
  `formula.referencia_por_tipo_guia` (`{guia, data, hora[, lacuna]}`; `erros_referencia_guia`). A 1.5.1
  (rco 1c5a2f2d, PR #187, PROG-RCO-38) e a 1.6.0 (rco b8444f52, PR #189, PROG-RCO-39) tem os campos da
  1.5.0, as mesmas 353 entradas e o mesmo publicador (`scripts/k1_entradas.py`, blob 54d03c50 em aed187d4
  e b8444f52): a 1.5.1 muda so o texto de `cidSecundarioInformado` e de `regime`; a 1.6.0 muda so
  `prazoComprovacaoUsoVencido` e traz, nessa formula, a chave OPCIONAL `referencia` (`arquivo#chave`,
  citacao de onde o termo e definido) em termo que NAO e `parametro` (`VERSOES_COM_REFERENCIA_EM_TERMO`;
  o rco nao a confere, o leitor a confere como `arquivo#chave`). Recortes e diferencas em
  `tests/fixtures/rco/entradas-1.6.0-recorte/`;
- `origem` no conjunto fechado; termo de formula tambem aceita `parametro` (contrato §5);
- `fallback_nulo` tem de ser `nunca_negar` em TODA entrada: o montador manda nulo quando falta o dado
  e so pode fazer isso porque o fornecedor garante que nulo nunca nega. Outra politica = recusa;
- `nome_dmn` unico.

O resumo do dominio (`"{1, 2}"`, `"faixa 0..300"`, `"padrão <regex>"`, `"tabela ..."`) e lido como o
rco o escreve (`scripts/k1_entradas.py::_resumo_dominio`). Lista truncada (`… (N valores)`) e tabela
externa nao sao verificaveis aqui: ficam `verificavel=False` e o montador so confere o tipo.

Este modulo NAO decide regra de negocio: so le e confere forma. So biblioteca padrao, sem rede.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from maezo.platform.rco_manifesto import ManifestoK1, ManifestoK1InvalidoError, anexo_pinado, json_estrito

#: Versoes do dicionario de entradas que o montador conhece (k1-entradas-origem.yaml `versao`).
VERSOES_SUPORTADAS: Final[frozenset[str]] = frozenset({"1.1.0", "1.2.0", "1.4.0", "1.5.0", "1.5.1", "1.6.0"})
ORIGENS: Final[frozenset[str]] = frozenset({"tiss", "cadastro", "fora_tiss", "derivado"})
ORIGENS_TERMO: Final[frozenset[str]] = ORIGENS | {"parametro"}
TIPOS: Final[frozenset[str]] = frozenset({"string", "integer", "boolean", "double"})
#: Unica politica de nulo aceita (contrato-k1.md §5; `k1_entradas.py::validar`).
FALLBACK_NULO: Final[str] = "nunca_negar"

CAMPOS_TOPO: Final[frozenset[str]] = frozenset({"entradas", "sha256_conteudo", "versao"})
CAMPOS_ENTRADA: Final[frozenset[str]] = frozenset(
    {
        "de_onde",
        "derivacao_lago",
        "dominio",
        "fallback_nulo",
        "formula",
        "lacuna_lago",
        "nome_dmn",
        "nome_maezo",
        "nome_snake",
        "origem",
        "tabelas",
        "tipo",
    }
)
#: Campos exatos de cada entrada por versao do dicionario: a 1.2.0 acrescenta `valores_maezo`; a 1.4.0,
#: a 1.5.0, a 1.5.1 e a 1.6.0 mantem os da 1.2.0 (`scripts/k1_entradas.py::linhas` no rco d9b8adfa,
#: aed187d4, 1c5a2f2d e b8444f52; o arquivo e o mesmo nos tres ultimos).
CAMPOS_ENTRADA_POR_VERSAO: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "1.1.0": CAMPOS_ENTRADA,
        "1.2.0": CAMPOS_ENTRADA | {"valores_maezo"},
        "1.4.0": CAMPOS_ENTRADA | {"valores_maezo"},
        "1.5.0": CAMPOS_ENTRADA | {"valores_maezo"},
        "1.5.1": CAMPOS_ENTRADA | {"valores_maezo"},
        "1.6.0": CAMPOS_ENTRADA | {"valores_maezo"},
    }
)
CAMPOS_FORMULA: Final[frozenset[str]] = frozenset({"formula", "termos"})
#: Campo opcional de `formula` a partir da 1.4.0 (`scripts/k1_entradas.py::formula_de` no rco d9b8adfa).
CAMPO_FORMULA_REGIMES: Final[str] = "regimes"
VERSOES_COM_REGIMES: Final[frozenset[str]] = frozenset({"1.4.0", "1.5.0", "1.5.1", "1.6.0"})
#: A partir da 1.5.0 (rco aed187d4): valor estruturado do termo `parametro` e a referencia por tipo de guia.
VERSOES_COM_VALOR_DE_PARAMETRO: Final[frozenset[str]] = frozenset({"1.5.0", "1.5.1", "1.6.0"})
#: A partir da 1.6.0 (rco b8444f52, `prazoComprovacaoUsoVencido`): termo que nao e `parametro` pode trazer
#: a chave opcional `referencia` (`arquivo#chave` ou lista deles), citacao de onde o termo e definido. Nao e
#: valor: o montador nunca a le. Antes da 1.6.0 a chave nesse termo e campo desconhecido (recusa).
VERSOES_COM_REFERENCIA_EM_TERMO: Final[frozenset[str]] = frozenset({"1.6.0"})
CAMPO_TERMO_REFERENCIA: Final[str] = "referencia"
CAMPO_FORMULA_REFERENCIA_GUIA: Final[str] = "referencia_por_tipo_guia"
CAMPOS_REFERENCIA_GUIA: Final[frozenset[str]] = frozenset({"guia", "data", "hora"})
#: Exatamente um destes num termo `parametro` (1.5.0; `scripts/k1_entradas.py::CAMPOS_PARAMETRO`, na ordem).
ORDEM_CAMPOS_VALOR_PARAMETRO: Final[tuple[str, ...]] = ("valor", "referencia", "valor_por_contrato", "lacuna")
CAMPOS_VALOR_PARAMETRO: Final[frozenset[str]] = frozenset(ORDEM_CAMPOS_VALOR_PARAMETRO)
#: `unidade` (so com `valor`; `scripts/k1_entradas.py::UNIDADES_PARAMETRO`).
UNIDADES_PARAMETRO: Final[frozenset[str]] = frozenset({"dias", "dias_uteis", "horas", "meses", "unidades"})
CAMPOS_REGIME: Final[frozenset[str]] = frozenset({"regime", "tipos_guia", "termo_data", "fonte"})
#: Regime sem termo de data: exige `lacuna` (o consumidor devolve nulo, nunca chuta a data).
REGIME_SEM_TERMO: Final[str] = "sem_regime"
CAMPOS_TERMO: Final[frozenset[str]] = frozenset({"nome", "origem", "fonte"})
CAMPOS_LAGO: Final[frozenset[str]] = frozenset(
    {"classe_lago", "rota", "campo_lago", "derivacao", "ressalva", "fonte"}
)

_SEMVER: Final[re.Pattern[str]] = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DIGEST: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
#: `None` num lado = faixa aberta (o rco imprime o `None` do Python quando o limite nao existe).
_FAIXA: Final[re.Pattern[str]] = re.compile(
    r"^faixa (-?[0-9]+(?:\.[0-9]+)?|None)\.\.(-?[0-9]+(?:\.[0-9]+)?|None)$"
)
_LISTA: Final[re.Pattern[str]] = re.compile(r"^\{(.*)\}$", re.DOTALL)


# --- conferencias COPIADAS do publicador (rco `scripts/k1_entradas.py` @ aed187d4; DL-0184) ---------------
# O rco nao usa expressao regular nessas conferencias: as funcoes abaixo sao as condicoes de
# `erros_parametro` e `erros_referencia_guia` daquele arquivo e commit, copiadas sem reescrever, para que o
# leitor aceite e recuse exatamente o que o publicador aceita e recusa (prova de paridade em
# `tests/unit/platform/test_rco_entradas_paridade.py`, contra a fonte do rco nesse commit).


def presente_no_publicador(valor: object) -> bool:
    """Campo PRESENTE como o rco o conta: `t.get(k) not in (None, "")` (`erros_parametro`, linha 135).

    Chave com `None` ou `""` e AUSENTE; qualquer outro valor (inclusive `0`, `False` ou lista vazia) conta."""
    return valor not in (None, "")


def referencia_arquivo_chave(ref: object) -> bool:
    """`arquivo#chave` como o rco confere (`erros_parametro`, linhas 150-152): `arq, _, chave =
    str(ref).partition("#")` com `arq` e `chave` nao vazios. A existencia do arquivo (linha 153) e conferida
    so no rco, contra a arvore dele, que o pacote nao leva."""
    arq, _, chave = str(ref).partition("#")
    return bool(arq) and bool(chave)


def elemento_xsd(valor: object) -> bool:
    """Elemento do XSD citado com `#` como o rco confere (`erros_referencia_guia`, linhas 180-181):
    `isinstance(v, str) and "#" in v` (o `None`, aceito pelo rco, e tratado por quem chama)."""
    return isinstance(valor, str) and "#" in valor


@dataclass(frozen=True, slots=True)
class DominioK1:
    """O resumo do dominio de valores, como o rco o publica.

    `tipo`: `lista` | `faixa` | `padrao` | `tabela`. `verificavel=False` quando o resumo nao permite
    conferir o valor (lista truncada, tabela externa): o montador entao so confere o tipo.
    """

    tipo: str
    verificavel: bool
    valores: tuple[str, ...] = ()  # lista: os valores como texto (`true`/`false` em minusculas)
    minimo: float | None = None
    maximo: float | None = None
    padrao: re.Pattern[str] | None = None
    texto: str = ""


@dataclass(frozen=True, slots=True)
class TermoFormulaK1:
    nome: str
    origem: str
    fonte: str
    #: Valor estruturado do termo `parametro` (dicionario 1.5.0): no maximo um preenchido (nenhum antes da
    #: 1.5.0). `valor` vem com `unidade`; `referencia` e a lista `arquivo#chave` (texto unico: tupla de um).
    #: Em termo que nao e `parametro` (dicionario 1.6.0), `referencia` e so a citacao de onde o termo e
    #: definido (`VERSOES_COM_REFERENCIA_EM_TERMO`), nunca valor.
    valor: int | float | None = None
    unidade: str | None = None
    referencia: tuple[str, ...] = ()
    valor_por_contrato: str | None = None
    lacuna: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenciaGuiaK1:
    """Um item de `formula.referencia_por_tipo_guia` (dicionario 1.5.0): o tipo de guia (texto do rco), o
    elemento do XSD da data e o da hora (`None` = a guia nao tem), e `lacuna` (obrigatoria com data nula)."""

    guia: str
    data: str | None
    hora: str | None
    lacuna: str | None = None


@dataclass(frozen=True, slots=True)
class RegimeFormulaK1:
    """Um item de `formula.regimes` (dicionario 1.4.0): o regime, os tipos de guia TISS que o definem e o
    termo de data da formula (None em `sem_regime`, que traz `lacuna`)."""

    regime: str
    tipos_guia: tuple[str, ...]
    termo_data: str | None
    fonte: str
    lacuna: str | None = None


@dataclass(frozen=True, slots=True)
class FormulaK1:
    """`formula` de uma entrada derivada: o texto da formula e os termos (nome, origem, fonte)."""

    formula: str
    termos: tuple[TermoFormulaK1, ...]
    #: `formula.regimes` (dicionario 1.4.0, opcional): vazio quando a entrada nao o declara.
    regimes: tuple[RegimeFormulaK1, ...] = ()
    #: `formula.referencia_por_tipo_guia` (dicionario 1.5.0, opcional): vazio quando a entrada nao o declara.
    referencia_por_tipo_guia: tuple[ReferenciaGuiaK1, ...] = ()


@dataclass(frozen=True, slots=True)
class EntradaK1:
    """Uma entrada do dicionario K1."""

    nome_dmn: str
    nome_snake: str
    nome_maezo: str | None
    tipo: str
    dominio: DominioK1
    origem: str
    formula: FormulaK1 | None
    tabelas: tuple[str, ...]
    lacuna_lago: bool
    classe_lago: str | None  # `derivacao_lago.classe_lago`, ou None (lacuna do lago)
    #: `valores_maezo` (dicionario 1.2.0+): de-para dos valores do Maezo para o codigo das regras, como o rco
    #: o publica (inclui a chave `base`, a citacao). `None` sem de-para ou em dicionario 1.1.0. So lido.
    valores_maezo: Mapping[str, str] | None = None

    @property
    def variavel_maezo(self) -> str:
        """Nome da variavel no Maezo: `nome_maezo` quando o dicionario o declara, senao `nome_snake`."""
        return self.nome_maezo or self.nome_snake


@dataclass(frozen=True, slots=True)
class DicionarioEntradasK1:
    versao: str
    sha256_conteudo: str
    entradas: Mapping[str, EntradaK1]  # por nome DMN


def ler_dominio(texto: str) -> DominioK1 | None:
    """Le o resumo do dominio (`scripts/k1_entradas.py::_resumo_dominio`). None = formato desconhecido."""
    if texto.startswith("tabela"):
        return DominioK1("tabela", False, texto=texto)
    if texto.startswith("padrão "):
        try:
            padrao = re.compile(texto[len("padrão ") :])
        except re.error:
            return None
        return DominioK1("padrao", True, padrao=padrao, texto=texto)
    faixa = _FAIXA.fullmatch(texto)
    if faixa is not None:
        minimo = None if faixa.group(1) == "None" else float(faixa.group(1))
        maximo = None if faixa.group(2) == "None" else float(faixa.group(2))
        if minimo is not None and maximo is not None and minimo > maximo:
            return None
        return DominioK1("faixa", True, minimo=minimo, maximo=maximo, texto=texto)
    lista = _LISTA.fullmatch(texto)
    if lista is not None:
        corpo = lista.group(1)
        truncada = "…" in corpo
        itens = [v.strip() for v in corpo.split(",")]
        valores = tuple(v for v in itens if v and not v.startswith("…"))
        if not valores:
            return None
        return DominioK1("lista", not truncada, valores=valores, texto=texto)
    return None


def _objeto(no: object, campos: frozenset[str], rotulo: str, problemas: list[str]) -> dict[str, Any] | None:
    if not isinstance(no, dict):
        problemas.append(f"{rotulo}: tem de ser objeto")
        return None
    faltam = sorted(campos - set(no))
    sobram = sorted(set(no) - campos)
    if faltam or sobram:
        problemas.append(f"{rotulo}: campos diferentes do contrato (faltam {faltam}, desconhecidos {sobram})")
        return None
    return no


def _texto(
    no: object, rotulo: str, problemas: list[str], padrao: re.Pattern[str] | None = None
) -> str | None:
    if not isinstance(no, str) or not no.strip() or (padrao is not None and padrao.fullmatch(no) is None):
        problemas.append(
            f"{rotulo}: tem de ser texto nao vazio" + (f" no padrao {padrao.pattern}" if padrao else "")
        )
        return None
    return no


def _ler_regimes(
    no: object, nomes_termos: set[str], rotulo: str, problemas: list[str]
) -> tuple[RegimeFormulaK1, ...] | None:
    """`formula.regimes` estrito: lista nao vazia; cada item com exatamente `regime`, `tipos_guia`,
    `termo_data` e `fonte` (mais `lacuna`, obrigatoria e so quando `termo_data` e null); `termo_data` e um
    termo da formula; regime e tipo de guia sem repeticao (cada tipo em exatamente um regime)."""
    if not isinstance(no, list) or not no:
        problemas.append(f"{rotulo}: tem de ser lista nao vazia")
        return None
    lidos: list[RegimeFormulaK1] = []
    regimes: set[str] = set()
    tipos: set[str] = set()
    for i, item in enumerate(no):
        onde = f"{rotulo}[{i}]"
        sem_termo = isinstance(item, dict) and item.get("termo_data") is None
        obj = _objeto(item, CAMPOS_REGIME | ({"lacuna"} if sem_termo else set()), onde, problemas)
        if obj is None:
            return None
        regime = _texto(obj["regime"], f"{onde}.regime", problemas, _NOME)
        fonte = _texto(obj["fonte"], f"{onde}.fonte", problemas)
        lacuna = _texto(obj["lacuna"], f"{onde}.lacuna", problemas) if sem_termo else None
        termo = obj["termo_data"]
        lista = obj["tipos_guia"]
        if not isinstance(lista, list) or not lista or not all(isinstance(t, str) and t for t in lista):
            problemas.append(f"{onde}.tipos_guia: tem de ser lista nao vazia de texto")
            return None
        if termo is not None and (not isinstance(termo, str) or termo not in nomes_termos):
            problemas.append(f"{onde}.termo_data: {termo!r} nao e termo da formula")
            return None
        if regime is None or fonte is None or (sem_termo and lacuna is None):
            return None
        repetidos = sorted(set(lista) & tipos) + sorted({t for t in lista if lista.count(t) > 1})
        if regime in regimes or repetidos:
            problemas.append(f"{onde}: regime ou tipo de guia repetido ({regime!r}, {repetidos})")
            return None
        regimes.add(regime)
        tipos.update(lista)
        lidos.append(RegimeFormulaK1(regime, tuple(lista), termo, fonte, lacuna))
    return tuple(lidos)


def _ler_valor_parametro(
    termo: Mapping[str, Any], rotulo: str, problemas: list[str]
) -> dict[str, Any] | None:
    """Os campos de valor de um termo `parametro` (1.5.0), como `scripts/k1_entradas.py::erros_parametro`
    (rco aed187d4): exatamente um de `CAMPOS_VALOR_PARAMETRO` PRESENTE (`presente_no_publicador`: chave com
    `None` ou `""` e ausente); `valor` numero (nao booleano) com `unidade` do vocabulario; fora do `valor`,
    `unidade` so `None`; `referencia` `arquivo#chave` ou lista deles (`referencia_arquivo_chave`, lista vazia
    aceita como no rco: o termo fica sem valor e o montador o le nulo). Mais estrito que o rco, e so
    recusando: `valor` finito; `valor_por_contrato` e `lacuna` texto nao vazio sem contar os espacos."""
    presentes = [c for c in ORDEM_CAMPOS_VALOR_PARAMETRO if presente_no_publicador(termo.get(c))]
    if len(presentes) != 1:
        problemas.append(
            f"{rotulo}: termo parametro com {presentes} "
            f"(exatamente um de {list(ORDEM_CAMPOS_VALOR_PARAMETRO)})"
        )
        return None
    (campo,) = presentes
    unidade = termo.get("unidade")
    bruto = termo[campo]
    if campo == "valor":
        if (
            isinstance(bruto, bool)
            or not isinstance(bruto, int | float)
            or not math.isfinite(bruto)
            or not isinstance(unidade, str)
            or unidade not in UNIDADES_PARAMETRO
        ):
            problemas.append(
                f"{rotulo}: `valor` tem de ser numero com `unidade` em {sorted(UNIDADES_PARAMETRO)}"
            )
            return None
        return {"valor": bruto, "unidade": unidade}
    if unidade is not None:
        problemas.append(f"{rotulo}: `unidade` so com `valor`")
        return None
    if campo == "referencia":
        lista = bruto if isinstance(bruto, list) else [bruto]
        if not all(referencia_arquivo_chave(r) for r in lista):
            problemas.append(f"{rotulo}.referencia: tem de ser `arquivo#chave` ou lista deles")
            return None
        return {"referencia": tuple(str(r) for r in lista)}
    texto = _texto(bruto, f"{rotulo}.{campo}", problemas)
    return None if texto is None else {campo: texto}


def _ler_referencia_citada(
    termo: Mapping[str, Any], rotulo: str, problemas: list[str]
) -> dict[str, Any] | None:
    """`referencia` de termo que nao e `parametro` (1.6.0): ausente = `{}`; presente, `arquivo#chave` (texto)
    ou lista nao vazia deles (`referencia_arquivo_chave`). O rco nao confere a chave nesse termo
    (`erros_formulas` so confere a de `parametro`): aqui ela e conferida e so pode RECUSAR (lado seguro)."""
    if CAMPO_TERMO_REFERENCIA not in termo:
        return {}
    bruto = termo[CAMPO_TERMO_REFERENCIA]
    lista = bruto if isinstance(bruto, list) else [bruto]
    if not lista or not all(isinstance(r, str) and referencia_arquivo_chave(r) for r in lista):
        problemas.append(f"{rotulo}.referencia: tem de ser `arquivo#chave` (texto) ou lista nao vazia deles")
        return None
    return {"referencia": tuple(lista)}


def _ler_referencias_guia(
    no: object, rotulo: str, problemas: list[str]
) -> tuple[ReferenciaGuiaK1, ...] | None:
    """`formula.referencia_por_tipo_guia` estrito (`scripts/k1_entradas.py::erros_referencia_guia`, rco
    aed187d4): lista nao vazia; cada item com exatamente `guia`, `data` e `hora` (mais a chave `lacuna`,
    opcional); `lacuna` com `None` ou `""` e ausente (`presente_no_publicador`) e e obrigatoria com data nula;
    `guia` texto sem repeticao; `data` e `hora` elemento do XSD com `#` (`elemento_xsd`) ou null. Mais
    estrito que o rco, e so recusando: `guia` e `lacuna` presente tem de ser texto nao vazio."""
    if not isinstance(no, list) or not no:
        problemas.append(f"{rotulo}: tem de ser lista nao vazia")
        return None
    lidos: list[ReferenciaGuiaK1] = []
    for i, item in enumerate(no):
        onde = f"{rotulo}[{i}]"
        com_lacuna = isinstance(item, dict) and "lacuna" in item
        obj = _objeto(item, CAMPOS_REFERENCIA_GUIA | ({"lacuna"} if com_lacuna else set()), onde, problemas)
        if obj is None:
            return None
        guia = _texto(obj["guia"], f"{onde}.guia", problemas)
        com_lacuna = com_lacuna and presente_no_publicador(obj["lacuna"])
        lacuna = _texto(obj["lacuna"], f"{onde}.lacuna", problemas) if com_lacuna else None
        elementos = (obj["data"], obj["hora"])
        if not all(e is None or elemento_xsd(e) for e in elementos):
            problemas.append(f"{onde}: `data` e `hora` tem de ser elemento do XSD com `#` ou null")
            return None
        if guia is None or (com_lacuna and lacuna is None):
            return None
        if obj["data"] is None and lacuna is None:
            problemas.append(f"{onde}: data nula exige `lacuna`")
            return None
        if any(r.guia.strip() == guia.strip() for r in lidos):  # `str(r["guia"] or "").strip()` (linha 175)
            problemas.append(f"{onde}.guia: {guia!r} repetida")
            return None
        lidos.append(ReferenciaGuiaK1(guia, obj["data"], obj["hora"], lacuna))
    return tuple(lidos)


def _ler_formula(
    no: object,
    rotulo: str,
    problemas: list[str],
    *,
    com_regimes: bool = False,
    com_valor_de_parametro: bool = False,
    com_referencia_em_termo: bool = False,
) -> FormulaK1 | None:
    opcionais = {CAMPO_FORMULA_REGIMES} if com_regimes else set()
    if com_valor_de_parametro:
        opcionais.add(CAMPO_FORMULA_REFERENCIA_GUIA)
    presentes = opcionais & set(no) if isinstance(no, dict) else set()
    obj = _objeto(no, CAMPOS_FORMULA | presentes, rotulo, problemas)
    if obj is None:
        return None
    texto = _texto(obj["formula"], f"{rotulo}.formula", problemas)
    if not isinstance(obj["termos"], list) or not obj["termos"]:
        problemas.append(f"{rotulo}.termos: tem de ser lista nao vazia")
        return None
    termos: list[TermoFormulaK1] = []
    for i, item in enumerate(obj["termos"]):
        onde = f"{rotulo}.termos[{i}]"
        parametro = com_valor_de_parametro and isinstance(item, dict) and item.get("origem") == "parametro"
        citado = com_referencia_em_termo and isinstance(item, dict) and item.get("origem") != "parametro"
        extras: frozenset[str] = frozenset()
        if parametro and isinstance(item, dict):
            extras = (CAMPOS_VALOR_PARAMETRO | {"unidade"}) & set(item)
        elif citado and isinstance(item, dict):
            extras = frozenset({CAMPO_TERMO_REFERENCIA}) & set(item)
        termo = _objeto(item, CAMPOS_TERMO | extras, onde, problemas)
        if termo is None:
            continue
        nome = _texto(termo["nome"], f"{onde}.nome", problemas, _NOME)
        fonte = _texto(termo["fonte"], f"{onde}.fonte", problemas)
        valor: dict[str, Any] | None = {}
        if parametro:
            valor = _ler_valor_parametro(termo, onde, problemas)
        elif citado:
            valor = _ler_referencia_citada(termo, onde, problemas)
        if termo["origem"] not in ORIGENS_TERMO:
            problemas.append(f"{onde}.origem: {termo['origem']!r} fora de {sorted(ORIGENS_TERMO)}")
        elif nome is not None and fonte is not None and valor is not None:
            termos.append(TermoFormulaK1(nome, str(termo["origem"]), fonte, **valor))
    if texto is None or len(termos) != len(obj["termos"]):
        return None
    referencias: tuple[ReferenciaGuiaK1, ...] = ()
    if CAMPO_FORMULA_REFERENCIA_GUIA in presentes:
        lidas = _ler_referencias_guia(
            obj[CAMPO_FORMULA_REFERENCIA_GUIA], f"{rotulo}.{CAMPO_FORMULA_REFERENCIA_GUIA}", problemas
        )
        if lidas is None:
            return None
        referencias = lidas
    regimes: tuple[RegimeFormulaK1, ...] = ()
    if CAMPO_FORMULA_REGIMES in presentes:
        lidos = _ler_regimes(
            obj[CAMPO_FORMULA_REGIMES], {t.nome for t in termos}, f"{rotulo}.regimes", problemas
        )
        if lidos is None:
            return None
        regimes = lidos
    return FormulaK1(texto, tuple(termos), regimes, referencias)


def _ler_valores_maezo(
    no: object, rotulo: str, problemas: list[str]
) -> tuple[bool, Mapping[str, str] | None]:
    """`(ok, mapa)`: `null` ou objeto de texto para texto (nao vazio)."""
    if no is None:
        return True, None
    if (
        not isinstance(no, dict)
        or not no
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in no.items())
    ):
        problemas.append(f"{rotulo}: tem de ser null ou objeto de texto para texto")
        return False, None
    return True, MappingProxyType(dict(no))


def _ler_entrada(
    no: object,
    i: int,
    problemas: list[str],
    campos: frozenset[str] = CAMPOS_ENTRADA,
    *,
    com_regimes: bool = False,
    com_valor_de_parametro: bool = False,
    com_referencia_em_termo: bool = False,
) -> EntradaK1 | None:
    onde = f"entradas[{i}]"
    obj = _objeto(no, campos, onde, problemas)
    if obj is None:
        return None
    ok = True
    nome = _texto(obj["nome_dmn"], f"{onde}.nome_dmn", problemas, _NOME)
    snake = _texto(obj["nome_snake"], f"{onde}.nome_snake", problemas, _NOME)
    maezo = obj["nome_maezo"]
    if maezo is not None and _texto(maezo, f"{onde}.nome_maezo", problemas, _NOME) is None:
        ok = False
    if obj["tipo"] not in TIPOS:
        problemas.append(f"{onde}.tipo: {obj['tipo']!r} fora de {sorted(TIPOS)}")
        ok = False
    if obj["origem"] not in ORIGENS:
        problemas.append(f"{onde}.origem: {obj['origem']!r} fora de {sorted(ORIGENS)}")
        ok = False
    if obj["fallback_nulo"] != FALLBACK_NULO:
        problemas.append(
            f"{onde}.fallback_nulo: {obj['fallback_nulo']!r} (o montador so aceita {FALLBACK_NULO!r})"
        )
        ok = False
    dominio = ler_dominio(obj["dominio"]) if isinstance(obj["dominio"], str) else None
    if dominio is None:
        problemas.append(f"{onde}.dominio: resumo de dominio desconhecido {obj['dominio']!r}")
        ok = False
    formula: FormulaK1 | None = None
    if obj["formula"] is not None:
        formula = _ler_formula(
            obj["formula"],
            f"{onde}.formula",
            problemas,
            com_regimes=com_regimes,
            com_valor_de_parametro=com_valor_de_parametro,
            com_referencia_em_termo=com_referencia_em_termo,
        )
        if formula is None:
            ok = False
        elif obj["origem"] != "derivado":
            problemas.append(f"{onde}.formula: so entrada de origem derivado tem formula")
            ok = False
    if not isinstance(obj["tabelas"], list) or not all(isinstance(t, str) and t for t in obj["tabelas"]):
        problemas.append(f"{onde}.tabelas: tem de ser lista de texto")
        ok = False
    if not isinstance(obj["lacuna_lago"], bool):
        problemas.append(f"{onde}.lacuna_lago: tem de ser booleano")
        ok = False
    classe_lago: str | None = None
    if obj["derivacao_lago"] is not None:
        lago = _objeto(obj["derivacao_lago"], CAMPOS_LAGO, f"{onde}.derivacao_lago", problemas)
        classe_lago = (
            _texto(lago["classe_lago"], f"{onde}.derivacao_lago.classe_lago", problemas) if lago else None
        )
        if classe_lago is None:
            ok = False
    if not isinstance(obj["de_onde"], str):
        problemas.append(f"{onde}.de_onde: tem de ser texto")
        ok = False
    valores_maezo: Mapping[str, str] | None = None
    if "valores_maezo" in campos:
        ok_vm, valores_maezo = _ler_valores_maezo(obj["valores_maezo"], f"{onde}.valores_maezo", problemas)
        ok = ok and ok_vm
    if not ok or nome is None or snake is None or dominio is None:
        return None
    return EntradaK1(
        nome_dmn=nome,
        nome_snake=snake,
        nome_maezo=maezo,
        tipo=str(obj["tipo"]),
        dominio=dominio,
        origem=str(obj["origem"]),
        formula=formula,
        tabelas=tuple(obj["tabelas"]),
        lacuna_lago=bool(obj["lacuna_lago"]),
        classe_lago=classe_lago,
        valores_maezo=valores_maezo,
    )


def ler_dicionario_entradas(raw: bytes) -> DicionarioEntradasK1:
    """Le e valida `entradas.json`. Levanta `ManifestoK1InvalidoError` com todos os motivos."""
    problemas: list[str] = []
    try:
        dados = json_estrito(raw)
    except ValueError as exc:  # chave repetida, NaN/Infinity, UTF-8 invalido
        raise ManifestoK1InvalidoError([f"entradas.json: JSON ilegivel ({type(exc).__name__})"]) from None
    topo = _objeto(dados, CAMPOS_TOPO, "entradas.json", problemas)
    if topo is None:
        raise ManifestoK1InvalidoError(problemas)
    versao = _texto(topo["versao"], "versao", problemas, _SEMVER)
    if versao is not None and versao not in VERSOES_SUPORTADAS:
        problemas.append(
            f"versao {versao!r} do dicionario de entradas nao suportada "
            f"(conhecidas: {sorted(VERSOES_SUPORTADAS)}); "
            "entrada nova exige rever o montador antes de pinar (contrato-k1.md §5)"
        )
    sha = _texto(topo["sha256_conteudo"], "sha256_conteudo", problemas, _DIGEST)
    campos = CAMPOS_ENTRADA_POR_VERSAO.get(versao or "", CAMPOS_ENTRADA)
    entradas: dict[str, EntradaK1] = {}
    if not isinstance(topo["entradas"], list) or not topo["entradas"]:
        problemas.append("entradas: tem de ser lista nao vazia")
    else:
        for i, item in enumerate(topo["entradas"]):
            entrada = _ler_entrada(
                item,
                i,
                problemas,
                campos,
                com_regimes=versao in VERSOES_COM_REGIMES,
                com_valor_de_parametro=versao in VERSOES_COM_VALOR_DE_PARAMETRO,
                com_referencia_em_termo=versao in VERSOES_COM_REFERENCIA_EM_TERMO,
            )
            if entrada is None:
                continue
            if entrada.nome_dmn in entradas:
                problemas.append(f"entradas[{i}]: nome_dmn {entrada.nome_dmn!r} repetido")
            entradas[entrada.nome_dmn] = entrada
    if problemas or versao is None or sha is None:
        raise ManifestoK1InvalidoError(problemas or ["entradas.json invalido"])
    return DicionarioEntradasK1(versao=versao, sha256_conteudo=sha, entradas=MappingProxyType(entradas))


def carregar_dicionario_entradas(manifesto: ManifestoK1, *, k1_dir: Path) -> DicionarioEntradasK1:
    """`entradas.json` pinado pelo manifesto, lido e CRUZADO com ele. Levanta `ManifestoK1InvalidoError`.

    Alem do sha256 do arquivo (`anexo_pinado`): `dicionario_entradas.versao`/`.sha256` do manifesto
    iguais a `versao`/`sha256_conteudo` do arquivo, e cada `entradas[]` de cada decisao com a mesma
    `origem` e o mesmo `nome_maezo` do dicionario (entrada que o dicionario nao tem tambem reprova).
    """
    dicionario = ler_dicionario_entradas(anexo_pinado(manifesto, "entradas", k1_dir=k1_dir))
    problemas: list[str] = []
    if manifesto.dicionario_entradas_versao != dicionario.versao:
        problemas.append(
            f"dicionario_entradas.versao {manifesto.dicionario_entradas_versao!r} do manifesto != "
            f"versao {dicionario.versao!r} de entradas.json"
        )
    if manifesto.dicionario_entradas_sha256 != dicionario.sha256_conteudo:
        problemas.append("dicionario_entradas.sha256 do manifesto != sha256_conteudo de entradas.json")
    for decisao in manifesto.decisoes.values():
        for nome, origem, nome_maezo in decisao.entradas_declaradas:
            entrada = dicionario.entradas.get(nome)
            if entrada is None:
                problemas.append(f"{decisao.id}: entrada {nome!r} ausente do dicionario")
            elif (origem, nome_maezo) != (entrada.origem, entrada.nome_maezo):
                problemas.append(
                    f"{decisao.id}: entrada {nome!r} com origem/nome_maezo {(origem, nome_maezo)!r} no "
                    f"manifesto e {(entrada.origem, entrada.nome_maezo)!r} no dicionario"
                )
    if problemas:
        raise ManifestoK1InvalidoError(problemas)
    return dicionario
