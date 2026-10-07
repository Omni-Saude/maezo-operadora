"""Shared UNIT doubles for the AGJ-VENDOR tests; contract-proving, never logic mocks.

The journey doubles are the SAME ``journeys`` test doubles JR1/JR2 exercise —
that identity is the reuse proof. The projection doubles prove only the port
contracts (types and refusal propagation); the projection logic under test
stays the production module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from maezo.gateway.capabilities.journeys.domain_boundary import ExistingDomainEffectBoundary
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.capabilities.vendor_journey import (
    VendorJourneyConfig,
    VendorStageReading,
    vendor_journey,
    vendor_journey_binding,
)
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
)


def vendor_config(*, enabled: bool = True) -> VendorJourneyConfig:
    return VendorJourneyConfig(
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-legal",
        environment_ref="unit-environment",
        principal_ref="unit-vendor-channel",
        data_policy_ref="unit-data-policy",
        journey_ref="unit-journey",
        topology_contract_ref="unit-topology-contract",
        topology_version_ref="unit-topology-version",
        source_transition_contract_ref="unit-transition-contract",
        enabled=enabled,
    )


def vendor_parts(steps: list[Step]) -> tuple[Any, ...]:
    """The JR4 fixture: the binding guard in front of the same JR1/JR2 doubles."""

    config = vendor_config()
    b = vendor_journey_binding(config)
    journal = UnitJournal(b)
    ingress = UnitIngress(b)
    oracle = UnitSourceOracle(b, journal, steps)
    executor = UnitExecution(journal, ingress)
    driver = vendor_journey(
        config,
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
    assert driver is not None
    executor.clock = lambda: driver.clock()
    oracle.clock = lambda: driver.clock()
    oracle.ingress = ingress
    return b, journal, ingress, oracle, executor, driver


class ScriptedOpportunityLedger:
    """Ledger port double: registered refs or None; it invents no journey."""

    def __init__(self, refs: tuple[str, ...] | None) -> None:
        self._refs = refs
        self.calls: list[str] = []

    async def resolve_journey_refs(self, tenant_ref: str, vendor_ref: str) -> tuple[str, ...] | None:
        self.calls.append(vendor_ref)
        return self._refs


class ScriptedStageSource:
    """Stage port double: one scripted núcleo reading or a typed refusal."""

    def __init__(self, reading: VendorStageReading | CapabilityRefusalReason) -> None:
        self._reading = reading
        self.calls: list[str] = []

    async def read_stage(
        self, tenant_ref: str, journey_ref: str
    ) -> VendorStageReading | CapabilityRefusalReason:
        self.calls.append(journey_ref)
        return self._reading


class ScriptedChannelAttributes:
    """Attribute port double: the raw OP14-shaped mapping under the fence."""

    def __init__(self, attributes: Mapping[str, object] | None = None) -> None:
        self._attributes = dict(attributes or {})
        self.calls: list[str] = []

    async def channel_attributes(self, tenant_ref: str, vendor_ref: str) -> Mapping[str, object]:
        self.calls.append(vendor_ref)
        return dict(self._attributes)
