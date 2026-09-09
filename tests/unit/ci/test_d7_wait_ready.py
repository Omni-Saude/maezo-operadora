"""Offline transport/clock controls of the actual integration startup helper; no engine."""

import json
from types import SimpleNamespace

import httpx
import pytest

from tests.integration import test_portal_engine_d7_package as package


@pytest.fixture
def poll(monkeypatch, tmp_path):
    root = tmp_path / "fixture"
    root.mkdir(mode=0o700)
    (root / "d7-fixture.json").write_text(json.dumps({"fixture_only": True, "tenant": "relay_" + "a" * 24}))
    (root / "boundary.json").write_text("{}")
    monkeypatch.setenv("MAEZO_D7_PACKAGE_FIXTURE", str(root))
    monkeypatch.setenv("MAEZO_D7_READINESS_RUN_ID", "d7-observer-12345678")
    monkeypatch.setenv("MAEZO_D7_READINESS_SOURCE_SHA", "1" * 40)
    monkeypatch.setenv("MAEZO_D7_READINESS_SOURCE_TREE", "2" * 40)
    clock = SimpleNamespace(
        now=0.0,
        sleeps=[],
        requests=[],
        replies=[],
        durations=[],
        root=root,
        observation=root / "readiness-attempts.json",
    )

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path, **kwargs):
            clock.requests.append((path, kwargs))
            clock.now += clock.durations.pop(0) if clock.durations else 0
            item = clock.replies.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(package, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    monkeypatch.setattr(package, "client", lambda purpose: Connection())
    return clock


def ready():
    return httpx.Response(200, json={"ready": True, "capabilities": ["synthetic"]})


def observation(poll):
    return json.loads(poll.observation.read_text())


def assert_closed_attempt(item):
    assert set(item) in (
        {"index", "started_ms", "ended_ms", "kind", "status"},
        {"index", "started_ms", "ended_ms", "kind", "transport_class"},
    )
    assert type(item["index"]) is int and item["index"] >= 1
    assert type(item["started_ms"]) is int and item["started_ms"] >= 0
    assert type(item["ended_ms"]) is int and item["ended_ms"] >= item["started_ms"]


@pytest.mark.parametrize(
    "exception", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout]
)
def test_transient_timeout_recovers_via_real_wait_ready(poll, exception):
    secret = "PRIVATE-EXCEPTION-TEXT"
    poll.replies = [exception(secret), ready()]
    package.wait_ready()
    assert len(poll.requests) == 2 and poll.sleeps == [0.25]
    assert all(path == "/engine-rest/maezo/v1/readiness" for path, _ in poll.requests)
    raw = poll.observation.read_text()
    assert secret not in raw
    report = json.loads(raw)
    assert report["completion"] == "ready"
    assert [item["kind"] for item in report["attempt_tail"]] == ["transport", "http"]
    assert report["attempt_tail"][-1]["status"] == 200


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError])
def test_existing_transient_network_errors_recover(poll, exception):
    poll.replies = [exception("synthetic"), ready()]
    package.wait_ready()
    assert len(poll.requests) == 2


def test_deadline_remains_ninety_seconds_and_late_response_cannot_pass(poll):
    poll.replies = [httpx.ConnectTimeout("synthetic"), ready()]
    poll.durations = [89.5, 0.25]
    with pytest.raises(pytest.fail.Exception, match="readiness did not recover"):
        package.wait_ready()
    assert poll.now == 90
    assert [options["timeout"] for _, options in poll.requests] == [15, 0.25]
    assert observation(poll)["completion"] == "deadline_exhausted"


def test_persistent_timeouts_expire_without_extra_request(poll):
    poll.replies = [httpx.ConnectTimeout("synthetic")] * 6
    poll.durations = [15] * 5 + [13.75]
    with pytest.raises(pytest.fail.Exception, match="readiness did not recover"):
        package.wait_ready()
    assert poll.now == 90 and len(poll.requests) == 6
    assert max(poll.sleeps) <= 0.25
    report = observation(poll)
    assert report["attempt_count"] == 6
    assert all(item["transport_class"] == "connect_timeout" for item in report["attempt_tail"])


@pytest.mark.parametrize(
    "body", [{"ready": False, "capabilities": ["x"]}, {"ready": True, "capabilities": []}, {}]
)
def test_success_status_does_not_hide_invalid_readiness_body(poll, body):
    poll.replies = [httpx.Response(200, json=body)]
    with pytest.raises((AssertionError, KeyError)):
        package.wait_ready()
    assert len(poll.requests) == 1 and poll.sleeps == []
    report = observation(poll)
    assert report["completion"] == "malformed_success"
    assert report["attempt_tail"][0]["status"] == 200


@pytest.mark.parametrize("status", [301, 401, 403, 503])
def test_non_success_status_never_counts_as_ready(poll, status):
    poll.replies = [httpx.Response(status, json={"ready": True, "capabilities": ["x"]})] * 6
    poll.durations = [15] * 5 + [13.75]
    with pytest.raises(pytest.fail.Exception):
        package.wait_ready()
    assert len(poll.requests) == 6
    report = observation(poll)
    assert report["attempt_count"] == 6
    assert {item["status"] for item in report["attempt_tail"]} == {status}


def test_unrelated_failure_is_not_retried(poll):
    poll.replies = [RuntimeError("programming failure")]
    with pytest.raises(RuntimeError):
        package.wait_ready()
    assert len(poll.requests) == 1 and poll.sleeps == []


def test_report_has_exact_closed_shape_and_run_source_binding(poll):
    poll.replies = [httpx.Response(503), ready()]
    package.wait_ready("worker")
    report = observation(poll)
    assert set(report) == {
        "schema",
        "run_id",
        "source_sha",
        "source_tree",
        "purpose",
        "budget_ms",
        "request_cap_ms",
        "backoff_cap_ms",
        "attempt_count",
        "tail_limit",
        "attempt_tail",
        "completion",
    }
    assert report == {
        **report,
        "schema": "maezo.d7.readiness-attempts.v1",
        "run_id": "d7-observer-12345678",
        "source_sha": "1" * 40,
        "source_tree": "2" * 40,
        "purpose": "worker",
        "budget_ms": 90_000,
        "request_cap_ms": 15_000,
        "backoff_cap_ms": 250,
        "attempt_count": 2,
        "tail_limit": 32,
        "completion": "ready",
    }
    assert [item["index"] for item in report["attempt_tail"]] == [1, 2]
    for item in report["attempt_tail"]:
        assert_closed_attempt(item)
    assert poll.observation.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "exception,category",
    [
        (httpx.ConnectTimeout("secret"), "connect_timeout"),
        (httpx.ReadTimeout("secret"), "read_timeout"),
        (httpx.WriteTimeout("secret"), "write_timeout"),
        (httpx.PoolTimeout("secret"), "pool_timeout"),
        (httpx.TimeoutException("secret"), "timeout"),
        (httpx.ConnectError("secret"), "connect_error"),
        (httpx.ReadError("secret"), "read_error"),
        (httpx.RemoteProtocolError("secret"), "remote_protocol_error"),
    ],
)
def test_transport_family_has_closed_class_without_exception_text(poll, exception, category):
    poll.replies = [exception, ready()]
    package.wait_ready()
    raw = poll.observation.read_text()
    assert "secret" not in raw
    assert observation(poll)["attempt_tail"][0]["transport_class"] == category


def test_attempt_tail_is_finite_and_retains_monotonic_indices(poll):
    poll.replies = [httpx.Response(503)] * 40 + [ready()]
    package.wait_ready()
    report = observation(poll)
    assert report["attempt_count"] == 41 and len(report["attempt_tail"]) == 32
    assert [item["index"] for item in report["attempt_tail"]] == list(range(10, 42))
    assert [item["started_ms"] for item in report["attempt_tail"]] == sorted(
        item["started_ms"] for item in report["attempt_tail"]
    )


def test_malicious_success_body_cannot_escape_and_fails_directly(poll):
    secret = "PRIVATE-BODY-VALUE"
    poll.replies = [httpx.Response(200, content=secret)]
    with pytest.raises(AssertionError, match="malformed readiness success") as failure:
        package.wait_ready()
    assert secret not in str(failure.value)
    assert secret not in poll.observation.read_text()
    assert len(poll.requests) == 1 and poll.sleeps == []


def test_interruption_is_recorded_without_being_swallowed(poll):
    poll.replies = [KeyboardInterrupt("PRIVATE-INTERRUPT-TEXT")]
    with pytest.raises(KeyboardInterrupt):
        package.wait_ready()
    raw = poll.observation.read_text()
    assert "PRIVATE-INTERRUPT-TEXT" not in raw
    report = json.loads(raw)
    assert report["completion"] == "interrupted" and report["attempt_count"] == 0


def test_recording_failure_cannot_promote_valid_readiness(poll, monkeypatch):
    poll.replies = [ready()]

    def fail_write(*_args, **_kwargs):
        raise OSError("PRIVATE-WRITE-FAILURE")

    monkeypatch.setattr(package, "_write_readiness_observation", fail_write)
    with pytest.raises(pytest.fail.Exception, match="readiness observation recording failed") as failure:
        package.wait_ready()
    assert "PRIVATE-WRITE-FAILURE" not in str(failure.value)
    assert len(poll.requests) == 1


def test_intermittent_recording_failure_persists_failed_completion(poll, monkeypatch):
    poll.replies = [ready()]
    actual_write = package._write_readiness_observation
    calls = 0

    def fail_once(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("PRIVATE-FIRST-WRITE-FAILURE")
        actual_write(value)

    monkeypatch.setattr(package, "_write_readiness_observation", fail_once)
    with pytest.raises(pytest.fail.Exception, match="readiness observation recording failed"):
        package.wait_ready()
    assert observation(poll)["completion"] == "recording_failed"


def test_final_ready_record_cannot_cross_global_deadline(poll, monkeypatch):
    poll.replies = [ready()]
    poll.durations = [89.5]
    actual_write = package._write_readiness_observation

    def slow_final_write(value):
        if value["completion"] == "ready":
            poll.now += 1
        actual_write(value)

    monkeypatch.setattr(package, "_write_readiness_observation", slow_final_write)
    with pytest.raises(pytest.fail.Exception, match="readiness did not recover"):
        package.wait_ready()
    assert poll.now == 90.5
    assert observation(poll)["completion"] == "deadline_exhausted"


def test_symlink_observation_path_is_refused_without_touching_target(poll):
    outside = poll.root.parent / "outside"
    outside.write_text("preserve")
    poll.observation.symlink_to(outside)
    poll.replies = [ready()]
    with pytest.raises(pytest.fail.Exception, match="readiness observation recording failed"):
        package.wait_ready()
    assert outside.read_text() == "preserve"
