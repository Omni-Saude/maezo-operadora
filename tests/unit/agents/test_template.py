"""Unit tests for the agent template (_template).

TDD London School: tests written BEFORE implementation.

HEL-13: `build()` is now the canonical fail-closed `build(config)`, so every construction here
injects the four required dependencies. The contract itself is pinned by
`tests/unit/agents/test_template_contract.py`.
"""

from pathlib import Path
from typing import Any

import yaml

from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


def _deps() -> dict[str, Any]:
    return {
        "inference": object(),
        "dmn": FakeDmnTransport(),
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
    }


_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


def test_template_has_required_fields() -> None:
    """The template agent.yaml must define all required fields for an agent contract."""
    template_path = _AGENTS_ROOT / "_template" / "agent.yaml"

    assert template_path.exists(), f"Template agent.yaml not found at {template_path}"

    with open(template_path) as f:
        data = yaml.safe_load(f)

    assert data is not None, "agent.yaml must be valid YAML"

    required_fields = ["id", "name", "role", "phase", "autonomy_level", "process_keys", "tools"]

    for field in required_fields:
        assert field in data, f"agent.yaml missing required field: {field}"


def test_template_graph_compiles() -> None:
    """The template graph.py must expose a build() function that returns a compiled StateGraph."""
    from maezo.agents._template.graph import build

    graph = build(_deps())

    # A compiled LangGraph StateGraph should have a compile() method
    assert graph is not None
    # The template's build returns an uncompiled StateGraph[TemplateState]
    assert hasattr(graph, "compile"), "Template graph must be a StateGraph (with compile method)"


def test_template_graph_has_required_nodes() -> None:
    """Template graph must have at least a start and end edge."""
    from maezo.agents._template.graph import build

    graph = build(_deps())
    compiled = graph.compile()

    # LangGraph compiled graphs expose nodes attribute
    nodes = compiled.get_graph().nodes if hasattr(compiled, "get_graph") else {}
    assert len(nodes) > 0, "Template graph must have at least one node"
