"""Disabled-by-default command boundary for the frozen DUR0 journal interface.

The injected ports must be independently qualified for data/custody, source
receipts and preparation. No implementation, grant, source retry, root binding
or production acceptance is provided here. Journal locks never span source IO.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, field_validator

from maezo.gateway.capabilities.admission import (
    AdmissionBinding,
    AdmissionDeniedError,
    AdmissionDTO,
    CapabilityAdmission,
    Digest,
    VerifiedSourceResult,
    _DurableAdmissionEvidence,
    result_digest,
)
from maezo.gateway.capabilities.durability.models import (
    CommandDescriptor,
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
    DispatchEvidence,
    DispatchToken,
    JournalBinding,
    JournalCallResult,
    JournalCallTechnicalStatus,
    OutboxDescriptor,
    PreDispatchRefusal,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from maezo.gateway.capabilities.journeys.contracts import (
    JourneyCapabilitySourcePort,
    JourneyContractError,
    JourneyEffectAuthority,
    JourneyEffectAuthorityPort,
    JourneyEffectGuard,
    PreparedCapabilityAction,
    _v21_envelope,
    parse_effect_authority,
)
from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityRefusalReason,
    DeclaredMemberships,
    Ref,
    parse_request,
    parse_result,
    request_digest,
)
from maezo.portal.engine.profile import canonicalize

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maezo.gateway.capabilities.service import CapabilitySourcePort

type DurableOutcome = JournalCallResult[CommandSnapshot] | CapabilityRefusalReason
_ACKNOWLEDGED = frozenset({JournalCallTechnicalStatus.RECORDED, JournalCallTechnicalStatus.UNCHANGED})


def _exception_refusal(error: Exception, *, fallback: CapabilityRefusalReason) -> CapabilityRefusalReason:
    """Read native exception storage without caller hooks; only genuine enum members leave."""
    storage: object = BaseException.__dict__["__dict__"].__get__(error, BaseException)
    if type(storage) is not dict:
        return fallback
    # Native iteration avoids equality/hash hooks on a forged dictionary key.
    for name, reason in dict.items(storage):
        if type(name) is str and name == "reason":
            if type(reason) is CapabilityRefusalReason:
                for member in CapabilityRefusalReason:
                    if reason is member:
                        return member
            break
    return fallback


def admission_binding_digest(binding: AdmissionBinding) -> str:
    return hashlib.sha256(canonicalize(binding.model_dump(mode="json", warnings="error"))).hexdigest()


def _model_pins(value: BaseModel) -> bytes:
    """Private canonical snapshot; do not let model serialization hide forged extras."""

    def closed(item: object) -> None:
        if isinstance(item, BaseModel):
            if item.__pydantic_extra__ or frozenset(item.__dict__) != frozenset(type(item).model_fields):
                raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
            for child in item.__dict__.values():
                closed(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                closed(child)
        elif isinstance(item, dict):
            for child in item.values():
                closed(child)

    closed(value)
    return canonicalize(value.model_dump(mode="json", warnings="error"))


def _assert_model_pins(value: BaseModel, original: bytes) -> None:
    if _model_pins(value) != original:
        raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)


@dataclass(frozen=True, slots=True, repr=False)
class RestoredCommand:
    """Qualified custody return; reparsed and digest/scope checked before use."""

    handle: CommandHandle
    descriptor: CommandDescriptor
    request: object


@dataclass(frozen=True, slots=True, repr=False)
class PreparedResultCommit:
    """Explicit atomic preparation, including empty tuples when no intent is authorized."""

    observation: VerifiedResultObservation
    outbox_intents: tuple[OutboxDescriptor, ...]
    wait_intents: tuple[WaitDescriptor, ...]


def _commit_pins(value: PreparedResultCommit) -> tuple[bytes, tuple[bytes, ...], tuple[bytes, ...]]:
    return (
        _model_pins(value.observation),
        tuple(_model_pins(intent) for intent in value.outbox_intents),
        tuple(_model_pins(intent) for intent in value.wait_intents),
    )


@dataclass(frozen=True, slots=True, repr=False)
class QualifiedLookup:
    result: object
    commit: PreparedResultCommit


@dataclass(frozen=True, slots=True, repr=False)
class QualifiedInbox:
    command: RestoredCommand
    result: object
    observation: VerifiedInboxObservation
    commit: PreparedResultCommit
    wait_ref: str | None


class SourceReadEvidence(AdmissionDTO):
    """Source-owned independently current READ authority, not renewed effect authority."""

    journal_binding: JournalBinding
    admission_binding: AdmissionBinding
    request_sha256: Digest
    result_sha256: Digest
    source_receipt_ref: Ref
    source_revision_ref: Ref
    currentness_ref: Ref
    read_authority_ref: Ref
    source_contract_publication_ref: Ref
    data_policy_ref: Ref
    checked_at: datetime
    valid_until: datetime

    @field_validator("checked_at", "valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("source read evidence requires timezone")
        return value


class QualifiedCommandCustodyPort(Protocol):
    """Authenticate current custody/data policy and exact protected command lineage.

    Publication/retention/erasure/source ownership and preparation are checked by
    this qualified port, not inferred from a reference's presence. It must never
    mint source identities, acceptance, enrollment or provider receipts.
    """

    async def prepare_command(
        self,
        binding: JournalBinding,
        admission_binding: AdmissionBinding,
        envelope: CapabilityEnvelope,
        request: CandidateDTO,
        predecessor_command_refs: tuple[str, ...],
        source: CapabilitySourcePort,
    ) -> CommandDescriptor: ...

    async def dispatch_evidence(
        self, binding: JournalBinding, evidence: _DurableAdmissionEvidence
    ) -> DispatchEvidence: ...

    async def pre_dispatch_refusal(
        self, binding: JournalBinding, handle: CommandHandle, reason: CapabilityRefusalReason
    ) -> PreDispatchRefusal: ...

    async def restore_command(self, binding: JournalBinding, command_ref: str) -> RestoredCommand: ...


class QualifiedResultPreparationPort(Protocol):
    """Verify protected result custody, provenance and every new wait/registered return intent.

    One result commit receives explicit tuples. It may not append intents to an
    old duplicate, reopen a wait, invent deadline/status or choose a caller target.
    The journal's independently qualified proof port rechecks these in its TX.
    """

    async def prepare(
        self,
        binding: JournalBinding,
        descriptor: CommandDescriptor,
        handle: CommandHandle,
        result: CandidateDTO,
        source_result: VerifiedSourceResult,
    ) -> PreparedResultCommit: ...


class QualifiedSourceReadPort(Protocol):
    """Independent receipt-query/inbox authority; never executes the original command.

    Every method authenticates real source/custody/data scope and current read
    authority. A type/reference/boolean is not proof. Lookup absence/outage is a
    bounded refusal and cannot authorize execute, a new key or a fence reset.
    """

    async def lookup(
        self,
        binding: JournalBinding,
        command: RestoredCommand,
        snapshot: CommandSnapshot,
        *,
        timeout_seconds: float,
    ) -> QualifiedLookup | CapabilityRefusalReason: ...

    async def verify_inbox(
        self, binding: JournalBinding, observation: VerifiedInboxObservation
    ) -> QualifiedInbox: ...

    async def current(
        self,
        binding: JournalBinding,
        descriptor: CommandDescriptor,
        source_result: VerifiedSourceResult,
    ) -> SourceReadEvidence: ...

    async def current_head(
        self, binding: JournalBinding, descriptor: CommandDescriptor, snapshot: CommandSnapshot
    ) -> VerifiedSourceResult:
        """Authenticate current READ of the exact stored head after an old inbox duplicate.

        Verify source/custody material, not echo snapshot fields. This read does
        not execute, manufacture an event, update the head or create intents.
        """
        ...


class DurableCapabilityExecutor:
    """Trusted composition of one journal scope; no operational defaults or redispatch API."""

    def __init__(
        self,
        *,
        binding: JournalBinding,
        journal: DurabilityJournalPort | None = None,
        custody: QualifiedCommandCustodyPort | None = None,
        preparation: QualifiedResultPreparationPort | None = None,
        reads: QualifiedSourceReadPort | None = None,
        enabled: bool = False,
        effect_authority_port: JourneyEffectAuthorityPort | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("invalid durable execution configuration")
        self.binding = JournalBinding.model_validate(binding)
        self.journal, self.custody, self.preparation, self.reads = journal, custody, preparation, reads
        self.enabled, self.clock = enabled, clock
        self.effect_authority_port = effect_authority_port

    def _available(self) -> None:
        if not self.enabled or self.journal is None or self.custody is None:
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def _scope(self, envelope: CapabilityEnvelope, admission: AdmissionBinding) -> None:
        if (
            (envelope.tenant_ref, envelope.legal_entity_ref, envelope.journey_ref)
            != (self.binding.tenant_ref, self.binding.legal_entity_ref, self.binding.journey_ref)
            or (admission.tenant_ref, admission.legal_entity_ref, admission.principal_ref, admission.task_ref)
            != (
                self.binding.tenant_ref,
                self.binding.legal_entity_ref,
                self.binding.principal_ref,
                self.binding.task_ref,
            )
            or admission.operation_name != envelope.operation_name
            or admission.schema_version != CANDIDATE_SCHEMA_VERSION
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    @staticmethod
    def _revision(value: int) -> None:
        if type(value) is not int or not 0 <= value <= 9223372036854775807:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)

    @staticmethod
    def _timeout(value: float) -> None:
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)

    def _descriptor(
        self,
        descriptor: CommandDescriptor,
        envelope: CapabilityEnvelope,
        request: CandidateDTO,
        admission: AdmissionBinding,
        predecessors: tuple[str, ...] | None = None,
    ) -> CommandDescriptor:
        descriptor = CommandDescriptor.model_validate(descriptor)
        if (
            descriptor.envelope != envelope
            or descriptor.request_sha256 != request_digest(envelope, request)
            or descriptor.admission_binding_sha256 != admission_binding_digest(admission)
            or (predecessors is not None and descriptor.predecessor_command_refs != predecessors)
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        return descriptor

    def _snapshot(
        self, value: object, *, handle: CommandHandle | None = None, digest: str | None = None
    ) -> JournalCallResult[CommandSnapshot]:
        result = JournalCallResult[CommandSnapshot].model_validate(value)
        if result.technical_status in _ACKNOWLEDGED:
            snapshot = result.snapshot
            assert snapshot is not None
            if (
                snapshot.handle.binding != self.binding
                or (handle is not None and snapshot.handle != handle)
                or (digest is not None and snapshot.handle.request_sha256 != digest)
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        return result

    def _commit(
        self,
        value: PreparedResultCommit,
        result: CandidateDTO,
        source: VerifiedSourceResult,
        *,
        handle: CommandHandle,
        correlation_ref: str,
    ) -> PreparedResultCommit:
        if type(value) is not PreparedResultCommit:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        _model_pins(value.observation)
        observation = VerifiedResultObservation.model_validate(deepcopy(value.observation.__dict__))
        if (
            observation.source_result != source
            or observation.result_sha256 != result_digest(result)
            or observation.result_sha256 != source.result_sha256
            or type(value.outbox_intents) is not tuple
            or type(value.wait_intents) is not tuple
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        parsed = PreparedResultCommit(
            observation,
            tuple(
                OutboxDescriptor.model_validate(deepcopy(intent.__dict__)) for intent in value.outbox_intents
            ),
            tuple(WaitDescriptor.model_validate(deepcopy(intent.__dict__)) for intent in value.wait_intents),
        )
        if (
            any(intent.command_ref != handle.command_ref for intent in parsed.outbox_intents)
            or any(
                wait.handle != handle or wait.intent.correlation_ref != correlation_ref
                for wait in parsed.wait_intents
            )
            or len({intent.outbox_ref for intent in parsed.outbox_intents}) != len(parsed.outbox_intents)
            or len({wait.intent.wait_ref for wait in parsed.wait_intents}) != len(parsed.wait_intents)
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        return parsed

    @staticmethod
    def _recorded_result(snapshot: CommandSnapshot, commit: PreparedResultCommit) -> None:
        observation = commit.observation
        if (
            snapshot.technical_state != CommandTechnicalState.RESPONSE_RECORDED
            or snapshot.result_ref != observation.result_ref
            or snapshot.result_sha256 != observation.result_sha256
            or snapshot.source_receipt_ref != observation.source_result.source_receipt_ref
            or snapshot.source_revision_ref != observation.source_result.source_revision_ref
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)

    async def _uncertain(self, token: DispatchToken) -> None:
        assert self.journal is not None
        # DISPATCH_FENCED remains a durable recovery candidate if this update fails.
        with contextlib.suppress(Exception):
            await self.journal.mark_uncertain(
                self.binding, token.handle, token.dispatch_ref, token.journal_revision
            )

    async def _preserve_uncertain(self, token: DispatchToken, timeout_seconds: float) -> None:
        # Own and drain the local task even if cleanup itself is cancelled/times out.
        task = asyncio.create_task(self._uncertain(token))
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.shield(task)
        except (Exception, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def execute(
        self,
        envelope: CapabilityEnvelope,
        request: CandidateDTO,
        *,
        admission: CapabilityAdmission,
        source: CapabilitySourcePort | JourneyCapabilitySourcePort,
        effect_authority: JourneyEffectAuthority | None,
        predecessor_command_refs: tuple[str, ...],
        expected_journal_revision: int,
        timeout_seconds: float,
        memberships: DeclaredMemberships,
    ) -> DurableOutcome:
        lease = None
        snapshot: CommandSnapshot | None = None
        token: DispatchToken | None = None
        persisted = False
        fence_attempted = False
        failure: CapabilityRefusalReason | None = None
        effect_guard: JourneyEffectGuard | None = None
        try:
            self._available()
            self._revision(expected_journal_revision)
            self._timeout(timeout_seconds)
            envelope = _v21_envelope(envelope)
            request = parse_request(
                envelope.operation_name,
                request,
                memberships=memberships,
                schema_version=CANDIDATE_SCHEMA_VERSION,
            )
            self._scope(envelope, admission.binding)
            if self.binding.task_ref in STAGES_BY_TASK:
                if effect_authority is None or self.effect_authority_port is None:
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                context = parse_effect_authority(effect_authority, memberships)
                if (
                    context.journey_binding.journal_binding() != self.binding
                    or type(context.action) is not PreparedCapabilityAction
                    or context.action.envelope != envelope
                    or context.action.request != request
                    or context.action.predecessor_command_refs != predecessor_command_refs
                ):
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                effect_guard = JourneyEffectGuard(
                    context, self.effect_authority_port, self.clock, memberships
                )
                effect_guard.assert_current()
            elif effect_authority is not None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if self.preparation is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            if type(predecessor_command_refs) is not tuple or any(
                type(ref) is not str or not ref.strip() for ref in predecessor_command_refs
            ):
                raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
            assert self.journal is not None and self.custody is not None
            async with asyncio.timeout(timeout_seconds):
                descriptor = self._descriptor(
                    await self.custody.prepare_command(
                        self.binding,
                        admission.binding,
                        envelope,
                        request,
                        predecessor_command_refs,
                        cast("CapabilitySourcePort", source),
                    ),
                    envelope,
                    request,
                    admission.binding,
                    predecessor_command_refs,
                )
                if effect_guard is not None:
                    effect_guard.assert_current()
                recorded = self._snapshot(
                    await self.journal.record_command(self.binding, descriptor, expected_journal_revision),
                    digest=descriptor.request_sha256,
                )
                if recorded.technical_status not in _ACKNOWLEDGED:
                    return recorded
                snapshot = recorded.snapshot
                assert snapshot is not None
                if snapshot.technical_state != CommandTechnicalState.RECORDED:
                    # Previously fenced/result/refused commands never re-enter execute.
                    if snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED:
                        await self._current_stored_head(descriptor, snapshot, admission.binding)
                    return recorded
                lease = await admission.authorize(envelope, request)
                private = await admission._durable_before_fence(lease)
                evidence = DispatchEvidence.model_validate(
                    await self.custody.dispatch_evidence(self.binding, private)
                )
                if (
                    evidence.authority != private.authority
                    or evidence.currentness != private.currentness
                    or evidence.audit_receipt_sha256 != private.audit_receipt_sha256
                ):
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                if effect_guard is not None:
                    await effect_guard.check("before_fence")
                    admission._durable_assert_current(lease)
                    effect_guard.assert_current()
                fence_attempted = True
                fenced = JournalCallResult[DispatchToken].model_validate(
                    await self.journal.begin_dispatch(
                        self.binding, snapshot.handle, evidence, snapshot.journal_revision
                    )
                )
                if fenced.technical_status != JournalCallTechnicalStatus.RECORDED:
                    return CapabilityRefusalReason.SOURCE_UNAVAILABLE
                candidate = fenced.snapshot
                assert candidate is not None
                if (
                    candidate.handle != snapshot.handle
                    or candidate.journal_revision <= snapshot.journal_revision
                ):
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                token = candidate
                # Fresh check AFTER awaited journal fence; legacy before_source stays one-use.
                if effect_guard is not None:
                    await effect_guard.check("before_effect")
                await admission._durable_after_fence(lease)
                if effect_guard is not None:
                    admission._durable_assert_current(lease)
                    effect_guard.assert_current()
                    checkpoint = effect_guard.checkpoint(admission._durable_effect_checkpoint(lease))
                    raw = await cast(JourneyCapabilitySourcePort, source).execute_under_authority(
                        envelope, request, invocation_checkpoint=checkpoint, timeout_seconds=timeout_seconds
                    )
                else:
                    raw = await cast("CapabilitySourcePort", source).execute(
                        envelope, request, timeout_seconds=timeout_seconds
                    )
                result = parse_result(envelope.operation_name, raw, memberships=memberships)
                await admission.verify_result(lease, result)
                result = parse_result(envelope.operation_name, result, memberships=memberships)
                verified = VerifiedSourceResult.model_validate(
                    deepcopy(admission._durable_source_result(lease).__dict__)
                )
                # Authentic originals stay private, including the complete body and attestation.
                # Frozen value models alone do not prevent a port from mutating __dict__.
                preparation_inputs = (
                    deepcopy(self.binding),
                    deepcopy(descriptor),
                    deepcopy(token.handle),
                    deepcopy(result),
                    deepcopy(verified),
                )
                original_pins = tuple(
                    _model_pins(value) for value in (self.binding, descriptor, token.handle, result, verified)
                )

                def assert_inputs() -> None:
                    for value, pins in zip(preparation_inputs, original_pins, strict=True):
                        _assert_model_pins(value, pins)

                    for value, pins in zip(
                        (self.binding, descriptor, token.handle, result, verified), original_pins, strict=True
                    ):
                        _assert_model_pins(value, pins)

                def assert_custody() -> None:
                    assert_inputs()
                    # Compare to the SAME lease's protected qualified proof, never to a port alias.
                    _assert_model_pins(admission._durable_source_result(lease), original_pins[-1])

                prepared = await self.preparation.prepare(*preparation_inputs)
                assert_custody()
                commit = self._commit(
                    prepared,
                    result,
                    verified,
                    handle=token.handle,
                    correlation_ref=envelope.correlation_ref,
                )
                original_commit = _commit_pins(commit)
                await admission._durable_before_result_write(lease)
                assert_custody()
                admission._durable_assert_current(lease)
                if _commit_pins(prepared) != original_commit or _commit_pins(commit) != original_commit:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                journal_observation = deepcopy(commit.observation)
                journal_outbox = deepcopy(commit.outbox_intents)
                journal_waits = deepcopy(commit.wait_intents)
                stored = self._snapshot(
                    await self.journal.record_verified_result(
                        self.binding,
                        token.handle,
                        token.dispatch_ref,
                        journal_observation,
                        journal_outbox,
                        journal_waits,
                        token.journal_revision,
                    ),
                    handle=token.handle,
                )
                if stored.technical_status not in _ACKNOWLEDGED:
                    return stored
                assert stored.snapshot is not None
                self._recorded_result(stored.snapshot, commit)
                persisted = True
                # Preserve a genuine recorded fact on later refusal; never replace its pins.
                assert_custody()
                if (
                    _commit_pins(prepared) != original_commit
                    or _commit_pins(commit) != original_commit
                    or _commit_pins(PreparedResultCommit(journal_observation, journal_outbox, journal_waits))
                    != original_commit
                ):
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                if effect_guard is not None:
                    await admission._durable_before_disclosure(lease)
                    await effect_guard.check("before_disclosure")
                    admission._durable_assert_current(lease)
                    effect_guard.assert_current()
                else:
                    await admission.revalidate(lease, phase="before_disclosure")
                # Legacy disclosure releases its lease; the private immutable originals remain.
                if effect_guard is not None:
                    assert_custody()
                else:
                    assert_inputs()
                if _commit_pins(prepared) != original_commit or _commit_pins(commit) != original_commit:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                return stored
        except (AdmissionDeniedError, CapabilityContractError, JourneyContractError) as exc:
            failure = _exception_refusal(exc, fallback=CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            return failure
        except asyncio.CancelledError:
            raise
        except Exception:
            failure = CapabilityRefusalReason.SOURCE_UNAVAILABLE
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        finally:
            if token is not None and not persisted:
                # Bound, awaited local cleanup only. No fence reset, source retry or new key.
                await self._preserve_uncertain(token, timeout_seconds)
            elif failure is not None and snapshot is not None and not fence_attempted:
                assert self.custody is not None and self.journal is not None
                # Only an authenticated local pre-fence observation; never a source receipt.
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(timeout_seconds):
                        refusal = PreDispatchRefusal.model_validate(
                            await self.custody.pre_dispatch_refusal(self.binding, snapshot.handle, failure)
                        )
                        if refusal.reason == failure:
                            await self.journal.record_pre_dispatch_refusal(
                                self.binding, snapshot.handle, refusal, snapshot.journal_revision
                            )
            if lease is not None:
                admission.release(lease)

    async def _restore(
        self,
        command_ref: str,
        admissions: Mapping[str, CapabilityAdmission],
        memberships: DeclaredMemberships,
    ) -> RestoredCommand:
        self._available()
        assert self.custody is not None
        restored = await self.custody.restore_command(self.binding, command_ref)
        if type(restored) is not RestoredCommand:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        handle = CommandHandle.model_validate(restored.handle)
        envelope = _v21_envelope(restored.descriptor.envelope)
        request = parse_request(
            envelope.operation_name,
            restored.request,
            memberships=memberships,
            schema_version=CANDIDATE_SCHEMA_VERSION,
        )
        admission = admissions.get(envelope.operation_name)
        if admission is None or handle.binding != self.binding or handle.command_ref != command_ref:
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        self._scope(envelope, admission.binding)
        descriptor = self._descriptor(restored.descriptor, envelope, request, admission.binding)
        if handle.request_sha256 != descriptor.request_sha256:
            raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        return RestoredCommand(handle, descriptor, request)

    async def observe(
        self,
        command_ref: str,
        *,
        admissions: Mapping[str, CapabilityAdmission],
        memberships: DeclaredMemberships,
    ) -> DurableOutcome:
        try:
            command = await self._restore(command_ref, admissions, memberships)
            assert self.journal is not None
            observed = self._snapshot(
                await self.journal.observe_command(self.binding, command.handle), handle=command.handle
            )
            if observed.technical_status in _ACKNOWLEDGED:
                assert observed.snapshot is not None
                if observed.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED:
                    admission = admissions[command.descriptor.envelope.operation_name].binding
                    await self._current_stored_head(command.descriptor, observed.snapshot, admission)
            return observed
        except (AdmissionDeniedError, CapabilityContractError) as exc:
            return _exception_refusal(exc, fallback=CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        except Exception:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE

    async def _current_read(
        self,
        descriptor: CommandDescriptor,
        source: VerifiedSourceResult,
        admission: AdmissionBinding,
    ) -> None:
        if self.reads is None:
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

        source = VerifiedSourceResult.model_validate(deepcopy(source.__dict__))
        original_source = _model_pins(source)
        proof_source = deepcopy(source)
        proof_binding = deepcopy(self.binding)
        proof_descriptor = deepcopy(descriptor)
        original_binding, original_descriptor = _model_pins(self.binding), _model_pins(descriptor)
        evidence = SourceReadEvidence.model_validate(
            await self.reads.current(proof_binding, proof_descriptor, proof_source)
        )
        _assert_model_pins(proof_source, original_source)
        _assert_model_pins(source, original_source)
        _assert_model_pins(proof_binding, original_binding)
        _assert_model_pins(self.binding, original_binding)
        _assert_model_pins(proof_descriptor, original_descriptor)
        _assert_model_pins(descriptor, original_descriptor)
        now = self.clock()
        if (
            source.binding != admission
            or source.request_sha256 != descriptor.request_sha256
            or evidence.journal_binding != self.binding
            or evidence.admission_binding != admission
            or evidence.request_sha256 != source.request_sha256
            or evidence.result_sha256 != source.result_sha256
            or evidence.source_receipt_ref != source.source_receipt_ref
            or evidence.source_revision_ref != source.source_revision_ref
            or evidence.currentness_ref != source.currentness_ref
            or evidence.read_authority_ref != source.authorization_ref
            or evidence.data_policy_ref != self.binding.data_policy_ref
            or now.utcoffset() is None
            or not source.checked_at <= now < source.valid_until
            or not source.checked_at <= evidence.checked_at <= now < evidence.valid_until
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    @staticmethod
    def _result_scope(result: CandidateDTO, admission: AdmissionBinding) -> None:
        if (
            admission.security_zone == "general"
            and getattr(result, "clinical_result_context_ref", None) is not None
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.PURPOSE_DENIED)

    async def _current_stored_head(
        self, descriptor: CommandDescriptor, snapshot: CommandSnapshot, admission: AdmissionBinding
    ) -> None:
        if self.reads is None:
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        head = VerifiedSourceResult.model_validate(
            await self.reads.current_head(self.binding, descriptor, snapshot)
        )
        if (
            head.result_sha256 != snapshot.result_sha256
            or head.source_receipt_ref != snapshot.source_receipt_ref
            or head.source_revision_ref != snapshot.source_revision_ref
        ):
            raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        await self._current_read(descriptor, head, admission)

    async def reconcile(
        self,
        command_ref: str,
        *,
        admissions: Mapping[str, CapabilityAdmission],
        memberships: DeclaredMemberships,
        expected_journal_revision: int,
        timeout_seconds: float,
    ) -> DurableOutcome:
        try:
            self._revision(expected_journal_revision)
            self._timeout(timeout_seconds)
            async with asyncio.timeout(timeout_seconds):
                command = await self._restore(command_ref, admissions, memberships)
                if self.reads is None:
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                assert self.journal is not None
                observed = self._snapshot(
                    await self.journal.observe_command(self.binding, command.handle), handle=command.handle
                )
                if observed.technical_status not in _ACKNOWLEDGED:
                    return observed
                snapshot = observed.snapshot
                assert snapshot is not None
                if snapshot.dispatch_ref is None:
                    return observed
                lookup = await self.reads.lookup(
                    self.binding, command, snapshot, timeout_seconds=timeout_seconds
                )
                if isinstance(lookup, CapabilityRefusalReason):
                    return lookup  # Authenticated absence is not a source execute permission.
                if type(lookup) is not QualifiedLookup:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                operation = command.descriptor.envelope.operation_name
                result = parse_result(operation, lookup.result, memberships=memberships)
                source = VerifiedSourceResult.model_validate(lookup.commit.observation.source_result)
                commit = self._commit(
                    lookup.commit,
                    result,
                    source,
                    handle=command.handle,
                    correlation_ref=command.descriptor.envelope.correlation_ref,
                )
                admission = admissions[operation].binding
                self._result_scope(result, admission)
                await self._current_read(command.descriptor, source, admission)
                stored = self._snapshot(
                    await self.journal.record_verified_result(
                        self.binding,
                        command.handle,
                        snapshot.dispatch_ref,
                        commit.observation,
                        commit.outbox_intents,
                        commit.wait_intents,
                        expected_journal_revision,
                    ),
                    handle=command.handle,
                )
                if stored.technical_status not in _ACKNOWLEDGED:
                    return stored
                assert stored.snapshot is not None
                self._recorded_result(stored.snapshot, commit)
                await self._current_read(command.descriptor, source, admission)
                return stored
        except (AdmissionDeniedError, CapabilityContractError) as exc:
            return _exception_refusal(exc, fallback=CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        except Exception:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE

    async def ingest(
        self,
        observation: VerifiedInboxObservation,
        *,
        admissions: Mapping[str, CapabilityAdmission],
        memberships: DeclaredMemberships,
        expected_journal_revision: int,
        timeout_seconds: float,
    ) -> DurableOutcome:
        try:
            self._available()
            self._revision(expected_journal_revision)
            self._timeout(timeout_seconds)
            if self.reads is None:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            observation = VerifiedInboxObservation.model_validate(observation)
            if observation.handle.binding != self.binding:
                raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
            async with asyncio.timeout(timeout_seconds):
                inbox = await self.reads.verify_inbox(self.binding, observation)
                if type(inbox) is not QualifiedInbox or inbox.observation != observation:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                command = await self._restore(observation.handle.command_ref, admissions, memberships)
                inbox_request = parse_request(
                    command.descriptor.envelope.operation_name,
                    inbox.command.request,
                    memberships=memberships,
                )
                if (
                    inbox.command.handle != command.handle
                    or inbox.command.descriptor != command.descriptor
                    or inbox_request != command.request
                    or observation.handle != command.handle
                ):
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                operation = command.descriptor.envelope.operation_name
                if observation.correlation_ref != command.descriptor.envelope.correlation_ref:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                if observation.source_authority_ref != command.descriptor.envelope.source_authority_ref:
                    raise AdmissionDeniedError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
                result = parse_result(operation, inbox.result, memberships=memberships)
                source = VerifiedSourceResult.model_validate(observation.observation.source_result)
                commit = self._commit(
                    inbox.commit,
                    result,
                    source,
                    handle=command.handle,
                    correlation_ref=command.descriptor.envelope.correlation_ref,
                )
                if commit.observation != observation.observation:
                    raise AdmissionDeniedError(CapabilityRefusalReason.CONTRACT_MISMATCH)
                admission = admissions[operation].binding
                self._result_scope(result, admission)
                await self._current_read(command.descriptor, source, admission)
                assert self.journal is not None
                stored = self._snapshot(
                    await self.journal.ingest_verified_observation(
                        self.binding,
                        observation,
                        inbox.wait_ref,
                        commit.outbox_intents,
                        commit.wait_intents,
                        expected_journal_revision,
                    ),
                    handle=command.handle,
                )
                if stored.technical_status not in _ACKNOWLEDGED:
                    return stored
                # An old exact event duplicate may correctly leave a newer head in place.
                # Fresh disclosure must authorize THAT head, not reuse old-event read authority.
                assert stored.snapshot is not None
                if (
                    stored.snapshot.result_sha256 != source.result_sha256
                    or stored.snapshot.source_receipt_ref != source.source_receipt_ref
                    or stored.snapshot.source_revision_ref != source.source_revision_ref
                ):
                    await self._current_stored_head(command.descriptor, stored.snapshot, admission)
                else:
                    await self._current_read(command.descriptor, source, admission)
                return stored
        except (AdmissionDeniedError, CapabilityContractError) as exc:
            return _exception_refusal(exc, fallback=CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        except Exception:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
