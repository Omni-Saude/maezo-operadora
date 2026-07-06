"""Unit tests for Carolina agent (Phase 2 — PAGTO, Revenue Cycle).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "maezo" / "agents"


def test_carolina_agent_yaml_exists() -> None:
    """Carolina's agent.yaml must exist at src/maezo/agents/carolina/agent.yaml."""
    agent_path = _AGENTS_ROOT / "carolina" / "agent.yaml"
    assert agent_path.exists(), f"Carolina agent.yaml not found at {agent_path}"


def test_carolina_agent_yaml_has_required_fields() -> None:
    """Carolina's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "carolina" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "carolina"
    assert data["name"] == "Carolina"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 2


def test_carolina_graph_compiles() -> None:
    """Carolina's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.carolina.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Carolina graph must be a StateGraph with compile method"


def test_carolina_graph_has_required_nodes() -> None:
    """Carolina's graph must include validate_payment, route_approval, execute_payment nodes."""
    from maezo.agents.carolina.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "validate_payment" in node_names, f"Carolina graph missing 'validate_payment'. Found: {node_names}"
    assert "route_approval" in node_names, f"Carolina graph missing 'route_approval'. Found: {node_names}"
    assert "execute_payment" in node_names, f"Carolina graph missing 'execute_payment'. Found: {node_names}"
