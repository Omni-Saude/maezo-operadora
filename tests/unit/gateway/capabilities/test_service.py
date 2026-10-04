"""Dispatcher UNIT doubles. No provider, policy or real receipt qualification."""

import asyncio

import pytest

from maezo.gateway.capabilities.admission import AdmissionBinding, AdmissionDeniedError
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityRefusalReason,
)
from maezo.gateway.capabilities.service import CapabilityService


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        schema_version=CANDIDATE_SCHEMA_VERSION,
        operation_name="acceptance.record",
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-entity",
        journey_ref="unit-journey",
        correlation_ref="unit-correlation",
        causation_ref="unit-causation",
        idempotency_key="unit-key",
        expected_business_revision="unit-revision",
        source_authority_ref="unit-source",
        policy_revision="unit-policy",
        data_classification="unit-general",
    )


def payload() -> dict[str, str]:
    return {
        "offer_ref": "unit-offer",
        "offer_version": "unit-offer-version",
        "decision": "accept",
        "terms_evidence_ref": "unit-terms",
        "customer_authority_proof_ref": "unit-customer",
    }


def unit_result() -> dict[str, str]:
    return {
        "acceptance_ref": "unit-acceptance",
        "recorded_decision": "declined",
        "recorded_at": "2026-10-04T12:00:00Z",
        "receipt_ref": "UNIT-DOUBLE-ONLY",
        "business_revision": "unit-next-revision",
    }


class UnitAdmission:
    """Tests routing/lifetime only; the real admission has its own independent suite."""

    def __init__(self, events: list[str], *, denial_phase: str | None = None) -> None:
        self.events, self.denial_phase = events, denial_phase
        self.lease = object()
        self.binding = AdmissionBinding(
            principal_ref="lucas",
            task_ref="journey.compras.step",
            tenant_ref="unit-tenant",
            legal_entity_ref="unit-entity",
            purpose_ref="UNIT-DOUBLE-ONLY",
            operation_name="acceptance.record",
            schema_version=CANDIDATE_SCHEMA_VERSION,
            contract_revision="PROPOSED",
            source_authority_ref="unit-source",
            policy_revision="unit-policy",
            data_classification="unit-general",
            autonomy_action="unit-action",
            security_zone="general",
        )

    async def authorize(self, envelope: CapabilityEnvelope, request: object) -> object:
        self.events.append("audit_authorize")
        if self.denial_phase == "audit":
            raise AdmissionDeniedError(CapabilityRefusalReason.AUDIT_UNAVAILABLE)
        return self.lease

    async def revalidate(self, lease: object, *, phase: str) -> None:
        assert lease is self.lease
        self.events.append(phase)
        if self.denial_phase == phase:
            raise AdmissionDeniedError(CapabilityRefusalReason.STALE_REVISION)

    async def verify_result(self, lease: object, result: object) -> None:
        assert lease is self.lease
        self.events.append("verify_result")
        if self.denial_phase == "verify_result":
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def release(self, lease: object) -> None:
        assert lease is self.lease
        self.events.append("release")


class UnitSource:
    def __init__(self, events: list[str], *, result: object = None, hang: bool = False) -> None:
        self.events, self.result, self.hang = events, result, hang
        self.calls = 0
        self.started = asyncio.Event()

    async def execute(
        self, envelope: CapabilityEnvelope, request: object, *, timeout_seconds: float
    ) -> object:
        self.events.append("source")
        self.calls += 1
        self.started.set()
        if self.hang:
            await asyncio.Event().wait()
        return self.result


@pytest.mark.asyncio
async def test_dispatch_reaches_exact_source_after_audit_and_currentness() -> None:
    events: list[str] = []
    source, admission = UnitSource(events, result=unit_result()), UnitAdmission(events)
    service = CapabilityService(sources={"acceptance.record": source}, admission=admission)
    assert service.is_bound_to_task("journey.compras.step")
    assert not service.is_bound_to_task("journey.suporte.step")
    outcome = await service.execute(envelope(), payload())
    assert outcome.succeeded
    # Input intent is accept, but the source's exact declined fact is preserved.
    assert outcome.result.recorded_decision == "declined"
    assert events == [
        "audit_authorize",
        "before_source",
        "source",
        "verify_result",
        "before_disclosure",
        "release",
    ]
    assert source.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["audit", "before_source", "verify_result", "before_disclosure"])
async def test_denial_never_becomes_success_and_releases_only_local_lease(phase: str) -> None:
    events: list[str] = []
    source, admission = UnitSource(events, result=unit_result()), UnitAdmission(events, denial_phase=phase)
    service = CapabilityService(sources={"acceptance.record": source}, admission=admission)
    outcome = await service.execute(envelope(), payload())
    assert not outcome.succeeded and outcome.result is None
    assert source.calls == (0 if phase in {"audit", "before_source"} else 1)
    assert events.count("release") == (0 if phase == "audit" else 1)


@pytest.mark.asyncio
async def test_missing_provider_or_authority_and_invalid_input_prevent_io() -> None:
    events: list[str] = []
    source, admission = UnitSource(events, result=unit_result()), UnitAdmission(events)
    no_authority = CapabilityService(sources={"acceptance.record": source})
    outcome = await no_authority.execute(envelope(), payload())
    assert outcome.refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    no_source = CapabilityService(admission=admission)
    outcome = await no_source.execute(envelope(), payload())
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    configured = CapabilityService(sources={"acceptance.record": source}, admission=admission)
    outcome = await configured.execute(envelope(), payload() | {"receipt_ref": "planted"})
    assert outcome.refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    outcome = await configured.execute(envelope().model_copy(update={"tenant_ref": False}), payload())
    assert outcome.refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert events == [] and source.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {}, {"ok": True}, unit_result() | {"doctor_verdict": "planted"}])
async def test_empty_transport_ack_and_output_extra_fields_are_not_receipts(result: object) -> None:
    events: list[str] = []
    source = UnitSource(events, result=result)
    service = CapabilityService(sources={"acceptance.record": source}, admission=UnitAdmission(events))
    outcome = await service.execute(envelope(), payload())
    assert outcome.refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert "verify_result" not in events and "before_disclosure" not in events
    assert events[-1] == "release"


@pytest.mark.asyncio
async def test_technical_timeout_preserves_uncertainty_without_retry() -> None:
    events: list[str] = []
    source = UnitSource(events, hang=True)
    service = CapabilityService(
        sources={"acceptance.record": source}, admission=UnitAdmission(events), timeout_seconds=0.01
    )
    outcome = await service.execute(envelope(), payload())
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert source.calls == 1
    assert "verify_result" not in events and events[-1] == "release"


@pytest.mark.asyncio
async def test_cancellation_propagates_and_does_not_release_source_claim() -> None:
    events: list[str] = []
    source = UnitSource(events, hang=True)
    service = CapabilityService(sources={"acceptance.record": source}, admission=UnitAdmission(events))
    task = asyncio.create_task(service.execute(envelope(), payload()))
    await source.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.calls == 1 and events[-1] == "release"


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_deadline_and_operation_mapping_cannot_be_widened(timeout: float) -> None:
    with pytest.raises(ValueError):
        CapabilityService(timeout_seconds=timeout)
    with pytest.raises(ValueError):
        CapabilityService(sources={"unpublished.operation": UnitSource([])})
    with pytest.raises(ValueError):
        CapabilityService(admissions={"access.resolve": UnitAdmission([])})
