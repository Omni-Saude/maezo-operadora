"""Lint estatico do vocabulario de saida das tabelas DMN (MZO-1b).

Dois gates, um por pasta (`dmn.is_rco_dmn` separa as duas):

(a) **Tabelas do Maezo** (`spec/processes/dmn/*.dmn`): nenhuma saida adversa. Materializa como gate
    estatico o ADR-0018 parte 2 (`docs/adr/0018-no-denial-structural-replication.md:77-78`), emendado
    pelo ADR-0040 D3/D4: "Nenhuma `decisionTable` do processo possui saida que confirme/aceite/negue o
    efeito adverso". Ate aqui ele era guardado so por cercas escritas a mao, processo a processo
    (`tests/integration/processes/test_sp_op_*_001.py::test_*_sem_saida_adversa`, que continuam).
    Um valor e adverso quando o literal string FEEL INTEIRO, normalizado, esta na lista `adversos`.
    A unica excecao e uma `permissao` declarada no arquivo de dados, por (arquivo, saida, valor), com
    citacao; permissao que nao casa com nenhuma saida real reprova (orfa). Literais dentro de
    expressoes (`if x then "NEGAR" else ...`) contam como saida.

(b) **Tabelas do pacote K1** (`spec/processes/dmn/rco/*.dmn`, ADR-0075 (d)): vocabulario FECHADO.
    `decisao` e `classeAdverso` so aceitam os valores declarados (o vazio de `classeAdverso` nas
    formas que `k1.vazio.textos_de_saida` lista; o canonico do pacote e a string `""`,
    contrato-k1.md §6); `negar`/`glosar`/
    `recomendar_negativa` exigem `classeAdverso` e `fila` preenchidas; `classeAdverso=clinico`
    nunca acompanha `negar`. As tabelas do K1 nao passam por (a): o K1 emite o adverso por desenho,
    e quem o executa e o worker guardado (ADR-0075 (d)).

O VOCABULARIO NAO MORA AQUI. Ele e lido de `config/integrations/rco/vocabulario-saida.yaml`
(versionado, com `schema` + `versao`), que a trilha rco congela (PROG-RCO-1). Este modulo so aplica
as regras. Fail-closed: arquivo de dados ausente, malformado ou fora do schema e erro bloqueante, e
nunca um lint que passa em branco.

Toda `decision` (nas duas pastas) tem de ser exatamente uma `decisionTable`: outro corpo
(`literalExpression`, `context`...) escaparia do lint e e recusado. O arquivo de dados pode estender o
vocabulario, nunca encolher o piso do ADR-0018/ADR-0040/ADR-0075 fixado aqui (`*_MINIMOS`).

Leitura das celulas de saida (`<outputEntry><text>`):
- texto vazio/ausente ou o literal `null` -> AUSENTE (o motor devolve null nos dois casos);
- a string vazia `""` e STRING de valor vazio: e o vazio CANONICO do K1 (contrato-k1.md §6) e so
  vale como vazio de `classeAdverso` se `k1.vazio.textos_de_saida` listar `'""'` (dado, nao codigo);
- um unico literal string FEEL (`"negar"`, aspas escapadas com `\\`) -> STRING;
- qualquer outra coisa (numero, booleano, `-`, expressao FEEL) -> EXPRESSAO. Em (a) expressao nao e
  token e nao casa; em (b) `decisao`, `classeAdverso` e `fila` tem de ser literais.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ET

from . import dmn
from ._loaders import ParseError, load_xml, load_yaml, local_name
from .result import Report

#: Caminho do arquivo de dados, relativo a raiz do repositorio.
VOCABULARIO_RELPATH = Path("config") / "integrations" / "rco" / "vocabulario-saida.yaml"
#: Valor esperado da chave `schema` do arquivo de dados.
SCHEMA = "maezo.dmn.vocabulario-saida"
#: Versoes do formato que este leitor entende.
VERSOES_SUPORTADAS = frozenset({1})
#: Estados aceitos para o arquivo de dados.
ESTADOS = frozenset({"provisorio", "congelado"})

#: Piso que o arquivo de dados pode ESTENDER, nunca encolher (fail-closed contra remocao silenciosa
#: de invariante). ADR-0018:72-73,:77 e ADR-0040 D3:147-151. O token de aquiescencia a glosa fica so'
#: no arquivo de dados: em `src/` ele e' vocabulario proibido pela cerca de perspectiva (ADR-0040 D7,
#: `test_validation_perspective.py::TestSourceAndProcessDocs`), cuja lista de excecoes e' fixa.
ADVERSOS_MINIMOS = frozenset({"DENY", "NEGAR", "NEGATIVA", "INDEFERIR", "GLOSAR"})
#: ADR-0075 (d):114-117,:132 — decisao adversa do K1 tem classe e dono humano (fila).
EXIGEM_CLASSE_E_FILA_MINIMO = frozenset({"negar", "glosar", "recomendar_negativa"})
#: ADR-0075 (d):116,:215 — negativa clinica e sempre do medico auditor.
CLASSES_PROIBIDAS_MINIMAS = frozenset({("clinico", "negar")})
#: Textos de saida que podem representar o vazio do K1: outputEntry sem texto e `null` (o motor devolve
#: null) e a string vazia `""` (o vazio canonico do pacote, contrato-k1.md §6 e §11 item 3).
TEXTO_STRING_VAZIA = '""'
TEXTOS_VAZIO_ACEITOS = frozenset({"", "null", TEXTO_STRING_VAZIA})
#: Elementos de expressao DMN 1.3 que podem ser o corpo de uma `decision` alem de `decisionTable`.
_EXPRESSOES_NAO_TABELA = frozenset(
    {"literalExpression", "context", "invocation", "relation", "list", "functionDefinition"}
)

_STRING_LITERAL = re.compile(r'^"((?:[^"\\]|\\.)*)"$', re.DOTALL)
_STRING_LITERALS_IN_LIST = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)


class VocabularioInvalidoError(Exception):
    """O arquivo de dados do vocabulario nao pode ser usado (ausente, malformado, fora do schema)."""


class Tipo(Enum):
    """Como uma celula de saida foi escrita."""

    AUSENTE = "ausente"
    STRING = "string"
    EXPRESSAO = "expressao"


@dataclass(frozen=True, slots=True)
class Celula:
    """Uma celula de saida lida do XML."""

    tipo: Tipo
    texto: str
    valor: str = ""


@dataclass(frozen=True, slots=True)
class Permissao:
    """Excecao declarada a regra (a): um trio (arquivo, saida, valor) com citacao."""

    arquivo: str
    saida: str
    valor: str
    citacao: str


@dataclass(frozen=True, slots=True)
class ClasseProibida:
    """Regra (b): `classe` nunca acompanha nenhuma das `decisoes`."""

    classe: str
    decisoes: frozenset[str]
    citacao: str


@dataclass(frozen=True, slots=True)
class VocabularioK1:
    """Vocabulario fechado das tabelas sob `rco/` (ADR-0075 (d))."""

    saidas_obrigatorias: tuple[str, ...]
    decisoes: frozenset[str]
    classes: frozenset[str]
    vazio: str
    textos_vazio: frozenset[str]
    exigem_classe_e_fila: frozenset[str]
    classes_proibidas: tuple[ClasseProibida, ...]


@dataclass(frozen=True, slots=True)
class Vocabulario:
    """O arquivo de dados inteiro, ja validado."""

    versao: int
    estado: str
    adversos: frozenset[str]
    permissoes: tuple[Permissao, ...]
    k1: VocabularioK1


def normalizar(valor: str) -> str:
    """Forma canonica de um token para a comparacao de (a): maiusculas, `-`/espaco -> `_`."""
    return re.sub(r"[\s\-]+", "_", valor.strip()).upper()


# ---------------------------------------------------------------------------------------------
# Leitura do arquivo de dados
# ---------------------------------------------------------------------------------------------


def _exigir_mapa(no: object, onde: str) -> Mapping[str, object]:
    if not isinstance(no, Mapping):
        raise VocabularioInvalidoError(f"{onde}: esperado um mapa")
    return no


def _exigir_texto(no: object, onde: str) -> str:
    if not isinstance(no, str) or not no.strip():
        raise VocabularioInvalidoError(f"{onde}: esperado texto nao vazio")
    return no.strip()


def _exigir_lista_de_textos(no: object, onde: str, *, permite_vazio: bool = False) -> tuple[str, ...]:
    if not isinstance(no, list) or (not no and not permite_vazio):
        raise VocabularioInvalidoError(f"{onde}: esperada uma lista nao vazia de textos")
    itens = tuple(_exigir_texto(item, f"{onde}[{i}]") for i, item in enumerate(no))
    if len(set(itens)) != len(itens):
        raise VocabularioInvalidoError(f"{onde}: valor repetido")
    return itens


def _ler_adversos(no: object) -> frozenset[str]:
    if not isinstance(no, list) or not no:
        raise VocabularioInvalidoError("maezo.adversos: esperada uma lista nao vazia")
    vistos: set[str] = set()
    for i, item in enumerate(no):
        mapa = _exigir_mapa(item, f"maezo.adversos[{i}]")
        valor = _exigir_texto(mapa.get("valor"), f"maezo.adversos[{i}].valor")
        if normalizar(valor) != valor:
            raise VocabularioInvalidoError(
                f"maezo.adversos[{i}].valor '{valor}' nao esta na forma normalizada '{normalizar(valor)}'"
            )
        _exigir_lista_de_textos(mapa.get("fontes"), f"maezo.adversos[{i}].fontes")
        if valor in vistos:
            raise VocabularioInvalidoError(f"maezo.adversos: valor repetido '{valor}'")
        vistos.add(valor)
    faltam = ADVERSOS_MINIMOS - vistos
    if faltam:
        raise VocabularioInvalidoError(
            f"maezo.adversos nao pode remover o piso do ADR-0018/ADR-0040: faltam {sorted(faltam)}"
        )
    return frozenset(vistos)


def _ler_permissoes(no: object, adversos: frozenset[str]) -> tuple[Permissao, ...]:
    if not isinstance(no, list):
        raise VocabularioInvalidoError("maezo.permissoes: esperada uma lista (vazia se nao ha permissao)")
    permissoes: list[Permissao] = []
    for i, item in enumerate(no):
        onde = f"maezo.permissoes[{i}]"
        mapa = _exigir_mapa(item, onde)
        permissao = Permissao(
            arquivo=_exigir_texto(mapa.get("arquivo"), f"{onde}.arquivo"),
            saida=_exigir_texto(mapa.get("saida"), f"{onde}.saida"),
            valor=normalizar(_exigir_texto(mapa.get("valor"), f"{onde}.valor")),
            citacao=_exigir_texto(mapa.get("citacao"), f"{onde}.citacao"),
        )
        if permissao.valor not in adversos:
            raise VocabularioInvalidoError(
                f"{onde}: '{permissao.valor}' nao e adverso — permissao sem objeto"
            )
        if any(
            (p.arquivo, p.saida, p.valor) == (permissao.arquivo, permissao.saida, permissao.valor)
            for p in permissoes
        ):
            raise VocabularioInvalidoError(f"{onde}: permissao repetida")
        permissoes.append(permissao)
    return tuple(permissoes)


def _ler_k1(no: object) -> VocabularioK1:
    mapa = _exigir_mapa(no, "k1")
    saidas = _exigir_lista_de_textos(mapa.get("saidas_obrigatorias"), "k1.saidas_obrigatorias")
    for exigida in ("decisao", "classeAdverso", "fila"):
        if exigida not in saidas:
            raise VocabularioInvalidoError(f"k1.saidas_obrigatorias: falta '{exigida}'")
    decisoes = frozenset(_exigir_lista_de_textos(mapa.get("decisao"), "k1.decisao"))
    classes = frozenset(_exigir_lista_de_textos(mapa.get("classeAdverso"), "k1.classeAdverso"))

    vazio_mapa = _exigir_mapa(mapa.get("vazio"), "k1.vazio")
    vazio = _exigir_texto(vazio_mapa.get("valor"), "k1.vazio.valor")
    if vazio not in classes:
        raise VocabularioInvalidoError(f"k1.vazio.valor '{vazio}' nao esta em k1.classeAdverso")
    textos = vazio_mapa.get("textos_de_saida")
    if not isinstance(textos, list) or not textos or not all(isinstance(t, str) for t in textos):
        raise VocabularioInvalidoError("k1.vazio.textos_de_saida: esperada uma lista nao vazia de textos")
    textos_vazio = frozenset(t.strip() for t in textos)
    if not textos_vazio <= TEXTOS_VAZIO_ACEITOS:
        raise VocabularioInvalidoError(
            "k1.vazio.textos_de_saida: so '' (outputEntry vazio), 'null' e '\"\"' (string vazia, o vazio "
            "canonico do K1) representam o vazio"
        )

    regras = _exigir_mapa(mapa.get("regras"), "k1.regras")
    exigem = frozenset(
        _exigir_lista_de_textos(regras.get("exigem_classe_e_fila"), "k1.regras.exigem_classe_e_fila")
    )
    if not exigem <= decisoes:
        raise VocabularioInvalidoError(
            f"k1.regras.exigem_classe_e_fila fora de k1.decisao: {sorted(exigem - decisoes)}"
        )
    if not exigem >= EXIGEM_CLASSE_E_FILA_MINIMO:
        raise VocabularioInvalidoError(
            "k1.regras.exigem_classe_e_fila nao pode remover o piso do ADR-0075 (d): faltam "
            f"{sorted(EXIGEM_CLASSE_E_FILA_MINIMO - exigem)}"
        )
    proibidas_no = regras.get("classe_proibida")
    if not isinstance(proibidas_no, list):
        raise VocabularioInvalidoError("k1.regras.classe_proibida: esperada uma lista")
    proibidas: list[ClasseProibida] = []
    for i, item in enumerate(proibidas_no):
        onde = f"k1.regras.classe_proibida[{i}]"
        item_mapa = _exigir_mapa(item, onde)
        classe = _exigir_texto(item_mapa.get("classe"), f"{onde}.classe")
        if classe not in classes or classe == vazio:
            raise VocabularioInvalidoError(
                f"{onde}.classe '{classe}' nao e uma classe preenchida de k1.classeAdverso"
            )
        com = frozenset(_exigir_lista_de_textos(item_mapa.get("decisoes"), f"{onde}.decisoes"))
        if not com <= decisoes:
            raise VocabularioInvalidoError(f"{onde}.decisoes fora de k1.decisao: {sorted(com - decisoes)}")
        proibidas.append(
            ClasseProibida(classe, com, _exigir_texto(item_mapa.get("citacao"), f"{onde}.citacao"))
        )

    pares = {(p.classe, d) for p in proibidas for d in p.decisoes}
    if not pares >= CLASSES_PROIBIDAS_MINIMAS:
        raise VocabularioInvalidoError(
            "k1.regras.classe_proibida nao pode remover o piso do ADR-0075 (d):116,:215: faltam "
            f"{sorted(CLASSES_PROIBIDAS_MINIMAS - pares)}"
        )

    return VocabularioK1(
        saidas_obrigatorias=saidas,
        decisoes=decisoes,
        classes=classes,
        vazio=vazio,
        textos_vazio=textos_vazio,
        exigem_classe_e_fila=exigem,
        classes_proibidas=tuple(proibidas),
    )


def carregar(path: Path) -> Vocabulario:
    """Le e valida o arquivo de dados. Levanta `VocabularioInvalidoError` em qualquer desvio."""
    if not path.is_file():
        raise VocabularioInvalidoError("arquivo de vocabulario de saida ausente")
    try:
        bruto = load_yaml(path, reject_duplicate_keys=True)
    except ParseError as exc:
        raise VocabularioInvalidoError(str(exc)) from exc
    raiz = _exigir_mapa(bruto, "raiz")
    if raiz.get("schema") != SCHEMA:
        raise VocabularioInvalidoError(f"schema deve ser '{SCHEMA}' — got {raiz.get('schema')!r}")
    versao = raiz.get("versao")
    if not isinstance(versao, int) or isinstance(versao, bool) or versao not in VERSOES_SUPORTADAS:
        raise VocabularioInvalidoError(
            f"versao {versao!r} nao suportada (suportadas: {sorted(VERSOES_SUPORTADAS)})"
        )
    estado = raiz.get("estado")
    if estado not in ESTADOS:
        raise VocabularioInvalidoError(f"estado deve ser um de {sorted(ESTADOS)} — got {estado!r}")
    _exigir_texto(raiz.get("congelamento"), "congelamento")
    maezo = _exigir_mapa(raiz.get("maezo"), "maezo")
    adversos = _ler_adversos(maezo.get("adversos"))
    return Vocabulario(
        versao=versao,
        estado=str(estado),
        adversos=adversos,
        permissoes=_ler_permissoes(maezo.get("permissoes"), adversos),
        k1=_ler_k1(raiz.get("k1")),
    )


# ---------------------------------------------------------------------------------------------
# Leitura das tabelas
# ---------------------------------------------------------------------------------------------


def ler_celula(entry: ET.Element | None) -> Celula:
    """Classifica uma celula de saida (`outputEntry`/`defaultOutputEntry`)."""
    if entry is None:
        return Celula(Tipo.AUSENTE, "")
    text_el = next((c for c in entry if local_name(c.tag) == "text"), None)
    texto = (text_el.text or "").strip() if text_el is not None else ""
    if texto == "" or texto == "null":
        return Celula(Tipo.AUSENTE, texto)
    casou = _STRING_LITERAL.match(texto)
    if casou is not None:
        return Celula(Tipo.STRING, texto, re.sub(r"\\(.)", r"\1", casou.group(1)))
    return Celula(Tipo.EXPRESSAO, texto)


def literais(texto: str) -> list[str]:
    """Todo literal string FEEL dentro de `texto` (aspas escapadas com `\\`), ja desescapado."""
    return [re.sub(r"\\(.)", r"\1", m.group(1)) for m in _STRING_LITERALS_IN_LIST.finditer(texto)]


@dataclass(frozen=True, slots=True)
class _Tabela:
    decision_id: str
    saidas: tuple[str, ...]
    outputs: tuple[ET.Element, ...]
    regras: tuple[tuple[str, tuple[Celula, ...]], ...]


def _nome_saida(output: ET.Element, indice: int) -> str:
    return (output.get("name") or output.get("label") or output.get("id") or f"#{indice}").strip()


def _tabelas(path: Path, root: ET.Element, report: Report) -> list[_Tabela]:
    tabelas: list[_Tabela] = []
    for decision in (e for e in root.iter() if local_name(e.tag) == "decision"):
        did = (decision.get("id") or "").strip()
        corpos = [local_name(c.tag) for c in decision if local_name(c.tag) in _EXPRESSOES_NAO_TABELA]
        tabelas_da_decisao = [c for c in decision if local_name(c.tag) == "decisionTable"]
        if corpos or len(tabelas_da_decisao) != 1:
            report.error(
                path,
                f"vocabulario de saida: decisao '{did}' nao e exatamente uma decisionTable "
                f"(corpo: {corpos or 'nenhum'}) — o lint so le tabelas, entao outro corpo e recusado "
                "(fail-closed: uma literalExpression/context escaparia da checagem de saida)",
            )
            continue
        for table in tabelas_da_decisao:
            outputs = tuple(c for c in table if local_name(c.tag) == "output")
            saidas = tuple(_nome_saida(o, i) for i, o in enumerate(outputs))
            regras: list[tuple[str, tuple[Celula, ...]]] = []
            for indice, rule in enumerate(c for c in table if local_name(c.tag) == "rule"):
                rid = rule.get("id") or f"#{indice}"
                entries = [c for c in rule if local_name(c.tag) == "outputEntry"]
                if len(entries) != len(outputs):
                    report.error(
                        path,
                        f"vocabulario de saida: decisao '{did}', regra '{rid}' tem "
                        f"{len(entries)} outputEntry para {len(outputs)} output(s) — "
                        "nao da para atribuir valor a coluna",
                    )
                    continue
                regras.append((rid, tuple(ler_celula(e) for e in entries)))
            tabelas.append(_Tabela(did, saidas, outputs, tuple(regras)))
    return tabelas


# ---------------------------------------------------------------------------------------------
# (a) Tabelas do Maezo: nenhuma saida adversa
# ---------------------------------------------------------------------------------------------


def _valores_declarados(output: ET.Element) -> list[tuple[str, str]]:
    """Todo literal string de `defaultOutputEntry` e `outputValues` (expressao inclusa): (origem, valor)."""
    achados: list[tuple[str, str]] = []
    for child in output:
        nome = local_name(child.tag)
        if nome in ("defaultOutputEntry", "outputValues"):
            text_el = next((c for c in child if local_name(c.tag) == "text"), None)
            texto = (text_el.text or "") if text_el is not None else ""
            achados.extend((nome, valor) for valor in literais(texto))
    return achados


def _check_maezo(
    path: Path, tabelas: Sequence[_Tabela], vocab: Vocabulario, usadas: set[Permissao], report: Report
) -> None:
    def avaliar(did: str, saida: str, onde: str, valor: str) -> None:
        token = normalizar(valor)
        if token not in vocab.adversos:
            return
        for permissao in vocab.permissoes:
            if (permissao.arquivo, permissao.saida, permissao.valor) == (path.name, saida, token):
                usadas.add(permissao)
                return
        report.error(
            path,
            f"vocabulario de saida: decisao '{did}', saida '{saida}', {onde} emite o valor adverso "
            f"'{valor}' — ADR-0018 parte 2 (emendado por ADR-0040 D4): tabela do Maezo nao emite o "
            f"adverso, so sinaliza e roteia a humano. Excecao so por permissao declarada em "
            f"{VOCABULARIO_RELPATH.as_posix()} (maezo.permissoes)",
        )

    for tabela in tabelas:
        for saida, output in zip(tabela.saidas, tabela.outputs, strict=True):
            for origem, valor in _valores_declarados(output):
                avaliar(tabela.decision_id, saida, origem, valor)
        for rid, celulas in tabela.regras:
            for saida, celula in zip(tabela.saidas, celulas, strict=True):
                if celula.tipo is Tipo.STRING:
                    avaliar(tabela.decision_id, saida, f"regra '{rid}'", celula.valor)
                elif celula.tipo is Tipo.EXPRESSAO:
                    # Literal dentro de expressao (`if x then "NEGAR" else ...`, `["NEGAR"]`) tambem e saida.
                    for valor in literais(celula.texto):
                        avaliar(tabela.decision_id, saida, f"regra '{rid}' (dentro de expressao)", valor)


# ---------------------------------------------------------------------------------------------
# (b) Tabelas do K1: vocabulario fechado
# ---------------------------------------------------------------------------------------------


def _check_k1(path: Path, tabelas: Sequence[_Tabela], k1: VocabularioK1, report: Report) -> None:
    def erro(did: str, msg: str) -> None:
        report.error(path, f"vocabulario de saida K1 (ADR-0075 (d)): decisao '{did}': {msg}")

    for tabela in tabelas:
        did = tabela.decision_id
        faltam = [s for s in k1.saidas_obrigatorias if s not in tabela.saidas]
        if faltam:
            erro(did, f"faltam as saidas obrigatorias {faltam} (declaradas: {list(tabela.saidas)})")
            continue
        repetidas = sorted({s for s in tabela.saidas if tabela.saidas.count(s) > 1})
        if repetidas:
            erro(did, f"saida declarada mais de uma vez: {repetidas}")
            continue
        for saida, output in zip(tabela.saidas, tabela.outputs, strict=True):
            if saida not in k1.saidas_obrigatorias:
                continue
            if any(local_name(c.tag) == "defaultOutputEntry" for c in output):
                erro(
                    did,
                    f"saida '{saida}' declara defaultOutputEntry — no K1 nenhuma linha casada vai a humano "
                    "(ADR-0075 (c)); um default escaparia da checagem por linha",
                )
            dominio = {"decisao": k1.decisoes, "classeAdverso": k1.classes - {k1.vazio}}.get(saida)
            if dominio is not None:
                for _, valor in _valores_declarados(output):
                    if valor not in dominio:
                        erro(
                            did,
                            f"outputValues de '{saida}' declara '{valor}', "
                            f"fora do vocabulario {sorted(dominio)}",
                        )

        i_decisao = tabela.saidas.index("decisao")
        i_classe = tabela.saidas.index("classeAdverso")
        i_fila = tabela.saidas.index("fila")
        i_id_regra = tabela.saidas.index("idRegra") if "idRegra" in tabela.saidas else None
        for rid, celulas in tabela.regras:
            onde = f"regra '{rid}'"
            decisao, classe, fila = celulas[i_decisao], celulas[i_classe], celulas[i_fila]

            valor_decisao: str | None = None
            if decisao.tipo is Tipo.STRING and decisao.valor in k1.decisoes:
                valor_decisao = decisao.valor
            elif decisao.tipo is Tipo.AUSENTE:
                erro(did, f"{onde}: decisao vazia — decisao e obrigatoria e '{k1.vazio}' nao e decisao")
            else:
                erro(
                    did, f"{onde}: decisao {decisao.texto} fora do vocabulario fechado {sorted(k1.decisoes)}"
                )

            valor_classe: str | None = None  # None = vazio
            classe_ok = True
            if classe.tipo is Tipo.STRING and classe.valor in k1.classes and classe.valor != k1.vazio:
                valor_classe = classe.valor
            elif classe.tipo is Tipo.AUSENTE and classe.texto in k1.textos_vazio:
                valor_classe = None
            elif classe.tipo is Tipo.STRING and classe.valor == "" and TEXTO_STRING_VAZIA in k1.textos_vazio:
                valor_classe = None  # `""`: o vazio canonico do pacote K1 (contrato-k1.md §6)
            else:
                classe_ok = False
                erro(
                    did,
                    f"{onde}: classeAdverso {classe.texto or '(vazio)'} fora do vocabulario "
                    f"{sorted(k1.classes)} — '{k1.vazio}' se escreve como "
                    f"{' ou '.join(t if t else 'outputEntry vazio' for t in sorted(k1.textos_vazio))}",
                )

            fila_preenchida = False
            if fila.tipo is Tipo.STRING:
                fila_preenchida = bool(fila.valor.strip())
            elif fila.tipo is Tipo.EXPRESSAO:
                erro(did, f"{onde}: fila {fila.texto} nao e literal string nem vazio")

            if valor_decisao in k1.exigem_classe_e_fila:
                if classe_ok and valor_classe is None:
                    erro(did, f"{onde}: decisao '{valor_decisao}' exige classeAdverso preenchida")
                if not fila_preenchida:
                    erro(did, f"{onde}: decisao '{valor_decisao}' exige fila preenchida (dono humano, K6)")
                if i_id_regra is not None:
                    id_regra = celulas[i_id_regra]
                    if id_regra.tipo is not Tipo.STRING or not id_regra.valor.strip():
                        erro(
                            did,
                            f"{onde}: decisao '{valor_decisao}' exige idRegra preenchido "
                            '(contrato-k1.md §6, combinacao.adversa_exige_preenchidos; `""` so vale em '
                            "linha nao adversa)",
                        )
            for proibida in k1.classes_proibidas:
                if valor_classe == proibida.classe and valor_decisao in proibida.decisoes:
                    erro(
                        did,
                        f"{onde}: classeAdverso '{proibida.classe}' com decisao "
                        f"'{valor_decisao}' e proibido — {proibida.citacao}",
                    )


# ---------------------------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------------------------


def check(dmn_files: Iterable[Path], vocabulario_path: Path, report: Report) -> None:
    """Aplica (a) as tabelas do Maezo e (b) as do K1, lendo o vocabulario de `vocabulario_path`.

    Arquivo de dados invalido e um erro bloqueante unico (nada e checado contra um vocabulario
    que nao se pode ler). `.dmn` ilegivel ja e reportado por `dmn.validate_file`; aqui e pulado.
    """
    try:
        vocab = carregar(vocabulario_path)
    except VocabularioInvalidoError as exc:
        report.error(vocabulario_path, f"vocabulario de saida invalido: {exc}")
        return

    usadas: set[Permissao] = set()
    for path in dmn_files:
        try:
            root = load_xml(path)
        except ParseError:
            continue
        tabelas = _tabelas(path, root, report)
        if dmn.is_rco_dmn(path):
            _check_k1(path, tabelas, vocab.k1, report)
        else:
            _check_maezo(path, tabelas, vocab, usadas, report)

    for permissao in vocab.permissoes:
        rotulo = f"{permissao.arquivo}/{permissao.saida}={permissao.valor}"
        if permissao in usadas:
            report.notice(f"vocabulario de saida: permissao {rotulo} usada ({permissao.citacao})")
        else:
            report.error(
                vocabulario_path,
                f"vocabulario de saida: permissao orfa {rotulo} — nenhuma saida de tabela do primeiro nivel "
                "a usa; remova-a (excecao que nao excetua nada so esconde regressao)",
            )
