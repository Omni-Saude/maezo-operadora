"""Offline D7-D provisioning-plan controls; no engine/authority is simulated."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from maezo.gateway.engine_contracts import (
    EngineCapabilityProfile,
    EngineIdentity,
    EngineOperation,
    EngineTarget,
    canonical_json,
)
from maezo.gateway.engine_schemas import CONSENT_REVOKED, CONTAS_PAGTO_START, HELENA_START, start_read_schema
from maezo.platform.engine_bootstrap.secured_plan import ProvisioningPlanError, compile_boundary_plan


def boundary(schema=HELENA_START, source_kind="", attestations=None):
    identity = EngineIdentity(
        "synthetic", "isolated-test", schema.workload, "v1", "CN=SyntheticCA", "spiffe://synthetic/runtime"
    )
    profile = EngineCapabilityProfile(
        identity,
        EngineTarget(schema.process_key, 7, "definition-7", schema.topic, schema.message),
        schema,
        worker_id="actual-worker" if schema.topic else "",
        source_target=EngineTarget(schema.source_process_key, 3, "source-3", schema.source_topic)
        if schema.source_process_key
        else None,
    )
    profiles = [profile]
    if schema.operation is EngineOperation.START:
        profiles += [
            replace(profile, schema=start_read_schema(schema, op))
            for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
        ]
    bindings = [
        dict(
            document=p.document(),
            digest=p.digest,
            source_kind=source_kind,
            source_worker_id="actual-source-worker" if source_kind == "locked_external" else "",
            attestations=attestations or [],
        )
        for p in profiles
    ]
    # Derived reads carry no fields but retain source metadata; bind only their own required fields.
    for binding in bindings[1:]:
        binding["attestations"] = []
    peer = dict(
        certificate_sha256="a" * 64,
        spki_sha256="b" * 64,
        issuer_dn=identity.issuer,
        subject_dn="CN=Runtime",
        uri_san=identity.subject,
        purpose="nonhuman",
        engine_user="syntheticworker",
        identity=json.loads(canonical_json(profile.document()["identity"])),
        not_before=1,
        expires_at=100,
        capabilities=bindings,
    )
    peers = [peer]
    for index, purpose in enumerate(("bootstrap", "deployment", "human-relay")):
        item = json.loads(json.dumps(peer))
        item.update(
            certificate_sha256=str(index + 1) * 64,
            spki_sha256=str(index + 4) * 64,
            purpose=purpose,
            engine_user="synthetic" + purpose.replace("-", ""),
            capabilities=[],
        )
        item["identity"].update(workload=purpose, subject="spiffe://synthetic/" + purpose)
        item["uri_san"] = item["identity"]["subject"]
        peers.append(item)
    return dict(
        protocol="maezo.engine-boundary.v1",
        tenant="synthetic",
        environment="isolated-test",
        engine_name="engine-synthetic",
        not_before=1,
        expires_at=100,
        roots=[dict(path="/mounted/ca.pem", sha256="a" * 64)],
        files=[],
        listener_port=8443,
        max_tasks=10,
        max_lock_millis=10000,
        max_poll_millis=1000,
        peers=peers,
    )


def compile(document):
    raw = canonical_json(document)
    return compile_boundary_plan(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_minimum_start_and_read_grants_are_exact_and_operators_not_granted():
    plan = compile(boundary())
    assert plan["execution_authorized"] is False
    assert plan["grants"] == [
        dict(
            engine_user="syntheticworker",
            resource="PROCESS_DEFINITION",
            resource_id=HELENA_START.process_key,
            permissions=["CREATE_INSTANCE", "READ", "READ_HISTORY", "READ_INSTANCE"],
        ),
        dict(
            engine_user="syntheticworker",
            resource="PROCESS_INSTANCE",
            resource_id="*",
            permissions=["CREATE"],
        ),
    ]
    assert plan["preserve_existing_users"] == ["synthetichumanrelay"]
    assert plan["operators"] == {"bootstrap": "syntheticbootstrap", "deployment": "syntheticdeployment"}


@pytest.mark.parametrize(
    "change",
    [
        "admin-grant",
        "mixed-identity",
        "operator-runtime",
        "unknown-schema",
        "digest",
        "source-missing",
        "extra-attestation",
        "wildcard-process",
        "missing-operator",
        "same-operator",
        "extra-peer-key",
    ],
)
def test_unreviewed_authority_is_refused(change):
    value = boundary()
    peer = value["peers"][0]
    binding = peer["capabilities"][0]
    if change == "admin-grant":
        value["peers"][1]["capabilities"] = peer["capabilities"]
    elif change == "mixed-identity":
        peer["identity"]["tenant"] = "wrong"
    elif change == "operator-runtime":
        value["peers"][1]["engine_user"] = peer["engine_user"]
    elif change == "unknown-schema":
        binding["document"]["schema"]["schema_id"] = "invented"
    elif change == "digest":
        binding["digest"] = "f" * 64
    elif change == "source-missing":
        binding["source_kind"] = "completed_human"
    elif change == "extra-attestation":
        binding["attestations"] = [
            dict(name="decision", source_variable="decision", human_task_definition="human")
        ]
    elif change == "wildcard-process":
        binding["document"]["target"]["process_key"] = "*"
    elif change == "missing-operator":
        value["peers"].pop(1)
    elif change == "same-operator":
        value["peers"][2]["engine_user"] = value["peers"][1]["engine_user"]
    elif change == "extra-peer-key":
        peer["admin"] = True
    with pytest.raises(ProvisioningPlanError):
        compile(value)


def test_hash_mismatch_is_not_a_plan():
    with pytest.raises(ProvisioningPlanError):
        compile_boundary_plan(canonical_json(boundary()), expected_sha256="f" * 64)


def test_prior_human_source_requires_exact_read_history_and_no_source_update():
    value = boundary(
        CONSENT_REVOKED,
        "completed_human",
        [
            dict(
                name="consent_event_ref",
                source_variable="consent_event_ref",
                human_task_definition="UT_ExecutarDSR",
            ),
            dict(
                name="beneficiario_pseudo_id",
                source_variable="beneficiario_pseudo_id",
                human_task_definition="",
            ),
        ],
    )
    plan = compile(value)
    grants = {g["resource_id"]: g["permissions"] for g in plan["grants"]}
    assert grants == {
        "SP-OP-PROGRAMA-001": ["READ", "READ_INSTANCE", "UPDATE_INSTANCE"],
        "SP-OP-LGPD-DSR-001": ["READ", "READ_HISTORY"],
    }


def test_external_source_grants_only_required_reads():
    value = boundary(
        CONTAS_PAGTO_START,
        "locked_external",
        [
            dict(name="fonte_valor", source_variable="fonte_valor", human_task_definition=""),
            dict(name="lastro_origem", source_variable="lastro_origem", human_task_definition=""),
            dict(
                name="lastro_decisor_id",
                source_variable="analista_id",
                human_task_definition="UT_AnalistaContas",
            ),
        ],
    )
    grants = {g["resource_id"]: g["permissions"] for g in compile(value)["grants"]}
    assert grants["SP-OP-CONTAS-001"] == ["READ", "READ_HISTORY", "READ_INSTANCE"]
    assert "UPDATE_INSTANCE" not in grants["SP-OP-CONTAS-001"]


def all_rows():
    from maezo.gateway.engine_schemas import SCHEMAS, worker_lifecycle_schema

    rows = list(SCHEMAS)
    for schema in SCHEMAS:
        if schema.operation is EngineOperation.START:
            rows.extend(
                start_read_schema(schema, op)
                for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
            )
        if schema.topic:
            rows.extend(
                worker_lifecycle_schema(schema, op)
                for op in (
                    EngineOperation.FETCH_LOCK,
                    EngineOperation.FAILURE,
                    EngineOperation.EXTEND_LOCK,
                    EngineOperation.UNLOCK,
                )
            )
    return rows


@pytest.mark.parametrize("schema", all_rows(), ids=lambda s: s.schema_id)
def test_all_57_frozen_rows_have_only_b_minimum_grants(schema):
    from maezo.gateway.engine_contracts import FieldOrigin

    attestations = [
        dict(
            name=f.name,
            source_variable=f.name,
            human_task_definition="actual-human-task" if f.origin is FieldOrigin.PRIOR_HUMAN else "",
        )
        for f in schema.fields
        if f.origin in (FieldOrigin.ENGINE, FieldOrigin.PRIOR_HUMAN)
    ]
    attestations += [
        dict(name=name, source_variable=name, human_task_definition="")
        for name in schema.correlation_fields
        if name != "tenant_id"
    ]
    source = (
        ("locked_external" if schema.source_topic else "completed_human") if schema.source_process_key else ""
    )
    plan = compile(boundary(schema, source, attestations))
    # WP-J1-03 (2026-09-12): 47 -> 57. The count is a drift detector, not a ceiling: it exists
    # so that adding a reviewed row forces someone to re-read THIS test and confirm the new row
    # still gets only B-minimum grants. That happened. The ten new rows are the dedicated PHI
    # document-request bridge's two base rows (`auth.request_documents.bridge.fetch_lock.v2` and
    # `.complete.v2`, workload `auth_document_request_bridge`) plus the eight lifecycle rows
    # `worker_lifecycle_schema` derives from them — the bridge fetches, and must therefore also
    # be able to fail/extend/unlock, its own external task. Two base rows and not one because
    # `FetchProfile` requires the classified variables in `read_projection` while the
    # external_complete contract requires that same field to be EMPTY.
    assert len(all_rows()) == 57
    assert plan["execution_authorized"] is False
    assert all(g["engine_user"] == "syntheticworker" for g in plan["grants"])
    for grant in plan["grants"]:
        assert grant["resource"] in {"PROCESS_DEFINITION", "PROCESS_INSTANCE"}
        if grant["resource_id"] == "*":
            assert schema.operation is EngineOperation.START
            assert grant["resource"] == "PROCESS_INSTANCE" and grant["permissions"] == ["CREATE"]
        else:
            assert grant["resource_id"] in {schema.process_key, schema.source_process_key}
            assert not {"ALL", "DELETE", "UPDATE", "CREATE"}.intersection(grant["permissions"])
    assert len(plan["grant_sources"]) == len(plan["grants"])


def test_schema_widening_with_recomputed_digest_is_still_refused():
    value = boundary()
    binding = value["peers"][0]["capabilities"][0]
    binding["document"]["schema"]["fields"].append(
        dict(binding["document"]["schema"]["fields"][0], name="decision")
    )
    binding["digest"] = hashlib.sha256(canonical_json(binding["document"])).hexdigest()
    with pytest.raises(ProvisioningPlanError):
        compile(value)


@pytest.mark.parametrize("change", ["purpose", "key", "subject", "capability-source", "peer-window"])
def test_rotation_cannot_smuggle_authority(change):
    value = boundary()
    peer = json.loads(json.dumps(value["peers"][0]))
    peer["certificate_sha256"] = "e" * 64
    if change == "purpose":
        peer["purpose"] = "bootstrap"
        peer["capabilities"] = []
    elif change == "key":
        peer["engine_user"] = "other"
        peer["purpose"] = "bootstrap"
        peer["capabilities"] = []
    elif change == "subject":
        peer["spki_sha256"] = "f" * 64
        peer["engine_user"] = "other"
        peer["purpose"] = "bootstrap"
        peer["capabilities"] = []
    elif change == "capability-source":
        peer["capabilities"][0]["source_kind"] = "locked_external"
    elif change == "peer-window":
        peer["expires_at"] = 101
    value["peers"].append(peer)
    with pytest.raises(ProvisioningPlanError):
        compile(value)


def test_identical_authority_rotation_is_deduplicated():
    value = boundary()
    original = compile(value)
    peer = json.loads(json.dumps(value["peers"][0]))
    peer["certificate_sha256"] = "e" * 64
    peer["spki_sha256"] = "f" * 64
    value["peers"].append(peer)
    rotated = compile(value)
    assert original["grants"] == rotated["grants"]
    assert original["grant_sources"] == rotated["grant_sources"]


@pytest.mark.parametrize(
    "raw", [b'{"protocol":"a","protocol":"b"}', b'{"value":NaN}', b"[]", b"x" * 1_048_577]
)
def test_malformed_manifest_refuses_without_echo(raw):
    with pytest.raises(ProvisioningPlanError) as exc:
        compile_boundary_plan(raw, expected_sha256=hashlib.sha256(raw).hexdigest())
    assert str(exc.value) == "engine_provisioning_plan_refused"


def test_cli_reads_only_exact_file_and_has_no_apply(tmp_path, capsys):
    from maezo.platform.engine_bootstrap.secured_plan_cli import main

    raw = canonical_json(boundary())
    path = tmp_path / "boundary.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert main(["--boundary-file", str(path), "--boundary-sha256", digest]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execution_authorized"] is False
    assert path.read_bytes() == raw and list(tmp_path.iterdir()) == [path]
    with pytest.raises(SystemExit) as exc:
        main(["--boundary-file", str(path), "--boundary-sha256", digest, "--apply"])
    assert exc.value.code == 2
    assert path.read_bytes() == raw


def test_cli_safe_error_and_symlink_refusal(tmp_path, capsys):
    from maezo.platform.engine_bootstrap.secured_plan_cli import main

    path = tmp_path / "sensitive-looking-name"
    path.write_bytes(canonical_json(boundary()))
    link = tmp_path / "link"
    link.symlink_to(path)
    assert main(["--boundary-file", str(link), "--boundary-sha256", "f" * 64]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "engine_provisioning_plan_refused\n"


def test_source_business_key_adds_read_history_for_exact_source():
    value = boundary(
        CONSENT_REVOKED,
        "locked_external",
        [
            dict(
                name="consent_event_ref",
                source_variable="consent_event_ref",
                human_task_definition="actual-human-task",
            ),
            dict(name="beneficiario_pseudo_id", source_variable="@business_key", human_task_definition=""),
        ],
    )
    source = next(g for g in compile(value)["grants"] if g["resource_id"] == "SP-OP-LGPD-DSR-001")
    assert source["permissions"] == ["READ", "READ_HISTORY", "READ_INSTANCE"]


def test_same_private_key_cannot_span_runtime_engine_users():
    value = boundary()
    peer = json.loads(json.dumps(value["peers"][0]))
    peer["certificate_sha256"] = "e" * 64
    peer["engine_user"] = "otherworker"
    value["peers"].append(peer)
    with pytest.raises(ProvisioningPlanError):
        compile(value)
