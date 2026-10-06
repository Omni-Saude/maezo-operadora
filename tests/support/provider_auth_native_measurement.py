"""ROOT-only acquisition for the admitted 0467 TestOnly measurement protocol.

This support library creates no principals, engine, database, authority or receipt.
ROOT supplies its separately qualified Linux lane and private material captures.
A completed primary is an as-of observation, never a remote cleanup guarantee.
"""

from __future__ import annotations

import asyncio
import grp
import hashlib
import io
import ipaddress
import json
import os
import pwd
import re
import select
import socket
import stat
import struct
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from jsonschema import Draft202012Validator, FormatChecker

from tests.support import provider_auth_native_runtime as runtime

PROTOCOL_SCHEMA_SHA256 = "d55733ba7703f39c7c71f13da0eb20f14825cbff03c1106894aae610423cebfe"
MAX_BYTES = 2097152
MAX_FRAME = 32768
SESSION_KEYS = (
    "backend_pid",
    "backend_start",
    "transaction_started_at",
    "transaction_id_if_assigned",
    "backend_ssl",
    "backend_tls_version",
)

# Independent observer connection, never the producer's connection or query map.
_VISIBILITY_SQL = """SELECT session_user::text AS observer_session_user,
 current_setting('server_version')::text AS server_version,
 current_setting('track_activities')::text AS track_activities,
 (r.rolsuper OR pg_has_role(session_user,'pg_read_all_stats','MEMBER')) AS visible
 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user"""
_CHECKPOINT_SQL = """SELECT clock_timestamp() AS observed_at,
 current_database()::text AS database_name,d.oid::text AS database_oid,
 (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier,
 a.pid::text AS backend_pid,a.backend_start,a.xact_start AS transaction_started_at,
 a.backend_xid::text AS backend_xid32,a.usename::text AS session_login,
 a.client_addr::text AS client_address,a.client_port,s.ssl AS backend_ssl,
 s.version::text AS backend_tls_version,
 ARRAY(SELECT x::text FROM pg_catalog.pg_snapshot_xip(pg_catalog.pg_current_snapshot()) x)
 AS active_full_xids FROM pg_catalog.pg_stat_activity a
 JOIN pg_catalog.pg_stat_ssl s ON s.pid=a.pid
 CROSS JOIN pg_catalog.pg_database d WHERE a.pid=$1 AND d.datname=current_database()"""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    runtime._check_observation_value(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def decode(raw: bytes, *, limit: int) -> dict[str, Any]:
    value = runtime._observation_json(raw, limit=limit)
    if type(value) is not dict or canonical(value) != raw:
        raise runtime.NativeObservationError("Measurement canonical closed object required")
    return value


def validate(schema: dict[str, Any], name: str, value: Any) -> None:
    checker = Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker()
    )
    if not checker.is_valid(value):
        raise runtime.NativeObservationError(f"Measurement {name} shape refused")

    # V4 embedded timestamps use a pattern rather than format; validate real dates too.
    def dates(v: Any) -> None:
        if type(v) is dict:
            for key, item in v.items():
                if type(item) is str and (
                    key.endswith("_at")
                    or key in {"before", "after", "original_deadline", "issued_at", "backend_start"}
                ):
                    runtime._observation_time(item)
                dates(item)
        elif type(v) is list:
            for item in v:
                dates(item)

    dates(value)


@dataclass(frozen=True, repr=False)
class NativeMeasurementConfiguration:
    artifact: runtime.NativeObservationFile
    schema: runtime.NativeObservationFile

    def load(self, deadline: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        guard = runtime._ObservationWaitGuard.start(deadline)
        if (
            type(self.artifact) is not runtime.NativeObservationFile
            or type(self.schema) is not runtime.NativeObservationFile
        ):
            raise runtime.NativeObservationError("Pinned measurement config/schema required")
        if self.schema.sha256 != PROTOCOL_SCHEMA_SHA256:
            raise runtime.NativeObservationError("Exact admitted measurement schema required")
        schema = runtime._observation_json(
            self.schema.read(deadline=deadline, limit=262144, private=False), limit=262144
        )
        value = decode(self.artifact.read(deadline=deadline, limit=65536), limit=65536)
        validate(schema, "Config", value)
        nonce = value["session_nonce"]
        directory = Path(value["root_evidence_directory"])
        issued = runtime._observation_time(value["issued_at"])
        if (
            value["protocol_schema_sha256"] != self.schema.sha256
            or value["original_deadline"] != runtime.clock(deadline)
            or not issued < deadline <= issued + timedelta(minutes=15)
            or issued > guard.start_wall + timedelta(seconds=5)
            or value["root_peer_uid"] == value["jvm_uid"]
            or value["socket_path"] != f"/run/maezo-native-qualify/{nonce}/root.sock"
            or value["outcome_socket_path"] != f"/run/maezo-native-qualify/{nonce}/outcome.sock"
            or self.artifact.path.name != "provider-native-independent-measurement-config.json"
            or directory.name != f"measurement-{nonce}"
            or not directory.is_absolute()
            or directory.resolve() != directory
        ):
            raise runtime.NativeObservationError("Measurement config relations refused")
        guard.check()
        return schema, value


def installed_measurement_origin(
    support: runtime.NativeObservationFile | None, *, admission: dict[str, Any], deadline: datetime
) -> bool:
    """Read actual admitted support JAR; caller None cannot designate normality.

    The support raw pin is already in immutable V4 admission. Its independently
    read class directory determines instrumentation presence before any body is
    constructed. A historical two-owner JAR may prove absence; missing material
    or ambiguous class aliases cannot. This proves material scope, not that an
    engine is installed or a class is live. Those physical gates remain separate.
    """
    guard = runtime._ObservationWaitGuard.start(deadline)
    if (
        type(support) is not runtime.NativeObservationFile
        or support.path.name != "provider-auth-test-support.jar"
    ):
        raise runtime.NativeObservationError("Actual admitted installed support JAR origin required")
    if support.sha256 != admission["support_jar_sha256"]:
        raise runtime.NativeObservationError("Actual installed support JAR differs from admission")
    raw = support.read(deadline=deadline, limit=33554432, private=False)
    expected = "br/com/maezo/workload/ProviderNativeIndependentMeasurement.class"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as jar:
            names = jar.namelist()
            if not 1 <= len(names) <= 20000 or len(names) != len(set(names)):
                raise runtime.NativeObservationError("Actual support JAR closed census refused")
            for name in names:
                if (
                    name.startswith("/")
                    or ".." in name.split("/")
                    or "//" in name
                    or "/./" in name
                    or "\\" in name
                    or (name.endswith("ProviderNativeIndependentMeasurement.class") and name != expected)
                ):
                    raise runtime.NativeObservationError("Actual support measurement class alias refused")
            present = expected in names
    except (zipfile.BadZipFile, UnicodeError, ValueError):
        raise runtime.NativeObservationError("Actual installed support JAR unavailable") from None
    guard.check()
    return present


def full_xid(xid32: Any, active: Any) -> str | None:
    if type(active) is not list or len(active) > 4096 or len(set(active)) != len(active):
        raise runtime.NativeObservationError("Actual full-XID snapshot refused")
    for value in active:
        if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None or int(value) >= 2**64:
            raise runtime.NativeObservationError("Actual snapshot XID refused")
    if xid32 is None:
        return None
    if type(xid32) is not str or re.fullmatch(r"[1-9][0-9]{0,9}", xid32) is None or int(xid32) >= 2**32:
        raise runtime.NativeObservationError("Actual backend xid32 refused")
    matches: list[str] = [value for value in active if int(value) % 2**32 == int(xid32)]
    if len(matches) != 1:
        raise runtime.NativeObservationError("Actual full-XID mapping absent/ambiguous")
    return matches[0]


def session(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: capture[k] for k in SESSION_KEYS if k != "transaction_id_if_assigned"},
        "transaction_id_if_assigned": full_xid(capture["backend_xid32"], capture["active_full_xids"]),
    }


async def acquire_pg_checkpoint(
    connection: asyncpg.Connection,
    *,
    hold: dict[str, Any],
    hold_raw: bytes,
    phase: str,
    guard: runtime._ObservationWaitGuard,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """One fresh read-only transaction; caller invokes exactly twice per HOLD."""
    guard.check()
    if (
        not isinstance(connection, asyncpg.Connection)
        or connection.is_in_transaction()
        or phase not in {"ROOT_FIRST", "ROOT_SECOND"}
    ):
        raise runtime.NativeObservationError("Actual clean independent PostgreSQL connection required")
    try:
        async with connection.transaction(isolation="read_committed", readonly=True):
            guard.check()
            await connection.execute("SELECT pg_catalog.pg_stat_clear_snapshot()", timeout=remaining(guard))
            guard.check()
            visibility = await connection.fetchrow(_VISIBILITY_SQL, timeout=remaining(guard))
            guard.check()
            if (
                visibility is None
                or visibility["visible"] is not True
                or visibility["track_activities"] != "on"
            ):
                raise runtime.NativeObservationError("Actual PostgreSQL stats visibility unavailable")
            witness = hold["measurement"]["session_transaction"]
            row = await connection.fetchrow(
                _CHECKPOINT_SQL, int(witness["backend_pid"]), timeout=remaining(guard)
            )
            guard.check()
            if row is None:
                raise runtime.NativeObservationError("Held PostgreSQL backend absent")
            capture = dict(
                schema="provider-native-root-pg-checkpoint-capture.v1",
                source="root-independent-pg-stat-snapshot-and-ssl-query",
                root_observed_at=runtime.clock(row["observed_at"]),
                collector_uid=os.geteuid(),
                collector_pid_namespace=os.getpid(),
                observer_session_user=visibility["observer_session_user"],
                server_version=visibility["server_version"],
                stats_visibility_qualified=visibility["visible"],
                track_activities_observed_on=visibility["track_activities"] == "on",
                stage_sequence=hold["stage_sequence"],
                server_stage_execution_uuid=hold["server_stage_execution_uuid"],
                endpoint=hold["endpoint"],
                phase=phase,
                hold_raw_sha256=sha(hold_raw),
                query_schema="fixed-root-pg-checkpoint-query.v1",
                query_source_sha256=source_sha(guard=guard),
            )
            for key in (
                "database_name",
                "database_oid",
                "system_identifier",
                "backend_pid",
                "backend_xid32",
                "session_login",
                "client_address",
                "client_port",
                "backend_ssl",
                "backend_tls_version",
            ):
                capture[key] = row[key]
            capture["backend_start"] = runtime.clock(row["backend_start"])
            capture["transaction_started_at"] = runtime.clock(row["transaction_started_at"])
            capture["active_full_xids"] = list(row["active_full_xids"])
            validate(schema, "RootPgRawCapture", capture)
            if session(capture) != {k: witness[k] for k in SESSION_KEYS}:
                raise runtime.NativeObservationError("Root PG tuple differs from held enlisted session")
            physical = hold["measurement"]["stable_database_projection"]
            if (
                any(capture[k] != physical[k] for k in ("database_name", "database_oid", "system_identifier"))
                or capture["session_login"] != physical["session_user"]
            ):
                raise runtime.NativeObservationError("Root PG database/login differs")
            # stat.usename is login only. current_user remains separately sourced.
        guard.check()  # Includes transaction exit; no refreshed deadline.
        return capture
    except (asyncpg.PostgresError, KeyError, TypeError, ValueError):
        raise runtime.NativeObservationError("Actual PostgreSQL checkpoint unavailable") from None


def source_sha(*, guard: runtime._ObservationWaitGuard | None = None) -> str:
    # Exact admitted file only, never source discovery or foreign checkout import.
    if guard is not None:
        guard.check()
    path = Path(__file__)
    if path.resolve() != path:
        raise runtime.NativeObservationError("Actual collector source origin substituted")
    before = path.stat(follow_symlinks=False)
    raw = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or len(raw) != before.st_size
        or any(
            getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_uid", "st_mode")
        )
    ):
        raise runtime.NativeObservationError("Actual collector source changed")
    if guard is not None:
        guard.check()  # Includes actual source FD close.
    return sha(raw)


def remaining(guard: runtime._ObservationWaitGuard) -> float:
    guard.check()
    return min(
        5.0,
        (guard.deadline - datetime.now(UTC)).total_seconds(),
        (guard.deadline - guard.start_wall).total_seconds() - (time.monotonic_ns() - guard.start_ns) / 1e9,
    )


def _basename(name: str) -> None:
    fixed = {
        "root-provenance.json",
        "root-crosslink.json",
        "root-terminal-readback.json",
        "root-process-socket.json",
        "root-installation.json",
        "root-trusted-candidate-integrity.json",
        "root-host-unit-toolchain.json",
        "root-class-resource-census.json",
        "root-source-build-link.json",
    }
    event = re.fullmatch(
        r"event-(0[1-9]|[1-5][0-9]|6[0-5])-(HELLO|HOLD|RELEASE|SEAL|STAGE_CLOSED|TERMINAL)[.]json", name
    )
    pg = re.fullmatch(r"pg-s0[0-8]-(ENTRY|COMMITTING)-(ROOT_FIRST|ROOT_SECOND)[.]json", name)
    if name not in fixed and event is None and pg is None:
        raise runtime.NativeObservationError("Closed ROOT artifact basename refused before I/O")


def artifact_ref(
    directory: Path, basename: str, raw: bytes, guard: runtime._ObservationWaitGuard
) -> dict[str, Any]:
    guard.check()
    _basename(basename)
    if (
        directory.resolve() != directory
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
        or directory.stat().st_uid != os.geteuid()
    ):
        raise runtime.NativeObservationError("Actual ROOT evidence directory custody refused")
    path = directory / basename
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        guard.check()
        offset = 0
        while offset < len(raw):
            guard.check()
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise runtime.NativeObservationError("Incomplete ROOT evidence write")
            offset += count
            guard.check()
        os.fsync(fd)
        guard.check()
    finally:
        os.close(fd)
    guard.check()
    ref = dict(path=str(path), sha256=sha(raw), size_bytes=len(raw))
    read_ref(ref, directory, basename, guard, len(raw))
    guard.check()
    return ref


def read_ref(
    ref: Any, directory: Path, basename: str, guard: runtime._ObservationWaitGuard, limit: int
) -> bytes:
    guard.check()
    _basename(basename)
    if (
        type(ref) is not dict
        or set(ref) != {"path", "sha256", "size_bytes"}
        or ref["path"] != str(directory / basename)
        or type(ref["size_bytes"]) is not int
        or not 0 < ref["size_bytes"] <= limit
    ):
        raise runtime.NativeObservationError("Closed ROOT artifact path/size refused before read")
    path = directory / basename
    if path.stat(follow_symlinks=False).st_uid != os.geteuid() or directory.stat().st_uid != os.geteuid():
        raise runtime.NativeObservationError("ROOT artifact owner refused")
    raw = runtime.NativeObservationFile(path, ref["sha256"]).read(deadline=guard.deadline, limit=limit)
    if len(raw) != ref["size_bytes"]:
        raise runtime.NativeObservationError("ROOT artifact actual size differs")
    guard.check()
    return raw


def _proc_bytes(pid: int, name: str, guard: runtime._ObservationWaitGuard) -> bytes:
    guard.check()
    fd = os.open(f"/proc/{pid}/{name}", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        guard.check()
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise runtime.NativeObservationError("Proc read bound refused")
        guard.check()
    finally:
        os.close(fd)
    guard.check()
    return raw


def _process_identity(pid: int, guard: runtime._ObservationWaitGuard) -> tuple[Any, ...]:
    raw = _proc_bytes(pid, "stat", guard).decode("ascii")
    # comm may contain spaces or parentheses; fields after its last ')' are fixed.
    fields = raw[raw.rfind(")") + 2 :].split()
    start_ticks = int(fields[19])
    status = _proc_bytes(pid, "status", guard).decode("ascii")
    ids = {}
    for line in status.splitlines():
        if line.startswith(("NSpid:", "Uid:", "Gid:", "Groups:")):
            key, value = line.split(":", 1)
            ids[key] = tuple(int(v) for v in value.split())
    namespaces = tuple(os.readlink(f"/proc/{pid}/ns/{name}") for name in ("pid", "mnt", "net"))
    guard.check()
    if not ids.get("NSpid") or ids["NSpid"][0] != pid:
        raise runtime.NativeObservationError("Actual Linux host PID namespace required")
    if (
        len(ids.get("Uid", ())) != 4
        or len(ids.get("Gid", ())) != 4
        or "Groups" not in ids
        or len(set(ids["Uid"])) != 1
        or len(set(ids["Gid"])) != 1
        or any(value < 0 for values in ids.values() for value in values)
    ):
        raise runtime.NativeObservationError("Process credentials change privilege domains")
    return (
        pid,
        ids["NSpid"][-1],
        ids["Uid"][0],
        ids["Gid"][0],
        start_ticks,
        *namespaces,
        tuple(sorted(set((ids["Gid"][0], *ids["Groups"])))),
    )


def _xattrs(path: Path) -> list[str]:
    getter = getattr(os, "listxattr", None)
    if getter is None:
        raise runtime.NativeObservationError("Actual Linux ACL readback unavailable")
    values = getter(path, follow_symlinks=False)
    if type(values) is not list or any(type(v) is not str for v in values):
        raise runtime.NativeObservationError("Actual ACL readback refused")
    return values


def _socket_custody(
    path: Path,
    config: dict[str, Any],
    guard: runtime._ObservationWaitGuard,
    *,
    exists: bool,
    jvm_identity: tuple[Any, ...] | None = None,
) -> tuple[int, int] | None:
    guard.check()
    if path.resolve() != path or path.parent.resolve() != path.parent:
        raise runtime.NativeObservationError("Actual UDS path custody refused")
    parent = path.parent.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o750
        or parent.st_uid != config["root_peer_uid"]
        or parent.st_gid != config["root_peer_gid"]
        or os.geteuid() != config["root_peer_uid"]
        or os.getegid() != config["root_peer_gid"]
    ):
        raise runtime.NativeObservationError("Actual UDS parent/principals refused")
    group = grp.getgrgid(parent.st_gid)
    members = {pwd.getpwnam(name).pw_uid for name in group.gr_mem}
    members.update(user.pw_uid for user in pwd.getpwall() if user.pw_gid == parent.st_gid)
    if (
        group.gr_name != config["root_peer_group"]
        or pwd.getpwuid(parent.st_uid).pw_name != config["root_peer_user"]
        or members != {config["root_peer_uid"], config["jvm_uid"]}
    ):
        raise runtime.NativeObservationError("Actual exclusive IPC group membership refused")
    # Before SO_PEERCRED is available, group-write is refused for every group.
    # Once the kernel peer is joined, its actual primary/supplementary group set
    # determines write access; configured/account-directory membership is not
    # promoted to a running process credential observation.
    jvm_groups = None
    if jvm_identity is not None:
        if (
            len(jvm_identity) != 9
            or jvm_identity[2:4] != (config["jvm_uid"], config["jvm_gid"])
            or type(jvm_identity[8]) is not tuple
            or not jvm_identity[8]
            or any(type(gid) is not int or gid < 0 for gid in jvm_identity[8])
        ):
            raise runtime.NativeObservationError("Actual JVM ancestor credentials unavailable")
        jvm_groups = jvm_identity[8]
    for parent_path in (path.parent, *path.parent.parents):
        st = parent_path.stat(follow_symlinks=False)
        if (
            parent_path.resolve() != parent_path
            or not stat.S_ISDIR(st.st_mode)
            or st.st_mode & 0o002
            or (st.st_uid == config["jvm_uid"] and st.st_mode & 0o200)
            or (st.st_mode & 0o020 and (jvm_groups is None or st.st_gid in jvm_groups))
        ):
            raise runtime.NativeObservationError("UDS ancestor writable or substituted")
        if "system.posix_acl_access" in _xattrs(parent_path):
            raise runtime.NativeObservationError("Unqualified UDS ACL refused")
    result = None
    if exists:
        st = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISSOCK(st.st_mode)
            or stat.S_IMODE(st.st_mode) != 0o660
            or st.st_uid != parent.st_uid
            or st.st_gid != parent.st_gid
            or "system.posix_acl_access" in _xattrs(path)
        ):
            raise runtime.NativeObservationError("Actual UDS inode/custody refused")
        result = (st.st_dev, st.st_ino)
    elif path.exists():
        raise runtime.NativeObservationError("Irreversibly held nonce/socket already exists")
    guard.check()
    return result


class NativeMeasurementCollector:
    """Two actual ROOT listeners. No retries, ACK, third connect or nonce refund.

    Constructor is inert. collect() binds only the admitted pre-provisioned paths.
    Socket pathnames are retained on all exits for explicit ROOT inventory/custody.
    ROOT supplies a real independent PG connection and previously acquired raw
    installation capture; image/container provenance cannot come from config.
    """

    def __init__(self, configuration: NativeMeasurementConfiguration, *, deadline: datetime):
        if type(configuration) is not NativeMeasurementConfiguration:
            raise runtime.NativeObservationError("Concrete pinned measurement configuration required")
        self.configuration = configuration
        self.guard = runtime._ObservationWaitGuard.start(deadline)
        self.schema, self.config = configuration.load(deadline)
        self.directory = Path(self.config["root_evidence_directory"])
        self.total_bytes = 0
        self.events: list[dict[str, Any]] = []
        self.pg_captures: list[dict[str, Any]] = []
        self._used = False
        self._failed = False
        self._collected = False
        self._peer: tuple[Any, ...] | None = None
        self._primary: socket.socket | None = None
        self._primary_listener: socket.socket | None = None
        self._primary_accepted = False
        self._outcome_accepted = False
        self._outcome_arrival_observed = False
        self._terminal_seen = False
        self._outcome_listener: socket.socket | None = None
        self._listener_inodes: dict[str, tuple[int, int]] = {}
        self._process_capture: dict[str, Any] | None = None
        self._terminal_capture: dict[str, Any] | None = None

    def _check(self) -> None:
        if self._failed:
            raise runtime.NativeObservationError("Measurement acquisition irreversibly UNKNOWN")
        self.guard.check()
        self.configuration.load(self.guard.deadline)
        if self._peer is not None:
            if _process_identity(self._peer[0], self.guard) != self._peer:
                raise runtime.NativeObservationError("Actual JVM process start/namespace changed")
            for channel, inode in self._listener_inodes.items():
                path = Path(self.config["socket_path" if channel == "primary" else "outcome_socket_path"])
                if (
                    _socket_custody(path, self.config, self.guard, exists=True, jvm_identity=self._peer)
                    != inode
                ):
                    raise runtime.NativeObservationError("Actual socket inode changed")
        if (
            self._primary_listener is not None
            and self._primary_accepted
            and select.select([self._primary_listener], [], [], 0)[0]
        ):
            raise runtime.NativeObservationError("Extra primary UDS connection refused")
        self._check_outcome_arrival()
        self.guard.check()

    def _check_outcome_arrival(self) -> None:
        listener = self._outcome_listener
        if listener is None or not select.select([listener], [], [], 0)[0]:
            return
        if self._outcome_accepted:
            self._failed = True
            raise runtime.NativeObservationError("Extra outcome UDS connection refused")
        if self._primary is not None:
            # HUP/RDHUP is a kernel observation of remote stream closure even
            # with buffered TERMINAL bytes. Peeking those bytes cannot establish
            # EOF. The closed primary grammar still must be consumed and its
            # actual EOF/local close completed before outcome acceptance.
            poll = select.poll()
            closed_mask = select.POLLHUP | getattr(select, "POLLRDHUP", 0)
            poll.register(self._primary, select.POLLIN | closed_mask | select.POLLERR)
            events = poll.poll(0)
            closed = (
                len(events) == 1
                and events[0][1] & closed_mask
                and not events[0][1] & (select.POLLERR | select.POLLNVAL)
            )
            if closed:
                self._outcome_arrival_observed = True
                return
        elif self._terminal_seen:
            self._outcome_arrival_observed = True
            return
        self._failed = True
        raise runtime.NativeObservationError("Actual premature outcome UDS connection refused")

    def _observe_outcome_arrival(self) -> None:
        # A readiness callback records an observed early queue while other
        # owned asynchronous I/O is pending; checks also bracket every I/O.
        # It cannot reconstruct an arrival then close between observations.
        try:
            self._check_outcome_arrival()
        except runtime.NativeObservationError:
            self._failed = True
        finally:
            if self._outcome_listener is not None:
                asyncio.get_running_loop().remove_reader(self._outcome_listener.fileno())

    async def _wait(self, awaitable: Any, *, accepting: bool = False) -> Any:
        try:
            self._check()
        except BaseException:
            # All awaitables are private loop socket operations created here.
            awaitable.close()
            raise
        result = await asyncio.wait_for(awaitable, timeout=remaining(self.guard))
        try:
            self._check()
        except BaseException:
            if accepting:
                result[0].close()
            raise
        return result

    def _bind(self, field: str) -> socket.socket:
        path = Path(self.config[field])
        _socket_custody(path, self.config, self.guard, exists=False)
        result = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.guard.check()
            result.bind(str(path))
            self.guard.check()
            os.chmod(path, 0o660, follow_symlinks=False)
            self.guard.check()
            result.listen(1)
            result.setblocking(False)
            channel = "primary" if field == "socket_path" else "outcome"
            inode = _socket_custody(path, self.config, self.guard, exists=True)
            if inode is None:
                raise runtime.NativeObservationError("Actual socket inode unavailable")
            self._listener_inodes[channel] = inode
            return result
        except BaseException:
            result.close()
            raise

    def _credential(self, peer: socket.socket) -> tuple[Any, ...]:
        self.guard.check()
        if not hasattr(socket, "SO_PEERCRED") or os.geteuid() == 0:
            raise runtime.NativeObservationError("Qualified non-root Linux kernel peer credentials required")
        pid, uid, gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        identity = _process_identity(pid, self.guard)
        if (
            uid != self.config["jvm_uid"]
            or gid != self.config["jvm_gid"]
            or identity[2:4] != (uid, gid)
            or uid == os.geteuid()
        ):
            raise runtime.NativeObservationError("Actual JVM peer process/principals refused")
        self.guard.check()
        return identity

    async def _exact(self, peer: socket.socket, count: int) -> bytes:
        raw = bytearray()
        while len(raw) < count:
            part = await self._wait(asyncio.get_running_loop().sock_recv(peer, count - len(raw)))
            if not part:
                raise runtime.NativeObservationError("Measurement premature EOF")
            raw.extend(part)
        self._check()
        return bytes(raw)

    async def _read(self, peer: socket.socket, name: str) -> tuple[dict[str, Any], bytes]:
        length = struct.unpack("!I", await self._exact(peer, 4))[0]
        if not 1 <= length <= MAX_FRAME or self.total_bytes + 4 + length > MAX_BYTES:
            raise runtime.NativeObservationError("Measurement finite framing bound refused")
        raw = await self._exact(peer, length)
        self.total_bytes += 4 + length
        value = decode(raw, limit=MAX_FRAME)
        validate(self.schema, name, value)
        if name in {"Frame", "TERMINAL"} and value.get("kind") == "TERMINAL":
            self._terminal_seen = True
        self._check()
        return value, raw

    def _event(self, value: dict[str, Any], raw: bytes, direction: str) -> None:
        if len(self.events) >= 65 or value["event_ordinal"] != len(self.events) + 1:
            raise runtime.NativeObservationError("Measurement ordinal/replay/finite count refused")
        for key, expected in (
            ("session_nonce", self.config["session_nonce"]),
            ("request_sha256", self.config["request_sha256"]),
            ("config_sha256", self.configuration.artifact.sha256),
            ("original_deadline", self.config["original_deadline"]),
        ):
            if value[key] != expected:
                raise runtime.NativeObservationError("Measurement session binding differs")
        emitted = runtime._observation_time(value["emitted_at"])
        if not self.guard.start_wall <= emitted < self.guard.deadline:
            raise runtime.NativeObservationError("Measurement event outside original bracket")
        ref = artifact_ref(
            self.directory, f"event-{value['event_ordinal']:02d}-{value['kind']}.json", raw, self.guard
        )
        self.events.append(
            dict(
                event_ordinal=value["event_ordinal"],
                kind=value["kind"],
                direction=direction,
                raw_artifact=ref,
            )
        )
        self._check()

    def _join_pg_process(self, capture: dict[str, Any]) -> None:
        """Join actual pg_stat client tuple to sockets owned by kernel peer PID.

        Direct local tuple equality is required. NAT/proxy ambiguity is not
        inferred away from matching DSNs or backend IDs; that lane stays unknown.
        Only the identified process' FD links and two fixed proc TCP tables are
        read. No FD contents, launch secrets, source discovery or attach API.
        """
        if self._peer is None:
            raise runtime.NativeObservationError("Actual JVM peer missing for PG socket join")
        self._check()
        pid = self._peer[0]
        fd_names = os.listdir(f"/proc/{pid}/fd")
        if not 1 <= len(fd_names) <= 4096 or any(re.fullmatch(r"[0-9]+", name) is None for name in fd_names):
            raise runtime.NativeObservationError("Actual JVM FD census unavailable/unbounded")
        socket_inodes: set[str] = set()
        for name in fd_names:
            self.guard.check()
            target = os.readlink(f"/proc/{pid}/fd/{name}")
            match = re.fullmatch(r"socket:\[([1-9][0-9]*)\]", target)
            if match is not None:
                socket_inodes.add(match[1])
            self.guard.check()
        expected_address = ipaddress.ip_address(capture["client_address"])
        if isinstance(expected_address, ipaddress.IPv6Address) and expected_address.ipv4_mapped is not None:
            expected_address = expected_address.ipv4_mapped
        matches = []
        for table, family in (("net/tcp", socket.AF_INET), ("net/tcp6", socket.AF_INET6)):
            raw = _proc_bytes(pid, table, self.guard).decode("ascii")
            for line in raw.splitlines()[1:]:
                fields = line.split()
                if len(fields) < 10:
                    raise runtime.NativeObservationError("Actual process TCP table refused")
                if fields[3] != "01" or fields[9] not in socket_inodes:
                    continue
                address_hex, port_hex = fields[1].split(":")
                encoded = bytes.fromhex(address_hex)
                if family == socket.AF_INET:
                    encoded = encoded[::-1]
                else:
                    encoded = b"".join(encoded[i : i + 4][::-1] for i in range(0, 16, 4))
                address = ipaddress.ip_address(socket.inet_ntop(family, encoded))
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                    address = address.ipv4_mapped
                if address == expected_address and int(port_hex, 16) == capture["client_port"]:
                    matches.append(fields[9])
        if len(matches) != 1:
            raise runtime.NativeObservationError("PG client tuple lacks unique actual JVM-owned TCP socket")
        self._check()

    async def collect(
        self, connection: asyncpg.Connection, *, installation_file: runtime.NativeObservationFile
    ) -> None:
        """Capture the finite transcript. finish() still needs response+all sources."""
        if self._used:
            raise runtime.NativeObservationError("Measurement nonce irreversibly reserved")
        self._used = True
        primary_listener = outcome_listener = peer = outcome_peer = None
        try:
            if (
                type(installation_file) is not runtime.NativeObservationFile
                or installation_file.path != self.directory / "root-installation.json"
            ):
                raise runtime.NativeObservationError(
                    "Exact independently acquired ROOT installation file required"
                )
            installation = decode(
                installation_file.read(deadline=self.guard.deadline, limit=65536), limit=65536
            )
            validate(self.schema, "RootInstallationRawCapture", installation)
            if (
                installation["candidate_sha"] != self.config["candidate_sha"]
                or installation["actual_image_id"] != self.config["expected_image_id"]
            ):
                raise runtime.NativeObservationError("Independent installed candidate differs")
            primary_listener = self._bind("socket_path")
            self._primary_listener = primary_listener
            outcome_listener = self._bind("outcome_socket_path")
            self._outcome_listener = outcome_listener
            asyncio.get_running_loop().add_reader(outcome_listener, self._observe_outcome_arrival)
            peer, _ = await self._wait(
                asyncio.get_running_loop().sock_accept(primary_listener), accepting=True
            )
            self._primary_accepted = True
            self._primary = peer
            self._peer = self._credential(peer)
            hello, raw = await self._read(peer, "HELLO")
            self._event(hello, raw, "JVM_TO_ROOT")
            if hello["jvm_toolchain"]["process_pid_namespace"] != self._peer[1]:
                raise runtime.NativeObservationError("HELLO actual JVM namespace PID differs")
            origin = hello["instrument_origin"]
            if (
                origin["jar_sha256"] != installation["support_jar_raw_sha256"]
                or origin["jar_sha256"] != self.config["support_jar_sha256"]
                or origin["class_sha256"] != installation["admitted_instrument_class_raw_sha256"]
                or origin["class_sha256"] != self.config["instrument_class_sha256"]
            ):
                raise runtime.NativeObservationError("Live HELLO instrument material differs")
            self._capture_process(installation)
            stages = 0
            stage_uuids: set[str] = set()
            while stages < 9:
                first, first_raw = await self._read(peer, "Frame")
                if first["kind"] == "TERMINAL":
                    if stages == 0:
                        raise runtime.NativeObservationError("Empty native measurement refused")
                    terminal, terminal_raw = first, first_raw
                    break
                stage_uuid = first.get("server_stage_execution_uuid")
                if stage_uuid in stage_uuids or type(stage_uuid) is not str:
                    raise runtime.NativeObservationError("Duplicate stage execution UUID")
                stage_uuids.add(stage_uuid)
                for endpoint in ("ENTRY", "COMMITTING"):
                    if endpoint == "ENTRY":
                        hold, hold_raw = first, first_raw
                    else:
                        hold, hold_raw = await self._read(peer, "HOLD")
                    validate(self.schema, "HOLD", hold)
                    if (
                        hold["stage_sequence"] != stages
                        or hold["endpoint"] != endpoint
                        or hold["server_stage_execution_uuid"] != stage_uuid
                    ):
                        raise runtime.NativeObservationError("Contiguous stage/endpoint order refused")
                    self._event(hold, hold_raw, "JVM_TO_ROOT")
                    captures = []
                    for phase in ("ROOT_FIRST", "ROOT_SECOND"):
                        self._check()
                        capture = await acquire_pg_checkpoint(
                            connection,
                            hold=hold,
                            hold_raw=hold_raw,
                            phase=phase,
                            guard=self.guard,
                            schema=self.schema,
                        )
                        self._join_pg_process(capture)
                        self._check()
                        ref = artifact_ref(
                            self.directory,
                            f"pg-s{stages:02d}-{endpoint}-{phase}.json",
                            canonical(capture),
                            self.guard,
                        )
                        item = dict(
                            stage_sequence=stages,
                            server_stage_execution_uuid=stage_uuid,
                            endpoint=endpoint,
                            phase=phase,
                            raw_artifact=ref,
                        )
                        self.pg_captures.append(item)
                        captures.append(capture)
                    if session(captures[0]) != session(captures[1]):
                        raise runtime.NativeObservationError(
                            "Held session changed between fresh ROOT snapshots"
                        )
                    release = {
                        k: hold[k]
                        for k in (
                            "schema",
                            "session_nonce",
                            "request_sha256",
                            "config_sha256",
                            "original_deadline",
                            "stage_sequence",
                            "server_stage_execution_uuid",
                            "endpoint",
                        )
                    }
                    release.update(
                        kind="RELEASE",
                        event_ordinal=len(self.events) + 1,
                        emitted_at=runtime.clock(datetime.now(UTC)),
                        hold_raw_sha256=sha(hold_raw),
                        root_capture_before_raw_sha256=self.pg_captures[-2]["raw_artifact"]["sha256"],
                        root_capture_after_raw_sha256=self.pg_captures[-1]["raw_artifact"]["sha256"],
                    )
                    validate(self.schema, "RELEASE", release)
                    release_raw = canonical(release)
                    self.total_bytes += len(release_raw) + 4
                    if self.total_bytes > MAX_BYTES:
                        raise runtime.NativeObservationError("Measurement total byte ceiling")
                    self._event(release, release_raw, "ROOT_TO_JVM")
                    await self._wait(
                        asyncio.get_running_loop().sock_sendall(
                            peer, struct.pack("!I", len(release_raw)) + release_raw
                        )
                    )
                    seal, seal_raw = await self._read(peer, "SEAL")
                    self._event(seal, seal_raw, "JVM_TO_ROOT")
                    if (
                        any(
                            seal[k] != hold[k]
                            for k in ("stage_sequence", "server_stage_execution_uuid", "endpoint")
                        )
                        or seal["hold_raw_sha256"] != sha(hold_raw)
                        or seal["release_raw_sha256"] != sha(release_raw)
                    ):
                        raise runtime.NativeObservationError("SEAL hash/endpoint binding differs")
                closed, closed_raw = await self._read(peer, "STAGE_CLOSED")
                self._event(closed, closed_raw, "JVM_TO_ROOT")
                if closed["stage_sequence"] != stages or closed["server_stage_execution_uuid"] != stage_uuid:
                    raise runtime.NativeObservationError("Closed stage identity differs")
                stages += 1
            else:
                terminal, terminal_raw = await self._read(peer, "TERMINAL")
            self._event(terminal, terminal_raw, "JVM_TO_ROOT")
            if (
                terminal["stages_completed"] != stages
                or terminal["last_stage_closed_raw_sha256"] != self.events[-2]["raw_artifact"]["sha256"]
            ):
                raise runtime.NativeObservationError("Terminal stage/count differs")
            if await self._wait(asyncio.get_running_loop().sock_recv(peer, 1)):
                raise runtime.NativeObservationError("Extra primary data after TERMINAL")
            peer.close()
            self._primary = None
            self._check()  # Includes mandatory actual primary close.
            before = datetime.now(UTC)
            outcome_peer, _ = await self._wait(
                asyncio.get_running_loop().sock_accept(outcome_listener), accepting=True
            )
            self._outcome_accepted = True
            if self._credential(outcome_peer) != self._peer:
                raise runtime.NativeObservationError("Second peer is not the same actual JVM process")
            outcome, outcome_raw = await self._read(outcome_peer, "FinalOutcome")
            if await self._wait(asyncio.get_running_loop().sock_recv(outcome_peer, 1)):
                raise runtime.NativeObservationError("Extra/duplicate outcome refused")
            outcome_peer.close()
            outcome_peer = None
            closed_at = datetime.now(UTC)
            self._check()
            guarded_at = datetime.now(UTC)
            self._terminal_capture = dict(
                schema="provider-native-root-terminal-readback-capture.v2",
                source="root-owned-second-unix-peer-bound-sealed-outcome-readback",
                before=runtime.clock(before),
                after=runtime.clock(guarded_at),
                primary_terminal_raw_sha256=sha(terminal_raw),
                outcome_raw_sha256=sha(outcome_raw),
                outcome=outcome,
                jvm_pid_host=self._peer[0],
                peercred_uid=self._peer[2],
                peercred_gid=self._peer[3],
                socket_inode=str(self._listener_inodes["outcome"][1]),
                root_local_closed_at=runtime.clock(closed_at),
                root_local_postclose_guard_at=runtime.clock(guarded_at),
                raw_query_source_sha256=source_sha(guard=self.guard),
                remote_readback_cleanup_certainty="NOT_OBSERVED_NOT_CLAIMED",
                result="PRIMARY_COMPLETE_AS_OF_SEAL_OBSERVED",
                outcome_socket_path=self.config["outcome_socket_path"],
            )
            validate(self.schema, "RootTerminalReadbackRawCapture", self._terminal_capture)
            artifact_ref(
                self.directory, "root-terminal-readback.json", canonical(self._terminal_capture), self.guard
            )
        except BaseException:
            self._failed = True
            raise
        finally:
            if outcome_listener is not None:
                asyncio.get_running_loop().remove_reader(outcome_listener.fileno())
            close_error = None
            for owned in (peer, outcome_peer, primary_listener, outcome_listener):
                if owned is not None:
                    try:
                        owned.close()
                    except OSError as error:
                        close_error = error
            self._primary = self._primary_listener = self._outcome_listener = None
            if close_error is not None:
                self._failed = True
                raise runtime.NativeObservationError("Owned listener/peer close unavailable") from close_error
        try:
            self._check()  # Listener close is part of proof; never claim remote close.
            self._collected = True
        except BaseException:
            self._failed = True
            raise

    def _capture_process(self, installation: dict[str, Any]) -> None:
        if self._peer is None:
            raise runtime.NativeObservationError("Actual JVM peer not acquired")
        root = _process_identity(os.getpid(), self.guard)
        if root[0] != root[1]:
            raise runtime.NativeObservationError("Collector must run in actual Linux host PID namespace")
        cgroup = _proc_bytes(self._peer[0], "cgroup", self.guard).decode("ascii")
        if installation["actual_container_id"] not in cgroup:
            raise runtime.NativeObservationError("Actual kernel cgroup/container join unavailable")
        self._check()  # Actual kernel peer groups and every ancestor precede custody claim.
        peer = self._peer
        self._process_capture = dict(
            schema="provider-native-root-process-socket-capture.v1",
            source="root-kernel-procfs-unix-peercred-container-readback",
            observed_at=runtime.clock(datetime.now(UTC)),
            actual_container_id=installation["actual_container_id"],
            actual_image_id=installation["actual_image_id"],
            candidate_sha=installation["candidate_sha"],
            jvm_pid_host=peer[0],
            jvm_pid_namespace=peer[1],
            jvm_uid=peer[2],
            jvm_gid=peer[3],
            root_pid_host=root[0],
            root_uid=root[2],
            root_gid=root[3],
            peercred_pid_host=peer[0],
            peercred_uid=peer[2],
            peercred_gid=peer[3],
            pid_namespace_inode=peer[5],
            mount_namespace_inode=peer[6],
            network_namespace_inode=peer[7],
            socket_path=self.config["socket_path"],
            socket_inode=str(self._listener_inodes["primary"][1]),
            parent_owner_uid=root[2],
            parent_group_gid=root[3],
            parent_mode="0750",
            socket_mode="0660",
            exclusive_group_member_uids=sorted([root[2], peer[2]]),
            no_symlink_or_parent_write_by_jvm=True,
            raw_query_source_sha256=source_sha(guard=self.guard),
        )
        validate(self.schema, "RootProcessSocketRawCapture", self._process_capture)
        artifact_ref(self.directory, "root-process-socket.json", canonical(self._process_capture), self.guard)

    def finish(
        self,
        *,
        engine: runtime.NativeEngineObservation,
        inputs: runtime.NativeObservationInputs,
        root_crosslink: runtime.NativeObservationRootCrosslink,
    ) -> NativeMeasurementProvenance:
        """Seal ROOT proof only after its own acquisition and all separate sources.

        ROOT must previously store its independent installation, integrity,
        host UNIT evidence, class census, build link and V4 crosslink in the
        closed namespace. Missing sources do not become expected config values.
        No authority/signature/receipt or remote readback cleanup is emitted.
        """
        self._check()
        if not self._collected or self._process_capture is None or self._terminal_capture is None:
            raise runtime.NativeObservationError("Actual primary and final outcome acquisition incomplete")
        if inputs.measurement_configuration != self.configuration:
            raise runtime.NativeObservationError("Frozen HTTP and measurement config differ")
        operation = engine._operation_proof
        if type(operation) is not runtime.NativeOperationObservationProof:
            raise runtime.NativeObservationError("Actual native raw response proof required")
        root_crosslink.validate(engine, inputs=inputs, deadline=self.guard.deadline)

        def existing(basename: str, name: str, limit: int = 65536) -> tuple[dict[str, Any], dict[str, Any]]:
            self._check()
            path = self.directory / basename
            metadata = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= limit:
                raise runtime.NativeObservationError("Actual independent ROOT source unavailable")
            # Pin then guarded NOFOLLOW read with actual byte hash and custody.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                self._check()
                raw = os.read(fd, limit + 1)
                self._check()
            finally:
                os.close(fd)
            self._check()
            ref = dict(path=str(path), sha256=sha(raw), size_bytes=len(raw))
            checked = read_ref(ref, self.directory, basename, self.guard, limit)
            value = decode(checked, limit=limit)
            validate(self.schema, name, value)
            self._check()
            return value, ref

        process, process_ref = existing("root-process-socket.json", "RootProcessSocketRawCapture")
        _, installation_ref = existing("root-installation.json", "RootInstallationRawCapture")
        _, integrity_ref = existing(
            "root-trusted-candidate-integrity.json", "TrustedCandidateIntegrityRawCapture"
        )
        host, host_ref = existing("root-host-unit-toolchain.json", "HostUnitRawEvidence")
        _, terminal_ref = existing("root-terminal-readback.json", "RootTerminalReadbackRawCapture")
        hello_raw = read_ref(
            self.events[0]["raw_artifact"], self.directory, "event-01-HELLO.json", self.guard, MAX_FRAME
        )
        hello = decode(hello_raw, limit=MAX_FRAME)
        process_projection = {
            key: process[key]
            for key in (
                "jvm_uid",
                "jvm_gid",
                "jvm_pid_host",
                "jvm_pid_namespace",
                "root_uid",
                "root_gid",
                "root_pid_host",
                "socket_inode",
                "parent_mode",
                "socket_mode",
            )
        }
        process_projection.update(
            source="root-kernel-process-socket-container-readback",
            raw_artifact=process_ref,
            actual_peer_credentials_checked=True,
            same_actual_jvm_process_checked=True,
        )
        self._check()
        proof = dict(
            schema="provider-native-root-measurement-provenance.v2",
            source="root-owned-independent-instrument-and-physical-readback",
            protocol=self.config["protocol"],
            protocol_schema_sha256=self.configuration.schema.sha256,
            observation_schema_sha256=self.config["observation_schema_sha256"],
            config_sha256=self.configuration.artifact.sha256,
            candidate_sha=self.config["candidate_sha"],
            actual_image_id=process["actual_image_id"],
            request_sha256=sha(operation.raw_request),
            response_sha256=sha(operation.raw_response),
            crosslink_raw_sha256=root_crosslink.artifact.sha256,
            root_pg_preboot_witness_sha256=self.config["root_pg_preboot_witness_sha256"],
            before=runtime.clock(self.guard.start_wall),
            after=runtime.clock(datetime.now(UTC)),
            stages_completed=self._terminal_capture["outcome"]["stages_completed"],
            instrument_origin=hello["instrument_origin"],
            live_jvm_toolchain=hello["jvm_toolchain"],
            host_unit_toolchain=dict(
                source="root-retained-unit-toolchain-evidence",
                java_runtime_version=host["java_runtime_version"],
                class_major=host["class_major"],
                raw_artifact=host_ref,
            ),
            process_socket=process_projection,
            installation_material_readback=installation_ref,
            events=self.events,
            pg_captures=self.pg_captures,
            trusted_candidate_integrity_evidence=integrity_ref,
            status="ALL_REQUIRED_ORIGINS_AND_PRIMARY_FINAL_OUTCOME_OBSERVED_AND_JOINED",
            terminal_readback=terminal_ref,
        )
        validate(self.schema, "RootProvenance", proof)
        ref = artifact_ref(self.directory, "root-provenance.json", canonical(proof), self.guard)
        provenance = NativeMeasurementProvenance(
            runtime.NativeObservationFile(Path(ref["path"]), ref["sha256"])
        )
        provenance.validate(
            engine, inputs=inputs, root_crosslink=root_crosslink, deadline=self.guard.deadline
        )
        self._check()
        return provenance


@dataclass(frozen=True, repr=False)
class NativeMeasurementProvenance:
    artifact: runtime.NativeObservationFile

    def validate(
        self,
        engine: runtime.NativeEngineObservation,
        *,
        inputs: runtime.NativeObservationInputs,
        root_crosslink: runtime.NativeObservationRootCrosslink,
        deadline: datetime,
    ) -> dict[str, Any]:
        guard = runtime._ObservationWaitGuard.start(deadline)
        if (
            type(engine) is not runtime.NativeEngineObservation
            or type(inputs) is not runtime.NativeObservationInputs
            or type(root_crosslink) is not runtime.NativeObservationRootCrosslink
        ):
            raise runtime.NativeObservationError("Concrete native raw proof/crosslink inputs required")
        configuration = inputs.measurement_configuration
        if (
            type(configuration) is not NativeMeasurementConfiguration
            or type(self.artifact) is not runtime.NativeObservationFile
        ):
            raise runtime.NativeObservationError("Immutable ROOT measurement provenance/config required")
        schema, config = configuration.load(deadline)
        directory = Path(config["root_evidence_directory"])
        if (
            self.artifact.path != directory / "root-provenance.json"
            or root_crosslink.artifact.path != directory / "root-crosslink.json"
        ):
            raise runtime.NativeObservationError("Exact ROOT provenance/crosslink namespace required")
        raw = self.artifact.read(deadline=deadline, limit=1048576)
        proof = decode(raw, limit=1048576)
        validate(schema, "RootProvenance", proof)
        if root_crosslink.artifact.sha256 != proof["crosslink_raw_sha256"]:
            raise runtime.NativeObservationError("ROOT raw crosslink hash differs")
        actual = root_crosslink.validate(engine, inputs=inputs, deadline=deadline)
        operation = engine._operation_proof
        if type(operation) is not runtime.NativeOperationObservationProof:
            raise runtime.NativeObservationError("Detached native raw response proof missing")
        for key, expected in (
            ("protocol_schema_sha256", configuration.schema.sha256),
            ("config_sha256", configuration.artifact.sha256),
            ("request_sha256", sha(operation.raw_request)),
            ("response_sha256", sha(operation.raw_response)),
            ("candidate_sha", config["candidate_sha"]),
            ("actual_image_id", config["expected_image_id"]),
            ("root_pg_preboot_witness_sha256", config["root_pg_preboot_witness_sha256"]),
        ):
            if proof[key] != expected:
                raise runtime.NativeObservationError("ROOT immutable provenance bindings differ")
        if (
            inputs.admission.sha256 != config["observation_admission_sha256"]
            or config["request_sha256"] != proof["request_sha256"]
        ):
            raise runtime.NativeObservationError("Preboot config admission/request differs")
        before, after = runtime._observation_time(proof["before"]), runtime._observation_time(proof["after"])
        if (
            not before <= engine.before <= engine.after <= after < deadline
            or (after - before).total_seconds() > 5
        ):
            raise runtime.NativeObservationError("ROOT whole measurement bracket refused")
        seen: set[str] = set()
        total = 0

        def get(ref: Any, basename: str, name: str, limit: int = 65536) -> tuple[dict[str, Any], bytes]:
            nonlocal total
            if type(ref) is not dict or ref.get("path") in seen:
                raise runtime.NativeObservationError("Duplicate/cyclic ROOT artifact reference")
            raw_value = read_ref(ref, directory, basename, guard, limit)
            seen.add(ref["path"])
            total += len(raw_value)
            value = decode(raw_value, limit=limit)
            validate(schema, name, value)
            guard.check()
            return value, raw_value

        process, process_raw = get(
            proof["process_socket"]["raw_artifact"], "root-process-socket.json", "RootProcessSocketRawCapture"
        )
        installation, installation_raw = get(
            proof["installation_material_readback"], "root-installation.json", "RootInstallationRawCapture"
        )
        integrity, _ = get(
            proof["trusted_candidate_integrity_evidence"],
            "root-trusted-candidate-integrity.json",
            "TrustedCandidateIntegrityRawCapture",
        )
        host, _ = get(
            proof["host_unit_toolchain"]["raw_artifact"],
            "root-host-unit-toolchain.json",
            "HostUnitRawEvidence",
        )
        census, _ = get(
            installation["class_and_resource_census_artifact"],
            "root-class-resource-census.json",
            "ClosedRawCensus",
            1048576,
        )
        build, _ = get(
            installation["build_source_freeze_artifact"],
            "root-source-build-link.json",
            "SourceBuildFreezeLink",
        )
        terminal_capture, _ = get(
            proof["terminal_readback"], "root-terminal-readback.json", "RootTerminalReadbackRawCapture"
        )
        for source in (process, installation, integrity, build):
            if source["candidate_sha"] != proof["candidate_sha"]:
                raise runtime.NativeObservationError("Independent source candidate differs")
        for source in (process, installation, integrity):
            if source["actual_image_id"] != proof["actual_image_id"]:
                raise runtime.NativeObservationError("Independent source image differs")
        if (
            installation["actual_container_id"] != process["actual_container_id"]
            or integrity["root_process_socket_capture_raw_sha256"] != sha(process_raw)
            or integrity["root_installation_capture_raw_sha256"] != sha(installation_raw)
            or installation["protocol_schema_raw_sha256"] != configuration.schema.sha256
            or installation["support_jar_raw_sha256"] != config["support_jar_sha256"]
            or installation["admitted_instrument_class_raw_sha256"] != config["instrument_class_sha256"]
            or build["host_unit_java_runtime_version"] != host["java_runtime_version"]
            or proof["host_unit_toolchain"]["java_runtime_version"] != host["java_runtime_version"]
        ):
            raise runtime.NativeObservationError("Independent material/source/toolchain join differs")
        for key in (
            "jvm_uid",
            "jvm_gid",
            "jvm_pid_host",
            "jvm_pid_namespace",
            "root_uid",
            "root_gid",
            "root_pid_host",
            "socket_inode",
            "parent_mode",
            "socket_mode",
        ):
            if proof["process_socket"][key] != process[key]:
                raise runtime.NativeObservationError("ROOT process projection differs from raw acquisition")
        if (
            process["peercred_pid_host"] != process["jvm_pid_host"]
            or process["peercred_uid"] != process["jvm_uid"]
            or process["peercred_gid"] != process["jvm_gid"]
            or process["root_uid"] != config["root_peer_uid"]
            or process["root_gid"] != config["root_peer_gid"]
            or process["jvm_uid"] != config["jvm_uid"]
            or process["jvm_gid"] != config["jvm_gid"]
            or process["parent_owner_uid"] != process["root_uid"]
            or process["parent_group_gid"] != process["root_gid"]
            or set(process["exclusive_group_member_uids"]) != {process["root_uid"], process["jvm_uid"]}
            or process["root_uid"] == process["jvm_uid"]
            or process["socket_path"] != config["socket_path"]
        ):
            raise runtime.NativeObservationError("Actual process/kernel/IPC principals differ")
        if process["raw_query_source_sha256"] != source_sha(guard=guard):
            raise runtime.NativeObservationError("Actual collector source origin differs")
        n = proof["stages_completed"]
        if len(proof["events"]) != 2 + 7 * n or len(proof["pg_captures"]) != 4 * n:
            raise runtime.NativeObservationError("Exact finite event/PG capture count required")
        events: list[dict[str, Any]] = []
        event_raw: list[bytes] = []
        last_at = before
        for ordinal, ref in enumerate(proof["events"], 1):
            kind = ref["kind"]
            value, raw_value = get(ref["raw_artifact"], f"event-{ordinal:02d}-{kind}.json", kind, MAX_FRAME)
            if (
                ref["event_ordinal"] != ordinal
                or value["event_ordinal"] != ordinal
                or ref["direction"] != ("ROOT_TO_JVM" if kind == "RELEASE" else "JVM_TO_ROOT")
                or any(
                    value[key] != expected
                    for key, expected in (
                        ("session_nonce", config["session_nonce"]),
                        ("request_sha256", proof["request_sha256"]),
                        ("config_sha256", proof["config_sha256"]),
                        ("original_deadline", config["original_deadline"]),
                    )
                )
            ):
                raise runtime.NativeObservationError("Event immutable direction/session/ordinal differs")
            at = runtime._observation_time(value["emitted_at"])
            if not last_at <= at <= after:
                raise runtime.NativeObservationError("Event clock/order differs")
            last_at = at
            events.append(value)
            event_raw.append(raw_value)
        if events[0]["kind"] != "HELLO" or events[-1]["kind"] != "TERMINAL":
            raise runtime.NativeObservationError("HELLO/TERMINAL mandatory")
        hello = events[0]
        if (
            hello["instrument_origin"] != proof["instrument_origin"]
            or hello["jvm_toolchain"] != proof["live_jvm_toolchain"]
        ):
            raise runtime.NativeObservationError("Immutable instrument/live toolchain differs")
        origin, vm = proof["instrument_origin"], proof["live_jvm_toolchain"]
        if (
            origin["class_sha256"] != config["instrument_class_sha256"]
            or origin["jar_sha256"] != config["support_jar_sha256"]
            or vm["process_pid_namespace"] != process["jvm_pid_namespace"]
        ):
            raise runtime.NativeObservationError("HELLO physical class/process differs")
        snapshot = engine.route_probe["snapshot"]
        stages = [snapshot["database_witness"], *snapshot["emission_recheck"]["read_stages"]]
        if len(stages) != n:
            raise runtime.NativeObservationError("Native response stage count differs")
        stage_uuids: set[str] = set()
        for seq in range(n):
            stage_events = events[1 + 7 * seq : 8 + 7 * seq]
            if [v["kind"] for v in stage_events] != [
                "HOLD",
                "RELEASE",
                "SEAL",
                "HOLD",
                "RELEASE",
                "SEAL",
                "STAGE_CLOSED",
            ]:
                raise runtime.NativeObservationError("Exact stage event grammar refused")
            uuid = stage_events[0]["server_stage_execution_uuid"]
            if (
                uuid in stage_uuids
                or stages[seq]["server_stage_execution_uuid"] != uuid
                or str(seq) != stages[seq]["stage_sequence"]
            ):
                raise runtime.NativeObservationError("Contiguous independent stage UUID differs")
            stage_uuids.add(uuid)
            if any(
                v["stage_sequence"] != seq or v["server_stage_execution_uuid"] != uuid for v in stage_events
            ):
                raise runtime.NativeObservationError("Stage event identity differs")
            for endpoint_index, endpoint in enumerate(("ENTRY", "COMMITTING")):
                hold, release, seal = stage_events[3 * endpoint_index : 3 * endpoint_index + 3]
                raw_index = 1 + 7 * seq + 3 * endpoint_index
                if (
                    any(v["endpoint"] != endpoint for v in (hold, release, seal))
                    or release["hold_raw_sha256"] != sha(event_raw[raw_index])
                    or seal["hold_raw_sha256"] != sha(event_raw[raw_index])
                    or seal["release_raw_sha256"] != sha(event_raw[raw_index + 1])
                ):
                    raise runtime.NativeObservationError("HOLD/RELEASE/SEAL local raw hashes differ")
                self._measurements(
                    hold,
                    seal,
                    stages[seq][endpoint.lower()],
                    seq,
                    vm,
                    origin,
                    actual,
                    installation,
                    census,
                    before,
                    after,
                )
                captures = []
                for phase_index, phase in enumerate(("ROOT_FIRST", "ROOT_SECOND")):
                    ref = proof["pg_captures"][4 * seq + 2 * endpoint_index + phase_index]
                    expected = dict(
                        stage_sequence=seq, server_stage_execution_uuid=uuid, endpoint=endpoint, phase=phase
                    )
                    if any(ref[key] != value for key, value in expected.items()):
                        raise runtime.NativeObservationError("Contiguous two fresh PG captures required")
                    capture, capture_raw = get(
                        ref["raw_artifact"], f"pg-s{seq:02d}-{endpoint}-{phase}.json", "RootPgRawCapture"
                    )
                    if any(capture[key] != value for key, value in expected.items()):
                        raise runtime.NativeObservationError("PG raw locator differs")
                    field = (
                        "root_capture_before_raw_sha256"
                        if phase_index == 0
                        else "root_capture_after_raw_sha256"
                    )
                    if release[field] != sha(capture_raw) or capture["hold_raw_sha256"] != sha(
                        event_raw[raw_index]
                    ):
                        raise runtime.NativeObservationError("Release PG raw acquisition hash differs")
                    witness = hold["measurement"]["session_transaction"]
                    if session(capture) != {key: witness[key] for key in SESSION_KEYS}:
                        raise runtime.NativeObservationError("Fresh real snapshot/session tuple differs")
                    at = runtime._observation_time(capture["root_observed_at"])
                    if (
                        not runtime._observation_time(hold["emitted_at"])
                        <= at
                        <= runtime._observation_time(release["emitted_at"])
                    ):
                        raise runtime.NativeObservationError("PG capture outside HOLD/RELEASE bracket")
                    if (
                        any(
                            capture[k] != hold["measurement"]["stable_database_projection"][k]
                            for k in ("database_name", "database_oid", "system_identifier")
                        )
                        or capture["session_login"]
                        != hold["measurement"]["stable_database_projection"]["session_user"]
                    ):
                        raise runtime.NativeObservationError("PG login/database source differs")
                    if (
                        capture["collector_uid"] != process["root_uid"]
                        or capture["query_source_sha256"] != process["raw_query_source_sha256"]
                    ):
                        raise runtime.NativeObservationError("PG collector/process origin differs")
                    captures.append(capture)
                if any(
                    captures[0][key] != captures[1][key]
                    for key in (
                        *SESSION_KEYS[:3],
                        "backend_xid32",
                        "session_login",
                        "client_address",
                        "client_port",
                        "backend_ssl",
                        "backend_tls_version",
                    )
                ):
                    raise runtime.NativeObservationError("PG before/after held physical tuple changed")
            closed = stage_events[-1]
            if (
                closed["ctx_reference_ordinal"] != seq + 1
                or closed["connection_reference_ordinal"]
                != stage_events[0]["measurement"]["connection_reference_ordinal"]
                or not runtime._observation_time(stage_events[5]["emitted_at"])
                <= runtime._observation_time(closed["committed_marker_at"])
                <= runtime._observation_time(closed["executor_return_at"])
                <= runtime._observation_time(closed["context_closed_at"])
                <= runtime._observation_time(closed["emitted_at"])
            ):
                raise runtime.NativeObservationError(
                    "Independent commit/executor/context close order differs"
                )
        terminal = events[-1]
        outcome = terminal_capture["outcome"]
        if (
            terminal["response_sha256"] != proof["response_sha256"]
            or terminal["stages_completed"] != n
            or terminal["last_stage_closed_raw_sha256"] != sha(event_raw[-2])
            or terminal_capture["primary_terminal_raw_sha256"] != sha(event_raw[-1])
            or terminal_capture["outcome_raw_sha256"] != sha(canonical(outcome))
            or terminal_capture["jvm_pid_host"] != process["jvm_pid_host"]
            or terminal_capture["peercred_uid"] != process["jvm_uid"]
            or terminal_capture["peercred_gid"] != process["jvm_gid"]
            or terminal_capture["raw_query_source_sha256"] != process["raw_query_source_sha256"]
            or terminal_capture["outcome_socket_path"] != config["outcome_socket_path"]
            or terminal_capture["socket_inode"] == process["socket_inode"]
        ):
            raise runtime.NativeObservationError("Unique outcome raw primary/kernel binding differs")
        for key, expected in (
            ("session_nonce", config["session_nonce"]),
            ("request_sha256", proof["request_sha256"]),
            ("response_sha256", proof["response_sha256"]),
            ("config_sha256", proof["config_sha256"]),
            ("terminal_raw_sha256", sha(event_raw[-1])),
            ("last_stage_closed_raw_sha256", sha(event_raw[-2])),
            ("candidate_sha", proof["candidate_sha"]),
            ("stages_completed", n),
            ("boot_uuid", vm["boot_uuid"]),
            ("instrument_class_sha256", origin["class_sha256"]),
            ("original_deadline", config["original_deadline"]),
        ):
            if outcome[key] != expected:
                raise runtime.NativeObservationError("Sealed final outcome session/code differs")
        clocks = [
            runtime._observation_time(outcome[key])
            for key in (
                "primary_http_closed_at",
                "primary_uds_closed_at",
                "primary_postclose_guard_at",
                "sealed_at",
            )
        ]
        if (
            not before
            <= clocks[0]
            <= runtime._observation_time(terminal["emitted_at"])
            <= clocks[1]
            <= clocks[2]
            <= clocks[3]
            <= after
            < deadline
            or not before
            <= runtime._observation_time(terminal_capture["before"])
            <= runtime._observation_time(terminal_capture["root_local_closed_at"])
            <= runtime._observation_time(terminal_capture["root_local_postclose_guard_at"])
            <= runtime._observation_time(terminal_capture["after"])
            <= after
        ):
            raise runtime.NativeObservationError("As-of primary/ROOT postclose clocks refused")
        if (
            sum(len(v) + 4 for v in event_raw) + len(canonical(outcome)) + 4 > MAX_BYTES
            or len(seen) != 2 + 7 * n + 4 * n + 7
        ):
            raise runtime.NativeObservationError("Finite combined frame/reference budget refused")
        # All refs are closed and consumed; no directory scans or recursive readers.
        guard.check()
        return proof

    @staticmethod
    def _measurements(
        hold: dict[str, Any],
        seal: dict[str, Any],
        response: dict[str, Any],
        seq: int,
        vm: dict[str, Any],
        origin: dict[str, Any],
        actual: dict[str, Any],
        installation: dict[str, Any],
        census: dict[str, Any],
        before: datetime,
        after: datetime,
    ) -> None:
        a, b = hold["measurement"], seal["measurement"]
        stable = (
            "ctx_reference_ordinal",
            "connection_reference_ordinal",
            "stable_database_projection",
            "runtime_installation_projection",
            "jvm_toolchain",
            "instrument_origin",
        )
        if (
            any(a[k] != b[k] for k in stable)
            or a["ctx_reference_ordinal"] != seq + 1
            or a["jvm_toolchain"] != vm
            or a["instrument_origin"] != origin
        ):
            raise runtime.NativeObservationError("Independent direct measurement identity/config changed")
        for measurement in (a, b):
            session_value = measurement["session_transaction"]
            if (
                {k: session_value[k] for k in SESSION_KEYS}
                != {k: response["session_transaction"][k] for k in SESSION_KEYS}
                or measurement["stable_database_projection"] != actual["stable_database_projection"]
                or runtime._number_free_observation(measurement["runtime_installation_projection"])
                != runtime._number_free_observation(actual["runtime_installation_projection"])
            ):
                raise runtime.NativeObservationError("Independent instrument/ROOT/producer facts differ")
            started = runtime._observation_time(measurement["started_at"])
            finished = runtime._observation_time(measurement["finished_at"])
            if (
                not before
                <= started
                <= runtime._observation_time(session_value["observed_at"])
                <= finished
                <= after
                or abs((finished - started).total_seconds() * 1e9 - int(measurement["monotonic_elapsed_ns"]))
                > 1000000
            ):
                raise runtime.NativeObservationError("Direct own measurement paired clocks refused")
            install = measurement["runtime_installation_projection"]
            jvm = install["jvm_process"]
            if (
                jvm["boot_uuid"] != vm["boot_uuid"]
                or jvm["pid_namespace"] != vm["process_pid_namespace"]
                or jvm["start_time_epoch_ms"] != vm["runtime_mxbean_start_epoch_ms"]
            ):
                raise runtime.NativeObservationError("Actual JVM BOOT/start/toolchain differs")
            pins = install["mounted_pins"]
            for pin, raw_pin in (
                ("native_jar_sha256", "native_jar_raw_sha256"),
                ("support_jar_sha256", "support_jar_raw_sha256"),
                ("rest_spi_jar_sha256", "rest_spi_jar_raw_sha256"),
                ("descriptor_sha256", "descriptor_raw_sha256"),
                ("startup_descriptor_inventory_sha256", "startup_descriptor_inventory_raw_sha256"),
                ("startup_vendor_inventory_sha256", "startup_vendor_inventory_raw_sha256"),
                ("observation_schema_sha256", "observation_schema_raw_sha256"),
            ):
                if pins[pin] != installation[raw_pin]:
                    raise runtime.NativeObservationError("Actual installed material pins differ")
            classes = install["class_provenance"]
            if tuple(c["class_name"] for c in classes) != runtime._OBSERVATION_CLASSES:
                raise runtime.NativeObservationError("Four exact ordered runtime class origins required")
            required = [
                (c["jar_relative_path"], c["class_name"].replace(".", "/") + ".class", c["class_sha256"])
                for c in classes
            ]
            required.append(
                (
                    origin["jar_relative_path"],
                    origin["instrument_class"].replace(".", "/") + ".class",
                    origin["class_sha256"],
                )
            )
            entries = census["entries"]
            for jar, name, pin in required:
                found = [v for v in entries if v["entry_name"] == name]
                if len(found) != 1 or found[0]["jar_relative_path"] != jar or found[0]["raw_sha256"] != pin:
                    raise runtime.NativeObservationError("Exclusive actual class/resource census differs")
            names = [(v["jar_relative_path"], v["entry_name"]) for v in entries]
            if len(names) != len(set(names)) or any(
                Path(v).is_absolute() or ".." in Path(v).parts or "//" in v or "/./" in v or "\\" in v
                for pair in names
                for v in pair
            ):
                raise runtime.NativeObservationError("Census alias/path/duplicate refused")


async def acquire_native_measurement(
    *,
    inputs: runtime.NativeObservationInputs,
    transport: runtime.NativeObservationTransport,
    connection: asyncpg.Connection,
    installation_file: runtime.NativeObservationFile,
    deadline: datetime,
) -> tuple[NativeMeasurementCollector, runtime.NativeEngineObservation]:
    """First physical lane call, with coordinated failure/owned FD teardown.

    Supplies no driver credentials or actors. ROOT must provide the actual
    qualified connection, Linux principals, socket setup and preboot materials.
    This returns captured HTTP/UDS evidence pending ROOT independent crosslink,
    immutable provenance and gates. It cannot construct Measurements by itself.
    """
    guard = runtime._ObservationWaitGuard.start(deadline)
    if type(inputs) is not runtime.NativeObservationInputs:
        raise runtime.NativeObservationError("Concrete actual observation inputs required")
    configuration = inputs.measurement_configuration
    if type(configuration) is not NativeMeasurementConfiguration:
        raise runtime.NativeObservationError("Actual pinned instrument configuration required")
    inputs.load(deadline)
    collector = NativeMeasurementCollector(configuration, deadline=deadline)
    async with asyncio.TaskGroup() as group:
        # collect binds both fixed listeners synchronously before its first wait.
        group.create_task(collector.collect(connection, installation_file=installation_file))
        operation = group.create_task(
            runtime.observe_native_engine_operation(inputs=inputs, transport=transport, deadline=deadline)
        )
    guard.check()  # Includes both tasks' complete owned socket teardown.
    collector._check()
    result = operation.result()
    guard.check()
    return collector, result
