"""Cerca do padrao: dominios FECHADOS de entrada + desfecho HONESTO de envio (Fernando).

Fecha, com uma verificacao ESTRUTURAL (AST) e nao apenas comportamental, os quatro achados da
auditoria de frota de 2026-09-04 sobre `agents/fernando/graph.py`:

* FER-05 (`INPUT-NO-ALLOWLIST`): `tipo_plano` tinha dominio fechado DOCUMENTADO e nenhuma
  allowlist — um valor plantado pelo chamador chegava as entradas da DMN, ao dossie e as
  variaveis de processo do engine LITERALMENTE.
* FER-04 (mesma familia): `canal` aceitava qualquer string; um canal sem remetente pulava o
  envio calado.
* FER-03 (`DESFECHO-DESPITE-FAILURE`): o `desfecho` de `notify` era calculado so' a partir de
  `status_inadimplencia`, ignorando o resultado REAL do envio.
* FER-10 (`SILENT-DEFAULT-MASKS-CALLER`): `origem_solicitacao` ausente virava o literal
  `"agente_fernando"` — o silencio do chamador ficava indistinguivel de uma declaracao.

POR QUE UMA CERCA DE AST E NAO SO' TESTES DE COMPORTAMENTO: os testes de `test_fernando.py`
provam os caminhos que existem HOJE. O modo de falha real desta familia e' um caminho NOVO —
mais um `state.get("tipo_plano")` cru num no futuro, mais um `or "<literal>"` numa variavel de
processo — que nenhum teste de comportamento existente cobriria. A cerca abaixo falha na hora
em que o padrao reaparece, independentemente de quem o escreveu.

Nada aqui decide nada (C3): sao asserções sobre a FORMA do modulo e sobre a coerencia entre o
codigo, o vocabulario fechado de telemetria (`runtime/turn_telemetry.py::_DESFECHO_VOCAB`) e o
CONTRATO (`docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`) — o contrato e' a fonte, o
codigo e' o espelho.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from maezo.agents.fernando import graph as fernando_graph
from maezo.runtime import turn_telemetry

_GRAPH_PATH = Path(fernando_graph.__file__)
_CONTRACT_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "docs"
    / "processes"
    / "contracts"
    / "SP-OP-INADIMPLENCIA-001.md"
)

#: Campo de estado com dominio fechado -> a UNICA funcao do modulo autorizada a le-lo cru.
#: Qualquer outra leitura tem de passar pelo normalizador (revalidacao do lado da leitura,
#: mesma disciplina que `_STATUS_ALLOW` ja aplicava a `status_inadimplencia`).
_NORMALIZADOR_POR_CAMPO: dict[str, str] = {"tipo_plano": "_tipo_plano", "canal": "_canal"}


def _module_tree() -> ast.Module:
    return ast.parse(_GRAPH_PATH.read_text(encoding="utf-8"), filename=str(_GRAPH_PATH))


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    """Mapeia cada no' ao nome da funcao mais interna que o contem (`""` = escopo de modulo)."""
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            nome = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else current
            owner[child] = nome
            walk(child, nome)

    walk(tree, "")
    return owner


def _mapping_reads(tree: ast.Module, campo: str) -> list[tuple[int, str]]:
    """Toda leitura de `<mapping>[campo]` ou `<mapping>.get(campo, ...)` no modulo."""
    owner = _enclosing_functions(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        alvo = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == campo
        ):
            alvo = node
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == campo
        ):
            alvo = node
        if alvo is not None:
            hits.append((alvo.lineno, owner.get(alvo, "")))
    return hits


@pytest.mark.parametrize(("campo", "normalizador"), sorted(_NORMALIZADOR_POR_CAMPO.items()))
def test_closed_domain_field_is_only_read_through_its_normalizer(campo: str, normalizador: str) -> None:
    """FER-04/FER-05: nenhuma leitura crua de um campo de dominio fechado fora do normalizador.

    Um `state.get("tipo_plano")` novo em qualquer no' (DMN, dossie, mensagem, variaveis de
    processo) reabre EXATAMENTE o vazamento que a auditoria plantou e provou.
    """
    tree = _module_tree()
    leituras = _mapping_reads(tree, campo)
    assert leituras, f"cerca vazia: nenhuma leitura de {campo!r} encontrada em {_GRAPH_PATH.name}"
    fora = [(linha, fn) for linha, fn in leituras if fn != normalizador]
    assert not fora, (
        f"{_GRAPH_PATH.name}: leitura CRUA de {campo!r} fora de `{normalizador}` em "
        f"{fora} — todo consumo de um campo de dominio fechado passa pelo normalizador "
        f"(revalidacao do lado da leitura, FER-04/FER-05)"
    )


def test_normalizers_reject_out_of_domain_and_pass_through_the_domain() -> None:
    """NAO-VACUIDADE dos normalizadores: dominio passa, fora do dominio vira string vazia."""
    for valor in fernando_graph._TIPO_PLANO_ALLOW:
        assert fernando_graph._tipo_plano({"tipo_plano": valor}) == valor
    assert fernando_graph._tipo_plano({"tipo_plano": "individual CPF=123.456.789-09"}) == ""
    assert fernando_graph._tipo_plano({}) == ""

    for valor in fernando_graph._CANAL_ALLOW:
        assert fernando_graph._canal({"canal": valor}) == valor
    assert fernando_graph._canal({"canal": "portal-hack"}) == ""
    assert fernando_graph._canal({}) == fernando_graph._CANAL_DEFAULT


def test_process_variables_carry_no_fabricated_literal_default() -> None:
    """FER-10: `_inadimplencia_variables` nao pode ter nenhum `<expr> or "<literal>"` — a forma
    exata que transformava a AUSENCIA de `origem_solicitacao` no fato `"agente_fernando"`.

    `or ""` continua permitido: string vazia nao afirma nada (e' a propria ausencia, normalizada
    para o tipo declarado no contrato).
    """
    tree = _module_tree()
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == "_inadimplencia_variables"
    )
    fabricados = [
        (node.lineno, ast.unparse(node))
        for node in ast.walk(fn)
        if isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and any(isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value for v in node.values)
    ]
    assert not fabricados, (
        "`_inadimplencia_variables` fabrica um valor literal para um campo ausente do chamador "
        f"(FER-10): {fabricados}"
    )


def test_notify_desfecho_tables_are_declared_in_the_closed_vocabulary() -> None:
    """CC-09 + FER-03/FER-04: todo literal de desfecho que `notify` pode emitir esta declarado
    em `_DESFECHO_VOCAB['fernando']` — um rotulo novo sem declaracao viraria `"outro"` no
    Prometheus (KPI cego), que e' a especie de defeito que CC-09 fechou."""
    vocab = turn_telemetry._DESFECHO_VOCAB["fernando"]
    for tabela in (fernando_graph._DESFECHO_NOTIFICACAO_PREVIA, fernando_graph._DESFECHO_LEMBRETE):
        assert set(tabela) == set(fernando_graph._ENTREGA_ESTADOS)
        for token in tabela.values():
            assert token in vocab, f"desfecho {token!r} nao declarado em _DESFECHO_VOCAB['fernando']"


def test_only_the_delivering_entrega_state_claims_a_send() -> None:
    """FER-03/FER-04: apenas o estado `enviada` produz um rotulo que AFIRMA envio. Os outros
    dois nunca podem terminar com o sufixo de envio (`_enviada`/`_enviado`)."""
    for tabela in (fernando_graph._DESFECHO_NOTIFICACAO_PREVIA, fernando_graph._DESFECHO_LEMBRETE):
        assert tabela["enviada"].endswith(("_enviada", "_enviado"))
        for estado in ("nao_enviada", "canal_sem_entrega"):
            assert not tabela[estado].endswith(("a_enviada", "o_enviado")), (
                f"o desfecho de {estado!r} ({tabela[estado]!r}) afirma um envio que nao ocorreu"
            )


def test_delivering_canals_are_a_subset_of_the_declared_domain() -> None:
    assert fernando_graph._CANAL_COM_ENTREGA <= fernando_graph._CANAL_ALLOW
    assert fernando_graph._CANAL_DEFAULT in fernando_graph._CANAL_ALLOW


# ---------------------------------------------------------------------------
# Contrato como FONTE do dominio (spec-first) — o codigo e' o espelho, nunca o inverso.
# ---------------------------------------------------------------------------


def _contract_domain(variavel: str) -> frozenset[str]:
    """Le o dominio declarado na tabela `## Variaveis de entrada` do contrato.

    A linha tem a forma `| \\`<variavel>\\` | <tipo> | <obrigatoria> | \\`v1\\` \\| \\`v2\\` ... |`
    — todo token entre crases DEPOIS do nome da variavel e' um valor do dominio (por isso a
    prosa daquela celula nao pode conter crases; ver a nota do contrato).
    """
    linhas = _CONTRACT_PATH.read_text(encoding="utf-8").splitlines()
    alvo = next((ln for ln in linhas if ln.startswith(f"| `{variavel}` |")), None)
    assert alvo is not None, f"contrato {_CONTRACT_PATH.name} nao declara a variavel {variavel!r}"
    tokens = re.findall(r"`([^`]+)`", alvo)
    assert tokens and tokens[0] == variavel
    return frozenset(tokens[1:])


@pytest.mark.parametrize(
    ("variavel", "allowlist_attr"),
    [("tipo_plano", "_TIPO_PLANO_ALLOW"), ("canal", "_CANAL_ALLOW")],
)
def test_allowlist_matches_the_contract_declared_domain(variavel: str, allowlist_attr: str) -> None:
    """O dominio fechado nao pode divergir do contrato do processo: a allowlist do grafo e o
    conjunto declarado em `SP-OP-INADIMPLENCIA-001.md` sao O MESMO conjunto. Sem esta cerca,
    'ampliar a allowlist' seria uma decisao de negocio tomada em Python (C3)."""
    assert getattr(fernando_graph, allowlist_attr) == _contract_domain(variavel)
