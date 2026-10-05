"""DUR1 real PostgreSQL component proofs, ROOT owns the only execution lane.

No CIB client/engine call is involved. Inputs are explicitly synthetic storage
metadata with test-only proof doubles, never provider/source/privacy qualification.
This module replaces the unrelated parent engine reachability fixture only; it
requires real PostgreSQL and adds no skip, xfail or deselection. Schema/roles/grants
for production remain a separate gate; these tests use a disposable local database.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.gateway.capabilities.durability.fixtures import (
    SyntheticProofs,
    binding,
    descriptor,
    dispatch_evidence,
    inbox,
    observation,
    outbox,
    wait,
)

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.gateway.capabilities.durability import postgres
from maezo.gateway.capabilities.durability.models import (
    CommandTechnicalState,
    JournalCallTechnicalStatus,
    JournalRefusalReason,
    OutboxAckEvidence,
    OutboxSnapshot,
    OutboxTechnicalState,
    PreDispatchRefusal,
    WaitTechnicalState,
)
from maezo.gateway.capabilities.durability.postgres import PostgresDurabilityJournal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable():
    """This is a PostgreSQL-only component: no engine is mocked or called."""


@pytest.fixture
async def db():
    dsn = normalize_dsn(_default_test_dsn())
    tenant = "dur1_" + uuid4().hex
    b = binding(tenant)
    schema = schema_for_tenant(tenant)
    admin = await asyncpg.connect(dsn, server_settings={"search_path": "pg_catalog"})
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.execute(f'SET search_path TO "{schema}",pg_catalog')
    await admin.execute(Path(postgres.__file__).with_name("schema.sql").read_text())
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, server_settings={"search_path": "pg_catalog"}
    )
    pool2 = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, server_settings={"search_path": "pg_catalog"}
    )
    proofs = SyntheticProofs()
    store = PostgresDurabilityJournal(pool=pool, binding=b, proofs=proofs, enabled=True)
    try:
        yield b, store, proofs, admin, pool, pool2, dsn
    finally:
        await pool.close()
        await pool2.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def acknowledged(result):
    assert result.technical_status in {
        JournalCallTechnicalStatus.RECORDED,
        JournalCallTechnicalStatus.UNCHANGED,
    }, result
    assert result.snapshot is not None
    return result.snapshot


async def fenced(db):
    b, store, *_ = db
    d = descriptor(b)
    command = acknowledged(await store.record_command(b, d, 0))
    token = acknowledged(await store.begin_dispatch(b, command.handle, dispatch_evidence(b, d), 1))
    return d, command.handle, token


async def test_literal_dedup_immutable_identity_and_restart_new_pool(db):
    b, store, proofs, _, _, pool2, _ = db
    d = descriptor(b)
    first = acknowledged(await store.record_command(b, d, 0))
    restarted = PostgresDurabilityJournal(pool=pool2, binding=b, proofs=proofs, enabled=True)
    duplicate = acknowledged(await restarted.record_command(b, d, 0))
    assert duplicate.handle == first.handle and duplicate.journal_revision == 1
    divergent = d.model_copy(update={"request_ref": "unit-another-protected-request"})
    assert (
        await restarted.record_command(b, divergent, 1)
    ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    other_binding = binding(b.tenant_ref, journey_ref="unit-other-journey")
    other = PostgresDurabilityJournal(pool=pool2, binding=other_binding, proofs=proofs, enabled=True)
    assert (
        await other.record_command(other_binding, descriptor(other_binding), 0)
    ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    assert acknowledged(await other.observe_journey(other_binding)).journal_revision == 0
    assert acknowledged(await restarted.observe_journey(b)).command_refs == (first.handle.command_ref,)
    # Original literal idempotency identity is not reconstructed/composed by the journal.
    assert d.envelope.idempotency_key == "unit-literal-key"
    assert d.envelope.expected_business_revision == "source:rev/opaque-Z"


async def test_two_pools_cas_race_has_one_winner_and_one_revision(db):
    b, store, proofs, _, _, pool2, _ = db
    other = PostgresDurabilityJournal(pool=pool2, binding=b, proofs=proofs, enabled=True)
    results = await asyncio.gather(
        store.record_command(b, descriptor(b, key="literal-A"), 0),
        other.record_command(b, descriptor(b, key="literal-B"), 0),
    )
    assert sorted(r.technical_status.value for r in results) == ["conflict", "recorded"]
    assert next(r for r in results if r.snapshot is None).refusal_reason == JournalRefusalReason.CAS_CONFLICT
    journey = acknowledged(await store.observe_journey(b))
    assert journey.journal_revision == 1 and len(journey.command_refs) == 1


async def test_fence_no_retry_unknown_recovery_and_pre_dispatch_boundary(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    evidence = await admin.fetchval("SELECT dispatch_evidence FROM v21_capability_command")
    assert json.loads(evidence)["audit_intent_ref"] == "unit-durable-audit-intent"
    unknown = acknowledged(await store.mark_uncertain(b, handle, token.dispatch_ref, 2))
    assert unknown.technical_state == CommandTechnicalState.UNCERTAIN
    assert (
        await store.begin_dispatch(b, handle, dispatch_evidence(b, d), 3)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    refusal = PreDispatchRefusal(
        reason=JournalRefusalReason.PROOF_UNAVAILABLE,
        evidence_ref="unit-refusal",
        observed_at=datetime.now(UTC),
    )
    assert (
        await store.record_pre_dispatch_refusal(b, handle, refusal, 3)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    recovery = acknowledged(await store.recover(b, 1, None))
    assert recovery.command_refs_for_reconciliation == (handle.command_ref,)
    assert recovery.journal_revision == 3
    assert acknowledged(await store.record_command(b, d, 0)).handle == handle


async def test_direct_result_wait_outbox_one_transaction_and_duplicate_cannot_add_intents(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    obs, w, o = observation(b, d), wait(handle), outbox(handle)
    first = acknowledged(
        await store.record_verified_result(b, handle, token.dispatch_ref, obs, (o,), (w,), 2)
    )
    assert first.technical_state == CommandTechnicalState.RESPONSE_RECORDED and first.journal_revision == 3
    journey = acknowledged(await store.observe_journey(b))
    assert journey.wait_refs == ("unit-wait",) and journey.outbox_refs == ("unit-outbox",)
    duplicate = await store.record_verified_result(b, handle, token.dispatch_ref, obs, (o,), (w,), 0)
    assert duplicate.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert duplicate.snapshot.journal_revision == 3
    divergent = await store.record_verified_result(
        b, handle, token.dispatch_ref, obs, (o, outbox(handle, ref="unit-extra-outbox")), (w,), 3
    )
    assert divergent.refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_observation") == 1
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_outbox") == 1
    assert await admin.fetchval("SELECT count(*) FROM v21_external_wait") == 1


async def test_constraint_fault_rolls_back_head_wait_outbox_and_revision(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    a = outbox(handle, ref="unit-one", dedupe_identity_ref="unit-same-return")
    collision = outbox(handle, ref="unit-two", dedupe_identity_ref="unit-same-return")
    result = await store.record_verified_result(
        b, handle, token.dispatch_ref, observation(b, d), (a, collision), (wait(handle),), 2
    )
    assert result.refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    unchanged = acknowledged(await store.observe_command(b, handle))
    assert (
        unchanged.technical_state == CommandTechnicalState.DISPATCH_FENCED and unchanged.journal_revision == 2
    )
    for table in ("v21_journal_observation", "v21_external_wait", "v21_journal_outbox"):
        assert await admin.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_revocation_after_write_rolls_back_before_commit_and_denies_duplicate_metadata(db):
    b, store, proofs, admin, *_ = db
    d, handle, token = await fenced(db)
    proofs.revoke_after_result_checks = 2
    failed = await store.record_verified_result(
        b, handle, token.dispatch_ref, observation(b, d), (outbox(handle),), (wait(handle),), 2
    )
    assert failed.snapshot is None and failed.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 2
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_observation") == 0
    assert (await store.record_command(b, d, 0)).refusal_reason == JournalRefusalReason.DATA_GATE_CLOSED
    assert (await store.recover(b, 1, None)).snapshot is None


async def test_first_callback_pending_then_lookup_new_head_and_old_event_replay(db):
    b, store, proofs, admin, *_ = db
    d, handle, token = await fenced(db)
    acknowledged(await store.mark_uncertain(b, handle, token.dispatch_ref, 2))
    first = observation(b, d, revision="source:Z")
    event = inbox(handle, first)
    initial = acknowledged(await store.ingest_verified_observation(b, event, None, (), (), 3))
    assert initial.source_revision_ref == "source:Z" and initial.journal_revision == 4
    later = observation(b, d, revision="source:A", status="held", witness="unit-Z-before-A")
    proofs.order.add(("source:Z", "source:A", "unit-Z-before-A"))
    final = acknowledged(await store.record_verified_result(b, handle, token.dispatch_ref, later, (), (), 4))
    assert final.source_revision_ref == "source:A" and final.journal_revision == 5
    replay = await store.ingest_verified_observation(b, event, None, (), (), 3)
    assert replay.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert replay.snapshot.source_revision_ref == "source:A" and replay.snapshot.journal_revision == 5
    divergent = event.model_copy(update={"event_sha256": "f" * 64})
    assert (
        await store.ingest_verified_observation(b, divergent, None, (), (), 5)
    ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_inbox") == 1
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_observation") == 2


async def test_poll_only_opaque_order_witness_missing_older_and_same_revision_divergence(db):
    b, store, proofs, *_ = db
    d, handle, token = await fenced(db)
    first = observation(b, d, revision="source:Z")
    acknowledged(await store.record_verified_result(b, handle, token.dispatch_ref, first, (), (), 2))
    later = observation(b, d, revision="source:A", status="held")
    missing = await store.record_verified_result(b, handle, token.dispatch_ref, later, (), (), 3)
    assert missing.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    later = later.model_copy(update={"source_revision_order_witness_ref": "unit-forward"})
    proofs.order.add(("source:Z", "source:A", "unit-forward"))
    acknowledged(await store.record_verified_result(b, handle, token.dispatch_ref, later, (), (), 3))
    older = first.model_copy(update={"source_revision_order_witness_ref": "unit-forward"})
    assert (
        await store.record_verified_result(b, handle, token.dispatch_ref, older, (), (), 4)
    ).refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    divergence = observation(b, d, revision="source:A", digest="d" * 64)
    assert (
        await store.record_verified_result(b, handle, token.dispatch_ref, divergence, (), (), 4)
    ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    current = acknowledged(await store.observe_command(b, handle))
    assert current.source_revision_ref == "source:A" and current.journal_revision == 4


class _RaceProofs(SyntheticProofs):
    """Hold the chosen writer at proof verification after its real aggregate lock."""

    def __init__(self, order, winning_revision):
        super().__init__()
        self.order = set(order)
        self.winning_revision = winning_revision
        self.winner_holds_lock = asyncio.Event()
        self.release_winner = asyncio.Event()
        self.result_attempts = []

    async def verify_result(self, b, d, dispatch, previous, candidate):
        accepted = await super().verify_result(b, d, dispatch, previous, candidate)
        previous_revision = previous.source_result.source_revision_ref if previous else None
        candidate_revision = candidate.source_result.source_revision_ref
        self.result_attempts.append(
            (previous_revision, candidate_revision, candidate.source_revision_order_witness_ref, accepted)
        )
        if candidate_revision == self.winning_revision and not self.winner_holds_lock.is_set():
            assert previous_revision == "source:origin" and accepted is True
            self.winner_holds_lock.set()
            await self.release_winner.wait()
        return accepted


class _LockAttemptConnection:
    """Observe the losing connection's lock query; execute all SQL on PostgreSQL."""

    def __init__(self, connection, attempted):
        self.connection, self.attempted = connection, attempted

    def __getattr__(self, name):
        return getattr(self.connection, name)

    async def fetchrow(self, query, *args, **kwargs):
        if "FROM v21_journey_journal WHERE" in query and query.endswith("FOR UPDATE"):
            self.attempted.set()
        return await self.connection.fetchrow(query, *args, **kwargs)


class _LockAttemptPool:
    def __init__(self, pool, attempted):
        self.pool, self.attempted = pool, attempted

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as connection:
            yield _LockAttemptConnection(connection, self.attempted)


@pytest.mark.parametrize("winner", ["lookup", "callback"])
async def test_lookup_callback_race_preserves_only_qualified_head_and_no_source_execute(db, winner):
    b, store, proofs, admin, pool, pool2, _ = db
    d, handle, token = await fenced(db)
    previous = observation(b, d, revision="source:origin")
    initial = acknowledged(
        await store.record_verified_result(b, handle, token.dispatch_ref, previous, (), (), 2)
    )
    assert initial.journal_revision == 3
    original_row = await admin.fetchrow(
        "SELECT command_ref,idempotency_key,descriptor,dispatch_evidence FROM v21_capability_command"
    )
    a = observation(b, d, revision="source:candidate-A", witness="unit-origin-A")
    z = observation(b, d, revision="source:candidate-Z", witness="unit-origin-Z")
    event = inbox(handle, z)
    proofs.order.update(
        {
            ("source:origin", "source:candidate-A", "unit-origin-A"),
            ("source:origin", "source:candidate-Z", "unit-origin-Z"),
        }
    )
    winning_observation = a if winner == "lookup" else z
    losing_observation = z if winner == "lookup" else a
    ordered_proofs = _RaceProofs(proofs.order, winning_observation.source_result.source_revision_ref)
    loser_attempted_lock = asyncio.Event()
    lookup_pool = pool if winner == "lookup" else _LockAttemptPool(pool, loser_attempted_lock)
    callback_pool = _LockAttemptPool(pool2, loser_attempted_lock) if winner == "lookup" else pool2
    lookup = PostgresDurabilityJournal(pool=lookup_pool, binding=b, proofs=ordered_proofs, enabled=True)
    callback = PostgresDurabilityJournal(pool=callback_pool, binding=b, proofs=ordered_proofs, enabled=True)

    async def query():
        return await lookup.record_verified_result(b, handle, token.dispatch_ref, a, (), (), 3)

    async def receive():
        return await callback.ingest_verified_observation(b, event, None, (), (), 3)

    winning_call, losing_call = (query, receive) if winner == "lookup" else (receive, query)
    tasks = []
    try:
        # Test watchdog only. Events choose the order; elapsed time never does.
        async with asyncio.timeout(10):
            winning_task = asyncio.create_task(winning_call())
            tasks.append(winning_task)
            await ordered_proofs.winner_holds_lock.wait()
            losing_task = asyncio.create_task(losing_call())
            tasks.append(losing_task)
            await loser_attempted_lock.wait()
            assert not winning_task.done() and not losing_task.done()
            ordered_proofs.release_winner.set()
            winning_result, losing_result = await asyncio.gather(winning_task, losing_task)
    finally:
        ordered_proofs.release_winner.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert winning_result.technical_status == JournalCallTechnicalStatus.RECORDED
    assert winning_result.refusal_reason is None
    assert winning_result.snapshot is not None and winning_result.snapshot.journal_revision == 4
    assert (
        sum(
            r.technical_status == JournalCallTechnicalStatus.RECORDED for r in (winning_result, losing_result)
        )
        == 1
    )
    assert losing_result.snapshot is None
    winning_proof = (
        "source:origin",
        winning_observation.source_result.source_revision_ref,
        winning_observation.source_revision_order_witness_ref,
        True,
    )
    assert winning_proof in ordered_proofs.result_attempts
    if winner == "lookup":
        assert losing_result.technical_status == JournalCallTechnicalStatus.CONFLICT
        assert losing_result.refusal_reason == JournalRefusalReason.CAS_CONFLICT
        assert all(attempt[1] != "source:candidate-Z" for attempt in ordered_proofs.result_attempts)
        assert await admin.fetchval("SELECT count(*) FROM v21_journal_inbox") == 0
    else:
        assert losing_result.technical_status == JournalCallTechnicalStatus.UNAVAILABLE
        assert losing_result.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
        assert (
            "source:candidate-Z",
            "source:candidate-A",
            "unit-origin-A",
            False,
        ) in ordered_proofs.result_attempts
        assert ("source:candidate-Z", "source:candidate-A", "unit-origin-A") not in ordered_proofs.order
        inbox_row = await admin.fetchrow("SELECT observation,applied_journal_revision FROM v21_journal_inbox")
        assert inbox_row is not None and inbox_row["applied_journal_revision"] == 4
        assert json.loads(inbox_row["observation"]) == event.model_dump(mode="json")
        assert await admin.fetchval("SELECT count(*) FROM v21_journal_inbox") == 1

    current = acknowledged(await store.observe_command(b, handle))
    assert current.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert current.journal_revision == initial.journal_revision + 1 == 4
    assert current.source_revision_ref == winning_observation.source_result.source_revision_ref
    assert current.result_ref == winning_observation.result_ref
    assert current.result_sha256 == winning_observation.result_sha256
    assert current.handle == handle
    assert current.dispatch_ref == token.dispatch_ref and current.fence_version == token.fence_version == 1
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 4
    observations = await admin.fetch("SELECT observation_ref,observation FROM v21_journal_observation")
    assert len(observations) == 2
    assert {
        json.loads(row["observation"])["source_result"]["source_revision_ref"] for row in observations
    } == {"source:origin", winning_observation.source_result.source_revision_ref}
    assert all(
        json.loads(row["observation"])["source_result"]["source_revision_ref"]
        != losing_observation.source_result.source_revision_ref
        for row in observations
    )
    assert (
        await admin.fetchval("SELECT head_ref FROM v21_capability_command")
        == current.verified_result_observation_ref
    )
    assert current.verified_result_observation_ref in {row["observation_ref"] for row in observations}
    for table in ("v21_external_wait", "v21_journal_outbox"):
        assert await admin.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert await admin.fetchval("SELECT count(*) FROM v21_capability_command") == 1
    persisted_row = await admin.fetchrow(
        "SELECT command_ref,idempotency_key,descriptor,dispatch_evidence FROM v21_capability_command"
    )
    assert persisted_row == original_row
    assert persisted_row["command_ref"] == handle.command_ref
    assert persisted_row["idempotency_key"] == d.envelope.idempotency_key == "unit-literal-key"
    assert (
        await store.begin_dispatch(b, handle, dispatch_evidence(b, d), 4)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    assert acknowledged(await store.observe_command(b, handle)) == current
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 4


async def test_operational_elapsed_wait_authenticated_settlement_and_no_reopening(db):
    b, store, _, admin, *_ = db
    d, handle, _ = await fenced(db)
    w = wait(handle, wakeup=datetime.now(UTC) - timedelta(seconds=1))
    acknowledged(await store.record_wait(b, w, 2))
    elapsed = acknowledged(await store.mark_wait_elapsed(b, w.intent.wait_ref, datetime.now(UTC), 3))
    assert elapsed.technical_state == WaitTechnicalState.ELAPSED
    # Technical elapsed status contains no domain timeout/negative/business resolution.
    assert set(elapsed.model_fields) == {
        "descriptor",
        "journal_revision",
        "technical_state",
        "source_observation_ref",
        "elapsed_observed_at",
    }
    bad = inbox(handle, observation(b, d), producer_ref="unit-unregistered-producer")
    assert (await store.ingest_verified_observation(b, bad, w.intent.wait_ref, (), (), 4)).snapshot is None
    event = inbox(handle, observation(b, d))
    same_ref_new = await store.ingest_verified_observation(b, event, w.intent.wait_ref, (), (w,), 4)
    assert same_ref_new.refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    acknowledged(
        await store.ingest_verified_observation(
            b, event, w.intent.wait_ref, (outbox(handle),), (wait(handle, ref="unit-next-wait"),), 4
        )
    )
    same = acknowledged(await store.record_wait(b, w, 0))
    assert same.technical_state == WaitTechnicalState.OBSERVATION_RECORDED and same.journal_revision == 5
    assert await admin.fetchval("SELECT count(*) FROM v21_external_wait") == 2
    assert (
        await store.mark_wait_elapsed(b, w.intent.wait_ref, datetime.now(UTC), 5)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION


async def test_outbox_claim_fencing_no_retry_after_unknown_and_ack_is_not_domain_receipt(db):
    b, store, _, _, *_ = db
    _, handle, _ = await fenced(db)
    acknowledged(await store.enqueue_outbox(b, outbox(handle), 2))
    first = acknowledged(
        await store.claim_outbox(
            b, "unit-outbox", "unit-worker-A", datetime.now(UTC) + timedelta(milliseconds=150), 3
        )
    )
    await asyncio.sleep(0.2)
    second = acknowledged(
        await store.claim_outbox(
            b, "unit-outbox", "unit-worker-B", datetime.now(UTC) + timedelta(minutes=1), 4
        )
    )
    assert second.fence_version == first.fence_version + 1 and second.claim_ref != first.claim_ref
    assert (
        await store.begin_outbox_delivery(b, "unit-outbox", first.claim_ref, 5)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    delivery = acknowledged(await store.begin_outbox_delivery(b, "unit-outbox", second.claim_ref, 5))
    assert delivery.technical_state == OutboxTechnicalState.DELIVERY_FENCED
    unknown = acknowledged(await store.mark_outbox_uncertain(b, "unit-outbox", delivery.delivery_ref, 6))
    assert unknown.technical_state == OutboxTechnicalState.UNCERTAIN
    assert (
        await store.claim_outbox(
            b, "unit-outbox", "unit-worker-C", datetime.now(UTC) + timedelta(minutes=1), 7
        )
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    ack = OutboxAckEvidence(
        outbox_ref="unit-outbox",
        delivery_ref=delivery.delivery_ref,
        target_binding_ref=delivery.descriptor.target_binding_ref,
        payload_sha256=delivery.descriptor.payload_sha256,
        acknowledgement_ref="unit-authenticated-target-ack",
        acknowledgement_sha256="d" * 64,
        verifier_binding_ref="unit-ack-verifier",
        observed_at=datetime.now(UTC),
    )
    forged = ack.model_copy(update={"payload_sha256": "f" * 64})
    assert (
        await store.record_outbox_ack(b, "unit-outbox", delivery.delivery_ref, forged, 7)
    ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
    acknowledged_ack = acknowledged(
        await store.record_outbox_ack(b, "unit-outbox", delivery.delivery_ref, ack, 7)
    )
    assert acknowledged_ack.technical_state == OutboxTechnicalState.ACK_RECORDED
    assert acknowledged_ack.acknowledgement_ref == "unit-authenticated-target-ack"
    assert not hasattr(acknowledged_ack, "provider_delivery_receipt_ref")
    assert (
        await store.record_outbox_ack(b, "unit-outbox", delivery.delivery_ref, ack, 0)
    ).technical_status == JournalCallTechnicalStatus.UNCHANGED


async def test_tenant_isolation_per_acquire_and_cursor_scope(db):
    b, store, proofs, admin, pool, _, _ = db
    d, handle, _ = await fenced(db)
    other_binding = binding("dur1_other_" + uuid4().hex)
    other_schema = schema_for_tenant(other_binding.tenant_ref)
    await admin.execute(f'CREATE SCHEMA "{other_schema}"')
    try:
        await admin.execute(f'SET search_path TO "{other_schema}",pg_catalog')
        await admin.execute(Path(postgres.__file__).with_name("schema.sql").read_text())
        other = PostgresDurabilityJournal(pool=pool, binding=other_binding, proofs=proofs, enabled=True)
        own = acknowledged(await other.record_command(other_binding, descriptor(other_binding), 0))
        assert (
            await other.observe_command(other_binding, handle)
        ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
        assert (
            await store.observe_command(b, own.handle)
        ).refusal_reason == JournalRefusalReason.IDENTITY_CONFLICT
        for current, bound in ((store, b), (other, other_binding), (store, b)):
            assert len(acknowledged(await current.observe_journey(bound)).command_refs) == 1
        async with pool.acquire() as connection:
            assert await connection.fetchval("SHOW search_path") == "pg_catalog"
        assert (
            await other.recover(other_binding, 1, "command:" + handle.command_ref)
        ).refusal_reason == JournalRefusalReason.BINDING_DENIED
        assert (await store.recover(b, True, None)).refusal_reason == JournalRefusalReason.CONTRACT_MISMATCH
        assert acknowledged(await store.record_command(b, d, 0)).handle == handle
    finally:
        await admin.execute(f'DROP SCHEMA "{other_schema}" CASCADE')


class LostCommitAck:
    """Fault after real PostgreSQL commit: no fake engine/database durability."""

    def __init__(self, transaction):
        self.transaction = transaction

    async def start(self):
        await self.transaction.start()

    async def commit(self):
        await self.transaction.commit()
        raise ConnectionResetError("synthetic lost local acknowledgement")

    async def rollback(self):
        await self.transaction.rollback()


class ConnectionWithLostAck:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self, **kwargs):
        return LostCommitAck(self.connection.transaction(**kwargs))

    def __getattr__(self, name):
        return getattr(self.connection, name)


class PoolWithLostAck:
    def __init__(self, pool):
        self.pool = pool

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as connection:
            yield ConnectionWithLostAck(connection)


async def test_actual_commit_with_lost_ack_returns_no_token_and_restart_finds_fence(db):
    b, store, proofs, _, pool, pool2, _ = db
    d = descriptor(b)
    first = acknowledged(await store.record_command(b, d, 0))
    lost = PostgresDurabilityJournal(pool=PoolWithLostAck(pool), binding=b, proofs=proofs, enabled=True)
    uncertain = await lost.begin_dispatch(b, first.handle, dispatch_evidence(b, d), 1)
    assert uncertain.technical_status == JournalCallTechnicalStatus.UNCERTAIN
    assert uncertain.refusal_reason == JournalRefusalReason.COMMIT_UNCERTAIN and uncertain.snapshot is None
    restarted = PostgresDurabilityJournal(pool=pool2, binding=b, proofs=proofs, enabled=True)
    current = acknowledged(await restarted.observe_command(b, first.handle))
    assert current.technical_state == CommandTechnicalState.DISPATCH_FENCED and current.journal_revision == 2
    assert (
        await restarted.begin_dispatch(b, first.handle, dispatch_evidence(b, d), 2)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION
    assert acknowledged(await restarted.recover(b, 1, None)).command_refs_for_reconciliation == (
        first.handle.command_ref,
    )


async def test_database_guards_reject_command_fence_reset_and_observation_overwrite(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute(
            "UPDATE v21_capability_command SET "
            "snapshot=jsonb_set(snapshot,'{technical_state}','\"RECORDED\"')"
        )
    assert (
        acknowledged(await store.observe_command(b, handle)).technical_state
        == CommandTechnicalState.DISPATCH_FENCED
    )
    acknowledged(
        await store.record_verified_result(b, handle, token.dispatch_ref, observation(b, d), (), (), 2)
    )
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute(
            "UPDATE v21_journal_observation SET observation=observation || "
            '\'{"provenance_ref":"planted"}\'::jsonb'
        )
    assert acknowledged(await store.observe_command(b, handle)).journal_revision == 3


class CommitRevokesProof:
    """Real commit acknowledgement race with the synthetic metadata data gate."""

    def __init__(self, transaction, proofs):
        self.transaction, self.proofs = transaction, proofs

    async def start(self):
        await self.transaction.start()

    async def commit(self):
        await self.transaction.commit()
        self.proofs.allowed = False

    async def rollback(self):
        await self.transaction.rollback()


class RevokingConnection(ConnectionWithLostAck):
    def __init__(self, connection, proofs):
        super().__init__(connection)
        self.proofs = proofs

    def transaction(self, **kwargs):
        return CommitRevokesProof(self.connection.transaction(**kwargs), self.proofs)


class RevokingPool(PoolWithLostAck):
    def __init__(self, pool, proofs):
        super().__init__(pool)
        self.proofs = proofs

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as connection:
            yield RevokingConnection(connection, self.proofs)


async def test_data_gate_revoked_at_commit_ack_discloses_no_token_but_keeps_fence(db):
    b, store, proofs, _, pool, *_ = db
    d = descriptor(b)
    first = acknowledged(await store.record_command(b, d, 0))
    revoking = PostgresDurabilityJournal(
        pool=RevokingPool(pool, proofs), binding=b, proofs=proofs, enabled=True
    )
    denied = await revoking.begin_dispatch(b, first.handle, dispatch_evidence(b, d), 1)
    assert denied.snapshot is None and denied.refusal_reason == JournalRefusalReason.DATA_GATE_CLOSED
    assert (await store.observe_command(b, first.handle)).snapshot is None
    proofs.allowed = True  # Explicit restoration of the synthetic fixture gate only.
    recovered = acknowledged(await store.observe_command(b, first.handle))
    assert recovered.technical_state == CommandTechnicalState.DISPATCH_FENCED
    assert recovered.journal_revision == 2
    assert (
        await store.begin_dispatch(b, first.handle, dispatch_evidence(b, d), 2)
    ).refusal_reason == JournalRefusalReason.INVALID_TRANSITION


async def test_new_event_same_head_refuses_stale_cas_then_advances_once_not_replay(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    obs = observation(b, d)
    head = acknowledged(await store.record_verified_result(b, handle, token.dispatch_ref, obs, (), (), 2))
    event = inbox(handle, obs, event="unit-new-same-head-event")
    stale = await store.ingest_verified_observation(b, event, None, (), (), 0)
    assert stale.refusal_reason == JournalRefusalReason.CAS_CONFLICT and stale.snapshot is None
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_inbox") == 0
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 3
    current = await store.ingest_verified_observation(b, event, None, (), (), 3)
    assert current.technical_status == JournalCallTechnicalStatus.RECORDED
    assert current.snapshot.journal_revision == 4
    assert current.snapshot.verified_result_observation_ref == head.verified_result_observation_ref
    assert await admin.fetchval("SELECT applied_journal_revision FROM v21_journal_inbox") == 4
    duplicate = await store.ingest_verified_observation(b, event, None, (), (), 0)
    assert duplicate.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert duplicate.snapshot.journal_revision == 4
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_inbox") == 1
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_observation") == 1
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 4


@pytest.mark.parametrize(
    "field", ["payload_ref", "payload_sha256", "target_binding_ref", "dedupe_identity_ref", "command_ref"]
)
async def test_database_outbox_snapshot_cannot_change_original_descriptor_on_permitted_transition(db, field):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    original = outbox(handle)
    acknowledged(
        await store.record_verified_result(
            b, handle, token.dispatch_ref, observation(b, d), (original,), (), 2
        )
    )
    row = await admin.fetchrow("SELECT descriptor,snapshot FROM v21_journal_outbox")
    planted = json.loads(row["snapshot"])
    planted.update(
        technical_state="CLAIMED",
        claim_ref="unit-claim",
        worker_ref="unit-worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        fence_version=1,
    )
    planted["descriptor"][field] = "f" * 64 if field.endswith("sha256") else "unit-planted-protected-ref"
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute("UPDATE v21_journal_outbox SET snapshot=$1::jsonb", json.dumps(planted))
    preserved = await admin.fetchrow("SELECT descriptor,snapshot FROM v21_journal_outbox")
    assert preserved == row
    assert json.loads(preserved["descriptor"]) == json.loads(preserved["snapshot"])["descriptor"]
    assert await admin.fetchval("SELECT journal_revision FROM v21_journey_journal") == 3


async def test_database_outbox_descriptor_equality_also_applies_on_insert_and_fence_lease_is_immutable(db):
    b, store, _, admin, *_ = db
    d, handle, token = await fenced(db)
    original = outbox(handle)
    acknowledged(
        await store.record_verified_result(
            b, handle, token.dispatch_ref, observation(b, d), (original,), (), 2
        )
    )
    desc = original.model_copy(
        update={"outbox_ref": "unit-new-outbox", "dedupe_identity_ref": "unit-new-dedupe"}
    )
    snapshot = {
        "descriptor": desc.model_dump(mode="json") | {"payload_ref": "unit-planted-payload"},
        "journal_revision": 3,
        "technical_state": "RECORDED",
    }
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute(
            "INSERT INTO v21_journal_outbox (environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,outbox_ref,command_ref,descriptor,snapshot) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)",
            b.environment_ref,
            b.tenant_ref,
            b.legal_entity_ref,
            b.journey_ref,
            b.principal_ref,
            b.task_ref,
            desc.outbox_ref,
            desc.command_ref,
            desc.model_dump_json(),
            json.dumps(snapshot),
        )
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_outbox") == 1
    claim = acknowledged(
        await store.claim_outbox(
            b, original.outbox_ref, "unit-worker", datetime.now(UTC) + timedelta(minutes=1), 3
        )
    )
    delivery = acknowledged(await store.begin_outbox_delivery(b, original.outbox_ref, claim.claim_ref, 4))
    planted = delivery.model_dump(mode="json") | {
        "technical_state": "UNCERTAIN",
        "lease_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute("UPDATE v21_journal_outbox SET snapshot=$1::jsonb", json.dumps(planted))
    saved = json.loads(await admin.fetchval("SELECT snapshot FROM v21_journal_outbox"))
    assert saved["technical_state"] == "DELIVERY_FENCED"
    assert saved["lease_until"] == delivery.model_dump(mode="json")["lease_until"]


async def test_database_outbox_rejects_consistent_descriptor_linked_to_foreign_journey_command(db):
    b, _, proofs, admin, pool, *_ = db
    other_binding = b.model_copy(update={"journey_ref": "unit-other-journey"})
    other = PostgresDurabilityJournal(pool=pool, binding=other_binding, proofs=proofs, enabled=True)
    command = acknowledged(await other.record_command(other_binding, descriptor(other_binding), 0))
    original = outbox(command.handle)
    snapshot = OutboxSnapshot(
        descriptor=original, journal_revision=0, technical_state=OutboxTechnicalState.RECORDED
    )
    # Create the caller journey aggregate, then attempt a cross-journey command FK.
    local = PostgresDurabilityJournal(pool=pool, binding=b, proofs=proofs, enabled=True)
    acknowledged(await local.record_command(b, descriptor(b, key="literal-local-command"), 0))
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute(
            "INSERT INTO v21_journal_outbox (environment_ref,tenant_ref,legal_entity_ref,journey_ref,"
            "principal_ref,task_ref,outbox_ref,command_ref,descriptor,snapshot) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)",
            b.environment_ref,
            b.tenant_ref,
            b.legal_entity_ref,
            b.journey_ref,
            b.principal_ref,
            b.task_ref,
            original.outbox_ref,
            original.command_ref,
            original.model_dump_json(),
            snapshot.model_dump_json(),
        )
    assert await admin.fetchval("SELECT count(*) FROM v21_journal_outbox") == 0


@pytest.mark.parametrize("proof_kind", ["result", "dispatch", "claim", "delivery"])
async def test_expiry_crossed_by_final_verifier_denies_return_preserving_database_fence(
    db, monkeypatch, proof_kind
):
    b, store, _, admin, pool, *_ = db
    d = descriptor(b)
    command = acknowledged(await store.record_command(b, d, 0))
    token = None
    if proof_kind != "dispatch":
        token = acknowledged(await store.begin_dispatch(b, command.handle, dispatch_evidence(b, d), 1))
    if proof_kind in {"claim", "delivery"}:
        acknowledged(
            await store.record_verified_result(
                b, command.handle, token.dispatch_ref, observation(b, d), (outbox(command.handle),), (), 2
            )
        )
    now = datetime.now(UTC)
    expiry = now + timedelta(seconds=5)
    if proof_kind == "delivery":
        claim = acknowledged(await store.claim_outbox(b, "unit-outbox", "unit-worker", expiry, 3))
    clock = SimpleNamespace(now=now)
    monkeypatch.setattr(postgres, "datetime", SimpleNamespace(now=lambda zone: clock.now))

    class ExpiringProofs(SyntheticProofs):
        def __init__(self):
            super().__init__()
            self.selected_calls = 0

        async def selected(self):
            self.selected_calls += 1
            if self.selected_calls == 3:
                clock.now = expiry + timedelta(microseconds=1)
            return True

        async def verify_dispatch(self, *args):
            return await self.selected()

        async def verify_result(self, *args):
            return await self.selected()

        async def verify_outbox(self, *args):
            return await self.selected()

    proofs = ExpiringProofs()
    guarded = PostgresDurabilityJournal(pool=pool, binding=b, proofs=proofs, enabled=True)
    if proof_kind == "dispatch":
        evidence = dispatch_evidence(b, d)
        evidence = evidence.model_copy(
            update={
                "authority": evidence.authority.model_copy(
                    update={"verified_at": now, "valid_until": expiry}
                ),
                "currentness": evidence.currentness.model_copy(
                    update={"checked_at": now, "valid_until": expiry}
                ),
            }
        )
        result = await guarded.begin_dispatch(b, command.handle, evidence, 1)
        table, state = "v21_capability_command", "DISPATCH_FENCED"
    elif proof_kind == "result":
        obs = observation(b, d)
        obs = obs.model_copy(
            update={
                "source_result": obs.source_result.model_copy(
                    update={"checked_at": now, "valid_until": expiry}
                )
            }
        )
        result = await guarded.record_verified_result(b, command.handle, token.dispatch_ref, obs, (), (), 2)
        table, state = "v21_capability_command", "RESPONSE_RECORDED"
    elif proof_kind == "claim":
        result = await guarded.claim_outbox(b, "unit-outbox", "unit-worker", expiry, 3)
        table, state = "v21_journal_outbox", "CLAIMED"
    else:
        result = await guarded.begin_outbox_delivery(b, "unit-outbox", claim.claim_ref, 4)
        table, state = "v21_journal_outbox", "DELIVERY_FENCED"
    assert proofs.selected_calls == 3 and clock.now > expiry
    assert result.snapshot is None and result.refusal_reason == JournalRefusalReason.PROOF_UNAVAILABLE
    assert await admin.fetchval(f"SELECT snapshot->>'technical_state' FROM {table}") == state
