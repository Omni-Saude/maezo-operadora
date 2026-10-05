"""Actual driver/adapter/DUR3 UNIT mechanics; synthetic owner ports, no provider qualification."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.capabilities.admission import VerifiedSourceResult, result_digest
from maezo.gateway.capabilities.durability.models import (
    CommandTechnicalState,
    JournalCallResult,
    JournalCallTechnicalStatus,
    JourneySnapshot,
)
from maezo.gateway.capabilities.durable_execution import DurableCapabilityExecutor
from maezo.gateway.capabilities.journeys.contracts import (
    JourneyContractError,
    JourneyEffectGuard,
    JourneyInvocationCheckpoint,
    PreparedCapabilityAction,
    VerifiedJourneyContinuation,
    VerifiedJourneyEffectCurrentness,
    effect_authority_digest,
)
from maezo.gateway.capabilities.journeys.driver import CapabilityServiceExecutionAdapter, JourneyDriver
from maezo.gateway.capabilities.models import (
    CapabilityRefusalReason,
    ContextAccessIntent,
    ContextAccessResult,
    request_digest,
)
from maezo.gateway.capabilities.service import CapabilityService
from tests.unit.gateway.capabilities.test_admission import (
    EXPIRY,
    NOW,
    UnitAudit,
    UnitAuthority,
    UnitSource,
    envelope,
    guard,
)
from tests.unit.gateway.capabilities.test_admission import (
    binding as admission_binding,
)
from tests.unit.gateway.capabilities.test_durable_execution import (
    UnitCustody,
    UnitJournal,
    UnitPreparation,
    UnitReads,
)

from .helpers import binding, turn

RESULT = ContextAccessResult(
    access_status="available",
    context_refs=("UNIT-protected-access-context",),
    source_revision_ref="UNIT-original-source-revision",
    access_decision_ref="UNIT-authenticated-access-decision",
)


class OperationOwner(UnitAuthority):
    """Synthetic authenticated original operation A, independent of transition grant."""

    def __init__(self, h):
        super().__init__()
        self.h = h
        self.current_calls = 0
        self.current_hook = lambda call: None
        self.original = None

    async def authorize(self, scope, env, request):
        self.original = deepcopy(await super().authorize(scope, env, request))
        return deepcopy(self.original)

    async def check_current(self, original, *, source_result=None):
        self.current_calls += 1
        await asyncio.sleep(0)
        self.current_hook(self.current_calls)
        return await super().check_current(original, source_result=source_result)


class TransitionOwner:
    def __init__(self, h):
        self.h = h
        self.valid_until = NOW + timedelta(seconds=60)
        self.hook = lambda phase: None
        self.updates = {}
        self.revoked = False

    async def check_current(self, context, *, phase):
        await asyncio.sleep(0)
        self.hook(phase)
        if self.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        original = context.original_transition
        return VerifiedJourneyEffectCurrentness(
            **{
                "schema_version": "v21-journey-effect-currentness.proposed.v1",
                "effect_authority_sha256": effect_authority_digest(context),
                "source_decision_ref": original.source_decision_ref,
                "source_observation_ref": original.source_observation_ref,
                "transition_contract_ref": original.transition_contract_ref,
                "policy_revision_ref": original.policy_revision_ref,
                "currentness_ref": "UNIT-original-transition-anchor",
                "checked_at": NOW,
                "valid_until": self.valid_until,
                **self.updates,
            }
        )


class QualifiedUnitSource:
    """Synthetic qualified owner; checkpoint checks at actual spy effect after IO awaits."""

    def __init__(self, h):
        self.h = h
        self.calls = self.effects = 0
        self.received = None
        self.internal_hook = lambda: None
        self.effect_hook = lambda: None

    async def execute_under_authority(self, env, request, *, invocation_checkpoint, timeout_seconds):
        self.calls += 1
        incoming = JourneyInvocationCheckpoint.model_validate(deepcopy(invocation_checkpoint.__dict__))
        self.received = deepcopy(incoming)
        pins = incoming.model_dump(mode="json", warnings="error")
        original = incoming.invocation_authority.original_authority
        # Authenticate ORIGINAL A using this UNIT source owner's protected issuance,
        # never a newly valid grant for the same request. No constructor admission.
        if original != self.h.authority.original:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        transition = JourneyEffectGuard(incoming.original_effect_authority, self.h.transition, self.h.clock)
        transition.first = deepcopy(incoming.original_transition_currentness)
        transition.latest = deepcopy(incoming.latest_transition_currentness)
        transition.ceiling = incoming.transition_accumulated_valid_until
        await asyncio.sleep(0)  # Source-owned preparation after DUR3 invocation.
        self.internal_hook()
        await transition.check("before_effect")
        provided = deepcopy(original)
        current = await self.h.authority.check_current(provided)
        operation = incoming.invocation_authority
        if provided != original or incoming.model_dump(mode="json", warnings="error") != pins:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if (
            current.binding != original.binding
            or current.authorization_ref != original.authorization_ref
            or current.currentness_ref != original.currentness_ref
            or current.request_sha256 != original.request_sha256
            or current.valid_until > operation.accumulated_valid_until
            or current.checked_at < operation.last_checked_at
            or request_digest(env, request) != incoming.original_effect_authority.request_sha256
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if original != self.h.authority.original:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        transition.assert_current()
        # LAST synchronous BOTH check; no await between this check and spy effect.
        now = self.h.clock()
        if not current.checked_at <= now < min(incoming.effective_valid_until, current.valid_until):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        self.effects += 1
        self.effect_hook()
        return RESULT


class ActualJournalSpy(UnitJournal):
    async def observe_journey(self, b):
        assert b == self.binding
        return JournalCallResult[JourneySnapshot](
            technical_status=JournalCallTechnicalStatus.UNCHANGED,
            snapshot=JourneySnapshot(
                binding=b,
                journal_revision=self.snapshot.journal_revision if self.snapshot else 0,
                command_refs=(self.snapshot.handle.command_ref,) if self.snapshot else (),
                wait_refs=(),
                outbox_refs=(),
            ),
        )


class PreparationAndEntry:
    def __init__(self, h):
        self.h = h

    async def prepare(self, b, snapshot, current):
        return self.h.action

    async def verify(self, b, snapshot, action, current, *, phase):
        return self.h.entry

    async def source_case_refs(self, b, snapshot):
        return ()


class CurrentIngress:
    async def verify(self, b, current):
        return "administrative"


def harness():
    h = SimpleNamespace(tick=NOW)
    h.clock = lambda: h.tick
    h.binding = binding().model_copy(update={"legal_entity_ref": "unit-entity", "principal_ref": "lucas"})
    h.authority = OperationOwner(h)
    h.journal = ActualJournalSpy(h.authority.events)
    h.journal.binding = h.binding.journal_binding()
    scope = admission_binding(task_ref=h.binding.task_ref, operation_name="access.resolve")
    h.admission = guard(scope=scope, authority=h.authority, audit=UnitAudit(h.authority))
    h.admission._clock = h.clock
    env = envelope(operation_name="access.resolve")
    request = ContextAccessIntent(
        subject_type="prospect",
        subject_or_prospect_ref="UNIT-prospect",
        operation_scope_ref="UNIT-scope",
        purpose_policy_ref="UNIT-source-purpose",
        source_context_refs=(),
    )
    h.action = PreparedCapabilityAction.model_validate(
        {
            "schema_version": "v21-journey-action.proposed.v1",
            "task_ref": h.binding.task_ref,
            "tenant_ref": h.binding.tenant_ref,
            "legal_entity_ref": h.binding.legal_entity_ref,
            "journey_ref": h.binding.journey_ref,
            "topology_contract_ref": h.binding.topology_contract_ref,
            "topology_version_ref": h.binding.topology_version_ref,
            "cursor_ref": "C1_need",
            "predecessor_command_refs": (),
            "predecessor_source_receipt_refs": (),
            "preparation_authority_ref": "UNIT-source-preparation",
            "transition_contract_ref": h.binding.source_transition_contract_ref,
            "envelope": env,
            "request": request,
        }
    )
    h.entry = VerifiedJourneyContinuation(
        schema_version="v21-journey-continuation.proposed.v1",
        task_ref=h.binding.task_ref,
        tenant_ref=h.binding.tenant_ref,
        legal_entity_ref=h.binding.legal_entity_ref,
        journey_ref=h.binding.journey_ref,
        topology_contract_ref=h.binding.topology_contract_ref,
        topology_version_ref=h.binding.topology_version_ref,
        from_cursor_ref="C1_need",
        successor_cursor_ref="C1_options",
        predecessor_command_refs=(),
        source_receipt_refs=(),
        source_decision_ref="UNIT-original-entry-A",
        source_observation_ref="UNIT-original-source-observation",
        transition_contract_ref=h.binding.source_transition_contract_ref,
        policy_revision_ref="UNIT-original-policy",
        valid_until=NOW + timedelta(seconds=60),
    )
    h.transition = TransitionOwner(h)
    h.source = QualifiedUnitSource(h)
    h.legacy = UnitSource(h.authority)
    h.custody = UnitCustody(h.journal)
    h.preparation = UnitPreparation(h.authority.events)
    h.reads = UnitReads(h)
    h.source_proof = lambda: VerifiedSourceResult(
        binding=h.admission.binding,
        request_sha256=request_digest(env, request),
        result_sha256=result_digest(RESULT),
        authorization_ref="unit-authorized",
        source_receipt_ref="unit-domain-receipt",
        source_revision_ref="unit-revision-2",
        currentness_ref="unit-currentness",
        checked_at=NOW,
        valid_until=EXPIRY,
    )
    h.executor = DurableCapabilityExecutor(
        binding=h.binding.journal_binding(),
        journal=h.journal,
        custody=h.custody,
        preparation=h.preparation,
        reads=h.reads,
        enabled=True,
        effect_authority_port=h.transition,
        clock=h.clock,
    )
    h.service = CapabilityService(
        admission=h.admission,
        sources={"access.resolve": h.legacy},
        journey_sources={"access.resolve": h.source},
        durable=h.executor,
    )
    h.adapter = CapabilityServiceExecutionAdapter(h.service)
    prepare = PreparationAndEntry(h)
    h.driver = JourneyDriver(
        h.binding,
        journal=h.journal,
        preparation=prepare,
        transitions=prepare,
        currentness=CurrentIngress(),
        execution=h.adapter,
        clock=h.clock,
        max_actions_per_turn=1,
    )
    h.context = h.driver._effect_authority(h.action, h.entry)
    return h


async def run(h):
    return await h.driver.accept_turn(turn(h.binding, 0))


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["custody", "record", "evidence", "fence", "transition", "admission"])
async def test_actual_driver_adapter_durable_await_expiry_zero_source_effect(boundary):
    h = harness()

    def expire():
        h.tick = NOW + timedelta(seconds=61)

    if boundary == "fence":
        h.journal.on_fence = expire
    elif boundary == "transition":
        h.transition.hook = lambda phase: expire() if phase == "before_effect" else None
    elif boundary == "admission":
        h.authority.current_hook = lambda call: expire() if call == 2 else None
    else:
        owner, method = {
            "custody": (h.custody, "prepare_command"),
            "record": (h.journal, "record_command"),
            "evidence": (h.custody, "dispatch_evidence"),
        }[boundary]
        original = getattr(owner, method)

        async def delayed(*args, **kwargs):
            value = await original(*args, **kwargs)
            await asyncio.sleep(0)
            expire()
            return value

        setattr(owner, method, delayed)
    outcome = await run(h)
    assert outcome.technical_refusal is not None
    assert h.source.calls == h.source.effects == h.legacy.calls == 0
    if boundary in {"fence", "transition", "admission"}:
        original = h.journal.snapshot.handle
        assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
        assert h.journal.descriptor.envelope.idempotency_key == h.action.envelope.idempotency_key
        again = await h.adapter.execute(
            h.binding,
            h.action,
            effect_authority=h.context,
            expected_journal_revision=h.journal.snapshot.journal_revision,
        )
        assert h.journal.snapshot.handle == original and h.source.calls == 0
        assert again is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["transition", "operation"])
async def test_source_internal_await_cannot_restore_upstream_narrowed_ceiling(which):
    h = harness()
    if which == "transition":
        h.transition.valid_until = NOW + timedelta(seconds=5)
    else:
        h.authority.current_updates["valid_until"] = NOW + timedelta(seconds=5)

    def delayed():
        h.tick = NOW + timedelta(seconds=6)
        h.transition.valid_until = NOW + timedelta(seconds=60)
        h.authority.current_updates["valid_until"] = EXPIRY

    h.source.internal_hook = delayed
    result = await run(h)
    assert result.technical_refusal is not None and h.source.calls == 1 and h.source.effects == 0
    assert h.source.received.effective_valid_until == NOW + timedelta(seconds=5)
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["transition_revoked", "operation_revoked", "authorization", "currentness", "binding", "publication"],
)
async def test_source_internal_original_authority_revocation_or_substitution_zero_effect(change):
    h = harness()

    def swap():
        if change == "transition_revoked":
            h.transition.revoked = True
        elif change == "operation_revoked":
            h.authority.revoked = True
        elif change == "publication":
            h.authority.original = h.authority.original.model_copy(
                update={"source_contract_publication_ref": "UNIT-valid-but-other-publication"}
            )
        else:
            field = {
                "authorization": "authorization_ref",
                "currentness": "currentness_ref",
                "binding": "binding",
            }[change]
            h.authority.current_updates[field] = (
                h.admission.binding.model_copy(update={"principal_ref": "UNIT-other"})
                if change == "binding"
                else "UNIT-separately-valid-B"
            )

    h.source.internal_hook = swap
    result = await run(h)
    assert result.technical_refusal is not None and h.source.effects == 0
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN


@pytest.mark.asyncio
async def test_positive_narrowed_checkpoint_exact_original_once_and_posteffect_fact_preserved():
    h = harness()
    h.transition.valid_until = NOW + timedelta(seconds=5)
    result = await run(h)
    assert h.source.calls == h.source.effects == 1 and h.legacy.calls == 0
    assert h.source.received.effective_valid_until == NOW + timedelta(seconds=5)
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert result.technical_refusal is None
    historical = deepcopy(h.journal.snapshot)
    h.tick = NOW + timedelta(seconds=6)
    await h.adapter.execute(
        h.binding, h.action, effect_authority=h.context, expected_journal_revision=historical.journal_revision
    )
    assert h.source.effects == 1 and h.journal.snapshot == historical


@pytest.mark.asyncio
async def test_expiry_during_disclosure_preserves_actual_committed_fact_without_disclosure():
    h = harness()
    h.journal.on_result = lambda: setattr(h, "tick", NOW + timedelta(seconds=61))
    result = await run(h)
    assert result.technical_refusal is not None and result.verified_transition_ref is None
    assert (
        h.source.effects == 1
        and h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    )
    assert "mark_uncertain" not in h.authority.events


@pytest.mark.asyncio
async def test_missing_context_or_qualified_source_mapping_never_uses_legacy():
    for missing in ("context", "mapping", "verifier"):
        h = harness()
        if missing == "mapping":
            h.service = CapabilityService(
                admission=h.admission, sources={"access.resolve": h.legacy}, durable=h.executor
            )
        elif missing == "verifier":
            h.executor.effect_authority_port = None
        result = await h.service.execute_durable(
            h.action.envelope,
            h.action.request,
            effect_authority=None if missing == "context" else h.context,
            predecessor_command_refs=(),
            expected_journal_revision=0,
        )
        assert isinstance(result, CapabilityRefusalReason)
        assert h.source.calls == h.legacy.calls == 0 and h.journal.snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "source_decision_ref",
        "source_observation_ref",
        "transition_contract_ref",
        "policy_revision_ref",
        "currentness_ref",
        "effect_authority_sha256",
    ],
)
async def test_transition_currentness_original_anchor_substitution_refuses_before_effect(field):
    h = harness()

    def swap(phase):
        if phase == "before_effect":
            h.transition.updates[field] = (
                "f" * 64 if field == "effect_authority_sha256" else "UNIT-other-original"
            )

    h.transition.hook = swap
    await run(h)
    assert h.source.calls == 0 and h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN


@pytest.mark.asyncio
async def test_transition_verifier_mutation_cannot_modify_private_original_context():
    h = harness()
    original = h.transition.check_current

    async def mutating(context, *, phase):
        value = await original(context, phase=phase)
        context.original_transition.__dict__["source_decision_ref"] = "UNIT-forged-mutation"
        return value

    h.transition.check_current = mutating
    await run(h)
    assert (
        h.source.calls == 0 and h.context.original_transition.source_decision_ref == "UNIT-original-entry-A"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["custody", "record", "evidence", "fence", "transition", "admission"])
async def test_revocation_during_actual_durable_boundary_await_zero_effect(boundary):
    h = harness()

    def revoke():
        h.transition.revoked = True

    if boundary == "fence":
        h.journal.on_fence = revoke
    elif boundary == "transition":
        h.transition.hook = lambda phase: revoke() if phase == "before_effect" else None
    elif boundary == "admission":
        h.authority.current_hook = lambda call: setattr(h.authority, "revoked", True) if call == 2 else None
    else:
        owner, method = {
            "custody": (h.custody, "prepare_command"),
            "record": (h.journal, "record_command"),
            "evidence": (h.custody, "dispatch_evidence"),
        }[boundary]
        original = getattr(owner, method)

        async def delayed(*args, **kwargs):
            value = await original(*args, **kwargs)
            await asyncio.sleep(0)
            revoke()
            return value

        setattr(owner, method, delayed)
    result = await run(h)
    assert result.technical_refusal is not None
    assert h.source.calls == h.source.effects == h.legacy.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["binding", "action", "digest", "transition", "extra", "operation_none"])
async def test_forged_original_context_never_reaches_custody_or_source(part):
    h = harness()
    changed = {
        "binding": {"journey_binding": h.binding.model_copy(update={"principal_ref": "UNIT-other"})},
        "action": {"action": h.action.model_copy(update={"preparation_authority_ref": "UNIT-other-action"})},
        "digest": {"request_sha256": "d" * 64},
        "transition": {"original_transition": h.entry.model_copy(update={"tenant_ref": "UNIT-other-tenant"})},
        "extra": {"public_lease": "UNIT-invented"},
        "operation_none": {"request_sha256": None},
    }[part]
    result = await h.adapter.execute(
        h.binding,
        h.action,
        effect_authority=h.context.model_copy(update=changed),
        expected_journal_revision=0,
    )
    assert isinstance(result, CapabilityRefusalReason)
    assert h.source.calls == h.legacy.calls == 0 and "qualified_data" not in h.authority.events


@pytest.mark.asyncio
@pytest.mark.parametrize("renewal", ["expanded_ceiling", "past_checked_at", "future_checked_at"])
async def test_transition_refresh_never_renews_previous_ceiling_or_nonmonotonic_time(renewal):
    h = harness()
    if renewal == "expanded_ceiling":
        h.transition.valid_until = NOW + timedelta(seconds=5)
        h.transition.hook = lambda phase: (
            setattr(h.transition, "valid_until", NOW + timedelta(seconds=60))
            if phase == "before_effect"
            else None
        )
    elif renewal == "past_checked_at":
        h.transition.hook = lambda phase: (
            h.transition.updates.update(checked_at=NOW - timedelta(seconds=1))
            if phase == "before_effect"
            else None
        )
    else:
        h.transition.updates["checked_at"] = NOW + timedelta(seconds=1)
    await run(h)
    assert h.source.calls == 0


@pytest.mark.asyncio
async def test_posteffect_timeout_keeps_original_uncertainty_and_replay_cannot_redispatch():
    h = harness()

    def uncertain_after_effect():
        raise TimeoutError("UNIT-possibly-applied-effect")

    h.source.effect_hook = uncertain_after_effect
    result = await run(h)
    assert result.technical_refusal is not None and h.source.effects == 1
    original = deepcopy(h.journal.snapshot)
    assert original.technical_state == CommandTechnicalState.UNCERTAIN
    again = await h.adapter.execute(
        h.binding, h.action, effect_authority=h.context, expected_journal_revision=original.journal_revision
    )
    assert again.snapshot == original and h.source.calls == h.source.effects == 1
    assert h.journal.descriptor.envelope.idempotency_key == h.action.envelope.idempotency_key


@pytest.mark.asyncio
async def test_expiry_inside_last_disclosure_transition_await_preserves_genuine_fact():
    h = harness()
    h.transition.hook = lambda phase: (
        setattr(h, "tick", NOW + timedelta(seconds=61)) if phase == "before_disclosure" else None
    )
    result = await run(h)
    assert result.technical_refusal is not None and result.verified_transition_ref is None
    assert (
        h.source.effects == 1
        and h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    )
    assert "mark_uncertain" not in h.authority.events


@pytest.mark.asyncio
async def test_source_verifier_mutation_preserves_upstream_operation_pins_and_zero_effect():
    h = harness()
    original = h.authority.check_current

    async def mutate(value, *, source_result=None):
        evidence = await original(value, source_result=source_result)
        if h.source.calls:
            value.__dict__["source_contract_publication_ref"] = "UNIT-mutated-B"
        return evidence

    h.authority.check_current = mutate
    await run(h)
    assert h.source.calls == 1 and h.source.effects == 0
    assert (
        h.source.received.invocation_authority.original_authority.source_contract_publication_ref
        == "unit-source-publication"
    )
    assert h.authority.original.source_contract_publication_ref == "unit-source-publication"
