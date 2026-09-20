"""Read actual native installation custody through an owner-supplied connection.

This is technical evidence, not NativePrincipalQualificationBinding. The missing
native-owner qualification operation must independently define and retain that
receipt before D can consume NATIVE_QUALIFIED. This module creates no authority,
connections, roles, grants, receipts in protected tables, or runtime admission.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


class ReadbackError(ValueError):
    """Technical readback was unavailable or differed from the reviewed pins."""


class ReadbackCursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


@dataclass(frozen=True, slots=True)
class NativeInstallationPins:
    """Review inputs; equality with actual catalog data never grants permission."""

    owner_login: str
    owner_oid: int
    native_schema_owner_oid: int
    native_function_owner_oid: int
    database_oid: int
    act_schema: str
    act_schema_oid: int
    native_manifest_sha256: str
    catalogue_function_source_sha256: str
    catalogue_sha256: str
    preparation_id: str
    preparation_receipt_sha256: str
    migration_receipt: str

    def __post_init__(self) -> None:
        for name in (self.owner_login, self.act_schema):
            _need(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name)))
        for oid in (
            self.owner_oid,
            self.native_schema_owner_oid,
            self.native_function_owner_oid,
            self.database_oid,
            self.act_schema_oid,
        ):
            _need(type(oid) is int and 0 < oid <= 4294967295)
        for digest in (
            self.native_manifest_sha256,
            self.catalogue_function_source_sha256,
            self.catalogue_sha256,
            self.preparation_receipt_sha256,
        ):
            _need(bool(re.fullmatch(r"[0-9a-f]{64}", digest)))
        _need(bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.preparation_id)))
        _need(
            type(self.migration_receipt) is str
            and 0 < len(self.migration_receipt) <= 256
            and all(33 <= ord(c) <= 126 for c in self.migration_receipt)
        )


SESSION_SQL = """SELECT session_user::text,current_user::text,r.oid::bigint,
    d.oid::bigint,current_setting('server_version_num')::integer,
    current_setting('transaction_isolation'),current_setting('transaction_read_only'),
    pg_catalog.pg_has_role(session_user,n.nspowner,'USAGE'),n.nspowner::bigint
  FROM pg_catalog.pg_roles r,pg_catalog.pg_database d,pg_catalog.pg_namespace n
  WHERE r.rolname=session_user AND d.datname=current_database()
    AND n.nspname='maezo_native_v2'"""

CATALOGUE_FUNCTION_SQL = """SELECT p.proowner::bigint,p.prosecdef,
    p.proconfig,p.provolatile::text,p.prorettype::regtype::text,
    encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
  FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='maezo_native_v2' AND p.proname='catalogue_v2'
    AND p.pronargs=0"""

INSTALLATION_SQL = """SELECT v.manifest_digest,v.database_oid::bigint,
    v.act_schema,v.act_schema_oid::bigint,v.function_owner::bigint,
    v.catalogue_digest,v.migration_receipt,v.preparation_binding,
    v.preparation_receipt_bytes,
    (SELECT result_bytes FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT"
      WHERE preparation_id=%s::uuid AND event='PREPARED'),
    (SELECT result_sha256 FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT"
      WHERE preparation_id=%s::uuid AND event='PREPARED'),
    (SELECT oid::bigint FROM pg_catalog.pg_namespace WHERE nspname=v.act_schema)
  FROM maezo_native_v2.schema_version v WHERE migration_key='native-acquisition-v2'"""

CATALOGUE_SQL = """SELECT encode(sha256(convert_to(
    maezo_native_v2.catalogue_v2()::text,'UTF8')),'hex')"""


def _need(value: bool) -> None:
    if not value:
        raise ReadbackError("native_installation_readback_unavailable")


def _one(cursor: ReadbackCursor, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    cursor.execute(query, params)
    row = cursor.fetchone()
    _need(type(row) is tuple and cursor.fetchone() is None)
    assert row is not None
    return row


def read_installation(cursor: ReadbackCursor, pins: NativeInstallationPins) -> bytes:
    """Read in an already established REPEATABLE READ READ ONLY owner transaction.

    The caller owns begin/end/rollback and secure connection construction. Any DB
    error propagates without interpolating private input into a new diagnostic.
    The result deliberately cannot be parsed as a D qualification binding.
    """
    session = _one(cursor, SESSION_SQL)
    _need(len(session) == 9)
    _need(
        session[:4] == (pins.owner_login, pins.owner_login, pins.owner_oid, pins.database_oid)
        and type(session[4]) is int
        and 160000 <= session[4] < 170000
        and session[5:] == ("repeatable read", "on", True, pins.native_schema_owner_oid)
    )
    function = _one(cursor, CATALOGUE_FUNCTION_SQL)
    _need(
        function
        == (
            pins.native_function_owner_oid,
            True,
            ["search_path=pg_catalog, pg_temp"],
            "s",
            "jsonb",
            pins.catalogue_function_source_sha256,
        )
    )
    row = _one(cursor, INSTALLATION_SQL, (pins.preparation_id, pins.preparation_id))
    _need(len(row) == 12)
    _need(
        row[:7]
        == (
            pins.native_manifest_sha256,
            pins.database_oid,
            pins.act_schema,
            pins.act_schema_oid,
            pins.native_function_owner_oid,
            pins.catalogue_sha256,
            pins.migration_receipt,
        )
    )
    preparation = row[7]
    _need(type(preparation) is dict)
    _need(
        preparation.get("preparation_id") == pins.preparation_id
        and preparation.get("receipt_sha256") == pins.preparation_receipt_sha256
        and isinstance(row[8], (bytes, memoryview))
        and isinstance(row[9], (bytes, memoryview))
    )
    retained, original = bytes(row[8]), bytes(row[9])
    _need(
        0 < len(retained) <= 1048576
        and retained == original
        and hashlib.sha256(retained).hexdigest() == pins.preparation_receipt_sha256 == row[10]
        and row[11] == pins.act_schema_oid
    )
    _need(_one(cursor, CATALOGUE_SQL) == (pins.catalogue_sha256,))
    evidence = {
        "protocol": "maezo.native-v2-installation-readback.v1",
        "owner_login": pins.owner_login,
        "owner_oid": pins.owner_oid,
        "database_oid": pins.database_oid,
        "act_schema_oid": pins.act_schema_oid,
        "native_manifest_sha256": pins.native_manifest_sha256,
        "catalogue_sha256": pins.catalogue_sha256,
        "preparation_id": pins.preparation_id,
        "preparation_receipt_sha256": pins.preparation_receipt_sha256,
        "migration_receipt_sha256": hashlib.sha256(pins.migration_receipt.encode()).hexdigest(),
        "native_principal_qualification": False,
        "runtime_admission_verified": False,
    }
    return json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
