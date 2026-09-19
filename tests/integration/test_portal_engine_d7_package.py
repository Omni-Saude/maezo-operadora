"""ROOT-only D7 actual-image tests; no Docker lifecycle, fake engine, or credential skip.

Set MAEZO_D7_PACKAGE_FIXTURE to prepare_fixture.py's private, seeded, secured fixture.
The database snapshot is read-only and emits hashes, never engine rows or credentials.

wait_ready()'s readiness observation additionally requires three operator bindings, exported
next to MAEZO_D7_PACKAGE_FIXTURE (procedure and derivations in
deploy/cibseven/secured/ACCEPTANCE.md): MAEZO_D7_READINESS_RUN_ID names the run
(`d7-[a-z0-9-]{8,64}`) and MAEZO_D7_READINESS_SOURCE_SHA / MAEZO_D7_READINESS_SOURCE_TREE are
the 40-hex commit/tree of the observed source. They are written into readiness-attempts.json
so every recorded attempt stays attributable; without them the observation fails closed.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4

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


def resources() -> dict:
    value = json.loads((fixture() / "resources.json").read_text())
    tenant = json.loads((fixture() / "d7-fixture.json").read_text())["tenant"]
    assert value["human"]["tenantId"] == value["external"]["tenantId"] == tenant
    assert value["human_definition"]["tenantId"] == tenant
    assert value["human"]["processDefinitionId"] == value["human_definition"]["id"]
    assert value["tenant_schema"] == tenant and value["decision_receipt_fabricated"] is False
    return value


async def database():
    root = fixture()
    return await asyncpg.connect(
        host="127.0.0.1",
        port=15433,
        database="maezo",
        user="maezo",
        password=(root / "postgres-password").read_text(),
    )


async def assert_bound_resources() -> None:
    refs = resources()
    tenant = refs["tenant_schema"]
    connection = await database()
    try:
        definitions = json.loads((fixture() / "definitions.json").read_text())
        definitions["MZO-HUMAN-SYNTHETIC"] = refs["human_definition"]
        for key, definition in definitions.items():
            row = await connection.fetchrow(
                "SELECT key_, version_, tenant_id_, deployment_id_ FROM act_re_procdef WHERE id_=$1",
                definition["id"],
            )
            assert row is not None
            assert (row["key_"], row["version_"], row["tenant_id_"]) == (key, definition["version"], tenant)
            assert row["deployment_id_"] == definition["deploymentId"]
        for table, identifier, definition in (
            ("act_ru_task", refs["human"]["id"], refs["human_definition"]["id"]),
            ("act_ru_execution", refs["human_instance"], refs["human_definition"]["id"]),
            ("act_ru_ext_task", refs["external"]["id"], refs["external"]["processDefinitionId"]),
        ):
            row = await connection.fetchrow(
                f"SELECT proc_def_id_, tenant_id_ FROM {table} WHERE id_=$1", identifier
            )
            assert row is not None, "attack target must exist before policy refusal can receive credit"
            assert (row["proc_def_id_"], row["tenant_id_"]) == (definition, tenant)
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM mzo_human_receipt WHERE tenant_=$1 AND command_=$2",
                tenant,
                refs["receipt_command_id"],
            )
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM act_ru_identitylink WHERE task_id_=$1", refs["human"]["id"]
            )
            > 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM act_ru_variable WHERE task_id_=$1", refs["human"]["id"]
            )
            > 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM act_hi_detail WHERE task_id_=$1", refs["human"]["id"]
            )
            > 0
        )
    finally:
        await connection.close()


async def database_snapshot() -> dict[str, tuple[int, str]]:
    refs = resources()
    tenant = refs["tenant_schema"]
    assert re.fullmatch(r"relay_[0-9a-f]{24}", tenant)
    connection = await database()
    result = {}
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            tables = {
                name: "tenant_id_=$1"
                for name in (
                    "act_ru_task",
                    "act_ru_execution",
                    "act_ru_ext_task",
                    "act_ru_variable",
                    "act_ge_bytearray",
                    "act_re_deployment",
                    "act_re_procdef",
                    "act_hi_op_log",
                    "act_hi_procinst",
                    "act_hi_taskinst",
                    "act_hi_varinst",
                    "act_hi_detail",
                )
            }
            tables["act_ru_identitylink"] = "task_id_ IN (SELECT id_ FROM act_ru_task WHERE tenant_id_=$1)"
            tables["act_hi_identitylink"] = "tenant_id_=$1"
            tables["mzo_human_receipt"] = "tenant_=$1"
            # Real production migrations and a real D6 claim/receipt populate these;
            # absence is a failed precondition, never an empty successful snapshot.
            for table in ("audit_chain", "human_command_outbox", "human_command_delivery"):
                tables[f'"{tenant}".{table}'] = "true"
            for table, predicate in tables.items():
                args = [tenant] if "$1" in predicate else []
                rows = await connection.fetch(
                    f"SELECT to_jsonb(t)::text AS row FROM {table} t WHERE {predicate} LIMIT 20001", *args
                )
                assert len(rows) <= 20000, "fixture snapshot bound exceeded"
                digest = hashlib.sha256()
                for row in sorted(row["row"] for row in rows):
                    raw = row.encode()
                    digest.update(len(raw).to_bytes(8, "big"))
                    digest.update(raw)
                result[table] = (len(rows), digest.hexdigest())
            for table in (
                "act_ru_task",
                "act_ru_execution",
                "act_ru_ext_task",
                "act_ru_variable",
                "act_hi_varinst",
                "act_hi_detail",
                "mzo_human_receipt",
            ):
                assert result[table][0] > 0, "nonempty native/history/receipt witness required"
            for table in ("audit_chain", "human_command_outbox", "human_command_delivery"):
                assert result[f'"{tenant}".{table}'][0] > 0, "nonempty actual D6 witness required"
    finally:
        await connection.close()
    return result


def snapshot() -> dict[str, tuple[int, str]]:
    asyncio.run(assert_bound_resources())
    return asyncio.run(database_snapshot())


def denied(response: httpx.Response, code: str = "engine_operation_denied", status: int = 403) -> None:
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"error": code}


def bind_path(path: str) -> str:
    refs = resources()
    return path.format(
        task=refs["human"]["id"],
        execution=refs["human_execution"],
        instance=refs["human_instance"],
        external=refs["external"]["id"],
        definition=refs["human_definition"]["id"],
        deployment=refs["human_definition"]["deploymentId"],
    )


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
        "resource_ref": "d7-http-" + uuid4().hex,
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
        "/engine-rest/task/{task}/complete",
        "/engine-rest/task/{task}/claim",
        "/engine-rest/task/{task}/assignee",
        "/engine-rest/task/{task}/delegate",
        "/engine-rest/task/{task}/resolve",
        "/engine-rest/task/{task}/identity-links",
        "/engine-rest/task/{task}/identity-links/groups/synthetic-reviewers/type/candidate",
        "/engine-rest/task/{task}/variables",
        "/engine-rest/task/{task}/variables/fixture_value",
        "/engine-rest/task/{task}/variables/fixture_value/data",
        "/engine-rest/task/{task}/localVariables",
        "/engine-rest/task/{task}/localVariables/fixture_local",
        "/engine-rest/task/{task}/localVariables/fixture_binary/data",
        "/engine-rest/execution/{execution}/variables",
        "/engine-rest/execution/{execution}/variables/fixture_value",
        "/engine-rest/execution/{execution}/localVariables",
        "/engine-rest/execution/{execution}/localVariables/fixture_local",
        "/engine-rest/execution/{execution}/signal",
        "/engine-rest/process-instance/{instance}/variables",
        "/engine-rest/process-instance/{instance}/variables/fixture_value",
        "/engine-rest/process-instance/{instance}/modification",
        "/engine-rest/process-instance/{instance}/suspended",
        "/engine-rest/process-definition/{definition}/restart",
        "/engine-rest/process-definition/{definition}/start",
        "/engine-rest/process-instance/restart",
        "/engine-rest/external-task/{external}/complete",
        "/engine-rest/external-task/{external}/failure",
        "/engine-rest/external-task/{external}/bpmnError",
        "/engine-rest/external-task/{external}/unlock",
        "/engine-rest/external-task/{external}/extendLock",
        "/engine-rest/external-task/{external}/lock",
        "/engine-rest/external-task/{external}/retries",
        "/engine-rest/external-task/{external}/priority",
        "/engine-rest/external-task/fetchAndLock",
        "/engine-rest/deployment/create",
        "/engine-rest/deployment/{deployment}",
        "/engine-rest/message",
        "/engine-rest/identity/verify",
        "/engine-rest/user",
        "/engine-rest/group",
        "/engine-rest/authorization",
    ],
)
@pytest.mark.parametrize("alias", ["native", "engine-name", "camunda-engine"])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"])
def test_raw_aliases_and_mutations_leave_engine_and_receipts_unchanged(
    path: str, alias: str, method: str
) -> None:
    readiness()
    before = snapshot()
    path = bind_path(path)
    if alias == "engine-name":
        path = path.replace("/engine-rest/", "/engine-rest/engine/default/", 1)
    elif alias == "camunda-engine":
        path = path.replace("/engine-rest/", "/camunda/api/engine/engine/default/", 1)
    with client("worker") as connection:
        response = connection.request(
            method,
            path,
            content=b'{"variables":{"forbidden":{"value":true}}}',
            headers={"Content-Type": "application/json"},
        )
        if method == "HEAD":
            # RFC HEAD intentionally suppresses response bodies. Prove the same route's
            # boundary code with GET, then require HEAD's exact status and safe headers.
            denied(connection.get(path))
            assert response.status_code == 403 and response.content == b""
            assert response.headers["cache-control"] == "no-store"
        else:
            assert response.status_code == 403
            assert response.json() == {"error": "engine_operation_denied"}
            assert response.headers["cache-control"] == "no-store"
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize(
    "path",
    [
        "/camunda/api/tasklist/tasks/{task}/complete",
        "/camunda/api/cockpit/plugin/base/default/process-instance/{instance}",
        "/camunda/api/admin/setup/default/user/create",
        "/engine-rest/maezo/%76%31/operations",
        "/engine-rest/maezo/v1/operations;anything",
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"])
def test_closed_ui_and_noncanonical_paths_have_explicit_boundary_oracle(path: str, method: str) -> None:
    readiness()
    before = snapshot()
    with client("worker") as connection:
        response = connection.request(method, bind_path(path), json={})
        if method == "HEAD":
            denied(connection.get(bind_path(path)))
            assert response.status_code == 403 and response.content == b""
        else:
            denied(response)
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
    denied(response)
    assert snapshot() == before
    readiness()


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
    denied(response, "engine_invalid_body", 400)
    assert snapshot() == before
    readiness()


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
    readiness()


def typed_request(schema_id: str, resource: str) -> dict:
    policy = json.loads((fixture() / "boundary.json").read_text())
    binding = next(
        c
        for p in policy["peers"]
        for c in p["capabilities"]
        if c["document"]["schema"]["schema_id"] == schema_id
    )
    doc = binding["document"]
    schema = doc["schema"]
    defaults = {"String": "synthetic", "Boolean": False, "Double": 1.0, "Integer": 1, "Json": []}
    values = {
        f["name"]: json.loads(f["fixed_json"]) if f["fixed_json"] is not None else defaults[f["kind"]]
        for f in schema["fields"]
        if f["required"]
    }
    request = operation_request()
    request.update({k: schema[k] for k in ("operation", "process_key", "topic", "message", "all_matching")})
    request.update(
        capability_digest=binding["digest"],
        variables=values,
        resource_ref=resource,
        worker_id=doc["worker_id"],
        error_code=next(iter(schema["error_codes"]), ""),
    )
    request["parameters"] = {
        "fetch_lock": {"maxTasks": 1, "lockDuration": 60000, "asyncResponseTimeout": 1, "variables": []},
        "external_extend_lock": {"newDuration": 60000},
        "external_failure": {"retries": 1, "retryTimeout": 0, "errorCategory": "worker_failure"},
    }.get(schema["operation"], {})
    return request


def operation(connection: httpx.Client, request: dict) -> object:
    response = connection.post("/engine-rest/maezo/v1/operations", json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["capability_digest"] == request["capability_digest"]
    assert response.headers["cache-control"] == "no-store"
    return body["result"]


def test_scoped_start_read_and_worker_lifecycle_have_native_causal_effects() -> None:
    readiness()
    start = operation_request()
    with client() as connection:
        result = operation(connection, start)
        for suffix in ("read_active", "read_history"):
            request = typed_request("helena.escalation.start.v1." + suffix, start["resource_ref"])
            assert [r["id"] for r in operation(connection, request)] == [result["id"]]
    base = "contas.calculate_impact.complete.v1"
    with client("worker") as connection:
        fetched = operation(connection, typed_request(base + ".fetch_lock", "d7-fetch"))
        assert len(fetched) == 1
        target = fetched[0]
        assert target["definition_id"] == resources()["external"]["processDefinitionId"]
        assert target["worker_id"] == "fixture-worker"

        async def observe():
            db = await database()
            try:
                return dict(await db.fetchrow("SELECT * FROM act_ru_ext_task WHERE id_=$1", target["id"]))
            finally:
                await db.close()

        assert asyncio.run(observe())["worker_id_"] == "fixture-worker"
        for suffix in ("external_extend_lock", "external_failure", "external_unlock"):
            assert operation(connection, typed_request(base + "." + suffix, target["id"])) == {
                "applied": True
            }
            observed = asyncio.run(observe())
            if suffix == "external_extend_lock":
                assert observed["lock_exp_time_"].timestamp() * 1000 >= target["lock_expires_at"]
            else:
                if suffix == "external_failure":
                    assert observed["retries_"] == 1
                assert (
                    observed["lock_exp_time_"] is None
                    or observed["lock_exp_time_"].timestamp() <= time.time()
                )
                acquired = operation(connection, typed_request(base + ".fetch_lock", "d7-reacquire"))
                # Other pending tasks can be returned: consume the returned real identity,
                # never assume fetching a named task or complete a stale ID.
                assert len(acquired) == 1
                target = acquired[0]
        complete = typed_request(base, target["id"])
        assert operation(connection, complete) == {"applied": True}

        async def committed():
            db = await database()
            try:
                assert (
                    await db.fetchval("SELECT count(*) FROM act_ru_ext_task WHERE id_=$1", target["id"]) == 0
                )
                assert (
                    await db.fetchval(
                        "SELECT count(*) FROM act_ru_task WHERE proc_inst_id_=$1 AND task_def_key_='human'",
                        target["process_instance_id"],
                    )
                    == 1
                )
                for key in complete["variables"]:
                    assert (
                        await db.fetchval(
                            "SELECT count(*) FROM act_hi_varinst WHERE proc_inst_id_=$1 AND name_=$2",
                            target["process_instance_id"],
                            key,
                        )
                        == 1
                    )
            finally:
                await db.close()

        asyncio.run(committed())
    readiness()


@pytest.mark.parametrize("purpose", ["agent", "worker", "observer", "bridge", "bootstrap", "deployment"])
@pytest.mark.parametrize(
    "path",
    ["/maezo-human/v1/commands", "/maezo-human/v1/authority", "/maezo-human/v1/receipts/{task}/pending"],
)
def test_nonhuman_peers_cannot_enter_human_native_boundary(purpose: str, path: str) -> None:
    readiness()
    before = snapshot()
    with client(purpose) as connection:
        denied(connection.request("GET" if "/receipts/" in path else "POST", bind_path(path), json={}))
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Forwarded-Proto", "https"),
        ("X-Forwarded-Client-Cert", "forged"),
        ("X-Forwarded-User", "d7-agent"),
        ("Authorization", "Bearer synthetic-forgery"),
        ("X-Maezo-Tenant", "foreign-tenant"),
        ("X-Maezo-Environment", "production"),
    ],
)
def test_forwarded_identity_never_borrows_capability(header: str, value: str) -> None:
    readiness()
    before = snapshot()
    with client("worker") as connection:
        denied(
            connection.post(
                "/engine-rest/maezo/v1/operations", json=operation_request(), headers={header: value}
            )
        )
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize("certificate", ["unknown-leaf", "wrong-san"])
def test_known_ca_unknown_or_wrong_san_leaf_is_attributed_denial(certificate: str) -> None:
    readiness()
    before = snapshot()
    with client(certificate) as connection:
        denied(connection.post("/engine-rest/maezo/v1/operations", json=operation_request()))
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize("certificate", ["untrusted-client", "expired", "no-eku"])
def test_invalid_client_chain_requires_typed_tls_or_exact_application_refusal(certificate: str) -> None:
    readiness()
    before = snapshot()
    context = ssl.create_default_context(cafile=fixture() / "ca.crt")
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(fixture() / f"{certificate}.crt", fixture() / f"{certificate}.key")
    try:
        with (
            socket.create_connection(("127.0.0.1", 18443), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname="localhost") as tls,
        ):
            tls.sendall(
                b"POST /engine-rest/maezo/v1/operations HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
            )
            response = b""
            while chunk := tls.recv(4096):
                response += chunk
            # Missing EKU can be accepted by JSSE and rejected by our certificate gate.
            assert certificate == "no-eku"
            assert response.startswith(b"HTTP/1.1 403 ")
            assert b'{"error":"engine_operation_denied"}' in response
    except ssl.SSLError as failure:
        allowed = {
            "TLSV1_ALERT_UNKNOWN_CA",
            "SSLV3_ALERT_CERTIFICATE_UNKNOWN",
            "SSLV3_ALERT_BAD_CERTIFICATE",
            "SSLV3_ALERT_CERTIFICATE_EXPIRED",
            "SSLV3_ALERT_UNSUPPORTED_CERTIFICATE",
        }
        assert failure.reason in allowed, (
            "network/reset/server-auth failures never establish client rejection"
        )
    assert snapshot() == before
    readiness()


def rewrite_in_place(path: Path, raw: bytes) -> None:
    inode = path.stat().st_ino
    with path.open("r+b") as stream:
        stream.seek(0)
        stream.write(raw)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    assert path.stat().st_ino == inode, "mounted-file corruption must preserve the bound inode"


def test_actual_mounted_policy_corruption_denies_and_same_identity_recovers() -> None:
    readiness()
    before = snapshot()
    path = fixture() / "boundary.json"
    saved = path.read_bytes()
    pending = operation_request()
    with client() as established:
        assert established.get("/engine-rest/maezo/v1/readiness").status_code == 200
        try:
            rewrite_in_place(path, b"{}")
            for connection in (established, client()):
                try:
                    denied(
                        connection.get("/engine-rest/maezo/v1/readiness"), "engine_profile_unavailable", 503
                    )
                    denied(
                        connection.post("/engine-rest/maezo/v1/operations", json=pending),
                        "engine_profile_unavailable",
                        503,
                    )
                finally:
                    if connection is not established:
                        connection.close()
            assert snapshot() == before
        finally:
            rewrite_in_place(path, saved)
        readiness()
        assert operation(established, pending)["id"]


def restart_owned_engine(document: dict) -> None:
    """Executed only by ROOT's explicit serial lifecycle lane; never during collection."""
    project = os.environ.get("MAEZO_D7_COMPOSE_PROJECT", "")
    assert re.fullmatch(r"d7-[a-z0-9-]{8,64}", project), "explicit ROOT-owned d7 project required"
    root = fixture()
    rewrite_in_place(root / "boundary.json", (json.dumps(document, indent=2) + "\n").encode())
    compose_path = root / "secured-compose.json"
    compose = json.loads(compose_path.read_text())
    compose["services"]["engine"]["environment"]["MAEZO_ENGINE_BOUNDARY_SHA256"] = hashlib.sha256(
        (root / "boundary.json").read_bytes()
    ).hexdigest()
    assert compose["services"]["engine"]["ports"] == ["127.0.0.1:18443:8443"]
    assert (
        compose["services"]["engine"]["image"]
        == json.loads((root / "d7-fixture.json").read_text())["secured_image"]
    )
    compose_path.write_text(json.dumps(compose, indent=2) + "\n")
    argv = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(compose_path),
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "engine",
    ]
    process = subprocess.run(argv, capture_output=True, timeout=90, check=False)
    evidence = root / ("restart-" + uuid4().hex)
    evidence.mkdir(mode=0o700)
    (evidence / "argv.json").write_text(json.dumps(argv))
    (evidence / "stdout").write_bytes(process.stdout)
    (evidence / "stderr").write_bytes(process.stderr)
    (evidence / "exit").write_text(str(process.returncode))
    assert process.returncode == 0, "owned engine recreation failed; see private lifecycle streams"


_READINESS_TAIL_LIMIT = 32
_READINESS_PURPOSES = {"agent", "agent-next", "worker"}
_READINESS_TRANSPORT_CLASSES = (
    (httpx.ConnectTimeout, "connect_timeout"),
    (httpx.ReadTimeout, "read_timeout"),
    (httpx.WriteTimeout, "write_timeout"),
    (httpx.PoolTimeout, "pool_timeout"),
    (httpx.TimeoutException, "timeout"),
    (httpx.ConnectError, "connect_error"),
    (httpx.ReadError, "read_error"),
    (httpx.RemoteProtocolError, "remote_protocol_error"),
)


def _new_readiness_observation(purpose: str) -> dict:
    run_id = os.environ.get("MAEZO_D7_READINESS_RUN_ID", "")
    source_sha = os.environ.get("MAEZO_D7_READINESS_SOURCE_SHA", "")
    source_tree = os.environ.get("MAEZO_D7_READINESS_SOURCE_TREE", "")
    assert purpose in _READINESS_PURPOSES, "invalid readiness observation purpose"
    assert re.fullmatch(r"d7-[a-z0-9-]{8,64}", run_id), (
        "MAEZO_D7_READINESS_RUN_ID is required to attribute readiness-attempts.json to the run "
        "that produced it: d7- prefix plus 8-64 of [a-z0-9-] (the D7_PROJECT compose name "
        "qualifies); export it next to MAEZO_D7_PACKAGE_FIXTURE — "
        "deploy/cibseven/secured/ACCEPTANCE.md"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", source_sha), (
        "MAEZO_D7_READINESS_SOURCE_SHA is required to bind readiness-attempts.json to the exact "
        'observed source revision: 40-hex `git -C "$CANDIDATE" rev-parse HEAD`; export it next '
        "to MAEZO_D7_PACKAGE_FIXTURE — deploy/cibseven/secured/ACCEPTANCE.md"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", source_tree), (
        "MAEZO_D7_READINESS_SOURCE_TREE is required to bind readiness-attempts.json to the exact "
        "observed source tree: 40-hex `git -C \"$CANDIDATE\" rev-parse 'HEAD^{tree}'`; export it "
        "next to MAEZO_D7_PACKAGE_FIXTURE — deploy/cibseven/secured/ACCEPTANCE.md"
    )
    return {
        "schema": "maezo.d7.readiness-attempts.v1",
        "run_id": run_id,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "purpose": purpose,
        "budget_ms": 90_000,
        "request_cap_ms": 15_000,
        "backoff_cap_ms": 250,
        "attempt_count": 0,
        "tail_limit": _READINESS_TAIL_LIMIT,
        "attempt_tail": [],
        "completion": "polling",
    }


def _write_readiness_observation(observation: dict) -> None:
    path = fixture() / "readiness-attempts.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("unsafe readiness observation target")
    encoded = (json.dumps(observation, separators=(",", ":"), sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _readiness_transport_class(error: Exception) -> str:
    for error_type, category in _READINESS_TRANSPORT_CLASSES:
        if isinstance(error, error_type):
            return category
    raise AssertionError("unclassified readiness transport error")


def _readiness_milliseconds(started_at: float) -> int:
    return max(0, int(round((time.monotonic() - started_at) * 1000)))


def _readiness_connection_trace(started_at: float) -> tuple[dict, object]:
    """Observe six pinned httpcore events only; never inspect trace info."""
    trace = {"events": [], "overflow": False, "unavailable": False}
    names = {
        f"connection.{stage}.{outcome}": (stage, outcome)
        for stage in ("connect_tcp", "start_tls")
        for outcome in ("started", "complete", "failed")
    }

    def observe(name: str, info: object) -> None:
        del info
        if type(name) is not str or name not in names:
            return
        if len(trace["events"]) >= 8:
            trace["overflow"] = True
            return
        try:
            elapsed = _readiness_milliseconds(started_at)
            stage, outcome = names[name]
            event = {
                "stage": stage,
                "outcome": outcome,
                "elapsed_ms": min(elapsed, 86_400_000),
            }
            if elapsed > 86_400_000:
                event["elapsed_ms_saturated"] = True
            trace["events"].append(event)
        except Exception:
            trace["unavailable"] = True

    return trace, observe


def wait_ready(purpose: str = "agent") -> None:
    started_at = time.monotonic()
    deadline = started_at + 90
    observation = _new_readiness_observation(purpose)
    recording_failed = False

    def record(completion: str | None = None) -> bool:
        nonlocal recording_failed
        if completion is not None:
            observation["completion"] = completion
        try:
            _write_readiness_observation(observation)
        except Exception:
            recording_failed = True
            return False
        return True

    def append_attempt(item: dict) -> None:
        if connection_trace["events"] or connection_trace["overflow"] or connection_trace["unavailable"]:
            item["connection_trace"] = connection_trace
        # v1 elapsed extension: exact rounded milliseconds through one day;
        # beyond this representation cap, explicitly mark a saturated value.
        # This is diagnostic only and never extends the 90-second deadline.
        if item["ended_ms"] > 86_400_000:
            item["ended_ms"] = 86_400_000
            item["ended_ms_saturated"] = True
        observation["attempt_count"] += 1
        item["index"] = observation["attempt_count"]
        observation["attempt_tail"].append(item)
        del observation["attempt_tail"][:-_READINESS_TAIL_LIMIT]
        record()

    record()
    while time.monotonic() < deadline:
        request_started_ms = _readiness_milliseconds(started_at)
        connection_trace, observe_connection = _readiness_connection_trace(started_at)
        try:
            with client(purpose) as connection:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                response = connection.get(
                    "/engine-rest/maezo/v1/readiness",
                    timeout=min(15, remaining),
                    extensions={"trace": observe_connection},
                )
            status = response.status_code
            assert type(status) is int and 100 <= status <= 599, "invalid readiness HTTP status"
            append_attempt(
                {
                    "started_ms": request_started_ms,
                    "ended_ms": _readiness_milliseconds(started_at),
                    "kind": "http",
                    "status": status,
                }
            )
            if time.monotonic() >= deadline:
                break
            if status == 200:
                try:
                    body = response.json()
                    assert body["ready"] and body["capabilities"]
                except Exception:
                    record("malformed_success")
                    raise AssertionError("malformed readiness success response") from None
                if recording_failed or not record("ready"):
                    record("recording_failed")
                    pytest.fail("readiness observation recording failed")
                if time.monotonic() >= deadline:
                    if not record("deadline_exhausted"):
                        record("recording_failed")
                    break
                return
        except (
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        ) as error:
            append_attempt(
                {
                    "started_ms": request_started_ms,
                    "ended_ms": _readiness_milliseconds(started_at),
                    "kind": "transport",
                    "transport_class": _readiness_transport_class(error),
                }
            )
        except (KeyboardInterrupt, SystemExit):
            record("interrupted")
            raise
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))
    if not record("recording_failed" if recording_failed else "deadline_exhausted"):
        record("recording_failed")
    if recording_failed:
        pytest.fail("readiness observation recording failed")
    pytest.fail("actual authenticated capability readiness did not recover")


def test_real_certificate_overlap_digest_restart_and_old_leaf_revocation() -> None:
    readiness()
    before = snapshot()
    original = json.loads((fixture() / "boundary.json").read_text())
    overlap = copy.deepcopy(original)
    old = next(p for p in overlap["peers"] if p["engine_user"] == "d7-agent")
    new = copy.deepcopy(old)
    new.update(json.loads((fixture() / "certificate-variants.json").read_text())["agent-next"])
    overlap["peers"].append(new)
    pending = operation_request()
    try:
        restart_owned_engine(overlap)
        wait_ready()
        wait_ready("agent-next")
        revoked = copy.deepcopy(overlap)
        revoked["peers"] = [
            p for p in revoked["peers"] if p["certificate_sha256"] != old["certificate_sha256"]
        ]
        with client() as established:
            assert established.get("/engine-rest/maezo/v1/readiness").status_code == 200
            # Before restarting: old established TLS session must not outlive mounted digest loss.
            rewrite_in_place(fixture() / "boundary.json", (json.dumps(revoked, indent=2) + "\n").encode())
            denied(
                established.post("/engine-rest/maezo/v1/operations", json=pending),
                "engine_profile_unavailable",
                503,
            )
        restart_owned_engine(revoked)
        wait_ready("agent-next")
        with client() as old_connection:
            denied(old_connection.post("/engine-rest/maezo/v1/operations", json=pending))
        assert snapshot() == before
        with client("agent-next") as new_connection:
            assert operation(new_connection, pending)["id"]
    finally:
        restart_owned_engine(original)
        wait_ready()


def test_actual_human_commit_lost_response_then_secured_engine_restart_reconciles_same_pending_identity() -> (
    None
):
    from maezo.gateway.human.outbox import PostgresHumanOutbox
    from maezo.portal.engine.profile import HumanCommand
    from tests.support.human_relay_live import ObservedTransport, RelayConfig, relay

    readiness()
    original = json.loads((fixture() / "boundary.json").read_text())
    os.environ["MAEZO_HUMAN_RELAY_PRIVATE_DIR"] = str(fixture())
    config = RelayConfig.load()
    command = HumanCommand(**json.loads((fixture() / "pending-release.json").read_text()))
    assert command.command_id == resources()["pending_command_id"]

    async def committed_then_lost():
        pool = await config.pool()
        try:
            store = PostgresHumanOutbox(scope=config.scope, pool=pool)
            observer = ObservedTransport(config.transport())
            observer.drop_response = True
            assert await relay(store, observer).run_once()
            assert observer.events == [
                "GET.begin",
                "GET.missing",
                "POST.begin",
                "POST.committed",
                "response.dropped.after.actual.commit",
            ]
            return observer.receipts[0]
        finally:
            await pool.close()

    receipt = asyncio.run(committed_then_lost())
    after_effect = snapshot()
    restart_owned_engine(original)
    wait_ready()
    assert snapshot() == after_effect, (
        "image restart preserves the actual committed task and pending identity"
    )

    async def reconcile():
        db = await database()
        pool = await config.pool()
        try:
            tenant = resources()["tenant_schema"]
            await db.execute(
                f'UPDATE "{tenant}".human_command_delivery '
                "SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE command_id=$1",
                command.command_id,
            )
            observer = ObservedTransport(config.transport())
            assert await relay(PostgresHumanOutbox(scope=config.scope, pool=pool), observer).run_once()
            assert observer.events == ["GET.begin", "GET.committed"]
            assert observer.receipts == [receipt]
            assert (
                await db.fetchval(
                    "SELECT count(*) FROM mzo_human_receipt WHERE tenant_=$1 AND command_=$2",
                    tenant,
                    command.command_id,
                )
                == 1
            )
            assert (
                await db.fetchval(
                    f'SELECT status FROM "{tenant}".human_command_delivery WHERE command_id=$1',
                    command.command_id,
                )
                == "committed"
            )
        finally:
            await pool.close()
            await db.close()

    asyncio.run(reconcile())
    readiness()


@pytest.mark.parametrize("field", ["spki_sha256", "uri_san", "issuer_dn"])
def test_known_leaf_metadata_mismatch_has_exact_refusal_and_restoration(field: str) -> None:
    readiness()
    before = snapshot()
    original = json.loads((fixture() / "boundary.json").read_text())
    changed = copy.deepcopy(original)
    peer = next(p for p in changed["peers"] if p["engine_user"] == "d7-agent")
    peer[field] = (
        "0" * 64
        if field == "spki_sha256"
        else "CN=Wrong-Issuer"
        if field == "issuer_dn"
        else peer[field] + "/wrong"
    )
    if field in {"uri_san", "issuer_dn"}:
        identity_key = "subject" if field == "uri_san" else "issuer"
        peer["identity"][identity_key] = peer[field]
        # Identity and capability are pinned consistently; the mismatch under test
        # is the known actual leaf versus its explicitly wrong deployment metadata.
        for binding in peer["capabilities"]:
            binding["document"]["identity"][identity_key] = peer[field]
            from maezo.gateway.engine_contracts import canonical_json

            binding["digest"] = hashlib.sha256(canonical_json(binding["document"])).hexdigest()
    try:
        restart_owned_engine(changed)
        wait_ready("worker")
        with client() as connection:
            denied(connection.post("/engine-rest/maezo/v1/operations", json=operation_request()))
        assert snapshot() == before
    finally:
        restart_owned_engine(original)
        wait_ready()


def record_response(evidence: Path, label: str, response: httpx.Response) -> None:
    """Keep actual wire outcomes in the owned private fixture, separate by fault phase."""
    (evidence / (label + ".body")).write_bytes(response.content)
    (evidence / (label + ".json")).write_text(
        json.dumps(
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
                "headers": dict(response.headers),
                "body_bytes": len(response.content),
                "body_sha256": hashlib.sha256(response.content).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


def record_fault_attempt(evidence: Path, label: str, send) -> httpx.Response | None:
    """Retain failed transport attempts too; they never satisfy an HTTP outcome oracle."""
    try:
        response = send()
    except httpx.TransportError as error:
        (evidence / (label + "-transport-error.json")).write_text(
            json.dumps({"error_type": type(error).__name__, "accepted_as_unavailability": False}) + "\n"
        )
        return None
    record_response(evidence, label, response)
    return response


def assert_start_effect(request: dict, instance_id: str | None) -> None:
    """Observe the exact pending business key in native runtime AND committed history."""

    async def observe():
        db = await database()
        try:
            definition = json.loads((fixture() / "definitions.json").read_text())[request["process_key"]]
            expected = [] if instance_id is None else [instance_id]
            for table in ("act_ru_execution", "act_hi_procinst"):
                rows = await db.fetch(
                    f"SELECT id_, proc_def_id_, tenant_id_ FROM {table} WHERE business_key_=$1",
                    request["resource_ref"],
                )
                assert sorted(row["id_"] for row in rows) == expected
                for row in rows:
                    assert row["proc_def_id_"] == definition["id"]
                    assert row["tenant_id_"] == request["variables"]["tenant_id"]
            if instance_id is not None:
                assert (
                    await db.fetchval(
                        "SELECT count(*) FROM act_ru_ext_task "
                        "WHERE proc_inst_id_=$1 AND proc_def_id_=$2 AND tenant_id_=$3 "
                        "AND topic_name_='operadora.escalation.notify_team'",
                        instance_id,
                        definition["id"],
                        definition["tenantId"],
                    )
                    == 1
                )
        finally:
            await db.close()

    asyncio.run(observe())


def assert_started_response(response: httpx.Response, request: dict) -> str:
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["capability_digest"] == request["capability_digest"]
    instance_id = body["result"]["id"]
    assert isinstance(instance_id, str) and instance_id
    assert_start_effect(request, instance_id)
    return instance_id


def captured_docker(evidence: Path, label: str, argv: list[str]) -> bytes:
    """ROOT's reviewed image wrapper must authorize these child Docker reads too."""
    result = subprocess.run(argv, capture_output=True, timeout=10, check=False)
    (evidence / (label + ".argv.json")).write_text(json.dumps(argv) + "\n")
    (evidence / (label + ".stdout")).write_bytes(result.stdout)
    (evidence / (label + ".stderr")).write_bytes(result.stderr)
    (evidence / (label + ".exit")).write_text(str(result.returncode) + "\n")
    assert result.returncode == 0, "container observation failed; cannot infer startup refusal"
    return result.stdout + result.stderr if argv[1] == "logs" else result.stdout


def owned_engine_context(evidence: Path, label: str) -> dict:
    root = fixture()
    project = os.environ.get("MAEZO_D7_COMPOSE_PROJECT", "")
    assert re.fullmatch(r"d7-[a-z0-9-]{8,64}", project)
    container = (
        captured_docker(
            evidence,
            label + "-ps",
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(root / "secured-compose.json"),
                "ps",
                "-q",
                "engine",
            ],
        )
        .decode()
        .strip()
    )
    assert re.fullmatch(r"[0-9a-f]{64}", container), "exact single running engine required"
    # Deliberately excludes Config.Env and mounts: no private credentials in inspection.
    template = (
        '{"id":{{json .Id}},"image":{{json .Image}},"started_at":{{json .State.StartedAt}},'
        '"running":{{json .State.Running}},"restart_count":{{json .RestartCount}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
    )
    current = json.loads(
        captured_docker(evidence, label + "-inspect", ["docker", "inspect", "--format", template, container])
    )
    assert current["id"] == container and current["project"] == project and current["service"] == "engine"
    assert current["image"] == json.loads((root / "d7-fixture.json").read_text())["secured_image"]
    assert current["running"] is True and current["restart_count"] == 0
    assert re.fullmatch(r"20[0-9]{2}-[0-9TZ:.+-]+", current["started_at"])
    return current


def failed_engine_rest_context(logs: bytes) -> bool:
    """Exact pinned Tomcat context failure; a generic exception line is insufficient."""
    text = logs.decode("utf-8", errors="strict")
    return all(
        marker in text
        for marker in (
            "Exception starting filter [maezo-boundary]",
            "br.com.maezo.workload.Refused: engine_profile_unavailable",
            "StandardContext.startInternal Context [/engine-rest] startup failed due to previous errors",
            'Starting ProtocolHandler ["https-jsse-nio-8443"]',
        )
    )


def assert_failed_context_response(response: httpx.Response, logs: bytes) -> None:
    assert failed_engine_rest_context(logs), "current restarted /engine-rest context must have failed"
    # This is unavailable deployment evidence, never native authorization credit.
    # Unexpected 503, 405, timeout/reset or other outcomes fail for explicit diagnosis.
    assert response.status_code == 404
    assert response.headers["content-type"].split(";", 1)[0] == "text/html"
    assert "<title>HTTP Status 404 – Not Found</title>" in response.text


@pytest.mark.parametrize("dependency", ["ca-mount", "tenant", "environment"])
def test_missing_dependency_or_inconsistent_identity_prevents_startup(dependency: str) -> None:
    readiness()
    root = fixture()
    evidence = root / ("startup-" + dependency + "-" + uuid4().hex)
    evidence.mkdir(mode=0o700)
    original = json.loads((root / "boundary.json").read_text())
    changed = copy.deepcopy(original)
    saved_compose = (root / "secured-compose.json").read_bytes()
    pending = operation_request()
    pending_wire = json.dumps(pending, separators=(",", ":"), allow_nan=False).encode()
    positive = {**pending, "resource_ref": "d7-http-" + uuid4().hex}
    assert_start_effect(pending, None)
    assert_start_effect(positive, None)
    with client() as connection:
        ready = connection.get("/engine-rest/maezo/v1/readiness")
        record_response(evidence, "before-readiness", ready)
        assert ready.status_code == 200 and ready.json()["ready"] is True and ready.json()["capabilities"]
        response = connection.post("/engine-rest/maezo/v1/operations", json=positive)
        record_response(evidence, "before-operation", response)
        assert_started_response(response, positive)
    readiness()
    before = snapshot()
    baseline_context = owned_engine_context(evidence, "before")
    (evidence / "pending.json").write_text(
        json.dumps(
            {
                "wire_sha256": hashlib.sha256(pending_wire).hexdigest(),
                "peer_certificate_sha256": hashlib.sha256((root / "agent.crt").read_bytes()).hexdigest(),
                "snapshot": before,
            },
            indent=2,
        )
        + "\n"
    )
    if dependency == "ca-mount":
        compose = json.loads(saved_compose)
        compose["services"]["engine"]["volumes"] = [
            v for v in compose["services"]["engine"]["volumes"] if v.get("target") != "/run/maezo/ca.crt"
        ]
        (root / "secured-compose.json").write_text(json.dumps(compose))
    else:
        changed[dependency] = "foreign-tenant" if dependency == "tenant" else "foreign-environment"
    try:
        restart_owned_engine(changed)
        fault_context = owned_engine_context(evidence, "fault")
        assert fault_context["id"] != baseline_context["id"]
        assert fault_context["started_at"] > baseline_context["started_at"]
        argv = ["docker", "logs", "--timestamps", "--since", fault_context["started_at"], fault_context["id"]]
        deadline = time.monotonic() + 90
        logs = b""
        attempt = 0
        while time.monotonic() < deadline and not failed_engine_rest_context(logs):
            logs = captured_docker(evidence, f"fault-logs-{attempt:03d}", argv)
            attempt += 1
            if not failed_engine_rest_context(logs):
                time.sleep(0.25)
        # Attempt BOTH live paths even if the expected startup/log outcome differs.
        # Transport failures are retained separately and then fail, never pass by fallback.
        with client() as connection:
            fault_readiness = record_fault_attempt(
                evidence,
                "fault-readiness",
                lambda: connection.get("/engine-rest/maezo/v1/readiness"),
            )
            fault_operation = record_fault_attempt(
                evidence,
                "fault-operation",
                lambda: connection.post(
                    "/engine-rest/maezo/v1/operations",
                    content=pending_wire,
                    headers={"Content-Type": "application/json"},
                ),
            )
        fault_snapshot = snapshot()
        (evidence / "fault-snapshot.json").write_text(json.dumps(fault_snapshot, indent=2) + "\n")
        assert owned_engine_context(evidence, "fault-after-https") == fault_context
        assert fault_readiness is not None and fault_operation is not None, "faulted HTTPS transport failed"
        assert_failed_context_response(fault_readiness, logs)
        assert_failed_context_response(fault_operation, logs)
        assert fault_snapshot == before
        assert_start_effect(pending, None)
        with pytest.raises(ConnectionRefusedError), socket.create_connection(("127.0.0.1", 18080), timeout=3):
            pytest.fail("failed secured startup cannot reopen plaintext")
    finally:
        (root / "secured-compose.json").write_bytes(saved_compose)
        restart_owned_engine(original)
        wait_ready()
    readiness()
    restored_context = owned_engine_context(evidence, "restored")
    assert restored_context["id"] != fault_context["id"]
    assert restored_context["started_at"] > fault_context["started_at"]
    assert snapshot() == before
    assert_start_effect(pending, None)
    with client() as connection:
        ready = connection.get("/engine-rest/maezo/v1/readiness")
        record_response(evidence, "restored-readiness", ready)
        assert ready.status_code == 200 and ready.json()["ready"] is True and ready.json()["capabilities"]
        response = connection.post(
            "/engine-rest/maezo/v1/operations",
            content=pending_wire,
            headers={"Content-Type": "application/json"},
        )
        record_response(evidence, "restored-operation", response)
        assert_started_response(response, pending)
    assert snapshot() != before
    (evidence / "restored-snapshot.json").write_text(json.dumps(snapshot(), indent=2) + "\n")
    readiness()


def operation_wire_boundary(request: dict) -> tuple[bytes, bytes]:
    """D7 Json.LIMIT, separate from signed-human JCS limits; pad only JSON whitespace."""
    limit = 1_048_576
    encoded = json.dumps(request, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert len(encoded) <= limit
    within = encoded + b" " * (limit - len(encoded))
    oversized = within + b" "
    assert len(within) == limit and len(oversized) == limit + 1
    assert json.loads(within) == json.loads(oversized) == request
    return within, oversized


def test_valid_operation_at_actual_byte_limit_executes_and_one_byte_over_has_no_effect() -> None:
    readiness()
    evidence = fixture() / ("body-limit-" + uuid4().hex)
    evidence.mkdir(mode=0o700)
    pending = operation_request()
    assert_start_effect(pending, None)
    within, oversized = operation_wire_boundary(pending)
    (evidence / "wire-pair.json").write_text(
        json.dumps(
            {
                "within": {"bytes": len(within), "sha256": hashlib.sha256(within).hexdigest()},
                "oversized": {"bytes": len(oversized), "sha256": hashlib.sha256(oversized).hexdigest()},
                "only_difference": "one additional trailing ASCII space",
                "peer_certificate_sha256": hashlib.sha256((fixture() / "agent.crt").read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    # Independent, same-capability operation first proves the live identity/route.
    positive = {**pending, "resource_ref": "d7-http-" + uuid4().hex}
    assert_start_effect(positive, None)
    with client() as connection:
        response = connection.post("/engine-rest/maezo/v1/operations", json=positive)
        record_response(evidence, "before-operation", response)
        assert_started_response(response, positive)
        readiness()
        before = snapshot()
        (evidence / "before-snapshot.json").write_text(json.dumps(before, indent=2) + "\n")
        response = connection.post(
            "/engine-rest/maezo/v1/operations",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        assert response.request.headers["Content-Length"] == str(1_048_577)
        assert "transfer-encoding" not in response.request.headers
        record_response(evidence, "oversized-operation", response)
        denied(response, "engine_invalid_body", 400)
        assert snapshot() == before
        assert_start_effect(pending, None)
        (evidence / "fault-snapshot.json").write_text(json.dumps(snapshot(), indent=2) + "\n")
        readiness()
        # Exact same operation/identity; removing one trailing byte is the only change.
        response = connection.post(
            "/engine-rest/maezo/v1/operations",
            content=within,
            headers={"Content-Type": "application/json"},
        )
        assert response.request.headers["Content-Length"] == str(1_048_576)
        assert "transfer-encoding" not in response.request.headers
        record_response(evidence, "within-limit-operation", response)
        assert_started_response(response, pending)
        assert snapshot() != before
        (evidence / "after-snapshot.json").write_text(json.dumps(snapshot(), indent=2) + "\n")
    readiness()


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"outer":{"k":1,"k":2}}',
        b'{"x":"\xff"}',
        b'{"x":"\\ud800"}',
        b'{"x":1e9999}',
        b'{"x":Infinity}',
        b'{"x":' + b"[" * 128 + b"0" + b"]" * 128 + b"}",
        b'{"x":"' + b"x" * 131073 + b'"}',
    ],
    ids=["array", "nested-duplicate", "utf8", "surrogate", "overflow", "infinity", "depth", "size"],
)
def test_malformed_json_fails_with_exact_body_error(body: bytes) -> None:
    readiness()
    before = snapshot()
    with client() as connection:
        denied(
            connection.post(
                "/engine-rest/maezo/v1/operations", content=body, headers={"Content-Type": "application/json"}
            ),
            "engine_invalid_body",
            400,
        )
    assert snapshot() == before
    readiness()


@pytest.mark.parametrize(
    "path", ["/manager/html", "/manager/text/serverinfo", "/host-manager/html", "/host-manager/text/list"]
)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"])
def test_container_admin_realm_presence_has_separate_exact_oracle(path: str, method: str) -> None:
    readiness()
    before = snapshot()
    project = os.environ.get("MAEZO_D7_COMPOSE_PROJECT", "")
    assert re.fullmatch(r"d7-[a-z0-9-]{8,64}", project)
    app = path.split("/")[1]
    assert app in {"manager", "host-manager"}
    # Read actual deployment presence. A missing app earns absence coverage only.
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(fixture() / "secured-compose.json"),
        "exec",
        "-T",
        "engine",
        "sh",
        "-c",
        f"test -e /camunda/webapps/{app} -o -e /camunda/webapps/{app}.war",
    ]
    presence = subprocess.run(command, capture_output=True, check=False, timeout=10)
    assert presence.returncode in {0, 1}, "container inventory failure is not app absence"
    with client("worker") as connection:
        response = connection.request(method, path)
        if presence.returncode == 0:
            if method == "HEAD":
                denied(connection.get(path))
                assert response.status_code == 403 and response.content == b""
            else:
                denied(response)
        else:
            assert response.status_code == 404, "explicit absent-app oracle; no native policy credit"
            if method == "HEAD":
                assert response.content == b""
            else:
                assert response.headers["content-type"].startswith("text/html")
                assert b"404" in response.content
    assert snapshot() == before
    readiness()
