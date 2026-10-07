"""Reuse proof (PW5/W3 pattern) and the grep-countable non-duplication census.

JR1 (compras), JR2 (suporte) and JR4 (AGJ-VENDOR) complete in THIS ONE test over
the same ``JourneyDriver`` class, the same núcleo topology object and the same
source-receipt vocabulary — wrapper/fork/copy would be diluted reuse. The census
pins one definition site per motor symbol, zero subclasses of the motor types,
and zero vendor coupling to OP16/submissions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import maezo
from maezo.gateway.capabilities.journeys.contracts import JourneyBinding
from maezo.gateway.capabilities.journeys.domain_boundary import ExistingDomainEffectBoundary
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.journeys.returns import JourneyReplyBoundary
from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK, JourneyStage
from maezo.gateway.capabilities.vendor_journey import (
    VENDOR_TASK_REF,
    vendor_journey_binding,
)
from tests.unit.agents.test_administrative_journeys import (
    COMPRA_START,
    SUPPORT_START,
    finish_script,
    fixture_driver,
)
from tests.unit.gateway.capabilities.journeys.helpers import Step
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_config, vendor_parts

SRC = Path(maezo.__file__).resolve().parent

# symbol → the single núcleo file allowed to define it (relative to src/maezo).
_DEFINITION_SITES = {
    "class JourneyDriver": "gateway/capabilities/journeys/driver.py",
    "class JourneyStage": "gateway/capabilities/journeys/topology.py",
    "class JourneyBinding": "gateway/capabilities/journeys/contracts.py",
    "class CurrentJourneyTurn": "gateway/capabilities/journeys/contracts.py",
    "class VerifiedJourneyContinuation": "gateway/capabilities/journeys/contracts.py",
    "class JourneyPreparationPort": "gateway/capabilities/journeys/contracts.py",
    "class JourneyTransitionAuthorityPort": "gateway/capabilities/journeys/contracts.py",
    "class ExistingDomainHandoffPort": "gateway/capabilities/journeys/contracts.py",
    "class JourneyEffectAuthorityPort": "gateway/capabilities/journeys/contracts.py",
    "class ExistingDomainEffectBoundary": "gateway/capabilities/journeys/domain_boundary.py",
    "class JourneyReplyBoundary": "gateway/capabilities/journeys/returns.py",
    "class ContractPublicationLedger": "gateway/capabilities/publication.py",
}


def test_each_motor_symbol_has_exactly_one_definition_site() -> None:
    for symbol, expected in _DEFINITION_SITES.items():
        sites = {
            path.relative_to(SRC).as_posix()
            for path in SRC.rglob("*.py")
            if symbol in path.read_text(encoding="utf-8")
        }
        assert sites == {expected}, symbol


def test_no_second_topology_cursor_map_or_driver_exists() -> None:
    stage_constructors = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "JourneyStage(" in path.read_text(encoding="utf-8")
    }
    assert stage_constructors == {"gateway/capabilities/journeys/topology.py"}
    root_cursors = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if '"C1_need"' in path.read_text(encoding="utf-8")
    }
    assert root_cursors == {
        "gateway/capabilities/journeys/topology.py",
        "gateway/capabilities/journeys/driver.py",
    }


def test_motor_types_have_zero_subclasses_anywhere() -> None:
    assert JourneyDriver.__subclasses__() == []
    assert JourneyStage.__subclasses__() == []
    assert JourneyBinding.__subclasses__() == []
    assert ExistingDomainEffectBoundary.__subclasses__() == []
    assert JourneyReplyBoundary.__subclasses__() == []


def test_vendor_package_never_couples_to_op16_or_submissions() -> None:
    package = SRC / "gateway" / "capabilities" / "vendor_journey"
    files = sorted(package.glob("*.py"))
    assert {path.name for path in files} >= {"binding.py", "projection.py"}
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "vendor_submissions" not in text, path
        assert "OP16" not in text, path


def test_jr4_binding_rides_the_published_nucleo_topology_object() -> None:
    config = vendor_config()
    binding = vendor_journey_binding(config)
    assert binding.task_ref == VENDOR_TASK_REF
    assert binding.task_ref in STAGES_BY_TASK
    assert STAGES_BY_TASK[binding.task_ref]["C_terminal"].operations == frozenset(
        {"notice.prepare_or_send", "milestone.publish"}
    )


@pytest.mark.asyncio
async def test_jr1_jr2_and_jr4_complete_over_the_same_motor_and_contract() -> None:
    jr1 = fixture_driver(
        "journey.compras.step",
        COMPRA_START
        + [
            Step("C3_enrollment", "enrollment.request"),
            Step("C3_wait", variant="wait"),
            Step("C4_fulfillment", "fulfillment.observe"),
            Step("C_terminal", variant="terminal"),
        ],
    )
    jr2 = fixture_driver("journey.suporte.step", SUPPORT_START)
    jr4 = vendor_parts(
        [
            Step("C1_need", "access.resolve"),
            Step("C1_options", "offer.compose"),
            Step("C_recovery", variant="wait"),
            Step("C_terminal", variant="terminal"),
        ]
    )
    outcome_jr1 = await finish_script(jr1)
    outcome_jr2 = await finish_script(jr2)
    outcome_jr4 = await finish_script(jr4)
    # One source-receipt vocabulary: the same núcleo completion contract.
    receipt = "unit-source-completion-receipt"
    assert (
        outcome_jr1.source_completion_receipt_ref
        == outcome_jr2.source_completion_receipt_ref
        == outcome_jr4.source_completion_receipt_ref
        == receipt
    )
    # One motor class, one binding record type, one topology object.
    assert type(jr1[5]) is type(jr2[5]) is type(jr4[5]) is JourneyDriver
    assert type(jr1[0]) is type(jr2[0]) is type(jr4[0]) is JourneyBinding
    # JR4 rides the exact núcleo purchase topology JR1 rides — never a fork.
    assert jr4[0].task_ref == jr1[0].task_ref == VENDOR_TASK_REF
    assert jr4[3].steps[0].cursor == jr1[3].steps[0].cursor == "C1_need"
    # The vendor composition consumed only the registry intent set; JR1 holds the
    # acceptance seat (núcleo), JR4 never does.
    assert [a.envelope.operation_name for a in jr4[4].effects] == ["access.resolve", "offer.compose"]
    assert any(a.envelope.operation_name == "acceptance.record" for a in jr1[4].effects)
    assert not any(a.envelope.operation_name == "acceptance.record" for a in jr4[4].effects)
