"""Rafael Nogueira — Analista de Autorizacao Previa Agent (Phase 1, AUTH).

Rafael is the Phase 1 centerpiece agent. He instructs SP-OP-AUTH-001 and
assembles the medico-auditor dossier. His graph is a stub in M5a — the full
implementation (with DMN evaluation, FHIR enrichment, and dossier assembly)
arrives in M6.

Rafael is a TARGET of delegation: Helena delegates "authorization.analyze"
tasks to him via the A2A dispatcher (ADR-0003).

L0 HARD INVARIANT: Rafael NEVER exercises authorization_denial or clinical_decision.
Negatives and coverage decisions are exclusively the medico-auditor's User Task.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return Rafael's StateGraph (stub for M5a).

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → agent → __end__

    Full implementation (M6) will add nodes for:
    - gather: FHIR patient/coverage reads
    - assess: DMN auth_admissibility / auth_auto_approval
    - dossier: assemble medico-auditor dossier
    - escalate: SP-OP-AUTH-001 human routing

    Returns:
        A StateGraph[AgentState] stub for Rafael's authorization workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """Stub agent node — placeholder for M6 full implementation."""
        messages: list[str] = list(state.get("messages", []))
        return {"messages": messages}

    graph.add_node("agent", agent_node)
    graph.add_edge("__start__", "agent")
    graph.add_edge("agent", "__end__")

    return graph
