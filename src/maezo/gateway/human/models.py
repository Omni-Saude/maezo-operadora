"""Server-only D4 port contracts; shape is never proof of current authority/durable commit."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from maezo.portal.contracts.models import (
    FormKey,
    HumanPrincipal,
    OpaqueRef,
    PositiveVersion,
    Revision,
    SchemaVersion,
    Sha256Digest,
    SubjectBinding,
    TaskAction,
    TaskSnapshot,
)


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )


class Scope(Closed):
    tenant: OpaqueRef
    environment: OpaqueRef
    workload_ref: OpaqueRef


class AssignmentCommand(Closed):
    """Browser claim/release expectations, without actor, tenant, variables or outcome."""

    schema_version: SchemaVersion
    command_id: OpaqueRef
    operation: Literal["claim", "release"]
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: PositiveVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: PositiveVersion
    form_digest: Sha256Digest
    expected_task_revision: Revision
    expected_evidence_revision: Revision
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: Revision
    expected_authority_revision: Revision


class Timed(Closed):
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value


class AuthoritativeTask(Timed):
    """Produced only by trusted task transport, never parsed from browser state.

    The adapter must pin the deployment/form catalog and resolve engine identity-link
    expressions. Its validity policy and role/subject/consent requirements come from the
    classified contract. D5 must serialize these revisions again at actual execution.
    """

    tenant: OpaqueRef
    snapshot: TaskSnapshot = Field(repr=False)
    active: bool
    authority_revision: Revision
    required_roles: tuple[OpaqueRef, ...] = Field(min_length=1)
    required_subject_bindings: tuple[SubjectBinding, ...]
    required_consent_scopes: tuple[OpaqueRef, ...]


class CurrentTaskAuthority(Timed):
    """Current authorization projection, not an audit/outbox admission or permission DTO.

    The trusted projector verifies contractual consent, authoritative process/form pin and
    membership freshness and returns only the operations it actually permits. A browser
    cannot submit this object to HumanGateway. Absence of the adapter fails closed.
    """

    tenant: OpaqueRef
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: PositiveVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: PositiveVersion
    form_digest: Sha256Digest
    issuer: str = Field(repr=False)
    subject: OpaqueRef = Field(repr=False)
    principal_ref: OpaqueRef
    membership_revision: Revision
    authority_revision: Revision
    task_revision: Revision
    evidence_revision: Revision
    evidence_digest: Sha256Digest
    read_permitted: bool
    permitted_operations: tuple[TaskAction, ...]
    consent_scopes: tuple[OpaqueRef, ...]


class AssignmentContext(Timed):
    """Current mutation context, independent of the Q2 read-only projection."""

    snapshot: TaskSnapshot = Field(repr=False)
    expected_membership_revision: Revision
    expected_authority_revision: Revision


class AuthorizedAssignment(Closed):
    """Minimized request to the dedicated transactional admission port.

    No narrative, clinical inputs or generic engine variables. This object is neither
    a signature nor an engine command envelope. D6 projects the D5 wire profile explicitly.
    """

    scope: Scope
    workload_ref: OpaqueRef
    principal: HumanPrincipal = Field(repr=False)
    snapshot: TaskSnapshot = Field(repr=False)
    authority_revision: Revision
    command: AssignmentCommand = Field(repr=False)


class PendingAdmission(Closed):
    """Returned by durable adapter after intent+outbox commit; never synthesized by gateway.

    Shape validation cannot prove persistence. PostgresHumanAdmission constructs this
    only after confirmed commit. The production factory still refuses absent verified
    authority/credential provisioning. This is not an engine execution receipt.
    """

    schema_version: SchemaVersion
    status: Literal["pending"] = "pending"
    tenant: OpaqueRef
    task_id: OpaqueRef
    command_id: OpaqueRef
    principal_ref: OpaqueRef
    workload_ref: OpaqueRef
    audit_intent_ref: OpaqueRef
    outbox_ref: OpaqueRef
    transaction_ref: OpaqueRef
    committed_at: datetime

    @field_validator("committed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value


class GovernedAssignmentCommand(AssignmentCommand):
    schema_version: Annotated[StrictInt, Field(ge=2, le=2)]
    operation: Literal["claim", "release", "reassign"]  # type: ignore[assignment]  # frozen distinct wire discriminator
    expected_assignee_ref: OpaqueRef | None
    expected_binding_ref: OpaqueRef
    expected_binding_version: PositiveVersion
    expected_binding_digest: Sha256Digest
    expected_policy_ref: OpaqueRef
    expected_policy_version: PositiveVersion
    expected_policy_digest: Sha256Digest
    expected_source_revision: Revision
    expected_generation_digest: Sha256Digest
    target_ref: OpaqueRef | None
    expected_target_membership_revision: Revision | None

    @model_validator(mode="after")
    def target_shape(self) -> Self:
        if self.operation == "reassign":
            if self.target_ref is None or self.expected_target_membership_revision is None:
                raise ValueError("reassignment target required")
        elif self.target_ref is not None or self.expected_target_membership_revision is not None:
            raise ValueError("unexpected assignment target")
        for value in self.model_dump().values():
            if type(value) is int and not 0 <= value < 2**63:
                raise ValueError("assignment revision overflow")
        return self


class GovernedAssignmentContext(Closed):
    schema_version: Literal["portal-assignment-context.v2"]
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: str
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: str
    form_digest: Sha256Digest
    expected_task_revision: str
    expected_evidence_revision: str
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: str
    expected_authority_revision: str
    assignee_ref: OpaqueRef | None
    allowed_operations: tuple[Literal["claim", "release", "reassign"], ...]
    valid_until: datetime
    binding_ref: OpaqueRef
    binding_version: str
    binding_digest: Sha256Digest
    policy_ref: OpaqueRef
    policy_version: str
    policy_digest: Sha256Digest
    source_revision: str
    generation_digest: Sha256Digest

    @model_validator(mode="after")
    def closed_expectations(self) -> Self:
        import re

        for name in (
            "process_definition_version",
            "form_version",
            "expected_task_revision",
            "expected_evidence_revision",
            "expected_membership_revision",
            "expected_authority_revision",
            "binding_version",
            "policy_version",
            "source_revision",
        ):
            value = getattr(self, name)
            if not re.fullmatch(r"0|[1-9][0-9]*", value) or len(value) > 19 or int(value) >= 2**63:
                raise ValueError("invalid assignment revision")
            if name.endswith("version") and int(value) == 0:
                raise ValueError("invalid assignment version")
        if self.valid_until.tzinfo is None or len(self.allowed_operations) != len(
            set(self.allowed_operations)
        ):
            raise ValueError("invalid assignment context")
        return self


class GovernedAssignmentReadContext(Closed):
    principal: HumanPrincipal = Field(repr=False)
    scope: Scope
    task: AuthoritativeTask = Field(repr=False)
    context: GovernedAssignmentContext


class AssignmentCandidate(Closed):
    target_ref: OpaqueRef
    target_membership_revision: str

    @field_validator("target_membership_revision")
    @classmethod
    def revision(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"0|[1-9][0-9]*", value) or len(value) > 19 or int(value) >= 2**63:
            raise ValueError("invalid target revision")
        return value


class GovernedAssignmentCandidates(Closed):
    schema_version: Literal["portal-assignment-candidates.v1"]
    context: GovernedAssignmentContext
    candidates: tuple[AssignmentCandidate, ...]
    candidate_count: str
    candidate_digest: Sha256Digest
    valid_until: datetime

    @model_validator(mode="after")
    def complete_candidates(self) -> Self:
        from .read_profile import digest

        identities = tuple(candidate.target_ref for candidate in self.candidates)
        if (
            identities != tuple(sorted(set(identities)))
            or self.candidate_count != str(len(identities))
            or self.candidate_digest != digest(self.candidates)
        ):
            raise ValueError("incomplete assignment candidates")
        if self.valid_until.tzinfo is None or self.valid_until > self.context.valid_until:
            raise ValueError("invalid candidate deadline")
        return self


class AuthorizedGovernedAssignment(Closed):
    scope: Scope
    principal: HumanPrincipal = Field(repr=False)
    read_context: GovernedAssignmentReadContext = Field(repr=False)
    authority: CurrentTaskAuthority = Field(repr=False)
    command: GovernedAssignmentCommand = Field(repr=False)
    valid_until: datetime
