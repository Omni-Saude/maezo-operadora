"""Finite canonical records only; these vectors are never installation authority."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from tests.unit.platform.engine_bootstrap import test_controller_contracts as vectors
from tests.unit.platform.engine_bootstrap.test_controller_storage import H, U

from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_owner_contracts as n


def record_vector():
    core = vectors.core("candidate")
    scope = core["scope"]
    db = scope["database_binding"]
    db_hash = s.digest(s.canonical(db))
    birth = dict(
        protocol="maezo.d7-owner-preparation-result.v1",
        installation_id=U,
        owner_operation_id=U,
        preparation_id=U,
        event="PREPARED",
        scope=scope,
        database_binding_sha256=db_hash,
        principal={
            "kind": "generation",
            **{key: value for key, value in core.items() if key not in {"scope", "epoch"}},
        },
        generation_core=core,
        owner_catalog_bytes=s.encode64(b"{}"),
        owner_catalog_sha256=s.digest(b"{}"),
        external_resource_readback_sha256=H,
        native_qualification=None,
        request_sha256=H,
        owner_session_user="migration",
        owner_login_oid=999,
        owner_effective_user="migration",
        owner_effective_oid=999,
        recorded_before_commit_at_ms=1,
    )
    birth_wire = s.Document.create("OwnerPreparationResult", birth).wire
    selector = {
        **{key: value for key, value in scope.items() if key != "database_binding"},
        "database_incarnation": db["database_incarnation"],
        "generation_id": core["generation_id"],
        "epoch": core["epoch"],
        "run_id": U,
        "purpose": core["purpose"],
        "preparation_id": U,
        "receipt_sha256": s.digest(birth_wire),
        "generation_core_sha256": s.digest(s.canonical(core)),
        "decision_sha256": H,
        "native_function_owner_oid": 2,
        "native_issuer_oid": 3,
        "login_name": core["login_name"],
        "login_oid": core["login_oid"],
        "d_schema_owner": "d_owner",
        "d_schema_owner_oid": 4,
        "database_binding_sha256": db_hash,
    }
    native_text = b'{"functions": [], "namespace": {"oid": 1}}'
    installed = dict(
        migration_key="native-acquisition-v2",
        manifest_digest=H,
        act_schema=db["schema_name"],
        act_schema_oid=db["schema_oid"],
        database_oid=db["database_oid"],
        database_binding_sha256=db_hash,
        function_owner=2,
        issuer_role=3,
        d_helper_oid=7,
        abi_digest=n.ABI_SHA256,
        migration_receipt="migration1",
        preparation_binding=selector,
        preparation_receipt_bytes=s.encode64(birth_wire),
        catalogue_digest=s.digest(native_text),
    )
    q = dict(
        protocol="maezo.native-owner-initial-qualification-request.v1",
        native_owner_operation_id=U,
        owner_installation_id=U,
        d_installation_id=U,
        d_preparation_id=U,
        native_installation_receipt_sha256=s.digest(s.canonical(installed)),
        scope=scope,
        generation_id=core["generation_id"],
        purpose="candidate",
        login_name=core["login_name"],
        login_oid=core["login_oid"],
        generation_core_sha256=selector["generation_core_sha256"],
        decision_sha256=H,
        database_binding_sha256=db_hash,
        before_catalog_sha256=s.digest(native_text),
    )
    raw = s.canonical(q)
    value = dict(
        protocol="maezo.native-owner-initial-qualification-receipt.v1",
        request_bytes=s.encode64(raw),
        request_sha256=s.digest(raw),
        installed_record=installed,
        installed_record_sha256=s.digest(s.canonical(installed)),
        native_catalog_bytes=s.encode64(native_text),
        native_catalog_sha256=s.digest(native_text),
        d_before_catalog_bytes=s.encode64(b"{}"),
        d_before_catalog_sha256=s.digest(b"{}"),
        d_after_catalog_bytes=s.encode64(b"{}"),
        d_after_catalog_sha256=s.digest(b"{}"),
        owner_store_catalog_sha256=H,
        owner_session_user="migration",
        owner_login_oid=999,
        owner_effective_user="migration",
        owner_effective_oid=999,
        recorded_before_commit_at_ms=1,
    )
    return value


def test_finite_closed_receipt_projects_existing_binding_without_claiming_authority():
    value = record_vector()
    raw = s.canonical(value)
    assert n.receipt(raw) == value
    outer = s.parse_wire(n.qualification_from_receipt(raw))
    s.validate_native_qualification(outer)
    assert s.decode64(outer["native_receipt_bytes"]) == raw
    assert outer["before_catalog_sha256"] == outer["after_catalog_sha256"]
    assert set(outer) == set(s.NATIVE_QUALIFICATION_FIELDS.split())


def test_installed_selector_cannot_advance_retained_preparation_epoch():
    value = record_vector()["installed_record"]
    value["preparation_binding"]["epoch"] += 1
    with pytest.raises(s.Refusal):
        n.installed_record(value)


@pytest.mark.parametrize("field", n.RECEIPT_FIELDS.split())
def test_each_missing_receipt_field_is_refused(field):
    value = record_vector()
    del value[field]
    with pytest.raises(s.Refusal):
        n.receipt(s.canonical(value))


@pytest.mark.parametrize("field", n.INSTALLED_FIELDS.split())
def test_each_missing_original_installed_field_is_refused(field):
    value = record_vector()["installed_record"]
    del value[field]
    with pytest.raises(s.Refusal):
        n.installed_record(value)


@pytest.mark.parametrize(
    "field",
    [
        "native_catalog_sha256",
        "d_before_catalog_sha256",
        "d_after_catalog_sha256",
        "request_sha256",
        "installed_record_sha256",
    ],
)
def test_every_byte_digest_pair_is_checked(field):
    value = record_vector()
    value[field] = "f" * 64
    with pytest.raises(s.Refusal):
        n.receipt(s.canonical(value))


@pytest.mark.parametrize(
    "field,new",
    [
        ("purpose", "runtime"),
        ("login_oid", 81),
        ("generation_id", 77),
        ("login_name", "other"),
        ("decision_sha256", "f" * 64),
        ("database_binding_sha256", "e" * 64),
        ("d_preparation_id", "00000000-0000-4000-8000-000000000002"),
    ],
)
def test_rehashed_request_cannot_change_installed_birth_binding(field, new):
    value = record_vector()
    q = s.parse_wire(s.decode64(value["request_bytes"]))
    q[field] = new
    raw = s.canonical(q)
    value.update(request_bytes=s.encode64(raw), request_sha256=s.digest(raw))
    with pytest.raises(s.Refusal):
        n.receipt(s.canonical(value))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(extra=True),
        lambda v: v.update(owner_effective_user="assumed"),
        lambda v: v.update(owner_effective_oid=998),
        lambda v: v.update(recorded_before_commit_at_ms=True),
    ],
)
def test_receipt_shape_identity_and_time_controls(mutation):
    value = copy.deepcopy(record_vector())
    mutation(value)
    with pytest.raises(s.Refusal):
        n.receipt(s.canonical(value))


def test_postgres_native_catalog_text_is_preserved_and_never_rehashed_as_python_json():
    value = record_vector()
    raw = s.decode64(value["native_catalog_bytes"])
    assert raw != s.canonical(json.loads(raw))
    assert n.receipt(s.canonical(value))["native_catalog_sha256"] == s.digest(raw)
    value["native_catalog_bytes"] = s.encode64(s.canonical(json.loads(raw)))
    with pytest.raises(s.Refusal):
        n.receipt(s.canonical(value))


def test_owner_schema_is_separate_and_has_no_runtime_or_public_grants():
    root = Path(__file__).parents[4]
    sql = (
        root / "src/maezo/portal/engine/java/src/main/resources/native-owner-qualification-v1-postgres.sql"
    ).read_bytes()
    manifest = json.loads(
        (root / "deploy/cibseven/secured/migrations/native-owner-qualification-0001.json").read_bytes()
    )
    assert manifest["sql_template_sha256"] == hashlib.sha256(sql).hexdigest()
    assert sql.count(b"CREATE TABLE ") == 2
    assert sql.count(b"BEFORE UPDATE OR DELETE OR TRUNCATE") == 2
    assert b"GRANT " not in sql and b"CREATE OR REPLACE" not in sql
    assert b"UNIQUE(native_installation_receipt_sha256,d_preparation_id)" in sql
    assert b"maezo_native_v2." not in sql and b"maezo_d7_control." not in sql
    assert manifest["execution_authorized"] is False and manifest["runtime_admission"] is False
