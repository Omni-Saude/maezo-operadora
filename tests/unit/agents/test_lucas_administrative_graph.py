"""Candidate consumer mechanics, shared dispatch, interruptions and root isolation.

RecordingCapabilityService is a UNIT double, not a provider, authority verifier,
receipt publisher or proof of operational journeys. No CIB mock is involved.
"""

import asyncio
import warnings
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, cast

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import ensure_config
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langsmith import get_tracing_context, tracing_context

from maezo.agents.lucas.administrative.graph import compras_consumer, suporte_consumer
from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA, AdministrativeInputError
from maezo.agents.lucas.administrative.state import administrative_checkpoint_config, new_administrative_state
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityOutcome,
    CapabilityRefusalReason,
    DeclaredMemberships,
    ExternalWaitOutcome,
    ResolutionCaseStatus,
    VerifiedFulfillmentFact,
)
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.observability import get_metrics_collector


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


def lucas_error_counts() -> dict[str, float]:
    return {
        sample.labels["error_type"]: sample.value
        for metric in get_metrics_collector().registry.collect()
        for sample in metric.samples
        if sample.name == "maezo_agent_errors_total" and sample.labels.get("agent") == "lucas"
    }


class UncataloguedAdministrativeError(RuntimeError):
    pass


class AdministrativeAbort(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["journey.compras.step", "journey.suporte.step"])
@pytest.mark.parametrize("entry", ["consumer", "compiled"])
@pytest.mark.parametrize(
    ("error_class", "error_type"),
    [(RuntimeError, "runtime"), (TimeoutError, "timeout"), (UncataloguedAdministrativeError, "outro")],
)
async def test_failed_graph_turn_counts_once_with_bounded_private_labels(
    task: str, entry: str, error_class: type[Exception], error_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "SYNTHETIC_CLINICAL_ERROR_BODY_90210"
    original = error_class(marker)

    class FailingService(RecordingCapabilityService):
        async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
            await super().execute(envelope, payload)
            raise original

    service = FailingService(task)
    factory = compras_consumer if task == "journey.compras.step" else suporte_consumer
    consumer = factory(tenant_ref="unit-tenant-a", service=service, enabled=True)
    saver = InMemorySaver()
    runner = consumer.build().compile(
        checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-error-metric-key")
    )
    invoke = runner.ainvoke if entry == "compiled" else consumer.invoke
    before = lucas_error_counts()
    with warnings.catch_warnings(record=True) as warnings_seen, pytest.raises(error_class) as caught:
        await invoke(values(task))
    after = lucas_error_counts()
    changes = {
        label: after.get(label, 0.0) - before.get(label, 0.0)
        for label in before.keys() | after.keys()
        if after.get(label, 0.0) != before.get(label, 0.0)
    }
    assert changes == {error_type: 1.0}
    assert caught.value is original
    assert len(service.calls) == 1
    assert bool(saver.storage) is (entry == "compiled")
    samples = [
        sample
        for metric in get_metrics_collector().registry.collect()
        for sample in metric.samples
        if sample.name == "maezo_agent_errors_total" and sample.labels.get("agent") == "lucas"
    ]
    assert all(set(sample.labels) == {"agent", "error_type"} for sample in samples)
    assert marker not in repr(samples) + caplog.text + repr(warnings_seen)
    assert "unit-tenant-a" not in repr(samples)
    assert "unit-journey-a" not in repr(samples)
    assert error_class.__name__ not in repr(samples)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["consumer", "compiled"])
@pytest.mark.parametrize("outcome", ["disabled", "refused", "health", "human", "invalid"])
async def test_non_failed_graph_turn_and_rejected_input_do_not_count_errors(entry: str, outcome: str) -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=outcome != "disabled")
    saver = InMemorySaver()
    runner = consumer.build().compile(
        checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-non-error-metric-key")
    )
    invoke = runner.ainvoke if entry == "compiled" else consumer.invoke
    initial = values()
    if outcome == "health":
        initial["health_priority"] = True
    elif outcome == "human":
        initial["human_requested"] = True
    elif outcome == "invalid":
        initial["clinical_body"] = "SYNTHETIC_CLINICAL_BODY_90210"
    before = lucas_error_counts()
    if outcome == "invalid":
        with pytest.raises(AdministrativeInputError, match="administrative_input_contract_mismatch"):
            await invoke(initial)
        assert not saver.storage and not saver.blobs and not saver.writes
    else:
        result = await invoke(initial)
        assert (
            result["technical_status"]
            == {
                "disabled": "disabled",
                "refused": "refused",
                "health": "interrupted_health",
                "human": "interrupted_human",
            }[outcome]
        )
    assert lucas_error_counts() == before
    assert len(service.calls) == (outcome == "refused")


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
    saver = InMemorySaver()
    compiled = consumer.build().compile(
        checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-clinical-result-key")
    )
    result = await compiled.ainvoke(values(operation="fulfillment.observe"))
    assert result["technical_status"] == "refused"
    assert result["outcome"].refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert result["outcome"].result is None
    assert "unit-clinical-context" not in repr(result)
    assert "unit-clinical-context" not in repr((saver.storage, saver.blobs, saver.writes))


@pytest.mark.asyncio
async def test_compiled_call_rejects_output_planting_before_any_checkpoint() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service)
    initial = new_administrative_state(values())
    planted: dict[str, object] = dict(initial) | {"technical_status": "succeeded", "outcome": service.outcome}
    saver = InMemorySaver()
    compiled = consumer.build().compile(
        checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-planting-key")
    )
    with pytest.raises(AdministrativeInputError, match="administrative_input_contract_mismatch"):
        await compiled.ainvoke(planted)
    assert not saver.storage and not saver.blobs and not saver.writes
    assert service.calls == []


@pytest.mark.asyncio
async def test_two_journeys_do_not_overwrite_roots_in_actual_memory_saver() -> None:
    saver = InMemorySaver()
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
    pseudonymizer = Pseudonymizer(key=b"unit-isolation-key")
    compiled = consumer.build().compile(checkpointer=saver, pseudonymizer=pseudonymizer)
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
    snapshots = [
        await compiled.aget_state(journey_ref=journey) for journey in ["unit-journey-a", "unit-journey-b"]
    ]
    turns = [cast(dict[str, object], snapshot.values)["turn"] for snapshot in snapshots]
    assert [turn.envelope.journey_ref for turn in turns] == ["unit-journey-a", "unit-journey-b"]
    suporte = suporte_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
    support_graph = suporte.build().compile(checkpointer=saver, pseudonymizer=pseudonymizer)
    support_config = administrative_checkpoint_config(
        task_type="journey.suporte.step",
        tenant_ref="unit-tenant-a",
        journey_ref="unit-journey-a",
        pseudonymizer=pseudonymizer,
    )
    await support_graph.ainvoke(new_administrative_state(values("journey.suporte.step")), support_config)
    first_purchase = await compiled.aget_state(journey_ref="unit-journey-a")
    assert first_purchase.values["turn"].handoff.task_type == "journey.compras.step"
    assert (await support_graph.aget_state(journey_ref="unit-journey-a")).values[
        "turn"
    ].handoff.task_type == ("journey.suporte.step")
    other_tenant = compras_consumer(tenant_ref="unit-tenant-b", service=CapabilityService())
    tenant_graph = other_tenant.build().compile(checkpointer=saver, pseudonymizer=pseudonymizer)
    other_values = values()
    other_values["envelope"] = dict(cast(dict[str, object], other_values["envelope"])) | {
        "tenant_ref": "unit-tenant-b"
    }
    other_values["handoff"] = dict(cast(dict[str, object], other_values["handoff"])) | {
        "tenant_ref": "unit-tenant-b"
    }
    await tenant_graph.ainvoke(other_values)
    assert (await tenant_graph.aget_state(journey_ref="unit-journey-a")).values[
        "turn"
    ].envelope.tenant_ref == ("unit-tenant-b")
    assert (await compiled.aget_state(journey_ref="unit-journey-a")).values["turn"].envelope.tenant_ref == (
        "unit-tenant-a"
    )
    assert len(saver.storage) == 4


@pytest.mark.asyncio
async def test_consumer_fixed_tenant_refuses_an_otherwise_consistent_other_tenant() -> None:
    service = RecordingCapabilityService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-b", service=service, enabled=True)
    with pytest.raises(AdministrativeInputError, match="administrative_consumer_object_mismatch"):
        await consumer.invoke(values())
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("input_form", ["factory", "caller"])
@pytest.mark.parametrize("field", ["outcome", "technical_status", "business_revision", "clinical_body"])
async def test_unknown_and_output_fields_never_reach_saver_or_warnings(
    input_form: str, field: str, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "SYNTHETIC_CLINICAL_BODY_90210"
    saver = InMemorySaver()
    service = RecordingCapabilityService("journey.compras.step")
    compiled = (
        compras_consumer(tenant_ref="unit-tenant-a", service=service)
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-adversarial-key"))
    )
    initial: dict[str, object] = (
        dict(new_administrative_state(values())) if input_form == "factory" else values()
    )
    initial[field] = CapabilityOutcome(
        result=VerifiedFulfillmentFact(
            fact_kind="pending",
            source_fact_ref="unit-fact",
            source_revision="unit-revision-a",
            clinical_result_context_ref=marker,
        )
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(AdministrativeInputError) as error:
            await compiled.ainvoke(initial)
    assert not saver.storage and not saver.blobs and not saver.writes
    assert marker not in str(error.value)
    assert marker not in caplog.text
    assert not caught
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forgery",
    [
        "nested_model_copy",
        "unknown_model_copy",
        "model_construct",
        "forged_envelope",
        "forged_handoff",
        "forged_priority",
        "stale_message",
        "foreign_task",
        "foreign_tenant",
    ],
)
async def test_forged_input_is_deeply_revalidated_before_saver(
    forgery: str, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "SYNTHETIC_CLINICAL_BODY_90210"
    saver = InMemorySaver()
    service = RecordingCapabilityService("journey.compras.step")
    compiled = (
        compras_consumer(tenant_ref="unit-tenant-a", service=service)
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-forgery-key"))
    )
    initial = new_administrative_state(values())
    turn = initial["turn"]
    if forgery == "nested_model_copy":
        initial["turn"] = replace(
            turn, payload=turn.payload.model_copy(update={"wait_ref": {"clinical": marker}})
        )
    elif forgery == "unknown_model_copy":
        initial["turn"] = replace(turn, payload=turn.payload.model_copy(update={"clinical_body": marker}))
    elif forgery == "model_construct":
        initial["turn"] = replace(turn, payload=type(turn.payload).model_construct(wait_ref=marker))
    elif forgery == "forged_envelope":
        object.__setattr__(
            turn, "envelope", turn.envelope.model_copy(update={"tenant_ref": {"clinical": marker}})
        )
    elif forgery == "forged_handoff":
        object.__setattr__(turn.handoff, "message_ref", {"clinical": marker})
    elif forgery == "forged_priority":
        object.__setattr__(turn, "health_priority", {"clinical": marker})
    elif forgery == "stale_message":
        object.__setattr__(turn, "current_message_ref", "hk1_" + "b" * 64)
    elif forgery == "foreign_task":
        object.__setattr__(turn.handoff, "task_type", "journey.suporte.step")
    elif forgery == "foreign_tenant":
        object.__setattr__(turn, "envelope", turn.envelope.model_copy(update={"tenant_ref": "unit-tenant-b"}))
        object.__setattr__(turn.handoff, "tenant_ref", "unit-tenant-b")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(AdministrativeInputError) as error:
            await compiled.ainvoke(initial)
    assert not saver.storage and not saver.blobs and not saver.writes
    assert marker not in str(error.value)
    assert marker not in caplog.text
    assert not caught
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("config_field", ["thread_id", "checkpoint_ns", "checkpoint_id", "metadata"])
async def test_caller_cannot_select_foreign_root_namespace_replay_or_metadata(config_field: str) -> None:
    saver = InMemorySaver()
    pseudonymizer = Pseudonymizer(key=b"unit-root-key")
    compiled = (
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
        .build()
        .compile(checkpointer=saver, pseudonymizer=pseudonymizer)
    )
    config = administrative_checkpoint_config(
        task_type="journey.compras.step",
        tenant_ref="unit-tenant-a",
        journey_ref="unit-journey-a",
        pseudonymizer=pseudonymizer,
    )
    if config_field == "metadata":
        config["metadata"] = {"clinical": "SYNTHETIC_CLINICAL_BODY_90210"}
    else:
        config["configurable"][config_field] = "foreign-root-or-replay"
    with pytest.raises(AdministrativeInputError, match="administrative_checkpoint_config_mismatch"):
        await compiled.ainvoke(values(), config)
    assert not saver.storage and not saver.blobs and not saver.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uncontracted_input", [None, Command(update={"outcome": "planted"}), "clinical body"]
)
async def test_resume_command_and_non_mapping_inputs_have_no_checkpoint_entry(
    uncontracted_input: Any,
) -> None:
    saver = InMemorySaver()
    compiled = (
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-command-key"))
    )
    with pytest.raises(AdministrativeInputError, match="administrative_input_contract_mismatch"):
        await compiled.ainvoke(uncontracted_input)
    assert not saver.storage and not saver.blobs and not saver.writes
    assert not hasattr(compiled, "update_state")
    assert not hasattr(compiled, "aupdate_state")
    assert not hasattr(compiled, "builder")
    assert not hasattr(compiled, "astream")


def test_real_checkpointer_requires_injected_gateway_pseudonymizer() -> None:
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
    with pytest.raises(AdministrativeInputError, match="administrative_checkpoint_identity_unavailable"):
        consumer.build().compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_prior_success_is_reset_on_next_turn_with_real_saver() -> None:
    saver = InMemorySaver()
    service = RecordingCapabilityService("journey.compras.step")
    service.outcome = CapabilityOutcome(
        result=ExternalWaitOutcome(
            wait_status="pending", settlement_revision="unit-revision-a", owner_case_ref="unit-case-a"
        )
    )
    compiled = (
        compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-reset-key"))
    )
    first = await compiled.ainvoke(values())
    assert first["technical_status"] == "succeeded"
    assert first["outcome"].result.wait_status == "pending"
    second = await compiled.ainvoke(values() | {"health_priority": True})
    assert second["technical_status"] == "interrupted_health"
    assert second["outcome"] is None
    snapshot = await compiled.aget_state(journey_ref="unit-journey-a")
    assert snapshot.values["outcome"] is None
    assert len(service.calls) == 1
    assert len(saver.storage) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["journey.compras.step", "journey.suporte.step"])
async def test_current_case_contract_and_pending_source_state_survive_checkpoint(task: str) -> None:
    members = DeclaredMemberships(
        case_kinds=frozenset({"unit-source-kind"}), case_statuses=frozenset({"unit-source-pending"})
    )
    service = RecordingCapabilityService(task)
    service.memberships = members
    service.outcome = CapabilityOutcome(
        result=ResolutionCaseStatus.model_validate(
            {
                "case_ref": "unit-case-a",
                "case_status": "unit-source-pending",
                "authority_receipt_ref": "unit-receipt-a",
                "business_revision": "unit-revision-a",
            },
            context={"memberships": members},
        )
    )
    consumer_factory = compras_consumer if task == "journey.compras.step" else suporte_consumer
    saver = InMemorySaver()
    compiled = (
        consumer_factory(tenant_ref="unit-tenant-a", service=service, enabled=True)
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-case-key"))
    )
    initial = values(task, operation="case.open_or_update")
    initial["payload"] = {
        "problem_ref": "unit-problem-a",
        "origin_case_or_journey_ref": "unit-journey-a",
        "case_kind": "unit-source-kind",
        "requester_authority_ref": "unit-requester-a",
        "evidence_refs": [],
    }
    result = await compiled.ainvoke(initial)
    assert result["outcome"].result.case_status == "unit-source-pending"
    snapshot = await compiled.aget_state(journey_ref="unit-journey-a")
    assert snapshot.values["outcome"].result.case_status == "unit-source-pending"
    assert snapshot.values["turn"].handoff.task_type == task
    assert len(service.calls) == 1
    # A previously parsed DTO does not retain a revoked vocabulary's admission.
    service.memberships = DeclaredMemberships()
    before = repr((saver.storage, saver.blobs, saver.writes))
    with pytest.raises(
        AdministrativeInputError, match="administrative_envelope_or_payload_contract_mismatch"
    ):
        await compiled.ainvoke({"turn": result["turn"]})
    assert repr((saver.storage, saver.blobs, saver.writes)) == before
    assert len(service.calls) == 1


_RUNNABLE_MARKER = "SYNTHETIC_RUNNABLE_CLINICAL_BODY_90210"
_APPLICATION_IDENTITY: ContextVar[tuple[str, str]] = ContextVar("unit_security_tenant_correlation")


class ParentCallbacks(BaseCallbackHandler):
    """Observe parent callbacks locally; no tracer, network or credentials."""

    run_inline = True

    def __init__(self) -> None:
        self.names: list[str] = []

    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        self.names.append(kwargs.get("name", ""))


def assert_parent_context_unchanged(before: RunnableConfig, tracing_before: dict[str, Any]) -> None:
    after = ensure_config()
    for key in ["metadata", "tags", "configurable", "run_name", "recursion_limit", "max_concurrency"]:
        assert after.get(key) == before.get(key)
    assert after["callbacks"].handlers == before["callbacks"].handlers
    assert after["callbacks"].parent_run_id == before["callbacks"].parent_run_id
    assert get_tracing_context() == tracing_before


def hostile_parent_config(callbacks: ParentCallbacks) -> RunnableConfig:
    return {
        "metadata": {"clinical_body": _RUNNABLE_MARKER},
        "tags": [_RUNNABLE_MARKER],
        "callbacks": [callbacks],
        "run_name": "unit-parent",
        "recursion_limit": 1,
        "max_concurrency": 1,
        "configurable": {
            "thread_id": "foreign-parent-root",
            "checkpoint_ns": "foreign-parent-namespace",
            "checkpoint_id": "foreign-parent-history",
            "clinical_body": _RUNNABLE_MARKER,
        },
    }


class ContextRecordingService(RecordingCapabilityService):
    async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
        assert _APPLICATION_IDENTITY.get() == (envelope.tenant_ref, envelope.correlation_ref)
        config = ensure_config()
        assert _RUNNABLE_MARKER not in repr(config)
        assert "clinical_body" not in config["configurable"]
        assert "run_name" not in config
        assert config["recursion_limit"] != 1
        assert "max_concurrency" not in config
        assert get_tracing_context()["parent"] is None
        assert get_tracing_context()["metadata"] == {}
        assert get_tracing_context()["tags"] == []
        assert get_tracing_context()["enabled"] is False
        # Overlap real graph tasks to expose context crossing between tenants.
        await asyncio.sleep(0)
        return await super().execute(envelope, payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["journey.compras.step", "journey.suporte.step"])
async def test_default_disabled_runnable_child_keeps_checkpoint_metadata_clean(task: str) -> None:
    saver = InMemorySaver()
    consumer_factory = compras_consumer if task == "journey.compras.step" else suporte_consumer
    service = RecordingCapabilityService(task)
    runner = (
        consumer_factory(tenant_ref="unit-tenant-a", service=service)
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-runnable-metadata-key"))
    )

    async def child(_: str) -> str:
        result = await runner.ainvoke(values(task))
        assert result["outcome"] is None
        return result["technical_status"]

    result = (
        await RunnableLambda(child)
        .with_config(metadata={"clinical_body": _RUNNABLE_MARKER})
        .ainvoke("opaque-parent-input")
    )
    assert result == "disabled"
    assert service.calls == []
    snapshot = await runner.aget_state(journey_ref="unit-journey-a")
    assert snapshot.values["technical_status"] == "disabled"
    assert _RUNNABLE_MARKER not in repr(snapshot.metadata)
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))
    assert saver.storage and saver.blobs and saver.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("with_saver", [False, True])
async def test_runnable_parent_configuration_and_tracing_are_isolated_and_preserved(with_saver: bool) -> None:
    saver = InMemorySaver()
    service = ContextRecordingService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    runner = (
        consumer.build().compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-context-key"))
        if with_saver
        else consumer.build().compile()
    )
    callbacks = ParentCallbacks()
    identity = ("unit-tenant-a", "unit-correlation-a")

    async def child(_: str) -> str:
        before, tracing_before = ensure_config(), get_tracing_context()
        result = await runner.ainvoke(values())
        if with_saver:
            snapshot = await runner.aget_state(journey_ref="unit-journey-a")
            assert snapshot.values["technical_status"] == "refused"
            assert _RUNNABLE_MARKER not in repr(snapshot.metadata)
        assert _APPLICATION_IDENTITY.get() == identity
        assert_parent_context_unchanged(before, tracing_before)
        return result["technical_status"]

    token = _APPLICATION_IDENTITY.set(identity)
    try:
        with tracing_context(
            parent=False, metadata={"clinical_body": _RUNNABLE_MARKER}, tags=[_RUNNABLE_MARKER], enabled=False
        ):
            tracing_before = get_tracing_context()
            result = (
                await RunnableLambda(child).with_config(hostile_parent_config(callbacks)).ainvoke("opaque")
            )
            assert get_tracing_context() == tracing_before
    finally:
        _APPLICATION_IDENTITY.reset(token)
    assert result == "refused"
    assert len(service.calls) == 1
    assert callbacks.names == ["unit-parent"]
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))
    assert bool(saver.storage) is with_saver


@pytest.mark.asyncio
async def test_nested_real_parent_graph_cannot_select_child_history_or_checkpoint_root() -> None:
    parent_saver, child_saver = InMemorySaver(), InMemorySaver()
    pseudonymizer = Pseudonymizer(key=b"unit-nested-runnable-key")
    runner = (
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
        .build()
        .compile(checkpointer=child_saver, pseudonymizer=pseudonymizer)
    )
    await runner.ainvoke(values())
    old_snapshot = await runner.aget_state(journey_ref="unit-journey-a")
    child_root = old_snapshot.config["configurable"]["thread_id"]

    async def node(_: dict[str, Any]) -> dict[str, Any]:
        before, tracing_before = ensure_config(), get_tracing_context()
        result = await runner.ainvoke(values() | {"health_priority": True})
        snapshot = await runner.aget_state(journey_ref="unit-journey-a")
        assert result["technical_status"] == snapshot.values["technical_status"] == "interrupted_health"
        assert snapshot.config["configurable"]["thread_id"] == child_root
        assert (
            snapshot.config["configurable"]["checkpoint_id"]
            != old_snapshot.config["configurable"]["checkpoint_id"]
        )
        assert _RUNNABLE_MARKER not in repr(snapshot.metadata)
        assert_parent_context_unchanged(before, tracing_before)
        return {"done": True}

    graph = StateGraph(dict[str, Any])
    graph.add_node("parent", node)
    graph.add_edge(START, "parent")
    graph.add_edge("parent", END)
    parent = graph.compile(checkpointer=parent_saver)
    parent_config: RunnableConfig = {
        "configurable": {"thread_id": "unit-parent-root"},
        "metadata": {"clinical_body": _RUNNABLE_MARKER},
        "tags": [_RUNNABLE_MARKER],
    }
    assert (await parent.ainvoke({"done": False}, parent_config))["done"] is True
    assert set(child_saver.storage) == {child_root}
    assert set(parent_saver.storage) == {"unit-parent-root"}
    assert _RUNNABLE_MARKER not in repr((child_saver.storage, child_saver.blobs, child_saver.writes))
    # The enclosing graph still owns its original metadata after the child returns.
    assert _RUNNABLE_MARKER in repr((await parent.aget_state(parent_config)).metadata)


@pytest.mark.asyncio
async def test_parent_time_travel_coordinates_cannot_replay_child_state() -> None:
    saver = InMemorySaver()
    runner = (
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-child-history-key"))
    )
    await runner.ainvoke(values())
    old = await runner.aget_state(journey_ref="unit-journey-a")
    callbacks = ParentCallbacks()
    parent_config = hostile_parent_config(callbacks)
    parent_config["configurable"] = dict(old.config["configurable"])

    async def child(_: str) -> str:
        before, tracing_before = ensure_config(), get_tracing_context()
        await runner.ainvoke(values() | {"human_requested": True})
        current = await runner.aget_state(journey_ref="unit-journey-a")
        assert current.values["technical_status"] == "interrupted_human"
        assert current.config["configurable"]["checkpoint_id"] != old.config["configurable"]["checkpoint_id"]
        assert_parent_context_unchanged(before, tracing_before)
        return current.values["technical_status"]

    assert await RunnableLambda(child).with_config(parent_config).ainvoke("opaque") == "interrupted_human"
    assert callbacks.names == ["unit-parent"]
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "abort", "cancellation"])
@pytest.mark.parametrize("entry", ["consumer", "compiled"])
async def test_runnable_context_is_restored_after_child_failure(failure: str, entry: str) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    class FailingService(ContextRecordingService):
        async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
            await super().execute(envelope, payload)
            started.set()
            try:
                if failure == "exception":
                    raise RuntimeError("unit-service-failure")
                if failure == "abort":
                    raise AdministrativeAbort("unit-service-abort")
                await asyncio.Event().wait()
                raise AssertionError("cancelled execution resumed")
            finally:
                stopped.set()

    saver = InMemorySaver()
    service = FailingService("journey.compras.step")
    consumer = compras_consumer(tenant_ref="unit-tenant-a", service=service, enabled=True)
    runner = consumer.build().compile(
        checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-context-failure-key")
    )
    invoke = runner.ainvoke if entry == "compiled" else consumer.invoke
    callbacks = ParentCallbacks()
    identity = ("unit-tenant-a", "unit-correlation-a")

    async def child(_: str) -> str:
        before, tracing_before = ensure_config(), get_tracing_context()
        errors_before = lucas_error_counts()
        if failure == "exception":
            with pytest.raises(RuntimeError, match="unit-service-failure"):
                await invoke(values())
            assert lucas_error_counts() == errors_before | {
                "runtime": errors_before.get("runtime", 0.0) + 1.0
            }
        elif failure == "abort":
            with pytest.raises(AdministrativeAbort, match="unit-service-abort"):
                await invoke(values())
            assert lucas_error_counts() == errors_before
        else:
            running = asyncio.create_task(invoke(values()))
            await asyncio.wait_for(started.wait(), timeout=2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert lucas_error_counts() == errors_before
        assert stopped.is_set()
        assert _APPLICATION_IDENTITY.get() == identity
        assert_parent_context_unchanged(before, tracing_before)
        return "restored"

    token = _APPLICATION_IDENTITY.set(identity)
    try:
        with tracing_context(
            parent=False, metadata={"clinical_body": _RUNNABLE_MARKER}, tags=[_RUNNABLE_MARKER], enabled=False
        ):
            tracing_before = get_tracing_context()
            assert (
                await RunnableLambda(child).with_config(hostile_parent_config(callbacks)).ainvoke("opaque")
                == "restored"
            )
            assert get_tracing_context() == tracing_before
    finally:
        _APPLICATION_IDENTITY.reset(token)
    assert callbacks.names == ["unit-parent"]
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))


@pytest.mark.asyncio
async def test_state_read_context_is_isolated_even_when_saver_raises() -> None:
    class ReadFailingSaver(InMemorySaver):
        fail_reads = False

        async def aget_tuple(self, config: RunnableConfig) -> Any:
            assert _RUNNABLE_MARKER not in repr(ensure_config())
            assert get_tracing_context()["metadata"] == {}
            assert get_tracing_context()["parent"] is None
            if self.fail_reads:
                raise RuntimeError("unit-checkpoint-read-failure")
            return await super().aget_tuple(config)

    saver = ReadFailingSaver()
    runner = (
        compras_consumer(tenant_ref="unit-tenant-a", service=CapabilityService())
        .build()
        .compile(checkpointer=saver, pseudonymizer=Pseudonymizer(key=b"unit-read-failure-key"))
    )
    await runner.ainvoke(values())
    saver.fail_reads = True
    callbacks = ParentCallbacks()

    async def child(_: str) -> str:
        before, tracing_before = ensure_config(), get_tracing_context()
        with pytest.raises(RuntimeError, match="unit-checkpoint-read-failure"):
            await runner.aget_state(journey_ref="unit-journey-a")
        assert_parent_context_unchanged(before, tracing_before)
        return "restored"

    assert (
        await RunnableLambda(child).with_config(hostile_parent_config(callbacks)).ainvoke("opaque")
        == "restored"
    )
    assert callbacks.names == ["unit-parent"]
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))


@pytest.mark.asyncio
async def test_concurrent_runnable_parents_keep_tenant_task_roots_and_security_context_separate() -> None:
    saver = InMemorySaver()
    pseudonymizer = Pseudonymizer(key=b"unit-concurrent-context-key")

    async def invoke(task: str, tenant: str) -> str:
        service = ContextRecordingService(task)
        consumer_factory = compras_consumer if task == "journey.compras.step" else suporte_consumer
        runner = (
            consumer_factory(tenant_ref=tenant, service=service, enabled=True)
            .build()
            .compile(checkpointer=saver, pseudonymizer=pseudonymizer)
        )
        initial = values(task)
        initial["envelope"]["tenant_ref"] = initial["handoff"]["tenant_ref"] = tenant
        callbacks = ParentCallbacks()
        identity = (tenant, "unit-correlation-a")

        async def child(_: str) -> str:
            before, tracing_before = ensure_config(), get_tracing_context()
            result = await runner.ainvoke(initial)
            assert result["technical_status"] == "refused"
            snapshot = await runner.aget_state(journey_ref="unit-journey-a")
            assert snapshot.values["turn"].envelope.tenant_ref == tenant
            assert snapshot.values["turn"].handoff.task_type == task
            assert _RUNNABLE_MARKER not in repr(snapshot.metadata)
            assert_parent_context_unchanged(before, tracing_before)
            assert _APPLICATION_IDENTITY.get() == identity
            return snapshot.config["configurable"]["thread_id"]

        token = _APPLICATION_IDENTITY.set(identity)
        try:
            root = await RunnableLambda(child).with_config(hostile_parent_config(callbacks)).ainvoke("opaque")
        finally:
            _APPLICATION_IDENTITY.reset(token)
        assert callbacks.names == ["unit-parent"]
        assert len(service.calls) == 1
        return root

    roots = await asyncio.gather(
        invoke("journey.compras.step", "unit-tenant-a"),
        invoke("journey.compras.step", "unit-tenant-b"),
        invoke("journey.suporte.step", "unit-tenant-a"),
    )
    assert len(set(roots)) == 3
    assert set(saver.storage) == set(roots)
    assert _RUNNABLE_MARKER not in repr((saver.storage, saver.blobs, saver.writes))
