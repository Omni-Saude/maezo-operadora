"""Ownership ASGI wiring through the real gateway and synthetic trusted unit ports."""

import json

import httpx
import pytest
from tests.unit.gateway.human.test_gateway import SECRET, command, setup, snapshot
from tests.unit.portal.test_decision_api import assert_error, headers, receipt_client
from tests.unit.portal.test_human_session import ORIGIN, config

from maezo.gateway.human.receipt import PublicReceipt
from maezo.portal.api.app import create_app
from maezo.portal.contracts.assignments import BrowserAssignment

PREFIX = "/api/v1/portal"
PATH = PREFIX + "/tasks/task-1/assignments"


async def client(operation="claim", *, active=True):
    snap = snapshot(assignee_ref="human-internal-1" if operation == "release" else None)
    parts = await setup(snap=snap)
    gateway, store, *_ = parts

    def factory(resolver):
        gateway._resolver = resolver
        return gateway

    app = create_app(config(), store=store, decision_service_factory=factory if active else None)
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        headers={"cookie": "__Host-maezo-session=" + SECRET},
    )
    value = command(snap, operation=operation)
    body = {
        "schema_version": "portal-assignment-submission.v1",
        "command": BrowserAssignment.from_command(value).model_dump(mode="json"),
    }
    return c, app, body, parts


@pytest.mark.parametrize("operation", ["claim", "release"])
async def test_assignment_uses_current_gateway_and_acknowledges_only_admitted_intent(operation):
    c, _, body, parts = await client(operation)
    async with c:
        response = await c.post(PATH, json=body, headers=headers())
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["command_id"] == "cmd-1"
    assert "engine_receipt_ref" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    admitted = parts[4].calls[0]
    assert admitted.command.operation == operation
    assert admitted.command.expected_task_revision == 2
    assert admitted.principal == (await parts[0]._resolver.resolve(SECRET)).principal


@pytest.mark.parametrize(
    "changes",
    [
        {"actor": "private-injected"},
        {"tenant": "private-injected"},
        {"variables": {"admin": True}},
        {"target_ref": "private-injected"},
        {"operation": "reassign"},
        {"expected_task_revision": 2},
        {"expected_authority_revision": "07"},
        {"expected_membership_revision": True},
        {"form_version": "0"},
    ],
)
async def test_closed_ownership_input_rejects_authority_injection_before_ports(changes):
    c, _, body, parts = await client()
    body["command"].update(changes)
    async with c:
        response = await c.post(PATH, json=body, headers=headers())
    assert_error(response, 422, "invalid_decision")
    assert "private-injected" not in response.text
    assert parts[2].calls == 0 and not parts[4].calls


@pytest.mark.parametrize(
    "changes,status,code",
    [
        ({"task_id": "another-task"}, 400, "invalid_request"),
        ({"expected_task_revision": "3"}, 409, "revision_conflict"),
        ({"expected_membership_revision": "2"}, 409, "revision_conflict"),
        ({"expected_authority_revision": "8"}, 409, "revision_conflict"),
        ({"process_definition_digest": "0" * 64}, 409, "revision_conflict"),
    ],
)
async def test_current_revision_and_identity_guards_remain_in_gateway(changes, status, code):
    c, _, body, parts = await client()
    body["command"].update(changes)
    async with c:
        response = await c.post(PATH, json=body, headers=headers())
    assert_error(response, status, code)
    assert not parts[4].calls


@pytest.mark.parametrize("mode", ["csrf", "origin", "cookie", "q2", "authority", "admission", "factory"])
async def test_assignment_never_promotes_missing_authentication_or_read_permission(mode):
    c, _, body, parts = await client(active=mode != "factory")
    request_headers = headers()
    code, status = "authentication_unavailable", 401
    if mode == "csrf":
        request_headers["x-csrf-token"] = "wrong"
    elif mode == "origin":
        request_headers["origin"] = "https://other.invalid"
    elif mode == "cookie":
        c.headers["cookie"] = "__Host-maezo-session=wrong"
    elif mode == "q2":
        task = parts[2].task
        parts[2].task = task.model_copy(
            update={"snapshot": task.snapshot.model_copy(update={"allowed_actions": ()})}
        )
        code, status = "operation_forbidden", 403
    elif mode == "authority":
        parts[3].fail = True
        code, status = "authority_unavailable", 503
    elif mode == "admission":
        parts[4].fail = True
        code, status = "admission_unavailable", 503
    else:
        code, status = "production_capabilities_unavailable", 503
    async with c:
        response = await c.post(PATH, json=body, headers=request_headers)
    assert_error(response, status, code)


@pytest.mark.parametrize("mode", ["duplicate", "query", "nonfinite", "oversized", "content-type"])
async def test_assignment_reuses_strict_bounded_framing(mode):
    c, _, body, parts = await client()
    raw, path, hs = json.dumps(body), PATH, headers()
    if mode == "duplicate":
        raw = raw.replace('"operation": "claim"', '"operation": "release", "operation": "claim"')
    elif mode == "query":
        path += "?actor=injected"
    elif mode == "nonfinite":
        raw = raw.replace('"schema_version": 1', '"schema_version": NaN')
    elif mode == "oversized":
        raw += " " * 65536
    else:
        hs["content-type"] = "text/plain"
    async with c:
        response = await c.post(path, content=raw, headers=hs)
    assert_error(response, 400, "invalid_request")
    assert parts[2].calls == 0 and not parts[4].calls


@pytest.mark.parametrize("status", ["pending", "committed", "conflict"])
@pytest.mark.parametrize("suffix", ["", "/receipt"])
async def test_assignment_receipt_recovery_keeps_current_authority_and_actual_proof(status, suffix):
    c, _, _, _, store, authority = await receipt_client(status)
    values = store.result.model_dump(exclude={"operation"})
    values["schema_version"] = "human-public-receipt.v1"
    if status == "committed":
        values["resulting_task_revision"] = "3"
    store.result = PublicReceipt.model_validate(values)
    async with c:
        response = await c.get(PREFIX + "/commands/cmd-1" + suffix + "?task_id=task-1")
    assert response.status_code == 200
    assert response.json() == store.result.model_dump(mode="json")
    assert "operation" not in response.json()
    assert store.calls == authority.calls == 2


async def test_assignment_receipt_schema_is_closed_and_operation_discriminator_is_generated():
    c, app, _, _ = await client()
    async with c:
        schemas = app.openapi()["components"]["schemas"]
    receipt = schemas["AssignmentReceiptResponse"]
    assert receipt["additionalProperties"] is False
    assert receipt["properties"]["schema_version"]["const"] == "human-public-receipt.v1"
    assert "operation" not in receipt["properties"]
    submission = schemas["AssignmentSubmission"]
    assert submission["properties"]["command"]["discriminator"]["propertyName"] == "operation"


def test_browser_codec_preserves_large_revisions_without_float_roundtrip():
    value = command(expected_task_revision=9007199254740993, expected_authority_revision=10**100)
    browser = BrowserAssignment.from_command(value)
    assert browser.expected_task_revision == "9007199254740993"
    assert browser.to_command() == value


@pytest.mark.parametrize("operation", ["claim", "release"])
async def test_context_supplies_exact_submission_basis_from_current_authority(operation):
    c, _, body, parts = await client(operation)
    async with c:
        response = await c.get(PREFIX + "/tasks/task-1/assignment-context")
        assert response.status_code == 200
        basis = response.json()
        assert basis["allowed_operations"] == [operation]
        assert basis["expected_authority_revision"] == "7"
        assert basis["expected_membership_revision"] == "1"
        assert "allowed_inputs" not in basis and "read_only_evidence" not in basis
        for field in ("schema_version", "assignee_ref", "allowed_operations", "valid_until"):
            basis.pop(field)
        basis.update(schema_version=1, command_id="from-context", operation=operation)
        body["command"] = basis
        ack = await c.post(PATH, json=body, headers=headers())
    assert ack.status_code == 202
    assert parts[4].calls[0].command.command_id == "from-context"


async def test_q2_projection_never_becomes_assignment_context_permission():
    c, _, _, parts = await client()
    task = parts[2].task
    parts[2].task = task.model_copy(
        update={"snapshot": task.snapshot.model_copy(update={"allowed_actions": ()})}
    )
    async with c:
        response = await c.get(PREFIX + "/tasks/task-1/assignment-context")
    assert response.status_code == 200
    assert response.json()["allowed_operations"] == []
    assert not parts[4].calls


async def test_context_rejects_revocation_during_authority_io():
    c, _, _, parts = await client()
    original = parts[3].current_authority

    async def revoking(*args):
        result = await original(*args)
        parts[1].memberships.clear()
        return result

    parts[3].current_authority = revoking
    async with c:
        response = await c.get(PREFIX + "/tasks/task-1/assignment-context")
    assert_error(response, 401, "authentication_unavailable")


async def test_context_checks_deadline_after_serialization(monkeypatch):
    from datetime import UTC, datetime, timedelta

    import maezo.portal.api.decisions as api
    from maezo.portal.contracts.assignments import AssignmentContextResponse

    c, _, _, _ = await client()
    original = AssignmentContextResponse.model_dump_json

    class Later:
        @classmethod
        def now(cls, tz):
            return datetime.now(UTC) + timedelta(days=1)

    def elapsed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        monkeypatch.setattr(api, "datetime", Later)
        return result

    monkeypatch.setattr(AssignmentContextResponse, "model_dump_json", elapsed)
    async with c:
        response = await c.get(PREFIX + "/tasks/task-1/assignment-context")
    assert_error(response, 503, "authority_unavailable")
