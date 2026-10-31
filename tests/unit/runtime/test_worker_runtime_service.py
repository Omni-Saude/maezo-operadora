"""Unit tests for `maezo.runtime.worker_runtime.service` (T1.1 §3/§10/§12).

No engine — exercises the bootstrap (`register_default_workers`), the readiness checks
(fail-closed on partial topic coverage), and `WorkerState`. The full `run()` lifecycle against a
real engine is covered by `tests/integration/`.

T1.2/ADR-0026 (+ T3.1 R2 + VW1-P0-FENCES + VW4/GP11): `register_default_workers` now delegates to the FULL
19-module `bootstrap.register_all_workers` composition (16 + T3.1 R2's `events` module +
VW1-P0-FENCES' `vendor_admin` module, closing the
`operadora.events.publish` gap) — closing the T1.1 verifier's interim-scope note (T1.1 shipped
only the 3 `WorkerBase` modules — auth/escalation/lgpd, 15 topics). The exact topic count is
intentionally NOT hard-coded here (`ALL_WORKER_BOOTSTRAPS`/spec-driven counts would make this
test brittle to a module gaining/losing a topic) — assertions instead check the self-consistency
invariant and representative membership across all 18 modules.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

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


def test_register_default_workers_registers_all_19_modules() -> None:
    """T1.2/ADR-0026 + T3.1 R2 + VW1-P0-FENCES: the daemon's bootstrap now covers all 18 worker
    modules (the 3 `WorkerBase` modules T1.1 shipped + the 13 `FunctionWorker`-wrapped modules
    T1.2 adds + T3.1 R2's raw-handler `events` module + VW1-P0-FENCES' raw-handler `vendor_admin`
    module), closing the interim-scope note. `registry.count()` ==
    `len(registered_topics) - 25` because TWENTY-FIVE topics use the raw `harness.register()` path
    (which populates `_handlers` but NOT the `WorkerRegistry`) — MERGE RECONCILIATION (T3.1 P2b
    recurso × origin/main LGPD batch-1 × t5 escalation/ans-notify × DL-0033 dossier A2A ×
    item-9 wave-5 programa): `operadora.events.publish` (T3.1 R2); the three LGPD raw handlers
    `operadora.lgpd.request_additional_proof` (#55 R-B, T2.8), `operadora.lgpd.send_response`
    (#55 R-F, T2.8), `operadora.lgpd.notify_sla_risk` (#55 R-G, T2.8); recurso's THREE
    `operadora.recurso.notify_sla_risk`/`escalate_ans_timeout`/`comunicar_resposta` (async Kafka
    seam for their `notifications_of_type`/domain-event observability; `comunicar_resposta` ALSO
    needs `task.business_key` to mint its protocolo deterministically. ADR-0040 deleted the
    appellant branch, so `submit_appeal`/`track_status` are GONE — the delta drops 4 -> 3);
    `regulatorio.anssubmit.notify_regulatorio` (t5 FINDING A fix: raw handler
    emitting the `anssubmit.notify_regulatorio` notification) + the other TWO anssubmit raw
    handlers, `regulatorio.anssubmit.retransmit` (t9-nack-vars) and `regulatorio.anssubmit.submit`
    (W8 W5-XFIX PR-2 — each publishes its typed notification the sync boundary cannot reach);
    escalation's two
    `operadora.escalation.notify_team`/`notify_supervisor` (DL-0034, built in t5: converted from
    `WorkerBase` to raw async handlers — notifying IS the business effect and needed the async Kafka
    seam the old classes lacked, so they moved OUT of the WorkerRegistry); the two DL-0033 dossier
    A2A raw handlers `operadora.cred.prepare_dossier`/`operadora.adequacao.prepare_remediation_
    dossier` (real `DelegationDispatcher` seam); and programa's four raw handlers (item-9 wave-5)
    `operadora.programa.stratify_risk`/`stop_processing` (item A, root fix — MOVED off
    `FunctionWorker`) + `proactive_contact`/`notify_sla_risk` (items B/C, NEW workers). All
    raw handlers need `ExternalTask`/async-Kafka seams a dict-first `FunctionWorker` boundary does
    not expose. Delta breakdown (re-derived, the running total had drifted from the list above):
    1 events + 3 lgpd + 3 recurso + 3 anssubmit (notify_regulatorio t5 + retransmit t9-nack-vars
    + submit W8 W5-XFIX PR-2) + 2 escalation + 4 dossier (DL-0033 + the item9-w3 pagto edge + the
    R-081 inadimplencia edge) + 4 programa + 1 adequacao update_monitoring_plan + 1 auth + 1 vendor
    membership publication (VW1-P0-FENCES) + 2 vendor.suppression (VW4/GP11 envelope — raw
    handlers with declared fetch scopes, module docstring) = 25. Every other module/topic goes through
    `WorkerHarness.register_worker` -> `WorkerRegistry.register`."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    assert len(ALL_WORKER_BOOTSTRAPS) == 19
    assert len(harness.registered_topics) > 90
    # DL-0033 real A2A wiring (dossier branch): `operadora.cred.prepare_dossier`,
    # `operadora.adequacao.prepare_remediation_dossier` and (item9-w3) the pagto edge
    # `operadora.pagto.prepare_approval_dossier` moved from `FunctionWorker` (dict-first, in the
    # WorkerRegistry) to RAW async handlers (in `_handlers`, NOT the registry) because they now
    # `await dispatcher.delegate(...)`. R-081 (metade de ORIGEM) adds the FOURTH dossier edge,
    # `operadora.inadimplencia.prepare_dossier` (`arrears.followup` -> Fernando), for the SAME
    # structural reason.
    # item-9 wave-5 (event-wiring): programa's 4 raw handlers — `stratify_risk`/`stop_processing`
    # (item A, root fix: MOVED off `FunctionWorker` so they can publish an internal notification)
    # and the 2 NEW workers `proactive_contact`/`notify_sla_risk` (items B/C) — same async-Kafka-
    # seam rationale (programa.py's own module docstring).
    # item-9 notify-wiring fix: `operadora.adequacao.update_monitoring_plan` MOVED off
    # `FunctionWorker` onto a raw handler too, so it can publish an internal notification
    # (`register_adequacao_workers` used to `del kafka # unused`) — same async-Kafka-seam
    # rationale (adequacao.py's own module docstring).
    # t9-nack-vars: `regulatorio.anssubmit.retransmit` MOVED off `FunctionWorker` onto a raw handler
    # (`make_retransmit_handler` — the name the contract itself uses, SP-OP-ANS-SUBMIT-001.md:157)
    # for the SAME async-Kafka-seam reason: it publishes the `anssubmit.retransmit` internal
    # notification, which the sync `FunctionWorker.execute` boundary cannot reach.
    # WP-J1-09 (owner decision #17): `operadora.auth.notify_sla_risk` MOVED off
    # `register_worker(NotifySlaRiskWorker())` onto a raw handler, for the SAME async-Kafka-seam
    # reason as every entry above — it now publishes the `auth.notify_sla_risk` internal
    # notification the bridge turns into `ESC-{tenant}-sla-auth-{guia}`, and a synchronous
    # `WorkerBase.execute` reaches no producer (that absence is exactly why the alert used to
    # reach nobody). The CLASS still exists and is called BY the handler; it is simply no longer
    # registered itself, so the topic keeps exactly one server.
    # Raw-handler count, re-derived cell by cell (the previous one-line sums had drifted from the
    # assertion below): 1 events + 3 lgpd + 3 recurso + 3 anssubmit (notify_regulatorio +
    # retransmit + submit/W8-W5-XFIX) + 2 escalation + 4 dossier + 4 programa + 1 adequacao
    # + 1 auth + 1 vendor membership (VW1-P0-FENCES: the publication cadence is a raw async
    # handler over injected source seams — vendor_admin.py's module docstring) + 2
    # vendor.suppression (VW4/GP11 envelope handlers) = 25.
    assert harness.registry.count() == len(harness.registered_topics) - 25


def test_register_default_workers_topics_match_expected_prefixes() -> None:
    """Representative membership across every one of the 18 modules' domain prefixes."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness)

    topics = set(harness.registered_topics)
    assert "operadora.auth.analyze_request" in topics
    assert "operadora.escalation.notify_team" in topics
    assert "operadora.lgpd.execute_request" in topics  # R-181: colapso dos 3 orfaos no modelado
    assert "operadora.adequacao.register_fallback_commitment" in topics
    assert "operadora.ans_cron.trigger_submissions" in topics
    assert "regulatorio.anssubmit.submit" in topics
    assert "operadora.cancel.send_cancellation_notice" in topics
    assert "operadora.contas.registrar_glosa" in topics
    assert "operadora.cred.register_cred_denial" in topics
    assert "operadora.events.publish" in topics
    assert "operadora.fraude.register_fraud_accusation" in topics
    assert "operadora.inadimplencia.register_contract_suspension" in topics
    assert "operadora.nip.submit_response" in topics
    assert "operadora.pagto.release_high_value_payment" in topics
    assert "operadora.programa.register_program_discharge" in topics
    assert "operadora.recurso.registrar_indeferimento" in topics
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
    what "ready" means now that it covers all 18 modules (T3.1 R2 added `events`,
    VW1-P0-FENCES added `vendor_admin`), not just the
    3 T1.1 shipped."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    register_default_workers(harness)
    state = _state(harness=harness, expected_topics=_expected_worker_topics())

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["workers_registered"]()

    assert result.healthy is True
    assert result.detail is not None
    assert "19/19" in result.detail
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
        denial_written_notice: bool = False,
        tenant_id: str = "",
        kafka: Any = None,
        dossier_dispatcher: Any = None,
        document_request_host_installed: bool = False,
        publish_ledger: Any = None,
        pagto_valor_invalido_pendente_dados: bool = False,
        inad_rn593_estrito: bool = False,
        pagto_fatos_estritos: bool = False,
        inad_fatos_estritos: bool = False,
        contas_sla_risk_entrega: bool = False,
        lgpd_legal_hold_predicate: bool = False,
        junta_rn424_guard: bool = False,
        auth_prazo_dias_uteis: bool = False,
    ) -> None:
        captured["auth_prazo_dias_uteis"] = auth_prazo_dias_uteis
        captured["contas_sla_risk_entrega"] = contas_sla_risk_entrega
        captured["pagto_fatos_estritos"] = pagto_fatos_estritos
        captured["inad_fatos_estritos"] = inad_fatos_estritos
        captured["lgpd_legal_hold_predicate"] = lgpd_legal_hold_predicate
        captured["junta_rn424_guard"] = junta_rn424_guard
        captured["publish_ledger"] = publish_ledger
        captured["pagto_valor_invalido_pendente_dados"] = pagto_valor_invalido_pendente_dados
        captured["inad_rn593_estrito"] = inad_rn593_estrito
        captured["engine"] = engine
        captured["dmn"] = dmn
        captured["audit_sink"] = audit_sink
        captured["denial_written_notice"] = denial_written_notice
        captured["tenant_id"] = tenant_id
        captured["kafka"] = kafka
        captured["dossier_dispatcher"] = dossier_dispatcher
        captured["document_request_host_installed"] = document_request_host_installed

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    # WP-J1-03: the probe now takes the document-request exclusivity seam, exactly as the
    # live registration does — a stub with the old arity would let the readiness
    # expectation and the real registration silently disagree about the topic set.
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
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
        # WP-J1-03: the exclusivity seam reaches the bootstrap, defaulting to the
        # historical posture (generic `RequestDocumentsWorker` registered) when unset.
        assert captured["document_request_host_installed"] is False
        assert captured["publish_ledger"] is None  # F-092 dedup ships dark (MAEZO_EXTASK_PUBLISH_DEDUP)
        # F-142 / R-053: PAGTO invalid-amount -> PENDENTE_DADOS fact ships dark.
        assert settings.pagto_valor_invalido_pendente_dados is False
        assert captured["pagto_valor_invalido_pendente_dados"] is False
        assert captured["inad_rn593_estrito"] is False  # R-027 ships dark (MAEZO_INAD_RN593_ESTRITO)
        # R-063 / F-029: fatos estritos ship dark (MAEZO_PAGTO_FATOS_ESTRITOS / MAEZO_INAD_FATOS_ESTRITOS).
        assert captured["pagto_fatos_estritos"] is False
        assert captured["inad_fatos_estritos"] is False
        # R-107 / F-135: a entrega do alerta de SLA de CONTAS ships dark (MAEZO_CONTAS_SLA_RISK_ENTREGA).
        assert settings.contas_sla_risk_entrega is False
        assert captured["contas_sla_risk_entrega"] is False
        # R-044 / F-062: the legal-hold predicate ships dark (MAEZO_LGPD_LEGAL_HOLD_PREDICATE).
        assert settings.lgpd_legal_hold_predicate is False
        assert captured["lgpd_legal_hold_predicate"] is False
        assert captured["junta_rn424_guard"] is False  # R-064 ships dark (MAEZO_AUTH_JUNTA_RN424_GUARD)
        # R-005 / F-018: prazo de resposta AUTH em dias uteis ships dark (MAEZO_AUTH_PRAZO_DIAS_UTEIS).
        assert settings.auth_prazo_dias_uteis is False
        assert captured["auth_prazo_dias_uteis"] is False
        # R-004 / F-032: the second NEGAR owner is gone — no `denial_notice_host` seam is
        # threaded at all; the retired switch stays off by default (ON refuses to register).
        assert settings.denial_notice_owner is False
        assert "denial_notice_host" not in captured
        # R-004 (RN 623 art. 14): the written-notice state ships DARK.
        assert settings.auth_denial_written_notice is False
        assert captured["denial_written_notice"] is False
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
    # WP-J1-03: the probe now takes the document-request exclusivity seam, exactly as the
    # live registration does — a stub with the old arity would let the readiness
    # expectation and the real registration silently disagree about the topic set.
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
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


# ---------------------------------------------------------------------------------------------
# R-027 — fiacao da flag dark `MAEZO_INAD_RN593_ESTRITO` (revisao independente do PR #1102, F1):
# (a) `register_default_workers` repassa a flag aos DOIS workers de SP-OP-INADIMPLENCIA-001;
# (b) o alias de env da setting; (c) `_bring_up_dependencies` repassa a setting ao bootstrap.
# Sem estes testes, forcar o modo estrito ligado (ou desligado) em producao nao quebrava nada.
# ---------------------------------------------------------------------------------------------

_INAD_RESOLVE_FACTS = "operadora.inadimplencia.resolve_facts"
_INAD_SUSPENSION = "operadora.inadimplencia.register_contract_suspension"


@pytest.mark.parametrize("flag", [False, True])
def test_register_default_workers_threads_inad_rn593_flag_to_both_workers(flag: bool) -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness, inad_rn593_estrito=flag)

    facts = harness.registry.get(_INAD_RESOLVE_FACTS)
    susp = harness.registry.get(_INAD_SUSPENSION)
    assert facts is not None and susp is not None
    assert facts._fn.keywords["rn593_estrito"] is flag  # type: ignore[attr-defined]
    assert susp._fn.keywords["estrito"] is flag  # type: ignore[attr-defined]


def test_settings_inad_rn593_estrito_env_alias_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_INAD_RN593_ESTRITO", raising=False)
    assert WorkerRuntimeSettings().inad_rn593_estrito is False
    monkeypatch.setenv("MAEZO_INAD_RN593_ESTRITO", "true")
    assert WorkerRuntimeSettings().inad_rn593_estrito is True


@pytest.mark.parametrize("flag", [False, True])
async def test_bring_up_threads_inad_rn593_estrito_setting(
    monkeypatch: pytest.MonkeyPatch, flag: bool
) -> None:
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo", MAEZO_INAD_RN593_ESTRITO=flag
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert captured["inad_rn593_estrito"] is flag
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


# ---------------------------------------------------------------------------------------------
# R-044 — fiacao da flag dark `MAEZO_LGPD_LEGAL_HOLD_PREDICATE` (F-062, ADR-0020 item 6):
# (a) `register_default_workers` repassa a flag ao `ExecuteRequestWorker`; (b) o alias de env;
# (c) `_bring_up_dependencies` repassa a setting ao bootstrap. Cada um falha sob mutacao da fiacao.
# ---------------------------------------------------------------------------------------------

_LGPD_EXECUTE_REQUEST = "operadora.lgpd.execute_request"


@pytest.mark.parametrize("flag", [False, True])
def test_register_default_workers_threads_lgpd_legal_hold_flag(flag: bool) -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness, lgpd_legal_hold_predicate=flag)

    worker = harness.registry.get(_LGPD_EXECUTE_REQUEST)
    assert worker is not None
    assert worker._legal_hold_predicate is flag  # type: ignore[attr-defined]


def test_settings_lgpd_legal_hold_predicate_env_alias_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_LGPD_LEGAL_HOLD_PREDICATE", raising=False)
    assert WorkerRuntimeSettings().lgpd_legal_hold_predicate is False
    monkeypatch.setenv("MAEZO_LGPD_LEGAL_HOLD_PREDICATE", "true")
    assert WorkerRuntimeSettings().lgpd_legal_hold_predicate is True


@pytest.mark.parametrize("flag", [False, True])
async def test_bring_up_threads_lgpd_legal_hold_predicate_setting(
    monkeypatch: pytest.MonkeyPatch, flag: bool
) -> None:
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo", MAEZO_LGPD_LEGAL_HOLD_PREDICATE=flag
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert captured["lgpd_legal_hold_predicate"] is flag
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_threads_the_written_notice_flag_when_the_deployment_turns_it_on(
    monkeypatch: Any,
) -> None:
    """R-004 (RN 623 art. 14): `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE=true` reaches the bootstrap.

    Mutation guard for the wiring: a hard-coded `denial_written_notice=False` in the live
    composition would pass the default bring-up test above and fail here."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo",
        MAEZO_AUTH_DENIAL_WRITTEN_NOTICE=True,
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert captured["denial_written_notice"] is True
        assert isinstance(state.harness, _SpyHarness)
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_threads_the_junta_rn424_guard_when_the_deployment_turns_it_on(
    monkeypatch: Any,
) -> None:
    """R-064 (RN 424/2017 art. 3 I): `MAEZO_AUTH_JUNTA_RN424_GUARD=true` reaches the bootstrap.

    Mutation guard for the wiring: a hard-coded `junta_rn424_guard=False` (or the kwarg dropped)
    in the live composition would pass the default bring-up test above and fail here."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo",
        MAEZO_AUTH_JUNTA_RN424_GUARD=True,
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert captured["junta_rn424_guard"] is True
        assert isinstance(state.harness, _SpyHarness)
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_threads_the_business_day_deadline_flag_when_the_deployment_turns_it_on(
    monkeypatch: Any,
) -> None:
    """R-005 / F-018 (RN 623/2024 art. 12): `MAEZO_AUTH_PRAZO_DIAS_UTEIS=true` reaches the bootstrap.

    Mutation guard for the wiring: a hard-coded `auth_prazo_dias_uteis=False` (or the kwarg
    dropped) in the live composition would pass the default bring-up test above and fail here."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo",
        MAEZO_AUTH_PRAZO_DIAS_UTEIS=True,
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert captured["auth_prazo_dias_uteis"] is True
        assert isinstance(state.harness, _SpyHarness)
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_refuses_to_register_when_the_retired_second_owner_is_requested(
    monkeypatch: Any,
) -> None:
    """R-004 / F-032: `MAEZO_DENIAL_NOTICE_OWNER=true` is a tombstone — the daemon registers
    NOTHING (harness unbuilt => readiness red), never a silent fallback to a removed owner."""
    import maezo.runtime.worker_runtime.service as svc

    calls: list[Any] = []

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(
        DATABASE_URL="postgresql://maezo@localhost:5432/maezo",
        MAEZO_DENIAL_NOTICE_OWNER=True,
    )
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert calls == [], "the retired owner switch must stop registration before it starts"
        assert state.harness is None
        assert state.harness_task is None
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


async def test_bring_up_threads_pagto_flag_from_env_when_on(monkeypatch: Any) -> None:
    """F-142 / R-053 fiação (a): `MAEZO_PAGTO_VALOR_INVALIDO_PENDENTE_DADOS=true` chega ao
    `register_default_workers` pela raiz de composição — sem isso a flag ligada seria inerte."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False  # no fetch rotation: this test only proves the threading

    monkeypatch.setenv("MAEZO_PAGTO_VALOR_INVALIDO_PENDENTE_DADOS", "true")
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    assert settings.pagto_valor_invalido_pendente_dados is True
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)

        assert captured["pagto_valor_invalido_pendente_dados"] is True
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


def test_contas_sla_risk_entrega_setting_alias_e_default_off(monkeypatch: Any) -> None:
    """R-107 / F-135 fiacao (a): alias `MAEZO_CONTAS_SLA_RISK_ENTREGA`, default OFF (dark)."""
    monkeypatch.delenv("MAEZO_CONTAS_SLA_RISK_ENTREGA", raising=False)
    assert WorkerRuntimeSettings().contas_sla_risk_entrega is False
    assert (
        WorkerRuntimeSettings.model_fields["contas_sla_risk_entrega"].alias == "MAEZO_CONTAS_SLA_RISK_ENTREGA"
    )
    monkeypatch.setenv("MAEZO_CONTAS_SLA_RISK_ENTREGA", "true")
    assert WorkerRuntimeSettings().contas_sla_risk_entrega is True


async def test_bring_up_threads_contas_sla_risk_entrega_from_env_when_on(monkeypatch: Any) -> None:
    """R-107 / F-135 fiacao (b): a bandeira ligada chega ao `register_default_workers` pela raiz
    de composicao — sem isso a entrega ligada seria inerte."""
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False  # sem rotacao de fetch: so prova a fiacao

    monkeypatch.setenv("MAEZO_CONTAS_SLA_RISK_ENTREGA", "true")
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)

        assert captured["contas_sla_risk_entrega"] is True
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


def _pagto_calculate_facts_via_default_workers(**kwargs: Any) -> dict[str, Any]:
    """Executa `operadora.pagto.calculate_facts` registrado pelo `register_default_workers` REAL
    (não o `register_pagto_workers` direto), com `valor_pagamento_cents=0` (inválido)."""
    from maezo.tools.workers.dmn_transport import FakeDmnTransport

    fake = FakeDmnTransport()
    fake.register(
        "pagto_alcada",
        [{"faixa_valor": "ANALISE_HUMANA", "grupo_aprovador": "comite-financeiro", "tier_minimo": 4}],
    )
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="unit-test-pagto-default-workers")
    register_default_workers(harness, dmn=fake, **kwargs)  # type: ignore[arg-type]
    worker = harness.registry.get("operadora.pagto.calculate_facts")
    assert worker is not None
    result: dict[str, Any] = worker.execute(
        {"tenant_id": "amh", "ordem_pagamento_id": "OP-F142-WIRE", "valor_pagamento_cents": 0}
    )
    return result


def test_register_default_workers_threads_pagto_flag_when_on() -> None:
    """F-142 / R-053 fiação (b): o kwarg ligado atravessa `register_default_workers` até o
    handler de PAGTO — valor inválido vira o FATO `dados_pagamento_validos=False`."""
    result = _pagto_calculate_facts_via_default_workers(pagto_valor_invalido_pendente_dados=True)
    assert result["dados_pagamento_validos"] is False


def test_register_default_workers_pagto_flag_omitted_stays_dark() -> None:
    """Kwarg omitido (default off): o handler não escreve `dados_pagamento_validos`."""
    result = _pagto_calculate_facts_via_default_workers()
    assert "dados_pagamento_validos" not in result


# ---------------------------------------------------------------------------------------------
# R-063 / F-029 — fiacao das flags dark `MAEZO_PAGTO_FATOS_ESTRITOS` / `MAEZO_INAD_FATOS_ESTRITOS`:
# (a) alias de env + default OFF; (b) `_bring_up_dependencies` repassa a setting; (c)
# `register_default_workers` REAL repassa ao worker, provado pela execucao do handler registrado
# (o default das funcoes e o estrito, entao um kwarg nao repassado tambem quebra o teste OFF).
# ---------------------------------------------------------------------------------------------

_R063_FLAGS = [
    ("pagto_fatos_estritos", "MAEZO_PAGTO_FATOS_ESTRITOS"),
    ("inad_fatos_estritos", "MAEZO_INAD_FATOS_ESTRITOS"),
]


@pytest.mark.parametrize(("campo", "alias"), _R063_FLAGS)
def test_settings_r063_flags_env_alias_defaults_off(
    monkeypatch: pytest.MonkeyPatch, campo: str, alias: str
) -> None:
    monkeypatch.delenv(alias, raising=False)
    assert getattr(WorkerRuntimeSettings(), campo) is False
    monkeypatch.setenv(alias, "true")
    assert getattr(WorkerRuntimeSettings(), campo) is True


@pytest.mark.parametrize(("campo", "alias"), _R063_FLAGS)
@pytest.mark.parametrize("flag", [False, True])
async def test_bring_up_threads_r063_flags(
    monkeypatch: pytest.MonkeyPatch, campo: str, alias: str, flag: bool
) -> None:
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False  # no fetch rotation: this test only proves the threading

    monkeypatch.setenv(alias, "true" if flag else "false")
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    state = WorkerState(
        settings=WorkerRuntimeSettings(DATABASE_URL="postgresql://maezo@localhost:5432/maezo")
    )
    try:
        await svc._bring_up_dependencies(state)
        assert captured[campo] is flag
        outro = next(c for c, _a in _R063_FLAGS if c != campo)
        assert captured[outro] is False
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


def _executa_registrado(topic: str, variables: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="unit-test-r063-default-workers")
    register_default_workers(harness, **kwargs)
    worker = harness.registry.get(topic)
    assert worker is not None
    result: dict[str, Any] = worker.execute(variables)
    return result


_PAGTO_SEMENTE = {
    "ordem_pagamento_id": "OP-R063-WIRE",
    "prestador_id": "prov:teste-0001",
    "instrumento_pagamento": "ted",
    "lastro_confirmado": True,
}


@pytest.mark.parametrize("kwargs", [{}, {"pagto_fatos_estritos": False}])
def test_register_default_workers_pagto_fatos_flag_off_ecoa(kwargs: dict[str, Any]) -> None:
    out = _executa_registrado("operadora.pagto.validate_payment_data", dict(_PAGTO_SEMENTE), **kwargs)
    assert out["lastro_confirmado"] is True


def test_register_default_workers_pagto_fatos_flag_on_resolve() -> None:
    out = _executa_registrado(
        "operadora.pagto.validate_payment_data", dict(_PAGTO_SEMENTE), pagto_fatos_estritos=True
    )
    assert out["lastro_confirmado"] is False
    assert out["dados_pagamento_validos"] is True


@pytest.mark.parametrize("kwargs", [{}, {"inad_fatos_estritos": False}])
def test_register_default_workers_inad_fatos_flag_off_ecoa(kwargs: dict[str, Any]) -> None:
    out = _executa_registrado(
        "operadora.inadimplencia.resolve_facts", {"dentro_janela_purga": False}, **kwargs
    )
    assert out["dentro_janela_purga"] is False


def test_register_default_workers_inad_fatos_flag_on_nao_ecoa() -> None:
    out = _executa_registrado(
        "operadora.inadimplencia.resolve_facts", {"dentro_janela_purga": False}, inad_fatos_estritos=True
    )
    assert out["dentro_janela_purga"] is True
