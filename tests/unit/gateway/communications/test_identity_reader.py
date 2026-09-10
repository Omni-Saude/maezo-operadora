"""Request-local original identity reads and conditional remote revocation."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.communications.identity_owner import identity_record_digest
from maezo.gateway.communications.identity_reader import PhiIdentityReader
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

NOW = datetime.now(UTC)
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"


def records(name: str) -> tuple[SessionRecord, MembershipRecord]:
    member = MembershipRecord(
        tenant="tenant",
        issuer=ISSUER,
        subject="subject-" + name,
        principal_ref="principal-" + name,
        revision=2,
        audience="provider",
        memberships=(MembershipBinding(membership_ref="member-" + name, roles=("provider",), groups=()),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref="provider-resource-" + name),),
        reviewed_until=NOW + timedelta(minutes=5),
    )
    session = SessionRecord(
        secret_hash=("a" if name == "one" else "b") * 64,
        session_ref="session-" + name,
        csrf_token="csrf-" + name,
        issuer=ISSUER,
        subject=member.subject,
        principal_ref=member.principal_ref,
        membership_revision=1,
        authenticated_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    return session, member


class Result:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class Connection:
    def __init__(self, sessions: dict[str, SessionRecord], members: dict[str, MembershipRecord]) -> None:
        self.sessions, self.members = sessions, members

    async def execute(self, query, params):
        sql = str(query)
        if "public.portal_sessions" in sql:
            value = self.sessions.get(params["secret"])
        else:
            value = self.members.get(params["subject"])
        return Result(None if value is None else value.model_dump_json())


class Context:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_request_local_observations_do_not_cross_concurrent_sessions():
    first = records("one")
    second = records("two")
    sessions = {record.secret_hash: record for record, _ in (first, second)}
    members = {record.subject: record for _, record in (first, second)}
    connection = Connection(sessions, members)
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: Context(connection),
        dispose=AsyncMock(),
    )
    owner = SimpleNamespace(tenant="tenant", revoke=AsyncMock(), close=AsyncMock())
    reader = PhiIdentityReader("tenant", engine, owner)
    ready = asyncio.Event()
    count = 0

    async def resolve(pair):
        nonlocal count
        session, member = pair
        assert await reader.get_session(session.secret_hash, NOW) == session
        count += 1
        if count == 2:
            ready.set()
        await ready.wait()
        assert await reader.get_membership(member.issuer, member.subject) == member
        await reader.revoke_session(session.secret_hash)

    await asyncio.gather(resolve(first), resolve(second))
    calls = {
        call.kwargs["session_hash"]: (
            call.kwargs["observed_session_digest"],
            call.kwargs["observed_membership_digest"],
        )
        for call in owner.revoke.await_args_list
    }
    assert calls == {
        session.secret_hash: (identity_record_digest(session), identity_record_digest(member))
        for session, member in (first, second)
    }


@pytest.mark.asyncio
async def test_missing_or_changed_request_observation_never_calls_owner():
    session, member = records("one")
    connection = Connection({session.secret_hash: session}, {member.subject: member})
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: Context(connection),
        dispose=AsyncMock(),
    )
    owner = SimpleNamespace(tenant="tenant", revoke=AsyncMock(), close=AsyncMock())
    reader = PhiIdentityReader("tenant", engine, owner)
    with pytest.raises(AuthenticationError):
        await reader.revoke_session(session.secret_hash)
    assert await reader.get_session(session.secret_hash, NOW) == session
    with pytest.raises(AuthenticationError):
        await reader.get_membership(ISSUER, "other-subject")
    with pytest.raises(AuthenticationError):
        await reader.revoke_session(session.secret_hash)
    owner.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_refuses_all_local_writes_and_closes_owned_resources_once():
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"), dispose=AsyncMock(), connect=AsyncMock()
    )
    owner = SimpleNamespace(tenant="tenant", revoke=AsyncMock(), close=AsyncMock())
    reader = PhiIdentityReader("tenant", engine, owner)
    with pytest.raises(AuthenticationError):
        await reader.put_session(records("one")[0], None)
    with pytest.raises(AuthenticationError):
        await reader.purge_expired(NOW)
    await reader.close()
    await reader.close()
    owner.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
