from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
from scripts.dev import run_engine_integration as runner
from scripts.dev.run_engine_integration import (
    _compose_base,
    _runtime_env,
    verified_result_code,
)
from scripts.dev.run_engine_integration import (
    _run as run_supervised,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "dev" / "run_engine_integration.py"


def _run(
    *args: str,
    timeout: float = 30.0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(extra_env or {})
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


def _kill_probe_group(group_id: int) -> None:
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(group_id, signal.SIGKILL)


def _descendant_probe_code() -> str:
    return """
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

directory = Path(sys.argv[1])
directory.joinpath("parent.pid").write_text(str(os.getpid()))
grandchild = '''
import os
from pathlib import Path
import signal
import sys
import time

directory = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
directory.joinpath("grandchild.pid").write_text(str(os.getpid()))
print("grandchild-started", flush=True)
while True:
    directory.joinpath("heartbeat").write_text(str(time.time_ns()))
    time.sleep(0.02)
'''
subprocess.Popen([sys.executable, "-c", grandchild, str(directory)])
deadline = time.monotonic() + 10
while not directory.joinpath("grandchild.pid").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("grandchild did not start")
    time.sleep(0.02)
print("parent-started", flush=True)
if sys.argv[2] == "sleep":
    time.sleep(3600)
"""


@pytest.mark.parametrize("parent_mode,timeout,expected_rc", [("sleep", 0.2, 124), ("exit", 5.0, 0)])
def test_supervisor_reaps_term_resistant_grandchild_before_return(
    tmp_path: Path, parent_mode: str, timeout: float, expected_rc: int
) -> None:
    log = tmp_path / "partial.log"
    parent_pid = 0
    grandchild_pid = 0
    try:
        result = run_supervised(
            [sys.executable, "-c", _descendant_probe_code(), str(tmp_path), parent_mode],
            cwd=REPO_ROOT,
            timeout=timeout,
            log_path=log,
        )
        parent_pid = int((tmp_path / "parent.pid").read_text())
        grandchild_pid = int((tmp_path / "grandchild.pid").read_text())
        heartbeat = (tmp_path / "heartbeat").read_text()
        time.sleep(0.1)

        assert result.returncode == expected_rc
        assert not _pid_exists(parent_pid)
        assert not _pid_exists(grandchild_pid)
        assert (tmp_path / "heartbeat").read_text() == heartbeat
        assert "grandchild-started" in log.read_text()
    finally:
        if parent_pid:
            _kill_probe_group(parent_pid)
        elif (tmp_path / "parent.pid").exists():
            _kill_probe_group(int((tmp_path / "parent.pid").read_text()))


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


def test_discovery_ignores_ambient_pytest_selectors_and_plugins(tmp_path: Path) -> None:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    baseline_dir = tmp_path / "baseline"
    baseline = _run(
        "discover",
        "--checkout",
        str(REPO_ROOT),
        "--sha",
        sha,
        "--results-dir",
        str(baseline_dir),
        timeout=120,
    )
    assert baseline.returncode == 0, baseline.stderr
    expected = json.loads((baseline_dir / "discovery.json").read_text())["integration_nodeids"]

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "foreign_selection_plugin.py").write_text(
        "def pytest_collection_modifyitems(items):\n"
        "    items[:] = [item for item in items if 'alerta_interno_nao_interruptivo' not in item.nodeid]\n"
    )
    polluted_dir = tmp_path / "polluted"
    polluted = _run(
        "discover",
        "--checkout",
        str(REPO_ROOT),
        "--sha",
        sha,
        "--results-dir",
        str(polluted_dir),
        timeout=120,
        extra_env={
            "PYTEST_ADDOPTS": "-k not\\ test_alerta_interno_nao_interruptivo",
            "PYTHONPATH": str(plugin_dir),
            "PYTEST_PLUGINS": "foreign_selection_plugin",
        },
    )

    assert polluted.returncode == 0, polluted.stderr
    actual = json.loads((polluted_dir / "discovery.json").read_text())["integration_nodeids"]
    assert actual == expected


def test_discovery_rejects_untracked_test_outside_integration_directory(tmp_path: Path) -> None:
    injected = REPO_ROOT / "tests" / "unit" / "test_runner_untracked_probe.py"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    try:
        injected.write_text("import pytest\npytestmark = pytest.mark.integration\ndef test_probe(): pass\n")
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
        assert result.returncode == 64
        assert "untracked" in result.stderr
        assert not (tmp_path / "discovery.json").exists()
    finally:
        injected.unlink(missing_ok=True)


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


def test_busy_lock_return_code_matches_durable_run_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "engine.lock"
    owner = runner.EngineLock.create(lock_dir, checkout="owner", sha="owner", suite="owner")
    assert owner.acquire(0)
    original_owner = owner.owner_path.read_bytes()
    monkeypatch.setattr(runner, "LOCK_DIR", lock_dir)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    args = runner.build_parser().parse_args(
        [
            "run",
            "--checkout",
            str(REPO_ROOT),
            "--sha",
            sha,
            "--suite",
            "core",
            "--test-file",
            "tests/integration/processes/test_sp_op_lgpd_dsr_001.py",
            "--results-dir",
            str(tmp_path / "results"),
            "--lock-timeout",
            "0",
        ]
    )
    try:
        return_code = runner.run_suite(args)
        state = json.loads((tmp_path / "results" / "run-state.json").read_text())
        assert return_code == 73
        assert state["state"] == "lock_busy"
        assert state["return_code"] == return_code
        assert owner.owner_path.read_bytes() == original_owner
        assert not (tmp_path / "results" / "teardown.log").exists()
    finally:
        assert owner.release()


def test_junit_empty_or_partial_cannot_turn_into_a_successful_suite() -> None:
    assert verified_result_code(
        0,
        actual_count=4,
        expected_count=4,
        passed_count=3,
        skipped_count=0,
        xfailed_count=1,
        xpassed_count=0,
        test_file="tests/x.py",
    ) == (0, None)
    partial_rc, partial_error = verified_result_code(
        0,
        actual_count=3,
        expected_count=4,
        passed_count=3,
        skipped_count=0,
        xfailed_count=0,
        xpassed_count=0,
        test_file="tests/x.py",
    )
    empty_rc, empty_error = verified_result_code(
        0,
        actual_count=0,
        expected_count=4,
        passed_count=0,
        skipped_count=0,
        xfailed_count=0,
        xpassed_count=0,
        test_file="tests/x.py",
    )
    skipped_rc, skipped_error = verified_result_code(
        0,
        actual_count=4,
        expected_count=4,
        passed_count=3,
        skipped_count=1,
        xfailed_count=0,
        xpassed_count=0,
        test_file="tests/x.py",
    )

    assert partial_rc == 1
    assert "executou 3" in str(partial_error)
    assert empty_rc == 1
    assert "executou 0" in str(empty_error)
    assert skipped_rc == 1
    assert "skip inesperado" in str(skipped_error)


def test_runtime_env_pins_endpoints_and_drops_external_execution_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "SYNTHETIC_CREDENTIAL_MARKER_TEST_ONLY"
    dsn_keys = (
        "MAEZO_TEST_DATABASE_URL",
        "MAEZO_TEST_A2A_EDGE_DATABASE_URL",
        "MAEZO_TEST_AMH_INBOX_DATABASE_URL",
        "MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL",
        "MAEZO_TEST_CHECKPOINT_DATABASE_URL",
    )
    control_keys = (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTHONPATH",
        "PYTHONHOME",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_CONFIG_FILE",
    )
    polluted = f"postgresql://outside:{marker}@127.0.0.1:1/outside"
    for key in dsn_keys:
        monkeypatch.setenv(key, polluted)
    for key in control_keys:
        monkeypatch.setenv(key, marker)

    env = _runtime_env()
    compose = _compose_base(REPO_ROOT)

    assert all(env[key] == "postgresql://maezo:maezo@127.0.0.1:15433/maezo" for key in dsn_keys)
    assert all(key not in env for key in control_keys)
    assert marker not in json.dumps(env, sort_keys=True)
    assert compose[:3] == ["docker", "--context", "colima"]
    assert compose[3:6] == ["compose", "--env-file", "/dev/null"]


def test_real_pytest_skip_is_rejected_while_strict_xfail_is_reported(tmp_path: Path) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        test_file = tmp_path / "test_outcomes.py"
        test_file.write_text(
            "import pytest\ndef test_unavailable(): pytest.skip('COULD NOT VERIFY local reserved endpoint')\n"
        )
        junit = tmp_path / "junit.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-q",
                "-ra",
                f"--junitxml={junit}",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode == 0
    rejected, reason = verified_result_code(
        result.returncode,
        actual_count=1,
        expected_count=1,
        passed_count=0,
        skipped_count=1,
        xfailed_count=0,
        xpassed_count=0,
        test_file=test_file.as_posix(),
    )
    assert rejected == 1
    assert "skip inesperado" in str(reason)
