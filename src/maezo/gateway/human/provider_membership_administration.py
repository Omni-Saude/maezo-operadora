"""PW1-B provider administrative acts; never an identity feed or an access grant.

ADR-0063: a current independently published human administration authority and
provider relationship permit recording a reviewed change. The receipt means
validated administrative act, not applied/native-published membership. Existing
AUTH-SL1 revoke-before-change and provider publication remain separate gates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maezo.gateway.pep import PEP, Decision
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

ADMIN_ACTION = "provider_membership_administration"


class AdministrationReason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    AUTHORITY_UNPROVEN = "AUTHORITY_UNPROVEN"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    REVISION_CONFLICT = "REVISION_CONFLICT"


class ProviderAdministrationError(PermissionError):
    def __init__(self, reason: AdministrationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def require(condition: bool, reason: AdministrationReason) -> None:
    if not condition:
        raise ProviderAdministrationError(reason)


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class AdministrationBinding(Closed):
    """Server-authenticated actor/session scope; constructing it authenticates nothing."""

    tenant: OpaqueRef
    actor_ref: OpaqueRef
    actor_session_ref: OpaqueRef
    source_authority_ref: OpaqueRef
    policy_revision: OpaqueRef
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("aware timestamp required")
        return value


class ProviderMembershipCommand(Closed):
    schema_version: Literal["provider-membership-administration.v1"]
    command_id: OpaqueRef
    operation: Literal["grant", "revoke"]
    provider_ref: OpaqueRef
    expected_revision: int = Field(ge=0, lt=2**63 - 1)
    record: MembershipRecord = Field(repr=False)

    @model_validator(mode="after")
    def exact_provider(self) -> Self:
        record = MembershipRecord.model_validate(self.record.model_dump(mode="python"))
        require(
            record.audience == "provider"
            and record.revision == self.expected_revision + 1
            and len(record.subject_bindings) == 1
            and record.subject_bindings[0].kind == "provider"
            and record.subject_bindings[0].resource_ref == self.provider_ref
            and record.revoked is (self.operation == "revoke"),
            AdministrationReason.CONTRACT_MISMATCH,
        )
        roles = [r for m in record.memberships for r in m.roles]
        groups = [g for m in record.memberships for g in m.groups]
        memberships = [m.membership_ref for m in record.memberships]
        require(
            bool(roles)
            and len(roles) == len(set(roles))
            and len(groups) == len(set(groups))
            and len(memberships) == len(set(memberships)),
            AdministrationReason.CONTRACT_MISMATCH,
        )
        return self


def command_bytes(command: ProviderMembershipCommand) -> bytes:
    # JSON mode preserves membership revisions as integers and times as explicit ISO instants.
    validated = ProviderMembershipCommand.model_validate(command.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def command_digest(command: ProviderMembershipCommand) -> str:
    return hashlib.sha256(command_bytes(command)).hexdigest()


class ProviderAdministrationReceipt(Closed):
    schema_version: Literal["provider-membership-administration-receipt.v1"]
    tenant: OpaqueRef
    command_id: OpaqueRef
    request_digest: Sha256Digest
    principal_ref: OpaqueRef
    provider_ref: OpaqueRef
    membership_revision: int = Field(gt=0, lt=2**63)
    administrative_act_ref: OpaqueRef
    authority_receipt_ref: OpaqueRef
    relationship_receipt_ref: OpaqueRef
    audit_receipt_ref: OpaqueRef
    committed_at: datetime
    proof_state: Literal["validated"]
    application_status: Literal["pending"]

    @field_validator("committed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return AdministrationBinding.aware(value)


class AdministrationStore(Protocol):
    """Source-owned transaction validates locked authority/relationship before its CAS.

    A source must never accept a parsed binding/receipt as authority, infer human
    approval from a PEP ALLOW, or write a second portal identity feed.
    """

    async def record(
        self, command: ProviderMembershipCommand, binding: AdministrationBinding
    ) -> ProviderAdministrationReceipt: ...


class ProviderMembershipAdministration:
    def __init__(
        self, *, binding: AdministrationBinding, pep: PEP, store: AdministrationStore | None = None
    ) -> None:
        self.binding = AdministrationBinding.model_validate(binding.model_dump(mode="python"))
        self.pep, self.store = pep, store

    async def record(self, command: ProviderMembershipCommand) -> ProviderAdministrationReceipt:
        try:
            command = ProviderMembershipCommand.model_validate(command.model_dump(mode="python"))
            require(command.record.tenant == self.binding.tenant, AdministrationReason.CONTRACT_MISMATCH)
            require(
                self.pep.matrix.tenant == self.binding.tenant
                and datetime.now(UTC) < self.binding.valid_until,
                AdministrationReason.AUTHORITY_UNPROVEN,
            )
            # REQUIRE_HUMAN is satisfied only by a locked published human approval in the source.
            require(
                self.pep.evaluate(ADMIN_ACTION) in {Decision.ALLOW, Decision.REQUIRE_HUMAN},
                AdministrationReason.AUTHORITY_UNPROVEN,
            )
            require(self.store is not None, AdministrationReason.SOURCE_UNAVAILABLE)
            assert self.store is not None  # narrowed after a fail-closed source check
            receipt = await self.store.record(command, self.binding)
            require(datetime.now(UTC) < self.binding.valid_until, AdministrationReason.AUTHORITY_UNPROVEN)
            receipt = ProviderAdministrationReceipt.model_validate(receipt.model_dump(mode="python"))
            require(
                (
                    receipt.tenant,
                    receipt.command_id,
                    receipt.request_digest,
                    receipt.principal_ref,
                    receipt.provider_ref,
                    receipt.membership_revision,
                )
                == (
                    self.binding.tenant,
                    command.command_id,
                    command_digest(command),
                    command.record.principal_ref,
                    command.provider_ref,
                    command.record.revision,
                ),
                AdministrationReason.CONTRACT_MISMATCH,
            )
            return receipt
        except ProviderAdministrationError:
            raise
        except Exception:
            # Neither caller/DB validation text nor provider exception causes cross the boundary.
            raise ProviderAdministrationError(AdministrationReason.SOURCE_UNAVAILABLE) from None
