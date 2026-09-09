"""Actual local ownership and HTTP-wire controls; no engine/admission acceptance."""

import asyncio
import copy
import hashlib
import pickle
import threading
from concurrent.futures import Future
from dataclasses import replace

import httpx
import pytest

from maezo.gateway.engine_contracts import EngineCapabilityError, EngineOperation, EngineRequest
from maezo.gateway.engine_lifetime import (
    CallerSelection,
    LifetimeBorrower,
    LocalLifetimeOwner,
    ProfileSelectionKey,
    Submission,
)
from maezo.gateway.engine_schemas import HELENA_START
from maezo.gateway.engine_transport import EngineOperationBorrower, EngineOperationsClient
from maezo.tools.mcp_cibseven.secured_transport import SecuredCibSevenTransport
from maezo.tools.mcp_cibseven.transport import CibSevenStartAuthorizationError
from tests.unit.gateway.test_engine_operations_transport import config as config
from tests.unit.gateway.test_engine_operations_transport import readiness_response, result, wire


def read_request():
    return EngineRequest(EngineOperation.READ_ACTIVE, HELENA_START.process_key, "synthetic", b"{}")


def test_client_captures_config_and_nested_identity(config):
    client = EngineOperationsClient(config)
    object.__setattr__(config.identity, "tenant", "other-tenant")
    assert client.config.identity.tenant == "synthetic"
    exposed = client.config
    object.__setattr__(exposed.profiles[0].target, "definition_id", "other-definition")
    assert (
        client.profile(EngineOperation.START, HELENA_START.process_key).target.definition_id == "definition-7"
    )


@pytest.mark.asyncio
async def test_borrower_close_does_not_close_sibling(config, monkeypatch):
    calls = wire(monkeypatch, config)
    client = EngineOperationsClient(config)
    first = SecuredCibSevenTransport(client, process_key=HELENA_START.process_key)
    second = SecuredCibSevenTransport(client, process_key=HELENA_START.process_key)
    await first.close()
    try:
        assert await second.find_active_instance("synthetic") is None
        with pytest.raises(CibSevenStartAuthorizationError):
            await first.find_active_instance("synthetic")
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_racing_valid_response_does_not_release_consumable_result(config, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    real = httpx.AsyncClient

    async def handler(request):
        if request.method == "GET":
            return readiness_response(config)
        entered.set()
        await release.wait()
        return result(config, EngineOperation.READ_ACTIVE, [])

    monkeypatch.setattr(
        "maezo.gateway.engine_transport.httpx.AsyncClient",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    client = EngineOperationsClient(config)
    operation = asyncio.create_task(client.execute(read_request()))
    await asyncio.wait_for(entered.wait(), 2)
    closing = asyncio.create_task(client.close())
    await asyncio.sleep(0)
    release.set()
    try:
        with pytest.raises(EngineCapabilityError):
            await operation
    finally:
        await closing


@pytest.fixture
def owner():
    # Synthetic local selection, deliberately not a provider or authority fixture.
    document = b'{"synthetic":"profile"}'
    digest = hashlib.sha256(document).hexdigest()
    return LocalLifetimeOwner(
        CallerSelection(
            b'{"tenant":"synthetic","workload":"test"}',
            "a" * 64,
            (ProfileSelectionKey(document, digest, "a" * 64),),
        )
    )


def borrow(owner):
    return owner.borrow(tuple(p.profile_digest for p in owner.selection.profiles))


async def reached(event):
    async def poll():
        while not event.is_set():  # noqa: ASYNC110 - observes actual thread Event
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 3)


@pytest.mark.asyncio
async def test_cancelled_thread_wrapper_keeps_real_child_through_drain(owner):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    borrower = borrow(owner)

    def work():
        entered.set()
        try:
            assert release.wait(5)
            return 42
        finally:
            finished.set()

    task = asyncio.create_task(borrower.run_sync(work))
    try:
        await reached(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.done() and not finished.is_set()
        assert owner.pending_children == 1
        assert await owner.close(timeout=0) is False
        assert not finished.is_set() and owner.pending_children == 1
        with pytest.raises(EngineCapabilityError):
            borrower.check()
    finally:
        release.set()
        assert await owner.close(timeout=3) is True
    assert finished.is_set() and owner.pending_children == 0


@pytest.mark.asyncio
async def test_old_callback_cannot_remove_new_local_generation(owner):
    first = borrow(owner)
    entered, release = asyncio.Event(), asyncio.Event()

    async def work():
        entered.set()
        await release.wait()
        return "done"

    previous_task = asyncio.create_task(first.run_async(work))
    await entered.wait()
    previous_child = next(iter(owner._children))
    previous_execution = previous_child.execution
    release.set()
    assert await previous_task == "done"
    assert await first.close() is True
    entered.clear()
    release.clear()
    second = borrow(owner)
    current_task = asyncio.create_task(second.run_async(work))
    await entered.wait()
    try:
        owner._finish(previous_child, previous_execution)
        assert owner.pending_children == 1
        assert await second.close(timeout=0) is False
        assert not current_task.done()
    finally:
        release.set()
        with pytest.raises(EngineCapabilityError):
            await current_task
        assert await owner.close(timeout=3) is True


@pytest.mark.asyncio
async def test_cancelled_async_child_is_retained_and_siblings_are_isolated(owner):
    entered, release = asyncio.Event(), asyncio.Event()
    first, sibling = borrow(owner), borrow(owner)

    async def work():
        entered.set()
        await release.wait()
        return 1

    task = asyncio.create_task(first.run_async(work))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await first.close(timeout=0) is False
    assert await sibling.run_sync(lambda: 2) == 2
    release.set()
    assert await first.close(timeout=3) is True
    assert await sibling.run_sync(lambda: 3) == 3
    assert await owner.close() is True


def test_real_thread_terminal_signal_survives_awaiting_loop_shutdown(owner):
    entered, release = threading.Event(), threading.Event()
    borrower = borrow(owner)

    def work():
        entered.set()
        assert release.wait(5)
        return 1

    async def first_loop():
        task = asyncio.create_task(borrower.run_sync(work))
        await reached(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await borrower.close(timeout=0) is False

    try:
        asyncio.run(first_loop())
        assert owner.pending_children == 1
    finally:
        release.set()
        assert asyncio.run(owner.close(timeout=3)) is True
    assert owner.pending_children == 0


@pytest.mark.asyncio
async def test_running_returned_future_is_not_a_result_or_terminal_child(owner):
    inner = Future()
    inner.set_running_or_notify_cancel()
    borrower = borrow(owner)
    task = asyncio.create_task(borrower.run_sync(lambda: inner))
    try:
        while not owner.poisoned:  # noqa: ASYNC110 - observes a thread callback
            await asyncio.sleep(0)
        assert owner.pending_children == 1
        assert await owner.close(timeout=0) is False
        assert not inner.cancelled() and not task.done()
    finally:
        inner.set_result(99)
        with pytest.raises(EngineCapabilityError):
            await task
        assert await owner.close(timeout=3) is True


@pytest.mark.asyncio
async def test_unstarted_coroutine_is_closed_and_never_escapes(owner):
    called = []

    async def latent():
        called.append(True)

    coroutine = latent()
    with pytest.raises(EngineCapabilityError):
        await borrow(owner).run_sync(lambda: coroutine)
    assert coroutine.cr_frame is None and called == []
    assert await owner.close() is True


@pytest.mark.asyncio
async def test_failure_is_terminal_but_close_deadline_is_not(owner):
    borrower = borrow(owner)

    def fail():
        raise ValueError("synthetic")

    with pytest.raises(ValueError, match="synthetic"):
        await borrower.run_sync(fail)
    assert owner.pending_children == 0
    assert await borrower.run_sync(lambda: 9) == 9
    assert await owner.close(timeout=0) is True


@pytest.mark.parametrize("variant", ["tenant", "config", "profile", "identity"])
def test_selection_is_exact_and_deep_immutable(owner, variant):
    borrower = borrow(owner)
    selection = owner.selection
    borrower.check(selection=selection)
    changed = (
        replace(selection, identity_document=b'{"tenant":"other"}')
        if variant == "tenant"
        else replace(selection, identity_document=b'{"workload":"other"}')
        if variant == "identity"
        else CallerSelection(
            selection.identity_document,
            "b" * 64,
            tuple(replace(p, config_digest="b" * 64) for p in selection.profiles),
        )
        if variant == "config"
        else replace(
            selection,
            profiles=(ProfileSelectionKey(b"changed", hashlib.sha256(b"changed").hexdigest(), "a" * 64),),
        )
    )
    with pytest.raises(EngineCapabilityError):
        borrower.check(selection=changed)
    object.__setattr__(selection.profiles[0], "profile_digest", "c" * 64)
    assert owner.selection.profiles[0].profile_digest != "c" * 64
    with pytest.raises(EngineCapabilityError):
        borrower.check(selection=selection)


@pytest.mark.asyncio
async def test_forged_owner_handle_alias_and_submission_cannot_gain_ownership(owner):
    borrower = borrow(owner)
    other = LocalLifetimeOwner(owner.selection)
    forged = LifetimeBorrower(owner)
    for fake in (forged, borrow(other)):
        with pytest.raises(EngineCapabilityError):
            owner._check(fake)
    digest = owner.selection.profiles[0].profile_digest
    token = owner.reserve(borrower, digest, "start", "e" * 64)
    for handle in (owner, borrower, token):
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(handle)
    with pytest.raises(EngineCapabilityError):
        Submission(owner).submitted()
    alias = borrower
    assert await borrower.close() is True
    with pytest.raises(EngineCapabilityError):
        alias.check()
    with pytest.raises(EngineCapabilityError):
        token.submitted()
    assert await owner.close() is True
    assert await other.close() is True


@pytest.mark.asyncio
async def test_ambiguous_submission_is_sticky_and_cannot_be_relabelled_by_new_owner(owner):
    borrower = borrow(owner)
    digest = owner.selection.profiles[0].profile_digest
    token = owner.reserve(borrower, digest, "start", "e" * 64)
    token.submitted()
    token.ambiguous()
    other = LocalLifetimeOwner(owner.selection)
    assert owner.poisoned and owner.submissions[0].state == "ambiguous"
    assert other.submissions == ()
    with pytest.raises(EngineCapabilityError):
        borrow(owner)
    with pytest.raises(EngineCapabilityError):
        other._transition(token, "acknowledged", "f" * 64)
    assert await owner.close() is True
    assert owner.submissions[0].state == "ambiguous"
    # Exact late acknowledgement settles accounting only; it never revives owner.
    token.acknowledged("f" * 64)
    assert owner.submissions[0].state == "acknowledged" and owner.poisoned
    with pytest.raises(EngineCapabilityError):
        borrower.check()
    assert await other.close() is True


@pytest.mark.asyncio
async def test_identical_config_cannot_transfer_borrower_between_clients(config):
    one, two = EngineOperationsClient(config), EngineOperationsClient(config)
    borrower = one.borrow(HELENA_START.process_key)
    assert one.selection == two.selection
    with pytest.raises(EngineCapabilityError):
        EngineOperationBorrower(two, borrower._borrower, HELENA_START.process_key)
    for handle in (one, borrower):
        with pytest.raises(TypeError):
            pickle.dumps(handle)
    await one.close()
    await two.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["readiness", "submitted"])
@pytest.mark.parametrize("action", ["cancel", "close", "cancel-close-waiter"])
async def test_exact_http_submission_cancellation_and_close_races(config, monkeypatch, phase, action):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    real = httpx.AsyncClient

    async def handler(request):
        calls.append(request)
        if (request.method == "GET") == (phase == "readiness"):
            entered.set()
            await release.wait()
        if request.method == "GET":
            return readiness_response(config)
        return result(
            config,
            EngineOperation.START,
            {
                "id": "acknowledged",
                "definition_id": "definition-7",
                "tenant": "synthetic",
            },
        )

    monkeypatch.setattr(
        "maezo.gateway.engine_transport.httpx.AsyncClient",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    from maezo.gateway.engine_contracts import canonical_json
    from tests.unit.gateway.test_engine_capability_contracts import variables

    client = EngineOperationsClient(config)
    request = EngineRequest(
        EngineOperation.START, HELENA_START.process_key, "synthetic", canonical_json(variables())
    )
    operation = asyncio.create_task(client.execute(request))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        record = client.submissions[0]
        assert record.state == ("prepared" if phase == "readiness" else "submitted")
        if action == "cancel":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        elif action == "cancel-close-waiter":
            closing = asyncio.create_task(client.close())
            await asyncio.sleep(0)
            assert not closing.done()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert await client.close(timeout=0) is False
        assert client._lifetime.pending_children == 1
        with pytest.raises(EngineCapabilityError):
            await client.execute(read_request())
        assert len(calls) == (1 if phase == "readiness" else 2)
        if phase == "submitted":
            assert hashlib.sha256(calls[-1].content).hexdigest() == record.body_digest
        release.set()
        if action != "cancel":
            with pytest.raises(EngineCapabilityError):
                await operation
        assert await client.close(timeout=3) is True
        settled = client.submissions[0]
        assert settled.sequence == record.sequence and settled.body_digest == record.body_digest
        assert settled.state == ("refused" if phase == "readiness" else "acknowledged")
        with pytest.raises(EngineCapabilityError):
            client.borrow(HELENA_START.process_key)
    finally:
        release.set()
        await client.close(timeout=3)


@pytest.mark.asyncio
async def test_lost_wire_reply_keeps_ambiguity_and_rejects_sibling_retry(config, monkeypatch):
    calls = wire(monkeypatch, config, [httpx.ReadTimeout("private lost reply")])
    client = EngineOperationsClient(config)
    first, second = client.borrow(HELENA_START.process_key), client.borrow(HELENA_START.process_key)
    with pytest.raises(EngineCapabilityError):
        await first.execute(read_request())
    assert client.submissions[0].state == "ambiguous"
    assert client._lifetime.poisoned
    with pytest.raises(EngineCapabilityError):
        await second.execute(read_request())
    assert [r.method for r in calls] == ["GET", "POST"]
    assert await client.close() is True
    assert client.submissions[0].state == "ambiguous"


@pytest.mark.asyncio
async def test_known_native_refusal_is_terminal_and_does_not_poison_sibling(config, monkeypatch):
    calls = wire(monkeypatch, config, [httpx.Response(403, json={"error": "engine_operation_denied"})])
    client = EngineOperationsClient(config)
    with pytest.raises(EngineCapabilityError):
        await client.execute(read_request())
    assert client.submissions[0].state == "refused" and not client._lifetime.poisoned
    assert await client.borrow(HELENA_START.process_key).execute(read_request()) == []
    assert len(calls) == 4
    assert [s.state for s in client.submissions] == ["refused", "acknowledged"]
    assert await client.close() is True


@pytest.mark.asyncio
async def test_same_request_has_distinct_local_records_and_no_wire_command_invention(config, monkeypatch):
    calls = wire(monkeypatch, config)
    client = EngineOperationsClient(config)
    assert await client.execute(read_request()) == []
    assert await client.execute(read_request()) == []
    left, right = client.submissions
    assert left.sequence != right.sequence and left.body_digest == right.body_digest
    assert calls[1].content == calls[3].content
    import json

    assert set(json.loads(calls[1].content)) == {
        "protocol",
        "capability_digest",
        "operation",
        "process_key",
        "resource_ref",
        "variables",
        "correlation",
        "all_matching",
        "error_code",
        "topic",
        "message",
        "worker_id",
        "parameters",
        "source_ref",
    }
    assert await client.close() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [-1, True, float("inf"), float("nan"), "0"])
async def test_invalid_close_deadline_does_not_claim_terminal(owner, invalid):
    borrower = borrow(owner)
    with pytest.raises(ValueError):
        await owner.close(timeout=invalid)
    borrower.check()
    assert await owner.close(timeout=0) is True


@pytest.mark.asyncio
async def test_profile_return_and_config_return_cannot_mutate_selected_wire(config, monkeypatch):
    calls = wire(monkeypatch, config)
    client = EngineOperationsClient(config)
    borrower = client.borrow(HELENA_START.process_key)
    selected = borrower.profile(EngineOperation.READ_ACTIVE, HELENA_START.process_key)
    old_digest = selected.digest
    object.__setattr__(selected.target, "definition_id", "changed")
    exposed = client.config
    object.__setattr__(exposed, "policy_digest", "b" * 64)
    assert await borrower.execute(read_request()) == []
    import json

    assert json.loads(calls[-1].content)["capability_digest"] == old_digest
    assert await client.close() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"not json", b'{"result":null,"result":null}', b"[]"])
async def test_invalid_response_bytes_cannot_be_recorded_as_native_refusal(config, monkeypatch, raw):
    calls = wire(
        monkeypatch, config, [httpx.Response(200, content=raw, headers={"content-type": "application/json"})]
    )
    client = EngineOperationsClient(config)
    with pytest.raises(EngineCapabilityError):
        await client.execute(read_request())
    assert client.submissions[0].state == "ambiguous" and client._lifetime.poisoned
    assert len(calls) == 2
    assert await client.close() is True


@pytest.mark.asyncio
async def test_thread_returning_running_async_future_retains_actual_terminal(owner):
    future = asyncio.get_running_loop().create_future()
    borrower = borrow(owner)
    task = asyncio.create_task(borrower.run_sync(lambda: future))
    try:
        while not owner.poisoned:  # noqa: ASYNC110 - observes thread callback
            await asyncio.sleep(0)
        assert await owner.close(timeout=0) is False
        assert not future.cancelled() and owner.pending_children == 1
    finally:
        future.set_result("terminal")
        with pytest.raises(EngineCapabilityError):
            await task
        assert await owner.close(timeout=3) is True
