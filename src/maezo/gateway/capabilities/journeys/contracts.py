"""Closed DUR2 internal records and qualified, server-only orchestration ports.

These types never grant source authority. Qualified composition authenticates
current intent, source preparation, predecessors, receipts and transitions.
No provider, planner, ingress, effect adapter or authority is installed by default.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime
from typing import Annotated, Literal, Protocol, cast

from pydantic import Field, SerializeAsAny, ValidationError, ValidationInfo, field_validator, model_validator

from maezo.agents.lucas.administrative.handoff import (
    AdministrativeHandoff,
    AdministrativeInputError,
    AdministrativeTask,
    parse_handoff,
    require_message_ref,
)
from maezo.gateway.capabilities.admission import AdmissionDTO, Digest, OperationInvocationAuthority
from maezo.gateway.capabilities.durability.models import (
    AwareUTCInstant,
    CommandSnapshot,
    JournalBinding,
    JournalCallResult,
    JourneySnapshot,
    LocalRevision,
    OutboxDescriptor,
    VerifiedInboxObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    DeclaredMemberships,
    Ref,
    parse_envelope,
    parse_request,
    request_digest,
)
from maezo.portal.engine.profile import canonicalize

from .topology import STAGES_BY_TASK


class JourneyContractError(ValueError):
    """Only a bounded refusal leaves a parsing/binding failure."""

    def __init__(self, reason: CapabilityRefusalReason = CapabilityRefusalReason.CONTRACT_MISMATCH) -> None:
        self.reason = reason
        super().__init__(reason.value)


class JourneyBinding(AdmissionDTO):
    task_ref: AdministrativeTask
    tenant_ref: Ref
    legal_entity_ref: Ref
    topology_contract_ref: Ref
    topology_version_ref: Ref
    source_transition_contract_ref: Ref
    enabled: bool = False
    environment_ref: Ref
    principal_ref: Ref
    data_policy_ref: Ref
    journey_ref: Ref

    def journal_binding(self) -> JournalBinding:
        return JournalBinding(**{key: getattr(self, key) for key in JournalBinding.model_fields})


class CurrentJourneyTurn(AdmissionDTO):
    handoff: AdministrativeHandoff
    current_message_ref: str
    manifestation_evidence_ref: Ref
    safety_observation_ref: Ref
    expected_journal_revision: LocalRevision

    @field_validator("handoff", mode="before")
    @classmethod
    def closed_handoff(cls, value: object) -> AdministrativeHandoff:
        return parse_handoff(value)

    @field_validator("current_message_ref")
    @classmethod
    def keyed_current_message(cls, value: str) -> str:
        return require_message_ref(value)


def _v21_envelope(value: object) -> CapabilityEnvelope:
    """R5 retains its exact original schema while the generic service is additive."""
    parsed = parse_envelope(value)
    if type(parsed) is not CapabilityEnvelope or parsed.schema_version != CANDIDATE_SCHEMA_VERSION:
        raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return parsed


class _PreparedAction(AdmissionDTO):
    schema_version: Literal["v21-journey-action.proposed.v1"]
    task_ref: AdministrativeTask
    tenant_ref: Ref
    legal_entity_ref: Ref
    journey_ref: Ref
    topology_contract_ref: Ref
    topology_version_ref: Ref
    cursor_ref: Ref
    predecessor_command_refs: tuple[Ref, ...]
    predecessor_source_receipt_refs: tuple[Ref, ...]
    preparation_authority_ref: Ref
    transition_contract_ref: Ref


class PreparedCapabilityAction(_PreparedAction):
    envelope: SerializeAsAny[CapabilityEnvelope]
    request: SerializeAsAny[CandidateDTO]

    @field_validator("envelope", mode="plain")
    @classmethod
    def exact_envelope(cls, value: object) -> CapabilityEnvelope:
        return _v21_envelope(value)

    @field_validator("request", mode="plain")
    @classmethod
    def exact_request(cls, value: object, info: ValidationInfo) -> CandidateDTO:
        envelope = info.data.get("envelope")
        if not isinstance(envelope, CapabilityEnvelope):
            raise JourneyContractError()
        memberships = (info.context or {}).get("memberships")
        return parse_request(
            envelope.operation_name, value, memberships=memberships, schema_version=CANDIDATE_SCHEMA_VERSION
        )


class PreparedDomainHandoffAction(_PreparedAction):
    route_authority_ref: Ref
    source_case_ref: Ref
    existing_target_binding_ref: Ref
    minimum_material_refs: tuple[Ref, ...]
    expected_return_binding_ref: Ref


class PreparedAwaitObservationAction(_PreparedAction):
    wait_ref: Ref
    expected_producer_ref: Ref
    correlation_ref: Ref
    authoritative_deadline_ref: Ref | None
    source_case_or_journey_ref: Ref


class PreparedCurrentManifestationAction(_PreparedAction):
    source_case_or_offer_ref: Ref
    manifestation_contract_ref: Ref
    authorized_notice_intent_ref: Ref


class PreparedTerminalAction(_PreparedAction):
    source_terminal_fact_ref: Ref
    source_receipt_refs: tuple[Ref, ...]
    source_completion_contract_ref: Ref


type PreparedJourneyAction = (
    PreparedCapabilityAction
    | PreparedDomainHandoffAction
    | PreparedAwaitObservationAction
    | PreparedCurrentManifestationAction
    | PreparedTerminalAction
)
_ACTION_MODELS = (
    PreparedCapabilityAction,
    PreparedDomainHandoffAction,
    PreparedAwaitObservationAction,
    PreparedCurrentManifestationAction,
    PreparedTerminalAction,
)


class VerifiedJourneyContinuation(AdmissionDTO):
    schema_version: Literal["v21-journey-continuation.proposed.v1"]
    task_ref: AdministrativeTask
    tenant_ref: Ref
    legal_entity_ref: Ref
    journey_ref: Ref
    topology_contract_ref: Ref
    topology_version_ref: Ref
    from_cursor_ref: Ref
    successor_cursor_ref: Ref | None
    predecessor_command_refs: tuple[Ref, ...]
    source_receipt_refs: tuple[Ref, ...]
    source_decision_ref: Ref
    source_observation_ref: Ref
    transition_contract_ref: Ref
    policy_revision_ref: Ref
    valid_until: AwareUTCInstant


class JourneyDispatchOutcome(AdmissionDTO):
    journey_ref: Ref
    source_case_refs: tuple[Ref, ...]
    journal_revision: LocalRevision
    source_completion_receipt_ref: Ref | None
    pending_command_refs: tuple[Ref, ...]
    wait_refs: tuple[Ref, ...]
    outbox_refs: tuple[Ref, ...]
    technical_refusal: CapabilityRefusalReason | None
    verified_transition_ref: Ref | None


def _mapping(value: object, model: type[AdmissionDTO]) -> Mapping[str, object]:
    if type(value) is model:
        return cast(Mapping[str, object], dict(value.__dict__))
    if not isinstance(value, Mapping):
        raise JourneyContractError()
    return value


def parse_turn(value: object) -> CurrentJourneyTurn:
    try:
        return CurrentJourneyTurn.model_validate(_mapping(value, CurrentJourneyTurn))
    except (ValidationError, AdministrativeInputError):
        raise JourneyContractError() from None


def parse_action(value: object, memberships: DeclaredMemberships) -> PreparedJourneyAction:
    try:
        if type(value) in _ACTION_MODELS:
            # Keep exact nested DTO types for their canonical parser; also retain
            # unknown __dict__ keys from forged model_copy instances for rejection.
            value = dict(cast(PreparedJourneyAction, value).__dict__)
        if not isinstance(value, Mapping):
            raise JourneyContractError()
        for model in _ACTION_MODELS:
            if frozenset(value) == frozenset(model.model_fields):
                return model.model_validate(value, context={"memberships": memberships})
    except (ValidationError, CapabilityContractError, AdministrativeInputError):
        raise JourneyContractError() from None
    raise JourneyContractError()


type JourneyStimulus = CurrentJourneyTurn | str
type JourneyControl = Literal["administrative", "health", "human", "topic_switch"]


class JourneyCurrentnessPort(Protocol):
    """Authenticates current ingress/observation and all binding identities.

    Resolve health/human/topic observations from protected current evidence;
    never infer them or acceptance/confirmation from silence. Observation refs
    must already be durable and tied to the original scoped command/journey.
    Each call is fresh; after-await revocation closes subsequent effects/return.
    """

    async def verify(
        self, binding: JourneyBinding, current: JourneyStimulus
    ) -> JourneyControl | CapabilityRefusalReason: ...


class JourneyPreparationPort(Protocol):
    """Source-qualified preparation/custody; exact lineage, no restart on missing refs."""

    async def prepare(
        self, binding: JourneyBinding, snapshot: JourneySnapshot, current: JourneyStimulus
    ) -> PreparedJourneyAction | CapabilityRefusalReason: ...

    async def prepare_wait(
        self, binding: JourneyBinding, snapshot: JourneySnapshot, action: PreparedAwaitObservationAction
    ) -> WaitDescriptor | CapabilityRefusalReason: ...

    async def prepare_continuation(
        self,
        binding: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedJourneyAction,
        continuation: VerifiedJourneyContinuation,
    ) -> OutboxDescriptor | CapabilityRefusalReason: ...

    async def source_case_refs(
        self, binding: JourneyBinding, snapshot: JourneySnapshot
    ) -> tuple[str, ...] | CapabilityRefusalReason: ...


class JourneyTransitionAuthorityPort(Protocol):
    """Independently checks preparation, original lineage and current source facts.

    Before an action, qualify its current admissibility/obligation. After a
    committed result, qualify the actual continuation against new original-
    command source evidence; no predicted receipt or future completion proof.
    A record alone is never this authentication. Domain/terminal proof includes
    genuine contracted ack/resolution, not transport or a decision token alone.
    """

    async def verify(
        self,
        binding: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedJourneyAction,
        current: JourneyStimulus,
        *,
        phase: Literal["entry", "result"],
    ) -> VerifiedJourneyContinuation | CapabilityRefusalReason: ...


class GatewayExecutionAdapter(Protocol):
    """Serial DUR3 adapter to durable service; private custody/admission/fences.

    resolve original command handles/request refs from qualified custody. No
    raw CapabilityOutcome, public lease or effect retry reaches the driver.
    """

    async def execute(
        self,
        binding: JourneyBinding,
        action: PreparedCapabilityAction,
        *,
        effect_authority: JourneyEffectAuthority,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason: ...

    async def observe(
        self, binding: JourneyBinding, command_ref: str
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason: ...

    async def reconcile(
        self, binding: JourneyBinding, command_ref: str, *, expected_journal_revision: int
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason: ...

    async def ingest(
        self,
        binding: JourneyBinding,
        observation: VerifiedInboxObservation,
        *,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot] | CapabilityRefusalReason: ...


class ExistingDomainHandoffPort(Protocol):
    """Own contracted route/idempotency/ack, and qualified protected continuation.

    Return a real, source-attested handoff intent only after the registered
    target's admitted acknowledgement. Driver persists it under separate CAS;
    no distributed atomicity or resolution is inferred from this handoff.
    """

    async def execute(
        self,
        binding: JourneyBinding,
        snapshot: JourneySnapshot,
        action: PreparedDomainHandoffAction,
        *,
        invocation_checkpoint: JourneyInvocationCheckpoint,
    ) -> OutboxDescriptor | CapabilityRefusalReason: ...


class JourneyReturnRecoveryPort(Protocol):
    """Receipt-first recovery of a registered outbox target; never blind resend."""

    async def reconcile(
        self, binding: JournalBinding, outbox_ref: str, *, expected_journal_revision: int
    ) -> CapabilityRefusalReason | None: ...


class JourneyEffectAuthority(AdmissionDTO):
    """Invocation-private original context; construction never grants authority."""

    schema_version: Literal["v21-journey-effect-authority.proposed.v1"]
    journey_binding: JourneyBinding
    action: SerializeAsAny[PreparedCapabilityAction | PreparedDomainHandoffAction]
    original_transition: VerifiedJourneyContinuation
    request_sha256: Digest | None
    action_sha256: Digest

    @field_validator("journey_binding", "original_transition", mode="before")
    @classmethod
    def exact_nested(cls, value: object) -> object:
        return deepcopy(value.__dict__) if isinstance(value, AdmissionDTO) else deepcopy(value)

    @field_validator("action", mode="plain")
    @classmethod
    def exact_effect_action(cls, value: object, info: ValidationInfo) -> PreparedJourneyAction:
        action = parse_action(value, (info.context or {}).get("memberships") or DeclaredMemberships())
        if type(action) not in {PreparedCapabilityAction, PreparedDomainHandoffAction}:
            raise JourneyContractError()
        return deepcopy(action)

    @model_validator(mode="after")
    def scoped_original(self) -> JourneyEffectAuthority:
        b, action, proof = self.journey_binding, self.action, self.original_transition
        for key in (
            "task_ref",
            "tenant_ref",
            "legal_entity_ref",
            "journey_ref",
            "topology_contract_ref",
            "topology_version_ref",
        ):
            if getattr(action, key) != getattr(b, key) or getattr(proof, key) != getattr(b, key):
                raise JourneyContractError()
        if (
            not b.enabled
            or action.transition_contract_ref != b.source_transition_contract_ref
            or proof.transition_contract_ref != action.transition_contract_ref
        ):
            raise JourneyContractError()
        stages = STAGES_BY_TASK[b.task_ref]
        if action.cursor_ref not in stages or proof.from_cursor_ref not in stages:
            raise JourneyContractError()
        if (
            proof.successor_cursor_ref is not None
            and proof.successor_cursor_ref not in stages[proof.from_cursor_ref].successor_cursor_refs
        ):
            raise JourneyContractError()
        if proof.from_cursor_ref != action.cursor_ref and proof.successor_cursor_ref != action.cursor_ref:
            raise JourneyContractError()
        if not set(action.predecessor_command_refs) <= set(proof.predecessor_command_refs) or not set(
            action.predecessor_source_receipt_refs
        ) <= set(proof.source_receipt_refs):
            raise JourneyContractError()
        if self.action_sha256 != action_digest(action):
            raise JourneyContractError()
        if isinstance(action, PreparedCapabilityAction):
            if action.envelope.operation_name not in stages[action.cursor_ref].operations:
                raise JourneyContractError(CapabilityRefusalReason.UNKNOWN_OPERATION)
            if any(
                getattr(action.envelope, key) != getattr(b, key)
                for key in ("tenant_ref", "legal_entity_ref", "journey_ref")
            ) or self.request_sha256 != request_digest(action.envelope, action.request):
                raise JourneyContractError()
        elif self.request_sha256 is not None:
            raise JourneyContractError()
        return self


class VerifiedJourneyEffectCurrentness(AdmissionDTO):
    schema_version: Literal["v21-journey-effect-currentness.proposed.v1"]
    effect_authority_sha256: Digest
    source_decision_ref: Ref
    source_observation_ref: Ref
    transition_contract_ref: Ref
    policy_revision_ref: Ref
    currentness_ref: Ref
    checked_at: AwareUTCInstant
    valid_until: AwareUTCInstant


class OriginalDomainInvocationAuthority(AdmissionDTO):
    kind: Literal["existing_domain"]
    journey_binding_sha256: Digest
    action_sha256: Digest
    domain_contract_ref: Ref
    source_contract_publication_ref: Ref
    authorization_ref: Ref
    native_authority_evidence_ref: Ref
    native_authority_evidence_sha256: Digest
    currentness_ref: Ref
    verified_at: AwareUTCInstant
    original_valid_until: AwareUTCInstant
    accumulated_valid_until: AwareUTCInstant
    last_checked_at: AwareUTCInstant

    @model_validator(mode="after")
    def monotonic_original(self) -> OriginalDomainInvocationAuthority:
        if (
            not self.verified_at
            <= self.last_checked_at
            < self.accumulated_valid_until
            <= self.original_valid_until
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        return self


class JourneyInvocationCheckpoint(AdmissionDTO):
    schema_version: Literal["v21-journey-invocation-checkpoint.proposed.v1"]
    original_effect_authority: SerializeAsAny[JourneyEffectAuthority]
    effect_authority_sha256: Digest
    original_transition_currentness: VerifiedJourneyEffectCurrentness
    latest_transition_currentness: VerifiedJourneyEffectCurrentness
    transition_accumulated_valid_until: AwareUTCInstant
    transition_last_checked_at: AwareUTCInstant
    invocation_authority: Annotated[
        OperationInvocationAuthority | OriginalDomainInvocationAuthority, Field(discriminator="kind")
    ]
    effective_valid_until: AwareUTCInstant

    @field_validator("original_effect_authority", mode="plain")
    @classmethod
    def exact_effect_context(cls, value: object, info: ValidationInfo) -> JourneyEffectAuthority:
        return parse_effect_authority(value, (info.context or {}).get("memberships") or DeclaredMemberships())

    @field_validator(
        "original_transition_currentness",
        "latest_transition_currentness",
        "invocation_authority",
        mode="before",
    )
    @classmethod
    def exact_checkpoint_evidence(cls, value: object) -> object:
        return deepcopy(value.__dict__) if isinstance(value, AdmissionDTO) else deepcopy(value)

    @model_validator(mode="after")
    def preserve_accumulated_originals(self) -> JourneyInvocationCheckpoint:
        context, first, latest = (
            self.original_effect_authority,
            self.original_transition_currentness,
            self.latest_transition_currentness,
        )
        digest = effect_authority_digest(context)
        if (
            self.effect_authority_sha256 != digest
            or first.effect_authority_sha256 != digest
            or latest.effect_authority_sha256 != digest
        ):
            raise JourneyContractError()
        for key in (
            "source_decision_ref",
            "source_observation_ref",
            "transition_contract_ref",
            "policy_revision_ref",
        ):
            if getattr(first, key) != getattr(context.original_transition, key) or getattr(
                latest, key
            ) != getattr(first, key):
                raise JourneyContractError()
        if (
            first.currentness_ref != latest.currentness_ref
            or not first.checked_at
            <= latest.checked_at
            == self.transition_last_checked_at
            < self.transition_accumulated_valid_until
            <= latest.valid_until
            <= first.valid_until
            <= context.original_transition.valid_until
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        invocation = self.invocation_authority
        if isinstance(context.action, PreparedCapabilityAction):
            if (
                type(invocation) is not OperationInvocationAuthority
                or invocation.request_sha256 != context.request_sha256
                or invocation.envelope_sha256 != action_digest(context.action.envelope)
            ):
                raise JourneyContractError()
            original = invocation.original_authority
            if (
                any(
                    getattr(original.binding, key) != getattr(context.journey_binding, key)
                    for key in ("task_ref", "principal_ref", "tenant_ref", "legal_entity_ref")
                )
                or original.binding.operation_name != context.action.envelope.operation_name
            ):
                raise JourneyContractError()
        elif (
            type(invocation) is not OriginalDomainInvocationAuthority
            or invocation.action_sha256 != context.action_sha256
            or invocation.journey_binding_sha256 != action_digest(context.journey_binding)
        ):
            raise JourneyContractError()
        if self.effective_valid_until > min(
            self.transition_accumulated_valid_until, invocation.accumulated_valid_until
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        return self


def action_digest(value: AdmissionDTO | CandidateDTO) -> str:
    return hashlib.sha256(canonicalize(value.model_dump(mode="json", warnings="error"))).hexdigest()


def effect_authority_digest(value: JourneyEffectAuthority) -> str:
    return action_digest(value)


def parse_effect_authority(
    value: object, memberships: DeclaredMemberships | None = None
) -> JourneyEffectAuthority:
    try:
        return JourneyEffectAuthority.model_validate(
            deepcopy(_mapping(value, JourneyEffectAuthority)),
            context={"memberships": memberships or DeclaredMemberships()},
        )
    except (ValidationError, CapabilityContractError):
        raise JourneyContractError() from None


class JourneyEffectAuthorityPort(Protocol):
    async def check_current(
        self,
        authority: JourneyEffectAuthority,
        *,
        phase: Literal["before_fence", "before_effect", "before_disclosure"],
    ) -> VerifiedJourneyEffectCurrentness | CapabilityRefusalReason: ...


class JourneyCapabilitySourcePort(Protocol):
    """Qualified source enforces original checkpoint atomically after internal awaits."""

    async def execute_under_authority(
        self,
        envelope: CapabilityEnvelope,
        request: CandidateDTO,
        *,
        invocation_checkpoint: JourneyInvocationCheckpoint,
        timeout_seconds: float,
    ) -> object: ...


class ExistingDomainInvocationAuthorityPort(Protocol):
    async def authorize_original(
        self, authority: JourneyEffectAuthority
    ) -> OriginalDomainInvocationAuthority | CapabilityRefusalReason: ...

    async def check_current(
        self,
        authority: OriginalDomainInvocationAuthority,
        *,
        phase: Literal["before_effect", "before_disclosure"],
    ) -> OriginalDomainInvocationAuthority | CapabilityRefusalReason: ...


class JourneyEffectGuard:
    """Private invocation pins; qualified observations cannot renew the original grant."""

    def __init__(
        self,
        authority: JourneyEffectAuthority,
        verifier: JourneyEffectAuthorityPort,
        clock: Callable[[], datetime],
        memberships: DeclaredMemberships | None = None,
    ) -> None:
        self.memberships = memberships or DeclaredMemberships()
        self._authority = parse_effect_authority(authority, self.memberships)
        self._digest = effect_authority_digest(self._authority)
        self.verifier, self.clock = verifier, clock
        self.first: VerifiedJourneyEffectCurrentness | None = None
        self.latest: VerifiedJourneyEffectCurrentness | None = None
        self.ceiling = self._authority.original_transition.valid_until

    @property
    def authority(self) -> JourneyEffectAuthority:
        return parse_effect_authority(self._authority, self.memberships)

    def assert_current(self) -> None:
        now = self.clock()
        if (
            now.utcoffset() is None
            or effect_authority_digest(self._authority) != self._digest
            or now >= self.ceiling
            or self.latest is not None
            and now < self.latest.checked_at
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    async def check(
        self, phase: Literal["before_fence", "before_effect", "before_disclosure"]
    ) -> VerifiedJourneyEffectCurrentness:
        self.assert_current()
        provided = self.authority
        raw = await self.verifier.check_current(provided, phase=phase)
        if effect_authority_digest(provided) != self._digest:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if isinstance(raw, CapabilityRefusalReason):
            raise JourneyContractError(raw)
        if type(raw) is not VerifiedJourneyEffectCurrentness:
            raise JourneyContractError()
        evidence = VerifiedJourneyEffectCurrentness.model_validate(deepcopy(raw.__dict__))
        proof = self._authority.original_transition
        if evidence.effect_authority_sha256 != self._digest or any(
            getattr(evidence, key) != getattr(proof, key)
            for key in (
                "source_decision_ref",
                "source_observation_ref",
                "transition_contract_ref",
                "policy_revision_ref",
            )
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        now = self.clock()
        if now.utcoffset() is None or not evidence.checked_at <= now < evidence.valid_until <= self.ceiling:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if self.latest is not None and (
            evidence.currentness_ref != self.latest.currentness_ref
            or evidence.checked_at < self.latest.checked_at
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        if self.first is None:
            self.first = deepcopy(evidence)
        self.latest = deepcopy(evidence)
        self.ceiling = min(self.ceiling, evidence.valid_until)
        self.assert_current()
        return deepcopy(evidence)

    def checkpoint(
        self, invocation_authority: OperationInvocationAuthority | OriginalDomainInvocationAuthority
    ) -> JourneyInvocationCheckpoint:
        self.assert_current()
        if self.first is None or self.latest is None:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        checkpoint = JourneyInvocationCheckpoint.model_validate(
            {
                "schema_version": "v21-journey-invocation-checkpoint.proposed.v1",
                "original_effect_authority": self.authority,
                "effect_authority_sha256": self._digest,
                "original_transition_currentness": deepcopy(self.first),
                "latest_transition_currentness": deepcopy(self.latest),
                "transition_accumulated_valid_until": self.ceiling,
                "transition_last_checked_at": self.latest.checked_at,
                "invocation_authority": deepcopy(invocation_authority),
                "effective_valid_until": min(self.ceiling, invocation_authority.accumulated_valid_until),
            },
            context={"memberships": self.memberships},
        )
        if self.clock() >= checkpoint.effective_valid_until:
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        return checkpoint
