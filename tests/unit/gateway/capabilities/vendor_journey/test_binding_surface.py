"""AGJ-VENDOR surface fence: default-off, núcleo identity, composition contract pins.

The flag is the rollback lever: off means no driver object exists at all. The
binding rides the exact núcleo task/topology record types — nothing vendor-local
is a second contract.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyContractError
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.vendor_journey import (
    CHANNEL_FORBIDDEN_SEAT_OPERATIONS,
    VENDOR_OPERATIONS,
    VENDOR_TASK_REF,
    VendorJourneyConfig,
    vendor_journey,
    vendor_journey_binding,
)
from tests.unit.gateway.capabilities.journeys.helpers import (
    MEMBERS,
    Step,
    UnitExecution,
    UnitIngress,
    UnitJournal,
    UnitSourceOracle,
)
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_config

_IDENTITY_FIELDS = (
    "tenant_ref",
    "legal_entity_ref",
    "environment_ref",
    "principal_ref",
    "data_policy_ref",
    "journey_ref",
    "topology_contract_ref",
    "topology_version_ref",
    "source_transition_contract_ref",
)


def test_composition_contract_is_exactly_the_registry_intent_set() -> None:
    assert VENDOR_TASK_REF == "journey.compras.step"
    assert frozenset({"access.resolve", "offer.compose", "external_wait.settle"}) == VENDOR_OPERATIONS
    assert frozenset({"acceptance.record", "enrollment.request"}) == CHANNEL_FORBIDDEN_SEAT_OPERATIONS
    assert CHANNEL_FORBIDDEN_SEAT_OPERATIONS.isdisjoint(VENDOR_OPERATIONS)


def test_binding_is_the_exact_nucleo_record_with_the_channel_principal() -> None:
    b = vendor_journey_binding(vendor_config())
    assert type(b) is JourneyBinding
    assert b.task_ref == VENDOR_TASK_REF
    assert b.principal_ref == "unit-vendor-channel"
    assert b.journey_ref == "unit-journey"


def test_flag_defaults_off_and_off_binding_is_disabled() -> None:
    config = VendorJourneyConfig(
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-legal",
        environment_ref="unit-environment",
        principal_ref="unit-vendor-channel",
        data_policy_ref="unit-data-policy",
        journey_ref="unit-journey",
        topology_contract_ref="unit-topology-contract",
        topology_version_ref="unit-topology-version",
        source_transition_contract_ref="unit-transition-contract",
    )
    assert config.enabled is False
    assert vendor_journey_binding(config).enabled is False


def test_flag_off_builds_zero_vendor_capability_even_with_every_port_supplied() -> None:
    config = vendor_config(enabled=False)
    b = vendor_journey_binding(config)
    journal, ingress = UnitJournal(b), UnitIngress(b)
    oracle = UnitSourceOracle(b, journal, [Step("C1_need", "access.resolve")])
    executor = UnitExecution(journal, ingress)
    assert (
        vendor_journey(
            config,
            journal=journal,
            preparation=oracle,
            transitions=oracle,
            currentness=ingress,
            execution=executor,
            memberships=MEMBERS,
        )
        is None
    )


def test_flag_on_without_qualified_ports_is_a_construction_error() -> None:
    with pytest.raises(JourneyContractError):
        vendor_journey(vendor_config())
    enabled = vendor_config()
    journal = UnitJournal(vendor_journey_binding(enabled))
    with pytest.raises(JourneyContractError):
        vendor_journey(enabled, journal=journal)


def test_flag_on_builds_the_one_motor_with_the_seat_fence_in_front() -> None:
    config = vendor_config()
    b = vendor_journey_binding(config)
    journal, ingress = UnitJournal(b), UnitIngress(b)
    oracle = UnitSourceOracle(b, journal, [Step("C1_need", "access.resolve")])
    driver = vendor_journey(
        config,
        journal=journal,
        preparation=oracle,
        transitions=oracle,
        currentness=ingress,
        memberships=MEMBERS,
    )
    assert type(driver) is JourneyDriver
    assert driver is not None
    assert driver.binding == b and driver.binding.enabled is True
    assert driver.preparation is not None
    assert type(driver.preparation).__name__ == "VendorCompositionGuard"


@pytest.mark.parametrize("field", _IDENTITY_FIELDS)
def test_blank_identity_field_is_refused_at_construction(field: str) -> None:
    with pytest.raises(JourneyContractError):
        replace(vendor_config(), **{field: "   "})


def test_non_bool_flag_is_refused_at_construction() -> None:
    with pytest.raises(JourneyContractError):
        replace(vendor_config(), enabled="yes")  # type: ignore[arg-type]
