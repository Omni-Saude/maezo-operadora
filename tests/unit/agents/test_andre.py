"""Unit tests for André agent (Phase 3 — Population Health analytics).

TDD London School: tests written BEFORE/ALONGSIDE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_andre_agent_yaml_exists() -> None:
    """André's agent.yaml must exist at spec/agents/andre/agent.yaml."""
    agent_path = _AGENTS_ROOT / "andre" / "agent.yaml"
    assert agent_path.exists(), f"André agent.yaml not found at {agent_path}"


def test_andre_agent_yaml_has_required_fields() -> None:
    """André's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "andre" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "andre"
    assert "name" in data
    assert "role" in data
    assert "tools" in data


def test_andre_graph_compiles() -> None:
    """André's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.andre.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "André graph must be a StateGraph with compile method"


def test_andre_graph_has_required_nodes() -> None:
    """André's graph must include analyze_population, identify_risk_cohorts, recommend_interventions."""
    from maezo.agents.andre.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "analyze_population" in node_names, (
        f"André graph missing 'analyze_population' node. Found: {node_names}"
    )
    assert "identify_risk_cohorts" in node_names, (
        f"André graph missing 'identify_risk_cohorts' node. Found: {node_names}"
    )
    assert "recommend_interventions" in node_names, (
        f"André graph missing 'recommend_interventions' node. Found: {node_names}"
    )
    assert len(node_names) == 3, (
        f"André graph should have exactly 3 nodes. Found {len(node_names)}: {node_names}"
    )
