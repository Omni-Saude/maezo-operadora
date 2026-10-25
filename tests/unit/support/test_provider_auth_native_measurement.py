"""Selected grammar and snapshot counterproofs; no physical runtime claims."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from tests.support import provider_auth_native_measurement as measurement
from tests.support import provider_auth_native_runtime as runtime


def test_snapshot_mapping_uses_unique_real_full_xid_and_allows_unassigned():
    full = str(2**32 + 17)
    assert measurement.full_xid("17", [full], witness=full) == (
        full,
        measurement.XID_SOURCE_IN_PROGRESS,
    )
    assert measurement.full_xid(None, [full], witness=None) == (
        None,
        measurement.XID_SOURCE_IN_PROGRESS,
    )


def test_committed_clog_path_admits_zero_candidates_with_same_statement_verdict():
    full = "761"
    # Live committed verdict from the checkpoint's own statement, with the lagging
    # pg_stat_activity projection still showing the xid32 (pre-clear lag).
    assert measurement.full_xid("761", [], witness=full, live_status="committed") == (
        full,
        measurement.XID_SOURCE_CLOG_COMMITTED,
    )
    # The projection already cleared between the row and the snapshot: null xid32
    # is non-contradictory on the committed path, never a waive.
    assert measurement.full_xid(None, [], witness=full, live_status="committed") == (
        full,
        measurement.XID_SOURCE_CLOG_COMMITTED,
    )
    # Offline re-derivation of a stored zero-candidate capture (pair verdicts and
    # provenance revalidation): the live guard is the only way such a capture exists.
    assert measurement.full_xid("761", [], witness=full) == (
        full,
        measurement.XID_SOURCE_CLOG_COMMITTED,
    )


@pytest.mark.parametrize(
    "status,reason",
    [
        ("aborted", "aborted"),
        ("in progress", "non-decisive"),
        ("inprogress", "non-decisive"),
        ("subcommitted", "subcommitted"),
        (None, "horizon"),
        ("committed ", "unknown"),
        ("COMMITTED", "unknown"),
    ],
)
def test_only_exact_committed_clog_literal_admits(status, reason):
    with pytest.raises(runtime.NativeObservationError, match=reason):
        measurement.full_xid("761", [], witness="761", live_status=status)


@pytest.mark.parametrize(
    "xid32,active,witness,live_status,reason",
    [
        ("9", [], "761", "committed", "differs from witness epoch"),
        (None, ["761"], "761", "in progress", "cleared backend xid32"),
        ("761", ["761", str(2**32 + 761)], "761", None, "ambiguous"),
        ("761", [str(2**32 + 761)], "761", None, "differs from witness epoch"),
        ("762", ["761"], "761", "committed", "differs from witness epoch"),
        ("761", [], None, "committed", "unassigned witness"),
        ("bogus", [], "761", "committed", "backend xid32 refused"),
        ("761", [], "9" * 20, "committed", "witness full XID refused"),
    ],
)
def test_discriminant_refusals_cover_every_non_admissible_state(xid32, active, witness, live_status, reason):
    with pytest.raises(runtime.NativeObservationError, match=reason):
        measurement.full_xid(xid32, active, witness=witness, live_status=live_status)


@pytest.mark.parametrize(
    "xid,active",
    [
        ("17", ["17", "17"]),
        ("17", [str(2**64)]),
        (True, ["17"]),
    ],
)
def test_snapshot_ambiguous_unbounded_or_typed_xid_is_unknown(xid, active):
    with pytest.raises(runtime.NativeObservationError):
        measurement.full_xid(xid, active, witness="17")


def test_session_derivation_preserves_witness_equality_on_both_decisive_paths():
    capture = {
        "backend_pid": "81",
        "backend_start": "2026-10-22T15:11:15.942000Z",
        "transaction_started_at": "2026-10-22T15:11:15.942219Z",
        "backend_ssl": True,
        "backend_tls_version": "TLSv1.3",
        "backend_xid32": "761",
        "active_full_xids": [],
    }
    witness = "761"
    live = measurement.session(capture, witness=witness, live_status="committed")
    offline = measurement.session(capture, witness=witness)
    assert live == offline
    assert live["transaction_id_if_assigned"] == witness
    in_progress = dict(capture, active_full_xids=["761"])
    assert measurement.session(in_progress, witness=witness)["transaction_id_if_assigned"] == witness


@pytest.mark.parametrize(
    "raw", [b'{"a":1,"a":2}', b'{"a":1.0}', b'{"a":true}\n', b'{ "a":1}', b'{"a":01}', b'{"a":NaN}']
)
def test_frame_grammar_refuses_duplicate_float_or_noncanonical_bytes(raw):
    with pytest.raises(runtime.NativeObservationError):
        measurement.decode(raw, limit=measurement.MAX_FRAME)


def test_frame_canonical_ascii_preserves_actual_vm_name_spaces():
    value = {"vm": "OpenJDK 64-Bit Server VM", "label": "á"}
    raw = measurement.canonical(value)
    assert b"\\u00e1" in raw
    assert measurement.decode(raw, limit=measurement.MAX_FRAME) == value


def test_reference_rejects_foreign_path_before_any_file_read(tmp_path, monkeypatch):
    directory = tmp_path / "measurement-unit"
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=5))

    def forbidden_read(*args, **kwargs):
        pytest.fail("Reader must reject foreign path before attempting any read")

    monkeypatch.setattr(runtime.NativeObservationFile, "read", forbidden_read)
    ref = dict(path=str(tmp_path / "foreign.json"), sha256="a" * 64, size_bytes=1)
    with pytest.raises(runtime.NativeObservationError, match="before read"):
        measurement.read_ref(ref, directory, "root-terminal-readback.json", guard, 65536)


def test_pg_acquisition_refuses_foreign_connection_without_query():
    # An inert value cannot masquerade as an actual asyncpg connection.
    import asyncio

    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=5))
    with pytest.raises(runtime.NativeObservationError, match="Actual clean"):
        asyncio.run(
            measurement.acquire_pg_checkpoint(
                object(), hold={}, hold_raw=b"{}", phase="ROOT_FIRST", guard=guard, schema={}
            )
        )


def test_actual_collector_source_pin_is_the_exact_owned_source_file():
    assert measurement.source_sha() == measurement.sha(Path(measurement.__file__).read_bytes())


@pytest.mark.parametrize("phase", ["before-HELLO", "HOLD", "TERMINAL-primary-open"])
def test_unit_actual_outcome_queue_before_primary_kernel_eof_refused(tmp_path, monkeypatch, phase):
    import socket
    from types import SimpleNamespace

    # Bind by relative name after chdir: darwin caps sun_path at 104 bytes and
    # the pytest tmp_path prefix alone exceeds it (capture-boundary unit idiom).
    monkeypatch.chdir(tmp_path)
    collector = measurement.NativeMeasurementCollector.__new__(measurement.NativeMeasurementCollector)
    collector._failed = False
    collector.guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=5))
    collector.configuration = SimpleNamespace(load=lambda deadline: None)
    collector._peer = None
    collector._primary_listener = None
    collector._primary_accepted = False
    collector._outcome_accepted = False
    collector._terminal_seen = phase == "TERMINAL-primary-open"
    primary, producer = socket.socketpair()
    primary.setblocking(False)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind("unit-outcome")
        listener.listen(1)
        listener.setblocking(False)
        client.connect("unit-outcome")
        collector._primary = primary
        collector._outcome_listener = listener
        with pytest.raises(runtime.NativeObservationError, match="premature"):
            collector._check()
    finally:
        for owned in (primary, producer, listener, client):
            owned.close()


@pytest.mark.parametrize("owner,group,mode", [(2002, 0, 0o755), (0, 3030, 0o775)])
def test_unit_metadata_jvm_writable_ancestor_refused(monkeypatch, owner, group, mode):
    import os
    import stat
    from types import SimpleNamespace

    path = Path("/unit-protected/ipc/primary.sock")
    config = dict(
        root_peer_uid=1001,
        root_peer_gid=3030,
        root_peer_group="unit-ipc",
        root_peer_user="unit-root",
        jvm_uid=2002,
        jvm_gid=3030,
    )
    monkeypatch.setattr(Path, "resolve", lambda self: self)

    def metadata(self, **kwargs):
        if self == path:
            raise FileNotFoundError(2, "unit absence", str(self))
        uid, gid, permissions = (1001, 3030, 0o750) if self == path.parent else (0, 0, 0o755)
        if self == Path("/unit-protected"):
            uid, gid, permissions = owner, group, mode
        return SimpleNamespace(st_mode=stat.S_IFDIR | permissions, st_uid=uid, st_gid=gid)

    monkeypatch.setattr(Path, "stat", metadata)
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    monkeypatch.setattr(os, "getegid", lambda: 3030)
    monkeypatch.setattr(measurement, "_xattrs", lambda path: [])
    users = [
        SimpleNamespace(pw_uid=1001, pw_gid=3030, pw_name="unit-root"),
        SimpleNamespace(pw_uid=2002, pw_gid=3030, pw_name="unit-jvm"),
    ]
    monkeypatch.setattr(
        measurement.grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="unit-ipc", gr_mem=[])
    )
    monkeypatch.setattr(measurement.pwd, "getpwall", lambda: users)
    monkeypatch.setattr(measurement.pwd, "getpwuid", lambda uid: users[0])
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=5))
    with pytest.raises(runtime.NativeObservationError, match="ancestor"):
        measurement._socket_custody(path, config, guard, exists=False)


@pytest.mark.parametrize("buffered", [False, True])
def test_unit_outcome_queue_with_actual_primary_remote_close_is_deferred(tmp_path, monkeypatch, buffered):
    import socket

    # Relative AF_UNIX bind after chdir: sun_path <= 104 on darwin, tmp_path
    # prefix alone exceeds it (capture-boundary unit idiom).
    monkeypatch.chdir(tmp_path)
    collector = measurement.NativeMeasurementCollector.__new__(measurement.NativeMeasurementCollector)
    collector._failed = False
    collector._outcome_accepted = False
    collector._outcome_arrival_observed = False
    collector._terminal_seen = False
    primary, producer = socket.socketpair()
    primary.setblocking(False)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind("outcome")
        listener.listen(1)
        listener.setblocking(False)
        if buffered:
            producer.sendall(b"UnitOnly-buffered-terminal-not-protocol-authority")
        producer.close()
        client.connect("outcome")
        collector._primary = primary
        collector._outcome_listener = listener
        collector._check_outcome_arrival()
        assert collector._outcome_arrival_observed
        assert not collector._outcome_accepted
        if buffered:
            assert primary.recv(256) == b"UnitOnly-buffered-terminal-not-protocol-authority"
        assert primary.recv(1) == b""
    finally:
        for owned in (primary, producer, listener, client):
            owned.close()


# ---------------------------------------------------------------------------
# REFUSAL-63 (v25 + ADENDO 2 deferral): the ONE decisive T3 read at the
# COMMITTING straddle corner, deferred by the unconditional bounded
# _T3_DEFERRED_SECONDS wait so the verdict is terminal (commit horizon measured
# at emit+35.4/36.3/37.2 ms; the immediate v25 read lost that race by <2 ms).
# ---------------------------------------------------------------------------

_OBSERVED_AT = datetime(2026, 10, 25, 1, 53, 27, 128000, tzinfo=UTC)
_UNIT_STRADDLE_UNSET = object()
_IN_GAP_REFUSAL = "Actual witness clog in progress without xip candidacy (non-decisive, never waived)"
# Today's exact capture key set: the T3 verdict and its sub-source must never
# leak into the byte-stable document (schema fixture pins additionalProperties).
_CAPTURE_SCHEMA_KEYS = (
    "active_full_xids",
    "backend_pid",
    "backend_ssl",
    "backend_start",
    "backend_tls_version",
    "backend_xid32",
    "client_address",
    "client_port",
    "collector_pid_namespace",
    "collector_uid",
    "database_name",
    "database_oid",
    "endpoint",
    "hold_raw_sha256",
    "observer_session_user",
    "phase",
    "query_schema",
    "query_source_sha256",
    "root_observed_at",
    "schema",
    "server_stage_execution_uuid",
    "server_version",
    "session_login",
    "source",
    "stage_sequence",
    "stats_visibility_qualified",
    "system_identifier",
    "track_activities_observed_on",
    "transaction_started_at",
)


class _UnitTransaction:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exception: Any) -> bool:
        return False


class _CheckpointConnection(asyncpg.Connection):
    """Inert asyncpg.Connection stand-in routed by exact module SQL identity."""

    __slots__ = ("_straddle", "_witness_row", "_xip", "checkpoint_txid_param", "queries")

    def __init__(self, *, checkpoint_row: dict, xip: list, straddle: Any = _UNIT_STRADDLE_UNSET):
        self._aborted = True  # base __del__ reads it; no transport is mocked here
        self._witness_row = checkpoint_row
        self._xip = list(xip)
        self._straddle = straddle
        self.queries = []
        self.checkpoint_txid_param = "never-queried"

    def is_in_transaction(self) -> bool:
        return False

    def transaction(self, isolation: Any = None, readonly: bool = False) -> _UnitTransaction:
        return _UnitTransaction()

    async def execute(self, query: str, *args: Any, **timeout: Any) -> str:
        self.queries.append(("execute", query, args))
        return "SELECT 1"

    async def fetchrow(self, query: str, *args: Any, **timeout: Any) -> Any:
        self.queries.append(("fetchrow", query, args))
        if query is measurement._VISIBILITY_SQL:
            return dict(
                observer_session_user="root_observer",
                server_version="16.15",
                track_activities="on",
                visible=True,
            )
        if query is measurement._CHECKPOINT_SQL:
            self.checkpoint_txid_param = args[1]
            return dict(self._witness_row)
        if query is measurement._SNAPSHOT_SQL:
            return dict(active_full_xids=list(self._xip))
        raise AssertionError(f"unit connection: unexpected fetchrow {query[:48]!r}")

    async def fetchval(self, query: str, *args: Any, **timeout: Any) -> Any:
        self.queries.append(("fetchval", query, args))
        if query is not measurement._STRADDLE_SQL or self._straddle is _UNIT_STRADDLE_UNSET:
            raise AssertionError("unit connection: unexpected fetchval (T3 must not run)")
        return self._straddle


def _checkpoint_row(*, backend_xid32: Any, witness_status: Any) -> dict:
    return dict(
        observed_at=_OBSERVED_AT,
        database_name="maezo_n2",
        database_oid="16384",
        system_identifier="7398612345678901",
        backend_pid="81",
        backend_start=_OBSERVED_AT,
        transaction_started_at=_OBSERVED_AT,
        backend_xid32=backend_xid32,
        session_login="maezo_jvm",
        client_address="127.0.0.1/32",
        client_port=49152,
        backend_ssl=True,
        backend_tls_version="TLSv1.3",
        witness_xact_status=witness_status,
    )


def _hold(*, endpoint: str, witness_txid: Any) -> dict:
    return dict(
        stage_sequence=0,
        server_stage_execution_uuid="unit-stage-execution-uuid",
        endpoint=endpoint,
        measurement=dict(
            session_transaction=dict(
                backend_pid="81",
                backend_start=runtime.clock(_OBSERVED_AT),
                transaction_started_at=runtime.clock(_OBSERVED_AT),
                transaction_id_if_assigned=witness_txid,
                backend_ssl=True,
                backend_tls_version="TLSv1.3",
            ),
            stable_database_projection=dict(
                database_name="maezo_n2",
                database_oid="16384",
                system_identifier="7398612345678901",
                session_user="maezo_jvm",
            ),
        ),
    )


def _capture_schema() -> dict:
    return {
        "$defs": {
            "RootPgRawCapture": dict(
                type="object",
                properties={key: {} for key in _CAPTURE_SCHEMA_KEYS},
                required=list(_CAPTURE_SCHEMA_KEYS),
                additionalProperties=False,
            )
        }
    }


def _spy_session(monkeypatch: pytest.MonkeyPatch, observed: list) -> None:
    real = measurement.session

    def spy(capture, *, witness, live_status=measurement._LIVE_STATUS_UNSET):
        observed.append(live_status)
        return real(capture, witness=witness, live_status=live_status)

    monkeypatch.setattr(measurement, "session", spy)


def _acquire(connection: _CheckpointConnection, hold: dict) -> dict:
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=30))
    return asyncio.run(
        measurement.acquire_pg_checkpoint(
            connection,
            hold=hold,
            hold_raw=b"{}",
            phase="ROOT_SECOND",
            guard=guard,
            schema=_capture_schema(),
        )
    )


def _query_sequence(connection: _CheckpointConnection) -> list:
    return [(kind, query) for kind, query, _ in connection.queries]


def _spy_sleep(monkeypatch: pytest.MonkeyPatch, connection: _CheckpointConnection, *, delegate: bool) -> list:
    """Interpose a spy on the module's only wait (asyncio.sleep).

    Every call is recorded as (delay, queries-so-far) AND journalled into the
    connection query log, so the ordering contract — the T3' wait strictly
    between the T2 snapshot and the single straddle read, and nowhere else — is
    provable from one sequence. delegate=True also awaits the real sleep so the
    wall-clock deferral itself stays under test.
    """
    real = asyncio.sleep
    waited: list = []

    async def spy(delay: Any, *args: Any, **kwargs: Any) -> None:
        waited.append((delay, len(connection.queries)))
        connection.queries.append(("sleep", delay, ()))
        if delegate:
            await real(delay)

    monkeypatch.setattr(measurement.asyncio, "sleep", spy)
    return waited


def test_in_gap_straddle_t3_committed_admits_with_distinct_t3_sub_source(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=[],
        straddle="committed",
    )
    observed = []
    _spy_session(monkeypatch, observed)
    waited = _spy_sleep(monkeypatch, connection, delegate=True)
    started = time.monotonic()
    capture = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    # Banked capture document stays byte-stable (no T3 field) and pins this source.
    assert set(capture) == set(_CAPTURE_SCHEMA_KEYS)
    assert capture["active_full_xids"] == []
    assert capture["query_source_sha256"] == measurement.source_sha()
    # The admission was decided by the T3 verdict and banks the DISTINCT sub-source.
    assert observed == [measurement._T3_COMMITTED]
    assert measurement.full_xid("761", [], witness="761", live_status=measurement._T3_COMMITTED) == (
        "761",
        measurement.XID_SOURCE_CLOG_COMMITTED_T3,
    )
    assert measurement.session(capture, witness="761")["transaction_id_if_assigned"] == "761"
    # Exactly ONE decisive read, strictly AFTER the snapshot: no retry, no poll.
    # The single T3' wait is interposed exactly there (ADENDO 2): after the four
    # preceding statements, before the one straddle statement, never elsewhere.
    assert _query_sequence(connection) == [
        ("execute", "SELECT pg_catalog.pg_stat_clear_snapshot()"),
        ("fetchrow", measurement._VISIBILITY_SQL),
        ("fetchrow", measurement._CHECKPOINT_SQL),
        ("fetchrow", measurement._SNAPSHOT_SQL),
        ("sleep", measurement._T3_DEFERRED_SECONDS),
        ("fetchval", measurement._STRADDLE_SQL),
    ]
    assert connection.queries[-1][2] == (761,)
    assert connection.checkpoint_txid_param == 761
    # One real, bounded, protocol-derived wait of exactly Δ — awaited, not a
    # synchronous stub, and it is the whole added cost of the corner.
    assert waited == [(measurement._T3_DEFERRED_SECONDS, 4)]
    assert time.monotonic() - started >= measurement._T3_DEFERRED_SECONDS


@pytest.mark.parametrize(
    "straddle", ["in progress", "inprogress", "aborted", "subcommitted", None, "unknown"]
)
def test_in_gap_straddle_t3_other_verdicts_keep_exact_refusal(straddle, monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=[],
        straddle=straddle,
    )
    waited = _spy_sleep(monkeypatch, connection, delegate=False)
    with pytest.raises(runtime.NativeObservationError) as refused:
        _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    assert str(refused.value) == _IN_GAP_REFUSAL
    # T3 ran exactly once; every non-committed outcome is discarded unadmitted.
    assert _query_sequence(connection)[-1] == ("fetchval", measurement._STRADDLE_SQL)
    assert connection.queries[-1][2] == (761,)
    assert sum(1 for kind, _, _ in connection.queries if kind == "fetchval") == 1
    # The deferral is UNCONDITIONAL at the corner: it never inspects a verdict
    # before or after, so a non-committed T3' still waited the same Δ once
    # (agendamento incondicional limitado — never a retry/poll loop).
    assert waited == [(measurement._T3_DEFERRED_SECONDS, 4)]


def test_t3_deferred_wait_never_runs_outside_the_straddle_corner(monkeypatch):
    """ADENDO 2 scope: the wait exists ONLY on the in-gap corner.

    Legacy xip-present, legacy committed-in-T1 and the ENTRY checkpoint all
    reach acquire_pg_checkpoint without the corner, so asyncio.sleep must never
    be called — the deferral adds zero latency to every non-straddle path.
    """
    corners = [
        (
            "legacy-xip-present",
            _CheckpointConnection(
                checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
                xip=["761"],
            ),
            "COMMITTING",
            "761",
        ),
        (
            "legacy-committed-in-T1",
            _CheckpointConnection(
                checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="committed"),
                xip=[],
            ),
            "COMMITTING",
            "761",
        ),
        (
            "entry-unassigned",
            _CheckpointConnection(
                checkpoint_row=_checkpoint_row(backend_xid32=None, witness_status=None),
                xip=[],
            ),
            "ENTRY",
            None,
        ),
    ]
    for name, connection, endpoint, witness_txid in corners:
        waited = _spy_sleep(monkeypatch, connection, delegate=False)
        observed = []
        _spy_session(monkeypatch, observed)
        capture = _acquire(connection, _hold(endpoint=endpoint, witness_txid=witness_txid))
        assert name and waited == [], name
        assert not [entry for entry in connection.queries if entry[0] == "sleep"], name
        assert not [entry for entry in connection.queries if entry[0] == "fetchval"], name
        assert capture["backend_xid32"] == witness_txid, name


def test_legacy_xip_present_path_admits_without_executing_t3(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=["761"],
    )
    observed = []
    _spy_session(monkeypatch, observed)
    capture = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    # The xip match decides; the T1 verdict is passed through but never admits by
    # itself, and no T3 read exists on this path.
    assert observed == ["in progress"]
    assert not [entry for entry in connection.queries if entry[0] == "fetchval"]
    assert measurement.session(capture, witness="761")["transaction_id_if_assigned"] == "761"
    assert measurement.full_xid("761", ["761"], witness="761", live_status="in progress") == (
        "761",
        measurement.XID_SOURCE_IN_PROGRESS,
    )


def test_legacy_committed_in_t1_admits_without_executing_t3(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="committed"),
        xip=[],
    )
    observed = []
    _spy_session(monkeypatch, observed)
    capture = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    # v23 committed-in-T1 admission: same statement verdict, T3 never executes.
    assert observed == ["committed"]
    assert not [entry for entry in connection.queries if entry[0] == "fetchval"]
    assert measurement.session(capture, witness="761")["transaction_id_if_assigned"] == "761"


def test_entry_checkpoint_never_executes_t3(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32=None, witness_status=None),
        xip=[],
    )
    observed = []
    _spy_session(monkeypatch, observed)
    capture = _acquire(connection, _hold(endpoint="ENTRY", witness_txid=None))
    # ENTRY witnesses carry no assigned xid and no in-progress verdict, so the
    # straddle corner is structurally unreachable and T3 must never execute.
    assert observed == [None]
    assert not [entry for entry in connection.queries if entry[0] == "fetchval"]
    assert connection.checkpoint_txid_param is None
    assert capture["backend_xid32"] is None
    assert measurement.session(capture, witness=None)["transaction_id_if_assigned"] is None
