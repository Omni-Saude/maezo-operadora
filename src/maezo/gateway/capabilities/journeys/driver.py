"""Shared full-journey driver over source-owned preparation and durable lineage.

The driver has no business cursor/cache, rule, source provider or production
binding. Every invocation reconstructs from the same journal and qualified
source custody; an uncertain command blocks new actions until receipt lookup.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from maezo.gateway.capabilities.durability.models import (
    CommandSnapshot,
    CommandTechnicalState,
    JournalCallResult,
    JournalCallTechnicalStatus,
    JourneySnapshot,
    OutboxDescriptor,
    OutboxTechnicalKind,
    VerifiedInboxObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from maezo.gateway.capabilities.models import (
    CapabilityRefusalReason,
    DeclaredMemberships,
    Ref,
    request_digest,
)
from maezo.gateway.capabilities.service import CapabilityService

from .contracts import (
    GatewayExecutionAdapter,
    JourneyBinding,
    JourneyContractError,
    JourneyCurrentnessPort,
    JourneyDispatchOutcome,
    JourneyEffectAuthority,
    JourneyPreparationPort,
    JourneyReturnRecoveryPort,
    JourneyStimulus,
    JourneyTransitionAuthorityPort,
    PreparedAwaitObservationAction,
    PreparedCapabilityAction,
    PreparedCurrentManifestationAction,
    PreparedDomainHandoffAction,
    PreparedJourneyAction,
    PreparedTerminalAction,
    VerifiedJourneyContinuation,
    action_digest,
    parse_action,
    parse_effect_authority,
    parse_turn,
)
from .domain_boundary import ExistingDomainEffectBoundary
from .topology import STAGES_BY_TASK

_ACCEPTED = {JournalCallTechnicalStatus.RECORDED, JournalCallTechnicalStatus.UNCHANGED}
_FENCED = {CommandTechnicalState.DISPATCH_FENCED, CommandTechnicalState.UNCERTAIN}
_PENDING = _FENCED | {CommandTechnicalState.RECORDED}
_REFS = TypeAdapter(tuple[Ref, ...])


class CapabilityServiceExecutionAdapter:
    """Fixed DUR3 service adapter; every call checks the seven-field journal binding.

    No compatibility fallback to execute or authority/request-ref fields supplied
    by a caller. The durable boundary independently resolves protected custody.
    """

    def __init__(self, service: CapabilityService) -> None:
        self.service = service

    def _bound(self, binding: JourneyBinding) -> bool:
        return self.service.durable is not None and self.service.durable.binding == binding.journal_binding()

    async def execute(
        self,
        binding: JourneyBinding,
        action: PreparedCapabilityAction,
        *,
        effect_authority: JourneyEffectAuthority,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason:
        if not self._bound(binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.service.execute_durable(
            action.envelope,
            action.request,
            effect_authority=effect_authority,
            predecessor_command_refs=action.predecessor_command_refs,
            expected_journal_revision=expected_journal_revision,
        )

    async def observe(
        self, binding: JourneyBinding, command_ref: str
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason:
        if not self._bound(binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.service.observe_durable(command_ref)

    async def reconcile(
        self, binding: JourneyBinding, command_ref: str, *, expected_journal_revision: int
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason:
        if not self._bound(binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.service.reconcile_durable(
            command_ref, expected_journal_revision=expected_journal_revision
        )

    async def ingest(
        self,
        binding: JourneyBinding,
        observation: VerifiedInboxObservation,
        *,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason:
        if not self._bound(binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.service.ingest_durable(
            observation, expected_journal_revision=expected_journal_revision
        )


class JourneyDriver:
    def __init__(
        self,
        binding: JourneyBinding,
        *,
        journal: DurabilityJournalPort | None = None,
        preparation: JourneyPreparationPort | None = None,
        transitions: JourneyTransitionAuthorityPort | None = None,
        currentness: JourneyCurrentnessPort | None = None,
        execution: GatewayExecutionAdapter | None = None,
        domain_handoff: ExistingDomainEffectBoundary | None = None,
        returns: JourneyReturnRecoveryPort | None = None,
        memberships: DeclaredMemberships | None = None,
        max_actions_per_turn: int = 64,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        try:
            if type(binding) is not JourneyBinding:
                raise JourneyContractError()
            self.binding = JourneyBinding.model_validate(dict(binding.__dict__))
        except ValidationError:
            raise JourneyContractError() from None
        if type(max_actions_per_turn) is not int or not 1 <= max_actions_per_turn <= 1024:
            raise JourneyContractError()
        self.journal = journal
        self.preparation = preparation
        self.transitions = transitions
        self.currentness = currentness
        self.execution = execution
        self.domain_handoff = domain_handoff
        self.returns = returns
        self.memberships = memberships or DeclaredMemberships()
        self.max_actions_per_turn = max_actions_per_turn
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _control(self, current: JourneyStimulus) -> CapabilityRefusalReason | None:
        if self.currentness is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        control = await self.currentness.verify(self.binding, current)
        if isinstance(control, CapabilityRefusalReason):
            return control
        if control != "administrative":
            # Health, human and topic switch preserve journal/claims and stop new actions.
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return None

    async def _snapshot(self) -> JourneySnapshot:
        if self.journal is None:
            raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        result = await self.journal.observe_journey(self.binding.journal_binding())
        if result.technical_status not in _ACCEPTED or type(result.snapshot) is not JourneySnapshot:
            raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        snapshot = JourneySnapshot.model_validate(dict(result.snapshot.__dict__))
        if snapshot.binding != self.binding.journal_binding():
            raise JourneyContractError()
        return snapshot

    def _command(self, result: object) -> CommandSnapshot:
        if not isinstance(result, JournalCallResult) or result.technical_status not in _ACCEPTED:
            raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        if type(result.snapshot) is not CommandSnapshot:
            raise JourneyContractError()
        snapshot = CommandSnapshot.model_validate(dict(result.snapshot.__dict__))
        if snapshot.handle.binding != self.binding.journal_binding():
            raise JourneyContractError()
        return snapshot

    async def _pending(self, snapshot: JourneySnapshot) -> tuple[tuple[str, ...], bool]:
        if not snapshot.command_refs:
            return (), False
        if self.execution is None:
            raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        pending = []
        fenced = False
        for ref in snapshot.command_refs:
            result = await self.execution.observe(self.binding, ref)
            if isinstance(result, CapabilityRefusalReason):
                raise JourneyContractError(result)
            command = self._command(result)
            if command.handle.command_ref != ref:
                raise JourneyContractError()
            if command.technical_state in _PENDING:
                pending.append(ref)
            fenced |= command.technical_state in _FENCED
        return tuple(pending), fenced

    async def _outcome(
        self,
        snapshot: JourneySnapshot | None,
        *,
        refusal: CapabilityRefusalReason | None = None,
        transition: VerifiedJourneyContinuation | None = None,
        completion: str | None = None,
        current: JourneyStimulus | None = None,
    ) -> JourneyDispatchOutcome:
        cases: tuple[str, ...] = ()
        pending: tuple[str, ...] = ()
        if snapshot is not None:
            try:
                pending, _ = await self._pending(snapshot)
                if current is not None and (control := await self._control(current)) is not None:
                    refusal = refusal or control
                if (
                    self.preparation is not None
                    and current is not None
                    and await self._control(current) is None
                ):
                    resolved = await self.preparation.source_case_refs(self.binding, snapshot)
                    if isinstance(resolved, CapabilityRefusalReason):
                        refusal = refusal or resolved
                    elif await self._control(current) is None:
                        cases = _REFS.validate_python(resolved)
                    else:
                        refusal = CapabilityRefusalReason.AUTHORITY_UNPROVEN
            except Exception:
                refusal = refusal or CapabilityRefusalReason.AUTHORITY_UNPROVEN
                completion = None
                transition = None
        if transition is not None:
            try:
                # Pending/control/case-source awaits cannot renew this distinct source proof.
                self._proof_current(transition)
            except (JourneyContractError, ValidationError, TypeError, ValueError):
                refusal = refusal or CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if refusal is not None:
            completion = None
            transition = None
        return JourneyDispatchOutcome(
            journey_ref=self.binding.journey_ref,
            source_case_refs=cases,
            journal_revision=snapshot.journal_revision if snapshot else 0,
            source_completion_receipt_ref=completion,
            pending_command_refs=pending,
            wait_refs=snapshot.wait_refs if snapshot else (),
            outbox_refs=snapshot.outbox_refs if snapshot else (),
            technical_refusal=refusal,
            verified_transition_ref=transition.source_decision_ref if transition else None,
        )

    def _proof_current(self, proof: VerifiedJourneyContinuation) -> None:
        """Check the original qualified transition after awaits; ingress is a separate gate."""
        if type(proof) is not VerifiedJourneyContinuation:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        parsed = VerifiedJourneyContinuation.model_validate(dict(proof.__dict__))
        if (
            any(
                getattr(parsed, key) != getattr(self.binding, key)
                for key in (
                    "task_ref",
                    "tenant_ref",
                    "legal_entity_ref",
                    "journey_ref",
                    "topology_contract_ref",
                    "topology_version_ref",
                )
            )
            or parsed.transition_contract_ref != self.binding.source_transition_contract_ref
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        now = self.clock()
        if now.utcoffset() is None or parsed.valid_until <= now:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def _action(self, raw: object, snapshot: JourneySnapshot) -> PreparedJourneyAction:
        action = parse_action(raw, self.memberships)
        for key in (
            "task_ref",
            "tenant_ref",
            "legal_entity_ref",
            "journey_ref",
            "topology_contract_ref",
            "topology_version_ref",
        ):
            if getattr(action, key) != getattr(self.binding, key):
                raise JourneyContractError()
        if action.transition_contract_ref != self.binding.source_transition_contract_ref:
            raise JourneyContractError()
        stages = STAGES_BY_TASK[self.binding.task_ref]
        if action.cursor_ref not in stages:
            raise JourneyContractError()
        if not set(action.predecessor_command_refs) <= set(snapshot.command_refs):
            raise JourneyContractError()
        if snapshot.command_refs and not action.predecessor_command_refs:
            raise JourneyContractError()
        if isinstance(action, PreparedCapabilityAction):
            if action.envelope.operation_name not in stages[action.cursor_ref].operations:
                raise JourneyContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
            for key in ("tenant_ref", "legal_entity_ref", "journey_ref"):
                if getattr(action.envelope, key) != getattr(self.binding, key):
                    raise JourneyContractError()
        elif (
            isinstance(action, PreparedTerminalAction)
            and action.cursor_ref
            not in {
                "C_terminal",
                "S_terminal",
            }
            or isinstance(action, PreparedDomainHandoffAction)
            and action.cursor_ref
            not in {
                "C3_existing_AUTH",
                "C_to_support",
                "S3_resolve_or_handoff",
                "S_to_compras",
            }
            or (
                isinstance(action, PreparedAwaitObservationAction)
                and "external_wait.settle" not in stages[action.cursor_ref].operations
            )
            or isinstance(action, PreparedCurrentManifestationAction)
            and action.cursor_ref
            not in {
                "C2_wait_manifestation",
                "C3_route",
                "S4_current_confirmation",
            }
        ):
            raise JourneyContractError()
        return action

    async def _transition(
        self,
        snapshot: JourneySnapshot,
        action: PreparedJourneyAction,
        current: JourneyStimulus,
        *,
        phase: Literal["entry", "result"] = "entry",
    ) -> VerifiedJourneyContinuation:
        if self.transitions is None:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        proof = await self.transitions.verify(self.binding, snapshot, action, current, phase=phase)
        if isinstance(proof, CapabilityRefusalReason):
            raise JourneyContractError(proof)
        if type(proof) is not VerifiedJourneyContinuation:
            raise JourneyContractError()
        proof = VerifiedJourneyContinuation.model_validate(dict(proof.__dict__))
        for key in (
            "task_ref",
            "tenant_ref",
            "legal_entity_ref",
            "journey_ref",
            "topology_contract_ref",
            "topology_version_ref",
        ):
            if getattr(proof, key) != getattr(self.binding, key):
                raise JourneyContractError()
        now = self.clock()
        if now.utcoffset() is None or proof.valid_until <= now:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if proof.transition_contract_ref != action.transition_contract_ref:
            raise JourneyContractError()
        stages = STAGES_BY_TASK[self.binding.task_ref]
        if proof.from_cursor_ref not in stages:
            raise JourneyContractError()
        stage = stages[proof.from_cursor_ref]
        if (
            proof.successor_cursor_ref is not None
            and proof.successor_cursor_ref not in stage.successor_cursor_refs
        ):
            raise JourneyContractError()
        if proof.successor_cursor_ref is None and proof.from_cursor_ref not in {
            "C_terminal",
            "S_terminal",
            "S_optional_feedback",
        }:
            raise JourneyContractError()
        initial = "C1_need" if self.binding.task_ref == "journey.compras.step" else "S1_case"
        if phase == "entry":
            if not snapshot.command_refs:
                if action.cursor_ref != initial or proof.from_cursor_ref != initial:
                    raise JourneyContractError()
            elif action.cursor_ref == "S_optional_feedback":
                # Source-qualified independent queue, outside the terminal path.
                if proof.from_cursor_ref != "S_optional_feedback":
                    raise JourneyContractError()
            elif proof.successor_cursor_ref != action.cursor_ref:
                raise JourneyContractError()
        elif proof.from_cursor_ref != action.cursor_ref:
            raise JourneyContractError()
        if (
            not set(action.predecessor_command_refs)
            <= set(proof.predecessor_command_refs)
            <= set(snapshot.command_refs)
        ):
            raise JourneyContractError()
        if not set(action.predecessor_source_receipt_refs) <= set(proof.source_receipt_refs):
            raise JourneyContractError()
        return proof

    def _effect_authority(
        self,
        action: PreparedCapabilityAction | PreparedDomainHandoffAction,
        proof: VerifiedJourneyContinuation,
    ) -> JourneyEffectAuthority:
        return parse_effect_authority(
            {
                "schema_version": "v21-journey-effect-authority.proposed.v1",
                "journey_binding": self.binding,
                "action": action,
                "original_transition": proof,
                "request_sha256": request_digest(action.envelope, action.request)
                if isinstance(action, PreparedCapabilityAction)
                else None,
                "action_sha256": action_digest(action),
            },
            self.memberships,
        )

    async def accept_turn(self, value: object) -> JourneyDispatchOutcome:
        try:
            turn = parse_turn(value)
            if (turn.handoff.task_type, turn.handoff.tenant_ref, turn.handoff.journey_ref) != (
                self.binding.task_ref,
                self.binding.tenant_ref,
                self.binding.journey_ref,
            ) or not hmac.compare_digest(turn.handoff.message_ref, turn.current_message_ref):
                raise JourneyContractError()
        except JourneyContractError as exc:
            return await self._outcome(None, refusal=exc.reason)
        return await self._run(turn, turn.expected_journal_revision)

    async def resume(self, observation_ref: str, expected_journal_revision: int) -> JourneyDispatchOutcome:
        try:
            observation_ref = TypeAdapter(Ref).validate_python(observation_ref)
            if type(expected_journal_revision) is not int or expected_journal_revision < 0:
                raise JourneyContractError()
        except (ValidationError, JourneyContractError):
            return await self._outcome(None, refusal=CapabilityRefusalReason.CONTRACT_MISMATCH)
        return await self._run(observation_ref, expected_journal_revision)

    async def _run(self, current: JourneyStimulus, expected: int) -> JourneyDispatchOutcome:
        snapshot: JourneySnapshot | None = None
        proof: VerifiedJourneyContinuation | None = None
        try:
            control = await self._control(current)
            snapshot = await self._snapshot()
            if control is not None or not self.binding.enabled:
                return await self._outcome(
                    snapshot, refusal=control or CapabilityRefusalReason.AUTHORITY_UNPROVEN
                )
            if snapshot.journal_revision != expected:
                raise JourneyContractError()
            if self.preparation is None:
                raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            visited: set[str] = set()
            for _ in range(self.max_actions_per_turn):
                pending, fenced = await self._pending(snapshot)
                if fenced:
                    return await self._outcome(snapshot, current=current)
                if (control := await self._control(current)) is not None:
                    return await self._outcome(snapshot, refusal=control)
                raw = await self.preparation.prepare(self.binding, snapshot, current)
                if isinstance(raw, CapabilityRefusalReason):
                    raise JourneyContractError(raw)
                action = self._action(raw, snapshot)
                proof = await self._transition(snapshot, action, current)
                if (control := await self._control(current)) is not None:
                    return await self._outcome(snapshot, refusal=control)
                self._proof_current(proof)
                identity = action.preparation_authority_ref
                if identity in visited:
                    return await self._outcome(snapshot, transition=proof, current=current)
                visited.add(identity)
                if isinstance(action, PreparedCurrentManifestationAction):
                    proof = await self._transition(snapshot, action, current, phase="result")
                    prepared_intent = await self.preparation.prepare_continuation(
                        self.binding, snapshot, action, proof
                    )
                    if isinstance(prepared_intent, CapabilityRefusalReason):
                        raise JourneyContractError(prepared_intent)
                    intent = OutboxDescriptor.model_validate(prepared_intent.model_dump(mode="python"))
                    if (
                        intent.kind != OutboxTechnicalKind.RETURN_INTENT
                        or intent.command_ref not in action.predecessor_command_refs
                    ):
                        raise JourneyContractError()
                    if (control := await self._control(current)) is not None:
                        return await self._outcome(snapshot, refusal=control)
                    self._proof_current(proof)
                    assert self.journal is not None
                    enqueued = await self.journal.enqueue_outbox(
                        snapshot.binding, intent, snapshot.journal_revision
                    )
                    if enqueued.technical_status not in _ACCEPTED:
                        raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                    snapshot = await self._snapshot()
                    continue
                if isinstance(action, PreparedTerminalAction):
                    proof = await self._transition(snapshot, action, current, phase="result")
                    # Ambiguous/missing source receipt cannot be selected or fabricated locally.
                    if proof.successor_cursor_ref is not None or len(action.source_receipt_refs) != 1:
                        raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                    receipt = action.source_receipt_refs[0]
                    if receipt not in proof.source_receipt_refs or pending:
                        raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                    return await self._outcome(
                        snapshot, transition=proof, completion=receipt, current=current
                    )
                if isinstance(action, PreparedAwaitObservationAction):
                    prepared = await self.preparation.prepare_wait(self.binding, snapshot, action)
                    if isinstance(prepared, CapabilityRefusalReason):
                        raise JourneyContractError(prepared)
                    descriptor = WaitDescriptor.model_validate(prepared.model_dump(mode="python"))
                    wait_intent = descriptor.intent
                    if (
                        descriptor.handle.binding != snapshot.binding
                        or descriptor.handle.command_ref not in action.predecessor_command_refs
                    ):
                        raise JourneyContractError()
                    if (
                        wait_intent.wait_ref,
                        wait_intent.expected_producer_ref,
                        wait_intent.correlation_ref,
                        wait_intent.authoritative_deadline_ref,
                    ) != (
                        action.wait_ref,
                        action.expected_producer_ref,
                        action.correlation_ref,
                        action.authoritative_deadline_ref,
                    ):
                        raise JourneyContractError()
                    if (control := await self._control(current)) is not None:
                        return await self._outcome(snapshot, refusal=control)
                    self._proof_current(proof)
                    assert self.journal is not None
                    recorded = await self.journal.record_wait(
                        snapshot.binding, descriptor, snapshot.journal_revision
                    )
                    if recorded.technical_status not in _ACCEPTED:
                        raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                    snapshot = await self._snapshot()
                    return await self._outcome(snapshot, transition=proof, current=current)
                if isinstance(action, PreparedDomainHandoffAction):
                    if self.domain_handoff is None:
                        raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                    prepared_outbox = await self.domain_handoff.execute(
                        self.binding, snapshot, action, effect_authority=self._effect_authority(action, proof)
                    )
                    if isinstance(prepared_outbox, CapabilityRefusalReason):
                        raise JourneyContractError(prepared_outbox)
                    descriptor_outbox = OutboxDescriptor.model_validate(
                        prepared_outbox.model_dump(mode="python")
                    )
                    if (
                        descriptor_outbox.kind != OutboxTechnicalKind.RETURN_INTENT
                        or descriptor_outbox.command_ref not in action.predecessor_command_refs
                        or descriptor_outbox.target_binding_ref != action.expected_return_binding_ref
                    ):
                        raise JourneyContractError()
                    proof = await self._transition(snapshot, action, current, phase="result")
                    if (control := await self._control(current)) is not None:
                        return await self._outcome(snapshot, refusal=control)
                    self._proof_current(proof)
                    assert self.journal is not None
                    enqueued = await self.journal.enqueue_outbox(
                        snapshot.binding, descriptor_outbox, snapshot.journal_revision
                    )
                    if enqueued.technical_status not in _ACCEPTED:
                        raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                    snapshot = await self._snapshot()
                    return await self._outcome(snapshot, transition=proof, current=current)
                if self.execution is None:
                    raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                result = await self.execution.execute(
                    self.binding,
                    action,
                    effect_authority=self._effect_authority(action, proof),
                    expected_journal_revision=snapshot.journal_revision,
                )
                if isinstance(result, CapabilityRefusalReason):
                    raise JourneyContractError(result)
                command = self._command(result)
                if command.handle.request_sha256 != request_digest(action.envelope, action.request):
                    raise JourneyContractError()
                snapshot = await self._snapshot()
                if command.handle.command_ref not in snapshot.command_refs:
                    raise JourneyContractError()
                if (control := await self._control(current)) is not None:
                    return await self._outcome(snapshot, refusal=control)
                if command.technical_state in _PENDING:
                    return await self._outcome(snapshot, current=current)
                if command.technical_state != CommandTechnicalState.RESPONSE_RECORDED:
                    return await self._outcome(snapshot, refusal=CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                proof = await self._transition(snapshot, action, current, phase="result")
                if (control := await self._control(current)) is not None:
                    return await self._outcome(snapshot, refusal=control)
                if action.cursor_ref == "S_optional_feedback":
                    return await self._outcome(snapshot, transition=proof, current=current)
            return await self._outcome(snapshot, transition=proof, current=current)
        except JourneyContractError as exc:
            return await self._outcome(snapshot, refusal=exc.reason)
        except (ValidationError, TypeError, ValueError):
            return await self._outcome(snapshot, refusal=CapabilityRefusalReason.CONTRACT_MISMATCH)
        except Exception:
            # A private boundary preserves any post-fence uncertainty; never resend here.
            return await self._outcome(snapshot, refusal=CapabilityRefusalReason.SOURCE_UNAVAILABLE)

    async def accept_observation(
        self, observation: VerifiedInboxObservation, expected_journal_revision: int
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason:
        if self.execution is None:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        try:
            if type(observation) is not VerifiedInboxObservation:
                raise JourneyContractError()
            observation = VerifiedInboxObservation.model_validate(dict(observation.__dict__))
            if observation.handle.binding != self.binding.journal_binding():
                raise JourneyContractError()
            if type(expected_journal_revision) is not int or expected_journal_revision < 0:
                raise JourneyContractError()
            result = await self.execution.ingest(
                self.binding, observation, expected_journal_revision=expected_journal_revision
            )
            if isinstance(result, CapabilityRefusalReason):
                return result
            self._command(result)
            return result  # Only an acknowledged verified commit/duplicate is ACK eligible.
        except (JourneyContractError, ValidationError):
            return CapabilityRefusalReason.CONTRACT_MISMATCH

    async def recover(self, *, limit: int, cursor_ref: str | None) -> JourneyDispatchOutcome:
        snapshot: JourneySnapshot | None = None
        try:
            if self.journal is None or type(limit) is not int or not 1 <= limit <= 1024:
                raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            recovery = await self.journal.recover(self.binding.journal_binding(), limit, cursor_ref)
            if recovery.technical_status not in _ACCEPTED or recovery.snapshot is None:
                raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            if recovery.snapshot.binding != self.binding.journal_binding():
                raise JourneyContractError()
            snapshot = await self._snapshot()
            if self.execution is None:
                raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            for ref in recovery.snapshot.command_refs_for_reconciliation:
                result = await self.execution.reconcile(
                    self.binding, ref, expected_journal_revision=snapshot.journal_revision
                )
                if isinstance(result, CapabilityRefusalReason):
                    raise JourneyContractError(result)
                command = self._command(result)
                if command.handle.command_ref != ref:
                    raise JourneyContractError()
                snapshot = await self._snapshot()
            for ref in recovery.snapshot.outbox_refs_for_reconciliation:
                if self.returns is None:
                    raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
                refusal = await self.returns.reconcile(
                    snapshot.binding, ref, expected_journal_revision=snapshot.journal_revision
                )
                if refusal is not None:
                    raise JourneyContractError(refusal)
                snapshot = await self._snapshot()
            return await self._outcome(snapshot)
        except JourneyContractError as exc:
            return await self._outcome(snapshot, refusal=exc.reason)
