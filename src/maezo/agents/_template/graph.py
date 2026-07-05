"""Agent template graph — minimal StateGraph with required nodes.

Every agent graph in Maezo MUST follow this pattern:
- A build() function returning a StateGraph[AgentState]
- At minimum, one node connected from start to end
- The AgentState is the canonical state type from the runtime harness

This template serves as the starting point for all concrete agent graphs
(helena, rafael, etc.).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return the template agent StateGraph.

    Returns an uncompiled StateGraph[AgentState] ready for node
    registration and compilation by the agent harness.

    Returns:
        A StateGraph[AgentState] instance with start→agent→end wiring.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """Default agent node — template placeholder."""
        messages: list[str] = list(state.get("messages", []))
        return {"messages": messages}

    graph.add_node("agent", agent_node)
    graph.add_edge("__start__", "agent")
    graph.add_edge("agent", "__end__")

    return graph
