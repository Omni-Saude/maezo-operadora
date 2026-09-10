"""Actual inert installer, fixed SQL catalogue and owner-port construction tests.

SQL source inspection is not live PostgreSQL validation. The separately marked
integration module owns execution under real owner roles and real DynamoDB.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.unit.platform.engine_bootstrap import test_controller_contracts as v
from tests.unit.platform.engine_bootstrap.test_controller_storage import H, U, preparation

from maezo.platform.engine_bootstrap import controller_storage as s

ROOT = Path(__file__).parents[4]
SQL_PATH = ROOT / "src/maezo/portal/engine/java/src/main/resources/provisioning-schema-postgres.sql"
PACKAGE_PATH = ROOT / "deploy/cibseven/secured/migrations/d7-control-0001.json"
INSTALLER_PATH = PACKAGE_PATH.with_name("d7_control_install.py")
SQL = SQL_PATH.read_text()
PACKAGE = json.loads(PACKAGE_PATH.read_bytes())
_SPEC = importlib.util.spec_from_file_location("d7_owner_install_tested", INSTALLER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
owner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = owner
_SPEC.loader.exec_module(owner)


def catalog() -> dict[str, Any]:
    roles = []
    for i, kind in enumerate(owner.SINGLETONS):
        roles.append(
            dict(
                role_class=kind,
                role_name="owned_" + str(i),
                role_oid=100 + i,
                rolcanlogin=kind in {"actual issuer login", "scoped D observer login"},
                rolsuper=False,
                rolcreaterole=kind == "D issuer-function owner",
                rolcreatedb=False,
                rolreplication=False,
                rolbypassrls=False,
                rolinherit=False,
            )
        )
    return rehash(
        {
            "protocol": "maezo.d7-role-acl-manifest.v1",
            "installation_id": U,
            "roles": roles,
            "memberships": [],
            "object_grants": [],
            "operational_principals": [],
        }
    )


def rehash(value: dict[str, Any]) -> dict[str, Any]:
    value["catalog_sha256"] = s.digest(s.canonical({k: v for k, v in value.items() if k != "catalog_sha256"}))
    return value


def input_record() -> dict[str, Any]:
    return dict(
        protocol="maezo.d7-control-installation.v1",
        installation_id=U,
        control_scope_id=U,
        component="maezo-d7-control",
        version=1,
        database_binding=v.sample("DatabaseBinding"),
        act_schema_name="synthetic",
        controller_table_arn="arn:aws:dynamodb:sa-east-1:123456789012:table/owned",
        source_manifest_sha256=H,
        cib_abi_sha256=H,
        roles=sorted(
            [{"role_class": r["role_class"], "role_name": r["role_name"]} for r in catalog()["roles"]],
            key=lambda r: (r["role_class"], r["role_name"]),
        ),
        issuer_scopes=[v.sample("Scope")],
        prepared_principals=[],
    )


def function_body(name: str) -> str:
    pattern = r"CREATE FUNCTION maezo_d7_control\." + re.escape(name) + r"\(.*?AS \$d7\$(.*?)\$d7\$;"
    result = re.search(pattern, SQL, re.S)
    assert result is not None
    return result.group(1)


def test_actual_migration_has_exact_seven_relations_complete_columns_and_pinned_bytes() -> None:
    names = re.findall(r'CREATE TABLE maezo_d7_control\."([A-Z_]+)"', SQL)
    assert set(names) == set(PACKAGE["tables"]) and len(names) == 7
    assert hashlib.sha256(SQL.encode()).hexdigest() == PACKAGE["sql_sha256"]
    assert PACKAGE["max_depth"] == 32 and PACKAGE["execution_authorized"] is False
    for name, table in PACKAGE["tables"].items():
        block = SQL.split('CREATE TABLE maezo_d7_control."' + name + '" (', 1)[1].split("\n);", 1)[0]
        columns = re.findall(r'^  "([a-z_0-9]+)" ', block, re.M)
        assert set(columns) == set(table["columns"])
        assert 'CREATE TRIGGER d7_immutable BEFORE UPDATE OR DELETE ON maezo_d7_control."' + name + '"' in SQL
    assert not re.search(r"\b(COMMIT|ROLLBACK|CREATE ROLE|CREATE EXTENSION|IF NOT EXISTS)\b", SQL)


def test_fixed_public_overloads_and_private_validators_are_hardened() -> None:
    for entry in PACKAGE["functions"]["functions"] + PACKAGE["functions"]["private_wire_validators"]:
        name = entry["name"].split(".")[-1]
        assert function_body(name)
        header = SQL.split("CREATE FUNCTION maezo_d7_control." + name + "(", 1)[1].split("AS $d7$", 1)[0]
        assert "VOLATILE PARALLEL UNSAFE SECURITY DEFINER" in header
        assert "SET search_path=pg_catalog,pg_temp" in header
    assert "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA maezo_d7_control FROM PUBLIC" in SQL
    assert "depth<=32" in function_body("_w1") and "depth<=33" in function_body("_wire")
    assert "parsed:=txt::json" in function_body("_wire") and "count(DISTINCT key" in function_body("_w1")
    assert "maezo_d7_control._b64(b)=v" in function_body("_unb64")


def test_sql_receipt_and_native_retirement_source_controls_are_retained_for_live_lane() -> None:
    helper = function_body("lock_runtime_v2_scope")
    issuer = function_body("_issuer")
    assert helper.index("MZO_PROVISIONING_FENCE") < helper.index("MZO_RUNTIME_ADMISSION")
    assert "SESSION_USER=g.login_name" in helper and "g.status='RETIRED'" in helper
    assert "CASE WHEN retiring THEN NULL ELSE g.valid_until_ms END" in helper
    assert helper.index("now_ms:=maezo_d7_control._now_ms()") > helper.index("FOR UPDATE")
    assert (
        issuer.index("-- Current authorization")
        < issuer.index("IF FOUND THEN\n  PERFORM maezo_d7_control._need(r.tenant")
        < issuer.index("f.revision=(q->>'expected_fence_revision')")
    )
    assert "activation_decision_bytes" in issuer and "decision_wire" in issuer
    assert "x.event='NATIVE_QUALIFIED'" in issuer and "g.status<>'RETIRED'" in issuer
    assert "request_bytes=request_wire" in issuer


def test_owner_input_is_closed_no_credentials_and_identifier_escaping_is_fixed() -> None:
    value = input_record()
    assert owner.installation_input(s.canonical(value)) == value
    for key in ("password", "dsn", "role_sql", "execute"):
        bad = copy.deepcopy(value)
        bad[key] = "secret"
        with pytest.raises(s.Refusal):
            owner.installation_input(s.canonical(bad))
    assert owner.identifier('quoted"role') == '"quoted""role"'
    with pytest.raises(s.Refusal):
        owner.identifier("a" * 64)
    with pytest.raises(s.Refusal):
        owner.OwnerInstaller(None, PACKAGE, SQL.encode(), None)


@pytest.mark.parametrize(
    "field", ["rolsuper", "rolcreatedb", "rolreplication", "rolbypassrls", "rolcreaterole", "rolcanlogin"]
)
def test_owner_catalog_rejects_privileged_or_login_guard_even_with_recomputed_digest(field: str) -> None:
    value = catalog()
    guard = next(r for r in value["roles"] if r["role_class"] == "D guard-function owner")
    guard[field] = True
    with pytest.raises(s.Refusal, match="AUTH_REFUSED"):
        owner.catalog_check(rehash(value), owner_login_oid=999)


@pytest.mark.parametrize("option", ["admin_option", "inherit_option", "set_option"])
def test_recursive_owner_membership_is_not_accepted_by_manifest_claim(option: str) -> None:
    value = catalog()
    issuer = next(r for r in value["roles"] if r["role_class"] == "actual issuer login")
    target = next(r for r in value["roles"] if r["role_class"] == "D issuer-function owner")
    value["memberships"] = [
        dict(
            role_oid=target["role_oid"],
            member_oid=issuer["role_oid"],
            grantor_oid=999,
            admin_option=False,
            inherit_option=False,
            set_option=False,
        )
    ]
    value["memberships"][0][option] = True
    with pytest.raises(s.Refusal):
        owner.catalog_check(rehash(value), owner_login_oid=999)


@pytest.mark.parametrize(
    "role_class,object_class,object_name,privilege,grantable",
    [
        ("native function owner", "table", "MZO_RUNTIME_ADMISSION", "SELECT", False),
        ("native function owner", "function", "prepare_generation", "EXECUTE", False),
        ("actual issuer login", "function", "_issuer", "EXECUTE", False),
        ("scoped D observer login", "function", "open_runtime_generation", "EXECUTE", False),
        ("D receipt-function owner", "table", "MZO_PROVISIONING_FENCE", "SELECT", False),
        ("D guard-function owner", "table", "MZO_RUNTIME_ADMISSION", "INSERT", False),
        ("native function owner", "function", "lock_runtime_v2_scope", "EXECUTE", True),
    ],
)
def test_role_catalog_does_not_allow_extra_object_routes(
    role_class: str, object_class: str, object_name: str, privilege: str, grantable: bool
) -> None:
    value = catalog()
    oid = next(r["role_oid"] for r in value["roles"] if r["role_class"] == role_class)
    value["object_grants"] = [
        dict(
            object_class=object_class,
            schema_name=owner.SCHEMA,
            object_name=object_name,
            object_oid=200,
            function_argument_types=["jsonb"] if object_class == "function" else [],
            column_names=[],
            grantee_oid=oid,
            grantor_oid=100,
            privilege=privilege,
            grantable=grantable,
        )
    ]
    with pytest.raises(s.Refusal):
        owner.catalog_check(rehash(value), owner_login_oid=999)


def test_real_owner_receipt_insert_constructor_carries_all_seventh_table_columns() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, sql: str, params: Any = ()) -> None:
            self.calls.append((sql, params))

        def fetchone(self) -> tuple[int]:
            return (100,)

    class Authority:
        pass

    obj = owner.OwnerInstaller(SimpleNamespace(), PACKAGE, SQL.encode(), Authority())
    cursor = Cursor()
    prep = preparation()
    p = prep["principal"] | {"login_oid": 17}
    core = {k: v for k, v in p.items() if k != "kind"} | {"scope": prep["scope"], "epoch": prep["epoch"]}
    version = {"installation_binding_bytes": "\\x" + s.canonical({"control_scope_id": U}).hex()}
    wire = obj._owner_receipt(
        cursor,
        prep,
        s.canonical(prep),
        ("migration", "migration", 999, 1, "synthetic", 160004, "read committed"),
        version,
        p,
        core,
        catalog(),
        H,
        H,
        "PREPARED",
        None,
    )
    parsed = s.Document("OwnerPreparationResult", wire)
    assert parsed.value()["principal"]["login_oid"] == 17 and parsed.value()["generation_core"] == core
    statement, parameters = next((sql, args) for sql, args in cursor.calls if sql.startswith("INSERT"))
    columns = re.findall(r'"([a-z_0-9]+)"', statement)
    assert set(columns) == set(PACKAGE["tables"]["MZO_OWNER_PREPARATION_RECEIPT"]["columns"])
    assert len(parameters) == len(columns) and "private" not in wire.decode()


def test_existing_owner_operation_exact_replay_preserves_bytes_changed_input_conflicts() -> None:
    class Cursor:
        def execute(self, *args: Any) -> None:
            pass

        def fetchone(self) -> tuple[bytes, bytes]:
            return (b"{}", b"{}")

    obj = owner.OwnerInstaller(SimpleNamespace(), PACKAGE, SQL.encode(), SimpleNamespace())
    with pytest.raises(s.Refusal, match="REQUEST_CONFLICT"):
        obj._replay(Cursor(), {"owner_operation_id": U}, b'{"different":1}')


def test_bounded_nofollow_package_custody(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    sql = tmp_path / "schema.sql"
    package.write_bytes(PACKAGE_PATH.read_bytes())
    sql.write_bytes(SQL_PATH.read_bytes())
    package.chmod(0o600)
    sql.chmod(0o600)
    result, wire = owner.read_package(
        package,
        sql,
        expected_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
        expected_uid=package.stat().st_uid,
    )
    assert result["sql_sha256"] == hashlib.sha256(wire).hexdigest()
    link = tmp_path / "link"
    link.symlink_to(sql)
    with pytest.raises(s.Refusal):
        owner.read_package(
            package,
            link,
            expected_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            expected_uid=package.stat().st_uid,
        )
