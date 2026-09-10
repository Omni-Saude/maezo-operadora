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

from maezo.platform.engine_bootstrap import controller_contracts as c
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


def forbidden_statements(sql: str) -> list[str]:
    # Remove literal/identifier contents and comments, retaining PL/pgSQL statements
    # inside dollar-quoted bodies. Keywords inside a JSON enum are not statements.
    tokens = re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", sql, flags=re.S)
    return re.findall(
        r"\b(?:COMMIT|ROLLBACK|CREATE\s+ROLE|CREATE\s+EXTENSION|IF\s+NOT\s+EXISTS)\b", tokens, re.I
    )


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT 'ROLLBACK'; -- COMMIT\n", False),
        ("SELECT 'it''s COMMIT';", False),
        ('SELECT "ROLLBACK";', False),
        ("COMMIT;", True),
        ("DO $d7$ BEGIN ROLLBACK; END $d7$;", True),
        ("CREATE\nROLE intrusive;", True),
        ("CREATE EXTENSION pgcrypto;", True),
        ("CREATE TABLE IF NOT EXISTS bad(x int);", True),
    ],
)
def test_statement_inspection_distinguishes_executable_tokens(sql: str, expected: bool) -> None:
    assert bool(forbidden_statements(sql)) is expected


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
    assert not forbidden_statements(SQL)


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

        def fetchone(self) -> tuple[int, int]:
            return (100, 99)

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


def receipt_mapping() -> list[dict[str, Any]]:
    return sorted(
        [
            dict(
                purpose="receipt_read",
                native_actor="native-" + r["role_name"],
                login_name=r["role_name"],
                login_oid=r["role_oid"],
            )
            for r in catalog()["roles"]
            if r["role_class"] in {"actual issuer login", "scoped D observer login"}
        ],
        key=lambda p: tuple(p[k] for k in ("purpose", "native_actor", "login_name", "login_oid")),
    )


def test_owner_singleton_mapping_accepts_both_qualified_receipt_readers() -> None:
    owner.singleton_receipt_principals(catalog()["roles"], receipt_mapping())


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign_oid", "foreign_name", "wrong_purpose"])
def test_owner_singleton_mapping_refuses_incomplete_or_foreign_tuple(fault: str) -> None:
    mapping = receipt_mapping()
    if fault == "missing":
        mapping.pop()
    elif fault == "duplicate":
        mapping.append(mapping[0] | {"native_actor": "other-actor"})
    else:
        field, replacement = {
            "foreign_oid": ("login_oid", 999),
            "foreign_name": ("login_name", "other"),
            "wrong_purpose": ("purpose", "grant"),
        }[fault]
        mapping[0][field] = replacement
    mapping.sort(key=lambda p: tuple(p[k] for k in ("purpose", "native_actor", "login_name", "login_oid")))
    with pytest.raises(s.Refusal):
        owner.singleton_receipt_principals(catalog()["roles"], mapping)


def test_singleton_sql_has_current_guarded_read_and_origin_dependent_catalog_mutation() -> None:
    read = function_body("read_issuer_operation")
    assert "PERFORM maezo_d7_control._need(false,'P7D09');RETURN NULL" not in read.split("EXCEPTION", 1)[0]
    assert read.index("lock_provisioning_scope") < read.index("SELECT * INTO r")
    assert "q-ARRAY['protocol','read_guard']" in read
    issuer = function_body("_issuer")
    assert issuer.index("_permit_origin(body") < issuer.index("SELECT * INTO r")
    assert "p.state='ISSUED'" in issuer[: issuer.index("SELECT * INTO r")]
    assert (
        "p.principal_origin->>'kind'='owner_prepared_one_shot' THEN PERFORM maezo_d7_control._login" in issuer
    )
    assert "CREATE UNIQUE INDEX d7_singleton_issued" in SQL
    resolver = function_body("_permit_origin")
    assert "matches=1 AND actor=origin->>'native_actor'" in resolver
    assert "binding->'issuer_scopes'" in resolver
    assert "RETURN actor" in resolver


def test_installation_scope_retention_is_required_in_source_and_metadata() -> None:
    source = INSTALLER_PATH.read_text()
    assert '"issuer_scopes": value["issuer_scopes"]' in source
    assert "singleton_receipt_principals(roles, operational)" in source
    assert "issuer_scopes" in PACKAGE["installation"]["closed_records"]["InstallationBinding"]["fields"]


class InstallationProtocolConnection:
    """Actual installer SQL/insert constructor calls against finite DB-API responses."""

    def __init__(self) -> None:
        self.autocommit = False
        self.info = SimpleNamespace(transaction_status=0)
        self.calls: list[Any] = []
        self.row: Any = None
        self.version: dict[str, Any] | None = None
        self.manifest = catalog()
        self.manifest["roles"].sort(key=lambda r: (r["role_class"], r["role_name"]))
        self.manifest["operational_principals"] = receipt_mapping()
        rehash(self.manifest)

    def cursor(self) -> Any:
        return self

    def execute(self, sql: str, params: Any = ()) -> None:
        self.calls.append((sql, params))
        if sql == owner.SESSION_SQL:
            self.row = ("migration", "migration", 999, 1, "synthetic", 160004, "read committed")
        elif sql == owner.ROLE_SQL:
            r = next(r for r in self.manifest["roles"] if r["role_name"] == params[0])
            self.row = (r["role_oid"], r["role_name"], *(r[k] for k in owner.ROLE_FIELDS.split()[3:]))
        elif sql.startswith("SELECT pg_catalog.pg_has_role"):
            self.row = (True, True)
        elif "to_regnamespace" in sql:
            self.row = (77 if self.version else None,)
        elif sql == owner.VERSION_SQL:
            self.row = (self.version,)
        elif sql.startswith("SELECT maezo_d7_control._owner_catalog"):
            self.row = (self.manifest,)
        elif sql == owner.OBJECTS_SQL:
            self.row = ([dict(object_class="table", object_name=name) for name in PACKAGE["tables"]],)
        elif "SELECT oid::bigint FROM pg_catalog.pg_namespace" in sql:
            self.row = (77,)
        elif sql == "SELECT maezo_d7_control._now_ms()":
            self.row = (100,)

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> list[Any]:
        return [
            (f["name"].split(".")[-1], ",".join(f["arguments"]).replace("pg_catalog.", ""), 200 + i)
            for i, f in enumerate(
                PACKAGE["functions"]["functions"] + PACKAGE["functions"]["private_wire_validators"]
            )
        ]

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


class InstallationProtocolAuthority:
    def __init__(self) -> None:
        from tests.unit.platform.engine_bootstrap.test_controller_dynamodb import initial_root

        self.initial = initial_root()
        self.calls: list[Any] = []

    def installation(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return owner.InstallationPrerequisites((self.initial,), s.canonical(receipt_mapping()))

    def catalog(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_actual_installer_initial_columns_and_exact_empty_installation_replay() -> None:
    conn, authority = InstallationProtocolConnection(), InstallationProtocolAuthority()
    installer = owner.OwnerInstaller(conn, PACKAGE, SQL.encode(), authority)
    value = input_record()
    result = installer.install(s.canonical(value))
    assert result.kind == "COMMITTED", result.code
    binding = s.parse_wire(result.receipt_wire)
    assert binding["issuer_scopes"] == value["issuer_scopes"]
    inserts = [row for row in conn.calls if isinstance(row, tuple) and row[0].startswith("INSERT")]
    assert len(inserts) == 2
    version_sql, version_values = inserts[0]
    fields = re.findall(r'"([a-z_0-9]+)"', version_sql)
    conn.version = {
        k: ("\\x" + v.hex() if isinstance(v, bytes) else v)
        for k, v in zip(fields, version_values, strict=True)
    }
    conn.calls.clear()
    replay = installer.install(s.canonical(value))
    assert replay.kind == "COMMITTED" and replay.receipt_wire == result.receipt_wire
    assert not [row for row in conn.calls if isinstance(row, tuple) and row[0].startswith("INSERT")]


@pytest.mark.parametrize("clock_now,previous", [(100, 100), (99, 100)])
def test_owner_receipt_never_invents_future_commit_time(clock_now: int, previous: int) -> None:
    class ClockCursor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def execute(self, sql: str, params: Any = ()) -> None:
            self.calls.append(sql)

        def fetchone(self) -> tuple[int, int]:
            return clock_now, previous

    cursor = ClockCursor()
    installer = owner.OwnerInstaller(SimpleNamespace(), PACKAGE, SQL.encode(), SimpleNamespace())
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        installer._owner_receipt(cursor, {}, b"{}", (), {}, {}, None, {}, H, H, "PREPARED", None)
    assert len(cursor.calls) == 1 and "INSERT" not in cursor.calls[0]


@pytest.mark.parametrize("authorized", [False, True])
def test_prepare_replay_rechecks_full_catalog_and_current_external_authority(authorized: bool) -> None:
    value = preparation()
    calls: list[Any] = []

    class Cursor:
        def execute(self, sql: str, params: Any = ()) -> None:
            calls.append(sql)

    class Authority:
        def replay(self, **kwargs: Any) -> None:
            calls.append(kwargs)
            if not authorized:
                raise s.Refusal("AUTH_REFUSED")

    class ReplayInstaller(owner.OwnerInstaller):
        def _role(self, *args: Any) -> dict[str, Any]:
            return {"role_oid": 17}

        def _owner_context(self, *args: Any) -> tuple[Any, Any, Any]:
            return (
                dict(
                    mode="CLOSED",
                    current_generation_id=None,
                    restore_state="RECONCILED",
                    database_binding_bytes="\\x" + s.canonical(value["database_binding"]).hex(),
                ),
                {},
                None,
            )

        def _replay(self, *args: Any) -> bytes:
            return b'{"retained":"exact"}'

    installer = ReplayInstaller(SimpleNamespace(), PACKAGE, SQL.encode(), Authority())
    if authorized:
        assert installer._prepare(Cursor(), s.canonical(value), ("owner",)) == b'{"retained":"exact"}'
    else:
        with pytest.raises(s.Refusal, match="AUTH_REFUSED"):
            installer._prepare(Cursor(), s.canonical(value), ("owner",))
    assert calls[0] == "SELECT maezo_d7_control._assert_catalog()"
    assert calls[1]["input_wire"] == s.canonical(value)
    assert calls[1]["retained_result_wire"] == b'{"retained":"exact"}'


def test_native_append_result_projection_uses_actual_closed_body_grammar() -> None:
    """Schema/source projection control, explicitly not PostgreSQL execution."""
    sql = function_body("append_provisioning_receipt")
    projection = sql[sql.index("body:=maezo_d7_control._wire(result_wire)") : sql.index("SELECT * INTO r")]
    fields = set(re.findall(r"body->>?'([^']+)'", projection))
    permitted = set(c._DEFINITIONS["DeployResultBody"]["properties"]) | set(
        c._DEFINITIONS["GrantResultBody"]["properties"]
    )
    assert fields <= permitted
    assert fields == {"operation", "tenant", "deployment_receipt"}


def prepare_through_actual_owner_source(
    reserved: s.Document,
    reserved_wire: bytes,
    purpose: str = "candidate",
    credential_binding_sha256: str | None = None,
) -> tuple[Any, Any]:
    """Execute the owner constructor using finite SQL responses, not real PG authority."""
    value = preparation(purpose)
    if credential_binding_sha256 is not None:
        value["principal"]["credential_binding_sha256"] = credential_binding_sha256
    value.update(
        scope=reserved.value()["scope"], run_id=reserved.value()["run_id"], epoch=reserved.value()["epoch"]
    )
    value["principal"]["generation_id"] = s.parse_wire(reserved_wire)["generation_id"]
    prepared_role = dict(
        role_class="generation login",
        role_name=value["principal"]["login_name"],
        role_oid=17,
        **{k: False for k in owner.ROLE_FIELDS.split()[3:]},
    )

    class PreparationConnection(InstallationProtocolConnection):
        def __init__(self) -> None:
            super().__init__()
            self.version = dict(
                installation_id=U,
                migration_sha256=PACKAGE["sql_sha256"],
                role_acl_manifest_bytes="\\x" + s.canonical(self.manifest).hex(),
                installation_binding_bytes="\\x" + s.canonical({"control_scope_id": U}).hex(),
            )
            self.fence = dict(
                **{
                    k: value["scope"][k]
                    for k in ("account", "region", "tenant", "environment", "engine_name")
                },
                epoch=value["epoch"],
                owner_run_id=value["run_id"],
                mode="CLOSED",
                current_generation_id=None,
                restore_state="RECONCILED",
                database_binding_bytes="\\x" + s.canonical(value["database_binding"]).hex(),
            )

        def execute(self, sql: str, params: Any = ()) -> None:
            if sql == owner.ROLE_SQL and params[0] == prepared_role["role_name"]:
                self.calls.append((sql, params))
                self.row = (
                    17,
                    prepared_role["role_name"],
                    *(prepared_role[k] for k in owner.ROLE_FIELDS.split()[3:]),
                )
                return
            super().execute(sql, params)
            if sql == owner.FENCE_LOCK:
                self.row = (self.fence,)
            elif sql == owner.GENERATION_LOCK:
                self.row = None
            elif sql == owner.OWNER_LOCK:
                self.row = (self.version,)
            elif sql.startswith("SELECT request_bytes,result_bytes") or sql.startswith(
                "SELECT result_bytes FROM"
            ):
                self.row = None
            elif sql.startswith("SELECT pg_catalog.has_database_privilege"):
                self.row = (False,)
            elif sql.startswith("SELECT count(*)"):
                self.row = (0,)
            elif sql.startswith("SELECT maezo_d7_control._owner_catalog"):
                after = copy.deepcopy(self.manifest)
                after["roles"].append(prepared_role)
                after["roles"].sort(key=lambda r: (r["role_class"], r["role_name"]))
                self.row = (rehash(after),)
            elif sql.startswith("SELECT maezo_d7_control._now_ms(),COALESCE"):
                self.row = (100, -1)

    class PreparationAuthority:
        def preparation(self, **kwargs: Any) -> str:
            request = s.Document("OwnerPreparationInput", kwargs["input_wire"]).value()
            reservation = s.parse_wire(reserved_wire)
            assert reservation["state"] == "RESERVED" and reservation["tombstone"] is True
            assert (
                request["principal"]["generation_id"]
                == reservation["generation_id"]
                < reserved.value()["next_generation_id"]
            )
            assert kwargs["actual_login_oid"] == 17
            return H

        def catalog(self, **kwargs: Any) -> None:
            before, after = s.parse_wire(kwargs["before_wire"]), s.parse_wire(kwargs["after_wire"])
            assert [r for r in after["roles"] if r["role_oid"] != 17] == before["roles"]
            assert (
                after["memberships"] == before["memberships"]
                and after["object_grants"] == before["object_grants"]
            )

    connection = PreparationConnection()
    installer = owner.OwnerInstaller(connection, PACKAGE, SQL.encode(), PreparationAuthority())
    result = installer.prepare(s.canonical(value))
    assert result.kind == "COMMITTED", result
    return s.Document("OwnerPreparationResult", result.receipt_wire), connection


def test_embedded_sql_definitions_equal_current_contract_byte_for_byte() -> None:
    match = re.search(
        r"CREATE FUNCTION maezo_d7_control\._definitions\(\).*?SELECT '(.*?)'::pg_catalog.jsonb", SQL, re.S
    )
    assert match is not None
    embedded = match.group(1).replace("''", "'")
    assert embedded.encode() == json.dumps(c._DEFINITIONS, separators=(",", ":"), ensure_ascii=False).encode()
    assert json.loads(embedded) == c._DEFINITIONS
