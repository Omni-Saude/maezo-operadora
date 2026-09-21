"""Pinned source extraction tests, no PG execution or actual resource qualification."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_owner_contracts as n
from tests.unit.platform.engine_bootstrap.test_native_owner_contracts import record_vector

ROOT = Path(__file__).parents[3]
SOURCE = ROOT / "deploy/cibseven/secured/install_v2.py"
RESOURCES = ROOT / "src/maezo/portal/engine/java/src/main/resources"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


readback = module(
    ROOT / "deploy/cibseven/secured/migrations/native_owner_readback.py", "native_readback_tested"
)
renderer = module(SOURCE, "native_renderer_tested")


def binding():
    installed = record_vector()["installed_record"]
    selector = installed["preparation_binding"]
    value = dict(
        schema_owner="n_schema",
        schema_owner_oid=8,
        function_owner="n_function",
        function_owner_oid=2,
        issuer_role="n_issuer",
        issuer_role_oid=3,
        runtime_role=selector["login_name"],
        runtime_role_oid=selector["login_oid"],
        d_guard_owner="d_guard",
        d_guard_owner_oid=5,
        migration_role="migration",
        migration_role_oid=999,
        d_schema_owner="d_owner",
        d_schema_owner_oid=4,
        act_schema=installed["act_schema"],
        act_schema_oid=installed["act_schema_oid"],
        database_oid=installed["database_oid"],
        database_binding_sha256=installed["database_binding_sha256"],
        migration_receipt=installed["migration_receipt"],
    )
    value.update(
        {
            key: "a" * 64
            for key in (
                "d_helper_source_sha256",
                "d_schema_version_digest",
                "d_source_manifest_sha256",
                "d_migration_sha256",
                "d_role_acl_sha256",
                "d_objects_sha256",
            )
        }
    )
    value["d_preparation"] = {
        key: selector[key]
        for key in (
            "tenant",
            "environment",
            "engine_name",
            "account",
            "region",
            "database_incarnation",
            "generation_id",
            "epoch",
            "run_id",
            "purpose",
            "preparation_id",
            "receipt_sha256",
            "generation_core_sha256",
            "decision_sha256",
        )
    }
    return value


def source(**changes):
    args = dict(
        installer_sha256=s.digest(SOURCE.read_bytes()),
        manifest_sha256=s.digest((RESOURCES / "native-acquisition-v2-migration.json").read_bytes()),
        template_sha256=s.digest((RESOURCES / "native-acquisition-v2-postgres.sql").read_bytes()),
        act_schema_owner_oid=81,
        act_relation_owner_oid=82,
    )
    args.update(changes)
    return readback.FrozenNativeReadback(s.canonical(binding()), **args)


def test_readback_extracts_original_preflight_and_replay_without_installation_or_transaction():
    value = binding()
    rendered = renderer.render(value)
    checks = renderer.qualification_checks(value)
    expected = rendered.split("SET LOCAL statement_timeout='5000ms';\n", 1)[1].split(
        "SELECT to_regnamespace('maezo_native_v2')", 1
    )[0]
    expected += rendered.split("\\else\n", 1)[1].split("\\endif\n", 1)[0]
    assert checks == expected
    assert "FOR UPDATE" in checks and "native_v2_migration_drift" in checks
    assert "CREATE TABLE" not in checks and "COMMIT;" not in checks and "\\if" not in checks
    actual = source()
    assert actual.checks == checks and len(actual.function_names) == 19


@pytest.mark.parametrize("pin", ["installer_sha256", "manifest_sha256", "template_sha256"])
def test_no_source_pin_can_be_replaced_by_actual_unreviewed_bytes(pin):
    with pytest.raises(s.Refusal):
        source(**{pin: "f" * 64})


def test_request_is_checked_against_pinned_principal_before_any_lock():
    actual = source()
    q = n.request(s.decode64(record_vector()["request_bytes"]))
    actual.validate_request(s.canonical(q))
    q["generation_id"] += 1
    with pytest.raises(s.Refusal):
        actual.validate_request(s.canonical(q))
