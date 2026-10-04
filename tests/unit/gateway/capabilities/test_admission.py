"""UNIT synthetic authority/audit/source doubles; no provider or runtime qualification.

These tests exercise the inert guard, real canonical PEP and candidate dispatcher.
The doubles intentionally do not represent published contracts or human signatures.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from maezo.gateway.capabilities.admission import (
    AdmissionBinding,
    AdmissionDeniedError,
    AdmissionLease,
    CapabilityAdmission,
    VerifiedAuthority,
    VerifiedCurrentness,
    VerifiedSourceResult,
    result_digest,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    ProviderReservationReceipt,
    ReservationCommand,
    VerifiedFulfillmentFact,
    request_digest,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pep import HARD_ACTIONS, build_pep

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
EXPIRY = NOW + timedelta(minutes=5)  # Synthetic UNIT source expiry, no product TTL.


def binding(**updates):
    return AdmissionBinding(
        **{
            "principal_ref": "lucas",
            "task_ref": "unit-task",
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "purpose_ref": "unit-source-declared-purpose",
            "operation_name": "reservation.command",
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "contract_revision": "unit-contract-revision",
            "source_authority_ref": "unit-authority",
            "policy_revision": "unit-policy-revision",
            "data_classification": "unit-opaque-refs",
            "autonomy_action": "scheduling",
            "security_zone": "general",
            **updates,
        }
    )


def envelope(**updates):
    return CapabilityEnvelope(
        **{
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "operation_name": "reservation.command",
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "journey_ref": "unit-journey",
            "correlation_ref": "unit-correlation",
            "causation_ref": "unit-cause",
            "idempotency_key": "unit-command-key",
            "expected_business_revision": "unit-revision-1",
            "source_authority_ref": "unit-authority",
            "policy_revision": "unit-policy-revision",
            "data_classification": "unit-opaque-refs",
            **updates,
        }
    )


REQUEST = ReservationCommand(
    provider_ref="unit-provider",
    operation="hold",
    offer_or_booking_ref="unit-offer",
    expected_provider_revision="unit-provider-revision",
    customer_commitment_ref="unit-commitment",
)
RESULT = ProviderReservationReceipt(
    reservation_status="held",
    booking_ref="unit-booking",
    hold_expires_at="2026-10-04T12:05:00Z",
    provider_receipt_ref="unit-reservation-receipt",
    provider_revision="unit-provider-revision-2",
)


class UnitAuthority:
    def __init__(self):
        self.events = []
        self.authority_updates = {}
        self.current_updates = {}
        self.result_updates = {}
        self.revoked = False
        self.force_return = None

    async def authorize(self, scope, env, request):
        self.events.append("authority")
        if self.force_return is not None:
            return self.force_return
        return VerifiedAuthority.model_construct(
            **{
                "binding": scope,
                "request_sha256": request_digest(env, request),
                "authorization_ref": "unit-authorized",
                "signed_task_ref": "unit-verified-task-signature",
                "agent_card_sha256": "a" * 64,
                "source_contract_publication_ref": "unit-source-publication",
                "policy_ratification_ref": "unit-policy-ratification",
                "currentness_ref": "unit-currentness",
                "enforcement": "enforcing",
                "decision": "allow",
                "verified_at": NOW,
                "valid_until": EXPIRY,
                **self.authority_updates,
            }
        )

    async def check_current(self, authority, *, source_result=None):
        self.events.append("current")
        await asyncio.sleep(0)
        if self.revoked:
            raise AdmissionDeniedError(CapabilityRefusalReason.STALE_REVISION)
        values = {
            "binding": authority.binding,
            "request_sha256": authority.request_sha256,
            "authorization_ref": authority.authorization_ref,
            "currentness_ref": authority.currentness_ref,
            "source_result_sha256": source_result.result_sha256 if source_result else None,
            "checked_at": NOW,
            "valid_until": EXPIRY,
            **self.current_updates,
        }
        return VerifiedCurrentness.model_construct(**values)

    async def verify_source_result(self, authority, result):
        self.events.append("receipt")
        return VerifiedSourceResult.model_construct(
            **{
                "binding": authority.binding,
                "request_sha256": authority.request_sha256,
                "result_sha256": result_digest(result),
                "authorization_ref": authority.authorization_ref,
                "source_receipt_ref": "unit-domain-receipt",
                "source_revision_ref": "unit-revision-2",
                "currentness_ref": authority.currentness_ref,
                "checked_at": NOW,
                "valid_until": EXPIRY,
                **self.result_updates,
            }
        )


class UnitAudit:
    def __init__(self, authority):
        self.authority = authority
        self.receipt = "b" * 64
        self.intents = []
        self.revoke = False

    async def emit_intent(self, intent):
        self.authority.events.append("audit")
        self.intents.append(intent)
        if self.revoke:
            self.authority.revoked = True
        return self.receipt


class UnitSource:
    def __init__(self, authority):
        self.authority = authority
        self.calls = 0
        self.revoke = False

    async def execute(self, env, request, *, timeout_seconds):
        self.authority.events.append("source")
        self.calls += 1
        if self.revoke:
            self.authority.revoked = True
        return RESULT.model_dump(mode="json")


def guard(scope=None, authority=None, audit=None):
    scope = scope or binding()
    return CapabilityAdmission(
        binding=scope,
        authority=authority,
        audit=audit,
        autonomy=build_pep(tenant=scope.tenant_ref),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_exact_lifecycle_audits_before_source_and_checks_before_disclosure():
    authority = UnitAuthority()
    audit = UnitAudit(authority)
    source = UnitSource(authority)
    service = CapabilityService(
        admission=guard(authority=authority, audit=audit),
        sources={"reservation.command": source},
    )
    outcome = await service.execute(envelope(), REQUEST)
    assert outcome.refusal is None
    assert outcome.result == RESULT
    assert authority.events == ["authority", "audit", "current", "source", "receipt", "current"]
    assert "customer_commitment_ref" not in audit.intents[0].model_dump_json()


@pytest.mark.asyncio
async def test_no_authority_or_audit_cannot_issue_lease():
    with pytest.raises(AdmissionDeniedError) as refusal:
        await guard().authorize(envelope(), REQUEST)
    assert refusal.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    authority = UnitAuthority()
    with pytest.raises(AdmissionDeniedError) as refusal:
        await guard(authority=authority).authorize(envelope(), REQUEST)
    assert refusal.value.reason == CapabilityRefusalReason.AUDIT_UNAVAILABLE
    assert authority.events == []


@pytest.mark.parametrize("action", sorted(HARD_ACTIONS))
def test_hard_binding_is_rejected_even_before_source(action):
    with pytest.raises(ValidationError):
        binding(autonomy_action=action)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["unknown_action", "high_value_payment", "erase_patient_memory"])
async def test_unknown_and_human_actions_cannot_use_agent_admission(action):
    authority = UnitAuthority()
    with pytest.raises(AdmissionDeniedError):
        await guard(binding(autonomy_action=action), authority, UnitAudit(authority)).authorize(
            envelope(), REQUEST
        )
    assert authority.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["tenant_ref", "legal_entity_ref", "source_authority_ref", "policy_revision", "data_classification"],
)
async def test_mismatched_envelope_scope_performs_no_authority_io(field):
    authority = UnitAuthority()
    with pytest.raises(AdmissionDeniedError):
        await guard(authority=authority, audit=UnitAudit(authority)).authorize(
            envelope(**{field: "unit-other"}), REQUEST
        )
    assert authority.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["principal_ref", "task_ref", "purpose_ref", "contract_revision"])
async def test_verified_scope_must_match_every_server_binding_field(field):
    authority = UnitAuthority()
    authority.authority_updates["binding"] = binding(**{field: "unit-other"})
    audit = UnitAudit(authority)
    with pytest.raises(AdmissionDeniedError):
        await guard(authority=authority, audit=audit).authorize(envelope(), REQUEST)
    assert audit.intents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unverified", [True, False, {"decision": "allow"}])
async def test_booleans_and_caller_claim_dicts_are_not_verified_authority(unverified):
    authority = UnitAuthority()
    authority.force_return = unverified
    audit = UnitAudit(authority)
    with pytest.raises(AdmissionDeniedError):
        await guard(authority=authority, audit=audit).authorize(envelope(), REQUEST)
    assert audit.intents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, True, "ack", "c" * 63])
async def test_audit_without_durable_receipt_cannot_issue_lease(receipt):
    authority = UnitAuthority()
    audit = UnitAudit(authority)
    audit.receipt = receipt
    with pytest.raises(AdmissionDeniedError) as refusal:
        await guard(authority=authority, audit=audit).authorize(envelope(), REQUEST)
    assert refusal.value.reason == CapabilityRefusalReason.AUDIT_UNAVAILABLE


@pytest.mark.asyncio
async def test_revocation_after_audit_blocks_source_dispatch():
    authority = UnitAuthority()
    audit = UnitAudit(authority)
    audit.revoke = True
    source = UnitSource(authority)
    outcome = await CapabilityService(
        admission=guard(authority=authority, audit=audit), sources={"reservation.command": source}
    ).execute(envelope(), REQUEST)
    assert outcome.refusal == CapabilityRefusalReason.STALE_REVISION
    assert source.calls == 0


@pytest.mark.asyncio
async def test_late_result_after_revocation_never_discloses_success():
    authority = UnitAuthority()
    source = UnitSource(authority)
    source.revoke = True
    outcome = await CapabilityService(
        admission=guard(authority=authority, audit=UnitAudit(authority)),
        sources={"reservation.command": source},
    ).execute(envelope(), REQUEST)
    assert outcome.refusal == CapabilityRefusalReason.STALE_REVISION
    assert outcome.result is None
    assert source.calls == 1  # Possible effect remains owned by source reconciliation.


@pytest.mark.asyncio
async def test_concurrent_dispatch_revalidation_is_single_use():
    authority = UnitAuthority()
    admission = guard(authority=authority, audit=UnitAudit(authority))
    lease = await admission.authorize(envelope(), REQUEST)
    outcomes = await asyncio.gather(
        admission.revalidate(lease, phase="before_source"),
        admission.revalidate(lease, phase="before_source"),
        return_exceptions=True,
    )
    assert sum(outcome is None for outcome in outcomes) == 1
    assert sum(isinstance(outcome, AdmissionDeniedError) for outcome in outcomes) == 1


@pytest.mark.asyncio
async def test_value_copy_of_lease_cannot_authorize_and_release_is_safe():
    authority = UnitAuthority()
    admission = guard(authority=authority, audit=UnitAudit(authority))
    lease = await admission.authorize(envelope(), REQUEST)
    forged = AdmissionLease(lease.token)
    admission.release(forged)
    with pytest.raises(AdmissionDeniedError):
        await admission.revalidate(forged, phase="before_source")
    await admission.revalidate(lease, phase="before_source")
    admission.release(lease)
    admission.release(lease)
    with pytest.raises(AdmissionDeniedError):
        await admission.verify_result(lease, RESULT)


@pytest.mark.asyncio
async def test_result_receipt_cannot_attest_other_bytes():
    authority = UnitAuthority()
    authority.result_updates["result_sha256"] = "f" * 64
    admission = guard(authority=authority, audit=UnitAudit(authority))
    lease = await admission.authorize(envelope(), REQUEST)
    await admission.revalidate(lease, phase="before_source")
    with pytest.raises(AdmissionDeniedError):
        await admission.verify_result(lease, RESULT)
    with pytest.raises(AdmissionDeniedError):
        await admission.revalidate(lease, phase="before_disclosure")


@pytest.mark.asyncio
async def test_clinical_result_reference_cannot_enter_general_disclosure():
    authority = UnitAuthority()
    scope = binding(operation_name="fulfillment.observe")
    admission = guard(scope, authority, UnitAudit(authority))
    env = envelope(operation_name="fulfillment.observe")
    from maezo.gateway.capabilities.models import FulfillmentObservation

    request = FulfillmentObservation(
        fulfillment_ref="unit-fulfillment",
        source_event_or_observation_ref="unit-event",
        source_authority_contract_ref="unit-contract",
        expected_revision="unit-revision-1",
    )
    lease = await admission.authorize(env, request)
    await admission.revalidate(lease, phase="before_source")
    result = VerifiedFulfillmentFact(
        fact_kind="result_available",
        source_fact_ref="unit-fact",
        source_revision="unit-revision-2",
        clinical_result_context_ref="unit-clinical-context",
    )
    with pytest.raises(AdmissionDeniedError) as refusal:
        await admission.verify_result(lease, result)
    assert refusal.value.reason == CapabilityRefusalReason.PURPOSE_DENIED
    assert "receipt" not in authority.events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"enforcement": "shadow"},
        {"decision": "deny"},
        {"source_contract_publication_ref": ""},
        {"policy_ratification_ref": ""},
        {"signed_task_ref": ""},
        {"agent_card_sha256": "missing"},
        {"valid_until": NOW},
        {"verified_at": NOW + timedelta(seconds=1)},
        {"verified_at": NOW.replace(tzinfo=None)},
        {"request_sha256": "f" * 64},
    ],
)
async def test_unproven_constructed_evidence_is_revalidated_without_audit(updates):
    authority = UnitAuthority()
    authority.authority_updates = updates
    audit = UnitAudit(authority)
    with pytest.raises(AdmissionDeniedError):
        await guard(authority=authority, audit=audit).authorize(envelope(), REQUEST)
    assert audit.intents == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"currentness_ref": "unit-other"},
        {"request_sha256": "f" * 64},
        {"authorization_ref": "unit-other"},
        {"checked_at": NOW - timedelta(seconds=1)},
        {"valid_until": NOW},
    ],
)
async def test_currentness_must_match_the_authorized_request_and_revision(updates):
    authority = UnitAuthority()
    authority.current_updates = updates
    audit = UnitAudit(authority)
    admission = guard(authority=authority, audit=audit)
    lease = await admission.authorize(envelope(), REQUEST)
    with pytest.raises(AdmissionDeniedError):
        await admission.revalidate(lease, phase="before_source")


@pytest.mark.asyncio
async def test_result_source_expiry_is_rechecked_after_final_currentness_await():
    authority = UnitAuthority()
    authority.result_updates["valid_until"] = NOW + timedelta(seconds=1)
    ticks = [NOW]
    admission = CapabilityAdmission(
        binding=binding(),
        authority=authority,
        audit=UnitAudit(authority),
        autonomy=build_pep(tenant="unit-tenant"),
        clock=lambda: ticks[0],
    )
    lease = await admission.authorize(envelope(), REQUEST)
    await admission.revalidate(lease, phase="before_source")
    await admission.verify_result(lease, RESULT)
    ticks[0] = NOW + timedelta(seconds=1)
    with pytest.raises(AdmissionDeniedError) as refusal:
        await admission.revalidate(lease, phase="before_disclosure")
    assert refusal.value.reason == CapabilityRefusalReason.STALE_REVISION


@pytest.mark.asyncio
async def test_mutating_a_frozen_request_after_audit_is_denied():
    authority = UnitAuthority()
    admission = guard(authority=authority, audit=UnitAudit(authority))
    request = REQUEST.model_copy()
    lease = await admission.authorize(envelope(), request)
    object.__setattr__(request, "provider_ref", "unit-other")
    with pytest.raises(AdmissionDeniedError) as refusal:
        await admission.revalidate(lease, phase="before_source")
    assert refusal.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH


@pytest.mark.asyncio
async def test_final_currentness_must_bind_the_exact_source_result():
    authority = UnitAuthority()
    admission = guard(authority=authority, audit=UnitAudit(authority))
    lease = await admission.authorize(envelope(), REQUEST)
    await admission.revalidate(lease, phase="before_source")
    await admission.verify_result(lease, RESULT)
    authority.current_updates["source_result_sha256"] = "f" * 64
    with pytest.raises(AdmissionDeniedError):
        await admission.revalidate(lease, phase="before_disclosure")
