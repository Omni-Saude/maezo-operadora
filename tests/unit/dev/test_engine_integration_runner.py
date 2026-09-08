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


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_lock_contention_never_grants_cleanup_and_owner_releases_on_interrupt(tmp_path: Path) -> None:
    lock_dir = tmp_path / "engine.lock"
    owner_events = tmp_path / "owner.jsonl"
    contender_events = tmp_path / "contender.jsonl"
    child_pid_file = tmp_path / "child.pid"
    child_log = tmp_path / "child.log"
    owner = subprocess.Popen(
        [
            sys.executable,
            str(RUNNER),
            "child-probe",
            "--lock-dir",
            str(lock_dir),
            "--events",
            str(owner_events),
            "--child-pid-file",
            str(child_pid_file),
            "--child-log",
            str(child_log),
            "--timeout",
            "60",
        ],
        cwd=REPO_ROOT,
        text=True,
    )
    try:
        _wait_for(lock_dir / "owner.json")
        _wait_for(child_pid_file)
        _wait_for(child_log)
        child_pid = int(child_pid_file.read_text())
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

        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=10) == 130
        assert not _pid_exists(child_pid)
        assert "child-started" in child_log.read_text()
        assert not lock_dir.exists()
        owner_event_names = [event["event"] for event in _events(owner_events)]
        assert owner_event_names == [
            "lock_acquired",
            "child_started",
            "interrupted",
            "cleanup_permitted",
            "lock_released",
        ]
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_child_timeout_preserves_partial_log_reaps_process_and_releases_lock(tmp_path: Path) -> None:
    lock_dir = tmp_path / "engine.lock"
    events_file = tmp_path / "events.jsonl"
    child_pid_file = tmp_path / "child.pid"
    child_log = tmp_path / "child.log"

    result = _run(
        "child-probe",
        "--lock-dir",
        str(lock_dir),
        "--events",
        str(events_file),
        "--child-pid-file",
        str(child_pid_file),
        "--child-log",
        str(child_log),
        "--timeout",
        "0.2",
    )

    child_pid = int(child_pid_file.read_text())
    assert result.returncode == 124
    assert not _pid_exists(child_pid)
    assert "child-started" in child_log.read_text()
    assert not lock_dir.exists()
    assert [event["event"] for event in _events(events_file)] == [
        "lock_acquired",
        "child_started",
        "child_timeout",
        "cleanup_permitted",
        "lock_released",
    ]


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
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
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
    assert discovery["integration_count"] > 0
    assert discovery["integration_dir_count"] > 0
    assert discovery["db_unit_count"] > 0
    assert (
        discovery["core_count"] + discovery["chaos_count"] + discovery["db_unit_count"]
        == discovery["integration_count"]
    )
    assert discovery["unmarked_nodeids"] == []
    assert discovery["overlap_nodeids"] == []
    assert discovery["required_families"]["lgpd"]
    assert discovery["required_families"]["escalation"]
    assert discovery["missing_test_files"] == []
    assert discovery["suite_dependencies"]["db-unit"]["engine_required"]
    assert (
        "tests/unit/gateway/test_audit_dmn_versions.py"
        in discovery["suite_dependencies"]["db-unit"]["engine_evidence"]
    )
    manifest_nodeids = {nodeid for entry in discovery["execution_manifest"] for nodeid in entry["nodeids"]}
    assert manifest_nodeids == set(discovery["integration_nodeids"])
    assert (
        sum(entry["expected_count"] for entry in discovery["execution_manifest"])
        == discovery["integration_count"]
    )
    assert all(entry["expected_count"] > 0 for entry in discovery["execution_manifest"])


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
