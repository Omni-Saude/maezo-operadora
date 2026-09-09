#!/usr/bin/env python3
"Render an owner-bound native migration or v2 descriptor; never connect or supply authority."

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[3]
RESOURCES = REPO / "src/maezo/portal/engine/java/src/main/resources"
SQL = RESOURCES / "native-acquisition-v2-postgres.sql"
MANIFEST = RESOURCES / "native-acquisition-v2-migration.json"
ROLES = ("schema_owner", "function_owner", "issuer_role", "runtime_role", "d_guard_owner", "migration_role")
RUNTIME_FUNCTIONS = {
    "guard_runtime_v2",
    "validate_guard_v2",
    "read_receipt_v2",
    "read_acquisitions_v2",
    "insert_acquisition_v2",
    "renew_acquisition_v2",
    "close_acquisition_v2",
    "append_receipt_v2",
}
ISSUER_FUNCTIONS = {
    "register_admission_v2",
    "refresh_admission_v2",
    "retire_admission_v2",
    "set_receipt_history_v2",
}


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate_member")
        value[key] = item
    return value


def read_json(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_input")
    return json.loads(path.read_bytes(), object_pairs_hook=pairs)


def ident(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value) is None:
        raise ValueError("invalid_identifier")
    return '"' + value + '"'


def literal(value):
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ValueError("invalid_literal")
    return "'" + value.replace("'", "''") + "'"


def render(binding):
    keys = (
        set(ROLES)
        | {r + "_oid" for r in ROLES}
        | {
            "act_schema",
            "act_schema_oid",
            "database_oid",
            "database_binding_sha256",
            "d_helper_source_sha256",
            "d_schema_version_digest",
            "d_source_manifest_sha256",
            "d_migration_sha256",
            "d_role_acl_sha256",
            "d_objects_sha256",
            "migration_receipt",
        }
    )
    if type(binding) is not dict or set(binding) != keys:
        raise ValueError("invalid_binding")
    for key in ROLES + ("act_schema",):
        ident(binding[key])
    if len({binding[k] for k in ROLES}) != len(ROLES):
        raise ValueError("role_alias")
    for key in [r + "_oid" for r in ROLES] + ["act_schema_oid", "database_oid"]:
        if type(binding[key]) is not int or not 1 <= binding[key] <= 4294967295:
            raise ValueError("invalid_oid")
    if len({binding[r + "_oid"] for r in ROLES}) != len(ROLES):
        raise ValueError("role_oid_alias")
    for key in (
        "database_binding_sha256",
        "d_helper_source_sha256",
        "d_schema_version_digest",
        "d_source_manifest_sha256",
        "d_migration_sha256",
        "d_role_acl_sha256",
        "d_objects_sha256",
    ):
        if type(binding[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", binding[key]) is None:
            raise ValueError("invalid_digest")
    manifest = read_json(MANIFEST)
    sql = SQL.read_text()
    if hashlib.sha256(sql.encode()).hexdigest() != manifest["sql_template_sha256"]:
        raise ValueError("template_drift")
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    declarations = re.findall(
        r"CREATE FUNCTION maezo_native_v2\.(\w+)\(([^)]*)\) RETURNS ([^\n]+?) LANGUAGE", sql
    )
    names = [name for name, _, _ in declarations]
    if len(names) != len(set(names)) or set(names) != {row["name"] for row in manifest["functions"]}:
        raise ValueError("function_inventory")
    acl = []
    for name, arguments, _ in declarations:
        types = ",".join(arg.strip().split(maxsplit=1)[1] for arg in arguments.split(",") if arg.strip())
        signature = "maezo_native_v2." + name + "(" + types + ")"
        acl.append("ALTER FUNCTION " + signature + " OWNER TO " + ident(binding["function_owner"]) + ";")
        acl.append("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC;")
        allowed = (
            "runtime_role"
            if name in RUNTIME_FUNCTIONS
            else "issuer_role"
            if name in ISSUER_FUNCTIONS
            else None
        )
        if allowed:
            acl.append("GRANT EXECUTE ON FUNCTION " + signature + " TO " + ident(binding[allowed]) + ";")
    verify_acl = []
    for name, arguments, _ in declarations:
        types = ",".join(arg.strip().split(maxsplit=1)[1] for arg in arguments.split(",") if arg.strip())
        signature = "maezo_native_v2." + name + "(" + types + ")"
        role = (
            "runtime_role"
            if name in RUNTIME_FUNCTIONS
            else "issuer_role"
            if name in ISSUER_FUNCTIONS
            else None
        )
        allowed = [binding["function_owner_oid"]] + ([binding[role + "_oid"]] if role else [])
        verify_acl.append(
            (
                "IF EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coales"
                "ce(p.proacl,acldefault('f',p.proowner))) x WHERE p.oid=to_regprocedure"
                "("
            )
            + literal(signature)
            + ") AND (x.grantee NOT IN ("
            + ",".join(map(str, allowed))
            + (
                ") OR x.privilege_type<>'EXECUTE' OR (x.grantee<>p.proowner AND x.is_gr"
                "antable))) THEN RAISE EXCEPTION 'native_v2_function_acl_drift';END IF;"
            )
        )
    verify_acl.append(
        (
            "IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.reln"
            "amespace CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r"
            "',c.relowner))) x WHERE n.nspname='maezo_native_v2' AND c.relkind='r' "
            "AND (x.grantee NOT IN ("
        )
        + str(binding["schema_owner_oid"])
        + ","
        + str(binding["function_owner_oid"])
        + ") OR (x.grantee="
        + str(binding["function_owner_oid"])
        + (
            " AND (x.is_grantable OR x.privilege_type NOT IN ('SELECT','INSERT','UP"
            "DATE') OR (c.relname='schema_version' AND x.privilege_type<>'SELECT') "
            "OR (c.relname='operation_receipt' AND x.privilege_type='UPDATE'))))) T"
            "HEN RAISE EXCEPTION 'native_v2_relation_acl_drift';END IF;"
        )
    )
    verify_acl.append(
        (
            "IF EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(c"
            "oalesce(n.nspacl,acldefault('n',n.nspowner))) x WHERE n.nspname='maezo"
            "_native_v2' AND (x.grantee NOT IN ("
        )
        + ",".join(
            str(binding[r + "_oid"])
            for r in ("schema_owner", "function_owner", "issuer_role", "runtime_role")
        )
        + (
            ") OR (x.grantee<>n.nspowner AND (x.privilege_type<>'USAGE' OR x.is_gra"
            "ntable)))) THEN RAISE EXCEPTION 'native_v2_namespace_acl_drift';END IF"
            ";"
        )
    )
    verify_acl.append(
        (
            "IF EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attr"
            "elid JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL ac"
            "lexplode(a.attacl) x WHERE n.nspname='maezo_native_v2' AND (c.relname<"
            ">'operation_receipt' OR a.attname<>'command_id' OR x.grantee<>"
        )
        + str(binding["function_owner_oid"])
        + (
            " OR x.privilege_type<>'UPDATE' OR x.is_grantable)) THEN RAISE EXC"
            "EPTION 'native_v2_column_acl_drift';END IF;"
        )
    )
    replacements = {
        "__FUNCTION_OWNERSHIP_AND_ACL__": "\n".join(acl),
        "__VERIFY_NATIVE_ACL__": "DO $native_acl$ BEGIN\n" + "\n".join(verify_acl) + "\nEND $native_acl$;",
    }
    for role in ROLES:
        replacements["__" + role.upper() + "__"] = ident(binding[role])
    replacements.update(
        {
            "__ACT_SCHEMA__": ident(binding["act_schema"]),
            "__ACT_SCHEMA_VALUE__": binding["act_schema"],
            "__ACT_SCHEMA_OID__": str(binding["act_schema_oid"]),
            "__DATABASE_OID__": str(binding["database_oid"]),
            "__FUNCTION_OWNER_OID__": str(binding["function_owner_oid"]),
            "__ISSUER_ROLE_OID__": str(binding["issuer_role_oid"]),
            "__DATABASE_BINDING_DIGEST__": binding["database_binding_sha256"],
            "__MANIFEST_DIGEST__": digest,
            "__CIB_ABI_DIGEST__": manifest["cib_abi_sha256"],
            "'__MIGRATION_RECEIPT__'": literal(binding["migration_receipt"]),
        }
    )
    for marker, value in replacements.items():
        sql = sql.replace(marker, value)
    if re.search(r"__[A-Z_]+__", sql):
        raise ValueError("unresolved_placeholder")
    checks = []
    for role in ROLES:
        login = "true" if role in ("issuer_role", "runtime_role", "migration_role") else "false"
        checks.append(
            "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE oid="
            + str(binding[role + "_oid"])
            + " AND rolname="
            + literal(binding[role])
            + " AND rolcanlogin="
            + login
            + (
                " AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT ro"
                "lreplication AND NOT rolbypassrls) THEN RAISE EXCEPTION 'native_v2_rol"
                "e_unavailable';END IF;"
            )
        )
    for role in ("schema_owner", "function_owner", "d_guard_owner", "issuer_role"):
        checks.append(
            "IF pg_catalog.pg_has_role("
            + str(binding["runtime_role_oid"])
            + ","
            + str(binding[role + "_oid"])
            + ",'MEMBER') THEN RAISE EXCEPTION 'native_v2_role_assumption_denied';END IF;"
        )
    for login in ("issuer_role", "function_owner"):
        for owner in ("schema_owner", "d_guard_owner", "migration_role"):
            checks.append(
                "IF pg_catalog.pg_has_role("
                + str(binding[login + "_oid"])
                + ","
                + str(binding[owner + "_oid"])
                + ",'MEMBER') THEN RAISE EXCEPTION 'native_v2_owner_assumption_denied';END IF;"
            )
    checks.append(
        "IF current_setting('server_version_num')::int<160000 OR current_settin"
        "g('server_version_num')::int>=170000 THEN RAISE EXCEPTION 'native_v2_p"
        "ostgres_abi';END IF;"
    )
    checks.append(
        "IF session_user<>"
        + literal(binding["migration_role"])
        + (" OR current_user<>session_user THEN RAISE EXCEPTION 'native_v2_migration_identity';END IF;")
    )
    checks.append(
        "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE oid="
        + str(binding["act_schema_oid"])
        + " AND nspname="
        + literal(binding["act_schema"])
        + ") OR (SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())<>"
        + str(binding["database_oid"])
        + " THEN RAISE EXCEPTION 'native_v2_database_binding';END IF;"
    )
    checks.append(
        (
            "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc WHERE oid=to_regprocedu"
            "re('maezo_d7_control.lock_runtime_v2_scope(jsonb)') AND proowner="
        )
        + str(binding["d_guard_owner_oid"])
        + (
            " AND prosecdef AND provolatile='v' AND proparallel='u' AND prorettype="
            "'jsonb'::regtype AND proconfig=ARRAY['search_path=pg_catalog, pg_temp'"
            "] AND encode(sha256(convert_to(prosrc,'UTF8')),'hex')="
        )
        + literal(binding["d_helper_source_sha256"])
        + ") THEN RAISE EXCEPTION 'native_v2_D_helper_unavailable';END IF;"
    )
    # Only the separately authenticated migration owner reads this exact D receipt.
    # Native/runtime/function-owner receives no direct D table grant.
    checks.append(
        (
            'IF NOT EXISTS(SELECT 1 FROM maezo_d7_control."MZO_PROVISIONING_SCHEMA_'
            "VERSION\" WHERE component='maezo-d7-control' AND version=1 AND migratio"
            "n_id='maezo-d7-control-0001' AND predecessor_sha256 IS NULL AND manife"
            "st_sha256="
        )
        + literal(binding["d_source_manifest_sha256"])
        + " AND migration_sha256="
        + literal(binding["d_migration_sha256"])
        + " AND installation_binding_sha256="
        + literal(binding["d_schema_version_digest"])
        + (
            " AND installation_binding_sha256=encode(sha256(installation_bindi"
            "ng_bytes),'hex') AND role_acl_manifest_sha256="
        )
        + literal(binding["d_role_acl_sha256"])
        + (
            " AND role_acl_manifest_sha256=encode(sha256(role_acl_manifest_byt"
            "es),'hex') AND object_manifest_sha256="
        )
        + literal(binding["d_objects_sha256"])
        + (" AND object_manifest_sha256=encode(sha256(object_manifest_bytes),'hex') AND database_oid=")
        + str(binding["database_oid"])
        + " AND database_name=current_database() AND act_schema_oid="
        + str(binding["act_schema_oid"])
        + " AND act_schema_name="
        + literal(binding["act_schema"])
        + " AND control_schema_oid=to_regnamespace('maezo_d7_control')::oid AND cib_abi_sha256="
        + literal(manifest["cib_abi_sha256"])
        + (
            ') OR EXISTS(SELECT 1 FROM maezo_d7_control."MZO_PROVISIONING_SCHEMA_VE'
            "RSION\" WHERE component='maezo-d7-control' AND version<>1) THEN RAISE E"
            "XCEPTION 'native_v2_D_version_unavailable';END IF;"
        )
    )
    checks.append(
        "IF NOT has_schema_privilege("
        + str(binding["function_owner_oid"])
        + ",'maezo_d7_control','USAGE') OR NOT has_function_privilege("
        + str(binding["function_owner_oid"])
        + ",'maezo_d7_control.lock_runtime_v2_scope(jsonb)','EXECUTE') OR has_schema_privilege("
        + str(binding["runtime_role_oid"])
        + ",'maezo_d7_control','USAGE') OR has_function_privilege("
        + str(binding["runtime_role_oid"])
        + (
            ",'maezo_d7_control.lock_runtime_v2_scope(jsonb)','EXECUTE') OR EXISTS("
            "SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proac"
            "l,acldefault('f',p.proowner))) x WHERE p.oid=to_regprocedure('maezo_d7"
            "_control.lock_runtime_v2_scope(jsonb)') AND x.grantee=0) THEN RAISE EX"
            "CEPTION 'native_v2_D_acl_unavailable';END IF;"
        )
    )
    preflight = "DO $native_preflight$ BEGIN\n" + "\n".join(checks) + "\nEND $native_preflight$;\n"
    verify = (
        (
            "DO $native_replay$ DECLARE v record;BEGIN SELECT * INTO STRICT v FROM "
            "maezo_native_v2.schema_version WHERE migration_key='native-acquisition"
            "-v2';IF v.manifest_digest<>"
        )
        + literal(digest)
        + (
            " OR v.catalogue_digest<>encode(sha256(convert_to(maezo_native_v2.catal"
            "ogue_v2()::text,'UTF8')),'hex') OR v.act_schema_oid<>"
        )
        + str(binding["act_schema_oid"])
        + " OR v.database_oid<>"
        + str(binding["database_oid"])
        + " OR v.database_binding_sha256<>"
        + literal(binding["database_binding_sha256"])
        + " OR v.function_owner<>"
        + str(binding["function_owner_oid"])
        + " OR v.issuer_role<>"
        + str(binding["issuer_role_oid"])
        + " THEN RAISE EXCEPTION 'native_v2_migration_drift';END IF;END $native_replay$;\n"
    )
    return (
        "\\set ON_ERROR_STOP on\nBEGIN;\n"
        + preflight
        + (
            "SELECT to_regnamespace('maezo_native_v2') IS NULL AS native_v2_in"
            "stall \\gset\n\\if :native_v2_install\n"
        )
        + sql
        + "\n\\else\n"
        + replacements["__VERIFY_NATIVE_ACL__"]
        + "\n"
        + verify
        + "\\endif\nCOMMIT;\n"
    )


def descriptor(source, target):
    tree = ET.parse(source)
    matches = [
        e
        for e in tree.iter()
        if e.tag.rsplit("}", 1)[-1] == "property" and e.attrib.get("name") == "databaseSchemaUpdate"
    ]
    if len(matches) != 1 or (matches[0].text or "").strip() != "true":
        raise ValueError("descriptor_drift")
    matches[0].text = "false"
    tree.write(target, encoding="utf-8", xml_declaration=True)


def main():
    if len(sys.argv) != 4:
        raise ValueError("usage")
    action, source, destination = sys.argv[1:]
    path = Path(destination)
    if path.exists() or path.is_symlink():
        raise ValueError("output_exists")
    if action == "render-migration":
        data = render(read_json(Path(source))).encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    elif action == "descriptor":
        descriptor(Path(source), path)
    else:
        raise ValueError("invalid_action")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError):
        raise SystemExit("native_v2_packaging_refused") from None
