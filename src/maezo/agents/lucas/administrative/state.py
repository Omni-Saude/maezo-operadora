"""Input/output partition and checkpoint identity for the internal candidate.

The graph remembers technical execution only. Routing state stays in the
existing conversation router; business currentness/CAS/receipts stay at the
qualified source. A LangGraph checkpoint is not a business revision or receipt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, TypedDict

from langchain_core.runnables import RunnableConfig

from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    REQUEST_MODELS,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityOutcome,
    DeclaredMemberships,
    parse_envelope,
    parse_request,
)
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.security import hash_message_id
from maezo.runtime.checkpoint import checkpoint_thread_config

from .handoff import (
    ADMINISTRATIVE_TASKS,
    AdministrativeHandoff,
    AdministrativeInputError,
    AdministrativeTask,
    handoff_priority,
    parse_handoff,
    require_message_ref,
    require_reference,
)

AdministrativeStatus = Literal[
    "received",
    "disabled",
    "interrupted_health",
    "interrupted_human",
    "operation_not_in_task",
    "succeeded",
    "refused",
]
INPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {"envelope", "payload", "handoff", "current_message_ref", "health_priority", "human_requested"}
)


@dataclass(frozen=True, slots=True)
class AdministrativeInput:
    """Input carrier; its class/type identity never substitutes for deep parsing.

    The factory and every compiled runner reconstruct this carrier from the
    closed contracts. Existing instances, including checkpoint revival, must
    pass validate_administrative_input with current composition memberships.
    """

    envelope: CapabilityEnvelope = field(repr=False)
    payload: CandidateDTO = field(repr=False)
    handoff: AdministrativeHandoff = field(repr=False)
    current_message_ref: str = field(repr=False)
    health_priority: bool
    human_requested: bool

    def __post_init__(self) -> None:
        if type(self.envelope) is not CapabilityEnvelope or type(self.handoff) is not AdministrativeHandoff:
            raise AdministrativeInputError("administrative_input_contract_mismatch")
        if type(self.payload) is not REQUEST_MODELS.get(self.envelope.operation_name):
            raise AdministrativeInputError("administrative_payload_contract_mismatch")
        if (
            self.envelope.tenant_ref != self.handoff.tenant_ref
            or self.envelope.journey_ref != self.handoff.journey_ref
        ):
            raise AdministrativeInputError("administrative_handoff_object_mismatch")
        handoff_priority(
            self.handoff,
            current_message_ref=self.current_message_ref,
            health_priority=self.health_priority,
            human_requested=self.human_requested,
        )


class AdministrativeState(TypedDict, total=False):
    turn: AdministrativeInput
    technical_status: AdministrativeStatus
    outcome: CapabilityOutcome | None


class AdministrativeGraphInput(TypedDict):
    """LangGraph input channels; output channels belong only to its nodes."""

    turn: AdministrativeInput


def new_administrative_state(
    values: Mapping[str, object], *, memberships: DeclaredMemberships | None = None
) -> AdministrativeGraphInput:
    """Strict caller boundary; outcomes and business facts are never caller input."""
    if not isinstance(values, Mapping) or frozenset(values) != INPUT_FIELDS:
        raise AdministrativeInputError("administrative_input_contract_mismatch")
    try:
        envelope = parse_envelope(values["envelope"])
        # JR1/JR2 retain their closed v21 contract. The shared parser also
        # accepts provider envelopes; that is not a migration of this consumer.
        # Close the reparsed boundary before any payload or checkpoint state.
        if (
            not isinstance(envelope, CapabilityEnvelope)
            or envelope.schema_version != CANDIDATE_SCHEMA_VERSION
        ):
            raise AdministrativeInputError("administrative_envelope_or_payload_contract_mismatch")
        request = parse_request(envelope.operation_name, values["payload"], memberships=memberships)
    except CapabilityContractError:
        raise AdministrativeInputError("administrative_envelope_or_payload_contract_mismatch") from None
    health = values["health_priority"]
    human = values["human_requested"]
    if type(health) is not bool or type(human) is not bool:
        raise AdministrativeInputError("administrative_priority_contract_mismatch")
    return {
        "turn": AdministrativeInput(
            envelope=envelope,
            payload=request,
            handoff=parse_handoff(values["handoff"]),
            current_message_ref=require_message_ref(values["current_message_ref"]),
            health_priority=health,
            human_requested=human,
        )
    }


def validate_administrative_input(
    values: Mapping[str, object], *, memberships: DeclaredMemberships | None = None
) -> AdministrativeGraphInput:
    """Rebuild every public input before LangGraph can touch a checkpoint.

    Accept the strict caller envelope or the factory's input-only state. Existing
    DTO/dataclass instances are reparsed with the current composition membership;
    neither frozen nor model_copy/model_construct establishes validity.
    """
    if not isinstance(values, Mapping):
        raise AdministrativeInputError("administrative_input_contract_mismatch")
    if frozenset(values) == frozenset({"turn"}):
        turn = values["turn"]
        if type(turn) is not AdministrativeInput:
            raise AdministrativeInputError("administrative_input_contract_mismatch")
        values = {
            "envelope": turn.envelope,
            "payload": turn.payload,
            "handoff": turn.handoff,
            "current_message_ref": turn.current_message_ref,
            "health_priority": turn.health_priority,
            "human_requested": turn.human_requested,
        }
    return new_administrative_state(values, memberships=memberships)


def administrative_checkpoint_config(
    *, task_type: AdministrativeTask, tenant_ref: str, journey_ref: str, pseudonymizer: Pseudonymizer
) -> RunnableConfig:
    """Distinct root thread per task/tenant/journey; checkpoint_ns is not isolation.

    The trusted composition must inject its gateway-built pseudonymizer. The
    candidate consumer uses no saver by default. This helper allocates neither
    storage nor business identity and contains no reversible journey/recipient.
    """
    require_reference(tenant_ref)
    require_reference(journey_ref)
    if task_type not in ADMINISTRATIVE_TASKS:
        raise AdministrativeInputError("administrative_checkpoint_task_mismatch")
    material = json.dumps(
        ["lucas-administrative.proposed.v1", task_type, tenant_ref, journey_ref],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    pseudonym = hash_message_id(material, tenant_ref, pseudonymizer)
    return checkpoint_thread_config(f"lucas:administrative:{pseudonym}")
