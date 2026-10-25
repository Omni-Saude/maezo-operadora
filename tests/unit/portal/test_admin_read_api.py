"""D-40 adversarial tests: the administrative READ boundary is read-only, dark, and fenced.

No live Cognito, PostgreSQL or engine execution is claimed here. Sessions are seeded directly
in the local/test store (session semantics are proven in `test_human_session.py`); what this
suite proves is the NEW surface only: the roster route answers GET and nothing else, refuses
before any effect while dark, never imports `maezo.portal.admin`, and projects exactly the
reviewed facts — never the identity coordinates.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from maezo.gateway.staff_cases.production_config import PortalProductionSettings
from maezo.portal.api.admin_read import (
    ROUTE_PATH,
    AdminReadServiceFactory,
    StaffAdminReadService,
    admin_read_router,
)
from maezo.portal.api.app import create_app
from maezo.portal.api.auth import digest
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.store import IdentityStore, LocalTestIdentityStore, RosterUnavailableError
from maezo.portal.contracts.admin_read import PortalAdminReadError, StaffAdminVinculos
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ROOT = Path(__file__).parents[3]
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
ORIGIN = "https://portal.example.test"
SESSION_COOKIE = "__Host-maezo-session"
ADMIN_PROFILE = "identity,staff_cases,human,staff_admin_read"
pytestmark = pytest.mark.asyncio

_NOW = datetime.now(UTC)


def config(**updates: object) -> PortalSettings:
    values: dict[str, object] = dict(
        tenant="test-tenant",
        issuer=ISSUER,
        cognito_origin="https://humans.auth.sa-east-1.amazoncognito.com",
        client_id="human123",
        machine_client_id="machine123",
        client_purpose="dedicated-human-code-pkce",
        public_origin=ORIGIN,
        mode="local-test",
    )
    values.update(updates)
    return PortalSettings(**values)


def staff_membership(**updates: object) -> MembershipRecord:
    values: dict[str, object] = dict(
        tenant="test-tenant",
        issuer=ISSUER,
        subject="00000000-0000-4000-8000-000000000001",
        principal_ref="human-internal-1",
        revision=3,
        audience="staff",
        memberships=(
            MembershipBinding(membership_ref="review-1", roles=("staff",), groups=("medico-auditor",)),
        ),
        subject_bindings=(),
        reviewed_until=_NOW + timedelta(hours=2),
    )
    values.update(updates)
    return MembershipRecord(**values)


def other_membership(subject_tail: int, principal: str, audience: str, **updates: object) -> MembershipRecord:
    return staff_membership(
        subject=f"00000000-0000-4000-8000-{subject_tail:012d}",
        principal_ref=principal,
        audience=audience,
        subject_bindings=(
            ()
            if audience == "staff"
            else (SubjectBinding(kind=audience, resource_ref=f"resource-{principal}"),)
        ),
        **updates,
    )


async def seed_session(store: LocalTestIdentityStore, membership: MembershipRecord) -> str:
    """The caller's own session: an ACTIVE staff membership, the only one that may resolve."""
    secret = "a" * 43
    await store.put_session(
        SessionRecord(
            secret_hash=digest(secret),
            session_ref="session-1",
            csrf_token="csrf-token",
            issuer=membership.issuer,
            subject=membership.subject,
            principal_ref=membership.principal_ref,
            membership_revision=membership.revision,
            authenticated_at=_NOW - timedelta(minutes=5),
            expires_at=_NOW + timedelta(minutes=30),
        ),
        None,
    )
    store.memberships[(membership.issuer, membership.subject)] = membership
    return secret


def build(
    store: IdentityStore,
    *,
    factory: AdminReadServiceFactory | None,
    **config_updates: object,
) -> httpx.AsyncClient:
    app = create_app(
        config(**config_updates),
        store=store,
        admin_read_service_factory=factory,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


def factory() -> AdminReadServiceFactory:
    return lambda resolver: StaffAdminReadService(resolver)


class Harness:
    def __init__(self, client: httpx.AsyncClient, store: LocalTestIdentityStore) -> None:
        self.client, self.store = client, store

    async def get(self, secret: str) -> httpx.Response:
        return await self.client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})


@pytest.fixture
async def seeded():
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=factory(), staff_admin_panel=True, capabilities=ADMIN_PROFILE) as client:
        yield Harness(client, store), secret


async def test_dark_by_default_refuses_before_any_effect() -> None:
    """No flag, no profile, no factory: the shipped state answers its own 503 and reads nothing."""
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=None) as client:
        response = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})
    assert response.status_code == 503
    assert response.json() == {"code": "admin_read_unavailable"}


async def test_flag_without_the_profile_refuses() -> None:
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=factory(), staff_admin_panel=True) as client:
        response = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})
    assert response.status_code == 403
    assert response.json() == {"code": "operation_forbidden"}


async def test_profile_without_the_flag_refuses() -> None:
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=factory(), capabilities=ADMIN_PROFILE) as client:
        response = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})
    assert response.status_code == 403
    assert response.json() == {"code": "operation_forbidden"}


async def test_flag_and_profile_without_a_bound_projection_refuse() -> None:
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=None, staff_admin_panel=True, capabilities=ADMIN_PROFILE) as client:
        response = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})
    assert response.status_code == 503
    assert response.json() == {"code": "admin_read_unavailable"}


async def test_unknown_session_refuses() -> None:
    store = LocalTestIdentityStore("test-tenant")
    async with build(store, factory=factory(), staff_admin_panel=True, capabilities=ADMIN_PROFILE) as client:
        assert (await client.get(ROUTE_PATH)).status_code == 401
        gone = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={'b' * 43}"})
    assert gone.status_code == 401


async def test_query_string_and_body_are_refused_as_invalid_requests(seeded) -> None:
    harness, secret = seeded
    query = await harness.client.get(
        ROUTE_PATH, params={"principal": "human-internal-1"}, headers={"Cookie": f"{SESSION_COOKIE}={secret}"}
    )
    assert query.status_code == 400
    assert query.json() == {"code": "invalid_request"}
    body = await harness.client.request(
        "GET", ROUTE_PATH, content=b"{}", headers={"Cookie": f"{SESSION_COOKIE}={secret}"}
    )
    assert body.status_code == 400
    assert body.json() == {"code": "invalid_request"}


async def test_route_is_get_only() -> None:
    """The read-only proof at the HTTP surface: every other method is not even routed."""
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(store, factory=factory(), staff_admin_panel=True, capabilities=ADMIN_PROFILE) as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = await client.request(
                method, ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"}
            )
            assert response.status_code == 405, method


async def test_roster_projects_reviewed_facts_only(seeded) -> None:
    harness, secret = seeded
    harness.store.memberships[(ISSUER, other_membership(2, "human-internal-2", "staff").subject)] = (
        other_membership(2, "human-internal-2", "staff", revision=7)
    )
    harness.store.memberships[(ISSUER, other_membership(3, "human-internal-3", "staff").subject)] = (
        other_membership(3, "human-internal-3", "staff", revoked=True, revision=1)
    )
    harness.store.memberships[(ISSUER, other_membership(4, "human-internal-4", "staff").subject)] = (
        other_membership(4, "human-internal-4", "staff", reviewed_until=_NOW - timedelta(hours=1))
    )
    harness.store.memberships[(ISSUER, other_membership(5, "vendor-1", "vendor").subject)] = other_membership(
        5, "vendor-1", "vendor"
    )
    harness.store.memberships[(ISSUER, other_membership(6, "beneficiary-1", "beneficiary").subject)] = (
        other_membership(6, "beneficiary-1", "beneficiary")
    )

    response = await harness.get(secret)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    # Identity coordinates never leave the server — not even for the rows the read consumed.
    assert ISSUER not in body
    assert "00000000-0000-4000-8000-00000000000" not in body
    parsed = StaffAdminVinculos.model_validate_json(response.content)
    assert parsed.schema_version == 1
    assert parsed.count == 4
    assert [vinculo.principal_ref for vinculo in parsed.vinculos] == [
        "human-internal-1",
        "human-internal-2",
        "human-internal-3",
        "human-internal-4",
    ]
    first, second, third, fourth = parsed.vinculos
    assert first.state == "active"
    assert first.memberships[0].roles == ("staff",)
    assert first.memberships[0].groups == ("medico-auditor",)
    assert second.membership_revision == 7
    assert second.subject_bindings == ()
    assert third.revoked is True and third.state == "revoked"
    assert fourth.state == "expired"
    # The non-staff rows were never projected: not as members, not in the count.
    assert "vendor-1" not in body and "beneficiary-1" not in body


async def test_roster_is_read_live_not_cached(seeded) -> None:
    harness, secret = seeded
    extra = other_membership(7, "human-internal-5", "staff")
    harness.store.memberships[(ISSUER, extra.subject)] = extra
    first = await harness.get(secret)
    assert first.status_code == 200
    assert StaffAdminVinculos.model_validate_json(first.content).count == 2
    # A reviewed change commits a REVOKED replacement for that other principal; the caller's
    # own session survives (theirs is the revoked row — exactly what the panel exists to show).
    harness.store.memberships[(ISSUER, extra.subject)] = other_membership(
        7, "human-internal-5", "staff", revoked=True
    )
    second = await harness.get(secret)
    assert second.status_code == 200
    projected = StaffAdminVinculos.model_validate_json(second.content)
    assert projected.count == 2
    assert projected.vinculos[1].state == "revoked"


class _RosterlessStore:
    """An identity store shape with NO roster read — the AUTH-composed posture in miniature.

    Sessions keep resolving (per-identity reads delegate); the roster read is simply absent,
    which the route must answer as the missing dependency it is — never as an empty roster.
    """

    tenant = "test-tenant"
    persistent = False

    def __init__(self, inner: LocalTestIdentityStore) -> None:
        self._inner = inner

    async def get_session(self, secret_hash: str, now: datetime) -> SessionRecord | None:
        return await self._inner.get_session(secret_hash, now)

    async def get_membership(self, issuer: str, subject: str) -> MembershipRecord | None:
        return await self._inner.get_membership(issuer, subject)


async def test_store_without_a_roster_read_refuses_as_missing_dependency() -> None:
    store = LocalTestIdentityStore("test-tenant")
    secret = await seed_session(store, staff_membership())
    async with build(
        cast(IdentityStore, _RosterlessStore(store)),
        factory=factory(),
        staff_admin_panel=True,
        capabilities=ADMIN_PROFILE,
    ) as client:
        response = await client.get(ROUTE_PATH, headers={"Cookie": f"{SESSION_COOKIE}={secret}"})
    assert response.status_code == 503
    assert response.json() == {"code": "admin_read_unavailable"}


class _QuietEngine:
    """A PostgreSQL-shaped engine whose every USE is a test failure.

    `dialect` must exist (the store constructor checks it); nothing else may ever be
    reached on the roster path — the AUTH-composed refusal must come BEFORE any plain
    engine touch, or the read would use the wrong role and the wrong schema.
    """

    class dialect:  # noqa: N801 - mirrors the sqlalchemy attribute name
        name = "postgresql"

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the plain engine must never be touched on this path: {name}")


async def test_auth_composed_store_cannot_serve_the_roster() -> None:
    """D-* pendência: the AUTH participant exposes no roster read, so the store refuses.

    Once the AUTH lifecycle is installed, membership reads belong to the participant's own
    roles/schema; the plain engine must not be used. The honest answer is the refusal (the
    live-PG proof of the AUTH path is the owner decision's to commission).
    """
    postgres = PostgresIdentityStore("test-tenant", _QuietEngine())
    # The bound state exactly as `bind_auth_lifecycle` leaves it (the composition object is
    # opaque here; its type is proven by that method's own fence in the AUTH suite).
    postgres._auth_composition = object()  # type: ignore[assignment]
    with pytest.raises(RosterUnavailableError):
        await postgres.staff_memberships()


# --------------------------------------------------------------------------- fences


def test_admin_read_module_never_imports_the_admin_fence() -> None:
    """The `portal/admin` fence is structural: the D-40 module must not import it. At all."""
    source = (ROOT / "src/maezo/portal/api/admin_read.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden.extend(
                alias.name for alias in node.names if alias.name.startswith("maezo.portal.admin")
            )
        elif (
            isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("maezo.portal.admin")
        ):
            forbidden.append(node.module)
    assert forbidden == []
    # And the module is honestly ABOUT reading: no HTTP method but GET is ever mounted.
    methods = {
        method.upper() for route in admin_read_router.routes for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}
    assert [route.path for route in admin_read_router.routes] == [ROUTE_PATH]


def test_production_bootstrap_cannot_name_the_admin_read_profile() -> None:
    """The SC1 production bootstrap refuses to boot with the read profile: default is dark."""
    with pytest.raises(Exception) as refusal:
        PortalProductionSettings(
            capabilities=ADMIN_PROFILE,
            tenant="test-tenant",
            issuer=ISSUER,
        )
    assert refusal.value is not None


def test_contract_error_codes_are_closed() -> None:
    valid = {"invalid_request", "authentication_unavailable", "operation_forbidden", "admin_read_unavailable"}
    for code in sorted(valid):
        assert TypeAdapter(PortalAdminReadError).validate_python({"code": code}).code == code
    for bad in ("dependency_unavailable", "resource_unavailable", ""):
        with pytest.raises(ValidationError):
            TypeAdapter(PortalAdminReadError).validate_python({"code": bad})


# ------------------------------------------------------- cerca da flag dark (D-40), in-package


def test_admin_panel_flag_is_dark_in_the_real_tree() -> None:
    """The D-40 panel flag is DARK exactly where it is declared — package-side perimeter.

    `check_program_dark_flags.py` is the program-wide CI fence (CODEOWNERS-gated, extended
    by the owner separately); THIS fence pins the same discipline for the new flag in the
    unit lane: declared exactly once, default literal `False`, under the pinned env prefix
    that gives `MAEZO_PORTAL_STAFF_ADMIN_PANEL` its meaning. A default that is not the
    literal False would open the surface in every deployment that does NOT declare it.
    """
    config_source = (ROOT / "src/maezo/portal/api/config.py").read_text(encoding="utf-8")
    declarations = [
        line.strip()
        for line in config_source.splitlines()
        if line.strip().startswith("staff_admin_panel: bool = ")
    ]
    assert len(declarations) == 1, declarations
    assert declarations[0].endswith("= False"), declarations[0]
    assert PortalSettings.model_config.get("env_prefix") == "MAEZO_PORTAL_"
    field = PortalSettings.model_fields["staff_admin_panel"]
    assert field.default is False


def test_admin_panel_env_name_is_never_mentioned_in_deploy() -> None:
    """No `deploy/**` may name the flag (not even commented): flipping is a ROLL-wave act."""
    deploy = ROOT / "deploy"
    files = [path for path in sorted(deploy.rglob("*")) if path.is_file()]
    assert files, "deploy ausente — varrer nada não é prova de flag OFF"
    problems: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            problems.append(f"{path.relative_to(ROOT)}: ilegível como UTF-8 — recusado por precaução")
            continue
        if "MAEZO_PORTAL_STAFF_ADMIN_PANEL" in text:
            problems.append(f"{path.relative_to(ROOT)}: menção à flag dark em deploy")
    assert problems == []
