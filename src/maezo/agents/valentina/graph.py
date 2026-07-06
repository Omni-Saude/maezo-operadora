"""Valentina — Fraud & Audit Agent (Phase 3, FRAUDE).

Valentina coordinates fraud investigation: triages indicators, coordinates
investigation, and reviews the sealed dossier. She operates in the PHI security
zone (ADR-0006).

L0 HARD INVARIANT (FRAUDE-001 / ADR-0018): Valentina NEVER exercises
fraud_accusation. The accusation is born exclusively in the human
User Task UT_DecisaoInvestigador over the sealed bundle_root.
No node in this graph sets decisao_fraude or reaches an adverse terminal.

Her graph consists of:
- triage_indicators: triages fraud indicators (NEVER accuses — score is routing FACT)
- coordinate_investigation: coordinates evidence gathering and dossier assembly
- review_dossier: reviews the sealed dossier (NEVER decides — dossier goes to human)
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return Valentina's StateGraph for fraud investigation.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → triage_indicators → coordinate_investigation → review_dossier → __end__

    Returns:
        A StateGraph[AgentState] for Valentina's fraud investigation workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def triage_indicators_node(state: AgentState) -> dict[str, Any]:
        """Triage fraud indicators from incoming case data.

        Scores and classifies indicators as ROUTING FACTS, NEVER as a verdict.
        The reference detect_fraud v2 summed DMN scores and issued FRAUD_DETECTED
        — here, triage produces routing intensity (LEVE/APROFUNDADA/PRIORITARIA)
        with ZERO auto-accusation.

        Args:
            state: The current AgentState with case data.

        Returns:
            Updated state dict with indicator triage results.
        """
        messages: list[str] = list(state.get("messages", []))
        case_id: str = cast(str, state.get("numero_caso", ""))
        evidence_refs: list[str] = cast(list[str], state.get("evidencia_refs", []))

        if not isinstance(evidence_refs, list):
            evidence_refs = []

        # Score indicators as ROUTING FACT — NEVER a verdict
        base_score: int = len(evidence_refs) * 10

        indicadores: list[str] = []
        if base_score > 0:
            indicadores.append("evidencia_presente")
        if base_score > 50:
            indicadores.append("score_elevado")
        if base_score > 100:
            indicadores.append("multiplos_indicadores")

        # Investigation intensity (NEVER accusation)
        if base_score > 100:
            intensidade: str = "PRIORITARIA"
        elif base_score > 50:
            intensidade = "APROFUNDADA"
        else:
            intensidade = "LEVE"

        triage: dict[str, Any] = {
            "numero_caso": case_id,
            "score_indicadores": base_score,
            "indicadores_presentes": indicadores,
            "intensidade_investigacao": intensidade,
            # CRITICAL: NEVER sets decisao_fraude
            "requires_human_review": base_score > 0,
        }

        return {
            "messages": messages,
            "indicator_triage": triage,
        }

    async def coordinate_investigation_node(state: AgentState) -> dict[str, Any]:
        """Coordinate the fraud investigation workflow.

        Orchestrates evidence gathering, scoring, and dossier assembly.
        Delegates to Beatriz (fraude.investigate) for evidence assembly.
        NEVER decides accusation — only coordinates the investigation pipeline.

        Args:
            state: The current AgentState with triage results.

        Returns:
            Updated state dict with investigation coordination results.
        """
        messages: list[str] = list(state.get("messages", []))
        triage: dict[str, Any] = cast(dict[str, Any], state.get("indicator_triage", {}))
        case_id: str = cast(str, triage.get("numero_caso", ""))
        evidence_refs: list[str] = cast(list[str], state.get("evidencia_refs", []))
        if not isinstance(evidence_refs, list):
            evidence_refs = []

        # Coordinate investigation — instructs, NEVER decides
        investigation: dict[str, Any] = {
            "numero_caso": case_id,
            "evidencia_refs": evidence_refs,
            "evidencia_coletada": len(evidence_refs) > 0,
            "dossie_montado": True,
            "dossie_items": len(evidence_refs),
            # CRITICAL: NEVER sets decisao_fraude
        }

        return {
            "messages": messages,
            "investigation": investigation,
        }

    async def review_dossier_node(state: AgentState) -> dict[str, Any]:
        """Review the sealed investigation dossier.

        Reviews the assembled dossier for completeness and routes it to the
        human investigator. NEVER decides accusation — the dossier is sealed
        and handed to UT_DecisaoInvestigador, where the human decides.

        L0 HARD: This node NEVER sets decisao_fraude. The accusation is
        exclusively born in the human User Task over the sealed bundle_root.

        Args:
            state: The current AgentState with investigation dossier.

        Returns:
            Updated state dict with dossier review results.
        """
        messages: list[str] = list(state.get("messages", []))
        investigation: dict[str, Any] = cast(dict[str, Any], state.get("investigation", {}))
        triage: dict[str, Any] = cast(dict[str, Any], state.get("indicator_triage", {}))
        case_id: str = cast(str, investigation.get("numero_caso", ""))
        evidence_count: int = cast(int, investigation.get("dossie_items", 0))
        score: int = cast(int, triage.get("score_indicadores", 0))

        # Review dossier completeness — NEVER decides accusation
        dossier_complete: bool = evidence_count > 0
        requires_escalation: bool = score > 50 or not dossier_complete

        review: dict[str, Any] = {
            "numero_caso": case_id,
            "dossier_complete": dossier_complete,
            "requires_escalation": requires_escalation,
            "routing": "UT_DecisaoInvestigador",  # ALWAYS routes to human
            # CRITICAL: NEVER sets decisao_fraude — accusation is human-only
        }

        return {
            "messages": messages,
            "dossier_review": review,
        }

    graph.add_node("triage_indicators", triage_indicators_node)
    graph.add_node("coordinate_investigation", coordinate_investigation_node)
    graph.add_node("review_dossier", review_dossier_node)

    graph.add_edge("__start__", "triage_indicators")
    graph.add_edge("triage_indicators", "coordinate_investigation")
    graph.add_edge("coordinate_investigation", "review_dossier")
    graph.add_edge("review_dossier", "__end__")

    return graph
