"""DUR3 UNIT doubles only: no PostgreSQL, provider receipts or production qualification."""

import asyncio
from dataclasses import dataclass

import pytest

from maezo.gateway.capabilities.admission import AdmissionDeniedError, VerifiedSourceResult
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
    JournalRefusalReason,
    OutboxDescriptor,
    OutboxTechnicalKind,
    PreDispatchRefusal,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.durable_execution import (
    DurableCapabilityExecutor,
    PreparedResultCommit,
    QualifiedInbox,
    QualifiedLookup,
    RestoredCommand,
    SourceReadEvidence,
    admission_binding_digest,
)
from maezo.gateway.capabilities.models import CapabilityRefusalReason, ExternalWaitIntent, request_digest
from maezo.gateway.capabilities.service import CapabilityService

from .test_admission import (
    EXPIRY,
    NOW,
    REQUEST,
    RESULT,
    UnitAudit,
    UnitAuthority,
    UnitSource,
    envelope,
    guard,
)


def journal_binding():
    return JournalBinding(
        environment_ref="UNIT-environment",
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-entity",
        journey_ref="unit-journey",
        principal_ref="lucas",
        task_ref="unit-task",
        data_policy_ref="UNIT-data-policy",
    )


class UnitJournal:
    """In-memory UNIT ordering witness, never storage acceptance."""

    def __init__(self, events):
        self.events = events
        self.descriptor = None
        self.snapshot = None
        self.fail_record = None
        self.fail_fence = None
        self.fail_result = None
        self.on_fence = lambda: None
        self.on_result = lambda: None
        self.recorded_waits = ()
        self.recorded_outbox = ()
        self.last_event = None

    @staticmethod
    def refused(status):
        return JournalCallResult(
            technical_status=status,
            refusal_reason=JournalRefusalReason.COMMIT_UNCERTAIN
            if status == JournalCallTechnicalStatus.UNCERTAIN
            else JournalRefusalReason.CAS_CONFLICT,
        )

    def acknowledged(self, status=JournalCallTechnicalStatus.RECORDED):
        return JournalCallResult[CommandSnapshot](technical_status=status, snapshot=self.snapshot)

    async def record_command(self, binding, descriptor, expected_journal_revision):
        self.events.append("record_command")
        if self.fail_record is not None:
            return self.refused(self.fail_record)
        if self.descriptor is not None:
            if self.descriptor != descriptor:
                return self.refused(JournalCallTechnicalStatus.CONFLICT)
            return self.acknowledged(JournalCallTechnicalStatus.UNCHANGED)
        self.descriptor = descriptor
        self.snapshot = CommandSnapshot(
            handle=CommandHandle(
                binding=binding, command_ref="UNIT-command", request_sha256=descriptor.request_sha256
            ),
            journal_revision=expected_journal_revision + 1,
            technical_state=CommandTechnicalState.RECORDED,
            recorded_at=NOW,
            last_observed_at=NOW,
        )
        return self.acknowledged()

    async def begin_dispatch(self, binding, handle, evidence, expected_journal_revision):
        self.events.append("begin_dispatch")
        assert evidence.audit_receipt_sha256 == "b" * 64
        assert evidence.authority.request_sha256 == handle.request_sha256
        if self.snapshot.technical_state != CommandTechnicalState.RECORDED:
            return self.refused(JournalCallTechnicalStatus.CONFLICT)
        self.snapshot = self.snapshot.model_copy(
            update={
                "technical_state": CommandTechnicalState.DISPATCH_FENCED,
                "dispatch_ref": "UNIT-dispatch",
                "fence_version": 1,
                "journal_revision": expected_journal_revision + 1,
            }
        )
        self.on_fence()
        if self.fail_fence is not None:
            return self.refused(self.fail_fence)
        return JournalCallResult[DispatchToken](
            technical_status=JournalCallTechnicalStatus.RECORDED,
            snapshot=DispatchToken(
                handle=handle,
                dispatch_ref="UNIT-dispatch",
                fence_version=1,
                journal_revision=self.snapshot.journal_revision,
            ),
        )

    async def mark_uncertain(self, binding, handle, dispatch_ref, expected_journal_revision):
        self.events.append("mark_uncertain")
        if self.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED:
            return self.refused(JournalCallTechnicalStatus.CONFLICT)
        self.snapshot = self.snapshot.model_copy(
            update={
                "technical_state": CommandTechnicalState.UNCERTAIN,
                "journal_revision": expected_journal_revision + 1,
            }
        )
        return self.acknowledged()

    async def record_pre_dispatch_refusal(self, binding, handle, refusal, expected_journal_revision):
        self.events.append("pre_dispatch_refusal")
        assert self.snapshot.technical_state == CommandTechnicalState.RECORDED
        self.snapshot = self.snapshot.model_copy(
            update={
                "technical_state": CommandTechnicalState.REFUSED_BEFORE_DISPATCH,
                "journal_revision": expected_journal_revision + 1,
            }
        )
        return self.acknowledged()

    async def record_verified_result(
        self,
        binding,
        handle,
        dispatch_ref,
        observation,
        outbox_intents,
        wait_intents,
        expected_journal_revision,
    ):
        self.events.append("record_verified_result")
        if self.fail_result is not None:
            return self.refused(self.fail_result)
        self.snapshot = self.snapshot.model_copy(
            update={
                "technical_state": CommandTechnicalState.RESPONSE_RECORDED,
                "verified_result_observation_ref": "UNIT-verified-observation",
                "result_ref": observation.result_ref,
                "result_sha256": observation.result_sha256,
                "source_receipt_ref": observation.source_result.source_receipt_ref,
                "source_revision_ref": observation.source_result.source_revision_ref,
                "journal_revision": expected_journal_revision + 1,
            }
        )
        self.recorded_waits, self.recorded_outbox = wait_intents, outbox_intents
        self.on_result()
        return self.acknowledged()

    async def observe_command(self, binding, handle):
        self.events.append("observe_command")
        return self.acknowledged(JournalCallTechnicalStatus.UNCHANGED)

    async def ingest_verified_observation(
        self, binding, observation, wait_ref, outbox_intents, wait_intents, expected_journal_revision
    ):
        self.events.append("ingest_verified_observation")
        if self.last_event == observation:
            return self.acknowledged(JournalCallTechnicalStatus.UNCHANGED)
        result = await self.record_verified_result(
            binding,
            observation.handle,
            self.snapshot.dispatch_ref,
            observation.observation,
            outbox_intents,
            wait_intents,
            expected_journal_revision,
        )
        self.last_event = observation
        return result


class UnitCustody:
    def __init__(self, journal):
        self.journal = journal
        self.bad_descriptor = False
        self.request = REQUEST

    async def prepare_command(self, binding, admission_binding, env, request, predecessors, source):
        self.journal.events.append("qualified_data")
        self.request = request
        return CommandDescriptor(
            envelope=env,
            request_ref="UNIT-protected-request",
            request_sha256="f" * 64 if self.bad_descriptor else request_digest(env, request),
            admission_binding_sha256=admission_binding_digest(admission_binding),
            predecessor_command_refs=predecessors,
        )

    async def dispatch_evidence(self, binding, private):
        self.journal.events.append("qualified_audit_link")
        return DispatchEvidence(
            authority=private.authority,
            currentness=private.currentness,
            audit_receipt_sha256=private.audit_receipt_sha256,
            audit_intent_ref="UNIT-qualified-local-audit-link",
        )

    async def pre_dispatch_refusal(self, binding, handle, reason):
        return PreDispatchRefusal(reason=reason, evidence_ref="UNIT-local-refusal", observed_at=NOW)

    async def restore_command(self, binding, command_ref):
        self.journal.events.append("qualified_restore")
        return RestoredCommand(self.journal.snapshot.handle, self.journal.descriptor, self.request)


class UnitPreparation:
    def __init__(self, events):
        self.events = events
        self.with_intents = False
        self.bad_digest = False
        self.foreign_wait = False
        self.last_commit = None

    async def prepare(self, binding, descriptor, handle, result, source_result):
        self.events.append("qualified_result_preparation")
        waits = ()
        outbox = ()
        if self.with_intents:
            waits = (
                WaitDescriptor(
                    intent=ExternalWaitIntent(
                        wait_ref="UNIT-wait",
                        expected_producer_ref="UNIT-source-producer",
                        correlation_ref=descriptor.envelope.correlation_ref,
                    ),
                    handle=handle,
                ),
            )
            if self.foreign_wait:
                waits = (
                    waits[0].model_copy(
                        update={
                            "handle": handle.model_copy(
                                update={
                                    "binding": binding.model_copy(
                                        update={"journey_ref": "UNIT-foreign-journey"}
                                    )
                                }
                            )
                        }
                    ),
                )
            outbox = (
                OutboxDescriptor(
                    outbox_ref="UNIT-outbox",
                    command_ref=handle.command_ref,
                    kind=OutboxTechnicalKind.RETURN_INTENT,
                    target_binding_ref="UNIT-registered-target",
                    payload_ref="UNIT-protected-reply-intent",
                    payload_sha256="e" * 64,
                    causation_ref=descriptor.envelope.causation_ref,
                    dedupe_identity_ref="UNIT-original-return-identity",
                ),
            )
        self.last_commit = PreparedResultCommit(
            VerifiedResultObservation(
                source_result=source_result,
                result_ref="UNIT-protected-result",
                result_sha256="f" * 64 if self.bad_digest else source_result.result_sha256,
                observer_binding_ref="UNIT-qualified-observer",
                provenance_ref="UNIT-verified-source-provenance",
            ),
            outbox,
            waits,
        )
        return self.last_commit


class UnitReads:
    def __init__(self, harness):
        self.harness = harness
        self.absent = False
        self.revoked = False
        self.head_override = None

    async def lookup(self, binding, command, snapshot, *, timeout_seconds):
        self.harness.authority.events.append("independent_read_lookup")
        if self.absent:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        # UNIT independent READ evidence; does not call source.execute or effect authorize.
        source_result = self.harness.source_proof().model_copy(
            update={"authorization_ref": "UNIT-independent-read", "currentness_ref": "UNIT-read-current"}
        )
        commit = await self.harness.preparation.prepare(
            binding, command.descriptor, command.handle, RESULT, source_result
        )
        return QualifiedLookup(RESULT, commit)

    async def current(self, binding, descriptor, source_result):
        self.harness.authority.events.append("independent_read_current")
        if self.revoked:
            raise AdmissionDeniedError(CapabilityRefusalReason.STALE_REVISION)
        return SourceReadEvidence(
            journal_binding=binding,
            admission_binding=source_result.binding,
            request_sha256=source_result.request_sha256,
            result_sha256=source_result.result_sha256,
            source_receipt_ref=source_result.source_receipt_ref,
            source_revision_ref=source_result.source_revision_ref,
            currentness_ref=source_result.currentness_ref,
            read_authority_ref=source_result.authorization_ref,
            source_contract_publication_ref="UNIT-receipt-query-publication",
            data_policy_ref=binding.data_policy_ref,
            checked_at=NOW,
            valid_until=EXPIRY,
        )

    async def verify_inbox(self, binding, observation):
        self.harness.authority.events.append("authenticated_source_inbox")
        command = await self.harness.custody.restore_command(binding, observation.handle.command_ref)
        commit = PreparedResultCommit(observation.observation, (), ())
        return QualifiedInbox(command, RESULT, observation, commit, None)

    async def current_head(self, binding, descriptor, snapshot):
        self.harness.authority.events.append("independent_current_head")
        if self.head_override is not None:
            return self.head_override
        return self.harness.source_proof().model_copy(
            update={
                "authorization_ref": "UNIT-independent-read",
                "source_receipt_ref": snapshot.source_receipt_ref,
                "source_revision_ref": snapshot.source_revision_ref,
                "result_sha256": snapshot.result_sha256,
            }
        )


@dataclass
class Harness:
    authority: UnitAuthority
    admission: object
    source: UnitSource
    journal: UnitJournal
    custody: UnitCustody
    preparation: UnitPreparation
    reads: UnitReads | None = None
    executor: DurableCapabilityExecutor | None = None
    service: CapabilityService | None = None

    def source_proof(self):
        from maezo.gateway.capabilities.admission import result_digest

        return VerifiedSourceResult(
            binding=self.admission.binding,
            request_sha256=request_digest(envelope(), REQUEST),
            result_sha256=result_digest(RESULT),
            authorization_ref="unit-authorized",
            source_receipt_ref="UNIT-source-receipt-double",
            source_revision_ref="UNIT-opaque-next-source-ref",
            currentness_ref="unit-currentness",
            checked_at=NOW,
            valid_until=EXPIRY,
        )


def harness(*, enabled=True):
    authority = UnitAuthority()
    journal = UnitJournal(authority.events)
    h = Harness(
        authority,
        guard(authority=authority, audit=UnitAudit(authority)),
        UnitSource(authority),
        journal,
        UnitCustody(journal),
        UnitPreparation(authority.events),
    )
    h.reads = UnitReads(h)
    h.executor = DurableCapabilityExecutor(
        binding=journal_binding(),
        journal=journal,
        custody=h.custody,
        preparation=h.preparation,
        reads=h.reads,
        enabled=enabled,
        clock=lambda: NOW,
    )
    h.service = CapabilityService(
        admission=h.admission, sources={"reservation.command": h.source}, durable=h.executor
    )
    return h


async def execute(h, expected=0):
    return await h.service.execute_durable(
        envelope(),
        REQUEST,
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=expected,
    )


@pytest.mark.asyncio
async def test_exact_durable_order_and_no_raw_result_disclosure():
    h = harness()
    result = await execute(h)
    assert result.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert result.snapshot.source_revision_ref == "unit-revision-2"
    assert h.authority.events == [
        "qualified_data",
        "record_command",
        "authority",
        "audit",
        "current",
        "qualified_audit_link",
        "begin_dispatch",
        "current",
        "source",
        "receipt",
        "qualified_result_preparation",
        "current",
        "record_verified_result",
        "current",
    ]
    assert h.source.calls == 1
    assert not hasattr(result, "reservation_status")


@pytest.mark.asyncio
async def test_result_wait_and_reply_intents_are_one_explicit_atomic_call():
    h = harness()
    h.preparation.with_intents = True
    await execute(h)
    assert len(h.journal.recorded_waits) == len(h.journal.recorded_outbox) == 1
    assert h.authority.events.count("record_verified_result") == 1
    assert "enqueue_outbox" not in h.authority.events and "record_wait" not in h.authority.events


@pytest.mark.asyncio
async def test_foreign_wait_intent_rolls_back_result_preparation():
    h = harness()
    h.preparation.with_intents = h.preparation.foreign_wait = True
    assert await execute(h) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert "record_verified_result" not in h.authority.events
    assert h.journal.recorded_waits == h.journal.recorded_outbox == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [JournalCallTechnicalStatus.UNCERTAIN, JournalCallTechnicalStatus.CONFLICT]
)
async def test_unknown_command_or_fence_commit_forbids_source_io(failure):
    for target in ("fail_record", "fail_fence"):
        h = harness()
        setattr(h.journal, target, failure)
        await execute(h)
        assert h.source.calls == 0
        assert "record_verified_result" not in h.authority.events


@pytest.mark.asyncio
async def test_disabled_missing_ports_and_wrong_descriptor_do_not_fallback():
    h = harness(enabled=False)
    assert await execute(h) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.authority.events == []
    h = harness()
    h.custody.bad_descriptor = True
    assert await execute(h) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.source.calls == 0 and "record_command" not in h.authority.events
    h = harness()
    h.executor.preparation = None
    assert await execute(h) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.source.calls == 0


@pytest.mark.asyncio
async def test_revoked_after_audit_is_pre_dispatch_refusal_not_source_success():
    h = harness()
    h.authority.revoked = True
    assert await execute(h) == CapabilityRefusalReason.STALE_REVISION
    assert h.journal.snapshot.technical_state == CommandTechnicalState.REFUSED_BEFORE_DISPATCH
    assert h.source.calls == 0 and "begin_dispatch" not in h.authority.events


@pytest.mark.asyncio
async def test_revoked_during_db_fence_is_uncertain_and_never_calls_source():
    h = harness()
    h.journal.on_fence = lambda: setattr(h.authority, "revoked", True)
    assert await execute(h) == CapabilityRefusalReason.STALE_REVISION
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert h.source.calls == 0


@pytest.mark.asyncio
async def test_revoked_after_result_commit_preserves_fact_without_disclosure():
    h = harness()
    h.journal.on_result = lambda: setattr(h.authority, "revoked", True)
    assert await execute(h) == CapabilityRefusalReason.STALE_REVISION
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert "mark_uncertain" not in h.authority.events
    assert h.source.calls == 1


@pytest.mark.asyncio
async def test_bad_result_custody_digest_never_persists_fact():
    h = harness()
    h.preparation.bad_digest = True
    assert await execute(h) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert "record_verified_result" not in h.authority.events


@pytest.mark.asyncio
async def test_uncertain_result_write_and_duplicate_command_never_resubmit():
    h = harness()
    h.journal.fail_result = JournalCallTechnicalStatus.UNCERTAIN
    result = await execute(h)
    assert result.technical_status == JournalCallTechnicalStatus.UNCERTAIN
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    again = await execute(h, h.journal.snapshot.journal_revision)
    assert again.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert h.source.calls == 1


@pytest.mark.asyncio
async def test_completed_duplicate_is_reference_only_and_does_not_create_intents_again():
    h = harness()
    await execute(h)
    events = len(h.authority.events)
    again = await execute(h, h.journal.snapshot.journal_revision)
    assert again.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert h.authority.events[events:] == [
        "qualified_data",
        "record_command",
        "independent_current_head",
        "independent_read_current",
    ]
    assert h.source.calls == 1


@pytest.mark.asyncio
async def test_cancellation_after_fence_preserves_original_identity_and_drains_cleanup():
    h = harness()
    entered = asyncio.Event()

    class HangingSource:
        async def execute(self, env, request, *, timeout_seconds):
            entered.set()
            await asyncio.Event().wait()

    h.service = CapabilityService(
        admission=h.admission, sources={"reservation.command": HangingSource()}, durable=h.executor
    )
    task = asyncio.create_task(execute(h))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert h.journal.descriptor.envelope.idempotency_key == envelope().idempotency_key


@pytest.mark.asyncio
async def test_technical_timeout_after_fence_never_resets_or_retries_effect():
    h = harness()
    calls = []

    class HangingSource:
        async def execute(self, env, request, *, timeout_seconds):
            calls.append(env.idempotency_key)
            await asyncio.Event().wait()

    h.service = CapabilityService(
        admission=h.admission,
        sources={"reservation.command": HangingSource()},
        durable=h.executor,
        timeout_seconds=0.01,
    )
    assert await execute(h) == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    await execute(h, h.journal.snapshot.journal_revision)
    assert calls == [envelope().idempotency_key]


@pytest.mark.asyncio
async def test_wrong_journey_scope_or_source_custody_link_prevents_io():
    h = harness()
    result = await h.service.execute_durable(
        envelope(journey_ref="another-journey"),
        REQUEST,
        effect_authority=None,
        predecessor_command_refs=(),
        expected_journal_revision=0,
    )
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.authority.events == []


@pytest.mark.asyncio
async def test_malformed_result_keeps_original_fence_without_ref_claim():
    h = harness()

    class BadSource:
        async def execute(self, env, request, *, timeout_seconds):
            return {"ok": True, "patient_name": "SECRET-UNIT-PLANTED"}

    h.service = CapabilityService(
        admission=h.admission, sources={"reservation.command": BadSource()}, durable=h.executor
    )
    result = await execute(h)
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert "SECRET" not in str(result)
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert h.journal.snapshot.result_ref is None


@pytest.mark.asyncio
async def test_source_lookup_recovery_is_independent_read_and_never_execute():
    h = harness()
    h.preparation.bad_digest = True
    await execute(h)
    h.preparation.bad_digest = False
    h.authority.revoked = True  # Effect authorization cannot be renewed to recover.
    result = await h.service.reconcile_durable(
        "UNIT-command", expected_journal_revision=h.journal.snapshot.journal_revision
    )
    assert result.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert h.source.calls == 1
    assert "independent_read_lookup" in h.authority.events
    assert h.authority.events.count("authority") == 1
    assert h.authority.events.count("begin_dispatch") == 1


@pytest.mark.asyncio
async def test_lookup_absent_or_revoked_never_retries_source_or_allocates_key():
    for absent, revoked in ((True, False), (False, True)):
        h = harness()
        h.preparation.bad_digest = True
        await execute(h)
        h.preparation.bad_digest = False
        h.reads.absent, h.reads.revoked = absent, revoked
        result = await h.service.reconcile_durable(
            "UNIT-command", expected_journal_revision=h.journal.snapshot.journal_revision
        )
        assert isinstance(result, CapabilityRefusalReason)
        assert h.source.calls == 1
        assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN


@pytest.mark.asyncio
async def test_authenticated_inbox_and_duplicate_are_durable_before_ack_eligible_result():
    h = harness()
    h.preparation.bad_digest = True
    await execute(h)
    h.preparation.bad_digest = False
    proof = h.source_proof().model_copy(update={"authorization_ref": "UNIT-independent-read"})
    observation = VerifiedInboxObservation(
        event_ref="UNIT-actual-authenticated-event",
        source_authority_ref=envelope().source_authority_ref,
        producer_ref="UNIT-registered-producer",
        source_contract_revision_ref="UNIT-source-contract-revision",
        event_sha256="d" * 64,
        handle=h.journal.snapshot.handle,
        correlation_ref=envelope().correlation_ref,
        observation=VerifiedResultObservation(
            source_result=proof,
            result_ref="UNIT-protected-result",
            result_sha256=proof.result_sha256,
            observer_binding_ref="UNIT-qualified-observer",
            provenance_ref="UNIT-authenticated-event-proof",
        ),
    )
    result = await h.service.ingest_durable(
        observation, expected_journal_revision=h.journal.snapshot.journal_revision
    )
    assert result.technical_status == JournalCallTechnicalStatus.RECORDED
    revision = result.snapshot.journal_revision
    again = await h.service.ingest_durable(observation, expected_journal_revision=revision)
    assert again.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert again.snapshot.journal_revision == revision
    assert h.source.calls == 1
    assert h.authority.events.count("begin_dispatch") == 1


@pytest.mark.asyncio
async def test_observe_durable_returns_scoped_refs_without_source_or_effect_authorize():
    h = harness()
    await execute(h)
    previous = len(h.authority.events)
    result = await h.service.observe_durable("UNIT-command")
    assert result.snapshot.handle.command_ref == "UNIT-command"
    assert h.authority.events[previous:] == [
        "qualified_restore",
        "observe_command",
        "independent_current_head",
        "independent_read_current",
    ]
    assert h.source.calls == 1


@pytest.mark.asyncio
async def test_completed_replay_and_observe_require_independent_current_head_read():
    h = harness()
    await execute(h)
    h.reads.revoked = True
    assert await execute(h, h.journal.snapshot.journal_revision) == CapabilityRefusalReason.STALE_REVISION
    assert await h.service.observe_durable("UNIT-command") == CapabilityRefusalReason.STALE_REVISION
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert h.source.calls == 1
    h.reads.revoked = False
    h.reads.head_override = h.source_proof().model_copy(update={"source_revision_ref": "wrong-head"})
    assert await h.service.observe_durable("UNIT-command") == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.source.calls == 1


@pytest.mark.asyncio
async def test_old_service_is_compatible_and_new_durability_api_has_no_default():
    h = harness()
    service = CapabilityService(admission=h.admission, sources={"reservation.command": h.source})
    assert (
        await service.execute_durable(
            envelope(),
            REQUEST,
            effect_authority=None,
            predecessor_command_refs=(),
            expected_journal_revision=0,
        )
        == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )
    assert h.source.calls == 0
    legacy = await service.execute(envelope(), REQUEST)
    assert legacy.result == RESULT and h.source.calls == 1
