"""SP-OP-REEMBOLSO-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2
(V2-COMPLETION-PLAN §3, 13-family process-suite port). reembolso.py is one of ADR-0026's
6 typed-I/O modules with dict-boundary entry functions (mirrors cancel.py's STRUCTURE), and it
ALSO imports `CeilingResolver` (mirrors auth.py's D-07 ceiling-gap REASONING STYLE — confirmed
`grep -rl "CeilingResolver" src/maezo/tools/workers/*.py` returns exactly auth.py/pagto.py/
reembolso.py).

Implementa o test-spec do W5 contra o engine real (ADR-0011: sem mock de engine). Cada teste:

1. inicia a instancia via REST com business key `REEMB-amh-{protocolo_reembolso}`;
2. drena as external tasks com o `reembolso_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que negativa/reducao passa pela UT humana).

Dados sinteticos obvios: protocolo `REEMB-TESTE-NNNN`, beneficiario pseudonimizado
`benef-TESTE-001`, matricula `MAT-TESTE-001`, tenant `amh`. Business key
`REEMB-amh-{protocolo}`. Process key: SP-OP-REEMBOLSO-001 (exato — nao alterar). Valores em
CENTAVOS INTEIROS (ex.: valor_solicitado_cents=12000 = R$120,00).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.reembolso`/`harness`; `FakeKafkaPublisher` lives in
    `harness.py` (T1.1).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5). Unlike auth (3 separate DMN
    files), reembolso's admissibility AND auto-approval decisions are packed into ONE file,
    `reembolso_coverage.dmn` (verified: `grep '<decision ' reembolso_coverage.dmn` returns BOTH
    `reembolso_admissibility` and `reembolso_auto_approval`) — `_DMN_COVERAGE` covers both;
    `_DMN_CALCULO`/`_DMN_SLA` are the other two files. All 4 `camunda:decisionRef`s the BPMN
    declares (admissibility/calculo/auto_approval/sla) resolve across these 3 files — no missing
    DMN (verified by reading, per Step 2).
  - donor's dict-first `make_send_reembolso_denial_handler(kafka) -> Callable[[ExternalTask],...]`
    does not exist on v2 main; v2's dict-boundary entry function is
    `send_reembolso_denial_entry(variables: dict, *, kafka=None) -> dict` (ADR-0026 §2b, mirrors
    cancel.py's own adaptation of its guard unit-test). The guard raises
    `ReembolsoDenialNotHumanError` (a `PermissionError` subclass — harness routes any
    `PermissionError` straight to an engine incident, `harness.py` `_handle`), NOT donor's
    `WorkerBpmnError(error_code=...)` (a modeled BPMN error routed to a boundary catch) — reembolso.py
    does not raise `WorkerBpmnError` anywhere (verified by reading): both
    `ReembolsoDenialNotHumanError` and `ReembolsoProtocoloInvalidoError` are plain
    `PermissionError`/`ValueError` subclasses, so the harness's OWN generic classification
    (immediate incident, `retries=0`) applies without any `bpmn_error_allowlist` entry — UNLIKE
    auth/cancel/escalation, this probe wires NO `bpmn_error_allowlist` (verified: no
    `WorkerBpmnError` call site in reembolso.py). `test_worker_send_reembolso_denial_recusa_sem_humano`
    is adapted the same way as cancel's guard test: call the entry function directly with a plain
    dict, assert on the returned dict for the success cases (v2's entry function does not itself
    call `kafka.publish` — see finding 1 below).

FINDINGS (module docstring; see PR body / evidence-ledger for full detail):

  1. Kafka-publish gap (systemic, T3.1 cross-family fact): `grep -rn "kafka\\.publish(" \\
     src/maezo/tools/workers/*.py` returns EXACTLY ONE call site (`events.py:247`, the generic
     `operadora.events.publish` handler). NONE of reembolso.py's 8 dict-boundary entry functions
     call `kafka.publish` (every one does `del kafka  # unused`) — mirrors escalation's
     NotifyTeamWorker / auth's action-worker residuals / cancel's entry-function residuals (same
     ledger row, "T3.1 (events.publish fix)"). Domain events on the GENERIC publish path
     (`agents.events.reembolso.{received,completed,sla_breached}`, via `register_events_workers`)
     DO work and are asserted green below; PER-WORKER notifications
     (`reembolso_probe.notifications_of_type("reembolso.issue_payment")` etc., backed by
     `FakeKafkaPublisher.published` with a `type` key) can NEVER be observed — no reembolso.py
     function ever sets one. `_REEMBOLSO_WORKER_KAFKA_GAP_REASON` covers the 2 real-worker topics
     (`issue_payment`, `send_reembolso_denial`) whose donor tests assert this.

  2. Missing workers for 3 BPMN-declared topics — FIXED (T2.5-P2B reembolso reconciliation):
     `ST_SolicitarDocumentos` (`operadora.reembolso.request_documents`), `ST_PrepararDossie`
     (`operadora.reembolso.analyze_request` — the ONLY path INTO `UT_AnaliseReembolso`,
     `Flow_Sla_Dossie`/`Flow_Dossie_UTAnalista`), and `ST_NotificarRiscoSla`
     (`operadora.reembolso.notify_sla_risk`) now have REAL registered workers in
     `register_reembolso_workers` (non-adverse; analyze_request instructs, never decides,
     ADR-0005; notify_sla_risk mirrors cancel's proven informational pattern). The former
     test-fixture-only `_gap_topic_stub` is REMOVED (it would otherwise SHADOW the real workers —
     `harness.register` is last-registration-wins). The 2 xfails that blamed the missing workers
     keep their marks (strict, NO flip without live proof — port charter) with the reason retagged
     `_REEMBOLSO_BUILT_WORKER_PENDING_LIVE_PROOF_REASON`: the workers are built, but their
     `notifications_of_type(...)` assertions still hit finding 1's systemic kafka gap (the new
     entry functions, like every other ADR-0026 dict-boundary module, never call kafka.publish),
     and the `agents.events.reembolso.pended` event `ST_SolicitarDocumentos`'s own
     `event_topic_pended` inputParameter implies is likewise not published by the worker.

  3. D-07 ceiling gap (T1.9, `CeilingResolver`) DOES GENUINELY BLOCK reembolso's auto-approval path
     — UNLIKE auth's carve-out, LIKE pagto's non-carve-out (verified by reading, per Step 2):
     `calculate_amount_entry` (topic `operadora.reembolso.calculate_amount`) is registered by
     `register_reembolso_workers` and IS part of this probe's normal drain-topics list (no donor
     fixture excludes it, unlike auth's `_ANALYZE_TOPIC` carve-out) — every test that reaches
     `BRT_Calculo -> ST_CalculateAmount -> BRT_AutoApproval` exercises the REAL `CeilingResolver`.
     `spec/policies/autonomy/L0-core.yaml` (`reembolso_auto_approval: { ... max_value_brl: 0 }`)
     AND `tenants-amh.yaml` (`reembolso_auto_approval: { params: { max_value_brl: 0 } } # D-07 em
     aberto`) both resolve the ceiling to 0 — `CeilingResolver.within_l2_ceiling` is fail-closed on
     a 0 ceiling (`ceilings.py`: `if ceiling_brl == 0: return False`), so `dentro_teto_l2` can
     NEVER compute `True` for any positive value under the CURRENT tenant policy config, and
     `End_ReembolsoAprovadoAutomatico` is genuinely UNREACHABLE. The donor's own
     `test_teto_zero_d07_bloqueia_auto_aprovacao_mesmo_com_seed_true` already asserts EXACTLY this
     fail-closed behavior (D-07 blocks auto-approval even with an upstream `dentro_teto_l2=True`
     seed) — that test is EXPECTED TO PASS unmodified (it documents the current, correct,
     fail-closed reality, not a gap). The donor's OWN workaround for exercising the positive-teto
     path, `reembolso_probe_teto_positivo` (constructs `register_reembolso_workers(harness, kafka,
     resolver=CeilingResolver(core_path=core))`), CANNOT be ported: v2's
     `register_reembolso_workers(harness, kafka=None, **seams)` explicitly does
     `del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today`
     — there is no way to inject a resolver from the probe. `test_happy_path_aprovacao_
     automatica_l2_com_teto_positivo` is therefore marked `_REEMBOLSO_CEILING_D07_REASON` (D-07
     remains a genuine, OPEN config gap — same conclusion as auth/pagto — but here it DOES block a
     test, mirroring the task's own auth-vs-pagto contrast).

  4. Dead topic registrations — RECONCILED (T2.5-P2B): `operadora.reembolso.auto_approve_or_route`
     and `operadora.reembolso.notify_beneficiario` are DELETED from `register_reembolso_workers`
     (grep-verified: neither ever appeared as a `camunda:topic` in
     `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`; `BRT_AutoApproval` is a NATIVE
     `businessRuleTask`, `camunda:decisionRef="reembolso_auto_approval"`, resultVariable
     `auto_aprovacao`, consumed directly by `GW_AutoAprovacao`'s condition
     `${auto_aprovacao.recomendacao == 'AUTO_APROVAR'}` — the deleted Python fork was never
     invoked by this BPMN). `operadora.reembolso.publish_completed` is KEPT (documented
     registry-completeness convention shared with recurso.py; every `ST_Publish*` service task
     uses the generic `operadora.events.publish` topic) and is now INCLUDED in
     `_REEMBOLSO_WORKER_TOPICS` below so the drain list matches `register_reembolso_workers`'
     registered set exactly (the #93 inadimplencia drain-sync treatment; draining it is a no-op —
     no BPMN task is ever created on it).

  5. `notification_bridge.py`'s 5 rules (CONTAS→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED, FRAUDE→CANCEL,
     FRAUDE→INADIMPLENCIA): verified — reembolso is neither source nor target. N/A.

  6. Also worth flagging (NOT tested by the donor, so no new test added here — porting only):
     `BE_ReembolsoProtocoloInvalido` (boundary event, `errorRef=Error_ReembolsoProtocoloInvalido`,
     `errorCode=ERR_REEMBOLSO_INVALID_PROTOCOLO`) is attached to `ST_CheckCoverage`
     (`operadora.reembolso.check_coverage`), but the ONLY function that can raise
     `ReembolsoProtocoloInvalidoError` is `validate_reembolso` — wired to `check_prazo_entry`
     (`operadora.reembolso.check_prazo`), a DIFFERENT service task earlier in the flow. Even were
     this rewired, the exception is a plain `ValueError` subclass (not `WorkerBpmnError`), so the
     boundary catch could never fire regardless — a fail-closed incident is the only reachable
     outcome today. Since the donor itself never exercises this boundary, no test is added/altered
     for it here (would be new coverage, out of port scope).
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    WorkerHarness,
)
from maezo.tools.workers.reembolso import (
    ReembolsoDenialNotHumanError,
    register_reembolso_workers,
    send_reembolso_denial_entry,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN_COVERAGE = _REPO / "spec/processes/dmn/reembolso_coverage.dmn"  # admissibility + auto_approval
_DMN_CALCULO = _REPO / "spec/processes/dmn/reembolso_calculo.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/reembolso_sla.dmn"

# External task topics do contrato SP-OP-REEMBOLSO-001 (fonte da verdade: BPMN).
_PUBLISH_TOPIC = "operadora.events.publish"
_CHECK_COVERAGE_TOPIC = "operadora.reembolso.check_coverage"
_CHECK_PRAZO_TOPIC = "operadora.reembolso.check_prazo"
_CALC_AMOUNT_TOPIC = "operadora.reembolso.calculate_amount"
_ISSUE_PAYMENT_TOPIC = "operadora.reembolso.issue_payment"
_DENIAL_TOPIC = "operadora.reembolso.send_reembolso_denial"

# Registered by register_reembolso_workers SINCE the T2.5-P2B reconciliation (finding 2's
# missing workers were implemented in src; the former test-fixture `_gap_topic_stub` is gone):
_REQ_DOCS_TOPIC = "operadora.reembolso.request_documents"
_ANALYZE_TOPIC = "operadora.reembolso.analyze_request"
_NOTIFY_SLA_TOPIC = "operadora.reembolso.notify_sla_risk"
# Function-derived topic KEPT by documented registry-completeness convention (finding 4,
# reconciled) — no BPMN task is ever created on it; draining it is a no-op.
_PUBLISH_COMPLETED_TOPIC = "operadora.reembolso.publish_completed"

# Topicos servidos pelos workers REAIS registrados via `register_reembolso_workers` (mais o
# publish generico compartilhado). T2.5-P2B (findings 2+4, reconciled): a lista de drain agora
# coincide EXATAMENTE com o conjunto registrado em src — 8 topicos BPMN + o publish_completed
# de convencao (tratamento #93/inadimplencia: drain sincronizado ao registro real).
_REEMBOLSO_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _CHECK_COVERAGE_TOPIC,
    _CHECK_PRAZO_TOPIC,
    _CALC_AMOUNT_TOPIC,
    _ISSUE_PAYMENT_TOPIC,
    _DENIAL_TOPIC,
    # T2.5-P2B — real workers (previously finding-2 gap stubs).
    _REQ_DOCS_TOPIC,
    _ANALYZE_TOPIC,
    _NOTIFY_SLA_TOPIC,
    # Convention topic (finding 4, reconciled) — keeps drain == registered set.
    _PUBLISH_COMPLETED_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_REEMBOLSO_RECEIVED = "agents.events.reembolso.received"
_REEMBOLSO_PENDED = "agents.events.reembolso.pended"
_REEMBOLSO_SLA_BREACHED = "agents.events.reembolso.sla_breached"
_REEMBOLSO_COMPLETED = "agents.events.reembolso.completed"

_UT_ANALISTA = "UT_AnaliseReembolso"
_UT_AUDITOR = "UT_RevisaoAuditorMedico"
_UT_COORDENACAO = "UT_CoordenacaoReembolso"
_UT_PENDENCIA = "UT_DecidirPendenciaExpirada"

_END_AUTO = "End_ReembolsoAprovadoAutomatico"
_END_ANALISTA = "End_ReembolsoAprovadoAnalista"
_END_NEGADO = "End_ReembolsoNegado"
_END_PARCIAL = "End_ReembolsoParcial"
_END_CANCELADO = "End_ReembolsoCancelado"

_END_ADVERSOS = frozenset({_END_NEGADO, _END_PARCIAL})
_UT_HUMANAS_ADVERSA = frozenset({_UT_ANALISTA, _UT_AUDITOR, _UT_COORDENACAO})

# --- xfail reasons (distinct, well-named per finding — constraint-critical) ------------------

_REEMBOLSO_WORKER_KAFKA_GAP_REASON = (
    "v2 systemic drift (finding 1, T3.1 events.publish-fix ledger row): reembolso.py's ADR-0026 "
    "dict-boundary entry functions never call kafka.publish (issue_payment_entry/"
    "send_reembolso_denial_entry/analyze_request_entry/request_documents_entry/notify_sla_risk all "
    "`del kafka  # unused`) — the donor's workers published a "
    "per-worker notification (operadora.notifications.internal, `type` key) from INSIDE the "
    "handler; v2's entry functions only RETURN output variables. reembolso_probe."
    "notifications_of_type(...) can therefore never observe issue_payment/send_reembolso_denial/"
    "analyze_request/notify_sla_risk "
    "executions, even though the worker itself runs and completes normally against the engine "
    "(the flow/end-event assertions in these tests would pass). Same systemic residual as "
    "escalation's NotifyTeamWorker / auth's action-worker residuals / cancel's entry-function "
    "residuals. Fix belongs to the Kafka-producer wiring task, not this port. "
    "LIVE-PROVEN (wave2a, CIB Seven 2.1.0): test_happy_path_aprovado_pelo_analista + "
    "test_timer_alerta_sla_nao_interruptivo were retagged here from "
    "_REEMBOLSO_BUILT_WORKER_PENDING_LIVE_PROOF_REASON after a live run confirmed they fail on "
    "notifications_of_type('reembolso.analyze_request'/'reembolso.notify_sla_risk'). "
    "RETIRED (wave-3, live-proven 22p+1xf): the dead kafka echoes were adapted to engine-side "
    "evidence (activity history / live ST_Publish* has_event fields) and the markers removed — "
    "grep-confirmed: zero pytest.mark.xfail call sites reference this constant anymore."
)

_REEMBOLSO_VALOR_CENTS_UNPLUMBED_REASON = (
    "SRC-GAP (GK-flips finding 1, wave-3): issue_payment_entry reads "
    "variables.get('valor_cents', 0) but NOTHING produces valor_cents — calculate_amount returns "
    "valor_calculado_tabela_cents, the BPMN maps valor_reembolso_aprovado_cents only on "
    "ST_IssuePaymentAuto (the parcial/analista branches have NO input mapping), and the start "
    "seeds don't include it => process_payment issues valor_cents=0 on ALL THREE payment paths. "
    "This test asserts the human's approved reduced value (8000) reaches the payment via engine "
    "history (get_history_variable) and FAILS until the value is plumbed (Class-C src fix: read "
    "valor_reembolso_aprovado_cents/valor_calculado_tabela_cents at issue_payment or add the "
    "missing BPMN input mappings). "
    "RETIRED (item-9 w7+assembly, live-proven): issue_payment_entry now resolves "
    "valor_reembolso_aprovado_cents with a fail-closed money guard "
    "(ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO on absent/float/bool/<=0 — no payment, incident) and "
    "the marker came off with the money assert passing for real; zero xfail call sites reference "
    "this constant anymore."
)

_REEMBOLSO_BUILT_WORKER_PENDING_LIVE_PROOF_REASON = (
    "BUILT, pending live-proof flip (T2.5-P2B reembolso reconciliation; finding 2 FIXED in src): "
    "request_documents/analyze_request/notify_sla_risk now have REAL registered workers in "
    "register_reembolso_workers (the former test-fixture _gap_topic_stub is removed), so the flow "
    "genuinely executes these nodes with real handlers. The marks are NOT flipped without a live "
    "engine run (port charter: no pre-emptive flips): the residual expectation is that this "
    "test's worker-emitted-Kafka assertions (notifications_of_type('reembolso.analyze_request'/"
    "'reembolso.notify_sla_risk'), or the agents.events.reembolso.pended event "
    "ST_SolicitarDocumentos's own event_topic_pended inputParameter implies) still fail on "
    "finding 1's systemic kafka gap — the new entry functions, like every ADR-0026 dict-boundary "
    "module, never call kafka.publish. The next live-validation run decides: flip if it passes, "
    "retag to _REEMBOLSO_WORKER_KAFKA_GAP_REASON if it fails there."
)

_REEMBOLSO_CEILING_D07_REASON = (
    "D-07 (finding 3, OPEN governance item, GENUINELY blocks here — unlike auth's carve-out, like "
    "pagto's non-carve-out): tenant amh's reembolso_auto_approval.max_value_brl is 0 in both "
    "spec/policies/autonomy/L0-core.yaml and tenants-amh.yaml. CeilingResolver.within_l2_ceiling "
    "(ceilings.py) is fail-closed on a 0 ceiling, and calculate_amount_entry's topic "
    "(operadora.reembolso.calculate_amount) IS part of this probe's normal drain-topics list (no "
    "carve-out) — every request's dentro_teto_l2 is genuinely recomputed False, so "
    "End_ReembolsoAprovadoAutomatico is UNREACHABLE under the current tenant policy config. The "
    "donor's own workaround (reembolso_probe_teto_positivo, injecting "
    "register_reembolso_workers(..., resolver=CeilingResolver(core_path=core))) cannot be ported: "
    "v2's register_reembolso_workers(harness, kafka=None, **seams) explicitly `del`s **seams — no "
    "resolver-injection point exists. src/** fix (either a real teto or a resolver seam) is out of "
    "scope for this port."
)

# SP-OP-REEMBOLSO-001.md:100's "produz" obligation for agents.events.reembolso.pended RESOLVED by
# t3.1-event-gap-w4-reemb-inad (T3.1 event-gap remedy B, wave 4 latent conformance) —
# ST_PublishReembolsoPended (boundary-free operadora.events.publish task between
# ST_SolicitarDocumentos and GW_AguardarDocs) now emits it. LIVE-PROVEN against a real CIB Seven
# 2.1.0 engine: activity ST_PublishReembolsoPended COMPLETED (canceled=false) in engine history on
# the pendencia instance, the token advanced to GW_AguardarDocs, and has_event matched both
# payload vars (tenant_id, protocolo_reembolso). Its strict-xfail reason constant
# (_REEMBOLSO_PENDED_PUBLISH_ADDED_REASON) is retired on that proof (precedent: recurso's own
# retired _RECURSO_PENDED_PUBLISH_ADDED_REASON).


@dataclass
class ReembolsoEngineProbe:
    """Driva os workers reais de reembolso contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    worker_id: str

    @property
    def _captured(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self.kafka.published

    def _domain_events(self) -> list[dict[str, Any]]:
        out = []
        for topic, payload, _key in self._captured:
            if topic == _NOTIFICATIONS_TOPIC:
                continue
            out.append({"topic": topic, "payload": payload})
        return out

    def events_on(self, topic: str) -> list[dict[str, Any]]:
        return [e for e in self._domain_events() if e["topic"] == topic]

    def has_event(self, topic: str, **match: Any) -> bool:
        return any(all(e["payload"].get(k) == v for k, v in match.items()) for e in self.events_on(topic))

    def notifications_of_type(self, ntype: str) -> list[dict[str, Any]]:
        return [v for (_t, v, _k) in self._captured if v.get("type") == ntype]

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(
            self.transport, self.harness, self.worker_id, _REEMBOLSO_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de reembolso da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_COVERAGE, _DMN_CALCULO, _DMN_SLA, name="SP-OP-REEMBOLSO-001-qa")


@pytest_asyncio.fixture
async def reembolso_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[ReembolsoEngineProbe]:
    """Probe que serve as external tasks com os workers reais de reembolso (T2.5-P2B: TODOS os
    8 topicos BPMN tem worker real — os gap stubs de finding 2 foram removidos).

    Resolver de teto DEFAULT (matriz real, sem seam de injecao — finding 3):
    `reembolso_auto_approval.max_value_brl=0` para amh (D-07 em aberto) => `calculate_amount`
    recomputa `dentro_teto_l2=False` SEMPRE (fail-closed) — nenhuma instancia auto-aprova por este
    probe. Nenhum `bpmn_error_allowlist` e necessario (PORT NOTES): reembolso.py nao lanca
    `WorkerBpmnError` em lugar nenhum — os 2 guards (`ReembolsoDenialNotHumanError`,
    `ReembolsoProtocoloInvalidoError`) sao `PermissionError`/`ValueError` puros, roteados pela
    classificacao GENERICA do harness (incidente imediato).
    """
    worker_id = f"qa-reembolso-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    register_reembolso_workers(harness, kafka)
    # T3.1 R2: generic operadora.events.publish worker (every ST_Publish* service task).
    from maezo.tools.workers.events import register_events_workers

    register_events_workers(harness, kafka)
    # T2.5-P2B: request_documents/analyze_request/notify_sla_risk are served by the REAL workers
    # register_reembolso_workers registers above — no gap stubs (a stub here would SHADOW them:
    # harness.register is last-registration-wins).
    probe = ReembolsoEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_protocolo(prefix: str = "REEMB-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_reembolso(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key REEMB-amh-{protocolo} e payload canonico.

    Fatos pre-resolvidos (cobertura_prevista, documentacao_completa, dentro_prazo,
    beneficiario_ativo, carencia_cumprida, dentro_tabela, dentro_teto_l2, requer_avaliacao_clinica)
    sao SEEDED como variaveis de start (o teste e o agente de origem). `dentro_teto_l2` e
    RECOMPUTADO pelo worker `calculate_amount` (T1.9 §2.3, finding 3) antes de
    `BRT_AutoApproval` avaliar — o seed e sobrescrito.
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        protocolo = overrides.pop("protocolo_reembolso", _unique_protocolo())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "protocolo_reembolso": protocolo,
            "numero_guia_tiss": "GUIA-TESTE-0001",
            "beneficiario_pseudo_id": "benef-TESTE-001",
            "matricula_beneficiario": "MAT-TESTE-001",
            "tipo_reembolso": "livre_escolha",
            "codigo_procedimento_tuss": "10101012",  # sintetico: consulta em consultorio
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 12000,  # R$ 120,00
            "data_atendimento": "2026-05-20",
            "data_solicitacao": "2026-05-25",
            "cid10": "Z00.0",
            "documentos_refs": "[]",
            "cobertura_prevista": True,
            "documentacao_completa": True,
            "dentro_prazo": True,
            "beneficiario_ativo": True,
            "carencia_cumprida": True,
            "dentro_tabela": True,
            "dentro_teto_l2": True,
            "requer_avaliacao_clinica": False,
            # GAP F-1: este seed de ESCOPO DE PROCESSO e' INERTE para o worker desde que
            # `ST_CalculateAmount` passou a achatar `${calculo.*}` em variaveis LOCAIS da
            # activity (que sombreiam o homonimo de processo). Mantido de proposito: e' a
            # prova viva de que um valor semeado no start NAO decide dinheiro. O numero
            # coincide com o que a DMN `reembolso_calculo` emite para `consulta` (r_consulta =
            # 12000), entao ele tambem nao mascara divergencia.
            "valor_calculado_tabela_cents": 12000,
        }
        variables.update(overrides)
        business_key = f"REEMB-amh-{protocolo}"
        return await engine.start_by_key("SP-OP-REEMBOLSO-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna os end events atingidos."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que nenhum terminal adverso existe sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _END_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS_ADVERSA
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: terminal(is) adverso(s) {adversos_atingidos} atingido(s) "
            f"para instancia {iid} SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_ADVERSA}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de negativa/reducao — violacao do L0 hard (ADR-0005)."
        )


async def _drive_to_analista(engine: EngineRest, probe: ReembolsoEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseReembolso surgir (dossie preparado pelo worker real analyze_request)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs das DMNs)
# ===========================================================================


async def test_nenhum_caminho_automatizado_nega_reembolso(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado nega/reduz reembolso.

    Varredura de todas as 256 combinacoes booleanas de cobertura_prevista/documentacao_completa/
    dentro_prazo/beneficiario_ativo/carencia_cumprida/dentro_tabela/dentro_teto_l2/
    requer_avaliacao_clinica, mais 3 pontos de valor (abaixo/igual/acima da tabela). NUNCA atinge
    End_ReembolsoNegado/End_ReembolsoParcial sem decisao humana (provado via historia).
    """
    bools = [True, False]
    checked = 0

    for cobertura, docs, prazo, ativo, carencia, tabela, teto, clinica in itertools.product(
        bools, bools, bools, bools, bools, bools, bools, bools
    ):
        inst = await start_reembolso(
            cobertura_prevista=cobertura,
            documentacao_completa=docs,
            dentro_prazo=prazo,
            beneficiario_ativo=ativo,
            carencia_cumprida=carencia,
            dentro_tabela=tabela,
            dentro_teto_l2=teto,
            requer_avaliacao_clinica=clinica,
        )
        iid = inst["id"]
        await reembolso_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _END_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO: cobertura={cobertura} docs={docs} prazo={prazo} ativo={ativo} "
            f"carencia={carencia} tabela={tabela} teto={teto} clinica={clinica} atingiu "
            f"terminal adverso {adversos} automaticamente. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 256, f"Esperava 256 combinacoes booleanas varridas; varri {checked}"

    for valor, dentro in ((6000, False), (12000, True), (20000, False)):
        inst = await start_reembolso(valor_solicitado_cents=valor, dentro_tabela=dentro)
        iid = inst["id"]
        await reembolso_probe.drain()
        ended = await engine.activity_instances_ended(iid)
        assert not (ended & _END_ADVERSOS), (
            f"L0 VIOLADO: valor={valor} dentro_tabela={dentro} atingiu terminal adverso. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)


async def test_inelegibilidade_roteia_para_humano(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """cobertura_prevista=false (e variantes) => reembolso_admissibility -> ANALISE_HUMANA.

    Nunca atinge um fim de negativa; inelegibilidade aparente nunca vira End_ReembolsoNegado.
    """
    for overrides in (
        {"cobertura_prevista": False},
        {"dentro_prazo": False},
        {"carencia_cumprida": False},
        {"beneficiario_ativo": False},
    ):
        inst = await start_reembolso(**overrides)
        iid = inst["id"]

        ut = await _drive_to_analista(engine, reembolso_probe, iid)
        assert "analise-reembolso" in ut.candidate_groups

        ended = await engine.activity_instances_ended(iid)
        assert not (ended & _END_ADVERSOS), (
            f"Inelegibilidade {overrides} nao deve produzir terminal adverso automatico (L0). ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)


async def test_fora_de_tabela_nao_auto_aprova(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Procedimento sem tabela => dentro_tabela=false => reembolso_auto_approval -> ANALISE_HUMANA."""
    inst = await start_reembolso(
        categoria_procedimento="opme",
        dentro_tabela=False,
        valor_calculado_tabela_cents=0,
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    # GAP F-1: `opme` nao tem row na `reembolso_calculo` -> catch-all -> o worker LE
    # SEM_TABELA da DMN e escreve dentro_tabela=false. `dmn_decisao_id` so existe como saida
    # do worker (nenhum seed, nenhum input mapping), entao le-lo prova que o caminho novo
    # executou; `dentro_tabela` vive num unico escopo (processo), entao o historico devolve o
    # valor final — o do worker, nao o seed.
    assert await engine.get_history_variable(iid, "dmn_decisao_id") == "reembolso_calculo"
    assert await engine.get_history_variable(iid, "dentro_tabela") is False

    ended = await engine.activity_instances_ended(iid)
    assert _END_AUTO not in ended, "Fora de tabela NAO pode auto-aprovar (L0)"
    assert not (ended & _END_ADVERSOS), "Fora de tabela NAO pode produzir adverso automatico (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_valor_de_referencia_vem_da_dmn_e_nao_da_tabela_python_removida(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """GAP F-1 (auditoria D1): o valor de referencia e' o da DMN, provado ENGINE-SIDE.

    DISCRIMINANTE por construcao. `consulta` @ `valor_solicitado_cents=20000`:

    - sob a tabela em Python REMOVIDA (`_BASE_VALUES_CENTS["consulta"] = 35000`) o worker
      escreveria `dentro_tabela=true` (20000 <= 35000);
    - sob a DMN `reembolso_calculo` (`r_consulta` = 12000, o valor que `BRT_Calculo` realmente
      emite) o worker escreve `dentro_tabela=false` (20000 > 12000).

    O seed de start manda `dentro_tabela=True` e `valor_calculado_tabela_cents=35000` (o numero da
    tabela morta) DE PROPOSITO: nenhum dos dois pode sobreviver — o primeiro e' recomputado, o
    segundo e' sombreado pela variavel LOCAL que o `camunda:inputParameter` de `ST_CalculateAmount`
    escreve a partir de `${calculo.valor_calculado_tabela_cents}`.

    Le `dentro_tabela` (escopo unico: processo; o historico devolve o valor final) e
    `dmn_decisao_id` (produzido SO pelo worker — nem seed nem input mapping o escrevem), evitando
    de proposito `valor_calculado_tabela_cents`/`fonte_tabela`, que passam a existir em DOIS
    escopos (local da activity + processo) e cuja ordem de linhas no `/history/variable-instance`
    nao e' garantida.
    """
    inst = await start_reembolso(
        categoria_procedimento="consulta",
        tipo_reembolso="livre_escolha",
        valor_solicitado_cents=20000,
        dentro_tabela=True,  # seed hostil — deve ser recomputado
        valor_calculado_tabela_cents=35000,  # o valor da tabela Python REMOVIDA — deve ser sombreado
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    assert await engine.get_history_variable(iid, "dmn_decisao_id") == "reembolso_calculo", (
        "o worker de calculo nao emitiu a citacao da DMN — ST_CalculateAmount nao executou o caminho GAP F-1"
    )
    assert await engine.get_history_variable(iid, "dmn_atividade_bpmn") == "BRT_Calculo"
    assert await engine.get_history_variable(iid, "dentro_tabela") is False, (
        "dentro_tabela=true com solicitado=20000 so e' possivel a partir de um valor de referencia "
        ">= 20000 — i.e. a tabela em Python removida (consulta 35000), nao a DMN (12000)"
    )

    ended = await engine.activity_instances_ended(iid)
    assert _END_AUTO not in ended, "acima da tabela da DMN NAO pode auto-aprovar (L0)"
    assert not (ended & _END_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


async def test_teto_zero_d07_bloqueia_auto_aprovacao_mesmo_com_seed_true(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """CEILING (finding 3, D-07): teto amh=0 => TODO AUTO_APROVAR cai em analise humana.

    Tudo favoravel + seed dentro_teto_l2=TRUE de upstream: o worker calculate_amount RECOMPUTA
    dentro_teto_l2=False (teto 0 fail-closed) ANTES de BRT_AutoApproval avaliar — o seed NAO fura
    o teto. Documenta o estado REAL, correto e fail-closed do v2 atual (nao um gap desta suite).
    """
    inst = await start_reembolso()  # payload canonico ja seeda dentro_teto_l2=True
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups, (
        "com teto 0 (D-07), o caminho AUTO_APROVAR deve cair em UT_AnaliseReembolso"
    )

    ended = await engine.activity_instances_ended(iid)
    assert _END_AUTO not in ended, (
        "CEILING VIOLADO: teto 0 (D-07) NUNCA pode auto-aprovar — seed dentro_teto_l2=true furou o teto"
    )
    assert not (ended & _END_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert not reembolso_probe.notifications_of_type("reembolso.issue_payment")


@pytest.mark.xfail(reason=_REEMBOLSO_CEILING_D07_REASON, strict=True)
async def test_happy_path_aprovacao_automatica_l2_com_teto_positivo(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Teto POSITIVO (D-07 resolvido) + tudo favoravel => AUTO_APROVAR integral.

    Donor injects a resolver with a positive ceiling (`reembolso_probe_teto_positivo`); v2's
    `register_reembolso_workers` has no seam for this (finding 3) — the REAL tenant ceiling (0,
    D-07 open) always applies, so this path is unreachable via the standard probe.
    """
    inst = await start_reembolso()
    iid = inst["id"]

    await reembolso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_AUTO in ended, f"Deve atingir End_ReembolsoAprovadoAutomatico. ended={ended}"
    assert not await engine.list_user_tasks(iid), "Auto-aprovacao L2 nao cria User Tasks"
    assert reembolso_probe.has_event(_REEMBOLSO_RECEIVED)
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_automatico")
    pagamentos = reembolso_probe.notifications_of_type("reembolso.issue_payment")
    assert pagamentos, "issue_payment deve ser executado na auto-aprovacao"
    assert not reembolso_probe.notifications_of_type("reembolso.send_reembolso_denial")


async def test_happy_path_aprovado_pelo_analista(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """dentro_teto_l2=false => analise humana; analista completa APROVAR => End_ReembolsoAprovadoAnalista.

    FLIPPED (wave-3, live-proven): analyze_request is a REAL worker (T2.5-P2B); the dead
    kafka-echo dossie assertion was adapted to engine-side evidence (ST_PrepararDossie in
    activity history) and the strict-xfail marker removed.
    """
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    # Engine-side (kafka-echo `notifications_of_type` morto pos-#178): o serviceTask analyze_request executou.
    assert "ST_PrepararDossie" in await engine.activity_instances_ended(iid), (
        "Worker analyze_request (ST_PrepararDossie) deve ter sido executado"
    )

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": 12000,
            "analista_id": "analista-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ANALISTA in ended, f"APROVAR => End_ReembolsoAprovadoAnalista. ended={ended}"
    assert not (ended & _END_ADVERSOS)
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_analista")
    assert "ST_IssuePaymentAnalista" in ended, (
        "issue_payment (ST_IssuePaymentAnalista) deve executar na aprovacao do analista"
    )


async def test_happy_path_negado_pelo_analista(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Analista completa NEGAR com campos obrigatorios => End_ReembolsoNegado (humano-gated)."""
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_reembolso": "NEGAR",
            "justificativa": "Despesa sintetica fora de cobertura contratual (teste L0)",
            "fundamentacao_contratual": "Clausula 5.2 — exclusao sintetica para teste",
            "analista_id": "analista-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_NEGADO in ended, f"Negativa humana deve atingir End_ReembolsoNegado. ended={ended}"
    assert reembolso_probe.has_event(
        _REEMBOLSO_COMPLETED, desfecho="negado_analista", analista_id="analista-sintetico-001"
    )

    # Engine-side (kafka-echo morto pos-#178): send_reembolso_denial executou apos a negativa humana
    # (decisao/analista foram inputs humanos acima; desfecho negado_analista via has_event acima).
    assert "ST_EnviarNegativa" in ended, (
        "send_reembolso_denial (ST_EnviarNegativa) deve executar apos negativa humana"
    )


async def test_happy_path_aprovado_parcial_pelo_analista(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Analista completa APROVAR_PARCIAL (valor menor) => End_ReembolsoParcial (humano-gated, adverso).

    Prova engine-side do DINHEIRO incluida: o valor reduzido aprovado (8000) chega ao pagamento
    (valor_cents no historico — so o worker de pagamento produz essa variavel; era 0 pre-w7).
    """
    inst = await start_reembolso(dentro_teto_l2=False, valor_solicitado_cents=12000)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_reembolso": "APROVAR_PARCIAL",
            "justificativa": "Valor solicitado acima do limite de tabela; reembolso parcial (teste)",
            "fundamentacao_contratual": "Clausula 6.1 — limite de reembolso por tabela de referencia",
            "valor_reembolso_aprovado_cents": 8000,
            "analista_id": "analista-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_PARCIAL in ended, f"APROVAR_PARCIAL => End_ReembolsoParcial. ended={ended}"
    assert _END_NEGADO not in ended
    assert reembolso_probe.has_event(
        _REEMBOLSO_COMPLETED, desfecho="aprovado_parcial", analista_id="analista-sintetico-001"
    )

    # Engine-side (kafka-echo morto pos-#178): ambos os serviceTasks executaram.
    assert "ST_ComunicarReducao" in ended, (
        "send_reembolso_denial (ST_ComunicarReducao) deve comunicar a reducao"
    )
    assert "ST_IssuePaymentParcial" in ended, (
        "issue_payment (ST_IssuePaymentParcial) deve pagar o valor reduzido aprovado"
    )
    # Prova engine-side do DINHEIRO (nao-vacua: valor_reembolso_aprovado_cents ja esta no escopo
    # via UT, mas valor_cents SO existe como output do worker de pagamento — 0 pre-w7).
    assert await engine.get_history_variable(iid, "valor_cents") == 8000, (
        "o pagamento parcial deve receber o valor aprovado (8000), nao 0/None"
    )


async def test_revisao_auditor_medico_decide_merito(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Analista encaminha ao auditor (SOLICITAR_AUDITOR); auditor NEGA por merito clinico."""
    inst = await start_reembolso(dentro_teto_l2=False, requer_avaliacao_clinica=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_reembolso": "SOLICITAR_AUDITOR"})
    await reembolso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut_auditor.candidate_groups

    await engine.complete_task_as_human(
        ut_auditor.id,
        {
            "decisao_reembolso": "NEGAR",
            "justificativa": "Procedimento sem indicacao clinica para reembolso (sintetico)",
            "fundamentacao_contratual": "Diretriz de utilizacao — exclusao sintetica para teste",
            "cid10_referencia": "Z00.0",
            "parecer_auditor": "Parecer sintetico do auditor (teste L0)",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_NEGADO in ended, f"Negativa do auditor deve atingir End_ReembolsoNegado. ended={ended}"
    assert _UT_AUDITOR in ended, "UT_RevisaoAuditorMedico deve estar no historico"
    # Engine-side (kafka-echo morto pos-#178): send_reembolso_denial executou apos a negativa do auditor.
    assert "ST_EnviarNegativa" in ended, (
        "send_reembolso_denial (ST_EnviarNegativa) deve executar apos negativa do auditor"
    )


# ===========================================================================
# Efeito adverso exige campos / worker guard
# ===========================================================================


async def test_negar_reembolso_exige_campos(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """NEGAR sem justificativa/fundamentacao/analista_id => worker guard recusa.

    `send_reembolso_denial_entry` raises `ReembolsoDenialNotHumanError` (PermissionError family) —
    the harness routes it to an immediate incident, `End_ReembolsoNegado` never reached. Absence
    assertions here hold regardless of the Kafka gap (finding 1) — the guard fires BEFORE any
    notification could be produced.
    """
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_reembolso": "NEGAR"})
    await reembolso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGADO not in ended, (
        "Negativa sem campos obrigatorios NAO pode atingir End_ReembolsoNegado (guard do worker)"
    )
    assert not reembolso_probe.notifications_of_type("reembolso.send_reembolso_denial"), (
        "send_reembolso_denial NAO deve comunicar negativa sem campos obrigatorios"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_aprovar_parcial_exige_valor_menor(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """APROVAR_PARCIAL com valor_aprovado >= valor_solicitado => worker guard recusa."""
    inst = await start_reembolso(dentro_teto_l2=False, valor_solicitado_cents=12000)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_reembolso": "APROVAR_PARCIAL",
            "justificativa": "tentativa sem reducao real",
            "fundamentacao_contratual": "clausula X",
            "valor_reembolso_aprovado_cents": 12000,
            "analista_id": "analista-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_PARCIAL not in ended, (
        "APROVAR_PARCIAL sem reducao real NAO pode atingir End_ReembolsoParcial (guard do worker)"
    )
    assert not reembolso_probe.notifications_of_type("reembolso.send_reembolso_denial"), (
        "send_reembolso_denial NAO deve comunicar reducao sem valor menor"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_worker_send_reembolso_denial_recusa_sem_humano() -> None:
    """Invocacao direta do entry function send_reembolso_denial_entry sem decisao humana =>
    `ReembolsoDenialNotHumanError` (guard). Unit-style sobre o handler real (SEM engine).

    ADAPTED (port rule 1, verify each register_*/entry-function shape on v2 main): v2's
    `send_reembolso_denial_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary) replaces
    donor's `make_send_reembolso_denial_handler(kafka) -> Callable[[ExternalTask], ...]`; the guard
    raises `ReembolsoDenialNotHumanError` (`PermissionError` subclass) rather than
    `WorkerBpmnError(error_code=...)` — see module docstring. The 6 guard scenarios (a-f) are
    preserved verbatim; success-case assertions check the returned dict (v2's function does not
    itself call `kafka.publish` — finding 1).
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao_reembolso ausente -> recusa
    with pytest.raises(ReembolsoDenialNotHumanError) as exc_a:
        send_reembolso_denial_entry({}, kafka=kafka)
    assert "ERR_REEMBOLSO_DENIAL_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_reembolso fora do conjunto adverso (ex.: APROVAR) -> recusa
    with pytest.raises(ReembolsoDenialNotHumanError) as exc_b:
        send_reembolso_denial_entry({"decisao_reembolso": "APROVAR"}, kafka=kafka)
    assert "ERR_REEMBOLSO_DENIAL_NOT_HUMAN" in str(exc_b.value)

    # (c) NEGAR mas faltando identidade do decisor (analista_id/auditor_id) -> recusa
    with pytest.raises(ReembolsoDenialNotHumanError) as exc_c:
        send_reembolso_denial_entry(
            {
                "decisao_reembolso": "NEGAR",
                "justificativa": "x",
                "fundamentacao_contratual": "y",
            },
            kafka=kafka,
        )
    assert "ERR_REEMBOLSO_DENIAL_NOT_HUMAN" in str(exc_c.value)

    # (d) APROVAR_PARCIAL mas valor aprovado >= solicitado -> recusa (reducao nao real)
    with pytest.raises(ReembolsoDenialNotHumanError) as exc_d:
        send_reembolso_denial_entry(
            {
                "decisao_reembolso": "APROVAR_PARCIAL",
                "justificativa": "x",
                "fundamentacao_contratual": "y",
                "analista_id": "a-1",
                "valor_reembolso_aprovado_cents": 12000,
                "valor_solicitado_cents": 12000,
            },
            kafka=kafka,
        )
    assert "ERR_REEMBOLSO_DENIAL_NOT_HUMAN" in str(exc_d.value)
    assert not kafka.published, "Nenhum efeito adverso deve ser publicado quando o guard recusa"

    # (e) decisao humana completa (NEGAR) -> comunica e carrega analista_id
    result_e = send_reembolso_denial_entry(
        {
            "decisao_reembolso": "NEGAR",
            "justificativa": "Negativa fundamentada (teste)",
            "fundamentacao_contratual": "Clausula 5.2",
            "analista_id": "analista-sintetico-001",
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_e["denial_sent"] is True
    assert result_e["decisao_reembolso"] == "NEGAR"
    assert result_e["analista_id"] == "analista-sintetico-001"

    # (f) decisao humana completa (APROVAR_PARCIAL com valor menor + auditor_id) -> comunica
    result_f = send_reembolso_denial_entry(
        {
            "decisao_reembolso": "APROVAR_PARCIAL",
            "justificativa": "Reducao fundamentada (teste)",
            "fundamentacao_contratual": "Clausula 6.1",
            "auditor_id": "auditor-sintetico-001",
            "valor_reembolso_aprovado_cents": 8000,
            "valor_solicitado_cents": 12000,
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_f["denial_sent"] is True
    assert result_f["decisao_reembolso"] == "APROVAR_PARCIAL"
    assert result_f["analista_id"] == "auditor-sintetico-001"
    # Kafka gap (finding 1): the entry function never calls kafka.publish itself.
    assert not kafka.published


# ===========================================================================
# Pendencia de documentacao
# ===========================================================================


async def test_pendencia_docs_recebidos_reavalia(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """documentacao_completa=false => reembolso.pended publicado; msg.reembolso.docs_received reavalia."""
    inst = await start_reembolso(documentacao_completa=False)
    iid = inst["id"]

    await reembolso_probe.drain()

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.reembolso.docs_received",
        "businessKey": business_key,
        "processVariables": {
            "documentacao_completa": {"value": True, "type": "Boolean"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.reembolso.docs_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await reembolso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _END_ADVERSOS), "Reavaliacao por mensagem nunca auto-nega/reduz (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)
    assert await engine.instance_is_active(iid) or _END_AUTO in ended, (
        "Apos a mensagem, a instancia deve reavaliar (ativa) ou chegar a aprovacao integral"
    )


async def test_pendencia_docs_publica_reembolso_pended(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """T3.1 event-gap remedy B (wave 4, latent conformance, event-gap design doc §2.8).

    documentacao_completa=false => ST_SolicitarDocumentos roda => ST_PublishReembolsoPended
    (novo, boundary-free) publica agents.events.reembolso.pended com os business keys do processo.
    NENHUM teste pre-existente pinava esta obrigacao de contrato (SP-OP-REEMBOLSO-001.md:100
    "produz") — regressao NOVA, nao adaptacao de um xfail vermelho ja existente (contraste com o
    padrao recurso/cancel/auth Wave 1). LIVE-PROVEN (t3.1-event-gap-w4 live validation):
    ST_PublishReembolsoPended completou no engine history e has_event casou ambos os payload
    vars contra um CIB Seven 2.1.0 real — strict-xfail removido nessa prova.
    """
    protocolo = _unique_protocolo()
    inst = await start_reembolso(documentacao_completa=False, protocolo_reembolso=protocolo)
    iid = inst["id"]

    await reembolso_probe.drain()

    assert await engine.instance_is_active(iid), "Instancia deve aguardar documentacao na GW_AguardarDocs"
    assert reembolso_probe.has_event(
        _REEMBOLSO_PENDED,
        tenant_id="amh",
        protocolo_reembolso=protocolo,
    ), "reembolso.pended (ST_PublishReembolsoPended) deve ser publicado com os business keys do processo"


async def test_pendencia_expira_decisao_humana_nunca_auto_nega(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Prazo de pendencia (ICE_PrazoPendencia, P5D) expira => UT_DecidirPendenciaExpirada (humano)."""
    inst = await start_reembolso(documentacao_completa=False)
    iid = inst["id"]

    await reembolso_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_PENDENCIA)
    assert "analise-reembolso" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_pendencia": "cancelar_solicitacao"})
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CANCELADO in ended, f"cancelar_solicitacao => End_ReembolsoCancelado. ended={ended}"
    assert _END_NEGADO not in ended, "Expiracao de pendencia NUNCA atinge End_ReembolsoNegado (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="cancelado_pendencia")


# ===========================================================================
# Timers de SLA
# ===========================================================================


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSla (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta.

    FLIPPED (wave-3, live-proven): notify_sla_risk is a REAL worker (T2.5-P2B); the dead
    kafka-echo assertion was adapted to engine-side evidence (ST_NotificarRiscoSla in activity
    history, UT ainda aberta) and the strict-xfail marker removed.
    """
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    await _drive_to_analista(engine, reembolso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    # Engine-side (kafka-echo morto pos-#178): notify_sla_risk executou (UT segue aberta abaixo).
    assert "ST_NotificarRiscoSla" in await engine.activity_instances_ended(iid), (
        "Worker notify_sla_risk (ST_NotificarRiscoSla) deve executar no alerta de SLA"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnalise (interruptivo): UT_AnaliseReembolso cancelada; UT_CoordenacaoReembolso criada."""
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    await _drive_to_analista(engine, reembolso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    assert reembolso_probe.has_event(_REEMBOLSO_SLA_BREACHED), "reembolso.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-reembolso" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "UT_AnaliseReembolso deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _END_ADVERSOS), "Estouro de SLA nunca auto-nega/reduz (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_assume_e_nega(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e NEGA => End_ReembolsoNegado com UT humana."""
    inst = await start_reembolso(dentro_teto_l2=False)
    iid = inst["id"]

    await _drive_to_analista(engine, reembolso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_reembolso": "NEGAR",
            "justificativa": "Prazo esgotado — coordenacao nega (sintetico)",
            "fundamentacao_contratual": "Clausula 5.2 — exclusao sintetica",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADO in ended
    await _assert_no_adverse_without_human_task(engine, iid)
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="negado_analista")


async def test_dmn_reembolso_sla_urgencia(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """tipo_reembolso=urgencia_emergencia => DMN reembolso_sla resolve sla_analise (ISO); timers existem."""
    inst = await start_reembolso(tipo_reembolso="urgencia_emergencia", dentro_teto_l2=False)
    iid = inst["id"]

    await _drive_to_analista(engine, reembolso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    assert job.activity_id == "BT_AlertaSla"
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnalise")
    assert job_sla.activity_id == "BT_SlaAnalise"


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_dmn_nenhuma_tem_saida_de_negativa() -> None:
    """Nenhuma DMN tem coluna de saida NEGAR/REDUZIR; admissibilidade/auto-approval com dominio fechado."""
    from xml.etree import ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _first_output_values(dmn_path: Path, decision_id: str) -> tuple[set[str], str | None]:
        tree = ET.parse(dmn_path)
        root = tree.getroot()
        decision = next(
            (e for e in root.iter() if _local(e.tag) == "decision" and e.get("id") == decision_id), None
        )
        assert decision is not None, f"decision '{decision_id}' nao encontrada em {dmn_path.name}"
        values: set[str] = set()
        last_first_output: str | None = None
        for rule in (e for e in decision.iter() if _local(e.tag) == "rule"):
            outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
            assert outputs, "cada rule deve ter outputEntry"
            text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
            assert text_el is not None and text_el.text
            val = text_el.text.strip().strip('"')
            values.add(val)
            last_first_output = val
        return values, last_first_output

    admiss_vals, admiss_last = _first_output_values(_DMN_COVERAGE, "reembolso_admissibility")
    assert admiss_vals == {"SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"}, (
        f"dominio de roteamento de admissibilidade inesperado: {admiss_vals}"
    )
    assert admiss_last == "ANALISE_HUMANA", "catch-all da admissibilidade deve rotear a ANALISE_HUMANA"

    auto_vals, auto_last = _first_output_values(_DMN_COVERAGE, "reembolso_auto_approval")
    assert auto_vals == {"AUTO_APROVAR", "ANALISE_HUMANA"}, (
        f"dominio de auto-aprovacao inesperado: {auto_vals}"
    )
    assert auto_last == "ANALISE_HUMANA", "catch-all da auto-aprovacao deve rotear a ANALISE_HUMANA"

    for dmn_path in (_DMN_COVERAGE, _DMN_CALCULO, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for out_entry in (e for e in tree.getroot().iter() if _local(e.tag) == "outputEntry"):
            text_el = next((c for c in out_entry if _local(c.tag) == "text"), None)
            txt = (text_el.text or "") if text_el is not None else ""
            up = txt.upper()
            assert "NEGAR" not in up, f"{dmn_path.name}: saida contem NEGAR (L0 violado): {txt}"
            assert "REDUZIR" not in up, f"{dmn_path.name}: saida contem REDUZIR (L0 violado): {txt}"


def test_dmn_calculo_dinheiro_integer_nunca_number() -> None:
    """reembolso_calculo: valor_calculado_tabela_cents e typeRef=integer (centavos), nunca 'number'."""
    from xml.etree import ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    tree = ET.parse(_DMN_CALCULO)
    root = tree.getroot()

    outputs = {o.get("name"): (o.get("typeRef") or "") for o in root.iter() if _local(o.tag) == "output"}
    assert outputs.get("valor_calculado_tabela_cents") == "integer", (
        "valor_calculado_tabela_cents deve ser integer (centavos) — nunca 'number'"
    )
    assert outputs.get("multiplo_tabela_aplicado") == "double"
    assert outputs.get("fonte_tabela") == "string"

    fontes = set()
    for out_entry in (e for e in root.iter() if _local(e.tag) == "outputEntry"):
        text_el = next((c for c in out_entry if _local(c.tag) == "text"), None)
        if text_el is not None and text_el.text:
            fontes.add(text_el.text.strip().strip('"'))
    assert "SEM_TABELA" in fontes, "reembolso_calculo deve ter catch-all SEM_TABELA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_COVERAGE, _DMN_CALCULO, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use integer/double)"


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_protocolo(
    engine: EngineRest,
    reembolso_probe: ReembolsoEngineProbe,
    start_reembolso: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa."""
    protocolo = "REEMB-TESTE-IDEM-001"
    business_key = f"REEMB-amh-{protocolo}"

    first = await start_reembolso(protocolo_reembolso=protocolo, dentro_teto_l2=False)
    await _drive_to_analista(engine, reembolso_probe, first["id"])

    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
