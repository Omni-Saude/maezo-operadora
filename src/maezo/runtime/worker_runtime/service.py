"""Worker-runtime SERVICE — the daemon supervising `WorkerHarness.run()` (T1.1 design §3/§10).

This is the residual leg of ADR-0001: the engine (CIB Seven) calls external tasks (engine ->
worker); this daemon fetches them, runs the registered handler, and completes/fails the task on
the engine.

Bring-up order (design §3/§10/§12), A -> E:
  STEP A  Bind the health app IMMEDIATELY in an asyncio task. `/healthz` answers 200 right away
          (liveness) BEFORE any dependency comes up — the pod never CrashLoops because the
          engine is slow/unavailable. Signal ownership (SIGTERM/SIGINT) is claimed here.
  STEP B  Bring up dependencies BOUNDED and NON-FATAL: build the CIB Seven transport (pure
          construction — no network until the first fetch) and register every worker this build
          serves — as of T1.2/ADR-0026 this is the FULL 16-module composition
          (`bootstrap.register_all_workers`; T1.1 shipped only the 3 `WorkerBase` modules —
          auth/escalation/lgpd — the interim-scope note this closes). Failure logs + leaves the
          corresponding readiness check unhealthy, but NEVER brings the process down (liveness
          stays up).
  STEP C  Readiness checks read `WorkerState`: `engine_reachable` / `workers_registered` /
          `harness_running` (+ `kafka_ready`, currently a no-op pass — see `kafka_ready`'s
          docstring in `build_readiness_checks` below). `/readyz` only turns 200 once every
          check is healthy (fail-closed, design §12).
  STEP D  SIGTERM/SIGINT -> drain: `live=False` (`/healthz` -> 503), stop the health server,
          cancel the harness-supervising task (stops NEW fetches), `harness.drain(deadline)`
          (wait for in-flight handlers, explicitly `unlock` stragglers — design §8), close the
          transport.
  STEP E  Supervise `harness.run()` until shutdown. If the harness task ends on its own (it
          shouldn't, except on cancellation), the service drains and exits — the Deployment
          restarts the pod.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.observability import get_metrics_collector
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    TopicSubscription,
    WorkerHarness,
)

from .settings import WorkerRuntimeSettings

logger = structlog.get_logger(__name__)

# T1.2/ADR-0026: the daemon now registers the FULL 16-module composition
# (`bootstrap.register_all_workers` — the donor's `_register_all_workers` shape, T1.1 design
# §16), closing the T1.1 verifier's interim-scope note (T1.1 shipped only the 3 `WorkerBase`
# modules — auth/escalation/lgpd). `register_default_workers` stays the daemon's STEP-B
# bootstrap name/call site; it now delegates to the full composition rather than a
# hand-maintained class tuple, so adding/removing a module (any of the 16, or a future 17th)
# never requires a second, parallel edit here.


def register_default_workers(harness: WorkerHarness) -> None:
    """Register every worker this build serves. The daemon's ONE bootstrap call (STEP B).

    Idempotent (`WorkerHarness.register_worker` replaces on re-registration, same topic).
    """
    register_all_workers(harness)


def _expected_worker_topics() -> frozenset[str]:
    """The canonical topic set this daemon MUST serve — the readiness check compares the live
    harness against this. Derived from `register_default_workers` itself (never a hand-maintained
    constant that would silently drift when a worker is added/removed) by registering into a
    disposable, transport-less harness — `register_worker` is pure (only populates a dict; the
    transport is untouched until `run()`), so this is a zero-network probe.
    """
    probe = WorkerHarness(_NullTransport(), worker_id="topic-probe")
    register_default_workers(probe)
    return frozenset(probe.registered_topics)


class _NullTransport:
    """`WorkerTransport`-shaped placeholder used only to derive `_expected_worker_topics()` —
    never connected, never used to fetch/complete/fail a task. Typed to match the Protocol
    exactly (rather than `*args`/`**kwargs`) so mypy verifies `WorkerHarness` still accepts it."""

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        raise NotImplementedError("probe transport is never run")

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        raise NotImplementedError("probe transport is never run")

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
        raise NotImplementedError("probe transport is never run")

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError("probe transport is never run")

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        raise NotImplementedError("probe transport is never run")

    async def unlock(self, task_id: str) -> None:
        raise NotImplementedError("probe transport is never run")

    async def close(self) -> None:
        return None


# --- Mutable shared state (read by the readiness checks) -------------------------------------


@dataclass
class WorkerState:
    """Daemon state, mutated as dependencies come up (STEP B). Readiness checks (STEP C) read
    THIS object — keeps the health app (generic) decoupled from the runtime. `live` drives
    `/healthz` (drain: SIGTERM -> live=False)."""

    settings: WorkerRuntimeSettings
    live: bool = True
    transport: CibSevenWorkerTransport | None = None
    harness: WorkerHarness | None = None
    expected_topics: frozenset[str] = field(default_factory=frozenset)
    harness_task: asyncio.Task[None] | None = None

    def is_live(self) -> bool:
        return self.live

    def harness_running(self) -> bool:
        return self.harness_task is not None and not self.harness_task.done()


# --- STEP C: readiness checks (read WorkerState) ----------------------------------------------


def build_readiness_checks(state: WorkerState) -> list[Callable[[], Awaitable[CheckResult]]]:
    """Assemble the NAMED readiness checks the health app (platform/health.py) runs concurrently
    on every `/readyz`. Every check is defensive — never raises, always returns a CheckResult."""

    async def engine_reachable(_state: WorkerState = state) -> CheckResult:
        # A CHEAP, non-blocking presence check by design (design §12): a synchronous round-trip
        # to the engine here would let a slow/degraded engine hang `/readyz`. The harness's own
        # `engine_reachable` property (flips False after N consecutive fetch failures, T1.1 §6)
        # reflects the REAL, ongoing health of the fetch loop once it's running; before the
        # harness exists, presence of the transport is the best available signal.
        harness = _state.harness
        if harness is not None:
            healthy = harness.engine_reachable
            return CheckResult(
                name="engine_reachable",
                healthy=healthy,
                detail=None if healthy else "consecutive fetch-and-lock failures exceeded threshold",
            )
        present = _state.transport is not None
        return CheckResult(
            name="engine_reachable",
            healthy=present,
            detail=None if present else "CIB Seven transport not constructed",
        )

    async def workers_registered(_state: WorkerState = state) -> CheckResult:
        # Fail-closed (design §12): /readyz only turns ready once EVERY expected topic (the
        # canonical set derived from `register_default_workers` itself, see
        # `_expected_worker_topics`) is registered. A loose `> 0` would mask a partially
        # registered harness — processes would deadlock on their first service task while the pod
        # announced itself ready.
        harness = _state.harness
        if harness is None:
            return CheckResult(name="workers_registered", healthy=False, detail="harness not constructed")
        expected = _state.expected_topics
        registered = set(harness.registered_topics)
        missing = expected - registered
        if not expected:
            return CheckResult(
                name="workers_registered", healthy=False, detail="expected topic set not computed"
            )
        healthy = not missing
        if healthy:
            # T1.2/ADR-0026: the scope detail the T1.1 verifier's interim-scope note asked for —
            # readiness now reflects the FULL 16-module composition, not just the 3 WorkerBase
            # modules T1.1 shipped. Included on the HEALTHY path too (not just failures) so
            # `/readyz` is self-describing about what "ready" means.
            detail = (
                f"{len(ALL_WORKER_BOOTSTRAPS)}/16 worker modules registered "
                f"({len(registered)} topics; scope: T1.2/ADR-0026 full composition)"
            )
        else:
            detail = f"missing topics ({len(missing)}/{len(expected)}): {sorted(missing)}"
        return CheckResult(name="workers_registered", healthy=healthy, detail=detail)

    async def harness_running(_state: WorkerState = state) -> CheckResult:
        running = _state.harness_running()
        return CheckResult(
            name="harness_running",
            healthy=running,
            detail=None if running else "fetch-and-lock loop is not supervised",
        )

    async def kafka_ready(_state: WorkerState = state) -> CheckResult:
        # T1.2/ADR-0026: `kafka` (a `KafkaPublisher | None`) is now threaded via
        # `functools.partial` into every one of the 13 function-based modules' entry functions
        # (the donor `register_<domain>_workers(harness, kafka=None, **seams)` contract) — but no
        # entry function actually CALLS `kafka.publish` yet (documented per-module: the sync
        # entry-function boundary vs the async `KafkaPublisher.publish` seam is a real gap, not
        # fabricated here). This daemon does not construct a real producer either. Gating
        # readiness on a producer no registered worker actually drives would be a readiness check
        # on an unused dependency. Always healthy, with an explicit detail so this is never
        # mistaken for "Kafka verified reachable".
        return CheckResult(
            name="kafka_ready",
            healthy=True,
            detail="not required by any worker registered in this build (kafka=None; unused today)",
        )

    return [engine_reachable, workers_registered, harness_running, kafka_ready]


# --- STEP B: dependency bring-up (bounded, non-fatal) -------------------------------------------


async def _bring_up_dependencies(state: WorkerState) -> None:
    """Bring up the daemon's dependencies. Each block is isolated: failure logs + leaves the
    corresponding check unhealthy, but NEVER propagates (liveness must stay up)."""
    settings = state.settings

    try:
        state.transport = CibSevenWorkerTransport(
            settings.cibseven_base_url,
            auth_token=settings.cibseven_auth_token_value(),
            timeout=settings.client_timeout_s,
        )
    except Exception:  # noqa: BLE001 — construction failure leaves engine_reachable unhealthy.
        logger.error("worker_transport_build_failed", exc_info=True)

    try:
        if state.transport is not None:
            harness = WorkerHarness(
                state.transport,
                worker_id=settings.worker_id,
                tenant=settings.tenant_id,
                lock_duration_ms=settings.lock_duration_ms,
                poll_interval_ms=settings.poll_interval_ms,
                max_tasks_per_poll=settings.max_tasks_per_poll,
                max_retry_attempts=settings.max_retry_attempts,
                async_response_timeout_ms=settings.async_response_timeout_ms,
                # No bpmnError code is gate-proven yet (T1.1 §9 open Q-3 — the CI-side boundary-
                # proof gate is a T1.3/T1.4 follow-up); every WorkerBpmnError demotes to a
                # fail-closed incident until a code is added here with proof.
                bpmn_error_allowlist=frozenset(),
            )
            register_default_workers(harness)
            state.harness = harness
            state.expected_topics = _expected_worker_topics()
    except Exception:  # noqa: BLE001 — registration failure leaves workers_registered unhealthy.
        logger.error("worker_harness_build_failed", exc_info=True)

    try:
        if state.harness is not None:
            state.harness_task = asyncio.create_task(state.harness.run(), name="worker-harness")
    except Exception:  # noqa: BLE001 — spawn failure leaves harness_running unhealthy.
        logger.error("worker_harness_spawn_failed", exc_info=True)

    logger.info(
        "worker_dependencies_brought_up",
        transport=state.transport is not None,
        workers_registered=(len(state.harness.registered_topics) if state.harness is not None else 0),
        harness_running=state.harness_running(),
    )


# --- run() — orchestrates STEP A..E -------------------------------------------------------------


async def run(settings: WorkerRuntimeSettings) -> None:
    """Run the worker-runtime until SIGTERM/SIGINT. See the module docstring for STEP A..E."""
    logger.info(
        "worker_runtime_starting",
        tenant=settings.tenant_id,
        worker_id=settings.worker_id,
        health_port=settings.health_port,
    )
    state = WorkerState(settings=settings)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    # STEP A: health app + uvicorn server NOW — /healthz 200 immediately (liveness), BEFORE any
    # dependency comes up. We claim signal ownership (uvicorn's own capture disabled) so the
    # daemon controls the drain sequence exactly (design §8).
    app = create_health_app(
        readiness_checks=build_readiness_checks(state),
        is_live=state.is_live,
        registry=get_metrics_collector().registry,
    )
    server = build_health_server(app, port=settings.health_port)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # we own the signals
    serve_task = asyncio.create_task(server.serve(), name="health-server")

    def _request_shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
        state.live = False  # /healthz -> 503: leave the load-balancing rotation (drain).
        server.should_exit = True
        shutdown.set()

    def _on_serve_done(task: asyncio.Task[None]) -> None:
        # Fail-fast: if the health server dies on its own (e.g. bind failure), don't hang on
        # shutdown.wait() with no health server up — force live=False + shutdown so run() drains
        # and exits.
        state.live = False
        if not shutdown.is_set():
            exc = None if task.cancelled() else task.exception()
            logger.error("health_server_stopped_early", error=repr(exc) if exc else "no_exception")
            shutdown.set()

    serve_task.add_done_callback(_on_serve_done)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            # add_signal_handler is unavailable on some loops (e.g. Windows ProactorEventLoop):
            # suppress and move on — not a supported deployment target here.
            loop.add_signal_handler(_sig, _request_shutdown, _sig)

    # STEP B: dependencies (bounded, non-fatal) + spawn the harness-supervising task. Skip if a
    # signal already arrived (or the health server already died) — go straight to drain.
    if not shutdown.is_set():
        await _bring_up_dependencies(state)

    # STEP E: supervise the fetch-and-lock loop until shutdown. If the harness task ends on its
    # own (should never happen except cancellation), drain and exit — the Deployment restarts.
    if state.harness_task is not None and not shutdown.is_set():
        state.harness_task.add_done_callback(lambda _t: shutdown.set())
    logger.info("shutdown_before_run" if shutdown.is_set() else "worker_runtime_running")
    await shutdown.wait()

    # STEP D drain: stop the loop, cancel the supervising task (stops NEW fetches), drain
    # in-flight work with an explicit unlock of stragglers (design §8), close the transport.
    if state.harness is not None:
        with contextlib.suppress(Exception):
            await state.harness.stop()
    if state.harness_task is not None and not state.harness_task.done():
        state.harness_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await state.harness_task
    if state.harness is not None:
        with contextlib.suppress(Exception):
            await state.harness.drain(settings.drain_deadline_s)
    if state.transport is not None:
        with contextlib.suppress(Exception):
            await state.transport.close()

    # The health server's should_exit was already set in the drain trigger; make sure it's set
    # regardless of which path got us here, then wait for it to actually stop.
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    logger.info("worker_runtime_stopped", tenant=settings.tenant_id, worker_id=settings.worker_id)
