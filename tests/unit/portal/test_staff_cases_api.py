"""Audience dispatch for the exact staff case API; all providers are synthetic."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.unit.portal.test_human_session import (
    ISSUER,
    PREFIX,
    SUBJECT,
    Harness,
    config,
    h,
    membership,
)

from maezo.gateway.staff_cases.models import StaffCaseError
from maezo.gateway.staff_cases.service import StaffCaseService
from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import SubjectBinding

__all__ = ["h"]

CASE_REF = "case_staff_abcdefghijklmnop"


def projection(outcome: dict[str, object] | None = None) -> bytes:
    return json.dumps(
        {
            "schema": "portal-staff-case-detail.v1",
            "case": {
                "case_ref": CASE_REF,
                "kind": "authorization",
                "state": "active" if outcome is None else "ended",
                "record_revision": "7",
                "state_observed_at": "2099-09-10T12:00:00.000000Z",
            },
            "identity": {
                "upstream_resource_key": "guide-opaque",
                "case_ref": CASE_REF,
                "process_instance_ref": "instance-opaque",
                "process_definition_id": "definition-opaque",
                "process_definition_key": "SP-OP-AUTH-001",
                "process_definition_version": "3",
                "process_definition_digest": "a" * 64,
                "kind": "authorization",
            },
            "active_tasks": [],
            "next_task_cursor": None,
            "tasks_complete": True,
            "freshness": {
                "observed_at": "2099-09-10T12:00:00.000000Z",
                "source_observed_at": "2099-09-10T11:59:59.000000Z",
                "valid_until": "2099-09-10T12:00:10.000000Z",
                "refresh_after_seconds": 10,
            },
            "outcome": outcome,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def page() -> bytes:
    return json.dumps(
        {
            "schema": "portal-staff-case-page.v1",
            "items": [
                {
                    "case_ref": CASE_REF,
                    "kind": "authorization",
                    "state": "active",
                    "record_revision": "7",
                    "state_observed_at": "2099-09-10T12:00:00.000000Z",
                }
            ],
            "next_cursor": "cursor-opaque",
            "freshness": {
                "observed_at": "2099-09-10T12:00:00.000000Z",
                "source_observed_at": "2099-09-10T11:59:59.000000Z",
                "valid_until": "2099-09-10T12:00:10.000000Z",
                "refresh_after_seconds": 10,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class StubStaffService(StaffCaseService):
    def __init__(self, resolver, result: bytes | None = None, error: str | None = None):
        self.resolver = resolver
        self.result = result or projection()
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def read(
        self,
        secret: str,
        *,
        case_ref: str | None = None,
        task_limit: str = "25",
        task_cursor: str | None = None,
    ) -> bytes:
        self.calls.append(
            {
                "secret": secret,
                "case_ref": case_ref,
                "task_limit": task_limit,
                "task_cursor": task_cursor,
            }
        )
        if self.error is not None:
            raise StaffCaseError(self.error)  # type: ignore[arg-type]
        return self.result

    async def list(
        self,
        secret: str,
        *,
        kind: str = "authorization",
        limit: str = "25",
        cursor: str | None = None,
    ) -> bytes:
        self.calls.append({"secret": secret, "kind": kind, "limit": limit, "cursor": cursor})
        if self.error is not None:
            raise StaffCaseError(self.error)  # type: ignore[arg-type]
        return page()


def denying_real_service(resolver) -> tuple[StaffCaseService, AsyncMock]:
    scope = SimpleNamespace(tenant=resolver.settings.tenant)
    observed = AsyncMock()
    service = StaffCaseService(
        resolver,
        SimpleNamespace(tenant=resolver.settings.tenant, issuer=resolver.settings.issuer),
        SimpleNamespace(source=SimpleNamespace(scope=scope), observe=observed),
        SimpleNamespace(
            authority=SimpleNamespace(designation=SimpleNamespace(scope=scope)),
            signer=SimpleNamespace(role="read_requester"),
        ),
    )
    return service, observed


@pytest.mark.asyncio
async def test_staff_detail_uses_only_staff_service_and_closed_query(h: Harness):
    await h.login()
    services: list[StubStaffService] = []

    def factory(resolver):
        service = StubStaffService(resolver)
        services.append(service)
        return service

    h.app.state.staff_case_service_factory = factory
    h.app.state.case_service_factory = lambda resolver: pytest.fail("external service selected")
    response = await h.client.get(PREFIX + "/cases/" + CASE_REF + "?task_limit=25")
    assert response.status_code == 200
    assert response.content == projection()
    assert response.json()["freshness"]["refresh_after_seconds"] == 10
    assert services[0].calls == [
        {
            "secret": h.client.cookies["__Host-maezo-session"],
            "case_ref": CASE_REF,
            "task_limit": "25",
            "task_cursor": None,
        }
    ]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_staff_detail_exposes_the_closed_outcome_of_an_ended_case(h: Harness):
    await h.login()
    outcome = {"phase": "decisao_executada", "desfecho": "aprovada_auditor", "authorization_ref": "d" * 64}
    h.app.state.staff_case_service_factory = lambda resolver: StubStaffService(
        resolver, result=projection(outcome)
    )
    h.app.state.case_service_factory = lambda resolver: pytest.fail("external service selected")
    response = await h.client.get(PREFIX + "/cases/" + CASE_REF + "?task_limit=25")
    assert response.status_code == 200
    body = response.json()
    assert body["case"]["state"] == "ended" and body["outcome"] == outcome
    # O composto `AUTH-{tenant}-{numero_guia_tiss}-{uuid8}` (`tools/workers/auth.py:1329`)
    # fica no recibo de comando: o bloco de desfecho so carrega a referencia opaca.
    assert "AUTH-" not in json.dumps(body["outcome"])
    assert len(body["outcome"]["authorization_ref"]) == 64


@pytest.mark.asyncio
async def test_staff_list_is_authorized_paginated_and_never_falls_into_external_service(h: Harness):
    await h.login()
    service = StubStaffService(h.app.state.human_session_resolver)
    h.app.state.staff_case_service_factory = lambda resolver: service
    h.app.state.case_service_factory = lambda resolver: pytest.fail("external service selected")
    response = await h.client.get(PREFIX + "/cases?kind=authorization&limit=25&cursor=cursor-opaque")
    invalid = await h.client.get(PREFIX + "/cases/" + CASE_REF + "?kind=authorization")
    assert response.status_code == 200
    assert response.content == page()
    assert service.calls == [
        {
            "secret": h.client.cookies["__Host-maezo-session"],
            "kind": "authorization",
            "limit": "25",
            "cursor": "cursor-opaque",
        }
    ]
    assert invalid.status_code == 400
    assert invalid.json() == {"code": "invalid_request"}


@pytest.mark.asyncio
async def test_staff_list_rejects_external_kind_and_maps_scope_denial_without_case_disclosure(h: Harness):
    await h.login()
    service = StubStaffService(h.app.state.human_session_resolver, error="denied")
    h.app.state.staff_case_service_factory = lambda resolver: service
    h.app.state.case_service_factory = lambda resolver: pytest.fail("external service selected")
    invalid = await h.client.get(PREFIX + "/cases?kind=reimbursement")
    denied = await h.client.get(PREFIX + "/cases")
    assert invalid.status_code == 400
    assert invalid.json() == {"code": "invalid_request"}
    assert denied.status_code == 403
    assert denied.json() == {"code": "operation_forbidden"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        ("invalid", 400, "invalid_request"),
        ("denied", 404, "resource_unavailable"),
        ("conflict", 409, "conflict"),
        ("unavailable", 503, "dependency_unavailable"),
    ],
)
async def test_staff_errors_keep_closed_status_and_case_opacity(
    h: Harness, error: str, status: int, code: str
):
    await h.login()
    h.app.state.staff_case_service_factory = lambda actual: StubStaffService(actual, error=error)
    h.app.state.case_service_factory = lambda actual: pytest.fail("audience fallback occurred")
    response = await h.client.get(PREFIX + "/cases/" + CASE_REF)
    assert response.status_code == status
    assert response.json() == {"code": code}
    assert CASE_REF not in response.text


@pytest.mark.asyncio
async def test_audience_change_between_resolutions_refuses_without_external_fallback(h: Harness):
    await h.login()
    resolver = h.app.state.human_session_resolver
    original_resolve = resolver.resolve
    calls = 0

    async def changing_resolve(secret):
        nonlocal calls
        calls += 1
        if calls == 2:
            h.store.memberships[(ISSUER, SUBJECT)] = membership(
                audience="provider",
                subject_bindings=(SubjectBinding(kind="provider", resource_ref="provider-opaque"),),
            )
        return await original_resolve(secret)

    resolver.resolve = changing_resolve
    service, observed = denying_real_service(resolver)
    staff_calls = 0

    def staff_factory(actual):
        nonlocal staff_calls
        staff_calls += 1
        assert actual is resolver
        return service

    h.app.state.staff_case_service_factory = staff_factory
    h.app.state.case_service_factory = lambda actual: pytest.fail("audience fallback occurred")
    response = await h.client.get(PREFIX + "/cases/" + CASE_REF)
    assert response.status_code == 404
    assert response.json() == {"code": "resource_unavailable"}
    assert calls == 2 and staff_calls == 1
    observed.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_revision_change_between_resolutions_invalidates_session(h: Harness):
    await h.login()
    resolver = h.app.state.human_session_resolver
    original_resolve = resolver.resolve
    calls = 0

    async def changing_resolve(secret):
        nonlocal calls
        calls += 1
        if calls == 2:
            h.store.memberships[(ISSUER, SUBJECT)] = membership(revision=2)
        return await original_resolve(secret)

    resolver.resolve = changing_resolve
    service, observed = denying_real_service(resolver)
    h.app.state.staff_case_service_factory = lambda actual: service
    h.app.state.case_service_factory = lambda actual: pytest.fail("audience fallback occurred")
    response = await h.client.get(PREFIX + "/cases/" + CASE_REF)
    assert response.status_code == 401
    assert response.json() == {"code": "authentication_unavailable"}
    observed.assert_not_awaited()


@pytest.mark.asyncio
async def test_app_closes_only_the_injected_staff_runtime():
    settings = config()

    class Runtime:
        sessions = SimpleNamespace(tenant=settings.tenant, issuer=settings.issuer)

        def __init__(self):
            self.closed = False

        def service(self, resolver):
            return StubStaffService(resolver)

        async def close(self):
            self.closed = True

    runtime = Runtime()
    app = create_app(
        settings,
        store=LocalTestIdentityStore(settings.tenant),
        staff_case_runtime=runtime,  # type: ignore[arg-type] -- explicit local lifecycle double
    )
    assert app.state.staff_case_service_factory.__self__ is runtime
    async with app.router.lifespan_context(app):
        assert runtime.closed is False
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_staff_query_parameters_reach_the_actual_openapi_contract(h: Harness):
    operation = h.app.openapi()["paths"][PREFIX + "/cases/{case_ref}"]["get"]
    query = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    assert set(query) == {"task_limit", "task_cursor"}
    assert query["task_limit"]["schema"]["default"] == "25"
    for parameter in query.values():
        assert parameter["required"] is False
        assert parameter["schema"]["type"] == "string"
        assert "Staff only" in parameter["description"]
    assert "503" in query["task_cursor"]["description"]
