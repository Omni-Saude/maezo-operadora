"""ROOT-only D7 actual-image tests; no Docker lifecycle, fake engine, or credential skip.

Set MAEZO_D7_PACKAGE_FIXTURE to prepare_fixture.py's private, seeded, secured fixture.
The database snapshot is read-only and emits hashes, never engine rows or credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import ssl
from pathlib import Path

import asyncpg
import httpx
import pytest

from tests.support.tls_oracle import (
    expect_pinned_jsse_missing_client_certificate_alert,
    server_authenticated_tls13_context,
)

pytestmark = pytest.mark.integration


def fixture() -> Path:
    value = os.environ.get("MAEZO_D7_PACKAGE_FIXTURE")
    assert value, "explicit isolated D7 fixture configuration required"
    root = Path(value)
    assert root.is_absolute() and root.resolve() == root
    metadata = json.loads((root / "d7-fixture.json").read_text())
    assert metadata["fixture_only"] is True and metadata["tenant"].startswith("relay_")
    assert (root / "boundary.json").is_file()
    return root


def client(purpose: str = "agent") -> httpx.Client:
    root = fixture()
    context = ssl.create_default_context(cafile=root / "ca.crt")
    context.load_cert_chain(root / f"{purpose}.crt", root / f"{purpose}.key")
    return httpx.Client(
        base_url="https://localhost:18443",
        verify=context,
        trust_env=False,
        follow_redirects=False,
        timeout=15,
    )


def readiness() -> None:
    with client() as connection:
        response = connection.get("/engine-rest/maezo/v1/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True and body["capabilities"]
    assert body["policy_digest"] == hashlib.sha256((fixture() / "boundary.json").read_bytes()).hexdigest()


async def database_snapshot() -> dict[str, tuple[int, str]]:
    root = fixture()
    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=15433,
        database="maezo",
        user="maezo",
        password=(root / "postgres-password").read_text(),
    )
    result = {}
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            for table in (
                "act_ru_task",
                "act_ru_execution",
                "act_ru_ext_task",
                "act_ru_variable",
                "act_ge_bytearray",
                "act_re_deployment",
                "act_re_procdef",
                "mzo_human_receipt",
                "act_hi_op_log",
            ):
                # Table names are the fixed code-owned list above, never environment/request data.
                rows = await connection.fetch(f"SELECT to_jsonb(t)::text AS row FROM {table} t LIMIT 20001")
                assert len(rows) <= 20000, "fixture snapshot bound exceeded"
                digest = hashlib.sha256()
                for row in sorted(row["row"] for row in rows):
                    raw = row.encode()
                    digest.update(len(raw).to_bytes(8, "big"))
                    digest.update(raw)
                result[table] = (len(rows), digest.hexdigest())
    finally:
        await connection.close()
    return result


def snapshot() -> dict[str, tuple[int, str]]:
    return asyncio.run(database_snapshot())


def operation_request() -> dict:
    policy = json.loads((fixture() / "boundary.json").read_text())
    peer = next(p for p in policy["peers"] if p["engine_user"] == "d7-agent")
    capability = next(c for c in peer["capabilities"] if c["document"]["schema"]["operation"] == "start")
    identity = peer["identity"]
    return {
        "protocol": "maezo.engine-operation.v1",
        "capability_digest": capability["digest"],
        "operation": "start",
        "process_key": "SP-OP-ESCALATION-001",
        "resource_ref": "d7-http-isolated-start",
        "variables": {
            "tenant_id": identity["tenant"],
            "source_agent_id": "helena",
            "source_agent_version": "fixture-v1",
            "conversation_id": "d7-fixture",
            "beneficiario_pseudo_id": "d7-fixture",
            "canal": "d7-fixture",
            "motivo_categoria": "d7-fixture",
            "severidade": "d7-fixture",
            "resumo_contexto": "d7-fixture",
        },
        "correlation": {},
        "all_matching": False,
        "error_code": "",
        "topic": "",
        "message": "",
        "worker_id": "",
        "parameters": {},
        "source_ref": "",
    }


def test_authenticated_readiness_and_scoped_start_on_actual_image() -> None:
    readiness()
    with client() as connection:
        response = connection.post("/engine-rest/maezo/v1/operations", json=operation_request())
    assert response.status_code == 200
    assert response.json()["result"]["id"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/engine-rest/task/fixture/complete",
        "/engine-rest/task/fixture/claim",
        "/engine-rest/task/fixture/assignee",
        "/engine-rest/task/fixture/variables",
        "/engine-rest/task/fixture/localVariables/x/data",
        "/engine-rest/process-instance/fixture/variables",
        "/engine-rest/execution/fixture/localVariables",
        "/engine-rest/process-instance/fixture/modification",
        "/engine-rest/process-instance/restart",
        "/engine-rest/external-task/fixture/complete",
        "/engine-rest/external-task/fetchAndLock",
        "/engine-rest/message",
        "/engine-rest/deployment/create",
        "/engine-rest/identity/verify",
        "/engine-rest/engine/default/identity/verify",
        "/engine-rest/engine/default/task/fixture/complete",
        "/camunda/api/engine/engine/default/task/fixture/complete",
        "/camunda/api/tasklist/tasks/fixture/complete",
        "/camunda/api/cockpit/plugin/base/default/process-instance/fixture",
        "/camunda/api/admin/setup/default/user/create",
        "/engine-rest/maezo/%76%31/operations",
        "/engine-rest/maezo/v1/operations;anything",
    ],
)
def test_raw_aliases_and_mutations_leave_engine_and_receipts_unchanged(path: str) -> None:
    readiness()
    before = snapshot()
    with client("worker") as connection:
        response = connection.post(
            path,
            content=b'{"variables":{"forbidden":{"value":true}}}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code in (400, 401, 403, 404, 405)
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize(
    "purpose", ["worker", "observer", "bridge", "command", "authority", "bootstrap", "deployment"]
)
def test_certificate_purpose_cannot_borrow_agent_capability(purpose: str) -> None:
    readiness()
    before = snapshot()
    with client(purpose) as connection:
        response = connection.post("/engine-rest/maezo/v1/operations", json=operation_request())
    assert response.status_code in (401, 403)
    assert snapshot() == before


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b'{"protocol":"maezo.engine-operation.v1","protocol":"changed"}', "application/json"),
        (b"{}", "multipart/form-data"),
        (b"{}", "application/octet-stream"),
        (b"{}", "application/json; charset=UTF-8"),
        (b'{"x":NaN}', "application/json"),
    ],
)
def test_strict_body_refusal_is_before_mutation(body: bytes, content_type: str) -> None:
    readiness()
    before = snapshot()
    with client() as connection:
        response = connection.post(
            "/engine-rest/maezo/v1/operations", content=body, headers={"Content-Type": content_type}
        )
    assert response.status_code in (400, 403)
    assert snapshot() == before


def test_missing_client_certificate_has_attributable_native_tls_alert() -> None:
    readiness()
    before = snapshot()
    context = server_authenticated_tls13_context(str(fixture() / "ca.crt"))
    with (
        httpx.Client(verify=context, trust_env=False, follow_redirects=False) as connection,
        expect_pinned_jsse_missing_client_certificate_alert(),
    ):
        connection.post("https://localhost:18443/engine-rest/maezo/v1/operations", json=operation_request())
    assert snapshot() == before
    readiness()


def test_secured_image_has_no_plaintext_listener() -> None:
    readiness()
    before = snapshot()
    with pytest.raises(ConnectionRefusedError), socket.create_connection(("127.0.0.1", 18080), timeout=3):
        pytest.fail("the isolated secured image must not expose an HTTP connector")
    assert snapshot() == before
