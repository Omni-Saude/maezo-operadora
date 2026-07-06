"""Unit tests for Fernando agent (Phase 3 — Provider Network CRED/ADEQUACAO).

TDD London School: tests written BEFORE/ALONGSIDE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "maezo" / "agents"


def test_fernando_agent_yaml_exists() -> None:
    """Fernando's agent.yaml must exist at src/maezo/agents/fernando/agent.yaml."""
    agent_path = _AGENTS_ROOT / "fernando" / "agent.yaml"
    assert agent_path.exists(), f"Fernando agent.yaml not found at {agent_path}"


def test_fernando_agent_yaml_has_required_fields() -> None:
    """Fernando's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "fernando" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "fernando"
    assert "name" in data
    assert "role" in data
    assert "tools" in data


def test_fernando_graph_compiles() -> None:
    """Fernando's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.fernando.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Fernando graph must be a StateGraph with compile method"


def test_fernando_graph_has_required_nodes() -> None:
    """Fernando's graph must include evaluate_provider, measure_network_gap, recommend_action nodes."""
    from maezo.agents.fernando.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "evaluate_provider" in node_names, (
        f"Fernando graph missing 'evaluate_provider' node. Found: {node_names}"
    )
    assert "measure_network_gap" in node_names, (
        f"Fernando graph missing 'measure_network_gap' node. Found: {node_names}"
    )
    assert "recommend_action" in node_names, (
        f"Fernando graph missing 'recommend_action' node. Found: {node_names}"
    )
    assert len(node_names) == 3, (
        f"Fernando graph should have exactly 3 nodes. Found {len(node_names)}: {node_names}"
    )
