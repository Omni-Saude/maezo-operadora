"""Unit tests for maezo.runtime.harness."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.support.audit_fakes import FakeStartAuditSink

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
            "audit_sink": FakeStartAuditSink(),
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


def test_create_graph_gustavo_resolves_real_build() -> None:
    """`create_graph('gustavo')` resolves Gustavo's REAL `build(config)` (T1.12) — same wiring
    contract as helena/rafael: `self._tool_deps` merged with `inference`; `fhir` optional."""
    harness = Harness(
        inference=MagicMock(),
        tool_deps={
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        },
    )

    graph = harness.create_graph("gustavo")

    assert graph is not None
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {
        "receive",
        "gather",
        "assess",
        "review_submission",
        "instruct_nip",
        "start_process",
        "finalize",
    } <= node_names


def test_create_graph_gustavo_missing_deps_raises_value_error() -> None:
    """Gustavo's own `build(config)` fail-closes when a required dependency is missing (T1.12)."""
    harness = Harness(inference=MagicMock())
    with pytest.raises(ValueError, match="missing required dependencies"):
        harness.create_graph("gustavo")


def test_create_graph_lucas_resolves_real_build() -> None:
    """`create_graph('lucas')` resolves Lucas's REAL `build(config)` (T1.12) — not the trivial
    default — via `self._tool_deps` merged with `inference`."""
    mock_inference = MagicMock()
    harness = Harness(
        inference=mock_inference,
        tool_deps={
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": _fake_whatsapp(),
            "audit_sink": FakeStartAuditSink(),
        },
    )

    graph = harness.create_graph("lucas")

    assert graph is not None
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {
        "receive",
        "gather",
        "assess",
        "respond_member",
        "escalate_human",
        "start_process",
        "complete",
    } <= (node_names)


def test_create_graph_lucas_missing_deps_raises_value_error() -> None:
    """Lucas's own `build(config)` fail-closes when a required dependency is missing — the
    harness does not swallow or reinterpret that as `UnknownAgentError`."""
    harness = Harness(inference=MagicMock())
    with pytest.raises(ValueError, match="missing required dependencies"):
        harness.create_graph("lucas")


def test_create_graph_stub_agent_uses_no_arg_build() -> None:
    """A still-stubbed agent's `build()` takes no parameters — `create_graph` must call it with
    no arguments (introspected via `inspect.signature`, not guessed). Post-B6 all 10 named
    agents expose `build(config)`, so the `_template` scaffold (never a named agent) is the
    durable no-arg-build example."""
    harness = Harness()
    graph = harness.create_graph("_template")
    assert graph is not None
    assert hasattr(graph, "compile")


def test_create_graph_none_agent_id_preserves_trivial_default() -> None:
    """Back-compat: `create_graph()` / `create_graph(None)` behavior is byte-for-byte unchanged."""
    harness = Harness()
    graph = harness.create_graph(None)
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert node_names == {"agent"}


# ---------------------------------------------------------------------------
# G3 — PHI-gated turn telemetry: harness.invoke emits ONE record per completed turn
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from maezo.platform.observability import turn_conversation_digest  # noqa: E402


def _turn_record_from(mock_logger: MagicMock) -> dict[str, object]:
    """Extract the single `agent_turn_completed` record's fields from a patched module logger."""
    calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "agent_turn_completed"]
    assert len(calls) == 1
    return dict(calls[0].kwargs)


@pytest.mark.asyncio
async def test_harness_invoke_emits_turn_telemetry_with_shape_counts() -> None:
    """A completed turn emits ONE `agent_turn_completed` record carrying the turn's SHAPE (message
    counts), not its content. With inference appending one reply, input 1 -> output 2, produced 1.
    (The module logger is patched — see `test_observability._capture_record` for why not
    `capture_logs`.)"""
    mock_inference = MagicMock()
    mock_inference.generate = AsyncMock(return_value="mocked response")
    harness = Harness(inference=mock_inference)
    harness.create_graph()  # trivial default graph -> agent_id is None

    with patch("maezo.platform.observability.logger") as mock_logger:
        await harness.invoke({"messages": ["hello"]})

    record = _turn_record_from(mock_logger)
    assert record["agent_id"] is None
    assert record["input_message_count"] == 1
    assert record["output_message_count"] == 2
    assert record["produced_message_count"] == 1
    assert record["conversation_digest"] is None  # no thread id supplied


@pytest.mark.asyncio
async def test_harness_invoke_hashes_the_thread_id_and_never_logs_it_raw() -> None:
    """The thread id reaches the telemetry sink only as a HASH. A phone-bearing conversation id
    passed to `invoke` must appear NOWHERE raw in the `agent_turn_completed` record — only its
    `turn_conversation_digest`."""
    thread_id = "wa:amh:hk1_" + "c" * 64  # a keyed, PHI-safe conversation id
    harness = Harness()
    harness.create_graph()

    with patch("maezo.platform.observability.logger") as mock_logger:
        await harness.invoke({"messages": ["oi"]}, thread_id=thread_id)

    record = _turn_record_from(mock_logger)
    assert record["conversation_digest"] == turn_conversation_digest(thread_id)
    for key, value in record.items():
        assert value != thread_id, key
