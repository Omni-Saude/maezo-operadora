"""Integration acceptance test for T1.1 (design §14 point 3, plan T1.1 acceptance criterion):

  "Integration test: engine task -> worker executes -> completes/fails with retry."

Runs the REAL `WorkerHarness` + `CibSevenWorkerTransport` against a REAL CIB Seven engine
(`docker compose --profile core up`, ADR-0011 — never a mock in this file). Deploys a minimal
ad hoc test-only BPMN (NOT under `spec/` — constraint 5) with a single external task per test
process, starts an instance, and asserts:

  1. happy path    — task fetched -> handler completes -> engine history shows the process
                      instance ended.
  2. retry/incident — handler always fails (transient) -> the engine's `retries` decrements each
                      redelivery (client-computed, engine does NOT auto-decrement, design §2) ->
                      an incident opens at retries == 0 (never before).
  3. graceful drain — a task is fetched (locked) and, before its slow handler finishes,
                      `WorkerHarness.drain()` is forced with a near-zero deadline: the straggler
                      is cancelled and explicitly unlocked (design §8) -> the SAME task is
                      immediately refetchable by a different worker (no need to wait out
                      `lock_duration_ms`).

If the engine is unreachable, every test in this module SKIPS via the session-scoped
`_skip_if_engine_unreachable` autouse fixture (`conftest.py`) with an explicit, loud reason —
never a silent pass, never a fabricated result (constraint 3).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
import pytest

from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    TopicSubscription,
    WorkerHarness,
)

from .conftest import RUN_ID, deploy_process, history_process_instance, list_incidents, start_process

pytestmark = pytest.mark.integration


async def test_happy_path_fetch_complete_history(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    process_key = f"t11_it_happy_{RUN_ID}"
    topic = f"t11.it.happy.{RUN_ID}"
    await deploy_process(
        engine_client, process_key=process_key, topic=topic, deployment_name=f"t11-happy-{RUN_ID}"
    )
    process_instance_id = await start_process(engine_client, process_key=process_key, business_key="bk-happy")

    transport = CibSevenWorkerTransport(engine_base_url)
    harness = WorkerHarness(transport, worker_id=f"it-worker-{RUN_ID}", async_response_timeout_ms=2_000)

    completed: list[str] = []

    async def handler(task: ExternalTask) -> dict[str, Any]:
        completed.append(task.task_id)
        return {"handled": True}

    harness.register(topic, handler)

    run_task = asyncio.create_task(harness.run())
    try:
        for _ in range(60):
            if completed:
                break
            await asyncio.sleep(0.5)
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await transport.close()

    assert completed, "external task was never fetched/completed by the real harness"

    history = await history_process_instance(engine_client, process_instance_id)
    assert history["endTime"] is not None, (
        f"process instance {process_instance_id} did not reach an end event after task completion "
        f"(history: {history})"
    )


async def test_transient_failure_retries_decrement_then_incident(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    process_key = f"t11_it_fail_{RUN_ID}"
    topic = f"t11.it.fail.{RUN_ID}"
    await deploy_process(
        engine_client, process_key=process_key, topic=topic, deployment_name=f"t11-fail-{RUN_ID}"
    )
    process_instance_id = await start_process(engine_client, process_key=process_key, business_key="bk-fail")

    transport = CibSevenWorkerTransport(engine_base_url)
    # Small max_retry_attempts so the test observes exhaustion quickly: seed=2 -> first failure
    # reports retries=1 (redeliverable) -> second failure reports retries=0 (incident).
    harness = WorkerHarness(
        transport, worker_id=f"it-worker-{RUN_ID}", max_retry_attempts=2, async_response_timeout_ms=2_000
    )

    async def always_fails(task: ExternalTask) -> None:
        raise RuntimeError("integration-test transient failure")

    harness.register(topic, always_fails)

    try:
        # First delivery: fetch + handle directly (tight control over the sequence, still the
        # REAL harness dispatch + REAL transport — design §14 point 3 "run the real
        # harness+transport").
        first = await _fetch_one(transport, harness, worker_id=f"it-worker-{RUN_ID}", topic=topic)
        await harness._handle(first)

        incidents_after_first = await list_incidents(engine_client, process_instance_id)
        assert incidents_after_first == [], (
            "an incident opened after the FIRST failure — retries were not honored "
            f"(client must report retries=1, not 0): {incidents_after_first}"
        )

        # Wait out the jittered retryTimeout (harness computed it — bounded, a few seconds) and
        # re-fetch the same task for its second (final) delivery.
        second = await _fetch_one(
            transport, harness, worker_id=f"it-worker-{RUN_ID}", topic=topic, timeout_s=20.0
        )
        assert second.task_id == first.task_id
        assert second.retries == 1, f"engine did not persist the client-reported retries=1: {second.retries}"
        await harness._handle(second)

        incidents_after_second = await list_incidents(engine_client, process_instance_id)
        assert len(incidents_after_second) == 1, (
            f"expected exactly one incident after retries were exhausted (retries=0): "
            f"{incidents_after_second}"
        )
    finally:
        await transport.close()


async def test_graceful_shutdown_drain_unlocks_task_for_immediate_refetch(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    process_key = f"t11_it_drain_{RUN_ID}"
    topic = f"t11.it.drain.{RUN_ID}"
    await deploy_process(
        engine_client, process_key=process_key, topic=topic, deployment_name=f"t11-drain-{RUN_ID}"
    )
    await start_process(engine_client, process_key=process_key, business_key="bk-drain")

    transport = CibSevenWorkerTransport(engine_base_url)
    harness = WorkerHarness(transport, worker_id=f"it-worker-{RUN_ID}", lock_duration_ms=60_000)

    release = asyncio.Event()

    async def slow_handler(task: ExternalTask) -> dict[str, Any]:
        await release.wait()  # never released within the test -> always a straggler at drain()
        return {}

    harness.register(topic, slow_handler)

    try:
        task = await _fetch_one(transport, harness, worker_id=f"it-worker-{RUN_ID}", topic=topic)
        harness._spawn(task)
        assert harness.inflight_count == 1

        # Drain with a near-zero deadline: the handler is awaiting `release` forever, so it is
        # ALWAYS still pending -> drain() cancels it and explicitly unlocks the task (design §8).
        await harness.drain(0.05)
        assert harness.inflight_count == 0

        # lock_duration_ms=60_000 above means, WITHOUT the explicit unlock, this task would not
        # be refetchable for another minute. Immediate refetchability is the direct, real-engine
        # proof that `drain()` actually called POST /external-task/{id}/unlock.
        refetched = await _fetch_one(
            transport, harness, worker_id=f"it-worker-2-{RUN_ID}", topic=topic, timeout_s=5.0
        )
        assert refetched.task_id == task.task_id
    finally:
        release.set()
        await transport.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _fetch_one(
    transport: CibSevenWorkerTransport,
    harness: WorkerHarness,
    *,
    worker_id: str,
    topic: str,
    timeout_s: float = 15.0,
) -> ExternalTask:
    """Poll `fetch_and_lock` (short client-side interval — this helper does NOT use the
    engine's long-poll, to keep retry-timing assertions in the caller's control) until exactly
    one task on `topic` is returned, or raise on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        tasks = await transport.fetch_and_lock(
            worker_id,
            [TopicSubscription(topic, harness._lock_duration_ms)],
            max_tasks=1,
            async_response_timeout_ms=500,
        )
        if tasks:
            return tasks[0]
        await asyncio.sleep(0.5)
    raise AssertionError(f"no task fetched on topic {topic!r} within {timeout_s}s")
