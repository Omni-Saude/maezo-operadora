"""Cerca CC-01: uma falha de start NUNCA vira sucesso fabricado nem caso perdido em silencio.

O DEFEITO (auditoria de frota 2026-09-04, CC-01 / RAF-02 / LUC-05; CONFIRMED por 3 confirmadores
R1, reproduzido em 9 dos 10 agentes). Quando `start_process_idempotent` levanta `CibSevenError`,
o no `start_process` devolvia `{"process_started": False, "error": ...}` e a aresta seguinte era
INCONDICIONAL para um terminal no-op. Consequencias, todas simultaneas:

  * o `desfecho` de SUCESSO ja gravado a montante (`encaminhado_auditor`, `escalado_humano`,
    `enrollment_realizado`, ...) sobrevivia — o estado AFIRMA um fato que nao aconteceu;
  * o timer de SLA vive na instancia BPMN que nunca nasceu, entao o caso nao tem prazo, nao tem
    alerta e ninguem o procura;
  * `record_agent_error` nunca disparava (o unico sitio que o chama e o `except` do `ainvoke`, e
    a excecao havia sido engolida DENTRO do grafo), entao `MaezoAgentCrashLoop` ficava cego.

DUAS CAMADAS, ambas duraveis (mesma forma de
`test_start_process_provenance_contract.py`, a cerca irma que cobre o audit-before-effect):

  1. ESTRUTURAL (AST) — todo grafo de agente que registra um no `start_process` TEM de sair dele
     por uma aresta CONDICIONAL cujo mapa de destinos inclui `notify_start_failure`, e TEM de
     registrar esse no. Um agente novo que aterre um start com aresta incondicional quebra aqui.
  2. COMPORTAMENTAL — os 9 agentes que iniciam processo (8 com no `start_process` + helena, que
     inicia por `_start_escalation` dentro de `escalate`) sao dirigidos com um transporte cujo
     start SEMPRE levanta `CibSevenError`; o turno tem de terminar com
     `desfecho == "erro_inicio_processo"` e com `record_agent_error` chamado exatamente uma vez.

O literal `"erro_inicio_processo"` e escrito A MAO aqui, e nao importado de
`maezo.runtime.start_outcome.DESFECHO_ERRO_INICIO_PROCESSO`, DE PROPOSITO: este arquivo e a
especificacao externa do valor contratual (o mesmo literal declarado na secao "Desfecho de
agente" dos contratos SP-OP). Importar a constante faria o teste concordar com o codigo por
construcao — renomear o desfecho passaria despercebido.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import observability
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink

#: O desfecho contratual de falha de start (CC-01). Literal deliberado — ver docstring do modulo.
DESFECHO_ERRO = "erro_inicio_processo"

#: O nome do no de falha, identico em todo grafo (o que torna um alerta agregavel).
NODE_FALHA = "notify_start_failure"


# ---------------------------------------------------------------------------
# Dobradas
# ---------------------------------------------------------------------------


class _FakeInference:
    model_id = "claude-cc01-probe"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "sintetico"


class _FakeDmn:
    async def evaluate(
        self, table: str, dmn_input: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        return (
            [
                {
                    "roteamento": "SEGUE_ROTEAMENTO",
                    "recomendacao": "AUTO_APROVAR",
                    "faixa_valor": "DENTRO_TETO_L2",
                }
            ],
            DmnVersion("t", "id1", 1, "d1"),
        )


class _RecordingWhatsApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"ok": True}


class _FailingStartTransport(FakeCibSevenTransport):
    """O engine recusa o start. So `start_process_instance` muda — o chokepoint roda inteiro."""

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError(f"engine indisponivel (probe CC-01): start de {process_key}")


# ---------------------------------------------------------------------------
# Casos — derivados de `test_start_process_provenance_contract.py::_CASES` (mesmos estados
# minimos, ja provados suficientes para ALCANCAR o start sem cair num guard de no-op).
# ---------------------------------------------------------------------------

# (agent_id, GraphClass, ctor extra, no de start, no de falha, estado minimo)
_CASES: list[tuple[str, str, dict[str, Any], str, str, dict[str, Any]]] = [
    (
        "rafael",
        "RafaelGraph",
        {"fhir": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "AUTH-amh-1",
            "route": "human_auditor",
            "desfecho": "encaminhado_auditor",
        },
    ),
    (
        "carolina",
        "CarolinaGraph",
        {"fhir": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "CRED-amh-1",
            "route": "auto_route",
            "desfecho": "credenciamento_clerical",
        },
    ),
    (
        "marina",
        "MarinaGraph",
        {"fhir": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "CONTAS-amh-1",
            "route": "auto_route",
            "flow": "contas",
            "fluxo": "contas",
            "desfecho": "pagar_integral",
        },
    ),
    (
        "andre",
        "AndreGraph",
        {"fhir": None, "population": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "PAGTO-amh-1",
            "route": "auto_route",
            "flow": "pagto_dossier",
            "desfecho": "dossie_pronto_clerical",
        },
    ),
    (
        "gustavo",
        "GustavoGraph",
        {"fhir": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "NIP-amh-1",
            "process_key": "SP-OP-NIP-001",
            "route": "instruct_nip",
            "fluxo": "nip",
            "desfecho": "nip_instruida",
        },
    ),
    (
        "valentina",
        "ValentinaGraph",
        {"fhir": None},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "PROG-amh-1",
            "route": "auto_route",
            "programa": "cronicos",
            "desfecho": "enrollment_realizado",
        },
    ),
    (
        "fernando",
        "FernandoGraph",
        {"whatsapp": _RecordingWhatsApp()},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "INAD-amh-1",
            "route": "escalate",
            "desfecho": "escalado_humano",
        },
    ),
    (
        "lucas",
        "LucasGraph",
        {"whatsapp": _RecordingWhatsApp()},
        "start_process",
        NODE_FALHA,
        {
            "tenant_id": "amh",
            "business_key": "ESC-amh-1",
            "route": "escalate_human",
            "desfecho": "escalado_humano",
        },
    ),
    (
        "helena",
        "HelenaGraph",
        {"whatsapp": _RecordingWhatsApp()},
        # Helena nao tem no `start_process`: ela inicia dentro de `escalate`
        # (`_start_escalation`) e o no seguinte, `respond`, e quem fala com o beneficiario.
        "escalate",
        "respond",
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hash",
            "beneficiario_pseudo_id": "p1",
            "canal": "whatsapp",
            "escalation_motivo": "duvida_geral",
            "escalation_severidade": "leve",
        },
    ),
]

_AGENT_IDS = [c[0] for c in _CASES]

#: Os agentes cujo grafo tem literalmente um no `start_process` (helena fica de fora: ela nao
#: tem esse no — ver o comentario do caso dela acima).
_START_NODE_AGENT_IDS = [c[0] for c in _CASES if c[3] == "start_process"]


def _graph_module(agent_id: str) -> Any:
    return importlib.import_module(f"maezo.agents.{agent_id}.graph")


def _graph_class(agent_id: str, class_name: str) -> Any:
    return getattr(_graph_module(agent_id), class_name)


# ---------------------------------------------------------------------------
# 1. Cerca ESTRUTURAL (AST) — duravel sobre todo grafo presente E futuro
# ---------------------------------------------------------------------------


def _agent_graph_paths() -> list[Path]:
    root = Path(_graph_module("rafael").__file__).parent.parent
    return sorted(p for p in root.glob("*/graph.py"))


def _string_args(call: ast.Call) -> list[str]:
    return [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _calls_named(tree: ast.Module, attr: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def _mapping_values(call: ast.Call) -> set[str]:
    """Chaves + valores string do ultimo argumento posicional dict de `add_conditional_edges`."""
    out: set[str] = set()
    for arg in call.args:
        if not isinstance(arg, ast.Dict):
            continue
        for node in (*arg.keys, *arg.values):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return out


@pytest.mark.parametrize("path", _agent_graph_paths(), ids=lambda p: p.parent.name)
def test_every_start_process_node_routes_to_notify_start_failure(path: Path) -> None:
    """Todo grafo com no `start_process` sai dele por aresta CONDICIONAL ate `notify_start_failure`.

    Uma aresta INCONDICIONAL saindo de `start_process` (`g.add_edge("start_process", ...)`) e
    exatamente a forma do defeito CC-01 e e recusada aqui, mesmo que o no de falha exista.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = {name for call in _calls_named(tree, "add_node") for name in _string_args(call)[:1]}
    if "start_process" not in nodes:
        pytest.skip(f"{path.parent.name} nao registra um no `start_process`")

    assert NODE_FALHA in nodes, (
        f"{path.parent.name}: o grafo tem `start_process` mas NAO registra o no {NODE_FALHA!r} "
        "(CC-01: a falha de start precisa de um destino que grave o desfecho de erro e alerte)"
    )

    unconditional = [
        call for call in _calls_named(tree, "add_edge") if _string_args(call)[:1] == ["start_process"]
    ]
    assert not unconditional, (
        f"{path.parent.name}: `start_process` ainda tem aresta INCONDICIONAL "
        f"(add_edge) — e o defeito CC-01 na sua forma original"
    )

    conditional = [
        call
        for call in _calls_named(tree, "add_conditional_edges")
        if _string_args(call)[:1] == ["start_process"]
    ]
    assert conditional, (
        f"{path.parent.name}: nenhuma `add_conditional_edges('start_process', ...)` — a falha de "
        "start nao tem para onde desviar"
    )
    destinos: set[str] = set()
    for call in conditional:
        destinos |= _mapping_values(call)
    assert NODE_FALHA in destinos, (
        f"{path.parent.name}: o mapa de destinos da aresta condicional de `start_process` nao "
        f"inclui {NODE_FALHA!r} (destinos vistos: {sorted(destinos)})"
    )


#: `_template/graph.py::TemplateGraph.start_process` TEM um no `start_process` (HEL-12/13, o
#: contrato canonico ja carrega o padrao CC-01 desde a origem) — mas `_template` nao e um agente
#: implantado com fluxo de negocio proprio, e por isso nunca esteve em `_CASES`/
#: `_START_NODE_AGENT_IDS` (a cerca COMPORTAMENTAL exercita agentes reais com estado minimo
#: proprio a cada um). Exclusao deliberada, nao esquecimento — ver `_agent_graph_paths()`, que
#: enumera `agents/*/graph.py` sem filtrar scaffolds.
_NON_AGENT_GRAPH_DIRS = frozenset({"_template"})


def test_start_node_agent_ids_matches_ast_walk() -> None:
    """Inventario fechado: `_START_NODE_AGENT_IDS` (curado a mao em `_CASES`, usado pela cerca

    COMPORTAMENTAL abaixo) tem de ser EXATAMENTE o conjunto de agentes REAIS cujo `graph.py`
    registra um no `start_process`, apurado de forma independente pelo mesmo walk AST que
    `test_every_start_process_node_routes_to_notify_start_failure` usa (menos `_NON_AGENT_GRAPH_
    DIRS`, ver acima). Sem esta cerca, um agente novo que ganhe um no `start_process` (ou um que
    o perca) so quebraria a cerca ESTRUTURAL — a lista curada de `_CASES`/`_START_NODE_AGENT_IDS`
    da cerca COMPORTAMENTAL ficaria silenciosamente desatualizada, cobrindo de menos (ou de mais)
    sem nenhum teste acusar.
    """
    from_ast: set[str] = set()
    for path in _agent_graph_paths():
        if path.parent.name in _NON_AGENT_GRAPH_DIRS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        nodes = {name for call in _calls_named(tree, "add_node") for name in _string_args(call)[:1]}
        if "start_process" in nodes:
            from_ast.add(path.parent.name)

    assert set(_START_NODE_AGENT_IDS) == from_ast, (
        f"_START_NODE_AGENT_IDS (curado) = {sorted(_START_NODE_AGENT_IDS)} != walk AST de "
        f"`graph.py` = {sorted(from_ast)} — a cerca COMPORTAMENTAL (test_start_failure_yields_"
        "error_desfecho_and_counts_an_agent_error) esta cobrindo um conjunto de agentes diferente "
        "do que o codigo realmente tem"
    )


# ---------------------------------------------------------------------------
# 2. Cerca COMPORTAMENTAL — os 9 agentes que iniciam processo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_id", "class_name", "extra", "start_node", "failure_node", "state"),
    _CASES,
    ids=_AGENT_IDS,
)
async def test_start_failure_yields_error_desfecho_and_counts_an_agent_error(
    agent_id: str,
    class_name: str,
    extra: dict[str, Any],
    start_node: str,
    failure_node: str,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CibSevenError` no start => `desfecho == "erro_inicio_processo"` + 1 `record_agent_error`.

    O estado de entrada de cada caso ja carrega o desfecho de SUCESSO que o agente grava a
    montante — e exatamente o fato fabricado de CC-01. O teste prova que ele NAO sobrevive.
    """
    contagem: list[int] = []
    monkeypatch.setattr(observability, "record_agent_error", lambda: contagem.append(1))

    graph = _graph_class(agent_id, class_name)(
        inference=_FakeInference(),
        dmn=_FakeDmn(),
        cibseven=_FailingStartTransport(),
        audit_sink=FakeStartAuditSink(),
        **extra,
    )

    entrada = dict(state)
    assert entrada.get("desfecho") != DESFECHO_ERRO  # o caso comeca com o desfecho de sucesso

    depois_do_start = await getattr(graph, start_node)(entrada)
    assert depois_do_start.get("start_failed") is True, (
        f"{agent_id}: o no de start nao marcou `start_failed` — sem o marcador a aresta "
        "condicional nao consegue distinguir a falha tecnica de um no-op legitimo"
    )
    assert not contagem, f"{agent_id}: o no de start nao deve, ele proprio, contar o erro"

    fundido = {**entrada, **depois_do_start}
    saida = await getattr(graph, failure_node)(fundido)

    assert saida.get("desfecho") == DESFECHO_ERRO, (
        f"{agent_id}: desfecho {saida.get('desfecho')!r} — o desfecho de sucesso gravado a "
        f"montante ({entrada.get('desfecho')!r}) sobreviveu a uma instancia que nunca nasceu"
    )
    assert saida.get("process_started") is not True, f"{agent_id}: process_started afirmado apos falha"
    assert contagem == [1], (
        f"{agent_id}: record_agent_error chamado {len(contagem)}x (esperado 1) — sem ele "
        "MaezoAgentCrashLoop fica cego para esta classe inteira de falha"
    )


@pytest.mark.parametrize(
    ("agent_id", "class_name", "extra", "start_node", "failure_node", "state"),
    _CASES,
    ids=_AGENT_IDS,
)
async def test_route_after_start_sends_a_failed_start_to_the_failure_node(
    agent_id: str,
    class_name: str,
    extra: dict[str, Any],
    start_node: str,
    failure_node: str,
    state: dict[str, Any],
) -> None:
    """O predicado compartilhado roteia o estado pos-falha para `notify_start_failure`.

    Complementa a cerca AST: aquela prova que a ARESTA existe, esta prova que o PREDICADO que a
    aresta usa devolve o ramo de falha para o estado que o no de start realmente produz.
    """
    from maezo.runtime.start_outcome import route_after_start

    graph = _graph_class(agent_id, class_name)(
        inference=_FakeInference(),
        dmn=_FakeDmn(),
        cibseven=_FailingStartTransport(),
        audit_sink=FakeStartAuditSink(),
        **extra,
    )
    depois_do_start = await getattr(graph, start_node)(dict(state))
    assert route_after_start({**state, **depois_do_start}) == NODE_FALHA, agent_id


async def test_route_after_start_keeps_a_successful_start_on_the_continue_branch() -> None:
    """Simetria: sem o marcador, o predicado NUNCA desvia — inclusive quando `process_started`
    e False por um no-op LEGITIMO (Andre: fluxo sem processo, delegacao de dentro da instancia,
    `ALREADY_COMPLETED`; Lucas: rota `respond_member`). Um alarme falso aqui e como um alerta
    real de start orfao acaba ignorado."""
    from maezo.runtime.start_outcome import route_after_start

    assert route_after_start({"process_started": True}) == "continue"
    assert route_after_start({"process_started": False}) == "continue"
    assert route_after_start({}) == "continue"
    assert route_after_start({"process_started": False, "start_failed": False}) == "continue"
