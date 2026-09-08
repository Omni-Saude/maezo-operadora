"""EIR-SD-01/02/03: processos próprios e pytest real, sem serviços."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

ROOT = Path(__file__).resolve().parents[3]
SUPERVISOR = r"""
import argparse, contextlib, json, os, pathlib, signal, sys
from scripts.dev import run_engine_integration as r
root=pathlib.Path.cwd();out=pathlib.Path(sys.argv[1]);mode=sys.argv[2]
signal.signal(signal.SIGTERM,r._signal_handler)
r.LOCK_DIR=out/'engine.lock'
r.validate_checkout=lambda *_:(root,str(root/'src/maezo/__init__.py'))
r.execution_checkout=lambda *_:contextlib.nullcontext((root,str(root/'src/maezo/__init__.py')))
r._assert_execution_source=lambda *_:None
r.discover=lambda *_a,**_kw:{'execution_manifest':[{'suite':'core','test_file':'fixture.py',
 'expected_count':1,'items':[],'dependencies':{'engine_required':False}}]}
r._validate_docker_context=lambda *_:'synthetic-no-docker'
r._start_stack=lambda *_:[]
r._run_pytest=lambda *_:{'return_code':0}
def teardown(checkout,args,**kwargs):
    assert args==['down','-v','--remove-orphans']
    (out/'state-before-teardown.json').write_bytes((out/'run-state.json').read_bytes())
    code=("import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
          "print('synthetic teardown child',flush=True); ")
    code+= 'time.sleep(60)' if mode in ('signal','timeout') else ('sys.exit(9)' if mode=='error' else 'pass')
    return r._checked([sys.executable,'-c',code,str(out/'child.pid')],cwd=root,
                      timeout=.2 if mode=='timeout' else 30,log_path=kwargs['log_path'])
r._compose=teardown
args=argparse.Namespace(checkout=str(root),sha='synthetic',suite='core',test_file='fixture.py',
                       results_dir=str(out),lock_timeout=0)
try:rc=r.run_suite(args)
except BaseException as exc:
    (out/'exception.json').write_text(json.dumps({'type':type(exc).__name__}))
    rc=130 if isinstance(exc,r.RunnerInterrupted) else 1
(out/'supervisor-result.json').write_text(json.dumps({'rc':rc}))
raise SystemExit(rc)
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def authenticate_fixture(root: Path, module: Any = runner) -> None:
    """Bootstrap confiável: fixa blobs Git da fixture, nunca habilita fallback.

    Alimenta somente o registro privado de autoridade do teste. A resolução do
    núcleo passa por _assert_execution_source, SHA/blob, path e digest reais.
    A CLI obtém esse registro exclusivamente de execution_checkout.
    """
    target = root / "scripts/ci/pytest_execution_evidence.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((ROOT / "scripts/ci/pytest_execution_evidence.py").read_bytes())
    (root / "scripts/dev").mkdir(parents=True, exist_ok=True)
    (root / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {}\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Synthetic test")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Fixture autenticada")
    sha = _git(root, "rev-parse", "HEAD")
    digests = {}
    for entry in _git(root, "ls-tree", "-rz", sha).split("\0"):
        if not entry:
            continue
        header, name = entry.split("\t", 1)
        content = (root / name).read_bytes()
        blob = b"blob " + str(len(content)).encode() + b"\0" + content
        assert hashlib.sha1(blob, usedforsecurity=False).hexdigest() == header.split()[2]
        digests[name] = hashlib.sha256(content).hexdigest()
    module._source_digests[root.resolve()] = (sha, digests)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("mode", ["normal", "error", "timeout", "signal"])
def test_teardown_is_non_green_until_completion_and_interruptions_are_durable(
    tmp_path: Path, mode: str
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", SUPERVISOR, str(tmp_path), mode],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "child.pid").exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        child = int((tmp_path / "child.pid").read_text())
        if mode == "signal":
            process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        before = json.loads((tmp_path / "state-before-teardown.json").read_text())
        state = json.loads((tmp_path / "run-state.json").read_text())
        assert before["state"] == "teardown" and before["return_code"] != 0
        assert not _alive(child)
        assert state["return_code"] == process.returncode
        if mode == "normal":
            assert state["state"] == "passed" and process.returncode == 0
            assert not (tmp_path / "engine.lock").exists()
        else:
            assert state["state"] != "passed" and process.returncode != 0
            assert "teardown_error" in state
            owner = json.loads((tmp_path / "engine.lock/owner.json").read_text())
            assert owner["pid"] == process.pid
            assert owner["subprocess_quiescent"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if child is not None:
            with suppress(ProcessLookupError):
                os.killpg(child, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while _alive(child) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _alive(child)
        owner = tmp_path / "engine.lock/owner.json"
        if owner.exists():
            assert json.loads(owner.read_text())["pid"] == process.pid
            owner.unlink()
            owner.parent.rmdir()


def _secret() -> str:
    return "_".join(("EIR", "REPAIR", "PRIVATE", "PROBE"))


@pytest.mark.parametrize("partial", [False, True])
def test_real_stdout_stderr_and_partial_logs_redact_structured_credentials(
    tmp_path: Path, partial: bool
) -> None:
    secret = _secret()
    code = "import sys,time; print(sys.argv[1],flush=True); print(sys.argv[2],file=sys.stderr,flush=True); "
    code += "time.sleep(60)" if partial else "pass"
    result = runner._run(
        [
            sys.executable,
            "-c",
            code,
            json.dumps({"password": secret}),
            "ValidationError input_value=" + repr({"api_key": secret + " with space"}),
        ],
        cwd=ROOT,
        timeout=0.2 if partial else 5,
        log_path=tmp_path / "partial.log" if partial else None,
    )
    public = result.stdout + result.stderr
    if partial:
        public += (tmp_path / "partial.log").read_text()
    leaked = secret in public
    assert not leaked
    assert result.returncode == (124 if partial else 0)
    assert "<redacted>" in public


def test_nested_json_and_xml_are_safe_without_corrupting_private_lock_token(tmp_path: Path) -> None:
    secret = _secret()
    target = tmp_path / "public.json"
    original_hash = "a" * 64
    runner._json_write(
        target,
        {
            "nested": {"password": secret, "api_key": secret, "token": secret},
            "nodeid": "safe-label",
            "nodeid_sha256": original_hash,
        },
    )
    payload = json.loads(target.read_text())
    leaked = secret in target.read_text()
    assert not leaked
    assert payload["nodeid_sha256"] == original_hash
    raw = ET.Element("testsuite", tests="1")
    case = ET.SubElement(raw, "testcase", name="example")
    ET.SubElement(case, "skipped", message=json.dumps({"password": secret}))
    ET.ElementTree(raw).write(tmp_path / "raw.xml")
    runner._publish_xml(tmp_path / "raw.xml", tmp_path / "safe.xml")
    ET.parse(tmp_path / "safe.xml")
    leaked = secret in (tmp_path / "safe.xml").read_text()
    assert not leaked
    lock = runner.EngineLock.create(
        tmp_path / "engine.lock", checkout="fixture", sha="fixture", suite="fixture"
    )
    assert lock.acquire(0)
    try:
        assert lock.update("test-checkpoint", diagnostic={"password": secret})
        owner = json.loads(lock.owner_path.read_text())
        assert owner["token"] == lock.token
        leaked = secret in lock.owner_path.read_text()
        assert not leaked
        assert lock.owns()
    finally:
        assert lock.release()


@pytest.mark.parametrize("style", ["json", "repr"])
def test_schema2_uses_complete_identities_then_preserves_public_correlation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, style: str
) -> None:
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    secret = _secret()
    ids = [
        json.dumps({"password": secret + str(n)}) if style == "json" else repr({"api_key": secret + str(n)})
        for n in [1, 2]
    ]
    (tmp_path / "test_parameters.py").write_text(
        "import pytest\npytestmark=pytest.mark.integration\n"
        f'@pytest.mark.parametrize("value",[1,2],ids={ids!r})\ndef test_value(value): assert value > 0\n'
    )
    authenticate_fixture(tmp_path)
    out = tmp_path / "result"
    result = runner._run_pytest(tmp_path, "core", "test_parameters.py", runner._runtime_env(), out, 2)
    assert result["return_code"] == 0
    assert not any(secret in p.read_text() for p in out.iterdir())
    evidence = json.loads((out / "pytest-execution.json").read_text())
    assert evidence["schema"] == 2
    identities = {i["junit_identity_sha256"] for i in evidence["collection"]}
    assert len(identities) == len({i["nodeid"] for i in evidence["collection"]}) == 2
    assert {c["junit_identity_sha256"] for c in result["cases"]} == identities
    assert {
        c.get("maezo_identity_sha256") for c in ET.parse(out / "junit.xml").iter("testcase")
    } == identities


def test_plugin_and_validator_ignore_mutated_launcher_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    entry = launcher / "scripts/dev/run_engine_integration.py"
    entry.parent.mkdir(parents=True)
    entry.write_bytes((ROOT / "scripts/dev/run_engine_integration.py").read_bytes())
    helper = launcher / "scripts/ci/pytest_execution_evidence.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes((ROOT / "scripts/ci/pytest_execution_evidence.py").read_bytes())
    spec = importlib.util.spec_from_file_location("fixture_runner", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (owned / "test_control.py").write_text("def test_control(): assert True\n")
    authenticate_fixture(owned, module)
    marker = tmp_path / "outside-code-ran"
    helper.write_text(helper.read_text() + f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.setattr(module, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    assert module._collect(owned, ["test_control.py"], tmp_path / "collected.log") == {
        "test_control.py::test_control"
    }
    validator = module._evidence_validator(owned)
    assert Path(validator.__code__.co_filename) == owned / "scripts/ci/pytest_execution_evidence.py"
    assert not marker.exists()


def test_subprocess_refuses_core_bytes_changed_after_command_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "test_control.py").write_text("def test_control(): assert True\n")
    authenticate_fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    command = runner._pytest_command(
        tmp_path, "test_control.py", "--collect-only", evidence_path=tmp_path / "metadata.json"
    )
    marker = tmp_path / "unverified-code-ran"
    helper = tmp_path / "scripts/ci/pytest_execution_evidence.py"
    helper.write_text(helper.read_text() + f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
    result = runner._run(command, cwd=tmp_path, timeout=10)
    assert result.returncode != 0 and not marker.exists()
    with pytest.raises(runner.RunnerError):
        runner._evidence_validator(tmp_path)


@pytest.mark.parametrize("style", ["json", "repr"])
def test_incomplete_quoted_credential_does_not_publish_its_remaining_words(
    tmp_path: Path, style: str
) -> None:
    first, last = _secret(), "private-tail-fragment"
    value = {"password": first + " " + last}
    text = (json.dumps(value) if style == "json" else repr(value))[:-2]
    result = runner._run(
        [sys.executable, "-c", "import sys; print(sys.argv[1],end='',flush=True)", text],
        cwd=ROOT,
        log_path=tmp_path / "partial.log",
    )
    leaked = any(part in result.stdout for part in (first, last))
    assert not leaked
    assert "<redacted>" in result.stdout and result.returncode == 0
