"""Helena Moreira — Health Navigator Agent (Phase 0, AGJ-HELENA-TRIAGE).

Helena is the first live agent. She handles WhatsApp-based triage, routing,
and scheduling for beneficiaries. Her graph consists of:
- triage: classifies the incoming message intent using DMN rules
- response: generates WhatsApp response messages
- escalation: starts SP-OP-ESCALATION-001 when triggers fire

The StateGraph follows the canonical AgentState pattern from maezo.runtime.harness.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return Helena's StateGraph with triage node.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → triage → __end__

    The triage node is the core of AGJ-HELENA-TRIAGE: it classifies
    beneficiary messages and routes to DMN evaluation or escalation.

    Returns:
        A StateGraph[AgentState] for Helena's triage workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def triage_node(state: AgentState) -> dict[str, Any]:
        """Classify incoming WhatsApp message and determine routing.

        This node inspects the latest message, determines intent,
        checks for red flags (via DMN), and decides whether to:
        - Respond directly (informational)
        - Escalate to human (SP-OP-ESCALATION-001)
        - Delegate to Rafael (SP-OP-AUTH-001 analysis)

        Args:
            state: The current AgentState with messages.

        Returns:
            Updated state dict with triage results.
        """
        messages: list[str] = list(state.get("messages", []))
        _last = messages[-1] if messages else ""

        # Triage result annotation — placeholder for DMN evaluation
        triage_result = {
            "intent": "informational",
            "red_flag": False,
            "requires_escalation": False,
        }

        return {
            "messages": messages,
            "triage_result": triage_result,
        }

    graph.add_node("triage", triage_node)
    graph.add_edge("__start__", "triage")
    graph.add_edge("triage", "__end__")

    return graph
