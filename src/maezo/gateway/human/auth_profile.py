"""Closed E04 AUTH wire. Trace: approved e04-native-contract-repair/SCHEMAS.md.

Parsing establishes shape, never source designation or permission. No raw variables,
PHI document bytes, or browser-chosen native identities enter these commands.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Sha256Digest

from .read_profile import ArtifactPin, Closed, MembershipProjection, SourceProvenance, digest

N = Annotated[int, Field(ge=0, le=2**63 - 1)]
EngineId = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=512, pattern=r"^[^\x00-\x1f\x7f/?#]+$")
]
InputKind = Literal[
    "actor",
    "resource_authority",
    "guide",
    "start_facts",
    "document_custody",
    "document_policy",
    "audit_intent",
]
Operation = Literal["auth.start", "auth.documents.respond"]
Purpose = Literal[
    "human-auth-start",
    "human-auth-documents",
    "human-auth-read",
    "human-auth-input-publication",
    "human-auth-publication-read",
    "human-auth-result",
]


class Scope(Closed):
    tenant: OpaqueRef
    environment: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    installation_ref: OpaqueRef
    installation_revision: N


class Actor(Closed):
    principal_ref: OpaqueRef
    issuer: str = Field(repr=False, min_length=1, max_length=2048)
    subject: OpaqueRef = Field(repr=False)
    membership_revision: N
    audience: Literal["staff", "beneficiary", "provider"]

    @field_validator("issuer")
    @classmethod
    def issuer_origin(cls, value: str) -> str:
        return HumanPrincipal._issuer_is_an_origin_url(value)

    @classmethod
    def from_principal(
        cls, principal: HumanPrincipal, audience: Literal["staff", "beneficiary", "provider"]
    ) -> Actor:
        return cls(
            principal_ref=principal.principal_ref,
            issuer=principal.issuer,
            subject=principal.subject,
            membership_revision=principal.membership_revision,
            audience=audience,
        )


class Pin(Closed):
    kind: InputKind
    resource_ref: OpaqueRef
    head_generation: N
    source: SourceProvenance
    payload_digest: Sha256Digest


class Definition(Closed):
    process_key: Literal["SP-OP-AUTH-001"]
    definition_id: EngineId
    definition_digest: Sha256Digest
    deployment_id: EngineId
    input_profile: Literal["portal-auth-intake.v1"]
    profile_digest: Sha256Digest


class AuditIntent(Closed):
    intent_ref: OpaqueRef
    admitted_command_id: OpaqueRef
    admitted_digest: Sha256Digest
    source: SourceProvenance


class DocumentRef(Closed):
    document_ref: OpaqueRef
    custody_revision: N
    content_sha256: Sha256Digest = Field(repr=False)
    storage_version_ref: OpaqueRef = Field(repr=False)
    policy_digest: Sha256Digest


DocumentRefs = Annotated[tuple[DocumentRef, ...], Field(max_length=256)]
Pins = Annotated[tuple[Pin, ...], Field(max_length=272)]


def validate_documents(documents: tuple[DocumentRef, ...]) -> None:
    refs = [d.document_ref for d in documents]
    if refs != sorted(set(refs)):
        raise ValueError("invalid document order")


def validate_pins(pins: tuple[Pin, ...]) -> None:
    keys = [(p.kind, p.resource_ref) for p in pins]
    if keys != sorted(set(keys)):
        raise ValueError("invalid pin order")


class OccurrenceBinding(Closed):
    request_ref: OpaqueRef
    generation: N
    request_revision: N
    binding_revision: N
    process_instance_id: EngineId
    definition: Definition
    scope_execution_id: EngineId
    subscription_id: EngineId
    subscription_revision: N
    subscription_execution_id: EngineId
    execution_revision: N
    timer_job_id: EngineId
    timer_deadline: datetime


class HumanStartCommand(Closed):
    schema_: Literal["human-auth-start.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    actor: Actor = Field(repr=False)
    intake_ref: OpaqueRef
    command_id: OpaqueRef
    admission: AuditIntent
    guide_identity_ref: OpaqueRef
    definition: Definition
    input_pins: Pins
    start_facts_ref: OpaqueRef
    start_facts_digest: Sha256Digest
    projected_variables_digest: Sha256Digest

    @model_validator(mode="after")
    def bound(self) -> HumanStartCommand:
        validate_pins(self.input_pins)
        if self.command_id != self.admission.admitted_command_id:
            raise ValueError("invalid admission command")
        required = {"actor", "resource_authority", "guide", "start_facts", "document_policy"}
        if {p.kind for p in self.input_pins} - required - {"document_custody"} or not required <= {
            p.kind for p in self.input_pins
        }:
            raise ValueError("invalid start pins")
        if any(sum(p.kind == kind for p in self.input_pins) != 1 for kind in required):
            raise ValueError("invalid repeated source kind")
        for kind, ref in [
            ("actor", self.actor.principal_ref),
            ("guide", self.guide_identity_ref),
            ("start_facts", self.start_facts_ref),
        ]:
            if [(p.resource_ref) for p in self.input_pins if p.kind == kind] != [ref]:
                raise ValueError("invalid source pin")
        if (
            next(p.payload_digest for p in self.input_pins if p.kind == "start_facts")
            != self.start_facts_digest
        ):
            raise ValueError("invalid facts digest")
        return self


class GuideIdentity(Closed):
    guide_identity_ref: OpaqueRef
    source_ref: OpaqueRef
    namespace_ref: OpaqueRef
    source_guide_ref: OpaqueRef = Field(repr=False)
    cutover_ref: OpaqueRef
    cutover_revision: N
    legacy_state: Literal["absent_at_cutover", "existing", "ambiguous", "unreconciled"]
    prior_instance_id: EngineId | None
    prior_case_ref: OpaqueRef | None
    source: SourceProvenance

    @model_validator(mode="after")
    def legacy(self) -> GuideIdentity:
        if (self.legacy_state == "existing") != (
            self.prior_instance_id is not None and self.prior_case_ref is not None
        ) or ((self.prior_instance_id is None) != (self.prior_case_ref is None)):
            raise ValueError("invalid legacy linkage")
        return self


class StartFacts(Closed):
    facts_ref: OpaqueRef
    intake_ref: OpaqueRef
    guide_identity_ref: OpaqueRef
    beneficiary_pseudo_id: OpaqueRef
    provider_ref: OpaqueRef
    procedure_code: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    character: Literal["urgencia", "eletivo"]
    claimed_amount_cents: N
    document_refs: DocumentRefs
    requer_autorizacao: bool
    beneficiario_ativo: bool
    carencia_cumprida: bool
    documentacao_completa: bool
    missing_requirement_codes: tuple[OpaqueRef, ...] = Field(max_length=256)
    documentary_assessment_ref: OpaqueRef
    request_digest: Sha256Digest
    factual_sources: tuple[SourceProvenance, ...] = Field(min_length=1, max_length=64)
    policy_artifacts: tuple[ArtifactPin, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def attachments(self) -> StartFacts:
        validate_documents(self.document_refs)
        if len(set(self.missing_requirement_codes)) != len(self.missing_requirement_codes):
            raise ValueError("duplicate requirement")
        return self


class HumanDocumentCommand(Closed):
    schema_: Literal["human-auth-documents.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    actor: Actor = Field(repr=False)
    case_ref: OpaqueRef
    command_id: OpaqueRef
    admission: AuditIntent
    occurrence: OccurrenceBinding
    input_pins: Pins
    document_refs: DocumentRefs
    document_set_digest: Sha256Digest
    assessment_ref: OpaqueRef
    assessment_digest: Sha256Digest
    documentacao_completa: bool

    @model_validator(mode="after")
    def bound(self) -> HumanDocumentCommand:
        validate_documents(self.document_refs)
        validate_pins(self.input_pins)
        if (
            self.command_id != self.admission.admitted_command_id
            or digest(self.document_refs) != self.document_set_digest
        ):
            raise ValueError("invalid document binding")
        expected = {("actor", self.actor.principal_ref), ("document_policy", self.assessment_ref)} | {
            ("document_custody", d.document_ref) for d in self.document_refs
        }
        actual = {(p.kind, p.resource_ref) for p in self.input_pins}
        if (
            actual - expected
            != {(p.kind, p.resource_ref) for p in self.input_pins if p.kind == "resource_authority"}
            or len(actual - expected) != 1
            or not expected <= actual
        ):
            raise ValueError("invalid document pins")
        if (
            next(p.payload_digest for p in self.input_pins if p.kind == "document_policy")
            != self.assessment_digest
        ):
            raise ValueError("invalid assessment pin")
        return self


class DocumentOccurrence(Closed):
    request_ref: OpaqueRef
    scope: Scope
    case_ref: OpaqueRef
    process_instance_id: EngineId
    definition: Definition
    generation: N
    request_revision: N
    binding_revision: N
    creator_execution_id: EngineId
    producer_external_task_id: EngineId
    publication_external_task_id: EngineId | None
    state: Literal[
        "created", "awaiting_publication_worker", "bound", "consumed", "expired", "cancelled", "replaced"
    ]
    created_at: datetime
    policy_ref: OpaqueRef | None
    policy_digest: Sha256Digest | None
    binding: OccurrenceBinding | None
    successor_ref: OpaqueRef | None
    terminal_command_id: OpaqueRef | None

    @model_validator(mode="after")
    def bound(self) -> DocumentOccurrence:
        b = self.binding
        if self.state == "bound" and b is None:
            raise ValueError("missing binding")
        if b is not None and (
            b.request_ref,
            b.generation,
            b.request_revision,
            b.binding_revision,
            b.process_instance_id,
            b.definition,
        ) != (
            self.request_ref,
            self.generation,
            self.request_revision,
            self.binding_revision,
            self.process_instance_id,
            self.definition,
        ):
            raise ValueError("invalid occurrence binding")
        return self


class SessionBinding(Closed):
    """Original committed admission observation; shape alone is never authority."""

    session_ref: OpaqueRef
    authenticated_at: datetime
    session_expires_at: datetime
    authorization_until: datetime
    session_source_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    session_record_digest: Sha256Digest

    @model_validator(mode="after")
    def original_ceiling(self) -> SessionBinding:
        if not self.authenticated_at < self.authorization_until <= self.session_expires_at:
            raise ValueError("invalid original session interval")
        return self


class AuditIntentPayload(Closed):
    intent_ref: OpaqueRef
    intake_or_response_ref: OpaqueRef
    command_id: OpaqueRef
    actor: Actor = Field(repr=False)
    admitted_digest: Sha256Digest
    operation: Operation
    state: Literal["committed"]
    admitted_at: datetime
    session_binding: SessionBinding

    @model_validator(mode="after")
    def admitted_session(self) -> AuditIntentPayload:
        if not self.session_binding.authenticated_at <= self.admitted_at < self.session_binding.authorization_until:
            raise ValueError("invalid admission session interval")
        return self


class ResourceAuthority(Closed):
    authority_ref: OpaqueRef
    actor: Actor = Field(repr=False)
    beneficiary_ref: OpaqueRef
    provider_ref: OpaqueRef | None
    resource_kind: Literal["guide", "intake", "case"]
    resource_ref: OpaqueRef
    action: Literal["auth.start", "auth.documents.respond", "auth.receipt.read", "auth.document_context.read"]
    request_ref: OpaqueRef | None
    relationship_revision: N
    consent_revision: N
    grant_ref: OpaqueRef
    basis_ref: OpaqueRef
    legal_basis: Literal["consent", "other_qualified_basis"]
    consent_state: Literal["valid", "not_required", "revoked"]
    state: Literal["active", "revoked"]
    valid_from: datetime
    valid_until: datetime
    source: SourceProvenance


class DocumentCustody(Closed):
    document: DocumentRef
    resource_kind: Literal["intake", "case"]
    resource_ref: OpaqueRef
    creator_principal_ref: OpaqueRef
    screening_ref: OpaqueRef
    screening_revision: N
    screening_result: Literal["clean", "quarantined", "rejected", "pending"]
    custody_state: Literal["available", "revoked", "deleted"]
    key_custody_ref: OpaqueRef
    key_custody_revision: N
    valid_until: datetime


class DocumentPolicy(Closed):
    assessment_ref: OpaqueRef
    resource_kind: Literal["intake", "case"]
    resource_ref: OpaqueRef
    request_ref: OpaqueRef | None
    request_revision: N
    policy: ArtifactPin
    policy_revision: N
    recipient_principal_refs: tuple[OpaqueRef, ...] = Field(max_length=256)
    required_codes: tuple[OpaqueRef, ...] = Field(max_length=256)
    missing_codes: tuple[OpaqueRef, ...] = Field(max_length=256)
    submitted_response_digest: Sha256Digest | None
    effective_document_refs: DocumentRefs
    document_set_digest: Sha256Digest
    complete: bool
    source: SourceProvenance
    valid_until: datetime

    @model_validator(mode="after")
    def bound(self) -> DocumentPolicy:
        validate_documents(self.effective_document_refs)
        if self.document_set_digest != digest(self.effective_document_refs) or (
            self.resource_kind == "case"
        ) != (self.request_ref is not None):
            raise ValueError("invalid policy binding")
        if self.resource_kind == "intake" and (
            self.request_ref is not None
            or self.submitted_response_digest is not None
            or self.request_revision != 0
        ):
            raise ValueError("invalid intake policy")
        for refs in (self.recipient_principal_refs, self.required_codes, self.missing_codes):
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate policy reference")
        return self


InputPayload = (
    MembershipProjection
    | GuideIdentity
    | StartFacts
    | AuditIntentPayload
    | ResourceAuthority
    | DocumentCustody
    | DocumentPolicy
)
PAYLOAD_TYPES = {
    "actor": MembershipProjection,
    "guide": GuideIdentity,
    "start_facts": StartFacts,
    "audit_intent": AuditIntentPayload,
    "resource_authority": ResourceAuthority,
    "document_custody": DocumentCustody,
    "document_policy": DocumentPolicy,
}


class InputPublication(Closed):
    schema_: Literal["human-auth-input-publication.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    publication_id: OpaqueRef
    kind: InputKind
    resource_ref: OpaqueRef
    expected_generation: N
    source: SourceProvenance
    state: Literal["active", "frozen", "revoked"]
    payload: InputPayload | None = Field(repr=False)
    payload_digest: Sha256Digest | None
    valid_until: datetime

    @model_validator(mode="after")
    def bound(self) -> InputPublication:
        if self.state == "active":
            if (
                type(self.payload) is not PAYLOAD_TYPES[self.kind]
                or digest(self.payload) != self.payload_digest
            ):
                raise ValueError("invalid publication payload")
        elif self.payload is not None or self.payload_digest is not None:
            raise ValueError("invalid tombstone")
        if self.valid_until > self.source.valid_until:
            raise ValueError("invalid source lifetime")
        return self


class HumanReceiptQuery(Closed):
    schema_: Literal["human-auth-receipt-query.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    actor: Actor = Field(repr=False)
    query_id: OpaqueRef
    operation: Operation
    command_id: OpaqueRef
    expected_command_digest: Sha256Digest
    intake_or_case_ref: OpaqueRef


class DocumentContextQuery(Closed):
    schema_: Literal["human-auth-document-context-query.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    actor: Actor = Field(repr=False)
    query_id: OpaqueRef
    case_ref: OpaqueRef
    request_ref: OpaqueRef


class NativeEffectReceipt(Closed):
    schema_: Literal["human-auth-effect-receipt.v1"] = Field(alias="schema")
    scope: Scope
    receipt_ref: OpaqueRef
    command_id: OpaqueRef
    command_digest: Sha256Digest
    operation: Operation
    actor_principal_ref: OpaqueRef
    admission_ref: OpaqueRef
    admitted_digest: Sha256Digest
    intake_ref: OpaqueRef | None
    case_ref: OpaqueRef
    process_instance_id: EngineId
    definition: Definition
    guide_identity_ref: OpaqueRef | None
    request_ref: OpaqueRef | None
    occurrence_generation: N | None
    subscription_id: EngineId | None
    document_set_digest: Sha256Digest | None
    outcome: Literal["started", "existing", "documents_correlated"]
    committed_at: datetime

    @model_validator(mode="after")
    def complete(self) -> NativeEffectReceipt:
        start = (self.intake_ref, self.guide_identity_ref)
        documents = (
            self.request_ref,
            self.occurrence_generation,
            self.subscription_id,
            self.document_set_digest,
        )
        if self.operation == "auth.start":
            if (
                None in start
                or any(x is not None for x in documents)
                or self.outcome not in ("started", "existing")
            ):
                raise ValueError("invalid start receipt")
        elif any(x is not None for x in start) or None in documents or self.outcome != "documents_correlated":
            raise ValueError("invalid document receipt")
        return self


class NativeReceiptLookup(Closed):
    schema_: Literal["human-auth-receipt-lookup.v1"] = Field(alias="schema")
    scope: Scope
    query_id: OpaqueRef
    query_digest: Sha256Digest
    observed_at: datetime
    status: Literal["committed", "absent"]
    receipt: NativeEffectReceipt | None

    @model_validator(mode="after")
    def complete(self) -> NativeReceiptLookup:
        if (self.status == "committed") != (self.receipt is not None):
            raise ValueError("invalid lookup")
        return self


class DocumentContextResult(Closed):
    schema_: Literal["human-auth-document-context.v1"] = Field(alias="schema")
    scope: Scope
    query_id: OpaqueRef
    query_digest: Sha256Digest
    actor: Actor = Field(repr=False)
    occurrence: DocumentOccurrence
    input_pins: Pins
    valid_until: datetime

    @model_validator(mode="after")
    def bound(self) -> DocumentContextResult:
        validate_pins(self.input_pins)
        if self.scope != self.occurrence.scope:
            raise ValueError("invalid context scope")
        return self


class PublicationQuery(Closed):
    schema_: Literal["human-auth-publication-query.v1"] = Field(alias="schema")
    scope: Scope
    workload_ref: OpaqueRef
    query_id: OpaqueRef
    publication_id: OpaqueRef
    expected_digest: Sha256Digest


class PublicationReceipt(Closed):
    schema_: Literal["human-auth-input-receipt.v1"] = Field(alias="schema")
    scope: Scope
    publication_id: OpaqueRef
    request_digest: Sha256Digest
    kind: InputKind
    resource_ref: OpaqueRef
    previous_generation: N
    head_generation: N
    state: Literal["active", "frozen", "revoked"]
    payload_digest: Sha256Digest | None
    committed_at: datetime


class PublicationLookup(Closed):
    schema_: Literal["human-auth-publication-lookup.v1"] = Field(alias="schema")
    scope: Scope
    query_id: OpaqueRef
    query_digest: Sha256Digest
    status: Literal["committed", "absent"]
    receipt: PublicationReceipt | None
    observed_at: datetime

    @model_validator(mode="after")
    def complete(self) -> PublicationLookup:
        if (self.status == "committed") != (self.receipt is not None):
            raise ValueError("invalid lookup")
        return self


EffectCommand = HumanStartCommand | HumanDocumentCommand
Request = EffectCommand | HumanReceiptQuery | DocumentContextQuery | InputPublication | PublicationQuery
Result = (
    NativeEffectReceipt | NativeReceiptLookup | DocumentContextResult | PublicationReceipt | PublicationLookup
)
