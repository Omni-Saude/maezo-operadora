"""Consent lifecycle controls. SQL-shaped doubles do NOT qualify PostgreSQL."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from maezo.gateway import amh, tool_registry
from maezo.gateway import amh_consent_revision as revision
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.gateway.test_amh import consumer, harness  # noqa: F401
from tests.unit.gateway.test_amh_consent_revision import decision, guard, postgres_guard  # noqa: F401


class TransactionDouble:
    """Offline SQL adapter seam with explicit transactional rollback/unknown ACK.

    No SQL is parsed or executed. Real SQL tests below are separately marked.
    """

    def __init__(self):
        self.state = {"pending": {}, "floor": {}}
        self.fail_observe = False
        self.fail_after_commit = False
        self.lock = asyncio.Lock()
        self.calls = []

    @asynccontextmanager
    async def begin(self):
        async with self.lock:
            snapshot = deepcopy(self.state)
            yield self.connection(snapshot)
            self.state = snapshot
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise OSError("synthetic commit acknowledgment lost")

    @asynccontextmanager
    async def connect(self):
        yield self.connection(deepcopy(self.state))

    def connection(self, state):
        async def execute(statement, p):
            key = (p["domain"], p["subject_purpose"])
            pending, floors = state["pending"], state["floor"]
            row = None
            self.calls.append(str(statement))
            if statement is revision._BEGIN:
                if key not in pending:
                    pending[key] = p["token"]
                    row = (p["token"],)
            elif statement is revision._LOCK_PENDING:
                if key in pending:
                    row = (pending[key],)
            elif statement is revision._OBSERVE:
                if self.fail_observe:
                    raise OSError("synthetic observation rollback")
                old = floors.get(key)
                value = (p["revision"], p["decision"], False)
                if old is None or int(value[0]) > int(old[0]):
                    floors[key] = value
                elif int(value[0]) == int(old[0]):
                    floors[key] = (old[0], old[1], old[2] or old[1] != value[1])
                row = floors[key]
            elif statement is revision._COMPLETE:
                if pending.get(key) == p["token"]:
                    row = (pending.pop(key),)
            elif statement is revision._CURRENT:
                row = None if key in pending else floors.get(key)
            elif statement is revision._PENDING:
                if key in pending:
                    row = (pending[key], *floors.get(key, (None, None, None)))
            else:
                raise AssertionError("Unexpected SQL seam")
            return SimpleNamespace(one=lambda: row, one_or_none=lambda: row)

        return SimpleNamespace(execute=execute)


@pytest.mark.asyncio
async def test_unknown_observation_survives_guard_reconstruction_and_cannot_reacquire():
    engine = TransactionDouble()
    first = guard(engine)
    denied = decision(9)
    intent = await first.begin_observation(denied.portable_subject_ref, denied.purpose_of_use)
    engine.fail_observe = True
    with pytest.raises(OSError):
        await first.observe(denied, intent=intent)
    engine.fail_observe = False
    reconstructed = guard(engine)
    state = await reconstructed.pending_status(denied.portable_subject_ref, denied.purpose_of_use)
    assert state.intent == intent and state.persisted_revision is None
    assert await reconstructed.begin_observation(denied.portable_subject_ref, denied.purpose_of_use) is None
    assert not await reconstructed.is_current(decision(8, granted=True))
    assert await reconstructed.begin_observation("another-subject", denied.purpose_of_use)
    # Exact known observation retained by this test can retry its original intent.
    assert await reconstructed.observe(denied, intent=intent)
    assert await reconstructed.pending_status(denied.portable_subject_ref, denied.purpose_of_use) is None
    assert not await reconstructed.is_current(decision(8, granted=True))


@pytest.mark.asyncio
async def test_unknown_completed_observation_has_floor_before_intent_disappears():
    engine = TransactionDouble()
    g, denied = guard(engine), decision(9)
    intent = await g.begin_observation(denied.portable_subject_ref, denied.purpose_of_use)
    engine.fail_after_commit = True
    with pytest.raises(OSError):
        await g.observe(denied, intent=intent)
    rebuilt = guard(engine)
    assert await rebuilt.pending_status(denied.portable_subject_ref, denied.purpose_of_use) is None
    assert await rebuilt.is_current(denied)
    assert not await rebuilt.is_current(decision(8, granted=True))
    assert not await rebuilt.observe(denied, intent=intent)  # No implicit reacquisition.


@pytest.mark.asyncio
async def test_pending_observation_masks_old_floor_and_exposes_read_only_reconciliation():
    engine = TransactionDouble()
    g, granted = guard(engine), decision(8, granted=True)
    intent = await g.begin_observation(granted.portable_subject_ref, granted.purpose_of_use)
    assert await g.observe(granted, intent=intent)
    pending = await g.begin_observation(granted.portable_subject_ref, granted.purpose_of_use)
    before = deepcopy(engine.state)
    status = await guard(engine).pending_status(granted.portable_subject_ref, granted.purpose_of_use)
    assert status.intent == pending and status.persisted_revision == 8 and status.conflicted is False
    assert not await g.is_current(granted)
    assert engine.state == before
    forged = replace(pending, observation_token="b" * 32)
    assert not await g.observe(decision(9), intent=forged)
    assert engine.state == before


@pytest.mark.asyncio
async def test_cancellation_after_durable_intent_preserves_unknown_state(harness, monkeypatch):  # noqa: F811
    h = harness
    engine = TransactionDouble()
    actual = guard(engine)
    h.executor._consent_revisions = actual
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.consent, "latest_decision", blocked)
    task = asyncio.create_task(h.executor.execute(h.request))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await guard(engine).pending_status("subject1", "purpose1")
    assert not h.requests


@pytest.mark.asyncio
async def test_begin_unknown_commit_never_calls_source_and_restart_remains_fenced(harness):  # noqa: F811
    h = harness
    engine = TransactionDouble()
    engine.fail_after_commit = True
    h.executor._consent_revisions = guard(engine)
    assert not (await h.executor.execute(h.request)).succeeded
    assert not any(event[0] == "consent" for event in h.events)
    assert await guard(engine).pending_status("subject1", "purpose1")
    assert not (await h.executor.execute(h.request)).succeeded
    assert not h.requests


@pytest.mark.asyncio
async def test_failed_denial_persistence_then_reconstructed_executor_refuses_lower(harness):  # noqa: F811
    h = harness
    engine = TransactionDouble()
    h.executor._consent_revisions = guard(engine)
    engine.fail_observe = True
    h.consent.result = replace(h.consent.result, consent_revision=9, granted=False)
    result = await h.executor.execute(h.request)
    assert not result.succeeded and result.failure.reason == Reason.UPSTREAM_UNAVAILABLE
    engine.fail_observe = False
    h.consent.result = replace(h.consent.result, consent_revision=8, granted=True)
    runtime = replace(h.runtime, consent_revisions=guard(engine))
    reconstructed = tool_registry.build_amh_context(runtime=runtime, seam=h.seam).inner
    assert not (await reconstructed.execute(h.request)).succeeded
    assert not h.requests and not h.audit.records
    assert len([event for event in h.events if event[0] == "consent"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["response", "transport"])
async def test_final_disclosure_guard_runs_after_all_http_cleanup(harness, monkeypatch, cleanup):  # noqa: F811
    h = harness
    old = h.consent.result
    if cleanup == "transport":
        cls, name = amh.httpx.AsyncHTTPTransport, "__aexit__"
    else:
        cls, name = amh.httpx.Response, "aclose"
    original = getattr(cls, name)

    async def revoke(instance, *args):
        await original(instance, *args)
        assert await h.revisions.observe(replace(old, consent_revision=2, granted=False))

    monkeypatch.setattr(cls, name, revoke)
    result = await h.executor.execute(h.request)
    assert not result.succeeded and result.failure.reason == Reason.CONSENT_REQUIRED
    assert ("response_closed",) in h.events and ("transport_closed",) in h.events


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_pending_intent_survives_reconstruction_and_transaction_rollback(postgres_guard):  # noqa: F811
    engine = postgres_guard
    denied, g = decision(9), guard(engine)
    intent = await g.begin_observation(denied.portable_subject_ref, denied.purpose_of_use)
    p = {**g._parameters(denied), "token": intent.observation_token}
    with pytest.raises(RuntimeError):
        async with engine.begin() as conn:
            assert (await conn.execute(revision._LOCK_PENDING, p)).one()[0] == intent.observation_token
            await conn.execute(revision._OBSERVE, p)
            await conn.execute(revision._COMPLETE, p)
            raise RuntimeError("synthetic failure before commit")
    rebuilt = guard(engine)
    status = await rebuilt.pending_status(denied.portable_subject_ref, denied.purpose_of_use)
    assert status.intent == intent and status.persisted_revision is None
    attempts = await asyncio.gather(
        *(
            guard(engine).begin_observation(denied.portable_subject_ref, denied.purpose_of_use)
            for _ in range(3)
        )
    )
    assert attempts == [None, None, None]
    assert await rebuilt.observe(denied, intent=intent)
    assert not await guard(engine).is_current(decision(8, granted=True))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_real_commit_then_lost_client_ack_preserves_floor(postgres_guard):  # noqa: F811
    engine = postgres_guard
    denied, g = decision(9), guard(engine)
    intent = await g.begin_observation(denied.portable_subject_ref, denied.purpose_of_use)

    @asynccontextmanager
    async def lost_ack():
        async with engine.begin() as conn:
            yield conn
        raise OSError("synthetic acknowledgment loss AFTER actual PostgreSQL commit")

    writer = guard(SimpleNamespace(begin=lost_ack))
    with pytest.raises(OSError):
        await writer.observe(denied, intent=intent)
    assert await guard(engine).pending_status(denied.portable_subject_ref, denied.purpose_of_use) is None
    assert await guard(engine).is_current(denied)
    assert not await guard(engine).is_current(decision(8, granted=True))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_begin_ack_loss_persists_fence_before_any_source_observation(postgres_guard):  # noqa: F811
    engine = postgres_guard

    @asynccontextmanager
    async def lost_ack():
        async with engine.begin() as conn:
            yield conn
        raise OSError("synthetic admission acknowledgment loss AFTER PostgreSQL commit")

    with pytest.raises(OSError):
        await guard(SimpleNamespace(begin=lost_ack)).begin_observation("subject", "purpose")
    status = await guard(engine).pending_status("subject", "purpose")
    assert status is not None and status.persisted_revision is None
    assert await guard(engine).begin_observation("subject", "purpose") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_concurrent_intent_admission_has_exactly_one_owner(postgres_guard):  # noqa: F811
    engine = postgres_guard
    intents = await asyncio.gather(*(guard(engine).begin_observation("subject", "purpose") for _ in range(4)))
    admitted = [intent for intent in intents if intent is not None]
    assert len(admitted) == 1
    assert (await guard(engine).pending_status("subject", "purpose")).intent == admitted[0]
