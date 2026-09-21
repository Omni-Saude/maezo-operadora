"""ADR-0054 explicit owner-only store; no connections, authority discovery or startup DDL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from maezo.platform.engine_bootstrap.controller_storage import (
    canonical,
    decode64,
    digest,
    encode64,
    exact,
    parse_wire,
    require,
    scalar,
)
from maezo.platform.engine_bootstrap.native_owner_contracts import (
    qualification_from_receipt,
    receipt,
    request,
)

SCHEMA = "maezo_native_owner_v1"
SESSION_SQL = """SELECT session_user::text,current_user::text,r.oid::bigint,d.oid::bigint,
 current_setting('server_version_num')::integer,current_setting('transaction_isolation'),
current_setting('transaction_read_only'),r.rolsuper,r.rolcreaterole,r.rolcreatedb,r.rolreplication,r.rolbypassrls,
 pg_catalog.pg_has_role(r.oid,%s::oid,'USAGE')
 FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_database d
 WHERE r.rolname=session_user AND d.datname=current_database()"""
CATALOG_SQL = """SELECT pg_catalog.jsonb_build_object(
 'namespace',(SELECT
pg_catalog.jsonb_build_object('oid',n.oid::bigint,'owner',n.nspowner::bigint,'acl',n.nspacl::text)
   FROM pg_catalog.pg_namespace n WHERE n.nspname='maezo_native_owner_v1'),
 'relations',(SELECT
pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('oid',c.oid::bigint,'name',c.relname,
'kind',c.relkind,'owner',c.relowner::bigint,'acl',c.relacl::text,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
   'columns',(SELECT
pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('name',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
'not_null',a.attnotnull,'acl',a.attacl::text,'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid))
ORDER BY a.attnum)
      FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND
d.adnum=a.attnum WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
   'constraints',(SELECT pg_catalog.jsonb_agg(pg_catalog.pg_get_constraintdef(k.oid,true) ORDER BY
k.conname) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid),
   'indexes',(SELECT pg_catalog.jsonb_agg(pg_catalog.pg_get_indexdef(i.indexrelid) ORDER BY
i.indexrelid) FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid),
   'triggers',(SELECT
pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('definition',pg_catalog.pg_get_triggerdef(t.oid,true),'enabled',t.tgenabled)
ORDER BY t.tgname) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)) ORDER
BY c.relname)
   FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE
n.nspname='maezo_native_owner_v1' AND c.relkind<>'i'),
 'functions',(SELECT
pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('oid',p.oid::bigint,'name',p.proname,'owner',p.proowner::bigint,
    'acl',p.proacl::text,'definition',pg_catalog.pg_get_functiondef(p.oid)) ORDER BY p.oid)
    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE
n.nspname='maezo_native_owner_v1'),
 'default_acls',(SELECT
COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('role',a.defaclrole::bigint,'kind',a.defaclobjtype,'acl',a.defaclacl::text)
ORDER BY a.oid),'[]'::jsonb)
    FROM pg_catalog.pg_default_acl a WHERE
a.defaclnamespace=to_regnamespace('maezo_native_owner_v1')))"""
ACL_SQL = """WITH objects AS (
 SELECT n.nspowner owner_oid,COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner)) acl FROM
pg_catalog.pg_namespace n WHERE n.nspname='maezo_native_owner_v1'
 UNION ALL SELECT c.relowner,COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner)) FROM
pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE
n.nspname='maezo_native_owner_v1' AND c.relkind='r'
 UNION ALL SELECT p.proowner,COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner)) FROM
pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE
n.nspname='maezo_native_owner_v1')
 SELECT NOT EXISTS(SELECT 1 FROM objects o CROSS JOIN LATERAL pg_catalog.aclexplode(o.acl) a WHERE
a.grantee<>o.owner_oid OR a.grantor<>o.owner_oid),
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='maezo_native_owner_v1' AND
a.attacl IS NOT NULL)"""
INSTALLATION_SQL = """SELECT installation_id::text,source_manifest_sha256,ddl_sha256,database_oid::bigint,
schema_oid::bigint,schema_owner_oid::bigint,owner_login_oid::bigint,owner_login_name,catalog_bytes,catalog_sha256
 FROM maezo_native_owner_v1.installation WHERE singleton FOR UPDATE"""
OPERATION_SQL = """SELECT
request_bytes,request_sha256,result_bytes,result_sha256,qualification_bytes,qualification_sha256
  ,creator_top_xid IS DISTINCT FROM pg_catalog.pg_current_xact_id_if_assigned()
 AS committed_before_this_transaction,
 owner_installation_id::text,native_installation_receipt_sha256,d_preparation_id::text
 FROM maezo_native_owner_v1.principal_qualification_receipt WHERE native_owner_operation_id=%s::uuid"""


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> Any: ...


def one(cursor: Cursor, query: str, params: tuple[Any, ...] = ()) -> Any:
    cursor.execute(query, params)
    row = cursor.fetchone()
    require(row is not None and cursor.fetchone() is None, "UNAVAILABLE")
    return row


@dataclass(frozen=True, slots=True)
class StorePins:
    installation_id: str
    owner_login_name: str
    owner_login_oid: int
    schema_owner_name: str
    schema_owner_oid: int
    database_oid: int
    protected_role_oids: tuple[int, ...]
    source_manifest_sha256: str
    ddl_sha256: str

    def __post_init__(self) -> None:
        scalar("Uuid", self.installation_id)
        for name in (self.owner_login_name, self.schema_owner_name):
            scalar("Id", name)
            require(len(name.encode()) <= 63)
        for oid in (
            self.owner_login_oid,
            self.schema_owner_oid,
            self.database_oid,
            *self.protected_role_oids,
        ):
            scalar("Oid", oid)
        require(type(self.protected_role_oids) is tuple and 1 <= len(self.protected_role_oids) <= 1024)
        require(len(set(self.protected_role_oids)) == len(self.protected_role_oids))
        require(
            self.schema_owner_oid not in self.protected_role_oids
            and self.owner_login_oid not in self.protected_role_oids
        )
        scalar("Sha256", self.source_manifest_sha256)
        scalar("Sha256", self.ddl_sha256)


def authenticated_session(cursor: Cursor, pins: StorePins) -> tuple[Any, ...]:
    session = one(cursor, SESSION_SQL, (pins.schema_owner_oid,))
    require(
        session[:4]
        == (pins.owner_login_name, pins.owner_login_name, pins.owner_login_oid, pins.database_oid),
        "AUTH_REFUSED",
    )
    require(
        type(session[4]) is int
        and 160000 <= session[4] < 170000
        and session[5:7] == ("read committed", "off"),
        "PRECONDITION_MISMATCH",
    )
    require(session[7:] == (False, False, False, False, False, True), "AUTH_REFUSED")
    row = one(
        cursor,
        (
            "SELECT "
            "rolname,rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_catalog.pg_roles WHERE oid=%s::oid"
        ),
        (pins.schema_owner_oid,),
    )
    require(row == (pins.schema_owner_name, False, False, False, False, False, False), "AUTH_REFUSED")
    row = one(
        cursor,
        (
            "SELECT count(*)::integer,bool_and(NOT pg_catalog.pg_has_role(r.oid,%s::oid,'MEMBER') "
            "AND NOT pg_catalog.pg_has_role(r.oid,%s::oid,'MEMBER')) FROM pg_catalog.pg_roles r "
            "WHERE r.oid=ANY(%s::oid[])"
        ),
        (pins.schema_owner_oid, pins.owner_login_oid, list(pins.protected_role_oids)),
    )
    require(row == (len(pins.protected_role_oids), True), "AUTH_REFUSED")
    return cast(tuple[Any, ...], session)


def catalogue(cursor: Cursor, pins: StorePins) -> bytes:
    value = one(cursor, CATALOG_SQL)[0]
    exact(value, "namespace relations functions default_acls")
    namespace = value["namespace"]
    require(type(namespace) is dict and namespace["owner"] == pins.schema_owner_oid, "AUTH_REFUSED")
    relations = value["relations"]
    require(
        type(relations) is list
        and {r["name"] for r in relations} == {"installation", "principal_qualification_receipt"}
        and len(relations) == 2,
        "PRECONDITION_MISMATCH",
    )
    require(
        all(
            r["owner"] == pins.schema_owner_oid and r["kind"] == "r" and not r["rls"] and not r["force_rls"]
            for r in relations
        ),
        "AUTH_REFUSED",
    )
    functions = value["functions"]
    require(
        type(functions) is list
        and len(functions) == 2
        and {function["name"] for function in functions} == {"deny_mutation", "stamp_creator"}
        and all(function["owner"] == pins.schema_owner_oid for function in functions),
        "AUTH_REFUSED",
    )
    require(value["default_acls"] == [] and one(cursor, ACL_SQL) == (True, True), "AUTH_REFUSED")
    return canonical(value)


def read_installation(cursor: Cursor, pins: StorePins) -> bytes:
    """D-first caller already holds F/G/V/birth locks; acquire owner-store lock last."""
    authenticated_session(cursor, pins)
    row = one(cursor, INSTALLATION_SQL)
    require(
        row[:4] == (pins.installation_id, pins.source_manifest_sha256, pins.ddl_sha256, pins.database_oid),
        "PRECONDITION_MISMATCH",
    )
    require(row[5:8] == (pins.schema_owner_oid, pins.owner_login_oid, pins.owner_login_name), "AUTH_REFUSED")
    wire = bytes(row[8])
    require(digest(wire) == row[9], "PRECONDITION_MISMATCH")
    require(parse_wire(wire)["namespace"]["oid"] == row[4], "PRECONDITION_MISMATCH")
    require(catalogue(cursor, pins) == wire, "PRECONDITION_MISMATCH")
    return wire


def read_operation(cursor: Cursor, operation_id: str) -> tuple[bytes, bytes, bytes] | None:
    """Call only after authenticated installation custody; this does not assert admission."""
    scalar("Uuid", operation_id)
    cursor.execute(OPERATION_SQL, (operation_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    require(cursor.fetchone() is None and len(row) == 10 and row[6] is True, "PRECONDITION_MISMATCH")
    # MVCC hides other transactions' uncommitted rows. Reject our own current
    # full top-level transaction too, including its subtransactions. The pinned
    # BEFORE INSERT trigger overwrites any supplied creator stamp. D consumes
    # only a prior committed owner operation, without opening another connection.
    qraw, raw, binding = bytes(row[0]), bytes(row[2]), bytes(row[4])
    require((digest(qraw), digest(raw), digest(binding)) == (row[1], row[3], row[5]), "PRECONDITION_MISMATCH")
    q = request(qraw)
    require(
        row[7:]
        == (q["owner_installation_id"], q["native_installation_receipt_sha256"], q["d_preparation_id"]),
        "PRECONDITION_MISMATCH",
    )
    retained = receipt(raw)
    require(
        q["native_owner_operation_id"] == operation_id and retained["request_bytes"] == encode64(qraw),
        "PRECONDITION_MISMATCH",
    )
    require(qualification_from_receipt(raw) == binding, "PRECONDITION_MISMATCH")
    return qraw, raw, binding


def append_operation(cursor: Cursor, pins: StorePins, raw: bytes) -> bytes:
    """Enlisted transaction only; the connection owner alone may report COMMITTED."""
    value = receipt(raw)
    qraw = decode64(value["request_bytes"])
    q = request(qraw)
    require(q["owner_installation_id"] == pins.installation_id, "PRECONDITION_MISMATCH")
    binding = qualification_from_receipt(raw)
    retained = read_operation(cursor, q["native_owner_operation_id"])
    if retained is not None:
        require(retained == (qraw, raw, binding), "REQUEST_CONFLICT")
        return binding
    cursor.execute(
        """INSERT INTO maezo_native_owner_v1.principal_qualification_receipt
     (native_owner_operation_id,owner_installation_id,native_installation_receipt_sha256,d_preparation_id,
      request_bytes,request_sha256,result_bytes,result_sha256,qualification_bytes,qualification_sha256,recorded_before_commit_at_ms)
     VALUES (%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s)""",
        (
            q["native_owner_operation_id"],
            pins.installation_id,
            q["native_installation_receipt_sha256"],
            q["d_preparation_id"],
            qraw,
            digest(qraw),
            raw,
            digest(raw),
            binding,
            digest(binding),
            value["recorded_before_commit_at_ms"],
        ),
    )
    return binding
