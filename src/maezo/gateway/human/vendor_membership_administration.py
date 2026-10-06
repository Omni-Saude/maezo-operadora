"""VW1-P0 vendor administrative acts; never a self-service signup or an identity feed.

ADR-0063 semantics on the `vendor` audience: the only actor who provisions is an operator's
human administrative session (auto-provisioning is forbidden), the only authorized source is the
vendor channel authority store (migration `0019_vendor_channel_authority`), and a receipt means
validated administrative act, never an applied access grant. The store being absent or empty
answers the typed refusal `SOURCE_UNAVAILABLE` — honest inertia, never a fabricated answer.
Existing AUTH-SL1 revoke-before-change and membership publication remain separate gates: revoke
is a NEW record carrying `revoked=true` (`portal/api/records.py`), never a DELETE and never an
in-place mutation of what a human may already have acted on. Mirrors
`provider_membership_administration.py` (PW1-B) shape for shape; that module stays a read-only
molde and is never edited from here.
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

ADMIN_ACTION = "vendor_membership_administration"


class VendorAdministrationReason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    AUTHORITY_UNPROVEN = "AUTHORITY_UNPROVEN"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    REVISION_CONFLICT = "REVISION_CONFLICT"


class VendorAdministrationError(PermissionError):
    def __init__(self, reason: VendorAdministrationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def require(condition: bool, reason: VendorAdministrationReason) -> None:
    if not condition:
        raise VendorAdministrationError(reason)


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


class VendorChannelState(Closed):
    """One `portal_vendor_channels` row (migration 0019): the channel's accreditation, opaquely.

    `revision` is the CAS token of the next administrative act over this channel; the row is
    never deleted — `revoked` is a written status like any other.
    """

    tenant: OpaqueRef
    channel_ref: OpaqueRef
    revision: int = Field(ge=0, lt=2**63 - 1)
    status: Literal["active", "revoked"]
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("aware timestamp required")
        return value


class VendorMembershipCommand(Closed):
    schema_version: Literal["vendor-membership-administration.v1"]
    command_id: OpaqueRef
    operation: Literal["grant", "revoke"]
    channel_ref: OpaqueRef
    expected_revision: int = Field(ge=0, lt=2**63 - 1)
    record: MembershipRecord = Field(repr=False)

    @model_validator(mode="after")
    def exact_vendor(self) -> Self:
        record = MembershipRecord.model_validate(self.record.model_dump(mode="python"))
        require(
            record.audience == "vendor"
            and record.revision == self.expected_revision + 1
            and len(record.subject_bindings) == 1
            and record.subject_bindings[0].kind == "vendor"
            and record.subject_bindings[0].resource_ref == self.channel_ref
            and record.revoked is (self.operation == "revoke"),
            VendorAdministrationReason.CONTRACT_MISMATCH,
        )
        roles = [r for m in record.memberships for r in m.roles]
        groups = [g for m in record.memberships for g in m.groups]
        memberships = [m.membership_ref for m in record.memberships]
        require(
            bool(roles)
            and len(roles) == len(set(roles))
            and len(groups) == len(set(groups))
            and len(memberships) == len(set(memberships)),
            VendorAdministrationReason.CONTRACT_MISMATCH,
        )
        return self


def command_bytes(command: VendorMembershipCommand) -> bytes:
    # JSON mode preserves membership revisions as integers and times as explicit ISO instants.
    validated = VendorMembershipCommand.model_validate(command.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def command_digest(command: VendorMembershipCommand) -> str:
    return hashlib.sha256(command_bytes(command)).hexdigest()


class VendorAdministrationReceipt(Closed):
    """A validated administrative act, never the applied access grant.

    `application_status` stays `pending` until the publication plane turns the record into
    access; nothing here fabricates an applied state.
    """

    schema_version: Literal["vendor-membership-administration-receipt.v1"]
    tenant: OpaqueRef
    command_id: OpaqueRef
    request_digest: Sha256Digest
    principal_ref: OpaqueRef
    channel_ref: OpaqueRef
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


class VendorChannelStore(Protocol):
    """Read-only view of the migration-0019 store; the absence of a row is UNKNOWN, not zero."""

    async def channel(self, tenant: str, channel_ref: str) -> VendorChannelState | None: ...


class AdministrationStore(Protocol):
    """Source-owned transaction validates locked authority/channel before its CAS.

    A source must never accept a parsed binding/receipt as authority, infer human
    approval from a PEP ALLOW, or write a second portal identity feed.
    """

    async def record(
        self, command: VendorMembershipCommand, binding: AdministrationBinding
    ) -> VendorAdministrationReceipt: ...


class VendorMembershipAdministration:
    """Human administrative acts over a vendor channel; no session is created here and no
    browser path reaches this class — the caller IS the operator's administrative authority."""

    def __init__(
        self,
        *,
        binding: AdministrationBinding,
        pep: PEP,
        channels: VendorChannelStore | None = None,
        store: AdministrationStore | None = None,
    ) -> None:
        self.binding = AdministrationBinding.model_validate(binding.model_dump(mode="python"))
        self.pep, self.channels, self.store = pep, channels, store

    async def record(self, command: VendorMembershipCommand) -> VendorAdministrationReceipt:
        try:
            command = VendorMembershipCommand.model_validate(command.model_dump(mode="python"))
            require(
                command.record.tenant == self.binding.tenant, VendorAdministrationReason.CONTRACT_MISMATCH
            )
            require(
                self.pep.matrix.tenant == self.binding.tenant
                and datetime.now(UTC) < self.binding.valid_until,
                VendorAdministrationReason.AUTHORITY_UNPROVEN,
            )
            # REQUIRE_HUMAN is satisfied only by a locked published human approval in the source.
            require(
                self.pep.evaluate(ADMIN_ACTION) in {Decision.ALLOW, Decision.REQUIRE_HUMAN},
                VendorAdministrationReason.AUTHORITY_UNPROVEN,
            )
            # The authorized source must EXIST and know this channel: absent store or absent row
            # is unknown, never an empty set to act over.
            require(
                self.channels is not None and self.store is not None,
                VendorAdministrationReason.SOURCE_UNAVAILABLE,
            )
            assert self.channels is not None and self.store is not None  # narrowed, fail-closed source check
            channel = await self.channels.channel(self.binding.tenant, command.channel_ref)
            require(channel is not None, VendorAdministrationReason.SOURCE_UNAVAILABLE)
            assert channel is not None
            require(
                (channel.tenant, channel.status) == (self.binding.tenant, "active")
                and channel.revision == command.expected_revision,
                VendorAdministrationReason.CONTRACT_MISMATCH,
            )
            receipt = await self.store.record(command, self.binding)
            require(
                datetime.now(UTC) < self.binding.valid_until, VendorAdministrationReason.AUTHORITY_UNPROVEN
            )
            receipt = VendorAdministrationReceipt.model_validate(receipt.model_dump(mode="python"))
            require(
                (
                    receipt.tenant,
                    receipt.command_id,
                    receipt.request_digest,
                    receipt.principal_ref,
                    receipt.channel_ref,
                    receipt.membership_revision,
                )
                == (
                    self.binding.tenant,
                    command.command_id,
                    command_digest(command),
                    command.record.principal_ref,
                    command.channel_ref,
                    command.record.revision,
                ),
                VendorAdministrationReason.CONTRACT_MISMATCH,
            )
            return receipt
        except VendorAdministrationError:
            raise
        except Exception:
            # Neither caller/DB validation text nor vendor exception causes cross the boundary.
            raise VendorAdministrationError(VendorAdministrationReason.SOURCE_UNAVAILABLE) from None
