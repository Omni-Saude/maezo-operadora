"""SP-OP-RECURSO-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2
(V2-COMPLETION-PLAN §3, 13-family process-suite port). recurso.py is one of the "6 typed-I/O
modules with new dict-boundary entry functions" (ADR-0026 §2b, `bootstrap.py`'s own
categorization) — this port mirrors `test_sp_op_cancel_001.py`'s dict-boundary/typed-I/O pattern
(typed dataclasses, entry functions, self-contained probe/fixtures) rather than auth's
`WorkerBase`-class pattern.

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-RECURSO-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `RECURSO-amh-{numero_guia_tiss}-{glosa_id}`;
2. drena as external tasks com o `recurso_probe` (workers reais Phase-2 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que manter-a-glosa passa pela UT
   humana).

Dados sinteticos obvios: guia `GUIA-TESTE-0001`, glosa `GLOSA-TESTE-NNNN`, lote `LOTE-TESTE-0001`,
prestador `PREST-TESTE-001`, beneficiario pseudonimizado `PSEUDO-TESTE-001`, tenant `amh`.
Business key `RECURSO-amh-{guia}-{glosa}`. Process key: SP-OP-RECURSO-001 (exato — nao alterar).

## Invariante L0 (DoD deliverable)

test_nenhum_caminho_automatizado_produz_desistencia:
  Varredura de TODAS as combinacoes de input das DMNs (recurso_admissibility / recurso_eligibility)
  em 120 combinacoes. As instancias NUNCA atingem End_RecursoNaoInterposto / End_GlosaMantida /
  End_RecursoInadmissivel sem que uma User Task humana tenha sido completada. A prova e feita
  consultando a historia do engine (history/activity-instance). Este teste so avanca UMA rodada de
  `drain()` sem completar nenhuma UT — nao toca nenhum topico nao-implementado (ver finding 2
  abaixo), permanece PASSA (nao marcado).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor unless a
finding below says otherwise):
  - import paths -> v2 `maezo.tools.workers.recurso`/`events`/`harness`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5; donor's own `_BPMN`/`_DMN_*`
    pointed at a stale `src/maezo/processes/{bpmn,dmn}/*` layout that does not exist on v2 main).
  - `engine` fixture: NOT redefined locally (donor self-contained `engine`/`_engine_available()`
    per-file) — v2's shared `conftest.py` hoists this fixture centrally (its own docstring:
    "every donor family file defines a byte-identical copy of this fixture, so it is hoisted here
    verbatim") — mirrors `test_sp_op_cancel_001.py`'s own adoption of the shared fixture.
    `deploy_artifacts`/`recurso_probe`/`start_recurso` stay LOCAL (probe construction contract is
    NOT identical across families, per `conftest.py`'s own rationale).
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`), same mechanical wrap cancel/auth use.
  - `test_worker_guard_register_desistencia_recusa_sem_humano` (no engine — unit-style over the
    real handler, mirrors cancel's own `test_send_cancellation_notice_recusa_sem_humano`
    adaptation): v2's dict-boundary entry function is `register_desistencia_entry(variables:
    dict, *, kafka=None) -> dict` (NOT donor's `make_register_desistencia_handler(kafka) ->
    Callable[[ExternalTask], ...]`, which does not exist on v2 main). The guard ALSO changed
    shape: v2's guard raises `DesistenciaNotHumanError` (a `PermissionError` subclass — the
    harness routes any `PermissionError` straight to an engine incident, `harness.py` `_handle`),
    not donor's `WorkerBpmnError(error_code=...)` (a modeled BPMN error routed to a boundary
    catch). Scenarios (a)-(d) (analista path) are preserved verbatim; scenario (e) (auditor
    ACEITAR_GLOSA path) originally surfaced TWO genuine, independently-verified v2 bugs (findings
    3 and 4 below) rather than a fixture-shape mismatch — split into its own
    `test_worker_guard_register_desistencia_auditor_path_finding4` test, xfail on both. BOTH
    findings are now RESOLVED (finding 3 by t3.1-a2-recurso-valor-glosa, finding 4 by
    t3.1-recurso-findings-a — `RecursoDesistenciaInput` now carries `decisao_auditor_recurso`/
    `auditor_id` and `register_desistencia`'s guard accepts the auditor channel) — that test is
    no longer xfail.

FINDINGS (grep/read/direct-execution confirmed — see PR body / evidence-ledger for full detail):

  1. THE Kafka-publish gap (systemic, `_RECURSO_KAFKA_GAP_REASON`): grep `kafka\\.publish\\(`
     across `src/maezo/tools/workers/*.py` returns exactly one call site (`events.py:247`,
     generic `operadora.events.publish` — already fixed, T3.1 R2). Every one of
     `register_recurso_workers`'s 8 entry functions (recurso.py:437-533) explicitly discards its
     `kafka` seam (`del kafka  # unused`) and never calls it — including
     `request_documents_entry`, whose underlying BPMN service task (`ST_SolicitarDocumentos`)
     declares an embedded `event_topic_pended` inputParameter
     (`agents.events.recurso.pended`) the worker was meant to publish directly (mirrors
     auth.py/escalation.py's own embedded-publish gaps) — `notify_prestador()` never reads that
     parameter at all. Distinguished from generic-publish-topic events (`has_event(...)` backed by
     `events.py`'s real `kafka.publish` call, which DOES work and stays unmarked below) —
     `notifications_of_type("recurso.<fn>")` and `has_event(_RECURSO_PENDED)` are the two
     assertion shapes this blocks.

  2. Registry drift (recurso-specific, `_RECURSO_UNIMPLEMENTED_TOPIC_REASON` +
     `test_recurso_worker_registry_drift_vs_bpmn`) — the GAPS half RESOLVED by t3.1-recurso-
     batch2 (built, pending live-proof flip); the ORPHANS half is UNCHANGED (still a real,
     documented drift, not this task's scope). Historical: unlike cancel's (now-fixed) 1:1
     registry, recurso's was drifted in BOTH directions, confirmed by cross-checking
     `register_recurso_workers` (registered exactly 8 `operadora.recurso.*` topics pre-fix)
     against `grep camunda:topic=\"operadora.recurso` (`spec/processes/bpmn/
     SP-OP-RECURSO-001_Recurso_Glosa.bpmn`, 13 distinct topics + 3 `register_desistencia` call
     sites):
       - GAPS (RESOLVED, t3.1-recurso-batch2): `notify_sla_risk`, `escalate_ans_timeout`,
         `submit_appeal`, `track_status`, `reconcile_payment` now each have an implementing
         function, registered in `register_recurso_workers` — `_RECURSO_UNIMPLEMENTED_TOPICS`
         below is now the empty set (`test_recurso_worker_registry_drift_vs_bpmn` reproves this
         statically, no engine needed). RECORRER is THEREFORE NO LONGER a dead end in `src/`
         terms — `ST_SubmitAppeal`/`ST_TrackStatus`/`ST_EscalateAnsTimeout`/
         `ST_ReconcilePaymentDeferido`/`ST_ReconcilePaymentParcial` all have a worker that will
         claim the external task. NOT YET LIVE-PROVEN against a real engine (no docker/engine in
         that task's scope) — `_RECURSO_WORKER_TOPICS` below (this suite's drain list) is
         DELIBERATELY left at its pre-fix 4 topics: expanding it now, before a live engine proves
         the new workers' actual behavior, risks an unreviewed XPASS against these `strict=True`
         xfails. Flipping the drain list + removing the xfail marks is the deliberate NEXT step
         ("pending live-proof flip"), left to whoever next has engine access (program discipline —
         precedent: auth's removed boundary xfail, findings 3/4 above).
       - ORPHANS (UNCHANGED, out of this task's scope — registered, but no BPMN service task ever
         creates a task for the topic — dead code from the engine's perspective):
         `validate_recurso`, `assess_eligibility` (the BPMN's `BRT_Admissibilidade`/
         `BRT_Eligibility` call `recurso_admissibility`/`recurso_eligibility` DIRECTLY via
         `camunda:decisionRef`, bypassing `assess_eligibility_entry` entirely — `assess_
         eligibility`'s own elaborate DMN-chaining logic never runs against a live instance),
         `prepare_dossier`, `escalate_to_junta` (`GW_DecisaoRecurso`'s `ESCALAR_AUDITOR` branch
         routes straight to `UT_RevisaoAuditorMedico`, no service task), `publish_completed`
         (folds into generic `operadora.events.publish`, by design per the module's own
         docstring).

  3. `valor_glosa_aceito` TypeError (recurso-specific CODE BUG) — RESOLVED by
     t3.1-a2-recurso-valor-glosa. Historical: `register_desistencia()` (recurso.py:366) did
     `if input_data.valor_glosa_aceito <= 0:` — a raw Python numeric comparison — on a dataclass
     field typed `float` (`RecursoDesistenciaInput.valor_glosa_aceito`, recurso.py:112). CORRECTED
     root cause (a prior version of this docstring wrongly claimed
     `engine_rest.py::EngineRest._to_camunda_vars` had no `float` branch — it DOES: engine_rest.py
     :145-149, mirroring `harness.py::_to_camunda_var`:335-336, floats always map to a Camunda
     `Double`, never a `String`): the actual cause is that this suite's own fixtures
     (`_desistencia_fields()`/`start_recurso`) seed `valor_glosa_aceito` as a Python `str`
     (`"150.00"`, the donor's own BRL-as-string convention, mirrored verbatim here per the port's
     synthetic-data rule) — a `str` never reaches the float branch at all, so it fell into the
     catch-all `else` branch regardless and was sent to the engine as a Camunda `String` variable;
     `harness.py::_from_camunda_var` then decoded that `String`-typed variable back to a Python
     `str` (no numeric coercion), so the comparison raised `TypeError: '<=' not supported between
     instances of 'str' and 'int'` and the task retried then incidented rather than cleanly
     reaching `End_RecursoNaoInterposto`/`End_GlosaMantida`/`End_RecursoInadmissivel`. FIX (SRC-
     local, recurso.py): `register_desistencia()` now parses the monetary value via
     `_parse_valor_glosa_aceito()` (float, dot-decimal, the `contas.py` `float(brl)` money idiom)
     BEFORE the `<= 0` guard, and FAILS CLOSED (treats a missing/blank/non-numeric value as a
     missing required field -> `DesistenciaNotHumanError`) — never silently defaults to 0.
     LIVE-VERIFIED against a real engine, the 5 xfails that previously carried this reason split
     as follows: (1) FLIPPED TO PASSING — `test_coordenacao_assume_e_mantem_glosa_humano` and the
     guard unit `test_worker_guard_register_desistencia_recusa_sem_humano` (scenarios a-d) now
     pass for the right reason (desistencia decision completes, terminal reached); (2) originally
     RE-POINTED to finding 1 (kafka drift) — `test_happy_path_nao_recorrer_humano` and
     `test_coordenacao_inadmissivel_humano_gated` reach End_RecursoNaoInterposto/
     End_RecursoInadmissivel and emit their _RECURSO_COMPLETED events, but their trailing
     `notifications_of_type('recurso.register_desistencia')` assert was STALE — that internal
     notification channel is structurally always-empty in v2 (register_desistencia_entry never
     publishes to it, `del kafka`; ADAPTED (t3.1-recurso-findings-a, finding 1a): both tests now
     assert the human decisor's id on the real, engine-observable `_RECURSO_COMPLETED` event
     instead (`has_event(..., analista_id=...)`), which DOES work (events.py's real kafka.publish
     call site) — both FLIPPED TO PASSING, finding 1 (kafka-notification-channel gap) remains
     out of scope and unaffected; (3) originally RE-POINTED to finding 4 (auditor gap) —
     `test_happy_path_auditor_aceita_glosa_mantem_glosa_humano` and the split-out
     `test_worker_guard_register_desistencia_auditor_path_finding4` — finding 4 is now RESOLVED
     (see below), and both tests are ADAPTED the same way as (2) and FLIPPED TO PASSING.

  4. Auditor `ACEITAR_GLOSA` unrecognized (recurso-specific) — RESOLVED by
     t3.1-recurso-findings-a. Historical (LIVE-CONFIRMED, independent of finding 3):
     `ST_RegisterGlosaMantida` (the auditor's `ACEITAR_GLOSA` path, `GW_MeritoAuditor` ->
     `Flow_GWMerito_AceitarGlosa`) routes to the SAME `operadora.recurso.register_desistencia`
     topic as the analista's `NAO_RECORRER` path, but `RecursoDesistenciaInput` (recurso.py:
     107-115, pre-fix) had ONLY `decisao_recurso`/`analista_id` fields — no
     `decisao_auditor_recurso`, no `auditor_id`. `pick_fields()` silently dropped both when the
     auditor completed `UT_RevisaoAuditorMedico` with `decisao_auditor_recurso='ACEITAR_GLOSA'`
     (the BPMN never resets `decisao_recurso` off its earlier `'ESCALAR_AUDITOR'` value), so the
     guard ALWAYS saw `decisao_recurso != 'NAO_RECORRER'` and refused even with every
     BPMN-documented field present. `End_GlosaMantida` was therefore PERMANENTLY unreachable via
     the intended human path, for two independent reasons (this + finding 3 both blocked the same
     terminal). FIX (SRC-local, recurso.py): `RecursoDesistenciaInput` now carries
     `decisao_auditor_recurso`/`auditor_id`, and `register_desistencia()`'s guard accepts EITHER
     the analista channel (`decisao_recurso == NAO_RECORRER` + `analista_id`) OR the auditor
     channel (`decisao_auditor_recurso == ACEITAR_GLOSA` + `auditor_id`) — both channels still
     require justificativa_desistencia/valor_glosa_aceito/referencia_contratual; the guard refuses
     ONLY if NEITHER channel's decision field matches, with a channel-aware missing-fields
     message. Zero BPMN/contract changes — the BPMN already routed `ACEITAR_GLOSA` ->
     `register_desistencia` -> `End_GlosaMantida`.

  5. `ERR_RECURSO_INVALID_GLOSA` guard missing (GAP-RECURSO-3, `_RECURSO_INVALID_GLOSA_GUARD_
     MISSING_REASON`, TWO independent reasons, both grep/read-confirmed) — BOTH RESOLVED at the
     `src/` level by t3.1-recurso-batch2 (built, pending live-proof flip). Historical: the BPMN
     declares `bpmn:error Error_RecursoGlosaInvalida`/`ERR_RECURSO_INVALID_GLOSA` with boundary
     catches `BE_GlosaInvalidaDocs` (on `ST_SolicitarDocumentos`) / `BE_GlosaInvalidaDossie` (on
     `ST_PrepararDossie`) routing to `End_RecursoGlosaInvalidaOrigem` when `glosa_id` arrives
     empty/absent. (a) `recurso.py` defined `RecursoGlosaInvalidaError` but never raised it
     anywhere — `request_documents_entry`/`analyze_request_entry` performed no `glosa_id`
     validation at all, so `glosa_id=""` just flowed through normally instead of hitting the
     boundary-caught terminal. FIX: `RecursoGlosaInvalidaError` (dead code) DELETED;
     `request_documents_entry`/`analyze_request_entry` now call `_require_glosa_id(glosa_id)`
     FIRST (before `notify_prestador`/`analyze_merits`), which raises
     `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` when absent/empty. (b) EVEN IF raised, the OLD
     `RecursoGlosaInvalidaError` subclassed `ValueError`, not `WorkerBpmnError` — `harness.py`'s
     `_handle` routes a bare `ValueError` straight to a fail-closed incident unconditionally; only
     a `WorkerBpmnError` with its `error_code` in the harness's `bpmn_error_allowlist` is ever
     dispatched as a real `bpmnError` reaching a BPMN boundary catch. FIX: `RECURSO_BPMN_ERROR_
     ALLOWLIST = frozenset({"ERR_RECURSO_INVALID_GLOSA"})` (recurso.py) is now unioned into
     `worker_runtime/service.py`'s `PRODUCTION_BPMN_ERROR_ALLOWLIST` — gate-proven consumption-
     covered (`scripts/ci/check_bpmn_error_allowlist.py` PASS; both
     `operadora.recurso.request_documents`/`operadora.recurso.analyze_request` are single-consumer
     topics whose sole consuming process, SP-OP-RECURSO-001, declares this errorCode on BOTH),
     G2-val class (ADR-0030 census) so NOT T-E-gated, Tier-0/Tier-2-enabled in production TODAY.
     NOT YET LIVE-PROVEN, for one remaining reason this suite's own fixture does not (yet) close:
     `recurso_probe` below constructs its `WorkerHarness` with NO `bpmn_error_allowlist=` argument
     (mirrors its pre-fix state) — against THIS test suite's own harness, the raised
     `WorkerBpmnError` would still demote to a fail-closed incident rather than reach the modeled
     boundary catch, even though production (`service.py`) is now correctly wired. Wiring
     `bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST` into `recurso_probe`'s `WorkerHarness`
     (mirroring auth's/cancel's own probe fixtures) is the deliberate remaining step for the actual
     live-proof flip — left to whoever next has engine access, alongside actually running these 2
     tests against a real engine and removing the xfail marks with that live evidence (program
     discipline; precedent: auth's removed boundary xfail).

  6. D-07 ceilings gap does NOT apply to recurso (grep-confirmed: recurso.py does not import
     `CeilingResolver`/`ceilings.py`) — not cited anywhere below.

  7. notification_bridge / CONTAS->RECURSO handoff: recurso's OWN donor suite starts
     SP-OP-RECURSO-001 directly via `engine.start_by_key` in every test (the test is its own
     origin agent, matching auth/cancel's convention) — it never asserts anything about being
     auto-triggered by an incoming CONTAS handoff. The bridge-not-instantiated gap (T2.6) is
     therefore NOT relevant to this file (it would only matter to contas' own suite, which is the
     upstream/origin side of that rule) and is not cited below.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.recurso import (
    DesistenciaNotHumanError,
    register_desistencia_entry,
    register_recurso_workers,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"
_DMN_ADMISSIBILITY = _REPO / "spec/processes/dmn/recurso_admissibility.dmn"
_DMN_ELIGIBILITY = _REPO / "spec/processes/dmn/recurso_eligibility.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/recurso_sla.dmn"

# External task topics do contrato SP-OP-RECURSO-001 que TEM worker real registrado E aparecem no
# BPMN (a intersecao pratica — ver finding 2 no docstring do modulo para os dois lados do drift).
_PUBLISH_TOPIC = "operadora.events.publish"
_REQUEST_DOCS_TOPIC = "operadora.recurso.request_documents"
_ANALYZE_TOPIC = "operadora.recurso.analyze_request"
_REGISTER_DESISTENCIA_TOPIC = "operadora.recurso.register_desistencia"

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). finding 2
# RESOLVED (t3.1-recurso-batch2, built pending live-proof flip): os 5 topicos antes
# BPMN-declarados-mas-sem-worker AGORA tem handler registrado em register_recurso_workers — mas
# esta lista de drain permanece DELIBERADAMENTE nao-expandida (os mesmos 4 topicos pre-fix) ate
# que um engine real prove o comportamento dos novos workers; expandi-la agora arriscaria um
# XPASS nao revisado contra os strict xfails que ainda os citam (ver
# _RECURSO_UNIMPLEMENTED_TOPIC_REASON).
_RECURSO_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _REQUEST_DOCS_TOPIC,
    _ANALYZE_TOPIC,
    _REGISTER_DESISTENCIA_TOPIC,
]

# finding 2 (module docstring) — RESOLVED by t3.1-recurso-batch2: register_recurso_workers now
# has a handler for all 5 (built, pending live-proof flip) — the gap set is the empty set.
# test_recurso_worker_registry_drift_vs_bpmn statically reproves this (no engine needed).
_RECURSO_UNIMPLEMENTED_TOPICS: frozenset[str] = frozenset()

# finding 2 (module docstring): topicos REGISTRADOS sem nenhuma service task BPMN correspondente
# (dead code do ponto de vista do engine — nunca invocados por SP-OP-RECURSO-001).
_RECURSO_ORPHAN_TOPICS = frozenset(
    {
        "operadora.recurso.validate_recurso",
        "operadora.recurso.assess_eligibility",
        "operadora.recurso.prepare_dossier",
        "operadora.recurso.escalate_to_junta",
        "operadora.recurso.publish_completed",
    }
)

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-RECURSO-001.md)
_RECURSO_RECEIVED = "agents.events.recurso.received"
_RECURSO_PENDED = "agents.events.recurso.pended"
_RECURSO_SLA_BREACHED = "agents.events.recurso.sla_breached"
_RECURSO_COMPLETED = "agents.events.recurso.completed"

# User Task definition keys (BPMN)
_UT_ANALISTA = "UT_AnaliseRecursoAnalista"
_UT_AUDITOR = "UT_RevisaoAuditorMedico"
_UT_COORDENACAO = "UT_CoordenacaoRecursoAssume"
_UT_ESCALONAMENTO = "UT_EscalonamentoPrazo"

# Terminais adversos (manter-a-glosa) — todos humano-gated (L0 hard).
_END_NAO_INTERPOSTO = "End_RecursoNaoInterposto"
_END_GLOSA_MANTIDA = "End_GlosaMantida"
_END_INADMISSIVEL = "End_RecursoInadmissivel"
_ENDS_ADVERSOS = frozenset({_END_NAO_INTERPOSTO, _END_GLOSA_MANTIDA, _END_INADMISSIVEL})

# Terminais neutros
_END_DEFERIDO = "End_RecursoDeferido"
_END_PARCIAL = "End_RecursoParcial"
_END_INDEFERIDO = "End_RecursoIndeferido"
# GAP-RECURSO-3 (dangling-catch fix, #136 no donor): terminal NEUTRO compartilhado pelos boundary
# catches de origem-invalida (BE_GlosaInvalidaDocs / BE_GlosaInvalidaDossie).
_END_GLOSA_INVALIDA_ORIGEM = "End_RecursoGlosaInvalidaOrigem"

# User Tasks humanas que podem produzir manter-a-glosa (L0 hard guard).
_UT_HUMANAS_DESISTENCIA = frozenset({_UT_ANALISTA, _UT_AUDITOR, _UT_COORDENACAO, _UT_ESCALONAMENTO})


# ---------------------------------------------------------------------------
# _REASON constants — um por finding recurso-specifico (ver module docstring FINDINGS).
# ---------------------------------------------------------------------------

_RECURSO_KAFKA_GAP_REASON = (
    "v2 systemic drift (T3.1 finding 1 — same class as auth/cancel/escalation residuals, ledger "
    "row 'T3.1 (events.publish fix)'): every register_recurso_workers entry function "
    "(validate_recurso_entry, assess_eligibility_entry, request_documents_entry, "
    "analyze_request_entry, prepare_dossier_entry, escalate_to_junta_entry, "
    "register_desistencia_entry, publish_completed_entry — recurso.py:437-533) explicitly "
    "discards its kafka seam ('del kafka  # unused') and never calls kafka.publish. Blocks (1) "
    "notifications_of_type('recurso.<fn>') for analyze_request/request_documents/"
    "register_desistencia and (2) has_event(_RECURSO_PENDED) — ST_SolicitarDocumentos's embedded "
    "event_topic_pended inputParameter (agents.events.recurso.pended) that "
    "request_documents_entry/notify_prestador() never reads or publishes. Distinct from generic "
    "operadora.events.publish assertions (has_event on _RECURSO_RECEIVED/_COMPLETED/"
    "_SLA_BREACHED), which DO work (events.py's real kafka.publish call site, T3.1 R2) and stay "
    "unmarked. Fix belongs to the Kafka-producer wiring task, not this port."
)

_RECURSO_UNIMPLEMENTED_TOPIC_REASON = (
    "v2 gap (recurso-specific, finding 2 — NOT the generic kafka-publish drift) — BUILT, PENDING "
    "LIVE-PROOF FLIP (t3.1-recurso-batch2): SP-OP-RECURSO-001's BPMN declares 5 "
    "operadora.recurso.* external-task topics that register_recurso_workers previously had ZERO "
    "handler for — notify_sla_risk (ST_NotificarRiscoSla), escalate_ans_timeout "
    "(ST_EscalateAnsTimeout), submit_appeal (ST_SubmitAppeal), track_status (ST_TrackStatus), "
    "reconcile_payment (ST_ReconcilePaymentDeferido/Parcial) — all 5 now have an implementing "
    "function + registration in recurso.py (notify_sla_risk/escalate_ans_timeout/submit_appeal/"
    "track_status as raw async harness.register() handlers threading kafka for their "
    "notifications_of_type/has_event observability; reconcile_payment mirrors contas.py exactly, "
    "no kafka). NOT YET LIVE-PROVEN: this suite's own _RECURSO_WORKER_TOPICS (drain list) is "
    "DELIBERATELY left unexpanded (still the pre-fix 4 topics) so this strict-xfail cannot "
    "silently XPASS before a real engine confirms the new workers' actual behavior — expanding "
    "the drain list + running against a live engine + removing this mark is the deliberate "
    "remaining step (program discipline; precedent: auth's removed boundary xfail)."
)

# Finding 3 (valor_glosa_aceito TypeError) RESOLVED by t3.1-a2-recurso-valor-glosa — the
# strict-xfail reason it carried was retired when the src fix landed (see module docstring
# finding 3). Finding 4 (auditor ACEITAR_GLOSA guard gap) is ALSO now RESOLVED, by
# t3.1-recurso-findings-a — its strict-xfail reason constant
# (_RECURSO_AUDITOR_ACEITAR_GLOSA_GUARD_GAP_REASON) is retired the same way (see module docstring
# finding 4); every path (analista, coordenacao, auditor) now passes.

_RECURSO_INVALID_GLOSA_GUARD_MISSING_REASON = (
    "v2 gap (GAP-RECURSO-3, finding 5, TWO independent reasons, both grep/read-confirmed) — BOTH "
    "BUILT, PENDING LIVE-PROOF FLIP (t3.1-recurso-batch2): the BPMN declares bpmn:error "
    "Error_RecursoGlosaInvalida/ERR_RECURSO_INVALID_GLOSA with boundary catches "
    "BE_GlosaInvalidaDocs (on ST_SolicitarDocumentos) / BE_GlosaInvalidaDossie (on "
    "ST_PrepararDossie) routing to End_RecursoGlosaInvalidaOrigem when glosa_id arrives "
    "empty/absent. (a) FIXED: the dead RecursoGlosaInvalidaError class (defined, never raised) "
    "was deleted; request_documents_entry/analyze_request_entry now call _require_glosa_id(...) "
    "FIRST (before notify_prestador/analyze_merits), raising "
    "WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA) when glosa_id is absent/empty. (b) FIXED: "
    "RECURSO_BPMN_ERROR_ALLOWLIST = frozenset({'ERR_RECURSO_INVALID_GLOSA'}) is unioned into "
    "worker_runtime/service.py's PRODUCTION_BPMN_ERROR_ALLOWLIST — gate-proven consumption-"
    "covered (scripts/ci/check_bpmn_error_allowlist.py PASS), G2-val class so NOT T-E-gated, "
    "enabled in production TODAY. NOT YET LIVE-PROVEN: this suite's own recurso_probe fixture "
    "still constructs its WorkerHarness with NO bpmn_error_allowlist= (deliberately unchanged — "
    "see the fixture's own docstring) — wiring bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST "
    "there (mirrors auth/cancel) + running against a live engine + removing this mark is the "
    "deliberate remaining step (program discipline; precedent: auth's removed boundary xfail)."
)


@dataclass
class RecursoEngineProbe:
    """Driva os workers reais de Phase-2 (recurso) contra o engine CIB Seven."""

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
            self.transport, self.harness, self.worker_id, _RECURSO_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de recurso de glosa da arvore no engine real."""
    return await engine.deploy(
        _BPMN, _DMN_ADMISSIBILITY, _DMN_ELIGIBILITY, _DMN_SLA, name="SP-OP-RECURSO-001-qa"
    )


@pytest_asyncio.fixture
async def recurso_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[RecursoEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-2 de recurso.

    Nenhum `bpmn_error_allowlist` e configurado (diferente de auth/cancel) — AINDA (finding 5 do
    docstring do modulo, t3.1-recurso-batch2): recurso.py AGORA levanta `WorkerBpmnError(
    ERR_RECURSO_INVALID_GLOSA)` (`request_documents_entry`/`analyze_request_entry`, guardado por
    `_require_glosa_id`) e `RECURSO_BPMN_ERROR_ALLOWLIST` ja esta unido ao allowlist de PRODUCAO
    (`worker_runtime/service.py`, gate-proven via `scripts/ci/check_bpmn_error_allowlist.py`) —
    mas este fixture ainda NAO passa `bpmn_error_allowlist=` ao `WorkerHarness` (deliberado,
    pending live-proof flip: ver module docstring finding 5). Contra ESTE harness, o
    `WorkerBpmnError` ainda demove para incidente fail-closed em vez de alcancar o boundary catch
    modelado — passar `bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST` aqui (espelhando
    auth/cancel) e o proximo passo deliberado para o flip real.
    """
    worker_id = f"qa-recurso-worker-{uuid.uuid4().hex[:8]}"
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
    register_recurso_workers(harness, kafka)
    # T3.1 R2: o worker generico de operadora.events.publish que toda ST_Publish* deste BPMN usa —
    # espelha a composicao register_phase0_workers do proprio donor.
    register_events_workers(harness, kafka)
    probe = RecursoEngineProbe(
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


def _unique_glosa(prefix: str = "GLOSA-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _future_anchor_iso(days: int = 40) -> str:
    """Ancora do teto P30D no FUTURO dinamico (GAP-RECURSO-1; licao GAP-NIP-1).

    Com o `timeDate` absoluto, ancora + P30D no PASSADO faz o boundary de teto disparar
    IMEDIATAMENTE ao attach (correto em prod: teto ja estourado; mas quebraria todo teste que
    precisa alcancar uma UT sem estouro). Ancora relativa a "agora" (+40d, confortavelmente alem
    do P30D do teto: teto em ~+70d).
    """
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


@pytest_asyncio.fixture
async def start_recurso(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key RECURSO-amh-{guia}-{glosa} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (GUIA-TESTE-0001, GLOSA-TESTE-NNNN, tenant amh).
    Os fatos pre-resolvidos por worker (glosa_existe, dentro_prazo_recurso,
    documentacao_recurso_completa) sao seeded como variaveis de start — o WorkerHarness NAO
    escreve facts de roteamento de volta ao engine (so observa/echo/publica), entao o teste e o
    agente de origem semeia os facts (mesma tecnica de auth/cancel).

    GAP-RECURSO-1: `data_recebimento_recurso_iso` (ancora do teto absoluto P30D) e seedada
    DINAMICA no futuro (+40d — ver `_future_anchor_iso`). Override com `None` REMOVE a variavel do
    start (testa o fail-safe da DMN: ancora indefinida -> data_ciencia_glosa).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        glosa = overrides.pop("glosa_id", _unique_glosa())
        guia = overrides.pop("numero_guia_tiss", "GUIA-TESTE-0001")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_guia_tiss": guia,
            "glosa_id": glosa,
            "numero_lote_tiss": "LOTE-TESTE-0001",
            "numero_conta": "CONTA-TESTE-0001",
            "prestador_id": "PREST-TESTE-001",
            "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
            "glosa_type": "administrativa",
            "glosa_reason_code": "GUIA_INCOMPLETA",
            "valor_glosado_brl": "150.00",
            "codigo_procedimento_tuss": "40304361",
            "cid10": "",
            "documentos_recurso_refs": "[]",
            "data_ciencia_glosa": "2026-06-01",
            "data_recebimento_recurso_iso": _future_anchor_iso(),
            "glosa_existe": True,
            "dentro_prazo_recurso": True,
            "documentacao_recurso_completa": True,
            "desfecho_humano": "",
            "loop_counter": 0,
        }
        variables.update(overrides)
        variables = {k: v for k, v in variables.items() if v is not None}
        business_key = f"RECURSO-amh-{guia}-{glosa}"
        return await engine.start_by_key("SP-OP-RECURSO-001", business_key, variables)

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


async def _assert_no_desistencia_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que os terminais adversos nao existem sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS_DESISTENCIA
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: terminal(is) adverso(s) {adversos_atingidos} atingido(s) "
            f"para instancia {iid} SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_DESISTENCIA}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de manter-a-glosa — violacao do L0 hard (ADR-0005)."
        )


async def _drive_to_analista(engine: EngineRest, probe: RecursoEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseRecursoAnalista surgir (dossie de Marina preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


def _desistencia_fields(*, analista: bool = True) -> dict[str, Any]:
    """Campos obrigatorios da desistencia humana (espelha o guard do worker register_desistencia)."""
    fields: dict[str, Any] = {
        "decisao_recurso": "NAO_RECORRER",
        "justificativa_desistencia": "Sem fundamento recursal apos analise (sintetico — teste L0)",
        "valor_glosa_aceito": "150.00",
        "referencia_contratual": "Clausula 12.3 do contrato de prestacao (sintetico)",
    }
    if analista:
        fields["analista_id"] = "analista-sintetico-001"
    return fields


def _parse_due(due: str) -> datetime:
    """dueDate do engine (ISO 8601, possivelmente com offset) -> datetime naive p/ comparacao."""
    return datetime.fromisoformat(due).replace(tzinfo=None)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs das DMNs)
# ===========================================================================


async def test_nenhum_caminho_automatizado_produz_desistencia(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado mantem a glosa (desiste/inadmite).

    Varredura de TODAS as combinacoes de input das DMNs:
      glosa_existe / dentro_prazo_recurso / documentacao_recurso_completa in {true, false}
      glosa_type in {administrativa, tecnica, clinica, linha_duplicada, formatacao}
      valor_glosado_brl em {baixo, medio, alto}
    (2*2*2 * 5 * 3 = 120 combinacoes). Cada instancia so avanca UMA rodada de `drain()` (sem
    completar UT humana) — nao toca nenhum dos 5 topicos nao-implementados (finding 2), permanece
    verde.
    """
    glosa_types = ["administrativa", "tecnica", "clinica", "linha_duplicada", "formatacao"]
    valores = ["150.00", "5000.00", "75000.00"]
    bools = [True, False]
    checked = 0

    for existe, prazo, docs, gtype, valor in itertools.product(bools, bools, bools, glosa_types, valores):
        inst = await start_recurso(
            glosa_existe=existe,
            dentro_prazo_recurso=prazo,
            documentacao_recurso_completa=docs,
            glosa_type=gtype,
            valor_glosado_brl=valor,
        )
        iid = inst["id"]
        await recurso_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO: existe={existe} prazo={prazo} docs={docs} gtype={gtype} valor={valor} "
            f"atingiu terminal adverso {adversos} automaticamente. ended={ended}"
        )
        await _assert_no_desistencia_without_human_task(engine, iid)
        checked += 1

    assert checked == 120, f"Esperava 120 combinacoes varridas; varri {checked}"


async def test_inadmissibilidade_aparente_roteia_para_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """dentro_prazo_recurso=false (inadmissibilidade procedural aparente — R5) => ANALISE_HUMANA."""
    for override in ({"dentro_prazo_recurso": False}, {"glosa_existe": False}):
        inst = await start_recurso(**override)
        iid = inst["id"]

        ut = await _drive_to_analista(engine, recurso_probe, iid)
        assert "analista-recurso-glosa" in ut.candidate_groups

        ended = await engine.activity_instances_ended(iid)
        assert not (ended & _ENDS_ADVERSOS), (
            f"Inadmissibilidade aparente ({override}) nao deve produzir terminal adverso automatico (L0)"
        )
        await _assert_no_desistencia_without_human_task(engine, iid)


async def test_inelegibilidade_roteia_para_humano_nao_nega(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_type=tecnica (merito clinico/tecnico) => recurso_eligibility -> medico-auditor.

    A DMN roteia grupo_revisor=medico-auditor; o fluxo chega a UT_AnaliseRecursoAnalista (entrada
    HITL — candidateGroups hardcoded no BPMN, independente do grupo_revisor da DMN), que pode
    ESCALAR_AUDITOR. NUNCA um fim adverso automatico.
    """
    inst = await start_recurso(glosa_type="tecnica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    assert "analista-recurso-glosa" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Glosa tecnica nao deve produzir terminal adverso automatico (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_RECURSO_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_recorrer_e_deferido(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """RECORRER => submit_appeal; msg.recurso.resposta_recebida (deferido) => reconcile + deferido.

    Fails FIRST at the `dossiers` assertion (kafka gap, finding 1 — analyze_request_entry never
    calls kafka.publish) — this fires BEFORE the instance ever completes UT_AnaliseRecursoAnalista
    with RECORRER. Independently, RECORRER -> ST_SubmitAppeal is ALSO a dead end
    (`_RECURSO_UNIMPLEMENTED_TOPIC_REASON`, finding 2) — moot here since the earlier assertion
    already fails.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    assert "analista-recurso-glosa" in ut.candidate_groups

    dossiers = recurso_probe.notifications_of_type("recurso.analyze_request")
    assert dossiers, "Worker analyze_request (Marina) deve ter sido executado"

    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "RECORRER"})
    await recurso_probe.drain()

    appeals = recurso_probe.notifications_of_type("recurso.submit_appeal")
    assert appeals, "Worker submit_appeal deve ser executado no RECORRER"

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.recurso.resposta_recebida",
        "businessKey": business_key,
        "processVariables": {"resposta_operadora": {"value": "deferido", "type": "String"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.recurso.resposta_recebida falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_DEFERIDO in ended, f"deferido => End_RecursoDeferido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="deferido")
    reconcilia = recurso_probe.notifications_of_type("recurso.reconcile_payment")
    assert reconcilia, "Worker reconcile_payment deve ser executado no deferimento"


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_happy_path_recurso_indeferido_pela_operadora(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """RECORRER; msg.recurso.resposta_recebida (indeferido) => indeferido (resposta de terceiro).

    RECORRER -> ST_SubmitAppeal has zero registered worker (finding 2) — the instance stalls
    there; the message correlation below has no waiting subscription to hit yet.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "RECORRER"})
    await recurso_probe.drain()

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.recurso.resposta_recebida",
        "businessKey": business_key,
        "processVariables": {"resposta_operadora": {"value": "indeferido", "type": "String"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204)

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_INDEFERIDO in ended, f"indeferido => End_RecursoIndeferido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="indeferido")
    assert not recurso_probe.notifications_of_type("recurso.register_desistencia")


async def test_happy_path_nao_recorrer_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """NAO_RECORRER com campos obrigatorios => register_desistencia; End_RecursoNaoInterposto.

    Unico caminho que mantem a glosa — e e humano. `_desistencia_fields()` seeds
    valor_glosa_aceito="150.00" (donor's BRL-as-string convention). Finding-3 (recurso.py:366)
    RESOLVED by t3.1-a2-recurso-valor-glosa: register_desistencia() now parses the monetary
    String via `_parse_valor_glosa_aceito` before the `<= 0` guard — LIVE-CONFIRMED the instance
    reaches End_RecursoNaoInterposto and emits _RECURSO_COMPLETED(desfecho="nao_interposto_humano").

    ADAPTED (t3.1-recurso-findings-a, finding 1a): the trailing
    `notifications_of_type('recurso.register_desistencia')` assert was STALE — that internal
    notification channel is structurally always-empty in v2 (register_desistencia_entry does
    `del kafka` and never publishes to it; unrelated to whether the desistencia registered).
    Asserting the human decisor's `analista_id` on the real, engine-observable
    `_RECURSO_COMPLETED` event (`ST_PublishNaoInterposto`'s own `event_payload_vars` carry
    `analista_id`) proves the SAME property — that register_desistencia executed AND carried the
    accountable human end-to-end — via a mechanism that actually works (events.py's real
    kafka.publish call site). No longer xfail.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, _desistencia_fields())
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_desistencia_without_human_task(engine, iid)
    assert _END_NAO_INTERPOSTO in ended, f"NAO_RECORRER humano => End_RecursoNaoInterposto. ended={ended}"
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="nao_interposto_humano", analista_id="analista-sintetico-001"
    ), "ST_PublishNaoInterposto deve emitir completed(desfecho=nao_interposto_humano) com o analista_id"


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_happy_path_escalar_auditor_mantem_recurso(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """ESCALAR_AUDITOR (glosa tecnica/clinica); auditor MANTER_RECURSO => submit_appeal; auditor_id.

    MANTER_RECURSO -> Flow_GWMerito_Manter -> ST_SubmitAppeal: same dead end as RECORRER
    (finding 2) — does not touch register_desistencia at all.
    """
    inst = await start_recurso(glosa_type="tecnica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut_auditor.candidate_groups

    await engine.complete_task_as_human(
        ut_auditor.id,
        {
            "decisao_auditor_recurso": "MANTER_RECURSO",
            "parecer_auditor": "Glosa tecnica improcedente — recurso mantido (sintetico)",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await recurso_probe.drain()

    appeals = recurso_probe.notifications_of_type("recurso.submit_appeal")
    assert appeals, "Worker submit_appeal deve ser executado no MANTER_RECURSO do auditor"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "MANTER_RECURSO nunca mantem a glosa (interpoe o recurso)"
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_happy_path_auditor_aceita_glosa_mantem_glosa_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """ESCALAR_AUDITOR; auditor ACEITAR_GLOSA com campos => End_GlosaMantida (humano-gated).

    Finding-3 (the valor_glosa_aceito='150.00' TypeError) was RESOLVED by
    t3.1-a2-recurso-valor-glosa; finding 4 (auditor ACEITAR_GLOSA unrecognized by the guard) is
    now ALSO RESOLVED by t3.1-recurso-findings-a: `RecursoDesistenciaInput` carries
    `decisao_auditor_recurso`/`auditor_id`, and `register_desistencia()`'s guard accepts the
    auditor channel (`decisao_auditor_recurso == ACEITAR_GLOSA` + `auditor_id`) alongside the
    analista channel — End_GlosaMantida is now reachable via the auditor path.

    ADAPTED (t3.1-recurso-findings-a, finding 1a — same as test_happy_path_nao_recorrer_humano):
    the trailing `notifications_of_type('recurso.register_desistencia')` assert was STALE (that
    internal channel is structurally always-empty in v2). Asserting the human decisor's
    `auditor_id` on the real `_RECURSO_COMPLETED` event (`ST_PublishGlosaMantida`'s own
    `event_payload_vars` carry `auditor_id`) proves the same property via a mechanism that
    actually works. No longer xfail.
    """
    inst = await start_recurso(glosa_type="clinica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(
        ut_auditor.id,
        {
            "decisao_auditor_recurso": "ACEITAR_GLOSA",
            "parecer_auditor": "Glosa clinica procedente — glosa mantida (sintetico)",
            "justificativa_desistencia": "Merito tecnico-clinico confirma a glosa (sintetico)",
            "valor_glosa_aceito": "150.00",
            "referencia_contratual": "Diretriz clinica DUT (sintetico)",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_desistencia_without_human_task(engine, iid)
    assert _END_GLOSA_MANTIDA in ended, f"auditor ACEITAR_GLOSA => End_GlosaMantida. ended={ended}"
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="nao_interposto_humano", auditor_id="auditor-sintetico-001"
    ), "ST_PublishGlosaMantida deve emitir completed(desfecho=nao_interposto_humano) com o auditor_id"


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_recurso_parcialmente_deferido(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """RECORRER; msg.recurso.resposta_recebida (parcialmente_deferido) => conciliacao parcial.

    RECORRER -> ST_SubmitAppeal: same dead end (finding 2).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "RECORRER"})
    await recurso_probe.drain()

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.recurso.resposta_recebida",
        "businessKey": business_key,
        "processVariables": {"resposta_operadora": {"value": "parcialmente_deferido", "type": "String"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204)

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_PARCIAL in ended, f"parcial => End_RecursoParcial. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="parcialmente_deferido")


# ===========================================================================
# NAO_RECORRER exige campos / worker guard
# ===========================================================================


async def test_nao_recorrer_exige_campos_obrigatorios(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """NAO_RECORRER sem justificativa/valor/referencia/analista_id => worker guard recusa.

    O engine despacha register_desistencia; o worker lanca DesistenciaNotHumanError (defesa em
    profundidade); a instancia NAO atinge nenhum terminal adverso (incidente no worker). Nao
    seta valor_glosa_aceito (fica no default float 0.0 do dataclass) — NAO tropeca no bug de tipo
    da finding 3 (so ocorre quando o campo e setado como string nao-vazia).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "NAO_RECORRER"})
    await recurso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), (
        "NAO_RECORRER sem campos obrigatorios NAO pode atingir terminal adverso (guard do worker)"
    )
    assert not recurso_probe.notifications_of_type("recurso.register_desistencia"), (
        "register_desistencia NAO deve registrar sem campos obrigatorios"
    )
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_worker_guard_register_desistencia_recusa_sem_humano() -> None:
    """Invocacao direta do entry function register_desistencia_entry sem decisao humana =>
    `DesistenciaNotHumanError` (guard). Unit-style sobre o handler real (SEM engine).

    ADAPTED (port rule 1, verify each register_*/entry-function shape on v2 main): v2's
    `register_desistencia_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary, no
    `ExternalTask`) replaces donor's `make_register_desistencia_handler(kafka) ->
    Callable[[ExternalTask], ...]` (that donor factory does not exist on v2 main). The guard
    raises `DesistenciaNotHumanError` (a `PermissionError` subclass — harness routes it straight
    to an incident) rather than donor's `WorkerBpmnError(error_code=...)`.

    Scenarios (a)-(b) exercise missing/wrong decisao_recurso (valor_glosa_aceito never set ->
    stays the dataclass's float 0.0 default). Scenarios (c)-(d) set valor_glosa_aceito="150.00"
    (donor's BRL-as-string convention): finding-3 (recurso.py:366 TypeError) is RESOLVED by
    t3.1-a2-recurso-valor-glosa — the monetary String is parsed via `_parse_valor_glosa_aceito`
    before the `<= 0` guard, so (c) refuses for the RIGHT reason (missing analista_id, not a
    crash) and (d) registers cleanly. The auditor ACEITAR_GLOSA path (former scenario (e)) is now
    ALSO resolved (finding 4, t3.1-recurso-findings-a) and lives in its own passing test below.
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao ausente -> recusa
    with pytest.raises(DesistenciaNotHumanError) as exc_a:
        register_desistencia_entry({}, kafka=kafka)
    assert "ERR_DESISTENCIA_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_recurso != NAO_RECORRER -> recusa
    with pytest.raises(DesistenciaNotHumanError) as exc_b:
        register_desistencia_entry({"decisao_recurso": "RECORRER"}, kafka=kafka)
    assert "ERR_DESISTENCIA_NOT_HUMAN" in str(exc_b.value)

    # (c) NAO_RECORRER mas faltando identidade do decisor (analista_id) -> recusa pela ausencia de
    # analista_id (valor_glosa_aceito="150.00" agora e parseado, NAO crasha; finding-3 resolvida).
    with pytest.raises(DesistenciaNotHumanError) as exc_c:
        register_desistencia_entry(
            {
                "decisao_recurso": "NAO_RECORRER",
                "justificativa_desistencia": "x",
                "valor_glosa_aceito": "150.00",
                "referencia_contratual": "clausula 12.3",
            },
            kafka=kafka,
        )
    assert "ERR_DESISTENCIA_NOT_HUMAN" in str(exc_c.value)
    assert "analista_id" in str(exc_c.value)
    assert "valor_glosa_aceito" not in str(exc_c.value), (
        "valor_glosa_aceito='150.00' e valido (>0) — a recusa e por analista_id ausente"
    )
    assert not kafka.published, "Nenhum registro deve ser publicado quando o guard recusa"

    # (d) decisao humana completa (analista) -> registra e carrega analista_id
    result_d = register_desistencia_entry(
        {
            "decisao_recurso": "NAO_RECORRER",
            "justificativa_desistencia": "Sem fundamento recursal (teste)",
            "valor_glosa_aceito": "150.00",
            "referencia_contratual": "Clausula 12.3",
            "analista_id": "analista-sintetico-001",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-TESTE-0001",
            "glosa_id": "GLOSA-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_d["registered"] is True
    assert result_d["protocolo"]


async def test_worker_guard_register_desistencia_auditor_path_finding4() -> None:
    """Auditor ACEITAR_GLOSA (former scenario (e) of the guard test) — donor espera registro.

    Finding-3 (the valor_glosa_aceito='150.00' TypeError) was RESOLVED by
    t3.1-a2-recurso-valor-glosa; finding 4 (register_desistencia never recognized
    `decisao_auditor_recurso=='ACEITAR_GLOSA'` nor an `auditor_id` field) is now ALSO RESOLVED by
    t3.1-recurso-findings-a: `RecursoDesistenciaInput` carries both fields, and the guard accepts
    this as its own auditor channel (alongside the pre-existing analista channel) — registers
    cleanly instead of refusing. No longer xfail.
    """
    kafka = FakeKafkaPublisher()
    result_e = register_desistencia_entry(
        {
            "decisao_auditor_recurso": "ACEITAR_GLOSA",
            "justificativa_desistencia": "Merito clinico confirma glosa (teste)",
            "valor_glosa_aceito": "150.00",
            "referencia_contratual": "Diretriz DUT",
            "auditor_id": "auditor-sintetico-001",
        },
        kafka=kafka,
    )
    assert result_e["registered"] is True
    assert result_e["protocolo"]


# ===========================================================================
# Boundary-error catches (GAP-RECURSO-3, dangling-catch fix no donor) — guards TECNICOS terminam
# LIMPO, nunca travam.
# ===========================================================================


@pytest.mark.xfail(reason=_RECURSO_INVALID_GLOSA_GUARD_MISSING_REASON, strict=True)
async def test_glosa_id_ausente_pendencia_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_id ausente + PENDENTE_DOCUMENTACAO => ST_SolicitarDocumentos deveria lancar
    ERR_RECURSO_INVALID_GLOSA (validacao de origem) e o boundary catch BE_GlosaInvalidaDocs
    deveria terminar a instancia LIMPO em End_RecursoGlosaInvalidaOrigem. v2 nunca valida
    glosa_id (finding 5) — a instancia so segue normalmente para GW_AguardarDocs.
    """
    inst = await start_recurso(
        glosa_id="",
        numero_guia_tiss=f"GUIA-TESTE-INVALIDO-DOCS-{uuid.uuid4().hex[:8]}",
        documentacao_recurso_completa=False,
    )
    iid = inst["id"]

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_GLOSA_INVALIDA_ORIGEM in ended, (
        f"glosa_id ausente (ramo PENDENTE_DOCUMENTACAO) deve terminar LIMPO em "
        f"{_END_GLOSA_INVALIDA_ORIGEM} (boundary catch BE_GlosaInvalidaDocs), nao travar "
        f"silenciosamente sem end event. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso (L0)"
    assert not recurso_probe.notifications_of_type("recurso.request_documents"), (
        "Guard deve recusar ANTES de abrir a pendencia (glosa_id ausente)"
    )
    assert not recurso_probe.has_event(_RECURSO_PENDED), "Guard recusa antes de publicar recurso.pended"
    assert not recurso_probe.notifications_of_type("recurso.register_desistencia")
    await _assert_no_desistencia_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_RECURSO_INVALID_GLOSA_GUARD_MISSING_REASON, strict=True)
async def test_glosa_id_ausente_analise_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_id ausente + SEGUE_ANALISE => ST_PrepararDossie deveria lancar
    ERR_RECURSO_INVALID_GLOSA; BE_GlosaInvalidaDossie deveria terminar em
    End_RecursoGlosaInvalidaOrigem. v2 nunca valida glosa_id (finding 5).
    """
    inst = await start_recurso(
        glosa_id="",
        numero_guia_tiss=f"GUIA-TESTE-INVALIDO-DOSSIE-{uuid.uuid4().hex[:8]}",
        documentacao_recurso_completa=True,
    )
    iid = inst["id"]

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_GLOSA_INVALIDA_ORIGEM in ended, (
        f"glosa_id ausente (ramo SEGUE_ANALISE) deve terminar LIMPO em "
        f"{_END_GLOSA_INVALIDA_ORIGEM} (boundary catch BE_GlosaInvalidaDossie), nao travar "
        f"silenciosamente sem end event. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso (L0)"
    assert not recurso_probe.notifications_of_type("recurso.analyze_request"), (
        "Guard deve recusar ANTES de convocar Marina/preparar o dossie (glosa_id ausente)"
    )
    assert not recurso_probe.notifications_of_type("recurso.register_desistencia")
    await _assert_no_desistencia_without_human_task(engine, iid)


# ===========================================================================
# Pendencia de documentacao
# ===========================================================================


@pytest.mark.xfail(reason=_RECURSO_KAFKA_GAP_REASON, strict=True)
async def test_pendencia_docs_recebidos_reavalia(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """documentacao_recurso_completa=false => PENDENTE_DOCUMENTACAO; docs_received => reavalia.

    Fails at `has_event(_RECURSO_PENDED)` — ST_SolicitarDocumentos's embedded event_topic_pended
    publish never happens (finding 1).
    """
    inst = await start_recurso(documentacao_recurso_completa=False)
    iid = inst["id"]

    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_PENDED), "recurso.pended deve ser publicado na pendencia"
    req_docs = recurso_probe.notifications_of_type("recurso.request_documents")
    assert req_docs, "Worker request_documents deve ser executado"

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.recurso.docs_received",
        "businessKey": business_key,
        "processVariables": {"documentacao_recurso_completa": {"value": True, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.recurso.docs_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await recurso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISTA)
    assert "analista-recurso-glosa" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Reavaliacao por mensagem nunca auto-desiste (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_pendencia_expira_decisao_humana(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Prazo de pendencia (ICE_PrazoPendencia P5D) expira => UT_AnaliseRecursoAnalista (humano decide)."""
    inst = await start_recurso(documentacao_recurso_completa=False)
    iid = inst["id"]

    await recurso_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISTA)
    assert "analista-recurso-glosa" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Pendencia expirada nunca auto-desiste (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA (auto-approve-on-timeout INVERTIDO)
# ===========================================================================


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaRecurso (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta.

    ST_NotificarRiscoSla (topic notify_sla_risk) has zero registered worker (finding 2) — the
    task is fetched by nobody.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    sla_alerts = recurso_probe.notifications_of_type("recurso.notify_sla_risk")
    assert sla_alerts, "Worker notify_sla_risk deve ser executado no alerta de SLA"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume_nao_auto_aprova(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnaliseRecurso (interruptivo): UT_AnaliseRecursoAnalista cancelada; coordenacao assume.

    recurso.sla_breached (fase=analise) publicado via ST_PublishSlaBreach (generic
    operadora.events.publish, real kafka.publish call site — works). NAO ha auto-aprovacao/
    auto-desistencia por timeout.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="analise"), (
        "recurso.sla_breached (fase=analise) deve ser publicado"
    )

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-recurso" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "UT_AnaliseRecursoAnalista deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-desiste (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_coordenacao_assume_e_mantem_glosa_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e NAO_RECORRER => End_RecursoNaoInterposto com UT humana.

    Prova que mesmo no escalonamento manter-a-glosa passa por UT humana (invariante). O payload
    seta valor_glosa_aceito="150.00" (string); finding-3 RESOLVIDA
    (t3.1-a2-recurso-valor-glosa) — o valor monetario e parseado antes do guard `<= 0`, entao a
    instancia atinge End_RecursoNaoInterposto.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_recurso": "NAO_RECORRER",
            "justificativa_desistencia": "Prazo esgotado — coordenacao mantem glosa (sintetico)",
            "valor_glosa_aceito": "150.00",
            "referencia_contratual": "Clausula 12.3 (sintetico)",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NAO_INTERPOSTO in ended
    await _assert_no_desistencia_without_human_task(engine, iid)
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="nao_interposto_humano")


async def test_coordenacao_inadmissivel_humano_gated(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao decide inadmissibilidade humana => End_RecursoInadmissivel (gated).

    Prova que End_RecursoInadmissivel e humano-gated (review gap pinado). O payload seta
    valor_glosa_aceito="150.00" (string); finding-3 RESOLVIDA (t3.1-a2-recurso-valor-glosa) — o
    valor e parseado antes do guard `<= 0`. LIVE-CONFIRMED: a instancia atinge
    End_RecursoInadmissivel e emite _RECURSO_COMPLETED(desfecho="inadmissivel").

    ADAPTADO (t3.1-recurso-findings-a, finding 1a — mesmo padrao de
    test_happy_path_nao_recorrer_humano): o assert final `notifications_of_type(
    'recurso.register_desistencia')` era STALE (canal interno estruturalmente sempre-vazio em
    v2). Asserir o `analista_id` do decisor humano (aqui a propria coordenacao, que reusa o campo
    `analista_id`) no evento `_RECURSO_COMPLETED` real (`ST_PublishInadmissivel`'s
    `event_payload_vars` carregam `analista_id`) prova a mesma propriedade por um mecanismo que
    de fato funciona. Nao mais xfail.
    """
    inst = await start_recurso(glosa_type="administrativa", dentro_prazo_recurso=False)
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_recurso": "NAO_RECORRER",
            "desfecho_humano": "inadmissivel",
            "justificativa_desistencia": "Recurso fora do prazo recursal — inadmissivel (sintetico)",
            "valor_glosa_aceito": "150.00",
            "referencia_contratual": "RN 424/2017 prazo recursal (sintetico DRAFT)",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_desistencia_without_human_task(engine, iid)
    assert _END_INADMISSIVEL in ended, f"inadmissibilidade humana => End_RecursoInadmissivel. ended={ended}"
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="inadmissivel", analista_id="coordenacao-sintetica-001"
    ), "ST_PublishInadmissivel deve emitir completed(desfecho=inadmissivel) com o analista_id"


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_loop_acompanhamento_limitado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Loop ICE_AguardarResposta (P5D) limitado (loop_counter < 6); ao exceder => coordenacao humana.

    RECORRER -> ST_SubmitAppeal never completes (finding 2), so ICE_AguardarResposta's timer is
    never even created; the retry loop below breaks on the very first `await_timer_job` timeout.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "RECORRER"})
    await recurso_probe.drain()

    for _ in range(8):
        try:
            job = await engine.await_timer_job(iid, "ICE_AguardarResposta", attempts=10)
        except Exception:
            break
        await engine.execute_job(job.id)
        await recurso_probe.drain()
        if not await engine.instance_is_active(iid):
            break
        open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
        if _UT_COORDENACAO in open_keys:
            break

    tracks = recurso_probe.notifications_of_type("recurso.track_status")
    assert tracks, "Worker track_status deve ser reexecutado no loop"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-recurso" in ut_coord.candidate_groups
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max"), (
        "loop excedido publica recurso.sla_breached (fase=prazo_max)"
    )
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Loop excedido nunca auto-desiste (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_prazo_max_recurso_escala_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_PrazoMaxRecurso (prazo_regulatorio): escalate_ans_timeout; UT_EscalonamentoPrazo.

    ST_EscalateAnsTimeout has zero registered worker (finding 2).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    escalations = recurso_probe.notifications_of_type("recurso.escalate_ans_timeout")
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max") or escalations, (
        "escalate_ans_timeout deve publicar recurso.sla_breached (fase=prazo_max)"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro do prazo max nunca auto-desfecho adverso (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


# ===========================================================================
# GAP-RECURSO-1 — teto P30D (RN 424): UMA UNICA JANELA ABSOLUTA em toda UT humana
# ===========================================================================


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_prazo_max_ancora_absoluta_nao_no_attach_da_ut(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: o teto P30D ancora em data_recebimento_recurso_iso, NAO no attach da UT.

    The due-date-vs-anchor assertions (first half, before `execute_job`) are genuinely
    independent of finding 2 and would pass on their own merit (BRT_Sla's DMN evaluation is
    unaffected by the missing worker) — but `execute_job` transitions to ST_EscalateAnsTimeout,
    which has zero registered worker (finding 2), so the whole function is marked xfail.
    """
    anchor_date = (datetime.now(UTC) + timedelta(days=40)).date()
    anchor_midnight = datetime.combine(anchor_date, datetime.min.time())

    inst = await start_recurso(
        glosa_type="administrativa",
        data_recebimento_recurso_iso=anchor_date.isoformat(),
    )
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.due_date, "dueDate do teto deve estar presente (timeDate absoluto)"

    due = _parse_due(job_max.due_date)
    expected_anchor_based = anchor_midnight + timedelta(days=30)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    legacy_attach_based = now_naive + timedelta(days=30)

    tolerance = timedelta(hours=6)
    far_enough = timedelta(days=20)

    assert abs(due - expected_anchor_based) < tolerance, (
        f"BT_PrazoMaxRecurso.dueDate={due} nao bate com ancora+P30D={expected_anchor_based} "
        "(GAP-RECURSO-1: teto deve ser absoluto, ancorado em data_recebimento_recurso_iso)"
    )
    assert abs(due - legacy_attach_based) > far_enough, (
        "BT_PrazoMaxRecurso.dueDate esta perto de attach-da-UT+P30D — regressao GAP-RECURSO-1"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max")
    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_prazo_max_fail_safe_ancora_data_ciencia_glosa(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1 fail-safe: sem data_recebimento_recurso_iso, a ancora e data_ciencia_glosa.

    Read-only (nao executa o job) — nao toca ST_EscalateAnsTimeout, permanece verde.
    """
    ciencia_date = (datetime.now(UTC) + timedelta(days=35)).date()
    ciencia_midnight = datetime.combine(ciencia_date, datetime.min.time())

    inst = await start_recurso(
        glosa_type="administrativa",
        data_ciencia_glosa=ciencia_date.isoformat(),
        data_recebimento_recurso_iso=None,
    )
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.due_date, "dueDate do teto deve estar presente mesmo sem a ancora primaria"

    due = _parse_due(job_max.due_date)
    expected = ciencia_midnight + timedelta(days=30)
    assert abs(due - expected) < timedelta(hours=6), (
        f"Fail-safe: BT_PrazoMaxRecurso.dueDate={due} deveria ancorar em "
        f"data_ciencia_glosa+P30D={expected} quando data_recebimento_recurso_iso esta ausente"
    )


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_prazo_max_coord_mesmo_instante_absoluto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: BT_SlaAnaliseRecurso mata BT_PrazoMaxRecurso; BT_PrazoMaxCoord reanexa o teto
    no MESMO instante absoluto (nunca janela nova).

    The due-date-equality proof (before the final `execute_job`) is independent of finding 2 —
    only the trailing execution (-> ST_EscalateAnsTimeout) is blocked.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_original = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_original.due_date, "teto original deve ter dueDate (timeDate absoluto)"

    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job_sla.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-recurso" in ut_coord.candidate_groups

    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxCoord")
    assert job_max.due_date, "teto reanexado deve ter dueDate (timeDate absoluto)"
    assert _parse_due(job_max.due_date) == _parse_due(job_original.due_date), (
        f"BT_PrazoMaxCoord.dueDate={job_max.due_date} != BT_PrazoMaxRecurso.dueDate="
        f"{job_original.due_date} — a escalada NAO pode reiniciar a janela do teto (GAP-RECURSO-1)"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max"), (
        "BT_PrazoMaxCoord deve publicar recurso.sla_breached (fase=prazo_max), igual ao teto original"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Teto reanexado na coordenacao nunca auto-desfecho adverso (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_prazo_max_auditor_mesmo_instante_absoluto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: ESCALAR_AUDITOR roteia sem teto proprio; BT_PrazoMaxAuditor reanexa o teto
    no MESMO instante absoluto (o hop analista->auditor nao estende o teto RN 424).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    job_original = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_original.due_date, "teto original deve ter dueDate (timeDate absoluto)"

    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut_auditor.candidate_groups

    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxAuditor")
    assert job_max.due_date, "teto no auditor deve ter dueDate (timeDate absoluto)"
    assert _parse_due(job_max.due_date) == _parse_due(job_original.due_date), (
        f"BT_PrazoMaxAuditor.dueDate={job_max.due_date} != BT_PrazoMaxRecurso.dueDate="
        f"{job_original.due_date} — o hop ao auditor NAO pode reiniciar a janela (GAP-RECURSO-1)"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max"), (
        "BT_PrazoMaxAuditor deve publicar recurso.sla_breached (fase=prazo_max), igual ao teto original"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR in open_keys, "BT_PrazoMaxAuditor e nao-interruptivo (cancelActivity=false)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Teto reanexado no auditor nunca auto-desfecho adverso (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_RECURSO_UNIMPLEMENTED_TOPIC_REASON, strict=True)
async def test_prazo_max_escalonamento_sem_cascata(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1 (guard adversarial): UT_EscalonamentoPrazo NAO carrega boundary de teto.

    Executes BT_PrazoMaxRecurso immediately -> ST_EscalateAnsTimeout, which has zero registered
    worker (finding 2): UT_EscalonamentoPrazo never appears.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_first = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job_first.id)
    await recurso_probe.drain()

    await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max")
    breaches_before = len(
        [e for e in recurso_probe.events_on(_RECURSO_SLA_BREACHED) if e["payload"].get("fase") == "prazo_max"]
    )

    pending = {job.activity_id for job in await engine.list_timer_jobs(iid)}
    assert "BT_PrazoMaxEscalonamento" not in pending, (
        "UT_EscalonamentoPrazo NAO pode carregar boundary de teto (timeDate ja-passado => cascata)"
    )
    assert pending <= {"BT_AlertaSlaRecurso", "BT_SlaAnaliseRecurso"}, (
        f"jobs de timer inesperados apos o estouro do teto: {pending}"
    )

    await recurso_probe.drain(rounds=6)
    open_esc = [t for t in await engine.list_user_tasks(iid) if t.task_definition_key == _UT_ESCALONAMENTO]
    assert len(open_esc) == 1, f"cascata detectada: {len(open_esc)} UT_EscalonamentoPrazo abertas"
    breaches_after = len(
        [e for e in recurso_probe.events_on(_RECURSO_SLA_BREACHED) if e["payload"].get("fase") == "prazo_max"]
    )
    assert breaches_after == breaches_before, "sla_breached (prazo_max) re-publicado sem novo estouro"

    assert await engine.instance_is_active(iid)
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro do teto nunca auto-desfecho adverso (L0)"
    await _assert_no_desistencia_without_human_task(engine, iid)


async def test_dmn_recurso_sla_valores(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_type=clinica, valor alto => DMN recurso_sla resolve sla_analise (ISO); timers existem."""
    inst = await start_recurso(glosa_type="clinica", valor_glosado_brl="75000.00")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaRecurso")
    assert job.activity_id == "BT_AlertaSlaRecurso"
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    assert job_sla.activity_id == "BT_SlaAnaliseRecurso"
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.activity_id == "BT_PrazoMaxRecurso"


# ===========================================================================
# Roteamento DMN (catch-all fail-safe) — sem engine, varredura estatica do XML
# ===========================================================================


def test_dmn_admissibility_sem_saida_adversa() -> None:
    """recurso_admissibility roteia EXATAMENTE {SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISSIBILITY)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    roteamentos: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_rule_first_output = val

    assert roteamentos == {"SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"}, (
        f"dominio inesperado: {roteamentos} — NAO pode conter NEGAR/DESISTIR/INADMISSIVEL (L0)"
    )
    joined = " ".join(roteamentos)
    assert "NEGAR" not in joined and "DESIST" not in joined and "INADMISS" not in joined
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_eligibility_glosa_tecnica_vai_ao_auditor() -> None:
    """recurso_eligibility roteia EXATAMENTE {RECORRIVEL, ANALISE_HUMANA}; tecnica/clinica -> auditor."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ELIGIBILITY)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    roteamentos: set[str] = set()
    grupos: set[str] = set()
    last_roteamento: str | None = None
    tecnica_grupo: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert len(outputs) >= 2
        rot_text = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        grp_text = next((c for c in outputs[1] if _local(c.tag) == "text"), None)
        assert rot_text is not None and rot_text.text
        assert grp_text is not None and grp_text.text
        rot = rot_text.text.strip().strip('"')
        grp = grp_text.text.strip().strip('"')
        roteamentos.add(rot)
        grupos.add(grp)
        last_roteamento = rot
        inputs = [c for c in rule if _local(c.tag) == "inputEntry"]
        first_in = next((c for c in inputs[0] if _local(c.tag) == "text"), None)
        if first_in is not None and first_in.text and "tecnica" in first_in.text:
            tecnica_grupo = grp

    assert roteamentos == {"RECORRIVEL", "ANALISE_HUMANA"}, (
        f"dominio inesperado: {roteamentos} — NAO pode conter NAO_RECORRIVEL/NEGAR (L0)"
    )
    assert "NAO_RECORRIVEL" not in " ".join(roteamentos)
    assert grupos == {"analista-recurso-glosa", "medico-auditor"}
    assert tecnica_grupo == "medico-auditor", "glosa tecnica/clinica deve rotear a medico-auditor"
    assert last_roteamento == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_ADMISSIBILITY, _DMN_ELIGIBILITY, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


def test_todos_os_fins_emitem_evento_de_dominio() -> None:
    """Cada end event do BPMN e precedido por uma service task de publish recurso.completed."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    desfechos: set[str] = set()
    for el in root.iter():
        if _local(el.tag) == "inputParameter" and el.get("name") == "event_desfecho" and el.text:
            desfechos.add(el.text.strip())

    esperados = {
        "deferido",
        "indeferido",
        "parcialmente_deferido",
        "inadmissivel",
        "nao_interposto_humano",
    }
    assert esperados <= desfechos, (
        f"Desfechos faltando no BPMN: {esperados - desfechos} "
        "(todos os fins devem publicar recurso.completed)"
    )


def test_recurso_worker_registry_drift_vs_bpmn() -> None:
    """Registry drift (finding 2) — static, no engine needed. Documents the exact drift sets so a
    future accidental registration change gets caught here (rather than silently in a live run).

    Orphans: registered by `register_recurso_workers` but no BPMN service task ever creates a
    task for the topic (dead code from the engine's perspective — BRT_Admissibilidade/
    BRT_Eligibility evaluate their DMNs directly via `camunda:decisionRef`, bypassing
    `assess_eligibility_entry`/`validate_recurso_entry` entirely; `GW_DecisaoRecurso`'s
    ESCALAR_AUDITOR branch has no service task; `publish_completed_entry` folds into the generic
    `operadora.events.publish`, by design).

    Gaps: BPMN declares the topic, `register_recurso_workers` has no handler for it at all — the
    external task is fetched by nobody and the instance stalls there.
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    camunda_ns = "{http://camunda.org/schema/1.0/bpmn}"
    bpmn_topics = {
        el.get(f"{camunda_ns}topic")
        for el in tree.getroot().iter()
        if el.get(f"{camunda_ns}topic") is not None
    }
    bpmn_recurso_topics = {t for t in bpmn_topics if t.startswith("operadora.recurso.")}

    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(transport, worker_id="static-registry-drift-check")
    register_recurso_workers(harness, FakeKafkaPublisher())
    registered_recurso_topics = {t for t in harness.registered_topics if t.startswith("operadora.recurso.")}

    orphans = registered_recurso_topics - bpmn_recurso_topics
    gaps = bpmn_recurso_topics - registered_recurso_topics

    assert orphans == _RECURSO_ORPHAN_TOPICS, (
        f"registered-but-not-in-BPMN drift changed (update _RECURSO_ORPHAN_TOPICS): {orphans}"
    )
    assert gaps == _RECURSO_UNIMPLEMENTED_TOPICS, (
        f"BPMN-declared-but-unregistered drift changed (update _RECURSO_UNIMPLEMENTED_TOPICS): {gaps}"
    )


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_glosa(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa."""
    glosa = "GLOSA-TESTE-IDEM-001"
    guia = "GUIA-TESTE-0001"
    business_key = f"RECURSO-amh-{guia}-{glosa}"

    first = await start_recurso(numero_guia_tiss=guia, glosa_id=glosa, glosa_type="tecnica")
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]
