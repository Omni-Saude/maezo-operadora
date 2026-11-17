"""E-SF-18 / TB-E-21-a: a vendor-channel journey never reaches the self-service DS/Carta.

RN 558 art. 3, par. 1, II requires the intermediary that "presenciou o
preenchimento da Declaracao de Saude" to be identified on the Carta; how that
presence is met in a digital self-service flow is the open Q5 (juridico). Until
it is ratified the vendor channel must not carry the DS/Carta at all: the
binding allowlist (`VENDOR_OPERATIONS`) is the whole ceiling, and anything a
source prepares outside it is refused `PURPOSE_DENIED` BEFORE the driver sees it.

The negative tests inject the violation (a source preparing a DS/Carta
operation on the vendor journey) through the production `JourneyDriver` and the
production binding guard, and assert the typed refusal with zero downstream
effect. The probes are every nucleo operation outside the allowlist: no DS/Carta
operation exists in the nucleo today, which is exactly why the allowlist (not a
denylist) must keep refusing whatever a source prepares.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

import maezo
from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK
from maezo.gateway.capabilities.models import CapabilityRefusalReason, OperationName
from maezo.gateway.capabilities.vendor_journey import (
    CHANNEL_FORBIDDEN_SEAT_OPERATIONS,
    VENDOR_OPERATIONS,
    VENDOR_TASK_REF,
)
from tests.unit.agents.test_administrative_journeys import observation
from tests.unit.gateway.capabilities.journeys.helpers import Step, turn
from tests.unit.gateway.capabilities.vendor_journey.test_composition_e2e import VENDOR_TERMINAL_VIA_OP08_WAIT
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_parts

SRC = Path(maezo.__file__).resolve().parent

# What would put the Declaracao de Saude / Carta de Orientacao in front of the buyer.
_DS_CARTA_NAME = re.compile(
    r"declara|declaration|health_decl|carta|orienta|letter|agravo|self_service|autosservi",
    re.IGNORECASE,
)

# The closed nucleo vocabulary is the only thing a source can prepare: a DS/Carta operation cannot even be
# named, so the executable fence is "every nucleo operation outside the allowlist is refused on the vendor
# journey" -- a DS/Carta operation added to the vocabulary (or to the allowlist) breaks this file.
_OUTSIDE_ALLOWLIST = tuple(sorted(set(get_args(OperationName)) - VENDOR_OPERATIONS))


def test_the_vendor_allowlist_names_no_ds_or_carta_operation() -> None:
    assert VENDOR_TASK_REF == "journey.compras.step"
    assert not [name for name in VENDOR_OPERATIONS if _DS_CARTA_NAME.search(name)]
    assert not [name for name in CHANNEL_FORBIDDEN_SEAT_OPERATIONS if _DS_CARTA_NAME.search(name)]
    # The vendor binding only ever sees the nucleo topology; it carries no DS/Carta operation either.
    assert not [name for name in get_args(OperationName) if _DS_CARTA_NAME.search(name)]
    assert set(get_args(OperationName)) >= VENDOR_OPERATIONS
    stage_ops = {op for stage in STAGES_BY_TASK[VENDOR_TASK_REF].values() for op in stage.operations}
    assert not [name for name in stage_ops if _DS_CARTA_NAME.search(name)]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _OUTSIDE_ALLOWLIST)
async def test_vendor_channel_journey_cannot_reach_ds_self_service(operation: str) -> None:
    steps = [*VENDOR_TERMINAL_VIA_OP08_WAIT[:3], Step("C1_present", operation)]
    b, journal, _ingress, oracle, executor, driver = vendor_parts(steps)
    first = await driver.accept_turn(turn(b, journal.revision))
    assert first.technical_refusal is None
    await driver.accept_observation(observation(b, journal, "unit-ds-fence-event"), journal.revision)
    result = await driver.resume("unit-ds-fence-event", journal.revision)
    assert result.technical_refusal == CapabilityRefusalReason.PURPOSE_DENIED
    assert result.source_completion_receipt_ref is None
    assert result.verified_transition_ref is None
    # Only OP01 + OP02 ever ran: the DS/Carta probe never reached the executor, the journal or a handoff.
    assert [a.envelope.operation_name for a in executor.effects] == ["access.resolve", "offer.compose"]
    assert len(journal.commands) == 2
    assert not journal.outboxes
    assert oracle.domain_calls == []


def test_the_fence_is_the_vendor_binding_not_a_property_of_every_compras_journey() -> None:
    # Faixa Express sem intermediario: the plain JR1 (compras) motor is NOT wrapped by the vendor
    # seat fence, so the non-vendor lane is unchanged by this guard.
    sites = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "VendorCompositionGuard(" in path.read_text(encoding="utf-8")
    }
    assert sites == {"gateway/capabilities/vendor_journey/binding.py"}
