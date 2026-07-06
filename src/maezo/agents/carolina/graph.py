"""Carolina — Revenue Cycle / Payment Analyst Agent (Phase 2, PAGTO).

Carolina handles payment validation, approval routing by alçada, and L2 payment
execution. Her graph consists of:
- validate_payment: validates payment data and computes alçada level
- route_approval: routes payment for approval based on alçada
- execute_payment: executes payment (L2 autonomy) after validation

The StateGraph follows the canonical AgentState pattern from maezo.runtime.harness.

L2 INVARIANT: Carolina executes payments at L2 autonomy level only. High-value
payments exceeding alçada must be routed to human approval. NO fraud accusation,
NO clinical decision, NO authorization denial.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState

logger = structlog.get_logger(__name__)


def build() -> StateGraph[AgentState]:
    """Build and return Carolina's StateGraph for revenue cycle / payments.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → validate_payment → route_approval → execute_payment → __end__

    - validate_payment: validates payment data and computes alçada level via DMN
    - route_approval: routes payment for human approval when alçada is exceeded
    - execute_payment: executes L2 payment after validation

    Returns:
        A StateGraph[AgentState] for Carolina's revenue cycle workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def validate_payment_node(state: AgentState) -> dict[str, Any]:
        """Validate payment data and compute alçada level.

        Validates payment amount, beneficiary data, contract references,
        and computes the alçada level via DMN pagamento_route.
        Routes to human approval when valor_excede_alcada.

        Args:
            state: The current AgentState with payment context.

        Returns:
            Updated state dict with validation results.
        """
        messages: list[str] = list(state.get("messages", []))
        logger.info(
            "carolina.validate_payment.start",
            messages_count=len(messages),
        )

        # Placeholder for DMN evaluation of payment validation
        validation = {
            "valid": True,
            "valor_brl": 0.0,
            "alcada": "L2",
            "excede_alcada": False,
            "requer_aprovacao_humana": False,
            "errors": [],
        }

        logger.info(
            "carolina.validate_payment.complete",
            valid=validation["valid"],
            alcada=validation["alcada"],
        )

        return {
            "messages": messages,
            "payment_validation": validation,
        }

    async def route_approval_node(state: AgentState) -> dict[str, Any]:
        """Route payment for approval based on alçada level.

        Payments within L2 alçada proceed to execute_payment.
        Payments exceeding alçada are routed to human approval
        (APROVACAO_ALCADA_SUPERIOR).

        Args:
            state: The current AgentState with payment validation.

        Returns:
            Updated state dict with routing decision.
        """
        messages: list[str] = list(state.get("messages", []))
        validation = cast(dict[str, Any], state.get("payment_validation", {}))
        logger.info(
            "carolina.route_approval.start",
            alcada=validation.get("alcada", "L2"),
        )

        # Route based on alçada
        excede_alcada = cast(bool, validation.get("excede_alcada", False))
        if excede_alcada:
            routing = {
                "status": "pendente_aprovacao",
                "grupo_aprovador": "financeiro-alcada-superior",
                "processo": "SP-OP-PAGTO-001",
                "motivo": "Valor excede alcada L2 — requer aprovacao humana",
            }
        else:
            routing = {
                "status": "aprovado_l2",
                "grupo_aprovador": "",
                "processo": "",
                "motivo": "Dentro da alcada L2 — prossegue para execucao",
            }

        logger.info(
            "carolina.route_approval.complete",
            status=routing["status"],
        )

        return {
            "messages": messages,
            "payment_routing": routing,
        }

    async def execute_payment_node(state: AgentState) -> dict[str, Any]:
        """Execute L2 payment after validation and routing.

        Executes the payment when within L2 alçada. If the payment was
        routed to human approval, this node awaits the human decision
        before proceeding.

        Args:
            state: The current AgentState with routing decision.

        Returns:
            Updated state dict with execution result.
        """
        messages: list[str] = list(state.get("messages", []))
        routing = cast(dict[str, Any], state.get("payment_routing", {}))
        logger.info(
            "carolina.execute_payment.start",
            status=routing.get("status", "unknown"),
        )

        # Execute only if approved
        can_execute = routing.get("status") == "aprovado_l2"
        execution = {
            "executed": can_execute,
            "status": "executado" if can_execute else "aguardando_aprovacao",
            "protocolo": "",
            "motivo_pendencia": "" if can_execute else str(routing.get("motivo", "")),
        }

        if can_execute:
            logger.info("carolina.execute_payment.executed")
        else:
            logger.info(
                "carolina.execute_payment.pending_approval",
                motivo=routing.get("motivo", ""),
            )

        return {
            "messages": messages,
            "payment_execution": execution,
        }

    graph.add_node("validate_payment", validate_payment_node)
    graph.add_node("route_approval", route_approval_node)
    graph.add_node("execute_payment", execute_payment_node)
    graph.add_edge("__start__", "validate_payment")
    graph.add_edge("validate_payment", "route_approval")
    graph.add_edge("route_approval", "execute_payment")
    graph.add_edge("execute_payment", "__end__")

    return graph
