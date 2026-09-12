"""Real ASGI plus injected trusted ports; no engine integration claim."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.unit.gateway.human.test_classified_decision import BINDING, PRIVATE, configured
from tests.unit.gateway.human.test_gateway import CSRF, SECRET
from tests.unit.portal.test_human_session import ORIGIN, config, membership

from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.decisions import BrowserTaskDecision

PREFIX = "/api/v1/portal"


async def client(*, form="auth_decisao", outcome="NEGAR", active=True):
    parts = await configured(form, outcome)
    g, decision, *_ = parts
    g.factory_calls = 0

    def factory(resolver):
        g.factory_calls += 1
        g._resolver = resolver
        return g

    app = create_app(
        config(),
        store=g._resolver.store,
        decision_service_factory=factory if active else None,
    )
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        headers={"cookie": "__Host-maezo-session=" + SECRET},
    )
    body = {
        "schema_version": "portal-decision-submission.v1",
        "decision": BrowserTaskDecision.from_decision(decision).model_dump(mode="json"),
        "expected_authority_revision": "7",
        "expected_binding_digest": BINDING,
    }
    return c, app, body, parts


def headers(**changes):
    return {"content-type": "application/json", "origin": ORIGIN, "x-csrf-token": CSRF, **changes}


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json() == {"schema_version": "portal-decision-error.v1", "code": code}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert PRIVATE not in response.text and SECRET not in response.text


@pytest.mark.parametrize(
    "form,outcome",
    [
        ("auth_decisao", "NEGAR"),
        ("auth_junta", "NEGAR"),
        ("escalation", "devolvido_agente"),
        ("pagto_admissibilidade", "DEVOLVER"),
    ],
)
async def test_actual_context_and_submission_preserve_basis_and_return_only_pending(form, outcome):
    c, _, body, parts = await client(form=form, outcome=outcome)
    g, decision, _, custody, admission, *_ = parts
    before = decision.model_dump()
    async with c:
        context = await c.get(PREFIX + "/tasks/task-1/decision-context")
        assert context.status_code == 200
        value = context.json()
        assert value["snapshot"]["allowed_actions"] == ["decision"]
        assert value["snapshot"]["task_revision"] == "2"
        assert value["expected_authority_revision"] == "7"
        assert value["expected_membership_revision"] == "1"
        assert value["binding_digest"] == BINDING
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert response.status_code == 202
    ack = response.json()
    assert ack["status"] == "pending" and "engine_receipt_ref" not in ack
    assert ack["payload_digest"] == admission.calls[0].digest
    assert custody.requests[0].decision.model_dump() == before
    assert decision.model_dump() == before
    assert PRIVATE not in response.text and PRIVATE.encode() not in admission.calls[0].canonical
    assert response.headers["cache-control"] == "no-store"
    assert g.factory_calls == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_authority_revision": 7},
        {"expected_authority_revision": True},
        {"expected_authority_revision": "07"},
        {"expected_authority_revision": "1e3"},
        {"expected_authority_revision": "-1"},
        {"expected_authority_revision": "1.0"},
        {"expected_authority_revision": "7\n"},
        {"expected_authority_revision": "７"},
        {"actor": PRIVATE},
        {"tenant": PRIVATE},
        {"schema_version": "unknown"},
    ],
)
async def test_closed_request_never_echoes_validation_inputs(changes, caplog):
    c, _, body, parts = await client()
    body.update(changes)
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert_error(response, 422, "invalid_decision")
    assert parts[0].factory_calls == 0 and not parts[3].requests
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("field", ["justificativa_clinica", "cid10_referencia", "fundamentacao_dut"])
async def test_actual_negative_conditional_validator_runs_before_custody(field):
    c, _, body, parts = await client()
    body["decision"]["inputs"][field] = None
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert_error(response, 422, "invalid_decision")
    assert not parts[3].requests and not parts[4].calls


@pytest.mark.parametrize(
    "form,outcome,field",
    [
        ("escalation", "devolvido_agente", "notas_resolucao"),
        ("pagto_admissibilidade", "DEVOLVER", "justificativa_recusa"),
    ],
)
async def test_other_canonical_required_fields_are_not_approximated(form, outcome, field):
    c, _, body, parts = await client(form=form, outcome=outcome)
    body["decision"]["inputs"][field] = "  "
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert_error(response, 422, "invalid_decision")
    assert not parts[3].requests


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other"),
        ("process_definition_id", "other"),
        ("expected_task_revision", "3"),
        ("expected_evidence_digest", "a" * 64),
        ("expected_membership_revision", "2"),
    ],
)
async def test_route_and_current_context_must_match(field, value):
    c, _, body, parts = await client()
    body["decision"][field] = value
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert_error(
        response,
        400 if field == "task_id" else 409,
        "invalid_request" if field == "task_id" else "revision_conflict",
    )
    assert not parts[3].requests and not parts[4].calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"secret":"x","secret":"y"}',
        b'{"nested":{"x":1,"x":2}}',
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":"\xff"}',
        b"{",
        b"{} garbage",
        b" " * 65537,
    ],
)
async def test_raw_duplicate_malformed_or_oversized_body_refused_before_service(raw):
    c, _, _, parts = await client()
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", content=raw, headers=headers())
    assert_error(response, 400, "invalid_request")
    assert parts[0].factory_calls == 0


@pytest.mark.parametrize("name", ["origin", "x-csrf-token", "content-type"])
async def test_duplicate_security_and_content_headers_refused(name):
    c, _, body, parts = await client()
    values = list(headers().items()) + [(name, headers()[name])]
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", content=json.dumps(body), headers=values)
    assert_error(
        response,
        400 if name == "content-type" else 401,
        "invalid_request" if name == "content-type" else "authentication_unavailable",
    )
    assert parts[0].factory_calls == 0


@pytest.mark.parametrize(
    "name,value,status,code",
    [
        ("origin", "https://other.test", 401, "authentication_unavailable"),
        ("x-csrf-token", "wrong", 401, "authentication_unavailable"),
        ("content-type", "text/plain", 400, "invalid_request"),
        ("host", "other.test", 400, "invalid_request"),
        ("cookie", "", 401, "authentication_unavailable"),
        ("cookie", "__Host-maezo-session=a; __Host-maezo-session=b", 401, "authentication_unavailable"),
    ],
)
async def test_human_session_origin_csrf_host_boundaries(name, value, status, code):
    c, _, body, parts = await client()
    async with c:
        response = await c.post(
            PREFIX + "/tasks/task-1/decisions", json=body, headers=headers(**{name: value})
        )
    assert_error(response, status, code)
    assert not parts[3].requests and not parts[4].calls


@pytest.mark.parametrize("path", ["/tasks/task-1/decision-context", "/tasks/task-1/decisions"])
async def test_query_is_not_an_authority_channel(path):
    c, _, body, parts = await client()
    async with c:
        if path.endswith("context"):
            response = await c.get(PREFIX + path + "?tenant=PRIVATE")
        else:
            response = await c.post(PREFIX + path + "?tenant=PRIVATE", json=body, headers=headers())
    assert_error(response, 400, "invalid_request")
    assert parts[0].factory_calls == 0


async def test_context_get_rejects_body_and_freshness_can_expire_during_render(monkeypatch):
    from maezo.portal.contracts.decisions import DecisionContextResponse

    c, _, _, parts = await client()
    async with c:
        response = await c.request("GET", PREFIX + "/tasks/task-1/decision-context", content=b"{}")
        assert_error(response, 400, "invalid_request")
        original = DecisionContextResponse.model_dump_json

        def expire(self, *args, **kwargs):
            object.__setattr__(self, "valid_until", datetime.now(UTC) - timedelta(seconds=1))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(DecisionContextResponse, "model_dump_json", expire)
        response = await c.get(PREFIX + "/tasks/task-1/decision-context")
    assert_error(response, 503, "authority_unavailable")
    assert not parts[3].requests


async def test_unknown_commit_keeps_identity_and_retries_exact_bytes(caplog):
    c, _, body, parts = await client()
    admission = parts[4]
    admission.fail = True
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
        assert_error(response, 503, "admission_unavailable")
        assert len(admission.rows) == 1
        admission.fail = False
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert response.status_code == 202 and len(admission.rows) == 1
    assert admission.calls[0].canonical == admission.calls[1].canonical
    assert PRIVATE not in caplog.text


async def test_revocation_while_preserving_basis_returns_no_general_admission():
    c, _, body, parts = await client()
    m = membership()
    parts[3].after = lambda: parts[5].memberships.update(
        {(m.issuer, m.subject): m.model_copy(update={"revoked": True})}
    )
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert_error(response, 401, "authentication_unavailable")
    assert not parts[4].calls


async def test_missing_production_composition_preserves_session_route():
    c, _, body, _ = await client(active=False)
    async with c:
        context = await c.get(PREFIX + "/tasks/task-1/decision-context")
        mutation = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
        session = await c.get(PREFIX + "/session")
    assert_error(context, 503, "production_capabilities_unavailable")
    assert_error(mutation, 503, "production_capabilities_unavailable")
    assert session.status_code == 200


async def test_generated_schema_preserves_canonical_input_union_and_decimal_fields():
    from maezo.portal.contracts.models import TaskDecision

    _, app, _, _ = await client()
    schema = app.openapi()["components"]["schemas"]
    wire = schema["BrowserTaskDecision"]
    original = TaskDecision.model_json_schema()["properties"]["inputs"]["discriminator"]["mapping"]
    actual = wire["properties"]["inputs"]["discriminator"]["mapping"]
    assert set(actual) == set(original)
    assert wire["additionalProperties"] is False
    for key in (
        "process_definition_version",
        "form_version",
        "expected_task_revision",
        "expected_evidence_revision",
        "expected_membership_revision",
    ):
        assert wire["properties"][key]["type"] == "string"
    assert schema["DecisionSubmission"]["properties"]["expected_authority_revision"]["type"] == "string"


def test_decision_routes_exist_in_actual_app():
    app = create_app(config(), store=LocalTestIdentityStore(config().tenant))
    assert "/api/v1/portal/tasks/{task_id}/decision-context" in app.openapi()["paths"]
    assert "/api/v1/portal/tasks/{task_id}/decisions" in app.openapi()["paths"]


@pytest.mark.parametrize(
    "field",
    [
        "task_revision",
        "evidence_revision",
        "authority_revision",
        "process_definition_version",
        "form_version",
    ],
)
async def test_huge_revisions_survive_actual_context_and_submission(field):
    from maezo.portal.contracts.decisions import revision_from_decimal

    c, _, body, parts = await client()
    _, _, _, _, admission, _, transport, authority, _ = parts
    digits = "9" * 5000
    huge = revision_from_decimal(digits)
    if field == "authority_revision":
        transport.task = transport.task.model_copy(update={field: huge})
        body["expected_authority_revision"] = digits
    else:
        transport.task = transport.task.model_copy(
            update={"snapshot": transport.task.snapshot.model_copy(update={field: huge})}
        )
        expected = field if "version" in field else "expected_" + field
        body["decision"][expected] = digits
    authority.authority = authority.authority.model_copy(update={field: huge})
    async with c:
        context = await c.get(PREFIX + "/tasks/task-1/decision-context")
        assert context.status_code == 200
        expected_context = (
            context.json()["expected_authority_revision"]
            if field == "authority_revision"
            else context.json()["snapshot"][field]
        )
        assert expected_context == digits
        result = await c.post(PREFIX + "/tasks/task-1/decisions", json=body, headers=headers())
    assert result.status_code == 202
    assert digits.encode() in admission.calls[0].canonical


@pytest.mark.parametrize("path", ["/tasks/%20/decision-context", "/tasks/%24%7Bx%7D/decisions"])
async def test_malformed_or_unmatched_reference_never_discloses_input(path):
    c, _, body, parts = await client()
    async with c:
        if path.endswith("context"):
            response = await c.get(PREFIX + path)
        else:
            response = await c.post(PREFIX + path, json=body, headers=headers())
    assert_error(response, 400, "invalid_request")
    assert not parts[3].requests and not parts[4].calls


async def receipt_client(status="pending"):
    from tests.unit.gateway.human.test_gateway import SCOPE

    from maezo.gateway.human.receipt import (
        BoundReceiptPorts,
        CurrentReceiptAuthority,
        PublicReceipt,
        ReceiptIdentity,
        ReceiptResourceAuthority,
        ReceiptStore,
    )

    c, app, body, parts = await client()
    g = parts[0]
    values = dict(
        schema_version="human-public-receipt.v2",
        operation="decision",
        status=status,
        tenant=SCOPE.tenant,
        task_id="task-1",
        command_id="cmd-1",
        payload_digest="d" * 64,
        principal_ref=membership().principal_ref,
        workload_ref=SCOPE.workload_ref,
        audit_intent_ref="intent-1",
        audit_intent_hash="a" * 64,
    )
    if status == "committed":
        values.update(
            audit_result_ref="b" * 64,
            engine_receipt_ref="engine-1",
            engine_recorded_at=datetime.now(UTC),
            consumed_task_revision="2",
        )
    elif status == "conflict":
        values.update(audit_result_ref="b" * 64, technical_code="REVISION_CONFLICT")

    class Store(ReceiptStore):
        scope = SCOPE

        def __init__(self):
            self.result = PublicReceipt(**values)
            self.calls = 0
            self.fail = False

        async def read_owned(self, principal, task_id, command_id):
            self.calls += 1
            assert principal.tenant == SCOPE.tenant
            if self.fail:
                raise RuntimeError(PRIVATE)
            return self.result

    class Authority(ReceiptResourceAuthority):
        scope = SCOPE

        def __init__(self):
            self.changes = {}
            self.calls = 0

        async def current_authority(self, principal, identity):
            self.calls += 1
            grant = dict(
                identity=ReceiptIdentity.model_validate(identity),
                issuer=principal.issuer,
                subject=principal.subject,
                membership_revision=principal.membership_revision,
                read_permitted=True,
                valid_until=datetime.now(UTC) + timedelta(minutes=1),
            )
            grant.update(self.changes)
            return CurrentReceiptAuthority(**grant)

    store, authority = Store(), Authority()
    g._receipt_ports = BoundReceiptPorts(store, authority)
    return c, app, body, parts, store, authority


@pytest.mark.parametrize("status", ["pending", "committed", "conflict"])
@pytest.mark.parametrize("suffix", ["", "/receipt"])
async def test_authorized_actual_v2_receipt_has_exact_d5_proof_and_null_result_revision(status, suffix):
    c, _, _, _, store, authority = await receipt_client(status)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1" + suffix + "?task_id=task-1")
    assert response.status_code == 200
    assert response.json() == store.result.model_dump(mode="json")
    assert response.json()["schema_version"] == "human-public-receipt.v2"
    assert response.json()["operation"] == "decision"
    assert response.json()["resulting_task_revision"] is None
    assert response.headers["cache-control"] == "no-store"
    assert store.calls == authority.calls == 2  # Final authority pass follows serialization.


@pytest.mark.parametrize(
    "query",
    [
        "",
        "task_id=",
        "task_id=task-1&task_id=task-2",
        "task_id=task-1&tenant=PRIVATE",
        "task_id=%",
        "task_id=%XX",
        "task_id=%FF",
        "task_id=%20",
        "task_id=x%2Fy",
        "actor=PRIVATE",
        "task_id=" + "x" * 513,
    ],
)
async def test_receipt_query_is_closed_and_rejected_before_lookup(query):
    c, _, _, parts, store, _ = await receipt_client()
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1/receipt?" + query)
    assert_error(response, 400, "invalid_request")
    assert parts[0].factory_calls == store.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other"),
        ("command_id", "other"),
        ("tenant", "other"),
        ("principal_ref", "other"),
        ("workload_ref", "other"),
    ],
)
async def test_receipt_store_cannot_rebind_response_identity(field, value):
    c, _, _, _, store, authority = await receipt_client()
    store.result = store.result.model_copy(update={field: value})
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1/receipt?task_id=task-1")
    assert_error(response, 503, "authority_unavailable")
    assert authority.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"read_permitted": False},
        {"membership_revision": 99},
        {"subject": "other"},
        {"issuer": "https://other.test"},
        {"valid_until": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
async def test_receipt_requires_current_resource_authority(changes):
    c, _, _, _, _, authority = await receipt_client()
    authority.changes.update(changes)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1?task_id=task-1")
    assert_error(response, 503, "authority_unavailable")


async def test_receipt_rechecks_session_after_render_and_never_returns_revoked_result(monkeypatch):
    from maezo.portal.contracts.decisions import DecisionReceiptResponse

    c, _, _, parts, store, _ = await receipt_client("committed")
    original = DecisionReceiptResponse.model_dump_json

    def revoke(self, *args, **kwargs):
        m = membership()
        parts[5].memberships[(m.issuer, m.subject)] = m.model_copy(update={"revoked": True})
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DecisionReceiptResponse, "model_dump_json", revoke)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1/receipt?task_id=task-1")
    assert_error(response, 401, "authentication_unavailable")
    assert store.calls == 1


async def test_receipt_changed_during_render_requires_refresh(monkeypatch):
    from maezo.portal.contracts.decisions import DecisionReceiptResponse

    c, _, _, _, store, _ = await receipt_client()
    original = DecisionReceiptResponse.model_dump_json

    def change(self, *args, **kwargs):
        store.result = store.result.model_copy(
            update={
                "status": "conflict",
                "technical_code": "REVISION_CONFLICT",
                "audit_result_ref": "b" * 64,
            }
        )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DecisionReceiptResponse, "model_dump_json", change)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1?task_id=task-1")
    assert_error(response, 409, "revision_conflict")


async def test_receipt_dependency_failure_is_safe_and_get_body_is_rejected(caplog):
    c, _, _, _, store, _ = await receipt_client()
    async with c:
        bad_body = await c.request("GET", PREFIX + "/commands/cmd-1?task_id=task-1", content=b"{}")
        assert_error(bad_body, 400, "invalid_request")
        assert store.calls == 0
        store.fail = True
        failed = await c.get(PREFIX + "/commands/cmd-1?task_id=task-1")
    assert_error(failed, 503, "authority_unavailable")
    assert PRIVATE not in caplog.text


async def test_receipt_schema_is_narrowed_from_actual_d5_source_and_old_assignment_wire_is_preserved():
    from maezo.gateway.human.receipt import PublicReceipt

    c, app, _, _, store, _ = await receipt_client()
    schema = app.openapi()["components"]["schemas"]["DecisionReceiptResponse"]
    assert schema["properties"]["schema_version"]["const"] == "human-public-receipt.v2"
    assert schema["properties"]["operation"]["const"] == "decision"
    assert schema["properties"]["resulting_task_revision"]["type"] == "null"
    assert set(schema["properties"]) == set(PublicReceipt.model_fields)
    assert schema["additionalProperties"] is False
    assignment = store.result.model_dump(exclude={"operation"})
    assignment["schema_version"] = "human-public-receipt.v1"
    store.result = PublicReceipt.model_validate(assignment)
    assert "operation" not in store.result.model_dump()
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1?task_id=task-1")
    assert response.status_code == 200
    assert response.json() == assignment
    assert "operation" not in response.json()


async def test_valid_but_oversized_chunked_form_is_rejected_before_gateway():
    c, _, body, parts = await client()
    body["decision"]["inputs"]["justificativa_clinica"] = "synthetic" * 9000
    raw = json.dumps(body).encode()

    async def chunks():
        for offset in range(0, len(raw), 8192):
            yield raw[offset : offset + 8192]

    async with c:
        result = await c.post(PREFIX + "/tasks/task-1/decisions", content=chunks(), headers=headers())
    assert_error(result, 400, "invalid_request")
    assert parts[0].factory_calls == 0 and not parts[3].requests


@pytest.mark.parametrize(
    "change",
    [
        {"status": "committed"},
        {"engine_receipt_ref": "unproven"},
        {"status": "conflict", "technical_code": "REVISION_CONFLICT"},
    ],
)
async def test_http_never_promotes_partial_d5_receipt_proof(change):
    c, _, _, _, store, _ = await receipt_client()
    store.result = store.result.model_copy(update=change)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1/receipt?task_id=task-1")
    assert_error(response, 503, "authority_unavailable")


async def test_duplicate_actual_authority_key_cannot_override_first_value():
    c, _, body, parts = await client()
    raw = json.dumps(body).replace(
        '"expected_authority_revision": "7"',
        '"expected_authority_revision": "8", "expected_authority_revision": "7"',
    )
    async with c:
        response = await c.post(PREFIX + "/tasks/task-1/decisions", content=raw, headers=headers())
    assert_error(response, 400, "invalid_request")
    assert parts[0].factory_calls == 0 and not parts[3].requests


async def test_valid_body_cannot_complete_a_different_url_task():
    c, _, body, parts = await client()
    async with c:
        response = await c.post(PREFIX + "/tasks/other/decisions", json=body, headers=headers())
    assert_error(response, 400, "invalid_request")
    assert not parts[3].requests and not parts[4].calls
