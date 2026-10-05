"""CANDIDATE-SEC-CURRENTNESS-01 closed control carrier regressions.

Existing runtime/driver/adapter/DUR3 are exercised with explicitly synthetic UNIT
ports. No provider permission, database, engine or operational effect is claimed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from enum import StrEnum
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.lucas.administrative.runtime import (
    RUNTIME_STIMULUS_SCHEMA,
    AdministrativeJourneyRuntime,
    RuntimeTurnStimulus,
)
from maezo.gateway.capabilities.journeys.contracts import JourneyDispatchOutcome
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from tests.unit.agents.test_administrative_journeys import COMPRA_START, SUPPORT_START, fixture_driver
from tests.unit.gateway.capabilities.journeys.helpers import binding, turn
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness, run


class StaticCurrentness:
    def __init__(self, control: object) -> None:
        self.control = control
        self.calls = 0

    async def verify(self, *args: Any) -> Any:
        self.calls += 1
        return self.control


class ForeignControl(StrEnum):
    ADMINISTRATIVE = "administrative"
    PURPOSE_DENIED = "PURPOSE_DENIED"


def hostile(hooks: list[str], variant: str) -> object:
    """Separate hook log avoids reading any field from a hostile returned carrier."""
    if variant == "comparison":

        class EqualityCarrier:
            def __eq__(self, other: object) -> bool:
                hooks.append("eq")
                return True

            def __ne__(self, other: object) -> bool:
                hooks.append("ne")
                return False

            def __str__(self) -> str:
                hooks.append("str")
                return "administrative"

            def __repr__(self) -> str:
                hooks.append("repr")
                return "UNIT_PRIVATE_REPRESENTATION"

            def __hash__(self) -> int:
                hooks.append("hash")
                return 0

            def __bool__(self) -> bool:
                hooks.append("bool")
                return True

        return EqualityCarrier()
    if variant == "class_spoof":

        class ClassSpoof:
            def __getattribute__(self, name: str) -> Any:
                hooks.append("class" if name == "__class__" else "attribute")
                if name == "__class__":
                    return CapabilityRefusalReason
                raise AssertionError("hostile storage must not be inspected")

            def __ne__(self, other: object) -> bool:
                hooks.append("ne")
                return False

            def __str__(self) -> str:
                hooks.append("str")
                return "administrative"

            def __repr__(self) -> str:
                hooks.append("repr")
                return "UNIT_PRIVATE_REPRESENTATION"

        return ClassSpoof()
    if variant == "str_subclass":

        class StringControl(str):
            def __eq__(self, other: object) -> bool:
                hooks.append("eq")
                return True

            def __ne__(self, other: object) -> bool:
                hooks.append("ne")
                return False

            def __str__(self) -> str:
                hooks.append("str")
                return "administrative"

            def __repr__(self) -> str:
                hooks.append("repr")
                return "UNIT_PRIVATE_REPRESENTATION"

        return StringControl("administrative")
    raise AssertionError("unknown UNIT carrier kind")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["comparison", "class_spoof", "str_subclass"])
async def test_invalid_carrier_refuses_without_any_inspection_or_comparison_hook(variant: str) -> None:
    hooks: list[str] = []
    currentness = StaticCurrentness(hostile(hooks, variant))
    driver = JourneyDriver(binding(), currentness=currentness)
    result = await driver._control("unit-protected-observation")
    assert result is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert currentness.calls == 1 and hooks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        1,
        1.0,
        b"administrative",
        [],
        {},
        (),
        ForeignControl.ADMINISTRATIVE,
        ForeignControl.PURPOSE_DENIED,
        "",
        "ADMINISTRATIVE",
        " administrative",
        "SOURCE_UNAVAILABLE",
        "health_priority",
        "human_requested",
    ],
)
async def test_untyped_or_unknown_control_is_not_converted_to_authority(value: object) -> None:
    driver = JourneyDriver(binding(), currentness=StaticCurrentness(value))
    assert await driver._control("unit-protected-observation") is CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
@pytest.mark.parametrize("value", list(CapabilityRefusalReason))
async def test_each_genuine_refusal_member_is_preserved_by_identity(value: CapabilityRefusalReason) -> None:
    driver = JourneyDriver(binding(), currentness=StaticCurrentness(value))
    assert await driver._control("unit-protected-observation") is value


@pytest.mark.asyncio
async def test_same_type_forged_enum_carrier_is_not_a_canonical_refusal() -> None:
    forged = str.__new__(CapabilityRefusalReason, "SOURCE_UNAVAILABLE")
    object.__setattr__(forged, "_name_", "SOURCE_UNAVAILABLE")
    object.__setattr__(forged, "_value_", "SOURCE_UNAVAILABLE")
    assert (
        type(forged) is CapabilityRefusalReason and forged is not CapabilityRefusalReason.SOURCE_UNAVAILABLE
    )
    driver = JourneyDriver(binding(), currentness=StaticCurrentness(forged))
    assert await driver._control("unit-protected-observation") is CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["administrative", "health", "human", "topic_switch"])
async def test_exact_closed_controls_retain_existing_priority_semantics(control: str) -> None:
    driver = JourneyDriver(binding(), currentness=StaticCurrentness(control))
    result = await driver._control("unit-protected-observation")
    if control == "administrative":
        assert result is None
    else:
        assert result is CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
async def test_missing_currentness_is_unproven() -> None:
    assert (
        await JourneyDriver(binding())._control("unit-protected-observation")
        is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["comparison", "class_spoof", "str_subclass"])
async def test_original_security_runtime_then_driver_verification_blocks_all_effects(variant: str) -> None:
    b, journal, ingress, _, executor, driver = fixture_driver("journey.compras.step", COMPRA_START)
    hooks: list[str] = []
    carrier = hostile(hooks, variant)
    original_verify = ingress.verify
    calls = 0

    async def verify(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        # First two calls are actual runtime pre-saver/pre-drive authentication.
        # The third is the existing driver's own post-authentication gate.
        if calls == 3:
            return carrier
        return await original_verify(*args, **kwargs)

    ingress.verify = verify
    runtime = AdministrativeJourneyRuntime(
        binding=b,
        driver=driver,
        currentness=ingress,
        checkpointer=InMemorySaver(),
        pseudonymizer=Pseudonymizer(key=b"unit-closed-currentness-runtime"),
        enabled=True,
    )
    result = await runtime.accept_turn(
        RuntimeTurnStimulus(schema_version=RUNTIME_STIMULUS_SCHEMA, kind="turn", turn=turn(b, 0))
    )
    assert isinstance(result, JourneyDispatchOutcome)
    assert result.technical_refusal is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert calls >= 3 and hooks == []
    assert executor.effects == [] and journal.commands == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["comparison", "class_spoof", "str_subclass"])
async def test_original_architecture_actual_driver_adapter_dur3_path_cannot_dispatch(variant: str) -> None:
    actual_harness: Callable[[], Any] = harness
    actual_run: Callable[[Any], Awaitable[JourneyDispatchOutcome]] = run
    h = actual_harness()
    hooks: list[str] = []
    h.driver.currentness = StaticCurrentness(hostile(hooks, variant))
    result = await actual_run(h)
    assert result.technical_refusal is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.source.calls == 0 and h.source.effects == 0 and h.legacy.calls == 0
    assert hooks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["health", "human", "topic_switch"])
async def test_legitimate_interruption_preserves_accepted_case_wait_and_outbox(control: str) -> None:
    b, journal, ingress, _, executor, driver = fixture_driver("journey.suporte.step", SUPPORT_START)
    first = await driver.accept_turn(turn(b, 0))
    assert first.technical_refusal is None
    preserved = deepcopy((journal.commands, journal.waits, journal.outboxes))
    effect_count = len(executor.effects)
    ingress.control = control
    result = await driver.accept_turn(turn(b, journal.revision))
    assert result.technical_refusal is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert (journal.commands, journal.waits, journal.outboxes) == preserved
    assert len(executor.effects) == effect_count
