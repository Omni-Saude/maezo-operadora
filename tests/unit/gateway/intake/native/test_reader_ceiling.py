"""Reader lease deadlines across deferred native I/O; no engine/SQL qualification."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.intake import native_authority, native_source_lifecycle
from maezo.portal.api import session as session_module
from tests.unit.gateway.intake.native.test_session_authority import caller_binding
from tests.unit.gateway.intake.native.test_wire_transport import command


async def setup_reader(monkeypatch):
    caller = await caller_binding()
    current_time = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0] if tz is not None else current_time[0].replace(tzinfo=None)

    for module in (native_authority, native_source_lifecycle, session_module):
        monkeypatch.setattr(module, "datetime", Clock)
    store = caller._issuer.store
    key = (caller.principal.issuer, caller.principal.subject)
    original_membership = store.memberships[key]

    def membership_until(until):
        store.memberships[key] = original_membership.model_copy(update={"reviewed_until": until})

    c = command().model_copy(
        update={"actor": Actor.from_principal(caller.principal, caller.original.membership.audience)}
    )
    source = SimpleNamespace(
        protected=SimpleNamespace(original_principal=AsyncMock(return_value=caller.principal))
    )
    return caller, current_time, membership_until, c, source


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initial_observation", "final_observation", "later_refresh"])
async def test_shorter_actual_reader_ceiling_refuses_after_deferred_native_observation(monkeypatch, stage):
    caller, clock, membership_until, c, source = await setup_reader(monkeypatch)
    start = clock[0]
    short = start + timedelta(seconds=5)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    blocked_call = {"initial_observation": 1, "final_observation": 2, "later_refresh": 3}[stage]
    if stage == "initial_observation":
        membership_until(short)

    async def observation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if stage == "final_observation" and calls == 1:
            clock[0] = start + timedelta(seconds=3)
            membership_until(short)
        if calls == blocked_call:
            entered.set()
            await release.wait()
        return "unchanged-native-authority", caller.valid_until

    authority = native_authority.PostgresAuthEffectAuthorizationSource(
        source=source, native=SimpleNamespace(observation=observation)
    )
    if stage == "later_refresh":
        lease = await authority.current(c, read=True, caller=caller)
        clock[0] = start + timedelta(seconds=3)
        membership_until(short)
        pending = asyncio.create_task(lease.revalidate())
    else:
        pending = asyncio.create_task(authority.current(c, read=True, caller=caller))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        # Restore a later membership deadline while I/O is pending. Neither a
        # subsequent resolver read nor native success may renew the retained cut.
        membership_until(caller.valid_until)
        clock[0] = start + timedelta(seconds=6)
        release.set()
        with pytest.raises(AuthUnavailableError):
            await pending
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert calls == blocked_call
    if stage == "later_refresh":
        with pytest.raises(AuthUnavailableError):
            lease.guard(c, clock[0], read=True)


@pytest.mark.asyncio
async def test_successful_refresh_retains_shorter_reader_ceiling_for_later_disclosure(monkeypatch):
    caller, clock, membership_until, c, source = await setup_reader(monkeypatch)
    start = clock[0]
    native = SimpleNamespace(
        observation=AsyncMock(return_value=("unchanged-native-authority", caller.valid_until))
    )
    authority = native_authority.PostgresAuthEffectAuthorizationSource(source=source, native=native)
    lease = await authority.current(c, read=True, caller=caller)
    clock[0] = start + timedelta(seconds=3)
    membership_until(start + timedelta(seconds=5))
    await lease.revalidate()
    lease.guard(c, clock[0], read=True)
    membership_until(caller.valid_until)
    clock[0] = start + timedelta(seconds=4)
    await lease.revalidate()
    lease.guard(c, clock[0], read=True)
    clock[0] = start + timedelta(seconds=6)
    with pytest.raises(AuthUnavailableError):
        lease.guard(c, clock[0], read=True)
