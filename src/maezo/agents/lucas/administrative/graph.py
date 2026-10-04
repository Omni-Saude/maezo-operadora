"""Two administrative step consumers over one proposed capability service.

This candidate is disabled by default and absent from all production roots.
It consumes source facts through the capability boundary without billing's
assess/DMNs, LLM rules, direct effects, or claims of journey completion.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from langgraph.graph import END, START, StateGraph

from maezo.gateway.capabilities.models import (
    CapabilityOutcome,
    CapabilityRefusalReason,
    VerifiedFulfillmentFact,
)
from maezo.gateway.capabilities.service import CapabilityService

from .handoff import AdministrativeInputError, AdministrativeTask, handoff_priority, require_reference
from .state import AdministrativeInput, AdministrativeState

# Candidate dispatch topology from the admitted ledger, not an autonomy grant.
_COMMON_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "access.resolve",
        "case.open_or_update",
        "external_wait.settle",
        "notice.prepare_or_send",
        "milestone.publish",
    }
)
OPERATIONS_BY_TASK: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "journey.compras.step": _COMMON_OPERATIONS
        | frozenset(
            {
                "offer.compose",
                "acceptance.record",
                "enrollment.request",
                "reservation.command",
                "fulfillment.observe",
            }
        ),
        "journey.suporte.step": _COMMON_OPERATIONS | frozenset({"feedback.record"}),
    }
)


class AdministrativeConsumer:
    """A fixed candidate task/tenant; the shared gateway owns effective admission.

    `enabled=True` permits unit invocation of this candidate only. It cannot
    publish contracts, mount ingress, authorize a source or bypass the gateway.
    No saver is installed here, and no business state is advanced locally.
    """

    def __init__(
        self,
        *,
        task_type: AdministrativeTask,
        tenant_ref: str,
        service: CapabilityService,
        enabled: bool = False,
    ) -> None:
        if task_type not in OPERATIONS_BY_TASK or type(enabled) is not bool:
            raise AdministrativeInputError("administrative_consumer_contract_mismatch")
        if enabled and not service.is_bound_to_task(task_type):
            raise AdministrativeInputError("administrative_consumer_task_binding_unavailable")
        self.task_type = task_type
        self.tenant_ref = require_reference(tenant_ref)
        self.service = service
        self.enabled = enabled

    def receive(self, state: AdministrativeState) -> AdministrativeState:
        """Reset prior/output-planted state and validate the current input."""
        turn = state.get("turn")
        if type(turn) is not AdministrativeInput:
            raise AdministrativeInputError("administrative_input_contract_mismatch")
        # Recheck even when a caller invokes the graph without the strict factory.
        turn.__post_init__()
        if turn.envelope.tenant_ref != self.tenant_ref or turn.handoff.task_type != self.task_type:
            raise AdministrativeInputError("administrative_consumer_object_mismatch")
        return {"technical_status": "received", "outcome": None}

    async def execute(self, state: AdministrativeState) -> AdministrativeState:
        turn = state["turn"]
        priority = handoff_priority(
            turn.handoff,
            current_message_ref=turn.current_message_ref,
            health_priority=turn.health_priority,
            human_requested=turn.human_requested,
        )
        if priority == "health":
            return {"technical_status": "interrupted_health", "outcome": None}
        if priority == "human":
            return {"technical_status": "interrupted_human", "outcome": None}
        if not self.enabled:
            return {"technical_status": "disabled", "outcome": None}
        if turn.envelope.operation_name not in OPERATIONS_BY_TASK[self.task_type]:
            return {"technical_status": "operation_not_in_task", "outcome": None}
        outcome = await self.service.execute(turn.envelope, turn.payload)
        # A clinical context reference is not administrative fulfillment data.
        # Refuse disclosure/persistence rather than silently dropping that field.
        if (
            isinstance(outcome.result, VerifiedFulfillmentFact)
            and outcome.result.clinical_result_context_ref is not None
        ):
            outcome = CapabilityOutcome.refused(CapabilityRefusalReason.CONTRACT_MISMATCH)
        return {
            "technical_status": "succeeded" if outcome.succeeded else "refused",
            "outcome": outcome,
        }

    def build(self) -> StateGraph[AdministrativeState]:
        graph = StateGraph(AdministrativeState)
        graph.add_node("receive", self.receive)
        graph.add_node("execute", self.execute)
        graph.add_edge(START, "receive")
        graph.add_edge("receive", "execute")
        graph.add_edge("execute", END)
        return graph

    async def invoke(self, values: Mapping[str, object]) -> AdministrativeState:
        """One candidate step through the strict boundary, without a checkpointer."""
        from .state import new_administrative_state

        initial = new_administrative_state(values, memberships=self.service.memberships)
        result = await self.build().compile(checkpointer=None).ainvoke(initial)
        return cast(AdministrativeState, result)


def compras_consumer(
    *, tenant_ref: str, service: CapabilityService, enabled: bool = False
) -> AdministrativeConsumer:
    return AdministrativeConsumer(
        task_type="journey.compras.step", tenant_ref=tenant_ref, service=service, enabled=enabled
    )


def suporte_consumer(
    *, tenant_ref: str, service: CapabilityService, enabled: bool = False
) -> AdministrativeConsumer:
    return AdministrativeConsumer(
        task_type="journey.suporte.step", tenant_ref=tenant_ref, service=service, enabled=enabled
    )
