"""ADR0049 closed ownership HTTP contracts; no browser-supplied acting authority."""

from typing import Annotated, Literal, Self

from pydantic import Field, SerializerFunctionWrapHandler, model_validator

from maezo.gateway.human.models import AssignmentCommand, AssignmentContext, Closed
from maezo.gateway.human.projection import decimal_revision
from maezo.gateway.human.receipt import PublicReceipt

from .decisions import revision_from_decimal
from .models import FormKey, OpaqueRef, SchemaVersion, Sha256Digest
from .queues import DecimalRevision, DecimalVersion, UTCDateTime

_NUMBERS = (
    "process_definition_version",
    "form_version",
    "expected_task_revision",
    "expected_evidence_revision",
    "expected_membership_revision",
    "expected_authority_revision",
)


class BrowserAssignment(Closed):
    schema_version: SchemaVersion
    command_id: OpaqueRef
    operation: Literal["claim", "release"]
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: DecimalVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: DecimalVersion
    form_digest: Sha256Digest
    expected_task_revision: DecimalRevision
    expected_evidence_revision: DecimalRevision
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: DecimalRevision
    expected_authority_revision: DecimalRevision

    def to_command(self) -> AssignmentCommand:
        data = self.model_dump()
        for name in _NUMBERS:
            data[name] = revision_from_decimal(data[name])
        return AssignmentCommand.model_validate(data)

    @model_validator(mode="after")
    def canonical_binding(self) -> Self:
        self.to_command()
        return self

    @classmethod
    def from_command(cls, command: AssignmentCommand) -> "BrowserAssignment":
        data = AssignmentCommand.model_validate(command).model_dump()
        for name in _NUMBERS:
            data[name] = decimal_revision(data[name])
        return cls.model_validate(data)


class ClaimAssignment(BrowserAssignment):
    operation: Literal["claim"]


class ReleaseAssignment(BrowserAssignment):
    operation: Literal["release"]


class AssignmentSubmission(Closed):
    schema_version: Literal["portal-assignment-submission.v1"]
    command: Annotated[ClaimAssignment | ReleaseAssignment, Field(discriminator="operation")]


class AssignmentContextResponse(Closed):
    """Minimal reviewed mutation basis, without decision inputs or read-only evidence."""

    schema_version: Literal["portal-assignment-context.v1"] = "portal-assignment-context.v1"
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: DecimalVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: DecimalVersion
    form_digest: Sha256Digest
    expected_task_revision: DecimalRevision
    expected_evidence_revision: DecimalRevision
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: DecimalRevision
    expected_authority_revision: DecimalRevision
    assignee_ref: OpaqueRef | None
    allowed_operations: tuple[Literal["claim", "release"], ...]
    valid_until: UTCDateTime

    @classmethod
    def from_context(cls, context: AssignmentContext) -> "AssignmentContextResponse":
        context = AssignmentContext.model_validate(context)
        data = context.snapshot.model_dump(
            include={
                "task_id",
                "process_definition_key",
                "process_definition_version",
                "process_definition_id",
                "process_definition_digest",
                "task_definition_key",
                "form_key",
                "form_version",
                "form_digest",
                "assignee_ref",
            }
        )
        data.update(
            expected_task_revision=context.snapshot.task_revision,
            expected_evidence_revision=context.snapshot.evidence_revision,
            expected_evidence_digest=context.snapshot.evidence_digest,
            expected_membership_revision=context.expected_membership_revision,
            expected_authority_revision=context.expected_authority_revision,
            allowed_operations=context.snapshot.allowed_actions,
            valid_until=context.valid_until,
        )
        for name in _NUMBERS:
            data[name] = decimal_revision(data[name])
        return cls.model_validate(data)


class AssignmentReceiptResponse(PublicReceipt):
    """Original assignment receipt with inherited actual proof/status validation."""

    schema_version: Literal["human-public-receipt.v1"] = "human-public-receipt.v1"
    operation: None = Field(default=None, exclude=True)

    def preserve_assignment_wire(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        # Field exclusion preserves the exact v1 wire and typed OpenAPI response.
        return handler(self)
