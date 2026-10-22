"""Candidate commercial seams; no source binding or buyer authorization is installed.

Trace: owner commercial-platform plan; ADR-0037 XRD-03/04/05/09. These
value objects are assertions until a separately composed authority verifies them.
Account, prospect, party, payer and beneficiary references are distinct domains;
none is converted into a portable subject reference by this module.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class SalesTransport:
    """Minimal server transport metadata, with no headers, cookies, query or body.

    A production admission provider must resolve its authenticated connection from
    server-owned state. Host, source address, M2M identity and this object alone
    never prove a buyer, representation, purpose or lifecycle authorization.
    """

    connection_ref: str | None
    authenticated_caller_ref: str | None = None
    method: str
    path: str


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CommercialScope:
    """Server-qualified, expiring scope, never a request DTO or session cookie."""

    admission_ref: str
    workload_ref: str
    tenant_ref: str
    legal_entity_ref: str
    purpose_ref: str
    lifecycle_ref: str
    identity_receipt_ref: str
    source_binding_revision: str
    authority_revision: str
    correlation_ref: str
    source_grant_ref: str
    publication_ref: str
    allowed_operations: frozenset[str]
    valid_from: datetime
    valid_until: datetime
    audience: Literal["public", "account"]
    session_ref: str | None = None
    actor_ref: str | None = None
    actor_kind: Literal["customer", "staff", "vendor", "service"] = "service"
    represented_party_ref: str | None = None
    representation_ref: str | None = None
    account_ref: str | None = None
    granted_scopes: frozenset[str] = frozenset()
    resource_grant_ref: str | None = None
    journey_linkage_ref: str | None = None


class CommercialRefusalError(Exception):
    """Bounded internal token; never preserves foreign errors or subject values."""

    def __init__(
        self,
        status: Literal["denied", "conflict", "uncertain", "unavailable"],
        reason_code: str = "admission_required",
    ) -> None:
        self.status = status
        self.reason_code = reason_code
        super().__init__(status)


class SalesAdmission(ABC):
    """Unbound by default. Qualified providers acquire authority before body intake.

    `current` resolves current session, tenant/entity, representation, purpose,
    lifecycle, publication and source mandate again, including revocation. For
    operation.get it verifies current original-owner/source authority BEFORE any
    lookup; a target_command_ref is a selector, never permission or a redispatch. A DTO
    constructor or an environment flag cannot implement this contract.
    """

    @abstractmethod
    async def acquire(self, transport: SalesTransport) -> CommercialScope: ...

    @abstractmethod
    async def current(
        self,
        scope: CommercialScope,
        *,
        phase: Literal["before_body", "before_source", "before_effect", "before_disclosure"],
        command: CommercialCommand | None = None,
    ) -> CommercialScope: ...


CommercialOperation = Literal[
    "operator.get",
    "operator.choices",
    "operator.draft",
    "operator.review",
    "operator.activate",
    "operator.proposal.get",
    "financial_action.get",
    "operator.register",
    "catalog.get",
    "catalog.draft",
    "catalog.import",
    "catalog.review",
    "catalog.publish",
    "catalog.withdraw",
    "intake.describe",
    "prospect.save",
    "contact.request",
    "party.prepare",
    "party.submit",
    "party.link",
    "offer.quote",
    "offer.terms",
    "contract.issue",
    "contract.get",
    "receivable.ensure",
    "receivable.get",
    "payment.create",
    "payment.reconcile",
    "mandate.create",
    "mandate.get",
    "mandate.revoke",
    "financial_action.request",
    "acceptance.record",
    "operation.get",
    "work.list",
    "work.claim",
    "work.assign",
    "work.handoff",
    "work.reconcile",
    "journey.save",
    "journey.get",
    "support.handoff",
]
CommercialStatus = Literal["succeeded", "denied", "conflict", "uncertain", "unavailable"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class TermsSelection:
    """Schema-validated selectors only; never proof of presentation or authority."""

    quote_ref: str
    source_revision: int
    terms_digest: str
    terms_review_ref: str | None = None
    decision: Literal["accept", "decline"] | None = None


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class TermsPresentation:
    terms_review_ref: str
    review_state: Literal["pending", "presented"]
    presentation_receipt_ref: str | None


class IssuerDocumentOrigins(ABC):
    """Separately qualified server source binding; never PSP/browser configuration.

    Resolve current issuer document origins and authenticated handoff validity for
    these exact source selectors. Redirect/authentication policy belongs to that
    qualified source. Missing binding or stale source evidence must refuse.
    """

    @abstractmethod
    def current_origins(self, selection: TermsSelection, terms_review_ref: str) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class TermsReviewAuthority:
    """Authenticated current issuer observation, not constructed from request refs.

    scope_digest binds tenant, actor/account, representation, purpose and source.
    Document validity and presentation receipt validity are independent source
    facts. The acceptance writer must independently recheck them atomically.
    """

    selection: TermsSelection
    scope_digest: str
    terms_review_ref: str
    review_state: Literal["pending", "presented"]
    presentation_receipt_ref: str | None
    presentation_valid_until: datetime | None
    currentness_ref: str
    document_validity_ref: str
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class PaymentChoiceSelection:
    """Closed request selectors; a current resource-bound proof is still required."""

    choice_ref: str
    scope_kind: Literal["receivable", "contract"]
    scope_ref: str
    provider: str
    method: str
    operation: str
    use: str


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class PaymentChoiceAuthority:
    """Source-qualified exact choice intersection, never administrative readiness.

    The provider resolves current original account, implemented wire profile,
    contractual use, actor/payer/representation and resource eligibility. It
    revalidates the immutable tuple/version at each phase and atomically at AMH.
    """

    selection: PaymentChoiceSelection
    scope_digest: str
    command_digest: str
    capability_ref: str
    source_revision: int
    account_binding_ref: str
    contractual_use_ref: str
    currentness_ref: str
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CommercialResultAuthority:
    """Current qualified exact response, resolved after source IO.

    Binds each resource to current tenant/actor/payer/representation and purpose;
    work queues/directory, customer discovery, case vocabulary/labels/topics,
    public fields and resource payment choices require their own source grants.
    Empty discovery is valid only after a real authorized current source read.
    C6-3 includes each journey's independently sourced profile, exact progressive
    linkage/commercial acceptance and quote facts, plus every receivable/payment/
    mandate/financial-action read reference under its ORIGINAL journey, contract,
    payer, account, tenant and purpose. Profile/account/prospect and OP03/commercial
    acceptance are separate identities, never aliases or inferred replacements.
    Null/empty is no usable continuation fact, not proof of absent obligations.
    C6-4 fresh plan choices require the exact active approved catalog/version/
    product tuple and label under the current owner/operator/tenant/purpose, even
    when no journey exists. Private option_ref is source-qualified independently,
    never inferred from public optionRef. Recovery plan choices are always empty.
    Digest equality alone does not supply this authority.
    """

    command_digest: str
    scope_digest: str
    result_digest: str
    resource_grant_ref: str
    currentness_ref: str
    valid_until: datetime


class CommercialDeliveryOrigins(ABC):
    """Current administrative issuer / external clinical channel policy.

    Separate from PSP origins and terms selection. Implementations verify exact
    resource ownership, published document/topic lineage, authentication/redirect
    policy and present delivery validity, including explicitly unknown expiry.
    """

    @abstractmethod
    def current_origins(
        self, *, kind: str, resource_ref: str, document_ref: str, source_revision: int
    ) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CommercialCommand:
    """Closed envelope plus immutable bytes validated by source-owned candidate schema.

    Payload stays opaque in payer core, as with CanonicalOutcome. It is never an
    unvalidated dictionary and never selects an endpoint, credential or authority.
    """

    command: CommercialOperation
    command_ref: str
    expected_revision: int | None
    wire_bytes: bytes
    terms_selection: TermsSelection | None = None
    payment_selection: PaymentChoiceSelection | None = None


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class OperationalResult:
    status: CommercialStatus
    operation_ref: str | None
    reason_code: str | None
    wire_bytes: bytes
    terms_presentation: TermsPresentation | None = None
    authorization_command: CommercialOperation | None = None
    requires_current_result: bool = False


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CommercialPublication:
    """Verified publication observation; mere construction establishes no authority."""

    publication_ref: str
    source_contract_digest: str
    shared_contract_digest: str
    source_binding_ref: str
    source_binding_revision: str
    tenant_ref: str
    legal_entity_ref: str
    currentness_ref: str
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class IssuedContractAuthority:
    """Source-verified designated-issuer receipt and current proceed authority.

    AMH verifies signature/authenticated issuer, audience, revocation, exact quote,
    acceptance, terms, party/payer, source revision and target linkage. OP03/OP04
    receipts alone can never satisfy this seam. No signature or foreign PHI is
    copied into payer-core fields or browser projections.
    """

    contract_ref: str
    issuer_receipt_ref: str
    proceed_authority_ref: str
    payer_ref: str
    source_revision_ref: str
    signed_receipt_verification_ref: str
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class CommercialPreparation:
    """Current source-qualified command proof, not an approval minted in Python.

    `execute` must atomically recheck this evidence, business idempotency and the
    original source/account bindings. A lease or a replay key is not permission.
    """

    operation_ref: str
    command_digest: str
    scope_digest: str
    source_evidence_ref: str
    publication: CommercialPublication
    valid_until: datetime
    registry_readiness_ref: str | None = None
    ready_methods: frozenset[str] = frozenset()
    issued_contract: IssuedContractAuthority | None = None
    terms_review: TermsReviewAuthority | None = None
    payment_choice: PaymentChoiceAuthority | None = None


class CommercialCodec(ABC):
    """No editable source schema in this repository; bytes supplied by trusted composition."""

    @property
    @abstractmethod
    def schema_digest(self) -> str: ...

    @abstractmethod
    def parse_command(self, body: bytes) -> CommercialCommand: ...

    @abstractmethod
    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult: ...

    def parse_public_result(self, body: bytes) -> bytes:
        raise CommercialRefusalError("unavailable", "source_unavailable")

    @abstractmethod
    def refusal(
        self, status: CommercialStatus, reason_code: str, operation_ref: str | None = None
    ) -> OperationalResult: ...


class CommercialSource(ABC):
    """Unbound source port. No effect, retry, default issuer or fixture fallback.

    Production implementations require source publication and full scope/resource
    provenance before preparation; execute must preserve durable source business
    dedupe and reconcile original effects under CURRENT authority. For operation.get,
    original owner/source/resource authorization precedes lookup and disclosure;
    only a bounded sanitized original result is read, never redispatched. No new HTTP
    preparation endpoint is implied by this internal seam.
    """

    @abstractmethod
    async def admit(self, scope: CommercialScope) -> CommercialPublication: ...

    @abstractmethod
    async def prepare(self, command: CommercialCommand, scope: CommercialScope) -> CommercialPreparation: ...

    @abstractmethod
    async def execute(
        self, command: CommercialCommand, scope: CommercialScope, preparation: CommercialPreparation
    ) -> OperationalResult: ...

    async def result_authority(
        self, command: CommercialCommand, scope: CommercialScope, result: OperationalResult
    ) -> CommercialResultAuthority:
        raise CommercialRefusalError("unavailable", "source_unavailable")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class PublicCatalogScope:
    """Machine-only public-data scope, independent of every customer admission."""

    workload_ref: str
    tenant_ref: str
    legal_entity_ref: str
    operator_ref: str
    source_grant_ref: str
    source_binding_revision: str
    publication_ref: str
    lifecycle_ref: str
    currentness_ref: str
    purpose: Literal["sales_public_catalog"]
    granted_scope: Literal["interop/sales.catalog.public.read"]
    valid_until: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class PublicCatalogProof:
    """Exact current public-audience publication/field policy from trusted source.

    A response digest requires independent verification of its source authority,
    operator/catalogue lineage, current price/network/association revisions,
    withdrawal/expiry and every field's public audience. Unknown expiry is
    explicitly qualified here, not interpreted as unlimited validity.
    """

    scope_digest: str
    schema_digest: str
    publication_ref: str
    currentness_ref: str
    result_digest: str | None
    valid_until: datetime


class PublicCatalogAdmission(ABC):
    @abstractmethod
    async def acquire(self, transport: SalesTransport) -> PublicCatalogScope: ...

    @abstractmethod
    async def current(self, scope: PublicCatalogScope, *, phase: str) -> PublicCatalogScope: ...


class PublicCatalogSource(ABC):
    @abstractmethod
    async def authorize(self, scope: PublicCatalogScope, result_digest: str | None) -> PublicCatalogProof: ...

    @abstractmethod
    async def read(self, scope: PublicCatalogScope) -> bytes: ...


def public_scope_digest(scope: PublicCatalogScope) -> str:
    values = asdict(scope)
    values["valid_until"] = scope.valid_until.isoformat()
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command_digest(command: CommercialCommand) -> str:
    return hashlib.sha256(command.wire_bytes).hexdigest()


def scope_digest(scope: CommercialScope) -> str:
    values = asdict(scope)
    values["allowed_operations"] = sorted(scope.allowed_operations)
    values["granted_scopes"] = sorted(scope.granted_scopes)
    values["valid_from"] = scope.valid_from.isoformat()
    values["valid_until"] = scope.valid_until.isoformat()
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
