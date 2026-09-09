"""D6 public receipt and independent current resource authorization.

This projection deliberately does not pretend D5 supplies D3's engine_commit_ref.
Receipt access may survive completion, but requires an explicit current resource
grant from the trusted projector plus a freshly resolved human session. Possession
of task/command IDs or the original admission is never a read capability.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import field_validator, model_validator

from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Revision, Sha256Digest

from .models import Closed, Scope, Timed
from .projection import DecimalRevision


class ReceiptIdentity(Closed):
    tenant: OpaqueRef
    task_id: OpaqueRef
    command_id: OpaqueRef
    payload_digest: Sha256Digest
    principal_ref: OpaqueRef
    workload_ref: OpaqueRef


class PublicReceipt(ReceiptIdentity):
    schema_version: Literal["human-public-receipt.v1"] = "human-public-receipt.v1"
    status: Literal["pending", "committed", "conflict"]
    audit_intent_ref: OpaqueRef
    audit_intent_hash: Sha256Digest
    audit_result_ref: Sha256Digest | None = None
    engine_receipt_ref: OpaqueRef | None = None
    engine_recorded_at: datetime | None = None
    consumed_task_revision: DecimalRevision | None = None
    resulting_task_revision: DecimalRevision | None = None
    technical_code: Literal["REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"] | None = None

    @field_validator("engine_recorded_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != timedelta(0) or value > datetime.now(UTC)
        ):
            raise ValueError("engine receipt timestamp unavailable")
        return value

    @model_validator(mode="after")
    def status_shape(self) -> "PublicReceipt":
        engine = (
            self.engine_receipt_ref,
            self.engine_recorded_at,
            self.consumed_task_revision,
            self.resulting_task_revision,
        )
        if self.status == "committed":
            if (
                any(v is None for v in engine)
                or self.audit_result_ref is None
                or self.technical_code is not None
            ):
                raise ValueError("committed receipt requires audited engine proof")
        elif any(v is not None for v in engine):
            raise ValueError("noncommitted receipt cannot claim engine proof")
        elif self.status == "pending":
            if self.audit_result_ref is not None or self.technical_code is not None:
                raise ValueError("pending receipt cannot claim result")
        elif self.audit_result_ref is None or self.technical_code is None:
            raise ValueError("conflict requires audited technical result")
        return self


class CurrentReceiptAuthority(Timed):
    identity: ReceiptIdentity
    issuer: str
    subject: OpaqueRef
    membership_revision: Revision
    read_permitted: bool


class ReceiptResourceAuthority(ABC):
    scope: Scope

    @abstractmethod
    async def current_authority(
        self, principal: HumanPrincipal, identity: ReceiptIdentity
    ) -> CurrentReceiptAuthority:
        """Current tenant/resource policy, including subject/consent and closed-case access.

        Must use authoritative records, not the admission's historic grant or browser
        assertions. Completion itself neither grants nor revokes receipt access.
        """
        raise NotImplementedError


class ReceiptStore(ABC):
    scope: Scope

    @abstractmethod
    async def read_owned(self, principal: HumanPrincipal, task_id: str, command_id: str) -> PublicReceipt:
        """Internal principal-bound read; gateway applies current resource authority before exposure."""
        raise NotImplementedError


@dataclass(frozen=True)
class BoundReceiptPorts:
    store: ReceiptStore
    authority: ReceiptResourceAuthority
