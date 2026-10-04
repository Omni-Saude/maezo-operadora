"""Candidate consumer mechanics, shared dispatch, interruptions and root isolation.

RecordingCapabilityService is a UNIT double, not a provider, authority verifier,
receipt publisher or proof of operational journeys. No CIB mock is involved.
"""

from typing import cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.lucas.administrative.graph import compras_consumer, suporte_consumer
from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA, AdministrativeInputError
from maezo.agents.lucas.administrative.state import administrative_checkpoint_config, new_administrative_state
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityOutcome,
    CapabilityRefusalReason,
    ExternalWaitOutcome,
    VerifiedFulfillmentFact,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pseudonymizer import Pseudonymizer


class RecordingCapabilityService(CapabilityService):
    """UNIT dispatch spy: intentionally has no business/source authority."""

    def __init__(self, task_ref: str) -> None:
        super().__init__()
        self.task_ref = task_ref
        self.calls: list[tuple[CapabilityEnvelope, object]] = []
        self.outcome = CapabilityOutcome.refused(CapabilityRefusalReason.SOURCE_UNAVAILABLE)

    def is_bound_to_task(self, task_ref: str) -> bool:
        return task_ref == self.task_ref

    async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
        self.calls.append((envelope, payload))
        return self.outcome


def values(
    task: str = "journey.compras.step",
    *,
    journey: str = "unit-journey-a",
    operation: str = "external_wait.settle",
) -> dict[str, object]:
    payload = {
        "wait_ref": "unit-wait-a",
        "expected_producer_ref": "unit-source-a",
        "correlation_ref": "unit-correlation-a",
        "source_outcome_ref": "unit-receipt-a",
    }
    if operation == "acceptance.record":
        payload = {
            "offer_ref": "unit-offer-a",
            "offer_version": "unit-offer-version-a",
            "decision": "accept",
            "terms_evidence_ref": "unit-terms-a",
            "customer_authority_proof_ref": "unit-customer-a",
        }
    elif operation == "fulfillment.observe":
        payload = {
            "fulfillment_ref": "unit-fulfillment-a",
            "source_event_or_observation_ref": "unit-observation-a",
            "source_authority_contract_ref": "unit-contract-a",
            "expected_revision": "unit-revision-a",
        }
    return {
        "envelope": {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "operation_name": operation,
            "tenant_ref": "unit-tenant-a",
            "legal_entity_ref": "unit-legal-a",
            "journey_ref": journey,
            "correlation_ref": "unit-correlation-a",
            "causation_ref": "unit-causation-a",
            "idempotency_key": "unit-command-a",
            "expected_business_revision": "unit-revision-a",
            "source_authority_ref": "unit-source-a",
            "policy_revision": "unit-policy-a",
            "data_classification": "unit-administrative",
        },
        "payload": payload,
        "handoff": {
            "schema_version": HANDOFF_SCHEMA,
            "task_type": task,
            "tenant_ref": "unit-tenant-a",
            "journey_ref": journey,
            "message_ref": "hk1_" + "a" * 64,
        },
        "current_message_ref": "hk1_" + "a" * 64,
        "health_priority": False,
        "human_requested": False,
    }


@pytest.mark.asyncio
async def test_default_candidate_is_disabled_and_never_dispatches() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service)
    result = await consumer.invoke(values())
    assert result["technical_status"] == "disabled"
    assert result["outcome"] is None
    assert service.calls == []


def test_enabled_consumer_requires_its_server_owned_task_binding() -> None:
    with pytest.raises(AdministrativeInputError, match="administrative_consumer_task_binding_unavailable"):
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService(), enabled=True)
    with pytest.raises(AdministrativeInputError, match="administrative_consumer_task_binding_unavailable"):
        suporte_consumer(
            tenant_ref="unit-tenant-a",
            service=RecordingCapabilityService("journey.compras.step"),
            enabled=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("health", "human", "status"),
    [
        (True, True, "interrupted_health"),
        (True, False, "interrupted_health"),
        (False, True, "interrupted_human"),
    ],
)
async def test_health_and_human_interrupt_before_any_gateway_call(
    health: bool, human: bool, status: str
) -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    result = await consumer.invoke(values() | {"health_priority": health, "human_requested": human})
    assert result["technical_status"] == status
    assert service.calls == []


@pytest.mark.asyncio
async def test_both_consumers_reuse_service_implementation_and_keep_pending_case() -> None:
    compras_service = RecordingCapabilityService("journey.compras.step")
    suporte_service = RecordingCapabilityService("journey.suporte.step")
    pending = ExternalWaitOutcome(
        wait_status="pending", settlement_revision="unit-revision-a", owner_case_ref="unit-case-a"
    )
    outcome = CapabilityOutcome(result=pending)
    compras_service.outcome = outcome
    suporte_service.outcome = outcome
    for consumer, service in [
        (
            compras_consumer(tenant_ref="unit-tenant-a", service=compras_service, enabled=True),
            compras_service,
        ),
        (
            suporte_consumer(tenant_ref="unit-tenant-a", service=suporte_service, enabled=True),
            suporte_service,
        ),
    ]:
        result = await consumer.invoke(values(consumer.task_type))
        assert result["outcome"] is outcome
        assert result["outcome"].result is pending
        assert pending.wait_status == "pending"
        assert pending.owner_case_ref == "unit-case-a"
        assert service.calls[0][0].expected_business_revision == "unit-revision-a"
        assert "resolved" not in result
        assert "business_revision" not in result


@pytest.mark.asyncio
async def test_support_can_never_record_commercial_acceptance() -> None:
    service = RecordingCapabilityService("journey.suporte.step")
    consumer = suporte_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    result = await consumer.invoke(values("journey.suporte.step", operation="acceptance.record"))
    assert result["technical_status"] == "operation_not_in_task"
    assert service.calls == []


@pytest.mark.asyncio
async def test_consumer_preserves_source_refusal_and_does_not_retry_uncertain_command() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    result = await consumer.invoke(values())
    assert result["technical_status"] == "refused"
    assert result["outcome"] is service.outcome
    assert result["outcome"].refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_clinical_result_context_is_not_disclosed_or_checkpointed() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    service.outcome = CapabilityOutcome(
        result=VerifiedFulfillmentFact(
            fact_kind="pending",
            source_fact_ref="unit-source-fact",
            source_revision="unit-revision-a",
            clinical_result_context_ref="unit-clinical-context",
        )
    )
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    result = await consumer.invoke(values(operation="fulfillment.observe"))
    assert result["technical_status"] == "refused"
    assert result["outcome"].refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert result["outcome"].result is None
    assert "unit-clinical-context" not in repr(result)


@pytest.mark.asyncio
async def test_direct_graph_call_cannot_reuse_or_plant_prior_output() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service)
    initial = new_administrative_state(values())
    initial["technical_status"] = "succeeded"
    initial["outcome"] = service.outcome
    result = await consumer.build().compile(checkpointer=None).ainvoke(initial)
    assert result["technical_status"] == "disabled"
    assert result["outcome"] is None


@pytest.mark.asyncio
async def test_two_journeys_do_not_overwrite_roots_in_actual_memory_saver() -> None:
    saver = InMemorySaver()
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
    compiled = consumer.build().compile(checkpointer=saver)
    pseudonymizer = Pseudonymizer(key=b"unit-isolation-key")
    configs = [
        administrative_checkpoint_config(
            task_type="journey.compras.step",
            tenant_ref="unit-tenant-a",
            journey_ref=journey,
            pseudonymizer=pseudonymizer,
        )
        for journey in ["unit-journey-a", "unit-journey-b"]
    ]
    for journey, config in zip(["unit-journey-a", "unit-journey-b"], configs, strict=True):
        await compiled.ainvoke(new_administrative_state(values(journey=journey)), config)
    snapshots = [await compiled.aget_state(config) for config in configs]
    turns = [cast(dict[str, object], snapshot.values)["turn"] for snapshot in snapshots]
    assert [turn.envelope.journey_ref for turn in turns] == ["unit-journey-a", "unit-journey-b"]
    suporte = suporte_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
    support_graph = suporte.build().compile(checkpointer=saver)
    support_config = administrative_checkpoint_config(
        task_type="journey.suporte.step",
        tenant_ref="unit-tenant-a",
        journey_ref="unit-journey-a",
        pseudonymizer=pseudonymizer,
    )
    await support_graph.ainvoke(new_administrative_state(values("journey.suporte.step")), support_config)
    first_purchase = await compiled.aget_state(configs[0])
    assert first_purchase.values["turn"].handoff.task_type == "journey.compras.step"
    assert (await support_graph.aget_state(support_config)).values["turn"].handoff.task_type == (
        "journey.suporte.step"
    )
    assert len(saver.storage) == 3


@pytest.mark.asyncio
async def test_consumer_fixed_tenant_refuses_an_otherwise_consistent_other_tenant() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-b", service=service, enabled=True)
    with pytest.raises(AdministrativeInputError, match="administrative_consumer_object_mismatch"):
        await consumer.invoke(values())
    assert service.calls == []
