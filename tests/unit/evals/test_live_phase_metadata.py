"""PHI-LIVE-01/02: UNIT seams, blocked network, actual pytest/runner phases.

Synthetic AWSResponse and backend replace only explicit private unit seams. No
positive below is HTTP authenticity, LLM liveness or environment authorization.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

from tests.evals import _live
from tests.unit.evals.test_live_eval_interface import CONFIG, ROOT, wrapper
from tests.unit.evals.test_live_phi_completion import (
    install_unit_wire,
)
from tests.unit.evals.test_live_phi_completion import (
    isolated as isolated,
)


@pytest.fixture
def observer():
    live = _live.build_live_inference()
    node = object()
    live.bind_to_node(node)
    yield live, node
    live.close()


@pytest.mark.parametrize("phase", ["setup", "teardown", "late_context", "foreign_node"])
async def test_outside_body_attempt_survives_valid_completion(monkeypatch, observer, phase):
    live, node = observer
    install_unit_wire(monkeypatch, live)
    with _live.live_eval_body(node):
        copied = contextvars.copy_context()
        if phase != "setup":
            await live.generate("synthetic valid", phi=True)
            live.verify()

    async def invalid():
        with pytest.raises(_live.LiveEvalError):
            await live.generate("synthetic invalid", phi=True)

    if phase == "foreign_node":
        with _live.live_eval_body(object()) as foreign:
            await invalid()
            assert foreign.invalid
    elif phase == "late_context":
        await copied.run(asyncio.create_task, invalid())
    else:
        await invalid()
    if phase == "setup":
        with _live.live_eval_body(node):
            await live.generate("synthetic valid", phi=True)
    assert (live.attempts, live.completions, live.failures) == (2, 1, 1)
    with pytest.raises(_live.LiveEvalError):
        live.verify()


async def test_setup_warmup_never_calls_backend_or_credits_body(monkeypatch, observer):
    live, node = observer
    install_unit_wire(monkeypatch, live)
    with pytest.raises(_live.LiveEvalError):
        await live.generate("synthetic warmup", phi=True)
    with _live.live_eval_body(node):
        pass
    assert (live.attempts, live.completions, live.failures) == (1, 0, 1)
    with pytest.raises(_live.LiveEvalError):
        live.verify()


@pytest.mark.parametrize("suspend_at", ["before_send", "after_send"])
async def test_completion_crossing_body_end_is_rejected(monkeypatch, observer, suspend_at):
    live, node = observer
    install_unit_wire(monkeypatch, live)
    ready, resume = asyncio.Event(), asyncio.Event()

    async def backend(*args, **kwargs):
        if suspend_at == "after_send":
            await asyncio.to_thread(live._session.send, object())
        ready.set()
        await resume.wait()
        if suspend_at == "before_send":
            await asyncio.to_thread(live._session.send, object())
        return "synthetic late response"

    monkeypatch.setattr(live._provider, "generate", backend)
    with _live.live_eval_body(node):
        task = asyncio.create_task(live.generate("synthetic", phi=True))
        await ready.wait()
    resume.set()
    with pytest.raises(_live.LiveEvalError):
        await task
    assert live.completions == 0 and live.failures > 0
    with pytest.raises(_live.LiveEvalError):
        live.verify()


async def test_valid_thread_completion_and_rebind_refusal(monkeypatch, observer):
    live, node = observer
    install_unit_wire(monkeypatch, live)
    with _live.live_eval_body(node):
        assert await live.generate("synthetic", phi=True) == "synthetic narrative"
    live.verify()
    with pytest.raises(_live.LiveEvalError, match="already bound"):
        live.bind_to_node(object())
    with pytest.raises(_live.LiveEvalError):
        live.verify()


# This fixture establishes a UNIT backend in setup but does not generate there.
UNIT_BACKEND = """
import asyncio, sys
@pytest.fixture
def unit_backend(live_inference, monkeypatch):
    response_type = sys.modules["botocore.awsrequest"].AWSResponse
    raw = response_type("https://bedrock-runtime.sa-east-1.amazonaws.com", 200, {}, None)
    monkeypatch.setattr(live_inference, "_original_send", lambda request: raw)
    async def backend(*args, **kwargs):
        await asyncio.to_thread(live_inference._session.send, object())
        return "synthetic unit narrative"
    monkeypatch.setattr(live_inference._provider, "generate", backend)
    return live_inference
"""


def run_recorded(tmp_path, action, body):
    start = datetime.now(UTC).isoformat()
    result = wrapper(tmp_path, action, body, CONFIG)
    streams = {}
    for name, value in (("stdout", result.stdout), ("stderr", result.stderr)):
        path = tmp_path / f"{action}.{name}"
        path.write_text(value)
        streams[name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / f"{action}.receipt.json").write_text(
        json.dumps(
            {
                "argv": result.args,
                "cwd": str(ROOT),
                "started_utc": start,
                "ended_utc": datetime.now(UTC).isoformat(),
                "rc": result.returncode,
                "streams": streams,
                "mode": "UNIT seam only; network denied",
            },
            indent=2,
        )
        + "\n"
    )
    return result


def check_runner(tmp_path, body, *, passes):
    collected = run_recorded(tmp_path, "collect", body)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    result = run_recorded(tmp_path, "run", body)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    assert ("[live-pytest] PASS" in result.stdout) is passes
    return result


def test_actual_runner_accepts_only_body_unit_completion(tmp_path):
    body = (
        UNIT_BACKEND
        + """
@pytest.mark.eval
@pytest.mark.llm_live
def test_case(unit_backend):
    assert unit_backend.attempts == 0
    asyncio.run(unit_backend.generate("synthetic", phi=True))
    assert unit_backend.completions == 1
"""
    )
    check_runner(tmp_path, body, passes=True)


@pytest.mark.parametrize("phase", ["setup", "teardown"])
@pytest.mark.parametrize("body_calls", [False, True])
def test_actual_runner_rejects_swallowed_fixture_generate(tmp_path, phase, body_calls):
    body = (
        UNIT_BACKEND
        + f'''
@pytest.fixture
def attempt(unit_backend):
    def invalid():
        try: asyncio.run(unit_backend.generate("synthetic fixture", phi=True))
        except Exception: pass
    if "{phase}" == "setup": invalid()
    yield unit_backend
    if "{phase}" == "teardown": invalid()
@pytest.mark.eval
@pytest.mark.llm_live
def test_case(attempt):
    if {body_calls}: asyncio.run(attempt.generate("synthetic body", phi=True))
'''
    )
    check_runner(tmp_path, body, passes=False)


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
@pytest.mark.parametrize(
    "fixture,attribute",
    [
        ("record_testsuite_property", "operator_notes"),
        ("record_property", "operator_notes"),
        ("record_xml_attribute", "classname"),
        ("record_xml_attribute", "name"),
        ("record_xml_attribute", "operator_notes"),
    ],
)
@pytest.mark.parametrize("body_fails", [False, True])
def test_metadata_is_refused_before_junit_writer_even_if_swallowed(
    tmp_path, phase, fixture, attribute, body_fails
):
    sentinel = "private-free-narrative-canary-6713"
    body = (
        UNIT_BACKEND
        + f'''
@pytest.fixture
def metadata({fixture}):
    def attempt():
        try: {fixture}("{attribute}", "{sentinel}")
        except Exception: pass
    if "{phase}" == "setup": attempt()
    yield attempt
    if "{phase}" == "teardown": attempt()
@pytest.mark.eval
@pytest.mark.llm_live
def test_case(unit_backend, metadata):
    asyncio.run(unit_backend.generate("synthetic", phi=True))
    if "{phase}" == "call": metadata()
    if {body_fails}: raise RuntimeError("{sentinel}")
'''
    )
    result = check_runner(tmp_path, body, passes=False)
    assert sentinel not in result.stdout + result.stderr
    for name in ("expected.json", "execution.json", "junit.xml", "validation.json"):
        assert sentinel not in (tmp_path / name).read_text()
    cases = ET.parse(tmp_path / "junit.xml").findall(".//testcase")
    assert cases
    assert all(case.attrib["name"] == "test_case" for case in cases)
    expected = json.loads((tmp_path / "expected.json").read_text())["collection"]
    identities = {
        (row["junit_classname"], row["junit_name"], row["junit_identity_sha256"]) for row in expected
    }
    assert {
        (case.attrib["classname"], case.attrib["name"], case.attrib["junit_identity_sha256"])
        for case in cases
    } == identities
    assert not ET.parse(tmp_path / "junit.xml").findall(".//property")
