from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path

import pytest
from scripts.dev import run_engine_integration as runner

ROOT = Path(__file__).resolve().parents[3]
SENTINEL = "SYNTHETIC_EIR_DELTA_NOT_SECRET"
SUPERVISOR = r"""
import argparse, contextlib, json, os, pathlib, signal, sys
from scripts.dev import run_engine_integration as r
root=pathlib.Path.cwd()
out=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
signal.signal(signal.SIGTERM,r._signal_handler)
r.LOCK_DIR=out/'engine.lock'
r.validate_checkout=lambda *_:(root,str(root/'src/maezo/__init__.py'))
r.execution_checkout=lambda *_:contextlib.nullcontext((root,str(root/'src/maezo/__init__.py')))
r.discover=lambda *_args,**_kw:{'execution_manifest':[{'suite':'core',
 'test_file':'synthetic.py','expected_count':1,'dependencies':{'engine_required':False}}]}
real_killpg=os.killpg
child=("import pathlib,os,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
       "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
       "print('postgresql://probe:SYNTHETIC_EIR_DELTA_NOT_SECRET@127.0.0.1:9/probe',flush=True); "
       "time.sleep(60)")
def context_probe(*_):
    if mode=='denied':
        def killpg(group,sig):
            if sig==signal.SIGKILL: raise PermissionError('synthetic denied')
            return real_killpg(group,sig)
        os.killpg=killpg
        r.PROCESS_TERM_GRACE=.05
    else:
        calls=0
        r._group_exists=lambda _:True
        def wait(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2: os.kill(os.getpid(),signal.SIGTERM)
            return False
        r._wait_group_gone=wait
    r._run([sys.executable,'-c',child,str(out/'child.pid')],cwd=root,timeout=.2,log_path=out/'partial.log')
    raise AssertionError('uncertain cleanup passed')
r._validate_docker_context=context_probe
def forbidden(*args,**kwargs):
    (out/'docker-attempt').touch()
    raise AssertionError('Docker forbidden')
r._compose=forbidden;r._start_stack=forbidden
args=argparse.Namespace(checkout=str(root),sha='synthetic',suite='core',test_file='synthetic.py',results_dir=str(out),lock_timeout=0)
rc=r.run_suite(args)
(out/'return.json').write_text(json.dumps({'rc':rc,'release':r._process_owner.release()}))
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("mode", ["denied", "pending-signal"])
def test_failed_quiescence_is_durable_across_error_and_signal(tmp_path: Path, mode: str) -> None:
    process = subprocess.Popen([sys.executable, "-c", SUPERVISOR, str(tmp_path), mode], cwd=ROOT)
    child_pid: int | None = None
    try:
        assert process.wait(timeout=15) == 0
        child_pid = int((tmp_path / "child.pid").read_text())
        state = json.loads((tmp_path / "run-state.json").read_text())
        owner = json.loads((tmp_path / "engine.lock/owner.json").read_text())
        result = json.loads((tmp_path / "return.json").read_text())
        assert result == {"rc": 1, "release": False}
        assert state["state"] == "subprocess_cleanup_unconfirmed"
        assert owner["subprocess_quiescent"] is False
        assert owner["pending_pgids"] == [child_pid]
        assert not (tmp_path / "docker-attempt").exists()
        assert SENTINEL not in (tmp_path / "partial.log").read_text()
        assert "<redacted>" in (tmp_path / "partial.log").read_text()
        if mode == "denied":
            assert _alive(child_pid)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child_pid is None and (tmp_path / "child.pid").exists():
            child_pid = int((tmp_path / "child.pid").read_text())
        if child_pid is not None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(child_pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while _alive(child_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _alive(child_pid)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=path, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _minimal_git(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "synthetic@example.invalid")
    _git(source, "config", "user.name", "Synthetic fixture")
    (source / "scripts/dev").mkdir(parents=True)
    (source / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {}\n")
    (source / "docker-compose.yml").write_text("services: {}\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "relative", ["tests/unit/test_ignored.py", "conftest.py", "json.py", ".env", "uv.toml"]
)
def test_untracked_and_ignored_execution_inputs_are_refused_without_reading_values(
    tmp_path: Path, relative: str
) -> None:
    source, sha = _minimal_git(tmp_path)
    target = source / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SENTINEL)
    original = target.read_bytes()
    (source / ".git/info/exclude").write_text(relative + "\n")
    assert not _git(source, "status", "--porcelain")
    with pytest.raises(runner.RunnerError) as error:
        runner.validate_checkout(str(source), sha)
    assert SENTINEL not in str(error.value)
    assert target.read_bytes() == original


def test_parent_http_never_uses_ambient_proxy_or_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    opener = runner._local_opener(runner.ENGINE_URL)
    proxy_handlers = [h for h in opener.handlers if isinstance(h, runner.urllib.request.ProxyHandler)]
    assert all(not h.proxies for h in proxy_handlers)
    redirect = next(h for h in opener.handlers if isinstance(h, runner._NoRedirect))
    with pytest.raises(runner.RunnerError):
        redirect.redirect_request(None, None, 302, "", None, "http://127.0.0.1:9")
    with pytest.raises(runner.RunnerError):
        runner._local_opener("http://127.0.0.1:9")


@pytest.mark.parametrize(
    "body,expected_rc",
    [
        (
            "def test_ok(): pass\n@pytest.mark.xfail(strict=True,reason='gap')\ndef test_x(): assert False\n",
            0,
        ),
        ("@pytest.mark.xfail(strict=True,run=False,reason='omitted')\ndef test_x(): assert False\n", 1),
        (
            "def test_ok(): pass\n"
            "@pytest.mark.skip(reason='only runs when MAEZO_CHAOS_MUTATE=invented')\ndef test_x(): pass\n",
            1,
        ),
    ],
)
def test_runner_uses_phase_evidence_and_sanitized_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, expected_rc: int
) -> None:
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text("import pytest\npytestmark=pytest.mark.integration\n" + body)
    result = runner._run_pytest(
        tmp_path, "core", test_file.name, runner._runtime_env(), tmp_path / "result", body.count("def test_")
    )
    assert result["return_code"] == expected_rc, result
    ET.parse(tmp_path / "result/junit.xml")
    assert (tmp_path / "result/pytest-execution.json").is_file()


def test_xml_and_json_redaction_preserves_parseable_artifacts(tmp_path: Path) -> None:
    raw = tmp_path / "raw.xml"
    safe = tmp_path / "safe.xml"
    raw.write_text(
        f'<testsuite tests="1"><testcase name="x"><skipped message="postgresql://a:{SENTINEL}@127.0.0.1:9/x"/></testcase></testsuite>'
    )
    runner._publish_xml(raw, safe)
    ET.parse(safe)
    assert SENTINEL not in safe.read_text()
    runner._json_write(tmp_path / "state.json", {"error": f"token={SENTINEL}"})
    assert json.loads((tmp_path / "state.json").read_text())["error"] == "token=<redacted>"
