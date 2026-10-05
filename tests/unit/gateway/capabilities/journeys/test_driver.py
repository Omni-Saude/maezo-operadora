"""Hostile preparation, scope, stale decisions and arrivals; UNIT doubles only."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from maezo.agents.lucas.administrative.journey import compras_journey, suporte_journey
from maezo.gateway.capabilities.journeys.contracts import JourneyContractError
from maezo.gateway.capabilities.journeys.driver import CapabilityServiceExecutionAdapter
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.capabilities.service import CapabilityService
from tests.unit.agents.test_administrative_journeys import (
    COMPRA_START,
    SUPPORT_START,
    finish_script,
    fixture_driver,
    observation,
)

from .helpers import NOW, Step, binding, turn


@pytest.mark.asyncio
async def test_disabled_empty_providers_close_effect_and_preserve_factory_scope() -> None:
    b = binding(enabled=False)
    result = await compras_journey(b).accept_turn(turn(b, 0))
    assert result.technical_refusal is not None
    assert result.source_completion_receipt_ref is None
    with pytest.raises(JourneyContractError):
        suporte_journey(b)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "task_ref",
        "tenant_ref",
        "legal_entity_ref",
        "journey_ref",
        "topology_contract_ref",
        "topology_version_ref",
        "transition_contract_ref",
        "cursor_ref",
    ],
)
async def test_unbound_or_unpublished_prepared_action_cannot_dispatch(field: str) -> None:
    b, journal, _, source, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    source.forgery = {field: "wrong-or-unpublished"}
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal is not None
    assert executor.effects == [] and not journal.commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["successor", "expired", "stale_turn", "current_decision", "missing_source"]
)
async def test_source_transition_and_current_manifestation_gates_close_before_effect(failure: str) -> None:
    b, journal, _, source, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    current = turn(b, 0)
    if failure == "successor":
        source.bad_successor = True
    elif failure == "expired":
        source.expired = True
    elif failure == "stale_turn":
        current = current.model_copy(update={"current_message_ref": "hk1_" + "b" * 64})
    elif failure == "current_decision":
        current = current.model_copy(update={"manifestation_evidence_ref": "forged-or-stale-decision"})
    else:
        driver.preparation = None
    result = await driver.accept_turn(current)
    assert result.technical_refusal is not None and executor.effects == []
    assert not journal.commands


@pytest.mark.asyncio
async def test_support_op03_never_passes_even_if_source_preparation_attempts_it() -> None:
    b, journal, _, _, executor, driver = fixture_driver(
        "journey.suporte.step", [Step("S1_case", "acceptance.record")]
    )
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.UNKNOWN_OPERATION
    assert executor.effects == [] and not journal.commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence", ["unit-current-decision", "forged-delegate", "stale-confirmation", "silence"]
)
async def test_no_silent_or_stale_confirmation_can_run_s4_op07(evidence: str) -> None:
    b, journal, ingress, source, executor, driver = fixture_driver("journey.suporte.step", SUPPORT_START)
    await driver.accept_turn(turn(b, 0))
    await driver.accept_turn(turn(b, journal.revision))  # Prepare source-bound wait after domain ACK.
    event = "unit-confirmation-test-event"
    await driver.accept_observation(observation(b, journal, event), journal.revision)
    await driver.resume(event, journal.revision)
    before = len(executor.effects)
    result = await driver.accept_turn(turn(b, journal.revision, decision=evidence, message="b"))
    assert result.source_completion_receipt_ref is None
    assert len(executor.effects) == before and not source.confirmation_seen


@pytest.mark.asyncio
async def test_duplicate_callback_no_new_revision_and_wrong_digest_never_ack() -> None:
    b, journal, _, _, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    await driver.accept_turn(turn(b, 0))
    callback = observation(b, journal, "unit-real-event-identity")
    first = await driver.accept_observation(callback, journal.revision)
    assert not isinstance(first, CapabilityRefusalReason)
    revision = journal.revision
    second = await driver.accept_observation(callback, revision)
    assert not isinstance(second, CapabilityRefusalReason) and journal.revision == revision
    divergent = await driver.accept_observation(
        callback.model_copy(update={"event_sha256": "d" * 64}), revision
    )
    assert divergent == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert journal.revision == revision and len(executor.effects) == 3


@pytest.mark.asyncio
async def test_resume_must_authenticate_original_durable_reference() -> None:
    b, journal, _, _, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    await driver.accept_turn(turn(b, 0))
    result = await driver.resume("caller-selected-uncommitted-observation", journal.revision)
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert len(executor.effects) == 3


@pytest.mark.asyncio
async def test_source_recovery_and_explicit_cross_journey_handoffs_preserve_cases() -> None:
    compra = fixture_driver(
        "journey.compras.step",
        [
            Step("C1_need", "access.resolve"),
            Step("C1_options", "offer.compose"),
            Step("C_recovery", "case.open_or_update"),
            Step("C_to_support", variant="handoff"),
            Step("C_recovery", "notice.prepare_or_send"),
            Step("C_terminal", variant="terminal"),
        ],
    )
    result = await finish_script(compra)
    assert result.source_case_refs == ("unit-source-case",)
    assert compra[3].domain_calls == ["C_to_support"]
    support = fixture_driver(
        "journey.suporte.step",
        [
            Step("S1_case", "case.open_or_update"),
            Step("S_recovery", "notice.prepare_or_send"),
            Step("S2_route", "case.open_or_update"),
            Step("S_to_compras", variant="handoff"),
            Step("S3_wait", variant="wait"),
            Step("S3_resume", "external_wait.settle"),
            Step("S4_current_confirmation", variant="manifestation", requires_confirmation=True),
            Step("S4_record_confirmation", "case.open_or_update"),
            Step("S_terminal", variant="terminal"),
        ],
    )
    result = await finish_script(support)
    assert result.source_case_refs == ("unit-source-case",)
    assert support[3].domain_calls == ["S_to_compras"]
    assert not any(a.envelope.operation_name == "acceptance.record" for a in support[4].effects)


@pytest.mark.asyncio
async def test_adapter_empty_durable_dependency_never_falls_back_to_legacy_execute() -> None:
    b, journal, _, source, _, _ = fixture_driver("journey.compras.step", COMPRA_START)
    snapshot = (await journal.observe_journey(b.journal_binding())).snapshot
    action = await source.prepare(b, snapshot, turn(b, 0))
    proof = await source.verify(b, snapshot, action, turn(b, 0), phase="entry")
    authority = _fixture_effect_authority(b, action, proof)
    adapter = CapabilityServiceExecutionAdapter(CapabilityService())
    assert (
        await adapter.execute(b, action, effect_authority=authority, expected_journal_revision=0)
        == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )
    assert await adapter.observe(b, "unit-command") == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert (
        await adapter.reconcile(b, "unit-command", expected_journal_revision=0)
        == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )


@pytest.mark.asyncio
async def test_cancel_after_fence_keeps_original_command_and_propagates_cancellation() -> None:
    b, journal, _, _, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    executor.uncertain_at = 0
    original_execute = executor.execute

    async def cancelled(binding, action, *, effect_authority, expected_journal_revision):
        await original_execute(
            binding,
            action,
            effect_authority=effect_authority,
            expected_journal_revision=expected_journal_revision,
        )
        raise asyncio.CancelledError("unit-cancelled-after-fence")

    executor.execute = cancelled
    with pytest.raises(asyncio.CancelledError, match="unit-cancelled-after-fence"):
        await driver.accept_turn(turn(b, 0))
    assert journal.commands["unit-command-0"].technical_state.value == "UNCERTAIN"
    original = executor.effects[0].envelope
    await driver.accept_turn(turn(b, journal.revision))
    assert len(executor.effects) == 1 and executor.effects[0].envelope == original


@pytest.mark.asyncio
async def test_extra_callback_output_refused_before_boundary_ingestion() -> None:
    b, journal, _, _, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    await driver.accept_turn(turn(b, 0))
    original = observation(b, journal, "unit-source-authenticated-event")
    refused = await driver.accept_observation(
        original.model_copy(update={"clinical_body": "SYNTHETIC_CLINICAL_BODY_90210"}), journal.revision
    )
    assert refused == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert not journal.events and len(executor.effects) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["handoff", "wait", "manifestation", "terminal"])
async def test_non_operation_variant_cannot_substitute_for_an_unadmitted_cursor(variant: str) -> None:
    b, journal, _, source, executor, driver = fixture_driver(
        "journey.compras.step", [Step("C1_need", variant=variant)]
    )
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert not source.domain_calls and not executor.effects
    assert not journal.commands and not journal.outboxes and not journal.waits


@pytest.mark.asyncio
async def test_original_entry_proof_expiring_during_control_blocks_source_effect() -> None:
    b, journal, ingress, oracle, execution, driver = fixture_driver(
        "journey.compras.step", [Step("C1_need", "access.resolve"), Step("C1_options", "offer.compose")]
    )
    ticks = [NOW]
    driver.clock = lambda: ticks[0]
    original_verify, original_control = oracle.verify, ingress.verify
    admitted = False

    async def verify(*args, phase, **kwargs):
        nonlocal admitted
        proof = await original_verify(*args, phase=phase, **kwargs)
        if phase == "entry":
            admitted = True
            return proof.model_copy(update={"valid_until": NOW + timedelta(seconds=1)})
        return proof

    async def control(*args, **kwargs):
        value = await original_control(*args, **kwargs)
        if admitted:
            ticks[0] = NOW + timedelta(seconds=2)
        return value  # Current ingress does not extend transition authority.

    oracle.verify, ingress.verify = verify, control
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert execution.effects == [] and journal.commands == {}
    assert result.verified_transition_ref is None and result.source_completion_receipt_ref is None


@pytest.mark.asyncio
async def test_terminal_proof_expiring_during_source_case_lookup_never_discloses_completion() -> None:
    parts = fixture_driver(
        "journey.compras.step",
        COMPRA_START
        + [
            Step("C3_enrollment", "enrollment.request"),
            Step("C4_fulfillment", "fulfillment.observe"),
            Step("C_terminal", variant="terminal"),
        ],
    )
    b, journal, _, oracle, _, driver = parts
    ticks = [NOW]
    driver.clock = lambda: ticks[0]
    original_verify, original_cases = oracle.verify, oracle.source_case_refs
    terminal_seen = False

    async def verify(*args, phase, **kwargs):
        nonlocal terminal_seen
        proof = await original_verify(*args, phase=phase, **kwargs)
        if args[2].cursor_ref == "C_terminal" and phase == "result":
            terminal_seen = True
            return proof.model_copy(update={"valid_until": NOW + timedelta(seconds=1)})
        return proof

    async def cases(*args, **kwargs):
        value = await original_cases(*args, **kwargs)
        if terminal_seen:
            ticks[0] = NOW + timedelta(seconds=2)
        return value

    oracle.verify, oracle.source_case_refs = verify, cases
    result = await driver.accept_turn(turn(b, journal.revision))
    for i in range(12):
        if terminal_seen:
            break
        step = oracle.steps[oracle.index()]
        if step.variant == "wait" and f"unit-wait-{oracle.index()}" in journal.waits:
            event = f"unit-expiry-event-{i}"
            await driver.accept_observation(observation(b, journal, event), journal.revision)
            result = await driver.resume(event, journal.revision)
        else:
            result = await driver.accept_turn(
                turn(b, journal.revision, decision="unit-current-confirmation", message="b")
            )
    assert terminal_seen
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert result.source_completion_receipt_ref is None and result.verified_transition_ref is None


@pytest.mark.asyncio
async def test_wait_preparation_await_cannot_extend_original_transition_proof() -> None:
    b, journal, _, oracle, execution, driver = fixture_driver("journey.compras.step", COMPRA_START)
    ticks = [NOW]
    driver.clock = lambda: ticks[0]
    original_verify, original_prepare = oracle.verify, oracle.prepare_wait

    async def verify(*args, phase, **kwargs):
        proof = await original_verify(*args, phase=phase, **kwargs)
        if args[2].cursor_ref == "C2_wait_manifestation":
            return proof.model_copy(update={"valid_until": NOW + timedelta(seconds=1)})
        return proof

    async def prepare(*args, **kwargs):
        descriptor = await original_prepare(*args, **kwargs)
        ticks[0] = NOW + timedelta(seconds=2)
        return descriptor

    oracle.verify, oracle.prepare_wait = verify, prepare
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert len(execution.effects) == 3 and not journal.waits
    assert result.source_completion_receipt_ref is None


def _fixture_effect_authority(b, action, proof):
    from maezo.gateway.capabilities.journeys.contracts import action_digest, parse_effect_authority
    from maezo.gateway.capabilities.models import request_digest

    from .helpers import MEMBERS

    return parse_effect_authority(
        {
            "schema_version": "v21-journey-effect-authority.proposed.v1",
            "journey_binding": b,
            "action": action,
            "original_transition": proof,
            "request_sha256": request_digest(action.envelope, action.request),
            "action_sha256": action_digest(action),
        },
        MEMBERS,
    )
