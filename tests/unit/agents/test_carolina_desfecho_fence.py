"""Cerca AST: em `carolina/graph.py`, todo encaminhamento humano NOMEIA o proprio `desfecho`.

POR QUE ESTA CERCA EXISTE (CAR-01, auditoria de frota 2026-09-05). Dois `return` de
`CarolinaGraph.assess` — o ramo `ANALISE_CREDENCIAMENTO` e o catch-all `ANALISE_HUMANA`/
`ANALISE_DESCREDENCIAMENTO` — encaminhavam para o humano SEM gravar `desfecho`, e `human_review`
completava a lacuna com `state.get("desfecho") or self._human_desfecho(state)`, um fallback
estatico chaveado em `direcao`. `direcao` e' o pedido do CHAMADOR; o ramo e' a decisao da DMN.
Sao eixos ortogonais, e quando divergiam (indicio de irregularidade num pedido de credenciamento,
ou uma `direcao` fora da allowlist) o MESMO caso passava a afirmar os dois ramos em campos
diferentes: `motivo_humano=analise_descredenciamento` + `grupo_humano=juridico-rede` de um lado,
`desfecho=analise_credenciamento` do outro. Era o desfecho incoerente que chegava ao contador
`maezo_agent_desfecho_total` (CC-09) e ao estado devolvido pelo handler A2A.

O QUE A CERCA PROVA, e que um teste de comportamento nao prova. Os goldens e os testes de turno
cobrem os ramos que existem HOJE; esta cerca cobre o ramo que alguem escrever AMANHA. Ela le a
arvore sintatica e exige, de cada `return` que roteia ao humano, um `desfecho` CONSTANTE decidido
ali mesmo — nunca lido do estado, nunca deduzido depois. Enquanto ela estiver verde, a classe
DESFECHO-DERIVED-FROM-WRONG-AXIS nao pode reabrir neste grafo por omissao.

ESCOPO, declarado e nao escondido: a cerca e' de carolina. `agents/marina/graph.py` e
`agents/andre/graph.py` tem HOJE o mesmo formato (`_human_desfecho` + retornos humanos sem
`desfecho` inline: 10/12 e 13/16 sitios respectivamente, medidos por AST) e sao escopo de OUTROS
WPs — uma cerca de frota aqui nasceria vermelha por causa deles e seria imediatamente afrouxada,
que e' o oposto do que uma cerca serve. Quando esses WPs fecharem, esta cerca deve ser promovida a
frota (mesma logica, `for` sobre os graphs).
"""

from __future__ import annotations

import ast
from pathlib import Path

_GRAPH_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "maezo"
    / "agents"
    / "carolina"
    / "graph.py"
)

#: Os nos que DECIDEM o encaminhamento. `human_review`/`auto_route` so' montam o dossie — nenhum
#: dos dois escreve `desfecho`, e e' exatamente essa simetria que a cerca preserva.
_ROUTING_NODES = ("receive", "assess")

#: Vocabulario de `desfecho` do agente (contrato SP-OP-CRED-001 §Desfecho de agente; espelhado em
#: `runtime/turn_telemetry.py::_DESFECHO_VOCAB["carolina"]`). `erro_inicio_processo` nasce em
#: `notify_start_failure`, via o helper unico de runtime, nunca num `_route_human`.
_DESFECHOS_DE_ROTEAMENTO = frozenset(
    {
        "analise_humana",
        "documentacao_pendente",
        "credenciamento_clerical",
        "analise_descredenciamento",
        "analise_credenciamento",
    }
)


def _module() -> ast.Module:
    return ast.parse(_GRAPH_PATH.read_text(encoding="utf-8"))


def _carolina_graph_class(module: ast.Module) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "CarolinaGraph":
            return node
    raise AssertionError("`CarolinaGraph` nao encontrada em carolina/graph.py")


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"`CarolinaGraph.{name}` nao encontrada")


def _routes_human(node: ast.Dict) -> bool:
    """O dict retornado encaminha ao humano (via `_route_human(...)` ou `route='human_review'`)?"""
    for key, value in zip(node.keys, node.values):
        if key is None and isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Attribute) and func.attr == "_route_human":
                return True
        if (
            isinstance(key, ast.Constant)
            and key.value == "route"
            and isinstance(value, ast.Constant)
            and value.value == "human_review"
        ):
            return True
    return False


def _desfecho_expr(node: ast.Dict) -> ast.expr | None:
    """A expressao que decide o `desfecho` deste retorno: chave inline OU kwarg de `_route_human`."""
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "desfecho":
            return value
        if key is None and isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Attribute) and func.attr == "_route_human":
                for kw in value.keywords:
                    if kw.arg == "desfecho":
                        return kw.value
    return None


def _human_routing_returns() -> list[tuple[str, ast.Return]]:
    cls = _carolina_graph_class(_module())
    found: list[tuple[str, ast.Return]] = []
    for name in _ROUTING_NODES:
        fn = _method(cls, name)
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                if _routes_human(node.value):
                    found.append((name, node))
    return found


def test_fence_sees_every_human_routing_return() -> None:
    """A cerca nao pode ser vacua por nao enxergar nada: `assess` tem os 5 encaminhamentos humanos
    (dmn_indisponivel x2, documentacao_pendente, ANALISE_CREDENCIAMENTO, catch-all) e `receive`
    tem o guard de contexto ausente."""
    per_node: dict[str, int] = {}
    for name, _ in _human_routing_returns():
        per_node[name] = per_node.get(name, 0) + 1
    assert per_node == {"receive": 1, "assess": 5}, per_node


def test_every_human_routing_return_names_a_constant_desfecho() -> None:
    """CAR-01: nenhum encaminhamento humano pode sair de `assess`/`receive` sem dizer QUAL e' o
    seu desfecho, e esse desfecho tem de ser uma constante do vocabulario — nunca um valor lido do
    estado (a porta por onde `direcao` entrou)."""
    faltando: list[str] = []
    nao_constante: list[str] = []
    for name, ret in _human_routing_returns():
        expr = _desfecho_expr(ret.value)  # type: ignore[arg-type]
        if expr is None:
            faltando.append(f"{name}() linha {ret.lineno}")
            continue
        if not (isinstance(expr, ast.Constant) and expr.value in _DESFECHOS_DE_ROTEAMENTO):
            nao_constante.append(f"{name}() linha {ret.lineno}: {ast.unparse(expr)}")
    assert not faltando, (
        "CAR-01: retorno(s) que encaminham ao humano sem nomear o proprio `desfecho` — o valor "
        f"acabaria deduzido a jusante, fora do ramo que o decidiu: {faltando}"
    )
    assert not nao_constante, (
        "CAR-01: `desfecho` derivado de uma expressao em vez de nomeado como constante do ramo "
        f"(era assim que `direcao` decidia o desfecho): {nao_constante}"
    )


def test_route_human_requires_the_desfecho_of_the_branch() -> None:
    """O helper que MONTA o encaminhamento humano exige o desfecho como keyword obrigatoria — e'
    a assinatura, e nao a disciplina de quem escreve, que impede um ramo novo de omiti-lo."""
    helper = _method(_carolina_graph_class(_module()), "_route_human")
    kwonly = [a.arg for a in helper.args.kwonlyargs]
    assert "desfecho" in kwonly, (
        "`_route_human` deixou de exigir `desfecho` como keyword-only obrigatoria: "
        f"kwonly={kwonly}"
    )
    defaults = helper.args.kw_defaults[kwonly.index("desfecho")]
    assert defaults is None, "`desfecho` ganhou default — um ramo novo voltaria a poder omiti-lo"


def test_no_direcao_keyed_desfecho_fallback_remains() -> None:
    """O fallback estatico chaveado em `direcao` (`_human_desfecho`) nao pode voltar a existir, nem
    como helper nem como leitura defensiva dentro de `human_review`."""
    module = _module()
    cls = _carolina_graph_class(module)
    nomes = {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_human_desfecho" not in nomes, (
        "`_human_desfecho` voltou: um desfecho deduzido de `direcao` contradiz o ramo DMN que "
        "de fato decidiu o encaminhamento (CAR-01)"
    )

    human_review = _method(cls, "human_review")
    escritas_de_desfecho = [
        ast.unparse(node)
        for node in ast.walk(human_review)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and key.value == "desfecho"
    ]
    assert not escritas_de_desfecho, (
        "`human_review` voltou a escrever `desfecho`; ele pertence ao no que DECIDE o "
        f"encaminhamento (simetria com `auto_route`, que tambem nao escreve): {escritas_de_desfecho}"
    )
