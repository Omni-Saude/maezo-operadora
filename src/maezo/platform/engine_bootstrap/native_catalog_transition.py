"""ADR-0054 non-grant invariants for the one initial native catalogue transition.

This never admits a transition. Caller must additionally prove the exact native/ACT
source allocation, qualified owner receipt and final original SQL catalogue guard.
"""

from __future__ import annotations

from typing import Any, Protocol

from maezo.platform.engine_bootstrap.controller_storage import (
    canonical,
    decode64,
    digest,
    parse_wire,
    require,
)


class Cursor(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...


def _row(cursor: Cursor, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
    cursor.execute(statement, parameters)
    row = cursor.fetchone()
    require(row is not None and cursor.fetchone() is None, "UNAVAILABLE")
    return row


def _hex_wire(value: str) -> bytes:
    require(type(value) is str and value.startswith("\\x"))
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError:
        require(False)
        raise AssertionError("unreachable") from None
    parse_wire(raw)
    return raw


def retained_manifest(cursor: Cursor, version: dict[str, Any]) -> bytes:
    cursor.execute(
        """SELECT result_bytes,result_sha256 FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT"
     WHERE installation_id=%s::uuid ORDER BY recorded_before_commit_at_ms DESC LIMIT 1""",
        (version["installation_id"],),
    )
    row = cursor.fetchone()
    if row is None:
        raw = _hex_wire(version["role_acl_manifest_bytes"])
        require(digest(raw) == version["role_acl_manifest_sha256"], "PRECONDITION_MISMATCH")
        return raw
    require(cursor.fetchone() is None and digest(bytes(row[0])) == row[1], "PRECONDITION_MISMATCH")
    result = parse_wire(bytes(row[0]))
    raw = decode64(result["owner_catalog_bytes"])
    require(
        digest(raw) == result["owner_catalog_sha256"]
        and result["installation_id"] == version["installation_id"],
        "PRECONDITION_MISMATCH",
    )
    return raw


def _objects(cursor: Cursor, version: dict[str, Any]) -> None:
    raw = _hex_wire(version["object_manifest_bytes"])
    require(digest(raw) == version["object_manifest_sha256"], "PRECONDITION_MISMATCH")
    objects = parse_wire(raw)["objects"]
    require(type(objects) is list and len(objects) <= 1024, "PRECONDITION_MISMATCH")
    # Authenticate every helper source directly before using _object_definition
    # for table/schema definitions. A changed helper may not vouch for itself.
    functions = [obj for obj in objects if obj["object_class"] == "function"]
    for obj in functions:
        row = _row(
            cursor,
            """SELECT proowner::bigint,encode(sha256(convert_to(pg_get_functiondef(oid),'UTF8')),'hex')
         FROM pg_catalog.pg_proc WHERE oid=%s::oid""",
            (obj["object_oid"],),
        )
        require(row == (obj["owner_oid"], obj["definition_sha256"]), "PRECONDITION_MISMATCH")
    for obj in objects:
        if obj["object_class"] == "function":
            continue
        require(obj["object_class"] in {"schema", "table"}, "PRECONDITION_MISMATCH")
        owner_query = (
            "SELECT nspowner::bigint FROM pg_catalog.pg_namespace WHERE oid=%s::oid"
            if obj["object_class"] == "schema"
            else "SELECT relowner::bigint FROM pg_catalog.pg_class WHERE oid=%s::oid"
        )
        require(_row(cursor, owner_query, (obj["object_oid"],)) == (obj["owner_oid"],), "AUTH_REFUSED")
        require(
            _row(
                cursor,
                "SELECT maezo_d7_control._object_definition(%s::oid,%s)",
                (obj["object_oid"], obj["object_class"]),
            )
            == (obj["definition_sha256"],),
            "PRECONDITION_MISMATCH",
        )
    require(
        _row(
            cursor,
            """SELECT
      (SELECT count(*) FROM pg_catalog.pg_proc WHERE pronamespace=%s::oid),
      (SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=%s::oid
       AND relkind IN ('r','p','v','m','S','f'))""",
            (version["control_schema_oid"], version["control_schema_oid"]),
        )
        == (len(functions), 7),
        "PRECONDITION_MISMATCH",
    )


def _expected_login(cursor: Cursor, role: dict[str, Any], database_oid: int) -> bool:
    expected = role["rolcanlogin"]
    if role["role_class"] not in {"generation login", "one-shot login"}:
        return bool(expected)
    cursor.execute(
        """SELECT g.status,EXISTS(SELECT 1 FROM maezo_d7_control."MZO_ISSUER_OPERATION_RECEIPT" i
     WHERE i.generation_id=g.generation_id AND i.tenant=g.tenant AND i.environment=g.environment
     AND i.engine_name=g.engine_name AND i.operation IN ('admit_candidate_login','open_runtime_generation')
     AND i.recorded_before_commit_at_ms=g.opened_at_ms)
     FROM maezo_d7_control."MZO_RUNTIME_ADMISSION" g WHERE g.login_oid=%s::oid""",
        (role["role_oid"],),
    )
    row = cursor.fetchone()
    require(cursor.fetchone() is None, "PRECONDITION_MISMATCH")
    if row is not None:
        expected = row[0] == "OPEN"
        require(not expected or row[1] is True, "PRECONDITION_MISMATCH")
    else:
        cursor.execute(
            """SELECT p.state,p.revoked_by_issuer_operation_id IS NOT NULL AND EXISTS(
         SELECT 1 FROM maezo_d7_control."MZO_ISSUER_OPERATION_RECEIPT" i
         WHERE i.issuer_operation_id=p.revoked_by_issuer_operation_id
           AND i.operation IN ('revoke_permit','reconcile_fence'))
         FROM maezo_d7_control."MZO_PROVISIONING_PERMIT" p WHERE p.login_oid=%s::oid""",
            (role["role_oid"],),
        )
        row = cursor.fetchone()
        require(cursor.fetchone() is None, "PRECONDITION_MISMATCH")
        expected = row is not None and row[0] == "ISSUED"
        require(row is None or row[0] != "REVOKED" or row[1] is True, "PRECONDITION_MISMATCH")
    require(
        _row(
            cursor,
            "SELECT pg_catalog.has_database_privilege(%s::oid,%s::oid,'CONNECT')",
            (role["role_oid"], database_oid),
        )
        == (expected,),
        "AUTH_REFUSED",
    )
    return bool(expected)


def verify_non_grant_invariants(
    cursor: Cursor, version: dict[str, Any], before_wire: bytes, actual_wire: bytes
) -> list[dict[str, Any]]:
    """Return only previous grants with authenticated current CONNECT overlays.

    Both manifests must also pass existing catalog_check using the actual owner
    login. Their complete membership arrays come from the original _owner_catalog.
    No expected catalog is synthesized from unverified observed native grants.
    """
    before, actual = parse_wire(before_wire), parse_wire(actual_wire)
    require(
        before["installation_id"] == actual["installation_id"] == version["installation_id"],
        "PRECONDITION_MISMATCH",
    )
    require(before_wire == retained_manifest(cursor, version), "PRECONDITION_MISMATCH")
    _objects(cursor, version)
    require(before["operational_principals"] == actual["operational_principals"], "PRECONDITION_MISMATCH")
    require(
        before["memberships"] == actual["memberships"] and len(actual["memberships"]) <= 1024, "AUTH_REFUSED"
    )
    require(len(before["roles"]) == len(actual["roles"]) <= 1024, "AUTH_REFUSED")
    observed_roles = {r["role_oid"]: r for r in actual["roles"]}
    for role in before["roles"]:
        expected = {**role, "rolcanlogin": _expected_login(cursor, role, version["database_oid"])}
        require(observed_roles.get(role["role_oid"]) == expected, "AUTH_REFUSED")
    role_oids = {
        r["role_oid"] for r in before["roles"] if r["role_class"] in {"generation login", "one-shot login"}
    }
    grants = [
        g
        for g in before["object_grants"]
        if not (
            g["object_class"] == "database" and g["privilege"] == "CONNECT" and g["grantee_oid"] in role_oids
        )
    ]
    overlays = _row(
        cursor,
        """SELECT COALESCE(jsonb_agg(login_oid ORDER BY login_oid),'[]'::jsonb) FROM (
     SELECT login_oid::bigint FROM maezo_d7_control."MZO_RUNTIME_ADMISSION" WHERE status='OPEN'
     UNION SELECT login_oid::bigint FROM maezo_d7_control."MZO_PROVISIONING_PERMIT"
     WHERE state='ISSUED' AND principal_origin->>'kind'='owner_prepared_one_shot') x""",
    )[0]
    require(type(overlays) is list and len(overlays) <= 1024, "PRECONDITION_MISMATCH")
    issuer_owner = next(
        r["role_oid"] for r in before["roles"] if r["role_class"] == "D issuer-function owner"
    )
    for oid in overlays:
        grants.append(
            dict(
                object_class="database",
                schema_name="",
                object_name=version["database_name"],
                object_oid=version["database_oid"],
                function_argument_types=[],
                column_names=[],
                grantee_oid=oid,
                grantor_oid=issuer_owner,
                privilege="CONNECT",
                grantable=False,
            )
        )
    births = _row(
        cursor,
        """SELECT COALESCE(jsonb_agg(jsonb_build_object('oid',b.login_oid::bigint,'name',b.login_name,
     'removed',EXISTS(SELECT 1 FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" e
      WHERE e.preparation_id=b.preparation_id AND e.event='REMOVED'),
     'live',EXISTS(SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.oid=b.login_oid OR r.rolname=b.login_name))),'[]'::jsonb)
     FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" b WHERE b.event='PREPARED'""",
    )[0]
    require(type(births) is list and len(births) <= 1024, "PRECONDITION_MISMATCH")
    for birth in births:
        if birth["removed"]:
            require(not birth["live"], "AUTH_REFUSED")
        else:
            require(
                any(
                    r["role_oid"] == birth["oid"] and r["role_name"] == birth["name"] for r in before["roles"]
                ),
                "PRECONDITION_MISMATCH",
            )
    require(len(grants) <= 1024 and len(actual["object_grants"]) <= 1024, "PRECONDITION_MISMATCH")
    require(not any(g["grantee_oid"] == 0 for g in actual["object_grants"]), "AUTH_REFUSED")
    return grants


def exact_initial_grant_union(
    previous_grants: list[dict[str, Any]],
    actual_grants: list[dict[str, Any]],
    source_allocation: list[dict[str, Any]],
) -> None:
    """Source allocation is independently reconstructed by the native owner verifier."""
    expected = {canonical(g) for g in previous_grants} | {canonical(g) for g in source_allocation}
    actual = [canonical(g) for g in actual_grants]
    require(len(actual) == len(set(actual)) <= 1024 and set(actual) == expected, "AUTH_REFUSED")
