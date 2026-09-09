"""PHG-V1: validity must survive authority I/O and real session re-resolution."""

from datetime import datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import NOW, SECRET, setup

import maezo.gateway.human.gateway as gateway_module
from maezo.gateway.human import GatewayRefusalError

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("expires", ["task", "authority"])
@pytest.mark.parametrize("stage", ["authority_read", "second_session"])
@pytest.mark.parametrize("offset_us", [-1, 0, 1], ids=["before", "equal", "after"])
async def test_read_requires_both_grants_valid_after_io(monkeypatch, expires, stage, offset_us):
    gateway, store, task_port, authority_port, admission = await setup()
    deadline = NOW + timedelta(minutes=1)
    completion = deadline + timedelta(microseconds=offset_us)

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(gateway_module, "datetime", Clock)
    task_port.task = task_port.task.model_copy(
        update={"valid_until": deadline if expires == "task" else NOW + timedelta(minutes=30)}
    )
    authority_port.authority = authority_port.authority.model_copy(
        update={"valid_until": deadline if expires == "authority" else NOW + timedelta(minutes=30)}
    )
    original_snapshot = task_port.task.snapshot
    original_membership = store.get_membership
    original_authority = authority_port.current_authority
    membership_calls = 0
    authority_calls = 0

    async def membership_read(issuer, subject):
        nonlocal membership_calls
        result = await original_membership(issuer, subject)
        membership_calls += 1
        if stage == "second_session" and membership_calls == 2:
            Clock.current = completion
        return result

    async def authority_read(principal, task):
        nonlocal authority_calls
        result = await original_authority(principal, task)
        authority_calls += 1
        if stage == "authority_read":
            Clock.current = completion
        return result

    monkeypatch.setattr(store, "get_membership", membership_read)
    monkeypatch.setattr(authority_port, "current_authority", authority_read)
    if offset_us < 0:
        projection = await gateway.read_task(session_secret=SECRET, task_id="task-1")
        assert projection == original_snapshot.model_copy(update={"allowed_actions": ("claim",)})
    else:
        with pytest.raises(GatewayRefusalError, match="^authority_unavailable$"):
            await gateway.read_task(session_secret=SECRET, task_id="task-1")
    # The real resolver must reach the selected I/O seam; no sleep or resolver bypass.
    early_authority_refusal = stage == "authority_read" and expires == "authority" and offset_us >= 0
    assert membership_calls == (1 if early_authority_refusal else 2)
    assert authority_calls == 1
    assert task_port.calls == 1
    assert task_port.task.snapshot == original_snapshot
    assert not admission.calls
