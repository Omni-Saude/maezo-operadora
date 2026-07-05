"""Unit tests for Helena agent (Phase 0 — AGJ-HELENA-TRIAGE).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "maezo" / "agents"


def test_helena_agent_yaml_exists() -> None:
    """Helena's agent.yaml must exist at src/maezo/agents/helena/agent.yaml."""
    agent_path = _AGENTS_ROOT / "helena" / "agent.yaml"
    assert agent_path.exists(), f"Helena agent.yaml not found at {agent_path}"


def test_helena_agent_yaml_has_required_fields() -> None:
    """Helena's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "helena" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "helena"
    assert data["name"] == "Helena Moreira"
    assert "role" in data
    assert "tools" in data
    # Helena is Phase 0
    assert data.get("phase") == 0


def test_helena_graph_compiles() -> None:
    """Helena's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.helena.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Helena graph must be a StateGraph with compile method"


def test_helena_graph_has_triage_node() -> None:
    """Helena's graph must include a 'triage' node for initial routing."""
    from maezo.agents.helena.graph import build

    graph = build()
    compiled = graph.compile()

    # Inspect the compiled graph's nodes
    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "triage" in node_names, f"Helena graph missing 'triage' node. Found: {node_names}"
