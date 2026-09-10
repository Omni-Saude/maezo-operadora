"""Explicit D owner installation and immutable preparation-event readback.

This module is inert. A gateway-owned connection and independently qualified owner
resource/native readback ports are required. It discovers no credentials and never
creates roles, installs an extension, changes native objects or runs on import.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import (
    Document,
    Refusal,
    canonical,
    decode64,
    digest,
    encode64,
    exact,
    parse_wire,
    present,
    require,
    scalar,
    validate_native_qualification,
    validate_owner_result,
)

SCHEMA = "maezo_d7_control"
COMPONENT = "maezo-d7-control"
SINGLETONS = (
    "D schema owner",
    "D guard-function owner",
    "D issuer-function owner",
    "D receipt-function owner",
    "actual issuer login",
    "native function owner",
    "scoped D observer login",
)
OWNER_INPUT = (
    "protocol installation_id control_scope_id component version database_binding "
    "act_schema_name controller_table_arn source_manifest_sha256 cib_abi_sha256 "
    "roles issuer_scopes prepared_principals"
)
ROLE_FIELDS = (
    "role_class role_name role_oid rolcanlogin rolsuper rolcreaterole rolcreatedb "
    "rolreplication rolbypassrls rolinherit"
)
ROLE_SQL = """SELECT oid::bigint,rolname,rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,
 rolreplication,rolbypassrls,rolinherit FROM pg_catalog.pg_roles WHERE rolname=%s"""
SESSION_SQL = (
    "SELECT "
    "SESSION_USER,CURRENT_USER,r.oid::bigint,d.oid::bigint,d.datname,\n "
    "pg_catalog.current_setting('server_version_num')::int,\n "
    "pg_catalog.current_setting('transaction_isolation') FROM "
    "pg_catalog.pg_roles r\n CROSS JOIN pg_catalog.pg_database d WHERE "
    "r.rolname=SESSION_USER AND "
    "d.datname=pg_catalog.current_database()"
)
VERSION_SQL = (
    "SELECT pg_catalog.to_jsonb(v) FROM "
    'maezo_d7_control."MZO_PROVISIONING_SCHEMA_VERSION" v WHERE '
    "component=%s AND version=1"
)
FENCE_LOCK = (
    "SELECT pg_catalog.to_jsonb(f) FROM "
    'maezo_d7_control."MZO_PROVISIONING_FENCE" f WHERE tenant=%s AND '
    "environment=%s AND engine_name=%s FOR UPDATE"
)
GENERATION_LOCK = (
    "SELECT pg_catalog.to_jsonb(g) FROM "
    'maezo_d7_control."MZO_RUNTIME_ADMISSION" g WHERE tenant=%s AND '
    "environment=%s AND engine_name=%s AND generation_id=%s FOR UPDATE"
)
OWNER_LOCK = (
    "SELECT pg_catalog.to_jsonb(v) FROM "
    'maezo_d7_control."MZO_PROVISIONING_SCHEMA_VERSION" v WHERE '
    "component=%s AND version=1 FOR UPDATE"
)
OBJECTS_SQL = (
    "WITH objects AS (\n SELECT 'schema'::text AS "
    "object_class,n.oid,n.nspowner AS owner_oid,n.nspname AS "
    "object_name FROM pg_catalog.pg_namespace n WHERE "
    "n.nspname='maezo_d7_control'\n UNION ALL SELECT "
    "'table',c.oid,c.relowner,c.relname FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE "
    "n.nspname='maezo_d7_control' AND c.relkind='r'\n UNION ALL SELECT "
    "'function',p.oid,p.proowner,p.proname FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE "
    "n.nspname='maezo_d7_control')\n SELECT "
    "pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('schema_name',"
    "'maezo_d7_control',\n "
    "'object_name',o.object_name,'object_class',o.object_class,'object"
    "_oid',o.oid::bigint,\n "
    "'owner_name',r.rolname,'owner_oid',o.owner_oid::bigint,\n "
    "'definition_sha256',maezo_d7_control._object_definition(o.oid,o.o"
    "bject_class),\n 'function_argument_types',CASE WHEN "
    "o.object_class='function' THEN "
    "pg_catalog.to_jsonb(COALESCE(pg_catalog.string_to_array(pg_catalo"
    "g.oidvectortypes(p.proargtypes),', '),ARRAY[]::text[])) ELSE "
    "'[]'::jsonb END,\n "
    "'security_definer',p.prosecdef,'volatility',p.provolatile::text,'"
    "parallel',p.proparallel::text,\n 'search_path',CASE WHEN "
    "o.object_class='function' THEN pg_catalog.to_jsonb(p.proconfig) "
    'ELSE NULL END)\n ORDER BY o.object_class COLLATE "C",o.object_name '
    'COLLATE "C",o.oid)\n FROM objects o JOIN pg_catalog.pg_roles r ON '
    "r.oid=o.owner_oid LEFT JOIN pg_catalog.pg_proc p ON p.oid=o.oid "
    "AND o.object_class='function'"
)
FUNCTIONS_SQL = """SELECT p.proname,pg_catalog.oidvectortypes(p.proargtypes),p.oid::bigint
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='maezo_d7_control' ORDER BY p.proname COLLATE "C",p.oid LIMIT 1025"""


class OwnerCursor(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> Any: ...


class OwnerConnection(Protocol):
    autocommit: bool
    info: Any

    def cursor(self) -> OwnerCursor: ...
    def commit(self) -> Any: ...
    def rollback(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class InstallationPrerequisites:
    """Output from the existing qualified owner boundary, never a wire authority."""

    roots: tuple[Document, ...]
    operational_principals_wire: bytes

    def __post_init__(self) -> None:
        require(type(self.roots) is tuple and 1 <= len(self.roots) <= 1024)
        for root in self.roots:
            require(type(root) is Document and root.kind == "Root")
        _operational(parse_wire(self.operational_principals_wire))


class OwnerAuthority(Protocol):
    """Actual external resource ownership/strong controller read qualification.

    No implementation is supplied here. The owner gateway must bind the existing
    migration principal, actual full DB identity, table installation, reserved
    generation/root, immutable secret resource metadata and native actor inventory.
    Secret values are neither arguments nor results of this interface.
    """

    def installation(
        self, *, input_wire: bytes, session: tuple[Any, ...], role_catalog_wire: bytes
    ) -> InstallationPrerequisites: ...
    def preparation(
        self, *, input_wire: bytes, session: tuple[Any, ...], fence_wire: bytes, actual_login_oid: int
    ) -> str: ...
    def removal(
        self, *, input_wire: bytes, birth_wire: bytes, session: tuple[Any, ...], fence_wire: bytes
    ) -> str: ...
    def replay(
        self, *, input_wire: bytes, retained_result_wire: bytes, fence_wire: bytes, session: tuple[Any, ...]
    ) -> None: ...
    def catalog(
        self, *, before_wire: bytes | None, after_wire: bytes, input_wire: bytes, session: tuple[Any, ...]
    ) -> None: ...


class NativeOwnerAuthority(Protocol):
    """Separate unavailable native owner operation; no accepting default."""

    def verify(
        self,
        *,
        qualification_wire: bytes,
        birth_wire: bytes,
        generation_wire: bytes,
        session: tuple[Any, ...],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class OwnerResult:
    kind: str
    receipt_wire: bytes | None
    code: str | None

    def __post_init__(self) -> None:
        require(self.kind in {"COMMITTED", "REFUSED", "UNKNOWN"})
        if self.kind == "COMMITTED":
            parse_wire(self.receipt_wire)
            require(self.code is None)
        else:
            require(self.receipt_wire is None)
            scalar("RefusalCode", self.code)


def identifier(name: str) -> str:
    scalar("Id", name)
    require(len(name.encode()) <= 63)
    return '"' + name.replace('"', '""') + '"'


def _operational(values: Any) -> None:
    require(type(values) is list and len(values) <= 1024)
    keys = []
    for row in values:
        exact(row, "purpose native_actor login_name login_oid")
        require(row["purpose"] in {"deploy", "grant", "receipt_read"})
        scalar("Id", row["native_actor"])
        scalar("Id", row["login_name"])
        scalar("Oid", row["login_oid"])
        keys.append((row["purpose"], row["native_actor"], row["login_name"], row["login_oid"]))
    require(keys == sorted(set(keys)))


def singleton_receipt_principals(roles: list[dict[str, Any]], operational: list[dict[str, Any]]) -> None:
    """Compare qualified owner output to exact persistent receipt-reader identities."""
    _operational(operational)
    for role_class in ("actual issuer login", "scoped D observer login"):
        selected = [r for r in roles if r["role_class"] == role_class]
        require(len(selected) == 1, "AUTH_REFUSED")
        role = selected[0]
        matches = [
            p
            for p in operational
            if p["purpose"] == "receipt_read"
            and (p["login_name"] == role["role_name"] or p["login_oid"] == role["role_oid"])
        ]
        require(
            len(matches) == 1
            and matches[0]["login_name"] == role["role_name"]
            and matches[0]["login_oid"] == role["role_oid"],
            "AUTH_REFUSED",
        )


def installation_input(wire: bytes) -> dict[str, Any]:
    value = exact(parse_wire(wire), OWNER_INPUT)
    require(
        value["protocol"] == "maezo.d7-control-installation.v1"
        and value["component"] == COMPONENT
        and value["version"] == 1
        and type(value["version"]) is int
    )
    for key in ("installation_id", "control_scope_id"):
        scalar("Uuid", value[key])
    for key in ("source_manifest_sha256", "cib_abi_sha256"):
        scalar("Sha256", value[key])
    scalar("DatabaseBinding", value["database_binding"])
    scalar("Arn", value["controller_table_arn"])
    identifier(value["act_schema_name"])
    roles = value["roles"]
    require(type(roles) is list and len(roles) == len(SINGLETONS))
    for role in roles:
        exact(role, "role_class role_name")
        identifier(role["role_name"])
    require(
        {r["role_class"] for r in roles} == set(SINGLETONS)
        and len({r["role_name"] for r in roles}) == len(SINGLETONS)
    )
    require(roles == sorted(roles, key=lambda r: (r["role_class"], r["role_name"])))
    scopes = value["issuer_scopes"]
    require(type(scopes) is list and 1 <= len(scopes) <= 1024)
    require([canonical(s) for s in scopes] == sorted(set(canonical(s) for s in scopes)))
    for scope in scopes:
        scalar("Scope", scope)
        require(
            scope["database_binding"] == value["database_binding"]
            and value["controller_table_arn"].startswith(
                "arn:aws:dynamodb:" + scope["region"] + ":" + scope["account"] + ":table/"
            ),
            "SCOPE_REFUSED",
        )
    require(type(value["prepared_principals"]) is list and len(value["prepared_principals"]) <= 1024)
    for principal in value["prepared_principals"]:
        Document.create("OwnerPreparationInput", principal)
    return value


def catalog_check(manifest: dict[str, Any], *, owner_login_oid: int) -> None:
    """Reject privilege routes even if a caller copied them into an expected manifest."""
    exact(
        manifest,
        ("protocol installation_id roles memberships object_grants operational_principals catalog_sha256"),
    )
    require(manifest["protocol"] == "maezo.d7-role-acl-manifest.v1")
    require(
        digest(canonical({k: v for k, v in manifest.items() if k != "catalog_sha256"}))
        == manifest["catalog_sha256"]
    )
    roles = manifest["roles"]
    require(type(roles) is list and 1 <= len(roles) <= 1024)
    require(len({r["role_oid"] for r in roles}) == len(roles) == len({r["role_name"] for r in roles}))
    by_class: dict[str, list[int]] = {}
    for role in roles:
        exact(role, ROLE_FIELDS)
        scalar("Oid", role["role_oid"])
        identifier(role["role_name"])
        require(role["role_class"] in {*SINGLETONS, "generation login", "one-shot login"})
        require(
            all(
                type(role[k]) is bool
                for k in ROLE_FIELDS.split()
                if k.startswith("rol") and k not in {"role_class", "role_name", "role_oid"}
            )
        )
        require(
            not any(role[k] for k in ("rolsuper", "rolcreatedb", "rolreplication", "rolbypassrls")),
            "AUTH_REFUSED",
        )
        require(role["rolcreaterole"] == (role["role_class"] == "D issuer-function owner"), "AUTH_REFUSED")
        if role["role_class"] in {
            "D schema owner",
            "D guard-function owner",
            "D issuer-function owner",
            "D receipt-function owner",
            "native function owner",
        }:
            require(not role["rolcanlogin"], "AUTH_REFUSED")
        if role["role_class"] in {"actual issuer login", "scoped D observer login"}:
            require(role["rolcanlogin"], "AUTH_REFUSED")
        by_class.setdefault(role["role_class"], []).append(role["role_oid"])
    require(all(len(by_class.get(k, [])) == 1 for k in SINGLETONS))
    owners = {by_class[k][0] for k in SINGLETONS if k.startswith("D ")}
    untrusted = {
        r["role_oid"]
        for r in roles
        if r["role_class"]
        not in {
            "D schema owner",
            "D guard-function owner",
            "D issuer-function owner",
            "D receipt-function owner",
        }
    }
    issuer_owner = by_class["D issuer-function owner"][0]
    targets = {r["role_oid"] for r in roles if r["role_class"] in {"generation login", "one-shot login"}}
    edges = manifest["memberships"]
    require(type(edges) is list and len(edges) <= 1024)
    graph: dict[int, set[int]] = {}
    for edge in edges:
        exact(edge, "role_oid member_oid grantor_oid admin_option inherit_option set_option")
        for k in ("role_oid", "member_oid", "grantor_oid"):
            scalar("Oid", edge[k])
        require(all(type(edge[k]) is bool for k in ("admin_option", "inherit_option", "set_option")))
        graph.setdefault(edge["member_oid"], set()).add(edge["role_oid"])
        if edge["member_oid"] == issuer_owner and edge["role_oid"] in targets:
            require(
                edge["admin_option"] and not edge["inherit_option"] and not edge["set_option"], "AUTH_REFUSED"
            )
        elif edge["member_oid"] == issuer_owner:
            require(not edge["admin_option"], "AUTH_REFUSED")
    for start in untrusted:
        pending = [start]
        seen: set[int] = set()
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            require(len(seen) <= 1024)
            require(node not in owners and node != owner_login_oid, "AUTH_REFUSED")
            pending.extend(graph.get(node, ()))
    grants = manifest["object_grants"]
    require(type(grants) is list and len(grants) <= 1024)
    for grant in grants:
        exact(
            grant,
            (
                "object_class schema_name object_name object_oid "
                "function_argument_types column_names grantee_oid grantor_oid "
                "privilege grantable"
            ),
        )
        scalar("Oid", grant["grantee_oid"])
        scalar("Oid", grant["grantor_oid"])
        scalar("Oid", grant["object_oid"])
        require(type(grant["grantable"]) is bool)
        grantee = grant["grantee_oid"]
        if grant["schema_name"] == SCHEMA:
            classes = {r["role_oid"]: r["role_class"] for r in roles}
            allowed_functions = {
                "actual issuer login": {
                    "prepare_generation",
                    "admit_candidate_login",
                    "open_runtime_generation",
                    "retire_generation",
                    "record_current_proof",
                    "close_runtime_fence",
                    "mark_recovery",
                    "reconcile_fence",
                    "issue_permit",
                    "revoke_permit",
                    "read_issuer_operation",
                },
                "scoped D observer login": {"read_issuer_operation", "read_provisioning_receipt"},
                "native function owner": {"lock_runtime_v2_scope"},
                "one-shot login": {
                    "lock_provisioning_scope",
                    "read_provisioning_receipt",
                    "append_provisioning_receipt",
                },
                "generation login": set(),
            }
            if grantee in untrusted and grant["object_class"] == "function":
                require(grant["object_name"] in allowed_functions[classes[grantee]], "AUTH_REFUSED")
            if grantee in targets:
                row = next(r for r in roles if r["role_oid"] == grantee)
                require(row["role_class"] == "one-shot login", "AUTH_REFUSED")
            if grantee in untrusted:
                require(not grant["grantable"] and grant["privilege"] in {"USAGE", "EXECUTE"}, "AUTH_REFUSED")
                require(grant["object_class"] in {"schema", "function"}, "AUTH_REFUSED")
        if grantee == by_class["D guard-function owner"][0]:
            require(
                not grant["grantable"] and grant["privilege"] in {"USAGE", "SELECT", "UPDATE", "EXECUTE"},
                "AUTH_REFUSED",
            )
        if grantee == by_class["D receipt-function owner"][0] and grant["object_class"] in {
            "table",
            "column",
        }:
            require(
                grant["object_name"] == "MZO_PROVISIONING_RECEIPT"
                and grant["privilege"] in {"SELECT", "INSERT"},
                "AUTH_REFUSED",
            )
    _operational(manifest["operational_principals"])


def read_package(
    package_path: Path, sql_path: Path, *, expected_package_sha256: str, expected_uid: int
) -> tuple[dict[str, Any], bytes]:
    package = c.read_protected_file(
        str(package_path),
        expected_sha256=expected_package_sha256,
        expected_owner=expected_uid,
        sensitivity="public",
    )
    require(package.sha256 == expected_package_sha256, "PRECONDITION_MISMATCH")
    import json

    metadata = json.loads(package.data)
    sql = c.read_protected_file(
        str(sql_path),
        expected_sha256=metadata["sql_sha256"],
        expected_owner=expected_uid,
        sensitivity="public",
    )
    require(
        metadata["protocol"] == "maezo.d7-control-migration-package.v1"
        and metadata["component"] == COMPONENT
        and metadata["version"] == 1
        and metadata["predecessor"] is None
    )
    require(
        metadata["sql_sha256"] == sql.sha256
        and metadata["max_depth"] == 32
        and metadata["execution_authorized"] is False
    )
    require(sql.data.startswith(b"-- D7 storage source:"))
    return metadata, sql.data


class OwnerInstaller:
    def __init__(
        self,
        connection: OwnerConnection,
        metadata: dict[str, Any],
        sql_wire: bytes,
        authority: OwnerAuthority | None,
        native_authority: NativeOwnerAuthority | None = None,
    ) -> None:
        require(connection is not None and authority is not None, "UNAVAILABLE")
        require(digest(sql_wire) == metadata["sql_sha256"] and metadata["component"] == COMPONENT)
        self.connection = connection
        self.metadata = parse_wire(canonical(metadata))
        self.sql_wire = bytes(sql_wire)
        self.authority = present(authority, "UNAVAILABLE")
        self.native_authority = native_authority

    @staticmethod
    def _json(cursor: OwnerCursor, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
        require(row is not None and len(row) == 1, "UNAVAILABLE")
        value = row[0]
        if isinstance(value, str):
            import json

            value = json.loads(value)
        return parse_wire(canonical(value))

    def _session(self, cursor: OwnerCursor) -> tuple[Any, ...]:
        cursor.execute(SESSION_SQL)
        row = cursor.fetchone()
        require(row is not None and len(row) == 7, "UNAVAILABLE")
        require(row[0] == row[1] and 160000 <= row[5] < 170000 and row[6] == "read committed", "AUTH_REFUSED")
        return tuple(row)

    @staticmethod
    def _role(cursor: OwnerCursor, role_class: str, name: str) -> dict[str, Any]:
        cursor.execute(ROLE_SQL, (name,))
        row = cursor.fetchone()
        require(row is not None and len(row) == 9, "AUTH_REFUSED")
        return dict(zip(ROLE_FIELDS.split(), (role_class, row[1], row[0], *row[2:]), strict=True))

    @staticmethod
    def _owner(cursor: OwnerCursor, session: tuple[Any, ...], owner: dict[str, Any]) -> None:
        cursor.execute(
            (
                "SELECT "
                "pg_catalog.pg_has_role(%s::oid,%s::oid,'USAGE'),pg_catalog.has_da"
                "tabase_privilege(%s::oid,pg_catalog.current_database(),'CREATE')"
            ),
            (session[2], owner["role_oid"], session[2]),
        )
        row = cursor.fetchone()
        require(row == (True, True), "AUTH_REFUSED")

    def _begin(self) -> OwnerCursor:
        require(
            self.connection.autocommit is False and self.connection.info.transaction_status == 0,
            "PRECONDITION_MISMATCH",
        )
        cursor = self.connection.cursor()
        cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        return cursor

    def _catalog(
        self,
        cursor: OwnerCursor,
        roles: list[dict[str, Any]],
        operational: list[dict[str, Any]],
        installation_id: str,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._json(
                cursor,
                "SELECT maezo_d7_control._owner_catalog(%s::jsonb,%s::jsonb,%s::uuid)",
                (canonical(roles).decode(), canonical(operational).decode(), installation_id),
            ),
        )

    def _install_grants(self, cursor: OwnerCursor, roles: list[dict[str, Any]]) -> None:
        names = {r["role_class"]: identifier(r["role_name"]) for r in roles}
        schema_owner = names["D schema owner"]
        issuer = names["D issuer-function owner"]
        guard = names["D guard-function owner"]
        receipt = names["D receipt-function owner"]
        cursor.execute("ALTER SCHEMA maezo_d7_control OWNER TO " + schema_owner)
        for table, fields in self.metadata["tables"].items():
            relation = "maezo_d7_control." + identifier(table)
            cursor.execute("ALTER TABLE " + relation + " OWNER TO " + schema_owner)
            cursor.execute("GRANT SELECT ON " + relation + " TO " + guard + "," + issuer)
            if table in {"MZO_PROVISIONING_FENCE", "MZO_RUNTIME_ADMISSION", "MZO_PROVISIONING_PERMIT"}:
                cursor.execute("GRANT UPDATE ON " + relation + " TO " + guard)
                mutable = ",".join(identifier(k) for k in fields["mutable_columns"])
                cursor.execute("GRANT UPDATE (" + mutable + ") ON " + relation + " TO " + issuer)
            if table in {"MZO_RUNTIME_ADMISSION", "MZO_PROVISIONING_PERMIT", "MZO_ISSUER_OPERATION_RECEIPT"}:
                cursor.execute("GRANT INSERT ON " + relation + " TO " + issuer)
        cursor.execute('GRANT SELECT,INSERT ON maezo_d7_control."MZO_PROVISIONING_RECEIPT" TO ' + receipt)
        cursor.execute(
            "GRANT USAGE ON SCHEMA maezo_d7_control TO "
            + ",".join(names[k] for k in SINGLETONS if k != "D schema owner")
        )
        public = {f["name"].split(".")[-1]: f for f in self.metadata["functions"]["functions"]}
        private = {f["name"].split(".")[-1]: f for f in self.metadata["functions"]["private_wire_validators"]}
        cursor.execute(FUNCTIONS_SQL)
        functions = cursor.fetchall()
        require(len(functions) <= 1024)
        for name, arguments, _ in functions:
            identifier(name)
            require(
                all(
                    a.strip()
                    in {
                        "jsonb",
                        "json",
                        "bytea",
                        "text",
                        "text[]",
                        "integer",
                        "boolean",
                        "oid",
                        "oid[]",
                        "uuid",
                        'maezo_d7_control."MZO_PROVISIONING_FENCE"',
                    }
                    for a in arguments.split(",")
                )
                or not arguments
            )
            signature = "maezo_d7_control." + identifier(name) + "(" + arguments + ")"
            specification = public.get(name) or private.get(name)
            owner_class = (
                specification["owner_class"]
                if specification
                else ("D guard-function owner" if name == "_receipt_incarnation" else "D schema owner")
            )
            cursor.execute("ALTER FUNCTION " + signature + " OWNER TO " + names[owner_class])
            if specification:
                callers = specification["execute_caller_classes"]
                for caller in callers:
                    if caller in names:
                        cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + names[caller])
            elif name == "_receipt_incarnation":
                cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + receipt)
            elif name.startswith("_immutable_") or name in {
                "_issuer",
                "_decision",
                "_login",
                "_close_proofs",
                "_proof",
            }:
                cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + issuer)
            elif name == "_owner_catalog":
                pass  # Only schema owner / existing migration authority can produce owner snapshots.
            else:
                cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + guard + "," + issuer)
                if name in {
                    "_need",
                    "_keys",
                    "_sha",
                    "_b64",
                    "_unb64",
                    "_w1",
                    "_wire",
                    "_canonical",
                    "_definitions",
                    "_shape",
                    "_typed",
                }:
                    cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + receipt)

    def install(self, input_wire: bytes) -> OwnerResult:
        cursor: OwnerCursor | None = None
        sent = False
        committing = False
        try:
            value = installation_input(input_wire)
            cursor = self._begin()
            session = self._session(cursor)
            roles = [self._role(cursor, r["role_class"], r["role_name"]) for r in value["roles"]]
            owner = next(r for r in roles if r["role_class"] == "D schema owner")
            self._owner(cursor, session, owner)
            require(
                session[3] == value["database_binding"]["database_oid"]
                and session[4] == value["database_binding"]["database_name"],
                "SCOPE_REFUSED",
            )
            prerequisites = self.authority.installation(
                input_wire=input_wire, session=session, role_catalog_wire=canonical(roles)
            )
            require(type(prerequisites) is InstallationPrerequisites, "UNAVAILABLE")
            roots = [r.value() for r in prerequisites.roots]
            require(
                sorted(canonical(r["scope"]) for r in roots) == [canonical(s) for s in value["issuer_scopes"]]
            )
            cursor.execute("SELECT pg_catalog.to_regnamespace('maezo_d7_control')::oid")
            namespace = cursor.fetchone()
            require(namespace is not None, "UNAVAILABLE")
            if namespace != (None,):
                version = self._json(cursor, VERSION_SQL, (COMPONENT,))
                binding_wire = self._catalog_wire(version["installation_binding_bytes"])
                binding = parse_wire(binding_wire)
                require(
                    version["installation_binding_sha256"] == digest(binding_wire), "PRECONDITION_MISMATCH"
                )
                require(
                    version["migration_sha256"] == self.metadata["sql_sha256"]
                    and version["manifest_sha256"] == value["source_manifest_sha256"],
                    "REQUEST_CONFLICT",
                )
                for field in (
                    "installation_id",
                    "control_scope_id",
                    "component",
                    "version",
                    "database_binding",
                    "act_schema_name",
                    "controller_table_arn",
                    "issuer_scopes",
                    "cib_abi_sha256",
                ):
                    require(binding[field] == value[field], "REQUEST_CONFLICT")
                manifest = parse_wire(self._catalog_wire(version["role_acl_manifest_bytes"]))
                require(
                    [{k: r[k] for k in ("role_class", "role_name")} for r in manifest["roles"]]
                    == value["roles"],
                    "REQUEST_CONFLICT",
                )
                require(not value["prepared_principals"], "REQUEST_CONFLICT")
                singleton_receipt_principals(roles, parse_wire(prerequisites.operational_principals_wire))
                require(
                    manifest["operational_principals"]
                    == parse_wire(prerequisites.operational_principals_wire),
                    "AUTH_REFUSED",
                )
                cursor.execute("SELECT maezo_d7_control._assert_catalog()")
                result = OwnerResult("COMMITTED", binding_wire, None)
                committing = True
                self.connection.commit()
                return result
            for root in roots:
                require(
                    root["control_scope_id"] == value["control_scope_id"]
                    and root["state"] == "REVIEWED"
                    and root["epoch"] == root["revision"] == root["next_generation_id"] == 1
                    and root["current_generation_id"] is None
                    and not root["pending_operation_ids"]
                    and root["restore_state"] == "UNRECONCILED"
                )
            sent = True
            cursor.execute(self.sql_wire.decode("utf8"))
            self._install_grants(cursor, roles)
            operational = parse_wire(prerequisites.operational_principals_wire)
            singleton_receipt_principals(roles, operational)
            manifest = self._catalog(cursor, value["roles"], operational, value["installation_id"])
            catalog_check(manifest, owner_login_oid=session[2])
            self.authority.catalog(
                before_wire=None, after_wire=canonical(manifest), input_wire=input_wire, session=session
            )
            objects = self._json(cursor, OBJECTS_SQL)
            require(len([o for o in objects if o["object_class"] == "table"]) == 7)
            object_manifest = {
                "protocol": "maezo.d7-object-manifest.v1",
                "installation_id": value["installation_id"],
                "objects": objects,
            }
            role_wire, object_wire = canonical(manifest), canonical(object_manifest)
            cursor.execute(
                "SELECT oid::bigint FROM pg_catalog.pg_namespace WHERE nspname=%s",
                (value["act_schema_name"],),
            )
            act = cursor.fetchone()
            require(act is not None)
            cursor.execute("SELECT oid::bigint FROM pg_catalog.pg_namespace WHERE nspname='maezo_d7_control'")
            control = cursor.fetchone()
            require(control is not None)
            binding = {
                "protocol": "maezo.d7-installation-binding.v1",
                "installation_id": value["installation_id"],
                "control_scope_id": value["control_scope_id"],
                "component": COMPONENT,
                "version": 1,
                "database_binding": value["database_binding"],
                "act_schema_name": value["act_schema_name"],
                "act_schema_oid": act[0],
                "control_schema_oid": control[0],
                "controller_table_arn": value["controller_table_arn"],
                "issuer_scopes": value["issuer_scopes"],
                "role_acl_manifest_sha256": digest(role_wire),
                "object_manifest_sha256": digest(object_wire),
                "migration_sha256": self.metadata["sql_sha256"],
                "cib_abi_sha256": value["cib_abi_sha256"],
            }
            binding_wire = canonical(binding)
            cursor.execute("SELECT maezo_d7_control._now_ms()")
            now = cursor.fetchone()[0]
            record = {
                "component": COMPONENT,
                "version": 1,
                "installation_id": value["installation_id"],
                "migration_id": self.metadata["migration_id"],
                "migration_sha256": self.metadata["sql_sha256"],
                "predecessor_sha256": None,
                "manifest_sha256": value["source_manifest_sha256"],
                "database_name": session[4],
                "database_oid": session[3],
                "act_schema_name": value["act_schema_name"],
                "act_schema_oid": act[0],
                "control_schema_oid": control[0],
                "cib_abi_sha256": value["cib_abi_sha256"],
                "role_acl_manifest_bytes": role_wire,
                "role_acl_manifest_sha256": digest(role_wire),
                "object_manifest_bytes": object_wire,
                "object_manifest_sha256": digest(object_wire),
                "installation_binding_bytes": binding_wire,
                "installation_binding_sha256": digest(binding_wire),
                "installed_by": session[0],
                "installed_by_oid": session[2],
                "recorded_before_commit_at_ms": now,
            }
            self._insert(cursor, "MZO_PROVISIONING_SCHEMA_VERSION", record)
            for root in roots:
                f: dict[str, Any] = {
                    k: None for k in self.metadata["tables"]["MZO_PROVISIONING_FENCE"]["columns"]
                }
                f.update(
                    {k: v for k, v in root["scope"].items() if k != "database_binding"},
                    database_incarnation=value["database_binding"]["database_incarnation"],
                    database_binding_bytes=canonical(value["database_binding"]),
                    database_binding_sha256=digest(canonical(value["database_binding"])),
                    epoch=1,
                    revision=1,
                    owner_run_id=root["run_id"],
                    owner_subject=root["owner_subject"],
                    mode="CLOSED",
                    candidate_sha256=root["candidate_sha256"],
                    trust_profile_sha256=root["trust_profile_sha256"],
                    next_generation_id=1,
                    controller_state="REVIEWED",
                    controller_revision=1,
                    journal_revision=0,
                    pending_index_sha256=digest(canonical([])),
                    restore_state="UNRECONCILED",
                    updated_at_ms=now,
                )
                self._insert(cursor, "MZO_PROVISIONING_FENCE", f)
            # Preparation requires the current reconciled owner proof/reserved highwater;
            # nonempty initial entries use that same exact operation and cannot open G.
            require(not value["prepared_principals"], "PRECONDITION_MISMATCH")
            cursor.execute("SELECT maezo_d7_control._assert_catalog()")
            result = OwnerResult("COMMITTED", binding_wire, None)
            committing = True
            self.connection.commit()
            return result
        except Exception as error:
            return self._failure(error, cursor, sent, committing)
        finally:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    cursor.close()

    def _insert(self, cursor: OwnerCursor, table: str, record: dict[str, Any]) -> None:
        require(table in self.metadata["tables"])
        columns = self.metadata["tables"][table]["columns"]
        require(set(record) <= set(columns) and all(d["nullable"] or k in record for k, d in columns.items()))
        # All identifiers originate in the pinned local migration catalogue, never requests.
        keys = tuple(record)
        statement = (
            "INSERT INTO maezo_d7_control."
            + identifier(table)
            + "("
            + ",".join(identifier(k) for k in keys)
            + ") VALUES ("
            + ",".join("%s" for _ in keys)
            + ")"
        )
        cursor.execute(statement, tuple(record[k] for k in keys))

    def _failure(
        self, error: Exception, cursor: OwnerCursor | None, sent: bool, committing: bool
    ) -> OwnerResult:
        code: str = error.code if isinstance(error, Refusal) else "UNAVAILABLE"
        state = getattr(error, "sqlstate", None)
        if state in {
            "P7D01",
            "P7D02",
            "P7D03",
            "P7D04",
            "P7D05",
            "P7D06",
            "P7D07",
            "P7D08",
            "P7D09",
            "P7D10",
            "P7D11",
        }:
            from maezo.platform.engine_bootstrap.controller_postgres_storage import SQLSTATES

            code = SQLSTATES[state]
        rollback_ok = False
        if cursor is not None:
            try:
                self.connection.rollback()
                rollback_ok = True
            except Exception:
                pass
        return OwnerResult("UNKNOWN" if committing or (sent and not rollback_ok) else "REFUSED", None, code)

    def prepare(self, input_wire: bytes) -> OwnerResult:
        return self._event_transaction(input_wire, False)

    def event(self, input_wire: bytes) -> OwnerResult:
        return self._event_transaction(input_wire, True)

    def _event_transaction(self, input_wire: bytes, event: bool) -> OwnerResult:
        cursor: OwnerCursor | None = None
        committing = False
        try:
            cursor = self._begin()
            session = self._session(cursor)
            wire = (
                self._event(cursor, input_wire, session)
                if event
                else self._prepare(cursor, input_wire, session)
            )
            result = OwnerResult("COMMITTED", wire, None)
            committing = True
            self.connection.commit()
            return result
        except Exception as error:
            return self._failure(error, cursor, cursor is not None, committing)
        finally:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    cursor.close()

    def _owner_context(
        self,
        cursor: OwnerCursor,
        value: dict[str, Any],
        session: tuple[Any, ...],
        generation_id: int | None,
        target_oid: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        scope = value["scope"]
        params = tuple(scope[k] for k in ("tenant", "environment", "engine_name"))
        f = self._json(cursor, FENCE_LOCK, params)
        require(f["account"] == scope["account"] and f["region"] == scope["region"], "SCOPE_REFUSED")
        if "epoch" in value:
            require(f["epoch"] == value["epoch"] and f["owner_run_id"] == value["run_id"], "STALE_EPOCH")
        g = None
        if generation_id is not None:
            cursor.execute(GENERATION_LOCK, (*params, generation_id))
            row = cursor.fetchone()
            g = row[0] if row else None
        v = self._json(cursor, OWNER_LOCK, (COMPONENT,))
        require(
            v["installation_id"] == value["installation_id"]
            and v["migration_sha256"] == self.metadata["sql_sha256"],
            "PRECONDITION_MISMATCH",
        )
        before = self._catalog_wire(v["role_acl_manifest_bytes"])
        manifest = parse_wire(before)
        owner = next(r for r in manifest["roles"] if r["role_class"] == "D schema owner")
        self._owner(cursor, session, owner)
        cursor.execute(
            (
                "SELECT maezo_d7_control._database(f) FROM "
                'maezo_d7_control."MZO_PROVISIONING_FENCE" f WHERE tenant=%s AND '
                "environment=%s AND engine_name=%s"
            ),
            params,
        )
        cursor.execute("SELECT maezo_d7_control._assert_catalog(%s::oid)", (target_oid,))
        return f, v, g

    @staticmethod
    def _catalog_wire(value: Any) -> bytes:
        # pg_catalog.to_jsonb(bytea) uses the exact PostgreSQL hex representation.
        require(type(value) is str and value.startswith("\\x"))
        try:
            wire = bytes.fromhex(value[2:])
        except ValueError:
            raise Refusal("INVALID_BODY") from None
        parse_wire(wire)
        return wire

    def _latest_manifest(self, cursor: OwnerCursor, version: dict[str, Any]) -> bytes:
        cursor.execute(
            (
                "SELECT result_bytes FROM "
                'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE '
                "installation_id=%s ORDER BY recorded_before_commit_at_ms DESC "
                "LIMIT 1"
            ),
            (version["installation_id"],),
        )
        row = cursor.fetchone()
        return (
            decode64(parse_wire(bytes(row[0]))["owner_catalog_bytes"])
            if row
            else self._catalog_wire(version["role_acl_manifest_bytes"])
        )

    def _replay(self, cursor: OwnerCursor, value: dict[str, Any], input_wire: bytes) -> bytes | None:
        cursor.execute(
            (
                "SELECT request_bytes,result_bytes FROM "
                'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE '
                "owner_operation_id=%s"
            ),
            (value["owner_operation_id"],),
        )
        row = cursor.fetchone()
        if row:
            require(bytes(row[0]) == input_wire, "REQUEST_CONFLICT")
            wire = bytes(row[1])
            Document("OwnerPreparationResult", wire)
            return wire
        return None

    def _prepare(self, cursor: OwnerCursor, input_wire: bytes, session: tuple[Any, ...]) -> bytes:
        value = Document("OwnerPreparationInput", input_wire).value()
        p = value["principal"]
        role = self._role(
            cursor, "generation login" if p["kind"] == "generation" else "one-shot login", p["login_name"]
        )
        f, v, g = self._owner_context(cursor, value, session, p.get("generation_id"), role["role_oid"])
        require(
            value["database_binding"] == parse_wire(self._catalog_wire(f["database_binding_bytes"])),
            "SCOPE_REFUSED",
        )
        require(
            f["mode"] == "CLOSED"
            and f["current_generation_id"] is None
            and f["restore_state"] == "RECONCILED",
            "PRECONDITION_MISMATCH",
        )
        replay = self._replay(cursor, value, input_wire)
        if replay is not None:
            # Replaying history still requires today's owner and complete catalog.
            cursor.execute("SELECT maezo_d7_control._assert_catalog()")
            self.authority.replay(
                input_wire=input_wire, retained_result_wire=replay, fence_wire=canonical(f), session=session
            )
            return replay
        require(g is None, "STALE_GENERATION")
        role = self._role(
            cursor, "generation login" if p["kind"] == "generation" else "one-shot login", p["login_name"]
        )
        require(
            not role["rolcanlogin"]
            and not any(
                role[k]
                for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")
            ),
            "AUTH_REFUSED",
        )
        cursor.execute(
            "SELECT pg_catalog.has_database_privilege(%s::oid,pg_catalog.current_database(),'CONNECT')",
            (role["role_oid"],),
        )
        require(cursor.fetchone() == (False,), "AUTH_REFUSED")
        resource_sha = (
            digest(
                canonical(
                    {
                        "scope": value["scope"],
                        "kind": "generation",
                        "purpose": p["purpose"],
                        "resource_identity": p["immutable_secret_resource_version"],
                    }
                )
            )
            if p["kind"] == "generation"
            else p["resource_identity_sha256"]
        )
        cursor.execute(
            (
                "SELECT count(*) FROM "
                'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE '
                "event='PREPARED' AND (login_name=%s OR login_oid=%s OR "
                "resource_identity_sha256=%s OR preparation_id=%s)"
            ),
            (p["login_name"], role["role_oid"], resource_sha, value["preparation_id"]),
        )
        require(cursor.fetchone() == (0,), "REQUEST_CONFLICT")
        cursor.execute(
            "SELECT count(*) FROM maezo_d7_control.\"MZO_OWNER_PREPARATION_RECEIPT\" WHERE event='PREPARED'"
        )
        require(cursor.fetchone()[0] < 1024, "UNAVAILABLE")
        external = self.authority.preparation(
            input_wire=input_wire, session=session, fence_wire=canonical(f), actual_login_oid=role["role_oid"]
        )
        scalar("Sha256", external)
        before = self._latest_manifest(cursor, v)
        old = parse_wire(before)
        roles = [{"role_class": r["role_class"], "role_name": r["role_name"]} for r in old["roles"]]
        require(all(r["role_name"] != p["login_name"] for r in roles), "REQUEST_CONFLICT")
        roles.append({"role_class": role["role_class"], "role_name": role["role_name"]})
        operational = old["operational_principals"]
        if p["kind"] == "one_shot":
            operational = sorted(
                [
                    *operational,
                    {k: p[k] for k in ("purpose", "native_actor", "login_name")}
                    | {"login_oid": role["role_oid"]},
                ],
                key=lambda r: tuple(r[k] for k in ("purpose", "native_actor", "login_name", "login_oid")),
            )
        after = self._catalog(cursor, roles, operational, value["installation_id"])
        catalog_check(after, owner_login_oid=session[2])
        self.authority.catalog(
            before_wire=before, after_wire=canonical(after), input_wire=input_wire, session=session
        )
        core = (
            {k: val for k, val in p.items() if k != "kind"}
            | {"scope": value["scope"], "epoch": value["epoch"], "login_oid": role["role_oid"]}
            if p["kind"] == "generation"
            else None
        )
        return self._owner_receipt(
            cursor,
            value,
            input_wire,
            session,
            v,
            p | {"login_oid": role["role_oid"]},
            core,
            after,
            external,
            resource_sha,
            "PREPARED",
            None,
        )

    def _owner_receipt(
        self,
        cursor: OwnerCursor,
        value: dict[str, Any],
        input_wire: bytes,
        session: tuple[Any, ...],
        version: dict[str, Any],
        principal: dict[str, Any],
        core: dict[str, Any] | None,
        catalog: dict[str, Any],
        external: str,
        resource_sha: str,
        event: str,
        native: Any,
    ) -> bytes:
        cursor.execute(
            "SELECT "
            "maezo_d7_control._now_ms(),COALESCE(MAX(recorded_before_commit_at"
            '_ms),-1) FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT"'
        )
        observed = cursor.fetchone()
        now = observed[0]
        require(now > observed[1], "UNAVAILABLE")
        result = {
            "protocol": "maezo.d7-owner-preparation-result.v1",
            "installation_id": value["installation_id"],
            "owner_operation_id": value["owner_operation_id"],
            "preparation_id": value["preparation_id"],
            "event": event,
            "scope": value["scope"],
            "database_binding_sha256": digest(canonical(value["database_binding"]))
            if "database_binding" in value
            else version["database_binding_sha256"],
            "principal": principal,
            "generation_core": core,
            "owner_catalog_bytes": encode64(canonical(catalog)),
            "owner_catalog_sha256": digest(canonical(catalog)),
            "external_resource_readback_sha256": external,
            "native_qualification": native,
            "request_sha256": digest(input_wire),
            "owner_session_user": session[0],
            "owner_login_oid": session[2],
            "owner_effective_user": session[1],
            "owner_effective_oid": session[2],
            "recorded_before_commit_at_ms": now,
        }
        validate_owner_result(result)
        wire = canonical(result)
        row = {
            **{k: value["scope"][k] for k in ("tenant", "environment", "engine_name")},
            "owner_operation_id": value["owner_operation_id"],
            "preparation_id": value["preparation_id"],
            "event": event,
            "request_bytes": input_wire,
            "request_sha256": digest(input_wire),
            "result_bytes": wire,
            "result_sha256": digest(wire),
            "installation_id": value["installation_id"],
            "database_binding_sha256": result["database_binding_sha256"],
            "login_name": principal["login_name"],
            "login_oid": principal["login_oid"],
            "control_scope_id": value.get(
                "control_scope_id",
                parse_wire(self._catalog_wire(version["installation_binding_bytes"]))["control_scope_id"],
            ),
            "resource_identity_sha256": resource_sha,
            "owner_session_user": session[0],
            "owner_login_oid": session[2],
            "owner_effective_user": session[1],
            "owner_effective_oid": session[2],
            "recorded_before_commit_at_ms": now,
        }
        self._insert(cursor, "MZO_OWNER_PREPARATION_RECEIPT", row)
        cursor.execute("SELECT maezo_d7_control._assert_catalog()")
        return wire

    def _event(self, cursor: OwnerCursor, input_wire: bytes, session: tuple[Any, ...]) -> bytes:
        value = exact(
            parse_wire(input_wire),
            (
                "protocol installation_id owner_operation_id preparation_id event "
                "expected_previous_event_sha256 native_qualification "
                "terminal_proof_bytes terminal_proof_sha256"
            ),
        )
        require(
            value["protocol"] == "maezo.d7-owner-preparation-event.v1"
            and value["event"] in {"NATIVE_QUALIFIED", "REMOVED"}
        )
        cursor.execute(
            (
                "SELECT result_bytes,result_sha256,resource_identity_sha256 FROM "
                'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE '
                "preparation_id=%s AND event='PREPARED'"
            ),
            (value["preparation_id"],),
        )
        birth = cursor.fetchone()
        require(birth is not None, "UNAVAILABLE")
        birth_wire = bytes(birth[0])
        old = Document("OwnerPreparationResult", birth_wire).value()
        principal = old["principal"]
        require(value["installation_id"] == old["installation_id"] and digest(birth_wire) == birth[1])
        value = {**value, "scope": old["scope"]}
        f, v, g = self._owner_context(
            cursor, value, session, principal.get("generation_id"), principal["login_oid"]
        )
        v["database_binding_sha256"] = f["database_binding_sha256"]
        replay = self._replay(cursor, value, input_wire)
        if replay is not None:
            # Replaying history still requires today's owner and complete catalog.
            cursor.execute("SELECT maezo_d7_control._assert_catalog()")
            self.authority.replay(
                input_wire=input_wire, retained_result_wire=replay, fence_wire=canonical(f), session=session
            )
            return replay
        cursor.execute(
            (
                "SELECT result_sha256 FROM "
                'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE '
                "preparation_id=%s ORDER BY recorded_before_commit_at_ms DESC "
                "LIMIT 1"
            ),
            (value["preparation_id"],),
        )
        require(cursor.fetchone() == (value["expected_previous_event_sha256"],), "REQUEST_CONFLICT")
        before = self._latest_manifest(cursor, v)
        manifest = parse_wire(before)
        native = value["native_qualification"]
        if value["event"] == "NATIVE_QUALIFIED":
            require(
                value["terminal_proof_bytes"] is value["terminal_proof_sha256"] is None
                and principal["kind"] == "generation"
            )
            require(
                g is not None
                and g["status"] == "PREPARED"
                and g["phase"] == "UNADMITTED"
                and g["opened_at_ms"] is None
                and g["retired_at_ms"] is None
                and f["mode"] == "CLOSED"
                and f["current_generation_id"] is None
                and f["restore_state"] == "RECONCILED",
                "PRECONDITION_MISMATCH",
            )
            validate_native_qualification(native)
            require(
                native["d_preparation_id"] == value["preparation_id"]
                and native["scope"] == value["scope"]
                and native["generation_id"] == principal["generation_id"]
                and native["login_name"] == principal["login_name"]
                and native["login_oid"] == principal["login_oid"]
                and native["generation_core_sha256"] == digest(canonical(old["generation_core"]))
                and native["decision_sha256"] == present(g)["activation_decision_sha256"]
                and native["database_binding_sha256"] == f["database_binding_sha256"],
                "PRECONDITION_MISMATCH",
            )
            require(self.native_authority is not None, "UNAVAILABLE")
            present(self.native_authority, "UNAVAILABLE").verify(
                qualification_wire=canonical(native),
                birth_wire=birth_wire,
                generation_wire=canonical(g),
                session=session,
            )
            external = old["external_resource_readback_sha256"]
        else:
            require(native is None and value["terminal_proof_bytes"] is not None)
            terminal = decode64(value["terminal_proof_bytes"])
            require(digest(terminal) == value["terminal_proof_sha256"])
            parse_wire(terminal)
            require(g is None or g["status"] == "RETIRED", "PENDING_UNKNOWN")
            require(f["current_generation_id"] != principal.get("generation_id"), "PENDING_UNKNOWN")
            cursor.execute(
                "SELECT oid FROM pg_catalog.pg_roles WHERE oid=%s OR rolname=%s",
                (principal["login_oid"], principal["login_name"]),
            )
            require(cursor.fetchone() is None, "PENDING_UNKNOWN")
            external = self.authority.removal(
                input_wire=input_wire, birth_wire=birth_wire, session=session, fence_wire=canonical(f)
            )
            scalar("Sha256", external)
            if g is not None:
                cursor.execute(
                    (
                        'UPDATE maezo_d7_control."MZO_RUNTIME_ADMISSION" SET '
                        "quiescence_state='PROVEN_TERMINAL',terminal_proof_sha256=%s,revis"
                        "ion=revision+1 WHERE tenant=%s AND environment=%s AND "
                        "engine_name=%s AND generation_id=%s"
                    ),
                    (
                        value["terminal_proof_sha256"],
                        *[value["scope"][k] for k in ("tenant", "environment", "engine_name")],
                        principal["generation_id"],
                    ),
                )
            manifest["roles"] = [r for r in manifest["roles"] if r["role_oid"] != principal["login_oid"]]
            manifest["operational_principals"] = [
                r for r in manifest["operational_principals"] if r["login_oid"] != principal["login_oid"]
            ]
        roles = [{k: r[k] for k in ("role_class", "role_name")} for r in manifest["roles"]]
        after = self._catalog(cursor, roles, manifest["operational_principals"], value["installation_id"])
        catalog_check(after, owner_login_oid=session[2])
        self.authority.catalog(
            before_wire=before, after_wire=canonical(after), input_wire=input_wire, session=session
        )
        return self._owner_receipt(
            cursor,
            value,
            input_wire,
            session,
            v,
            principal,
            old["generation_core"],
            after,
            external,
            birth[2],
            value["event"],
            native,
        )
