"""Synthetic preparation controls; never install or resolve dependencies here."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
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
            if self.failure == "resolution" and "--offline" in args:
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
                "--no-config",
                "--no-build",
                "--no-python-downloads",
                "--python",
                "/synthetic/python",
            ),
        ) in calls
        assert any(d == name + "-offline" and a[0] == "lock" and "--offline" in a for d, a in calls)
        assert any(d == name + "-offline" and a[0] == "sync" and "--offline" in a for d, a in calls)
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
