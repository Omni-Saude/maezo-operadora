"""D6 public receipt and independent current resource authorization.

This projection deliberately does not pretend D5 supplies D3's engine_commit_ref.
Receipt access may survive completion, but requires an explicit current resource
grant from the trusted projector plus a freshly resolved human session. Possession
of task/command IDs or the original admission is never a read capability.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from pydantic import (
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

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
    schema_version: Literal["human-public-receipt.v1", "human-public-receipt.v2"] = "human-public-receipt.v1"
    operation: Literal["decision"] | None = None
    status: Literal["pending", "committed", "conflict"]
    audit_intent_ref: OpaqueRef
    audit_intent_hash: Sha256Digest
    audit_result_ref: Sha256Digest | None = None
    engine_receipt_ref: OpaqueRef | None = None
    engine_recorded_at: datetime | None = None
    consumed_task_revision: DecimalRevision | None = None
    resulting_task_revision: DecimalRevision | None = None
    technical_code: Literal["REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"] | None = None

    @model_serializer(mode="wrap")
    def preserve_assignment_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value = cast(dict[str, Any], handler(self))
        if self.schema_version == "human-public-receipt.v1":
            value.pop("operation", None)
        return value

    @field_validator("engine_recorded_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("engine receipt timestamp unavailable")
        return value

    @model_validator(mode="after")
    def status_shape(self) -> "PublicReceipt":
        decision = self.schema_version == "human-public-receipt.v2"
        if not decision and "operation" in self.model_fields_set:
            raise ValueError("assignment receipt cannot contain decision operation")
        if decision != (self.operation == "decision"):
            raise ValueError("receipt operation schema mismatch")
        if decision and self.resulting_task_revision is not None:
            raise ValueError("completed decision cannot retain task revision")
        engine = (
            self.engine_receipt_ref,
            self.engine_recorded_at,
            self.consumed_task_revision,
            self.resulting_task_revision,
        )
        if self.status == "committed":
            if (
                any(v is None for v in (engine[:3] if decision else engine))
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


class PublicAssignmentReceipt(PublicReceipt):
    """C9 closed governed status: command pins always present, effect proof only on commit."""

    schema_version: Literal["human-public-assignment-receipt.v1"]  # type: ignore[assignment]
    operation: Literal["claim", "release", "reassign"]  # type: ignore[assignment]
    audit_result_ref: Sha256Digest | None
    engine_receipt_ref: OpaqueRef | None
    engine_recorded_at: datetime | None
    consumed_task_revision: DecimalRevision | None
    resulting_task_revision: DecimalRevision | None
    technical_code: Literal["REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"] | None
    command_schema: Literal["human-assignment.v2"]
    binding_ref: OpaqueRef
    binding_version: DecimalRevision
    binding_digest: Sha256Digest
    policy_ref: OpaqueRef
    policy_version: DecimalRevision
    policy_digest: Sha256Digest
    source_revision: DecimalRevision
    generation_digest: Sha256Digest
    target_ref: OpaqueRef | None
    target_membership_revision: DecimalRevision | None
    prior_assignee_ref: OpaqueRef | None
    resulting_assignee_ref: OpaqueRef | None
    assignment_disposition: Literal["changed", "unchanged"] | None

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # The inherited wrap serializer advertises dict[str, Any], hiding these
        # closed fields from serialization-mode OpenAPI. Its sole transformation
        # removes operation for the LEGACY v1 discriminator, which this class
        # cannot have. Describe our unmodified field serialization instead, while
        # retaining the actual runtime serializer and all validators unchanged.
        schema = dict(core_schema)
        current = schema
        while current["type"] in {"function-after", "function-before", "function-wrap"}:
            current["schema"] = dict(current["schema"])
            current = current["schema"]
        if current["type"] != "model":
            raise TypeError("assignment receipt model schema unavailable")
        current.pop("serialization", None)
        return handler(cast(CoreSchema, schema))

    @model_validator(mode="after")
    def status_shape(self) -> "PublicAssignmentReceipt":
        if self.operation == "reassign":
            if self.target_ref is None or self.target_membership_revision is None:
                raise ValueError("governed target missing")
        elif self.target_ref is not None or self.target_membership_revision is not None:
            raise ValueError("unexpected governed target")
        # Reuse exactly the existing v1 audit/proof shape on its own closed projection.
        legacy = self.model_dump(include=set(PublicReceipt.model_fields) - {"schema_version", "operation"})
        PublicReceipt.model_validate(legacy)
        if self.status != "committed":
            if any(
                v is not None
                for v in (self.prior_assignee_ref, self.resulting_assignee_ref, self.assignment_disposition)
            ):
                raise ValueError("noncommitted receipt cannot claim assignment effect")
        else:
            expected = (
                self.principal_ref
                if self.operation == "claim"
                else None
                if self.operation == "release"
                else self.target_ref
            )
            if self.resulting_assignee_ref != expected or self.assignment_disposition != (
                "unchanged" if self.prior_assignee_ref == self.resulting_assignee_ref else "changed"
            ):
                raise ValueError("governed effect mismatch")
        return self


def parse_public_receipt(value: Any) -> PublicReceipt:
    if isinstance(value, PublicReceipt):
        value = value.model_dump()
    cls = (
        PublicAssignmentReceipt
        if isinstance(value, dict) and value.get("schema_version") == "human-public-assignment-receipt.v1"
        else PublicReceipt
    )
    return cls.model_validate(value)
