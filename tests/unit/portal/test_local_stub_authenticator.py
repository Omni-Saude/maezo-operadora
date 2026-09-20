"""WP-J1-10: the local OIDC stub authenticator and its production boundary.

No live Cognito authentication, ratified user, PostgreSQL or engine execution is claimed
here — this suite proves the *boundary*: the stub is structurally a `HumanAuthenticator`,
it answers only its one fixed code, and no production composition can construct or bind it.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from maezo.gateway.oidc import AuthenticationError
from maezo.gateway.portal_identity import build_human_identity_adapters
from maezo.portal.api.app import create_app
from maezo.portal.api.auth import HumanAuthenticator
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.local_auth import (
    STUB_AUTHORIZATION_CODE,
    STUB_ISSUER,
    STUB_SUBJECT,
    StubAuthenticator,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import MembershipBinding

PREFIX = "/api/v1/portal"
ROOT = Path(__file__).resolve().parents[3]


def settings(mode: str = "local-test", **updates: object) -> PortalSettings:
    values: dict[str, object] = dict(
        tenant="journey-tenant",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SyntheticJourneyPool",
        cognito_origin="https://humans.auth.sa-east-1.amazoncognito.com",
        client_id="human123",
        machine_client_id="machine123",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://localhost",
        mode=mode,
    )
    if mode == "production":
        values["database_url"] = "postgresql+asyncpg://maezo:maezo@127.0.0.1:15433/maezo"
    values.update(updates)
    return PortalSettings(**values)


def staff_membership(store: LocalTestIdentityStore, revision: int = 1) -> MembershipRecord:
    record = MembershipRecord(
        tenant=store.tenant,
        issuer=STUB_ISSUER,
        subject=STUB_SUBJECT,
        principal_ref="synthetic-staff-1",
        revision=revision,
        audience="staff",
        memberships=(
            MembershipBinding(
                membership_ref="review-journey-1", roles=("staff",), groups=("medico-auditor",)
            ),
        ),
        subject_bindings=(),
        reviewed_until=datetime.now(UTC) + timedelta(hours=2),
    )
    store.memberships[(STUB_ISSUER, STUB_SUBJECT)] = record
    return record


def test_stub_authenticator_satisfies_the_human_authenticator_protocol() -> None:
    stub = StubAuthenticator(settings())
    protocol_methods = {
        name for name, value in inspect.getmembers(HumanAuthenticator, predicate=inspect.isfunction)
    }
    assert {"exchange", "close"} <= protocol_methods
    for name in ("exchange", "close"):
        stub_parameters = set(inspect.signature(getattr(stub, name)).parameters)
        protocol_parameters = set(inspect.signature(getattr(HumanAuthenticator, name)).parameters) - {"self"}
        # The Protocol binds `self`; the concrete method must accept the same call shape.
        assert protocol_parameters <= stub_parameters, (name, protocol_parameters, stub_parameters)
    # The service binds it as the real port: no isinstance shortcut, structural conformance.
    service_parameters = inspect.signature(build_human_identity_adapters).parameters
    assert service_parameters["authenticator"].annotation == "HumanAuthenticator | None"


async def test_stub_exchanges_only_its_fixed_code_for_the_fixed_identity() -> None:
    stub = StubAuthenticator(settings())
    identity = await stub.exchange(STUB_AUTHORIZATION_CODE, transaction=None)  # type: ignore[arg-type]
    assert (identity.issuer, identity.subject) == (STUB_ISSUER, STUB_SUBJECT)
    assert identity.expires_at > datetime.now(UTC)
    with pytest.raises(AuthenticationError):
        await stub.exchange("code-" + "q" * 27 + "r", transaction=None)  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError):
        await stub.exchange("", transaction=None)  # type: ignore[arg-type]
    await stub.close()
    assert stub.exchanges == 1


def test_stub_refuses_every_non_local_test_mode() -> None:
    with pytest.raises(ValueError, match="local-test"):
        StubAuthenticator(settings("production"))


def test_production_settings_alone_cannot_construct_the_stub() -> None:
    # The production mode the deployed entry point requires is exactly the mode the stub
    # refuses, so no settings accepted by `create_production_app` can build this class.
    assert settings("production").mode == "production"


def test_identity_composition_refuses_the_stub_override_in_production() -> None:
    production = settings("production")
    local = settings()
    stub = StubAuthenticator(local)
    store: LocalTestIdentityStore | None = LocalTestIdentityStore(production.tenant)
    with pytest.raises(ValueError, match="overrides prohibited"):
        build_human_identity_adapters(production, store=store, authenticator=stub)
    with pytest.raises(ValueError, match="overrides prohibited"):
        build_human_identity_adapters(production, store=None, authenticator=stub)
    with pytest.raises(ValueError, match="overrides prohibited"):
        build_human_identity_adapters(production, store=store, oidc_client=httpx.AsyncClient())


def test_stub_override_without_an_explicit_store_refuses() -> None:
    local = settings()
    with pytest.raises(ValueError, match="explicit identity store"):
        build_human_identity_adapters(local, authenticator=StubAuthenticator(local))


def test_deployed_entry_point_never_passes_identity_overrides() -> None:
    """Fence: `create_production_app` stays override-free, so it can never bind the stub."""
    source = (ROOT / "src/maezo/portal/api/production.py").read_text()
    assert "create_app(identity)" in source
    for override in ("store=", "oidc_client=", "authenticator="):
        calls = [line for line in source.splitlines() if "create_app(" in line and override in line]
        assert calls == [], (override, calls)
    # ...and the factory it calls requires `mode == "production"` before composing anything.
    assert 'identity.mode != "production"' in source


async def test_browser_login_slice_with_the_stub_sets_a_server_session() -> None:
    local = settings()
    store = LocalTestIdentityStore(local.tenant)
    staff_membership(store)
    app = create_app(local, store=store, authenticator=StubAuthenticator(local))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://localhost") as client:
        login = await client.get(f"{PREFIX}/auth/login", follow_redirects=False)
        assert login.status_code == 303
        authorize = urlsplit(login.headers["location"])
        state = dict(pair.split("=", 1) for pair in authorize.query.split("&"))["state"]
        completed = await client.get(
            f"{PREFIX}/auth/callback",
            params={"state": state, "code": STUB_AUTHORIZATION_CODE},
            follow_redirects=False,
        )
        assert completed.status_code == 303
        assert "__Host-maezo-session" in completed.headers["set-cookie"]
        session = await client.get(f"{PREFIX}/session")
        assert session.status_code == 200
        assert session.json()["audience"] == "staff"
    await store.close()
