"""Publication-state UNIT tests; no store, route, DMN, provider or authority.

Pins the exact published contract bytes (schema/version digest) required by the
VW1-P1 exit gate (IMPLEMENTATION-WAVES-VENDOR-V1 section 2.3): the compras runner
consumes the published contract, the vendor projection shape (OP15, registry
section 5.1) refuses typed ``SOURCE_UNAVAILABLE`` when it is not published, and
withdrawal restores that refusal without deleting a retained record. Doubles here
are never providers, authority verifiers or receipt publishers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

import maezo.gateway.capabilities.publication as publication_module
from maezo.agents.lucas.administrative.graph import compras_consumer
from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA
from maezo.gateway.capabilities.admission import AdmissionBinding
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CONTRACT_STATE,
    CONTRACT_STATE_PUBLICATION_WITHDRAWN,
    CONTRACT_STATE_PUBLISHED,
    PROVIDER_SCHEMA_VERSION,
    REQUEST_MODELS,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityRefusalReason,
)
from maezo.gateway.capabilities.publication import (
    ContractPublicationLedger,
    _contract_digest,
)
from maezo.gateway.capabilities.service import CapabilityService

PUBLISHED_AT = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
# Byte-exact commitment of this package: a mapped-field mutation under the same
# schema version must fail these pins and force a conscious re-publication.
CANDIDATE_CONTRACT_SHA256 = "18dc5e4145b221b87da4781b11d1f7a774377dbe102e074dd9fa72d6bdec3fa7"
PROVIDER_CONTRACT_SHA256 = "0faeb6f104959d11a2aea679f5ba64dd2cc3e0b4f4b31dd50faa0d6fd063eac2"
PUBLICATION_RECEIPT = "unit-publication-receipt-0001"
WITHDRAWAL_RECEIPT = "unit-withdrawal-receipt-0001"


def clock() -> datetime:
    return PUBLISHED_AT


def ledger() -> ContractPublicationLedger:
    return ContractPublicationLedger(clock=clock)


def envelope(operation: str = "offer.compose") -> CapabilityEnvelope:
    return CapabilityEnvelope(
        schema_version=CANDIDATE_SCHEMA_VERSION,
        operation_name=operation,
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


def provider_envelope(operation: str = "offer.compose") -> Any:
    from maezo.gateway.capabilities.models import ProviderCapabilityEnvelope

    return ProviderCapabilityEnvelope(
        schema_version=PROVIDER_SCHEMA_VERSION,
        operation_name=operation,
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


def offer_payload() -> dict[str, object]:
    return {
        "catalogue_ref": "unit-catalogue",
        "catalogue_version": "unit-catalogue-version",
        "administrative_preferences_ref": "unit-preferences",
        "availability_refs": ["unit-availability-a"],
        "purpose_policy_ref": "unit-purpose",
    }


def acceptance_payload() -> dict[str, object]:
    return {
        "offer_ref": "unit-offer",
        "offer_version": "unit-offer-version",
        "decision": "accept",
        "terms_evidence_ref": "unit-terms",
        "customer_authority_proof_ref": "unit-customer-authority",
    }


OFFER_RESULT: dict[str, object] = {
    "option_refs": ["unit-option-a"],
    "explanation_refs": ["unit-explanation-a"],
    "status": "usable",
}
ACCEPTANCE_RESULT: dict[str, object] = {
    "acceptance_ref": "unit-acceptance",
    "recorded_decision": "accepted",
    "recorded_at": "2026-10-06T12:00:00Z",
    "receipt_ref": "UNIT-DOUBLE-ONLY",
    "business_revision": "unit-next-revision",
}


class RecordingSource:
    """UNIT source double; counts dispatches and never fabricates receipts."""

    def __init__(self, results: dict[str, object], events: list[str]) -> None:
        self.results, self.events = results, events
        self.calls: list[str] = []

    async def execute(
        self, envelope: CapabilityEnvelope, request: object, *, timeout_seconds: float
    ) -> object:
        self.calls.append(envelope.operation_name)
        self.events.append("source")
        return self.results[envelope.operation_name]


def binding(operation: str, *, task_ref: str = "journey.compras.step") -> AdmissionBinding:
    return AdmissionBinding(
        principal_ref="lucas",
        task_ref=task_ref,
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-entity",
        purpose_ref="UNIT-DOUBLE-ONLY",
        operation_name=operation,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        contract_revision="PROPOSED",
        source_authority_ref="unit-source",
        policy_revision="unit-policy",
        data_classification="unit-general",
        autonomy_action="unit-action",
        security_zone="general",
    )


class RecordingAdmission:
    """UNIT admission double: routing and lifetime only, never authority."""

    def __init__(self, operation: str, events: list[str], *, task_ref: str = "journey.compras.step") -> None:
        self.events, self.lease = events, object()
        self.binding = binding(operation, task_ref=task_ref)

    async def authorize(self, envelope: CapabilityEnvelope, request: object) -> object:
        self.events.append("authorize")
        return self.lease

    async def revalidate(self, lease: object, *, phase: str) -> None:
        assert lease is self.lease
        self.events.append(phase)

    async def verify_result(self, lease: object, result: object) -> None:
        assert lease is self.lease
        self.events.append("verify_result")

    def release(self, lease: object) -> None:
        assert lease is self.lease
        self.events.append("release")


def purchase_service(
    record_ledger: ContractPublicationLedger | None, events: list[str]
) -> tuple[CapabilityService, RecordingSource]:
    source = RecordingSource({"offer.compose": OFFER_RESULT, "acceptance.record": ACCEPTANCE_RESULT}, events)
    service = CapabilityService(
        sources={"offer.compose": source, "acceptance.record": source},
        admissions={
            "offer.compose": RecordingAdmission("offer.compose", events),
            "acceptance.record": RecordingAdmission("acceptance.record", events),
        },
        publication=record_ledger,
    )
    return service, source


# --- Publication state: the reversible transition over CONTRACT_STATE ------------


def test_proposed_state_constants_and_default_ledger_state() -> None:
    assert CONTRACT_STATE == "PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED"
    assert CONTRACT_STATE_PUBLISHED == "INTERNAL_CONTRACT_PUBLISHED"
    assert CONTRACT_STATE_PUBLICATION_WITHDRAWN == "INTERNAL_CONTRACT_PUBLICATION_WITHDRAWN"
    assert len({CONTRACT_STATE, CONTRACT_STATE_PUBLISHED, CONTRACT_STATE_PUBLICATION_WITHDRAWN}) == 3
    record_ledger = ledger()
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE
    assert record_ledger.state(PROVIDER_SCHEMA_VERSION) == CONTRACT_STATE
    assert record_ledger.state("unknown-schema") == CONTRACT_STATE
    assert record_ledger.published_contract(CANDIDATE_SCHEMA_VERSION) is None
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == ()


def test_publish_pins_exact_contract_bytes_and_closed_operations() -> None:
    record_ledger = ledger()
    record = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert record.contract_sha256 == CANDIDATE_CONTRACT_SHA256
    assert record.contract_sha256 == _contract_digest(CANDIDATE_SCHEMA_VERSION)
    assert record.operations == frozenset(REQUEST_MODELS)
    assert {"offer.compose", "acceptance.record"} <= record.operations
    assert record.published_at == PUBLISHED_AT and record.published_at.utcoffset() is not None
    assert record.publication_receipt_ref == PUBLICATION_RECEIPT
    assert record.contract_state == CONTRACT_STATE_PUBLISHED
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLISHED
    assert record_ledger.published_contract(CANDIDATE_SCHEMA_VERSION) is record
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record,)


def test_provider_schema_publishes_its_own_closed_registry() -> None:
    record_ledger = ledger()
    record = record_ledger.publish(PROVIDER_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert record.contract_sha256 == PROVIDER_CONTRACT_SHA256
    assert record.contract_sha256 == _contract_digest(PROVIDER_SCHEMA_VERSION)
    assert "contract.authority.resolve" in record.operations
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE
    assert record_ledger.state(PROVIDER_SCHEMA_VERSION) == CONTRACT_STATE_PUBLISHED


@pytest.mark.parametrize("receipt", ["", "   ", None, 7, "-leading-hyphen"])
def test_publish_and_withdraw_require_an_opaque_non_blank_receipt(receipt: object) -> None:
    record_ledger = ledger()
    with pytest.raises(CapabilityContractError) as caught:
        record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=receipt)  # type: ignore[arg-type]
    assert caught.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == ()


def test_publish_unknown_schema_version_is_unknown_operation() -> None:
    with pytest.raises(CapabilityContractError) as caught:
        ledger().publish("v99-capabilities.unknown.v1", publication_receipt_ref=PUBLICATION_RECEIPT)
    assert caught.value.reason == CapabilityRefusalReason.UNKNOWN_OPERATION


def test_republish_identical_content_is_the_same_record_without_duplication() -> None:
    record_ledger = ledger()
    record = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    replay = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert replay is record
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record,)


def test_published_record_is_immutable_under_conflicting_receipt_or_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_ledger = ledger()
    record = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    with pytest.raises(CapabilityContractError) as caught:
        record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref="unit-other-receipt")
    assert caught.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record,)

    # A mapped-field mutation under the same schema version cannot reuse the record.
    def mutated_digest(schema_version: str) -> str:
        return "0" * 64

    monkeypatch.setattr(publication_module, "_contract_digest", mutated_digest)
    with pytest.raises(CapabilityContractError) as caught:
        record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert caught.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record,)
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLISHED


def test_withdrawal_is_a_logical_reversal_that_retains_history() -> None:
    record_ledger = ledger()
    with pytest.raises(CapabilityContractError) as never:
        record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    assert never.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    record = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    withdrawal = record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    assert withdrawal.contract_sha256 == record.contract_sha256
    assert withdrawal.contract_state == CONTRACT_STATE_PUBLICATION_WITHDRAWN
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLICATION_WITHDRAWN
    assert record_ledger.published_contract(CANDIDATE_SCHEMA_VERSION) is None
    # Replay of the same withdrawal receipt returns the same record, without a duplicate.
    assert (
        record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
        is withdrawal
    )
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record, withdrawal)
    with pytest.raises(CapabilityContractError) as conflict:
        record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref="unit-other-receipt")
    assert conflict.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record, withdrawal)
    # Re-publication after withdrawal is a new act; retained records are never rewritten.
    republished = record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert republished is not record
    assert republished.contract_sha256 == record.contract_sha256
    assert record_ledger.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLISHED
    assert record_ledger.history(CANDIDATE_SCHEMA_VERSION) == (record, withdrawal, republished)


def test_require_published_is_fail_closed_and_operation_scoped() -> None:
    record_ledger = ledger()
    with pytest.raises(CapabilityContractError) as proposed:
        record_ledger.require_published(CANDIDATE_SCHEMA_VERSION, "offer.compose")
    assert proposed.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    for operation in REQUEST_MODELS:
        assert record_ledger.require_published(CANDIDATE_SCHEMA_VERSION, operation) is None
    with pytest.raises(CapabilityContractError) as other_schema:
        record_ledger.require_published(PROVIDER_SCHEMA_VERSION, "offer.compose")
    assert other_schema.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    with pytest.raises(CapabilityContractError) as withdrawn:
        record_ledger.require_published(CANDIDATE_SCHEMA_VERSION, "offer.compose")
    assert withdrawn.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE


def test_publication_clock_and_construction_fences() -> None:
    with pytest.raises(ValueError):
        ContractPublicationLedger(clock="not-callable")  # type: ignore[arg-type]
    with pytest.raises(CapabilityContractError):
        ContractPublicationLedger(clock=clock).publish(
            CANDIDATE_SCHEMA_VERSION, publication_receipt_ref="spaces are not opaque"
        )


# --- Dispatcher wiring: default-off binding and the fail-closed consumer cycle ----


def test_publication_binding_is_default_off_and_strictly_typed() -> None:
    assert CapabilityService().publication is None
    events: list[str] = []
    service, source = purchase_service(None, events)
    assert service.publication is None
    with pytest.raises(ValueError):
        CapabilityService(publication=object())  # type: ignore[arg-type]
    bound = CapabilityService(publication=ledger())
    assert type(bound.publication) is ContractPublicationLedger
    assert source.calls == []


@pytest.mark.asyncio
async def test_vendor_projection_shape_refuses_then_consumes_then_refuses_again() -> None:
    """OP15 shape (registry section 5.1): typed refusal is the unpublished inertia."""

    record_ledger = ledger()
    events: list[str] = []
    service, source = purchase_service(record_ledger, events)
    outcome = await service.execute(envelope("offer.compose"), offer_payload())
    assert not outcome.succeeded
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert outcome.result is None
    assert source.calls == [] and events == []
    record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    outcome = await service.execute(envelope("offer.compose"), offer_payload())
    assert outcome.succeeded and outcome.result is not None
    assert outcome.result.status == "usable"
    assert source.calls == ["offer.compose"]
    assert events == ["authorize", "before_source", "source", "verify_result", "before_disclosure", "release"]
    record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    outcome = await service.execute(envelope("offer.compose"), offer_payload())
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert source.calls == ["offer.compose"]
    assert events[-1] == "release"


@pytest.mark.asyncio
async def test_other_schema_version_stays_unpublished_when_only_candidate_is_published() -> None:
    record_ledger = ledger()
    events: list[str] = []
    source = RecordingSource({"offer.compose": OFFER_RESULT}, events)
    service = CapabilityService(
        sources={"offer.compose": source},
        admissions={"offer.compose": RecordingAdmission("offer.compose", events)},
        publication=record_ledger,
    )
    record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    outcome = await service.execute(provider_envelope("offer.compose"), offer_payload())
    assert outcome.refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert source.calls == [] and events == []
    record_ledger.publish(PROVIDER_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    outcome = await service.execute(provider_envelope("offer.compose"), offer_payload())
    assert outcome.succeeded and source.calls == ["offer.compose"]


class RecordingDurableExecutor:
    """UNIT durable double: proves whether the new-admission path reached it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, envelope: CapabilityEnvelope, request: object, **kwargs: object) -> object:
        self.calls.append(envelope.operation_name)
        return CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
async def test_durable_new_admission_is_gated_while_drain_paths_stay_open() -> None:
    """Withdrawal stops new admissions; observation/reconciliation still drain (V2)."""

    record_ledger = ledger()
    events: list[str] = []
    executor = RecordingDurableExecutor()
    source = RecordingSource({"offer.compose": OFFER_RESULT}, events)
    service = CapabilityService(
        sources={"offer.compose": source},
        admissions={
            "offer.compose": RecordingAdmission("offer.compose", events, task_ref="unit.task.standalone")
        },
        durable=executor,
        publication=record_ledger,
    )
    durable_arguments: dict[str, Any] = {
        "predecessor_command_refs": (),
        "expected_journal_revision": 0,
        "effect_authority": None,
    }
    refused = await service.execute_durable(envelope("offer.compose"), offer_payload(), **durable_arguments)
    assert refused == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert executor.calls == [] and events == []
    record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    # Past the publication gate the unit durable double is reached (its own refusal).
    reached = await service.execute_durable(envelope("offer.compose"), offer_payload(), **durable_arguments)
    assert reached == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert executor.calls == ["offer.compose"]
    record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    refused = await service.execute_durable(envelope("offer.compose"), offer_payload(), **durable_arguments)
    assert refused == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert executor.calls == ["offer.compose"]
    # Drain paths are not publication-gated: with the withdrawn ledger still bound,
    # observation/reconciliation reach the durability fence (never SOURCE_UNAVAILABLE).
    drained_service = CapabilityService(durable=None, publication=record_ledger)
    assert await drained_service.observe_durable("unit-command") == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert (
        await drained_service.reconcile_durable("unit-command", expected_journal_revision=0)
        == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )


@pytest.mark.asyncio
async def test_purchase_runner_consumes_both_published_core_intents() -> None:
    """Consumer 1 (compras runner) exercised over the PUBLISHED OP02/OP03 contract."""

    record_ledger = ledger()
    events: list[str] = []
    service, source = purchase_service(record_ledger, events)
    consumer = compras_consumer(tenant_ref="unit-tenant", service=service, enabled=True)
    assert service.is_bound_to_task("journey.compras.step")
    record_ledger.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    for operation, payload in (
        ("offer.compose", offer_payload()),
        ("acceptance.record", acceptance_payload()),
    ):
        initial = {
            "envelope": envelope(operation).model_dump(mode="json", exclude_none=True),
            "payload": payload,
            "handoff": {
                "schema_version": HANDOFF_SCHEMA,
                "task_type": "journey.compras.step",
                "tenant_ref": "unit-tenant",
                "journey_ref": "unit-journey",
                "message_ref": "hk1_" + "a" * 64,
            },
            "current_message_ref": "hk1_" + "a" * 64,
            "health_priority": False,
            "human_requested": False,
        }
        state = await consumer.invoke(initial)
        assert state["technical_status"] == "succeeded"
        assert state["outcome"].succeeded
    assert source.calls == ["offer.compose", "acceptance.record"]
    record_ledger.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    state = await consumer.invoke(
        {
            "envelope": envelope("offer.compose").model_dump(mode="json", exclude_none=True),
            "payload": offer_payload(),
            "handoff": {
                "schema_version": HANDOFF_SCHEMA,
                "task_type": "journey.compras.step",
                "tenant_ref": "unit-tenant",
                "journey_ref": "unit-journey",
                "message_ref": "hk1_" + "b" * 64,
            },
            "current_message_ref": "hk1_" + "b" * 64,
            "health_priority": False,
            "human_requested": False,
        }
    )
    assert state["technical_status"] == "refused"
    assert state["outcome"].refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert source.calls == ["offer.compose", "acceptance.record"]


def test_publication_record_rejects_planted_or_naive_fields() -> None:
    from maezo.gateway.capabilities.publication import ContractPublication

    base: dict[str, object] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "contract_sha256": CANDIDATE_CONTRACT_SHA256,
        "operations": frozenset(REQUEST_MODELS),
        "published_at": PUBLISHED_AT,
        "publication_receipt_ref": PUBLICATION_RECEIPT,
    }
    with pytest.raises(ValidationError):
        ContractPublication.model_validate(base | {"operations": list(REQUEST_MODELS)})
    with pytest.raises(ValidationError):
        ContractPublication.model_validate(base | {"published_at": PUBLISHED_AT.replace(tzinfo=None)})
    with pytest.raises(ValidationError):
        ContractPublication.model_validate(base | {"contract_state": CONTRACT_STATE})
    with pytest.raises(ValidationError):
        ContractPublication.model_validate(base | {"contract_sha256": "0" * 63})
    with pytest.raises(ValidationError):
        ContractPublication.model_validate(base | {"extra": "planted"})
