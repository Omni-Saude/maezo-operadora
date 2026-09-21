"""ADR-0051 inert owner-store renderer/installer. Explicit enlisted owner cursor only."""

from __future__ import annotations

import json

from maezo.gateway.native_owner_store import (
    Cursor,
    StorePins,
    authenticated_session,
    catalogue,
    one,
    read_installation,
)
from maezo.platform.engine_bootstrap.controller_storage import canonical, digest, exact, parse_wire, require

PACKAGE_FIELDS = (
    "protocol component version predecessor execution_authorized schema "
    "sql_template_sha256 relations functions operation adr runtime_admission"
)


def render(package_bytes: bytes, template: bytes, pins: StorePins) -> bytes:
    """The deployment owner supplies independently reviewed protected source bytes."""
    require(digest(package_bytes) == pins.source_manifest_sha256, "PRECONDITION_MISMATCH")
    package = exact(json.loads(package_bytes), PACKAGE_FIELDS)
    require(
        package
        == {
            "protocol": "maezo.native-owner-migration-package.v1",
            "component": "maezo-native-owner",
            "version": 1,
            "predecessor": None,
            "execution_authorized": False,
            "schema": "maezo_native_owner_v1",
            "sql_template_sha256": digest(template),
            "relations": ["installation", "principal_qualification_receipt"],
            "functions": ["deny_mutation()", "stamp_creator()"],
            "operation": "qualify_initial_installation",
            "adr": "ADR-0051",
            "runtime_admission": False,
        },
        "PRECONDITION_MISMATCH",
    )
    identifier = '"' + pins.schema_owner_name.replace('"', '""') + '"'
    sql = template.replace(b"__SCHEMA_OWNER__", identifier.encode())
    require(digest(sql) == pins.ddl_sha256 and b"__SCHEMA_OWNER__" not in sql, "PRECONDITION_MISMATCH")
    return sql


def _d_catalog(cursor: Cursor) -> bytes | None:
    present = one(cursor, "SELECT to_regnamespace('maezo_d7_control') IS NOT NULL")[0]
    if not present:
        return None
    cursor.execute("SELECT maezo_d7_control._assert_catalog()")
    # Retained installation roles establish complete protected grantee closure;
    # no caller role list can suppress owner-store effects on an installed D.
    row = one(
        cursor,
        """SELECT maezo_d7_control._grant_catalog(ARRAY(
     SELECT (r->>'role_oid')::oid FROM maezo_d7_control."MZO_PROVISIONING_SCHEMA_VERSION" v,
     LATERAL jsonb_array_elements(COALESCE((SELECT
     convert_from(maezo_d7_control._unb64(convert_from(e.result_bytes,'UTF8')::jsonb->>'owner_catalog_bytes'),'UTF8')::jsonb
     FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" e WHERE e.installation_id=v.installation_id
     ORDER BY e.recorded_before_commit_at_ms DESC LIMIT 1),
     convert_from(v.role_acl_manifest_bytes,'UTF8')::jsonb)->'roles') r
     WHERE v.component='maezo-d7-control' AND v.version=1))""",
    )
    return canonical(row[0])


def install(cursor: Cursor, package_bytes: bytes, template: bytes, pins: StorePins) -> bytes:
    """Install in an explicit owner transaction; return custody bytes, never COMMITTED.

    Prefer before D baseline installation. Late installation must cause exactly
    zero complete D grant-catalogue delta. No role/membership/grant repair occurs.
    Caller rolls back every exception and controls commit/UNKNOWN reconciliation.
    """
    sql = render(package_bytes, template, pins)
    authenticated_session(cursor, pins)
    present = one(cursor, "SELECT to_regnamespace('maezo_native_owner_v1') IS NOT NULL")[0]
    if present:
        return read_installation(cursor, pins)
    before = _d_catalog(cursor)
    # Serialize this separately versioned initial namespace allocation by a scoped
    # PostgreSQL transaction advisory lock. This is store installation, before
    # any native principal receipt; D-first receipt operations never take it.
    cursor.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock(%s::bigint)", ((740051 << 32) | pins.database_oid,)
    )
    require(one(cursor, "SELECT to_regnamespace('maezo_native_owner_v1') IS NULL")[0], "REQUEST_CONFLICT")
    cursor.execute(sql.decode())
    wire = catalogue(cursor, pins)
    schema_oid = parse_wire(wire)["namespace"]["oid"]
    now = one(cursor, "SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint")[0]
    require(type(now) is int and now >= 0, "UNAVAILABLE")
    cursor.execute(
        """INSERT INTO maezo_native_owner_v1.installation
     (singleton,installation_id,source_manifest_sha256,ddl_sha256,database_oid,schema_oid,schema_owner_oid,
      owner_login_oid,owner_login_name,catalog_bytes,catalog_sha256,recorded_before_commit_at_ms)
     VALUES (true,%s::uuid,%s,%s,%s::oid,%s::oid,%s::oid,%s::oid,%s,%s,%s,%s)""",
        (
            pins.installation_id,
            pins.source_manifest_sha256,
            pins.ddl_sha256,
            pins.database_oid,
            schema_oid,
            pins.schema_owner_oid,
            pins.owner_login_oid,
            pins.owner_login_name,
            wire,
            digest(wire),
            now,
        ),
    )
    require(_d_catalog(cursor) == before, "PRECONDITION_MISMATCH")
    require(read_installation(cursor, pins) == wire, "PRECONDITION_MISMATCH")
    return wire
