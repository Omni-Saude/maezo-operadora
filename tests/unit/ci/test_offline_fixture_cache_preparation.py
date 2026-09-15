"""Synthetic preparation controls; never install or resolve dependencies here."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from scripts.ci import prepare_offline_fixture_cache as p


def lock(*names: str, version: str = "9.1.1", artifact: str = "a") -> bytes:
    data = '[options]\nexclude-newer="2026-08-05T00:00:00Z"\n'
    for name in names:
        v = version if name == "pytest" else "1.4.0"
        data += (
            f'[[package]]\nname="{name}"\nversion="{v}"\n'
            'source={registry="https://pypi.org/simple"}\n'
            f'wheels=[{{url="https://files.pythonhosted.org/{name}.whl",'
            f'hash="sha256:{artifact * 64}"}}]\n'
        )
    return data.encode()


class Synthetic(p.Preparation):
    """Run the actual preparation choreography with command effects substituted."""

    def __init__(self, output: Path, failure: str = "") -> None:
        self.output = output
        self.root = output
        self.python = Path("/synthetic/python")
        self.inputs = {
            "uv.lock": lock("pytest", "pytest-asyncio"),
            "pyproject.toml": b'[tool.uv]\nexclude-newer="2026-08-05T00:00:00Z"\n',
            ".python-version": b"3.12\n",
            ".github/workflows/ci.yml": b'  MAEZO_CI_UV_VERSION: "0.11.26"\n',
        }
        self.head, self.sequence, self.failure = "selected-head", 0, failure
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.checks = 0

    def current(self) -> None:
        self.checks += 1
        if self.failure == "final-drift" and self.checks > 1:
            raise ValueError("selected source differs from commit")

    def command(self, cwd: Path, *args: str) -> bytes:
        self.current()
        self.sequence += 1
        self.commands.append((cwd.name, args))
        if args == ("--version",):
            return b"uv 0.11.26\n"
        if args[:2] == ("cache", "dir"):
            return b"/synthetic/default-cache\n"
        if args[0] == "lock":
            if self.failure == "metadata" and "--offline" in args:
                raise ValueError("cache preparation command refused")
            names = ("pytest", "pytest-asyncio") if "async" in cwd.name else ("pytest",)
            content = lock(*names, version="99.0" if self.failure == "closure" else "9.1.1")
            if self.failure == "resolution" and cwd.name.endswith("-offline"):
                content += b"# changed resolution\n"
            (cwd / "uv.lock").write_bytes(content)
        if self.failure == "wheel" and args[0] == "sync" and cwd.name == "tiny-offline":
            raise ValueError("cache preparation command refused")
        return b""


def test_exact_default_cache_preparation_has_fresh_offline_resolution_and_sync(
    tmp_path,
):
    runner = Synthetic(tmp_path)
    runner.run()
    assert (tmp_path / "result.json").is_file()
    for name in ("tiny", "tiny-async"):
        calls = [(directory, argv) for directory, argv in runner.commands if directory.startswith(name)]
        assert (
            name + "-online",
            (
                "lock",
                "--offline",
                "--no-config",
                "--no-build",
                "--no-python-downloads",
                "--python",
                "/synthetic/python",
            ),
        ) in calls
        assert any(d == name + "-offline" and a[0] == "lock" and "--offline" in a for d, a in calls)
        assert any(d == name + "-offline" and a[0] == "sync" and "--offline" in a for d, a in calls)
    for name in ("tiny", "tiny-async"):
        assert any(
            d == name + "-online"
            and a[:2] == ("pip", "compile")
            and "--constraint" in a
            and "--universal" in a
            for d, a in runner.commands
        )
    assert any(d == "root-offline" and "--locked" in a and "--offline" in a for d, a in runner.commands)
    assert all("--upgrade" not in a and "--cache-dir" not in a for _, a in runner.commands)


@pytest.mark.parametrize("failure", ["metadata", "wheel", "closure", "resolution", "final-drift"])
def test_preparation_failure_never_mints_ready_or_installs_unexpected_graph(tmp_path, failure):
    runner = Synthetic(tmp_path, failure)
    with pytest.raises(ValueError):
        runner.run()
    assert not (tmp_path / "result.json").exists()
    if failure == "closure":
        assert not any(d == "tiny-online" and a[0] == "sync" for d, a in runner.commands)


@pytest.mark.parametrize("version,artifact", [("99.0", "a"), ("9.1.1", "b")])
def test_source_selected_versions_and_artifact_hashes_cannot_widen(version, artifact):
    with pytest.raises(ValueError):
        p.check_fixture(lock("pytest"), lock("pytest", version=version, artifact=artifact))


def test_changed_selected_lock_is_supported_without_permanent_digest():
    # A separately reviewed checkout may change dependencies. Source selection,
    # not an absolute historical uv.lock hash, determines its permitted closure.
    p.check_fixture(lock("pytest", version="9.2.0"), lock("pytest", version="9.2.0"))


def test_cache_and_python_environment_override_remain_refused(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("UV_", "PYTHON", "PYTEST_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", "/synthetic/not-forwarded")
    assert "XDG_CACHE_HOME" not in p.clean_environment()
    monkeypatch.setenv("UV_CACHE_DIR", "/synthetic/action-cache")
    with pytest.raises(ValueError, match="ambient"):
        p.clean_environment()


def test_committed_source_and_tool_bytes_are_rechecked(tmp_path):
    source, tool = tmp_path / "source", tmp_path / "uv"
    source.write_bytes(b"source")
    tool.write_bytes(b"uv")
    runner = object.__new__(p.Preparation)
    runner.root, runner.head = tmp_path, "selected"
    runner.inputs = {"source": b"source"}
    runner.tool_pins = {str(tool): p.sha(b"uv")}
    runner.git = lambda *args: b"selected" if args[0] == "rev-parse" else b"source"
    runner.current()
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source"):
        runner.current()
    source.write_bytes(b"source")
    tool.write_bytes(b"changed")
    with pytest.raises(ValueError, match="tool"):
        runner.current()


def test_fixture_constraints_include_transitives_from_canonical_lock():
    root = (
        lock("pytest", "pytest-asyncio").replace(
            b'source={registry="https://pypi.org/simple"}',
            b'dependencies=[{name="helper"}]\nsource={registry="https://pypi.org/simple"}',
            1,
        )
        + b"[[package]]"
        + lock("helper").split(b"[[package]]", 1)[1]
    )
    assert p.fixture_constraints(root, ("pytest==9.1.1",)) == b"helper==1.4.0\npytest==9.1.1\n"


def test_fixture_constraints_reject_unselected_direct_pin():
    with pytest.raises(ValueError, match="outside selected lock"):
        p.fixture_constraints(lock("pytest"), ("pytest==99.0",))


def test_both_ci_consumers_bind_cache_to_preparer_and_run_it_before_pytest():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml").read_text())
    for job_name, consumer_name in (
        ("quality", "Unit tests + coverage gate (>=85%)"),
        ("release-floor", "Release-capability floor gate (audit §5 — no override hides a P0 regression)"),
    ):
        steps = workflow["jobs"][job_name]["steps"]
        install = next(step for step in steps if step.get("name") == "Install uv")
        assert install["with"]["cache-suffix"] == (
            "offline-fixture-${{ hashFiles('.github/workflows/ci.yml', "
            "'scripts/ci/prepare_offline_fixture_cache.py', "
            "'scripts/dev/run_engine_integration.py') }}"
        )
        assert install["with"]["cache-dependency-glob"] == "uv.lock"
        dependency_install = next(step for step in steps if step.get("name") == "Install dependencies")
        assert dependency_install["run"] == (
            'uv sync --locked --extra dev --cache-dir "$RUNNER_TEMP/maezo-project-build-cache"'
        )
        preparation = next(
            step for step in steps if step.get("name") == "Prepare default offline fixture cache"
        )
        consumer = next(step for step in steps if step.get("name") == consumer_name)
        assert steps.index(install) < steps.index(preparation) < steps.index(consumer)
        assert "env -u UV_CACHE_DIR -u UV_PYTHON_INSTALL_DIR .venv/bin/python -I" in preparation["run"]
        assert "scripts/ci/prepare_offline_fixture_cache.py" in preparation["run"]


@pytest.mark.parametrize("failure", ["timeout", "interrupt", "unexpected"])
def test_command_failures_quiesce_owned_child_and_preserve_failure_record(tmp_path, monkeypatch, failure):
    import json
    import signal
    import subprocess
    import sys
    import time

    runner = object.__new__(p.Preparation)
    runner.current = lambda: None
    runner.sequence = 0
    runner.output = tmp_path
    runner.uv = Path(sys.executable)
    runner.environment = {"PATH": os.environ["PATH"]}
    runner.deadline = time.monotonic() + (0.1 if failure == "timeout" else 10)
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if failure != "timeout":
            real_wait = process.wait

            def fail_once(*wait_args, **wait_kwargs):
                process.wait = real_wait
                if failure == "interrupt":
                    raise KeyboardInterrupt
                raise RuntimeError("injected wait error")

            process.wait = fail_once
        return process

    monkeypatch.setattr(p.subprocess, "Popen", spawn)
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    error = {"timeout": ValueError, "interrupt": KeyboardInterrupt, "unexpected": RuntimeError}[failure]
    with pytest.raises(error):
        runner.command(tmp_path, "-c", "import time; time.sleep(30)")
    record = json.loads((tmp_path / "command-01.json").read_text())
    assert record["quiescent"] is True
    assert record["timed_out"] is (failure == "timeout")
    assert record["returncode"] == (124 if failure == "timeout" else None)
    assert not (tmp_path / "result.json").exists()
    with pytest.raises(ProcessLookupError):
        os.killpg(record["pgid"], 0)
    assert {s: signal.getsignal(s) for s in previous} == previous


def test_main_catches_sigterm_and_restores_prior_handler(tmp_path, monkeypatch):
    import signal
    import sys

    class Interrupted:
        def __init__(self, *args):
            pass

        def run(self):
            os.kill(os.getpid(), signal.SIGTERM)
            pytest.fail("SIGTERM must interrupt preparation")

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(p, "Preparation", Interrupted)
    monkeypatch.setattr(sys, "argv", ["prepare", "--root", str(tmp_path), "--output", str(tmp_path / "out")])
    assert p.main() == 130
    assert signal.getsignal(signal.SIGTERM) == previous
