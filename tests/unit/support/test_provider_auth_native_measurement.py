"""Selected grammar and snapshot counterproofs; no physical runtime claims."""

from __future__ import annotations

import asyncio
import inspect
import re
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
# D2' (DESIGN-OPTION-REFUSAL-63-ROOT-v1 §4.2): a capture bound to an already
# dispatched COMMITTING RELEASE carries EXACTLY two more fields — the inverted
# certification pair. Any other key (verdicts included) stays out.
_RELEASE_BOUND_CAPTURE_KEYS = _CAPTURE_SCHEMA_KEYS + ("release_emitted_at", "release_raw_sha256")
# Unit fixtures for the inverted binding and the §6 fidelity block. The
# RELEASE dispatch time sits BEFORE the capture observation time so the bound
# fixtures reproduce the D2' ordering hold <= release <= capture.
_STRADDLE_READ_AT = datetime(2026, 10, 25, 1, 53, 27, 402000, tzinfo=UTC)
_RELEASE_EMITTED_AT = datetime(2026, 10, 25, 1, 53, 27, 127000, tzinfo=UTC)
_UNIT_HOLD_RAW = b'{"kind":"HOLD","unit":"fixture"}'
_UNIT_RELEASE_RAW = b'{"kind":"RELEASE","unit":"fixture"}'


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
        if query is measurement._STRADDLE_SQL:
            # The §6 fidelity read: exactly one statement returning the raw
            # verdict AND its PG clock. Absent corner config is a unit fault.
            if self._straddle is _UNIT_STRADDLE_UNSET:
                raise AssertionError("unit connection: unexpected straddle read (T3 must not run)")
            return dict(witness_xact_status=self._straddle, read_at=_STRADDLE_READ_AT)
        raise AssertionError(f"unit connection: unexpected fetchrow {query[:48]!r}")

    async def fetchval(self, query: str, *args: Any, **timeout: Any) -> Any:
        self.queries.append(("fetchval", query, args))
        raise AssertionError("unit connection: unexpected fetchval (T3 uses the fidelity row)")


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


def _capture_schema(*extra_keys: str) -> dict:
    keys = (*_CAPTURE_SCHEMA_KEYS, *extra_keys)
    return {
        "$defs": {
            "RootPgRawCapture": dict(
                type="object",
                properties={key: {} for key in keys},
                required=list(_CAPTURE_SCHEMA_KEYS),
                additionalProperties=False,
            )
        }
    }


def _spy_session(monkeypatch: pytest.MonkeyPatch, observed: list) -> None:
    real = measurement.session

    def spy(
        capture,
        *,
        witness,
        live_status=measurement._LIVE_STATUS_UNSET,
        deferred_t3=measurement._T3_NOT_RUN,
    ):
        observed.append(live_status)
        return real(capture, witness=witness, live_status=live_status, deferred_t3=deferred_t3)

    monkeypatch.setattr(measurement, "session", spy)


def _acquire(
    connection: _CheckpointConnection, hold: dict, *, schema: dict | None = None
) -> tuple[dict, dict | None]:
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=30))
    return asyncio.run(
        measurement.acquire_pg_checkpoint(
            connection,
            hold=hold,
            hold_raw=_UNIT_HOLD_RAW,
            phase="ROOT_SECOND",
            guard=guard,
            schema=_capture_schema() if schema is None else schema,
        )
    )


def _acquire_bound(connection: _CheckpointConnection, hold: dict) -> tuple[dict, dict | None]:
    """Same acquisition with an already dispatched early RELEASE (D2' COMMITTING)."""
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=30))
    release = dict(
        kind="RELEASE",
        endpoint=hold["endpoint"],
        emitted_at=runtime.clock(_RELEASE_EMITTED_AT),
    )
    return asyncio.run(
        measurement.acquire_pg_checkpoint(
            connection,
            hold=hold,
            hold_raw=_UNIT_HOLD_RAW,
            phase="ROOT_SECOND",
            guard=guard,
            schema=_capture_schema("release_emitted_at", "release_raw_sha256"),
            release=release,
            release_raw=_UNIT_RELEASE_RAW,
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
    capture, fidelity = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
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
        ("fetchrow", measurement._STRADDLE_SQL),
    ]
    assert connection.queries[-1][2] == (761,)
    assert connection.checkpoint_txid_param == 761
    # One real, bounded, protocol-derived wait of exactly Δ — awaited, not a
    # synchronous stub, and it is the whole added cost of the corner.
    assert waited == [(measurement._T3_DEFERRED_SECONDS, 4)]
    assert time.monotonic() - started >= measurement._T3_DEFERRED_SECONDS
    # §6 fidelity: the raw verdict, its PG clock and the EFFECTIVELY waited Δ
    # come back for the acquisition record — never inside the capture document.
    assert fidelity is not None
    assert set(fidelity) == {"t3_straddle_raw_status", "t3_straddle_read_at", "t3_deferred_seconds"}
    assert fidelity["t3_straddle_raw_status"] == "committed"
    assert fidelity["t3_straddle_read_at"] == runtime.clock(_STRADDLE_READ_AT)
    whole, nanos = fidelity["t3_deferred_seconds"].split(".")
    assert len(nanos) == 9 and whole == "0"


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
    # §6: the refusal text now carries the raw T3' verdict verbatim (the
    # discriminator between a slow live transaction and an aborted one) and
    # still never waives.
    assert str(refused.value) == (
        f"Actual witness clog in progress without xip candidacy (non-decisive; deferred T3 read {straddle!r})"
    )
    # §6-1 completed (P4-RESULT-v1 §7-2, owner decision 2026-10-26): the corner
    # MEASURED, so the refusal RECORD banks the fidelity block — the effectively
    # waited Δ (monotonic) is never discarded in the raise, whatever the
    # verdict, and the refusal keeps the exact text above (never waived).
    fidelity = refused.value.t3_fidelity
    assert set(fidelity) == {"t3_straddle_raw_status", "t3_straddle_read_at", "t3_deferred_seconds"}
    assert fidelity["t3_straddle_raw_status"] == straddle
    assert fidelity["t3_straddle_read_at"] == runtime.clock(_STRADDLE_READ_AT)
    assert re.fullmatch(r"[0-9]+\.[0-9]{9}", fidelity["t3_deferred_seconds"])
    assert float(fidelity["t3_deferred_seconds"]) >= 0.0
    # P0' (design §10.6): the refusal record also carries the declarative commit
    # provenance, pinned verbatim — an eventual post-refusal COMMIT in the
    # witness state_change is the unwind's implicit commit, and the honest
    # engine-commit discriminator is SEAL+STAGE_CLOSED (ordinals 7/8 under D2').
    assert refused.value.refusal_commit_provenance == measurement.REFUSAL_COMMIT_PROVENANCE
    assert measurement.REFUSAL_COMMIT_PROVENANCE == {
        "post_refusal_commit_witness": (
            "jdbc-transaction-resetautocommit-implicit-commit-on-aborted-command-unwind"
        ),
        "engine_commit_discriminator": "seal-and-stage-closed-committing-frames-7-and-8",
    }
    # T3 ran exactly once; every non-committed outcome is discarded unadmitted.
    assert _query_sequence(connection)[-1] == ("fetchrow", measurement._STRADDLE_SQL)
    assert connection.queries[-1][2] == (761,)
    assert sum(1 for kind, _, _ in connection.queries if kind == "fetchrow") == 4
    # The deferral is UNCONDITIONAL at the corner: it never inspects a verdict
    # before or after, so a non-committed T3' still waited the same Δ once
    # (agendamento incondicional limitado — never a retry/poll loop).
    assert waited == [(measurement._T3_DEFERRED_SECONDS, 4)]


def test_checkpoint_refusal_without_corner_banks_provenance_and_honest_none(monkeypatch):
    """P0' boundary: EVERY checkpoint refusal is a refusal record.

    The congruence refusal below fires BEFORE the corner (xact_start already
    NULL, fail-closed §4.2-riscos-2), so nothing was measured: t3_fidelity is
    None — an honest absence, never a fabricated measurement — while the
    declarative P0' commit provenance still rides the refusal, because the
    abort unwind (and its implicit commit) is the physics of every refused
    acquisition. No wait and no straddle read exist on this path.
    """
    connection = _CheckpointConnection(
        checkpoint_row=dict(
            _checkpoint_row(backend_xid32=None, witness_status="committed"), transaction_started_at=None
        ),
        xip=[],
    )
    waited = _spy_sleep(monkeypatch, connection, delegate=False)
    with pytest.raises(runtime.NativeObservationError) as refused:
        _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    assert str(refused.value) == "Root PG tuple differs from held enlisted session"
    assert refused.value.t3_fidelity is None
    assert refused.value.refusal_commit_provenance == measurement.REFUSAL_COMMIT_PROVENANCE
    assert waited == []
    assert not [entry for entry in connection.queries if entry[0] == "sleep"]


def test_post_corner_refusal_still_banks_the_measured_fidelity(monkeypatch):
    """§6-1: a refusal AFTER the corner ran keeps the measured fidelity too."""
    hold = _hold(endpoint="COMMITTING", witness_txid="761")
    hold["measurement"]["stable_database_projection"] = dict(
        database_name="maezo_n2",
        database_oid="16384",
        system_identifier="7398612345678901",
        session_user="someone_else",
    )
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=[],
        straddle="committed",
    )
    _spy_sleep(monkeypatch, connection, delegate=False)
    with pytest.raises(runtime.NativeObservationError) as refused:
        _acquire(connection, hold)
    assert str(refused.value) == "Root PG database/login differs"
    fidelity = refused.value.t3_fidelity
    assert fidelity is not None
    assert set(fidelity) == {"t3_straddle_raw_status", "t3_straddle_read_at", "t3_deferred_seconds"}
    assert fidelity["t3_straddle_raw_status"] == "committed"
    assert fidelity["t3_straddle_read_at"] == runtime.clock(_STRADDLE_READ_AT)
    assert refused.value.refusal_commit_provenance == measurement.REFUSAL_COMMIT_PROVENANCE


def test_corner_free_refusal_keeps_the_exact_never_waived_text():
    """§6 boundary: WITHOUT the corner the exact historic refusal text stands."""
    with pytest.raises(runtime.NativeObservationError, match=r".*never waived\)$"):
        measurement.full_xid("761", [], witness="761", live_status="in progress")
    with pytest.raises(runtime.NativeObservationError, match="deferred T3 read 'aborted'"):
        measurement.full_xid("761", [], witness="761", live_status="in progress", deferred_t3="aborted")


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
        capture, _fidelity = _acquire(connection, _hold(endpoint=endpoint, witness_txid=witness_txid))
        assert name and waited == [], name
        assert not [entry for entry in connection.queries if entry[0] == "sleep"], name
        straddles = [
            entry
            for entry in connection.queries
            if entry[0] == "fetchrow" and entry[1] is measurement._STRADDLE_SQL
        ]
        assert not straddles, name
        assert capture["backend_xid32"] == witness_txid, name


def test_legacy_xip_present_path_admits_without_executing_t3(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=["761"],
    )
    observed = []
    _spy_session(monkeypatch, observed)
    capture, _fidelity = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    # The xip match decides; the T1 verdict is passed through but never admits by
    # itself, and no T3 read exists on this path.
    assert observed == ["in progress"]
    assert not [
        entry
        for entry in connection.queries
        if entry[0] == "fetchrow" and entry[1] is measurement._STRADDLE_SQL
    ]
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
    capture, _fidelity = _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    # v23 committed-in-T1 admission: same statement verdict, T3 never executes.
    assert observed == ["committed"]
    assert not [
        entry
        for entry in connection.queries
        if entry[0] == "fetchrow" and entry[1] is measurement._STRADDLE_SQL
    ]
    assert measurement.session(capture, witness="761")["transaction_id_if_assigned"] == "761"


def test_entry_checkpoint_never_executes_t3(monkeypatch):
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32=None, witness_status=None),
        xip=[],
    )
    observed = []
    _spy_session(monkeypatch, observed)
    capture, _fidelity = _acquire(connection, _hold(endpoint="ENTRY", witness_txid=None))
    # ENTRY witnesses carry no assigned xid and no in-progress verdict, so the
    # straddle corner is structurally unreachable and T3 must never execute.
    assert observed == [None]
    assert not [
        entry
        for entry in connection.queries
        if entry[0] == "fetchrow" and entry[1] is measurement._STRADDLE_SQL
    ]
    assert connection.checkpoint_txid_param is None
    assert capture["backend_xid32"] is None
    assert measurement.session(capture, witness=None)["transaction_id_if_assigned"] is None


# ---------------------------------------------------------------------------
# D2' (DESIGN-OPTION-REFUSAL-63-ROOT-v1 §4.2/§5.2-P3/§6/§10.8): RELEASE-imediato
# no COMMITTING, binding captura->RELEASE invertido, reparo de fidelidade do T3'
# e o Delta deferido lido de env na importacao (conjunto fechado).
# ---------------------------------------------------------------------------


def test_capture_between_pair_phases_after_commit_refuses_never_adheres(monkeypatch):
    """P3-(i): the commit landing between the two pair captures is fail-closed.

    The first capture still finds the held transaction in progress (admitted);
    the second one reads the backend AFTER the commit — xact_start NULL — and
    must refuse on the held-session congruence instead of adhering. The exact
    pair-level guard lives in collect() (Linux lane); its refusal text is
    pinned here so it can never silently disappear.
    """
    first = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=["761"],
    )
    capture, _fidelity = _acquire(first, _hold(endpoint="COMMITTING", witness_txid="761"))
    assert capture["backend_xid32"] == "761"
    second = _CheckpointConnection(
        checkpoint_row=dict(
            _checkpoint_row(backend_xid32=None, witness_status="committed"), transaction_started_at=None
        ),
        xip=[],
    )
    _spy_sleep(monkeypatch, second, delegate=False)
    with pytest.raises(runtime.NativeObservationError, match="Root PG tuple differs from held"):
        _acquire(second, _hold(endpoint="COMMITTING", witness_txid="761"))
    source = Path(measurement.__file__).read_text()
    assert "Held session changed between fresh ROOT snapshots" in source


def test_post_commit_capture_with_committed_clog_refuses_never_false_adhesion(monkeypatch):
    """P3-(ii): a fully post-commit capture is refused, never re-attached."""
    connection = _CheckpointConnection(
        checkpoint_row=dict(
            _checkpoint_row(backend_xid32=None, witness_status="committed"), transaction_started_at=None
        ),
        xip=[],
    )
    _spy_sleep(monkeypatch, connection, delegate=False)
    with pytest.raises(runtime.NativeObservationError) as refused:
        _acquire(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    assert str(refused.value) == "Root PG tuple differs from held enlisted session"
    # No artifact-shaped capture escaped and the clog path alone never re-attaches
    # the ended session: the congruence tuple (xact_start) is the discriminator.
    assert connection.checkpoint_txid_param == 761


def test_committed_capture_bound_to_dispatched_release_carries_inverted_certification(monkeypatch):
    """P3-(iv): the D2' capture references the RELEASE it is certified against."""
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"),
        xip=["761"],
    )
    capture, _fidelity = _acquire_bound(connection, _hold(endpoint="COMMITTING", witness_txid="761"))
    assert set(capture) == set(_RELEASE_BOUND_CAPTURE_KEYS)
    assert capture["release_raw_sha256"] == measurement.sha(_UNIT_RELEASE_RAW)
    assert capture["release_emitted_at"] == runtime.clock(_RELEASE_EMITTED_AT)
    # Banked ordering proof, capture side: release <= capture root_observed_at
    # (the HOLD side is bound by hold_raw_sha256 and checked by the replay).
    assert runtime._observation_time(capture["release_emitted_at"]) <= runtime._observation_time(
        capture["root_observed_at"]
    )
    # The unbound (ENTRY) shape stays byte-stable: neither field ever appears.
    unbound, _fidelity = _acquire(
        _CheckpointConnection(
            checkpoint_row=_checkpoint_row(backend_xid32=None, witness_status=None), xip=[]
        ),
        _hold(endpoint="ENTRY", witness_txid=None),
    )
    assert set(unbound) == set(_CAPTURE_SCHEMA_KEYS)
    assert "release_raw_sha256" not in unbound and "release_emitted_at" not in unbound


def test_inverted_binding_half_pair_or_foreign_release_refuses():
    guard = runtime._ObservationWaitGuard.start(datetime.now(UTC) + timedelta(seconds=30))
    hold = _hold(endpoint="COMMITTING", witness_txid="761")
    connection = _CheckpointConnection(
        checkpoint_row=_checkpoint_row(backend_xid32="761", witness_status="in progress"), xip=["761"]
    )
    for kwargs in (
        {"release": dict(kind="RELEASE")},
        {"release_raw": _UNIT_RELEASE_RAW},
        {"release": dict(kind="SEAL"), "release_raw": _UNIT_RELEASE_RAW},
    ):
        with pytest.raises(runtime.NativeObservationError):
            asyncio.run(
                measurement.acquire_pg_checkpoint(
                    connection,
                    hold=hold,
                    hold_raw=_UNIT_HOLD_RAW,
                    phase="ROOT_SECOND",
                    guard=guard,
                    schema=_capture_schema(),
                    **kwargs,
                )
            )


def test_collect_commits_to_the_d2prime_release_order_on_committing():
    """GATE-T2 F1: the inversion ORDER in collect() is pinned by a canary.

    The unit lane cannot run collect() (Linux UDS custody chain: kernel peer
    credentials, exclusive group, uid custody), so the order is pinned in the
    source-text idiom already used for the pair guard above. Reverting the
    decision line to the v26 behaviour (early_release = False) MUST fail this
    test: on COMMITTING the RELEASE is dispatched BEFORE the two captures and
    carries NO capture digests, the captures are bound to the dispatched
    RELEASE (release_raw_sha256/release_emitted_at, both phases), and the
    ENTRY keeps the original order (release built after its captures).
    """
    source = inspect.getsource(measurement.NativeMeasurementCollector.collect)
    # The D2' decision line itself: mutated away by a revert to v26.
    assert 'early_release = endpoint == "COMMITTING"' in source
    early_block = source.index("if early_release:")
    phase_loop = source.index('for phase in ("ROOT_FIRST", "ROOT_SECOND"):')
    late_block = source.index("if not early_release:")
    # HOLD -> RELEASE dispatched -> captures -> ENTRY release, in source order.
    assert early_block < phase_loop < late_block
    early_send = source.index('self._event(release, release_raw, "ROOT_TO_JVM")', early_block)
    assert early_send < phase_loop
    # Both captures bind to the dispatched RELEASE exactly when it is early.
    binding = source.index("release=release if early_release else None")
    raw_binding = source.index("release_raw=release_raw if early_release else None")
    assert phase_loop < binding < late_block
    assert phase_loop < raw_binding < late_block
    # The early RELEASE carries NO capture digests: each digest key is assigned
    # exactly once in collect(), inside the ENTRY (not early) branch.
    assert source.count("root_capture_before_raw_sha256=") == 1
    assert source.count("root_capture_after_raw_sha256=") == 1
    assert source.index("root_capture_before_raw_sha256=") > late_block
    assert source.index("root_capture_after_raw_sha256=") > late_block


class TestT3DeferredEnvClosedSet:
    """§10.8 main side: the Δ is read ONCE at import from a CLOSED set."""

    _ENV = "MAEZO_NATIVE_T3_DEFERRED_SECONDS"

    @pytest.fixture(autouse=True)
    def _restore_import_default(self):
        yield
        import importlib
        import os

        os.environ.pop(self._ENV, None)
        importlib.reload(measurement)

    def _reload(self, monkeypatch, value):
        import importlib

        if value is None:
            monkeypatch.delenv(self._ENV, raising=False)
        else:
            monkeypatch.setenv(self._ENV, value)
        return importlib.reload(measurement)

    @pytest.mark.parametrize("raw,expected", [(None, 0.2), ("0", 0.0), ("0.2", 0.2)])
    def test_absent_or_closed_set_values_resolve(self, monkeypatch, raw, expected):
        module = self._reload(monkeypatch, raw)
        assert expected == module._T3_DEFERRED_SECONDS

    @pytest.mark.parametrize("raw", ["0.5", "0.20", " 0.2", "-1", "abc", "", "1", "0.2000000"])
    def test_any_other_value_refuses_loud_at_import(self, monkeypatch, raw):
        import importlib

        monkeypatch.setenv(self._ENV, raw)
        with pytest.raises(runtime.NativeObservationError, match=self._ENV):
            importlib.reload(measurement)
        # The refusal is the import-time state: the module is NOT left bound to
        # the refused value under any silent fallback.
        import os

        os.environ.pop(self._ENV, None)
        assert importlib.reload(measurement)._T3_DEFERRED_SECONDS == 0.2

    def test_runtime_reads_the_module_attribute_not_a_literal(self):
        # The single wait site consumes the attribute, so the env decision and
        # the executed wait can never diverge within one process.
        assert measurement._T3_DEFERRED_SECONDS in (0.0, 0.2)


class TestT3DeferredV28D42ClosedSet:
    """D-42 (owner decision 2026-10-26, route (a) of P4-RESULT-v1 §6).

    The v28 regime admits exactly ONE new member: the literal "4" — a 4 s
    deferred read crosses the measured post-RELEASE custody tail of the
    COMMITTING endpoint (3.174540 s at Δ=0, 3.217283 s at Δ=0.2) with margin,
    where the v27 set left the T3' read landing 15-16x before the tail ends.
    The set stays closed, the default stays the pinned "0.2", and the literal
    style stays integer-without-decimal for integral values, so every other
    spelling of four (and everything else) still refuses loudly at import.
    """

    _ENV = "MAEZO_NATIVE_T3_DEFERRED_SECONDS"

    @pytest.fixture(autouse=True)
    def _restore_import_default(self):
        yield
        import importlib
        import os

        os.environ.pop(self._ENV, None)
        importlib.reload(measurement)

    def _reload(self, monkeypatch, value):
        import importlib

        if value is None:
            monkeypatch.delenv(self._ENV, raising=False)
        else:
            monkeypatch.setenv(self._ENV, value)
        return importlib.reload(measurement)

    def test_v28_closed_set_is_exactly_the_three_regime_members(self):
        assert frozenset({"0", "0.2", "4"}) == measurement._T3_DEFERRED_ALLOWED

    @pytest.mark.parametrize("raw,expected", [("4", 4.0), ("0.2", 0.2), ("0", 0.0), (None, 0.2)])
    def test_v28_members_resolve_and_absent_env_keeps_the_pinned_default(self, monkeypatch, raw, expected):
        module = self._reload(monkeypatch, raw)
        assert expected == module._T3_DEFERRED_SECONDS

    @pytest.mark.parametrize(
        "raw",
        [
            "4.0",  # integral values are spelled without a decimal, like "0"
            "4s",
            " 4",
            "4 ",
            "0.4",
            "-4",
            "abc",
            "",
        ],
    )
    def test_v28_every_other_value_still_refuses_loud_at_import(self, monkeypatch, raw):
        import importlib

        monkeypatch.setenv(self._ENV, raw)
        with pytest.raises(runtime.NativeObservationError, match=self._ENV):
            importlib.reload(measurement)
        # Fail-closed: after the refused import the module is restored to the
        # pinned default, never left bound to the refused value.
        module = self._reload(monkeypatch, None)
        assert module._T3_DEFERRED_SECONDS == 0.2


# ---------------------------------------------------------------------------
# Schema v3 contract, main side: the early RELEASE document, the inverted
# capture and the acquisition record shapes validated against the REAL
# versioned schema file (the same bytes the sealed JVM loads).
# ---------------------------------------------------------------------------

_UNIT_UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _real_schema() -> dict:
    import json

    path = Path(measurement.__file__).resolve().parents[2]
    return json.loads(
        (path / "src/maezo/portal/engine/java/src/main/resources")
        .joinpath("provider-native-independent-measurement-schema-v1.json")
        .read_text()
    )


def _release_document(endpoint: str, **extra: Any) -> dict:
    value = dict(
        schema="provider-native-independent-measurement-frame.v2",
        event_ordinal=4,
        session_nonce="a" * 32,
        request_sha256="b" * 64,
        config_sha256="c" * 64,
        emitted_at=runtime.clock(_RELEASE_EMITTED_AT),
        original_deadline=runtime.clock(datetime.now(UTC) + timedelta(minutes=10)),
        kind="RELEASE",
        stage_sequence=0,
        server_stage_execution_uuid=_UNIT_UUID,
        endpoint=endpoint,
        hold_raw_sha256="d" * 64,
    )
    value.update(extra)
    return value


def _capture_document(*, bound: bool) -> dict:
    value = {
        "schema": "provider-native-root-pg-checkpoint-capture.v1",
        "source": "root-independent-pg-stat-snapshot-and-ssl-query",
        "root_observed_at": runtime.clock(_OBSERVED_AT),
        "collector_uid": 1001,
        "collector_pid_namespace": 42,
        "observer_session_user": "root_observer",
        "server_version": "16.15",
        "stats_visibility_qualified": True,
        "track_activities_observed_on": True,
        "stage_sequence": 0,
        "server_stage_execution_uuid": _UNIT_UUID,
        "endpoint": "COMMITTING",
        "phase": "ROOT_FIRST",
        "hold_raw_sha256": "d" * 64,
        "database_name": "maezo_n2",
        "database_oid": "16384",
        "system_identifier": "7398612345678901",
        "backend_pid": "81",
        "backend_start": runtime.clock(_OBSERVED_AT),
        "transaction_started_at": runtime.clock(_OBSERVED_AT),
        "backend_xid32": "761",
        "active_full_xids": ["761"],
        "session_login": "maezo_jvm",
        "client_address": "127.0.0.1/32",
        "client_port": 49152,
        "backend_ssl": True,
        "backend_tls_version": "TLSv1.3",
        "query_schema": "fixed-root-pg-checkpoint-query.v1",
        "query_source_sha256": "e" * 64,
    }
    if bound:
        value["release_raw_sha256"] = "f" * 64
        value["release_emitted_at"] = runtime.clock(_RELEASE_EMITTED_AT)
    return value


def _record_item(*, fidelity: bool) -> dict:
    value = dict(
        stage_sequence=0,
        server_stage_execution_uuid=_UNIT_UUID,
        endpoint="COMMITTING",
        phase="ROOT_FIRST",
        raw_artifact=dict(
            path="/unit/measurement-" + "a" * 32 + "/pg-s00-COMMITTING-ROOT_FIRST.json",
            sha256="1" * 64,
            size_bytes=256,
        ),
        hold_to_release_ns="12000000",
    )
    if fidelity:
        value.update(
            t3_straddle_raw_status="committed",
            t3_straddle_read_at=runtime.clock(_STRADDLE_READ_AT),
            t3_deferred_seconds="0.200123456",
        )
    return value


def test_schema_v3_admits_the_early_release_and_the_inverted_capture():
    schema = _real_schema()
    # The early COMMITTING RELEASE carries NO capture digests and validates.
    measurement.validate(schema, "RELEASE", _release_document("COMMITTING"))
    # The ENTRY release keeps its original embedding shape.
    measurement.validate(
        schema,
        "RELEASE",
        _release_document(
            "ENTRY",
            root_capture_before_raw_sha256="1" * 64,
            root_capture_after_raw_sha256="2" * 64,
        ),
    )
    measurement.validate(schema, "RootPgRawCapture", _capture_document(bound=True))
    measurement.validate(schema, "RootPgRawCapture", _capture_document(bound=False))
    measurement.validate(schema, "RootPgCaptureRef", _record_item(fidelity=True))
    measurement.validate(schema, "RootPgCaptureRef", _record_item(fidelity=False))


def test_schema_v3_refuses_foreign_frames_and_dropped_bindings():
    schema = _real_schema()
    with pytest.raises(runtime.NativeObservationError, match="RELEASE shape refused"):
        measurement.validate(schema, "RELEASE", _release_document("COMMITTING", foreign_field="x"))
    early = _release_document("COMMITTING")
    del early["hold_raw_sha256"]
    with pytest.raises(runtime.NativeObservationError, match="RELEASE shape refused"):
        measurement.validate(schema, "RELEASE", early)
    capture = _capture_document(bound=True)
    capture["t3_straddle_raw_status"] = "committed"
    with pytest.raises(runtime.NativeObservationError, match="RootPgRawCapture shape refused"):
        # The raw verdict NEVER enters the capture document (§8-4).
        measurement.validate(schema, "RootPgRawCapture", capture)
    item = _record_item(fidelity=False)
    del item["hold_to_release_ns"]
    with pytest.raises(runtime.NativeObservationError, match="RootPgCaptureRef shape refused"):
        measurement.validate(schema, "RootPgCaptureRef", item)
    leaked = _record_item(fidelity=False)
    leaked["t3_straddle_raw_status"] = "in progress"
    with pytest.raises(runtime.NativeObservationError, match="RootPgCaptureRef shape refused"):
        # Only the exact committed literal ever reaches the record (never-waived).
        measurement.validate(schema, "RootPgCaptureRef", leaked)


def test_hold_to_release_ns_registration_is_exact_integer_arithmetic():
    hold = dict(emitted_at=runtime.clock(datetime(2026, 10, 25, 1, 53, 27, 128000, tzinfo=UTC)))
    release = dict(emitted_at=runtime.clock(datetime(2026, 10, 25, 1, 53, 27, 372000, tzinfo=UTC)))
    assert measurement.hold_to_release_ns(hold, release) == "244000000"
    # A reversed clock order refuses instead of banking a negative latency.
    with pytest.raises(runtime.NativeObservationError, match="clock order"):
        measurement.hold_to_release_ns(release, hold)
