"""Lucas Ferreira — Beneficiary Experience Agent (Phase 2, CANCEL/INADIMPLENCIA).

Lucas handles beneficiary billing navigation, payment confirmation, and escalates
inadimplencia/cancelamento to human agents. His graph consists of:
- assess_beneficiary: evaluates beneficiary situation (billing status, payments)
- prepare_communication: prepares WhatsApp communications
- escalate_to_human: escalates to human when inadimplencia/cancelamento triggers

The StateGraph follows the canonical AgentState pattern from maezo.runtime.harness.

L0 HARD INVARIANT: Lucas NEVER exercises contract_termination (rescission/suspension
only in SP-OP-CANCEL-001 User Task), authorization_denial (deny reimbursement/coverage
= human), high_value_payment, or fraud_accusation. Inadimplencia/atraso/cancelamento
request => ESCALATES to human. No DMN/branch cancels, denies, or suspends.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState

logger = structlog.get_logger(__name__)


def build() -> StateGraph[AgentState]:
    """Build and return Lucas's StateGraph for beneficiary experience.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → assess_beneficiary → prepare_communication → escalate_to_human → __end__

    - assess_beneficiary: evaluates billing status, payment conciliation, and risk
    - prepare_communication: prepares WhatsApp message for beneficiary
    - escalate_to_human: routes to human when escalation triggers fire

    Returns:
        A StateGraph[AgentState] for Lucas's beneficiary experience workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def assess_beneficiary_node(state: AgentState) -> dict[str, Any]:
        """Assess beneficiary situation: billing status, payment conciliation, risk.

        Evaluates billing admissibility via DMN lucas_billing_admissibility,
        checks payment conciliation (CNAB, pre-resolved by worker), and determines
        whether the case can be handled autonomously or must escalate.

        Args:
            state: The current AgentState with beneficiary context.

        Returns:
            Updated state dict with assessment results.
        """
        messages: list[str] = list(state.get("messages", []))
        logger.info(
            "lucas.assess_beneficiary.start",
            messages_count=len(messages),
        )

        # Placeholder for DMN evaluation of beneficiary billing status
        assessment = {
            "status": "em_dia",
            "pagamento_conciliado": True,
            "ciclos_sem_conciliacao": 0,
            "inadimplencia": False,
            "pedido_cancelamento": False,
            "roteamento": "INFORMACIONAL",
            "requer_escalacao": False,
        }

        logger.info(
            "lucas.assess_beneficiary.complete",
            status=assessment["status"],
            requer_escalacao=assessment["requer_escalacao"],
        )

        return {
            "messages": messages,
            "beneficiary_assessment": assessment,
        }

    async def prepare_communication_node(state: AgentState) -> dict[str, Any]:
        """Prepare WhatsApp communication for the beneficiary.

        Generates informational messages: boleto 2a via, payment confirmation,
        collection nudges (never threatens suspension). Uses mcp-whatsapp.send_message
        for text delivery.

        Args:
            state: The current AgentState with beneficiary assessment.

        Returns:
            Updated state dict with prepared communication.
        """
        messages: list[str] = list(state.get("messages", []))
        assessment = cast(dict[str, Any], state.get("beneficiary_assessment", {}))
        logger.info(
            "lucas.prepare_communication.start",
            status=assessment.get("status", "unknown"),
        )

        # Prepare communication based on assessment
        requires_escalation = cast(bool, assessment.get("requer_escalacao", False))
        communication = {
            "canal": "whatsapp",
            "tipo": "lembrete_cobranca" if not requires_escalation else "escalacao_humano",
            "mensagem": "",
            "enviada": False,
        }

        logger.info(
            "lucas.prepare_communication.complete",
            tipo=communication["tipo"],
        )

        return {
            "messages": messages,
            "communication": communication,
        }

    async def escalate_to_human_node(state: AgentState) -> dict[str, Any]:
        """Escalate to human when escalation triggers fire.

        Routes to SP-OP-ESCALATION-001 when inadimplencia, cancelamento,
        DMN unavailable, or ambiguity triggers fire. May also route to
        SP-OP-CANCEL-001 when there's a cancelamento request/indicio.

        NEVER decides the adverse outcome — the human does.

        Args:
            state: The current AgentState with assessment and communication.

        Returns:
            Updated state dict with escalation routing.
        """
        messages: list[str] = list(state.get("messages", []))
        assessment = cast(dict[str, Any], state.get("beneficiary_assessment", {}))
        logger.info(
            "lucas.escalate_to_human.start",
            requer_escalacao=assessment.get("requer_escalacao", False),
        )

        # Determine escalation routing
        requires_escalation = cast(bool, assessment.get("requer_escalacao", False))
        has_cancelamento = cast(bool, assessment.get("pedido_cancelamento", False))

        if has_cancelamento:
            process = "SP-OP-CANCEL-001"
            grupo = "atendimento-cancelamento"
            motivo = "Pedido/indicio de cancelamento — humano decide RESCINDIR/MANTER/SUSPENDER"
        elif requires_escalation:
            process = "SP-OP-ESCALATION-001"
            grupo = "atendimento-humano"
            motivo = "Inadimplencia/atraso — humano decide encaminhamento"
        else:
            process = ""
            grupo = ""
            motivo = "Sem gatilho de escalacao"

        escalation = {
            "escalated": requires_escalation or has_cancelamento,
            "process": process,
            "grupo_humano": grupo,
            "motivo": motivo,
        }

        logger.info(
            "lucas.escalate_to_human.complete",
            escalated=escalation["escalated"],
            process=process,
        )

        return {
            "messages": messages,
            "escalation": escalation,
        }

    graph.add_node("assess_beneficiary", assess_beneficiary_node)
    graph.add_node("prepare_communication", prepare_communication_node)
    graph.add_node("escalate_to_human", escalate_to_human_node)
    graph.add_edge("__start__", "assess_beneficiary")
    graph.add_edge("assess_beneficiary", "prepare_communication")
    graph.add_edge("prepare_communication", "escalate_to_human")
    graph.add_edge("escalate_to_human", "__end__")

    return graph
