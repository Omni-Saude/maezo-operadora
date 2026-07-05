"""LangGraph StateGraph harness for agent runtime.

Integrates the checkpointer (state persistence) and inference provider (LLM)
into a runnable LangGraph state machine. This is the primary entry point
for agent execution.
"""

from __future__ import annotations

from typing import Any, TypedDict

import structlog
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from maezo.runtime.checkpoint import Checkpointer
from maezo.runtime.inference import InferenceProvider

logger = structlog.get_logger(__name__)


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
    ) -> None:
        """Initialize the harness.

        Args:
            checkpointer: Optional checkpointer for state persistence.
            inference: Optional inference provider for LLM calls.
        """
        self._checkpointer = checkpointer
        self._inference = inference
        self._graph: StateGraph[AgentState] | None = None
        self._compiled: CompiledStateGraph[AgentState, None, Any, Any] | None = None
        logger.info(
            "harness_initialized",
            has_checkpointer=checkpointer is not None,
            has_inference=inference is not None,
        )

    def create_graph(self) -> StateGraph[AgentState]:
        """Create and return a LangGraph StateGraph for the agent.

        The graph includes a default 'agent' node that processes messages
        through the inference provider.

        Returns:
            A configured StateGraph instance (not yet compiled).
        """
        graph: StateGraph[AgentState] = StateGraph(AgentState)

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
        result = await self._compiled.ainvoke(state)  # type: ignore[arg-type]
        return dict(result)
