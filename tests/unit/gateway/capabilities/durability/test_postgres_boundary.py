"""UNIT gate and unknown-ACK tests only; no fake engine or durability claim."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.capabilities.durability import postgres
from maezo.gateway.capabilities.durability.models import (
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
    JournalCallTechnicalStatus,
    JournalRefusalReason,
    JourneySnapshot,
    OutboxSnapshot,
    OutboxTechnicalState,
)
from maezo.gateway.capabilities.durability.postgres import (
    PostgresDurabilityJournal,
    _accepted,
    _RefusedError,
    _Work,
)
from tests.unit.gateway.capabilities.durability.fixtures import (
    SyntheticProofs,
    binding,
    descriptor,
    dispatch_evidence,
    inbox,
    observation,
    outbox,
)


class NoDatabase:
    def acquire(self):
        raise AssertionError("data gate must close before database access")


@pytest.mark.parametrize("enabled,proof", [(False, None), (False, SyntheticProofs()), (True, None)])
async def test_inert_configuration_cannot_access_storage(enabled, proof):
    b = binding()
    store = PostgresDurabilityJournal(pool=NoDatabase(), binding=b, enabled=enabled, proofs=proof)
    result = await store.record_command(b, descriptor(b), 0)
    assert result.technical_status == JournalCallTechnicalStatus.UNAVAILABLE
    assert result.snapshot is None
    assert result.refusal_reason in {
        JournalRefusalReason.DATA_GATE_CLOSED,
        JournalRefusalReason.PROOF_UNAVAILABLE,
    }


async def test_proof_presence_or_revocation_does_not_grant_metadata_reads_or_recovery():
    b, proofs = binding(), SyntheticProofs()
    proofs.allowed = False
    store = PostgresDurabilityJournal(pool=NoDatabase(), binding=b, proofs=proofs, enabled=True)
    for result in (await store.observe_journey(b), await store.recover(b, 1, None)):
        assert result.refusal_reason == JournalRefusalReason.DATA_GATE_CLOSED
        assert result.snapshot is None


async def test_wrong_binding_and_bool_cas_reject_before_database_access():
    b = binding()
    store = PostgresDurabilityJournal(pool=NoDatabase(), binding=b, proofs=SyntheticProofs(), enabled=True)
    assert (
        await store.observe_journey(binding(tenant="other"))
    ).refusal_reason == JournalRefusalReason.BINDING_DENIED
    assert (
        await store.record_command(b, descriptor(b), True)
    ).refusal_reason == JournalRefusalReason.CONTRACT_MISMATCH


@pytest.mark.parametrize("expected", [True, False, None, -1, 1.0, "1"])
async def test_explicit_local_cas_cannot_be_omitted_or_coerced(expected):
    b = binding()
    store = PostgresDurabilityJournal(pool=NoDatabase(), binding=b, proofs=SyntheticProofs(), enabled=True)
    result = await store.record_command(b, descriptor(b), expected)
    assert result.refusal_reason == JournalRefusalReason.CONTRACT_MISMATCH
    assert result.snapshot is None


class UnitTransaction:
    async def start(self):
        pass

    async def commit(self):
        raise ConnectionResetError("synthetic driver diagnostics must remain private")

    async def rollback(self):
        pass


class UnitConnection:
    def transaction(self, **kwargs):
        return UnitTransaction()

    async def execute(self, *args):
        return None

    async def fetchval(self, *args):
        from maezo.gateway.audit_postgres import schema_for_tenant

        return schema_for_tenant(binding().tenant_ref)

    async def fetchrow(self, *args):
        return None


class UnitPool:
    @asynccontextmanager
    async def acquire(self):
        yield UnitConnection()


async def test_unknown_local_commit_is_sanitized_and_has_no_dispatch_token():
    b = binding()
    store = PostgresDurabilityJournal(pool=UnitPool(), binding=b, proofs=SyntheticProofs(), enabled=True)

    async def action(work):
        return _accepted(
            JourneySnapshot(binding=b, journal_revision=0, command_refs=(), wait_refs=(), outbox_refs=())
        )

    result = await store._run(b, "observe_journey", action)
    assert result.technical_status == JournalCallTechnicalStatus.UNCERTAIN
    assert result.refusal_reason == JournalRefusalReason.COMMIT_UNCERTAIN
    assert result.snapshot is None
    assert "diagnostics" not in result.model_dump_json()


class AcknowledgedUnitTransaction:
    """UNIT SQL-control double; this is not a PostgreSQL transaction proof."""

    def __init__(self, connection):
        self.connection = connection

    async def start(self):
        self.connection.original = (self.connection.revision, self.connection.inbox_row)

    async def commit(self):
        self.connection.committed = True

    async def rollback(self):
        self.connection.rolled_back = True
        self.connection.revision, self.connection.inbox_row = self.connection.original


class JournalControlConnection(UnitConnection):
    def __init__(self, b, *, revision=9):
        self.b, self.revision = b, revision
        self.inbox_row = None
        self.inserts = self.bumps = 0
        self.committed = self.rolled_back = False

    def transaction(self, **kwargs):
        return AcknowledgedUnitTransaction(self)

    async def fetchrow(self, query, *args):
        if "FROM v21_journey_journal" in query:
            return {"binding": self.b.model_dump_json(), "journal_revision": self.revision}
        if "FROM v21_journal_inbox" in query:
            return self.inbox_row
        raise AssertionError("unexpected UNIT SQL control query")

    async def fetchval(self, query, *args):
        if query.startswith("UPDATE v21_journey_journal"):
            assert args[-1] == self.revision
            self.bumps += 1
            self.revision += 1
            return self.revision
        return await super().fetchval(query, *args)

    async def execute(self, query, *args):
        if query.startswith("INSERT INTO v21_journal_inbox"):
            self.inserts += 1
            self.inbox_row = {"observation": args[-3], "causal_intents": args[-2], "revision": args[-1]}


class JournalControlPool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class SameHeadStore(PostgresDurabilityJournal):
    """Controls only pre-existing command/head reads, retaining actual inbox logic."""

    def __init__(self, connection, proofs):
        super().__init__(
            pool=JournalControlPool(connection), binding=connection.b, proofs=proofs, enabled=True
        )
        self.d = descriptor(connection.b)
        self.handle = CommandHandle(
            binding=connection.b, command_ref="unit-command", request_sha256=self.d.request_sha256
        )
        self.obs = observation(connection.b, self.d)
        self.snapshot = CommandSnapshot(
            handle=self.handle,
            journal_revision=9,
            technical_state=CommandTechnicalState.RESPONSE_RECORDED,
            dispatch_ref="unit-dispatch",
            fence_version=1,
            verified_result_observation_ref="unit-head",
            result_ref=self.obs.result_ref,
            result_sha256=self.obs.result_sha256,
            source_receipt_ref=self.obs.source_result.source_receipt_ref,
            source_revision_ref=self.obs.source_result.source_revision_ref,
            recorded_at=datetime.now(UTC),
            last_observed_at=datetime.now(UTC),
        )

    async def _command(self, work, handle):
        assert handle == self.handle
        return self.d, self.snapshot.model_copy(update={"journal_revision": work.revision})

    async def _previous(self, work, snapshot):
        return self.obs, self._intents((), (), None)


@pytest.mark.parametrize("expected", [0, 9])
async def test_new_event_same_head_requires_cas_and_one_inbox_revision(expected):
    b, proofs = binding(), SyntheticProofs()
    connection = JournalControlConnection(b)
    store = SameHeadStore(connection, proofs)
    event = inbox(store.handle, store.obs, event="unit-new-identity")
    result = await store.ingest_verified_observation(b, event, None, (), (), expected)
    if expected == 0:
        assert result.refusal_reason == JournalRefusalReason.CAS_CONFLICT
        assert result.snapshot is None
        assert connection.inserts == connection.bumps == 0
        assert connection.rolled_back and not connection.committed
        assert connection.revision == 9
    else:
        assert result.technical_status == JournalCallTechnicalStatus.RECORDED
        assert result.snapshot.journal_revision == 10
        assert connection.inserts == connection.bumps == 1
        assert connection.inbox_row["revision"] == 10
        assert connection.committed
        # Exact durable inbox replay is read-only, even with stale expected CAS.
        duplicate = await store.ingest_verified_observation(b, event, None, (), (), 0)
        assert duplicate.technical_status == JournalCallTechnicalStatus.UNCHANGED
        assert duplicate.snapshot.journal_revision == 10
        assert connection.inserts == connection.bumps == 1


@pytest.mark.parametrize("reader", ["read", "existing_intent"])
async def test_outbox_reader_refuses_snapshot_descriptor_divergence(reader):
    b, d = binding(), descriptor(binding())
    handle = CommandHandle(binding=b, command_ref="unit-command", request_sha256=d.request_sha256)
    original = outbox(handle)
    planted = original.model_copy(update={"payload_ref": "unit-planted-protected-payload"})
    snapshot = OutboxSnapshot(
        descriptor=planted, journal_revision=9, technical_state=OutboxTechnicalState.RECORDED
    )

    class StoredRow:
        async def fetchrow(self, query, *args):
            if "FROM v21_capability_command" in query:
                command = CommandSnapshot(
                    handle=handle,
                    journal_revision=9,
                    technical_state=CommandTechnicalState.RECORDED,
                    recorded_at=datetime.now(UTC),
                    last_observed_at=datetime.now(UTC),
                )
                return {"snapshot": command.model_dump_json()}
            return {
                "descriptor": original.model_dump_json(),
                "snapshot": snapshot.model_dump_json(),
                **b.model_dump(),
                "outbox_ref": original.outbox_ref,
                "command_ref": original.command_ref,
            }

    store = PostgresDurabilityJournal(pool=NoDatabase(), binding=b, proofs=SyntheticProofs(), enabled=True)
    with pytest.raises(_RefusedError) as error:
        if reader == "read":
            await store._outbox(_Work(StoredRow(), b, 9), original.outbox_ref)
        else:
            await store._check_outbox(_Work(StoredRow(), b, 9), planted)
    assert error.value.reason == JournalRefusalReason.IDENTITY_CONFLICT


@pytest.mark.parametrize("proof_kind", ["result", "dispatch", "claim", "delivery"])
async def test_typed_expiry_crossed_by_final_proof_hides_snapshot_without_reset(proof_kind, monkeypatch):
    b = binding()
    d = descriptor(b)
    obs = observation(b, d)
    evidence = dispatch_evidence(b, d)
    now = max(obs.source_result.checked_at, evidence.currentness.checked_at)
    expiry = now + timedelta(seconds=1)
    clock = SimpleNamespace(now=now)
    monkeypatch.setattr(postgres, "datetime", SimpleNamespace(now=lambda zone: clock.now))

    class ExpiringProofs(SyntheticProofs):
        def __init__(self):
            super().__init__()
            self.selected_calls = 0

        async def cross_on_final(self):
            self.selected_calls += 1
            if self.selected_calls == 3:
                clock.now = expiry + timedelta(microseconds=1)
            return True

        async def verify_result(self, *args):
            return await self.cross_on_final()

        async def verify_dispatch(self, *args):
            return await self.cross_on_final()

        async def verify_outbox(self, *args):
            return await self.cross_on_final()

    proofs = ExpiringProofs()
    connection = JournalControlConnection(b, revision=1)

    class ControlledStore(PostgresDurabilityJournal):
        async def _command(self, work, handle):
            return d, CommandSnapshot(
                handle=handle,
                journal_revision=work.revision,
                technical_state=CommandTechnicalState.RECORDED,
                recorded_at=now,
                last_observed_at=now,
            )

        async def _save_command(self, work, snapshot, **kwargs):
            connection.saved_command = snapshot

        async def _outbox(self, work, ref):
            return OutboxSnapshot(
                descriptor=outbox(handle),
                journal_revision=work.revision,
                technical_state=OutboxTechnicalState.CLAIMED
                if proof_kind == "delivery"
                else OutboxTechnicalState.RECORDED,
                claim_ref="unit-claim" if proof_kind == "delivery" else None,
                worker_ref="unit-worker" if proof_kind == "delivery" else None,
                lease_until=expiry if proof_kind == "delivery" else None,
                fence_version=1 if proof_kind == "delivery" else None,
            )

        async def _save_outbox(self, work, snapshot):
            connection.saved_outbox = snapshot

    store = ControlledStore(pool=JournalControlPool(connection), binding=b, proofs=proofs, enabled=True)
    handle = CommandHandle(binding=b, command_ref="unit-command", request_sha256=d.request_sha256)
    if proof_kind == "result":
        obs = obs.model_copy(
            update={"source_result": obs.source_result.model_copy(update={"valid_until": expiry})}
        )

        async def action(work):
            await store._verify_result(work, d, "unit-dispatch", None, obs)
            return _accepted(
                JourneySnapshot(binding=b, journal_revision=1, command_refs=(), wait_refs=(), outbox_refs=())
            )

        result = await store._run(b, "observe_journey", action)
    elif proof_kind == "dispatch":
        evidence = evidence.model_copy(
            update={
                "authority": evidence.authority.model_copy(update={"valid_until": expiry}),
                "currentness": evidence.currentness.model_copy(update={"valid_until": expiry}),
            }
        )
        result = await store.begin_dispatch(b, handle, evidence, 1)
    elif proof_kind == "claim":
        result = await store.claim_outbox(b, "unit-outbox", "unit-worker", expiry, 1)
    else:
        result = await store.begin_outbox_delivery(b, "unit-outbox", "unit-claim", 1)
    assert proofs.selected_calls == 3
    assert clock.now >= expiry
    assert result.snapshot is None
    assert result.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    assert connection.committed and not connection.rolled_back
    if proof_kind == "dispatch":
        assert connection.saved_command.technical_state == CommandTechnicalState.DISPATCH_FENCED
    elif proof_kind == "delivery":
        assert connection.saved_outbox.technical_state == OutboxTechnicalState.DELIVERY_FENCED


async def test_later_awaited_proof_cannot_leave_earlier_result_bounds_stale(monkeypatch):
    b, d = binding(), descriptor(binding())
    obs = observation(b, d)
    expiry = obs.source_result.valid_until
    clock = SimpleNamespace(now=obs.source_result.checked_at)
    monkeypatch.setattr(postgres, "datetime", SimpleNamespace(now=lambda zone: clock.now))
    connection = JournalControlConnection(b, revision=1)
    store = PostgresDurabilityJournal(
        pool=JournalControlPool(connection), binding=b, proofs=SyntheticProofs(), enabled=True
    )
    later_calls = 0

    async def later_proof():
        nonlocal later_calls
        later_calls += 1
        if later_calls == 3:
            clock.now = expiry + timedelta(microseconds=1)
        return True

    async def action(work):
        await store._verify_result(work, d, "unit-dispatch", None, obs)
        await store._proof(work, later_proof)
        return _accepted(
            JourneySnapshot(binding=b, journal_revision=1, command_refs=(), wait_refs=(), outbox_refs=())
        )

    result = await store._run(b, "observe_journey", action)
    assert later_calls == 3
    assert connection.committed
    assert result.snapshot is None
    assert result.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE


@pytest.mark.parametrize("revoke_metadata", [True, False])
async def test_postcommit_source_await_rechecks_metadata_and_preserves_committed_inbox(revoke_metadata):
    """Independent metadata revocation does not invalidate a still-current source proof."""
    b = binding()
    connection = JournalControlConnection(b)

    class MetadataProofs(SyntheticProofs):
        def __init__(self):
            super().__init__()
            self.metadata_current = True
            self.postcommit_source = asyncio.Event()
            self.release_source = asyncio.Event()
            self.authorizations = []
            self.source_checks_after_commit = 0

        async def authorize(self, actual_binding, method):
            assert actual_binding == b and method == "ingest_verified_observation"
            self.authorizations.append((connection.committed, self.metadata_current))
            return self.metadata_current

        async def verify_result(self, *args):
            accepted = await super().verify_result(*args)
            if connection.committed:
                self.source_checks_after_commit += 1
                if not self.postcommit_source.is_set():
                    self.postcommit_source.set()
                    await self.release_source.wait()
            return accepted

    proofs = MetadataProofs()
    store = SameHeadStore(connection, proofs)
    event = inbox(store.handle, store.obs, event="unit-postcommit-metadata-identity")
    task = asyncio.create_task(store.ingest_verified_observation(b, event, None, (), (), 9))
    try:
        async with asyncio.timeout(5):
            await proofs.postcommit_source.wait()
            # Commit is known before an independent metadata lease changes during
            # the final source-verifier await; the source oracle still allows it.
            assert connection.committed and not connection.rolled_back
            assert connection.inserts == connection.bumps == 1
            assert connection.revision == 10
            assert proofs.authorizations[-1] == (True, True)
            proofs.metadata_current = not revoke_metadata
            proofs.release_source.set()
            result = await task
    finally:
        proofs.release_source.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert proofs.allowed is True and proofs.source_checks_after_commit == 1
    assert connection.committed and not connection.rolled_back
    assert connection.inserts == connection.bumps == 1 and connection.revision == 10
    committed_inbox = dict(connection.inbox_row)
    committed_fence = (store.snapshot.handle, store.snapshot.dispatch_ref, store.snapshot.fence_version)
    if revoke_metadata:
        assert result.snapshot is None
        assert result.technical_status == JournalCallTechnicalStatus.UNAVAILABLE
        assert result.refusal_reason == JournalRefusalReason.DATA_GATE_CLOSED
        assert proofs.authorizations[-1] == (True, False)
        assert "unit-command" not in result.model_dump_json()
        assert "unit-dispatch" not in result.model_dump_json()
    else:
        assert result.technical_status == JournalCallTechnicalStatus.RECORDED
        assert result.refusal_reason is None
        assert result.snapshot.handle == store.handle and result.snapshot.journal_revision == 10

    # Restoring only the explicit synthetic metadata gate permits a durable
    # exact-identity read. It cannot increment, reapply or reset the committed work.
    proofs.metadata_current = True
    replay = await store.ingest_verified_observation(b, event, None, (), (), 0)
    assert replay.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert replay.snapshot.handle == store.handle and replay.snapshot.journal_revision == 10
    assert connection.inserts == connection.bumps == 1 and connection.revision == 10
    assert connection.inbox_row == committed_inbox
    assert (
        store.snapshot.handle,
        store.snapshot.dispatch_ref,
        store.snapshot.fence_version,
    ) == committed_fence
    assert store.d.envelope.idempotency_key == "unit-literal-key"
    assert not connection.rolled_back


async def test_final_metadata_authorization_await_cannot_leave_source_validity_stale(monkeypatch):
    b, d = binding(), descriptor(binding())
    obs = observation(b, d)
    expiry = obs.source_result.valid_until
    clock = SimpleNamespace(now=obs.source_result.checked_at)
    monkeypatch.setattr(postgres, "datetime", SimpleNamespace(now=lambda zone: clock.now))
    connection = JournalControlConnection(b, revision=1)

    class MetadataAwaitExpiresSource(SyntheticProofs):
        async def authorize(self, actual_binding, method):
            assert actual_binding == b and method == "observe_journey"
            if connection.committed and self.result_checks == 3:
                await cross_expiry()
            return True

    crossed = False

    async def cross_expiry():
        nonlocal crossed
        # A separate awaited metadata authority call can consume the source
        # proof's remaining validity; no new product timeout is introduced.
        crossed = True
        clock.now = expiry + timedelta(microseconds=1)

    proofs = MetadataAwaitExpiresSource()
    store = PostgresDurabilityJournal(
        pool=JournalControlPool(connection), binding=b, proofs=proofs, enabled=True
    )

    async def action(work):
        await store._verify_result(work, d, "unit-dispatch", None, obs)
        return _accepted(
            JourneySnapshot(
                binding=b,
                journal_revision=1,
                command_refs=("unit-protected-command",),
                wait_refs=(),
                outbox_refs=(),
            )
        )

    result = await store._run(b, "observe_journey", action)
    assert result.snapshot is None
    assert result.technical_status == JournalCallTechnicalStatus.UNAVAILABLE
    assert result.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    assert crossed and clock.now >= expiry and proofs.result_checks == 3
    assert connection.committed and not connection.rolled_back
    assert connection.bumps == connection.inserts == 0 and connection.revision == 1


@pytest.mark.parametrize("authorization_phase", ["early", "final"])
async def test_postcommit_metadata_verifier_unavailable_hides_refs_without_reclassifying_known_commit(
    authorization_phase,
):
    b, d = binding(), descriptor(binding())
    obs = observation(b, d)
    connection = JournalControlConnection(b, revision=1)

    expected_result_checks = 2 if authorization_phase == "early" else 3

    class UnavailableFinalMetadata(SyntheticProofs):
        async def authorize(self, actual_binding, method):
            assert actual_binding == b and method == "observe_journey"
            if connection.committed and self.result_checks == expected_result_checks:
                raise ConnectionResetError("unit-private-metadata-verifier-diagnostics")
            return True

    proofs = UnavailableFinalMetadata()
    store = PostgresDurabilityJournal(
        pool=JournalControlPool(connection), binding=b, proofs=proofs, enabled=True
    )

    async def action(work):
        await store._verify_result(work, d, "unit-dispatch", None, obs)
        return _accepted(
            JourneySnapshot(
                binding=b,
                journal_revision=1,
                command_refs=("unit-protected-command",),
                wait_refs=(),
                outbox_refs=(),
            )
        )

    result = await store._run(b, "observe_journey", action)
    assert result.snapshot is None
    assert result.technical_status == JournalCallTechnicalStatus.UNAVAILABLE
    assert result.refusal_reason == JournalRefusalReason.DATA_GATE_CLOSED
    assert connection.committed and not connection.rolled_back
    assert connection.bumps == connection.inserts == 0 and connection.revision == 1
    assert proofs.result_checks == expected_result_checks
    assert "private-metadata" not in result.model_dump_json()
    assert "unit-protected-command" not in result.model_dump_json()
