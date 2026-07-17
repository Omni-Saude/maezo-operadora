"""External-task worker harness (CIB Seven fetch-and-lock REST loop).

T1.1 runtime spine (design: ``docs/design/T1.1-runtime-spine.md``). ADR-0001: BPMN processes
call workers via External Task (the residual direction — engine -> worker, not agent -> tool).

Interface::

    harness = WorkerHarness(transport, worker_id="maezo-worker-01", lock_duration_ms=30_000)
    harness.register("operadora.escalation.notify_team", handler)   # raw async handler
    harness.register_worker(NotifyTeamWorker())                     # WorkerBase adapter
    await harness.run()          # long-poll loop, runs until cancelled
    await harness.stop()         # stop fetching (loop exits on next check)
    await harness.drain(20.0)    # wait for in-flight handlers, then unlock stragglers

Each raw handler::

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        ...  # idempotent business logic
        # raise WorkerBpmnError(code) to signal a modeled BPMN error
        # raise WorkerFailureError(msg, retries_left=n) to override the computed retry count
        # RETURN a dict of output variables (loaded on complete), or None for no variables.

The harness completes/fails/reports EXACTLY ONCE per task — handlers never call the transport
themselves (that would risk a double-complete: the second call fails because the task already
left its lock).

Retry ownership (design §9 — the crux this module exists to fix). The **engine** is the single
system of record for durable retry/incident state:

- The harness performs **no** in-process retry of a handler (that is `WorkerBase.run()`'s
  business, scoped to "transient, idempotent, within-lock", and defaults OFF for the runtime
  path via ``max_retries=1``. See ``FunctionWorker`` — T1.2/ADR-0026, not this module).
- On failure the harness computes ``retries = task.retries - 1`` (first delivery ``retries is
  None`` seeds from ``max_retry_attempts``) and reports it to the engine via
  ``transport.handle_failure``. The engine — never the client — decides whether to re-deliver
  (``retries > 0``) or open an incident (``retries == 0``).
  ``WorkerFailureError.retries_left`` overrides the computed value when a handler raises it
  explicitly.
- Guard errors (``PermissionError`` family — e.g. ``*NotHumanError``) and validation errors
  (``ValueError`` family) ALWAYS report ``retries=0`` — an immediate, engine-guaranteed incident.
  Retrying an L0 guard could drive an adverse action (ADR-0008); retrying bad/immutable input
  wastes the re-delivery — both must reach a human, never be retried.
- ``WorkerBpmnError`` is reported as a BPMN error **only** when its ``error_code`` is in the
  harness's ``bpmn_error_allowlist`` — a set of codes proven (by a boundary-proof gate, design
  §9) to have a matching ``bpmn:error@errorCode`` boundary event in every consuming process. An
  unmodeled ``bpmnError`` does not open an incident on CIB Seven 2.1.0 — it silently **ends the
  process scope** (live-verified hazard). A code not in the allowlist is therefore demoted to
  ``failure(retries=0)`` with a loud log line, never silently dropped. The CI-side static gate
  that computes/verifies this allowlist against ``spec/processes/bpmn/**`` is a follow-up (T1.1
  design §9 "Design requirement (fail-closed gate)"); this module implements the runtime-side
  refusal only.

Idempotency: handlers MUST be idempotent (same task_id -> same result) — the explicit-unlock
drain (design §8) can cause the engine to re-deliver a task whose handler already ran.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, runtime_checkable

import httpx
import structlog

from maezo.tools.workers.base import WorkerBase, WorkerRegistry

logger = structlog.get_logger(__name__)

# Also expose a stdlib logger for callers/tests that patch `logging.getLogger` — structlog's
# stdlib bridge is not configured in every test context (mirrors the dual-logging seam used by
# `maezo.runtime.log_phi`), so warnings/errors are never silently lost outside the structlog chain.
_stdlib_logger = logging.getLogger(__name__)

# Java `int32` (java.lang.Integer) bounds. Integers outside this range are typed as `Long`
# (int64) when written back to the engine — high-value BRL cents overflow int32 (ADR-0018 part
# 2; see `_to_camunda_var`).
_JAVA_INT32_MIN = -(2**31)
_JAVA_INT32_MAX = 2**31 - 1

#: Terminal dispatch outcomes emitted on the `maezo_worker_task_total` / `_duration_seconds`
#: metrics (design §13). `incident` is additive over v1's {completed, bpmn_error, failed} — it
#: marks the subset of `failed` reports where `retries == 0` (an engine incident was opened).
WORKER_TASK_OUTCOMES: frozenset[str] = frozenset({"completed", "bpmn_error", "failed", "incident"})


# --------------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalTask:
    """A task fetched from CIB Seven via `fetchAndLock`.

    `retries` is `None` on first delivery (the engine has not yet recorded a retry count for
    this task) and an `int` on redelivery. Kept for the retry-ownership computation (module
    docstring, design §9) — NOT present in v1's donor dataclass, added additively.
    """

    task_id: str
    topic: str
    process_instance_id: str
    business_key: str
    worker_id: str
    variables: dict[str, Any] = field(default_factory=dict)
    retries: int | None = None


class TopicSubscription(NamedTuple):
    """One `fetchAndLock` topic subscription: per-topic lock duration + variable scoping.

    `variables`, when not None, restricts which process variables the engine returns for tasks
    on this topic (least-privilege — PHI stays out of the general zone unless a worker declares
    it, design §5). `None` means "return all variables" (engine default).
    """

    topic_name: str
    lock_duration_ms: int
    variables: list[str] | None = None


class WorkerBpmnError(Exception):
    """Raise to signal a BPMN error to the engine (e.g. `ERR_ESC_NOTIFY_FAILED`).

    Reported as `bpmnError` ONLY when `error_code` is in the harness's `bpmn_error_allowlist`
    (module docstring) — otherwise demoted to `failure(retries=0)`.
    """

    def __init__(self, error_code: str, message: str = "") -> None:
        super().__init__(message or error_code)
        self.error_code = error_code


class WorkerFailureError(Exception):  # noqa: N818 — domain name predates the *Error suffix convention.
    """Raise to report a failure to the engine with an explicit retry budget.

    `retries_left` OVERRIDES the harness's computed `task.retries - 1` (design §9) — this is how
    a handler opts out of the default engine-computed decrement, e.g. to force an immediate
    incident (`retries_left=0`) for a condition it recognizes as non-transient.
    """

    def __init__(self, message: str, *, retries_left: int = 3) -> None:
        super().__init__(message)
        self.retries_left = retries_left


# Alias kept for compatibility with donor-ported call sites and handler readability.
WorkerFailure = WorkerFailureError


# A handler returns a Mapping of output variables (loaded on the harness's `complete`,
# EXACTLY ONCE) or None to complete with no variables. Handlers NEVER call `transport.complete`
# themselves (that would double-complete).
TaskHandler = Callable[["ExternalTask"], Coroutine[Any, Any, Mapping[str, Any] | None]]


def _to_camunda_var(value: Any) -> dict[str, Any]:
    """Type an output-variable value into the CIB Seven / Camunda variable wire format.

    Mirrors the canonical serialization used elsewhere in this codebase (`mcp_cibseven`), with
    the extension the workers need: objects/lists (e.g. a dossier) serialize as a `Json`
    variable (JSON string + implied structure), never as Python `repr()` — only then does the
    engine store/deserialize them as a structured process variable. A value already in variable
    format (`{"value": ...}`) passes through unchanged (idempotent).

    **Load-bearing**: money (BRL cents) and dossier round-trips depend on the `Long`/`Json`
    typing below — v1 fixtures assert it; preserved verbatim (design §5/§16.1).
    """
    if isinstance(value, dict) and "value" in value:
        return value
    if isinstance(value, bool):
        return {"value": value, "type": "Boolean"}
    if isinstance(value, int):
        # Fits Java int32 -> Integer; otherwise -> Long (int64). High-value BRL cents (e.g. a
        # R$50MM payment = 5,000,000,000 cents) overflow int32 and MUST be Long, or the engine
        # rejects the complete with "Cannot convert value '<n>' of type 'Integer' to java type
        # java.lang.Integer" (ADR-0018 part 2).
        fits_int32 = _JAVA_INT32_MIN <= value <= _JAVA_INT32_MAX
        return {"value": value, "type": "Integer" if fits_int32 else "Long"}
    if isinstance(value, float):
        return {"value": value, "type": "Double"}
    if isinstance(value, dict | list):
        return {
            "value": json.dumps(value, ensure_ascii=False, default=str),
            "type": "Json",
        }
    if value is None:
        return {"value": None, "type": "String"}
    return {"value": str(value), "type": "String"}


# --------------------------------------------------------------------------------------------
# Transport abstraction
# --------------------------------------------------------------------------------------------


@runtime_checkable
class WorkerTransport(Protocol):
    """Transport interface for the fetch-and-lock loop.

    `fetch_and_lock`'s signature is v2-specific (design §16.2, NOT preserved from v1): topics
    carry per-topic lock duration + variable scoping, and `async_response_timeout_ms` drives the
    engine's long-poll (design §6). Every other method preserves v1's name/shape and adds
    `extend_lock`/`unlock` (design §8, additive).
    """

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]: ...

    async def complete(
        self,
        task_id: str,
        worker_id: str,
        variables: dict[str, Any],
    ) -> None: ...

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None: ...

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None: ...

    async def extend_lock(
        self,
        task_id: str,
        worker_id: str,
        *,
        new_duration_ms: int,
    ) -> None: ...

    async def unlock(self, task_id: str) -> None: ...

    async def close(self) -> None: ...


class CibSevenWorkerTransport:
    """Real transport: CIB Seven External Task REST API (`/engine-rest/external-task/*`).

    CIB Seven 2.x preserves the Camunda 7 REST contract under `/engine-rest` (design §2). Never
    swallows a transport error into a fake-empty success (design §6/§13 fail-closed rule) — every
    method raises `httpx.HTTPStatusError`/`httpx.RequestError` on failure; the caller (harness
    loop) is responsible for backoff/readiness, never this transport.
    """

    def __init__(self, base_url: str, *, auth_token: str | None = None, timeout: float = 20.0) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        topic_payload: list[dict[str, Any]] = []
        for sub in topics:
            entry: dict[str, Any] = {"topicName": sub.topic_name, "lockDuration": sub.lock_duration_ms}
            if sub.variables is not None:
                entry["variables"] = sub.variables
            topic_payload.append(entry)
        payload = {
            "workerId": worker_id,
            "maxTasks": max_tasks,
            "usePriority": True,
            "asyncResponseTimeout": async_response_timeout_ms,
            "topics": topic_payload,
        }
        resp = await self._client.post("/external-task/fetchAndLock", json=payload)
        resp.raise_for_status()

        tasks: list[ExternalTask] = []
        for item in resp.json():
            variables: dict[str, Any] = {k: v.get("value") for k, v in (item.get("variables") or {}).items()}
            tasks.append(
                ExternalTask(
                    task_id=item["id"],
                    topic=item["topicName"],
                    process_instance_id=item.get("processInstanceId", ""),
                    business_key=item.get("businessKey", "") or "",
                    worker_id=item.get("workerId", worker_id),
                    variables=variables,
                    retries=item.get("retries"),
                )
            )
        return tasks

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        camunda_vars = {k: _to_camunda_var(v) for k, v in variables.items()}
        resp = await self._client.post(
            f"/external-task/{task_id}/complete",
            json={"workerId": worker_id, "variables": camunda_vars},
        )
        resp.raise_for_status()

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "errorMessage": error_message,
            "retries": retries,
            "retryTimeout": retry_timeout_ms,
        }
        if error_details:
            payload["errorDetails"] = error_details
        resp = await self._client.post(f"/external-task/{task_id}/failure", json=payload)
        resp.raise_for_status()

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "errorCode": error_code,
            "errorMessage": error_message,
        }
        if variables:
            payload["variables"] = {k: _to_camunda_var(v) for k, v in variables.items()}
        resp = await self._client.post(f"/external-task/{task_id}/bpmnError", json=payload)
        resp.raise_for_status()

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        resp = await self._client.post(
            f"/external-task/{task_id}/extendLock",
            json={"workerId": worker_id, "newDuration": new_duration_ms},
        )
        resp.raise_for_status()

    async def unlock(self, task_id: str) -> None:
        resp = await self._client.post(f"/external-task/{task_id}/unlock")
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


class FakeWorkerTransport:
    """In-memory transport double for unit tests. NEVER imported by production code.

    `.completed` / `.failures` / `.bpmn_errors` / `.unlocked` / `.extended` record every call for
    assertions. `.failures` is a 4-tuple `(task_id, error_message, retries, retry_timeout_ms)` —
    widened from v1's 2-tuple so retry-ownership tests can assert the computed/overridden retry
    count (design §16.2: fixtures adapt to the new retry semantics).
    """

    def __init__(self, tasks: list[ExternalTask] | None = None) -> None:
        self._tasks: list[ExternalTask] = list(tasks or [])
        self.completed: list[tuple[str, dict[str, Any]]] = []
        self.failures: list[tuple[str, str, int, int]] = []
        self.bpmn_errors: list[tuple[str, str]] = []
        self.unlocked: list[str] = []
        self.extended: list[tuple[str, int]] = []
        self.closed: bool = False

    def enqueue(self, task: ExternalTask) -> None:
        self._tasks.append(task)

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        topic_names = {sub.topic_name for sub in topics}
        matched: list[ExternalTask] = []
        remaining: list[ExternalTask] = []
        for task in self._tasks:
            if len(matched) < max_tasks and task.topic in topic_names:
                matched.append(task)
            else:
                remaining.append(task)
        self._tasks = remaining
        return matched

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        self.completed.append((task_id, variables))

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None:
        self.failures.append((task_id, error_message, retries, retry_timeout_ms))

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None:
        self.bpmn_errors.append((task_id, error_code))

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        self.extended.append((task_id, new_duration_ms))

    async def unlock(self, task_id: str) -> None:
        self.unlocked.append(task_id)

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------------------------
# Kafka publishing seam (T1.1 boundary — real producer wiring is a later task)
# --------------------------------------------------------------------------------------------


@runtime_checkable
class KafkaPublisher(Protocol):
    """Domain-event publishing seam injected into worker bootstraps (T1.2/ADR-0026).

    Not exercised by the 3 WorkerBase modules registered today (none declare a Kafka
    dependency) — present so the harness/bootstrap surface matches the donor contract v1's
    process-integration fixtures expect to port against (design §16.1).
    """

    async def publish(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None: ...


class FakeKafkaPublisher:
    """In-memory `KafkaPublisher` double for unit tests. NEVER imported by production code."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any], str | None]] = []

    async def publish(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None:
        self.published.append((topic, value, key))


# --------------------------------------------------------------------------------------------
# Observability (best-effort, never breaks dispatch — design §13)
# --------------------------------------------------------------------------------------------


def _emit_worker_task_outcome(
    *,
    tenant: str,
    topic: str,
    outcome: str,
    duration_seconds: float | None = None,
) -> None:
    """Record a dispatch outcome DEFENSIVELY — never raises into dispatch.

    Delegates to `maezo.platform.observability.record_worker_task_outcome`. Import is local to
    avoid a hard dependency of this module on the observability stack at import time (mirrors
    `WorkerBase.run()`'s own defensive metrics import, `base.py:148-152`).
    """
    try:
        if outcome not in WORKER_TASK_OUTCOMES:
            logger.debug("worker_task_metric_skipped", outcome=outcome, topic=topic)
            return
        from maezo.platform.observability import record_worker_task_outcome  # noqa: PLC0415

        record_worker_task_outcome(
            tenant=tenant or "unknown",
            topic=topic,
            outcome=outcome,
            duration_seconds=duration_seconds,
        )
    except Exception:  # noqa: BLE001 — defensive: a metric error must never break dispatch.
        logger.debug("worker_task_metric_emit_failed", topic=topic, outcome=outcome, exc_info=True)


# --------------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------------


class WorkerHarness:
    """Fetch-and-lock loop for CIB Seven external tasks.

    Each iteration:
      1. `fetchAndLock` (long-poll) across every registered topic.
      2. Every returned task is dispatched to its handler as a tracked background task (so the
         loop can immediately re-poll — design §6 point 3 — while `drain()` can still observe
         and bound in-flight work at shutdown).
      3. The handler's outcome is reported to the engine EXACTLY ONCE: `complete` | `bpmnError`
         (gate-proven codes only) | `failure` (retry ownership — module docstring, design §9).

    Idempotency is the handler's responsibility (the harness never deduplicates by task_id).
    """

    def __init__(
        self,
        transport: WorkerTransport,
        *,
        worker_id: str,
        tenant: str = "unknown",
        lock_duration_ms: int = 30_000,
        poll_interval_ms: int = 5_000,
        max_tasks_per_poll: int = 10,
        max_retry_attempts: int = 3,
        async_response_timeout_ms: int = 25_000,
        bpmn_error_allowlist: frozenset[str] | None = None,
        engine_unreachable_after: int = 3,
    ) -> None:
        self._transport = transport
        self._worker_id = worker_id
        # `tenant` is an OBSERVABILITY-ONLY dimension (bounded, non-PHI metric label). Defaults
        # to "unknown" so every existing construction call site (tests, readiness probes) keeps
        # working unchanged; the service passes `settings.tenant_id`.
        self._tenant = tenant
        self._lock_duration_ms = lock_duration_ms
        self._poll_interval_ms = poll_interval_ms
        self._max_tasks = max_tasks_per_poll
        self._max_retries = max_retry_attempts
        self._async_response_timeout_ms = async_response_timeout_ms
        self._bpmn_error_allowlist = bpmn_error_allowlist or frozenset()
        self._engine_unreachable_after = max(engine_unreachable_after, 1)

        self._handlers: dict[str, TaskHandler] = {}
        self._topic_variables: dict[str, list[str] | None] = {}
        self._registry = WorkerRegistry()
        self._running = False

        # In-flight tracking for drain()/unlock (design §8): task_id -> (asyncio.Task, ExternalTask).
        self._inflight: dict[str, tuple[asyncio.Task[None], ExternalTask]] = {}

        # Fetch-error / idle backoff + readiness state (design §6).
        self._consecutive_fetch_errors = 0
        self._idle_streak = 0
        self._engine_reachable = True
        self.fetch_errors_total = 0

    # -- registration -------------------------------------------------------------------------

    def register(self, topic: str, handler: TaskHandler, *, variables: list[str] | None = None) -> None:
        """Register a raw async handler for a topic. Idempotent (last registration wins)."""
        self._handlers[topic] = handler
        self._topic_variables[topic] = variables

    def register_worker(self, worker: WorkerBase) -> None:
        """Register a `WorkerBase` instance (today's 3 modules: auth/escalation/lgpd).

        Adds `worker` to the harness's own `WorkerRegistry` (ADR-0026 §Decisao 2 contract —
        `register_worker` delegates to it) AND wraps it into a raw async handler stored in the
        same `_handlers` table `.register()` uses, so `_handle`'s dispatch stays uniform.

        Sync/async bridge (design §7): `WorkerBase.run()` is synchronous by design and may
        internally `time.sleep` between in-process retries — running it on the event loop would
        stall every other in-flight task. The dispatcher therefore runs it via
        `asyncio.to_thread`, UNLESS the worker overrides `run_async` (detected by identity
        against the base-class default, which just re-enters `run()` synchronously and would
        defeat the bridge if awaited directly).
        """
        self._registry.register(worker.topic, worker)
        run_async_overridden = type(worker).run_async is not WorkerBase.run_async

        async def _adapter(task: ExternalTask, *, _worker: WorkerBase = worker) -> Mapping[str, Any] | None:
            if run_async_overridden:
                return await _worker.run_async(task.variables)
            return await asyncio.to_thread(_worker.run, task.variables)

        self.register(worker.topic, _adapter)

    @property
    def registered_topics(self) -> list[str]:
        return sorted(self._handlers)

    @property
    def registry(self) -> WorkerRegistry:
        """The `WorkerBase` sub-registry (only entries added via `register_worker`)."""
        return self._registry

    @property
    def engine_reachable(self) -> bool:
        """False after `engine_unreachable_after` consecutive `fetch_and_lock` failures."""
        return self._engine_reachable

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def _topic_subscriptions(self) -> list[TopicSubscription]:
        return [
            TopicSubscription(topic, self._lock_duration_ms, self._topic_variables.get(topic))
            for topic in self.registered_topics
        ]

    # -- run loop -------------------------------------------------------------------------------

    async def run(self) -> None:
        """Long-poll loop. Exits cleanly on `asyncio.CancelledError` (design §6/§8)."""
        self._running = True
        logger.info("worker_harness_started", worker_id=self._worker_id, topics=self.registered_topics)
        try:
            while self._running:
                try:
                    tasks = await self._transport.fetch_and_lock(
                        self._worker_id,
                        self._topic_subscriptions(),
                        max_tasks=self._max_tasks,
                        async_response_timeout_ms=self._async_response_timeout_ms,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — fail-closed backoff, never a silent []`.
                    self._consecutive_fetch_errors += 1
                    self.fetch_errors_total += 1
                    if self._consecutive_fetch_errors >= self._engine_unreachable_after:
                        self._engine_reachable = False
                    logger.warning(
                        "fetch_and_lock_failed",
                        error=str(exc),
                        consecutive_failures=self._consecutive_fetch_errors,
                    )
                    await self._error_backoff()
                    continue

                self._consecutive_fetch_errors = 0
                self._engine_reachable = True

                if tasks:
                    self._idle_streak = 0
                    for task in tasks:
                        self._spawn(task)
                else:
                    await self._idle_backoff()
        except asyncio.CancelledError:
            logger.info("worker_harness_cancelled", worker_id=self._worker_id)
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the loop (v1-compatible signature). Does not itself drain in-flight work —
        callers that need bounded drain + explicit unlock should also call `drain()`."""
        self._running = False

    async def drain(self, deadline_s: float) -> None:
        """Wait up to `deadline_s` for in-flight handlers, then cancel + unlock stragglers.

        Design §8 step 3-4: completed handlers `complete` normally during the wait; anything
        still locked-but-not-completed at the deadline is explicitly unlocked so the engine can
        re-deliver it to a surviving replica immediately (rather than waiting out
        `lock_duration_ms`, v1's only mechanism).
        """
        pending = [fut for fut, _task in self._inflight.values()]
        if not pending:
            return
        _done, still_pending = await asyncio.wait(pending, timeout=max(deadline_s, 0.0))
        if not still_pending:
            return
        stragglers = [(fut, task) for fut, task in self._inflight.values() if fut in still_pending]
        for fut, _task in stragglers:
            fut.cancel()
        for fut, ext_task in stragglers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await fut
            with contextlib.suppress(Exception):
                await self._transport.unlock(ext_task.task_id)
                logger.warning(
                    "worker_task_unlocked_on_drain",
                    task_id=ext_task.task_id,
                    topic=ext_task.topic,
                )

    async def _idle_backoff(self) -> None:
        """Full-jitter backoff after an EMPTY fetch (design §6 point 4).

        With engine long-polling, the common idle wait is already the engine's
        `asyncResponseTimeout` — this jitter only spaces immediate reconnects (e.g. when the
        engine returns early with no tasks) so N replicas don't hammer the engine in lockstep.
        """
        self._idle_streak += 1
        base_s = max(self._poll_interval_ms / 1000.0, 0.01)
        cap_s = max(base_s, 30.0)
        ceiling = min(base_s * (2 ** min(self._idle_streak, 5)), cap_s)
        await asyncio.sleep(random.uniform(0, ceiling))

    async def _error_backoff(self) -> None:
        """Full-jitter exponential backoff after a `fetch_and_lock` TRANSPORT error (design §6
        point 5) — base 1s, cap 30s. Distinct from `_idle_backoff` (empty-but-successful fetch)."""
        ceiling = min(1.0 * (2 ** min(self._consecutive_fetch_errors, 5)), 30.0)
        await asyncio.sleep(random.uniform(0, ceiling))

    def _spawn(self, task: ExternalTask) -> None:
        fut = asyncio.create_task(self._run_handle(task), name=f"worker-task-{task.task_id}")
        self._inflight[task.task_id] = (fut, task)

        def _cleanup(_f: asyncio.Task[None], task_id: str = task.task_id) -> None:
            self._inflight.pop(task_id, None)

        fut.add_done_callback(_cleanup)

    async def _run_handle(self, task: ExternalTask) -> None:
        """Wrapper around `_handle` run as a tracked background task.

        `_handle` already catches every business/transport exception it can meaningfully
        classify (module docstring); this is a last-resort guard so a truly unexpected bug never
        leaves an "exception was never retrieved" warning from a fire-and-forget task.
        """
        try:
            await self._handle(task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — last-resort: dispatch must never crash the loop.
            logger.error("worker_handle_task_crashed", task_id=task.task_id, topic=task.topic, exc_info=True)

    # -- dispatch -------------------------------------------------------------------------------

    def _compute_engine_retries(self, task: ExternalTask) -> int:
        """`task.retries - 1`, seeding from `max_retry_attempts` on first delivery (retries=None).

        The engine does NOT auto-decrement retries (design §2) — the client (this harness) must.
        """
        current = task.retries if task.retries is not None else self._max_retries
        return max(current - 1, 0)

    def _jittered_retry_timeout_ms(self, task: ExternalTask) -> int:
        """Full-jitter exponential backoff for the engine `retryTimeout`, based on how many
        attempts this task has already had (`max_retry_attempts - task.retries`, bounded)."""
        current = task.retries if task.retries is not None else self._max_retries
        attempt = max(self._max_retries - current, 0)
        cap_ms = 60_000
        base_ms = 2_000
        ceiling = min(cap_ms, base_ms * (2 ** min(attempt, 5)))
        return int(random.uniform(base_ms, max(base_ms, ceiling)))

    async def _report_failure(
        self,
        task: ExternalTask,
        error_message: str,
        *,
        retries_override: int | None,
    ) -> str:
        """Report `failure` to the engine with the given (or computed) retry count.

        Returns the emitted outcome label: "incident" when the reported retries is 0 (the engine
        will open an incident), else "failed" (the engine will re-deliver after the timeout).
        """
        retries = retries_override if retries_override is not None else self._compute_engine_retries(task)
        retries = max(retries, 0)
        retry_timeout_ms = 0 if retries == 0 else self._jittered_retry_timeout_ms(task)
        await self._transport.handle_failure(
            task.task_id,
            self._worker_id,
            error_message=error_message,
            retries=retries,
            retry_timeout_ms=retry_timeout_ms,
        )
        return "incident" if retries == 0 else "failed"

    async def _handle(self, task: ExternalTask) -> None:
        handler = self._handlers.get(task.topic)
        started_at = time.perf_counter()
        outcome: str | None = None
        try:
            if handler is None:
                # Should only happen if fetchAndLock somehow returns a task for a topic we no
                # longer serve (race with a concurrent re-registration) — the readiness check
                # (worker_runtime/service.py) prevents this at boot by requiring full topic
                # coverage before /readyz goes green. Fail-closed: report an incident, never
                # silently drop the task (design §7 — "a task with no handler must not vanish").
                logger.error("worker_unregistered", topic=task.topic, task_id=task.task_id)
                outcome = await self._report_failure(
                    task,
                    f"no handler registered for topic {task.topic!r}",
                    retries_override=0,
                )
                return

            try:
                out_vars = await handler(task)
                await self._transport.complete(
                    task.task_id,
                    self._worker_id,
                    dict(out_vars) if out_vars else {},
                )
                outcome = "completed"
                logger.info(
                    "worker_task_completed",
                    task_id=task.task_id,
                    topic=task.topic,
                    business_key=task.business_key,
                )
            except WorkerBpmnError as exc:
                if exc.error_code in self._bpmn_error_allowlist:
                    outcome = "bpmn_error"
                    await self._transport.handle_bpmn_error(
                        task.task_id,
                        self._worker_id,
                        error_code=exc.error_code,
                        error_message=str(exc),
                    )
                else:
                    # Live-verified hazard (design §9): an unmodeled bpmnError silently ends the
                    # process with NO incident on CIB Seven 2.1.0. Demote to a fail-closed
                    # incident instead of risking a silent drop, and log loudly so this shows up
                    # in on-call triage even without the CI-side boundary-proof gate.
                    _stdlib_logger.error(
                        "bpmn_error_code_not_gate_proven_demoted_to_failure "
                        "task_id=%s topic=%s error_code=%s",
                        task.task_id,
                        task.topic,
                        exc.error_code,
                    )
                    logger.error(
                        "bpmn_error_code_not_gate_proven_demoted_to_failure",
                        task_id=task.task_id,
                        topic=task.topic,
                        error_code=exc.error_code,
                    )
                    outcome = await self._report_failure(task, str(exc), retries_override=0)
            except WorkerFailureError as exc:
                outcome = await self._report_failure(
                    task, str(exc), retries_override=max(exc.retries_left, 0)
                )
            except PermissionError as exc:
                # ERR_*_NOT_HUMAN guard family — NEVER retried (ADR-0008): an incident is the
                # engine-guaranteed, always-human-visible outcome.
                outcome = await self._report_failure(task, str(exc), retries_override=0)
            except ValueError as exc:
                # Bad/immutable input — won't fix itself on retry; route straight to incident.
                outcome = await self._report_failure(task, str(exc), retries_override=0)
            except Exception as exc:  # noqa: BLE001 — classified below; never escapes dispatch.
                _transient_types = (RuntimeError, OSError, TimeoutError, ConnectionError, httpx.HTTPError)
                transient = isinstance(exc, _transient_types)
                if not transient:
                    logger.error(
                        "worker_unclassified_error",
                        task_id=task.task_id,
                        topic=task.topic,
                        error_type=type(exc).__name__,
                    )
                outcome = await self._report_failure(task, str(exc), retries_override=None)
        finally:
            if outcome is not None:
                _emit_worker_task_outcome(
                    tenant=self._tenant,
                    topic=task.topic,
                    outcome=outcome,
                    duration_seconds=time.perf_counter() - started_at,
                )

    # Public alias (design §16.1: "keep `_handle` callable, public `handle_task` alias") — v1
    # fixtures that drive dispatch directly call `harness._handle(task)`; new code should prefer
    # the public name.
    handle_task = _handle
