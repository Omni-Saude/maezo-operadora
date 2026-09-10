"""Actual recovery HTTP route checks with synthetic identity/SQL source seams."""

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.gateway.intake.recovery.test_recovery import add, setup

from maezo.portal.api.intake_recovery import intake_recovery_router

__all__ = ["setup"]
PREFIX = "/api/v1/portal/intake-recovery"
COOKIE = {"Cookie": "__Host-maezo-session=" + "s" * 43}


def app(h):
    result = FastAPI()
    result.state.human_session_resolver = h.sessions
    result.state.intake_recovery_factory = lambda resolver: h.service
    result.include_router(intake_recovery_router)
    return result


@pytest.mark.asyncio
async def test_actual_pointer_routes_and_closed_openapi(setup):
    h = setup
    command, intake = add(h, state="started")
    application = app(h)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        observed = await client.get(PREFIX + "/commands/" + command, headers=COOKIE)
        assert observed.status_code == 200
        assert observed.json() == {
            "schema_version": 1,
            "command_id": command,
            "observation": "observed",
            "intake_ref": intake,
        }
        page = await client.get(PREFIX, headers=COOKIE)
        assert page.json() == {
            "schema_version": 1,
            "scope": "actor_admissions",
            "items": [{"command_id": command, "intake_ref": intake}],
            "next_cursor": None,
        }
        assert observed.headers["cache-control"] == page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["vary"] == "Cookie"
    schemas = application.openapi()["components"]["schemas"]
    assert schemas["IntakeRecoveryItem"]["additionalProperties"] is False
    assert set(schemas["IntakeRecoveryItem"]["properties"]) == {"command_id", "intake_ref"}
    assert schemas["IntakeRecoveryPage"]["properties"]["items"]["maxItems"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,body,headers,status",
    [
        ("?tenant=other", None, COOKIE, 400),
        ("?cursor=" + "a" * 32 + "&cursor=" + "b" * 32, None, COOKIE, 400),
        ("?cursor=" + "a" * 161, None, COOKIE, 400),
        ("?cursor=bad", None, COOKIE, 400),
        ("/commands/" + "a" * 32 + "?cursor=" + "b" * 32, None, COOKIE, 400),
        ("/commands/bad", None, COOKIE, 400),
        ("", b"{}", COOKIE, 400),
        ("", None, {}, 401),
        (
            "",
            None,
            {"Cookie": "__Host-maezo-session=" + "s" * 43 + "; __Host-maezo-session=" + "t" * 43},
            401,
        ),
    ],
)
async def test_closed_query_body_cookie_errors(setup, suffix, body, headers, status):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(setup)), base_url="https://test"
    ) as client:
        result = await client.request("GET", PREFIX + suffix, content=body, headers=headers)
    assert result.status_code == status
    assert set(result.json()) == {"code"}
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_missing_factory_and_private_source_failures_are_bounded(setup):
    h = setup
    application = app(h)
    application.state.intake_recovery_factory = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        result = await client.get(PREFIX, headers=COOKIE)
        assert result.status_code == 503 and result.json() == {"code": "dependency_unavailable"}
        application.state.intake_recovery_factory = lambda resolver: h.service
        add(h)

        def private_failure():
            raise RuntimeError("PRIVATE_AUTHORITY_CANARY")

        h.authority.on_read = private_failure
        result = await client.get(PREFIX, headers=COOKIE)
        assert result.status_code == 503
        assert "PRIVATE" not in result.text
