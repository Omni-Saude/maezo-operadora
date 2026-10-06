"""Selected grammar and snapshot counterproofs; no physical runtime claims."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support import provider_auth_native_measurement as measurement
from tests.support import provider_auth_native_runtime as runtime


def test_snapshot_mapping_uses_unique_real_full_xid_and_allows_unassigned():
    full = str(2**32 + 17)
    assert measurement.full_xid("17", [full]) == full
    assert measurement.full_xid(None, [full]) is None


@pytest.mark.parametrize(
    "xid,active",
    [
        ("17", []),
        ("17", ["18"]),
        ("17", ["17", str(2**32 + 17)]),
        ("17", ["17", "17"]),
        ("17", [str(2**64)]),
        (True, ["17"]),
    ],
)
def test_snapshot_missing_ambiguous_or_unbounded_xid_is_unknown(xid, active):
    with pytest.raises(runtime.NativeObservationError):
        measurement.full_xid(xid, active)


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
