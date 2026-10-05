"""Synthetic R5 UNIT controls; no native/provider or human qualification claim."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import pytest

from maezo.gateway.capabilities.durability.models import (
    JourneySnapshot,
    OutboxDescriptor,
    OutboxTechnicalKind,
)
from maezo.gateway.capabilities.journeys.contracts import (
    JourneyBinding,
    JourneyEffectAuthority,
    JourneyInvocationCheckpoint,
    OriginalDomainInvocationAuthority,
    PreparedDomainHandoffAction,
    VerifiedJourneyContinuation,
    VerifiedJourneyEffectCurrentness,
    action_digest,
    effect_authority_digest,
)
from maezo.gateway.capabilities.journeys.domain_boundary import ExistingDomainEffectBoundary
from maezo.gateway.capabilities.models import CapabilityRefusalReason

from .helpers import NOW, binding

DENIED = CapabilityRefusalReason.AUTHORITY_UNPROVEN


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


def original() -> tuple[JourneyBinding, JourneySnapshot, PreparedDomainHandoffAction, JourneyEffectAuthority]:
    b = binding()
    snapshot = JourneySnapshot(
        binding=b.journal_binding(), journal_revision=0, command_refs=(), wait_refs=(), outbox_refs=()
    )
    common = {
        key: getattr(b, key)
        for key in (
            "task_ref",
            "tenant_ref",
            "legal_entity_ref",
            "journey_ref",
            "topology_contract_ref",
            "topology_version_ref",
        )
    }
    action = PreparedDomainHandoffAction(
        **common,
        schema_version="v21-journey-action.proposed.v1",
        cursor_ref="C_to_support",
        predecessor_command_refs=(),
        predecessor_source_receipt_refs=(),
        preparation_authority_ref="unit-preparation",
        transition_contract_ref=b.source_transition_contract_ref,
        route_authority_ref="unit-existing-route",
        source_case_ref="unit-source-case",
        existing_target_binding_ref="unit-native-target",
        minimum_material_refs=("unit-material",),
        expected_return_binding_ref="unit-native-return",
    )
    proof = VerifiedJourneyContinuation(
        **common,
        schema_version="v21-journey-continuation.proposed.v1",
        from_cursor_ref=action.cursor_ref,
        successor_cursor_ref=None,
        predecessor_command_refs=(),
        source_receipt_refs=(),
        source_decision_ref="unit-original-decision",
        source_observation_ref="unit-original-observation",
        transition_contract_ref=b.source_transition_contract_ref,
        policy_revision_ref="unit-original-policy",
        valid_until=NOW + timedelta(seconds=60),
    )
    context = JourneyEffectAuthority(
        schema_version="v21-journey-effect-authority.proposed.v1",
        journey_binding=b,
        action=action,
        original_transition=proof,
        request_sha256=None,
        action_sha256=action_digest(action),
    )
    return b, snapshot, action, context


class Transition:
    """UNIT oracle attesting the exact source decision, never an operational verifier."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls = 0
        self.expire = False
        self.revoke = False
        self.ceiling = NOW + timedelta(seconds=60)

    async def check_current(
        self,
        authority: JourneyEffectAuthority,
        *,
        phase: Literal["before_fence", "before_effect", "before_disclosure"],
    ) -> VerifiedJourneyEffectCurrentness | CapabilityRefusalReason:
        self.calls += 1
        await asyncio.sleep(0)
        if self.expire:
            self.clock.now = NOW + timedelta(seconds=61)
        if self.revoke:
            return DENIED
        proof = authority.original_transition
        return VerifiedJourneyEffectCurrentness(
            schema_version="v21-journey-effect-currentness.proposed.v1",
            effect_authority_sha256=effect_authority_digest(authority),
            source_decision_ref=proof.source_decision_ref,
            source_observation_ref=proof.source_observation_ref,
            transition_contract_ref=proof.transition_contract_ref,
            policy_revision_ref=proof.policy_revision_ref,
            currentness_ref="unit-original-transition-currentness",
            checked_at=self.clock.now,
            valid_until=self.ceiling,
        )


class Native:
    """UNIT protected-evidence owner oracle; these refs are wholly synthetic."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.authorizations = 0
        self.checks = 0
        self.expire_at: str | None = None
        self.revoke_at: str | None = None
        self.change: dict[str, object] = {}
        self.mutate_input = False
        self.issued: OriginalDomainInvocationAuthority | None = None

    async def authorize_original(
        self, context: JourneyEffectAuthority
    ) -> OriginalDomainInvocationAuthority | CapabilityRefusalReason:
        self.authorizations += 1
        await asyncio.sleep(0)
        if self.expire_at == "authorize":
            self.clock.now = NOW + timedelta(seconds=61)
        if self.revoke_at == "authorize":
            return DENIED
        self.issued = OriginalDomainInvocationAuthority(
            kind="existing_domain",
            journey_binding_sha256=action_digest(context.journey_binding),
            action_sha256=context.action_sha256,
            domain_contract_ref="unit-admitted-native-contract",
            source_contract_publication_ref="unit-native-publication",
            authorization_ref="unit-original-native-authorization",
            native_authority_evidence_ref="unit-protected-original-native-evidence",
            native_authority_evidence_sha256="a" * 64,
            currentness_ref="unit-original-native-currentness",
            verified_at=NOW,
            original_valid_until=NOW + timedelta(seconds=60),
            accumulated_valid_until=NOW + timedelta(seconds=60),
            last_checked_at=NOW,
        )
        return deepcopy(self.issued)

    async def check_current(
        self,
        authority: OriginalDomainInvocationAuthority,
        *,
        phase: Literal["before_effect", "before_disclosure"],
    ) -> OriginalDomainInvocationAuthority | CapabilityRefusalReason:
        self.checks += 1
        await asyncio.sleep(0)
        if self.expire_at == phase:
            self.clock.now = NOW + timedelta(seconds=61)
        if self.revoke_at == phase:
            return DENIED
        checked = authority.model_copy(update={"last_checked_at": self.clock.now} | self.change)
        if self.mutate_input:
            object.__setattr__(authority, "authorization_ref", "unit-replacement-native-grant")
        return checked


class Target:
    """UNIT native effect spy, enforcing checkpoint minima after its own await."""

    def __init__(self, clock: Clock, native: Native) -> None:
        self.clock, self.native = clock, native
        self.calls = 0
        self.effects = 0
        self.expire_inside = False
        self.expire_after_effect = False
        self.replace_original = False
        self.checkpoint: JourneyInvocationCheckpoint | None = None

    async def execute(
        self,
        b: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedDomainHandoffAction,
        *,
        invocation_checkpoint: JourneyInvocationCheckpoint,
    ) -> OutboxDescriptor | CapabilityRefusalReason:
        self.calls += 1
        pinned = JourneyInvocationCheckpoint.model_validate(deepcopy(invocation_checkpoint.__dict__))
        self.checkpoint = deepcopy(pinned)
        assert pinned.original_effect_authority.journey_binding == b
        assert pinned.original_effect_authority.action == action
        assert snapshot.binding == b.journal_binding()
        assert type(pinned.invocation_authority) is OriginalDomainInvocationAuthority
        assert self.native.issued is not None
        # Authenticate the original source-owned evidence against our UNIT oracle.
        pins = set(OriginalDomainInvocationAuthority.model_fields) - {
            "last_checked_at",
            "accumulated_valid_until",
        }
        assert all(
            getattr(pinned.invocation_authority, key) == getattr(self.native.issued, key) for key in pins
        )
        await asyncio.sleep(0)
        if self.expire_inside:
            self.clock.now = NOW + timedelta(seconds=6)
        if self.replace_original:
            self.native.change = {"authorization_ref": "unit-separately-valid-native-B"}
        current = await self.native.check_current(pinned.invocation_authority, phase="before_effect")
        if isinstance(current, CapabilityRefusalReason):
            return current
        if (
            any(getattr(current, key) != getattr(pinned.invocation_authority, key) for key in pins)
            or current.accumulated_valid_until > pinned.invocation_authority.accumulated_valid_until
            or self.clock.now >= min(pinned.effective_valid_until, current.accumulated_valid_until)
        ):
            return DENIED
        self.effects += 1
        result = OutboxDescriptor(
            outbox_ref="unit-genuine-target-outbox",
            command_ref="unit-native-original-command",
            kind=OutboxTechnicalKind.RETURN_INTENT,
            target_binding_ref=action.expected_return_binding_ref,
            payload_ref="unit-genuine-target-payload",
            payload_sha256="b" * 64,
            causation_ref="unit-native-ack",
            dedupe_identity_ref="unit-native-idempotency",
        )
        if self.expire_after_effect:
            self.clock.now = NOW + timedelta(seconds=61)
        return result


def setup() -> tuple[Clock, Transition, Native, Target, ExistingDomainEffectBoundary]:
    clock = Clock()
    transition, native = Transition(clock), Native(clock)
    target = Target(clock, native)
    boundary = ExistingDomainEffectBoundary(
        clock=clock, transition_authority=transition, native_authority=native, target=target
    )
    return clock, transition, native, target, boundary


@pytest.mark.parametrize("missing", ["transition_authority", "native_authority", "target", "all"])
async def test_missing_dependencies_refuse_before_authority_or_target_calls(missing: str) -> None:
    clock, transition, native, target, _ = setup()
    dependencies = {"transition_authority": transition, "native_authority": native, "target": target}
    boundary = ExistingDomainEffectBoundary(
        clock=clock, **({} if missing == "all" else dependencies | {missing: None})
    )
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert native.authorizations == native.checks == transition.calls == target.calls == target.effects == 0


@pytest.mark.parametrize("bad", ["binding", "snapshot", "action", "context", "digest", "expired"])
async def test_invalid_original_is_refused_before_any_authority_await(bad: str) -> None:
    _, transition, native, target, boundary = setup()
    b, snapshot, action, context = original()
    if bad == "binding":
        b = b.model_copy(update={"principal_ref": "unit-other-principal"})
    elif bad == "snapshot":
        snapshot = snapshot.model_copy(update={"unexpected": "unit-forged-extra"})
    elif bad == "action":
        action = action.model_copy(update={"source_case_ref": "unit-other-case"})
    elif bad == "context":
        context = context.model_copy(update={"unexpected": "unit-forged-extra"})
    elif bad == "digest":
        context = context.model_copy(update={"action_sha256": "d" * 64})
    else:
        context = context.model_copy(
            update={
                "original_transition": context.original_transition.model_copy(update={"valid_until": NOW})
            }
        )
    assert isinstance(
        await boundary.execute(b, snapshot, action, effect_authority=context), CapabilityRefusalReason
    )
    assert native.authorizations == native.checks == transition.calls == target.calls == target.effects == 0


@pytest.mark.parametrize("phase", ["authorize", "transition", "before_effect"])
@pytest.mark.parametrize("failure", ["expire", "revoke"])
async def test_each_boundary_await_refuses_expiry_and_revocation(phase: str, failure: str) -> None:
    _, transition, native, target, boundary = setup()
    if phase == "transition":
        setattr(transition, failure, True)
    else:
        setattr(native, f"{failure}_at", phase)
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 0


@pytest.mark.parametrize(
    "pin",
    [
        "authorization_ref",
        "native_authority_evidence_ref",
        "native_authority_evidence_sha256",
        "domain_contract_ref",
        "source_contract_publication_ref",
        "currentness_ref",
        "journey_binding_sha256",
        "action_sha256",
        "verified_at",
        "original_valid_until",
    ],
)
async def test_separately_valid_native_pin_replacement_is_refused(pin: str) -> None:
    _, _, native, target, boundary = setup()
    native.change = {
        pin: (
            NOW - timedelta(seconds=1)
            if pin == "verified_at"
            else NOW + timedelta(seconds=70)
            if pin == "original_valid_until"
            else "c" * 64
            if pin.endswith("sha256")
            else "unit-separately-valid-B"
        )
    }
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 0


async def test_verifier_input_mutation_cannot_replace_original_native_grant() -> None:
    _, _, native, target, boundary = setup()
    native.mutate_input = True
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 0
    assert native.issued is not None
    assert native.issued.authorization_ref == "unit-original-native-authorization"


@pytest.mark.parametrize(
    "changes",
    [
        {"accumulated_valid_until": NOW + timedelta(seconds=70)},
        {"last_checked_at": NOW + timedelta(seconds=1)},
        {"last_checked_at": NOW - timedelta(seconds=1)},
        {"kind": "operation"},
        {"unexpected": "unit-forged-native-extra"},
    ],
)
async def test_native_renewal_future_backward_and_forged_currentness_refuse_before_target(
    changes: dict[str, object],
) -> None:
    _, _, native, target, boundary = setup()
    native.change = changes
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 0


@pytest.mark.parametrize("failure", ["expire", "revoke"])
async def test_final_native_disclosure_await_cannot_hide_expiry_or_revocation(failure: str) -> None:
    _, _, native, target, boundary = setup()
    setattr(native, f"{failure}_at", "before_disclosure")
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 1


async def test_naive_trusted_time_denies_before_native_authorization() -> None:
    clock, transition, native, target, boundary = setup()
    clock.now = NOW.replace(tzinfo=None)
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert native.authorizations == native.checks == transition.calls == target.calls == target.effects == 0


async def test_positive_narrowed_checkpoint_invokes_exact_target_once() -> None:
    _, transition, native, target, boundary = setup()
    transition.ceiling = NOW + timedelta(seconds=5)
    native.change = {"accumulated_valid_until": NOW + timedelta(seconds=4)}
    b, snapshot, action, context = original()
    result = await boundary.execute(b, snapshot, action, effect_authority=context)
    assert type(result) is OutboxDescriptor
    assert target.calls == target.effects == native.authorizations == 1
    assert target.checkpoint is not None
    checkpoint = target.checkpoint
    assert checkpoint.original_effect_authority == context
    assert checkpoint.transition_accumulated_valid_until == NOW + timedelta(seconds=5)
    assert checkpoint.effective_valid_until == NOW + timedelta(seconds=4)
    assert checkpoint.invocation_authority.kind == "existing_domain"


@pytest.mark.parametrize("failure", ["expire", "replace_native"])
async def test_target_internal_await_preserves_narrowed_ceiling_and_original_native(failure: str) -> None:
    _, transition, _, target, boundary = setup()
    transition.ceiling = NOW + timedelta(seconds=5)
    target.expire_inside = failure == "expire"
    target.replace_original = failure == "replace_native"
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == 1 and target.effects == 0
    assert target.checkpoint is not None
    assert target.checkpoint.effective_valid_until == NOW + timedelta(seconds=5)


async def test_post_effect_expiry_retains_real_target_fact_without_disclosure_or_resend() -> None:
    _, _, _, target, boundary = setup()
    target.expire_after_effect = True
    b, snapshot, action, context = original()
    assert await boundary.execute(b, snapshot, action, effect_authority=context) == DENIED
    assert target.calls == target.effects == 1
