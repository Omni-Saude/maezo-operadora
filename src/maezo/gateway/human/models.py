"""Server-only D4 port contracts; shape is never proof of current authority/durable commit."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class AuthorizedAssignment(Closed):
    """Minimized request to the dedicated transactional admission port.

    No narrative, clinical inputs or generic engine variables. This object is neither
    a signature nor an engine command envelope. D5/D6 must define the wire profile.
    """

    scope: Scope
    workload_ref: OpaqueRef
    principal: HumanPrincipal = Field(repr=False)
    snapshot: TaskSnapshot = Field(repr=False)
    authority_revision: Revision
    command: AssignmentCommand = Field(repr=False)


class PendingAdmission(Closed):
    """Returned by durable adapter after intent+outbox commit; never synthesized by gateway.

    Shape validation cannot prove persistence. The production factory has no adapter yet
    and refuses configuration. A subsequent D6 implementation must prove transactional
    linkage and recovery. This is deliberately not an engine execution receipt.
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
