"""Full candidate flows with UNIT source/authority/journal doubles only.

No source publication, real receipts, PostgreSQL durability, AUTH engine or
operational journey acceptance is claimed by these scripted mechanical tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.administrative.journey import compras_journey, suporte_journey
from maezo.gateway.capabilities.admission import AdmissionBinding, VerifiedSourceResult
from maezo.gateway.capabilities.durability.models import (
    CommandTechnicalState,
    VerifiedInboxObservation,
    VerifiedResultObservation,
)
from maezo.gateway.capabilities.journeys.domain_boundary import ExistingDomainEffectBoundary
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from tests.unit.gateway.capabilities.journeys.helpers import (
    MEMBERS,
    NOW,
    Step,
    UnitExecution,
    UnitIngress,
    UnitJournal,
    UnitJourneyEffectAuthority,
    UnitNativeAuthority,
    UnitSourceOracle,
    binding,
    turn,
)


def fixture_driver(task: str, steps: list[Step]) -> tuple[Any, ...]:
    b = binding(task)
    journal = UnitJournal(b)
    ingress = UnitIngress(b)
    oracle = UnitSourceOracle(b, journal, steps)
    executor = UnitExecution(journal, ingress)
    driver = JourneyDriver(
        b,
        journal=journal,
        preparation=oracle,
        transitions=oracle,
        currentness=ingress,
        execution=executor,
        domain_handoff=ExistingDomainEffectBoundary(
            clock=lambda: driver.clock(),
            transition_authority=UnitJourneyEffectAuthority(ingress, lambda: driver.clock()),
            native_authority=UnitNativeAuthority(ingress, lambda: driver.clock()),
            target=oracle,
        ),
        memberships=MEMBERS,
        clock=lambda: NOW,
    )
    executor.clock = lambda: driver.clock()
    oracle.clock = lambda: driver.clock()
    oracle.ingress = ingress
    factory = compras_journey if task == "journey.compras.step" else suporte_journey
    assert factory(b, driver) is driver
    return b, journal, ingress, oracle, executor, driver


def observation(b: Any, journal: UnitJournal, event: str) -> VerifiedInboxObservation:
    """UNIT callback record; the UnitExecution spy authenticates its registered event."""
    snapshot = list(journal.commands.values())[-1]
    admission = AdmissionBinding(
        principal_ref=b.principal_ref,
        task_ref=b.task_ref,
        tenant_ref=b.tenant_ref,
        legal_entity_ref=b.legal_entity_ref,
        purpose_ref="unit-purpose",
        operation_name="external_wait.settle",
        schema_version="v21-capabilities.proposed.v1",
        contract_revision="unit-contract",
        source_authority_ref="unit-source",
        policy_revision="unit-policy",
        data_classification="unit-administrative",
        autonomy_action="unit-action",
        security_zone="general",
    )
    source_result = VerifiedSourceResult(
        binding=admission,
        request_sha256=snapshot.handle.request_sha256,
        result_sha256="b" * 64,
        authorization_ref="unit-authorization",
        source_receipt_ref="unit-callback-receipt",
        source_revision_ref="unit-opaque-callback-revision",
        currentness_ref="unit-currentness",
        checked_at=NOW,
        valid_until=NOW.replace(year=2027),
    )
    verified = VerifiedResultObservation(
        source_result=source_result,
        result_ref="unit-protected-callback-result",
        result_sha256="b" * 64,
        observer_binding_ref="unit-observer",
        provenance_ref="unit-provenance",
    )
    return VerifiedInboxObservation(
        event_ref=event,
        source_authority_ref="unit-source",
        producer_ref="unit-producer",
        source_contract_revision_ref="unit-contract",
        event_sha256="c" * 64,
        handle=snapshot.handle,
        correlation_ref="unit-correlation",
        observation=verified,
    )


COMPRA_START = [
    Step("C1_need", "access.resolve"),
    Step("C1_options", "offer.compose"),
    Step("C1_present", "notice.prepare_or_send"),
    Step("C2_wait_manifestation", variant="wait"),
    Step("C2_acceptance", "acceptance.record"),
    Step("C3_route", variant="manifestation"),
]
SUPPORT_START = [
    Step("S1_case", "case.open_or_update"),
    Step("S1_ack", "notice.prepare_or_send"),
    Step("S2_route", "case.open_or_update"),
    Step("S3_resolve_or_handoff", variant="handoff"),
    Step("S3_wait", variant="wait"),
    Step("S3_resume", "external_wait.settle"),
    Step("S4_current_confirmation", variant="manifestation", requires_confirmation=True),
    Step("S4_record_confirmation", "case.open_or_update"),
    Step("S_terminal", variant="terminal"),
]


async def finish_script(parts: tuple[Any, ...]) -> Any:
    b, journal, ingress, oracle, executor, driver = parts
    result = await driver.accept_turn(turn(b, journal.revision))
    for i in range(12):
        if result.source_completion_receipt_ref:
            return result
        assert result.technical_refusal in {None, CapabilityRefusalReason.AUTHORITY_UNPROVEN}
        step = oracle.steps[oracle.index()]
        if step.variant == "wait" and f"unit-wait-{oracle.index()}" in journal.waits:
            event = f"unit-authenticated-event-{i}"
            accepted = await driver.accept_observation(observation(b, journal, event), journal.revision)
            assert not isinstance(accepted, CapabilityRefusalReason)
            result = await driver.resume(event, journal.revision)
        else:
            result = await driver.accept_turn(
                turn(b, journal.revision, decision="unit-current-confirmation", message="b")
            )
    raise AssertionError("full journey did not reach the scripted source terminal")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["enrollment", "reservation", "AUTH", "maintenance"])
async def test_compra_full_multiturn_same_driver_source_routes_wait_return_terminal(route: str) -> None:
    branch = {
        "enrollment": [
            Step("C3_enrollment", "enrollment.request"),
            Step("C3_wait", variant="wait"),
            Step("C3_reservation", "reservation.command"),
        ],
        "reservation": [Step("C3_reservation", "reservation.command"), Step("C3_wait", variant="wait")],
        "AUTH": [Step("C3_existing_AUTH", variant="handoff"), Step("C3_wait", variant="wait")],
        "maintenance": [
            Step("C3_reservation", "reservation.command"),
            Step("C4_fulfillment", "fulfillment.observe"),
            Step("C4_maintenance", "reservation.command"),
            Step("C3_wait", variant="wait"),
        ],
    }[route]
    steps = (
        COMPRA_START
        + branch
        + [Step("C4_fulfillment", "fulfillment.observe"), Step("C_terminal", variant="terminal")]
    )
    parts = fixture_driver("journey.compras.step", steps)
    outcome = await finish_script(parts)
    b, journal, _, oracle, executor, _ = parts
    assert outcome.source_completion_receipt_ref == "unit-source-completion-receipt"
    assert outcome.outbox_refs  # Source completion never implies reply transport/delivery.
    assert outcome.pending_command_refs == ()
    assert [a.envelope.operation_name for a in executor.effects][:3] == [
        "access.resolve",
        "offer.compose",
        "notice.prepare_or_send",
    ]
    assert all(type(a.envelope.expected_business_revision) is str for a in executor.effects)
    assert all(a.envelope.idempotency_key.startswith("unit-literal-key-") for a in executor.effects)
    assert "C3_route" in oracle.prepared and "C_terminal" in oracle.prepared
    if route == "AUTH":
        assert oracle.domain_calls == ["C3_existing_AUTH"]
        assert not any(a.envelope.operation_name == "enrollment.request" for a in executor.effects)
    assert journal.binding == b.journal_binding()


@pytest.mark.asyncio
async def test_support_full_case_domain_wait_resume_current_confirmation_op07_terminal() -> None:
    parts = fixture_driver("journey.suporte.step", SUPPORT_START)
    outcome = await finish_script(parts)
    _, journal, _, oracle, executor, _ = parts
    assert outcome.source_case_refs == ("unit-source-case",)
    assert outcome.source_completion_receipt_ref == "unit-source-completion-receipt"
    assert oracle.confirmation_seen
    assert oracle.domain_calls == ["S3_resolve_or_handoff"]
    operations = [a.envelope.operation_name for a in executor.effects]
    assert operations == [
        "case.open_or_update",
        "notice.prepare_or_send",
        "case.open_or_update",
        "external_wait.settle",
        "case.open_or_update",
    ]
    confirmation = executor.effects[-1].request
    assert confirmation.origin_case_or_journey_ref == "unit-source-case"
    assert "unit-current-confirmation" in confirmation.evidence_refs
    assert not hasattr(confirmation, "action")
    assert len(journal.events) == 1


@pytest.mark.asyncio
async def test_reconstruction_new_driver_does_not_restart_or_reissue_confirmed_source_commands() -> None:
    parts = fixture_driver(
        "journey.compras.step",
        COMPRA_START
        + [
            Step("C3_enrollment", "enrollment.request"),
            Step("C4_fulfillment", "fulfillment.observe"),
            Step("C_terminal", variant="terminal"),
        ],
    )
    b, journal, ingress, oracle, executor, driver = parts
    first = await driver.accept_turn(turn(b, 0))
    assert first.wait_refs and len(executor.effects) == 3
    new_driver = JourneyDriver(
        b,
        journal=journal,
        preparation=oracle,
        transitions=oracle,
        currentness=ingress,
        execution=executor,
        domain_handoff=ExistingDomainEffectBoundary(
            clock=lambda: driver.clock(),
            transition_authority=UnitJourneyEffectAuthority(ingress, lambda: driver.clock()),
            native_authority=UnitNativeAuthority(ingress, lambda: driver.clock()),
            target=oracle,
        ),
        memberships=MEMBERS,
        clock=lambda: NOW,
    )
    result = await finish_script((b, journal, ingress, oracle, executor, new_driver))
    assert result.source_completion_receipt_ref
    assert [a.envelope.operation_name for a in executor.effects].count("access.resolve") == 1
    assert [a.envelope.operation_name for a in executor.effects].count("enrollment.request") == 1


@pytest.mark.asyncio
async def test_uncertain_effect_preserves_original_command_and_only_reconciles() -> None:
    parts = fixture_driver(
        "journey.compras.step",
        COMPRA_START
        + [
            Step("C3_enrollment", "enrollment.request"),
            Step("C4_fulfillment", "fulfillment.observe"),
            Step("C_terminal", variant="terminal"),
        ],
    )
    b, journal, _, _, executor, driver = parts
    executor.uncertain_at = 0
    first = await driver.accept_turn(turn(b, 0))
    assert first.pending_command_refs == ("unit-command-0",)
    assert journal.commands["unit-command-0"].technical_state == CommandTechnicalState.UNCERTAIN
    original = executor.effects[0].envelope
    await driver.accept_turn(turn(b, journal.revision))
    assert len(executor.effects) == 1
    recovered = await driver.recover(limit=4, cursor_ref=None)
    assert recovered.pending_command_refs == ()
    assert executor.lookups == ["unit-command-0"]
    assert len(executor.effects) == 1 and executor.effects[0].envelope == original


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["health", "human", "topic_switch"])
async def test_interruption_preserves_case_protocol_waits_and_pending_outbox(control: str) -> None:
    parts = fixture_driver("journey.suporte.step", SUPPORT_START)
    b, journal, ingress, _, executor, driver = parts
    await driver.accept_turn(turn(b, 0))
    before = repr((journal.commands, journal.waits, journal.outboxes))
    count = len(executor.effects)
    ingress.control = control
    result = await driver.accept_turn(turn(b, journal.revision))
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert repr((journal.commands, journal.waits, journal.outboxes)) == before
    assert len(executor.effects) == count


@pytest.mark.asyncio
async def test_post_commit_revocation_keeps_source_fact_and_stops_next_effect_and_completion() -> None:
    parts = fixture_driver("journey.compras.step", COMPRA_START)
    b, journal, _, _, executor, driver = parts
    executor.revocation_at = 0
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert result.source_completion_receipt_ref is None
    assert journal.commands["unit-command-0"].source_receipt_ref == "unit-source-receipt-0"
    assert len(executor.effects) == 1


@pytest.mark.asyncio
async def test_optional_feedback_is_an_independent_source_qualified_queue() -> None:
    parts = fixture_driver("journey.suporte.step", SUPPORT_START)
    outcome = await finish_script(parts)
    b, journal, ingress, oracle, executor, driver = parts
    before_terminal = outcome.source_completion_receipt_ref
    oracle.steps.append(Step("S_optional_feedback", "feedback.record"))
    # Terminal classification is a protected source fact, not a journal business cursor.
    journal.lineage["unit-protected-terminal-source-fact"] = len(SUPPORT_START) - 1
    feedback = await driver.accept_turn(turn(b, journal.revision, decision="unit-current-confirmation"))
    assert executor.effects[-1].envelope.operation_name == "feedback.record"
    assert feedback.source_completion_receipt_ref is None
    assert before_terminal == "unit-source-completion-receipt"


@pytest.mark.asyncio
async def test_full_scenarios_visit_every_one_of_the_27_admitted_cursors() -> None:
    """The full-driver scripts include branches, interrupts/resume and terminal reads."""
    scenarios = [
        (
            "journey.compras.step",
            COMPRA_START
            + [
                Step("C3_enrollment", "enrollment.request"),
                Step("C3_wait", variant="wait"),
                Step("C3_reservation", "reservation.command"),
                Step("C4_fulfillment", "fulfillment.observe"),
                Step("C4_maintenance", "reservation.command"),
                Step("C3_wait", variant="wait"),
                Step("C4_fulfillment", "fulfillment.observe"),
                Step("C_terminal", variant="terminal"),
            ],
        ),
        (
            "journey.compras.step",
            COMPRA_START
            + [
                Step("C3_existing_AUTH", variant="handoff"),
                Step("C3_wait", variant="wait"),
                Step("C4_fulfillment", "fulfillment.observe"),
                Step("C_terminal", variant="terminal"),
            ],
        ),
        (
            "journey.compras.step",
            [
                Step("C1_need", "access.resolve"),
                Step("C1_options", "offer.compose"),
                Step("C_recovery", "case.open_or_update"),
                Step("C_to_support", variant="handoff"),
                Step("C_recovery", "notice.prepare_or_send"),
                Step("C_terminal", variant="terminal"),
            ],
        ),
        ("journey.suporte.step", SUPPORT_START),
        (
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
        ),
    ]
    visited: set[str] = set()
    for task, steps in scenarios:
        parts = fixture_driver(task, steps)
        await finish_script(parts)
        visited.update(parts[3].prepared)
        if task == "journey.suporte.step":
            b, journal, _, oracle, executor, driver = parts
            terminal_index = len(oracle.steps) - 1
            oracle.steps.append(Step("S_optional_feedback", "feedback.record"))
            journal.lineage["unit-protected-source-terminal"] = terminal_index
            await driver.accept_turn(turn(b, journal.revision, decision="unit-current-confirmation"))
            assert executor.effects[-1].envelope.operation_name == "feedback.record"
            visited.update(oracle.prepared)
    assert visited == {
        "C1_need",
        "C1_options",
        "C1_present",
        "C2_wait_manifestation",
        "C2_acceptance",
        "C3_route",
        "C3_enrollment",
        "C3_reservation",
        "C3_existing_AUTH",
        "C3_wait",
        "C4_fulfillment",
        "C4_maintenance",
        "C_recovery",
        "C_to_support",
        "C_terminal",
        "S1_case",
        "S1_ack",
        "S2_route",
        "S3_resolve_or_handoff",
        "S3_wait",
        "S3_resume",
        "S4_current_confirmation",
        "S4_record_confirmation",
        "S_to_compras",
        "S_recovery",
        "S_terminal",
        "S_optional_feedback",
    }
