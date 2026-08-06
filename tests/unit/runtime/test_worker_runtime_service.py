"""Unit tests for `maezo.runtime.worker_runtime.service` (T1.1 §3/§10/§12).

No engine — exercises the bootstrap (`register_default_workers`), the readiness checks
(fail-closed on partial topic coverage), and `WorkerState`. The full `run()` lifecycle against a
real engine is covered by `tests/integration/`.

T1.2/ADR-0026 (+ T3.1 R2): `register_default_workers` now delegates to the FULL 17-module
`bootstrap.register_all_workers` composition (16 + T3.1 R2's `events` module, closing the
`operadora.events.publish` gap) — closing the T1.1 verifier's interim-scope note (T1.1 shipped
only the 3 `WorkerBase` modules — auth/escalation/lgpd, 15 topics). The exact topic count is
intentionally NOT hard-coded here (`ALL_WORKER_BOOTSTRAPS`/spec-driven counts would make this
test brittle to a module gaining/losing a topic) — assertions instead check the self-consistency
invariant and representative membership across all 17 modules.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from maezo.runtime.worker_runtime.service import (
    WorkerState,
    _expected_worker_topics,
    build_readiness_checks,
    register_default_workers,
)
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers.auth import AnalyzeRequestWorker
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness


def test_register_default_workers_registers_all_17_modules() -> None:
    """T1.2/ADR-0026 + T3.1 R2: the daemon's bootstrap now covers all 17 worker modules (the 3
    `WorkerBase` modules T1.1 shipped + the 13 `FunctionWorker`-wrapped modules T1.2 adds + T3.1
    R2's raw-handler `events` module), closing the interim-scope note. `registry.count()` ==
    `len(registered_topics) - 17` because SEVENTEEN topics use the raw `harness.register()` path
    (which populates `_handlers` but NOT the `WorkerRegistry`) — MERGE RECONCILIATION (T3.1 P2b
    recurso × origin/main LGPD batch-1 × t5 escalation/ans-notify × DL-0033 dossier A2A ×
    item-9 wave-5 programa): `operadora.events.publish` (T3.1 R2); the three LGPD raw handlers
    `operadora.lgpd.request_additional_proof` (#55 R-B, T2.8), `operadora.lgpd.send_response`
    (#55 R-F, T2.8), `operadora.lgpd.notify_sla_risk` (#55 R-G, T2.8); recurso's four
    `operadora.recurso.notify_sla_risk`/`escalate_ans_timeout`/`submit_appeal`/`track_status`
    (Finding 2: async Kafka seam for their test-spec-demanded `notifications_of_type`/domain-event
    observability); `regulatorio.anssubmit.notify_regulatorio` (t5 FINDING A fix: raw handler
    emitting the `anssubmit.notify_regulatorio` notification); escalation's two
    `operadora.escalation.notify_team`/`notify_supervisor` (DL-0034, built in t5: converted from
    `WorkerBase` to raw async handlers — notifying IS the business effect and needed the async Kafka
    seam the old classes lacked, so they moved OUT of the WorkerRegistry); the two DL-0033 dossier
    A2A raw handlers `operadora.cred.prepare_dossier`/`operadora.adequacao.prepare_remediation_
    dossier` (real `DelegationDispatcher` seam); and programa's four raw handlers (item-9 wave-5)
    `operadora.programa.stratify_risk`/`stop_processing` (item A, root fix — MOVED off
    `FunctionWorker`) + `proactive_contact`/`notify_sla_risk` (items B/C, NEW workers). All
    seventeen need `ExternalTask`/async-Kafka seams a dict-first `FunctionWorker` boundary does not
    expose. Delta breakdown: 1 events + 3 lgpd + 4 recurso + 1 ans-notify + 2 escalation + 2 DL-0033
    dossier + 4 programa = 17. Every other module/topic goes through `WorkerHarness.register_worker`
    -> `WorkerRegistry.register`."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    assert len(ALL_WORKER_BOOTSTRAPS) == 17
    assert len(harness.registered_topics) > 90
    # DL-0033 real A2A wiring (dossier branch): `operadora.cred.prepare_dossier`,
    # `operadora.adequacao.prepare_remediation_dossier` and (item9-w3) the LAST edge
    # `operadora.pagto.prepare_approval_dossier` moved from `FunctionWorker` (dict-first, in the
    # WorkerRegistry) to RAW async handlers (in `_handlers`, NOT the registry) because they now
    # `await dispatcher.delegate(...)` — 11 base + 3 dossier.
    # item-9 wave-5 (event-wiring): programa's 4 raw handlers — `stratify_risk`/`stop_processing`
    # (item A, root fix: MOVED off `FunctionWorker` so they can publish an internal notification)
    # and the 2 NEW workers `proactive_contact`/`notify_sla_risk` (items B/C) — same async-Kafka-
    # seam rationale (programa.py's own module docstring).
    # item-9 notify-wiring fix: `operadora.adequacao.update_monitoring_plan` MOVED off
    # `FunctionWorker` onto a raw handler too, so it can publish an internal notification
    # (`register_adequacao_workers` used to `del kafka # unused`) — same async-Kafka-seam
    # rationale (adequacao.py's own module docstring).
    # Raw-handler count = 11 + 3 + 4 + 1 = 19.
    assert harness.registry.count() == len(harness.registered_topics) - 19


def test_register_default_workers_topics_match_expected_prefixes() -> None:
    """Representative membership across every one of the 17 modules' domain prefixes."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    topics = set(harness.registered_topics)
    assert "operadora.auth.analyze_request" in topics
    assert "operadora.escalation.notify_team" in topics
    assert "operadora.lgpd.execute_erasure" in topics
    assert "operadora.adequacao.register_fallback_commitment" in topics
    assert "operadora.ans_cron.check_calendar" in topics
    assert "regulatorio.anssubmit.submit" in topics
    assert "operadora.cancel.send_cancellation_notice" in topics
    assert "operadora.contas.register_glosa_accept" in topics
    assert "operadora.cred.register_cred_denial" in topics
    assert "operadora.events.publish" in topics
    assert "operadora.fraude.register_fraud_accusation" in topics
    assert "operadora.inadimplencia.register_contract_suspension" in topics
    assert "operadora.nip.submit_response" in topics
    assert "operadora.pagto.release_high_value_payment" in topics
    assert "operadora.programa.register_program_discharge" in topics
    assert "operadora.recurso.register_desistencia" in topics
    assert "operadora.reembolso.send_reembolso_denial" in topics


def test_register_default_workers_idempotent() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)
    first_count = len(harness.registered_topics)

    register_default_workers(harness)
    assert len(harness.registered_topics) == first_count


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


async def test_workers_registered_healthy_payload_carries_the_full_scope_detail() -> None:
    """T1.2 charter: add the scope detail string to the HEALTHY `workers_registered` payload
    (closes the T1.1 verifier's interim-scope note) — `/readyz` must be self-describing about
    what "ready" means now that it covers all 17 modules (T3.1 R2 added `events`), not just the
    3 T1.1 shipped."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    register_default_workers(harness)
    state = _state(harness=harness, expected_topics=_expected_worker_topics())

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["workers_registered"]()

    assert result.healthy is True
    assert result.detail is not None
    assert "17/17" in result.detail
    assert "topics" in result.detail


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
    """T4 producer-leg: a real (lazily-connecting) `AioKafkaEventsProducer` is now constructed at
    bring-up — see service.py's `kafka_ready` docstring. The check is present (design §12 lists
    it) but NEVER gates readiness (a Kafka outage is best-effort/audited for the mirrored topics,
    not a readiness failure)."""
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


# ---------------------------------------------------------------------------
# audit_sink_ready readiness gate (T1.10 T-D, ADR-0007 L0 fail-closed)
# ---------------------------------------------------------------------------


async def test_audit_sink_ready_red_when_sink_missing() -> None:
    """No DATABASE_URL / no sink -> /readyz RED, WITHOUT probing (fail-closed by construction)."""
    state = _state()  # default settings: no DATABASE_URL -> audit_sink None
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["audit_sink_ready"]()
    assert result.healthy is False
    assert "not constructed" in (result.detail or "")


async def test_audit_sink_ready_red_when_sink_unreachable() -> None:
    """A present sink pointed at an unreachable Postgres probes RED (connection refused, bounded)."""
    from maezo.gateway.audit_postgres import PostgresAuditSink

    settings = WorkerRuntimeSettings(dep_connect_timeout_s=1.0)
    # Port 1 => immediate ECONNREFUSED; construction is pure, the probe is what fails-closed.
    sink = PostgresAuditSink("postgresql://maezo@127.0.0.1:1/none", "amh")
    state = WorkerState(settings=settings, audit_sink=sink)
    try:
        checks = {c.__name__: c for c in build_readiness_checks(state)}
        result = await checks["audit_sink_ready"]()
        assert result.healthy is False
        assert "unreachable" in (result.detail or "")
    finally:
        await sink.aclose()


async def test_audit_sink_ready_green_when_probe_passes(monkeypatch: Any) -> None:
    """A sink whose bounded connectivity probe succeeds flips /readyz GREEN. The probe is stubbed
    (no live Postgres) — the real-PG green path is the integration suite."""
    from maezo.gateway.audit_postgres import PostgresAuditSink

    sink = PostgresAuditSink("postgresql://maezo@localhost:5432/maezo", "amh")

    async def _ok() -> None:
        return None

    monkeypatch.setattr(sink, "check_ready", _ok)
    state = _state(audit_sink=sink)
    try:
        checks = {c.__name__: c for c in build_readiness_checks(state)}
        result = await checks["audit_sink_ready"]()
        assert result.healthy is True
    finally:
        await sink.aclose()


# ---------------------------------------------------------------------------
# _bring_up_dependencies — fail-closed sink gating (design §7 T-D / MUST-FIX 2)
# ---------------------------------------------------------------------------


async def test_bring_up_fail_closed_without_database_url() -> None:
    """No DATABASE_URL => no audit sink => the harness is NEVER built and NEVER started, so the
    daemon does not enter the fetch-and-lock rotation (fail-closed, ADR-0007). Readiness is red."""
    from maezo.runtime.worker_runtime.service import _bring_up_dependencies

    state = _state()  # default settings: DATABASE_URL unset
    try:
        await _bring_up_dependencies(state)

        assert state.audit_sink is None
        assert state.audit_sink_ready is False
        assert state.harness is None, "harness must NOT be built without a durable audit sink"
        assert state.harness_task is None, "daemon must NOT enter the fetch rotation unaudited"

        checks = {c.__name__: c for c in build_readiness_checks(state)}
        audit = await checks["audit_sink_ready"]()
        workers = await checks["workers_registered"]()
        assert audit.healthy is False
        assert workers.healthy is False
    finally:
        if state.transport is not None:
            await state.transport.close()


class _SpyHarness:
    """Minimal WorkerHarness stand-in for composition-root wiring tests — records the `audit_sink`
    seam and exposes just enough surface for `_bring_up_dependencies`.

    Since the T1.10 wave merge the REAL `WorkerHarness` declares `audit_sink: AuditEmitter | None
    = None` (T-C, Optional-fail-closed: a sink-less harness raises `AuditEmitError` before any
    completion). This spy stays a spy for the same reason as before: these tests prove the
    COMPOSITION ROOT's threading/gating (sink into the ctor, fetch rotation gated on the probe),
    not harness mechanics — the real harness+sink end-to-end lives in the T-C acceptance suites
    (tests/unit/tools/workers/test_harness_audit_emit.py and the integration lane)."""

    def __init__(self, transport: Any, *, worker_id: str, audit_sink: Any = None, **kwargs: Any) -> None:
        self.transport = transport
        self.worker_id = worker_id
        self.audit_sink = audit_sink
        self.kwargs = kwargs
        self._topics: list[str] = []

    def register_worker(self, worker: Any) -> None:
        self._topics.append(worker.topic)

    def register(self, topic: str, handler: Any, *, variables: Any = None) -> None:
        self._topics.append(topic)

    @property
    def registered_topics(self) -> list[str]:
        return sorted(self._topics)

    async def run(self) -> None:
        await asyncio.Event().wait()  # stays "running" until cancelled


async def test_bring_up_threads_audit_sink_and_engine_and_spawns_when_probe_green(
    monkeypatch: Any,
) -> None:
    """Healthy path (probe stubbed green): the composition root THREADS `audit_sink` into the
    harness constructor (T-C's seam) AND the `engine`+`audit_sink` seams into
    `register_default_workers` (the worker-side seam is a per-call `FreshSinkAuditEmitter`, NOT
    the pooled sink — sync dispatches emit on fresh `asyncio.run` loops), and ENTERS the
    fetch-and-lock rotation (spawns the harness) only because the sink verified."""
    import maezo.runtime.worker_runtime.service as svc
    from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink

    captured: dict[str, Any] = {}

    def _spy_register(
        harness: Any,
        *,
        dmn: Any = None,
        engine: Any = None,
        audit_sink: Any = None,
        tenant_id: str = "",
        kafka: Any = None,
        dossier_dispatcher: Any = None,
    ) -> None:
        captured["engine"] = engine
        captured["dmn"] = dmn
        captured["audit_sink"] = audit_sink
        captured["tenant_id"] = tenant_id
        captured["kafka"] = kafka
        captured["dossier_dispatcher"] = dossier_dispatcher

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)

        assert isinstance(state.audit_sink, PostgresAuditSink)
        assert state.audit_sink_ready is True
        assert isinstance(state.harness, _SpyHarness)
        # T-C seam conformance: the SAME sink instance is threaded into the harness constructor.
        assert state.harness.audit_sink is state.audit_sink
        # GAP-INAD-1 engine seam threaded into the bootstrap.
        assert captured["engine"] is state.engine_transport
        # T1.10 wave: the worker-side audit seam is a loop-safe per-call emitter (handoff_rescisao
        # emits on its own asyncio.run loop — the pooled sink must never cross loops).
        assert isinstance(captured["audit_sink"], FreshSinkAuditEmitter)
        # T2.6-EB3 part 3: the daemon's own tenant identity threads into register_default_workers
        # (-> ans_cron.trigger_submissions' seam).
        assert captured["tenant_id"] == settings.tenant_id
        # T4 producer-leg: the real (lazily-connecting) Kafka publisher threads into
        # register_default_workers too — constructed purely, no network at bring-up.
        assert captured["kafka"] is state.kafka_publisher
        assert state.kafka_publisher is not None
        assert state.harness_task is not None, "verified sink -> daemon enters the fetch rotation"
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_does_not_spawn_when_sink_probe_red(monkeypatch: Any) -> None:
    """Sink present but unreachable at boot: the harness may be BUILT (with the sink) but the daemon
    must NOT enter the fetch rotation — an un-auditable daemon that locked tasks would stall them."""
    import maezo.runtime.worker_runtime.service as svc

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)

        assert state.audit_sink_ready is False
        assert state.harness_task is None, "unverified sink -> daemon must NOT fetch (fail-closed)"
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()
