"""Stage1 representation evidence only: no DB, AWS, native or authority claim."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from maezo.platform.engine_bootstrap import controller_contracts as c

PACKAGE = json.loads((Path(__file__).parents[4] / "spec/provisioning/d7d-stage1-v1.json").read_bytes())
SAMPLES = {v["type"]: v["value"] for v in PACKAGE["structural_vectors"]["positive"]}
H = "a" * 64
U = "00000000-0000-4000-8000-000000000001"
TASK = "arn:aws:ecs:sa-east-1:123456789012:task/task-one"
CLUSTER = "arn:aws:ecs:sa-east-1:123456789012:cluster/cluster-one"


def sample(name: str) -> Any:
    return copy.deepcopy(SAMPLES[name])


def raw(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(raw(value)).hexdigest()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def parsed(name: str, value: Any) -> Any:
    return c.parse(name, raw(value))


def task_binding() -> dict[str, Any]:
    v = sample("TaskDefinitionBinding")
    v["arn"] = "arn:aws:ecs:sa-east-1:123456789012:task-definition/engine:7"
    v["revision"] = 7
    return v


def core(purpose: str = "runtime") -> dict[str, Any]:
    v = sample("GenerationCore")
    v["purpose"] = purpose
    v["task_definition_revision"] = task_binding()
    v["immutable_secret_resource_version"] = {
        "resource_arn": "arn:aws:secretsmanager:sa-east-1:123456789012:secret:owned-one-AbCdEf",
        "version_id": U,
    }
    return v


def receipt_ref(operation: str) -> dict[str, Any]:
    v = sample("ReceiptRef")
    v["receipt_key"]["operation"] = operation
    return v


def decision(purpose: str = "runtime") -> dict[str, Any]:
    v = sample("ActivationDecision" if purpose == "runtime" else "CandidateDecision")
    v["generation_core"] = core(purpose)
    v["deployment_receipt"] = receipt_ref("deploy")
    v["grant_receipt"] = receipt_ref("grant")
    v["topology"]["cluster_arn"] = CLUSTER
    return v


def generation(purpose: str = "runtime", status: str = "OPEN") -> dict[str, Any]:
    return {
        "binding": core(purpose),
        "activation_decision_sha256": sha(decision(purpose)),
        "status": status,
        "revision": 1,
    }


def issuer(purpose: str = "runtime", status: str = "OPEN") -> dict[str, Any]:
    v = sample("IssuerReceipt")
    v.update(
        purpose=purpose,
        status=status,
        binding_sha256=sha(core(purpose)),
        activation_decision_sha256=sha(decision(purpose)),
    )
    return v


def database_observation(purpose: str = "runtime", status: str = "OPEN") -> dict[str, Any]:
    v = sample("DatabaseObservation")
    v["payload"]["generations"] = [generation(purpose, status)]
    role = sample("RoleObservation")
    role.update(
        oid=core(purpose)["login_oid"],
        name=core(purpose)["login_name"],
        owned=True,
        generation_id=1,
        can_login=status == "OPEN",
    )
    v["payload"]["roles"] = [role]
    v["payload"]["fence"]["state"] = "OPEN" if status == "OPEN" else "CLOSED"
    v["observed_revision"] = v["payload"]["fence"]["revision"]
    return v


def control_observation() -> dict[str, Any]:
    v = sample("ControlObservation")
    p = v["payload"]
    p.update(
        controller_state="ACTIVE",
        task_definitions=[task_binding()],
        cluster_arn=CLUSTER,
        desired_count=1,
        running_count=1,
        pending_count=0,
    )
    task = sample("TaskObservation")
    task.update(
        task_arn=TASK,
        task_definition=task_binding(),
        standalone=True,
        task_set_arn=None,
        last_status="RUNNING",
        desired_status="RUNNING",
        generation_id=1,
    )
    managed = sample("ManagedResource")
    managed.update(
        provider_resource_id=TASK,
        task_definition_revision=task_binding(),
        desired_state="READY",
        current_observed_state="READY",
        generation_id=1,
        retired_terminal_proof_sha256=None,
    )
    p.update(tasks=[task], managed_resources=[managed])
    return v


def activation() -> dict[str, Any]:
    b = sample("RuntimeActivationBinding")
    b["deployment_receipt_key"]["operation"] = "deploy"
    b["grant_receipt_key"]["operation"] = "grant"
    b["caller_identity"]["subject"] = b["caller_certificate_binding"]["uri_san"]
    b["activation_decision_sha256"] = sha(decision())
    b["task_definition_binding_sha256"] = sha(task_binding())
    b["issuer_restore_receipt_sha256"] = sha(issuer())
    return {
        "kind": "ACTIVE",
        "runtime_activation_binding": b,
        "controller_observation": control_observation(),
        "database_admission_observation": database_observation(),
    }


def grant_payload() -> dict[str, Any]:
    vector = PACKAGE["compiler_parity_vector"]
    boundary = vector["boundary"]
    v = sample("GrantPayload")
    v["deployment_receipt"] = receipt_ref("deploy")
    v["compiler_plan"] = copy.deepcopy(vector["plan"])
    for k in ("tenant", "environment", "engine_name"):
        v["deployment_receipt"]["receipt_key"][k] = boundary[k]
    v["compiler_plan_sha256"] = vector["plan_sha256"]
    v["final_boundary_manifest"] = {"sha256": vector["boundary_sha256"], "byte_length": len(raw(boundary))}
    workloads = {}
    for peer in boundary["peers"]:
        if peer["purpose"] != "nonhuman":
            continue
        caps = []
        for cap in peer["capabilities"]:
            doc = cap["document"]
            caps.append(
                {
                    "capability_sha256": cap["digest"],
                    "registered_schema_id": doc["schema"]["schema_id"],
                    "registered_schema_sha256": sha(doc["schema"]),
                    "target": doc["target"],
                    "worker_id": doc["worker_id"],
                    "source": {
                        "kind": cap["source_kind"] or "none",
                        "source_target": doc["source_target"],
                        "source_worker_id": cap["source_worker_id"],
                        "attestations": cap["attestations"],
                    },
                }
            )
        workloads[peer["engine_user"]] = {
            "engine_user": peer["engine_user"],
            "identity": peer["identity"],
            "capabilities": caps,
        }
    v["workload_plans"] = list(workloads.values())
    return v


def native_pair(operation: str) -> tuple[dict[str, Any], dict[str, Any]]:
    req = sample("DeployRequest" if operation == "deploy" else "GrantRequest")
    body = sample("DeployResultBody" if operation == "deploy" else "GrantResultBody")
    if operation == "deploy":
        resource = req["payload"]["resources"][0]
        resource["name"] = "release/main.bpmn"
        body["resources"][0].update({k: resource[k] for k in ("name", "kind", "sha256", "byte_length")})
        body["definitions"][0].update(resource_name=resource["name"])
    else:
        req["payload"] = grant_payload()
        for key in ("tenant", "environment", "engine_name"):
            req["scope"][key] = req["payload"]["compiler_plan"][key]
        body["deployment_receipt"] = req["payload"]["deployment_receipt"]
        body["boundary_sha256"] = req["payload"]["final_boundary_manifest"]["sha256"]
        body["compiler_plan_sha256"] = req["payload"]["compiler_plan_sha256"]
        body["identities"] = [
            {"engine_user": w["engine_user"], "identity_sha256": sha(w["identity"]), "disposition": "CREATED"}
            for w in req["payload"]["workload_plans"]
        ]
        body["grants"] = [
            {"authorization_id": f"authorization-{i}", "grant": row, "disposition": "CREATED"}
            for i, row in enumerate(req["payload"]["compiler_plan"]["grants"])
        ]
    receipt = sample("NativeReceipt")
    receipt.update(
        scope=copy.deepcopy(req["scope"]),
        request_sha256=sha(req),
        result_sha256=sha(body),
        canonical_result_bytes=b64(raw(body)),
    )
    receipt["receipt_key"].update({k: req["scope"][k] for k in ("tenant", "environment", "engine_name")})
    receipt["receipt_key"]["operation"] = operation
    receipt["native_actor"].update(task_definition_revision=task_binding()["arn"], purpose=operation)
    return req, receipt


def intent(action: str = "RunTask") -> dict[str, Any]:
    v = sample(action + "Intent")
    v["task_definition_revision"] = task_binding()
    if action == "RunTask":
        v["target_arn"] = CLUSTER
        v["request"].update(
            cluster=CLUSTER,
            taskDefinition=task_binding()["arn"],
            startedBy=v["logical_run_id"],
            clientToken=v["client_token"],
        )
        n = v["network_binding"]
        v["request"]["networkConfiguration"]["awsvpcConfiguration"] = {
            "subnets": n["subnet_ids"],
            "securityGroups": n["security_group_ids"],
            "assignPublicIp": n["assign_public_ip"],
        }
    else:
        v["target_arn"] = TASK
        v["request"].update(cluster=CLUSTER, task=TASK)
    v.update(request_bytes=b64(raw(v["request"])), request_sha256=sha(v["request"]))
    return v


def journal(state: str) -> dict[str, Any]:
    v = sample("JournalObservation")
    v.update(
        state=state, reservation=None, provider_outcome=None, settlement=None, cancellation_unsent_proof=None
    )
    if state in {"RESERVED", "DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING", "SETTLED"}:
        v["reservation"] = sample("DispatcherReservation")
    if state in {"ACKNOWLEDGED", "SETTLED", "REJECTED"}:
        v["provider_outcome"] = sample("ProviderOutcome")
    if state == "REJECTED":
        v["provider_outcome"]["failures"] = [sample("ProviderFailure")]
    if state == "SETTLED":
        v["settlement"] = sample("Settlement")
        v["settlement"].update(
            settlement_class="NO_EFFECT", resource_proofs=[sample("ResourceTerminalProof")]
        )
        v["settlement"]["resource_proofs"][0]["terminal_kind"] = "NO_EFFECT"
    if state == "CANCELLED_UNSENT":
        v["cancellation_unsent_proof"] = sample("DispatchTerminalProof")
        v["cancellation_unsent_proof"]["status"] = "CANCELLED_BEFORE_SEND"
    return v


def safe_file(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "input.json"
    path.write_bytes(data)
    path.chmod(0o600)
    return path.resolve()


def read(path: Path, data: bytes, **kwargs: Any) -> c.FileSnapshot:
    return c.read_protected_file(
        str(path),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_owner=os.getuid(),
        sensitivity="private",
        **kwargs,
    )


@pytest.mark.parametrize("v", PACKAGE["wire_vectors"]["positive"])
def test_canonical_wire_reference_vectors(v: dict[str, Any]) -> None:
    data = c.decode_base64(v["raw_b64"])
    assert c.canonical_json(c.decode_json(data)) == data
    assert hashlib.sha256(data).hexdigest() == v["sha256"]


@pytest.mark.parametrize("v", PACKAGE["wire_vectors"]["negative"])
def test_reject_wire_reference_vectors(v: dict[str, Any]) -> None:
    with pytest.raises(c.ContractError, match="^INVALID_BODY$"):
        c.decode_json(c.decode_base64(v["raw_b64"]))


@pytest.mark.parametrize(
    "data",
    [
        b"-0",
        b"1.0",
        b"1e0",
        b"01",
        b"-1",
        b"9007199254740992",
        b"NaN",
        b"Infinity",
        b"1 2",
        b"\xef\xbb\xbf1",
        b'"\\ud800"',
        b'"\\udfff"',
        b'{"a":1,"a":2}',
        b'{"a":1, "b":2}',
        b'"\\u00e9"',
        b'"\\/"',
        b'"\xff"',
        b"9" * 10000,
        b"[" * 10000 + b"]" * 10000,
    ],
)
def test_invalid_wire_lexical_domains(data: bytes) -> None:
    with pytest.raises(c.ContractError):
        c.decode_json(data)


@pytest.mark.parametrize(
    "name,value",
    [
        ("Positive", True),
        ("UInt", False),
        ("UInt", 1.0),
        ("Limits", {**sample("Limits"), "heartbeat_ms": 10000.0}),
        ("RunTaskProviderRequest", {**sample("RunTaskProviderRequest"), "enableExecuteCommand": 0}),
        ("Id", "é" * 128),
        ("Text", "é" * 128),
        ("Id", "x\u00a0y"),
        ("Id", "x\x7fy"),
        ("Text", "x\x9fy"),
    ],
)
def test_no_coercion_or_utf8_length_shortcut(name: str, value: Any) -> None:
    with pytest.raises(c.ContractError):
        parsed(name, value)


def test_depth_size_array_and_unicode_boundaries() -> None:
    assert parsed("Id", "é" * 127 + "x") == "é" * 127 + "x"
    assert c.decode_json(raw("e\u0301")) != c.decode_json(raw("é"))
    assert parsed("UInt", 9007199254740991) == 9007199254740991
    assert parsed("Oid", 4294967295) == 4294967295
    for name, value in (("Oid", 4294967296), ("Port", 65536)):
        with pytest.raises(c.ContractError):
            parsed(name, value)
    for value in ([0] * 1025, {"a": "x" * c.MAX_BYTES}):
        with pytest.raises(c.ContractError):
            c.decode_json(raw(value))
    assert c.decode_json(b"[" * 32 + b"0" + b"]" * 32)
    with pytest.raises(c.ContractError):
        c.decode_json(b"[" * 33 + b"0" + b"]" * 33)
    data = b'"' + b"x" * (c.MAX_BYTES - 2) + b'"'
    assert len(c.decode_json(data)) == c.MAX_BYTES - 2
    with pytest.raises(c.ContractError):
        c.decode_json(data + b" ")


@pytest.mark.parametrize("name,size", [("Challenge", 32), ("PublicKey", 32), ("Signature", 64)])
def test_canonical_base64_bounds_and_tail_bits(name: str, size: int) -> None:
    canonical = b64(bytes(size))
    assert parsed(name, canonical) == canonical
    for malformed in (
        canonical + "=",
        canonical[:-1] + "B",
        canonical + "A",
        canonical[:-1],
        "+" + canonical[1:],
    ):
        with pytest.raises(c.ContractError):
            parsed(name, malformed)


@pytest.mark.parametrize("v", PACKAGE["structural_vectors"]["positive"], ids=lambda v: v["type"])
def test_entire_reviewed_closed_shape_catalogue(v: dict[str, Any]) -> None:
    # These fixtures are expressly structural only, often relationally inconsistent.
    # Test the independent structural checker without calling them positive authorities.
    c._shape({"$ref": "#/$defs/" + v["type"]}, v["value"])
    assert c.canonical_json(v["value"]) == c.decode_base64(v["canonical_utf8_b64"])
    assert sha(v["value"]) == v["canonical_sha256"]


@pytest.mark.parametrize(
    "name", [n for n, s in PACKAGE["schema"]["$defs"].items() if s.get("type") == "object"]
)
def test_every_record_rejects_unknown_and_missing_fields(name: str) -> None:
    value = sample(name)
    value["unreviewed"] = "no authority"
    with pytest.raises(c.ContractError):
        parsed(name, value)
    value = sample(name)
    del value[next(iter(value))]
    with pytest.raises(c.ContractError):
        parsed(name, value)


def test_shared_catalogue_is_exact_and_classes_cover_all_objects() -> None:
    assert PACKAGE["schema"]["$defs"] == c._DEFINITIONS
    assert len(c._DEFINITIONS) == 133
    for name, schema in c._DEFINITIONS.items():
        if schema.get("type") == "object":
            assert [f.name for f in fields(getattr(c, name))] == schema["required"]


def test_record_constructor_and_snapshots_are_deeply_immutable() -> None:
    document = core()
    record = c.GenerationCore(**document)
    document["network_binding"]["subnet_ids"].append("changed")
    assert record.network_binding.subnet_ids == ("synthetic",)
    returned = record.to_wire()
    returned["network_binding"]["subnet_ids"].append("changed-again")
    assert record.to_wire() != returned
    with pytest.raises(FrozenInstanceError):
        record.login_oid = 99
    with pytest.raises(FrozenInstanceError):
        record.network_binding.vpc_id = "other"
    assert c.parse_record(c.GenerationCore, record.canonical_bytes()) == record
    assert record.unestablished_obligations == c.DEFERRED_OBLIGATIONS
    assert not hasattr(record, "verified")
    with pytest.raises(c.ContractError):
        c.GenerationCore(**{**core(), "login_oid": True})


@pytest.mark.parametrize("operation", ["deploy", "grant"])
def test_native_payload_and_receipt_positive_and_stored_replay_bytes(operation: str) -> None:
    request, receipt = native_pair(operation)
    q = parsed("NativeRequest", request)
    r = parsed("NativeReceipt", receipt)
    c.check_native_receipt(q, r)
    assert r.commit_epoch == 1
    # A historical permit/epoch is retained; no current retry credential is an input.
    c.check_native_receipt(q, replace(r, commit_epoch=7, permit_id="00000000-0000-4000-8000-000000000002"))
    for union, kinds in (("NativeResult", ("COMMITTED", "REPLAY")), ("ReceiptRead", ("FOUND",))):
        for kind in kinds:
            reply = {
                k: receipt[k]
                for k in ("receipt_key", "request_sha256", "canonical_result_bytes", "result_sha256")
            }
            reply["kind"] = kind
            actual = parsed(union, reply)
            assert actual.canonical_result_bytes == receipt["canonical_result_bytes"]
            assert c.decode_base64(actual.canonical_result_bytes) == c.decode_base64(r.canonical_result_bytes)


@pytest.mark.parametrize("operation", ["deploy", "grant"])
@pytest.mark.parametrize(
    "field", ["request_sha256", "source", "tree", "image_sha256", "jar_sha256", "trust_profile_sha256"]
)
def test_receipt_provenance_mismatch(operation: str, field: str) -> None:
    request, receipt = native_pair(operation)
    receipt[field] = "b" * len(receipt[field])
    with pytest.raises(c.ContractError):
        c.check_native_receipt(parsed("NativeRequest", request), parsed("NativeReceipt", receipt))


@pytest.mark.parametrize("mutation", ["digest", "operation", "tenant", "noncanonical", "unknown-body"])
def test_embedded_result_is_exact_closed_canonical(mutation: str) -> None:
    _, receipt = native_pair("deploy")
    body = json.loads(c.decode_base64(receipt["canonical_result_bytes"]))
    if mutation == "digest":
        receipt["result_sha256"] = "b" * 64
    elif mutation == "operation":
        receipt["receipt_key"]["operation"] = "grant"
    elif mutation == "tenant":
        body["tenant"] = "other"
    elif mutation == "unknown-body":
        body["admin"] = True
    if mutation in {"tenant", "unknown-body", "noncanonical"}:
        data = raw(body) + (b" " if mutation == "noncanonical" else b"")
        receipt.update(canonical_result_bytes=b64(data), result_sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(c.ContractError):
        parsed("NativeReceipt", receipt)


@pytest.mark.parametrize(
    "path", ["/absolute.bpmn", "../bad.bpmn", "a/../bad.bpmn", "a/./bad.bpmn", "a//bad.bpmn", "bad.dmn"]
)
def test_resource_names_are_safe_and_match_kind(path: str) -> None:
    request, _ = native_pair("deploy")
    request["payload"]["resources"][0]["name"] = path
    with pytest.raises(c.ContractError):
        parsed("DeployRequest", request)


@pytest.mark.parametrize(
    "mutation", ["missing-definition", "missing-resource", "wrong-resource", "wrong-hash"]
)
def test_deploy_exact_result_coverage(mutation: str) -> None:
    request, receipt = native_pair("deploy")
    body = json.loads(c.decode_base64(receipt["canonical_result_bytes"]))
    if mutation == "missing-definition":
        body["definitions"] = []
    elif mutation == "missing-resource":
        body["resources"] = []
    elif mutation == "wrong-resource":
        body["definitions"][0]["resource_name"] = "other.bpmn"
    else:
        body["resources"][0]["sha256"] = "b" * 64
    receipt.update(canonical_result_bytes=b64(raw(body)), result_sha256=sha(body))
    with pytest.raises(c.ContractError):
        c.check_native_receipt(parsed("NativeRequest", request), parsed("NativeReceipt", receipt))


@pytest.mark.parametrize("purpose", ["candidate", "runtime"])
@pytest.mark.parametrize("status", ["PREPARED", "OPEN", "RETIRED"])
def test_generation_purposes_hashes_and_status_observations(purpose: str, status: str) -> None:
    d = parsed("CandidateDecision" if purpose == "candidate" else "ActivationDecision", decision(purpose))
    g = parsed("RuntimeAdmissionGeneration", generation(purpose, status))
    c.check_generation_decision(g, d)
    assert g.binding.digest() != d.digest()
    assert replace(g, revision=2).binding == g.binding
    assert replace(g, revision=2).activation_decision_sha256 == d.digest()
    obs = database_observation(purpose, status)
    readback = sample("GenerationObserved")
    readback.update(
        status=status,
        binding_sha256=sha(core(purpose)),
        issuer_receipt_sha256=sha(issuer(purpose, status)),
        signed_observation=obs,
    )
    r = parsed("GenerationRead", readback)
    c.check_generation_read(r, parsed("IssuerReceipt", issuer(purpose, status)))
    assert r.status == status  # This is a parsed observation, never a transition test.


@pytest.mark.parametrize(
    "mutation", ["purpose", "digest", "core", "revision-arn", "oid-bool", "secret-alias"]
)
def test_generation_substitution_refused(mutation: str) -> None:
    g, d = generation(), decision()
    if mutation == "purpose":
        g["binding"]["purpose"] = "candidate"
    elif mutation == "digest":
        g["activation_decision_sha256"] = "b" * 64
    elif mutation == "core":
        g["binding"]["login_oid"] = 2
    elif mutation == "revision-arn":
        g["binding"]["task_definition_revision"]["revision"] = 8
    elif mutation == "oid-bool":
        g["binding"]["login_oid"] = True
    else:
        g["binding"]["immutable_secret_resource_version"]["version_id"] = "AWSCURRENT"
    with pytest.raises(c.ContractError):
        c.check_generation_decision(parsed("RuntimeAdmissionGeneration", g), parsed("ActivationDecision", d))


@pytest.mark.parametrize("state", c._DEFINITIONS["JournalState"]["enum"])
def test_all_journal_nullability_states(state: str) -> None:
    v = journal(state)
    r = parsed("JournalObservation", v)
    assert r.state == state
    assert r.unestablished_obligations  # No state sequence or authority discharge.


@pytest.mark.parametrize(
    "state,field,value",
    [
        ("INTENT", "reservation", sample("DispatcherReservation")),
        ("RESERVED", "provider_outcome", sample("ProviderOutcome")),
        ("RESERVED", "reservation", None),
        ("ACKNOWLEDGED", "provider_outcome", None),
        ("DISPATCHED_UNKNOWN", "settlement", sample("Settlement")),
        ("RECONCILING", "reservation", None),
        ("SETTLED", "reservation", None),
        ("SETTLED", "settlement", None),
        ("REJECTED", "provider_outcome", None),
        ("CANCELLED_UNSENT", "provider_outcome", sample("ProviderOutcome")),
        ("CANCELLED_UNSENT", "cancellation_unsent_proof", None),
    ],
)
def test_journal_invalid_nullability(state: str, field: str, value: Any) -> None:
    v = journal(state)
    v[field] = value
    with pytest.raises(c.ContractError):
        parsed("JournalObservation", v)


def test_absent_outcome_needs_positive_no_effect_proof() -> None:
    v = journal("SETTLED")
    v["provider_outcome"] = None
    assert parsed("JournalObservation", v).provider_outcome is None
    v["settlement"]["resource_proofs"] = []
    with pytest.raises(c.ContractError):
        parsed("JournalObservation", v)
    not_found = parsed("ReceiptRead", {"kind": "NOT_FOUND", "receipt_key": sample("ReceiptKey")})
    assert not hasattr(not_found, "no_effect")


@pytest.mark.parametrize("action", ["RunTask", "StopTask"])
def test_two_closed_unqualified_intent_grammars(action: str) -> None:
    r = parsed("AdmittedExternalIntent", intent(action))
    assert c.decode_base64(r.request_bytes) == r.request.canonical_bytes()
    assert r.unestablished_obligations
    assert not hasattr(r, "dispatch")
    if action == "StopTask":
        assert r.client_token is None


@pytest.mark.parametrize(
    "mutation",
    [
        "token",
        "startedBy",
        "taskDefinition",
        "network",
        "unknown",
        "mutable-platform",
        "request-bytes",
        "generation",
    ],
)
def test_intent_duplicate_binding_mutations(mutation: str) -> None:
    v = intent()
    if mutation == "token":
        v["client_token"] = "other"
    elif mutation in {"startedBy", "taskDefinition"}:
        v["request"][mutation] = U[:-1] + "2" if mutation == "startedBy" else task_binding()["arn"][:-1] + "8"
    elif mutation == "network":
        v["request"]["networkConfiguration"]["awsvpcConfiguration"]["subnets"] = ["other"]
    elif mutation == "unknown":
        v["request"]["overrides"] = {}
    elif mutation == "mutable-platform":
        v["request"]["platformVersion"] = "LATEST"
    elif mutation == "generation":
        v["target_generation"] = 2
    if mutation == "request-bytes":
        v["request_bytes"] = b64(raw(v["request"]) + b" ")
    else:
        v.update(request_bytes=b64(raw(v["request"])), request_sha256=sha(v["request"]))
    with pytest.raises(c.ContractError):
        parsed("AdmittedExternalIntent", v)


@pytest.mark.parametrize(
    "union,kinds",
    [
        ("NativeResult", ["REFUSED"]),
        ("ReceiptRead", ["REFUSED", "NOT_FOUND"]),
        ("ExternalOutcome", ["ACKNOWLEDGED", "PENDING", "SETTLED", "REFUSED"]),
        ("GenerationRead", ["REFUSED"]),
        ("ActivationRead", ["UNAVAILABLE"]),
    ],
)
def test_all_five_response_unions(union: str, kinds: list[str]) -> None:
    names = {
        "ACKNOWLEDGED": "ExternalAcknowledged",
        "PENDING": "ExternalPending",
        "SETTLED": "ExternalSettled",
        "REFUSED": "Refused",
        "NOT_FOUND": "ReceiptNotFound",
        "UNAVAILABLE": "ActivationUnavailable",
    }
    for kind in kinds:
        assert parsed(union, sample(names[kind])).kind == kind
    with pytest.raises(c.ContractError):
        parsed(union, {"kind": "UNREVIEWED", "verified": True})


def test_unsupported_action_stays_unknown_and_has_no_dispatch_cast() -> None:
    value = sample("UnsupportedObservedIntent")
    observed = parsed("UnsupportedObservedIntent", value)
    assert observed.state == "DISPATCHED_UNKNOWN"
    with pytest.raises(c.ContractError):
        parsed("AdmittedExternalIntent", value)


def test_active_read_requires_matching_decision_and_all_23_context_fields() -> None:
    a = parsed("ActivationRead", activation())
    assert type(a) is c.ActivationActive
    assert len(fields(c.RuntimeActivationBinding)) == 23
    c.check_activation_read(
        a,
        current_binding=a.runtime_activation_binding,
        decision=parsed("ActivationDecision", decision()),
        generation=parsed("RuntimeAdmissionGeneration", generation()),
        issuer=parsed("IssuerReceipt", issuer()),
    )
    assert not hasattr(a, "authority") and a.unestablished_obligations
    with pytest.raises(c.ContractError):
        c.check_activation_read(
            a,
            current_binding=replace(a.runtime_activation_binding, readiness_result_sha256="b" * 64),
            decision=parsed("ActivationDecision", decision()),
            generation=parsed("RuntimeAdmissionGeneration", generation()),
            issuer=parsed("IssuerReceipt", issuer()),
        )


@pytest.mark.parametrize(
    "mutation", ["candidate", "retired", "state", "unknown", "no-ready", "old-live", "db-scope", "epoch"]
)
def test_active_cannot_be_minted_by_shape(mutation: str) -> None:
    v = activation()
    if mutation == "candidate":
        v["database_admission_observation"] = database_observation("candidate")
    elif mutation == "retired":
        v["database_admission_observation"] = database_observation(status="RETIRED")
    elif mutation == "state":
        v["controller_observation"]["payload"]["controller_state"] = "RESTORED"
    elif mutation == "unknown":
        v["controller_observation"]["payload"]["journal"] = [journal("DISPATCHED_UNKNOWN")]
    elif mutation == "no-ready":
        v["controller_observation"]["payload"]["managed_resources"] = []
    elif mutation == "old-live":
        old = copy.deepcopy(v["controller_observation"]["payload"]["managed_resources"][0])
        old.update(provider_resource_id=TASK + "-old", generation_id=2, current_observed_state="STOPPING")
        v["controller_observation"]["payload"]["managed_resources"].append(old)
    elif mutation == "db-scope":
        v["runtime_activation_binding"]["database_binding"]["database_oid"] = 2
    else:
        v["runtime_activation_binding"]["controller_epoch"] = 2
    with pytest.raises(c.ContractError):
        parsed("ActivationRead", v)


@pytest.mark.parametrize("vector", PACKAGE["signature_vectors"], ids=lambda v: v["type"])
def test_detached_signature_bytes_and_crypto_vectors_are_no_authority(vector: dict[str, Any]) -> None:
    obs = parsed(vector["type"], vector["body"])
    message = c.signature_input(obs)
    assert message == c.decode_base64(vector["signed_bytes_b64"])
    key = Ed25519PublicKey.from_public_bytes(c.decode_base64(vector["public_key"]))
    key.verify(c.decode_base64(obs.signature), message)
    with pytest.raises(InvalidSignature):
        key.verify(c.decode_base64(obs.signature), message + b" ")
    assert "INVALID_SIGNATURE" not in c._REFUSALS
    assert obs.unestablished_obligations


def test_profile_key_separation_cycles_and_revocation() -> None:
    profile = sample("TrustProfile")
    db = profile["database_keys"][0]
    db.update(key_id="database", purpose="database-observation", public_key=b64(b"b" * 32))
    control = profile["control_keys"][0]
    control.update(key_id="control", purpose="control-observation")
    assert parsed("TrustProfile", profile)
    for change in (
        dict(public_key=control["public_key"]),
        dict(key_id="control"),
        dict(purpose="control-observation"),
        dict(predecessor_key_id="missing"),
        dict(predecessor_key_id="database"),
        dict(lifecycle="REVOKED", revoked_at_ms=None),
    ):
        bad = copy.deepcopy(profile)
        bad["database_keys"][0].update(change)
        with pytest.raises(c.ContractError):
            parsed("TrustProfile", bad)


def test_compiler_plan_parity_requires_actual_protected_bytes(tmp_path: Path) -> None:
    vector = PACKAGE["compiler_parity_vector"]
    data = raw(vector["boundary"])
    assert hashlib.sha256(data).hexdigest() == vector["boundary_sha256"]
    path = safe_file(tmp_path, data)
    payload = parsed("GrantPayload", grant_payload())
    snapshot = c.check_grant_boundary_file(payload, str(path), expected_owner=os.getuid())
    assert snapshot.data == data
    assert payload.compiler_plan.execution_authorized is False
    # A valid schema hash or peer descriptor is not enough to match the pinned registry.
    for field in ("registered_schema_id", "registered_schema_sha256", "worker_id"):
        doc = grant_payload()
        doc["workload_plans"][0]["capabilities"][0][field] = "b" * 64 if field.endswith("sha256") else "other"
        with pytest.raises(c.ContractError):
            c.check_grant_boundary_file(parsed("GrantPayload", doc), str(path), expected_owner=os.getuid())


@pytest.mark.parametrize(
    "mutation", ["wildcard", "create-definition", "operator", "source", "grant-order", "extra-peer"]
)
def test_compiler_plan_rejects_drift(mutation: str) -> None:
    v = grant_payload()
    plan = v["compiler_plan"]
    if mutation == "wildcard":
        next(g for g in plan["grants"] if g["resource"] == "PROCESS_DEFINITION")["resource_id"] = "*"
    elif mutation == "create-definition":
        next(g for g in plan["grants"] if g["resource"] == "PROCESS_DEFINITION")["permissions"] = ["CREATE"]
    elif mutation == "operator":
        plan["operators"]["bootstrap"] = plan["operators"]["deployment"]
    elif mutation == "source":
        plan["grant_sources"][0]["capability_digests"] = ["b" * 64]
    elif mutation == "grant-order":
        plan["grants"].reverse()
    else:
        extra = copy.deepcopy(v["workload_plans"][0])
        extra["engine_user"] = "extra"
        v["workload_plans"].append(extra)
    v["compiler_plan_sha256"] = sha(plan)
    with pytest.raises(c.ContractError):
        parsed("GrantPayload", v)


def test_read_custody_retains_bytes_and_parse_never_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = raw(core())
    path = safe_file(tmp_path, data)
    actual_open = os.open
    leaves = []

    def spy(name: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if name == path.name:
            leaves.append(name)
        return actual_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(c.os, "open", spy)
    record = c.parse_protected_file(
        "GenerationCore",
        str(path),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_owner=os.getuid(),
        sensitivity="private",
    )
    assert record == parsed("GenerationCore", core())
    assert leaves == [path.name]


@pytest.mark.parametrize(
    "case",
    [
        "leaf-symlink",
        "parent-symlink",
        "fifo",
        "directory",
        "hardlink",
        "mode",
        "owner",
        "digest",
        "oversize",
        "relative",
        "dotdot",
    ],
)
def test_protected_read_refuses_unsafe_input_without_paths(tmp_path: Path, case: str) -> None:
    data = b"sensitive-test-body"
    path = safe_file(tmp_path, data)
    kwargs: dict[str, Any] = {}
    if case == "leaf-symlink":
        link = path.with_name("link")
        link.symlink_to(path)
        path = link
    elif case == "parent-symlink":
        link = path.parent / "link"
        link.symlink_to(path.parent, target_is_directory=True)
        path = link / path.name
    elif case == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif case == "directory":
        path.unlink()
        path.mkdir()
    elif case == "hardlink":
        os.link(path, path.with_name("second"))
    elif case == "mode":
        path.chmod(0o640)
    elif case == "owner":
        kwargs["expected_owner"] = os.getuid() + 1
    elif case == "digest":
        kwargs["expected_sha256"] = "b" * 64
    elif case == "oversize":
        kwargs["max_size"] = len(data) - 1
    elif case == "relative":
        path = Path("input.json")
    else:
        path = path.parent / ".." / path.parent.name / path.name
    options = dict(
        expected_sha256=hashlib.sha256(data).hexdigest(), expected_owner=os.getuid(), sensitivity="private"
    )
    options.update(kwargs)
    with pytest.raises(c.ContractError, match="^UNAVAILABLE$") as caught:
        c.read_protected_file(str(path), **options)
    assert str(path) not in str(caught.value) and "sensitive" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("race", ["append", "same-size", "replace", "chmod", "parent-replace"])
def test_actual_single_descriptor_read_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    directory = tmp_path / "owned"
    directory.mkdir()
    data = b"x" * 66000
    path = safe_file(directory, data)
    original_read = os.read
    triggered = False

    def racing_read(descriptor: int, length: int) -> bytes:
        nonlocal triggered
        result = original_read(descriptor, length)
        if result and not triggered:
            triggered = True
            if race == "append":
                with path.open("ab") as f:
                    f.write(b"y")
            elif race == "same-size":
                path.write_bytes(b"y" * len(data))
            elif race == "replace":
                replacement = path.with_name("replacement")
                replacement.write_bytes(data)
                replacement.chmod(0o600)
                replacement.replace(path)
            elif race == "chmod":
                path.chmod(0o644)
            else:
                renamed = directory.with_name("old")
                directory.rename(renamed)
                directory.mkdir()
                (directory / path.name).write_bytes(data)
        return result

    monkeypatch.setattr(c.os, "read", racing_read)
    with pytest.raises(c.ContractError):
        read(path, data)
    assert triggered


def test_size_limit_is_enforced_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"x" * 65536
    path = safe_file(tmp_path, data)
    original_read = os.read
    requested = []

    def append_after_read(fd: int, size: int) -> bytes:
        requested.append(size)
        result = original_read(fd, size)
        if len(requested) == 1:
            with path.open("ab") as f:
                f.write(b"z" * 100000)
        return result

    monkeypatch.setattr(c.os, "read", append_after_read)
    with pytest.raises(c.ContractError):
        read(path, data, max_size=len(data))
    assert requested == [65536, 1]


def test_eleven_safe_codes_only() -> None:
    assert len(c._REFUSALS) == 11
    for code in c._REFUSALS:
        error = c.ContractError(code)
        assert str(error) == code == error.code
    assert str(c.ContractError("secret-path-body")) == "INVALID_BODY"


def test_constructor_projects_unknown_arguments_to_safe_code() -> None:
    with pytest.raises(c.ContractError, match="^INVALID_BODY$"):
        c.Scope(credential_looking_argument="never print this")
    with pytest.raises(c.ContractError, match="^INVALID_BODY$"):
        c.Scope()


def profile_and_bounds(obs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    bounds = sample("InventoryBounds")
    bounds["bounds"] = []

    def collect(value: Any, path: str) -> None:
        if type(value) is dict:
            for k, child in value.items():
                collect(child, path + "." + k)
        elif type(value) is list:
            bounds["bounds"].append(
                {"collection_path": path, "max_entries": len(value), "expected_inventory_sha256": sha(value)}
            )
            for i, child in enumerate(value):
                collect(child, path + "." + str(i))

    collect(obs["payload"], obs["kind"] + ".payload")
    profile = sample("TrustProfile")
    profile["inventory_bounds_sha256"] = sha(bounds)
    profile["control_keys"][0].update(
        key_id="control", purpose="control-observation", not_before_ms=0, expires_at_ms=9007199254740991
    )
    profile["database_keys"][0].update(
        key_id="database",
        purpose="database-observation",
        public_key=b64(b"d" * 32),
        not_before_ms=0,
        expires_at_ms=9007199254740991,
    )
    obs["key_id"] = obs["kind"]
    obs["trust_profile_sha256"] = sha(profile)
    if obs["kind"] == "database":
        obs["payload"]["fence"]["trust_profile_sha256"] = sha(profile)
    return profile, bounds


@pytest.mark.parametrize("kind", ["control", "database"])
def test_profile_binding_covers_all_observed_collection_bounds(kind: str) -> None:
    obs = control_observation() if kind == "control" else database_observation()
    profile, bounds = profile_and_bounds(obs)
    c.check_observation_profile(
        parsed("SignedObservation", obs), parsed("TrustProfile", profile), parsed("InventoryBounds", bounds)
    )
    # There is intentionally no authenticated/verified output and no trusted clock input.
    assert parsed("SignedObservation", obs).unestablished_obligations


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-bound",
        "small-bound",
        "wrong-inventory",
        "expired",
        "retired",
        "revoked",
        "wrong-key",
        "wrong-profile",
    ],
)
def test_profile_bindings_fail_closed(mutation: str) -> None:
    obs = control_observation()
    profile, bounds = profile_and_bounds(obs)
    if mutation == "missing-bound":
        bounds["bounds"].pop(0)
    elif mutation == "small-bound":
        next(b for b in bounds["bounds"] if b["max_entries"] > 0)["max_entries"] = 0
    elif mutation == "wrong-inventory":
        bounds["bounds"][0]["expected_inventory_sha256"] = "b" * 64
    elif mutation == "expired":
        obs.update(issued_at_ms=100, expires_at_ms=200)
        profile["control_keys"][0]["expires_at_ms"] = 150
    elif mutation == "retired":
        profile["control_keys"][0]["lifecycle"] = "RETIRED"
    elif mutation == "revoked":
        profile["control_keys"][0].update(lifecycle="REVOKED", revoked_at_ms=1)
    elif mutation == "wrong-key":
        obs["key_id"] = "other"
    if mutation != "wrong-profile":
        profile["inventory_bounds_sha256"] = sha(bounds)
        obs["trust_profile_sha256"] = sha(profile)
    else:
        obs["trust_profile_sha256"] = "b" * 64
    with pytest.raises(c.ContractError):
        c.check_observation_profile(
            parsed("SignedObservation", obs),
            parsed("TrustProfile", profile),
            parsed("InventoryBounds", bounds),
        )


def test_observer_rotation_cycle_and_cross_purpose_predecessor() -> None:
    obs = control_observation()
    profile, _ = profile_and_bounds(obs)
    second = copy.deepcopy(profile["control_keys"][0])
    second.update(key_id="second", public_key=b64(b"s" * 32), predecessor_key_id="control")
    profile["control_keys"].append(second)
    assert parsed("TrustProfile", profile)
    profile["control_keys"][0]["predecessor_key_id"] = "second"
    with pytest.raises(c.ContractError):
        parsed("TrustProfile", profile)
    profile["control_keys"][0]["predecessor_key_id"] = "database"
    with pytest.raises(c.ContractError):
        parsed("TrustProfile", profile)


@pytest.mark.parametrize("field,value", [("expires_at_ms", 1788973215001), ("expires_at_ms", 1788973200000)])
def test_observation_duration_is_fixed(field: str, value: int) -> None:
    obs = sample("ControlObservation")
    obs.update(issued_at_ms=1788973200000)
    obs[field] = value
    with pytest.raises(c.ContractError):
        parsed("ControlObservation", obs)


def test_foreign_null_generation_is_known_unassigned_and_pg_options_are_distinct() -> None:
    role = sample("RoleObservation")
    role.update(owned=False, generation_id=None)
    assert parsed("RoleObservation", role).generation_id is None
    role["owned"] = True
    with pytest.raises(c.ContractError):
        parsed("RoleObservation", role)
    membership = sample("MembershipObservation")
    membership.update(admin_option=True, inherit_option=False, set_option=True)
    m = parsed("MembershipObservation", membership)
    assert m.admin_option and not m.inherit_option and m.set_option
    session = sample("SessionObservation")
    session.update(owned=False, generation_id=None, backend_start_unix_us=1788973215123456)
    assert parsed("SessionObservation", session).backend_start_unix_us == 1788973215123456
    session["backend_start_unix_us"] = 1788973215123456.0
    with pytest.raises(c.ContractError):
        parsed("SessionObservation", session)


def test_live_settlement_tracks_current_managed_resources_not_retirement() -> None:
    obs = control_observation()
    row = journal("SETTLED")
    row["provider_outcome"]["returned_resource_ids"] = [TASK]
    row["settlement"].update(settlement_class="CURRENT_LIVE_TRACKED", resource_proofs=[])
    obs["payload"]["journal"] = [row]
    assert parsed("ControlObservation", obs).payload.journal[0].state == "SETTLED"
    obs["payload"]["managed_resources"][0]["desired_state"] = "STOPPED"
    with pytest.raises(c.ContractError):
        parsed("ControlObservation", obs)
    retired = journal("SETTLED")
    retired["settlement"].update(settlement_class="RETIRED_RESOURCES_TERMINAL", resource_proofs=[])
    with pytest.raises(c.ContractError):
        parsed("JournalObservation", retired)


@pytest.mark.parametrize("field", ["invocation_id", "intent_sha256"])
def test_settlement_exact_dispatch_identity(field: str) -> None:
    row = journal("SETTLED")
    row["settlement"]["dispatch_terminal"][field] = "b" * 64 if field.endswith("sha256") else U[:-1] + "2"
    with pytest.raises(c.ContractError):
        parsed("JournalObservation", row)


def test_native_result_rows_and_resource_definitions_are_unique_and_sorted() -> None:
    request, _ = native_pair("deploy")
    resource = request["payload"]["resources"][0]
    duplicated = copy.deepcopy(resource)
    duplicated["name"] = "release/second.bpmn"
    request["payload"]["resources"].append(duplicated)
    with pytest.raises(c.ContractError):
        parsed("DeployRequest", request)
    request["payload"]["resources"][1]["expected_definitions"][0]["key"] = "other"
    assert parsed("DeployRequest", request)
    request["payload"]["resources"].reverse()
    with pytest.raises(c.ContractError):
        parsed("DeployRequest", request)


def test_final_b_consumer_rejects_valid_but_altered_compiler_permissions(tmp_path: Path) -> None:
    data = raw(PACKAGE["compiler_parity_vector"]["boundary"])
    path = safe_file(tmp_path, data)
    payload = grant_payload()
    row = next(
        r
        for r in payload["compiler_plan"]["grants"]
        if r["resource"] == "PROCESS_DEFINITION" and "UPDATE_INSTANCE" not in r["permissions"]
    )
    row["permissions"] = sorted([*row["permissions"], "UPDATE_INSTANCE"])
    payload["compiler_plan_sha256"] = sha(payload["compiler_plan"])
    candidate = parsed("GrantPayload", payload)
    with pytest.raises(c.ContractError):
        c.check_grant_boundary_file(candidate, str(path), expected_owner=os.getuid())


def test_final_b_changed_after_advertised_digest_is_not_reopened(tmp_path: Path) -> None:
    data = raw(PACKAGE["compiler_parity_vector"]["boundary"])
    path = safe_file(tmp_path, data)
    path.write_bytes(data + b" ")
    with pytest.raises(c.ContractError, match="^UNAVAILABLE$"):
        c.check_grant_boundary_file(
            parsed("GrantPayload", grant_payload()), str(path), expected_owner=os.getuid()
        )


def test_issuer_prepare_is_separate_and_scope_bound() -> None:
    request = sample("IssuerRequest")
    request["body"] = {"operation": "prepare_generation", "generation": generation(status="PREPARED")}
    assert parsed("IssuerRequest", request).body.operation == "prepare_generation"
    request["scope"]["database_binding"]["database_incarnation"] = U[:-1] + "2"
    with pytest.raises(c.ContractError):
        parsed("IssuerRequest", request)
    with pytest.raises(c.ContractError):
        parsed("AdmittedExternalIntent", request)


@pytest.mark.parametrize(
    "operation,name",
    [
        ("admit_candidate_login", "IssuerCandidate"),
        ("open_runtime_generation", "IssuerOpen"),
        ("retire_generation", "IssuerRetire"),
    ],
)
def test_other_issuer_request_grammars_are_closed(operation: str, name: str) -> None:
    value = sample("IssuerRequest")
    value["body"] = sample(name)
    if name == "IssuerCandidate":
        value["body"].update(deployment_receipt=receipt_ref("deploy"), grant_receipt=receipt_ref("grant"))
    assert parsed("IssuerRequest", value).body.operation == operation
    value["body"]["new_permission"] = True
    with pytest.raises(c.ContractError):
        parsed("IssuerRequest", value)


def test_generation_read_requires_matching_selected_issuer_receipt() -> None:
    value = sample("GenerationObserved")
    value.update(
        binding_sha256=sha(core()),
        issuer_receipt_sha256=sha(issuer()),
        signed_observation=database_observation(),
        status="OPEN",
    )
    readback = parsed("GenerationObserved", value)
    receipt = parsed("IssuerReceipt", issuer())
    with pytest.raises(c.ContractError):
        c.check_generation_read(readback, replace(receipt, issuer_login_oid=2))
    value["binding_sha256"] = "b" * 64
    with pytest.raises(c.ContractError):
        parsed("GenerationObserved", value)


def test_parent_permissions_and_descriptor_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "unsafe"
    directory.mkdir()
    data = b"safe"
    path = safe_file(directory, data)
    directory.chmod(0o777)
    opened, closed = [], []
    actual_open, actual_close = os.open, os.close

    def open_spy(*args: Any, **kwargs: Any) -> int:
        fd = actual_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def close_spy(fd: int) -> None:
        closed.append(fd)
        actual_close(fd)

    monkeypatch.setattr(c.os, "open", open_spy)
    monkeypatch.setattr(c.os, "close", close_spy)
    try:
        with pytest.raises(c.ContractError):
            read(path, data)
        assert sorted(opened) == sorted(closed)
    finally:
        directory.chmod(0o700)


@pytest.mark.parametrize("action", ["RunTask", "StopTask"])
def test_intent_binds_exact_generation_decision_and_owned_target(action: str) -> None:
    value = intent(action)
    d = decision(value["purpose"])
    d["generation_core"]["credential_binding_sha256"] = sha(value["credential_binding"])
    g = generation(value["purpose"], status=value["expected_precondition"]["target_generation_status"])
    g.update(binding=d["generation_core"], activation_decision_sha256=sha(d))
    managed = control_observation()["payload"]["managed_resources"][0] if action == "StopTask" else None
    i = parsed("AdmittedExternalIntent", value)
    inputs = dict(
        generation=parsed("RuntimeAdmissionGeneration", g),
        decision=parsed("CandidateDecision" if value["purpose"] == "candidate" else "ActivationDecision", d),
        managed_resource=parsed("ManagedResource", managed) if managed else None,
    )
    c.check_intent_binding(i, **inputs)
    bad = copy.deepcopy(value)
    bad["credential_binding"]["session_id"] = "other-session"
    with pytest.raises(c.ContractError):
        c.check_intent_binding(parsed("AdmittedExternalIntent", bad), **inputs)
    if action == "StopTask":
        with pytest.raises(c.ContractError):
            c.check_intent_binding(i, **{**inputs, "managed_resource": None})


def test_repeat_public_key_can_only_keep_same_purpose_subject_audience() -> None:
    obs = control_observation()
    profile, _ = profile_and_bounds(obs)
    repeated = copy.deepcopy(profile["control_keys"][0])
    repeated.update(key_id="same-binding-alias", predecessor_key_id="control")
    profile["control_keys"].append(repeated)
    assert parsed("TrustProfile", profile)
    for key in ("subject", "audience"):
        bad = copy.deepcopy(profile)
        bad["control_keys"][1][key] = "different"
        with pytest.raises(c.ContractError):
            parsed("TrustProfile", bad)


def test_private_reader_checks_mode_on_success_and_public_reader_allows_readability(tmp_path: Path) -> None:
    data = b"public-fixture"
    path = safe_file(tmp_path, data)
    path.chmod(0o644)
    snapshot = c.read_protected_file(
        str(path),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_owner=os.getuid(),
        sensitivity="public",
    )
    assert snapshot.data == data and snapshot.size == len(data)
    with pytest.raises(c.ContractError):
        read(path, data)


@pytest.mark.parametrize("field", [f.name for f in fields(c.RuntimeActivationBinding)])
def test_all_23_activation_fields_are_compared_to_explicit_context(field: str) -> None:
    active = parsed("ActivationActive", activation())
    expected = active.runtime_activation_binding.to_wire()
    if field == "scope":
        expected[field]["account"] = "999999999999"
    elif field == "database_binding":
        expected[field]["database_oid"] = 2
        expected["scope"][field]["database_oid"] = 2
    elif field.endswith("receipt_key"):
        expected["deployment_receipt_key"]["run_id"] = U[:-1] + "2"
        expected["grant_receipt_key"]["run_id"] = U[:-1] + "2"
    elif field == "caller_identity":
        expected[field]["workload"] = "different"
    elif field == "caller_certificate_binding":
        expected[field]["certificate_sha256"] = "b" * 64
    elif type(expected[field]) is int:
        expected[field] += 1
    else:
        expected[field] = "b" * len(expected[field])
    binding = parsed("RuntimeActivationBinding", expected)
    with pytest.raises(c.ContractError):
        c.check_activation_read(
            active,
            current_binding=binding,
            decision=parsed("ActivationDecision", decision()),
            generation=parsed("RuntimeAdmissionGeneration", generation()),
            issuer=parsed("IssuerReceipt", issuer()),
        )


def test_deploy_dmn_result_descriptors_cover_decision_and_drd_without_xml_claim() -> None:
    request, receipt = native_pair("deploy")
    resource = request["payload"]["resources"][0]
    resource.update(
        name="release/rules.dmn",
        kind="DMN",
        expected_definitions=[{"kind": "decision", "key": "decision-one"}, {"kind": "drd", "key": "drd-one"}],
    )
    body = json.loads(c.decode_base64(receipt["canonical_result_bytes"]))
    body["resources"][0].update(name=resource["name"], kind="DMN")
    definition = body["definitions"][0]
    body["definitions"] = [
        dict(definition, **item, definition_id=f"generated-{i}", resource_name=resource["name"])
        for i, item in enumerate(resource["expected_definitions"])
    ]
    receipt.update(
        request_sha256=sha(request), result_sha256=sha(body), canonical_result_bytes=b64(raw(body))
    )
    c.check_native_receipt(parsed("NativeRequest", request), parsed("NativeReceipt", receipt))
    resource["expected_definitions"][1]["kind"] = "process"
    with pytest.raises(c.ContractError):
        parsed("DeployRequest", request)


@pytest.mark.parametrize(
    "operation",
    ["prepare_generation", "admit_candidate_login", "open_runtime_generation", "retire_generation"],
)
def test_issuer_complete_decision_identity(operation: str) -> None:
    purpose = "candidate" if operation == "admit_candidate_login" else "runtime"
    d = decision(purpose)
    g = generation(purpose, "PREPARED")
    request = sample("IssuerRequest")
    bodies = {
        "prepare_generation": {"operation": operation, "generation": g},
        "admit_candidate_login": {
            **sample("IssuerCandidate"),
            "candidate_decision_sha256": sha(d),
            "deployment_receipt": receipt_ref("deploy"),
            "grant_receipt": receipt_ref("grant"),
        },
        "open_runtime_generation": {**sample("IssuerOpen"), "activation_decision_sha256": sha(d)},
        "retire_generation": sample("IssuerRetire"),
    }
    request["body"] = bodies[operation]
    inputs = dict(
        generation=parsed("RuntimeAdmissionGeneration", g),
        decision=parsed("CandidateDecision" if purpose == "candidate" else "ActivationDecision", d),
    )
    r = parsed("IssuerRequest", request)
    c.check_issuer_request(r, **inputs)
    with pytest.raises(c.ContractError):
        c.check_issuer_request(replace(r, run_id=U[:-1] + "2"), **inputs)
