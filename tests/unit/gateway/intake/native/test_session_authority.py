"""Focused offline session/cap/uncertainty controls; no SQL/runtime qualification."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from maezo.gateway.human.auth_profile import SessionBinding
from maezo.gateway.human.auth_transport import AuthEffectCeiling, AuthUnavailableError, sign_request
from maezo.gateway.human.read_profile import digest
from maezo.gateway.intake.native_source_lifecycle import AuthCallerBinding, PostgresAuthSourceLifecycle
from maezo.portal.engine.profile import strict_loads
from tests.unit.gateway.intake.native.test_wire_transport import HASH, NOW, command, effect_cap, signing


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
