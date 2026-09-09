"""Offline transport/clock controls of the actual integration startup helper; no engine."""

from types import SimpleNamespace

import httpx
import pytest

from tests.integration import test_portal_engine_d7_package as package


@pytest.fixture
def poll(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[], requests=[], replies=[], durations=[])

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
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(package, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    monkeypatch.setattr(package, "client", lambda purpose: Connection())
    return clock


def ready():
    return httpx.Response(200, json={"ready": True, "capabilities": ["synthetic"]})


@pytest.mark.parametrize(
    "exception", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout]
)
def test_transient_timeout_recovers_via_real_wait_ready(poll, exception):
    poll.replies = [exception("synthetic startup timeout"), ready()]
    package.wait_ready()
    assert len(poll.requests) == 2 and poll.sleeps == [0.25]
    assert all(path == "/engine-rest/maezo/v1/readiness" for path, _ in poll.requests)


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


def test_persistent_timeouts_expire_without_extra_request(poll):
    poll.replies = [httpx.ConnectTimeout("synthetic")] * 6
    poll.durations = [15] * 5 + [13.75]
    with pytest.raises(pytest.fail.Exception, match="readiness did not recover"):
        package.wait_ready()
    assert poll.now == 90 and len(poll.requests) == 6
    assert max(poll.sleeps) <= 0.25


@pytest.mark.parametrize(
    "body", [{"ready": False, "capabilities": ["x"]}, {"ready": True, "capabilities": []}, {}]
)
def test_success_status_does_not_hide_invalid_readiness_body(poll, body):
    poll.replies = [httpx.Response(200, json=body)]
    with pytest.raises((AssertionError, KeyError)):
        package.wait_ready()
    assert len(poll.requests) == 1 and poll.sleeps == []


@pytest.mark.parametrize("status", [301, 401, 403, 503])
def test_non_success_status_never_counts_as_ready(poll, status):
    poll.replies = [httpx.Response(status, json={"ready": True, "capabilities": ["x"]})] * 6
    poll.durations = [15] * 5 + [13.75]
    with pytest.raises(pytest.fail.Exception):
        package.wait_ready()
    assert len(poll.requests) == 6


def test_unrelated_failure_is_not_retried(poll):
    poll.replies = [RuntimeError("programming failure")]
    with pytest.raises(RuntimeError):
        package.wait_ready()
    assert len(poll.requests) == 1 and poll.sleeps == []
