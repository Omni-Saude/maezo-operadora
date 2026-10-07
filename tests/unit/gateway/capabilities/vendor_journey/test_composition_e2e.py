"""JR4 E2E over the real motor; the channel never holds an acceptance seat.

Every run below executes the production ``JourneyDriver`` with the production
binding guard in front of the SAME contract-proving doubles JR1/JR2 use. No
mock reimplements driver, port or boundary logic here.
"""

from __future__ import annotations

import pytest

from maezo.gateway.capabilities.models import CapabilityRefusalReason
from tests.unit.agents.test_administrative_journeys import finish_script, observation
from tests.unit.gateway.capabilities.journeys.helpers import Step, turn
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_parts

# OP08 as journey state: the CDC art. 49 arrependimento window waits, the núcleo
# source owns the terminal fact at C_terminal (registry section 4).
VENDOR_TERMINAL_VIA_OP08_WAIT = [
    Step("C1_need", "access.resolve"),
    Step("C1_options", "offer.compose"),
    Step("C_recovery", variant="wait"),
    Step("C_terminal", variant="terminal"),
]

# OP08 additionally as a settled núcleo command on the recovery branch.
VENDOR_OP08_SETTLE_THEN_TERMINAL = [
    Step("C1_need", "access.resolve"),
    Step("C1_options", "offer.compose"),
    Step("C_recovery", variant="wait"),
    Step("C_recovery", "external_wait.settle"),
    Step("C_terminal", variant="terminal"),
]


@pytest.mark.asyncio
async def test_vendor_journey_completes_through_op08_wait_state_to_terminal() -> None:
    parts = vendor_parts(list(VENDOR_TERMINAL_VIA_OP08_WAIT))
    outcome = await finish_script(parts)
    b, journal, _, oracle, executor, _ = parts
    assert outcome.source_completion_receipt_ref == "unit-source-completion-receipt"
    # The v1 vendor composition prepares no return intent (no manifestation seat,
    # no reply transport claim): source completion never implies reply delivery.
    assert outcome.outbox_refs == ()
    assert [a.envelope.operation_name for a in executor.effects] == [
        "access.resolve",
        "offer.compose",
    ]
    assert "acceptance.record" not in [a.envelope.operation_name for a in executor.effects]
    assert list(journal.waits) == ["unit-wait-2"]
    assert "C_terminal" in oracle.prepared and "C_recovery" in oracle.prepared


@pytest.mark.asyncio
async def test_vendor_op08_settled_command_on_recovery_branch_reaches_terminal() -> None:
    parts = vendor_parts(list(VENDOR_OP08_SETTLE_THEN_TERMINAL))
    outcome = await finish_script(parts)
    _, journal, _, _, executor, _ = parts
    assert outcome.source_completion_receipt_ref == "unit-source-completion-receipt"
    assert [a.envelope.operation_name for a in executor.effects] == [
        "access.resolve",
        "offer.compose",
        "external_wait.settle",
    ]
    assert "C_terminal" in parts[3].prepared
    assert journal.binding.tenant_ref == "unit-tenant"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cursor", "variant", "operation"),
    [
        ("C2_acceptance", "capability", "acceptance.record"),
        ("C3_enrollment", "capability", "enrollment.request"),
        ("C_recovery", "handoff", None),
        ("C3_route", "manifestation", None),
    ],
)
async def test_channel_seats_are_refused_with_zero_downstream_effect(
    cursor: str, variant: str, operation: str | None
) -> None:
    steps = list(VENDOR_TERMINAL_VIA_OP08_WAIT[:3]) + [
        Step(cursor, operation) if variant == "capability" else Step(cursor, variant=variant)
    ]
    b, journal, ingress, oracle, executor, driver = vendor_parts(steps)
    first = await driver.accept_turn(turn(b, journal.revision))
    assert first.technical_refusal is None
    await driver.accept_observation(observation(b, journal, "unit-vendor-seat-event"), journal.revision)
    result = await driver.resume("unit-vendor-seat-event", journal.revision)
    assert result.technical_refusal == CapabilityRefusalReason.PURPOSE_DENIED
    assert result.source_completion_receipt_ref is None
    assert result.verified_transition_ref is None
    assert len(executor.effects) == 2  # only OP01 + OP02 ever ran
    assert [a.envelope.operation_name for a in executor.effects] == ["access.resolve", "offer.compose"]
    assert len(journal.commands) == 2 and not journal.outboxes
    assert oracle.domain_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bad_successor", "expired"])
async def test_transition_outside_source_authority_is_refused_before_any_effect(failure: str) -> None:
    b, journal, _, oracle, executor, driver = vendor_parts(list(VENDOR_TERMINAL_VIA_OP08_WAIT))
    if failure == "bad_successor":
        oracle.bad_successor = True
    else:
        oracle.expired = True
    result = await driver.accept_turn(turn(b, 0))
    if failure == "expired":
        assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    else:
        assert result.technical_refusal is not None
    assert result.source_completion_receipt_ref is None
    assert executor.effects == [] and not journal.commands


@pytest.mark.asyncio
async def test_revoked_currentness_stops_every_new_vendor_effect() -> None:
    b, journal, ingress, _, executor, driver = vendor_parts(list(VENDOR_TERMINAL_VIA_OP08_WAIT))
    ingress.revoked = True
    result = await driver.accept_turn(turn(b, 0))
    assert result.technical_refusal == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert result.source_completion_receipt_ref is None
    assert executor.effects == [] and not journal.commands and not journal.waits


@pytest.mark.asyncio
async def test_stale_current_turn_is_refused_without_touching_the_journal() -> None:
    b, journal, _, _, executor, driver = vendor_parts(list(VENDOR_TERMINAL_VIA_OP08_WAIT))
    stale = turn(b, 0).model_copy(update={"current_message_ref": "hk1_" + "b" * 64})
    result = await driver.accept_turn(stale)
    assert result.technical_refusal is not None
    assert executor.effects == [] and not journal.commands
