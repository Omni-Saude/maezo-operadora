"""Focused offline session/cap/uncertainty controls; no SQL/runtime qualification."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from maezo.gateway.human.auth_profile import Actor, SessionBinding
from maezo.gateway.human.auth_transport import AuthEffectCeiling, AuthUnavailableError, sign_request
from maezo.gateway.human.read_profile import digest, wire
from maezo.gateway.intake.native_authority import PostgresAuthEffectAuthorizationSource
from maezo.gateway.intake.native_source_lifecycle import AuthCallerBinding, PostgresAuthSourceLifecycle
from maezo.portal.admin.assignments import PostgresStaffAssignmentAdministration
from maezo.portal.api.auth import digest as session_digest
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.portal.engine.profile import strict_loads
from tests.unit.gateway.intake.native.test_wire_transport import HASH, NOW, command, effect_cap, signing

ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"


def identity_records(*, principal_ref: str = "principal") -> tuple[MembershipRecord, SessionRecord]:
    now = datetime.now(UTC)
    membership = MembershipRecord(
        tenant="tenant",
        issuer=ISSUER,
        subject="subject",
        principal_ref=principal_ref,
        revision=7,
        audience="provider",
        memberships=(MembershipBinding(membership_ref="review", roles=("provider",), groups=()),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref="provider"),),
        reviewed_until=now + timedelta(minutes=5),
    )
    session = SessionRecord(
        secret_hash=session_digest("s" * 43),
        session_ref="session",
        csrf_token="csrf",
        issuer=ISSUER,
        subject="subject",
        principal_ref=principal_ref,
        membership_revision=membership.revision,
        authenticated_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    return membership, session


async def caller_binding(*, principal_ref: str = "principal"):
    membership, session = identity_records(principal_ref=principal_ref)
    store = LocalTestIdentityStore("tenant")
    store.memberships[(membership.issuer, membership.subject)] = membership
    await store.put_session(session, None)
    resolver = HumanSessionResolver(
        PortalSettings(
            tenant="tenant",
            issuer=ISSUER,
            cognito_origin="https://humans.auth.sa-east-1.amazoncognito.com",
            client_id="human123",
            machine_client_id="machine123",
            client_purpose="dedicated-human-code-pkce",
            public_origin="https://portal.example.test",
            mode="local-test",
        ),
        store,
    )
    return await AuthCallerBinding.resolve(resolver, "s" * 43)


def test_mutation_requires_original_session_cap():
    with pytest.raises(AuthUnavailableError):
        sign_request(command(), signing(), NOW)


def test_original_minimum_is_floored_without_changing_command_bytes():
    c = command()
    cap = effect_cap(c, 3.9)
    outer = strict_loads(sign_request(c, signing(), NOW, effect_ceiling=cap))
    assert outer["expires_at"] == str(int(NOW.timestamp()) + 3)
    assert outer["digest"] == digest(c)


def test_cap_cannot_be_reused_for_another_immutable_command():
    c = command().model_copy(update={"projected_variables_digest": "b" * 64})
    with pytest.raises(AuthUnavailableError):
        sign_request(c, signing(), NOW, effect_ceiling=effect_cap())


def test_subsecond_remaining_session_cannot_sign_an_already_expired_envelope():
    with pytest.raises(AuthUnavailableError):
        sign_request(command(), signing(), NOW, effect_ceiling=effect_cap(seconds=0.9))


@pytest.mark.parametrize("until", [NOW - timedelta(seconds=10), NOW + timedelta(seconds=61)])
def test_closed_binding_refuses_authorization_outside_original_session(until):
    with pytest.raises(ValidationError):
        SessionBinding(
            session_ref="session",
            authenticated_at=NOW - timedelta(seconds=5),
            session_expires_at=NOW + timedelta(seconds=60),
            authorization_until=until,
            session_source_revision=1,
            session_record_digest=HASH,
        )


def test_independent_lower_source_deadline_is_retained():
    original = effect_cap()
    cap = AuthEffectCeiling(original.command_digest, original.session, NOW + timedelta(seconds=2))
    assert cap.until(command()) == NOW + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_arbitrary_session_supplier_cannot_mint_resolver_binding():
    supplier = SimpleNamespace(resolve=AsyncMock())
    with pytest.raises(AuthUnavailableError):
        await AuthCallerBinding.resolve(supplier, "synthetic-secret")
    supplier.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_recovers_same_source_change_before_missing_row_shortcut():
    source = object.__new__(PostgresAuthSourceLifecycle)
    source._resume_session_change = AsyncMock(return_value=True)
    # No reader exists: a completed/reconciling change owns this retry, so the
    # public entry point must not read absent source state or mint another intent.
    await source.revoke_session("synthetic-secret-hash")
    source._resume_session_change.assert_awaited_once_with(revoked_hash="synthetic-secret-hash")


@pytest.mark.asyncio
async def test_final_publication_checkpoint_refuses_disclosure_but_retains_committed_receipt():
    from maezo.gateway.human.auth_profile import InputPublication, PublicationReceipt
    from tests.unit.gateway.intake.native.test_wire_transport import scope, source

    publication = InputPublication(
        schema="human-auth-input-publication.v1",
        scope=scope(),
        workload_ref="publisher",
        publication_id="publication",
        kind="actor",
        resource_ref="principal",
        expected_generation=1,
        source=source(),
        state="frozen",
        payload=None,
        payload_digest=None,
        valid_until=NOW + timedelta(seconds=30),
    )
    receipt = PublicationReceipt(
        schema="human-auth-input-receipt.v1",
        scope=scope(),
        publication_id=publication.publication_id,
        request_digest=digest(publication),
        kind="actor",
        resource_ref="principal",
        previous_generation=1,
        head_generation=2,
        state="frozen",
        payload_digest=None,
        committed_at=NOW,
    )
    lifecycle = object.__new__(PostgresAuthSourceLifecycle)
    lifecycle.clock = lambda: NOW
    committed = []

    async def acknowledge(p, r):
        committed.append((p, r))

    lifecycle.journal = SimpleNamespace(freeze=AsyncMock(return_value=receipt), acknowledge=acknowledge)
    lifecycle.native = SimpleNamespace(execute=AsyncMock())

    async def current():
        if committed:
            raise AuthUnavailableError()

    with pytest.raises(AuthUnavailableError):
        await lifecycle._native_publication_receipt(publication, current)
    assert committed == [(publication, receipt)]
    lifecycle.native.execute.assert_not_awaited()


def test_identity_source_json_round_trips_numeric_revisions_separately_from_native_wire():
    membership, session = identity_records()
    for record, revision in ((membership, "revision"), (session, "membership_revision")):
        source = PostgresAuthSourceLifecycle.source_record(record)
        assert type(source[revision]) is int
        assert type(wire(record)[revision]) is str
        assert type(record).model_validate_json(json.dumps(source)) == record


@pytest.mark.asyncio
async def test_guarded_staff_snapshot_uses_only_pinned_owner_lock_function():
    membership, _ = identity_records()

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [membership.model_dump_json()]

    db = SimpleNamespace(execute=AsyncMock(return_value=Result()))
    lifecycle = object.__new__(PostgresAuthSourceLifecycle)
    lifecycle.binding = SimpleNamespace(writer_role="writer", tenant="tenant")
    lifecycle.qualified = AsyncMock()
    records = await lifecycle.locked_staff_memberships(db)
    assert records == (membership,)
    lifecycle.qualified.assert_awaited_once_with(db, "writer")
    assert str(db.execute.await_args.args[0]) == ("SELECT * FROM portal_auth.lock_staff_memberships(:tenant)")

    ddl = (Path(__file__).parents[5] / "src/maezo/gateway/intake/native-authority-postgres.sql").read_text()
    function = ddl.split("CREATE FUNCTION portal_auth.lock_staff_memberships", 1)[1].split(
        "CREATE FUNCTION portal_auth.apply_change", 1
    )[0]
    assert "SECURITY DEFINER" in function
    assert "session_user<>installed.writer_role" in function
    assert "ORDER BY principal_ref FOR SHARE" in function


@pytest.mark.asyncio
async def test_guarded_assignment_path_applies_change_then_uses_function_snapshot():
    membership, _ = identity_records()
    source = SimpleNamespace(
        apply_change=AsyncMock(),
        locked_staff_memberships=AsyncMock(return_value=(membership,)),
    )
    administration = object.__new__(PostgresStaffAssignmentAdministration)
    administration.auth_source = source
    db = object()
    applied: list[str] = []
    records = await administration._guarded_memberships(
        db, (membership,), {membership.principal_ref: "change"}, applied
    )
    assert records == (membership,)
    assert applied == ["change"]
    source.apply_change.assert_awaited_once_with("change", db)
    source.locked_staff_memberships.assert_awaited_once_with(db)


@pytest.mark.asyncio
async def test_receipt_read_requires_explicit_resolver_binding_and_accepts_same_human():
    c = command()
    actor = Actor(
        principal_ref="principal",
        issuer=ISSUER,
        subject="subject",
        membership_revision=7,
        audience="provider",
    )
    c = c.model_copy(update={"actor": actor})
    caller = await caller_binding()
    source = SimpleNamespace(
        protected=SimpleNamespace(
            original_identity=AsyncMock(),
            original_principal=AsyncMock(return_value=caller.principal),
        )
    )
    native = SimpleNamespace(observation=AsyncMock(return_value=("observation", caller.valid_until)))
    authority = PostgresAuthEffectAuthorizationSource(source=source, native=native)

    with pytest.raises(AuthUnavailableError):
        await authority.current(c, read=True)
    source.protected.original_identity.assert_not_awaited()
    source.protected.original_principal.assert_not_awaited()

    lease = await authority.current(c, read=True, caller=caller)
    lease.guard(c, datetime.now(UTC), read=True)
    source.protected.original_identity.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_read_refuses_explicit_binding_for_another_human():
    c = command()
    actor = Actor(
        principal_ref="principal",
        issuer=ISSUER,
        subject="subject",
        membership_revision=7,
        audience="provider",
    )
    c = c.model_copy(update={"actor": actor})
    original = await caller_binding()
    wrong = await caller_binding(principal_ref="other-principal")
    source = SimpleNamespace(
        protected=SimpleNamespace(
            original_identity=AsyncMock(),
            original_principal=AsyncMock(return_value=original.principal),
        )
    )
    native = SimpleNamespace(observation=AsyncMock())
    authority = PostgresAuthEffectAuthorizationSource(source=source, native=native)

    with pytest.raises(AuthUnavailableError):
        await authority.current(c, read=True, caller=wrong)
    native.observation.assert_not_awaited()
