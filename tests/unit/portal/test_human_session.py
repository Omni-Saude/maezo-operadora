"""ADR-0049 D4 adversarial ASGI + real RSA signatures with an explicit in-process test IdP.

No live Cognito authentication, ratified user, PostgreSQL or engine execution is claimed here.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from maezo.portal.api.app import PrivacyBoundary, create_app
from maezo.portal.api.auth import AuthenticationError, digest
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

PREFIX = "/api/v1/portal"
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
ORIGIN = "https://portal.example.test"
IDP = "https://humans.auth.sa-east-1.amazoncognito.com"
SUBJECT = "00000000-0000-4000-8000-000000000001"
SESSION_COOKIE = "__Host-maezo-session"
BROWSER_COOKIE = "__Host-maezo-login"
pytestmark = pytest.mark.asyncio


def config(**updates: object) -> PortalSettings:
    values: dict[str, object] = dict(
        tenant="test-tenant",
        issuer=ISSUER,
        cognito_origin=IDP,
        client_id="human123",
        machine_client_id="machine123",
        client_purpose="dedicated-human-code-pkce",
        public_origin=ORIGIN,
        mode="local-test",
    )
    values.update(updates)
    return PortalSettings(**values)


def membership(**updates: object) -> MembershipRecord:
    values: dict[str, object] = dict(
        tenant="test-tenant",
        issuer=ISSUER,
        subject=SUBJECT,
        principal_ref="human-internal-1",
        revision=1,
        audience="staff",
        memberships=(
            MembershipBinding(membership_ref="review-1", roles=("staff",), groups=("medico-auditor",)),
        ),
        subject_bindings=(),
        reviewed_until=datetime.now(UTC) + timedelta(hours=2),
    )
    values.update(updates)
    return MembershipRecord(**values)


class SignedTestIdP:
    """Fake transport, real PyJWT/cryptography: verifies request PKCE before signing a token."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.authorizations: dict[str, dict[str, list[str]]] = {}
        self.claim_updates: dict[str, object] = {}
        self.remove_claims: set[str] = set()
        self.headers: dict[str, object] = {}
        self.algorithm = "RS256"
        self.wrong_key = False
        self.key_changes: dict[str, object] = {}
        self.duplicate_key = False
        self.token_override: str | None = None
        self.exchange_status = 200
        self.jwks_status = 200
        self.exchanges = 0
        self.requests: list[httpx.Request] = []
        self.last_token = ""

    def authorize(self, url: str, code: str) -> dict[str, list[str]]:
        parsed = urlsplit(url)
        assert parsed.scheme + "://" + parsed.netloc == IDP
        assert parsed.path == "/oauth2/authorize"
        params = parse_qs(parsed.query)
        assert params["client_id"] == ["human123"]
        assert params["response_type"] == ["code"]
        assert params["scope"] == ["openid"]
        assert params["redirect_uri"] == [ORIGIN + PREFIX + "/auth/callback"]
        assert params["code_challenge_method"] == ["S256"]
        self.authorizations[code] = params
        return params

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == IDP + "/oauth2/token":
            self.exchanges += 1
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["client_id"] == ["human123"]
            assert form["redirect_uri"] == [ORIGIN + PREFIX + "/auth/callback"]
            verifier = form["code_verifier"][0]
            params = self.authorizations[form["code"][0]]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            )
            assert params["code_challenge"] == [challenge]
            now = int(datetime.now(UTC).timestamp())
            claims: dict[str, object] = dict(
                iss=ISSUER,
                aud="human123",
                sub=SUBJECT,
                nonce=params["nonce"][0],
                iat=now,
                exp=now + 1800,
                auth_time=now - 10,
                token_use="id",
            )
            claims.update(self.claim_updates)
            for name in self.remove_claims:
                claims.pop(name, None)
            signing_key = self.other_key if self.wrong_key else self.key
            if self.algorithm.startswith("HS"):
                signing_key = b"synthetic-hmac-key-never-production-0123456789"  # type: ignore[assignment]
            if self.algorithm == "none":
                signing_key = ""  # type: ignore[assignment]
            self.last_token = self.token_override or jwt.encode(
                claims,
                signing_key,
                algorithm=self.algorithm,
                headers={"kid": "test-key", **self.headers},
            )
            return httpx.Response(
                self.exchange_status,
                json={
                    "id_token": self.last_token,
                    "access_token": "ACCESS_CANARY_PRIVATE",
                    "refresh_token": "REFRESH_CANARY_PRIVATE",
                    "token_type": "Bearer",
                },
                headers={"Location": "https://attacker.test/exfiltrate"},
            )
        assert str(request.url) == ISSUER + "/.well-known/jwks.json"
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        key.update(kid="test-key", use="sig", alg="RS256")
        key.update(self.key_changes)
        return httpx.Response(
            self.jwks_status,
            json={"keys": [key, key] if self.duplicate_key else [key]},
            headers={"Location": "https://attacker.test/exfiltrate"},
        )


@dataclass
class Harness:
    client: httpx.AsyncClient
    store: LocalTestIdentityStore
    idp: SignedTestIdP
    app: object

    async def start(self, code: str = "TEST_CODE_CANARY") -> dict[str, list[str]]:
        response = await self.client.get(PREFIX + "/auth/login")
        assert response.status_code == 303
        return self.idp.authorize(response.headers["location"], code)

    async def finish(self, params: dict[str, list[str]], code: str = "TEST_CODE_CANARY") -> httpx.Response:
        return await self.client.get(
            PREFIX + "/auth/callback", params={"state": params["state"][0], "code": code}
        )

    async def login(self, code: str = "TEST_CODE_CANARY") -> httpx.Response:
        return await self.finish(await self.start(code), code)


@pytest.fixture
async def h() -> Harness:
    store = LocalTestIdentityStore("test-tenant")
    store.memberships[(ISSUER, SUBJECT)] = membership()
    idp = SignedTestIdP()
    oidc = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
    app = create_app(config(), store=store, oidc_client=oidc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        yield Harness(client, store, idp, app)
    await oidc.aclose()


async def test_real_pkce_cookie_session_projection_and_logout(h: Harness) -> None:
    login = await h.client.get(PREFIX + "/auth/login")
    cookie = login.headers["set-cookie"]
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
    assert "Domain=" not in cookie
    params = h.idp.authorize(login.headers["location"], "code-1")
    callback = await h.finish(params, "code-1")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    session_secret = h.client.cookies[SESSION_COOKIE]
    assert len(session_secret) == 43
    assert session_secret not in repr(h.store._sessions)
    response = await h.client.get(PREFIX + "/session")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"schema_version", "principal_ref", "audience", "roles", "expires_at", "csrf_token"}
    assert data["principal_ref"] == "human-internal-1"
    assert data["roles"] == ["staff"]
    assert all(x not in response.text for x in (ISSUER, SUBJECT, "medico-auditor", h.idp.last_token))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    logged_out = await h.client.post(
        PREFIX + "/auth/logout",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": data["csrf_token"],
        },
    )
    assert logged_out.status_code == 204
    assert SESSION_COOKIE not in h.client.cookies
    assert not h.store._sessions
    assert (
        await h.client.get(PREFIX + "/session", headers={"Cookie": f"{SESSION_COOKIE}={session_secret}"})
    ).status_code == 401


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": ISSUER + "wrong"},
        {"aud": "machine123"},
        {"aud": ["human123"]},
        {"azp": "machine123"},
        {"token_use": "access"},
        {"nonce": "attacker-nonce"},
        {"exp": 1},
        {"iat": 4102444800},
        {"nbf": 4102444800},
        {"exp": "4102444800"},
        {"iat": True},
        {"auth_time": True},
        {"auth_time": 4102444800},
        {"auth_time": 0},
        {"sub": ""},
        {"sub": "https://attacker.test"},
        {"sub": "name with space"},
        {"sub": 123},
        {"nbf": "1"},
        {"iat": 1, "auth_time": 1},
    ],
)
async def test_rejects_signed_invalid_claims(h: Harness, updates: dict[str, object]) -> None:
    h.idp.claim_updates = updates
    assert (await h.login()).status_code == 401
    assert not h.store._sessions


@pytest.mark.parametrize("field", ["iss", "aud", "sub", "exp", "iat", "auth_time", "nonce", "token_use"])
async def test_rejects_signed_missing_required_claims(h: Harness, field: str) -> None:
    h.idp.remove_claims = {field}
    assert (await h.login()).status_code == 401
    assert not h.store._sessions


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS384"])
async def test_rejects_algorithm_confusion(h: Harness, algorithm: str) -> None:
    h.idp.algorithm = algorithm
    assert (await h.login()).status_code == 401
    assert not h.store._sessions


async def test_rejects_wrong_signing_key(h: Harness) -> None:
    h.idp.wrong_key = True
    assert (await h.login()).status_code == 401
    assert not h.store._sessions


@pytest.mark.parametrize(
    "headers",
    [
        {"jku": "https://attacker.test/jwks"},
        {"x5u": "https://attacker.test/key"},
        {"jwk": {}},
        {"crit": ["any"]},
        {"kid": "unknown"},
    ],
)
async def test_never_fetches_token_selected_key_url(h: Harness, headers: dict[str, object]) -> None:
    h.idp.headers = headers
    assert (await h.login()).status_code == 401
    assert all("attacker" not in str(r.url) for r in h.idp.requests)


@pytest.mark.parametrize(
    "key", [{"kty": "EC"}, {"use": "enc"}, {"alg": "HS256"}, {"d": "private"}, {"key_ops": ["sign"]}]
)
async def test_rejects_wrong_jwk_purpose(h: Harness, key: dict[str, object]) -> None:
    h.idp.key_changes = key
    assert (await h.login()).status_code == 401


async def test_rejects_duplicate_jwk_id(h: Harness) -> None:
    h.idp.duplicate_key = True
    assert (await h.login()).status_code == 401


@pytest.mark.parametrize("endpoint", ["exchange_status", "jwks_status"])
async def test_tls_endpoint_redirect_fails_closed(h: Harness, endpoint: str) -> None:
    setattr(h.idp, endpoint, 302)
    assert (await h.login()).status_code == 401
    assert all("attacker" not in str(r.url) for r in h.idp.requests)
    assert not h.store._sessions


async def test_wrong_browser_and_state_do_not_reach_token_exchange(h: Harness) -> None:
    params = await h.start()
    bound_cookie = h.client.cookies[BROWSER_COOKIE]
    h.client.cookies.clear()
    assert (await h.finish(params)).status_code == 401
    h.client.cookies.set(BROWSER_COOKIE, "A" * 43)
    assert (await h.finish(params)).status_code == 401
    h.client.cookies.clear()
    h.client.cookies.set(BROWSER_COOKIE, bound_cookie)
    assert (await h.finish({"state": ["B" * 43]})).status_code == 401
    assert h.idp.exchanges == 0
    assert (await h.finish(params)).status_code == 303


async def test_callback_replay_and_reused_code_are_atomic(h: Harness) -> None:
    params = await h.start()
    results = await asyncio.gather(h.finish(params), h.finish(params))
    assert sorted(r.status_code for r in results) == [303, 401]
    assert h.idp.exchanges == 1
    assert len(h.store._sessions) == 1
    params2 = await h.start()
    assert (await h.finish(params2)).status_code == 401
    assert h.idp.exchanges == 1


async def test_failed_exchange_burns_transaction_and_code(h: Harness) -> None:
    params = await h.start()
    h.idp.exchange_status = 503
    assert (await h.finish(params)).status_code == 401
    h.idp.exchange_status = 200
    assert (await h.finish(params)).status_code == 401
    h.idp.authorizations["another-code"] = params
    assert (await h.finish(params, "another-code")).status_code == 401
    assert h.idp.exchanges == 1


async def test_rotation_revokes_previous_server_session(h: Harness) -> None:
    assert (await h.login("first")).status_code == 303
    old = h.client.cookies[SESSION_COOKIE]
    old_csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    assert (await h.login("second")).status_code == 303
    assert old != h.client.cookies[SESSION_COOKIE]
    assert len(h.store._sessions) == 1
    assert old_csrf != (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    assert (
        await h.client.get(PREFIX + "/session", headers={"Cookie": f"{SESSION_COOKIE}={old}"})
    ).status_code == 401


@pytest.mark.parametrize(
    "origin", [None, "null", "https://attacker.test", ORIGIN + ".attacker.test", ORIGIN + "/"]
)
async def test_logout_requires_exact_origin(h: Harness, origin: str | None) -> None:
    assert (await h.login()).status_code == 303
    csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    if origin:
        headers["Origin"] = origin
    response = await h.client.post(PREFIX + "/auth/logout", headers=headers)
    assert response.status_code == 401
    assert (await h.client.get(PREFIX + "/session")).status_code == 200


@pytest.mark.parametrize("csrf", [None, "", "forged"])
async def test_logout_requires_session_csrf(h: Harness, csrf: str | None) -> None:
    assert (await h.login()).status_code == 303
    headers = {"Origin": ORIGIN}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    assert (await h.client.post(PREFIX + "/auth/logout", headers=headers)).status_code == 401
    assert (await h.client.get(PREFIX + "/session")).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.test",
        "//attacker.test",
        "/\\attacker.test",
        "/portal?phi=name",
        "/portal#token",
        "/portal/../admin",
        "%2f%2fattacker",
        "/portal\r\nLocation:bad",
    ],
)
async def test_rejects_return_path_injection(h: Harness, path: str) -> None:
    response = await h.client.get(PREFIX + "/auth/login", params={"return_to": path})
    assert response.status_code == 401
    assert "location" not in response.headers
    assert path not in response.text


@pytest.mark.parametrize(
    "mutation",
    ["missing", "revoked", "revision", "expired", "other-tenant", "other-subject", "other-principal"],
)
async def test_membership_revalidated_each_resolve(h: Harness, mutation: str) -> None:
    assert (await h.login()).status_code == 303
    row = h.store.memberships[(ISSUER, SUBJECT)]
    updates = {
        "revoked": {"revoked": True},
        "revision": {"revision": 2},
        "expired": {"reviewed_until": datetime.now(UTC) - timedelta(seconds=1)},
        "other-tenant": {"tenant": "foreign"},
        "other-subject": {"subject": "foreign"},
        "other-principal": {"principal_ref": "foreign"},
    }
    if mutation == "missing":
        h.store.memberships.clear()
    else:
        h.store.memberships[(ISSUER, SUBJECT)] = row.model_copy(update=updates[mutation])
    assert (await h.client.get(PREFIX + "/session")).status_code == 401


async def test_login_does_not_create_membership_from_cognito_groups(h: Harness) -> None:
    h.store.memberships.clear()
    h.idp.claim_updates = {"cognito:groups": ["medico-auditor", "admin"], "email": "admin@example.test"}
    assert (await h.login()).status_code == 401
    assert not h.store._sessions
    assert not h.store.memberships


async def test_browser_tenant_and_actor_do_not_define_identity(h: Harness) -> None:
    assert (await h.login()).status_code == 303
    response = await h.client.get(
        PREFIX + "/session", headers={"X-Tenant-Id": "foreign", "X-Actor-Id": "admin"}
    )
    assert response.status_code == 200
    assert response.json()["principal_ref"] == "human-internal-1"
    assert (await h.client.get(PREFIX + "/session?tenant=foreign")).status_code == 401


@pytest.mark.parametrize("audience", ["beneficiary", "provider"])
async def test_external_identity_requires_binding_and_scopes_subject(h: Harness, audience: str) -> None:
    with pytest.raises(ValidationError):
        membership(audience=audience)
    record = membership(
        audience=audience, subject_bindings=(SubjectBinding(kind=audience, resource_ref="own"),)
    )
    h.store.memberships[(ISSUER, SUBJECT)] = record
    assert (await h.login()).status_code == 303
    resolver = h.app.state.human_session_resolver
    resolved = await resolver.resolve(h.client.cookies[SESSION_COOKIE])
    resolver.require_subject(resolved, audience, "own")
    with pytest.raises(AuthenticationError):
        resolver.require_subject(resolved, audience, "other-person")
    assert (await h.client.get(PREFIX + "/session")).json()["audience"] == audience


async def test_expired_session_and_transaction_fail_closed(h: Harness) -> None:
    params = await h.start()
    key, tx = next(iter(h.store._transactions.items()))
    h.store._transactions[key] = tx.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    assert (await h.finish(params)).status_code == 401
    assert h.idp.exchanges == 0
    assert (await h.login("new-code")).status_code == 303
    key, session = next(iter(h.store._sessions.items()))
    h.store._sessions[key] = session.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    assert (await h.client.get(PREFIX + "/session")).status_code == 401
    await h.store.purge_expired(datetime.now(UTC))
    assert not h.store._sessions
    assert not h.store._transactions


async def test_no_refresh_or_engine_mutation_routes(h: Harness) -> None:
    for suffix in ("/session/refresh", "/tasks/1/decisions", "/tasks/1/claim", "/engine/task/1/complete"):
        assert (await h.client.post(PREFIX + suffix)).status_code == 404
    assert (await h.client.get(PREFIX + "/auth/logout")).status_code == 405
    assert (
        await h.client.get(PREFIX + "/session", headers={"Authorization": "Bearer machine-token"})
    ).status_code == 401


async def test_duplicate_query_cookie_and_origin_refused(h: Harness) -> None:
    params = await h.start()
    state = params["state"][0]
    assert (
        await h.client.get(
            PREFIX
            + "/auth/callback?"
            + urlencode([("state", state), ("state", state), ("code", "TEST_CODE_CANARY")])
        )
    ).status_code == 401
    assert h.idp.exchanges == 0
    assert (await h.finish(params)).status_code == 303
    secret = h.client.cookies[SESSION_COOKIE]
    assert (
        await h.client.get(
            PREFIX + "/session", headers={"Cookie": f"{SESSION_COOKIE}={secret}; {SESSION_COOKIE}={secret}"}
        )
    ).status_code == 401
    assert (
        await h.client.post(
            PREFIX + "/auth/logout", headers=[("Origin", ORIGIN), ("Origin", ORIGIN), ("X-CSRF-Token", "x")]
        )
    ).status_code == 401


async def test_privacy_boundary_strips_server_access_log_query() -> None:
    messages = []
    observed = []
    scope = {"type": "http", "query_string": b"code=CODE_CANARY&state=STATE_CANARY"}

    async def app(private_scope, receive, send):
        observed.append(private_scope["query_string"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    await PrivacyBoundary(app)(scope, receive, send)
    assert observed == [b"code=CODE_CANARY&state=STATE_CANARY"]
    assert scope["query_string"] == b""
    assert (b"referrer-policy", b"no-referrer") in messages[0]["headers"]


async def test_dependency_exception_never_exports_secret(h: Harness, monkeypatch, caplog) -> None:
    canary = "PHI_CODE_TOKEN_DATABASE_PASSWORD_CANARY"

    async def broken(*args):
        raise RuntimeError(canary)

    monkeypatch.setattr(h.store, "put_transaction", broken)
    with caplog.at_level(logging.WARNING, logger="maezo.portal.security"):
        response = await h.client.get(PREFIX + "/auth/login")
    assert response.status_code == 503
    assert canary not in response.text
    assert canary not in caplog.text
    assert "portal_dependency_failure" in caplog.text
    assert response.headers["cache-control"] == "no-store"


async def test_tokens_never_persist_or_project(h: Harness, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        callback = await h.login()
        response = await h.client.get(PREFIX + "/session")
    visible = callback.text + repr(dict(callback.headers)) + response.text + caplog.text
    persisted = repr(h.store._sessions) + "".join(s.model_dump_json() for s in h.store._sessions.values())
    for secret in (h.idp.last_token, "ACCESS_CANARY_PRIVATE", "REFRESH_CANARY_PRIVATE", "TEST_CODE_CANARY"):
        assert secret not in visible
        assert secret not in persisted
    assert digest("TEST_CODE_CANARY") in h.store._codes


@pytest.mark.parametrize(
    "updates",
    [
        {"issuer": "https://attacker.test/pool"},
        {"issuer": ISSUER + "?redirect=bad"},
        {"issuer": ISSUER.replace("/sa-east-1_", "/us-east-1_")},
        {"cognito_origin": "http://localhost"},
        {"cognito_origin": IDP + "/path"},
        {"cognito_origin": "https://user:password@idp.test"},
        {"public_origin": ORIGIN + "#"},
        {"public_origin": ORIGIN + "/"},
        {"public_origin": "https://portal.test\\evil"},
        {"client_id": "machine123"},
        {"client_purpose": "client-credentials"},
        {"mode": "production"},
    ],
)
async def test_deployment_config_fails_closed(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        config(**updates)


async def test_production_rejects_memory_and_transport_overrides(h: Harness) -> None:
    production = config(mode="production", database_url="postgresql+asyncpg://placeholder@db.test/portal")
    with pytest.raises(ValueError, match="overrides prohibited"):
        create_app(production, store=h.store)
    with pytest.raises(ValueError, match="overrides prohibited"):
        create_app(production, oidc_client=h.client)
    with pytest.raises(ValueError, match="local test store must be explicit"):
        create_app(config())
    with pytest.raises(ValueError, match="store boundary"):
        create_app(config(), store=LocalTestIdentityStore("other-tenant"))


async def test_host_rejection_still_has_privacy_headers(h: Harness) -> None:
    response = await h.client.get(PREFIX + "/auth/login", headers={"Host": "attacker.test"})
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert not h.store._transactions


async def test_lifespan_closes_resources(h: Harness) -> None:
    async with h.app.router.lifespan_context(h.app):
        assert (await h.login()).status_code == 303
    assert not h.store._sessions
    assert not h.store._transactions


async def test_small_rsa_key_refused(h: Harness) -> None:
    h.idp.key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    assert (await h.login()).status_code == 401
    assert not h.store._sessions


async def test_session_csrf_is_not_transferable_between_browsers(h: Harness) -> None:
    assert (await h.login("first-browser")).status_code == 303
    first_cookie = h.client.cookies[SESSION_COOKIE]
    first_csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    h.client.cookies.clear()
    assert (await h.login("second-browser")).status_code == 303
    assert h.client.cookies[SESSION_COOKIE] != first_cookie
    assert (
        await h.client.post(
            PREFIX + "/auth/logout",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": first_csrf,
            },
        )
    ).status_code == 401
    assert len(h.store._sessions) == 2


async def test_membership_review_expiry_caps_session(h: Harness) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=90)
    h.store.memberships[(ISSUER, SUBJECT)] = membership(reviewed_until=deadline)
    assert (await h.login()).status_code == 303
    assert next(iter(h.store._sessions.values())).expires_at == deadline


async def test_oidc_token_expiry_caps_session(h: Harness) -> None:
    expiry = int(datetime.now(UTC).timestamp()) + 120
    h.idp.claim_updates = {"exp": expiry}
    assert (await h.login()).status_code == 303
    assert next(iter(h.store._sessions.values())).expires_at == datetime.fromtimestamp(expiry, UTC)


async def test_malformed_token_and_transport_error_static(h: Harness, caplog) -> None:
    h.idp.token_override = "BEARER_CANARY_INVALID_JWT"
    with caplog.at_level(logging.WARNING):
        response = await h.login()
    assert response.status_code == 401
    assert "BEARER_CANARY" not in response.text + caplog.text


async def test_entrypoint_disables_access_and_proxy_header_trust(monkeypatch) -> None:
    from maezo.portal.api.__main__ import main

    calls = []
    monkeypatch.setattr("maezo.portal.api.__main__.uvicorn.run", lambda *a, **kw: calls.append((a, kw)))
    main()
    assert len(calls) == 1
    assert calls[0][0] == ("maezo.portal.api.app:create_app",)
    assert calls[0][1]["access_log"] is False
    assert calls[0][1]["proxy_headers"] is False
    assert calls[0][1]["factory"] is True


async def test_factory_configuration_exception_redacts_values(monkeypatch) -> None:
    monkeypatch.setenv("MAEZO_PORTAL_ISSUER", "SECRET_CANARY_INVALID_ISSUER")
    with pytest.raises(ValueError) as failure:
        create_app()
    assert str(failure.value) == "Configuração do portal indisponível."
    assert failure.value.__suppress_context__ is True


async def test_lifespan_failure_suppresses_backend_exception(h: Harness, monkeypatch) -> None:
    async def broken(*args):
        raise RuntimeError("PASSWORD_CANARY_DATABASE")

    monkeypatch.setattr(h.store, "purge_expired", broken)
    with pytest.raises(RuntimeError) as failure:
        async with h.app.router.lifespan_context(h.app):
            pytest.fail("unavailable store cannot start BFF")
    assert str(failure.value) == "Persistência de identidade indisponível."
    assert failure.value.__suppress_context__ is True
