"""Worker-runtime SERVICE — the daemon supervising `WorkerHarness.run()` (T1.1 design §3/§10).

This is the residual leg of ADR-0001: the engine (CIB Seven) calls external tasks (engine ->
worker); this daemon fetches them, runs the registered handler, and completes/fails the task on
the engine.

Bring-up order (design §3/§10/§12), 0 -> E:
  STEP 0  Configure observability (AF-13) — `platform.observability.bootstrap_observability`
          installs structlog's processor chain and the OTel TracerProvider BEFORE the daemon's
          first log line. Isolated (a raise leaves `observability_configured` red, never
          CrashLoops) and EXPLICIT about the no-op exporter case.
  STEP A  Bind the health app IMMEDIATELY in an asyncio task. `/healthz` answers 200 right away
          (liveness) BEFORE any DEPENDENCY comes up — the pod never CrashLoops because the
          engine is slow/unavailable. Signal ownership (SIGTERM/SIGINT) is claimed here.
          STEP 0 runs first and is deliberately NOT a dependency: in-process only (structlog +
          an OTel provider whose OTLP channel is lazy), isolated, measured non-blocking (~9 ms
          even with a malformed endpoint). Ordering it after the bind would mean this daemon's
          own first log lines run under a configuration it is about to replace.
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
from functools import lru_cache
from typing import Any

import structlog

from maezo.a2a import DelegationDispatcher
from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.dmn import GatedDmnTransport
from maezo.gateway.seams.fhir import GatedFhirReader
from maezo.gateway.tool_registry import (
    agent_credential,
    build_agent_fhir_seam,
    build_cibseven_seam,
    build_dmn_seam,
    build_worker_credential_view,
    build_worker_seam_context,
    effect_seams_gated,
)
from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.integrations.events_kafka_producer import AioKafkaEventsProducer
from maezo.platform.observability import (
    ObservabilityStatus,
    bootstrap_observability,
    get_metrics_collector,
)
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.ans_submit import ANS_SUBMIT_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.auth import AUTH_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.credenciamento import CRED_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.escalation import ESCALATION_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.events import EVENTS_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    ExternalTask,
    TopicSubscription,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import LGPD_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.nip import NIP_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.pagto import PAGTO_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.programa import PROGRAMA_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.recurso import RECURSO_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.reembolso import REEMBOLSO_BPMN_ERROR_ALLOWLIST

from .settings import WorkerRuntimeSettings

logger = structlog.get_logger(__name__)

#: The TARGET agents of the dossier delegation edges whose graphs declare a FHIR read seam
#: (CC-03/AND-03). Mirrors `a2a_composition._DOSSIER_EDGE_AGENT_IDS` — kept as a local literal
#: rather than imported because that module is imported LAZILY inside `_bring_up_dependencies`
#: (the pre-existing deferral, so the worker daemon does not pay for the A2A stack at import
#: time). The root's refusal covers only ONE direction — an id in THIS tuple that the root does
#: not serve raises at composition; the opposite (an edge the root DOES serve that this tuple
#: forgets) raises nothing: that agent would simply get no reader, in silence, which is the
#: CC-03/AND-03 defect itself. The both-directions guard is therefore a unit test over the two
#: literals (`test_dossier_fhir_wiring.py::test_worker_fhir_agent_ids_match_the_a2a_root_edge_
#: agents`) — a repo-level invariant CI catches before any deploy. Deliberately NOT a bare
#: `assert` here (stripped under `python -O`) and NOT a raise at bring-up (that would turn a
#: constant typo into a dossier outage for the three dossier workers).
_DOSSIER_FHIR_AGENT_IDS: tuple[str, ...] = ("carolina", "andre")

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
# T-E (audited-refusal) lands. This resolves to `{ERR_EVENT_PUBLISH_FAILED,
# ERR_DSR_IDENTITY_UNVERIFIED}` (Tier-0 — the non-adverse technical fail-safes: a publish failure
# routed to retry/fallback; a mechanically-unverifiable LGPD titular routed to a neutral terminal)
# `| {ERR_ESC_NOTIFY_FAILED}` (t8-escalation-boundary, Tier-1 — escalation's notify-channel fail-safe:
# a notify_team/notify_supervisor publish failure routes via BE_FalhaNotificacao/BE_NotifFallbackFailed/
# BE_NotifSupervisorFailed to the supervisor fallback + the mandatory HITL user task; G2-fs, NOT
# T-E-gated) `| {ERR_RECURSO_INVALID_GLOSA}` (T3.1 P2b, Tier-2 — recurso's origin/consistency (G2-val)
# guard: `glosa_id` absent/empty at ST_SolicitarDocumentos/ST_PrepararDossie routes to the neutral
# terminal End_RecursoGlosaInvalidaOrigem; NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated)
# `| {ERR_NIP_PROTOCOLO_INVALIDO, ERR_PROGRAMA_NO_CONSENT}` (item-9 bucket-3, Tier-2 — two more
# non-adverse G2-val origin/consent guards: nip's blank `protocolo_ans` routes to
# End_NipProtocoloInvalido, programa's failed consent chokepoint routes to End_SemConsentimento;
# neither is a `*_NOT_HUMAN` guard, so neither is T-E-gated)
# `| {ERR_CRED_INVALID_PRESTADOR}` (item-9 bucket-3 Class-C, Tier-2 — cred's G2-val origin
# guard: a blank/non-string `prestador_id` at ST_VerifyCredentials routes via
# BE_PrestadorInvalido to the neutral terminal End_CredPrestadorInvalido ("fail-safe, nao
# adverso" per the BPMN's own documentation); NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated).


#: Codigos que ja' cumpriram a habilitacao T-E — espelha `TE_ENABLED_CODES` do gate do CI.
#:
#: `ERR_AUTH_DENIAL_INCOMPLETE` habilitado em 25/08/2026: o T-E (recusa auditada) aterrissou em
#: 24/07 e o desfecho roteado continua emitindo linha de auditoria. Antes disso a negativa sem
#: fundamentacao parava como incident — nada transmitido, mas sem volta a mesa do auditor.
#:
#: NAO CONFUNDA com "nao precisa ser auditado": `harness.is_guard_refusal_code` continua
#: reconhecendo este codigo e continua auditando a recusa. Ver o docstring de `_is_te_gated`.
_TE_ENABLED_CODES: frozenset[str] = frozenset({"ERR_AUTH_DENIAL_INCOMPLETE"})


def _is_te_gated(code: str) -> bool:
    """True iff `code` AINDA esta' hard-gated em T-E (ADR-0030 §4).

    The `*_NOT_HUMAN` guard family (matched by suffix) plus the denial-block codes, MENOS os que
    ja' foram habilitados (`_TE_ENABLED_CODES`). Mirrors the boundary-proof gate's `is_te_gated`,
    kept a tiny local predicate so the runtime carries no import dependency on the CI script.

    A relacao com o detector de auditoria do harness e' CONTENCAO, nao igualdade: um codigo
    habilitado sai DESTE predicado e permanece no de auditoria. Eram iguais so' enquanto nada
    estava habilitado.
    """
    if code in _TE_ENABLED_CODES:
        return False
    return code.endswith("_NOT_HUMAN") or code == "ERR_AUTH_DENIAL_INCOMPLETE"


#: Every gate-proven (consumption-covered) code raised by a worker that exposes an allowlist
#: constant. `AUTH_BPMN_ERROR_ALLOWLIST` is unioned in DELIBERATELY so the T-E filter below has
#: something to act on: `ERR_AUTH_DENIAL_INCOMPLETE` is proven yet filtered OUT — if a future edit
#: dropped the T-E gate, the denial-block code would leak into production and the unit test
#: (`tests/unit/runtime/test_worker_runtime_bpmn_error_allowlist.py`) fails. `LGPD_BPMN_ERROR_
#: ALLOWLIST` contributes `ERR_DSR_IDENTITY_UNVERIFIED` (T2.8) — a NON-adverse technical fail-safe
#: (mechanically-unverifiable titular -> End_IdentidadeInverificavel, a neutral terminal), so it is
#: NOT T-E-gated and DOES land in Tier-0. `RECURSO_BPMN_ERROR_ALLOWLIST` contributes
#: `ERR_RECURSO_INVALID_GLOSA` (T3.1 P2b) — a NON-adverse G2-val origin/consistency guard
#: (glosa_id absent/empty -> End_RecursoGlosaInvalidaOrigem, a neutral terminal), also NOT
#: T-E-gated, also lands directly (Tier-2, ADR-0030 migration plan). `ESCALATION_BPMN_ERROR_ALLOWLIST`
#: contributes `ERR_ESC_NOTIFY_FAILED` (t8-escalation-boundary, ADR-0030 Tier-1) — a NON-adverse
#: technical fail-safe (G2-fs): a notify-channel publish failure routes via BE_FalhaNotificacao/
#: BE_NotifFallbackFailed/BE_NotifSupervisorFailed to the supervisor fallback + the mandatory HITL
#: user task (never a silent drop, never a stalling incident), so it is NOT T-E-gated and lands
#: directly (Tier-1). `NIP_BPMN_ERROR_ALLOWLIST` contributes `ERR_NIP_PROTOCOLO_INVALIDO` and
#: `PROGRAMA_BPMN_ERROR_ALLOWLIST` contributes `ERR_PROGRAMA_NO_CONSENT` (item-9 bucket-3, ADR-0030
#: Tier-2) — both NON-adverse G2-val origin/consent guards routing to neutral terminals
#: (End_NipProtocoloInvalido / End_SemConsentimento), NOT `*_NOT_HUMAN`, so NOT T-E-gated; both land
#: directly. `CRED_BPMN_ERROR_ALLOWLIST` contributes `ERR_CRED_INVALID_PRESTADOR` (item-9
#: bucket-3 Class-C, Tier-2) — cred's NON-adverse G2-val origin guard (blank/non-string
#: `prestador_id` -> End_CredPrestadorInvalido, a neutral "fail-safe, nao adverso" terminal),
#: NOT `*_NOT_HUMAN`, lands directly; the constant deliberately EXCLUDES cred's two adverse
#: `*_NOT_HUMAN` guard codes, which stay T-E-deferred. `ERR_CANCEL_MANTER_NOT_HUMAN` is
#: gate-proven too but its worker exposes no constant (nothing is enabled for it at any tier
#: until T-E), so there is nothing to import. `ANS_SUBMIT_BPMN_ERROR_ALLOWLIST` contributes
#: `ERR_ANS_PROTOCOLO_NACK` and `ERR_ANS_DATASET_INCOMPLETO` (t2-ans-submit, the ANS family's
#: ADR-0030 migration) — both NON-adverse and routed to human remediation rather than to any
#: adverse terminal: a NACK enters `SUB_RetryEnvio` (bounded retry) and, if exhausted, reaches
#: `UT_TratarNack`; an origin-side assembly failure reaches `UT_CorrigirPendenciaEnvio` and the
#: process re-evaluates. Neither denies anything to a beneficiario/prestador (this process is
#: operadora -> regulador and has no adverse output at all), neither is `*_NOT_HUMAN`, so neither is
#: T-E-gated and both land directly. `ERR_ANS_RETRY_ESGOTADO` is deliberately NOT here: it is thrown
#: by the MODEL (`End_RetryEsgotado` inside `SUB_RetryEnvio`), not by a worker — see the note in
#: `tools/workers/ans_submit.py`. `PAGTO_BPMN_ERROR_ALLOWLIST` contributes `ERR_PAGTO_ORDEM_INVALIDA`
#: (WP-ADR-0030-COMPLETION, D3-01) — pagto's G2-val origin/consistency guard (blank/absent
#: `ordem_pagamento_id` at `validate_pagto`) routed via `BE_PagtoOrdemInvalida` to the neutral
#: terminal `End_PagtoOrdemInvalida`; NOT `*_NOT_HUMAN`, so NOT T-E-gated, lands directly.
#: `REEMBOLSO_BPMN_ERROR_ALLOWLIST` contributes `ERR_REEMBOLSO_INVALID_PROTOCOLO`
#: (WP-ADR-0030-COMPLETION, D3-01) — reembolso's G2-val origin/consistency guard (blank/absent
#: `protocolo_reembolso` at `check_coverage`) routed via `BE_ReembolsoProtocoloInvalido` to the
#: neutral terminal `End_ReembolsoProtocoloInvalido`; also NOT `*_NOT_HUMAN`, also lands directly.
#: Both were previously dead models (spec declared the boundary, no worker raised the code) that
#: this work package closed at the root by moving the raise off the coded
#: `PagtoError`/`ReembolsoProtocoloInvalidoError` and onto `WorkerBpmnError` at the exact
#: boundary-carrying task each code is declared on.
_GATE_PROVEN_BPMN_ERROR_CODES: frozenset[str] = (
    ANS_SUBMIT_BPMN_ERROR_ALLOWLIST
    | AUTH_BPMN_ERROR_ALLOWLIST
    | CRED_BPMN_ERROR_ALLOWLIST
    | ESCALATION_BPMN_ERROR_ALLOWLIST
    | EVENTS_BPMN_ERROR_ALLOWLIST
    | LGPD_BPMN_ERROR_ALLOWLIST
    | NIP_BPMN_ERROR_ALLOWLIST
    | PAGTO_BPMN_ERROR_ALLOWLIST
    | PROGRAMA_BPMN_ERROR_ALLOWLIST
    | RECURSO_BPMN_ERROR_ALLOWLIST
    | REEMBOLSO_BPMN_ERROR_ALLOWLIST
)

#: The production allowlist wired into the harness: gate-proven codes MINUS the T-E-gated
#: business-outcome codes. Resolves to `{ERR_EVENT_PUBLISH_FAILED, ERR_DSR_IDENTITY_UNVERIFIED,
#: ERR_RECURSO_INVALID_GLOSA, ERR_ESC_NOTIFY_FAILED, ERR_NIP_PROTOCOLO_INVALIDO,
#: ERR_PROGRAMA_NO_CONSENT, ERR_CRED_INVALID_PRESTADOR, ERR_ANS_PROTOCOLO_NACK,
#: ERR_ANS_DATASET_INCOMPLETO, ERR_AUTH_DENIAL_INCOMPLETE, ERR_PAGTO_ORDEM_INVALIDA,
#: ERR_REEMBOLSO_INVALID_PROTOCOLO}` today (Tier-0 pair + T3.1 P2b's Tier-2 addition +
#: t8-escalation-boundary's Tier-1 addition + item-9 bucket-3's three Tier-2 G2-val origin/consent
#: guards + t2-ans-submit's two + the T-E-enabled denial-block code +
#: WP-ADR-0030-COMPLETION's two Tier-2 G2-val origin/consistency guards).
PRODUCTION_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset(
    code for code in _GATE_PROVEN_BPMN_ERROR_CODES if not _is_te_gated(code)
)


def register_default_workers(
    harness: WorkerHarness,
    *,
    dmn: GatedDmnTransport | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
    tenant_id: str = "",
    kafka: AioKafkaEventsProducer | None = None,
    dossier_dispatcher: DelegationDispatcher | None = None,
) -> None:
    """Register every worker this build serves. The daemon's ONE bootstrap call (STEP B).

    `tenant_id` (T2.6-EB3 part 3 seam) is threaded ONLY into `ans_cron.trigger_submissions`
    (`register_ans_cron_workers`'s own `**seams` catch-all) — the daemon's own
    `settings.tenant_id` identity, since SP-OP-ANS-CRON-001's TimerStartEvent-triggered
    processes carry no per-instance tenant the way case-driven processes do. Every other
    module ignores this new seam key (the existing `**seams` catch-all every
    `register_<domain>_workers` already accepts).

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

    `kafka` (a `harness.KafkaPublisher`, T4 producer-leg wiring) is the domain-event/notifications
    egress seam threaded into every `register_<domain>_workers(harness, kafka, **seams)` bootstrap
    (the pre-existing `kafka: KafkaPublisher | None = None` parameter every one of the 17 modules
    already declares — T1.2/ADR-0026 — but which no composition root ever populated with a real
    producer). Absent (`None`, the topic-probe default) preserves the pre-existing `kafka=None`
    behavior byte-for-byte (`events.py`'s own documented decision: loud log, `event_published=
    False`, never a fabricated success, never a crash). In the live daemon it is an
    `AioKafkaEventsProducer` (`platform/integrations/events_kafka_producer.py`) — real, best-effort
    for the domain-completed-event/notifications topics (see that module's docstring for the
    fail-safe discipline and the CONTAS/FRAUDE-completed -> notifications-bridge mirror it applies).

    `dossier_dispatcher` (dossier-A2A seam, DL-0033 real wiring / DL-0037) is the
    `DelegationDispatcher` assembled by `build_dossier_delegation_dispatcher` at bring-up,
    threaded into the THREE RAW async dossier handlers (`credenciamento.prepare_dossier` ->
    Carolina; `adequacao.prepare_remediation_dossier` -> Andre; `pagto.prepare_approval_dossier`
    -> Andre) via the same `**seams` catch-all. Absent (`None`, the topic-probe default and the
    DEGRADED-runtime posture — no signing key / no DATABASE_URL) the topics still register and
    all three handlers fail-neutral with a disclosed gap marker; the human User Tasks always
    still open.

    Idempotent (`WorkerHarness.register_worker` replaces on re-registration, same topic).
    """
    register_all_workers(
        harness,
        dmn=dmn,
        engine=engine,
        audit_sink=audit_sink,
        tenant_id=tenant_id,
        kafka=kafka,
        dossier_dispatcher=dossier_dispatcher,
    )


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
    # AF-13: set by `run()` STEP 0, read by the `observability_configured` readiness check. See
    # the identically-shaped field on `agent_runtime.service.AgentState`.
    observability: ObservabilityStatus | None = None
    transport: CibSevenWorkerTransport | None = None
    dmn_transport: GatedDmnTransport | None = None
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
    # T4 producer-leg seam: real `KafkaPublisher` for `operadora.events.publish`/`lgpd.py`'s
    # notification handlers. Construction is PURE (no network — mirrors `dmn_transport`/
    # `engine_transport` above: `AioKafkaEventsProducer` connects LAZILY on its first `.publish()`
    # call, bounded by its own internal connect/send timeouts). `None` only if construction itself
    # raised (e.g. bad settings) -> every worker then sees `kafka=None`, the PRE-EXISTING
    # documented behavior (`events.py` module docstring), never a crash.
    kafka_publisher: AioKafkaEventsProducer | None = None
    # Dossier-A2A seam (DL-0033 real wiring / DL-0037): the worker->Carolina/Andre delegation
    # dispatcher assembled by `build_dossier_delegation_dispatcher` at bring-up. `None` = DEGRADED
    # (no signing key in non-local mode / missing DATABASE_URL / assembly failure): the daemon
    # still RUNS and serves every topic; the three dossier workers (cred/adequacao/PAGTO)
    # fail-neutral with a disclosed
    # gap marker and `dossier_delegation_ready` reports the degradation LOUDLY (never gates
    # /readyz — the dossier "instrui, nao decide", so its absence must not stop the human UTs).
    dossier_dispatcher: DelegationDispatcher | None = None
    dossier_dispatcher_detail: str = "not assembled (bring-up has not run)"
    # CC-03/AND-03: the PER-AGENT gated FHIR readers the dossier edges' target graphs need
    # (`{"carolina": GatedFhirReader, "andre": GatedFhirReader}`). Built at bring-up through the
    # ONE sanctioned constructor (`tool_registry.build_agent_fhir_seam`) — never a raw
    # `FhirServerReader`/httpx here, which is what the effect-chokepoint fence §8.1/§8.2 forbids
    # of a composition root. An agent MISSING from this map is the honest degraded posture: its
    # graph then emits its disclosed gap note ("leitor de resumo/FHIR nao configurado"), and the
    # human User Tasks still open. Never fail-open, never a fabricated reader.
    dossier_fhir_seams: dict[str, GatedFhirReader] = field(default_factory=dict)
    dossier_fhir_detail: str = "not built (bring-up has not run)"
    # ONDA 1 §5.5 / I-11: the RUNTIME half of inevitability for the worker root's `dmn=`/`engine=`
    # seams. Snapshotted at bring-up so `/readyz` stays cheap. RED, never fatal: this daemon's
    # fail-closed gate for effect traffic is `audit_sink_ready`, and an ungated-but-inert seam
    # must not be a second reason to stop serving ~110 topics while nothing enforces.
    effect_seams_gated: bool = False
    effect_seams_detail: str = "not evaluated (bring-up has not run)"
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


def _ensure_observability(state: WorkerState) -> ObservabilityStatus:
    """Guarantee STEP 0 ran, once (AF-13). Same contract as `gateway.service._ensure_observability`.

    `run()` calls it FIRST, before this daemon's own first log line; `_bring_up_dependencies` calls
    it again so the invariant "a brought-up daemon has observability configured" holds for the
    other way bring-up is reached (a test driving it directly) — without calling
    `setup_observability` twice in production, which OTel would refuse and warn about.
    """
    if state.observability is None:
        state.observability = bootstrap_observability(
            service_name=f"maezo-worker-runtime-{state.settings.worker_id}",
            otlp_endpoint=state.settings.otel_exporter_otlp_endpoint,
        )
    return state.observability


def build_readiness_checks(state: WorkerState) -> list[Callable[[], Awaitable[CheckResult]]]:
    """Assemble the NAMED readiness checks the health app (platform/health.py) runs concurrently
    on every `/readyz`. Every check is defensive — never raises, always returns a CheckResult."""

    async def observability_configured(_state: WorkerState = state) -> CheckResult:
        # AF-13, same contract as `agent_runtime`'s check of the same name: RED only when the
        # bootstrap RAISED (a real misconfiguration); an absent OTLP endpoint is the documented
        # dev no-op and stays healthy with a detail that says no span leaves this process.
        status = _state.observability
        if status is None:
            return CheckResult(
                name="observability_configured",
                healthy=False,
                detail="observability bootstrap has not run (run() has not reached STEP 0)",
            )
        return CheckResult(name="observability_configured", healthy=status.configured, detail=status.detail)

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
        # T4 producer-leg wiring: a real `AioKafkaEventsProducer` is now constructed at bring-up
        # (bounded, non-fatal — see `_bring_up_dependencies`). `events.py`'s generic
        # `operadora.events.publish` worker DOES call `kafka.publish` (unlike the stale comment
        # this replaced claimed) — but a Kafka outage must NEVER fail this readiness check, since
        # no BPMN gateway routes on any publish outcome (events.py's own documented invariant) and
        # the producer itself is best-effort for the topics that matter (module docstring,
        # `events_kafka_producer.py`). Reports the boot-time connectivity outcome for OPERATOR
        # visibility only — never gates `/readyz` (always healthy=True), mirroring the pre-existing
        # "kafka=None is not a readiness failure" posture this check has always held.
        detail = (
            "kafka producer constructed (connects lazily on first publish; a broker outage is "
            "best-effort/audited for the mirrored topics, never a readiness failure — see "
            "events_kafka_producer.py)"
            if _state.kafka_publisher is not None
            else "kafka producer NOT constructed (construction failed at boot) — workers fall back "
            "to kafka=None (event_published=False, never a crash, ADR-0026 pre-existing behavior); "
            "not a readiness failure"
        )
        return CheckResult(name="kafka_ready", healthy=True, detail=detail)

    async def dossier_delegation_ready(_state: WorkerState = state) -> CheckResult:
        # DL-0037 DEGRADATION POSTURE: reports whether the dossier A2A dispatcher (worker ->
        # Carolina/Andre) assembled — LOUD operator visibility, but NEVER gates `/readyz`
        # (always healthy=True, mirroring `kafka_ready`): the dossier only INSTRUCTS the human
        # User Tasks ("instrui, nao decide"); when the dispatcher is absent the three dossier
        # workers (cred/adequacao/PAGTO — the PAYMENT-approval dossier included, so the operator
        # readiness DETAIL discloses payment-dossier degradation too) return the disclosed-gap
        # marker and the UTs still open, so a missing signing
        # key / degraded assembly must degrade the DOSSIER, never the whole daemon.
        ready = _state.dossier_dispatcher is not None
        detail = (
            "dossier_delegation_ready=true — worker->Carolina/Andre dispatcher assembled "
            "(cred.prepare_dossier / adequacao.prepare_remediation_dossier / "
            "pagto.prepare_approval_dossier delegate for real)"
            if ready
            else (
                "dossier_delegation_ready=false — dossier A2A dispatcher NOT assembled "
                f"({_state.dossier_dispatcher_detail}); the three dossier workers "
                "(cred/adequacao/PAGTO) return "
                "{'dossier_prepared': False, 'dossier_gap': ...} and the human User Tasks "
                "still open (DL-0037 fail-neutral-with-disclosed-gap); not a readiness failure"
            )
        )
        return CheckResult(name="dossier_delegation_ready", healthy=True, detail=detail)

    async def dossier_fhir_ready(_state: WorkerState = state) -> CheckResult:
        # CC-03/AND-03 DEGRADATION POSTURE, mirroring `dossier_delegation_ready` (and, like it,
        # ALWAYS healthy=True): publishes WHICH dossier agents got a gated FHIR reader at
        # bring-up, and — the point — which did NOT. Without this check `dossier_fhir_detail`
        # was write-only: an empty `FHIR_BASE_URL` (or a per-agent build failure) left every
        # A2A dossier enriching from worker facts alone while `effect_seams_gated` stayed GREEN
        # (nothing ungated exists when nothing was built), so the operator had no signal at all.
        # NEVER gates `/readyz`: the dossier "instrui, nao decide" — a missing reader must
        # degrade the DOSSIER's enrichment (both graphs emit their disclosed gap note and the
        # human User Tasks still open), never stop a daemon that serves ~110 topics.
        return CheckResult(name="dossier_fhir_ready", healthy=True, detail=_state.dossier_fhir_detail)

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

    async def effect_seams_gated_check(_state: WorkerState = state) -> CheckResult:
        # ONDA 1 §5.5's boot assertion for root (e). Asserts the `dmn=`/`engine=` seams this
        # daemon threads into every worker are gated instances — the runtime complement to the CI
        # fence (I-11). Non-vacuous by construction (`tool_registry.effect_seams_gated`): a dep map
        # with no effect seam reports unhealthy rather than trivially green.
        return CheckResult(
            name="effect_seams_gated",
            healthy=_state.effect_seams_gated,
            detail=_state.effect_seams_detail,
        )

    return [
        observability_configured,
        engine_reachable,
        workers_registered,
        harness_running,
        kafka_ready,
        effect_seams_gated_check,
        dossier_delegation_ready,
        dossier_fhir_ready,
        audit_sink_ready,
    ]


# --- STEP B: dependency bring-up (bounded, non-fatal) -------------------------------------------


@lru_cache(maxsize=1)
def _worker_seam_cached(tenant: str) -> SeamContext:
    """ONE `SeamContext` per tenant for this daemon's seams.

    Cached because `build_worker_seam_context` builds a PEP (`build_pep` parses `L0-core.yaml` +
    `_hard_frozen.yaml` + the tenant overlay from disk), and the `dmn=` and `engine=` blocks below
    are separate try/except islands that would otherwise each pay for it. One context, two seams,
    same closure-bound principal.
    """
    return build_worker_seam_context(tenant=tenant)


def _worker_seam(settings: WorkerRuntimeSettings) -> SeamContext:
    return _worker_seam_cached(settings.tenant_id)


def _engine_credential(settings: WorkerRuntimeSettings) -> str | None:
    """O token do motor, obtido pela visao de agente do `CredentialVault` (AF-14 / ADR-0005 #3).

    Antes desta mudanca os dois seams gated abaixo liam `settings.cibseven_auth_token_value()`
    direto: nenhuma tabela de particao governava a credencial e o mecanismo #3 da ADR-0005 nao
    tinha raiz de composicao alguma (`CredentialVault` so' aparecia no re-export do
    `gateway/__init__.py`). Agora a credencial passa pelo cofre, e uma credencial humano-restrita
    (`HUMAN_CREDENTIAL_FIELDS`: NEGATIVA/FRAUDE) NAO consegue chegar aqui — a construcao LEVANTA
    `CredentialSeparationError`.

    TAMBEM O TRANSPORTE DE TAREFA EXTERNA, que nao e' seam gated (`CibSevenWorkerTransport`, o
    primeiro bloco de `_bring_up_dependencies`): ele nao decide nada, mas carrega a MESMA
    credencial, e deixa-lo de fora manteria um `getattr` nao-auditado no proprio arquivo que esta
    mudanca reescreveu. Depois disso nao resta em `src/` nenhuma leitura de
    `cibseven_auth_token`/`cibseven_auth_token_value()` que chegue a um transporte do motor por
    fora do cofre.

    CHAMADA DE DENTRO DE CADA BLOCO `try` a proposito: `_bring_up_dependencies` isola cada bloco
    (falha registra + deixa a checagem vermelha, nunca propaga). Uma recusa do cofre e' portanto
    "o seam nao foi construido" -> os workers que dependem dele falham fechado depois, que e'
    exatamente a forma de falha ja documentada para uma construcao de seam que nao deu certo.
    Construir a visao duas vezes nao custa I/O: e' varredura de dicionario (I-9).
    """
    return agent_credential(build_worker_credential_view(settings=settings), "cibseven_auth_token")


async def _bring_up_dependencies(state: WorkerState) -> None:
    """Bring up the daemon's dependencies. Each block is isolated: failure logs + leaves the
    corresponding check unhealthy, but NEVER propagates (liveness must stay up)."""
    _ensure_observability(state)  # AF-13 — no-op when `run()` already did it at STEP 0.
    settings = state.settings

    try:
        # AF-14: o transporte de tarefa externa NAO e' um seam gated (ele nao decide nada), mas
        # carrega a MESMA credencial do motor, e uma credencial que chega a um transporte por
        # `getattr` que tabela nenhuma governa e' exatamente o que o mecanismo #3 da ADR-0005
        # existe para nao permitir. Passa pelo cofre como os dois seams gated abaixo; o valor
        # entregue e' identico (`AGENT_CREDENTIAL_FIELDS['cibseven_auth_token']`), e uma
        # composicao que vazaria credencial humano-restrita passa a deixar `engine_reachable`
        # vermelho em vez de subir o transporte.
        state.transport = CibSevenWorkerTransport(
            settings.cibseven_base_url,
            auth_token=_engine_credential(settings),
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
        # ONDA 1 §5.5, root (e): resolved through the ONE sanctioned constructor and GATED
        # (`avaliacao_dmn`, C0). Construction stays pure; the gate is dict lookups (I-9).
        state.dmn_transport = build_dmn_seam(
            seam=_worker_seam(settings),
            base_url=settings.cibseven_base_url,
            auth_token=_engine_credential(settings),
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
        # ONDA 1 §5.5 / adversary A-2: this is the SECOND engine leg — the residual ADR-0001
        # path `harness.py` does NOT cover, which a new worker taking `engine=` could reach
        # un-fenced. `FreshClientCibSevenTransport` becomes the INNER of the gated decorator,
        # exactly as §2's defeat clause specifies; its allowlist entry in the start-process fence
        # (`scripts/ci/check_start_process_fence.py:81`) is untouched.
        state.engine_transport = build_cibseven_seam(
            seam=_worker_seam(settings),
            base_url=settings.cibseven_base_url,
            auth_token=_engine_credential(settings),
            timeout=settings.client_timeout_s,
            fresh_client=True,
        )
    except Exception:  # noqa: BLE001 — construction failure: engine-seam workers fail closed later.
        logger.error("engine_transport_build_failed", exc_info=True)

    # CC-03/AND-03: the dossier edges' PER-AGENT FHIR read seams. Before this, STEP B built NO
    # FHIR seam at all and `build_dossier_delegation_dispatcher` took no `fhir=` — so every
    # dossier this daemon produced over the A2A path took the DEGRADED branch of both target
    # graphs, permanently and silently. Construction is PURE (`mcp_fhir.FhirServer` opens a fresh
    # `httpx.AsyncClient` per call, so nothing is held and nothing is loop-bound) and goes through
    # the ONE sanctioned constructor, which returns the GATED wrapper — this root never names a
    # raw adapter (effect-chokepoint fence §8.1/§8.2/§8.4).
    #
    # PER AGENT, NEVER SHARED: `GatedFhirReader` closes over its `SeamContext.principal`, and
    # `leitura_phi_clinica` (C2) is decided per principal — one instance for both would decide and
    # record Andre's PHI read under Carolina's declared-capability record.
    #
    # runtime_mode/capabilities: the seam is built in EVERY mode, because refusing is not this
    # layer's job — the PEP is (`gateway/seams/_base.py::gate`), per tenant and per principal,
    # from `spec/policies/autonomy`. What this root owes is honesty about ABSENCE: no
    # `FHIR_BASE_URL` (empty string) => NO reader for anyone, logged loudly, and both graphs emit
    # their disclosed gap note. Never a reader aimed at a placeholder host.
    if settings.fhir_base_url:
        for agent_id in _DOSSIER_FHIR_AGENT_IDS:
            try:
                seam = build_agent_fhir_seam(settings=settings, agent_id=agent_id)
            except Exception:  # one agent's seam failing must not sink bring-up.
                logger.error("dossier_fhir_seam_build_failed", agent=agent_id, exc_info=True)
                continue
            if seam is None:
                # The agent declares no FHIR adapter in `_FHIR_ADAPTER_BY_AGENT` — a REGISTRY
                # fact, not a runtime failure. Loud anyway: this daemon believes it needs one.
                logger.error("dossier_fhir_seam_absent_for_agent", agent=agent_id)
                continue
            state.dossier_fhir_seams[agent_id] = seam
    else:
        logger.error(
            "dossier_fhir_unconfigured_fhir_base_url_empty",
            tenant=settings.tenant_id,
            detail="FHIR_BASE_URL is empty — the dossier graphs will emit their disclosed "
            "gap note instead of enriching (degraded, never fail-open)",
        )
    missing_fhir = [a for a in _DOSSIER_FHIR_AGENT_IDS if a not in state.dossier_fhir_seams]
    state.dossier_fhir_detail = (
        f"gated readers for {sorted(state.dossier_fhir_seams)}"
        if not missing_fhir
        else f"DEGRADED — no gated FHIR reader for {missing_fhir}"
    )

    # ONDA 1 §5.5: snapshot the gatedness of the seams this daemon threads onward. The two worker
    # seams (`dmn`/`cibseven`, this daemon's own principal) plus, since CC-03/AND-03, each dossier
    # agent's `fhir` reader — probed one at a time because `effect_seams_gated` takes ONE value
    # per seam key and these are per-principal instances. Isolated; never blocks bring-up.
    try:
        gated, detail = effect_seams_gated({"dmn": state.dmn_transport, "cibseven": state.engine_transport})
        for agent_id, seam in sorted(state.dossier_fhir_seams.items()):
            agent_gated, agent_detail = effect_seams_gated({"fhir": seam})
            detail = f"{detail}; {agent_id}.{agent_detail}"
            gated = gated and agent_gated
        state.effect_seams_gated, state.effect_seams_detail = gated, detail
        if not state.effect_seams_gated:
            logger.error("effect_seams_not_gated", detail=state.effect_seams_detail)
    except Exception as exc:  # noqa: BLE001 — probe failure leaves the check red, nothing else.
        state.effect_seams_detail = f"{type(exc).__name__}: {exc}"
        logger.error("effect_seams_gated_probe_failed", exc_info=True)

    try:
        # T4 producer-leg seam: PURE construction (no network — see `WorkerState.kafka_publisher`
        # docstring), so a bad/unreachable KAFKA_BOOTSTRAP_SERVERS never blocks bring-up; the first
        # actual `.publish()` call connects lazily and is itself best-effort for the topics that
        # matter (`events_kafka_producer.py`).
        state.kafka_publisher = AioKafkaEventsProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
    except Exception:  # noqa: BLE001 — construction failure: workers fall back to kafka=None.
        logger.error("kafka_publisher_build_failed", exc_info=True)

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
        # Dossier-A2A dispatcher (DL-0033 real wiring): worker->Carolina/Andre delegation edges,
        # assembled with the SAME seams built above (pooled audit sink on THIS loop — raw async
        # handlers run on it; fresh-per-call dmn/engine transports are loop-safe anywhere).
        # DEGRADATION POSTURE (DL-0037): assembly failure — no signing key in non-local mode
        # (`_require_signer_or_fail_closed` raises), missing DATABASE_URL/deps, card/registry
        # failure — is caught HERE: LOUD error, `dossier_dispatcher` stays None, the daemon RUNS,
        # `dossier_delegation_ready` reports the degradation, and the three dossier workers
        # (cred/adequacao/PAGTO) return
        # the disclosed-gap marker (the human UTs still open). Unsigned Cards NEVER compose in
        # non-local mode — degradation, not downgrade.
        if (
            state.dmn_transport is not None
            and state.engine_transport is not None
            and state.audit_sink is not None
        ):
            from maezo.runtime.agent_runtime.a2a_composition import (
                build_dossier_delegation_dispatcher,
                worker_runtime_mode_from_env,
            )

            state.dossier_dispatcher = build_dossier_delegation_dispatcher(
                tenant=settings.tenant_id,
                runtime_mode=worker_runtime_mode_from_env(),
                dmn=state.dmn_transport,
                cibseven=state.engine_transport,
                audit_sink=state.audit_sink,
                database_url=settings.database_url,
                # CC-03/AND-03: the per-agent GATED readers built above. `or None` keeps the
                # "nothing configured" case indistinguishable from the pre-existing degraded
                # posture at the root's own boundary.
                fhir=state.dossier_fhir_seams or None,
                # BLOCKED(external WB.4): there is NO concrete `PopulationFeatureClient` in
                # `src/` — `andre/graph.py` defines the Protocol only, `gateway/seams/population.
                # py` ships unwired, and `tool_registry.build_agent_seams` omits the key on
                # purpose. Passed EXPLICITLY as None so the gap is declared here rather than
                # inferred from a default; Andre's graph turns it into a disclosed gap note.
                population=None,
                # Delegation facts ride the no-op FactProducer default (mirrors the auth edge):
                # the DURABLE T-F record is the injected audit_sink, not a Kafka facts mirror, so
                # the real events producer is deliberately NOT threaded here.
            )
            state.dossier_dispatcher_detail = "assembled"
        else:
            state.dossier_dispatcher_detail = (
                "required seams unavailable (dmn/engine transport or durable audit sink missing "
                "— DATABASE_URL unset or construction failed)"
            )
            logger.error(
                "dossier_delegation_dispatcher_not_assembled",
                tenant=settings.tenant_id,
                detail=state.dossier_dispatcher_detail,
            )
    except Exception as exc:  # noqa: BLE001 — DL-0037: degrade the dossier, never the daemon.
        state.dossier_dispatcher_detail = f"assembly failed: {type(exc).__name__}"
        logger.error("dossier_delegation_dispatcher_build_failed", tenant=settings.tenant_id, exc_info=True)

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
                tenant_id=settings.tenant_id,
                kafka=state.kafka_publisher,
                # Dossier-A2A seam (DL-0033/DL-0037): None when degraded — the raw dossier
                # handlers then gap-mark instead of delegating; every topic still registers.
                dossier_dispatcher=state.dossier_dispatcher,
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
        dossier_delegation_ready=state.dossier_dispatcher is not None,
        workers_registered=(len(state.harness.registered_topics) if state.harness is not None else 0),
        harness_running=state.harness_running(),
    )


# --- run() — orchestrates STEP A..E -------------------------------------------------------------


async def run(settings: WorkerRuntimeSettings) -> None:
    """Run the worker-runtime until SIGTERM/SIGINT. See the module docstring for STEP 0/A..E."""
    state = WorkerState(settings=settings)

    # STEP 0 (AF-13): structlog + OTel BEFORE this daemon's first log line — see the module
    # docstring. Never propagates; a failure lands on `observability_configured`.
    observability = _ensure_observability(state)

    logger.info(
        "worker_runtime_starting",
        tenant=settings.tenant_id,
        worker_id=settings.worker_id,
        health_port=settings.health_port,
        observability=observability.detail,
    )
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
    if state.kafka_publisher is not None:
        with contextlib.suppress(Exception):
            await state.kafka_publisher.close()

    # The health server's should_exit was already set in the drain trigger; make sure it's set
    # regardless of which path got us here, then wait for it to actually stop.
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    logger.info("worker_runtime_stopped", tenant=settings.tenant_id, worker_id=settings.worker_id)
