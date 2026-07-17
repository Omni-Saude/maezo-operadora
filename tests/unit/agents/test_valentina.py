"""Unit tests for Valentina agent (Phase 3 — Fraud & Audit FRAUDE).

TDD London School: tests written BEFORE/ALONGSIDE implementation.

L0 HARD: Valentina NEVER auto-accuses. The test_valentina_no_auto_accusation
test verifies that no node in the graph sets decisao_fraude.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_valentina_agent_yaml_exists() -> None:
    """Valentina's agent.yaml must exist at spec/agents/valentina/agent.yaml."""
    agent_path = _AGENTS_ROOT / "valentina" / "agent.yaml"
    assert agent_path.exists(), f"Valentina agent.yaml not found at {agent_path}"


def test_valentina_agent_yaml_has_required_fields() -> None:
    """Valentina's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "valentina" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "valentina"
    assert "name" in data
    assert "role" in data
    assert "tools" in data


def test_valentina_graph_compiles() -> None:
    """Valentina's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.valentina.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Valentina graph must be a StateGraph with compile method"


def test_valentina_graph_has_required_nodes() -> None:
    """Valentina's graph must include triage_indicators, coordinate_investigation, review_dossier."""
    from maezo.agents.valentina.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "triage_indicators" in node_names, (
        f"Valentina graph missing 'triage_indicators' node. Found: {node_names}"
    )
    assert "coordinate_investigation" in node_names, (
        f"Valentina graph missing 'coordinate_investigation' node. Found: {node_names}"
    )
    assert "review_dossier" in node_names, (
        f"Valentina graph missing 'review_dossier' node. Found: {node_names}"
    )
    assert len(node_names) == 3, (
        f"Valentina graph should have exactly 3 nodes. Found {len(node_names)}: {node_names}"
    )


def test_valentina_no_auto_accusation() -> None:
    """L0 HARD: Valentina's graph must NEVER set decisao_fraude (auto-accusation).

    This test executes the full graph pipeline and verifies that no node
    produces a 'decisao_fraude' key in its output. The accusation is
    exclusively born in the human User Task UT_DecisaoInvestigador.
    """
    from maezo.agents.valentina.graph import build

    graph: StateGraph[AgentState] = build()
    compiled = graph.compile()

    # Invoke the graph with a realistic fraud investigation state
    initial_state: dict[str, Any] = {
        "messages": ["Possível fraude detectada em reembolso"],
        "numero_caso": "FRAUDE-2024-001",
        "evidencia_refs": [
            "ref://tiss/guias/12345",
            "ref://cdc/consulta/67890",
        ],
    }

    import asyncio

    result = asyncio.run(compiled.ainvoke(initial_state))  # type: ignore[arg-type]
    state_dict: dict[str, Any] = dict(result)

    # Verify that no node set decisao_fraude
    # Check all top-level keys and nested dicts
    forbidden_keys = {"decisao_fraude", "acusacao_registrada", "fraud_accusation"}

    def _check_no_accusation(data: Any, path: str = "root") -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                if key in forbidden_keys:
                    raise AssertionError(
                        f"L0 HARD VIOLATION: '{key}' found at {path}. "
                        f"Valentina MUST NEVER auto-accuse. Value: {value!r}"
                    )
                _check_no_accusation(value, f"{path}.{key}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                _check_no_accusation(item, f"{path}[{i}]")

    _check_no_accusation(state_dict)
