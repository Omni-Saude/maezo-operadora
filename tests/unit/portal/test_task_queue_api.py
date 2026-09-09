"""ASGI read boundary unit proofs, no production identity/engine qualification."""

from datetime import timedelta

import httpx
import pytest
from tests.unit.gateway.human.test_gateway import SECRET
from tests.unit.gateway.human.test_task_queue_gateway import NOW, harness
from tests.unit.portal.test_human_session import ORIGIN, config

from maezo.gateway.human.queue import ReadRefusalError
from maezo.portal.api.app import create_app
from maezo.portal.api.tasks import task_router

pytestmark = pytest.mark.asyncio
PREFIX = "/api/v1/portal/tasks"


async def client(monkeypatch, *, configured=True, ids=("task-1",)):
    g = await harness(monkeypatch, ids=ids)
    g.factory_calls = 0

    def factory(resolver):
        g.factory_calls += 1
        # Unit-only explicit request composition; production supplies a new bundle.
        g._resolver = resolver
        return g

    app = create_app(
        config(), store=g._resolver.store, task_read_service_factory=factory if configured else None
    )
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(
        transport=transport, base_url=ORIGIN, headers={"cookie": "__Host-maezo-session=" + SECRET}
    )
    return c, g, app


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"schema": "portal-read-error.v1", "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert SECRET not in response.text


async def test_real_routes_and_openapi_have_closed_public_models(monkeypatch):
    c, g, app = await client(monkeypatch)
    async with c:
        response = await c.get(PREFIX + "?queue=team")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["task_revision"] == "2"
    assert {r.path for r in task_router.routes} == {PREFIX, PREFIX + "/{task_id}"}
    schema = app.openapi()
    for name in ("TaskQueuePage", "PublicTaskSnapshot", "PortalReadError", "TaskReadResponse"):
        assert schema["components"]["schemas"][name]["additionalProperties"] is False
    assert set(schema["paths"][PREFIX]) == {"get"}
    assert set(schema["paths"][PREFIX + "/{task_id}"]) == {"get"}
    assert "TaskQueuePage" in str(schema["paths"][PREFIX]["get"]["responses"]["200"])


@pytest.mark.parametrize(
    "query",
    [
        "",
        "queue=",
        "queue=TEAM",
        "queue=team&queue=mine",
        "queue=team&tenant=private",
        "queue=team&actor=private",
        "queue=team&assignee=private",
        "queue=team&limit=0",
        "queue=team&limit=01",
        "queue=team&limit=-1",
        "queue=team&limit=101",
        "queue=team&limit=1.0",
        "queue=team&limit=+1",
        "queue=team&limit=9999999999999999",
        "queue=team&cursor=",
        "queue=team&cursor=abc.def",
        "queue=team&cursor=%FF",
        "queue=team&cursor=%",
        "queue=team&cursor=%XX",
        "queue=team&cursor=a%00b",
        "queue=team&cursor=" + "a" * 2049,
        "queue=team&limit=1&limit=2",
        "queue=team&cursor=a&cursor=b",
        "queue=team&filter=${private}",
        "queue=team&orphan",
    ],
)
async def test_query_is_closed_and_rejected_before_service(monkeypatch, query):
    c, g, _ = await client(monkeypatch)
    async with c:
        response = await c.get(PREFIX + "?" + query)
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0


@pytest.mark.parametrize("path", [PREFIX + "?queue=team", PREFIX + "/task-1"])
async def test_get_body_rejected_before_service(monkeypatch, path):
    c, g, _ = await client(monkeypatch)
    async with c:
        response = await c.request("GET", path, content=b'{"private":"secret"}')
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0


@pytest.mark.parametrize(
    "path", [PREFIX + "/task-1?queue=team", PREFIX + "/%20", PREFIX + "/%3F", PREFIX + "/" + "a" * 513]
)
async def test_detail_accepts_only_strict_reference_and_no_query(monkeypatch, path):
    c, g, _ = await client(monkeypatch)
    async with c:
        response = await c.get(path)
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0


@pytest.mark.parametrize("cookie", ["", "__Host-maezo-session=a; __Host-maezo-session=b"])
async def test_missing_or_ambiguous_cookie(monkeypatch, cookie):
    c, g, _ = await client(monkeypatch)
    async with c:
        response = await c.get(PREFIX + "?queue=team", headers={"cookie": cookie})
    assert_safe(response, 401, "session_unavailable")
    assert g.factory_calls == 0


async def test_invalid_host_has_read_error_and_no_service(monkeypatch):
    c, g, _ = await client(monkeypatch)
    async with c:
        response = await c.get(PREFIX + "?queue=team", headers={"host": "attacker.test"})
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0


async def test_missing_adapter_does_not_break_working_session(monkeypatch):
    c, _, _ = await client(monkeypatch, configured=False)
    async with c:
        response = await c.get(PREFIX + "?queue=team")
        session = await c.get("/api/v1/portal/session")
        login = await c.get("/api/v1/portal/auth/login")
    assert_safe(response, 503, "read_dependency_unavailable")
    assert session.status_code == 200
    assert login.status_code == 303


@pytest.mark.parametrize("where", ["query", "catalog", "task", "authority", "classification"])
async def test_dependency_failure_has_safe_body_no_logs_or_empty_page(monkeypatch, caplog, where):
    c, g, _ = await client(monkeypatch)

    async def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE upstream with cookie " + SECRET)

    targets = {
        "query": (g._query, "discover"),
        "catalog": (g._catalog_source, "current_catalog"),
        "task": (g._ports.task, "read_task"),
        "authority": (g._ports.authority, "current_authority"),
        "classification": (g._disclosure_source, "classify"),
    }
    target, method = targets[where]
    monkeypatch.setattr(target, method, fail)
    async with c:
        response = await c.get(PREFIX + ("/task-1" if where == "classification" else "?queue=team"))
    assert_safe(response, 503, "read_dependency_unavailable")
    assert "PRIVATE" not in caplog.text and SECRET not in caplog.text


@pytest.mark.parametrize(
    "code,status",
    [
        ("invalid_request", 400),
        ("refresh_required", 409),
        ("read_dependency_unavailable", 503),
    ],
)
async def test_typed_cursor_refusal_and_no_input_echo(monkeypatch, code, status):
    c, g, _ = await client(monkeypatch)

    async def refuse(*args, **kwargs):
        raise ReadRefusalError(code)

    monkeypatch.setattr(g._cursor_custody, "resolve", refuse)
    async with c:
        response = await c.get(PREFIX + "?queue=team&cursor=PRIVATE")
    assert_safe(response, status, code)
    assert "PRIVATE" not in response.text


async def test_detail_never_offers_mutation_and_keeps_internal_snapshot(monkeypatch):
    c, g, _ = await client(monkeypatch)
    before = g.rows["task-1"].snapshot.model_dump()
    async with c:
        response = await c.get(PREFIX + "/task-1")
        mutation = await c.post(PREFIX + "/task-1")
    assert response.status_code == 200
    assert response.json()["task"]["allowed_actions"] == []
    assert g.rows["task-1"].snapshot.model_dump() == before
    assert mutation.status_code == 405


async def test_expired_discovery_is_not_empty(monkeypatch):
    c, g, _ = await client(monkeypatch, ids=())
    g._query.change = lambda w: w.model_copy(update={"valid_until": NOW - timedelta(seconds=1)})
    async with c:
        response = await c.get(PREFIX + "?queue=team")
    assert_safe(response, 503, "read_dependency_unavailable")


async def test_unknown_error_code_cannot_escape_closed_error_envelope(monkeypatch):
    c, g, _ = await client(monkeypatch)

    async def invalid(*args, **kwargs):
        raise ReadRefusalError("PRIVATE-unknown-code")

    monkeypatch.setattr(g._query, "discover", invalid)
    async with c:
        response = await c.get(PREFIX + "?queue=team")
    assert_safe(response, 503, "read_dependency_unavailable")
