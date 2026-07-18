"""LangGraph StateGraph harness for agent runtime.

Integrates the checkpointer (state persistence) and inference provider (LLM)
into a runnable LangGraph state machine. This is the primary entry point
for agent execution.

T1.11 (defect B6): `create_graph` previously always built a trivial, hardcoded default graph —
no production code ever invoked an agent-specific `build()`. `create_graph(agent_id=...)` now
resolves the REAL per-agent graph via the T0.3 `AgentLoader` conventions (`spec/agents/<id>/
agent.yaml` existence + `maezo.agents.<id>.graph:build`), matching every agent.yaml's declared
`graph: graph.py:build`. Calling `create_graph()` with no `agent_id` (the default) preserves the
EXACT prior trivial-default behavior — existing callers/tests are unaffected.

Fail-closed (constraint 2): an `agent_id` with no `spec/agents/<id>/agent.yaml`, no importable
`graph` module, or no callable `build` raises `UnknownAgentError` — never a silent fallback to
the trivial default graph.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any, TypedDict, cast

import structlog
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from maezo.runtime.checkpoint import Checkpointer
from maezo.runtime.inference import InferenceProvider

logger = structlog.get_logger(__name__)


class UnknownAgentError(ValueError):
    """`create_graph(agent_id=...)` was given an agent with no resolvable graph.

    Raised when `spec/agents/<agent_id>/agent.yaml` does not exist (via `AgentLoader`), the
    `maezo.agents.<agent_id>.graph` module is not importable, or it has no callable `build`.
    Fail-closed: never falls back to the trivial default graph (constraint 2).
    """


def _resolve_agent_build(agent_id: str) -> Any:
    """Resolve `agent_id`'s real `build` callable via T0.3 `AgentLoader` conventions.

    Validates `spec/agents/<agent_id>/agent.yaml` exists (the single source of truth, T0.3/B14)
    before even attempting the import — an agent with tools/graph but no agent.yaml is not a
    registered agent, regardless of whether a `graph.py` happens to sit on disk.
    """
    from maezo.agents import AgentLoader  # local import: avoids a hard agents<->runtime coupling

    try:
        AgentLoader().load_by_id(agent_id)
    except (FileNotFoundError, ValueError) as exc:
        raise UnknownAgentError(f"unknown agent {agent_id!r}: {exc}") from exc

    module_name = f"maezo.agents.{agent_id}.graph"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise UnknownAgentError(
            f"agent {agent_id!r} has no importable graph module ({module_name}): {exc}"
        ) from exc

    build_fn = getattr(module, "build", None)
    if build_fn is None or not callable(build_fn):
        raise UnknownAgentError(f"agent {agent_id!r} graph module ({module_name}) has no callable build()")
    return build_fn


class AgentState(TypedDict, total=False):
    """Canonical agent state passed through the LangGraph nodes."""

    messages: list[str]


class Harness:
    """LangGraph harness integrating checkpointer and inference provider.

    Orchestrates the agent's StateGraph execution, wiring together
    the checkpointer for state persistence and the inference provider
    for LLM calls.

    Typical usage:
        harness = Harness(checkpointer=ck, inference=inf)
        graph = harness.create_graph()
        result = await harness.invoke({"messages": ["hello"]})
    """

    def __init__(
        self,
        checkpointer: Checkpointer | None = None,
        inference: InferenceProvider | None = None,
        tool_deps: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the harness.

        Args:
            checkpointer: Optional checkpointer for state persistence.
            inference: Optional inference provider for LLM calls.
            tool_deps: Extra dependencies (e.g. `dmn`, `cibseven`, `whatsapp`, `fhir` transports)
                merged into the `config` dict passed to a real agent's `build(config)` when
                `create_graph(agent_id=...)` is used. Ignored by the trivial-default graph
                (`create_graph()` with no `agent_id`).
        """
        self._checkpointer = checkpointer
        self._inference = inference
        self._tool_deps: dict[str, Any] = dict(tool_deps) if tool_deps else {}
        self._graph: StateGraph[Any] | None = None
        self._compiled: CompiledStateGraph[Any, None, Any, Any] | None = None
        logger.info(
            "harness_initialized",
            has_checkpointer=checkpointer is not None,
            has_inference=inference is not None,
            tool_deps=sorted(self._tool_deps),
        )

    def create_graph(self, agent_id: str | None = None) -> StateGraph[Any]:
        """Create and return a LangGraph StateGraph for the agent.

        With no `agent_id` (default): the trivial default graph — a single 'agent' node that
        processes messages through the inference provider. Preserved EXACTLY as before T1.11 for
        back-compat with existing callers/tests.

        With `agent_id`: resolves and invokes the agent's REAL `build(config)` (T1.11, defect
        B6) via `AgentLoader`/`maezo.agents.<agent_id>.graph:build` conventions. `config` is
        `self._tool_deps` merged with `inference` and a default `agent_version`. An agent whose
        `build` takes no parameters (the still-stubbed agents, e.g. andre/valentina/...) is called
        with no arguments — introspected via `inspect.signature`, never guessed by try/except
        (a real `TypeError` raised *inside* a real `build(config)` must propagate, not be
        misread as "this build takes no config").

        Raises:
            UnknownAgentError: `agent_id` has no `spec/agents/<agent_id>/agent.yaml`, no
                importable graph module, or no callable `build` — fail-closed, never a silent
                fallback to the trivial default graph (constraint 2).

        Returns:
            A configured StateGraph instance (not yet compiled).
        """
        if agent_id is None:
            graph: StateGraph[Any] = StateGraph(AgentState)

            async def agent_node(state: AgentState) -> dict[str, list[str]]:
                """Default agent node: process messages through inference."""
                messages: list[str] = list(state.get("messages", []))
                if self._inference:
                    last_msg = messages[-1] if messages else ""
                    response = await self._inference.generate(prompt=last_msg)
                    messages = [*messages, response]
                return {"messages": messages}

            graph.add_node("agent", agent_node)
            graph.add_edge("__start__", "agent")
            graph.add_edge("agent", "__end__")

            self._graph = graph
            self._compiled = None  # reset compiled version
            logger.info("harness_graph_created")
            return graph

        build_fn = _resolve_agent_build(agent_id)
        params = inspect.signature(build_fn).parameters
        if params:
            config: dict[str, Any] = dict(self._tool_deps)
            config.setdefault("inference", self._inference)
            config.setdefault("agent_version", f"{agent_id}@v0")
            resolved_graph = build_fn(config)
        else:
            resolved_graph = build_fn()

        self._graph = resolved_graph
        self._compiled = None
        logger.info("harness_graph_created", agent_id=agent_id)
        return cast("StateGraph[Any]", resolved_graph)

    async def invoke(self, state: dict[str, object]) -> dict[str, object]:
        """Compile the graph (if needed) and invoke it with the given state.

        Args:
            state: Initial state dict with 'messages' key.

        Returns:
            The final state after graph execution.
        """
        if self._graph is None:
            raise RuntimeError("No graph created. Call create_graph() first.")

        if self._compiled is None:
            checkpointer_arg = None
            if self._checkpointer is not None:
                checkpointer_arg = self._checkpointer._saver
            self._compiled = self._graph.compile(checkpointer=checkpointer_arg)

        msgs = state.get("messages", [])
        logger.info("harness_invoke", messages_count=len(msgs) if isinstance(msgs, list) else 0)
        result = await self._compiled.ainvoke(state)
        return dict(result)
