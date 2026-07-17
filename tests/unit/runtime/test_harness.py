"""Unit tests for maezo.runtime.harness."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maezo.runtime.harness import Harness, UnknownAgentError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport


def test_harness_create_graph() -> None:
    """Harness should create a LangGraph StateGraph."""
    harness = Harness()

    graph = harness.create_graph()

    assert graph is not None


@pytest.mark.asyncio
async def test_harness_invoke() -> None:
    """Harness.invoke should execute the graph with the given state and return a result."""
    harness = Harness()
    harness.create_graph()

    result = await harness.invoke({"messages": ["hello"]})

    assert isinstance(result, dict)
    assert "messages" in result


@pytest.mark.asyncio
async def test_harness_invoke_with_inference() -> None:
    """Harness.invoke should call inference provider when configured."""
    mock_inference = MagicMock()
    mock_inference.generate = AsyncMock(return_value="mocked response")

    harness = Harness(inference=mock_inference)
    harness.create_graph()

    result = await harness.invoke({"messages": ["hello"]})

    assert isinstance(result, dict)
    mock_inference.generate.assert_awaited_once_with(prompt="hello")


# ---------------------------------------------------------------------------
# create_graph(agent_id=...) — T1.11/defect B6: resolves the REAL per-agent build()
# ---------------------------------------------------------------------------


def _fake_whatsapp() -> object:
    class _Fake:
        async def send(self, to_hash: str, text: str) -> dict[str, object]:
            return {"to_hash": to_hash, "text": text}

    return _Fake()


def test_create_graph_unknown_agent_id_fails_closed() -> None:
    """No `spec/agents/<id>/agent.yaml` -> `UnknownAgentError`, never a silent fallback to the
    trivial default graph (constraint 2)."""
    harness = Harness()
    with pytest.raises(UnknownAgentError):
        harness.create_graph("not-a-real-agent")


def test_create_graph_helena_resolves_real_build() -> None:
    """`create_graph('helena')` resolves Helena's REAL `build(config)` — not the trivial
    default — via `self._tool_deps` merged with `inference`."""
    mock_inference = MagicMock()
    harness = Harness(
        inference=mock_inference,
        tool_deps={
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": _fake_whatsapp(),
        },
    )

    graph = harness.create_graph("helena")

    assert graph is not None
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {"receive", "classify", "inform", "schedule", "escalate", "respond"} <= node_names


def test_create_graph_helena_missing_deps_raises_value_error() -> None:
    """Helena's own `build(config)` fail-closes when a required dependency is missing — the
    harness does not swallow or reinterpret that as `UnknownAgentError`."""
    harness = Harness(inference=MagicMock())
    with pytest.raises(ValueError, match="missing required dependencies"):
        harness.create_graph("helena")


def test_create_graph_stub_agent_uses_no_arg_build() -> None:
    """A still-stubbed agent's `build()` takes no parameters — `create_graph` must call it with
    no arguments (introspected via `inspect.signature`, not guessed)."""
    harness = Harness()
    graph = harness.create_graph("andre")
    assert graph is not None
    assert hasattr(graph, "compile")


def test_create_graph_none_agent_id_preserves_trivial_default() -> None:
    """Back-compat: `create_graph()` / `create_graph(None)` behavior is byte-for-byte unchanged."""
    harness = Harness()
    graph = harness.create_graph(None)
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert node_names == {"agent"}
