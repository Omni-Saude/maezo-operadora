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
          serves — as of T1.2/ADR-0026 (+ T3.1 R2's `events` module) this is the FULL 17-module
          composition (`bootstrap.register_all_workers`; T1.1 shipped only the 3 `WorkerBase`
          modules — auth/escalation/lgpd — the interim-scope note this closes). Failure logs +
          leaves the corresponding readiness check unhealthy, but NEVER brings the process down
          (liveness stays up).
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

from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink
from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.observability import get_metrics_collector
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.auth import AUTH_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import EVENTS_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    TopicSubscription,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import LGPD_BPMN_ERROR_ALLOWLIST

from .settings import WorkerRuntimeSettings

logger = structlog.get_logger(__name__)

# T1.2/ADR-0026: the daemon now registers the FULL 17-module composition (16 + T3.1 R2's
# `events` module, closing the events.publish gap)
# (`bootstrap.register_all_workers` — the donor's `_register_all_workers` shape, T1.1 design
# §16), closing the T1.1 verifier's interim-scope note (T1.1 shipped only the 3 `WorkerBase`
# modules — auth/escalation/lgpd). `register_default_workers` stays the daemon's STEP-B
# bootstrap name/call site; it now delegates to the full composition rather than a
# hand-maintained class tuple, so adding/removing a module never requires a second, parallel
# edit here.


# ---------------------------------------------------------------------------
# ADR-0030 Tier-0 production BPMN-error allowlist
# ---------------------------------------------------------------------------
#
# The harness reports a worker's `WorkerBpmnError(code)` to the engine as a REAL `bpmnError`
# (firing the modeled boundary catch) ONLY when `code` is in this allowlist; every other code
# demotes to a fail-closed incident (harness.py §9). Before ADR-0030 this was `frozenset()` — so
# every one of the 24 deliberately-modeled boundary catches was dead code in production (the
# systemic blocker ADR-0030 Tier-0 closes).
#
# This set is ASSEMBLED from the per-worker allowlist constants that the boundary-proof gate
# (`scripts/ci/check_bpmn_error_allowlist.py`) mechanically proves consumption-covered against
# `spec/**` — never a hand-maintained code list. The T-E hard-gate (ADR-0030 §4) is then applied:
# business-outcome codes (every `*_NOT_HUMAN` guard + the denial-block `ERR_AUTH_DENIAL_INCOMPLETE`)
# are consumption-covered too, but activating one pre-T-E would trade a guaranteed-human-visible
# incident for a silent clean end at a neutral terminal, so they stay incident-fail-closed until
# T-E (audited-refusal) lands. At Tier-0 this resolves to `{ERR_EVENT_PUBLISH_FAILED,
# ERR_DSR_IDENTITY_UNVERIFIED}` — the non-adverse technical fail-safes (a publish failure routed to
# retry/fallback; a mechanically-unverifiable LGPD titular routed to a neutral terminal).


def _is_te_gated(code: str) -> bool:
    """True iff `code` is a business-outcome code hard-gated on T-E (ADR-0030 §4).

    The `*_NOT_HUMAN` guard family (matched by suffix) plus the denial-block
    `ERR_AUTH_DENIAL_INCOMPLETE`. Mirrors the boundary-proof gate's `is_te_gated`, kept a tiny
    local predicate so the runtime carries no import dependency on the CI script.
    """
    return code.endswith("_NOT_HUMAN") or code == "ERR_AUTH_DENIAL_INCOMPLETE"


#: Every gate-proven (consumption-covered) code raised by a worker that exposes an allowlist
#: constant. `AUTH_BPMN_ERROR_ALLOWLIST` is unioned in DELIBERATELY so the T-E filter below has
#: something to act on: `ERR_AUTH_DENIAL_INCOMPLETE` is proven yet filtered OUT — if a future edit
#: dropped the T-E gate, the denial-block code would leak into production and the unit test
#: (`tests/unit/runtime/test_worker_runtime_bpmn_error_allowlist.py`) fails. `LGPD_BPMN_ERROR_
#: ALLOWLIST` contributes `ERR_DSR_IDENTITY_UNVERIFIED` (T2.8) — a NON-adverse technical fail-safe
#: (mechanically-unverifiable titular -> End_IdentidadeInverificavel, a neutral terminal), so it is
#: NOT T-E-gated and DOES land in Tier-0. `ERR_CANCEL_MANTER_NOT_HUMAN` is gate-proven too but its
#: worker exposes no constant (nothing is enabled for it at any tier until T-E), so there is
#: nothing to import.
_GATE_PROVEN_BPMN_ERROR_CODES: frozenset[str] = (
    AUTH_BPMN_ERROR_ALLOWLIST | EVENTS_BPMN_ERROR_ALLOWLIST | LGPD_BPMN_ERROR_ALLOWLIST
)

#: The Tier-0 production allowlist wired into the harness: gate-proven codes MINUS the T-E-gated
#: business-outcome codes. Resolves to `{ERR_EVENT_PUBLISH_FAILED, ERR_DSR_IDENTITY_UNVERIFIED}` today.
PRODUCTION_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset(
    code for code in _GATE_PROVEN_BPMN_ERROR_CODES if not _is_te_gated(code)
)


def register_default_workers(
    harness: WorkerHarness,
    *,
    dmn: CibSevenDmnTransport | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> None:
    """Register every worker this build serves. The daemon's ONE bootstrap call (STEP B).

    `dmn` (ADR-0028 §1, the `dmn=` seam ADR-0026 §2 reserves) is threaded through to every
    `register_<domain>_workers(harness, kafka, **seams)` bootstrap via `functools.partial` at
    wrap time — modules with no DMN dependency ignore it. `None` (the default, and what the
    topic-probe below passes) lets modules register their topics with no live engine present;
    only an actual `dmn.evaluate(...)` call at task-execution time needs a real transport.

    `engine` (a `CibSevenTransport`, T1.10 T-D / GAP-INAD-1) is the agent->engine seam threaded
    the same way into the two `inadimplencia` workers that touch the engine directly:
    `resolve_facts`'s anti-dupla-terminacao `find_active_instance` query and `handoff_rescisao`'s
    CANCEL-001 `start_process`. Absent (`None`, the topic-probe default) leaves both FAILING CLOSED
    (resolve_facts -> `ja_em_rescisao_cancel := True`; handoff -> raises), exactly as before this
    seam was wired. In the live daemon it is a `FreshClientCibSevenTransport` (fresh-client-per-call
    — see that class's docstring for the "Event loop is closed" rationale).

    `audit_sink` (an `AuditStartSink`, T1.10 T-C2 fence / wave integration) is the durable
    ADR-0007 sink for `handoff_rescisao`'s fenced CANCEL-001 start — the 10th
    `start_process_idempotent` site. Absent (`None`, the topic-probe default) leaves the handoff
    FAILING CLOSED (raises before any engine effect — never an un-audited start). In the live
    daemon it is a `FreshSinkAuditEmitter` (gateway/audit_postgres.py), NOT the pooled
    `state.audit_sink`: sync worker dispatches emit on fresh per-call `asyncio.run` loops, and an
    asyncpg pool binds to the loop that created it (same rationale as
    `FreshClientCibSevenTransport`, sink-side).

    Idempotent (`WorkerHarness.register_worker` replaces on re-registration, same topic).
    """
    register_all_workers(harness, dmn=dmn, engine=engine, audit_sink=audit_sink)


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
    dmn_transport: CibSevenDmnTransport | None = None
    # Agent->engine seam (T1.10 T-D / GAP-INAD-1): fresh-client-per-call, threaded into the
    # inadimplencia workers that start/correlate CANCEL-001 and query cross-process rescisao.
    engine_transport: CibSevenTransport | None = None
    # Durable, fail-closed audit sink (ADR-0007 L0, T1.10 T-D). `None` when DATABASE_URL is unset
    # or construction failed -> THIS composition root refuses to build the harness (see
    # `_bring_up_dependencies`: the `WorkerHarness` ctor itself accepts `audit_sink=None`
    # Optional-fail-closed, T-C — a sink-less harness raises `AuditEmitError` before any
    # completion; the root simply never constructs that degraded harness) and `audit_sink_ready`
    # stays red. `audit_sink_ready` is the boot-time connectivity-probe result that gates whether
    # the daemon enters the fetch-and-lock rotation at all.
    audit_sink: PostgresAuditSink | None = None
    audit_sink_ready: bool = False
    harness: WorkerHarness | None = None
    expected_topics: frozenset[str] = field(default_factory=frozenset)
    harness_task: asyncio.Task[None] | None = None

    def is_live(self) -> bool:
        return self.live

    def harness_running(self) -> bool:
        return self.harness_task is not None and not self.harness_task.done()


# --- STEP C: readiness checks (read WorkerState) ----------------------------------------------


async def _probe_audit_sink(sink: PostgresAuditSink, timeout_s: float) -> bool:
    """Bounded, non-raising connectivity probe for the durable audit sink (T-D).

    Wraps `PostgresAuditSink.check_ready()` (which raises on any unreachable/missing-table failure)
    in a hard timeout so neither `/readyz` nor bring-up can hang on a slow/hung Postgres. Returns
    `True` only when the sink positively proves it can reach the tenant schema's `audit_chain`;
    every failure (timeout, connection refused, missing table) is `False` (fail-closed). Used both
    by the `audit_sink_ready` readiness gate and to gate the fetch-and-lock rotation at bring-up.
    """
    try:
        await asyncio.wait_for(sink.check_ready(), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — any failure means "not ready", never propagates.
        logger.warning("audit_sink_probe_failed", error=str(exc))
        return False
    return True


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
            # T1.2/ADR-0026 (+ T3.1 R2): the scope detail the T1.1 verifier's interim-scope note
            # asked for — readiness now reflects the FULL 17-module composition, not just the 3
            # WorkerBase modules T1.1 shipped. Included on the HEALTHY path too (not just
            # failures) so `/readyz` is self-describing about what "ready" means.
            total_modules = len(ALL_WORKER_BOOTSTRAPS)
            detail = (
                f"{total_modules}/{total_modules} worker modules registered "
                f"({len(registered)} topics; scope: T1.2/ADR-0026 full composition, "
                "T3.1 events.publish worker included)"
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

    async def audit_sink_ready(_state: WorkerState = state) -> CheckResult:
        # FAIL-CLOSED L0 gate (ADR-0007, design §7 T-D / Revision MUST-FIX 2): `/readyz` stays RED
        # until a bounded connectivity probe proves the durable audit sink can reach the tenant
        # schema's `audit_chain`. A daemon that cannot durably audit must NOT be routed work — and,
        # in bring-up, is not even started into the fetch-and-lock rotation (see
        # `_bring_up_dependencies`). A missing sink (DATABASE_URL unset / construction failed) is
        # red WITHOUT probing; a present sink is probed LIVE each `/readyz` (bounded, never hangs)
        # so the signal reflects ONGOING sink health, not just the boot-time snapshot.
        sink = _state.audit_sink
        if sink is None:
            return CheckResult(
                name="audit_sink_ready",
                healthy=False,
                detail=(
                    "audit sink not constructed (DATABASE_URL unset or construction failed) — "
                    "daemon refuses to serve effect-producing traffic it cannot durably audit "
                    "(ADR-0007 L0)"
                ),
            )
        healthy = await _probe_audit_sink(sink, _state.settings.dep_connect_timeout_s)
        return CheckResult(
            name="audit_sink_ready",
            healthy=healthy,
            detail=None
            if healthy
            else "durable audit sink unreachable / audit_chain missing — cannot durably audit "
            "(ADR-0007 fail-closed; /readyz stays red)",
        )

    return [engine_reachable, workers_registered, harness_running, kafka_ready, audit_sink_ready]


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
        # ADR-0028 §1: the DMN seam is built once at daemon boot, against the SAME CIB Seven
        # engine the external-task transport targets (`CIBSEVEN_BASE_URL`) — DMN evaluation and
        # external-task dispatch are on the same failure/retry/incident plane (ADR-0028
        # Consequencias). Construction is pure (no network) — a failure here is unexpected but
        # still non-fatal, mirroring the worker transport above.
        state.dmn_transport = CibSevenDmnTransport(
            settings.cibseven_base_url,
            auth_token=settings.cibseven_auth_token_value(),
            timeout=settings.client_timeout_s,
        )
    except Exception:  # noqa: BLE001 — construction failure: DMN-calling workers fail closed later.
        logger.error("dmn_transport_build_failed", exc_info=True)

    try:
        # Agent->engine seam (GAP-INAD-1): FRESH-CLIENT-PER-CALL (see FreshClientCibSevenTransport)
        # so consecutive engine-touching worker dispatches, each on its own fresh `asyncio.run`
        # loop, never share/outlive a client. Construction is pure (no network, no client held) —
        # a failure here is unexpected but non-fatal; the inadimplencia workers then fail closed on
        # a `None` engine seam (resolve_facts -> block; handoff_rescisao -> raise).
        state.engine_transport = FreshClientCibSevenTransport(
            settings.cibseven_base_url,
            auth_token=settings.cibseven_auth_token_value(),
            timeout=settings.client_timeout_s,
        )
    except Exception:  # noqa: BLE001 — construction failure: engine-seam workers fail closed later.
        logger.error("engine_transport_build_failed", exc_info=True)

    if settings.database_url:
        try:
            # Durable audit sink (ADR-0007 L0, ADR-0027). Construction is PURE (the asyncpg pool is
            # created lazily on first use / probe, like the DMN transport) — a bad DSN or an
            # unreachable Postgres does NOT fail here; it surfaces as a red `audit_sink_ready` probe
            # below, which is what keeps the daemon out of the fetch rotation (fail-closed).
            state.audit_sink = PostgresAuditSink(settings.database_url, settings.tenant_id)
        except Exception:  # noqa: BLE001 — a bad tenant id / DSN leaves audit_sink None -> red.
            logger.error("audit_sink_build_failed", exc_info=True)
    else:
        # FAIL-CLOSED (design §7 T-D / Revision MUST-FIX 2): no DATABASE_URL -> no sink -> the
        # harness is not built (audit_sink is a required seam) and the daemon never serves.
        logger.error("audit_sink_unconfigured_database_url_unset")

    # Boot-time connectivity probe: proves the sink can reach the tenant schema's `audit_chain`
    # BEFORE the daemon enters the fetch-and-lock rotation. Bounded (never hangs bring-up).
    if state.audit_sink is not None:
        state.audit_sink_ready = await _probe_audit_sink(state.audit_sink, settings.dep_connect_timeout_s)
        if not state.audit_sink_ready:
            logger.error(
                "audit_sink_not_ready_at_boot",
                tenant=settings.tenant_id,
                detail="durable audit sink unreachable / audit_chain missing — daemon will NOT "
                "enter the fetch-and-lock rotation (ADR-0007 fail-closed)",
            )

    try:
        # FAIL-CLOSED SEAM (design §7 T-C/T-D, reconciled at wave integration): THIS composition
        # root builds the harness ONLY with a durable audit sink. T-C's landed `WorkerHarness`
        # constructor takes `audit_sink: AuditEmitter | None = None` — Optional at CONSTRUCTION
        # (so topic probes / unit fixtures that never complete a task keep working), fail-closed
        # at COMPLETION (`_handle` raises `AuditEmitError` before `complete` when the sink is
        # missing). The `state.audit_sink is not None` guard here is the daemon's own stricter
        # posture on top of that belt-and-suspenders: no sink (missing DATABASE_URL / bad DSN)
        # => no harness => no fetching — the degraded raise-on-complete harness is never even
        # constructed in the live daemon.
        if state.transport is not None and state.audit_sink is not None:
            harness = WorkerHarness(
                state.transport,
                worker_id=settings.worker_id,
                tenant=settings.tenant_id,
                lock_duration_ms=settings.lock_duration_ms,
                poll_interval_ms=settings.poll_interval_ms,
                max_tasks_per_poll=settings.max_tasks_per_poll,
                max_retry_attempts=settings.max_retry_attempts,
                async_response_timeout_ms=settings.async_response_timeout_ms,
                # ADR-0030 Tier-0: the CI-side boundary-proof gate is now built
                # (scripts/ci/check_bpmn_error_allowlist.py) and proves the consumption-covered
                # codes; the production allowlist is populated from the per-worker constants it
                # verifies, with T-E-gated business-outcome codes excluded (§4). Every
                # non-allowlisted WorkerBpmnError still demotes to a fail-closed incident
                # (harness.py §9), so the fail-closed default is preserved, not weakened.
                bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
                # T-C seam, direct since the wave merge (the pre-merge `**` indirection is gone).
                audit_sink=state.audit_sink,
            )
            # Worker seams: the pooled `state.audit_sink` serves the harness on the daemon's main
            # loop; the `audit_sink` SEAM for sync per-call workers (handoff_rescisao's fenced
            # CANCEL-001 start) is a FreshSinkAuditEmitter — per-call loop-safe, same DSN/tenant.
            register_default_workers(
                harness,
                dmn=state.dmn_transport,
                engine=state.engine_transport,
                audit_sink=FreshSinkAuditEmitter(settings.database_url, settings.tenant_id)
                if settings.database_url
                else None,
            )
            state.harness = harness
            state.expected_topics = _expected_worker_topics()
    except Exception:  # noqa: BLE001 — registration failure leaves workers_registered unhealthy.
        logger.error("worker_harness_build_failed", exc_info=True)

    try:
        # FAIL-CLOSED: enter the fetch-and-lock rotation ONLY when the audit sink was verified
        # reachable at boot. An un-auditable daemon that fetched+locked tasks it could not durably
        # audit would stall them (design §7 T-D / Revision MUST-FIX 2); it must not start the loop.
        if state.harness is not None and state.audit_sink_ready:
            state.harness_task = asyncio.create_task(state.harness.run(), name="worker-harness")
        elif state.harness is not None and not state.audit_sink_ready:
            logger.error(
                "worker_harness_not_started_audit_sink_unverified",
                detail="harness built but NOT started — audit sink is not verified reachable "
                "(fail-closed, ADR-0007)",
            )
    except Exception:  # noqa: BLE001 — spawn failure leaves harness_running unhealthy.
        logger.error("worker_harness_spawn_failed", exc_info=True)

    logger.info(
        "worker_dependencies_brought_up",
        transport=state.transport is not None,
        audit_sink=state.audit_sink is not None,
        audit_sink_ready=state.audit_sink_ready,
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
    if state.dmn_transport is not None:
        with contextlib.suppress(Exception):
            await state.dmn_transport.close()
    if state.engine_transport is not None:
        with contextlib.suppress(Exception):
            await state.engine_transport.close()
    if state.audit_sink is not None:
        with contextlib.suppress(Exception):
            await state.audit_sink.aclose()

    # The health server's should_exit was already set in the drain trigger; make sure it's set
    # regardless of which path got us here, then wait for it to actually stop.
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    logger.info("worker_runtime_stopped", tenant=settings.tenant_id, worker_id=settings.worker_id)
