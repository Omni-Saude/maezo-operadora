"""Credential/collection fences for ADR-0009 nightly evals; synthetic keys, no network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC = "synthetic-interface-test-not-a-credential"
BOOTSTRAP = """
import socket, sys, pytest
def refuse(*args, **kwargs):
    raise AssertionError('network forbidden in configuration test')
socket.socket.connect = refuse
socket.create_connection = refuse
raise SystemExit(pytest.main(sys.argv[1:]))
"""


def invoke(tmp_path: Path, body: str, credentials: dict[str, str], *extra: str):
    test = tmp_path / "test_synthetic.py"
    test.write_text("import pytest\n" + body)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(credentials)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            BOOTSTRAP,
            "-p",
            "tests.evals.conftest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--confcutdir",
            str(tmp_path),
            str(test),
            "--require-live-evals",
            "-m",
            "eval and llm_live",
            "-q",
            *extra,
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


BODY = """
@pytest.mark.eval
@pytest.mark.llm_live
def test_effective_provider(request):
    from maezo.runtime.inference import AnthropicInferenceProvider
    gate = request.config.pluginmanager.get_plugin("maezo-live-evals")
    assert isinstance(gate.provider._impl, AnthropicInferenceProvider)
    assert gate.provider.provider_name == "anthropic"
    assert gate.provider.health_check()["status"] == "ok"
"""


@pytest.mark.parametrize(
    "credentials",
    [
        {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC},
        {"ANTHROPIC_API_KEY": SYNTHETIC},
        {"MAEZO_ANTHROPIC_API_KEY": "", "ANTHROPIC_API_KEY": SYNTHETIC},
    ],
)
def test_valid_configuration_constructs_actual_provider_without_network(tmp_path, credentials):
    result = invoke(tmp_path, BODY, credentials)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LIVE EVAL: collected=1 passed=1" in result.stdout


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"LLM_GENERAL_API_KEY": SYNTHETIC},
        {"MAEZO_ANTHROPIC_API_KEY": " \t\n"},
        {"ANTHROPIC_API_KEY": " \t\n"},
        {"MAEZO_ANTHROPIC_API_KEY": " ", "ANTHROPIC_API_KEY": SYNTHETIC},
    ],
)
def test_missing_or_whitespace_effective_key_fails_explicitly(tmp_path, credentials):
    result = invoke(tmp_path, BODY, credentials)
    assert result.returncode != 0
    assert "LIVE EVAL UNAVAILABLE" in result.stderr
    assert SYNTHETIC not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "body, extra",
    [
        ("def test_not_live(): pass\n", ()),
        (BODY, ("-k", "nothing_matches")),
        ("raise ImportError('synthetic collection failure')\n", ()),
        (BODY, ("--option-that-does-not-exist",)),
    ],
)
def test_empty_broken_or_invalid_collection_never_passes(tmp_path, body, extra):
    result = invoke(tmp_path, body, {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC}, *extra)
    assert result.returncode != 0


@pytest.mark.parametrize("statement", ["pytest.skip('synthetic')", "pytest.xfail('synthetic')"])
def test_skipped_or_xfailed_body_is_not_live_success(tmp_path, statement):
    body = f"@pytest.mark.eval\n@pytest.mark.llm_live\ndef test_case(): {statement}\n"
    result = invoke(tmp_path, body, {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC})
    assert result.returncode != 0
    assert "LIVE EVAL: collected=1 passed=0" in result.stdout


def test_workflow_keeps_pr_keyless_and_nightly_explicit():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    pr = jobs["evals"]
    nightly = jobs["evals-nightly"]
    assert "secrets." not in str(pr)
    assert "eval and not llm_live" in str(pr)
    assert nightly["if"] == "github.event_name == 'schedule'"
    assert "LLM_GENERAL_API_KEY" not in str(nightly)
    live_steps = [s for s in nightly["steps"] if "MAEZO_ANTHROPIC_API_KEY" in s.get("env", {})]
    assert len(live_steps) == 1
    live = live_steps[0]
    assert live["env"]["MAEZO_ANTHROPIC_API_KEY"] == "${{ secrets.MAEZO_ANTHROPIC_API_KEY }}"
    assert "--require-live-evals" in live["run"]
    assert "run_live_pytest.py collect" in live["run"]
    assert "run_live_pytest.py run" in live["run"]
    assert "4|5" not in str(pr) + str(nightly)
    assert "continue-on-error" not in str(live)


def wrapper(tmp_path: Path, action: str, body: str, credentials: dict[str, str]):
    test = tmp_path / "test_synthetic.py"
    test.write_text("import pytest\n" + body)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(credentials)
    env["PYTHONPATH"] = str(ROOT)
    bootstrap = """
import socket, sys, runpy
def refuse(*args, **kwargs):
    raise AssertionError("network forbidden")
socket.socket.connect = refuse
socket.create_connection = refuse
sys.path.insert(0, "scripts/ci")
sys.argv = ["scripts/ci/run_live_pytest.py", *sys.argv[1:]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    if action == "collect":
        options = ["--output", str(tmp_path / "expected.json")]
    else:
        options = [
            "--expected",
            str(tmp_path / "expected.json"),
            "--evidence",
            str(tmp_path / "execution.json"),
            "--junit",
            str(tmp_path / "junit.xml"),
            "--validation",
            str(tmp_path / "validation.json"),
        ]
    return subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "--root",
            str(tmp_path),
            action,
            *options,
            "--",
            "-p",
            "tests.evals.conftest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--confcutdir",
            str(tmp_path),
            str(test),
            "--require-live-evals",
            "-m",
            "eval and llm_live",
            "-q",
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_actual_private_runner_executes_synthetic_config_and_publishes_counts(tmp_path):
    credentials = {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC}
    collected = wrapper(tmp_path, "collect", BODY, credentials)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    result = wrapper(tmp_path, "run", BODY, credentials)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed=1" in result.stdout
    assert SYNTHETIC not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "body, credentials",
    [
        (BODY, {}),
        ("def test_other(): pass\n", {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC}),
    ],
)
def test_actual_private_runner_rejects_unavailable_and_empty_collection(tmp_path, body, credentials):
    result = wrapper(tmp_path, "collect", body, credentials)
    assert result.returncode != 0
    assert not (tmp_path / "expected.json").exists()
    assert "FAIL coleta" in result.stderr


def test_actual_private_runner_rejects_live_skip_after_successful_collection(tmp_path):
    body = "@pytest.mark.eval\n@pytest.mark.llm_live\ndef test_case(): pytest.skip('synthetic')\n"
    credentials = {"MAEZO_ANTHROPIC_API_KEY": SYNTHETIC}
    assert wrapper(tmp_path, "collect", body, credentials).returncode == 0
    result = wrapper(tmp_path, "run", body, credentials)
    assert result.returncode != 0
    assert "[live-pytest] PASS" not in result.stdout


@pytest.mark.parametrize("missing, expected_rc", [(False, 5), (True, 4)])
def test_actual_pr_collection_step_refuses_pytest_four_and_five(tmp_path, missing, expected_rc):
    import shlex

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    step = next(s for s in jobs["evals"]["steps"] if s.get("name") == "Collect nonzero Tier-A evals")
    target = tmp_path / "test_no_evals.py"
    if not missing:
        target.write_text("def test_ordinary(): pass\n")
    command = step["run"].replace("uv run pytest", shlex.quote(sys.executable) + " -m pytest")
    command = command.replace("tests/evals", shlex.quote(str(target)))
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == expected_rc, result.stdout + result.stderr


@pytest.mark.parametrize("key", ["", " \t\n"])
def test_actual_nightly_step_stops_before_runner_when_credential_unavailable(tmp_path, key):
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    step = next(s for s in jobs["evals-nightly"]["steps"] if s.get("id") == "live")
    # A trap executable ensures the preflight never reaches collection or an LLM call.
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho 'UNEXPECTED RUNNER ENTRY'\nexit 99\n")
    fake_uv.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        env={"PATH": str(tmp_path), "MAEZO_ANTHROPIC_API_KEY": key},
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "LIVE EVAL UNAVAILABLE" in result.stdout
    assert "UNEXPECTED RUNNER ENTRY" not in result.stdout + result.stderr
