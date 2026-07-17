"""Unit tests for Beatriz agent (Phase 3 — Chronic Disease PROGRAMA).

TDD London School: tests written BEFORE/ALONGSIDE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_beatriz_agent_yaml_exists() -> None:
    """Beatriz's agent.yaml must exist at spec/agents/beatriz/agent.yaml."""
    agent_path = _AGENTS_ROOT / "beatriz" / "agent.yaml"
    assert agent_path.exists(), f"Beatriz agent.yaml not found at {agent_path}"


def test_beatriz_agent_yaml_has_required_fields() -> None:
    """Beatriz's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "beatriz" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "beatriz"
    assert "name" in data
    assert "role" in data
    assert "tools" in data


def test_beatriz_graph_compiles() -> None:
    """Beatriz's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.beatriz.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Beatriz graph must be a StateGraph with compile method"


def test_beatriz_graph_has_required_nodes() -> None:
    """Beatriz's graph must include assess_eligibility, design_care_plan, monitor_adherence."""
    from maezo.agents.beatriz.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "assess_eligibility" in node_names, (
        f"Beatriz graph missing 'assess_eligibility' node. Found: {node_names}"
    )
    assert "design_care_plan" in node_names, (
        f"Beatriz graph missing 'design_care_plan' node. Found: {node_names}"
    )
    assert "monitor_adherence" in node_names, (
        f"Beatriz graph missing 'monitor_adherence' node. Found: {node_names}"
    )
    assert len(node_names) == 3, (
        f"Beatriz graph should have exactly 3 nodes. Found {len(node_names)}: {node_names}"
    )
