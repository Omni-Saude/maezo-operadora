"""INTERIM completion route (DL-0049): the declared gate, the closed body, CORS, the error map.

ASGI boundary proofs only. No production identity, engine or audit qualification is claimed —
the gateway behind the route is the unit-composed one from
`tests/unit/gateway/human/test_task_completion_gateway.py`, and what this file proves is the
HTTP contract `docs/portal/completion-endpoint.md` hands to the test page: 501 while the flag is
off, 422 for an outcome outside the BPMN's three values, 403 for the wrong group, 409 for a
claimed revision, and an `Access-Control-Allow-*` header that exists on no default deployment.
"""

import httpx
import pytest
from tests.unit.gateway.human.test_gateway import CSRF, SECRET
from tests.unit.gateway.human.test_task_completion_gateway import (
    HUMAN_GROUP,
    NOTES,
    RecordingAudit,
    RecordingCompletion,
    harness,
    staff_in,
)
from tests.unit.portal.test_human_session import ORIGIN, config

from maezo.portal.api.app import create_app
from maezo.portal.api.tasks import completion_router, task_router

pytestmark = pytest.mark.asyncio

PREFIX = "/api/v1/portal/tasks"
PATH = PREFIX + "/task-1/completion"
TEST_CHANNEL = "https://maezo-teste-dev.austa.com.br"
BODY = {"resultado": "resolvido_humano", "notas_resolucao": NOTES}
HEADERS = {
    "cookie": "__Host-maezo-session=" + SECRET,
    "content-type": "application/json",
    "origin": ORIGIN,
    "x-csrf-token": CSRF,
}


async def client(*, enabled=True, cors=(), completion=None, audit=None, member=None, snap=None):
    g, port, sink = await harness(
        member=member,
        completion=completion if completion is not None else RecordingCompletion(),
        audit=audit,
        snap=snap,
    )
    g.factory_calls = 0

    def factory(resolver):
        g.factory_calls += 1
        # Unit-only explicit request composition; production supplies a new bundle.
        g._resolver = resolver
        return g

    settings = config(direct_completion=enabled, cors_origins=",".join(cors))
    app = create_app(settings, store=g._resolver.store, task_read_service_factory=factory)
    connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS)
    return connection, g, app


def assert_safe(response, status, code):
    assert response.status_code == status
    assert response.json() == {"schema": "portal-completion-error.v1", "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert SECRET not in response.text and CSRF not in response.text


# ---------------------------------------------------------------------------------------
# The gate. This is the shipped behaviour.
# ---------------------------------------------------------------------------------------
async def test_the_flag_is_off_by_default_and_the_route_says_so_before_touching_anything():
    """No `direct_completion` in the settings at all: the default must be OFF."""
    g, _, _ = await harness(completion=RecordingCompletion())
    g.factory_calls = 0

    def factory(resolver):
        g.factory_calls += 1
        g._resolver = resolver
        return g

    app = create_app(config(), store=g._resolver.store, task_read_service_factory=factory)
    connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 501, "completion_unavailable")
    # No cookie was read, no gateway was built, no engine was called.
    assert g.factory_calls == 0
    assert g._ports.task.completion.calls == []


async def test_the_gate_refuses_without_a_session_at_all():
    connection, g, _ = await client(enabled=False)
    async with connection:
        response = await connection.post(PATH, json=BODY, headers={"cookie": ""})
    assert_safe(response, 501, "completion_unavailable")
    assert g.factory_calls == 0


# ---------------------------------------------------------------------------------------
# The route, enabled.
# ---------------------------------------------------------------------------------------
async def test_a_completion_returns_the_final_state_and_both_audit_refs():
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "schema": "portal-task-completion.v1",
        "task_id": "task-1",
        "process_definition_key": "SP-OP-ESCALATION-001",
        "process_definition_version": "1",
        "task_definition_key": "UT_TratarEscalonamento",
        "form_key": "escalation",
        "state": "completed",
        "resultado": "resolvido_humano",
        "consumed_task_revision": "2",
        "authority_revision": "7",
        "evidence_revision": "3",
        "evidence_digest": "e" * 64,
        "completed_at": payload["completed_at"],
        "audit_intent_ref": "a" * 64,
        "audit_result_ref": "b" * 64,
    }
    # Revisions travel as canonical decimal STRINGS, the repo's D5 convention.
    assert all(isinstance(payload[k], str) for k in ("consumed_task_revision", "authority_revision"))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    # The note the colleague typed never comes back, and neither do the session secrets.
    assert NOTES not in response.text
    assert SECRET not in response.text and CSRF not in response.text
    assert len(completion.calls) == 1


async def test_the_colleague_of_another_group_is_refused_with_403():
    completion = RecordingCompletion()
    connection, _, _ = await client(member=staff_in(HUMAN_GROUP), completion=completion)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 403, "employee_access_required")
    assert completion.calls == []


async def test_a_claimed_revision_is_refused_with_409():
    completion = RecordingCompletion()
    audit = RecordingAudit(dedup={"portal-direct-completion:intent:task-1:2"})
    connection, _, _ = await client(completion=completion, audit=audit)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 409, "revision_conflict")
    assert completion.calls == []


async def test_an_unreadable_task_is_refused_with_404(monkeypatch):
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)

    async def refuse(task_id):
        raise RuntimeError("PRIVATE upstream narrative")

    monkeypatch.setattr(g._ports.task, "read_task", refuse)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 404, "resource_unavailable")
    assert "PRIVATE upstream narrative" not in response.text


async def test_a_missing_service_factory_is_a_dependency_refusal_not_a_completion():
    g, _, _ = await harness(completion=RecordingCompletion())
    app = create_app(
        config(direct_completion=True),
        store=g._resolver.store,
        task_read_service_factory=None,
    )
    connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 503, "completion_dependency_unavailable")


# ---------------------------------------------------------------------------------------
# The closed body: 422 for a completion the BPMN does not define.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {"notas_resolucao": "x"},  # no `resultado` — there is NO default
        {"resultado": None, "notas_resolucao": "x"},
        {"resultado": "", "notas_resolucao": "x"},
        {"resultado": "aprovar", "notas_resolucao": "x"},
        {"resultado": "RESOLVIDO_HUMANO", "notas_resolucao": "x"},
        {"resultado": "resolvido_humano"},  # no note
        {"resultado": "resolvido_humano", "notas_resolucao": ""},
        {"resultado": "resolvido_humano", "notas_resolucao": "   "},
        {"resultado": "resolvido_humano", "notas_resolucao": "x" * 2001},
        {"resultado": "resolvido_humano", "notas_resolucao": 7},
        {"resultado": ["resolvido_humano"], "notas_resolucao": "x"},
        {"resultado": "resolvido_humano", "notas_resolucao": "x", "tenant": "outro"},
        {"resultado": "resolvido_humano", "notas_resolucao": "x", "task_id": "outra"},
        {"resultado": "resolvido_humano", "notas_resolucao": "x", "resultado_real": "x"},
        {},
    ],
)
async def test_a_body_outside_the_contract_is_422_and_never_reaches_the_engine(body):
    completion = RecordingCompletion()
    connection, _, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, json=body)
    assert_safe(response, 422, "invalid_completion")
    assert completion.calls == []


async def test_a_completion_accepts_each_of_the_three_bpmn_outcomes():
    for outcome in ("resolvido_humano", "devolvido_agente", "emergencia_acionada"):
        completion = RecordingCompletion()
        connection, _, _ = await client(completion=completion)
        async with connection:
            response = await connection.post(PATH, json={"resultado": outcome, "notas_resolucao": NOTES})
        assert response.status_code == 200, outcome
        assert completion.calls[0][1] == outcome


# ---------------------------------------------------------------------------------------
# Framing: refused before the body is parsed.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": PATH + "?queue=team"},
        {"headers": {"content-type": "text/plain"}},
        {"headers": {"content-type": "application/json; charset=utf-8"}},
        {"url": PREFIX + "/%20/completion"},
        {"url": PREFIX + "/" + "a" * 513 + "/completion"},
    ],
)
async def test_bad_framing_is_400_before_the_service(kwargs):
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    url = kwargs.pop("url", PATH)
    async with connection:
        response = await connection.post(url, content=b'{"resultado":"resolvido_humano"}', **kwargs)
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0 and completion.calls == []


async def test_an_oversized_body_is_refused_before_it_is_parsed():
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, content=b"x" * 70000)
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0 and completion.calls == []


async def test_a_malformed_json_object_is_refused():
    completion = RecordingCompletion()
    connection, _, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, content=b"[1,2,3]")
    assert response.status_code in (400, 422)
    assert completion.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"cookie": ""},
        {"cookie": "__Host-maezo-session=a; __Host-maezo-session=b"},
        {"cookie": "__Host-maezo-session=" + "z" * 43},
        {"x-csrf-token": "z" * 43},
        {"x-csrf-token": ""},
    ],
)
async def test_a_session_or_csrf_problem_is_401(headers):
    completion = RecordingCompletion()
    connection, _, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, json=BODY, headers=headers)
    assert_safe(response, 401, "session_unavailable")
    assert completion.calls == []


async def test_a_duplicated_csrf_or_origin_header_is_401():
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(
            PATH,
            content=b'{"resultado":"resolvido_humano","notas_resolucao":"x"}',
            headers=[
                ("cookie", "__Host-maezo-session=" + SECRET),
                ("content-type", "application/json"),
                ("origin", ORIGIN),
                ("x-csrf-token", CSRF),
                ("x-csrf-token", CSRF),
            ],
        )
    assert_safe(response, 401, "session_unavailable")
    assert g.factory_calls == 0 and completion.calls == []


async def test_an_origin_outside_the_allowlist_is_401_even_with_a_valid_session():
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, json=BODY, headers={"origin": TEST_CHANNEL})
    assert_safe(response, 401, "session_unavailable")
    assert g.factory_calls == 0 and completion.calls == []


async def test_a_foreign_host_answers_with_the_completion_schema_not_the_read_one():
    connection, g, _ = await client()
    async with connection:
        response = await connection.post(PATH, json=BODY, headers={"host": "attacker.test"})
    assert_safe(response, 400, "invalid_request")
    assert g.factory_calls == 0


# ---------------------------------------------------------------------------------------
# CORS: nothing by default, exact origins when declared.
# ---------------------------------------------------------------------------------------
async def test_no_default_deployment_emits_any_cors_header():
    connection, _, _ = await client()
    async with connection:
        post = await connection.post(PATH, json=BODY, headers={"origin": TEST_CHANNEL})
        preflight = await connection.options(
            PATH,
            headers={
                "origin": TEST_CHANNEL,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type,x-csrf-token",
            },
        )
    for response in (post, preflight):
        assert not [h for h in response.headers if h.startswith("access-control-")]


async def test_the_declared_origin_is_accepted_with_credentials_and_nothing_else_is():
    completion = RecordingCompletion()
    connection, _, _ = await client(cors=(TEST_CHANNEL,), completion=completion)
    async with connection:
        preflight = await connection.options(
            PATH,
            headers={
                "origin": TEST_CHANNEL,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type,x-csrf-token",
            },
        )
        allowed = await connection.post(PATH, json=BODY, headers={"origin": TEST_CHANNEL})
        other = await connection.options(
            PATH,
            headers={
                "origin": "https://attacker.test",
                "access-control-request-method": "POST",
            },
        )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == TEST_CHANNEL
    assert preflight.headers["access-control-allow-credentials"] == "true"
    # Never a wildcard, and never an echo of whatever asked.
    assert "*" not in preflight.headers["access-control-allow-origin"]
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == TEST_CHANNEL
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert other.headers.get("access-control-allow-origin") is None


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*.austa.com.br",
        "http://maezo-teste-dev.austa.com.br",
        "https://maezo-teste-dev.austa.com.br/",
        "https://maezo-teste-dev.austa.com.br:8443",
        "https://maezo-teste-dev.austa.com.br?x=1",
        "https://user:pw@maezo-teste-dev.austa.com.br",
        "null",
        ORIGIN,  # the portal's own origin is already allowed; declaring it is a config error
        "https://a.test,https://a.test",
        "https://a.test,https://b.test,https://c.test,https://d.test,https://e.test",
        "maezo-teste-dev.austa.com.br",
        "https://maezo teste.austa.com.br",
    ],
)
async def test_a_malformed_allowlist_fails_at_boot_not_at_the_first_preflight(value):
    with pytest.raises(ValueError):
        config(cors_origins=value)


async def test_the_cognito_origin_can_never_be_a_cross_origin_caller():
    from tests.unit.portal.test_human_session import IDP

    with pytest.raises(ValueError):
        config(cors_origins=IDP)


# ---------------------------------------------------------------------------------------
# The published surface.
# ---------------------------------------------------------------------------------------
async def test_the_route_lives_on_its_own_router_and_the_read_surface_is_unchanged():
    _, _, app = await client()
    assert {r.path for r in task_router.routes} == {PREFIX, PREFIX + "/{task_id}"}
    assert {r.path for r in completion_router.routes} == {PREFIX + "/{task_id}/completion"}
    schema = app.openapi()
    assert set(schema["paths"][PREFIX + "/{task_id}/completion"]) == {"post"}
    for name in ("TaskCompletionSubmission", "TaskCompletionResponse", "PortalCompletionError"):
        assert schema["components"]["schemas"][name]["additionalProperties"] is False
    responses = schema["paths"][PREFIX + "/{task_id}/completion"]["post"]["responses"]
    assert {"200", "400", "401", "403", "404", "409", "422", "501", "503"} <= set(responses)
