"""Unit tests for `maezo.runtime.worker_runtime.service` (T1.1 §3/§10/§12).

No engine — exercises the bootstrap (`register_default_workers`), the readiness checks
(fail-closed on partial topic coverage), and `WorkerState`. The full `run()` lifecycle against a
real engine is covered by `tests/integration/`.
"""

from __future__ import annotations

import asyncio
import contextlib

from maezo.runtime.worker_runtime.service import (
    WorkerState,
    _expected_worker_topics,
    build_readiness_checks,
    register_default_workers,
)
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers.auth import AnalyzeRequestWorker
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness


def test_register_default_workers_registers_all_15_workerbase_modules() -> None:
    """auth (6) + escalation (3) + lgpd (6) = 15, per ADR-0026's verified census — the 13
    function-based modules are explicitly OUT of T1.1 scope (T1.2/ADR-0026)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    assert len(harness.registered_topics) == 15
    assert harness.registry.count() == 15


def test_register_default_workers_topics_match_expected_prefixes() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    topics = set(harness.registered_topics)
    assert "operadora.auth.analyze_request" in topics
    assert "operadora.escalation.notify_team" in topics
    assert "operadora.lgpd.execute_erasure" in topics


def test_register_default_workers_idempotent() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)
    register_default_workers(harness)
    assert len(harness.registered_topics) == 15


def test_expected_worker_topics_matches_a_live_harness() -> None:
    """`_expected_worker_topics()` (the readiness check's source of truth) must always agree
    with what `register_default_workers` actually produces on a live harness — this is the
    self-consistency invariant the fail-closed readiness check relies on."""
    live = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    register_default_workers(live)

    assert _expected_worker_topics() == frozenset(live.registered_topics)


# ---------------------------------------------------------------------------
# Readiness checks (fail-closed, design §12)
# ---------------------------------------------------------------------------


def _state(**overrides: object) -> WorkerState:
    settings = WorkerRuntimeSettings()
    return WorkerState(settings=settings, **overrides)  # type: ignore[arg-type]


async def test_workers_registered_unhealthy_when_harness_missing() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["workers_registered"]()
    assert result.healthy is False
    assert "not constructed" in (result.detail or "")


async def test_workers_registered_unhealthy_when_topics_missing() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    harness.register_worker(AnalyzeRequestWorker())
    state = _state(
        harness=harness,
        expected_topics=frozenset({"operadora.auth.analyze_request", "operadora.escalation.notify_team"}),
    )

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["workers_registered"]()
    assert result.healthy is False
    assert "operadora.escalation.notify_team" in (result.detail or "")


async def test_workers_registered_healthy_when_full_coverage() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    register_default_workers(harness)
    state = _state(harness=harness, expected_topics=_expected_worker_topics())

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["workers_registered"]()
    assert result.healthy is True


async def test_engine_reachable_false_without_transport() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["engine_reachable"]()
    assert result.healthy is False


async def test_engine_reachable_reflects_harness_flag() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    state = _state(harness=harness)
    checks = {c.__name__: c for c in build_readiness_checks(state)}

    result = await checks["engine_reachable"]()
    assert result.healthy is True  # harness starts optimistic

    harness._engine_reachable = False
    result = await checks["engine_reachable"]()
    assert result.healthy is False


async def test_harness_running_false_before_spawn() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["harness_running"]()
    assert result.healthy is False


async def test_harness_running_true_while_task_alive() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w", poll_interval_ms=10)
    task = asyncio.create_task(harness.run())
    await asyncio.sleep(0.01)
    state = _state(harness_task=task)

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["harness_running"]()
    assert result.healthy is True

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_kafka_ready_always_healthy_with_explicit_detail() -> None:
    """No worker registered by this build declares a Kafka dependency — see service.py's
    `kafka_ready` docstring. The check is present (design §12 lists it) but never gates
    readiness until a T1.2 module actually needs Kafka."""
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["kafka_ready"]()
    assert result.healthy is True
    assert result.detail is not None


def test_worker_state_is_live_and_harness_running_helpers() -> None:
    state = _state()
    assert state.is_live() is True
    assert state.harness_running() is False
    state.live = False
    assert state.is_live() is False
