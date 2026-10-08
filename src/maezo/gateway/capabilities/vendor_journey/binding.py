"""AGJ-VENDOR: the vendor journey JR4 bound over the existing journey motor.

OP-REGISTRY-VENDOR-V1 section 4 is the contract of this module: the driver
(``journeys/driver.py#JourneyDriver``), the four contract ports, both effect and
return boundaries and the núcleo topology are consumed by reference and are
never redeclared, subclassed, cursor-cached or re-versioned here. JR4 has no
business cursor/cache/rule/provider of its own.

The channel is a portal audience with NO acceptance seat (Q13: cliente aceita /
fonte matricula / authority resolve stay with their owners). The vendor
composition therefore consumes exactly OP01 ``access.resolve``, OP02
``offer.compose`` (composition consumed, never duplicated) and OP08
``external_wait.settle`` (CDC art. 49 arrependimento as state towards
``C_terminal``/``C_recovery``); the acceptance seat (OP03) and the enrollment
route (OP04) are refused at this binding before the driver ever sees them.

The whole surface is default-off: ``VendorJourneyConfig.enabled`` defaults to
``False`` and the flag is the rollback lever — flag off means JR4 is absent
from the surface while the motor and the JR1/JR2 consumers stay untouched.
No ingress, worker, route, store or DMN is created here; trusted composition
still injects every source/authority port and the admission boundary keeps
verifying each invocation independently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from maezo.gateway.capabilities.durability.models import (
    JourneySnapshot,
    OutboxDescriptor,
    WaitDescriptor,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from maezo.gateway.capabilities.journeys.contracts import (
    GatewayExecutionAdapter,
    JourneyBinding,
    JourneyContractError,
    JourneyCurrentnessPort,
    JourneyPreparationPort,
    JourneyReturnRecoveryPort,
    JourneyStimulus,
    JourneyTransitionAuthorityPort,
    PreparedAwaitObservationAction,
    PreparedCapabilityAction,
    PreparedCurrentManifestationAction,
    PreparedDomainHandoffAction,
    PreparedJourneyAction,
    VerifiedJourneyContinuation,
)
from maezo.gateway.capabilities.journeys.domain_boundary import ExistingDomainEffectBoundary
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.models import CapabilityRefusalReason, DeclaredMemberships

VENDOR_JOURNEY_BINDING_ID: Final = "AGJ-VENDOR"
# The núcleo purchase topology is consumed as it is; extending JourneyStage or
# declaring a vendor cursor map would be a D4 núcleo contract change (forbidden).
VENDOR_TASK_REF: Final = "journey.compras.step"
# The only núcleo intents the vendor composition consumes (registry section 4).
VENDOR_OPERATIONS: Final = frozenset({"access.resolve", "offer.compose", "external_wait.settle"})
# The seats the channel does not hold: OP03 acceptance and OP04 enrollment route.
CHANNEL_FORBIDDEN_SEAT_OPERATIONS: Final = frozenset({"acceptance.record", "enrollment.request"})

_IDENTITY_FIELDS: Final = (
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


@dataclass(frozen=True, slots=True)
class VendorJourneyConfig:
    """Trusted-composition identity of one vendor journey; default-off by construction.

    Every reference is opaque and explicitly supplied — the binding never infers
    a tenant, principal, policy or topology version. ``enabled`` is the flag:
    ``False`` (the default) keeps JR4 absent from the surface.
    """

    tenant_ref: str
    legal_entity_ref: str
    environment_ref: str
    principal_ref: str
    data_policy_ref: str
    journey_ref: str
    topology_contract_ref: str
    topology_version_ref: str
    source_transition_contract_ref: str
    enabled: bool = False

    def __post_init__(self) -> None:
        for name in _IDENTITY_FIELDS:
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise JourneyContractError()
        if type(self.enabled) is not bool:
            raise JourneyContractError()


def vendor_journey_binding(config: VendorJourneyConfig) -> JourneyBinding:
    """Project the vendor identity onto the exact núcleo binding record type."""

    return JourneyBinding(
        task_ref=VENDOR_TASK_REF,
        tenant_ref=config.tenant_ref,
        legal_entity_ref=config.legal_entity_ref,
        topology_contract_ref=config.topology_contract_ref,
        topology_version_ref=config.topology_version_ref,
        source_transition_contract_ref=config.source_transition_contract_ref,
        enabled=config.enabled,
        environment_ref=config.environment_ref,
        principal_ref=config.principal_ref,
        data_policy_ref=config.data_policy_ref,
        journey_ref=config.journey_ref,
    )


class VendorCompositionGuard:
    """Composition fence around the injected source-owned preparation.

    The wrapper never replaces or re-derives the source's decision: whatever the
    inner port prepares is forwarded unchanged unless it would walk the channel
    through a seat it does not hold. The driver remains the sole transition and
    effect authority; OP08 wait/settle states and the núcleo-owned terminal are
    the only non-operation shapes that pass.
    """

    def __init__(self, inner: JourneyPreparationPort) -> None:
        self._inner = inner

    async def prepare(
        self, binding: JourneyBinding, snapshot: JourneySnapshot, current: JourneyStimulus
    ) -> PreparedJourneyAction | CapabilityRefusalReason:
        action = await self._inner.prepare(binding, snapshot, current)
        if isinstance(action, CapabilityRefusalReason):
            return action
        if isinstance(action, PreparedDomainHandoffAction | PreparedCurrentManifestationAction):
            # No handoff out of the núcleo (no NIP, no assistential resource) and
            # no manifestation/acceptance seat: OP03/OP04 are not vendor operations.
            return CapabilityRefusalReason.PURPOSE_DENIED
        if isinstance(action, PreparedCapabilityAction) and (
            action.envelope.operation_name not in VENDOR_OPERATIONS
        ):
            return CapabilityRefusalReason.PURPOSE_DENIED
        return action

    async def prepare_wait(
        self, binding: JourneyBinding, snapshot: JourneySnapshot, action: PreparedAwaitObservationAction
    ) -> WaitDescriptor | CapabilityRefusalReason:
        return await self._inner.prepare_wait(binding, snapshot, action)

    async def prepare_continuation(
        self,
        binding: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedJourneyAction,
        continuation: VerifiedJourneyContinuation,
    ) -> OutboxDescriptor | CapabilityRefusalReason:
        return await self._inner.prepare_continuation(binding, snapshot, action, continuation)

    async def source_case_refs(
        self, binding: JourneyBinding, snapshot: JourneySnapshot
    ) -> tuple[str, ...] | CapabilityRefusalReason:
        return await self._inner.source_case_refs(binding, snapshot)


def vendor_journey(
    config: VendorJourneyConfig,
    *,
    journal: DurabilityJournalPort | None = None,
    preparation: JourneyPreparationPort | None = None,
    transitions: JourneyTransitionAuthorityPort | None = None,
    currentness: JourneyCurrentnessPort | None = None,
    execution: GatewayExecutionAdapter | None = None,
    domain_handoff: ExistingDomainEffectBoundary | None = None,
    returns: JourneyReturnRecoveryPort | None = None,
    memberships: DeclaredMemberships | None = None,
    clock: Callable[[], datetime] | None = None,
) -> JourneyDriver | None:
    """Register JR4 on the existing motor — or nothing, while the flag is off.

    Flag off returns ``None``: JR4 is absent from the surface and the motor is
    never constructed (rollback without touching the engine). Flag on requires
    the four qualified ports from trusted composition; the injected preparation
    is wrapped in the vendor seat fence, and every other port is passed to the
    one and only ``JourneyDriver`` by reference.
    """

    if not config.enabled:
        return None
    if journal is None or preparation is None or transitions is None or currentness is None:
        raise JourneyContractError()
    return JourneyDriver(
        vendor_journey_binding(config),
        journal=journal,
        preparation=VendorCompositionGuard(preparation),
        transitions=transitions,
        currentness=currentness,
        execution=execution,
        domain_handoff=domain_handoff,
        returns=returns,
        memberships=memberships,
        clock=clock,
    )
