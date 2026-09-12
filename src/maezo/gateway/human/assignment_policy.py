"""Governed staff ownership source contracts, E03; no implicit organizational grants.

The source administrative transaction and native evaluator share these closed schemas.
Qualification pins come from the deployment owner, never from a request or local fixture.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt, model_validator

from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

from .models import Closed
from .read_profile import (
    Artifact,
    ArtifactPin,
    GroupDomain,
    MembershipProjection,
    SourceProvenance,
    digest,
    wire,
)

Number = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
Version = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]
Operation = Literal["claim", "release", "reassign"]
Ownership = Literal["unassigned", "actor_assigned", "other_assigned"]
Relation = Literal["actor", "current_assignee", "other"]


def ordered(values: tuple[Any, ...], key: Callable[[Any], Any] = lambda value: value) -> None:
    identities = tuple(key(value) for value in values)
    if identities != tuple(sorted(set(identities))):
        raise ValueError("unordered or duplicate assignment collection")


class StaffMembership(MembershipProjection):
    audience: Literal["staff"]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if not 0 <= self.membership_revision < 2**63:
            raise ValueError("membership revision overflow")
        ordered(self.memberships, lambda member: member.membership_ref)
        for member in self.memberships:
            ordered(member.roles)
            ordered(member.groups)
        return self


class RoleGroupRequirement(Closed):
    roles: tuple[OpaqueRef, ...] = Field(min_length=1)
    groups: tuple[OpaqueRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> Self:
        ordered(self.roles)
        ordered(self.groups)
        return self

    def matches(self, member: StaffMembership, candidates: tuple[str, ...]) -> bool:
        return any(
            set(self.roles).issubset(binding.roles)
            and bool(set(self.groups).intersection(binding.groups, candidates))
            for binding in member.memberships
        )


class AssignmentRule(Closed):
    operation: Operation
    actor: RoleGroupRequirement
    target: RoleGroupRequirement | None
    ownership_states: tuple[Ownership, ...]
    target_relations: tuple[Relation, ...]

    @model_validator(mode="after")
    def shape(self) -> Self:
        ordered(self.ownership_states)
        ordered(self.target_relations)
        if self.operation == "reassign":
            if self.target is None or not self.ownership_states or not self.target_relations:
                raise ValueError("reassignment policy incomplete")
        elif (
            self.target is not None
            or self.target_relations
            or self.ownership_states
            != (("unassigned",) if self.operation == "claim" else ("actor_assigned",))
        ):
            raise ValueError("assignment policy changes existing acquisition semantics")
        return self

    def permits(
        self,
        actor: StaffMembership,
        target: StaffMembership | None,
        assignee: str | None,
        candidates: tuple[str, ...],
        now: datetime,
    ) -> bool:
        if (
            actor.state != "active"
            or actor.reviewed_until <= now
            or not self.actor.matches(actor, candidates)
        ):
            return False
        state = (
            "unassigned"
            if assignee is None
            else "actor_assigned"
            if assignee == actor.principal_ref
            else "other_assigned"
        )
        if state not in self.ownership_states:
            return False
        if self.operation != "reassign":
            return target is None
        if target is None or target.state != "active" or target.reviewed_until <= now or self.target is None:
            return False
        relations = set()
        if target.principal_ref == actor.principal_ref:
            relations.add("actor")
        if target.principal_ref == assignee:
            relations.add("current_assignee")
        if not relations:
            relations.add("other")
        return relations.issubset(self.target_relations) and self.target.matches(target, candidates)


class AssignmentPolicy(Closed):
    schema_: Literal["human-assignment-policy.v1"] = Field(alias="schema")
    policy_ref: OpaqueRef
    version: Version
    tenant: OpaqueRef
    contract_pins: tuple[ArtifactPin, ...]
    rules: tuple[AssignmentRule, ...]
    review_receipt: ArtifactPin
    valid_until: Number

    @model_validator(mode="after")
    def unique(self) -> Self:
        ordered(self.rules, lambda rule: rule.operation)
        ordered(self.contract_pins, lambda pin: (pin.artifact_ref, pin.digest))
        if not self.contract_pins:
            raise ValueError("assignment contract pins absent")
        return self


class AssignmentBinding(Closed):
    schema_: Literal["human-assignment-binding.v1"] = Field(alias="schema")
    binding_ref: OpaqueRef
    version: Version
    tenant: OpaqueRef
    environment: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    process_definition_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: Version
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: OpaqueRef
    form_version: Version
    form_digest: Sha256Digest
    catalog_ref: OpaqueRef
    catalog_revision: Number
    catalog_digest: Sha256Digest
    deployment_receipt: ArtifactPin
    contract_pins: tuple[ArtifactPin, ...]
    group_domain: GroupDomain
    policy_ref: OpaqueRef
    policy_version: Version
    policy_digest: Sha256Digest
    allowed_operations: tuple[Operation, ...]
    constraint_mode: Literal["none", "published_resource"]
    subject_policy: ArtifactPin
    consent_policy: ArtifactPin
    qualification: Literal["qualified", "revoked"]
    qualification_receipt: ArtifactPin
    valid_until: Number

    @model_validator(mode="after")
    def unique(self) -> Self:
        ordered(self.allowed_operations)
        ordered(self.contract_pins, lambda pin: (pin.artifact_ref, pin.digest))
        if not self.contract_pins:
            raise ValueError("assignment contract pins absent")
        return self


class ResourceDesignation(Closed):
    task_id: OpaqueRef
    binding_ref: OpaqueRef
    binding_version: Version
    resource_ref: OpaqueRef
    resource_revision: Number
    resource_digest: Sha256Digest


class GenerationPayload(Closed):
    schema_: Literal["human-staff-assignment-generation.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    environment: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    source_revision: Number
    state: Literal["complete"]
    memberships: tuple[StaffMembership, ...]
    membership_count: Number
    membership_digest: Sha256Digest
    policies: tuple[AssignmentPolicy, ...]
    bindings: tuple[AssignmentBinding, ...]
    resource_designations: tuple[ResourceDesignation, ...]
    artifacts: tuple[Artifact, ...]
    valid_until: Number

    @model_validator(mode="after")
    def complete(self) -> Self:
        ordered(self.memberships, lambda member: member.principal_ref)
        identities = {(member.issuer, member.subject) for member in self.memberships}
        if (
            len(identities) != len(self.memberships)
            or self.membership_count != len(self.memberships)
            or self.membership_digest != digest(self.memberships)
        ):
            raise ValueError("incomplete staff generation")
        ordered(self.policies, lambda policy: (policy.policy_ref, policy.version))
        ordered(self.bindings, lambda binding: (binding.binding_ref, binding.version))
        ordered(self.resource_designations, lambda designation: designation.task_id)
        ordered(self.artifacts, lambda artifact: (artifact.artifact_ref, artifact.digest))
        available = {(artifact.artifact_ref, artifact.digest) for artifact in self.artifacts}
        required: set[tuple[str, str]] = set()
        policies = {(policy.policy_ref, policy.version, digest(policy)): policy for policy in self.policies}
        for policy in self.policies:
            if policy.tenant != self.tenant:
                raise ValueError("assignment policy scope mismatch")
            required.update(
                (pin.artifact_ref, pin.digest) for pin in (*policy.contract_pins, policy.review_receipt)
            )
            required.add((policy.policy_ref, digest(policy)))
        for binding in self.bindings:
            if any(
                getattr(binding, field) != getattr(self, field)
                for field in ("tenant", "environment", "engine_name", "database_incarnation")
            ):
                raise ValueError("assignment binding scope mismatch")
            matching_policy = policies.get(
                (binding.policy_ref, binding.policy_version, binding.policy_digest)
            )
            if matching_policy is None or not set(binding.allowed_operations).issubset(
                rule.operation for rule in matching_policy.rules
            ):
                raise ValueError("assignment binding policy missing")
            required.update(
                (pin.artifact_ref, pin.digest)
                for pin in (
                    *binding.contract_pins,
                    binding.deployment_receipt,
                    binding.qualification_receipt,
                    binding.subject_policy,
                    binding.consent_policy,
                )
            )
            required.update(
                ((binding.form_key, binding.form_digest), (binding.catalog_ref, binding.catalog_digest))
            )
        if required != available:
            raise ValueError("assignment artifact coverage mismatch")
        # Force the shared recursive codec and its number-free size boundary now.
        from maezo.portal.engine.profile import canonicalize

        if len(canonicalize(wire(self.model_dump(by_alias=True)))) > 65536:
            raise ValueError("assignment generation exceeds transport bound")
        return self


class AdminSourceAttestation(Closed):
    schema_: Literal["human-staff-assignment-source.v1"] = Field(alias="schema")
    source: SourceProvenance
    tenant: OpaqueRef
    environment: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    source_key_id: OpaqueRef
    algorithm: Literal["Ed25519"]
    generation_digest: Sha256Digest
    signature: str


class AssignmentPublication(Closed):
    schema_: Literal["human-assignment-publication.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    workload_ref: OpaqueRef
    publication_id: OpaqueRef
    expected_revision: Number
    operation: Literal["disable", "replace"]
    source: AdminSourceAttestation
    generation: GenerationPayload | None
    expected_generation_digest: Sha256Digest | None

    @model_validator(mode="after")
    def binding(self) -> Self:
        expected = (
            digest(self.generation)
            if self.generation is not None
            else digest(
                {
                    "operation": "disable",
                    "expected_generation_digest": self.expected_generation_digest,
                    "source_revision": self.source.source.source_revision,
                }
            )
        )
        if (
            (self.operation == "replace") != (self.generation is not None)
            or self.operation == "disable"
            and self.expected_generation_digest is None
        ):
            raise ValueError("assignment publication shape mismatch")
        if (
            self.source.generation_digest != expected
            or self.source.source.source_digest != expected
            or self.source.tenant != self.tenant
        ):
            raise ValueError("assignment source digest mismatch")
        return self


class AssignmentPublicationReceipt(Closed):
    schema_: Literal["human-assignment-publication-receipt.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    publication_id: OpaqueRef
    request_digest: Sha256Digest
    operation: Literal["disable", "replace"]
    authority_revision: Number
    source_revision: Number
    generation_digest: Sha256Digest
    state: Literal["disabled", "active"]
