"""Unit tests for Lucas agent (Phase 2 — CANCEL/INADIMPLENCIA, Beneficiary Experience).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "maezo" / "agents"


def test_lucas_agent_yaml_exists() -> None:
    """Lucas's agent.yaml must exist at src/maezo/agents/lucas/agent.yaml."""
    agent_path = _AGENTS_ROOT / "lucas" / "agent.yaml"
    assert agent_path.exists(), f"Lucas agent.yaml not found at {agent_path}"


def test_lucas_agent_yaml_has_required_fields() -> None:
    """Lucas's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "lucas" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "lucas"
    assert data["name"] == "Lucas Ferreira"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 2


def test_lucas_graph_compiles() -> None:
    """Lucas's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.lucas.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Lucas graph must be a StateGraph with compile method"


def test_lucas_graph_has_required_nodes() -> None:
    """Lucas's graph must include assess_beneficiary, prepare_communication, escalate_to_human nodes."""
    from maezo.agents.lucas.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "assess_beneficiary" in node_names, (
        f"Lucas graph missing 'assess_beneficiary'. Found: {node_names}"
    )
    assert "prepare_communication" in node_names, (
        f"Lucas graph missing 'prepare_communication'. Found: {node_names}"
    )
    assert "escalate_to_human" in node_names, f"Lucas graph missing 'escalate_to_human'. Found: {node_names}"
