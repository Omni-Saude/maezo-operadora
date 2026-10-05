"""Scripted UNIT source/custody/journal doubles; no product or authority qualification.

The script is a test oracle, never a business rule/provider adapter. Driver
position is read from reference lineage in this journal double, not an index in
JourneyDriver. UNIT receipts below are intentionally not real provider evidence.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA, AdministrativeHandoff
from maezo.gateway.capabilities.durability.models import (
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
    JournalCallResult,
    JournalCallTechnicalStatus,
    JourneySnapshot,
    OutboxDescriptor,
    OutboxSnapshot,
    OutboxTechnicalKind,
    OutboxTechnicalState,
    RecoverySnapshot,
    WaitDescriptor,
    WaitSnapshot,
    WaitTechnicalState,
)
from maezo.gateway.capabilities.journeys.contracts import (
    CurrentJourneyTurn,
    JourneyBinding,
    JourneyEffectGuard,
    OriginalDomainInvocationAuthority,
    PreparedAwaitObservationAction,
    PreparedCapabilityAction,
    PreparedCurrentManifestationAction,
    PreparedDomainHandoffAction,
    PreparedTerminalAction,
    VerifiedJourneyContinuation,
    VerifiedJourneyEffectCurrentness,
    action_digest,
    effect_authority_digest,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    DeclaredMemberships,
    ExternalWaitIntent,
    parse_request,
    request_digest,
)

NOW = datetime(2026, 10, 4, 20, tzinfo=UTC)
MEMBERS = DeclaredMemberships(
    case_kinds=frozenset({"unit-case-kind"}),
    case_statuses=frozenset({"unit-case-status"}),
    milestone_kinds=frozenset({"unit-milestone"}),
)


def binding(task: str = "journey.compras.step", *, enabled: bool = True) -> JourneyBinding:
    return JourneyBinding(
        task_ref=task,
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-legal",
        topology_contract_ref="unit-topology-contract",
        topology_version_ref="unit-topology-version",
        source_transition_contract_ref="unit-transition-contract",
        enabled=enabled,
        environment_ref="unit-environment",
        principal_ref="unit-lucas",
        data_policy_ref="unit-data-policy",
        journey_ref="unit-journey",
    )


def turn(
    b: JourneyBinding, revision: int, *, decision: str = "unit-current-decision", message: str = "a"
) -> CurrentJourneyTurn:
    message_ref = "hk1_" + message * 64
    return CurrentJourneyTurn(
        handoff=AdministrativeHandoff(HANDOFF_SCHEMA, b.task_ref, b.tenant_ref, b.journey_ref, message_ref),
        current_message_ref=message_ref,
        manifestation_evidence_ref=decision,
        safety_observation_ref="unit-current-safety",
        expected_journal_revision=revision,
    )


@dataclass(frozen=True)
class Step:
    cursor: str
    operation: str | None = None
    variant: str = "capability"
    requires_confirmation: bool = False


class UnitJournal:
    """In-memory UNIT spy. It is not the PostgreSQL implementation or acceptance."""

    def __init__(self, b: JourneyBinding) -> None:
        self.binding = b.journal_binding()
        self.revision = 0
        self.commands: dict[str, CommandSnapshot] = {}
        self.waits: dict[str, WaitSnapshot] = {}
        self.outboxes: dict[str, OutboxSnapshot] = {}
        self.lineage: dict[str, int] = {}
        self.events: dict[str, str] = {}
        self.calls: list[str] = []

    def completed(self) -> set[int]:
        return set(self.lineage.values())

    async def observe_journey(self, b: Any) -> Any:
        assert b == self.binding
        return JournalCallResult[JourneySnapshot](
            technical_status=JournalCallTechnicalStatus.UNCHANGED,
            snapshot=JourneySnapshot(
                binding=b,
                journal_revision=self.revision,
                command_refs=tuple(self.commands),
                wait_refs=tuple(self.waits),
                outbox_refs=tuple(self.outboxes),
            ),
        )

    async def record_wait(self, b: Any, descriptor: WaitDescriptor, revision: int) -> Any:
        assert b == self.binding and revision == self.revision
        ref = descriptor.intent.wait_ref
        status = JournalCallTechnicalStatus.UNCHANGED
        if ref not in self.waits:
            self.revision += 1
            self.waits[ref] = WaitSnapshot(
                descriptor=descriptor, journal_revision=self.revision, technical_state=WaitTechnicalState.OPEN
            )
            status = JournalCallTechnicalStatus.RECORDED
        return JournalCallResult[WaitSnapshot](technical_status=status, snapshot=self.waits[ref])

    async def enqueue_outbox(self, b: Any, descriptor: OutboxDescriptor, revision: int) -> Any:
        assert b == self.binding and revision == self.revision
        ref = descriptor.outbox_ref
        status = JournalCallTechnicalStatus.UNCHANGED
        if ref not in self.outboxes:
            self.revision += 1
            self.outboxes[ref] = OutboxSnapshot(
                descriptor=descriptor,
                journal_revision=self.revision,
                technical_state=OutboxTechnicalState.RECORDED,
            )
            self.lineage[ref] = int(descriptor.payload_ref.rsplit("-", 1)[-1])
            status = JournalCallTechnicalStatus.RECORDED
        return JournalCallResult[OutboxSnapshot](technical_status=status, snapshot=self.outboxes[ref])

    async def recover(self, b: Any, limit: int, cursor_ref: str | None) -> Any:
        assert b == self.binding and limit > 0
        return JournalCallResult[RecoverySnapshot](
            technical_status=JournalCallTechnicalStatus.UNCHANGED,
            snapshot=RecoverySnapshot(
                binding=b,
                journal_revision=self.revision,
                command_refs_for_reconciliation=tuple(
                    r
                    for r, s in self.commands.items()
                    if s.technical_state
                    in {CommandTechnicalState.DISPATCH_FENCED, CommandTechnicalState.UNCERTAIN}
                ),
                outbox_refs_for_reconciliation=(),
                wait_refs_for_observation=tuple(self.waits),
                next_cursor_ref=cursor_ref,
            ),
        )


class UnitIngress:
    def __init__(self, b: JourneyBinding) -> None:
        self.binding = b
        self.allowed_decisions = {"unit-current-decision", "unit-current-confirmation"}
        self.durable_observations: set[str] = set()
        self.control = "administrative"
        self.revoked = False
        self.calls = 0

    async def verify(self, b: Any, current: Any) -> Any:
        self.calls += 1
        if b != self.binding or self.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if isinstance(current, str):
            if current not in self.durable_observations:
                return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        elif current.manifestation_evidence_ref not in self.allowed_decisions:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return self.control


class UnitSourceOracle:
    """Closed scripted source ports, selected by server composition, never caller OP."""

    def __init__(self, b: JourneyBinding, journal: UnitJournal, steps: list[Step]) -> None:
        self.binding, self.journal, self.steps = b, journal, list(steps)
        self.prepared: list[str] = []
        self.refusal: CapabilityRefusalReason | None = None
        self.forgery: dict[str, Any] = {}
        self.expired = False
        self.bad_successor = False
        self.confirmation_seen = False
        self.domain_calls: list[str] = []

    def index(self) -> int:
        done = self.journal.completed()
        return next((i for i in range(len(self.steps)) if i not in done), len(self.steps) - 1)

    def common(self, i: int) -> dict[str, Any]:
        return dict(
            schema_version="v21-journey-action.proposed.v1",
            task_ref=self.binding.task_ref,
            tenant_ref=self.binding.tenant_ref,
            legal_entity_ref=self.binding.legal_entity_ref,
            journey_ref=self.binding.journey_ref,
            topology_contract_ref=self.binding.topology_contract_ref,
            topology_version_ref=self.binding.topology_version_ref,
            cursor_ref=self.steps[i].cursor,
            predecessor_command_refs=tuple(self.journal.commands),
            predecessor_source_receipt_refs=tuple(
                s.source_receipt_ref for s in self.journal.commands.values() if s.source_receipt_ref
            ),
            preparation_authority_ref=f"unit-preparation-{i}",
            transition_contract_ref=self.binding.source_transition_contract_ref,
        )

    def envelope(self, i: int) -> CapabilityEnvelope:
        return CapabilityEnvelope(
            schema_version=CANDIDATE_SCHEMA_VERSION,
            operation_name=self.steps[i].operation,
            tenant_ref=self.binding.tenant_ref,
            legal_entity_ref=self.binding.legal_entity_ref,
            journey_ref=self.binding.journey_ref,
            correlation_ref="unit-correlation",
            causation_ref=f"unit-cause-{i}",
            idempotency_key=f"unit-literal-key-{i}",
            expected_business_revision=f"unit-opaque-business-revision-{i}",
            source_authority_ref="unit-source-authority",
            policy_revision="unit-source-policy",
            data_classification="unit-administrative",
        )

    async def prepare(self, b: Any, snapshot: Any, current: Any) -> Any:
        assert b == self.binding and snapshot.binding == b.journal_binding()
        if self.refusal:
            return self.refusal
        i = self.index()
        step = self.steps[i]
        self.prepared.append(step.cursor)
        common = self.common(i)
        if step.variant == "terminal":
            action = PreparedTerminalAction(
                **common,
                source_terminal_fact_ref="unit-terminal-fact",
                source_receipt_refs=("unit-source-completion-receipt",),
                source_completion_contract_ref="unit-completion-contract",
            )
        elif step.variant == "manifestation":
            action = PreparedCurrentManifestationAction(
                **common,
                source_case_or_offer_ref="unit-source-case",
                manifestation_contract_ref="unit-manifestation-contract",
                authorized_notice_intent_ref="unit-notice-intent",
            )
        elif step.variant == "wait":
            action = PreparedAwaitObservationAction(
                **common,
                wait_ref=f"unit-wait-{i}",
                expected_producer_ref="unit-producer",
                correlation_ref="unit-correlation",
                authoritative_deadline_ref=None,
                source_case_or_journey_ref="unit-source-case",
            )
        elif step.variant == "handoff":
            action = PreparedDomainHandoffAction(
                **common,
                route_authority_ref="unit-domain-authority",
                source_case_ref="unit-source-case",
                existing_target_binding_ref="unit-registered-target",
                minimum_material_refs=("unit-minimum-material",),
                expected_return_binding_ref="unit-registered-return",
            )
        else:
            env = self.envelope(i)
            action = PreparedCapabilityAction.model_validate(
                common
                | {
                    "envelope": env,
                    "request": parse_request(
                        env.operation_name,
                        request_payload(env.operation_name, common["predecessor_source_receipt_refs"]),
                        memberships=MEMBERS,
                    ),
                },
                context={"memberships": MEMBERS},
            )
        if self.forgery:
            return action.model_copy(update=self.forgery)
        return action

    async def verify(self, b: Any, snapshot: Any, action: Any, current: Any, *, phase: str) -> Any:
        i = int(action.preparation_authority_ref.rsplit("-", 1)[-1])
        step = self.steps[i]
        if step.requires_confirmation and phase == "result":
            if (
                not isinstance(current, CurrentJourneyTurn)
                or current.manifestation_evidence_ref != "unit-current-confirmation"
            ):
                return CapabilityRefusalReason.AUTHORITY_UNPROVEN
            self.confirmation_seen = True
        previous = next((s.cursor for s in reversed(self.steps[:i]) if s.cursor != step.cursor), step.cursor)
        successor = self.steps[i + 1].cursor if i + 1 < len(self.steps) else None
        from_cursor = (
            previous
            if phase == "entry" and snapshot.command_refs and step.cursor != "S_optional_feedback"
            else step.cursor
        )
        if phase == "entry" and snapshot.command_refs and step.cursor != "S_optional_feedback":
            successor = step.cursor
        if self.bad_successor:
            successor = "unpublished-cursor"
        receipts = tuple(s.source_receipt_ref for s in self.journal.commands.values() if s.source_receipt_ref)
        return VerifiedJourneyContinuation(
            schema_version="v21-journey-continuation.proposed.v1",
            task_ref=b.task_ref,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            journey_ref=b.journey_ref,
            topology_contract_ref=b.topology_contract_ref,
            topology_version_ref=b.topology_version_ref,
            from_cursor_ref=from_cursor,
            successor_cursor_ref=successor,
            predecessor_command_refs=tuple(self.journal.commands),
            source_receipt_refs=receipts + ("unit-source-completion-receipt",),
            source_decision_ref=f"unit-source-transition-{i}",
            source_observation_ref=f"unit-source-observation-{i}",
            transition_contract_ref=b.source_transition_contract_ref,
            policy_revision_ref="unit-source-policy",
            valid_until=NOW - timedelta(seconds=1) if self.expired else NOW + timedelta(minutes=5),
        )

    async def prepare_wait(self, b: Any, snapshot: Any, action: Any) -> WaitDescriptor:
        handle = list(self.journal.commands.values())[-1].handle
        return WaitDescriptor(
            intent=ExternalWaitIntent(
                wait_ref=action.wait_ref,
                expected_producer_ref=action.expected_producer_ref,
                correlation_ref=action.correlation_ref,
            ),
            handle=handle,
        )

    async def prepare_continuation(
        self, b: Any, snapshot: Any, action: Any, continuation: Any
    ) -> OutboxDescriptor:
        i = int(action.preparation_authority_ref.rsplit("-", 1)[-1])
        return outbox(i, list(self.journal.commands)[-1])

    async def source_case_refs(self, b: Any, snapshot: Any) -> tuple[str, ...]:
        return ("unit-source-case",) if snapshot.command_refs else ()

    async def execute(
        self, b: Any, snapshot: Any, action: Any, *, invocation_checkpoint: Any
    ) -> OutboxDescriptor | CapabilityRefusalReason:
        if self.clock() >= invocation_checkpoint.effective_valid_until or self.ingress.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        assert invocation_checkpoint.original_effect_authority.action == action
        self.domain_calls.append(action.cursor_ref)
        i = int(action.preparation_authority_ref.rsplit("-", 1)[-1])
        return outbox(i, list(self.journal.commands)[-1])


def outbox(i: int, command_ref: str) -> OutboxDescriptor:
    return OutboxDescriptor(
        outbox_ref=f"unit-outbox-{i}",
        command_ref=command_ref,
        kind=OutboxTechnicalKind.RETURN_INTENT,
        target_binding_ref="unit-registered-return",
        payload_ref=f"unit-protected-continuation-{i}",
        payload_sha256="a" * 64,
        causation_ref=f"unit-cause-{i}",
        dedupe_identity_ref=f"unit-event-dedupe-{i}",
    )


class UnitExecution:
    """UNIT durable boundary spy; neither admission nor provider qualification."""

    def __init__(self, journal: UnitJournal, ingress: UnitIngress) -> None:
        self.journal, self.ingress = journal, ingress
        self.effects: list[Any] = []
        self.uncertain_at: int | None = None
        self.revocation_at: int | None = None
        self.lookups: list[str] = []
        self.disclosure_failure = False
        self.clock = lambda: NOW

    async def execute(
        self, b: Any, action: Any, *, effect_authority: Any, expected_journal_revision: int
    ) -> Any:
        guard = JourneyEffectGuard(
            effect_authority, UnitJourneyEffectAuthority(self.ingress, self.clock), self.clock, MEMBERS
        )
        await guard.check("before_effect")
        guard.assert_current()
        assert b.journal_binding() == self.journal.binding
        if expected_journal_revision != self.journal.revision:
            return CapabilityRefusalReason.CONTRACT_MISMATCH
        i = int(action.preparation_authority_ref.rsplit("-", 1)[-1])
        ref = f"unit-command-{i}"
        digest = request_digest(action.envelope, action.request)
        if ref in self.journal.commands:
            original = self.journal.commands[ref]
            if original.handle.request_sha256 != digest:
                return CapabilityRefusalReason.CONTRACT_MISMATCH
            return JournalCallResult[CommandSnapshot](
                technical_status=JournalCallTechnicalStatus.UNCHANGED, snapshot=original
            )
        self.effects.append(action)
        self.journal.revision += 1
        handle = CommandHandle(binding=b.journal_binding(), command_ref=ref, request_sha256=digest)
        self.journal.commands[ref] = CommandSnapshot(
            handle=handle,
            journal_revision=self.journal.revision,
            technical_state=CommandTechnicalState.UNCERTAIN,
            dispatch_ref=f"unit-dispatch-{i}",
            fence_version=1,
            recorded_at=NOW,
            last_observed_at=NOW,
        )
        if self.uncertain_at != i:
            self.commit(ref, i)
        if self.revocation_at == i:
            self.ingress.revoked = True
        return JournalCallResult[CommandSnapshot](
            technical_status=JournalCallTechnicalStatus.RECORDED, snapshot=self.journal.commands[ref]
        )

    def commit(self, ref: str, i: int) -> None:
        original = self.journal.commands[ref]
        self.journal.revision += 1
        self.journal.commands[ref] = original.model_copy(
            update=dict(
                technical_state=CommandTechnicalState.RESPONSE_RECORDED,
                journal_revision=self.journal.revision,
                verified_result_observation_ref=f"unit-result-observation-{i}",
                result_ref=f"unit-protected-result-{i}",
                result_sha256="b" * 64,
                source_receipt_ref=f"unit-source-receipt-{i}",
                source_revision_ref=f"unit-opaque-result-revision-{i}",
            )
        )
        self.journal.lineage[ref] = i

    async def observe(self, b: Any, command_ref: str) -> Any:
        if b.journal_binding() != self.journal.binding or command_ref not in self.journal.commands:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return JournalCallResult[CommandSnapshot](
            technical_status=JournalCallTechnicalStatus.UNCHANGED, snapshot=self.journal.commands[command_ref]
        )

    async def reconcile(self, b: Any, command_ref: str, *, expected_journal_revision: int) -> Any:
        assert expected_journal_revision == self.journal.revision
        self.lookups.append(command_ref)
        self.commit(command_ref, int(command_ref.rsplit("-", 1)[-1]))
        return await self.observe(b, command_ref)

    async def ingest(self, b: Any, observation: Any, *, expected_journal_revision: int) -> Any:
        assert expected_journal_revision == self.journal.revision
        if observation.event_ref in self.journal.events:
            if self.journal.events[observation.event_ref] != observation.event_sha256:
                return CapabilityRefusalReason.CONTRACT_MISMATCH
        else:
            self.journal.events[observation.event_ref] = observation.event_sha256
            self.journal.revision += 1
            for ref in self.journal.waits:
                i = int(ref.rsplit("-", 1)[-1])
                self.journal.lineage[ref] = i
            self.ingress.durable_observations.add(observation.event_ref)
        return await self.observe(b, observation.handle.command_ref)


def request_payload(operation: str, receipts: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "access.resolve": dict(
            subject_type="prospect",
            subject_or_prospect_ref="unit-prospect",
            operation_scope_ref="unit-scope",
            purpose_policy_ref="unit-purpose",
            source_context_refs=[],
        ),
        "offer.compose": dict(
            catalogue_ref="unit-catalogue",
            catalogue_version="unit-catalogue-version",
            administrative_preferences_ref="unit-preferences",
            availability_refs=[],
            purpose_policy_ref="unit-purpose",
        ),
        "acceptance.record": dict(
            offer_ref="unit-offer",
            offer_version="unit-offer-version",
            decision="accept",
            terms_evidence_ref="unit-terms",
            customer_authority_proof_ref="unit-current-decision",
        ),
        "enrollment.request": dict(
            accepted_commitment_ref="unit-accepted-commitment",
            prospect_linkage_ref="unit-prospect-linkage",
            enrollment_authority_contract_ref="unit-enrollment-authority",
            administrative_evidence_refs=[],
        ),
        "reservation.command": dict(
            provider_ref="unit-provider",
            operation="hold",
            offer_or_booking_ref="unit-offer",
            expected_provider_revision="unit-provider-revision",
            customer_commitment_ref="unit-accepted-commitment",
        ),
        "fulfillment.observe": dict(
            fulfillment_ref="unit-fulfillment",
            source_event_or_observation_ref="unit-source-event",
            source_authority_contract_ref="unit-fulfillment-contract",
            expected_revision="unit-fulfillment-revision",
        ),
        "case.open_or_update": dict(
            problem_ref="unit-original-problem",
            origin_case_or_journey_ref="unit-source-case",
            case_kind="unit-case-kind",
            requester_authority_ref="unit-current-requester",
            evidence_refs=list(receipts) + ["unit-current-confirmation"],
        ),
        "external_wait.settle": dict(
            wait_ref="unit-source-wait",
            expected_producer_ref="unit-producer",
            correlation_ref="unit-correlation",
        ),
        "notice.prepare_or_send": dict(
            notice_ref="unit-notice",
            notice_class="mandatory",
            authorized_content_ref="unit-content",
            recipient_authority_ref="unit-recipient",
            confirmed_channel_ref="unit-confirmed-channel",
            delivery_policy_ref="unit-delivery-policy",
        ),
        "milestone.publish": dict(
            source_fact_ref="unit-source-fact",
            milestone_kind="unit-milestone",
            source_revision="unit-opaque-source-revision",
            audience_contract_ref="unit-audience",
        ),
        "feedback.record": dict(
            case_or_fulfillment_ref="unit-source-case",
            instrument_version_ref="unit-instrument",
            respondent_authority_ref="unit-current-respondent",
            feedback_evidence_ref="unit-feedback-evidence",
        ),
    }[operation]


class UnitJourneyEffectAuthority:
    """Synthetic original transition verifier; no provider acceptance."""

    def __init__(self, ingress: Any, clock: Any) -> None:
        self.ingress, self.clock = ingress, clock

    async def check_current(self, authority: Any, *, phase: Any) -> Any:
        if self.ingress.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        proof = authority.original_transition
        return VerifiedJourneyEffectCurrentness(
            schema_version="v21-journey-effect-currentness.proposed.v1",
            effect_authority_sha256=effect_authority_digest(authority),
            source_decision_ref=proof.source_decision_ref,
            source_observation_ref=proof.source_observation_ref,
            transition_contract_ref=proof.transition_contract_ref,
            policy_revision_ref=proof.policy_revision_ref,
            currentness_ref="unit-original-transition-currentness",
            checked_at=self.clock(),
            valid_until=proof.valid_until,
        )


class UnitNativeAuthority:
    """Explicit UNIT native-domain source verifier, separate from operation authority."""

    def __init__(self, ingress: Any, clock: Any) -> None:
        self.ingress, self.clock = ingress, clock

    async def authorize_original(self, authority: Any) -> Any:
        if self.ingress.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return OriginalDomainInvocationAuthority(
            kind="existing_domain",
            journey_binding_sha256=action_digest(authority.journey_binding),
            action_sha256=authority.action_sha256,
            domain_contract_ref="unit-original-native-contract",
            source_contract_publication_ref="unit-native-publication",
            authorization_ref="unit-original-native-authorization",
            native_authority_evidence_ref="unit-protected-native-evidence",
            native_authority_evidence_sha256="e" * 64,
            currentness_ref="unit-original-native-currentness",
            verified_at=self.clock(),
            original_valid_until=NOW + timedelta(minutes=5),
            accumulated_valid_until=NOW + timedelta(minutes=5),
            last_checked_at=self.clock(),
        )

    async def check_current(self, authority: Any, *, phase: Any) -> Any:
        if self.ingress.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return deepcopy(authority).model_copy(update={"last_checked_at": self.clock()})
