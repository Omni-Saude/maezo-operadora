"""ADR0049 D3: closed lossless decision wire codec; canonical form validators stay server-owned.

The source DecisionInputs union drives generated forms. Schema presence does not activate
any binding; only the gateway's current qualified context can offer a decision action.
"""

from typing import Literal, Self

from pydantic import model_validator

from maezo.gateway.human.decision import DecisionContext
from maezo.gateway.human.models import Closed
from maezo.gateway.human.projection import decimal_revision
from maezo.gateway.human.receipt import PublicReceipt

from .models import (
    AllowedInput,
    DecisionInputs,
    FormKey,
    FormSourceStatus,
    OpaqueRef,
    PagtoAdmissibilityEvidence,
    SchemaVersion,
    Sha256Digest,
    TaskDecision,
    TaskSnapshot,
)
from .queues import DecimalRevision, DecimalVersion, UTCDateTime

_SNAPSHOT_NUMBERS = ("process_definition_version", "form_version", "task_revision", "evidence_revision")
_DECISION_NUMBERS = (
    "process_definition_version",
    "form_version",
    "expected_task_revision",
    "expected_evidence_revision",
    "expected_membership_revision",
)


def revision_from_decimal(value: str) -> int:
    """No float, coercion, or Python's decimal-string conversion size ceiling."""
    if type(value) is not str or not value or any(c not in "0123456789" for c in value):
        raise ValueError("invalid revision")
    if len(value) > 1 and value[0] == "0":
        raise ValueError("invalid revision")
    result = 0
    for offset in range(0, len(value), 9):
        chunk = value[offset : offset + 9]
        result = result * 10 ** len(chunk) + int(chunk)
    return result


class BrowserTaskDecision(Closed):
    """Exactly TaskDecision fields with explicit decimal revision/version codecs."""

    schema_version: SchemaVersion
    command_id: OpaqueRef
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
    inputs: DecisionInputs

    def to_decision(self) -> TaskDecision:
        data = self.model_dump()
        for name in _DECISION_NUMBERS:
            data[name] = revision_from_decimal(data[name])
        # Executes ALL original model_validator checks, including future source forms.
        return TaskDecision.model_validate(data)

    @model_validator(mode="after")
    def canonical_binding(self) -> Self:
        self.to_decision()
        return self

    @classmethod
    def from_decision(cls, decision: TaskDecision) -> "BrowserTaskDecision":
        data = TaskDecision.model_validate(decision).model_dump()
        for name in _DECISION_NUMBERS:
            data[name] = decimal_revision(data[name])
        return cls.model_validate(data)


class DecisionSubmission(Closed):
    schema_version: Literal["portal-decision-submission.v1"]
    decision: BrowserTaskDecision
    expected_authority_revision: DecimalRevision
    expected_binding_digest: Sha256Digest

    @model_validator(mode="after")
    def canonical_authority(self) -> Self:
        revision_from_decimal(self.expected_authority_revision)
        return self


class DecisionSnapshot(Closed):
    """Separate from Q1; offers the one implemented mutation, never fake claim/release."""

    schema_version: SchemaVersion
    snapshot_at: UTCDateTime
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: DecimalVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: DecimalVersion
    form_digest: Sha256Digest
    form_source_status: FormSourceStatus
    task_revision: DecimalRevision
    assignee_ref: OpaqueRef | None
    eligible_candidate_groups: tuple[OpaqueRef, ...]
    evidence_revision: DecimalRevision
    evidence_digest: Sha256Digest
    engine_due_at: UTCDateTime | None
    allowed_actions: tuple[Literal["decision"]]
    allowed_inputs: tuple[AllowedInput, ...]
    read_only_evidence: PagtoAdmissibilityEvidence | None

    @model_validator(mode="after")
    def canonical_binding(self) -> Self:
        data = self.model_dump()
        for name in _SNAPSHOT_NUMBERS:
            data[name] = revision_from_decimal(data[name])
        TaskSnapshot.model_validate(data)
        return self


class DecisionContextResponse(Closed):
    schema_version: Literal["portal-decision-context.v1"] = "portal-decision-context.v1"
    snapshot: DecisionSnapshot
    expected_membership_revision: DecimalRevision
    expected_authority_revision: DecimalRevision
    binding_digest: Sha256Digest
    valid_until: UTCDateTime

    @classmethod
    def from_context(cls, context: DecisionContext) -> "DecisionContextResponse":
        context = DecisionContext.model_validate(context)
        data = context.snapshot.model_dump()
        if "decision" not in context.snapshot.allowed_actions:
            raise ValueError("decision context unavailable")
        for name in _SNAPSHOT_NUMBERS:
            data[name] = decimal_revision(data[name])
        data["allowed_actions"] = ("decision",)
        return cls(
            snapshot=DecisionSnapshot.model_validate(data),
            expected_membership_revision=decimal_revision(context.expected_membership_revision),
            expected_authority_revision=decimal_revision(context.expected_authority_revision),
            binding_digest=context.binding_digest,
            valid_until=context.valid_until,
        )


DecisionErrorCode = Literal[
    "invalid_request",
    "invalid_decision",
    "authentication_unavailable",
    "operation_forbidden",
    "revision_conflict",
    "authority_unavailable",
    "task_unavailable",
    "form_projection_unavailable",
    "form_contract_unavailable",
    "admission_unavailable",
    "credential_scope_mismatch",
    "production_capabilities_unavailable",
    "dependency_unavailable",
]


class PortalDecisionError(Closed):
    schema_version: Literal["portal-decision-error.v1"] = "portal-decision-error.v1"
    code: DecisionErrorCode


# D5/D6 frozen 0ceaf6ff receipt interface. Inherit its actual status/proof validators;
# this is a narrower HTTP view, not a competing receipt semantics implementation.


class DecisionReceiptResponse(PublicReceipt):
    schema_version: Literal["human-public-receipt.v2"] = "human-public-receipt.v2"
    operation: Literal["decision"]
    resulting_task_revision: None = None
