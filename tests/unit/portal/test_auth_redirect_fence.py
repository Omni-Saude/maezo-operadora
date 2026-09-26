"""PHS-V1: real BFF routing behind explicit TLS offload; no proxy trust or engine mocks.

Reuses the unchanged author IdP helper for real RSA/PKCE. The independent verifier's
four original oracles remain external and immutable, and are executed separately.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.types import Receive, Scope, Send
from tests.unit.portal.test_human_session import (
    BROWSER_COOKIE,
    IDP,
    ISSUER,
    ORIGIN,
    PREFIX,
    SESSION_COOKIE,
    SUBJECT,
    Harness,
    SignedTestIdP,
    config,
    membership,
)

from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore

pytestmark = pytest.mark.asyncio
_PROXY_HEADERS = [
    {},
    {"X-Forwarded-Proto": "https"},
    {"Forwarded": 'for=192.0.2.1;proto=http;host="attacker.example.test"'},
    {
        "X-Forwarded-Proto": "http, https",
        "X-Forwarded-Host": "attacker.example.test",
        "X-Forwarded-Port": "80",
        "X-Forwarded-Prefix": "//attacker.example.test/collect",
        "Forwarded": 'for=192.0.2.1;proto=https;host="attacker.example.test"',
    },
]


@asynccontextmanager
async def edge_harness(scheme: str = "http") -> AsyncIterator[Harness]:
    store = LocalTestIdentityStore("test-tenant")
    store.memberships[(ISSUER, SUBJECT)] = membership()
    idp = SignedTestIdP()
    async with httpx.AsyncClient(transport=httpx.MockTransport(idp.handle)) as oidc:
        app = create_app(config(), store=store, oidc_client=oidc)

        async def backend(scope: Scope, receive: Receive, send: Send) -> None:
            # Browser sees HTTPS; entrypoint receives HTTP with proxy_headers=False.
            scope = {**scope, "scheme": scheme}
            await app(scope, receive, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(backend), base_url=ORIGIN) as client:
            yield Harness(client, store, idp, app)


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("headers", _PROXY_HEADERS, ids=["none", "tls", "forwarded", "conflicting"])
@pytest.mark.parametrize("method,route", [("GET", "login"), ("GET", "callback"), ("POST", "logout")])
async def test_noncanonical_auth_refused_without_redirect_or_effect(
    scheme: str, headers: dict[str, str], method: str, route: str
) -> None:
    async with edge_harness(scheme) as h:
        response = await h.client.request(
            method,
            f"{PREFIX}/auth/{route}/",
            headers=headers,
            params={"code": "CREDENTIAL_CANARY", "state": "STATE_CANARY", "extra": "not-allowed"},
        )
        assert response.status_code == 404
        assert "location" not in response.headers and "set-cookie" not in response.headers
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "CANARY" not in response.text and "attacker" not in response.text
        assert not h.idp.requests and not h.store._transactions and not h.store._sessions


@pytest.mark.parametrize("headers", _PROXY_HEADERS, ids=["none", "tls", "forwarded", "conflicting"])
async def test_exact_auth_lifecycle_behind_offload_ignores_proxy_authority(headers: dict[str, str]) -> None:
    async with edge_harness() as h:
        h.client.headers.update(headers)
        login = await h.client.get(PREFIX + "/auth/login", params={"return_to": "/portal"})
        assert login.status_code == 303
        assert login.headers["location"].startswith(IDP + "/oauth2/authorize?")
        params = h.idp.authorize(login.headers["location"], "canonical-code")
        callback = await h.finish(params, "canonical-code")
        assert callback.status_code == 303 and callback.headers["location"] == "/portal"
        assert h.idp.exchanges == 1 and not h.store._transactions
        cookie = callback.headers.get_list("set-cookie")[-1]
        assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
        assert "Domain=" not in cookie
        session = await h.client.get(PREFIX + "/session")
        assert session.status_code == 200
        assert session.json()["principal_ref"] == "human-internal-1"
        logout = await h.client.post(
            PREFIX + "/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": session.json()["csrf_token"]}
        )
        assert logout.status_code == 200 and "location" not in logout.headers
        assert logout.json()["idp_logout_url"].startswith(IDP + "/logout?client_id=")
        assert not h.store._sessions and SESSION_COOKIE not in h.client.cookies
        assert (await h.client.get(PREFIX + "/session")).status_code == 401


@pytest.mark.parametrize("method,route", [("GET", "login"), ("GET", "callback"), ("POST", "logout")])
async def test_proxy_header_cannot_repair_untrusted_host(method: str, route: str) -> None:
    async with edge_harness() as h:
        response = await h.client.request(
            method,
            f"{PREFIX}/auth/{route}",
            headers={
                "Host": "attacker.example.test",
                "X-Forwarded-Host": "portal.example.test",
                "X-Forwarded-Proto": "https",
                "Forwarded": 'proto=https;host="portal.example.test"',
            },
        )
        assert response.status_code == 400 and "location" not in response.headers
        assert not h.idp.requests and not h.store._transactions and not h.store._sessions


async def test_noncanonical_valid_callback_preserves_binding_without_exposing_credentials() -> None:
    async with edge_harness() as h:
        params = await h.start("bound-code")
        browser_binding = h.client.cookies[BROWSER_COOKIE]
        response = await h.client.get(
            PREFIX + "/auth/callback/", params={"code": "bound-code", "state": params["state"][0]}
        )
        assert response.status_code == 404 and "location" not in response.headers
        assert len(h.store._transactions) == 1 and not h.idp.requests
        assert h.client.cookies[BROWSER_COOKIE] == browser_binding
        assert not h.store._sessions
        assert (await h.finish(params, "bound-code")).status_code == 303
        assert h.idp.exchanges == 1 and not h.store._transactions


async def test_idp_denial_is_preconsumption_rejection_then_bound_code_can_finish() -> None:
    async with edge_harness() as h:
        params = await h.start("after-denial")
        denial = await h.client.get(
            PREFIX + "/auth/callback", params={"state": params["state"][0], "error": "access_denied"}
        )
        assert denial.status_code == 401 and "location" not in denial.headers
        assert len(h.store._transactions) == 1 and not h.idp.requests and not h.store._sessions
        assert (await h.finish(params, "after-denial")).status_code == 303
        assert h.idp.exchanges == 1 and not h.store._transactions
