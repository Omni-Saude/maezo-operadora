"""Bounded D7 recovery evidence; transport failures never establish a refusal or readiness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx


async def wait_authenticated_readiness(
    probe: Callable[[float], Awaitable[httpx.Response]],
    policy_digest: Callable[[], str],
    evidence: Path,
    *,
    budget: float = 90,
    interval: float = 0.25,
) -> None:
    """Bound network probes and readiness admission to one event-loop deadline.

    Synchronous policy/evidence file I/O is not asynchronously preemptible. Its
    elapsed time consumes the same budget; never start a new probe afterward.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + budget
    attempt = 0
    while (remaining := deadline - loop.time()) > 0:
        record: dict[str, object] = {"attempt": attempt, "outcome": "PENDING"}
        try:
            expected = policy_digest()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("policy read exhausted recovery deadline")
            async with asyncio.timeout_at(deadline):
                response = await probe(min(15, remaining))
            record.update(
                status=response.status_code,
                body_bytes=len(response.content),
                body_sha256=hashlib.sha256(response.content).hexdigest(),
            )
            if response.status_code == 200:
                body = response.json()
                assert isinstance(body, dict), "readiness must be an object"
                assert body.get("ready") is True, "readiness must be explicitly true"
                capabilities = body.get("capabilities")
                assert isinstance(capabilities, list) and capabilities, "actual capabilities required"
                assert all(
                    isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in capabilities
                ), "capability digests required"
                assert body.get("policy_digest") == expected == policy_digest(), "current policy required"
                assert loop.time() < deadline, "readiness arrived after the recovery deadline"
                record["outcome"] = "READY"
                return
        except (
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            TimeoutError,
        ) as exc:
            record["transport_exception"] = type(exc).__name__
        except BaseException as exc:
            record.update(outcome="FAILED", exception_type=type(exc).__name__)
            raise
        finally:
            record["elapsed_millis"] = int((loop.time() - started) * 1000)
            (evidence / f"attempt-{attempt:04d}.json").write_text(json.dumps(record) + "\n")
        attempt += 1
        remaining = deadline - loop.time()
        if remaining > 0:
            await asyncio.sleep(min(interval, remaining))
    raise AssertionError("actual authenticated current-policy capability readiness did not recover")


@contextmanager
def preserve_restoration_errors(restore: Callable[[], None], evidence: Path) -> Iterator[None]:
    """Always restore; retain both original tracebacks if the fault and restoration fail."""
    primary: BaseException | None = None
    restoration: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary = exc
    try:
        restore()
    except BaseException as exc:
        restoration = exc
    evidence_failure: BaseException | None = None
    try:
        (evidence / "phases.json").write_text(
            json.dumps(
                {
                    "primary_exception_type": type(primary).__name__ if primary else None,
                    "restoration_exception_type": type(restoration).__name__ if restoration else None,
                }
            )
            + "\n"
        )
    except BaseException as exc:
        evidence_failure = exc
    failures = [exc for exc in (primary, restoration, evidence_failure) if exc is not None]
    if len(failures) > 1:
        raise BaseExceptionGroup("D7 fault/restoration/evidence failures", failures)
    if failures:
        raise failures[0]


def naive_utc_epoch_millis(value: datetime) -> int:
    """For an actually verified UTC native timestamp-without-time-zone column only."""
    assert isinstance(value, datetime) and value.tzinfo is None, "naive native timestamp required"
    assert value.microsecond % 1000 == 0, "native Java lock timestamp must have millisecond precision"
    delta = value.replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
