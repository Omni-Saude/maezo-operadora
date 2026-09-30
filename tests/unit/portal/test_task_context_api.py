"""INTERIM escalation-context route (DL-0050): the declared gate, the closed request, the error map.

ASGI boundary proofs only. The gateway behind the route is the unit-composed one from
`tests/unit/gateway/human/test_task_completion_gateway.py`, and the engine behind it is a recording
double: what this file proves is the HTTP contract the web client depends on — 501 while the shared
interim gate is off (before any cookie is read), 403/404 by group, `no-store` on every answer, and
that no session secret or upstream narrative ever reaches the body.
"""

from datetime import datetime

import httpx
import pytest
from tests.unit.gateway.human.test_escalation_context import RecordingSource
from tests.unit.gateway.human.test_gateway import CSRF, SECRET
from tests.unit.gateway.human.test_task_completion_gateway import (
    HUMAN_GROUP,
    RecordingCompletion,
    harness,
    staff_in,
)
from tests.unit.portal.test_human_session import ORIGIN, config

from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.escalation_context import EscalationContext
from maezo.portal.api.app import create_app
from maezo.portal.api.tasks import context_router, task_router

pytestmark = pytest.mark.asyncio

PREFIX = "/api/v1/portal/tasks"
PATH = PREFIX + "/task-1/context"
HEADERS = {"cookie": "__Host-maezo-session=" + SECRET}


async def client(*, enabled=True, source=None, member=None, with_source=True):
    g, _, _ = await harness(member=member, completion=RecordingCompletion())
    g.factory_calls = 0
    source = source if source is not None else RecordingSource()
    g._escalation_context = source if with_source else None

    def factory(resolver):
        g.factory_calls += 1
        g._resolver = resolver
        return g

    app = create_app(
        config(direct_completion=enabled), store=g._resolver.store, task_read_service_factory=factory
    )
    connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS)
    return connection, g, source


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"schema": "portal-context-error.v1", "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text and CSRF not in response.text


# ---------------------------------------------------------------------------------------
# The gate. This is the shipped behaviour: it is the SAME gate as the completion.
# ---------------------------------------------------------------------------------------
async def test_the_gate_is_off_by_default_and_the_route_says_so_before_touching_anything():
    g, _, _ = await harness(completion=RecordingCompletion())
    g.factory_calls = 0
    g._escalation_context = RecordingSource()

    def factory(resolver):
        g.factory_calls += 1
        g._resolver = resolver
        return g

    app = create_app(config(), store=g._resolver.store, task_read_service_factory=factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS
    ) as connection:
        response = await connection.get(PATH)
    assert_safe(response, 501, "context_unavailable")
    # No cookie was read, no gateway was built, the engine was not asked.
    assert g.factory_calls == 0 and g._escalation_context.calls == []


async def test_the_gate_refuses_without_a_session_at_all():
    connection, g, source = await client(enabled=False)
    async with connection:
        response = await connection.get(PATH, headers={"cookie": ""})
    assert_safe(response, 501, "context_unavailable")
    assert g.factory_calls == 0 and source.calls == []


# ---------------------------------------------------------------------------------------
# The route, enabled.
# ---------------------------------------------------------------------------------------
async def test_the_context_of_a_readable_task_is_returned_with_the_documented_shape():
    connection, _, source = await client()
    async with connection:
        response = await connection.get(PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "schema": "portal-task-context.v1",
        "task_id": "task-1",
        "etapa": "atendimento",
        "motivo_categoria": "red_flag_clinico",
        "severidade": "grave",
        "prioridade": "P1",
        "grupo_atendimento": "plantao-clinico",
        "aberto_em": "2026-09-28T21:31:32Z",
        "ack_vence_em": "2026-09-28T21:36:33Z",
        "resolucao_vence_em": "2026-09-28T22:01:33Z",
        "resumo_contexto": "Idoso com dor no peito.",
        "observed_at": payload["observed_at"],
    }
    assert datetime.fromisoformat(payload["observed_at"]).tzinfo is not None
    # The summary is classified PHI in this repository: nothing may cache it.
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text and CSRF not in response.text
    assert source.calls == ["task-1"]


async def test_absent_facts_travel_as_null_and_never_as_invented_defaults():
    empty = EscalationContext(
        etapa="supervisao",
        motivo_categoria=None,
        severidade=None,
        prioridade=None,
        grupo_atendimento=None,
        aberto_em=None,
        ack_vence_em=None,
        resolucao_vence_em=None,
        resumo_contexto=None,
    )
    connection, _, _ = await client(source=RecordingSource(context=empty))
    async with connection:
        response = await connection.get(PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["etapa"] == "supervisao"
    for field in (
        "motivo_categoria",
        "severidade",
        "prioridade",
        "grupo_atendimento",
        "aberto_em",
        "ack_vence_em",
        "resolucao_vence_em",
        "resumo_contexto",
    ):
        assert payload[field] is None, field


async def test_the_colleague_of_another_group_gets_a_404_and_the_engine_is_never_asked():
    connection, _, source = await client(member=staff_in(HUMAN_GROUP))
    async with connection:
        response = await connection.get(PATH)
    assert_safe(response, 404, "resource_unavailable")
    assert source.calls == []


async def test_no_session_cookie_is_a_401():
    connection, _, source = await client()
    async with connection:
        response = await connection.get(PATH, headers={"cookie": ""})
    assert_safe(response, 401, "session_unavailable")
    assert source.calls == []


async def test_a_query_string_is_a_bad_request():
    connection, _, source = await client()
    async with connection:
        response = await connection.get(PATH + "?task=other")
    assert_safe(response, 400, "invalid_request")
    assert source.calls == []


async def test_a_malformed_task_reference_is_a_bad_request():
    connection, _, source = await client()
    async with connection:
        response = await connection.get(PREFIX + "/%20/context")
    assert_safe(response, 400, "invalid_request")
    assert source.calls == []


async def test_without_an_installed_source_the_route_is_a_503_not_an_empty_context():
    connection, _, _ = await client(with_source=False)
    async with connection:
        response = await connection.get(PATH)
    assert_safe(response, 503, "read_dependency_unavailable")


async def test_a_dependency_failure_is_a_503_with_no_upstream_narrative():
    boom = RecordingSource(fail=RuntimeError("PRIVATE engine narrative 10.0.0.9"))
    connection, _, _ = await client(source=boom)
    async with connection:
        response = await connection.get(PATH)
    assert_safe(response, 503, "read_dependency_unavailable")
    assert "PRIVATE" not in response.text and "10.0.0.9" not in response.text


async def test_a_proven_absence_from_the_engine_is_a_404():
    gone = RecordingSource(fail=GatewayRefusalError("task_unavailable"))
    connection, _, _ = await client(source=gone)
    async with connection:
        response = await connection.get(PATH)
    assert_safe(response, 404, "resource_unavailable")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_the_route_answers_only_get(method):
    connection, _, source = await client()
    async with connection:
        response = await connection.request(method, PATH)
    assert response.status_code in (404, 405)
    assert source.calls == []


async def test_the_route_lives_on_its_own_router_and_the_read_surface_is_unchanged():
    """The shortcut is deletable whole: the two real read routes must stay two."""
    assert {r.path for r in task_router.routes} == {PREFIX, PREFIX + "/{task_id}"}
    assert {r.path for r in context_router.routes} == {PATH.replace("task-1", "{task_id}")}
    assert {m for r in context_router.routes for m in r.methods} == {"GET"}
