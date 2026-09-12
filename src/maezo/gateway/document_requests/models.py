"""Closed E04 occurrence/inbox B1/B2/B1-R1 records; parsing never grants authority."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from maezo.gateway.communications.models import CommunicationScope, alive
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.human.auth_profile import Definition, DocumentPolicy, Scope
from maezo.gateway.human.auth_profile import PublicationReceipt as NativePublicationReceipt
from maezo.gateway.human.read_profile import ArtifactPin, Closed, SourceProvenance, digest, parse_model, wire
from maezo.portal.contracts.intake import DecimalRevision, ResourceRef
from maezo.portal.contracts.models import Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

Ref = ResourceRef
Hash = Sha256Digest
Revision = DecimalRevision
Positive = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,18}$")]
NOTICE = (
    "Existe uma solicitação de documentos vinculada a este caso. "
    "Consulte o pedido para ver os documentos e o andamento atuais."
)


def require(value: bool, code: str = "denied") -> None:
    if not value:
        raise ExternalCaseError(code)  # type: ignore[arg-type]


class Private(Closed):
    def __repr_args__(self) -> Iterable[tuple[str | None, object]]:
        return ()


def packed(value: Closed) -> bytes:
    raw = canonicalize(wire(value))
    require(len(raw) <= 65536, "unavailable")
    return raw


def parsed[T: Closed](kind: type[T], raw: bytes) -> T:
    require(type(raw) is bytes and len(raw) <= 65536, "unavailable")
    result = parse_model(kind, strict_loads(raw))
    require(packed(result) == raw, "unavailable")
    return result


class SystemProducer(Private):
    producer_ref: Ref
    issuer: Ref
    subject: Ref
    tenant: Ref
    environment: Ref
    identity_revision: Revision
    identity_receipt_ref: Ref
    identity_digest: Hash

    @model_validator(mode="after")
    def exact_identity(self) -> Self:
        expected = digest(
            {
                "schema": "maezo.communication.system-sender.v1",
                **{
                    name: getattr(self, name)
                    for name in ("tenant", "environment", "producer_ref", "issuer", "subject")
                },
            }
        )
        if expected != self.identity_digest:
            raise ValueError("system identity binding")
        return self


class RequestIdentity(Private):
    scope: Scope
    definition: Definition
    case_ref: Ref
    request_ref: Ref
    generation: Positive
    process_instance_id: Ref
    creator_execution_id: Ref
    producer_external_task_id: Ref
    created_at: datetime

    def communication_scope(self) -> CommunicationScope:
        return CommunicationScope(tenant=self.scope.tenant, environment=self.scope.environment)


class ProducerContext(RequestIdentity):
    request_revision: Revision
    binding_revision: Revision
    occurrence_state: Literal["created", "awaiting_publication_worker", "bound"]
    policy_ref: Ref | None
    policy_digest: Hash | None

    @model_validator(mode="after")
    def policy_pair(self) -> Self:
        if (self.policy_ref is None) != (self.policy_digest is None):
            raise ValueError("policy pair")
        return self

    def identity(self) -> RequestIdentity:
        return RequestIdentity.model_validate({k: getattr(self, k) for k in RequestIdentity.model_fields})


class SystemRecipient(Private):
    principal_ref: Ref
    identity_digest: Hash
    audience: Literal["staff", "beneficiary", "provider"]
    source_revision: Revision
    policy_digest: Hash
    valid_until: datetime


def recipient_digest(recipients: tuple[SystemRecipient, ...]) -> str:
    return digest([{k: v for k, v in wire(r).items() if k != "valid_until"} for r in recipients])


def child_id(kind: str, request: RequestIdentity, sender: str, outer: str, recipient: str) -> str:
    require(kind in {"body", "message"})
    return digest(
        dict(
            schema=f"maezo.auth-document-request.{kind}-command.v1",
            scope=wire(request.communication_scope()),
            sender_identity_digest=sender,
            outer_command_id=outer,
            request_identity_digest=digest(request),
            recipient_identity_digest=recipient,
        )
    )


class PolicyAssessmentVersion(Private):
    schema_: Literal["auth-document-request-assessment-version.v1"] = Field(alias="schema")
    request_identity_digest: Hash
    request_revision: Revision
    stable_definition_digest: Hash
    policy_ref: Ref
    policy_digest: Hash
    policy_publication_ref: Ref
    policy_publication_digest: Hash
    publication_request_digest: Hash
    publication_head_generation: Positive
    source_receipt_ref: Ref
    source_provenance_digest: Hash


def assessment(
    request: RequestIdentity, policy: DocumentPolicy, receipt: NativePublicationReceipt
) -> PolicyAssessmentVersion:
    require(
        policy.request_ref == request.request_ref
        and policy.resource_kind == "case"
        and policy.resource_ref == request.case_ref
    )
    require(
        receipt.scope == request.scope and receipt.kind == "document_policy" and receipt.state == "active"
    )
    require(receipt.resource_ref == policy.assessment_ref and receipt.payload_digest == digest(policy))
    definition = {
        name: wire(getattr(policy, name))
        for name in ("policy", "policy_revision", "recipient_principal_refs", "required_codes")
    }
    return PolicyAssessmentVersion(
        schema="auth-document-request-assessment-version.v1",
        request_identity_digest=digest(request),
        request_revision=str(policy.request_revision),
        stable_definition_digest=digest(
            {"schema": "maezo.auth-document-request.definition.v1", "definition": definition}
        ),
        policy_ref=policy.assessment_ref,
        policy_digest=digest(policy),
        policy_publication_ref=receipt.publication_id,
        policy_publication_digest=digest(receipt),
        publication_request_digest=receipt.request_digest,
        publication_head_generation=str(receipt.head_generation),
        source_receipt_ref=policy.source.receipt_ref,
        source_provenance_digest=digest(policy.source),
    )


class RequestBodyCommand(Private):
    schema_: Literal["auth-document-request-body.v1"] = Field(alias="schema")
    command_id: Ref
    outer_command_id: Ref
    message_command_id: Ref
    sender_identity_digest: Hash
    request: RequestIdentity
    request_revision: Revision
    assessment_version_digest: Hash
    recipient_set_digest: Hash
    recipient_identity_digest: Hash
    template: ArtifactPin
    body: str = Field(repr=False)

    @model_validator(mode="after")
    def exact_body(self) -> Self:
        if self.body != NOTICE:
            raise ValueError("fixed body required")
        for kind, actual in (("body", self.command_id), ("message", self.message_command_id)):
            if actual != child_id(
                kind,
                self.request,
                self.sender_identity_digest,
                self.outer_command_id,
                self.recipient_identity_digest,
            ):
                raise ValueError("child identity")
        return self


class RequestBodyReceipt(Private):
    schema_: Literal["auth-document-request-body-receipt.v1"] = Field(alias="schema")
    scope: CommunicationScope
    case_ref: Ref
    sender_identity_digest: Hash
    outer_command_id: Ref
    body_command_id: Ref
    message_command_id: Ref
    request_identity_digest: Hash
    request_revision: Revision
    assessment_version_digest: Hash
    recipient_set_digest: Hash
    recipient_identity_digest: Hash
    template: ArtifactPin
    body_ref: Ref
    body_request_digest: Hash
    body_authority_receipt_ref: Ref
    body_authority_digest: Hash


class RequestBodyReceiptSelector(Private):
    mode: Literal["body_receipt"]
    recipient_identity_digest: Hash
    sender_identity_digest: Hash
    outer_command_id: Ref
    body_command_id: Ref
    message_command_id: Ref
    request_identity_digest: Hash
    request_revision: Revision
    assessment_version_digest: Hash
    recipient_set_digest: Hash
    template: ArtifactPin
    body_request_digest: Hash
    body_ref: Ref | None


def selector(body: RequestBodyCommand, body_ref: str | None = None) -> RequestBodyReceiptSelector:
    return RequestBodyReceiptSelector(
        mode="body_receipt",
        body_command_id=body.command_id,
        request_identity_digest=digest(body.request),
        body_request_digest=digest(body),
        body_ref=body_ref,
        **{
            k: getattr(body, k)
            for k in (
                "recipient_identity_digest",
                "sender_identity_digest",
                "outer_command_id",
                "message_command_id",
                "request_revision",
                "assessment_version_digest",
                "recipient_set_digest",
                "template",
            )
        },
    )


class BodyFound(Private):
    state: Literal["found"]
    selector_digest: Hash
    receipt: RequestBodyReceipt


class BodyRefused(Private):
    state: Literal["refused"]


class DeliveryRecord(Private):
    recipient_identity_digest: Hash
    sender_identity_digest: Hash
    origin_command_id: Ref
    origin_command_digest: Hash
    body_command_id: Ref
    message_command_id: Ref
    message_request_digest: Hash
    communication_ref: Ref
    body_ref: Ref
    body_request_digest: Hash
    body_authority_receipt_ref: Ref
    body_authority_digest: Hash
    message_authority_receipt_ref: Ref
    message_authority_digest: Hash
    notice_request_revision: Revision
    notice_policy_digest: Hash
    notice_assessment_version_digest: Hash
    inbox_available_at: datetime


class NewDelivery(Private):
    mode: Literal["new"]
    recipient_identity_digest: Hash
    body_command_id: Ref
    message_command_id: Ref
    body_ref: Ref
    body_request_digest: Hash


class ExistingDelivery(Private):
    mode: Literal["existing"]
    recipient_identity_digest: Hash
    delivery: DeliveryRecord

    @model_validator(mode="after")
    def same_recipient(self) -> Self:
        if self.recipient_identity_digest != self.delivery.recipient_identity_digest:
            raise ValueError("recipient mismatch")
        return self


DeliveryEntry = NewDelivery | ExistingDelivery


class RequestDeliveryCommand(Private):
    schema_: Literal["auth-document-request-delivery.v1"] = Field(alias="schema")
    command_id: Ref
    sender_identity_digest: Hash
    request: RequestIdentity
    request_revision: Revision
    policy_ref: Ref
    policy_digest: Hash
    policy_publication_ref: Ref
    policy_publication_digest: Hash
    assessment_version_digest: Hash
    context_query_digest: Hash
    recipient_set_digest: Hash
    deliveries: tuple[DeliveryEntry, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def exact_entries(self) -> Self:
        ids = [e.recipient_identity_digest for e in self.deliveries]
        if ids != sorted(set(ids)):
            raise ValueError("ordered unique recipients required")
        for entry in self.deliveries:
            if isinstance(entry, NewDelivery):
                for kind, actual in (("body", entry.body_command_id), ("message", entry.message_command_id)):
                    if (
                        child_id(
                            kind,
                            self.request,
                            self.sender_identity_digest,
                            self.command_id,
                            entry.recipient_identity_digest,
                        )
                        != actual
                    ):
                        raise ValueError("child key mismatch")
        return self


class RequestInboxReceipt(Private):
    schema_: Literal["auth-document-request-inbox-receipt.v1"] = Field(alias="schema")
    command_id: Ref
    command_digest: Hash
    request: RequestIdentity
    request_revision: Revision
    policy_ref: Ref
    policy_digest: Hash
    assessment_version_digest: Hash
    deliveries: tuple[DeliveryRecord, ...] = Field(min_length=1, max_length=256)
    committed_at: datetime


class PreserveBodyBinding(Private):
    recipient_identity_digest: Hash
    outer_command_id: Ref
    body_command_id: Ref
    message_command_id: Ref
    body_ref: None
    body_request_digest: Hash


class SystemAccess(Private):
    scope: CommunicationScope
    producer: SystemProducer
    operation: Literal[
        "preserve_request_body", "publish_request_inbox", "read_request_receipt", "read_request_body_receipt"
    ]
    request: RequestIdentity
    request_revision: Revision
    policy_ref: Ref
    policy_digest: Hash
    policy_publication_ref: Ref
    policy_publication_digest: Hash
    assessment_version_digest: Hash
    context_query_digest: Hash
    command_id: Ref
    request_digest: Hash

    @model_validator(mode="after")
    def scope_bound(self) -> Self:
        if self.scope != self.request.communication_scope() or (
            self.producer.tenant,
            self.producer.environment,
        ) != (self.scope.tenant, self.scope.environment):
            raise ValueError("system scope mismatch")
        return self


class SystemGrant(Private):
    access: SystemAccess
    authority_receipt_ref: Ref
    authority_digest: Hash
    source: SourceProvenance
    valid_until: datetime
    policy_valid_until: datetime
    template: ArtifactPin
    recipients: tuple[SystemRecipient, ...] = Field(min_length=1, max_length=256)
    body_bindings: tuple[
        PreserveBodyBinding | NewDelivery | ExistingDelivery | RequestBodyReceiptSelector, ...
    ] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def bindings(self) -> Self:
        ids = [r.identity_digest for r in self.recipients]
        bodies = [b.recipient_identity_digest for b in self.body_bindings]
        if ids != sorted(set(ids)) or bodies != sorted(set(bodies)) or not set(bodies) <= set(ids):
            raise ValueError("recipient binding")
        operation = self.access.operation
        allowed = {
            "preserve_request_body": (PreserveBodyBinding,),
            "publish_request_inbox": (NewDelivery, ExistingDelivery),
            "read_request_receipt": (ExistingDelivery,),
            "read_request_body_receipt": (RequestBodyReceiptSelector,),
        }[operation]
        if any(not isinstance(b, allowed) for b in self.body_bindings):
            raise ValueError("wrong operation binding")
        if operation in {"preserve_request_body", "read_request_body_receipt"} and len(bodies) != 1:
            raise ValueError("single selected body required")
        if operation != "preserve_request_body" and ids != bodies:
            raise ValueError("exact recipients required")
        return self

    def ceiling(self) -> datetime:
        return alive(
            self.valid_until,
            self.policy_valid_until,
            self.source.valid_until,
            *(r.valid_until for r in self.recipients),
        )


class SystemPublication(Private):
    publication_ref: Ref
    access: SystemAccess
    expected_revision: Revision
    source_receipt_ref: Ref
    source_digest: Hash
    valid_until: datetime
    grant: SystemGrant | None


class SystemPublicationReceipt(Private):
    publication_ref: Ref
    request_digest: Hash
    revision: Revision
