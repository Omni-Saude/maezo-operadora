"""Current resource authorization unit fences, including post-completion receipts."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import SCOPE, SECRET, setup

from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.receipt import BoundReceiptPorts, CurrentReceiptAuthority, PublicReceipt

pytestmark = pytest.mark.asyncio


class UnitReceiptStore:
    scope = SCOPE

    def __init__(self):
        self.changes = {}

    async def read_owned(self, principal, task, command):
        values = dict(
            tenant=principal.tenant,
            task_id=task,
            command_id=command,
            principal_ref=principal.principal_ref,
            workload_ref=SCOPE.workload_ref,
            payload_digest="a" * 64,
            status="pending",
            audit_intent_ref="intent",
            audit_intent_hash="b" * 64,
        )
        values.update(self.changes)
        return PublicReceipt.model_validate(values)


class UnitResourceAuthority:
    scope = SCOPE

    def __init__(self):
        self.changes = {}
        self.callback = None

    async def current_authority(self, principal, identity):
        values = dict(
            identity=identity,
            issuer=principal.issuer,
            subject=principal.subject,
            membership_revision=principal.membership_revision,
            read_permitted=True,
            valid_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        values.update(self.changes)
        if self.callback:
            await self.callback()
        return CurrentReceiptAuthority.model_validate(values)


async def rig():
    gateway, identity, task, _, _ = await setup(active=False)
    store, authority = UnitReceiptStore(), UnitResourceAuthority()
    gateway._receipt_ports = BoundReceiptPorts(store=store, authority=authority)
    return gateway, identity, task, store, authority


async def read(gateway):
    return await gateway.read_receipt(session_secret=SECRET, task_id="task-1", command_id="command-1")


async def test_current_resource_grant_reads_receipt_after_task_completion():
    gateway, _, task, _, _ = await rig()
    assert (await read(gateway)).status == "pending"
    assert task.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        dict(read_permitted=False),
        dict(issuer="https://foreign.example"),
        dict(subject="other"),
        dict(membership_revision=99),
        dict(valid_until=datetime(2000, 1, 1, tzinfo=UTC)),
    ],
)
async def test_missing_current_resource_grant_refuses(changes):
    gateway, _, _, _, authority = await rig()
    authority.changes = changes
    with pytest.raises(GatewayRefusalError):
        await read(gateway)


@pytest.mark.parametrize("field", ["tenant", "principal_ref", "workload_ref", "task_id", "command_id"])
async def test_store_response_must_match_exact_current_identity(field):
    gateway, _, _, store, _ = await rig()
    store.changes[field] = "foreign"
    with pytest.raises(GatewayRefusalError):
        await read(gateway)


async def test_revocation_during_resource_io_is_rechecked():
    gateway, identity, _, _, authority = await rig()

    async def revoke():
        identity.memberships.clear()

    authority.callback = revoke
    with pytest.raises(GatewayRefusalError):
        await read(gateway)


async def test_expiry_during_final_session_io_is_rechecked():
    gateway, _, _, _, authority = await rig()
    original = gateway._session
    calls = 0

    async def session(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = await original(*args, **kwargs)
        if calls == 2:
            # Move gateway clock after the authority validity, without sleeping.
            import maezo.gateway.human.gateway as module

            class Clock:
                @classmethod
                def now(cls, tz):
                    return datetime.now(UTC) + timedelta(days=1)

            monkeypatch.setattr(module, "datetime", Clock)
        return result

    with pytest.MonkeyPatch.context() as monkeypatch:
        gateway._session = session
        with pytest.raises(GatewayRefusalError):
            await read(gateway)


async def test_absent_receipt_ports_fail_closed():
    gateway, *_ = await setup()
    with pytest.raises(GatewayRefusalError):
        await read(gateway)
