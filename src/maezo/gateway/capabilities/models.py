"""Closed technical candidate for the v2.1 internal contracts, still PROPOSED.

The field maps mirror the admitted plan, not a published AMH or provider wire.
Parsing proves shape only. Publication, current authority and source receipts
remain the admission boundary's responsibility. No domain memberships ship here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)

from maezo.portal.engine.profile import canonicalize, strict_loads

CONTRACT_STATE = "PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED"
CONTRACT_STATE_PUBLISHED = "INTERNAL_CONTRACT_PUBLISHED"
CONTRACT_STATE_PUBLICATION_WITHDRAWN = "INTERNAL_CONTRACT_PUBLICATION_WITHDRAWN"
CANDIDATE_SCHEMA_VERSION = "v21-capabilities.proposed.v1"
PROVIDER_SCHEMA_VERSION = "provider-capabilities.internal.v1"
OperationName = Literal[
    "access.resolve",
    "offer.compose",
    "acceptance.record",
    "enrollment.request",
    "reservation.command",
    "fulfillment.observe",
    "case.open_or_update",
    "external_wait.settle",
    "notice.prepare_or_send",
    "milestone.publish",
    "feedback.record",
]


class CapabilityRefusalReason(StrEnum):
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    AUTHORITY_UNPROVEN = "AUTHORITY_UNPROVEN"
    PURPOSE_DENIED = "PURPOSE_DENIED"
    STALE_REVISION = "STALE_REVISION"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"


class CapabilityContractError(ValueError):
    """Bounded outward error; never carries payload or provider validation text."""

    def __init__(self, reason: CapabilityRefusalReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _reference(value: str) -> str:
    # Opaque means no source prefixes, aliases or identifier semantics inferred.
    if not value.strip():
        raise ValueError("reference unavailable")
    return value


def _instant(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant requires timezone")
    return value


Ref = Annotated[str, AfterValidator(_reference)]
Instant = Annotated[str, AfterValidator(_instant)]


class CandidateDTO(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)


class CapabilityEnvelope(CandidateDTO):
    schema_version: Literal["v21-capabilities.proposed.v1"]
    operation_name: OperationName
    tenant_ref: Ref
    legal_entity_ref: Ref
    journey_ref: Ref
    correlation_ref: Ref
    causation_ref: Ref
    idempotency_key: Ref
    expected_business_revision: Ref
    source_authority_ref: Ref
    policy_revision: Ref
    data_classification: Ref


class ProviderCapabilityEnvelope(CandidateDTO):
    """Explicit provider revision; the original v21 envelope remains closed."""

    schema_version: Literal["provider-capabilities.internal.v1"]
    operation_name: Literal[
        "access.resolve",
        "offer.compose",
        "acceptance.record",
        "enrollment.request",
        "reservation.command",
        "fulfillment.observe",
        "case.open_or_update",
        "external_wait.settle",
        "notice.prepare_or_send",
        "milestone.publish",
        "feedback.record",
        "contract.authority.resolve",
    ]
    tenant_ref: Ref
    legal_entity_ref: Ref
    journey_ref: Ref
    correlation_ref: Ref
    causation_ref: Ref
    idempotency_key: Ref
    expected_business_revision: Ref
    source_authority_ref: Ref
    policy_revision: Ref
    data_classification: Ref


AnyCapabilityEnvelope = CapabilityEnvelope | ProviderCapabilityEnvelope
ClausePurpose = Literal[
    "payment_prazo",
    "glosa_symmetry",
    "tabela_valor",
    "prior_notice_prazo",
    "rescisao_procedimento",
    "credenciamento_conteudo",
]


class ContractAuthorityIntent(CandidateDTO):
    provider_ref: Ref
    contract_instrument_ref: Ref | None = None
    clause_purpose: ClausePurpose
    purpose_policy_ref: Ref
    expected_business_revision: Ref | None = None


class ContractAuthorityResult(CandidateDTO):
    contract_status: Literal["proposto", "vigente", "suspenso", "rescindido", "unknown"]
    clause_refs: tuple[Ref, ...]
    declared_value_refs: tuple[Ref, ...]
    declared_at: Instant | None = None
    evidence_ref: Ref | None = None
    source_revision_ref: Ref | None = None
    authority_receipt_ref: Ref
    single_provider_scope: Literal[True]
    no_inter_provider_aggregation: Literal[True]

    @field_validator("single_provider_scope", "no_inter_provider_aggregation", mode="before")
    @classmethod
    def exact_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("literal true required")
        return value

    @model_validator(mode="after")
    def no_unknown_success(self) -> Self:
        if self.contract_status == "unknown":
            raise ValueError("unknown is a typed refusal, never a source receipt")
        return self


class ContractResolutionUnavailable(CandidateDTO):
    """Negative observation; no authority receipt is fabricated for an absent source."""

    contract_status: Literal["unknown"] = "unknown"


@dataclass(frozen=True, slots=True)
class DeclaredMemberships:
    """Source-declared vocabulary, supplied by trusted composition, never callers.

    These empty defaults deny OP07/OP10 rather than publish invented members.
    The admission verifier must bind their source publication to its lease.
    """

    case_kinds: frozenset[str] = frozenset()
    case_statuses: frozenset[str] = frozenset()
    milestone_kinds: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for members in (self.case_kinds, self.case_statuses, self.milestone_kinds):
            if type(members) is not frozenset or any(
                type(member) is not str or not member.strip() for member in members
            ):
                raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)


def _declared(value: str, info: ValidationInfo, vocabulary: str) -> str:
    context = info.context
    memberships = context.get("memberships") if isinstance(context, dict) else None
    if not isinstance(memberships, DeclaredMemberships) or value not in getattr(memberships, vocabulary):
        raise ValueError("domain membership unpublished")
    return value


class ContextAccessIntent(CandidateDTO):
    subject_type: Literal["prospect", "beneficiary"]
    subject_or_prospect_ref: Ref
    operation_scope_ref: Ref
    purpose_policy_ref: Ref
    expected_consent_revision_ref: Ref | None = None
    source_context_refs: tuple[Ref, ...]


class ContextAccessResult(CandidateDTO):
    access_status: Literal["available", "denied", "unavailable"]
    context_refs: tuple[Ref, ...]
    source_revision_ref: Ref | None = None
    access_decision_ref: Ref


class OfferOptionsIntent(CandidateDTO):
    catalogue_ref: Ref
    catalogue_version: Ref
    administrative_preferences_ref: Ref
    availability_refs: tuple[Ref, ...]
    purpose_policy_ref: Ref


class VersionedOfferOptions(CandidateDTO):
    offer_ref: Ref | None = None
    offer_version: Ref | None = None
    valid_until: Instant | None = None
    option_refs: tuple[Ref, ...]
    explanation_refs: tuple[Ref, ...]
    status: Literal["usable", "unavailable", "review_required"]


class AcceptanceCommand(CandidateDTO):
    offer_ref: Ref
    offer_version: Ref
    decision: Literal["accept", "decline"]
    terms_evidence_ref: Ref
    customer_authority_proof_ref: Ref


class AcceptanceEvidenceReceipt(CandidateDTO):
    acceptance_ref: Ref
    recorded_decision: Literal["accepted", "declined"]
    recorded_at: Instant
    receipt_ref: Ref
    business_revision: Ref


class EnrollmentIntent(CandidateDTO):
    accepted_commitment_ref: Ref
    prospect_linkage_ref: Ref
    enrollment_authority_contract_ref: Ref
    administrative_evidence_refs: tuple[Ref, ...]


class EnrollmentAuthorityReceipt(CandidateDTO):
    enrollment_status: Literal["pending", "issued", "refused", "review_required"]
    enrollment_ref: Ref | None = None
    issuer_receipt_ref: Ref | None = None
    portable_subject_ref: Ref | None = None
    source_revision_ref: Ref | None = None


class ReservationCommand(CandidateDTO):
    provider_ref: Ref
    operation: Literal["hold", "confirm", "reschedule", "cancel"]
    offer_or_booking_ref: Ref
    expected_provider_revision: Ref
    customer_commitment_ref: Ref


class ProviderReservationReceipt(CandidateDTO):
    reservation_status: Literal["pending", "held", "confirmed", "cancelled", "disrupted"]
    booking_ref: Ref | None = None
    hold_expires_at: Instant | None = None
    provider_receipt_ref: Ref | None = None
    provider_revision: Ref | None = None


class FulfillmentObservation(CandidateDTO):
    fulfillment_ref: Ref
    source_event_or_observation_ref: Ref
    source_authority_contract_ref: Ref
    expected_revision: Ref


class VerifiedFulfillmentFact(CandidateDTO):
    fact_kind: Literal["performed", "result_available", "no_show", "disrupted", "pending"]
    factual_at: Instant | None = None
    source_fact_ref: Ref
    source_revision: Ref
    clinical_result_context_ref: Ref | None = None


class ResolutionCaseIntent(CandidateDTO):
    problem_ref: Ref
    origin_case_or_journey_ref: Ref
    case_kind: Ref
    requester_authority_ref: Ref
    evidence_refs: tuple[Ref, ...]
    formal_regulatory_request_ref: Ref | None = None

    @field_validator("case_kind")
    @classmethod
    def declared_kind(cls, value: str, info: ValidationInfo) -> str:
        return _declared(value, info, "case_kinds")


class ResolutionCaseStatus(CandidateDTO):
    case_ref: Ref
    protocol_ref: Ref | None = None
    case_status: Ref
    authority_receipt_ref: Ref
    business_revision: Ref

    @field_validator("case_status")
    @classmethod
    def declared_status(cls, value: str, info: ValidationInfo) -> str:
        return _declared(value, info, "case_statuses")


class ExternalWaitIntent(CandidateDTO):
    wait_ref: Ref
    expected_producer_ref: Ref
    correlation_ref: Ref
    authoritative_deadline_ref: Ref | None = None
    source_outcome_ref: Ref | None = None


class ExternalWaitOutcome(CandidateDTO):
    wait_status: Literal["pending", "completed", "timed_out", "incident"]
    outcome_evidence_ref: Ref | None = None
    settlement_revision: Ref
    owner_case_ref: Ref


class NoticeIntent(CandidateDTO):
    notice_ref: Ref
    notice_class: Literal["mandatory", "facultative"]
    authorized_content_ref: Ref
    recipient_authority_ref: Ref
    confirmed_channel_ref: Ref
    delivery_policy_ref: Ref


class DeliveryEvidenceReceipt(CandidateDTO):
    delivery_status: Literal["pending", "attempted", "delivered", "failed", "unknown"]
    provider_delivery_receipt_ref: Ref | None = None
    attempt_revision: Ref
    metadata_publication_receipt_ref: Ref | None = None


class VerifiedMilestoneIntent(CandidateDTO):
    source_fact_ref: Ref
    milestone_kind: Ref
    source_revision: Ref
    audience_contract_ref: Ref
    prediction_ref: Ref | None = None

    @field_validator("milestone_kind")
    @classmethod
    def declared_kind(cls, value: str, info: ValidationInfo) -> str:
        return _declared(value, info, "milestone_kinds")


class MilestonePublicationReceipt(CandidateDTO):
    publication_status: Literal["committed", "pending", "failed"]
    outbox_receipt_ref: Ref | None = None
    milestone_revision: Ref


class CustomerFeedbackIntent(CandidateDTO):
    case_or_fulfillment_ref: Ref
    instrument_version_ref: Ref
    respondent_authority_ref: Ref
    feedback_evidence_ref: Ref


class FeedbackObservationReceipt(CandidateDTO):
    feedback_observation_ref: Ref
    feedback_kind: Literal["observed", "contested", "no_response"]
    recorded_at: Instant
    does_not_authorize_clinical_or_financial_change: Literal[True]

    @field_validator("does_not_authorize_clinical_or_financial_change", mode="before")
    @classmethod
    def exact_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("literal true required")
        return value


class CustomerOfferAcceptanceCommand(AcceptanceCommand):
    acceptance_domain: Literal["customer_offer"]


class ProviderAcceptanceCommand(CandidateDTO):
    acceptance_domain: Literal["provider_contract"]
    offer_ref: Ref
    offer_version: Ref
    decision: Literal["accept", "decline"]
    terms_evidence_ref: Ref
    party_authority_proof_ref: Ref
    contract_instrument_ref: Ref
    contract_version: Ref


class ProviderExternalWaitIntent(ExternalWaitIntent):
    counterparty_deadline_ref: Ref | None = None
    symmetry_evidence_ref: Ref | None = None


class ProviderExternalWaitOutcome(ExternalWaitOutcome):
    symmetry_status: Literal["symmetric", "asymmetric", "undeclared"]


class ProviderFeedbackIntent(CustomerFeedbackIntent):
    respondent_kind: Literal["customer", "provider"]


REQUEST_MODELS = MappingProxyType[str, type[CandidateDTO]](
    {
        "access.resolve": ContextAccessIntent,
        "offer.compose": OfferOptionsIntent,
        "acceptance.record": AcceptanceCommand,
        "enrollment.request": EnrollmentIntent,
        "reservation.command": ReservationCommand,
        "fulfillment.observe": FulfillmentObservation,
        "case.open_or_update": ResolutionCaseIntent,
        "external_wait.settle": ExternalWaitIntent,
        "notice.prepare_or_send": NoticeIntent,
        "milestone.publish": VerifiedMilestoneIntent,
        "feedback.record": CustomerFeedbackIntent,
    }
)
RESULT_MODELS = MappingProxyType[str, type[CandidateDTO]](
    {
        "access.resolve": ContextAccessResult,
        "offer.compose": VersionedOfferOptions,
        "acceptance.record": AcceptanceEvidenceReceipt,
        "enrollment.request": EnrollmentAuthorityReceipt,
        "reservation.command": ProviderReservationReceipt,
        "fulfillment.observe": VerifiedFulfillmentFact,
        "case.open_or_update": ResolutionCaseStatus,
        "external_wait.settle": ExternalWaitOutcome,
        "notice.prepare_or_send": DeliveryEvidenceReceipt,
        "milestone.publish": MilestonePublicationReceipt,
        "feedback.record": FeedbackObservationReceipt,
    }
)


PROVIDER_REQUEST_MODELS = MappingProxyType[str, type[CandidateDTO]](
    dict(REQUEST_MODELS)
    | {
        "contract.authority.resolve": ContractAuthorityIntent,
        "acceptance.record": ProviderAcceptanceCommand,
        "external_wait.settle": ProviderExternalWaitIntent,
        "feedback.record": ProviderFeedbackIntent,
    }
)
PROVIDER_RESULT_MODELS = MappingProxyType[str, type[CandidateDTO]](
    dict(RESULT_MODELS)
    | {
        "contract.authority.resolve": ContractAuthorityResult,
        "external_wait.settle": ProviderExternalWaitOutcome,
    }
)
REQUEST_MODEL_REGISTRIES = MappingProxyType(
    {
        CANDIDATE_SCHEMA_VERSION: REQUEST_MODELS,
        PROVIDER_SCHEMA_VERSION: PROVIDER_REQUEST_MODELS,
    }
)
RESULT_MODEL_REGISTRIES = MappingProxyType(
    {
        CANDIDATE_SCHEMA_VERSION: RESULT_MODELS,
        PROVIDER_SCHEMA_VERSION: PROVIDER_RESULT_MODELS,
    }
)
OPERATION_NAMES = frozenset(PROVIDER_REQUEST_MODELS)


def _schema_registry(
    registries: MappingProxyType[str, MappingProxyType[str, type[CandidateDTO]]], schema_version: str
) -> MappingProxyType[str, type[CandidateDTO]]:
    registry = registries.get(schema_version)
    if registry is None:
        raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return registry


def _payload_field(payload: object, field: str) -> object:
    if isinstance(payload, BaseModel):
        return getattr(payload, field, None)
    if type(payload) is bytes:
        try:
            payload = strict_loads(payload)
        except Exception:
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH) from None
    return payload.get(field) if isinstance(payload, dict) else None


def _parse(
    model: type[CandidateDTO], payload: object, memberships: DeclaredMemberships | None
) -> CandidateDTO:
    try:
        if isinstance(payload, BaseModel):
            if type(payload) is not model or not set(payload.__dict__) <= set(model.model_fields):
                raise ValueError("wrong contract")
            payload = payload.model_dump(mode="json", exclude_none=True, warnings="error")
        raw = payload if type(payload) is bytes else canonicalize(payload)
        strict_loads(raw)
        return model.model_validate_json(raw, context={"memberships": memberships})
    except Exception:
        pass
    raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)


def parse_envelope(payload: object) -> AnyCapabilityEnvelope:
    schema = _payload_field(payload, "schema_version")
    if schema == CANDIDATE_SCHEMA_VERSION:
        result = _parse(CapabilityEnvelope, payload, None)
        assert isinstance(result, CapabilityEnvelope)
        return result
    if schema == PROVIDER_SCHEMA_VERSION:
        result = _parse(ProviderCapabilityEnvelope, payload, None)
        assert isinstance(result, ProviderCapabilityEnvelope)
        return result
    raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)


def parse_request(
    operation_name: str,
    payload: object,
    *,
    memberships: DeclaredMemberships | None = None,
    schema_version: str = CANDIDATE_SCHEMA_VERSION,
) -> CandidateDTO:
    model = _schema_registry(REQUEST_MODEL_REGISTRIES, schema_version).get(operation_name)
    if model is None:
        raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
    if schema_version == PROVIDER_SCHEMA_VERSION and operation_name == "acceptance.record":
        domain = _payload_field(payload, "acceptance_domain")
        if domain == "customer_offer":
            model = CustomerOfferAcceptanceCommand
        elif domain != "provider_contract":
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return _parse(model, payload, memberships)


def parse_result(
    operation_name: str,
    payload: object,
    *,
    memberships: DeclaredMemberships | None = None,
    schema_version: str = CANDIDATE_SCHEMA_VERSION,
    request: CandidateDTO | None = None,
) -> CandidateDTO:
    model = _schema_registry(RESULT_MODEL_REGISTRIES, schema_version).get(operation_name)
    if model is None:
        raise CapabilityContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
    if schema_version == PROVIDER_SCHEMA_VERSION and operation_name == "external_wait.settle":
        if request is None:
            if _payload_field(payload, "symmetry_status") is None:
                model = ExternalWaitOutcome
        elif isinstance(request, ProviderExternalWaitIntent):
            if request.counterparty_deadline_ref is None and request.symmetry_evidence_ref is None:
                model = ExternalWaitOutcome
        else:
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return _parse(model, payload, memberships)


def request_digest(envelope: AnyCapabilityEnvelope, request: BaseModel) -> str:
    return hashlib.sha256(
        canonicalize(
            {
                "envelope": envelope.model_dump(mode="json", exclude_none=True, warnings="error"),
                "request": request.model_dump(mode="json", exclude_none=True, warnings="error"),
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilityOutcome:
    """Source result or bounded technical refusal; success is not business completion."""

    result: CandidateDTO | None = None
    refusal: CapabilityRefusalReason | None = None
    unavailable_observation: ContractResolutionUnavailable | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.refusal is None):
            raise ValueError("exactly one capability outcome required")
        if self.unavailable_observation is not None and (
            self.result is not None
            or self.refusal
            not in {CapabilityRefusalReason.SOURCE_UNAVAILABLE, CapabilityRefusalReason.AUTHORITY_UNPROVEN}
        ):
            raise ValueError("unknown observation requires unavailable authority or source")

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @classmethod
    def refused(cls, reason: CapabilityRefusalReason) -> Self:
        return cls(refusal=reason)
