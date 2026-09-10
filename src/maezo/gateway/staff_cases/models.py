"""SC1 exact-case construction profile, derived from the approved staff-case schemas.

A parsed grant is not authority. Installation, signature, current source/policy,
current membership and native identity verification are separate mandatory steps.
Only the first detail/publication slice is accepted by these entry points.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from maezo.gateway.external_cases.models import (
    CaseRef, Closed as ExternalClosed, Digest, Identity, Ref, Scope, revision, timestamp,
)
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import SourceProvenance, wire
from maezo.portal.contracts.models import HumanPrincipal


class Closed(ExternalClosed):
    def wire(self) -> dict[str, object]:
        return wire(self)


def _number(value: str) -> str:
    revision(value)
    return value


def _time(value: str) -> str:
    timestamp(value)
    return value


N = Annotated[str, AfterValidator(_number)]
T = Annotated[str, AfterValidator(_time)]
Purpose = Literal[
    "installation", "membership_current", "staff_case_grant", "staff_policy_head", "native_case_facts", "native_result",
]
EnvelopePurpose = Literal["staff-case-publication.v1", "staff-case-read.v1", "staff-case-finalize.v1"]
Projection = Literal["staff_summary.v1", "staff_identity.v1", "staff_current_task.v1"]
ROLE_PURPOSES: dict[str, frozenset[str]] = {
    "installer": frozenset({"installation"}),
    "identity_verifier": frozenset({"membership_current"}),
    "case_issuer": frozenset({"staff_case_grant", "staff_policy_head"}),
    "publication_importer": frozenset({"staff-case-publication.v1"}),
    "native_facts": frozenset({"native_case_facts"}),
    "read_requester": frozenset({"staff-case-read.v1", "staff-case-finalize.v1"}),
    "native_result": frozenset({"native_result"}),
}
FIELDS = {
    "staff_summary.v1": frozenset({"case_ref", "kind", "state", "record_revision", "state_observed_at"}),
    "staff_identity.v1": frozenset(Identity.model_fields),
    "staff_current_task.v1": frozenset({
        "task_id", "task_definition_key", "task_revision", "created_at", "due_at", "assignee_ref",
    }),
}


class StaffCaseError(ValueError):
    def __init__(self, code: Literal["invalid", "denied", "unavailable", "conflict", "uncertain"]):
        self.code = code
        super().__init__("staff_case_" + code)


class Proof(Closed):
    schema_: Literal["staff-case-proof.v1"] = Field(alias="schema")
    purpose: Purpose
    algorithm: Literal["Ed25519"]
    key_fingerprint: Digest
    issued_at: T
    expires_at: T
    statement_digest: Digest
    signature: str = Field(repr=False, max_length=128)


class DesignationEntry(Closed):
    entry_ref: Ref
    role: Literal[
        "installer", "publication_importer", "identity_verifier", "case_issuer", "native_facts",
        "read_requester", "native_result",
    ]
    source_namespace: Ref
    source_ref: Ref
    key_fingerprint: Digest
    certificate_spki: Digest | None
    public_key: str = Field(repr=False, max_length=256)
    login_role: Ref
    purposes: tuple[Purpose | EnvelopePurpose, ...]
    projections: tuple[Projection, ...]
    operations: tuple[Literal["detail"], ...]
    not_before: T
    valid_until: T

    @model_validator(mode="after")
    def exact_capabilities(self) -> Self:
        if not self.purposes or not set(self.purposes) <= ROLE_PURPOSES[self.role]:
            raise ValueError("invalid staff role purpose")
        for values in (self.purposes, self.projections, self.operations):
            if len(values) != len(set(values)):
                raise ValueError("duplicate staff capability")
        if self.role in {"case_issuer", "read_requester"}:
            if self.operations != ("detail",) or not self.projections:
                raise ValueError("missing exact detail capability")
        elif self.projections or self.operations:
            raise ValueError("unexpected staff projection authority")
        return self


class Designation(Closed):
    schema_: Literal["staff-case-designation.v1"] = Field(alias="schema")
    scope: Scope
    designation_ref: Ref
    designation_revision: N
    expected_previous_revision: N
    authority_ref: Ref
    authority_revision: N
    entries: tuple[DesignationEntry, ...] = Field(min_length=1, max_length=32)
    issued_at: T
    valid_until: T
    state: Literal["active", "revoked"]

    @model_validator(mode="after")
    def independent_entries(self) -> Self:
        if int(self.designation_revision) != int(self.expected_previous_revision) + 1:
            raise ValueError("invalid designation succession")
        for field in ("entry_ref", "key_fingerprint", "public_key", "login_role"):
            if len({getattr(e, field) for e in self.entries}) != len(self.entries):
                raise ValueError("staff role identities must be separate")
        peers = [e.certificate_spki for e in self.entries if e.certificate_spki is not None]
        if len(peers) != len(set(peers)):
            raise ValueError("staff peers must be separate")
        return self


class PolicyDecision(Closed):
    decision_ref: Ref
    policy_ref: Ref
    policy_revision: N
    policy_digest: Digest
    subject_identity_digest: Digest
    membership_revision: N
    resource_identity_digest: Digest
    operation: Literal["detail"]
    projection: Projection
    fields: tuple[Ref, ...]
    receipt_ref: Ref
    receipt_digest: Digest
    decision_proof: Proof
    observed_at: T
    valid_until: T
    state: Literal["active", "revoked"]

    @model_validator(mode="after")
    def closed_fields(self) -> Self:
        if not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError("invalid staff fields")
        if not set(self.fields) <= FIELDS[self.projection]:
            raise ValueError("unknown staff projection field")
        return self


class StaffCaseGrant(Closed):
    grant_ref: Ref
    scope: Scope
    case_ref: CaseRef
    identity_digest: Digest
    issuer: str = Field(repr=False, max_length=2048)
    subject: Ref = Field(repr=False)
    principal_ref: Ref
    membership_revision: N
    audience: Literal["staff"]
    grant_revision: N
    source_ref: Ref
    source_revision: N
    decisions: tuple[PolicyDecision, ...] = Field(min_length=1, max_length=256)
    observed_at: T
    valid_until: T
    state: Literal["active", "revoked"]

    @field_validator("issuer")
    @classmethod
    def issuer_origin(cls, value: str) -> str:
        return HumanPrincipal._issuer_is_an_origin_url(value)

    @model_validator(mode="after")
    def unique_decisions(self) -> Self:
        refs = {d.decision_ref for d in self.decisions}
        if len(refs) != len(self.decisions):
            raise ValueError("duplicate staff decision")
        return self


class MembershipWitness(Closed):
    schema_: Literal["staff-case-membership-witness.v1"] = Field(alias="schema")
    scope: Scope
    actor: Actor
    session_ref: Ref
    principal_record_revision: N
    principal_record_digest: Digest
    source: SourceProvenance
    observed_at: T
    valid_until: T
    proof: Proof

    @model_validator(mode="after")
    def staff_only(self) -> Self:
        if self.actor.audience != "staff" or self.proof.purpose != "membership_current":
            raise ValueError("invalid staff membership witness")
        return self


class Revoke(Closed):
    target_kind: Literal["case_grant"]
    target_ref: Ref
    expected_revision: N


class StaffCurrentTaskResource(Closed):
    scope: Scope
    case_ref: CaseRef
    process_instance_id: Ref
    task_id: Ref
    task_definition_key: Ref


class StaffCurrentTaskCreatedAt(Closed):
    resource: StaffCurrentTaskResource
    task_revision: N
    created_at: T


class StaffPolicyHeadPublication(Closed):
    schema_: Literal["staff-policy-head.v1"] = Field(alias="schema")
    scope: Scope
    policy_ref: Ref
    policy_revision: N
    policy_digest: Digest
    head_revision: N
    expected_head_revision: N
    state: Literal["active", "revoked"]
    decision_issuer_key_fingerprint: Digest
    decision_purpose: Purpose
    source_ref: Ref
    source_revision: N
    observed_at: T
    valid_until: T
    proof: Proof

    @model_validator(mode="after")
    def exact_head(self) -> Self:
        if (int(self.head_revision) != int(self.expected_head_revision) + 1
                or int(self.policy_revision) < 1 or self.proof.purpose != "staff_policy_head"
                or self.decision_purpose != "staff_case_grant"):
            raise ValueError("invalid staff policy head")
        return self


class PolicyHeadPin(Closed):
    policy_ref: Ref
    policy_revision: N
    policy_digest: Digest
    head_revision: N
    head_digest: Digest


class StaffPublication(Closed):
    schema_: Literal["staff-case-publication.v1"] = Field(alias="schema")
    scope: Scope
    publication_id: Ref
    expected_source_revision: N
    source_ref: Ref
    source_revision: N
    kind: Literal["case_grant", "revoke", "policy_head"]
    membership_witness: MembershipWitness | None
    payload: StaffCaseGrant | Revoke | StaffPolicyHeadPublication
    payload_digest: Digest
    observed_at: T
    valid_until: T
    proof: Proof

    @model_validator(mode="after")
    def exact_publication(self) -> Self:
        if int(self.source_revision) != int(self.expected_source_revision) + 1:
            raise ValueError("invalid staff source succession")
        if self.kind == "case_grant":
            if not isinstance(self.payload, StaffCaseGrant) or self.membership_witness is None:
                raise ValueError("missing staff grant witness")
        elif self.kind == "policy_head":
            if not isinstance(self.payload, StaffPolicyHeadPublication) or self.membership_witness is not None:
                raise ValueError("invalid staff policy publication")
        elif not isinstance(self.payload, Revoke):
            raise ValueError("invalid staff tombstone")
        return self
