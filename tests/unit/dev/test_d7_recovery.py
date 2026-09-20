"""Offline recovery-helper controls; these never substitute for packaged engine acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.support.d7_recovery import (
    naive_utc_epoch_millis,
    preserve_restoration_errors,
    wait_authenticated_readiness,
)

DIGEST = "a" * 64


def ready(**changes: object) -> httpx.Response:
    return httpx.Response(
        200, json={"ready": True, "capabilities": ["b" * 64], "policy_digest": DIGEST, **changes}
    )


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError],
)
def test_transport_failure_is_pending_until_actual_readiness(tmp_path: Path, error: type[Exception]) -> None:
    attempts = []

    async def probe(phase_seconds: float) -> httpx.Response:
        attempts.append(phase_seconds)
        if len(attempts) == 1:
            raise error("private-canary must not enter evidence")
        return ready()

    asyncio.run(wait_authenticated_readiness(probe, lambda: DIGEST, tmp_path, budget=1, interval=0))
    records = [json.loads(path.read_text()) for path in sorted(tmp_path.glob("attempt-*.json"))]
    assert [row["outcome"] for row in records] == ["PENDING", "READY"]
    assert records[0]["transport_exception"] == error.__name__
    assert all(0 < value <= 1 for value in attempts)
    assert attempts[1] < attempts[0]
    assert "private-canary" not in repr(records)


def test_non_200_is_pending_and_cannot_pass(tmp_path: Path) -> None:
    responses = iter([httpx.Response(503, content=b"private-canary"), ready()])

    async def probe(phase_seconds: float) -> httpx.Response:
        return next(responses)

    asyncio.run(wait_authenticated_readiness(probe, lambda: DIGEST, tmp_path, budget=1, interval=0))
    first = json.loads((tmp_path / "attempt-0000.json").read_text())
    assert first["outcome"] == "PENDING" and first["status"] == 503
    assert first["body_bytes"] == len(b"private-canary")
    assert "private-canary" not in json.dumps(first)


@pytest.mark.parametrize(
    "body",
    [
        {"ready": False},
        {"ready": 1},
        {"capabilities": []},
        {"capabilities": {"fake": True}},
        {"capabilities": [None]},
        {"policy_digest": "c" * 64},
    ],
)
def test_incomplete_or_stale_200_never_establishes_readiness(tmp_path: Path, body: dict) -> None:
    async def probe(phase_seconds: float) -> httpx.Response:
        return ready(**body)

    with pytest.raises(AssertionError):
        asyncio.run(wait_authenticated_readiness(probe, lambda: DIGEST, tmp_path, budget=1))
    assert json.loads((tmp_path / "attempt-0000.json").read_text())["outcome"] == "FAILED"


def test_policy_change_during_probe_refuses(tmp_path: Path) -> None:
    digests = iter([DIGEST, "c" * 64])

    async def probe(phase_seconds: float) -> httpx.Response:
        return ready()

    with pytest.raises(AssertionError, match="current policy"):
        asyncio.run(wait_authenticated_readiness(probe, lambda: next(digests), tmp_path, budget=1))


def test_total_deadline_cancels_in_flight_probe(tmp_path: Path) -> None:
    cancelled = []

    async def probe(phase_seconds: float) -> httpx.Response:
        assert 0 < phase_seconds <= 0.02
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
        return ready()

    with pytest.raises(AssertionError, match="did not recover"):
        asyncio.run(wait_authenticated_readiness(probe, lambda: DIGEST, tmp_path, budget=0.02))
    assert cancelled == [True]
    record = json.loads((tmp_path / "attempt-0000.json").read_text())
    assert record["outcome"] == "PENDING" and record["transport_exception"] == "TimeoutError"


def test_primary_and_restoration_tracebacks_are_both_retained(tmp_path: Path) -> None:
    primary = AssertionError("primary-private")
    recovery = httpx.ConnectTimeout("restoration-private")

    def restore() -> None:
        raise recovery

    with pytest.raises(ExceptionGroup) as caught, preserve_restoration_errors(restore, tmp_path):
        raise primary
    assert caught.value.exceptions == (primary, recovery)
    assert primary.__traceback__ is not None and recovery.__traceback__ is not None
    assert json.loads((tmp_path / "phases.json").read_text()) == {
        "primary_exception_type": "AssertionError",
        "restoration_exception_type": "ConnectTimeout",
    }


@pytest.mark.parametrize("phase", ["primary", "restore", "neither"])
def test_single_failure_or_success_is_not_reclassified(tmp_path: Path, phase: str) -> None:
    restored = []
    failure = AssertionError("private")

    def restore() -> None:
        restored.append(True)
        if phase == "restore":
            raise failure

    def run() -> None:
        with preserve_restoration_errors(restore, tmp_path):
            if phase == "primary":
                raise failure

    if phase == "neither":
        run()
    else:
        with pytest.raises(AssertionError) as caught:
            run()
        assert caught.value is failure
    assert restored == [True]


def test_evidence_failure_cannot_mask_original_fault(tmp_path: Path) -> None:
    primary = AssertionError("original")
    with (
        pytest.raises(ExceptionGroup) as caught,
        preserve_restoration_errors(lambda: None, tmp_path / "missing"),
    ):
        raise primary
    assert caught.value.exceptions[0] is primary
    assert isinstance(caught.value.exceptions[1], FileNotFoundError)


@pytest.mark.parametrize("timezone", ["UTC", "America/Sao_Paulo"])
def test_native_utc_conversion_is_independent_of_host_timezone(timezone: str) -> None:
    script = """import time
from datetime import datetime
from tests.support.d7_recovery import naive_utc_epoch_millis
time.tzset()
assert naive_utc_epoch_millis(datetime(2026,9,15,4,14,43,58000)) == 1789445683058
"""
    subprocess.run([sys.executable, "-c", script], env={**os.environ, "TZ": timezone}, check=True)


def test_native_timestamp_contract_rejects_aware_or_submillisecond_values() -> None:
    with pytest.raises(AssertionError, match="naive"):
        naive_utc_epoch_millis(datetime(2026, 9, 15, tzinfo=UTC))
    with pytest.raises(AssertionError, match="millisecond"):
        naive_utc_epoch_millis(datetime(2026, 9, 15, microsecond=1))


def test_slow_initial_policy_read_cannot_start_a_probe_after_deadline(tmp_path: Path) -> None:
    import time

    probes = []

    def policy() -> str:
        time.sleep(0.05)
        return DIGEST

    async def probe(phase_seconds: float) -> httpx.Response:
        probes.append(phase_seconds)
        return ready()

    with pytest.raises(AssertionError):
        asyncio.run(wait_authenticated_readiness(probe, policy, tmp_path, budget=0.02))
    assert probes == []
    assert json.loads((tmp_path / "attempt-0000.json").read_text())["outcome"] != "READY"


def test_late_success_cannot_escape_absolute_deadline(tmp_path: Path) -> None:
    import time

    async def probe(phase_seconds: float) -> httpx.Response:
        # A blocking callback can delay asyncio cancellation; still refuse its late result.
        time.sleep(0.05)  # noqa: ASYNC251 - deliberate blocked-loop negative control
        return ready()

    with pytest.raises(AssertionError, match="after the recovery deadline"):
        asyncio.run(wait_authenticated_readiness(probe, lambda: DIGEST, tmp_path, budget=0.02))
    assert json.loads((tmp_path / "attempt-0000.json").read_text())["outcome"] == "FAILED"
