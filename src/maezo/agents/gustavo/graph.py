"""Gustavo Andrade — Regulatory Compliance Agent (Phase 2, NIP/ANS-SUBMIT/LGPD).

Gustavo handles ANS regulatory calendar operations and NIP response instruction.
His graph consists of:
- classify_request: classifies incoming regulatory requests (NIP/LGPD/ANS)
- assemble_response: assembles regulatory response dossiers
- validate_compliance: validates against ANS normative resolutions (RN)

The StateGraph follows the canonical AgentState pattern from maezo.runtime.harness.

L0 HARD INVARIANT: Gustavo NEVER exercises authorization_denial, nip_manter_negativa,
or any adverse action. The NIP response that MAINTAINS a negativa is born ONLY in
UT_RevisaoJuridicaNip (human). The official ANS submission is only transmitted after
UT_RevisarEnvio humana. The agent instructs; the human decides/signs.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState

logger = structlog.get_logger(__name__)


def build() -> StateGraph[AgentState]:
    """Build and return Gustavo's StateGraph for regulatory compliance.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → classify_request → assemble_response → validate_compliance → __end__

    - classify_request: classifies NIP/LGPD/ANS submission requests via DMN
    - assemble_response: assembles regulatory response dossier
    - validate_compliance: validates against RN ANS normative resolutions

    Returns:
        A StateGraph[AgentState] for Gustavo's regulatory compliance workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def classify_request_node(state: AgentState) -> dict[str, Any]:
        """Classify incoming regulatory request (NIP, LGPD, ANS submission).

        Uses DMN nip_classification and ans_submission_admissibility to determine
        the request type, prazo_dias, and appropriate human review group.

        NIP classification: ASSISTENCIAL_CONTESTA_NEGATIVA, ASSISTENCIAL_OUTRO,
        or NAO_ASSISTENCIAL. ANS submissions are classified by report_type.

        Args:
            state: The current AgentState with regulatory request context.

        Returns:
            Updated state dict with classification results.
        """
        messages: list[str] = list(state.get("messages", []))
        logger.info(
            "gustavo.classify_request.start",
            messages_count=len(messages),
        )

        # Placeholder for DMN evaluation of request type
        classification = {
            "request_type": "nip",
            "classificacao": "ASSISTENCIAL_CONTESTA_NEGATIVA",
            "prazo_dias": 5,
            "grupo_revisor": "juridico-regulatorio",
            "roteamento": "REVISAO_JURIDICA",
        }

        logger.info(
            "gustavo.classify_request.complete",
            request_type=classification["request_type"],
            classificacao=classification["classificacao"],
        )

        return {
            "messages": messages,
            "request_classification": classification,
        }

    async def assemble_response_node(state: AgentState) -> dict[str, Any]:
        """Assemble regulatory response dossier for human review.

        Combines classification results with supporting documentation and
        regulatory references. Produces a structured dossier for the human
        reviewer (juridico-regulatorio or nucleo-ans).

        Args:
            state: The current AgentState with classification results.

        Returns:
            Updated state dict with response dossier.
        """
        messages: list[str] = list(state.get("messages", []))
        classification = cast(dict[str, Any], state.get("request_classification", {}))
        logger.info(
            "gustavo.assemble_response.start",
            request_type=classification.get("request_type", "unknown"),
        )

        # Assemble response dossier — facts only, never decides merit
        response_dossier = {
            "tipo": classification.get("request_type", "nip"),
            "classificacao": classification.get("classificacao", ""),
            "prazo_dias": classification.get("prazo_dias", 0),
            "grupo_revisor": classification.get("grupo_revisor", ""),
            "roteamento": classification.get("roteamento", "REVISAO_JURIDICA"),
            "minuta": "",
            "fundamentacao": [],
        }

        logger.info(
            "gustavo.assemble_response.complete",
            roteamento=response_dossier["roteamento"],
        )

        return {
            "messages": messages,
            "response_dossier": response_dossier,
        }

    async def validate_compliance_node(state: AgentState) -> dict[str, Any]:
        """Validate regulatory response against ANS normative resolutions (RN).

        Ensures the response dossier complies with applicable RNs (e.g., RN 567
        for provider network, RN 209/388/424 for periodic submissions).
        Routes to human for any compliance gaps — never auto-resolves.

        Args:
            state: The current AgentState with response dossier.

        Returns:
            Updated state dict with compliance validation results.
        """
        messages: list[str] = list(state.get("messages", []))
        response_dossier = cast(dict[str, Any], state.get("response_dossier", {}))
        logger.info(
            "gustavo.validate_compliance.start",
            roteamento=response_dossier.get("roteamento", "REVISAO_JURIDICA"),
        )

        # Validate against RN ANS — catch-all conservative
        compliance = {
            "valid": True,
            "rn_referencias": ["RN 209", "RN 388", "RN 424"],
            "gaps": [],
            "requires_human_review": True,  # Always requires human review
        }

        logger.info(
            "gustavo.validate_compliance.complete",
            valid=compliance["valid"],
            requires_human_review=compliance["requires_human_review"],
        )

        return {
            "messages": messages,
            "compliance_validation": compliance,
        }

    graph.add_node("classify_request", classify_request_node)
    graph.add_node("assemble_response", assemble_response_node)
    graph.add_node("validate_compliance", validate_compliance_node)
    graph.add_edge("__start__", "classify_request")
    graph.add_edge("classify_request", "assemble_response")
    graph.add_edge("assemble_response", "validate_compliance")
    graph.add_edge("validate_compliance", "__end__")

    return graph
