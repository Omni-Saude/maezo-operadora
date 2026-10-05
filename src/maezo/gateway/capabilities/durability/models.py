"""Seventeen closed internal records from durability-contract.json (DUR0).

Parsing is never data admission, evidence verification or source authority.
Local integer revisions/fences do not order opaque source revisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from maezo.gateway.capabilities.admission import (
    AdmissionDTO,
    Digest,
    VerifiedAuthority,
    VerifiedCurrentness,
    VerifiedSourceResult,
)
from maezo.gateway.capabilities.models import (
    CapabilityEnvelope,
    CapabilityRefusalReason,
    ExternalWaitIntent,
    Ref,
)

LocalRevision = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
FenceVersion = Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("UTC instant requires timezone")
    return value.astimezone(UTC)


AwareUTCInstant = Annotated[datetime, AfterValidator(_utc)]


class CommandTechnicalState(StrEnum):
    RECORDED = "RECORDED"
    DISPATCH_FENCED = "DISPATCH_FENCED"
    UNCERTAIN = "UNCERTAIN"
    RESPONSE_RECORDED = "RESPONSE_RECORDED"
    REFUSED_BEFORE_DISPATCH = "REFUSED_BEFORE_DISPATCH"


class WaitTechnicalState(StrEnum):
    OPEN = "OPEN"
    ELAPSED = "ELAPSED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"


class OutboxTechnicalState(StrEnum):
    RECORDED = "RECORDED"
    CLAIMED = "CLAIMED"
    DELIVERY_FENCED = "DELIVERY_FENCED"
    UNCERTAIN = "UNCERTAIN"
    ACK_RECORDED = "ACK_RECORDED"


class OutboxTechnicalKind(StrEnum):
    RECONCILIATION_REQUEST = "RECONCILIATION_REQUEST"
    RETURN_INTENT = "RETURN_INTENT"


class JournalCallTechnicalStatus(StrEnum):
    RECORDED = "recorded"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    UNCERTAIN = "uncertain"


class JournalRefusalReason(StrEnum):
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    BINDING_DENIED = "BINDING_DENIED"
    DATA_GATE_CLOSED = "DATA_GATE_CLOSED"
    CAS_CONFLICT = "CAS_CONFLICT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PROOF_UNAVAILABLE = "PROOF_UNAVAILABLE"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    COMMIT_UNCERTAIN = "COMMIT_UNCERTAIN"


class JournalBinding(AdmissionDTO):
    environment_ref: Ref
    tenant_ref: Ref
    legal_entity_ref: Ref
    journey_ref: Ref
    principal_ref: Ref
    task_ref: Ref
    data_policy_ref: Ref


class CommandDescriptor(AdmissionDTO):
    envelope: CapabilityEnvelope
    request_ref: Ref
    request_sha256: Digest
    admission_binding_sha256: Digest
    predecessor_command_refs: tuple[Ref, ...]


class CommandHandle(AdmissionDTO):
    binding: JournalBinding
    command_ref: Ref
    request_sha256: Digest


class CommandSnapshot(AdmissionDTO):
    handle: CommandHandle
    journal_revision: LocalRevision
    technical_state: CommandTechnicalState
    dispatch_ref: Ref | None = None
    fence_version: FenceVersion | None = None
    verified_result_observation_ref: Ref | None = None
    result_ref: Ref | None = None
    result_sha256: Digest | None = None
    source_receipt_ref: Ref | None = None
    source_revision_ref: Ref | None = None
    recorded_at: AwareUTCInstant
    last_observed_at: AwareUTCInstant

    @model_validator(mode="after")
    def state_consistent(self) -> Self:
        fenced = self.technical_state in {
            CommandTechnicalState.DISPATCH_FENCED,
            CommandTechnicalState.UNCERTAIN,
            CommandTechnicalState.RESPONSE_RECORDED,
        }
        if fenced != (self.dispatch_ref is not None and self.fence_version is not None):
            raise ValueError("inconsistent command fence")
        if not fenced and (self.dispatch_ref is not None or self.fence_version is not None):
            raise ValueError("unexpected command fence")
        result_fields = (
            self.verified_result_observation_ref,
            self.result_ref,
            self.result_sha256,
            self.source_receipt_ref,
            self.source_revision_ref,
        )
        if self.technical_state == CommandTechnicalState.RESPONSE_RECORDED:
            if any(v is None for v in result_fields):
                raise ValueError("result evidence missing")
        elif any(v is not None for v in result_fields):
            raise ValueError("unexpected result evidence")
        return self


class JourneySnapshot(AdmissionDTO):
    binding: JournalBinding
    journal_revision: LocalRevision
    command_refs: tuple[Ref, ...]
    wait_refs: tuple[Ref, ...]
    outbox_refs: tuple[Ref, ...]


class DispatchEvidence(AdmissionDTO):
    authority: VerifiedAuthority
    currentness: VerifiedCurrentness
    audit_receipt_sha256: Digest
    audit_intent_ref: Ref


class DispatchToken(AdmissionDTO):
    handle: CommandHandle
    dispatch_ref: Ref
    fence_version: FenceVersion
    journal_revision: LocalRevision


class VerifiedResultObservation(AdmissionDTO):
    source_result: VerifiedSourceResult
    result_ref: Ref
    result_sha256: Digest
    observer_binding_ref: Ref
    provenance_ref: Ref
    source_revision_order_witness_ref: Ref | None = None


class VerifiedInboxObservation(AdmissionDTO):
    event_ref: Ref
    source_authority_ref: Ref
    producer_ref: Ref
    source_contract_revision_ref: Ref
    event_sha256: Digest
    handle: CommandHandle
    correlation_ref: Ref
    observation: VerifiedResultObservation


class WaitDescriptor(AdmissionDTO):
    intent: ExternalWaitIntent
    handle: CommandHandle
    operational_wakeup_at: AwareUTCInstant | None = None


class WaitSnapshot(AdmissionDTO):
    descriptor: WaitDescriptor
    journal_revision: LocalRevision
    technical_state: WaitTechnicalState
    source_observation_ref: Ref | None = None
    elapsed_observed_at: AwareUTCInstant | None = None


class OutboxDescriptor(AdmissionDTO):
    outbox_ref: Ref
    command_ref: Ref
    kind: OutboxTechnicalKind
    target_binding_ref: Ref
    payload_ref: Ref
    payload_sha256: Digest
    causation_ref: Ref
    dedupe_identity_ref: Ref


class OutboxSnapshot(AdmissionDTO):
    descriptor: OutboxDescriptor
    journal_revision: LocalRevision
    technical_state: OutboxTechnicalState
    claim_ref: Ref | None = None
    worker_ref: Ref | None = None
    lease_until: AwareUTCInstant | None = None
    fence_version: FenceVersion | None = None
    delivery_ref: Ref | None = None
    acknowledgement_ref: Ref | None = None
    acknowledgement_sha256: Digest | None = None


class OutboxAckEvidence(AdmissionDTO):
    outbox_ref: Ref
    delivery_ref: Ref
    target_binding_ref: Ref
    payload_sha256: Digest
    acknowledgement_ref: Ref
    acknowledgement_sha256: Digest
    verifier_binding_ref: Ref
    observed_at: AwareUTCInstant


class PreDispatchRefusal(AdmissionDTO):
    reason: CapabilityRefusalReason | JournalRefusalReason
    evidence_ref: Ref
    observed_at: AwareUTCInstant


class JournalCallResult[SnapshotT: AdmissionDTO](AdmissionDTO):
    technical_status: JournalCallTechnicalStatus
    snapshot: SnapshotT | None = None
    refusal_reason: JournalRefusalReason | None = None

    @model_validator(mode="after")
    def outcome_consistent(self) -> Self:
        accepted = self.technical_status in {
            JournalCallTechnicalStatus.RECORDED,
            JournalCallTechnicalStatus.UNCHANGED,
        }
        if accepted:
            if self.snapshot is None or self.refusal_reason is not None:
                raise ValueError("acknowledged journal snapshot required")
        elif self.snapshot is not None or self.refusal_reason is None:
            raise ValueError("unacknowledged commit cannot expose snapshot")
        return self


class RecoverySnapshot(AdmissionDTO):
    binding: JournalBinding
    journal_revision: LocalRevision
    command_refs_for_reconciliation: tuple[Ref, ...]
    outbox_refs_for_reconciliation: tuple[Ref, ...]
    wait_refs_for_observation: tuple[Ref, ...]
    next_cursor_ref: Ref | None = None
