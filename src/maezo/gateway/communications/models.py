"""Server-only communication authority. Current source adapters are mandatory."""

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Sha256Digest
from maezo.portal.engine.profile import canonicalize

Operation = Literal[
    "create_content",
    "publish",
    "list_messages",
    "read_message",
    "list_history",
    "read_history",
    "read_content",
    "index_receipt",
]
MESSAGE_FIELDS = frozenset(
    {"communication_ref", "sender_kind", "authored_at", "inbox_available_at", "delivery_state", "body_ref"}
)
HISTORY_FIELDS = frozenset(
    {"event_ref", "sequence", "occurred_at", "kind", "communication_ref", "command_ref", "receipt_ref"}
)


def now() -> datetime:
    return datetime.now(UTC)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


def identity(principal: HumanPrincipal) -> str:
    return fingerprint(
        {
            "tenant": principal.tenant,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "principal_ref": principal.principal_ref,
        }
    )


def alive(*deadlines: datetime) -> datetime:
    if not deadlines or any(d.tzinfo is None for d in deadlines):
        raise ExternalCaseError("unavailable")
    ceiling = min(deadlines)
    if now() >= ceiling:
        raise ExternalCaseError("denied")
    return ceiling


class CommunicationScope(Closed):
    tenant: OpaqueRef
    environment: OpaqueRef


class CommunicationAccess(Closed):
    scope: CommunicationScope
    principal: HumanPrincipal = Field(repr=False)
    operation: Operation
    case_ref: ResourceRef
    resource_ref: OpaqueRef | None = None
    request_digest: Sha256Digest


class IntendedRecipient(Closed):
    identity_digest: Sha256Digest
    audience: Literal["staff", "beneficiary", "provider"]
    source_revision: DecimalRevision
    policy_digest: Sha256Digest
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("aware deadline required")
        return value


class CommunicationGrant(Closed):
    access: CommunicationAccess
    authority_receipt_ref: ResourceRef
    authority_digest: Sha256Digest
    policy_valid_until: datetime
    valid_until: datetime
    permitted_fields: tuple[str, ...]
    intended_recipients: tuple[IntendedRecipient, ...] = ()

    @field_validator("valid_until", "policy_valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("aware deadline required")
        return value

    @model_validator(mode="after")
    def exact_sets(self) -> Self:
        if len(set(self.permitted_fields)) != len(self.permitted_fields):
            raise ValueError("duplicate field")
        ids = [r.identity_digest for r in self.intended_recipients]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate recipient")
        if self.access.operation == "publish" and not ids:
            raise ValueError("explicit recipients required")
        if self.access.operation != "publish" and ids:
            raise ValueError("unexpected recipients")
        return self

    def ceiling(self) -> datetime:
        return alive(
            self.valid_until, self.policy_valid_until, *(r.valid_until for r in self.intended_recipients)
        )


class CommunicationAuthority(ABC):
    """Independently resolves live case, relationship, consent, recipient and field policy.

    DTOs are never source authority. Denied resources raise `denied`; unavailable or
    uncertain source state must raise `unavailable`, never silently return empty grants.
    Raw content is not accepted by this interface; case/ref/digest are sufficient to
    ask permission, while actual PHI storage verifies content custody itself.
    """

    scope: CommunicationScope

    @abstractmethod
    async def authorize(self, access: CommunicationAccess) -> CommunicationGrant:
        raise NotImplementedError
