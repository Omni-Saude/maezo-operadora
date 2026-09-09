"""Offline observation/delegation controls, never live HTTP/engine evidence."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from tests.support import human_relay_diagnostics as diag
from tests.unit.gateway.human.test_durable_projection import wire

from maezo.gateway.human.projection import ProjectionError, verify_engine_receipt
from maezo.gateway.human.transport import EngineUnavailableError, MTLSHumanEngineTransport

_COV_CORE_KEYS = (
    "COV_CORE_SOURCE",
    "COV_CORE_CONFIG",
    "COV_CORE_DATAFILE",
    "COV_CORE_BRANCH",
    "COV_CORE_CONTEXT",
)


def _pytest_cov_owns_current_trace(controller, current) -> bool:
    collector = getattr(controller.cov, "_collector", None)
    tracers = getattr(collector, "tracers", ())
    return current is None or any(
        current is tracer or getattr(current, "__self__", None) is tracer for tracer in tracers
    )


@pytest.fixture(scope="module", autouse=True)
def _diagnostic_trace_lifecycle(request):
    """Pause only pytest-cov's own hook before diagnostics fixtures and child startup."""
    inherited_cov = {key: os.environ[key] for key in _COV_CORE_KEYS if key in os.environ}
    plugin = request.config.pluginmanager.getplugin("_cov")
    controller = getattr(plugin, "cov_controller", None)
    if controller is None or not controller.started:
        yield inherited_cov
        return

    current = sys.gettrace()
    if not _pytest_cov_owns_current_trace(controller, current):
        # Leave an arbitrary hook untouched; BoundaryDiagnostics must refuse it.
        yield inherited_cov
        return

    controller.pause()
    try:
        yield inherited_cov
    finally:
        assert sys.gettrace() is None, "diagnostic test leaked a trace hook; refusing to replace it"
        controller.resume()


@pytest.fixture
def diagnostics(tmp_path):
    previous = sys.gettrace()
    value = diag.BoundaryDiagnostics(tmp_path, None)
    try:
        yield value
    finally:
        sys.settrace(previous)
        assert diag._ACTIVE.get() is None


def wrapper(diagnostics):
    value = object.__new__(diag.DiagnosticMTLSHumanEngineTransport)
    value.diagnostics = diagnostics
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", [None, object()])
async def test_delegate_return_is_identical_and_called_once(monkeypatch, diagnostics, sentinel):
    calls = []
    command = wire()

    async def delegated(self, arg, *, query):
        calls.append((self, arg, query, diag._ACTIVE.get()))
        return sentinel

    monkeypatch.setattr(MTLSHumanEngineTransport, "_request", delegated)
    target = wrapper(diagnostics)
    assert await target._request(command, query=True) is sentinel
    assert len(calls) == 1 and calls[0][:3] == (target, command, True)
    assert calls[0][3] is diagnostics.requests[0]
    assert diagnostics.requests[0]["outcome"] == ("missing" if sentinel is None else "receipt")
    assert diag._ACTIVE.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [EngineUnavailableError(), asyncio.CancelledError()])
async def test_original_exception_identity_is_rethrown_and_clock_is_after(monkeypatch, diagnostics, error):
    events = []

    async def delegated(self, command, *, query):
        events.append("delegate")
        raise error

    async def measurement(record):
        assert diag._ACTIVE.get() is None
        assert record["outcome"] == "raised"
        events.append("clock_after_rejection")
        raise RuntimeError("measurement failure must not mask original")

    monkeypatch.setattr(MTLSHumanEngineTransport, "_request", delegated)
    monkeypatch.setattr(diagnostics, "failed_clock", measurement)
    with pytest.raises(type(error)) as caught:
        await wrapper(diagnostics)._request(wire(), query=False)
    assert caught.value is error
    assert events == (["delegate", "clock_after_rejection"] if isinstance(error, Exception) else ["delegate"])
    assert diag._ACTIVE.get() is None


@pytest.mark.asyncio
async def test_concurrent_requests_keep_separate_context_without_serializing(monkeypatch, diagnostics):
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def delegated(self, command, *, query):
        before = diag._ACTIVE.get()
        if query:
            first_entered.set()
            await second_entered.wait()
        else:
            await first_entered.wait()
            second_entered.set()
        assert diag._ACTIVE.get() is before
        assert before["method"] == ("GET" if query else "POST")
        return None

    monkeypatch.setattr(MTLSHumanEngineTransport, "_request", delegated)
    target = wrapper(diagnostics)
    assert await asyncio.gather(
        target._request(wire(), query=True), target._request(wire(), query=False)
    ) == [None, None]
    assert len(diagnostics.requests) == 2
    assert diagnostics.requests[0] is not diagnostics.requests[1]


def test_actual_verifier_exception_branch_is_observed_without_replacement(diagnostics):
    record = {"trace": []}
    token = diag._ACTIVE.set(record)
    try:
        with pytest.raises(ProjectionError):
            verify_engine_receipt(b"{}", wire())
    finally:
        diag._ACTIVE.reset(token)
    errors = [e for e in record["trace"] if e["event"] == "exception"]
    assert any(e["exception_type"].endswith("ValidationError") for e in errors)
    assert any(e["exception_type"].endswith("ProjectionError") for e in errors)
    assert all(e["line"] > 0 and e["host_utc"] and e["pid"] for e in errors)
    assert "receipt timestamp unavailable" not in json.dumps(record)


def test_existing_trace_hook_is_not_replaced():
    previous = sys.gettrace()

    def existing(frame, event, arg):
        return None

    try:
        sys.settrace(existing)
        with pytest.raises(RuntimeError, match="refuses to replace"):
            diag.install()
        assert sys.gettrace() is existing
    finally:
        sys.settrace(previous)


def test_serialized_packet_excludes_config_keys_and_exception_strings(diagnostics):
    diagnostics.config = SimpleNamespace(password="DO_NOT_CAPTURE", key="DO_NOT_CAPTURE")
    diagnostics.requests.append({"trace": [], "exception_type": "builtins.ValueError"})
    diagnostics.save()
    value = (diagnostics.directory / "boundary-diagnostics.json").read_text()
    assert "DO_NOT_CAPTURE" not in value
    assert json.loads(value)["requests"] == diagnostics.requests


def test_new_child_installs_same_observer_and_records_real_verifier_branch(
    tmp_path, _diagnostic_trace_lifecycle
):
    script = """
import json, os, sys
from pathlib import Path
from tests.support import human_relay_diagnostics as diag
from tests.unit.gateway.human.test_durable_projection import wire
from maezo.gateway.human.projection import verify_engine_receipt, ProjectionError
d = diag.BoundaryDiagnostics(Path(sys.argv[1]), None)
r = {"trace": []}
d.requests.append(r)
token = diag._ACTIVE.set(r)
try:
    verify_engine_receipt(b"{}", wire())
except ProjectionError:
    pass
finally:
    diag._ACTIVE.reset(token)
    d.save()
"""
    inherited_cov = _diagnostic_trace_lifecycle
    if inherited_cov:
        instrumented = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / "instrumented")],
            capture_output=True,
            env={**os.environ, **inherited_cov},
            check=False,
        )
        assert instrumented.returncode != 0
        assert b"disposable diagnostic refuses to replace another Python trace hook" in instrumented.stderr

    assert not set(_COV_CORE_KEYS).intersection(os.environ)
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, check=True)
    assert child.stdout == child.stderr == b""
    value = json.loads((tmp_path / "boundary-diagnostics.json").read_text())
    assert value["pid"] != os.getpid()
    errors = [e for e in value["requests"][0]["trace"] if e["event"] == "exception"]
    assert any(e["exception_type"].endswith("ProjectionError") for e in errors)
    assert all(e["pid"] == value["pid"] for e in errors)
