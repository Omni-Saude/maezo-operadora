"""Unit tests for Gustavo agent (Phase 2 — NIP/ANS-SUBMIT/LGPD).

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import yaml

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "maezo" / "agents"


def test_gustavo_agent_yaml_exists() -> None:
    """Gustavo's agent.yaml must exist at src/maezo/agents/gustavo/agent.yaml."""
    agent_path = _AGENTS_ROOT / "gustavo" / "agent.yaml"
    assert agent_path.exists(), f"Gustavo agent.yaml not found at {agent_path}"


def test_gustavo_agent_yaml_has_required_fields() -> None:
    """Gustavo's agent.yaml must contain all required fields for an agent definition."""
    agent_path = _AGENTS_ROOT / "gustavo" / "agent.yaml"

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"
    assert data["id"] == "gustavo"
    assert data["name"] == "Gustavo Andrade"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 2


def test_gustavo_graph_compiles() -> None:
    """Gustavo's graph.py must expose a build() function returning a compilable StateGraph."""
    from maezo.agents.gustavo.graph import build

    graph = build()

    assert graph is not None
    assert hasattr(graph, "compile"), "Gustavo graph must be a StateGraph with compile method"


def test_gustavo_classify() -> None:
    """Gustavo's graph must include classify_request, assemble_response, and validate_compliance nodes."""
    from maezo.agents.gustavo.graph import build

    graph = build()
    compiled = graph.compile()

    graph_repr = compiled.get_graph()
    node_names = {node for node in graph_repr.nodes if node not in ("__start__", "__end__")}

    assert "classify_request" in node_names, f"Gustavo graph missing 'classify_request'. Found: {node_names}"
    assert "assemble_response" in node_names, (
        f"Gustavo graph missing 'assemble_response'. Found: {node_names}"
    )
    assert "validate_compliance" in node_names, (
        f"Gustavo graph missing 'validate_compliance'. Found: {node_names}"
    )
