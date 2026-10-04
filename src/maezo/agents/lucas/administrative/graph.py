"""Two administrative step consumers over one proposed capability service.

This candidate is disabled by default and absent from all production roots.
It consumes source facts through the capability boundary without billing's
assess/DMNs, LLM rules, direct effects, or claims of journey completion.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any, Final, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot
from langsmith import tracing_context

from maezo.gateway.capabilities.models import (
    CapabilityOutcome,
    CapabilityRefusalReason,
    VerifiedFulfillmentFact,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pseudonymizer import Pseudonymizer

from .handoff import AdministrativeInputError, AdministrativeTask, handoff_priority, require_reference
from .state import (
    AdministrativeGraphInput,
    AdministrativeState,
    administrative_checkpoint_config,
    validate_administrative_input,
)

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


async def _in_administrative_context[Result](operation: Callable[[], Awaitable[Result]]) -> Result:
    """Detach framework configuration/tracing while retaining application context.

    Empty explicit metadata does not override LangGraph's ambient merge. The
    public helper copies the current Context and replaces only Runnable config;
    a task must execute in that copy for async context propagation to take effect.
    Tracing is scoped to the child, never changed in the parent or environment.
    """

    async def invoke() -> Result:
        with tracing_context(parent=False, metadata={}, tags=[], enabled=False):
            return await operation()

    with set_config_context({}) as context:
        return await asyncio.create_task(invoke(), context=context)


class CompiledAdministrativeConsumer:
    """Closed async entry point; a raw Pregel graph is never a public input API.

    Validation precedes even LangGraph's input checkpoint. Checkpoint roots are
    derived from the validated turn and gateway-owned key, never caller-selected.
    Commands, state mutation, arbitrary resume/replay and graph delegation have
    no administrative contract and are deliberately absent from this interface.
    """

    def __init__(
        self,
        *,
        consumer: AdministrativeConsumer,
        compiled: CompiledStateGraph[
            AdministrativeState, None, AdministrativeGraphInput, AdministrativeState
        ],
        pseudonymizer: Pseudonymizer | None,
    ) -> None:
        self.__consumer = consumer
        self.__compiled = compiled
        self.__pseudonymizer = pseudonymizer

    def __config(self, journey_ref: str, supplied: RunnableConfig | None = None) -> RunnableConfig | None:
        if self.__pseudonymizer is None:
            if supplied is not None:
                raise AdministrativeInputError("administrative_checkpoint_config_mismatch")
            return None
        expected = administrative_checkpoint_config(
            task_type=self.__consumer.task_type,
            tenant_ref=self.__consumer.tenant_ref,
            journey_ref=journey_ref,
            pseudonymizer=self.__pseudonymizer,
        )
        if supplied is not None:
            configurable = supplied.get("configurable") if type(supplied) is dict else None
            if (
                type(supplied) is not dict
                or frozenset(supplied) != frozenset({"configurable"})
                or type(configurable) is not dict
                or frozenset(configurable) != frozenset({"thread_id", "checkpoint_ns"})
                or type(configurable["thread_id"]) is not str
                or type(configurable["checkpoint_ns"]) is not str
                or configurable != expected["configurable"]
            ):
                raise AdministrativeInputError("administrative_checkpoint_config_mismatch")
        return expected

    async def ainvoke(
        self, values: Mapping[str, object], config: RunnableConfig | None = None
    ) -> AdministrativeState:
        initial = validate_administrative_input(values, memberships=self.__consumer.service.memberships)
        turn = initial["turn"]
        if (
            turn.envelope.tenant_ref != self.__consumer.tenant_ref
            or turn.handoff.task_type != self.__consumer.task_type
        ):
            raise AdministrativeInputError("administrative_consumer_object_mismatch")
        trusted_config = self.__config(turn.envelope.journey_ref, config)
        return cast(
            AdministrativeState,
            await _in_administrative_context(lambda: self.__compiled.ainvoke(initial, trusted_config)),
        )

    async def aget_state(self, *, journey_ref: str) -> StateSnapshot:
        if self.__pseudonymizer is None:
            raise AdministrativeInputError("administrative_checkpoint_unavailable")
        config = self.__config(require_reference(journey_ref))
        assert config is not None
        return await _in_administrative_context(lambda: self.__compiled.aget_state(config))


class AdministrativeGraphBuilder:
    """Compile only the fixed topology behind the validated consumer boundary."""

    def __init__(self, consumer: AdministrativeConsumer) -> None:
        self.__consumer = consumer

    def compile(
        self,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        pseudonymizer: Pseudonymizer | None = None,
    ) -> CompiledAdministrativeConsumer:
        if checkpointer is not None and (
            not isinstance(checkpointer, BaseCheckpointSaver) or not isinstance(pseudonymizer, Pseudonymizer)
        ):
            raise AdministrativeInputError("administrative_checkpoint_identity_unavailable")
        if checkpointer is None and pseudonymizer is not None:
            raise AdministrativeInputError("administrative_checkpoint_unavailable")
        graph = StateGraph(
            AdministrativeState, input_schema=AdministrativeGraphInput, output_schema=AdministrativeState
        )
        graph.add_node("receive", self.__consumer.receive)
        graph.add_node("execute", self.__consumer.execute)
        graph.add_edge(START, "receive")
        graph.add_edge("receive", "execute")
        graph.add_edge("execute", END)
        return CompiledAdministrativeConsumer(
            consumer=self.__consumer,
            # Without an injected saver, also prevent ambient parent inheritance.
            # A configured real saver is passed through unchanged.
            compiled=graph.compile(checkpointer=checkpointer if checkpointer is not None else False),
            pseudonymizer=pseudonymizer,
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
        turn = validate_administrative_input(
            {"turn": state.get("turn")}, memberships=self.service.memberships
        )["turn"]
        if turn.envelope.tenant_ref != self.tenant_ref or turn.handoff.task_type != self.task_type:
            raise AdministrativeInputError("administrative_consumer_object_mismatch")
        return {"turn": turn, "technical_status": "received", "outcome": None}

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

    def build(self) -> AdministrativeGraphBuilder:
        return AdministrativeGraphBuilder(self)

    async def invoke(self, values: Mapping[str, object]) -> AdministrativeState:
        """One candidate step through the strict boundary, without a checkpointer."""
        return await self.build().compile().ainvoke(values)


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
