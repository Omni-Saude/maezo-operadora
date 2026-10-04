"""Closed candidate handoff after the existing Helena health/human boundary.

This shape is an internal W0 proposal, not an A2A wire or a ratified extension
of Helena's conduct. The existing dispatcher never constructs it. Its tokens
carry no message body, symptom, diagnosis, commercial risk or raw recipient.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

AdministrativeTask = Literal["journey.compras.step", "journey.suporte.step"]
AdministrativePriority = Literal["health", "human", "administrative"]
HANDOFF_SCHEMA: Final[str] = "v21-administrative-handoff.proposed.v1"
ADMINISTRATIVE_TASKS: Final[tuple[str, ...]] = get_args(AdministrativeTask)
_MESSAGE_REF: Final[re.Pattern[str]] = re.compile(r"hk1_[0-9a-f]{64}\Z")
_HANDOFF_FIELDS: Final[frozenset[str]] = frozenset(
    {"schema_version", "task_type", "tenant_ref", "journey_ref", "message_ref"}
)


class AdministrativeInputError(ValueError):
    """Bounded technical error, without untrusted values or validation text."""


def require_reference(value: object) -> str:
    """Shape only: references do not prove authority or infer source semantics."""
    if type(value) is not str or not value.strip():
        raise AdministrativeInputError("administrative_reference_unavailable")
    return value


def require_message_ref(value: object) -> str:
    if type(value) is not str or _MESSAGE_REF.fullmatch(value) is None:
        raise AdministrativeInputError("administrative_message_reference_invalid")
    return value


@dataclass(frozen=True, slots=True)
class AdministrativeHandoff:
    schema_version: str
    task_type: AdministrativeTask
    tenant_ref: str
    journey_ref: str
    message_ref: str

    def __post_init__(self) -> None:
        if self.schema_version != HANDOFF_SCHEMA or self.task_type not in ADMINISTRATIVE_TASKS:
            raise AdministrativeInputError("administrative_handoff_contract_mismatch")
        require_reference(self.tenant_ref)
        require_reference(self.journey_ref)
        require_message_ref(self.message_ref)


def parse_handoff(payload: object) -> AdministrativeHandoff:
    """Reject unknown/output/clinical fields before constructing a candidate."""
    if type(payload) is AdministrativeHandoff:
        return payload
    if not isinstance(payload, Mapping) or frozenset(payload) != _HANDOFF_FIELDS:
        raise AdministrativeInputError("administrative_handoff_contract_mismatch")
    schema = payload["schema_version"]
    task = payload["task_type"]
    if type(schema) is not str or type(task) is not str or task not in ADMINISTRATIVE_TASKS:
        raise AdministrativeInputError("administrative_handoff_contract_mismatch")
    return AdministrativeHandoff(
        schema_version=schema,
        task_type=cast(AdministrativeTask, task),
        tenant_ref=require_reference(payload["tenant_ref"]),
        journey_ref=require_reference(payload["journey_ref"]),
        message_ref=require_message_ref(payload["message_ref"]),
    )


def handoff_priority(
    handoff: AdministrativeHandoff,
    *,
    current_message_ref: str,
    health_priority: bool,
    human_requested: bool,
) -> AdministrativePriority:
    """Health wins over human request; either interrupts administrative work.

    Safety flags must come from the trusted ingress, after its current triage;
    their presence is not signature, medical approval or authority evidence.
    The message reference is recomputed by that ingress from the current delivery.
    """
    require_message_ref(current_message_ref)
    if type(health_priority) is not bool or type(human_requested) is not bool:
        raise AdministrativeInputError("administrative_priority_contract_mismatch")
    if not hmac.compare_digest(handoff.message_ref, current_message_ref):
        raise AdministrativeInputError("administrative_handoff_stale_message")
    if health_priority:
        return "health"
    if human_requested:
        return "human"
    return "administrative"
