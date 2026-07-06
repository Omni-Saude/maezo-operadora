"""Marina Andrade — Medical Accounts Analyst Agent (Phase 2, CONTAS/RECURSO/REEMBOLSO).

Marina handles TISS medical account analysis, glosa triage, and recurso dossier
preparation. Her graph consists of:
- analyze_claim: inspects TISS demonstrativos, identifies glosa candidates
- prepare_dossier: assembles the triage/recurso dossier for human analyst review

A2A delegation: Marina delegates to Lucas when beneficiary information is needed.

The StateGraph follows the canonical AgentState pattern from maezo.runtime.harness.

L0 HARD INVARIANT: Marina NEVER exercises authorization_denial, standard_glosa_processing
(confirm glosa), clinical_decision, or fraud_accusation. She instructs the case
(dossier + routing); the human decides.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState

logger = structlog.get_logger(__name__)


def build() -> StateGraph[AgentState]:
    """Build and return Marina's StateGraph with analyze_claim and prepare_dossier nodes.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → analyze_claim → prepare_dossier → __end__

    The analyze_claim node inspects TISS demonstrativos and identifies glosa candidates.
    The prepare_dossier node assembles the triage/recurso dossier for human analyst review.

    Returns:
        A StateGraph[AgentState] for Marina's medical accounts workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def analyze_claim_node(state: AgentState) -> dict[str, Any]:
        """Analyze TISS medical accounts and identify glosa candidates.

        This node inspects the claim data, identifies glosa lines via DMN evaluation,
        computes denial ratios, and determines whether to route to CONTAS or RECURSO.

        Args:
            state: The current AgentState with messages and claim context.

        Returns:
            Updated state dict with claim analysis results.
        """
        messages: list[str] = list(state.get("messages", []))
        logger.info(
            "marina.analyze_claim.start",
            messages_count=len(messages),
        )

        # Placeholder for TISS claim analysis via DMN evaluation
        claim_analysis = {
            "has_glosas": False,
            "denial_ratio": 0.0,
            "glosa_count": 0,
            "roteamento": "ANALISE_HUMANA",
            "categoria_normalizada": "desconhecida",
        }

        logger.info(
            "marina.analyze_claim.complete",
            roteamento=claim_analysis["roteamento"],
        )

        return {
            "messages": messages,
            "claim_analysis": claim_analysis,
        }

    async def prepare_dossier_node(state: AgentState) -> dict[str, Any]:
        """Assemble the triage/recurso dossier for human analyst review.

        Combines claim analysis facts into a structured dossier. Routes to the
        appropriate human group (analista-contas or medico-auditor).
        Delegates to Lucas via A2A when beneficiary information is needed.

        Args:
            state: The current AgentState with claim analysis results.

        Returns:
            Updated state dict with dossier and routing information.
        """
        messages: list[str] = list(state.get("messages", []))
        claim_analysis = cast(dict[str, Any], state.get("claim_analysis", {}))
        logger.info(
            "marina.prepare_dossier.start",
            roteamento=claim_analysis.get("roteamento", "ANALISE_HUMANA"),
        )

        # A2A check: delegate to Lucas for beneficiary info when needed
        needs_beneficiary_info = cast(bool, state.get("needs_beneficiary_info", False))
        a2a_delegation = None
        if needs_beneficiary_info:
            a2a_delegation = {
                "target_agent": "lucas",
                "task_type": "beneficiary.assess",
                "reason": "Beneficiary information required for dossier completion",
            }
            logger.info(
                "marina.prepare_dossier.a2a_delegate",
                target="lucas",
                task_type="beneficiary.assess",
            )

        # Assemble dossier — facts only, never decides merit
        dossier = {
            "resumo": {
                "glosa_count": claim_analysis.get("glosa_count", 0),
                "denial_ratio": claim_analysis.get("denial_ratio", 0.0),
                "categoria": claim_analysis.get("categoria_normalizada", "desconhecida"),
            },
            "roteamento": claim_analysis.get("roteamento", "ANALISE_HUMANA"),
            "grupo_revisor": _resolve_revisor_group(claim_analysis),
        }

        logger.info(
            "marina.prepare_dossier.complete",
            roteamento=dossier["roteamento"],
            grupo_revisor=dossier["grupo_revisor"],
        )

        return {
            "messages": messages,
            "dossier": dossier,
            "a2a_delegation": a2a_delegation,
        }

    graph.add_node("analyze_claim", analyze_claim_node)
    graph.add_node("prepare_dossier", prepare_dossier_node)
    graph.add_edge("__start__", "analyze_claim")
    graph.add_edge("analyze_claim", "prepare_dossier")
    graph.add_edge("prepare_dossier", "__end__")

    return graph


def _resolve_revisor_group(claim_analysis: dict[str, Any]) -> str:
    """Map claim analysis category to the appropriate human review group.

    Glosa tecnica/clinica -> medico-auditor.
    Glosa administrativa/valor/documental -> analista-contas.
    Default -> analista-recurso-glosa (conservative).

    Args:
        claim_analysis: The claim analysis result dict.

    Returns:
        The human review group identifier.
    """
    categoria = claim_analysis.get("categoria_normalizada", "desconhecida")
    if categoria in ("tecnica", "clinica"):
        return "medico-auditor"
    elif categoria in ("administrativa", "valor", "documental"):
        return "analista-contas"
    return "analista-recurso-glosa"
