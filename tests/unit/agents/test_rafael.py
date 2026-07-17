"""Unit tests for Rafael agent (Phase 1 — AUTH).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_rafael_agent_yaml_exists() -> None:
    """Rafael's agent.yaml must exist at spec/agents/rafael/agent.yaml."""
    agent_path = _AGENTS_ROOT / "rafael" / "agent.yaml"
    assert agent_path.exists(), f"Rafael agent.yaml not found at {agent_path}"


def test_rafael_agent_yaml_has_required_fields() -> None:
    """Rafael's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "rafael" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "rafael"
    assert data["name"] == "Rafael Nogueira"
    assert "role" in data
    assert "tools" in data
    # Rafael is Phase 1
    assert data.get("phase") == 1


def test_rafael_definition_loads() -> None:
    """Rafael's agent.yaml must be loadable via AgentLoader."""
    from maezo.agents import AgentLoader

    agent_path = _AGENTS_ROOT / "rafael" / "agent.yaml"

    loader = AgentLoader()
    definition = loader.load(agent_path)

    assert definition.id == "rafael"
    assert definition.name == "Rafael Nogueira"
    assert definition.phase == 1


def test_rafael_graph_stub_compiles() -> None:
    """Rafael's graph.py must expose a build() function (stub) returning a compilable StateGraph."""
    from maezo.agents.rafael.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Rafael graph must be a StateGraph with compile method"
