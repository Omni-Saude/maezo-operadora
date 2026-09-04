"""Contrato canonico do `_template` — HEL-12 / HEL-13 / HEL-15.

O `_template` e o unico artefato que TODO agente novo copia. Enquanto ele nao carregar os
padroes fail-closed que helena/rafael provaram, cada agente derivado reinventa (ou omite) a
injecao de dependencias, a particao input/output do estado e o chokepoint de start.

Estes testes prendem o contrato:
  * `build(config)` existe, e fail-closed e NOMEIA as deps faltantes (HEL-13);
  * TODO `src/maezo/agents/*/graph.py::build` aceita `config` — conformidade de frota, hoje
    vermelha SO por causa do `_template` (HEL-13);
  * o `_template` usa `START`/`END` do langgraph, nao as strings `"__start__"`/`"__end__"`
    (HEL-15);
  * a particao `INPUT_FIELDS` x `NEUTRAL_OUTPUTS` cobre TODO campo do `TemplateState`, com
    guarda em import-time (padrao T1.11 anti-plantio de `rafael`/`beatriz`);
  * o start passa pelo chokepoint `start_process_idempotent` e a falha de start vira
    `desfecho="erro_inicio_processo"` em vez de sumir (CC-01).
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_SRC = _REPO_ROOT / "src" / "maezo" / "agents"
_TEMPLATE_GRAPH_SRC = _AGENTS_SRC / "_template" / "graph.py"


def _template_deps() -> dict[str, Any]:
    """As quatro deps que o contrato canonico exige (padrao `helena/graph.py::build`)."""
    return {
        "inference": object(),
        "dmn": FakeDmnTransport(),
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
    }


# =================================================================================================
# HEL-13 — `build(config)` fail-closed
# =================================================================================================


def test_template_build_accepts_config_and_names_missing_dependencies() -> None:
    """`build({})` levanta `ValueError` NOMEANDO cada dependencia obrigatoria ausente.

    O padrao e o de `helena/graph.py::build` (lista `missing` -> `ValueError`): um agente
    derivado nunca constroi um grafo que so quebraria no primeiro tool call.
    """
    from maezo.agents._template.graph import build

    params = inspect.signature(build).parameters
    assert "config" in params, f"_template build() sem parametro config: {list(params)}"

    with pytest.raises(ValueError) as excinfo:
        build({})
    message = str(excinfo.value)
    for dep in ("inference", "dmn", "cibseven", "audit_sink"):
        assert dep in message, f"dep {dep!r} nao nomeada em: {message}"


def test_template_build_without_arguments_is_also_fail_closed() -> None:
    """`build()` e `build(None)` sao o mesmo caminho fail-closed — nao um atalho silencioso."""
    from maezo.agents._template.graph import build

    with pytest.raises(ValueError, match="audit_sink"):
        build()
    with pytest.raises(ValueError, match="audit_sink"):
        build(None)


def test_template_build_with_every_dependency_returns_a_compilable_graph() -> None:
    from maezo.agents._template.graph import build

    graph = build(_template_deps())
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {"receive", "start_process", "notify_start_failure", "complete"} <= node_names


def test_every_agent_graph_build_accepts_config() -> None:
    """Conformidade de frota: TODO `maezo.agents.<id>.graph::build` recebe `config`.

    Hoje VERMELHO so por causa do `_template` — os 10 agentes nomeados ja migraram (defeito B6).
    O teste existe para que o proximo agente derivado do template nao regrida o contrato.
    """
    offenders: list[str] = []
    checked: list[str] = []
    for agent_dir in sorted(_AGENTS_SRC.iterdir()):
        if not (agent_dir / "graph.py").is_file():
            continue
        module = importlib.import_module(f"maezo.agents.{agent_dir.name}.graph")
        build_fn = getattr(module, "build", None)
        assert callable(build_fn), f"{agent_dir.name}: graph.py sem build() invocavel"
        checked.append(agent_dir.name)
        params = list(inspect.signature(build_fn).parameters)
        if not params or params[0] != "config":
            offenders.append(f"{agent_dir.name}: build{inspect.signature(build_fn)}")

    assert len(checked) >= 11, checked  # 10 agentes nomeados + `_template`
    assert not offenders, f"build() sem parametro `config`: {offenders}"


# =================================================================================================
# HEL-15 — START/END em vez de strings
# =================================================================================================


def test_template_uses_start_and_end_constants_not_string_literals() -> None:
    source = _TEMPLATE_GRAPH_SRC.read_text(encoding="utf-8")
    assert '"__start__"' not in source, "_template ainda usa a string literal __start__ (HEL-15)"
    assert '"__end__"' not in source, "_template ainda usa a string literal __end__ (HEL-15)"
    assert "from langgraph.graph import END, START, StateGraph" in source, (
        "_template deve importar START/END de langgraph.graph (HEL-15)"
    )


# =================================================================================================
# HEL-13 — particao INPUT-ONLY x OUTPUT-ONLY do estado (padrao T1.11 anti-plantio)
# =================================================================================================


def test_template_state_partition_is_complete() -> None:
    """Todo campo de `TemplateState` esta classificado em EXATAMENTE um dos dois conjuntos."""
    from maezo.agents._template.graph import (
        INPUT_FIELDS,
        NEUTRAL_OUTPUTS,
        TemplateState,
    )

    declared = frozenset(TemplateState.__annotations__)
    classified = INPUT_FIELDS | frozenset(NEUTRAL_OUTPUTS)
    assert declared == classified, (
        f"nao classificados={sorted(declared - classified)} "
        f"entradas obsoletas={sorted(classified - declared)}"
    )
    assert not (INPUT_FIELDS & frozenset(NEUTRAL_OUTPUTS)), "um campo nao pode ser input E output"
    assert INPUT_FIELDS and NEUTRAL_OUTPUTS  # nao-vacuidade


def test_template_import_time_guard_rejects_an_unclassified_field() -> None:
    """A guarda de completude e de IMPORT-TIME: reexecuta-la com um campo a mais deve explodir."""
    import maezo.agents._template.graph as template_graph

    with pytest.raises(RuntimeError, match="incompleta|incomplete"):
        template_graph._assert_state_partition_is_complete(
            declared=frozenset(template_graph.TemplateState.__annotations__) | {"campo_novo"},
            input_fields=template_graph.INPUT_FIELDS,
            neutral_outputs=template_graph.NEUTRAL_OUTPUTS,
        )


@pytest.mark.asyncio
async def test_template_receive_resets_every_output_only_field() -> None:
    """`receive` sobrescreve TODO campo output-only com seu neutro (plantio do chamador morre)."""
    from maezo.agents._template.graph import NEUTRAL_OUTPUTS, TemplateGraph

    graph = TemplateGraph(**_template_deps(), agent_version="_template@v0")
    planted = {
        "tenant_id": "amh",
        "correlation_id": "c-1",
        "desfecho": "aprovado_pelo_chamador",
        "process_started": True,
        "process_ref": {"instance_id": "forjado"},
        "error": "",
    }
    out = await graph.receive(planted)  # type: ignore[arg-type]
    for field, neutral in NEUTRAL_OUTPUTS.items():
        if field == "business_key":
            continue  # `receive` atribui a business key de verdade
        assert out[field] == neutral, f"{field} nao foi resetado: {out[field]!r}"
    assert out["business_key"]


# =================================================================================================
# CC-01 — start pelo chokepoint + fail-notify de start
# =================================================================================================


def _derived_graph(cibseven: Any) -> Any:
    """Um agente derivado: substitui os dois placeholders e implementa o unico seam abstrato."""
    from maezo.agents._template.graph import TemplateGraph

    class _Derived(TemplateGraph):
        PROCESS_KEY = "SP-OP-ESCALATION-001"
        BUSINESS_KEY_PREFIX = "ESC"

        def contract_variables(self, state: Any) -> dict[str, Any]:
            return {"motivo_categoria": "outro"}

    deps = _template_deps()
    deps["cibseven"] = cibseven
    return _Derived(**deps, agent_version="derived@v0")


def test_template_contract_variables_is_an_explicit_unbuilt_seam() -> None:
    """O esqueleto NAO devolve um dict vazio silencioso — ele recusa, nomeando o que falta."""
    from maezo.agents._template.graph import TemplateGraph

    graph = TemplateGraph(**_template_deps(), agent_version="_template@v0")
    with pytest.raises(NotImplementedError, match="contract_variables"):
        graph.contract_variables({})  # type: ignore[arg-type]


def test_template_start_goes_through_the_sanctioned_chokepoint() -> None:
    source = _TEMPLATE_GRAPH_SRC.read_text(encoding="utf-8")
    assert "start_process_idempotent" in source, (
        "_template deve demonstrar o chokepoint sancionado (check-start-process-fence)"
    )
    assert "start_process_instance" not in source, (
        "_template nunca chama o transport direto (check-start-process-fence)"
    )


@pytest.mark.asyncio
async def test_start_failure_becomes_erro_inicio_processo_instead_of_vanishing() -> None:
    """CC-01: `process_started is False` roteia para `notify_start_failure`, que escreve o
    desfecho `erro_inicio_processo` — o caso nao some sem SLA nem alerta."""
    from maezo.agents._template.graph import DESFECHO_ERRO_INICIO_PROCESSO

    class _FailingTransport(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            raise CibSevenError("engine indisponivel (teste)")

    graph = _derived_graph(_FailingTransport())
    compiled = graph.compile_graph().compile()
    final = await compiled.ainvoke({"tenant_id": "amh", "correlation_id": "c-9"})

    assert final["process_started"] is False
    assert final["desfecho"] == DESFECHO_ERRO_INICIO_PROCESSO == "erro_inicio_processo"
    assert final["error"]


@pytest.mark.asyncio
async def test_successful_start_does_not_take_the_failure_branch() -> None:
    graph = _derived_graph(FakeCibSevenTransport())
    compiled = graph.compile_graph().compile()
    final = await compiled.ainvoke({"tenant_id": "amh", "correlation_id": "c-10"})

    assert final["process_started"] is True
    assert final["business_key"] == "ESC-amh-c-10"
    assert final["desfecho"] != "erro_inicio_processo"
