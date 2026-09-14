"""Real subprocess probes for spawn protection without inherited supervisor masks."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest
from scripts.ci import generate_release_floor as floor
from scripts.dev import run_engine_integration as runner

from tests.support.measurement_python import measurement_python


def _run_probe(kind: str, directory: Path, code: str):
    if kind == "floor":
        (directory / "pytest.py").write_text(code)
        return floor._run_unit_measurement(directory, measurement_python(directory))
    return runner._run([sys.executable, "-c", code], cwd=directory, timeout=5)


@pytest.mark.parametrize("kind", ["floor", "engine"])
@pytest.mark.parametrize("blocked", [(), (signal.SIGUSR1,), (signal.SIGINT, signal.SIGUSR1)])
def test_child_inherits_exact_caller_mask(kind: str, blocked: tuple[int, ...], tmp_path: Path) -> None:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    expected = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    try:
        result = _run_probe(
            kind,
            tmp_path,
            "import signal,json; print(json.dumps(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, set()))))",
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == sorted(expected)
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == expected
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@pytest.mark.parametrize("kind", ["floor", "engine"])
@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_child_receives_its_own_interrupt_and_term(kind: str, sent: signal.Signals, tmp_path: Path) -> None:
    code = f"""import os,signal,json
seen=[]
signal.signal({int(sent)}, lambda signum, frame: seen.append(signum))
os.kill(os.getpid(), {int(sent)})
print(json.dumps(seen))
"""
    result = _run_probe(kind, tmp_path, code)
    assert result.returncode == 0
    assert json.loads(result.stdout) == [int(sent)]


@pytest.mark.parametrize("kind", ["floor", "engine"])
@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_interrupt_between_spawn_and_registration_is_deferred_then_reaps(
    kind: str, sent: signal.Signals, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = []
    original = runner._record_pending
    previous_handler = signal.signal(sent, runner._signal_handler)
    mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def record(pgid: int) -> None:
        # Signal arrives BEFORE ownership recording. An ordinary handler would throw
        # here, leaking an unregistered process. The guard must defer it until exit.
        os.kill(os.getpid(), sent)
        original(pgid)
        groups.append(pgid)

    monkeypatch.setattr(runner, "_record_pending", record)
    try:
        with pytest.raises(runner.RunnerInterrupted):
            _run_probe(kind, tmp_path, "import time; time.sleep(30)")
        assert len(groups) == 1
        assert not runner._group_exists(groups[0])
        assert groups[0] not in runner._pending_groups
        assert signal.getsignal(sent) == runner._signal_handler
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == mask_before
    finally:
        signal.signal(sent, previous_handler)


def test_spawn_guard_preserves_ignored_dispositions_and_nested_delivery(tmp_path: Path) -> None:
    seen = []
    previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
    previous_term = signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
    try:
        with runner._spawn_signal_guard():
            with runner._spawn_signal_guard():
                result = runner._run(
                    [sys.executable, "-c", "import signal; print(int(signal.getsignal(signal.SIGINT)))"],
                    cwd=tmp_path,
                    timeout=5,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                assert seen == []
            assert seen == []
        assert seen == [signal.SIGTERM]
        assert result.stdout.strip() == str(int(signal.SIG_IGN))
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def test_signal_arriving_during_restoration_uses_restored_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def handler(signum, frame):
        seen.append(signum)

    old = signal.signal(signal.SIGTERM, handler)
    original_signal = signal.signal
    armed = False

    def install(signum, target):
        previous = original_signal(signum, target)
        if armed and signum == signal.SIGTERM and target is handler:
            os.kill(os.getpid(), signal.SIGTERM)
        return previous

    monkeypatch.setattr(signal, "signal", install)
    try:
        with runner._spawn_signal_guard():
            armed = True
        assert seen == [signal.SIGTERM]
        assert signal.getsignal(signal.SIGTERM) is handler
    finally:
        original_signal(signal.SIGTERM, old)


@pytest.mark.parametrize("kind", ["floor", "engine"])
def test_registration_error_restores_handlers_and_cleans_owned_group(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = []
    record = runner._record_pending
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def fail(pgid: int) -> None:
        record(pgid)
        groups.append(pgid)
        raise RuntimeError("registration failed")

    monkeypatch.setattr(runner, "_record_pending", fail)
    with pytest.raises(RuntimeError, match="registration failed"):
        _run_probe(kind, tmp_path, "import time; time.sleep(30)")
    assert len(groups) == 1
    assert not runner._group_exists(groups[0])
    assert groups[0] not in runner._pending_groups
    assert {s: signal.getsignal(s) for s in before} == before
    assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask


@pytest.mark.parametrize("kind", ["floor", "engine"])
def test_spawn_exec_error_remains_synchronous_and_restores_handlers(kind: str, tmp_path: Path) -> None:
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    pending = set(runner._pending_groups)
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        if kind == "floor":
            floor._run_unit_measurement(tmp_path, missing)
        else:
            runner._run([missing], cwd=tmp_path)
    assert runner._pending_groups == pending
    assert {s: signal.getsignal(s) for s in before} == before


def test_floor_argv_environment_and_stdin_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAEZO_SIGNAL_INHERITANCE_PROBE", "preserved")
    result = _run_probe(
        "floor",
        tmp_path,
        """import json,os,sys
print(json.dumps([sys.argv[1:], os.environ['MAEZO_SIGNAL_INHERITANCE_PROBE'], sys.stdin.read()]))
""",
    )
    assert result.args == [str(tmp_path / "measurement-python/bin/python"), "-m", "pytest", "tests/", "-q"]
    assert json.loads(result.stdout) == [["tests/", "-q"], "preserved", ""]
    assert floor._UNIT_TESTS_TIMEOUT_SECONDS == 7200


def test_engine_thread_caller_refuses_before_spawn_and_restores_thread_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def cannot_spawn(*args, **kwargs):
        pytest.fail("non-main thread must refuse before Popen")

    monkeypatch.setattr(runner.subprocess, "Popen", cannot_spawn)

    def attempt():
        before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        with pytest.raises(ValueError, match="main thread"):
            runner._run([sys.executable, "-c", "pass"], cwd=tmp_path)
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(attempt).result(timeout=5)


@pytest.mark.parametrize("kind", ["floor", "engine"])
@pytest.mark.parametrize("registration_error", [False, True])
def test_both_deferred_signals_reap_before_unwinding_and_restore_mask(
    kind: str, registration_error: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = []
    original = runner._record_pending
    previous = {s: signal.signal(s, runner._signal_handler) for s in (signal.SIGINT, signal.SIGTERM)}
    before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def record(pgid: int) -> None:
        groups.append(pgid)
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGTERM)
        original(pgid)
        if registration_error:
            raise RuntimeError("registration failed after both signals")

    monkeypatch.setattr(runner, "_record_pending", record)
    try:
        with pytest.raises(runner.RunnerInterrupted):
            _run_probe(kind, tmp_path, "import time; time.sleep(30)")
        assert len(groups) == 1
        assert not runner._group_exists(groups[0])
        assert groups[0] not in runner._pending_groups
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask
        assert all(signal.getsignal(s) is runner._signal_handler for s in previous)
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, before_mask)
        for pgid in groups:
            if runner._group_exists(pgid):
                os.killpg(pgid, signal.SIGKILL)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_cleanup_failure_retains_priority_and_delivers_cleanup_time_signals() -> None:
    delivered = []
    closed = []

    def handler(signum, frame):
        delivered.append(signum)
        raise runner.RunnerInterrupted(signum)

    previous = {s: signal.signal(s, handler) for s in (signal.SIGINT, signal.SIGTERM)}
    before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def close() -> None:
        closed.append(True)
        os.kill(os.getpid(), signal.SIGTERM)
        raise runner.ProcessGroupCleanupError("owned cleanup failed")

    try:
        with (
            pytest.raises(runner.ProcessGroupCleanupError, match="owned cleanup failed"),
            runner._spawn_signal_guard(close),
        ):
            os.kill(os.getpid(), signal.SIGINT)
        assert closed == [True]
        assert sorted(delivered) == [signal.SIGINT, signal.SIGTERM]
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask
        assert all(signal.getsignal(s) is handler for s in previous)
    finally:
        for sig, previous_handler in previous.items():
            signal.signal(sig, previous_handler)


@pytest.mark.parametrize("kind", ["floor", "engine"])
@pytest.mark.parametrize("during_cleanup", [False, True])
def test_running_and_cleanup_signals_cancel_promptly(
    kind: str, during_cleanup: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    original_communicate = runner.subprocess.Popen.communicate
    original_quiesce = runner._quiesce_group
    groups = []
    sender = None
    previous = {s: signal.signal(s, runner._signal_handler) for s in (signal.SIGINT, signal.SIGTERM)}
    before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())

    def send() -> None:
        os.kill(os.getpid(), signal.SIGINT)
        if not during_cleanup:
            os.kill(os.getpid(), signal.SIGTERM)

    def communicate(process, *args, **kwargs):
        nonlocal sender
        if process.pid in runner._pending_groups and sender is None:
            groups.append(process.pid)
            sender = threading.Timer(0.05, send)
            sender.start()
        return original_communicate(process, *args, **kwargs)

    def quiesce(process, pgid):
        if during_cleanup:
            os.kill(os.getpid(), signal.SIGTERM)
        return original_quiesce(process, pgid)

    monkeypatch.setattr(runner.subprocess.Popen, "communicate", communicate)
    monkeypatch.setattr(runner, "_quiesce_group", quiesce)
    start = time.monotonic()
    try:
        with pytest.raises(runner.RunnerInterrupted):
            _run_probe(kind, tmp_path, "import time; time.sleep(30)")
        assert time.monotonic() - start < 5
        assert len(groups) == 1
        assert not runner._group_exists(groups[0])
        assert groups[0] not in runner._pending_groups
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask
        assert all(signal.getsignal(s) is runner._signal_handler for s in previous)
    finally:
        if sender is not None:
            sender.join(timeout=1)
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, before_mask)
        for pgid in groups:
            if runner._group_exists(pgid):
                os.killpg(pgid, signal.SIGKILL)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
