from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "dev" / "run_engine_integration.py"


def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _wait_for(path: Path, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {path}")


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_lock_contention_never_grants_cleanup_and_owner_releases_on_interrupt(tmp_path: Path) -> None:
    lock_dir = tmp_path / "engine.lock"
    release_file = tmp_path / "release-owner"
    owner_events = tmp_path / "owner.jsonl"
    contender_events = tmp_path / "contender.jsonl"
    owner = subprocess.Popen(
        [
            sys.executable,
            str(RUNNER),
            "lock-probe",
            "--lock-dir",
            str(lock_dir),
            "--events",
            str(owner_events),
            "--wait-for",
            str(release_file),
            "--timeout",
            "0",
        ],
        cwd=REPO_ROOT,
        text=True,
    )
    try:
        _wait_for(lock_dir / "owner.json")
        original_owner = (lock_dir / "owner.json").read_bytes()

        contender = _run(
            "lock-probe",
            "--lock-dir",
            str(lock_dir),
            "--events",
            str(contender_events),
            "--wait-for",
            str(tmp_path / "never"),
            "--timeout",
            "0",
        )

        assert contender.returncode == 73, contender.stderr
        assert (lock_dir / "owner.json").read_bytes() == original_owner
        assert [event["event"] for event in _events(contender_events)] == ["lock_busy"]

        owner.send_signal(signal.SIGINT)
        assert owner.wait(timeout=10) == 130
        assert not lock_dir.exists()
        owner_event_names = [event["event"] for event in _events(owner_events)]
        assert owner_event_names == [
            "lock_acquired",
            "interrupted",
            "cleanup_permitted",
            "lock_released",
        ]
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_lock_owner_releases_after_successful_real_subprocess(tmp_path: Path) -> None:
    lock_dir = tmp_path / "engine.lock"
    release_file = tmp_path / "release-owner"
    events_file = tmp_path / "events.jsonl"
    owner = subprocess.Popen(
        [
            sys.executable,
            str(RUNNER),
            "lock-probe",
            "--lock-dir",
            str(lock_dir),
            "--events",
            str(events_file),
            "--wait-for",
            str(release_file),
            "--timeout",
            "0",
        ],
        cwd=REPO_ROOT,
        text=True,
    )
    try:
        _wait_for(lock_dir / "owner.json")
        release_file.touch()
        assert owner.wait(timeout=10) == 0
        assert not lock_dir.exists()
        assert [event["event"] for event in _events(events_file)] == [
            "lock_acquired",
            "cleanup_permitted",
            "lock_released",
        ]
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_discovery_covers_every_current_integration_test_and_required_families(tmp_path: Path) -> None:
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    result = _run(
        "discover",
        "--checkout",
        str(REPO_ROOT),
        "--sha",
        sha,
        "--results-dir",
        str(tmp_path),
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    discovery = json.loads((tmp_path / "discovery.json").read_text())
    assert discovery["all_count"] > 0
    assert discovery["integration_count"] == discovery["all_count"]
    assert discovery["core_count"] + discovery["chaos_count"] == discovery["integration_count"]
    assert discovery["unmarked_nodeids"] == []
    assert discovery["overlap_nodeids"] == []
    assert discovery["required_families"]["lgpd"]
    assert discovery["required_families"]["escalation"]
    assert discovery["missing_test_files"] == []


def test_discovery_rejects_a_checkout_at_the_wrong_sha(tmp_path: Path) -> None:
    result = _run(
        "discover",
        "--checkout",
        str(REPO_ROOT),
        "--sha",
        "0" * 40,
        "--results-dir",
        str(tmp_path),
    )

    assert result.returncode == 64
    assert "SHA esperado" in result.stderr
    assert not (tmp_path / "discovery.json").exists()
