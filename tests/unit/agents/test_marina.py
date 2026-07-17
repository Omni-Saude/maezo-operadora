"""Unit tests for Marina agent (Phase 2 — CONTAS/RECURSO/REEMBOLSO).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_marina_agent_yaml_exists() -> None:
    """Marina's agent.yaml must exist at spec/agents/marina/agent.yaml."""
    agent_path = _AGENTS_ROOT / "marina" / "agent.yaml"
    assert agent_path.exists(), f"Marina agent.yaml not found at {agent_path}"


def test_marina_agent_yaml_has_required_fields() -> None:
    """Marina's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "marina" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "marina"
    assert data["name"] == "Marina Andrade"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 2


def test_marina_graph_compiles() -> None:
    """Marina's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.marina.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Marina graph must be a StateGraph with compile method"


def test_marina_analyze_claim() -> None:
    """Marina's graph must include analyze_claim and prepare_dossier nodes."""
    from maezo.agents.marina.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "analyze_claim" in node_names, f"Marina graph missing 'analyze_claim'. Found: {node_names}"
    assert "prepare_dossier" in node_names, f"Marina graph missing 'prepare_dossier'. Found: {node_names}"
