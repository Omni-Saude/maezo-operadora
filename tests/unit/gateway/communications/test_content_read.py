"""Actual encrypted read and final boundary with synthetic transaction/current authority."""

import json
from datetime import timedelta

import pytest
from tests.unit.gateway.communications.test_delivery import REF, SECRET, send, setup
from tests.unit.portal.test_human_session import config

from maezo.gateway.external_cases.models import ExternalCaseError, instant
from maezo.portal.api.communication_phi import create_phi_communication_app
from maezo.portal.api.store import LocalTestIdentityStore

__all__ = ["setup"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "narrow", ["authority", "key", "session", "final_session", "final_membership", "renewed_authority"]
)
async def test_content_deadline_retains_original_and_final_minimum(setup, monkeypatch, narrow):
    s = setup
    published = await send(s)
    shortened = s.clock[0] + timedelta(seconds=5)
    original = s.authority.authorize
    calls = 0

    async def changing(access):
        nonlocal calls
        calls += 1
        if narrow == "authority" and calls == 2:
            s.authority.until = shortened
        if calls == 2 and narrow == "final_session":
            s.resolver.session_until = shortened
        if calls == 2 and narrow == "final_membership":
            s.resolver.member_until = shortened
        if calls == 2 and narrow == "renewed_authority":
            s.authority.until = shortened + timedelta(minutes=1)
        return await original(access)

    monkeypatch.setattr(s.authority, "authorize", changing)
    monkeypatch.setattr("maezo.gateway.communications.content.now", lambda: s.clock[0])
    if narrow == "renewed_authority":
        s.authority.until = shortened
    if narrow == "key":
        s.keys.valid_until = shortened
    if narrow == "session":
        s.resolver.session_until = shortened
    raw = await s.phi.read_content(
        SECRET, case_ref=REF, communication_ref=published["communication_ref"], freeze=lambda raw: raw
    )
    value = json.loads(raw)
    assert set(value) == {"body", "communication_ref", "observed_at", "valid_until"}
    assert value["body"] == "PRIVATE_TEXT_CANARY"
    assert value["observed_at"] == instant(s.clock[0])
    assert value["valid_until"] == instant(shortened)

    if narrow == "renewed_authority":
        s.keys.valid_until = shortened

    def expired(raw):
        s.clock[0] = shortened + timedelta(seconds=1)
        return raw

    with pytest.raises(ExternalCaseError):
        await s.phi.read_content(
            SECRET, case_ref=REF, communication_ref=published["communication_ref"], freeze=expired
        )


def test_read_only_installed_profile_excludes_preservation_and_plaintext_general_routes():
    settings = config()

    def unavailable(_):
        raise AssertionError("Route graph must not call provider")

    app = create_phi_communication_app(
        settings,
        identity_store=LocalTestIdentityStore(settings.tenant),
        content_service_factory=unavailable,
        read_only=True,
    )
    schema = app.openapi()
    assert len(schema["paths"]) == 1
    assert all(set(operations) == {"get"} for operations in schema["paths"].values())
    assert "CommunicationContentSubmission" not in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["CommunicationContent"]["additionalProperties"] is False
