"""R5 typed dispatch boundary for independently qualified existing domain targets.

This boundary authenticates and transports original evidence. Native contracts,
human authorization, atomic effects and receipt-first recovery remain owned by
the injected native authority and target; no qualifying dependency is installed
by default. See the journey-effect-authority amendment of the DUR2 contract.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime

from pydantic import ValidationError

from maezo.gateway.capabilities.durability.models import JournalBinding, JourneySnapshot, OutboxDescriptor
from maezo.gateway.capabilities.journeys.contracts import (
    ExistingDomainHandoffPort,
    ExistingDomainInvocationAuthorityPort,
    JourneyBinding,
    JourneyContractError,
    JourneyEffectAuthority,
    JourneyEffectAuthorityPort,
    JourneyEffectGuard,
    OriginalDomainInvocationAuthority,
    PreparedDomainHandoffAction,
    action_digest,
    effect_authority_digest,
    parse_effect_authority,
)
from maezo.gateway.capabilities.models import CapabilityRefusalReason, DeclaredMemberships


class _NativeAuthorityGuard:
    """Invocation-private native evidence pins and a ceiling that only narrows."""

    def __init__(
        self,
        authority: OriginalDomainInvocationAuthority,
        binding: JourneyBinding,
        action: PreparedDomainHandoffAction,
        clock: Callable[[], datetime],
    ) -> None:
        self._clock = clock
        self._original = self._parse(authority)
        if self._original.journey_binding_sha256 != action_digest(
            binding
        ) or self._original.action_sha256 != action_digest(action):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        self._latest = self._parse(self._original)
        self.assert_current()

    @staticmethod
    def _parse(value: OriginalDomainInvocationAuthority) -> OriginalDomainInvocationAuthority:
        if type(value) is not OriginalDomainInvocationAuthority:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        try:
            return OriginalDomainInvocationAuthority.model_validate(deepcopy(value.__dict__))
        except ValidationError:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN) from None

    @staticmethod
    def _pins(value: OriginalDomainInvocationAuthority) -> dict[str, object]:
        return {
            key: item
            for key, item in value.__dict__.items()
            if key not in {"accumulated_valid_until", "last_checked_at"}
        }

    def copy(self) -> OriginalDomainInvocationAuthority:
        return self._parse(self._latest)

    def accept(self, value: OriginalDomainInvocationAuthority) -> None:
        checked = self._parse(value)
        if (
            self._pins(checked) != self._pins(self._original)
            or checked.accumulated_valid_until > self._latest.accumulated_valid_until
            or checked.last_checked_at < self._latest.last_checked_at
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        self._latest = checked
        self.assert_current()

    def assert_current(self) -> None:
        now = self._clock()
        if (
            now.utcoffset() is None
            or self._pins(self._latest) != self._pins(self._original)
            or self._latest.verified_at > self._latest.last_checked_at
            or self._latest.last_checked_at > now
            or self._latest.accumulated_valid_until > self._latest.original_valid_until
            or now >= self._latest.accumulated_valid_until
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)


class ExistingDomainEffectBoundary:
    """Driver-facing R5 boundary; only the injected target owns native effects."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        transition_authority: JourneyEffectAuthorityPort | None = None,
        native_authority: ExistingDomainInvocationAuthorityPort | None = None,
        target: ExistingDomainHandoffPort | None = None,
    ) -> None:
        self._clock = clock
        self._transition_authority = transition_authority
        self._native_authority = native_authority
        self._target = target

    async def execute(
        self,
        binding: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedDomainHandoffAction,
        *,
        effect_authority: JourneyEffectAuthority,
    ) -> OutboxDescriptor | CapabilityRefusalReason:
        transition_authority = self._transition_authority
        native_authority = self._native_authority
        target = self._target
        if transition_authority is None or native_authority is None or target is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        try:
            # Reparse before any verifier await, including forged model_copy extras.
            if (
                type(binding) is not JourneyBinding
                or type(snapshot) is not JourneySnapshot
                or type(action) is not PreparedDomainHandoffAction
                or type(snapshot.binding) is not JournalBinding
            ):
                raise JourneyContractError()
            original_binding = JourneyBinding.model_validate(deepcopy(binding.__dict__))
            snapshot_fields = deepcopy(snapshot.__dict__)
            snapshot_fields["binding"] = JournalBinding.model_validate(deepcopy(snapshot.binding.__dict__))
            original_snapshot = JourneySnapshot.model_validate(snapshot_fields)
            original_action = PreparedDomainHandoffAction.model_validate(deepcopy(action.__dict__))
            original_context = parse_effect_authority(deepcopy(effect_authority), DeclaredMemberships())
            if (
                original_snapshot.binding != original_binding.journal_binding()
                or original_context.journey_binding != original_binding
                or original_context.action != original_action
                or original_context.action_sha256 != action_digest(original_action)
                or original_context.request_sha256 is not None
                or not set(original_action.predecessor_command_refs) <= set(original_snapshot.command_refs)
                or original_snapshot.command_refs
                and not original_action.predecessor_command_refs
            ):
                raise JourneyContractError()
            transition = JourneyEffectGuard(
                original_context, transition_authority, self._clock, DeclaredMemberships()
            )
            transition.assert_current()
            supplied_context = transition.authority
            authorized = await native_authority.authorize_original(supplied_context)
            if effect_authority_digest(supplied_context) != effect_authority_digest(original_context):
                raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if isinstance(authorized, CapabilityRefusalReason):
                return authorized
            native = _NativeAuthorityGuard(authorized, original_binding, original_action, self._clock)
            await transition.check("before_effect")
            native.assert_current()
            supplied_native = native.copy()
            supplied_digest = action_digest(supplied_native)
            checked = await native_authority.check_current(supplied_native, phase="before_effect")
            if action_digest(supplied_native) != supplied_digest:
                raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if isinstance(checked, CapabilityRefusalReason):
                return checked
            native.accept(checked)
            # BOTH authorities are checked after the last await, immediately before dispatch.
            transition.assert_current()
            native.assert_current()
            checkpoint = transition.checkpoint(native.copy())
            result = await target.execute(
                deepcopy(original_binding),
                deepcopy(original_snapshot),
                deepcopy(original_action),
                invocation_checkpoint=checkpoint,
            )
            if isinstance(result, CapabilityRefusalReason):
                return result
            # The native target retains its genuine fact/fence if disclosure fails.
            # This boundary never retries or synthesizes a receipt/outbox.
            await transition.check("before_disclosure")
            native.assert_current()
            supplied_native = native.copy()
            supplied_digest = action_digest(supplied_native)
            checked = await native_authority.check_current(supplied_native, phase="before_disclosure")
            if action_digest(supplied_native) != supplied_digest:
                raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if isinstance(checked, CapabilityRefusalReason):
                return checked
            native.accept(checked)
            transition.assert_current()
            native.assert_current()
            if type(result) is not OutboxDescriptor:
                raise JourneyContractError()
            return OutboxDescriptor.model_validate(deepcopy(result.__dict__))
        except JourneyContractError as exc:
            return exc.reason
        except ValidationError:
            return CapabilityRefusalReason.CONTRACT_MISMATCH
