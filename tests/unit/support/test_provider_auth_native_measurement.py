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
