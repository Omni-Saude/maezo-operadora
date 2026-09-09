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


def preparation(binding):
    """Expected selectors only; actual qualified D owner state is read under F/G/V locks."""
    ident(binding["d_schema_owner"])
    oid = binding["d_schema_owner_oid"]
    if type(oid) is not int or not 1 <= oid <= 4294967295:
        raise ValueError("invalid_oid")
    for role in ROLES:
        if role != "migration_role" and (
            binding[role] == binding["d_schema_owner"] or binding[role + "_oid"] == oid
        ):
            raise ValueError("d_owner_alias")
    if (binding["migration_role"] == binding["d_schema_owner"]) != (binding["migration_role_oid"] == oid):
        raise ValueError("d_owner_alias")
    expected = binding["d_preparation"]
    fields = {
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
    }
    if type(expected) is not dict or set(expected) != fields:
        raise ValueError("invalid_preparation")
    for key in ("tenant", "environment", "engine_name", "region"):
        literal(expected[key])
        if "*" in expected[key]:
            raise ValueError("invalid_preparation")
    if type(expected["account"]) is not str or re.fullmatch(r"[0-9]{12}", expected["account"]) is None:
        raise ValueError("invalid_preparation")
    for key in ("database_incarnation", "run_id", "preparation_id"):
        if (
            type(expected[key]) is not str
            or re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", expected[key]) is None
        ):
            raise ValueError("invalid_preparation")
    for key in ("epoch", "generation_id"):
        if type(expected[key]) is not int or not 1 <= expected[key] <= 9007199254740991:
            raise ValueError("invalid_preparation")
    for key in ("receipt_sha256", "generation_core_sha256", "decision_sha256"):
        if type(expected[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", expected[key]) is None:
            raise ValueError("invalid_preparation")
    if expected["purpose"] not in ("runtime", "candidate"):
        raise ValueError("invalid_preparation")
    pinned = dict(
        expected,
        native_function_owner_oid=binding["function_owner_oid"],
        native_issuer_oid=binding["issuer_role_oid"],
        login_name=binding["runtime_role"],
        login_oid=binding["runtime_role_oid"],
        d_schema_owner=binding["d_schema_owner"],
        d_schema_owner_oid=oid,
        database_binding_sha256=binding["database_binding_sha256"],
    )
    # Every field above has a closed type/bound. This literal is not a SQL or authority input.
    encoded = json.dumps(pinned, sort_keys=True, separators=(",", ":"))
    return "'" + encoded.replace("'", "''") + "'::jsonb"


def preparation_checks(selector):
    return (
        "DO $native_preparation$\nDECLARE p jsonb := "
        + selector
        + (
            "; f record; g record; r record; b jsonb; v record; t text; owner_oid oid;\n"
            "BEGIN\n"
            " owner_oid:=(p->>'d_schema_owner_oid')::oid;\n"
            " IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE oid=owner_oid AND rolname=p->>'d_schema_o"
            "wner' AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplicati"
            "on AND NOT rolbypassrls)\n"
            "    OR NOT pg_has_role(current_user,owner_oid,'USAGE') THEN RAISE EXCEPTION 'native_v"
            "2_preparation_owner';END IF;\n"
            " IF NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='maezo_d7_control' AND nspown"
            "er=owner_oid) THEN RAISE EXCEPTION 'native_v2_preparation_owner';END IF;\n"
            " -- These tables are read only through the already qualified owner authority, never n"
            "ew grants.\n"
            " FOREACH t IN ARRAY ARRAY['MZO_PROVISIONING_FENCE','MZO_RUNTIME_ADMISSION','MZO_PROVI"
            "SIONING_SCHEMA_VERSION','MZO_OWNER_PREPARATION_RECEIPT'] LOOP\n"
            "  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='maezo_d7_control' AND c.relname=t AND c.relkind='r' AND c.relowner=o"
            "wner_oid)\n"
            "     OR NOT has_table_privilege(current_user,format('%I.%I','maezo_d7_control',t),'SE"
            "LECT')\n"
            "     OR has_table_privilege((p->>'login_oid')::oid,format('%I.%I','maezo_d7_control',"
            "t),'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')\n"
            "     OR has_table_privilege((p->>'native_function_owner_oid')::oid,format('%I.%I','ma"
            "ezo_d7_control',t),'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')\n"
            "     OR has_table_privilege((p->>'native_issuer_oid')::oid,format('%I.%I','maezo_d7_c"
            "ontrol',t),'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN RAISE EXCE"
            "PTION 'native_v2_preparation_owner';END IF;\n"
            " END LOOP;\n"
            " IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS"
            " JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x WHERE n.nsp"
            "name='maezo_d7_control' AND c.relname='MZO_OWNER_PREPARATION_RECEIPT' AND (x.grantee="
            "0 OR (x.grantee<>owner_oid AND (x.privilege_type<>'SELECT' OR x.is_grantable)))) OR E"
            "XISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namesp"
            "ace n ON n.oid=c.relnamespace WHERE n.nspname='maezo_d7_control' AND c.relname='MZO_O"
            "WNER_PREPARATION_RECEIPT' AND a.attacl IS NOT NULL) THEN RAISE EXCEPTION 'native_v2_p"
            "reparation_owner';END IF;\n"
            ' SELECT * INTO STRICT f FROM maezo_d7_control."MZO_PROVISIONING_FENCE"\n'
            " WHERE tenant=p->>'tenant' AND environment=p->>'environment' AND engine_name=p->>'eng"
            "ine_name' FOR UPDATE;\n"
            ' SELECT * INTO STRICT g FROM maezo_d7_control."MZO_RUNTIME_ADMISSION"\n'
            " WHERE tenant=f.tenant AND environment=f.environment AND engine_name=f.engine_name AN"
            "D generation_id=(p->>'generation_id')::bigint FOR UPDATE;\n"
            ' SELECT * INTO STRICT v FROM maezo_d7_control."MZO_PROVISIONING_SCHEMA_VERSION" WHERE'
            " component='maezo-d7-control' AND version=1 FOR UPDATE;\n"
            ' SELECT * INTO STRICT r FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT"\n'
            " WHERE preparation_id=(p->>'preparation_id')::uuid AND event='PREPARED' FOR UPDATE;\n"
            " IF (f.mode<>'CLOSED' OR f.restore_state<>'RECONCILED' OR f.current_generation_id IS "
            "NOT NULL OR f.epoch<>(p->>'epoch')::bigint OR f.owner_run_id<>(p->>'run_id')::uuid\n"
            "     OR f.database_incarnation<>(p->>'database_incarnation')::uuid OR f.database_bind"
            "ing_sha256<>p->>'database_binding_sha256'\n"
            "     OR f.account<>p->>'account' OR f.region<>p->>'region' OR g.generation_epoch<>f.e"
            "poch OR g.run_id<>f.owner_run_id\n"
            "     OR g.account<>f.account OR g.region<>f.region OR g.database_incarnation<>f.datab"
            "ase_incarnation OR g.database_binding_sha256<>f.database_binding_sha256\n"
            "     OR g.status<>'PREPARED' OR g.phase<>'UNADMITTED' OR g.phase_proof_bytes IS NOT N"
            "ULL OR g.phase_proof_sha256 IS NOT NULL OR g.valid_until_ms IS NOT NULL OR g.observat"
            "ion_deadline_ms IS NOT NULL OR g.opened_at_ms IS NOT NULL OR g.retired_at_ms IS NOT N"
            "ULL\n"
            "     OR g.login_name<>p->>'login_name' OR g.login_oid<>(p->>'login_oid')::oid OR g.pu"
            "rpose<>p->>'purpose'\n"
            "     OR g.owner_preparation_id<>r.preparation_id OR g.generation_binding_sha256<>p->>"
            "'generation_core_sha256'\n"
            "     OR g.generation_binding_sha256<>encode(sha256(g.generation_core_bytes),'hex')\n"
            "     OR g.activation_decision_sha256<>p->>'decision_sha256' OR g.activation_decision_"
            "sha256<>encode(sha256(g.activation_decision_bytes),'hex')\n"
            "     OR octet_length(g.generation_core_bytes)>1048576 OR octet_length(g.activation_de"
            "cision_bytes)>1048576\n"
            "     OR r.tenant<>f.tenant OR r.environment<>f.environment OR r.engine_name<>f.engine"
            "_name OR r.database_binding_sha256<>f.database_binding_sha256\n"
            "     OR r.installation_id<>v.installation_id OR r.login_name<>g.login_name OR r.login"
            "_oid<>g.login_oid\n"
            "     OR r.result_sha256<>p->>'receipt_sha256' OR r.result_sha256<>encode(sha256(r.res"
            "ult_bytes),'hex') OR r.request_sha256<>encode(sha256(r.request_bytes),'hex')\n"
            "     OR octet_length(r.result_bytes)>1048576 OR octet_length(r.request_bytes)>1048576"
            "\n"
            '     OR EXISTS(SELECT 1 FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE p'
            "reparation_id=r.preparation_id AND (event='REMOVED' OR (event='NATIVE_QUALIFIED' AND "
            "to_regnamespace('maezo_native_v2') IS NULL)))) IS NOT FALSE\n"
            " THEN RAISE EXCEPTION 'native_v2_preparation_unavailable';END IF;\n"
            " b:=convert_from(r.result_bytes,'UTF8')::jsonb;\n"
            " IF (b->>'protocol'<>'maezo.d7-owner-preparation-result.v1' OR b->>'event'<>'PREPARED"
            "'\n"
            "     OR b->>'preparation_id'<>r.preparation_id::text OR b->>'request_sha256'<>r.reque"
            "st_sha256\n"
            "     OR b->'generation_core' IS DISTINCT FROM convert_from(g.generation_core_bytes,'U"
            "TF8')::jsonb\n"
            "     OR b->'principal'->>'login_name'<>g.login_name OR (b->'principal'->>'login_oid')"
            "::oid<>g.login_oid\n"
            "     OR b->'principal'->>'purpose'<>g.purpose OR b->'principal'->>'kind'<>'generation"
            "'\n"
            "     OR (b->'principal'->>'generation_id')::bigint<>g.generation_id\n"
            "     OR b->>'owner_session_user'<>r.owner_session_user OR (b->>'owner_login_oid')::oi"
            "d<>r.owner_login_oid\n"
            "     OR b->>'owner_effective_user'<>r.owner_effective_user OR (b->>'owner_effective_o"
            "id')::oid<>r.owner_effective_oid\n"
            "     OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE oid=r.owner_login_oid AND rolname=r.o"
            "wner_session_user)\n"
            "     OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE oid=r.owner_effective_oid AND rolname"
            "=r.owner_effective_user)\n"
            "     OR NOT pg_has_role(r.owner_effective_oid,owner_oid,'USAGE')\n"
            "     OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE oid=g.login_oid AND rolname=g.login_n"
            "ame AND NOT rolcanlogin)\n"
            "     OR has_database_privilege(g.login_oid,current_database(),'CONNECT')) IS NOT FALS"
            "E THEN RAISE EXCEPTION 'native_v2_preparation_unavailable';END IF;\n"
            "END $native_preparation$;\n"
        )
    )


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
            "d_schema_owner",
            "d_schema_owner_oid",
            "d_preparation",
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
    preparation_selector = preparation(binding)
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
            "__PREPARATION_BINDING__": preparation_selector,
            "__PREPARATION_ID__": binding["d_preparation"]["preparation_id"],
            "'__MIGRATION_RECEIPT__'": literal(binding["migration_receipt"]),
        }
    )
    for marker, value in replacements.items():
        sql = sql.replace(marker, value)
    if re.search(r"__[A-Z_]+__", sql):
        raise ValueError("unresolved_placeholder")
    checks = []
    for role in ROLES:
        login = "true" if role in ("issuer_role", "migration_role") else "false"
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
    for role in (
        "schema_owner",
        "function_owner",
        "d_guard_owner",
        "d_schema_owner",
        "issuer_role",
        "migration_role",
    ):
        checks.append(
            "IF pg_catalog.pg_has_role("
            + str(binding["runtime_role_oid"])
            + ","
            + str(binding[role + "_oid"])
            + ",'MEMBER') THEN RAISE EXCEPTION 'native_v2_role_assumption_denied';END IF;"
        )
    for login in ("issuer_role", "function_owner"):
        for owner in ("schema_owner", "d_guard_owner", "d_schema_owner", "migration_role"):
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
    preflight = (
        "DO $native_preflight$ BEGIN\n"
        + "\n".join(checks)
        + "\nEND $native_preflight$;\n"
        + preparation_checks(preparation_selector)
    )
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
        + " OR v.migration_receipt<>"
        + literal(binding["migration_receipt"])
        + " OR v.preparation_binding IS DISTINCT FROM "
        + preparation_selector
        + (
            " OR v.preparation_receipt_bytes IS DISTINCT FROM (SELECT result_bytes FROM "
            'maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE preparation_id='
        )
        + literal(binding["d_preparation"]["preparation_id"])
        + "::uuid AND event='PREPARED')"
        + " THEN RAISE EXCEPTION 'native_v2_migration_drift';END IF;END $native_replay$;\n"
    )
    return (
        "\\set ON_ERROR_STOP on\nBEGIN;\nSET LOCAL lock_timeout='1000ms';\n"
        "SET LOCAL statement_timeout='5000ms';\n"
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
