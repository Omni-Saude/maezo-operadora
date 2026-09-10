"""ADR-0049 D3/D6 closed assignment and receipt projections; never infer authority.

The D4 task view has no evidence reference. A trusted server source must resolve it;
using a digest as a reference would manufacture an engine binding. All six real
decision forms remain outside this projector, pending their PHI custody contracts.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.assignment import GOVERNED_FIELDS, HumanAssignmentCommand
from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import HumanCommand, canonicalize, strict_loads

from .models import AuthorizedAssignment, AuthorizedGovernedAssignment, Closed, Scope, Timed


class ProjectionError(ValueError):
    """Static message only; a validation failure must not disclose input material."""


DecimalRevision = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)$")]


def decimal_revision(value: int) -> str:
    """Exact strict integer -> canonical decimal, including Python's >4300 digit case."""
    if type(value) is not int or value < 0:
        raise ProjectionError("invalid revision")
    if value == 0:
        return "0"
    chunks: list[int] = []
    while value:
        value, remainder = divmod(value, 10**9)
        chunks.append(remainder)
    return str(chunks[-1]) + "".join(f"{part:09d}" for part in reversed(chunks[:-1]))


class EvidenceReference(Timed):
    tenant: OpaqueRef
    task_id: OpaqueRef
    evidence_ref: OpaqueRef
    revision: Revision
    digest: Sha256Digest


class EvidenceReferenceSource(ABC):
    scope: Scope

    @abstractmethod
    async def current_reference(self, assignment: AuthorizedAssignment) -> EvidenceReference:
        """Resolve the existing authoritative engine evidence binding, never from browser input."""
        raise NotImplementedError


def intent_reference(scope: Scope, task_id: str, command_id: str) -> str:
    # Unambiguous identity; delimiters in opaque identifiers cannot alias another command.
    return hashlib.sha256(canonicalize([scope.model_dump(), task_id, command_id])).hexdigest()


def project_assignment(assignment: AuthorizedAssignment, evidence: EvidenceReference) -> HumanCommand:
    try:
        a = AuthorizedAssignment.model_validate(assignment)
        e = EvidenceReference.model_validate(evidence)
        s, c, p = a.snapshot, a.command, a.principal
        pins = (
            "task_id",
            "process_definition_key",
            "process_definition_version",
            "process_definition_id",
            "process_definition_digest",
            "task_definition_key",
            "form_key",
            "form_version",
            "form_digest",
        )
        if (
            a.scope.tenant != p.tenant
            or a.scope.workload_ref != a.workload_ref
            or p.principal_ref == a.workload_ref
            or any(getattr(s, key) != getattr(c, key) for key in pins)
            or c.expected_task_revision != s.task_revision
            or c.expected_evidence_revision != s.evidence_revision
            or c.expected_evidence_digest != s.evidence_digest
            or c.expected_membership_revision != p.membership_revision
            or c.expected_authority_revision != a.authority_revision
            or s.form_source_status != "BPMN_FORMDATA"
            or c.operation not in s.allowed_actions
            or (c.operation == "claim" and s.assignee_ref is not None)
            or (c.operation == "release" and s.assignee_ref != p.principal_ref)
            or e.tenant != a.scope.tenant
            or e.task_id != c.task_id
            or e.revision != s.evidence_revision
            or e.digest != s.evidence_digest
            or e.valid_until <= datetime.now(UTC)
        ):
            raise ProjectionError("assignment projection unavailable")
        return HumanCommand(
            tenant=a.scope.tenant,
            task_id=c.task_id,
            command_id=c.command_id,
            principal_ref=p.principal_ref,
            principal_issuer=p.issuer,
            principal_subject=p.subject,
            workload_ref=a.workload_ref,
            operation=c.operation,
            process_definition_id=s.process_definition_id,
            process_definition_key=s.process_definition_key,
            process_definition_version=decimal_revision(s.process_definition_version),
            process_definition_digest=s.process_definition_digest,
            task_definition_key=s.task_definition_key,
            form_key=s.form_key,
            form_version=decimal_revision(s.form_version),
            form_digest=s.form_digest,
            task_revision=decimal_revision(s.task_revision),
            authority_revision=decimal_revision(a.authority_revision),
            membership_revision=decimal_revision(p.membership_revision),
            evidence_revision=decimal_revision(s.evidence_revision),
            evidence_ref=e.evidence_ref,
            evidence_digest=e.digest,
            assignee_ref=s.assignee_ref,
            audit_intent_ref=intent_reference(a.scope, c.task_id, c.command_id),
            outcome=None,
        )
    except Exception:
        raise ProjectionError("assignment projection unavailable") from None


class GovernedEvidenceReferenceSource(ABC):
    scope: Scope

    @abstractmethod
    async def current_reference(self, assignment: AuthorizedGovernedAssignment) -> EvidenceReference:
        raise NotImplementedError


def project_governed_assignment(
    assignment: AuthorizedGovernedAssignment, evidence: EvidenceReference
) -> HumanAssignmentCommand:
    from .assignment_transport import PINS, validate_authority, validate_context

    a = AuthorizedGovernedAssignment.model_validate(assignment)
    e = EvidenceReference.model_validate(evidence)
    c, p, b = a.command, a.principal, a.read_context
    s, ctx = b.task.snapshot, b.context
    if b.scope != a.scope or b.principal != p or a.valid_until <= datetime.now(UTC):
        raise ProjectionError("assignment projection unavailable")
    validate_context(p, a.scope, b.task, ctx)
    validate_authority(p, b, a.authority, c.operation)
    if (
        any(getattr(c, k) != getattr(s, k) for k in PINS)
        or c.expected_task_revision != s.task_revision
        or c.expected_evidence_revision != s.evidence_revision
        or c.expected_evidence_digest != s.evidence_digest
        or c.expected_membership_revision != p.membership_revision
        or c.expected_authority_revision != b.task.authority_revision
        or c.expected_assignee_ref != s.assignee_ref
        or any(
            str(getattr(c, "expected_" + name)) != str(getattr(ctx, name))
            for name in (
                "binding_ref",
                "binding_version",
                "binding_digest",
                "policy_ref",
                "policy_version",
                "policy_digest",
                "source_revision",
                "generation_digest",
            )
        )
        or e.tenant != a.scope.tenant
        or e.task_id != c.task_id
        or e.revision != s.evidence_revision
        or e.digest != s.evidence_digest
        or e.valid_until <= datetime.now(UTC)
    ):
        raise ProjectionError("assignment projection unavailable")
    return HumanAssignmentCommand(
        tenant=a.scope.tenant,
        workload_ref=a.scope.workload_ref,
        task_id=c.task_id,
        command_id=c.command_id,
        principal_ref=p.principal_ref,
        principal_issuer=p.issuer,
        principal_subject=p.subject,
        operation=c.operation,
        process_definition_id=s.process_definition_id,
        process_definition_key=s.process_definition_key,
        process_definition_version=str(s.process_definition_version),
        process_definition_digest=s.process_definition_digest,
        task_definition_key=s.task_definition_key,
        form_key=s.form_key,
        form_version=str(s.form_version),
        form_digest=s.form_digest,
        task_revision=str(s.task_revision),
        authority_revision=str(b.task.authority_revision),
        membership_revision=str(p.membership_revision),
        evidence_revision=str(e.revision),
        evidence_ref=e.evidence_ref,
        evidence_digest=e.digest,
        assignee_ref=s.assignee_ref,
        audit_intent_ref=intent_reference(a.scope, c.task_id, c.command_id),
        outcome=None,
        binding_ref=ctx.binding_ref,
        binding_version=ctx.binding_version,
        binding_digest=ctx.binding_digest,
        policy_ref=ctx.policy_ref,
        policy_version=ctx.policy_version,
        policy_digest=ctx.policy_digest,
        source_revision=ctx.source_revision,
        generation_digest=ctx.generation_digest,
        target_ref=c.target_ref,
        target_membership_revision=None
        if c.expected_target_membership_revision is None
        else str(c.expected_target_membership_revision),
    )


class EngineReceipt(Closed):
    """Exact D5 wire schema; validation alone does not authenticate its origin."""

    schema_: Literal["human-engine-receipt.v1", "human-engine-receipt.v2"] = Field(alias="schema")
    status: Literal["committed"]
    tenant: OpaqueRef
    task_id: OpaqueRef
    command_id: OpaqueRef
    operation: Literal["claim", "release", "decision"]
    payload_digest: Sha256Digest
    principal_ref: OpaqueRef
    workload_ref: OpaqueRef
    audit_intent_ref: OpaqueRef
    consumed_task_revision: DecimalRevision
    resulting_task_revision: DecimalRevision | None
    engine_receipt_ref: OpaqueRef
    recorded_at: DecimalRevision

    @model_validator(mode="after")
    def operation_shape(self) -> EngineReceipt:
        if self.schema_ == "human-engine-receipt.v2":
            if self.operation != "decision" or self.resulting_task_revision is not None:
                raise ValueError("invalid completed decision receipt")
        elif self.operation not in ("claim", "release") or self.resulting_task_revision is None:
            raise ValueError("invalid assignment receipt")
        return self

    @property
    def engine_recorded_at(self) -> datetime:
        # Wire timestamps have a bounded engine int64 origin; datetime range is narrower.
        return datetime.fromtimestamp(int(self.recorded_at), UTC)


class GovernedEngineReceipt(EngineReceipt):
    schema_: Literal["human-engine-assignment-receipt.v1"] = Field(alias="schema")  # type: ignore[assignment]  # frozen distinct wire discriminator
    operation: Literal["claim", "release", "reassign"]  # type: ignore[assignment]  # frozen distinct wire discriminator
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
    assignment_disposition: Literal["changed", "unchanged"]

    @model_validator(mode="after")
    def operation_shape(self) -> GovernedEngineReceipt:
        if self.resulting_task_revision is None:
            raise ValueError("governed receipt lacks actual revision")
        if self.assignment_disposition != (
            "unchanged" if self.prior_assignee_ref == self.resulting_assignee_ref else "changed"
        ):
            raise ValueError("invalid assignment disposition")
        return self


def verify_engine_receipt(raw: bytes, command: HumanCommand | HumanDecisionCommand) -> EngineReceipt:
    """Use only on the authenticated dedicated endpoint or linked durable local bytes."""
    try:
        value = strict_loads(raw)
        receipt = (
            GovernedEngineReceipt if isinstance(command, HumanAssignmentCommand) else EngineReceipt
        ).model_validate(value)
        expected_schema = (
            "human-engine-assignment-receipt.v1"
            if isinstance(command, HumanAssignmentCommand)
            else "human-engine-receipt.v2"
            if isinstance(command, HumanDecisionCommand)
            else "human-engine-receipt.v1"
        )
        if receipt.schema_ != expected_schema:
            raise ProjectionError("receipt mismatch")
        for field in (
            "tenant",
            "task_id",
            "command_id",
            "operation",
            "principal_ref",
            "workload_ref",
            "audit_intent_ref",
        ):
            if getattr(receipt, field) != getattr(command, field):
                raise ProjectionError("receipt mismatch")
        if (
            receipt.payload_digest != command.digest
            or receipt.consumed_task_revision != command.task_revision
        ):
            raise ProjectionError("receipt mismatch")
        if isinstance(command, HumanAssignmentCommand):
            if not isinstance(receipt, GovernedEngineReceipt):
                raise ProjectionError("governed receipt schema mismatch")
            for field in GOVERNED_FIELDS:
                if getattr(receipt, field) != getattr(command, field):
                    raise ProjectionError("governed receipt pin mismatch")
            expected = (
                command.principal_ref
                if command.operation == "claim"
                else None
                if command.operation == "release"
                else command.target_ref
            )
            if (
                receipt.prior_assignee_ref != command.assignee_ref
                or receipt.resulting_assignee_ref != expected
            ):
                raise ProjectionError("governed receipt assignee mismatch")
        # Engine registration metadata is not an authorization freshness clock.
        # Keep UTC datetime representability validation without cross-clock ordering.
        _ = receipt.engine_recorded_at
        return receipt
    except Exception:
        raise ProjectionError("receipt verification unavailable") from None


def restore_command(raw: bytes) -> HumanCommand | HumanDecisionCommand:
    try:
        value = strict_loads(raw)
        if isinstance(value, dict) and value.get("schema") == "human-assignment.v2":
            command: HumanCommand = HumanAssignmentCommand(**value)
            if command.canonical != raw:
                raise ProjectionError("noncanonical governed command")
            return command
        if isinstance(value, dict) and value.get("schema") == "human-classified-decision.v1":
            return HumanDecisionCommand(raw)
        command = HumanCommand(**value)
        if command.canonical != raw or command.operation not in ("claim", "release"):
            raise ProjectionError("immutable command unavailable")
        return command
    except Exception:
        raise ProjectionError("immutable command unavailable") from None
