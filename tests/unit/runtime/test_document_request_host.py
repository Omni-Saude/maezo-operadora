"""Host unit boundaries; real adapter/installed wrapper, synthetic I/O and sources.

No engine, database, source authority or daemon deployment qualification.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.unit.gateway.document_requests.test_producer_repair import receipt_fixture
from tests.unit.gateway.native_fetch import test_native_fetch as f

from maezo.gateway.communications import models as clock_module
from maezo.gateway.document_requests import composition
from maezo.gateway.document_requests.completion import CompletionOutcome
from maezo.gateway.document_requests.producer import ProducerResult
from maezo.gateway.document_requests.transport import ProducerAcquisition
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.native_fetch.models import FetchProfile, FetchUnavailableError, encode, prepare
from maezo.runtime.worker_runtime.document_requests import PROCESS, TOPIC, DocumentRequestHost


@pytest.fixture
def host_case(monkeypatch):
    original = f.prepared.__wrapped__()
    doc = original.profile.value()
    doc["target"].update(process_key=PROCESS, definition_id="AUTH:1:def", topic=TOPIC)
    doc["schema"].update(process_key=PROCESS, topic=TOPIC)
    profile = FetchProfile(encode(doc), original.profile.classifications, original.profile.input_profile)
    expected = prepare(
        profile,
        engine="engine",
        database_incarnation="db",
        native_user="native",
        activation_ref="activation",
        command_id=f.REF,
        resource_ref="fetch-selector",
        max_tasks=1,
        lock_millis=10000,
        poll_millis=100,
        projection=("valor_estimado", "tenant_id"),
    )
    fixture_client, authority, clock, calls = f.client.__wrapped__(expected, monkeypatch)
    monkeypatch.setattr(clock_module, "now", lambda: clock[0])
    events = []
    gate = None
    late = False
    receipt, _ = receipt_fixture()
    delivered = ProducerResult(receipt, CompletionOutcome("not_prepared", None), f.NOW + timedelta(seconds=4))

    async def channel_close(timeout):  # noqa: ASYNC109 - installed drain interface
        events.append(("channel_close", timeout))
        return True

    channel = SimpleNamespace(tls=fixture_client._tls, close=channel_close)

    async def roles(placement):
        events.append("role_qualification")

    async def invoke(name, *args):
        events.append((name, args[1:] if name in {"run", "successor"} else args))
        if name in {"run", "successor"}:
            acquired = ProducerAcquisition(args[0])
            acquired.current()
            assert acquired.expected is expected
            with pytest.raises(ExternalCaseError):
                ProducerAcquisition(args[0])
        if state.gate is not None:
            state.entered.set()
            await state.gate.wait()
        if state.late:
            clock[0] += timedelta(seconds=6)
        return delivered

    worker = SimpleNamespace(
        context=SimpleNamespace(profile=profile, channel=channel),
        completion=SimpleNamespace(channel=channel),
        run=lambda *a: invoke("run", *a),
        successor=lambda *a: invoke("successor", *a),
        recover=lambda *a: invoke("recover", *a),
        recover_successor=lambda *a: invoke("recover_successor", *a),
    )
    monkeypatch.setattr(composition, "verify_database_roles", roles)
    installed = composition.InstalledProducer(
        worker, SimpleNamespace(live=lambda: events.append("placement"))
    )
    host = DocumentRequestHost(
        installed=installed,
        tls=fixture_client._tls,
        profile=profile,
        authority=authority,
        generic_topics=("other",),
        clock=lambda: clock[0],
    )
    exchange = fixture_client._exchange

    async def native_exchange(method, route, body, guard):
        if route.endswith("operations"):
            guard()
            calls.append((method, route, body))
            if state.lost_ack:
                raise OSError("synthetic private failure")
            value = f.response(expected, state.fetch_state)
            if value["result"]:
                value["result"]["value"][0]["topic"] = TOPIC
            return (503 if state.fetch_state == "unavailable" else 200), encode(value)
        return await exchange(method, route, body, guard)

    monkeypatch.setattr(host._client, "_exchange", native_exchange)
    state = SimpleNamespace(
        host=host,
        expected=expected,
        installed=installed,
        authority=authority,
        clock=clock,
        calls=calls,
        events=events,
        delivered=delivered,
        gate=gate,
        late=late,
        entered=asyncio.Event(),
        fetch_state="executed",
        lost_ack=False,
    )
    return state


@pytest.mark.parametrize("method", ["run", "successor"])
async def test_native_adapter_transfers_one_owned_acquisition_to_installed_producer(host_case, method):
    c = host_case
    args = () if method == "run" else ("original-command", "source-invocation-1")
    assert await getattr(c.host, method)(c.expected, *args) is c.delivered
    assert (method, args) in c.events
    assert sum(route.endswith("operations") for _, route, _ in c.calls) == 1
    assert c.events.count("role_qualification") == 1
    assert not c.host.busy
    assert await c.host.close(1)
    assert sum(isinstance(x, tuple) and x[0] == "channel_close" for x in c.events) == 2


@pytest.mark.parametrize("state", ["duplicate", "unavailable"])
async def test_noninitial_fetch_never_runs_worker_or_reconstructs_inputs(host_case, state):
    c = host_case
    c.fetch_state = state
    result = await c.host.run(c.expected)
    assert result.status == state
    assert "role_qualification" not in c.events


async def test_lost_ack_then_not_observed_never_retries_fetch_or_producer(host_case):
    c = host_case
    c.lost_ack = True
    with pytest.raises(FetchUnavailableError, match="native_fetch_unavailable"):
        await c.host.run(c.expected)
    result = await c.host.recover_fetch(c.expected)
    assert result.status == "not_observed" and not result.rows
    with pytest.raises(FetchUnavailableError):
        await c.host.run(c.expected)
    assert sum(route.endswith("operations") for _, route, _ in c.calls) == 1
    assert "role_qualification" not in c.events


@pytest.mark.parametrize(
    "method,args",
    [
        ("recover", ("exact-request",)),
        ("recover_successor", ("exact-request", "source-invocation-1")),
    ],
)
async def test_recovery_passes_original_selectors_without_fetch(host_case, method, args):
    c = host_case
    assert await getattr(c.host, method)(*args) is c.delivered
    assert (method, args) in c.events and c.calls == []


async def test_busy_guard_blocks_double_event_and_close_drains_without_cancel(host_case):
    c = host_case
    c.gate = asyncio.Event()
    task = asyncio.create_task(c.host.run(c.expected))
    await c.entered.wait()
    with pytest.raises(FetchUnavailableError):
        await c.host.successor(c.expected, "old", "source-invocation-1")
    close = asyncio.create_task(c.host.close(1))
    await asyncio.sleep(0)
    assert not task.cancelled() and not close.done()
    c.gate.set()
    assert await task is c.delivered
    assert await close
    with pytest.raises(FetchUnavailableError):
        await c.host.recover("exact-request")


async def test_cancellation_disables_new_effects_but_retains_exact_recovery(host_case):
    c = host_case
    c.gate = asyncio.Event()
    task = asyncio.create_task(c.host.run(c.expected))
    await c.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not c.host.busy
    with pytest.raises(FetchUnavailableError):
        await c.host.run(c.expected)
    c.gate = None
    assert await c.host.recover("exact-request") is c.delivered


async def test_original_producer_deadline_survives_host_return(host_case):
    c = host_case
    c.late = True
    with pytest.raises(FetchUnavailableError):
        await c.host.run(c.expected)
    assert not c.host.busy


async def test_wrong_profile_batch_and_generic_ownership_refused_before_io(host_case):
    c = host_case
    with pytest.raises(FetchUnavailableError):
        c.host.assert_exclusive((TOPIC,))
    with pytest.raises(FetchUnavailableError):
        await c.host.run(f.prepared.__wrapped__())
    # Rebuild a valid exact binding: the host rejects a qualified batch too.
    batch = prepare(
        c.expected.profile,
        engine="engine",
        database_incarnation="db",
        native_user="native",
        activation_ref="activation",
        command_id=f.REF,
        resource_ref="fetch-selector",
        max_tasks=2,
        lock_millis=10000,
        poll_millis=100,
        projection=("tenant_id",),
    )
    with pytest.raises(FetchUnavailableError):
        await c.host.run(batch)
    with pytest.raises(FetchUnavailableError):
        DocumentRequestHost(
            installed=c.installed,
            tls=c.host._client._tls,
            profile=replace(c.expected.profile, input_profile="native-classified.v2", classifications=()),
            authority=c.authority,
            generic_topics=(),
        )
    assert c.calls == []


@pytest.mark.parametrize("budget", [10.0, None])
async def test_installed_close_shares_budget_and_checks_both_channels(monkeypatch, budget):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(
        composition,
        "asyncio",
        SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0])),
    )

    async def context_close(remaining):
        calls.append(("context", remaining))
        clock[0] += 7.0
        return False

    async def completion_close(remaining):
        calls.append(("completion", remaining))
        clock[0] += 3.0
        return False

    worker = SimpleNamespace(
        context=SimpleNamespace(channel=SimpleNamespace(close=context_close)),
        completion=SimpleNamespace(channel=SimpleNamespace(close=completion_close)),
    )
    installed = composition.InstalledProducer(worker, SimpleNamespace(live=lambda: None))
    assert not await installed.close(budget)
    assert calls == [("context", budget), ("completion", None if budget is None else 3.0)]
