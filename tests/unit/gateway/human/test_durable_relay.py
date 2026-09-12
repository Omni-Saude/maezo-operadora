"""Unit-only controlled transport: proves orchestration, never actual engine execution."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_durable_projection import assignment, receipt_payload, wire

from maezo.gateway.human.outbox import DeliveryLease, HumanOutboxError, LeaseLostError
from maezo.gateway.human.relay import HumanCommandRelay, RelaySettings
from maezo.gateway.human.transport import EngineConflictError, EngineUnavailableError

pytestmark = pytest.mark.asyncio


class UnitOutbox:
    def __init__(self):
        self.scope = assignment().scope
        self.events = []
        self.lease = DeliveryLease(self.scope, wire(), "lease-1", 1, datetime.now(UTC) + timedelta(minutes=1))
        self.closed = False
        self.fail_result = False
        self.stale = False
        self.rows = 1

    async def claim(self, **_):
        self.events.append("claim")
        self.closed = True
        return self.lease if self.rows else None

    async def require_lease(self, lease):
        self.events.append("check")
        if self.stale:
            raise LeaseLostError("stale")

    async def finish(self, lease, **result):
        self.events.append(("finish", result))
        if self.fail_result:
            raise HumanOutboxError("audit failure")
        if self.stale:
            raise LeaseLostError("stale")
        self.rows = 0

    async def retry_later(self, lease, **_):
        self.events.append("pending")
        if self.stale:
            raise LeaseLostError("stale")


class UnitTransport:
    def __init__(self, outbox):
        self.scope = outbox.scope
        self.outbox = outbox
        self.previous = None
        self.error = None
        self.on_query = None
        self.commands = []

    async def receipt(self, command):
        assert self.outbox.closed, "HTTP must follow committed claim transaction"
        self.outbox.events.append("query")
        self.commands.append(command.canonical)
        if self.on_query:
            self.on_query()
        return self.previous

    async def dispatch(self, command):
        assert self.outbox.closed
        self.outbox.events.append("dispatch")
        self.commands.append(command.canonical)
        if self.error:
            raise self.error
        return receipt_payload(command)


def rig():
    store = UnitOutbox()
    transport = UnitTransport(store)
    relay = HumanCommandRelay(
        outbox=store,
        transport=transport,
        settings=RelaySettings(lease_seconds=30, retry_seconds=1, poll_seconds=1),
    )
    return relay, store, transport


async def test_query_first_and_same_immutable_payload_dispatch():
    relay, store, transport = rig()
    assert await relay.run_once()
    assert store.events[:5] == ["claim", "check", "query", "check", "dispatch"]
    assert store.events[-1][0] == "finish"
    assert transport.commands == [wire().canonical, wire().canonical]


async def test_lost_engine_response_recovered_without_second_dispatch():
    relay, store, transport = rig()
    transport.error = EngineUnavailableError()
    assert await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"
    transport.previous = receipt_payload()
    store.events.clear()
    assert await relay.run_once()
    assert "dispatch" not in store.events and store.rows == 0


async def test_result_audit_failure_keeps_reconciliation_pending():
    relay, store, transport = rig()
    store.fail_result = True
    await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"
    store.fail_result = False
    transport.previous = receipt_payload()
    store.events.clear()
    await relay.run_once()
    assert store.rows == 0 and "dispatch" not in store.events


async def test_lease_lost_during_query_cannot_dispatch_or_mark_successor():
    relay, store, transport = rig()
    transport.on_query = lambda: setattr(store, "stale", True)
    await relay.run_once()
    assert store.rows == 1
    assert store.events == ["claim", "check", "query", "check"]


@pytest.mark.parametrize(
    "bad", [dict(tenant="foreign"), dict(payload_digest="a" * 64), dict(principal_ref="other")]
)
async def test_unbound_receipt_never_finished(bad):
    relay, store, transport = rig()
    transport.previous = receipt_payload(**bad)
    await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"
    assert not any(isinstance(e, tuple) for e in store.events)


async def test_conflict_is_audited_without_engine_commit_fields():
    relay, store, transport = rig()
    transport.error = EngineConflictError("REVISION_CONFLICT")
    await relay.run_once()
    assert store.events[-1] == ("finish", {"conflict": "REVISION_CONFLICT"})


async def test_conflict_audit_failure_stays_pending():
    relay, store, transport = rig()
    transport.error = EngineConflictError("FORM_NOT_ACTIVATED")
    store.fail_result = True
    await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"


async def test_no_work_no_transport():
    relay, store, transport = rig()
    store.rows = 0
    assert not await relay.run_once()
    assert transport.commands == []


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "5", None])
async def test_operational_validity_must_be_explicit_strict_positive(value):
    with pytest.raises(HumanOutboxError):
        RelaySettings(lease_seconds=value, retry_seconds=1, poll_seconds=1)


async def test_scope_mismatch_refuses_constructor():
    _, store, transport = rig()
    transport.scope = transport.scope.model_copy(update={"workload_ref": "agent"})
    with pytest.raises(HumanOutboxError):
        HumanCommandRelay(
            outbox=store,
            transport=transport,
            settings=RelaySettings(lease_seconds=30, retry_seconds=1, poll_seconds=1),
        )
