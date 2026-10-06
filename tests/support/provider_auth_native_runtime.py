"""Executable, observation-only Phase A producers for ROOT's native lane.

SQL, HTTPS and TLS functions use actual connections/files when ROOT calls them.
This module emits no approval, signature, receipt, ACK or business authority.
The preserved REST adapter uses HTTP Date. The protected operation uses actual
JVM samples without claiming independently calibrated UTC clocks.
All timestamps retain the original caller-selected ceiling after every wait.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import ssl
import stat
import time
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import asyncpg
import h11
import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from jsonschema import Draft202012Validator

from maezo.gateway.human.auth_profile import Definition, Scope
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.gateway.intake.native_authority import (
    AuthLifecycleConfiguration,
    NativeDatabaseBinding,
    ProtectedStoreBinding,
)
from maezo.gateway.intake.native_source_lifecycle import IdentitySourceBinding
from maezo.portal.engine.profile import canonicalize, strict_loads
from tests.support.provider_auth_native import NativeTestMaterials
from tests.support.provider_auth_native_installation import (
    NativeInstallationError,
    _artifact_bytes,
    _protected_material,
    validate_record,
    validate_supplement,
    verify_owner_classpath,
)

_PRE_SIGNATURE_KINDS = frozenset(
    {"Binding", "BootSwitch", "Measurements", "OwnerClasspathManifest", "OwnerRuntimeManifest"}
)


if TYPE_CHECKING:
    from tests.support.provider_auth_native_measurement import (
        NativeMeasurementConfiguration,
        NativeMeasurementProvenance,
    )


class NativeObservationError(RuntimeError):
    """Missing, stale or inconsistent facts; diagnostics never contain secrets."""


def clock(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo != UTC:
        raise NativeObservationError("UTC observation required")
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def current(deadline: datetime) -> datetime:
    now = datetime.now(UTC)
    if type(deadline) is not datetime or deadline.tzinfo != UTC or now >= deadline:
        raise NativeObservationError("Original observation ceiling expired")
    return now


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plain(record: Any) -> Any:
    """Detach caller objects through the existing bounded JSON domain."""
    try:
        return strict_loads(canonicalize(record))
    except (ValueError, TypeError, RuntimeError):
        raise NativeObservationError("Observation is not JSON data") from None


def _positive(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise NativeObservationError("Observed OID missing")
    return value


def _rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_plain(dict(row)) for row in rows]


@dataclass(frozen=True, repr=False)
class NativeDatabaseObservation:
    binding: dict[str, Any]
    role_observations: tuple[dict[str, Any], ...]
    catalog: tuple[dict[str, Any], ...]
    database_clock: datetime
    before: datetime
    after: datetime
    server_version: str
    system_identifier: str
    backend_pid: int
    role_facts: tuple[dict[str, Any], ...]
    engine_database: dict[str, Any] | None = None

    @property
    def catalog_sha256(self) -> str:
        # Exact projection/order of ProviderAuthTestOwner.catalogDigest. Before
        # owner install this can be empty; no assertion of the nine-table gate.
        return digest(list(self.catalog))

    def evidence(self) -> dict[str, Any]:
        observed = dict(
            schema="provider-native-database-observation.v1",
            binding=self.binding,
            catalog=list(self.catalog),
            database_clock=clock(self.database_clock),
            before=clock(self.before),
            after=clock(self.after),
            server_version=self.server_version,
            system_identifier=self.system_identifier,
            backend_pid=str(self.backend_pid),
            role_observations=list(self.role_observations),
            role_facts=list(self.role_facts),
            projection_sha256=digest(
                dict(
                    binding=_BINDING_SQL,
                    catalog=_CATALOG_SQL,
                    roles=_ROLES_SQL,
                    membership=_MEMBERSHIP_SQL,
                    schema_acl=_SCHEMA_ACL_SQL,
                    table_acl=_TABLE_ACL_SQL,
                )
            ),
        )
        if self.engine_database is not None:
            observed["engine_database"] = self.engine_database
        return observed


_BINDING_SQL = """SELECT current_database()::text AS database_name,
 d.oid::text AS database_oid,n.nspname::text AS schema_name,n.oid::text AS schema_oid,
 pg_get_userbyid(n.nspowner)::text AS owner_role,
 clock_timestamp() AS database_clock,current_setting('server_version')::text AS server_version,
 pg_backend_pid() AS backend_pid,
 (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier
 FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_namespace n
 WHERE d.datname=current_database() AND n.nspname='maezo_native'"""
_CATALOG_SQL = """SELECT c.oid::text AS oid,c.relname::text AS relname,
 pg_get_userbyid(c.relowner)::text AS owner,c.relkind::text AS kind,
 c.relrowsecurity,c.relforcerowsecurity,COALESCE(c.relacl::text,'') AS acl
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname=$1 AND c.relname LIKE 'mzo_auth_%' ORDER BY c.relname"""
_ROLES_SQL = """SELECT rolname::text AS role,rolsuper AS superuser,
 rolreplication AS replication,rolbypassrls AS bypassrls,rolcreatedb AS createdb,
 rolcreaterole AS createrole FROM pg_catalog.pg_roles
 WHERE rolname=ANY($1::text[]) ORDER BY rolname"""
_MEMBERSHIP_SQL = """SELECT granted.rolname::text AS granted_role,
 member.rolname::text AS member_role,grantor.rolname::text AS grantor_role,
 a.admin_option,a.inherit_option,a.set_option
 FROM pg_catalog.pg_auth_members a
 JOIN pg_catalog.pg_roles granted ON granted.oid=a.roleid
 JOIN pg_catalog.pg_roles member ON member.oid=a.member
 JOIN pg_catalog.pg_roles grantor ON grantor.oid=a.grantor
 WHERE member.rolname=$1 OR granted.rolname=$1
 ORDER BY granted.rolname,member.rolname,grantor.rolname"""
_SCHEMA_ACL_SQL = """SELECT n.nspname::text AS schema_name,
 pg_get_userbyid(n.nspowner)::text AS owner,COALESCE(n.nspacl::text,'') AS acl,
 has_schema_privilege($1,n.oid,'USAGE') AS usage,
 has_schema_privilege($1,n.oid,'CREATE') AS create_allowed
 FROM pg_catalog.pg_namespace n WHERE n.nspname IN ('maezo_native','cibseven','public')
 ORDER BY n.nspname"""
_TABLE_ACL_SQL = """SELECT c.relname::text AS relation_name,c.oid::text AS relation_oid,
 COALESCE(c.relacl::text,'') AS table_acl,a.attnum::text AS ordinal,
 a.attname::text AS column_name,COALESCE(a.attacl::text,'') AS column_acl,
 has_table_privilege($1,c.oid,'SELECT') AS select_allowed,
 has_table_privilege($1,c.oid,'INSERT') AS insert_allowed,
 has_table_privilege($1,c.oid,'UPDATE') AS update_allowed,
 has_table_privilege($1,c.oid,'DELETE') AS delete_allowed,
 has_table_privilege($1,c.oid,'TRUNCATE') AS truncate_allowed,
 has_table_privilege($1,c.oid,'REFERENCES') AS references_allowed,
 has_table_privilege($1,c.oid,'TRIGGER') AS trigger_allowed,
 has_column_privilege($1,c.oid,a.attnum,'SELECT') AS column_select_allowed,
 has_column_privilege($1,c.oid,a.attnum,'INSERT') AS column_insert_allowed,
 has_column_privilege($1,c.oid,a.attnum,'UPDATE') AS column_update_allowed,
 has_column_privilege($1,c.oid,a.attnum,'REFERENCES') AS column_references_allowed
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
 WHERE n.nspname='maezo_native' AND a.attnum>0 AND NOT a.attisdropped
 ORDER BY c.relname,a.attnum"""

# A separate ACT3 projection, byte-compatible with the admitted Java catalogue.
# The AUTH/MZO catalogue above has a different domain and digest by design.
_ENGINE_SCHEMAS_SQL = """SELECT nspname::text AS name,oid::text AS oid,
 pg_get_userbyid(nspowner)::text AS owner FROM pg_catalog.pg_namespace
 WHERE nspname IN ('cibseven','maezo_native') ORDER BY nspname"""
_ENGINE_RELATIONS_SQL = """SELECT n.nspname::text AS schema,c.relname::text AS name,
 c.oid::text AS oid,pg_get_userbyid(c.relowner)::text AS owner,c.relkind::text AS kind,
 c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,c.relacl::text AS acl
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='cibseven' AND c.relname IN
 ('act_re_procdef','act_re_deployment','act_ge_bytearray') ORDER BY n.nspname,c.relname"""


def _engine_catalogue_projection(value: Any) -> dict[str, Any]:
    """Closed actual ACT3+two-schema domain; no expected-data fallback."""
    _check_observation_value(value)
    data = _plain(value)
    if (
        type(data) is not dict
        or set(data) != {"schema", "schemas", "relations"}
        or data["schema"] != "provider-native-act-catalogue.v1"
        or type(data["schemas"]) is not list
        or type(data["relations"]) is not list
        or len(data["schemas"]) != 2
        or len(data["relations"]) != 3
    ):
        raise NativeObservationError("Actual engine catalogue observation required")
    schemas = data["schemas"]
    relations = data["relations"]
    for schema in schemas:
        if (
            type(schema) is not dict
            or set(schema) != {"name", "oid", "owner"}
            or type(schema["name"]) is not str
            or type(schema["owner"]) is not str
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema["owner"]) is None
        ):
            raise NativeObservationError("Actual engine schema observation refused")
        _positive(schema["oid"])
    if [schema["name"] for schema in schemas] != ["cibseven", "maezo_native"]:
        raise NativeObservationError("Actual engine schema census differs")
    for relation in relations:
        if (
            type(relation) is not dict
            or set(relation) != {"schema", "name", "oid", "owner", "kind", "rls", "force_rls", "acl"}
            or relation["schema"] != "cibseven"
            or type(relation["name"]) is not str
            or type(relation["owner"]) is not str
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", relation["owner"]) is None
            or relation["kind"] != "r"
            or relation["rls"] is not False
            or relation["force_rls"] is not False
            or (relation["acl"] is not None and type(relation["acl"]) is not str)
        ):
            raise NativeObservationError("Actual ACT3 relation observation refused")
        _positive(relation["oid"])
    if [relation["name"] for relation in relations] != [
        "act_ge_bytearray",
        "act_re_deployment",
        "act_re_procdef",
    ] or len({relation["oid"] for relation in relations}) != 3:
        raise NativeObservationError("Actual ACT3 catalogue census differs")
    return data


async def observe_native_database(
    connection: asyncpg.Connection,
    *,
    roles: tuple[str, ...],
    deadline: datetime,
    include_engine: bool = False,
) -> NativeDatabaseObservation:
    """Read actual PostgreSQL 16+ facts in one read-only repeatable-read snapshot.

    ROOT supplies its real owner/admin connection; no connection or credential
    is created here. pg_control_system requires actual permission. Failure is
    not replaced by an invented incarnation. No table installation occurs.
    """
    guard = _ObservationWaitGuard.start(deadline)
    before = guard.start_wall
    if (
        not isinstance(connection, asyncpg.Connection)
        or connection.is_in_transaction()
        or not 3 <= len(roles) <= 32
        or len(set(roles)) != len(roles)
        or any(type(r) is not str or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", r) is None for r in roles)
        or not {"maezo_native_schema_owner", "cibseven_app"} <= set(roles)
        or type(include_engine) is not bool
    ):
        raise NativeObservationError("Real clean connection and explicit native roles required")
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            guard.check()
            row = await connection.fetchrow(_BINDING_SQL, timeout=5)
            guard.check()
            if row is None:
                raise NativeObservationError("Native schema is not installed")
            binding = validate_record(
                "Binding",
                dict(
                    schema="human-auth-native-database.v1",
                    database_name=row["database_name"],
                    database_oid=_positive(row["database_oid"]),
                    schema_name=row["schema_name"],
                    schema_oid=_positive(row["schema_oid"]),
                    owner_role=row["owner_role"],
                    runtime_role="cibseven_app",
                ),
            )
            catalog = _rows(await connection.fetch(_CATALOG_SQL, binding["schema_name"], timeout=5))
            guard.check()
            engine_database = None
            observed_roles = set(roles)
            if include_engine:
                schemas = _rows(await connection.fetch(_ENGINE_SCHEMAS_SQL, timeout=5))
                guard.check()
                relations = _rows(await connection.fetch(_ENGINE_RELATIONS_SQL, timeout=5))
                guard.check()
                engine_catalogue = _engine_catalogue_projection(
                    dict(schema="provider-native-act-catalogue.v1", schemas=schemas, relations=relations)
                )
                engine_database = dict(
                    database_name=row["database_name"],
                    database_oid=row["database_oid"],
                    system_identifier=row["system_identifier"],
                    catalogue=engine_catalogue,
                )
            role_rows = _rows(await connection.fetch(_ROLES_SQL, sorted(observed_roles), timeout=5))
            guard.check()
            if {r["role"] for r in role_rows} != observed_roles or len(role_rows) != len(observed_roles):
                raise NativeObservationError("Requested native role absent")
            observed = []
            facts = []
            for role in role_rows:
                membership = _rows(await connection.fetch(_MEMBERSHIP_SQL, role["role"], timeout=5))
                guard.check()
                schema_acl = _rows(await connection.fetch(_SCHEMA_ACL_SQL, role["role"], timeout=5))
                guard.check()
                table_acl = _rows(await connection.fetch(_TABLE_ACL_SQL, role["role"], timeout=5))
                guard.check()
                facts.append(
                    dict(
                        role=role["role"],
                        membership=membership,
                        schema_acl=schema_acl,
                        table_column_acl=table_acl,
                    )
                )
                observed.append(
                    validate_record(
                        "RoleObservation",
                        dict(
                            **role,
                            membership_digest=digest(membership),
                            schema_acl_digest=digest(schema_acl),
                            table_column_acl_digest=digest(table_acl),
                        ),
                    )
                )
            clock(row["database_clock"])
            _positive(row["system_identifier"])
            if type(row["backend_pid"]) is not int or row["backend_pid"] <= 0:
                raise NativeObservationError("PostgreSQL backend missing")
        after = current(deadline)  # Includes transaction cleanup/rollback wait.
        observed_database = NativeDatabaseObservation(
            binding=binding,
            role_observations=tuple(observed),
            catalog=tuple(catalog),
            database_clock=row["database_clock"],
            before=before,
            after=after,
            server_version=row["server_version"],
            system_identifier=row["system_identifier"],
            backend_pid=row["backend_pid"],
            role_facts=tuple(facts),
            engine_database=engine_database,
        )
        guard.check()  # Includes transaction cleanup and detached construction.
        return observed_database
    except (asyncpg.PostgresError, NativeInstallationError, KeyError, TypeError, ValueError):
        raise NativeObservationError("PostgreSQL observation refused") from None


def pin_file(
    path: Path, *, ref: str, media_type: str, deadline: datetime, secret: bool = False
) -> dict[str, Any]:
    """Hash the bytes of a real regular file, without exposing its contents."""
    guard = _ObservationWaitGuard.start(deadline)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise NativeObservationError("Absolute non-symlink artifact required")
    limit = 67108864 if media_type == "application/java-archive" else 4194304
    if secret:
        limit = 262144
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid not in (0, os.geteuid())
            or stat.S_IMODE(metadata.st_mode) & (0o077 if secret else 0o022)
            or not 0 < metadata.st_size <= limit
        ):
            raise NativeObservationError("Artifact custody refused")
        if secret:
            _directory(path.parent)
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            any(getattr(metadata, key) != getattr(after, key) for key in stable)
            or len(raw) != metadata.st_size
        ):
            raise NativeObservationError("Artifact changed during observation")
        result = validate_record(
            "Artifact", dict(ref=ref, path=str(path), sha256=_hash(raw), media_type=media_type)
        )
        _artifact_bytes(result)  # Reopen against independently observed bytes.
        current(deadline)
    except (OSError, NativeInstallationError):
        raise NativeObservationError("Artifact observation refused") from None
    finally:
        if fd >= 0:
            os.close(fd)
    guard.check()
    return result


def _directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path or not path.is_dir() or path.is_symlink():
        raise NativeObservationError("Real protected output directory required")
    info = path.stat()
    if info.st_uid not in (0, os.geteuid()) or stat.S_IMODE(info.st_mode) & 0o077:
        raise NativeObservationError("Protected output directory custody refused")


def _origin(origin: str) -> tuple[str, int]:
    if (
        type(origin) is not str
        or re.fullmatch(r"https://(127\.0\.0\.1|localhost):[1-9][0-9]{0,4}", origin) is None
    ):
        raise NativeObservationError("Pinned loopback HTTPS origin required")
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError:
        raise NativeObservationError("Loopback port refused") from None
    if port is None or not 1 <= port <= 65535 or parsed.hostname is None:
        raise NativeObservationError("Loopback port refused")
    return parsed.hostname, port


def _tls(materials: NativeTestMaterials) -> ssl.SSLContext:
    if type(materials) is not NativeTestMaterials:
        raise NativeObservationError("Owned native TLS materials required")
    materials.guard()
    context = materials.tls_context()
    cert = x509.load_pem_x509_certificate(materials.ca_certificate.read_bytes())
    if (
        context.verify_mode != ssl.CERT_REQUIRED
        or not context.check_hostname
        or context.get_ca_certs(binary_form=True) != [cert.public_bytes(serialization.Encoding.DER)]
    ):
        raise NativeObservationError("Native TLS trust differs")
    return context


@dataclass(frozen=True, repr=False)
class NativeTlsObservation:
    origin: str
    server_peer_spki_sha256: str
    client_ca_sha256: str
    probe: dict[str, Any]


async def probe_receiver_tls(
    *, origin: str, materials: NativeTestMaterials, expected_spki: str, deadline: datetime
) -> NativeTlsObservation:
    """Actual mTLS handshake, CA/hostname verification and peer SPKI readback."""
    guard = _ObservationWaitGuard.start(deadline)
    before = guard.start_wall
    host, port = _origin(origin)
    context = _tls(materials)
    current(deadline)
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host), timeout=5
        )
        current(deadline)
        session = writer.get_extra_info("ssl_object")
        if session is None:
            raise NativeObservationError("TLS session missing")
        peer = session.getpeercert(binary_form=True)
        if type(peer) is not bytes:
            raise NativeObservationError("TLS peer missing")
        cert = x509.load_der_x509_certificate(peer)
        public = cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        actual = _hash(public)
        if (
            actual != expected_spki
            or not cert.not_valid_before_utc <= datetime.now(UTC) < cert.not_valid_after_utc
        ):
            raise NativeObservationError("Actual TLS SPKI/currentness differs")
        probe = dict(
            schema="provider-native-tls-observation.v1",
            origin=origin,
            before=clock(before),
            certificate_sha256=_hash(peer),
            server_peer_spki_sha256=actual,
            tls_version=session.version(),
            cipher=[str(part) for part in session.cipher()],
        )
    except (OSError, ValueError, ssl.SSLError, TimeoutError):
        raise NativeObservationError("Actual native TLS probe refused") from None
    finally:
        if writer is not None:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=5)
    probe["after"] = clock(current(deadline))
    materials.guard()
    ca_sha256 = _hash(materials.ca_certificate.read_bytes())
    observed = NativeTlsObservation(origin, actual, ca_sha256, probe)
    guard.check()
    return observed


@dataclass(frozen=True, repr=False)
class NativeEngineObservation:
    definition: Definition
    version: str
    source_xml: bytes
    retrieved_xml: bytes
    profile: bytes
    engine_clock: datetime
    before: datetime
    after: datetime
    route_probe: dict[str, Any]
    origin: str
    _operation_proof: NativeOperationObservationProof | None = field(default=None, repr=False)
    _measurement_provenance: NativeMeasurementProvenance | None = field(default=None, repr=False)


@dataclass(frozen=True, repr=False)
class NativeOperationObservationProof:
    """Detached bytes needed to revalidate new observations before effects."""

    raw_response: bytes
    raw_request: bytes
    inputs: NativeObservationInputs
    transport: NativeObservationTransport
    socket_json: bytes


# The contract is outside the frozen R2b bundle. Loading another schema, even
# one with the same $id, must not let a caller weaken this observation parser.
_OBSERVATION_SCHEMA_SHA256 = "d0affa2deb9c75e8aa2a52b52abc70e8052e81a9c1d98540af889ec32d27e2a1"
_OBSERVATION_ROUTE = "/engine-rest/maezo/v1/operations"
_OBSERVATION_LIMIT = 1048576
_OBSERVATION_CLASSES = tuple(
    sorted(
        (
            "br.com.maezo.workload.WorkloadPlugin",
            "org.cibseven.bpm.engine.impl.ProcessEngineImpl",
            "br.com.maezo.workload.RuntimeDefinitionObservation",
            "br.com.maezo.human.AuthValues",
        )
    )
)


def _check_observation_value(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise NativeObservationError("Observation nesting bound refused")
    if type(value) is str:
        value.encode("utf-8", "strict")
    elif type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise NativeObservationError("Observation primitive key refused")
            _check_observation_value(key, depth + 1)
            _check_observation_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _check_observation_value(item, depth + 1)
    elif value is not None and type(value) not in (bool, int):
        raise NativeObservationError("Observation primitive type refused")
    elif type(value) is int and not -(2**63) <= value < 2**63:
        raise NativeObservationError("Observation integer overflow")


def _observation_json(raw: bytes, *, limit: int = _OBSERVATION_LIMIT) -> Any:
    """Closed primitive JSON domain; integers here are not AUTH wire numbers."""
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise NativeObservationError("Observation JSON bound refused")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise NativeObservationError("Observation duplicate key")
            result[key] = value
        return result

    def number(_: str) -> Any:
        raise NativeObservationError("Observation floating point refused")

    try:
        result = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_float=number,
            parse_constant=number,
        )
        _check_observation_value(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise NativeObservationError("Observation JSON refused") from None


def _observation_bytes(value: Any) -> bytes:
    # Snapshot integers are explicitly allowed; this is NOT signed AUTH/R2b
    # canonicalization. Raw request/response digests always use received bytes.
    try:
        _check_observation_value(value)
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    except (ValueError, UnicodeError):
        raise NativeObservationError("Observation primitive serialization refused") from None
    _observation_json(raw)
    return raw


def _observation_time(value: Any) -> datetime:
    if type(value) is not str or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value) is None:
        raise NativeObservationError("Observation timestamp refused")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise NativeObservationError("Observation timestamp refused") from None


@dataclass(frozen=True, repr=False)
class NativeObservationFile:
    """An actual file and raw byte pin. No implicit credential discovery."""

    path: Path
    sha256: str

    def read(self, *, deadline: datetime, limit: int, private: bool = True) -> bytes:
        guard = _ObservationWaitGuard.start(deadline)
        if (
            type(self.path) is not type(Path())
            or not self.path.is_absolute()
            or self.path.resolve() != self.path
            or type(self.sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", self.sha256) is None
        ):
            raise NativeObservationError("Pinned observation file refused")
        if private:
            _directory(self.path.parent)
            if stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
                raise NativeObservationError("Observer private parent must be mode0700")
        fd = -1
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(before.st_mode) != (0o600 if private else 0o644)
                or not 0 < before.st_size <= limit
            ):
                raise NativeObservationError("Pinned observation file custody refused")
            raw = os.read(fd, limit + 1)
            after = os.fstat(fd)
            if (
                any(
                    getattr(before, name) != getattr(after, name)
                    for name in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_uid",
                        "st_gid",
                        "st_nlink",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                )
                or len(raw) != before.st_size
                or _hash(raw) != self.sha256
                or self.path.stat(follow_symlinks=False).st_ino != before.st_ino
                or self.path.stat(follow_symlinks=False).st_dev != before.st_dev
                or self.path.resolve() != self.path
            ):
                raise NativeObservationError("Pinned observation bytes changed")
            if private:
                _directory(self.path.parent)
                if stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
                    raise NativeObservationError("Observer private parent custody changed")
            guard.check()
        except OSError:
            raise NativeObservationError("Pinned observation file unavailable") from None
        finally:
            if fd >= 0:
                os.close(fd)
        guard.check()  # File close/FD cleanup is part of the original budget.
        return raw


@dataclass(frozen=True, repr=False)
class NativeObservationTransport:
    """Dedicated observer identity; never the shared AUTH publication/read key."""

    origin: str
    client_certificate: NativeObservationFile
    client_key: NativeObservationFile
    ca_certificate: NativeObservationFile
    expected_server_spki_sha256: str
    forbidden_auth_spki_sha256: tuple[str, ...]

    def guard(self, deadline: datetime) -> tuple[bytes, bytes, bytes]:
        guard = _ObservationWaitGuard.start(deadline)
        _origin(self.origin)
        if (
            type(self.forbidden_auth_spki_sha256) is not tuple
            or not self.forbidden_auth_spki_sha256
            or any(
                type(v) is not str or re.fullmatch(r"[a-f0-9]{64}", v) is None
                for v in self.forbidden_auth_spki_sha256
            )
            or type(self.expected_server_spki_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", self.expected_server_spki_sha256) is None
        ):
            raise NativeObservationError("Observer identity separation pins missing")
        if any(
            type(ref) is not NativeObservationFile
            for ref in (self.client_certificate, self.client_key, self.ca_certificate)
        ):
            raise NativeObservationError("Observer file refs refused")
        cert_raw = self.client_certificate.read(deadline=deadline, limit=262144)
        key_raw = self.client_key.read(deadline=deadline, limit=262144)
        ca_raw = self.ca_certificate.read(deadline=deadline, limit=262144)
        now = current(deadline)
        try:
            if (
                cert_raw.count(b"-----BEGIN CERTIFICATE-----") != 1
                or ca_raw.count(b"-----BEGIN CERTIFICATE-----") != 1
            ):
                raise NativeObservationError("Single observer leaf and CA required")
            cert = x509.load_pem_x509_certificate(cert_raw)
            ca = x509.load_pem_x509_certificate(ca_raw)
            key = serialization.load_pem_private_key(key_raw, password=None)
            public = cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            if (
                public
                != key.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
                or _hash(public) in self.forbidden_auth_spki_sha256
                or _hash(public) == self.expected_server_spki_sha256
                or cert.issuer != ca.subject
                or ca.issuer != ca.subject
                or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                or not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
                or cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                or not cert.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature
                or cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
                or cert.extensions.get_extension_for_class(x509.KeyUsage).value.crl_sign
                or ExtendedKeyUsageOID.CLIENT_AUTH
                not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                or ExtendedKeyUsageOID.SERVER_AUTH
                in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                or not cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                or not cert.not_valid_before_utc <= now < cert.not_valid_after_utc
                or not ca.not_valid_before_utc <= now < ca.not_valid_after_utc
                or deadline > min(cert.not_valid_after_utc, ca.not_valid_after_utc)
            ):
                raise NativeObservationError("Observer PKI identity/currentness refused")
            cert.verify_directly_issued_by(ca)
            ca.verify_directly_issued_by(ca)
        except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature, UnsupportedAlgorithm):
            raise NativeObservationError("Observer PKI refused") from None
        guard.check()
        return cert_raw, key_raw, ca_raw

    def context(self, deadline: datetime) -> ssl.SSLContext:
        guard = _ObservationWaitGuard.start(deadline)
        _, _, ca_raw = self.guard(deadline)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cadata=ca_raw.decode("ascii"))
        # OpenSSL receives the already opened inodes, not mutable caller paths.
        cert_fd = key_fd = -1
        try:
            cert_fd = os.open(self.client_certificate.path, os.O_RDONLY | os.O_NOFOLLOW)
            key_fd = os.open(self.client_key.path, os.O_RDONLY | os.O_NOFOLLOW)
            context.load_cert_chain(f"/dev/fd/{cert_fd}", f"/dev/fd/{key_fd}")
            self.guard(deadline)
            for fd, pin in ((cert_fd, self.client_certificate), (key_fd, self.client_key)):
                os.lseek(fd, 0, os.SEEK_SET)
                if _hash(os.read(fd, 262145)) != pin.sha256:
                    raise NativeObservationError("Observer TLS opened inode changed")
        except (OSError, ValueError, ssl.SSLError):
            raise NativeObservationError("Observer TLS context refused") from None
        finally:
            for fd in (cert_fd, key_fd):
                if fd >= 0:
                    os.close(fd)
        ca = x509.load_pem_x509_certificate(ca_raw)
        if context.get_ca_certs(binary_form=True) != [ca.public_bytes(serialization.Encoding.DER)]:
            raise NativeObservationError("Observer TLS root differs")
        guard.check()  # All opened credential FDs and CA parsing are complete.
        return context


@dataclass(frozen=True, repr=False)
class NativeObservationInputs:
    """Preboot pins. Expected image/candidate are not server attestations."""

    admission: NativeObservationFile
    schema: NativeObservationFile
    source_xml: NativeObservationFile
    profile: NativeObservationFile
    policy_digest: str
    capability_digest: str
    measurement_configuration: NativeMeasurementConfiguration | None = field(default=None, repr=False)
    installed_support_jar: NativeObservationFile | None = field(default=None, repr=False)

    def load(self, deadline: datetime) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
        guard = _ObservationWaitGuard.start(deadline)
        if any(
            type(ref) is not NativeObservationFile
            for ref in (self.admission, self.schema, self.source_xml, self.profile)
        ):
            raise NativeObservationError("Observer input refs refused")
        if self.schema.sha256 != _OBSERVATION_SCHEMA_SHA256:
            raise NativeObservationError("Observer contract schema differs")
        schema = _observation_json(
            self.schema.read(deadline=deadline, limit=262144, private=False), limit=262144
        )
        admission = _observation_json(self.admission.read(deadline=deadline, limit=65536), limit=65536)
        _observation_validate(schema, "ObservationAdmission", admission)
        issued = _observation_time(admission["issued_at"])
        if (
            admission["original_deadline"] != clock(deadline)
            or not issued < deadline <= issued + timedelta(minutes=15)
            or issued > current(deadline) + timedelta(seconds=5)
            or any(
                type(v) is not str or re.fullmatch(r"[a-f0-9]{64}", v) is None
                for v in (self.policy_digest, self.capability_digest)
            )
        ):
            raise NativeObservationError("Observer original admission ceiling differs")
        source = self.source_xml.read(deadline=deadline, limit=524288, private=False)
        profile = self.profile.read(deadline=deadline, limit=65536, private=False)
        try:
            if (
                _hash(source) != admission["xml_sha256"]
                or _hash(profile) != admission["profile_sha256"]
                or canonicalize(strict_loads(profile)) != profile
                or b"<!DOCTYPE" in source.replace(b"\x00", b"").upper()
                or b"<!ENTITY" in source.replace(b"\x00", b"").upper()
            ):
                raise NativeObservationError("Observer source/profile bytes differ")
            document = ElementTree.fromstring(source)
            processes = document.findall("{http://www.omg.org/spec/BPMN/20100524/MODEL}process")
            if len(processes) != 1 or processes[0].get("id") != admission["process_key"]:
                raise NativeObservationError("Observer source process differs")
        except (ValueError, ElementTree.ParseError):
            raise NativeObservationError("Observer source/profile refused") from None
        from tests.support.provider_auth_native_measurement import (
            NativeMeasurementConfiguration,
            installed_measurement_origin,
        )

        measured_origin = installed_measurement_origin(
            self.installed_support_jar, admission=admission, deadline=deadline
        )
        if measured_origin != (self.measurement_configuration is not None):
            raise NativeObservationError("Actual installed measurement designation/config differs")
        if self.measurement_configuration is not None:
            if type(self.measurement_configuration) is not NativeMeasurementConfiguration:
                raise NativeObservationError("Concrete pinned measurement configuration required")
            _, measurement_config = self.measurement_configuration.load(deadline)
            if (
                measurement_config["observation_admission_sha256"] != self.admission.sha256
                or measurement_config["candidate_sha"] != admission["candidate_sha"]
                or measurement_config["expected_image_id"] != admission["expected_image_id"]
                or measurement_config["root_pg_preboot_witness_sha256"]
                != admission["expected_database"]["root_pg_preboot_witness_sha256"]
            ):
                raise NativeObservationError("Measurement preboot admission/candidate differs")
        guard.check()  # Includes the last XML/profile/schema CPU validation.
        return schema, admission, source, profile


def _observation_validate(schema: dict[str, Any], name: str, value: Any) -> None:
    try:
        validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"})
        if not validator.is_valid(value):
            raise NativeObservationError("Closed observer schema refused")
    except (KeyError, TypeError, ValueError):
        raise NativeObservationError("Observer schema unavailable") from None


def native_observation_request(inputs: NativeObservationInputs, *, deadline: datetime) -> bytes:
    guard = _ObservationWaitGuard.start(deadline)
    if type(inputs) is not NativeObservationInputs:
        raise NativeObservationError("Closed native observation input required")
    schema, admission, _, _ = inputs.load(deadline)
    request = dict(
        protocol="maezo.engine-operation.v1",
        capability_digest=inputs.capability_digest,
        operation="read_runtime_definition",
        process_key="SP-OP-AUTH-001",
        resource_ref=admission["nonce"],
        variables={},
        correlation={},
        all_matching=False,
        error_code="",
        topic="",
        message="",
        worker_id="",
        parameters=dict(
            admission_sha256=inputs.admission.sha256,
            nonce=admission["nonce"],
            original_deadline=clock(deadline),
        ),
        source_ref="",
    )
    _observation_validate(schema, "Request", request)
    raw = _observation_bytes(request)
    if inputs.measurement_configuration is not None:
        _, measurement_config = inputs.measurement_configuration.load(deadline)
        if _hash(raw) != measurement_config["request_sha256"]:
            raise NativeObservationError("Immutable measurement config/raw request differs")
    guard.check()
    return raw


def _number_free_observation(value: Any) -> Any:
    """Evidence adapter only. Raw response SHA preserves its original types."""
    if type(value) is int:
        return str(value)
    if type(value) is list:
        return [_number_free_observation(item) for item in value]
    if type(value) is dict:
        return {key: _number_free_observation(item) for key, item in value.items()}
    return value


def _decode_observed_artifact(record: dict[str, Any], *, profile: bool) -> bytes:
    try:
        encoded = record["base64" if profile else "xml_base64"]
        raw = base64.b64decode(encoded, validate=True)
        if (
            base64.b64encode(raw).decode("ascii") != encoded
            or not 0 < len(raw) <= (65536 if profile else 524288)
            or len(raw) != record["size" if profile else "xml_size"]
            or _hash(raw) != record["sha256" if profile else "xml_sha256"]
        ):
            raise NativeObservationError("Observed artifact bytes refused")
        return raw
    except (ValueError, TypeError, binascii.Error):
        raise NativeObservationError("Observed artifact encoding refused") from None


def _validate_observation_stages(
    result: dict[str, Any],
    admission: dict[str, Any],
    *,
    inputs: NativeObservationInputs,
    before: datetime,
    after: datetime,
    deadline: datetime,
) -> None:
    """Compare only stable projections; preserve genuine NEW transaction facts."""
    expected_binding = {
        key: admission[key]
        for key in (
            "engine_name",
            "definition_id",
            "deployment_id",
            "process_key",
            "process_version",
            "xml_sha256",
            "profile_sha256",
            "original_deadline",
        )
    }
    expected_binding.update(
        tenant=admission["identity"]["tenant"],
        environment=admission["identity"]["environment"],
        policy_digest=inputs.policy_digest,
        observation_admission_sha256=inputs.admission.sha256,
        capability_digest=inputs.capability_digest,
        candidate_expectations=dict(
            candidate_sha=admission["candidate_sha"],
            image_id=admission["expected_image_id"],
            validation_origin="root-crosslink-required",
        ),
    )
    expected_install = {
        key: result[key]
        for key in (
            "engine_name",
            "authorization_enabled",
            "tenant_check_enabled",
            "schema_update",
            "jvm_process",
            "class_provenance",
            "mounted_pins",
        )
    }
    if result["candidate_expectations"] != expected_binding["candidate_expectations"]:
        raise NativeObservationError("Observed candidate expectation differs")
    if (
        any(result["mounted_pins"][key] != admission[key] for key in result["mounted_pins"])
        or result["jvm_process"]["engine_registry_name"] != admission["engine_name"]
        or tuple(row["class_name"] for row in result["class_provenance"]) != _OBSERVATION_CLASSES
    ):
        raise NativeObservationError("Observed installation origins differ")
    classes = result["class_provenance"]
    native = admission["native_jar_sha256"]
    if any(
        row["jar_sha256"] != native
        for row in classes
        if row["class_name"] != "org.cibseven.bpm.engine.impl.ProcessEngineImpl"
    ):
        raise NativeObservationError("Observed native classes differ")
    stages = [result["database_witness"], *result["emission_recheck"]["read_stages"]]
    seen_uuid: set[str] = set()
    seen_xid: set[str] = set()
    backend_starts: dict[str, str] = {}
    previous_pg: datetime | None = None
    previous_jvm = _observation_time(result["clock"]["sample_started_at"])
    cutoff = _observation_time(result["cutoff_observed_at"])
    finished = _observation_time(result["clock"]["sample_finished_at"])
    engine_time = _observation_time(result["clock"]["engine_clock"])
    lower, upper = before - timedelta(seconds=5), after + timedelta(seconds=5)
    jvm_lower, jvm_upper = before - timedelta(milliseconds=1), after + timedelta(milliseconds=1)
    elapsed = int(result["clock"]["monotonic_elapsed_ns"])
    if (
        not jvm_lower <= previous_jvm <= cutoff <= finished <= jvm_upper
        or not jvm_lower <= engine_time <= jvm_upper
        or any(value.microsecond % 1000 for value in (previous_jvm, cutoff, finished, engine_time))
        or (after - before).total_seconds() > 5
        or elapsed > 5_000_000_000
        or elapsed / 1e9 > (after - before).total_seconds() + 0.002
        or abs(elapsed / 1e9 - (finished - previous_jvm).total_seconds()) > 0.001
    ):
        raise NativeObservationError("Captured JVM clock bracket refused")
    for sequence, stage in enumerate(stages):
        if (
            stage["stage"] != ("CAPTURE" if sequence == 0 else "EMISSION_RECHECK")
            or type(stage["stage_sequence"]) is not int
            or stage["stage_sequence"] != sequence
            or stage["server_stage_execution_uuid"] in seen_uuid
        ):
            raise NativeObservationError("Observed stage order/identity refused")
        seen_uuid.add(stage["server_stage_execution_uuid"])
        entry, committing = stage["entry"], stage["committing"]
        for sample in (entry, committing):
            if (
                sample["stable_database_projection"] != admission["expected_database"]["stable_projection"]
                or sample["binding_projection"] != expected_binding
                or sample["runtime_installation_projection"] != expected_install
            ):
                raise NativeObservationError("Observed stable stage projection differs")
        first, last = entry["session_transaction"], committing["session_transaction"]
        if any(
            first[key] != last[key]
            for key in (
                "backend_pid",
                "backend_start",
                "transaction_started_at",
                "backend_ssl",
                "backend_tls_version",
            )
        ):
            raise NativeObservationError("Connection changed within observed stage")
        xid_entry, xid_commit = first["transaction_id_if_assigned"], last["transaction_id_if_assigned"]
        if xid_entry is not None and xid_entry != xid_commit:
            raise NativeObservationError("Observed stage assigned XID changed")
        if xid_commit is not None:
            if xid_commit in seen_xid:
                raise NativeObservationError("Observed new transaction copied assigned XID")
            seen_xid.add(xid_commit)
        start = _observation_time(first["backend_start"])
        tx_start = _observation_time(first["transaction_started_at"])
        entered = _observation_time(first["observed_at"])
        committed_sample = _observation_time(last["observed_at"])
        jvm_after = _observation_time(stage["jvm_after_commit_at"])
        if (
            not start <= tx_start <= entered <= committed_sample
            or not lower <= entered <= committed_sample <= upper
            or not jvm_lower <= jvm_after <= jvm_upper
            or jvm_after.microsecond % 1000 != 0
            or not previous_jvm <= jvm_after < deadline
            or (sequence == 0 and not finished <= jvm_after)
            or (previous_pg is not None and entered < previous_pg)
            or committed_sample > jvm_after + timedelta(seconds=5)
            or any(_observation_time(sample["observed_at"]) >= deadline for sample in (first, last))
        ):
            raise NativeObservationError("Observed transaction/JVM clock relation refused")
        pid = first["backend_pid"]
        if pid in backend_starts and backend_starts[pid] != first["backend_start"]:
            raise NativeObservationError("Observed backend incarnation changed")
        backend_starts[pid] = first["backend_start"]
        previous_pg, previous_jvm = committed_sample, jvm_after
    if result["emission_recheck"]["observed_at"] != stages[-1]["jvm_after_commit_at"]:
        raise NativeObservationError("Observed emission clock copied or retagged")


def parse_native_engine_observation(
    *,
    raw_response: bytes,
    raw_request: bytes,
    inputs: NativeObservationInputs,
    transport: NativeObservationTransport,
    socket_evidence: Mapping[str, Any],
    before: datetime,
    after: datetime,
    deadline: datetime,
) -> NativeEngineObservation:
    """Real protected-operation envelope, never three simulated REST responses.

    This parser proves shape and bindings, not ROOT's independent readbacks.
    A parsed snapshot remains PENDING_ROOT_READBACK until every witness is
    corroborated and joined by ROOT before Measurements construction.
    """
    guard = _ObservationWaitGuard.start(deadline)
    if type(inputs) is not NativeObservationInputs or type(transport) is not NativeObservationTransport:
        raise NativeObservationError("Closed observation inputs/transport required")
    schema, admission, source_xml, profile = inputs.load(deadline)
    cert_raw, _, _ = transport.guard(deadline)
    if (
        type(before) is not datetime
        or type(after) is not datetime
        or before.tzinfo != UTC
        or after.tzinfo != UTC
        or before > after
        or after >= deadline
    ):
        raise NativeObservationError("Observed transport clock bracket refused")
    response = _observation_json(raw_response)
    request = _observation_json(raw_request, limit=65536)
    _observation_validate(schema, "Response", response)
    _observation_validate(schema, "Request", request)
    if request != _observation_json(native_observation_request(inputs, deadline=deadline)):
        raise NativeObservationError("Observed request admission differs")
    result = response["result"]
    cert = x509.load_pem_x509_certificate(cert_raw)
    actual_client_spki = _hash(
        cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    if (
        response["capability_digest"] != inputs.capability_digest
        or result["capability_digest"] != inputs.capability_digest
        or result["request_sha256"] != _hash(raw_request)
        or result["observation_admission_sha256"] != inputs.admission.sha256
        or result["policy_digest"] != inputs.policy_digest
        or any(
            result[key] != admission[key]
            for key in (
                "identity",
                "peer_certificate_sha256",
                "peer_spki_sha256",
                "engine_name",
                "nonce",
                "original_deadline",
            )
        )
        or result["peer_certificate_sha256"] != _hash(cert.public_bytes(serialization.Encoding.DER))
        or result["peer_spki_sha256"] != actual_client_spki
        or any(
            result["definition"][key] != admission[key]
            for key in ("definition_id", "deployment_id", "process_key", "process_version", "xml_sha256")
        )
        or result["definition"]["tenant"] != admission["identity"]["tenant"]
        or _decode_observed_artifact(result["definition"], profile=False) != source_xml
        or _decode_observed_artifact(result["profile"], profile=True) != profile
        or result["profile"]["sha256"] != admission["profile_sha256"]
    ):
        raise NativeObservationError("Observed envelope/source/peer bindings differ")
    _validate_observation_stages(
        result, admission, inputs=inputs, before=before, after=after, deadline=deadline
    )
    if type(socket_evidence) is not dict:
        raise NativeObservationError("Concrete observer socket evidence required")
    socket = _observation_json(_observation_bytes(socket_evidence), limit=65536)
    if (
        type(socket) is not dict
        or set(socket)
        != {
            "schema",
            "origin",
            "server_certificate_sha256",
            "server_peer_spki_sha256",
            "tls_version",
            "cipher",
            "before",
            "after",
        }
        or socket["schema"] != "provider-native-observer-http-tls.v1"
        or socket["origin"] != transport.origin
        or socket["server_peer_spki_sha256"] != transport.expected_server_spki_sha256
        or socket["tls_version"] not in ("TLSv1.2", "TLSv1.3")
        or re.fullmatch(r"[a-f0-9]{64}", socket["server_certificate_sha256"]) is None
        or socket["before"] != clock(before)
        or socket["after"] != clock(after)
    ):
        raise NativeObservationError("Observed same-origin socket binding differs")
    metadata = _observation_json(_observation_bytes(result))
    del metadata["definition"]["xml_base64"]
    del metadata["profile"]["base64"]
    if len(_observation_bytes(metadata)) > 65536:
        raise NativeObservationError("Observed stage metadata exceeds bound")
    route = dict(
        schema="provider-native-engine-operation-observation.v4",
        before=clock(before),
        after=clock(after),
        original_deadline=clock(deadline),
        origin=transport.origin,
        request_sha256=_hash(raw_request),
        response_sha256=_hash(raw_response),
        observation_admission_sha256=inputs.admission.sha256,
        contract_schema_sha256=inputs.schema.sha256,
        transport=_number_free_observation(socket),
        snapshot=_number_free_observation(metadata),
        root_crosslink_status="PENDING_ROOT_READBACK",
    )
    # Carry-compatible number-free R2b evidence, with original raw hashes.
    if len(canonicalize(route)) > 65536:
        raise NativeObservationError("Observed route evidence exceeds bound")
    observed = NativeEngineObservation(
        Definition(
            process_key="SP-OP-AUTH-001",
            definition_id=admission["definition_id"],
            deployment_id=admission["deployment_id"],
            definition_digest=_hash(source_xml),
            input_profile="portal-auth-intake.v1",
            profile_digest=_hash(profile),
        ),
        result["cibseven_version"],
        source_xml,
        source_xml,
        profile,
        _observation_time(result["clock"]["engine_clock"]),
        before,
        after,
        route,
        transport.origin,
        NativeOperationObservationProof(
            raw_response, raw_request, inputs, transport, _observation_bytes(socket)
        ),
    )
    guard.check()  # Object construction and raw-proof serialization are done.
    return observed


@dataclass
class _ObservationWaitGuard:
    deadline: datetime
    start_wall: datetime
    start_ns: int
    last_wall: datetime

    @classmethod
    def start(cls, deadline: datetime) -> _ObservationWaitGuard:
        wall = current(deadline)
        return cls(deadline, wall, time.monotonic_ns(), wall)

    def check(self) -> None:
        wall = current(self.deadline)
        elapsed = time.monotonic_ns() - self.start_ns
        if (
            wall < self.last_wall
            or elapsed < 0
            or elapsed >= (self.deadline - self.start_wall).total_seconds() * 1e9
            or abs((wall - self.start_wall).total_seconds() - elapsed / 1e9) > 5
        ):
            raise NativeObservationError("Observer original wait ceiling/clock refused")
        self.last_wall = wall


async def _observation_wait(
    awaitable: Awaitable[Any],
    *,
    guard: _ObservationWaitGuard,
    transport: NativeObservationTransport,
    inputs: NativeObservationInputs,
    close_connection_on_refusal: bool = False,
) -> Any:
    result = await asyncio.wait_for(
        awaitable, timeout=max(0, min(5, (guard.deadline - datetime.now(UTC)).total_seconds()))
    )
    try:
        guard.check()
        transport.guard(guard.deadline)
        inputs.load(guard.deadline)
        guard.check()  # Trusted synchronous validation also consumes time.
    except BaseException:
        if close_connection_on_refusal:
            result[1].close()
            await asyncio.wait_for(result[1].wait_closed(), timeout=5)
        raise
    return result


def _observation_http_headers(raw: bytes) -> None:
    # h11 rejects ambiguous framing. Also forbid identical duplicate framing
    # headers, folding, compression and a simultaneous CL/TE before parsing.
    lines = raw.split(b"\r\n")
    if not lines[0].startswith(b"HTTP/1.1 200 "):
        raise NativeObservationError("Protected observer status/protocol refused")
    headers: dict[bytes, bytes] = {}
    for line in lines[1:]:
        if not line:
            continue
        if line[:1] in (b" ", b"\t") or b":" not in line:
            raise NativeObservationError("Protected observer folded header refused")
        name, value = line.split(b":", 1)
        name, value = name.lower(), value.strip()
        if name in headers:
            raise NativeObservationError("Protected observer duplicate header refused")
        headers[name] = value
    if (
        headers.get(b"content-type", b"").lower()
        not in (b"application/json", b"application/json;charset=utf-8", b"application/json; charset=utf-8")
        or headers.get(b"content-encoding", b"identity") != b"identity"
        or b"location" in headers
        or (b"content-length" in headers and b"transfer-encoding" in headers)
    ):
        raise NativeObservationError("Protected observer HTTP envelope refused")


async def observe_native_engine_operation(
    *, inputs: NativeObservationInputs, transport: NativeObservationTransport, deadline: datetime
) -> NativeEngineObservation:
    """Actual single POST; verified socket SPKI BEFORE any request bytes.

    h11 handles bounded HTTP/1.1 framing; asyncio owns the actual TLS socket.
    No HTTP proxy, redirect, retry, connection pool, compression or hidden GET.
    """
    if type(inputs) is not NativeObservationInputs or type(transport) is not NativeObservationTransport:
        raise NativeObservationError("Closed observer transport/inputs required")
    guard = _ObservationWaitGuard.start(deadline)
    before = guard.start_wall
    request = native_observation_request(inputs, deadline=deadline)
    context = transport.context(deadline)
    host, port = _origin(transport.origin)
    guard.check()
    writer = None
    raw = bytearray()
    socket: dict[str, Any]
    try:
        reader, writer = await _observation_wait(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host),
            guard=guard,
            transport=transport,
            inputs=inputs,
            close_connection_on_refusal=True,
        )
        session = writer.get_extra_info("ssl_object")
        peer_address = writer.get_extra_info("peername")
        if (
            type(peer_address) is not tuple
            or len(peer_address) not in (2, 4)
            or type(peer_address[0]) is not str
            or not ipaddress.ip_address(peer_address[0]).is_loopback
            or type(peer_address[1]) is not int
            or peer_address[1] != port
        ):
            raise NativeObservationError("Protected observer actual socket must be loopback")
        peer = None if session is None else session.getpeercert(binary_form=True)
        if session is None or type(peer) is not bytes:
            raise NativeObservationError("Protected observer TLS socket unavailable")
        cert = x509.load_der_x509_certificate(peer)
        spki = _hash(
            cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        if (
            spki != transport.expected_server_spki_sha256
            or not cert.not_valid_before_utc <= current(deadline) < cert.not_valid_after_utc
            or deadline > cert.not_valid_after_utc
            or session.version() not in ("TLSv1.2", "TLSv1.3")
        ):
            raise NativeObservationError("Protected observer server TLS identity refused")
        socket = dict(
            schema="provider-native-observer-http-tls.v1",
            origin=transport.origin,
            server_certificate_sha256=_hash(peer),
            server_peer_spki_sha256=spki,
            tls_version=session.version(),
            cipher=[str(part) for part in session.cipher()],
            before=clock(before),
        )
        connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
        pieces = [
            connection.send(
                h11.Request(
                    method="POST",
                    target=_OBSERVATION_ROUTE,
                    headers=[
                        ("Host", f"{host}:{port}"),
                        ("Content-Type", "application/json"),
                        ("Accept", "application/json"),
                        ("Accept-Encoding", "identity"),
                        ("Content-Length", str(len(request))),
                        ("Connection", "close"),
                    ],
                )
            ),
            connection.send(h11.Data(data=request)),
            connection.send(h11.EndOfMessage()),
        ]
        request_wire = b"".join(part for part in pieces if part is not None)
        transport.guard(deadline)
        inputs.load(deadline)
        # Immutable bytes are ready; this final wall+monotonic check performs
        # no I/O and is immediately adjacent to the first remote effect.
        guard.check()
        writer.write(request_wire)
        await _observation_wait(writer.drain(), guard=guard, transport=transport, inputs=inputs)
        response_seen = False
        header_buffer = bytearray()
        headers_checked = False
        total_wire = 0
        while True:
            event = connection.next_event()
            if event is h11.NEED_DATA:
                chunk = await _observation_wait(
                    reader.read(8192), guard=guard, transport=transport, inputs=inputs
                )
                total_wire += len(chunk)
                if total_wire > 2 * _OBSERVATION_LIMIT:
                    raise NativeObservationError("Protected observer total wire bound refused")
                if not headers_checked:
                    header_buffer.extend(chunk)
                    boundary = header_buffer.find(b"\r\n\r\n")
                    if boundary >= 0:
                        if boundary > 16384:
                            raise NativeObservationError("Protected observer header bound refused")
                        _observation_http_headers(bytes(header_buffer[:boundary]))
                        headers_checked = True
                    elif len(header_buffer) > 16384:
                        raise NativeObservationError("Protected observer header bound refused")
                connection.receive_data(chunk)
            elif type(event) is h11.Response:
                if (
                    response_seen
                    or event.status_code != 200
                    or event.http_version != b"1.1"
                    or not headers_checked
                ):
                    raise NativeObservationError("Protected observer response refused")
                response_seen = True
            elif type(event) is h11.Data:
                if not response_seen:
                    raise NativeObservationError("Protected observer body before headers refused")
                raw.extend(event.data)
                if len(raw) > _OBSERVATION_LIMIT:
                    raise NativeObservationError("Protected observer HTTP bytes exceed bound")
                guard.check()
            elif type(event) is h11.EndOfMessage:
                if not response_seen or event.headers or connection.trailing_data[0]:
                    raise NativeObservationError("Protected observer trailers/mixed HTTP refused")
                break
            else:
                raise NativeObservationError("Protected observer incomplete/interim HTTP refused")
        guard.check()
    except (OSError, ValueError, ssl.SSLError, TimeoutError, h11.ProtocolError):
        raise NativeObservationError("Protected native observation transport refused") from None
    finally:
        if writer is not None:
            writer.close()
            # Cleanup is mandatory even after expiry; validity is not renewed.
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=5)
            except (OSError, ssl.SSLError, TimeoutError):
                raise NativeObservationError("Protected observer TLS close refused") from None
    guard.check()  # Includes stream/socket close awaits, with the same ceiling.
    transport.guard(deadline)
    inputs.load(deadline)
    after = current(deadline)
    socket["after"] = clock(after)
    observed = parse_native_engine_observation(
        raw_response=bytes(raw),
        raw_request=request,
        inputs=inputs,
        transport=transport,
        socket_evidence=socket,
        before=before,
        after=after,
        deadline=deadline,
    )
    guard.check()  # Includes parsing after the complete socket close.
    return observed


@dataclass(frozen=True, repr=False)
class NativeObservationRootCrosslink:
    """ROOT-produced private readback artifact, separate from server claims.

    Its producer and custody remain ROOT's independent gate responsibility.
    This adapter checks the closed values against EACH server witness, not an
    authority signature, qualification receipt or assumption about provenance.
    """

    artifact: NativeObservationFile

    def validate(
        self, engine: NativeEngineObservation, *, inputs: NativeObservationInputs, deadline: datetime
    ) -> dict[str, Any]:
        guard = _ObservationWaitGuard.start(deadline)
        if type(self.artifact) is not NativeObservationFile or type(engine) is not NativeEngineObservation:
            raise NativeObservationError("ROOT independent readback artifact required")
        _validate_operation_proof(engine, deadline=deadline)
        schema, admission, _, _ = inputs.load(deadline)
        actual = _observation_json(self.artifact.read(deadline=deadline, limit=65536), limit=65536)
        required = {
            "schema",
            "source",
            "before",
            "after",
            "origin",
            "request_sha256",
            "response_sha256",
            "root_pg_preboot_witness_sha256",
            "candidate_sha",
            "image_id",
            "source_xml_sha256",
            "profile_sha256",
            "server_peer_spki_sha256",
            "server_certificate_sha256",
            "stable_database_projection",
            "runtime_installation_projection",
            "stage_session_correlations",
        }
        if type(actual) is not dict or set(actual) != required:
            raise NativeObservationError("Closed ROOT independent readback shape refused")
        route = engine.route_probe
        if route.get("schema") != "provider-native-engine-operation-observation.v4":
            raise NativeObservationError("ROOT readback needs protected operation snapshot")
        if (
            actual["schema"] != "provider-native-root-observation-crosslink.v1"
            or actual["source"] != "root-owned-physical-resource-and-jar-readback"
            or actual["origin"] != engine.origin
            or actual["request_sha256"] != route["request_sha256"]
            or actual["response_sha256"] != route["response_sha256"]
            or actual["root_pg_preboot_witness_sha256"]
            != admission["expected_database"]["root_pg_preboot_witness_sha256"]
            or actual["candidate_sha"] != admission["candidate_sha"]
            or actual["image_id"] != admission["expected_image_id"]
            or actual["source_xml_sha256"] != _hash(engine.source_xml)
            or actual["profile_sha256"] != _hash(engine.profile)
            or actual["server_peer_spki_sha256"] != route["transport"]["server_peer_spki_sha256"]
            or actual["server_certificate_sha256"] != route["transport"]["server_certificate_sha256"]
        ):
            raise NativeObservationError("ROOT physical candidate/transport readback differs")
        before, after = _observation_time(actual["before"]), _observation_time(actual["after"])
        if (
            not before <= after < deadline
            or before > engine.before
            or after < engine.after
            or (after - before).total_seconds() > 5
        ):
            raise NativeObservationError("ROOT contemporaneous readback bracket refused")
        _observation_validate(schema, "StableDatabaseProjection", actual["stable_database_projection"])
        _observation_validate(
            schema, "RuntimeInstallationProjection", actual["runtime_installation_projection"]
        )
        if actual["stable_database_projection"] != admission["expected_database"]["stable_projection"]:
            raise NativeObservationError("ROOT actual PG incarnation/catalogue differs")
        snapshot = route["snapshot"]
        install = {
            key: snapshot[key]
            for key in (
                "engine_name",
                "authorization_enabled",
                "tenant_check_enabled",
                "schema_update",
                "jvm_process",
                "class_provenance",
                "mounted_pins",
            )
        }
        if _number_free_observation(actual["runtime_installation_projection"]) != install:
            raise NativeObservationError("ROOT actual runtime/JAR/boot readback differs")
        stages = [snapshot["database_witness"], *snapshot["emission_recheck"]["read_stages"]]
        correlations = actual["stage_session_correlations"]
        if type(correlations) is not list or len(correlations) != 2 * len(stages):
            raise NativeObservationError("ROOT EACH stage session readback missing")
        session_keys = {
            "backend_pid",
            "backend_start",
            "transaction_started_at",
            "transaction_id_if_assigned",
            "backend_ssl",
            "backend_tls_version",
        }
        for index, correlation in enumerate(correlations):
            stage, endpoint = stages[index // 2], ("entry" if index % 2 == 0 else "committing")
            if (
                type(correlation) is not dict
                or set(correlation)
                != {
                    "stage_sequence",
                    "server_stage_execution_uuid",
                    "sample",
                    "source",
                    "root_observed_at",
                    "session",
                    "full_xid_mapping",
                }
                or type(correlation["stage_sequence"]) is not int
                or str(correlation["stage_sequence"]) != stage["stage_sequence"]
                or correlation["server_stage_execution_uuid"] != stage["server_stage_execution_uuid"]
                or correlation["sample"] != endpoint
                or correlation["source"] != "root-pg-stat-activity-and-ssl-readback"
                or type(correlation["session"]) is not dict
                or set(correlation["session"]) != session_keys
                or type(correlation["session"]["backend_ssl"]) is not bool
            ):
                raise NativeObservationError("ROOT closed stage/session correlation refused")
            observed = stage[endpoint]["session_transaction"]
            if correlation["session"] != {key: observed[key] for key in session_keys}:
                raise NativeObservationError("ROOT stage session physical readback differs")
            mapping = correlation["full_xid_mapping"]
            if (
                type(mapping) is not dict
                or set(mapping) != {"source", "backend_xid32", "active_full_xids"}
                or mapping["source"] != "root-pg-current-snapshot-and-stat-activity"
                or type(mapping["active_full_xids"]) is not list
                or any(
                    type(value) is not str
                    or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None
                    or int(value) >= 2**64
                    for value in mapping["active_full_xids"]
                )
                or len(set(mapping["active_full_xids"])) != len(mapping["active_full_xids"])
            ):
                raise NativeObservationError("ROOT actual full-XID snapshot readback missing")
            xid, xid32 = observed["transaction_id_if_assigned"], mapping["backend_xid32"]
            if xid is None:
                if xid32 is not None:
                    raise NativeObservationError("ROOT unassigned XID readback differs")
            else:
                if (
                    type(xid32) is not str
                    or re.fullmatch(r"[1-9][0-9]{0,9}", xid32) is None
                    or int(xid32) >= 2**32
                ):
                    raise NativeObservationError("ROOT backend xid32 readback refused")
                matches = [value for value in mapping["active_full_xids"] if int(value) % 2**32 == int(xid32)]
                if matches != [xid]:
                    raise NativeObservationError("ROOT full-XID mapping absent/ambiguous/different")
            observed_at = _observation_time(correlation["root_observed_at"])
            if (
                not before <= observed_at <= after
                or abs((observed_at - _observation_time(observed["observed_at"])).total_seconds()) > 5
            ):
                raise NativeObservationError("ROOT stage session readback stale")
        guard.check()
        return actual


def corroborate_native_engine_observation(
    *,
    engine: NativeEngineObservation,
    inputs: NativeObservationInputs,
    root_crosslink: NativeObservationRootCrosslink,
    deadline: datetime,
    provenance: NativeMeasurementProvenance | None = None,
) -> NativeEngineObservation:
    """Attach a checked independent readback; absence is not silently waived."""
    guard = _ObservationWaitGuard.start(deadline)
    if type(root_crosslink) is not NativeObservationRootCrosslink:
        raise NativeObservationError("ROOT each-witness corroboration required")
    root_crosslink.validate(engine, inputs=inputs, deadline=deadline)
    if engine._operation_proof is not None and inputs != engine._operation_proof.inputs:
        raise NativeObservationError("Corroboration cannot replace frozen operation inputs")
    if (
        inputs.measurement_configuration is not None
        or engine._measurement_provenance is not None
        or provenance is not None
    ):
        from tests.support.provider_auth_native_measurement import NativeMeasurementProvenance

        if type(provenance) is not NativeMeasurementProvenance:
            raise NativeObservationError("Measurements retained pending immutable ROOT provenance")
        provenance.validate(engine, inputs=inputs, root_crosslink=root_crosslink, deadline=deadline)
    route = _plain(engine.route_probe)
    route.update(
        root_crosslink_status="ROOT_READBACK_VALUES_MATCHED",
        root_crosslink=dict(path=str(root_crosslink.artifact.path), sha256=root_crosslink.artifact.sha256),
        admission=dict(path=str(inputs.admission.path), sha256=inputs.admission.sha256),
        contract_schema=dict(path=str(inputs.schema.path), sha256=inputs.schema.sha256),
        source_xml=dict(path=str(inputs.source_xml.path), sha256=inputs.source_xml.sha256),
        profile=dict(path=str(inputs.profile.path), sha256=inputs.profile.sha256),
    )
    if provenance is not None:
        route["measurement_provenance"] = dict(
            path=str(provenance.artifact.path), sha256=provenance.artifact.sha256
        )
    if len(canonicalize(route)) > 65536:
        raise NativeObservationError("Corroborated route evidence exceeds bound")
    observed = NativeEngineObservation(
        engine.definition,
        engine.version,
        engine.source_xml,
        engine.retrieved_xml,
        engine.profile,
        engine.engine_clock,
        engine.before,
        engine.after,
        route,
        engine.origin,
        engine._operation_proof,
        provenance,
    )
    guard.check()
    return observed


def _validate_operation_proof(engine: NativeEngineObservation, *, deadline: datetime) -> None:
    guard = _ObservationWaitGuard.start(deadline)
    if (
        type(engine) is not NativeEngineObservation
        or type(engine.route_probe) is not dict
        or type(engine.definition) is not Definition
        or any(
            type(value) is not bytes for value in (engine.source_xml, engine.retrieved_xml, engine.profile)
        )
    ):
        raise NativeObservationError("Concrete protected operation observation required")
    proof = engine._operation_proof
    if type(proof) is not NativeOperationObservationProof:
        raise NativeObservationError("Protected operation detached raw proof missing")
    observed = parse_native_engine_observation(
        raw_response=proof.raw_response,
        raw_request=proof.raw_request,
        inputs=proof.inputs,
        transport=proof.transport,
        socket_evidence=_observation_json(proof.socket_json),
        before=engine.before,
        after=engine.after,
        deadline=deadline,
    )
    allowed_extensions = {
        "root_crosslink",
        "admission",
        "contract_schema",
        "source_xml",
        "profile",
        "measurement_provenance",
    }
    route = {key: value for key, value in engine.route_probe.items() if key not in allowed_extensions}
    route["root_crosslink_status"] = "PENDING_ROOT_READBACK"
    if (
        route != observed.route_probe
        or engine.definition != observed.definition
        or engine.version != observed.version
        or engine.source_xml != observed.source_xml
        or engine.retrieved_xml != observed.retrieved_xml
        or engine.profile != observed.profile
        or engine.engine_clock != observed.engine_clock
        or engine.origin != observed.origin
    ):
        raise NativeObservationError("Protected operation observation mutated after capture")
    guard.check()


def _join_physical_engine_database(database: NativeDatabaseObservation, physical: dict[str, Any]) -> None:
    """Join genuine Root PG reads to engine/Root P_DB; ACT3 is not MZO."""
    sample = database.engine_database
    if type(sample) is not dict or set(sample) != {
        "database_name",
        "database_oid",
        "system_identifier",
        "catalogue",
    }:
        raise NativeObservationError("Independent Root engine database observation missing")
    _check_observation_value(sample)
    _check_observation_value(database.binding)
    _positive(database.system_identifier)
    catalogue = _engine_catalogue_projection(sample["catalogue"])
    engine_schema, native_schema = catalogue["schemas"]
    if (
        sample["database_name"] != database.binding["database_name"]
        or sample["database_oid"] != database.binding["database_oid"]
        or sample["system_identifier"] != database.system_identifier
        or physical["database_name"] != database.binding["database_name"]
        or physical["database_oid"] != database.binding["database_oid"]
        or physical["system_identifier"] != database.system_identifier
        or physical["native_schema_name"] != database.binding["schema_name"]
        or physical["native_schema_oid"] != database.binding["schema_oid"]
        or engine_schema["name"] != physical["engine_schema_name"]
        or engine_schema["oid"] != physical["engine_schema_oid"]
        or native_schema["name"] != physical["native_schema_name"]
        or native_schema["oid"] != physical["native_schema_oid"]
        or native_schema["owner"] != database.binding["owner_role"]
        or _hash(_observation_bytes(catalogue)) != physical["catalogue_projection_sha256"]
        or physical["current_user"] != database.binding["runtime_role"]
        or physical["session_user"] != database.binding["runtime_role"]
    ):
        raise NativeObservationError("Engine and Root source use different physical database bindings")
    try:
        roles = {row["role"]: validate_record("RoleObservation", row) for row in database.role_observations}
    except (NativeInstallationError, KeyError, TypeError, ValueError):
        raise NativeObservationError("Actual native role observation refused") from None
    if len(roles) != len(database.role_observations) or not {
        database.binding["owner_role"],
        database.binding["runtime_role"],
    } <= set(roles):
        raise NativeObservationError("Actual native owner/runtime role observation missing")
    # The catalogue records actual ACT owners, which may differ from the
    # command's runtime role. They are bound by their own Root ACT digest; no
    # schema-owner == current_user equality or fabricated role flags is used.


def _rest_json(raw: bytes) -> Any:
    # Actual CIB REST definition JSON has numeric version/TTL. This decoder is
    # for observations only; signed canonical preimages remain number-free.
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise NativeObservationError("Actual REST JSON duplicate key")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise NativeObservationError("Actual REST JSON non-finite number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def parse_engine_observation(
    *,
    version_response: httpx.Response,
    definition_response: httpx.Response,
    xml_response: httpx.Response,
    definition_id: str,
    source_xml: bytes,
    profile: bytes,
    before: datetime,
    after: datetime,
    deadline: datetime,
    origin: str,
) -> NativeEngineObservation:
    """Parse actual REST response bytes; UNIT callers supply labelled extracts."""
    guard = _ObservationWaitGuard.start(deadline)
    _origin(origin)
    clock(before)
    clock(after)
    if before > after:
        raise NativeObservationError("HTTP observation bracket reversed")
    responses = (version_response, definition_response, xml_response)
    for response in responses:
        if (
            type(response) is not httpx.Response
            or response.status_code != 200
            or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
            or not 0 < len(response.content) <= 4194304
        ):
            raise NativeObservationError("Actual engine REST response refused")
    try:
        version = _rest_json(version_response.content)
        deployed = _rest_json(definition_response.content)
        xml = _rest_json(xml_response.content)
        dates = [parsedate_to_datetime(r.headers["date"]) for r in responses]
        if any(d.tzinfo != UTC or abs((d - after).total_seconds()) > 5 for d in dates):
            raise NativeObservationError("Actual HTTP Date absent or skewed")
        observed = dates[-1]
        if version != {"version": "2.1.0"} or set(xml) != {"id", "bpmn20Xml"}:
            raise NativeObservationError("Measured engine version/XML shape differs")
        if (
            type(deployed) is not dict
            or deployed.get("id") != definition_id
            or deployed.get("key") != "SP-OP-AUTH-001"
            or xml["id"] != definition_id
            or type(xml["bpmn20Xml"]) is not str
            or xml["bpmn20Xml"].encode() != source_xml
            or b"<!DOCTYPE" in source_xml.upper()
            or b"<!ENTITY" in source_xml.upper()
        ):
            raise NativeObservationError("Retrieved deployment/XML differs")
        document = ElementTree.fromstring(source_xml)
        processes = document.findall("{http://www.omg.org/spec/BPMN/20100524/MODEL}process")
        if len(processes) != 1 or processes[0].get("id") != "SP-OP-AUTH-001":
            raise NativeObservationError("Actual BPMN process differs")
        profile_record = strict_loads(profile)
        if canonicalize(profile_record) != profile:
            raise NativeObservationError("Actual profile is not canonical")
        definition = Definition(
            process_key="SP-OP-AUTH-001",
            definition_id=definition_id,
            deployment_id=deployed["deploymentId"],
            definition_digest=_hash(source_xml),
            input_profile="portal-auth-intake.v1",
            profile_digest=_hash(profile),
        )
    except (KeyError, TypeError, ValueError, ElementTree.ParseError):
        raise NativeObservationError("Actual engine observation refused") from None
    route = dict(
        schema="provider-native-engine-rest-observation.v1",
        before=clock(before),
        after=clock(after),
        engine_clock=clock(observed),
        engine_clock_source="HTTP Date",
        origin=origin,
        engine_clock_resolution_seconds="1",
        engine_clock_limit="server wall clock; not JVM sampling or monotonic qualification",
        responses=[dict(status=str(r.status_code), body_sha256=_hash(r.content)) for r in responses],
    )
    result = NativeEngineObservation(
        definition,
        version["version"],
        source_xml,
        source_xml,
        profile,
        observed,
        before,
        after,
        route,
        origin,
    )
    guard.check()
    return result


async def observe_engine_rest(
    *,
    origin: str,
    materials: NativeTestMaterials,
    definition_id: str,
    source_xml: bytes,
    profile: bytes,
    deadline: datetime,
) -> NativeEngineObservation:
    """Read exact HTTPS REST resources with owned mTLS, no redirects/env proxy."""
    guard = _ObservationWaitGuard.start(deadline)
    before = guard.start_wall
    _origin(origin)
    context = _tls(materials)
    current(deadline)
    if type(definition_id) is not str or not 1 <= len(definition_id) <= 256:
        raise NativeObservationError("Definition identifier missing")
    encoded = quote(definition_id, safe="")
    async with httpx.AsyncClient(
        verify=context, trust_env=False, follow_redirects=False, timeout=5
    ) as client:
        responses = []
        for path in (
            "/engine-rest/version",
            f"/engine-rest/process-definition/{encoded}",
            f"/engine-rest/process-definition/{encoded}/xml",
        ):
            current(deadline)
            async with client.stream("GET", origin + path) as response:
                stream = response.extensions.get("network_stream")
                session = None if stream is None else stream.get_extra_info("ssl_object")
                peer = None if session is None else session.getpeercert(binary_form=True)
                if type(peer) is not bytes:
                    raise NativeObservationError("Actual HTTPS peer unavailable")
                peer_key = (
                    x509.load_der_x509_certificate(peer)
                    .public_key()
                    .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                )
                expected = [
                    d.peer_spki_sha256
                    for d in materials.proposed_designations
                    if d.purpose == "human-auth-result"
                ]
                if expected != [_hash(peer_key)]:
                    raise NativeObservationError("Actual HTTPS engine peer SPKI differs")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    current(deadline)
                    raw.extend(chunk)
                    if len(raw) > 4194304:
                        raise NativeObservationError("Engine response exceeds bound")
                responses.append(
                    httpx.Response(response.status_code, headers=response.headers, content=bytes(raw))
                )
            current(deadline)
    after = current(deadline)  # Includes response/client close awaits.
    materials.guard()
    observed = parse_engine_observation(
        version_response=responses[0],
        definition_response=responses[1],
        xml_response=responses[2],
        definition_id=definition_id,
        source_xml=source_xml,
        profile=profile,
        before=before,
        after=after,
        deadline=deadline,
        origin=origin,
    )
    guard.check()
    return observed


@dataclass(frozen=True)
class ArtifactLink:
    """Explicit edge to an earlier artifact, never a guessed future hash."""

    name: str


@dataclass(frozen=True, repr=False)
class CanonicalPreimages:
    records: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    order: tuple[str, ...]
    kinds: dict[str, str]


def prepare_canonical_dag(
    directory: Path, nodes: Mapping[str, tuple[str, Mapping[str, Any]]], *, deadline: datetime
) -> CanonicalPreimages:
    """Resolve/hash the complete finite DAG before writing or asking for signatures.

    Kinds are existing frozen schemas or the two admitted manifest supplements.
    Cycles, missing edges, direct future refs, drift and unknown fields refuse.
    No record is asserted to have been independently signed or installed.
    """
    guard = _ObservationWaitGuard.start(deadline)
    _directory(directory)
    if not 1 <= len(nodes) <= 32 or any(
        re.fullmatch(r"[a-z][a-z0-9-]{0,63}\.json", n) is None for n in nodes
    ):
        raise NativeObservationError("Finite canonical node names required")
    records: dict[str, dict[str, Any]] = {}
    refs: dict[str, dict[str, Any]] = {}
    pending: set[str] = set()
    order: list[str] = []
    planned = {str(directory / n) for n in nodes}

    def resolve(value: Any) -> Any:
        if type(value) is ArtifactLink:
            visit(value.name)
            return dict(refs[value.name])
        if type(value) is dict:
            if set(value) == {"ref", "path", "sha256", "media_type"}:
                if value["path"] in planned:
                    raise NativeObservationError("Future artifact needs an explicit DAG edge")
                _artifact_bytes(value)
                current(deadline)
            return {key: resolve(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [resolve(item) for item in value]
        if type(value) not in (str, int, bool, type(None)):
            raise NativeObservationError("Canonical DAG contains unknown data")
        return value

    def visit(name: str) -> None:
        if name in refs:
            return
        if name not in nodes or name in pending:
            raise NativeObservationError("Canonical DAG missing edge or cycle")
        pending.add(name)
        kind, template = nodes[name]
        if type(kind) is not str or kind not in _PRE_SIGNATURE_KINDS or type(template) is not dict:
            raise NativeObservationError("Canonical preimage must be a record")
        value = resolve(template)
        try:
            record = (
                validate_supplement(kind, value)
                if kind in {"OwnerClasspathManifest", "OwnerRuntimeManifest"}
                else validate_record(kind, value)
            )
        except (NativeInstallationError, KeyError):
            raise NativeObservationError("Preimage violates the frozen schema") from None
        records[name] = record
        refs[name] = validate_record(
            "Artifact",
            dict(
                ref="provider-native-" + name,
                path=str(directory / name),
                sha256=digest(record),
                media_type="application/json",
            ),
        )
        pending.remove(name)
        order.append(name)
        current(deadline)

    for name in nodes:
        visit(name)
    result = CanonicalPreimages(records, refs, tuple(order), {n: nodes[n][0] for n in order})
    guard.check()
    return result


def _preimage_snapshot(
    preimages: CanonicalPreimages, directory: Path
) -> tuple[tuple[str, bytes, dict[str, Any]], ...]:
    """Validate public/mutable plans and detach all write inputs before effects."""
    if type(preimages) is not CanonicalPreimages or type(preimages.order) is not tuple:
        raise NativeObservationError("Closed canonical preimage plan required")
    maps = (preimages.records, preimages.artifacts, preimages.kinds)
    if any(type(mapping) is not dict for mapping in maps):
        raise NativeObservationError("Closed preimage maps required")
    order = preimages.order
    if not 1 <= len(order) <= 32 or any(
        type(name) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,63}\.json", name) is None for name in order
    ):
        raise NativeObservationError("Finite canonical leaf names required")
    names = set(order)
    if len(names) != len(order) or any(
        any(type(key) is not str for key in mapping) or set(mapping) != names for mapping in maps
    ):
        raise NativeObservationError("Preimage set/order differs")
    # Copy the outer maps before invoking schema validation or filesystem APIs.
    records, artifacts, kinds = (dict(mapping) for mapping in maps)
    writes: list[tuple[str, bytes, dict[str, Any]]] = []
    try:
        for name in order:
            kind = kinds[name]
            if type(kind) is not str or kind not in _PRE_SIGNATURE_KINDS:
                raise NativeObservationError("Only pre-signature artifact kinds are writable")
            if type(records[name]) is not dict or type(artifacts[name]) is not dict:
                raise NativeObservationError("Closed preimage record/reference required")
            # canonicalize accepts only exact JSON primitives/containers and rejects
            # custom getters/comparators, numbers, cycles and excessive nesting.
            raw = canonicalize(records[name])
            ref_raw = canonicalize(artifacts[name])
            if not 0 < len(raw) <= 4194304 or len(ref_raw) > 65536:
                raise NativeObservationError("Preimage byte limit exceeded")
            record, ref = json.loads(raw), json.loads(ref_raw)
            validate_record("Artifact", ref)
            child = directory / name
            if (
                child.parent != directory
                or ref["path"] != str(child)
                or ref["ref"] != "provider-native-" + name
                or ref["media_type"] != "application/json"
                or ref["sha256"] != _hash(raw)
            ):
                raise NativeObservationError("Preimage bytes/path/reference drifted")
            if kind in {"OwnerClasspathManifest", "OwnerRuntimeManifest"}:
                validate_supplement(kind, record)
            else:
                validate_record(kind, record)
            writes.append((name, raw, ref))
    except (NativeInstallationError, KeyError, TypeError, ValueError, RuntimeError):
        raise NativeObservationError("Canonical preimage validation refused") from None
    return tuple(writes)


def write_preimages(directory: Path, preimages: CanonicalPreimages, *, deadline: datetime) -> None:
    """CREATE_NEW protected files only; partial evidence is retained on failure."""
    guard = _ObservationWaitGuard.start(deadline)
    if type(directory) is not type(Path()):
        raise NativeObservationError("Exact filesystem path required")
    _directory(directory)
    writes = _preimage_snapshot(preimages, directory)
    current(deadline)
    expected = directory.stat()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        actual = os.fstat(fd)
        if (
            (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
            or not stat.S_ISDIR(actual.st_mode)
            or actual.st_uid not in (0, os.geteuid())
            or stat.S_IMODE(actual.st_mode) & 0o077
        ):
            raise NativeObservationError("Protected output directory changed")
        for name, raw, ref in writes:
            current(deadline)
            output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=fd)
            with os.fdopen(output, "wb") as stream:
                guard.check()
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            current(deadline)
            _artifact_bytes(ref)
    finally:
        os.close(fd)
    guard.check()


def assemble_phase_a_preimages(
    directory: Path,
    *,
    measurements: Mapping[str, Any],
    boot_switch: Mapping[str, Any],
    runtime_manifest: Mapping[str, Any],
    ordered_classpath: tuple[Mapping[str, Any], ...],
    database: NativeDatabaseObservation,
    engine: NativeEngineObservation,
    tls: NativeTlsObservation,
    phase_a_descriptor: Mapping[str, Any],
    phase_b_descriptor: Mapping[str, Any],
    pom_path: Path,
    identity_binding: IdentitySourceBinding,
    protected_binding: ProtectedStoreBinding,
    deadline: datetime,
) -> CanonicalPreimages:
    """Bind actual producer observations into the four pre-signature records.

    ROOT provides actual inspected image/descriptor/resource/source pins and
    protected configuration files. Their existence/cross-binding is checked,
    not inferred from counts. Future qualification/registry/signatures remain
    external steps. Native config refs must already be acyclic preimages.
    """
    guard = _ObservationWaitGuard.start(deadline)
    now = guard.start_wall
    measured = _plain(measurements)
    boot = _plain(boot_switch)
    runtime = _plain(runtime_manifest)
    if (
        type(database) is not NativeDatabaseObservation
        or type(engine) is not NativeEngineObservation
        or type(tls) is not NativeTlsObservation
    ):
        raise NativeObservationError("Concrete observations required")
    if engine._measurement_provenance is not None and engine._operation_proof is None:
        raise NativeObservationError("Immutable measurement proof cannot choose a legacy observation")
    # The frozen raw proof identifies a protected-origin observation. A mutable
    # public schema/status tag may never choose the legacy admission path.
    root_actual = None
    if engine._operation_proof is not None:
        route = engine.route_probe
        if (
            type(route) is not dict
            or route.get("schema") != "provider-native-engine-operation-observation.v4"
        ):
            raise NativeObservationError("Protected operation observation mutated after capture")
        if route.get("root_crosslink_status") != "ROOT_READBACK_VALUES_MATCHED":
            raise NativeObservationError("Measurements retained pending ROOT EACH witness readback")
        _validate_operation_proof(engine, deadline=deadline)
        try:
            refs = {
                key: NativeObservationFile(Path(route[key]["path"]), route[key]["sha256"])
                for key in ("admission", "contract_schema", "source_xml", "profile", "root_crosslink")
            }
            snapshot = route["snapshot"]
            inputs = NativeObservationInputs(
                refs["admission"],
                refs["contract_schema"],
                refs["source_xml"],
                refs["profile"],
                snapshot["policy_digest"],
                snapshot["capability_digest"],
                installed_support_jar=engine._operation_proof.inputs.installed_support_jar,
            )
            if engine._operation_proof.inputs.measurement_configuration is not None:
                inputs = NativeObservationInputs(
                    refs["admission"],
                    refs["contract_schema"],
                    refs["source_xml"],
                    refs["profile"],
                    snapshot["policy_digest"],
                    snapshot["capability_digest"],
                    engine._operation_proof.inputs.measurement_configuration,
                    engine._operation_proof.inputs.installed_support_jar,
                )
            root_actual = NativeObservationRootCrosslink(refs["root_crosslink"]).validate(
                engine, inputs=inputs, deadline=deadline
            )
            if (
                engine._operation_proof.inputs.measurement_configuration is not None
                or engine._measurement_provenance is not None
            ):
                from tests.support.provider_auth_native_measurement import NativeMeasurementProvenance

                provenance = engine._measurement_provenance
                if type(provenance) is not NativeMeasurementProvenance:
                    raise NativeObservationError("Measurements retained pending immutable ROOT provenance")
                # Frozen original inputs, not rebuilt mutable public route metadata.
                provenance.validate(
                    engine,
                    inputs=engine._operation_proof.inputs,
                    root_crosslink=NativeObservationRootCrosslink(refs["root_crosslink"]),
                    deadline=deadline,
                )
                advertised = route.get("measurement_provenance")
                if advertised != dict(path=str(provenance.artifact.path), sha256=provenance.artifact.sha256):
                    raise NativeObservationError("Immutable measurement provenance mutated after capture")
            _join_physical_engine_database(database, root_actual["stable_database_projection"])
            if (
                measured.get("candidate_sha") != root_actual["candidate_sha"]
                or measured.get("image_id") != root_actual["image_id"]
                or measured.get("descriptor_sha256") != snapshot["mounted_pins"]["descriptor_sha256"]
                or measured.get("native_jar", {}).get("sha256")
                != snapshot["mounted_pins"]["native_jar_sha256"]
                or measured.get("support_jar", {}).get("sha256")
                != snapshot["mounted_pins"]["support_jar_sha256"]
                or tls.server_peer_spki_sha256 != root_actual["server_peer_spki_sha256"]
                or tls.probe.get("certificate_sha256") != root_actual["server_certificate_sha256"]
            ):
                raise NativeObservationError("Measurements disagree with ROOT physical native readback")
        except (KeyError, TypeError, ValueError):
            raise NativeObservationError("Corroborated native observation inputs absent") from None
    elif engine.route_probe.get("schema") != "provider-native-engine-rest-observation.v1":
        raise NativeObservationError("Protected operation detached raw proof missing")
    until = clock(deadline)
    for value in (measured, boot, runtime):
        if value.get("valid_until") != until:
            raise NativeObservationError("Shared original ceiling differs")
    if (
        measured.get("scope") != boot.get("scope")
        or measured.get("scope") != runtime.get("scope")
        or measured.get("candidate_sha") != boot.get("candidate_sha")
        or measured.get("candidate_sha") != runtime.get("candidate_sha")
        or measured.get("binding") != database.binding
        or measured.get("definition") != wire(engine.definition)
        or boot.get("definition") != wire(engine.definition)
        or runtime.get("origin") != tls.origin
        or engine.origin != tls.origin
        or measured.get("owned_resources") != runtime.get("owned_resources")
        or boot.get("database_binding_sha256") != digest(database.binding)
        or boot.get("phase_a_image_id") != measured.get("image_id")
        or boot.get("phase_a_descriptor_sha256") != measured.get("descriptor_sha256")
        or boot.get("native_jar_sha256") != measured.get("native_jar", {}).get("sha256")
        or boot.get("support_jar_sha256") != measured.get("support_jar", {}).get("sha256")
    ):
        raise NativeObservationError("Observed scope/definition/configuration binding differs")
    scope = parse_model(Scope, measured["scope"])
    if not scope.environment.startswith("TestOnly-"):
        raise NativeObservationError("TestOnly scope required")
    issued = datetime.fromisoformat(runtime["issued_at"].replace("Z", "+00:00"))
    if (
        issued.tzinfo != UTC
        or issued > now + timedelta(seconds=5)
        or deadline <= issued
        or deadline - issued > timedelta(minutes=15)
    ):
        raise NativeObservationError("Native preflight ceiling exceeds fifteen minutes")
    if (
        type(identity_binding) is not IdentitySourceBinding
        or type(protected_binding) is not ProtectedStoreBinding
        or scope not in identity_binding.scopes
        or scope.tenant != identity_binding.tenant
        or identity_binding.database_name != database.binding["database_name"]
        or str(identity_binding.database_oid) != database.binding["database_oid"]
        or protected_binding.database_name != database.binding["database_name"]
        or str(protected_binding.database_oid) != database.binding["database_oid"]
        or deadline > min(identity_binding.valid_until, protected_binding.valid_until)
        or runtime["java_settings"]["scope"] != wire(scope)
        or runtime["java_settings"]["signing_spki_sha256"] != tls.server_peer_spki_sha256
    ):
        raise NativeObservationError("Actual identity/protected scope or original ceiling differs")
    for key in (
        "signing_pkcs12",
        "signing_password",
        "owner_jdbc_config",
        "native_database_binding",
        "lifecycle_configuration",
    ):
        _protected_material(runtime[key])
        current(deadline)
    if root_actual is not None:
        try:
            native = parse_model(
                NativeDatabaseBinding, strict_loads(_artifact_bytes(runtime["native_database_binding"]))
            )
            configuration_raw = _artifact_bytes(runtime["lifecycle_configuration"])
            # The persisted lifecycle DTO uses its existing numeric/ISO JSON
            # format, distinct from the number-free AUTH Binding wire format.
            _observation_json(configuration_raw)
            configuration = AuthLifecycleConfiguration.model_validate_json(configuration_raw, strict=True)
            if (
                native.scope != scope
                or configuration.native != native
                or configuration.identity != identity_binding
                or configuration.protected != protected_binding
                or native.database_name != database.binding["database_name"]
                or str(native.database_oid) != database.binding["database_oid"]
                or native.schema_name != database.binding["schema_name"]
                or str(native.schema_oid) != database.binding["schema_oid"]
                or native.owner_role != database.binding["owner_role"]
                or deadline > min(native.valid_until, configuration.native.valid_until)
                or snapshot["identity"]["tenant"] != scope.tenant
                or snapshot["identity"]["environment"] != scope.environment
                or snapshot["engine_name"] != scope.engine_name
            ):
                raise NativeObservationError("Actual source/native/protected configuration join differs")
        except (KeyError, TypeError, ValueError):
            raise NativeObservationError("Actual source/native/protected configuration unavailable") from None
        guard.check()
    flags_a = observe_engine_descriptor(phase_a_descriptor, scope=scope, installed=False, deadline=deadline)
    observe_engine_descriptor(phase_b_descriptor, scope=scope, installed=True, deadline=deadline)
    if (
        phase_a_descriptor["sha256"] != boot["phase_a_descriptor_sha256"]
        or phase_b_descriptor["sha256"] != boot["phase_b_descriptor_sha256"]
        or any(measured.get(k) != v for k, v in flags_a.items())
    ):
        raise NativeObservationError("Actual engine descriptor settings differ")
    pom_ref = pin_file(
        pom_path, ref="provider-native-measured-pom", media_type="application/xml", deadline=deadline
    )
    if pom_ref["sha256"] != measured["pom"]["git_blob_sha256"]:
        raise NativeObservationError("Actual Maven source pin differs")
    pom_raw = _artifact_bytes(pom_ref)
    if b"<!DOCTYPE" in pom_raw.upper() or b"<!ENTITY" in pom_raw.upper():
        raise NativeObservationError("Maven descriptor DTD refused")
    pom_xml = ElementTree.fromstring(pom_raw)
    compiler = [
        p
        for p in pom_xml.iter()
        if p.tag.rsplit("}", 1)[-1] == "plugin"
        and any(c.tag.rsplit("}", 1)[-1] == "artifactId" and c.text == "maven-compiler-plugin" for c in p)
    ]
    if len(compiler) != 1:
        raise NativeObservationError("Actual Maven compiler plugin absent or ambiguous")
    versions = [c.text for c in compiler[0] if c.tag.rsplit("}", 1)[-1] == "version"]
    if versions != [measured["maven_compiler_plugin"]]:
        raise NativeObservationError("Actual Maven compiler version differs")
    for ref, actual in (
        (measured["source_xml"], engine.source_xml),
        (measured["retrieved_xml"], engine.retrieved_xml),
        (measured["profile_artifact"], engine.profile),
    ):
        if _artifact_bytes(ref) != actual:
            raise NativeObservationError("Measured definition artifact drifted")
        current(deadline)
    for start in (database.before, database.after, engine.before, engine.after):
        if start > now or abs((now - start).total_seconds()) > 5:
            raise NativeObservationError("Measurement bracket stale or future")
    for key in ("before", "after"):
        observed_tls = datetime.fromisoformat(tls.probe[key].replace("Z", "+00:00"))
        if observed_tls.tzinfo != UTC or observed_tls > now or abs((now - observed_tls).total_seconds()) > 5:
            raise NativeObservationError("Actual TLS bracket stale or future")
    evidence = strict_loads(_artifact_bytes(measured["execution_record"]))
    if evidence != observation_evidence(database=database, engine=engine, tls=tls):
        raise NativeObservationError("Actual observation evidence absent or substituted")
    if (
        abs((database.database_clock - now).total_seconds()) > 5
        or abs((engine.engine_clock - now).total_seconds()) > 5
    ):
        raise NativeObservationError("Actual engine/database clock skew refused")
    measured.update(
        role_observations=list(database.role_observations),
        database_catalog_sha256=database.catalog_sha256,
        clock_database=clock(database.database_clock),
        clock_engine=clock(engine.engine_clock),
        observed_at=clock(now),
        cibseven_version=engine.version,
        client_ca_sha256=tls.client_ca_sha256,
        server_peer_spki_sha256=tls.server_peer_spki_sha256,
        tls_probe_sha256=digest(tls.probe),
        route_probe_sha256=digest(engine.route_probe),
    )
    boot["observed_at"] = clock(now)
    classpath = dict(
        schema="provider-auth-testonly-owner-classpath-manifest.v1",
        candidate_sha=measured["candidate_sha"],
        ordered_artifacts=list(ordered_classpath),
    )
    validate_supplement("OwnerClasspathManifest", classpath)
    verify_owner_classpath(ordered_classpath, classpath)
    if list(ordered_classpath[:2]) != [measured["native_jar"], measured["support_jar"]]:
        raise NativeObservationError("Actual native/support classpath order differs")
    if not {measured["native_jar"]["sha256"], measured["support_jar"]["sha256"]} <= {
        r["sha256"] for r in ordered_classpath
    }:
        raise NativeObservationError("Measured JAR absent from actual ordered classpath")
    measured["boot_switch"] = ArtifactLink("boot-switch.json")
    preimages = prepare_canonical_dag(
        directory,
        {
            "boot-switch.json": ("BootSwitch", boot),
            "measurements.json": ("Measurements", measured),
            "owner-classpath.json": ("OwnerClasspathManifest", classpath),
            "owner-runtime.json": ("OwnerRuntimeManifest", runtime),
        },
        deadline=deadline,
    )
    guard.check()  # No verified preimages escape after final file/schema work.
    return preimages


def observation_evidence(
    *, database: NativeDatabaseObservation, engine: NativeEngineObservation, tls: NativeTlsObservation
) -> dict[str, Any]:
    record = _plain(
        dict(
            schema="provider-native-phase-a-observation-evidence.v1",
            database=database.evidence(),
            engine=engine.route_probe,
            tls=tls.probe,
        )
    )
    if type(record) is not dict:
        raise NativeObservationError("Observation evidence is not a record")
    return record


def write_observation_evidence(
    path: Path,
    *,
    database: NativeDatabaseObservation,
    engine: NativeEngineObservation,
    tls: NativeTlsObservation,
    deadline: datetime,
) -> dict[str, Any]:
    """Persist real measurement sources, including clock resolution and ACL rows."""
    guard = _ObservationWaitGuard.start(deadline)
    _directory(path.parent)
    raw = canonicalize(observation_evidence(database=database, engine=engine, tls=tls))
    current(deadline)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        guard.check()
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    current(deadline)
    result = pin_file(
        path,
        ref="provider-native-phase-a-observation-evidence",
        media_type="application/json",
        deadline=deadline,
    )
    guard.check()
    return result


def observe_engine_descriptor(
    artifact: Mapping[str, Any], *, scope: Scope, installed: bool, deadline: datetime
) -> dict[str, Any]:
    """Measure explicit descriptor settings; no assumed engine-default flag."""
    current(deadline)
    raw = _artifact_bytes(artifact)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise NativeObservationError("Engine descriptor DTD refused")
    try:
        root = ElementTree.fromstring(raw)
        engines = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "process-engine"]
        if len(engines) != 1 or engines[0].get("name") != scope.engine_name:
            raise NativeObservationError("Actual engine descriptor scope differs")
        flags = {}
        for name, value in (
            ("authorizationEnabled", "true"),
            ("tenantCheckEnabled", "true"),
            ("databaseSchemaUpdate", "false"),
        ):
            matches = [
                p.text
                for p in engines[0].iter()
                if p.tag.rsplit("}", 1)[-1] == "property" and p.get("name") == name
            ]
            if matches != [value]:
                raise NativeObservationError("Explicit actual engine setting missing or ambiguous")
            flags[name] = value
        plugins = [p.text for p in engines[0].iter() if p.tag.rsplit("}", 1)[-1] == "class"]
        human = "br.com.maezo.human.HumanCommandPlugin"
        composition = "br.com.maezo.human.ProviderAuthTestComposition"
        if (
            plugins.count(human) != 1
            or plugins.count(composition) != int(installed)
            or any(
                p in plugins
                for p in (
                    "br.com.maezo.human.StaffDeploymentComposition",
                    "br.com.maezo.human.PortalReadPlugin",
                )
            )
            or installed
            and plugins.index(composition) >= plugins.index(human)
        ):
            raise NativeObservationError("Actual native plugin composition differs")
    except ElementTree.ParseError:
        raise NativeObservationError("Actual engine descriptor XML refused") from None
    current(deadline)
    return dict(
        authorization_enabled=True, tenant_check_enabled=True, schema_update=flags["databaseSchemaUpdate"]
    )
