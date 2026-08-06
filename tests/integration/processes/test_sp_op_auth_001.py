"""SP-OP-AUTH-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 1
(V2-COMPLETION-PLAN §3). auth.py is NOT one of ADR-0028's 9 migrated modules — its ceilings
come from the T1.9 `CeilingResolver` (policy YAML), not a DMN-fork, so it does not race the
in-flight T1.5 DMN cutover.

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-AUTH-001.md) contra o
engine real (ADR-0011: sem mock de engine). Cada teste:

1. inicia a instancia via REST com business key `AUTH-amh-GUIA-TESTE-{N}`;
2. drena as external tasks com o `auth_probe` (workers reais Phase-1 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que negativa passa pela UT).

Dados sinteticos obvios: `Paciente Teste NNN`, tenant `amh`, guias `GUIA-TESTE-NNNN`.
Process key: SP-OP-AUTH-001 (exato — nao alterar).
Business key: AUTH-amh-{numero_guia_tiss} (contrato SP-OP-AUTH-001.md).

## Invariante Phase-1 (DoD deliverable)

test_invariant_nenhum_caminho_automatizado_produz_negativa:
  Para toda combinacao de inputs DMN possivel, a instancia NUNCA atinge End_NegadaAuditor
  sem que UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta tenha
  sido completada por humano. A prova e feita consultando a historia do engine:
  - history/activity-instance do End_NegadaAuditor
  - history/activity-instance das User Tasks humanas obrigatorias
  Se End_NegadaAuditor esta no historico sem nenhuma UT humana -> falha (guardraill L0).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.auth`/`harness`; `FakeKafkaPublisher` moved to
    `harness.py` (T1.1). `phi_vars.REDACTED_PHI` DOES exist on v2 main
    (`src/maezo/tools/workers/phi_vars.py`) — see findings 3 and 5; item-9 auth Class-A RESTORED
    the import and the donor's PHI-redaction assertions in `test_happy_path_negada_pelo_auditor`,
    which now prove one-way redaction in the engine's own variable store.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `_AUTH_WORKER_TOPICS` / `_AnalyzeRequestStub` / `drain_analyze()` kept VERBATIM: v1's
    `operadora.auth.analyze_request` is deliberately excluded from the generic drain (donor
    rationale preserved in the stub's docstring) — this is a donor FIXTURE choice (which topic
    the harness-level probe serves vs. which topic a dedicated stub serves), not a test
    assertion, so it is not touched even though v2's `register_auth_workers` happens to also
    register a real `AnalyzeRequestWorker` for that same topic.
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`); `drain_analyze()`'s own inline fetch loop is
    adapted the same way (topics wrapped in `TopicSubscription`, `async_response_timeout_ms`
    instead of `lock_duration_ms` on the call).

FINDINGS (see PR body / evidence-ledger for full detail):
  0. ROOT CAUSE, FIXED (T3.1 R2): this suite originally documented that grep for `kafka.publish(`
     across all 16 `src/maezo/tools/workers/*.py` modules returned ZERO call sites (v2's own
     `worker_runtime/service.py` `kafka_ready` readiness check said so explicitly). T3.1 R2 ports
     the donor's `make_publish_event_handler` into `maezo.tools.workers.events
     .register_events_workers` (a 17th bootstrap) — `auth_probe` now registers it too, mirroring
     the donor's own `register_phase0_workers` composition.
  1. `operadora.events.publish` gap, FIXED (T3.1 R2) — same fix as finding 0 (this suite
     originally tracked it as a separate finding since it blocks EVERY test that expects the
     process to progress past its first `ST_Publish*` node; auth's BPMN declares the identical
     `camunda:topic="operadora.events.publish"` service tasks as escalation's). `strict-xfail`
     markers whose documented reason was exactly this gap are REMOVED below.
  2. D-07 (open governance item, STILL OPEN — NOT fixed here, src/** out of scope for this PR):
     tenant `amh`'s `authorization_approval.max_value_brl` is `0` in both
     `spec/policies/autonomy/L0-core.yaml` and `tenants-amh.yaml` ("teto REAL e definido por
     tenant ... D-07 em aberto"). `CeilingResolver.within_l2_ceiling` (T1.9,
     `src/maezo/tools/workers/ceilings.py`) is fail-closed on a `0` ceiling: `AnalyzeRequestWorker`
     (`auth.py`) can therefore never compute `teto_ok=True` for any positive
     `valor_estimado_brl`, so `End_AprovadaAutomatica` (L2 auto-approval) is UNREACHABLE under
     the current tenant policy config — a genuine, documented v2 config gap, independent of the
     (now-fixed) publish-topic gap, affecting the 2 tests that assert the auto-approval terminal;
     those two tests are no longer xfailed — item-9 auth Class-A
     removed the last markers in this file (D-07 never had its own constant here; it was prose
     only). The config gap itself is unchanged and human-gated (D-07, finance).
  3. `phi_vars.REDACTED_PHI`, FIXED (T3.1 auth-denial-hardening, THIS branch): v2 now has
     `maezo.tools.workers.phi_vars` (one-way, class-token port of the donor's module — no
     reversible pseudonymizer branch at this engine/Kafka-facing edge), and
     `SendDenialNoticeWorker.execute()` (`auth.py`) redacts
     `justificativa_clinica`/`cid10_referencia`/`fundamentacao_dut` via `redact_phi_vars` before
     any variable leaves the worker (GAP-XPHI-1 lineage, ADR-0006). Same branch also implements
     the `ERR_AUTH_DENIAL_INCOMPLETE` completeness guard, unit-proven
     (tests/unit/tools/workers/test_auth_denial_guard.py, test_phi_vars.py) AND now live-verified
     end-to-end by `test_negativa_incompleta_bloqueada_pelo_guard` below (T3.1 R3: former
     strict-xfail XPASSED against a real CIB Seven engine — boundary catch, blocked notice, empty
     incident — marker flipped, test now runs as a normal pass).
     `test_happy_path_negada_pelo_auditor`'s redaction assertion is ASSERTED again as of item-9
     auth Class-A (finding 5 below): it was re-expressed OFF the dead notification channel and
     ONTO engine history. `SendDenialNoticeWorker.execute()` returns `redact_phi_vars(notice)`,
     and the harness writes a worker's whole return dict back as PROCESS VARIABLES
     (`WorkerHarness._handle` -> `transport.complete(..., dict(out_vars))`), so the redacted
     clinical fields OVERWRITE the raw values the User Task set — making
     `engine.get_history_variable(iid, "cid10_referencia") == REDACTED_PHI` a stronger proof of
     the one-way redaction than the donor's probe echo ever was (the engine's own record, not a
     copy the worker handed the fake publisher).
  4. T3.1 Wave 1 remedy B (event-gap design doc §2.3, THIS PR): `test_pendencia_docs_recebidos_
     reavalia` and `test_solicitar_info_volta_para_pendencia` each asserted BOTH
     `has_event(_AUTH_PENDED)` (C1 — no publish task existed) AND
     `notifications_of_type("auth.request_documents")` (C2 — dead internal channel), both
     previously folded under `_ACTION_WORKER_KAFKA_GAP_REASON`. BPMN now carries
     `ST_PublishAuthPended` (a boundary-free `operadora.events.publish` task on
     `Flow_Solicitar_WaitDocs`, mirroring `ST_PublishReceived`), closing the C1 half for both
     tests (reached via either route into `ST_SolicitarDocumentos`); the C2 half of each is
     adapted to `has_event(..., prestador_id=...)` per the #108/#123 precedent. Split into their
     own `_AUTH_PENDED_PUBLISH_ADDED_REASON` reason — since RETIRED: those flips were live-proven
     on a real CIB Seven 2.1.0 engine and the constant now has ZERO call sites (see its RETIRED
     note).
  5. item-9 auth Class-A (THIS batch): the LAST 6 `_ACTION_WORKER_KAFKA_GAP_REASON` strict-xfails
     are adapted and their markers removed. Two independent things closed at different times:
     (a) the reason's `human_approved` clause is STALE — f271db9 (#185, "the src prerequisite of
     the 6 _ACTION_WORKER_KAFKA_GAP_REASON flips") reworked the three action-worker guards to
     derive human provenance from ENGINE-VISIBLE evidence (`decisao_auditor` literal /
     `auditor_id` / the DMN's own `auto_aprovacao.recomendacao`), so `numero_autorizacao` DOES
     populate now and the `human_approved` threading is itself observable in engine history;
     (b) the reason's `kafka.publish` clause is STILL TRUE (re-verified: `grep -n "kafka.publish("
     src/maezo/tools/workers/auth.py` = 0 hits), so each dead `notifications_of_type(...)` echo
     was RE-EXPRESSED against engine-side evidence — `activity_instances_ended` for the exact
     BPMN serviceTask id that carries the worker's topic ON THAT TEST'S BRANCH, plus
     `get_history_variable` for every field value the echo used to carry. NOTHING was weakened:
     see each test body for the id -> topic -> path citation and the per-field recovery.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.auth import AUTH_BPMN_ERROR_ALLOWLIST, register_auth_workers
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    TopicSubscription,
    WorkerHarness,
)
from maezo.tools.workers.phi_vars import REDACTED_PHI

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/auth_admissibility.dmn"
_DMN_AUTO = _REPO / "spec/processes/dmn/auth_auto_approval.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/auth_sla.dmn"

# External task topics do contrato SP-OP-AUTH-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_ANALYZE_TOPIC = "operadora.auth.analyze_request"
_REQ_DOCS_TOPIC = "operadora.auth.request_documents"
_ISSUE_AUTH_TOPIC = "operadora.auth.issue_authorization"
_DENIAL_TOPIC = "operadora.auth.send_denial_notice"
_NOTIFY_SLA_TOPIC = "operadora.auth.notify_sla_risk"
_JUNTA_TOPIC = "operadora.auth.convene_junta"

# Tópicos servidos pelos workers REAIS registrados no harness (drain genérico).
# `_ANALYZE_TOPIC` é deliberadamente EXCLUÍDO: ver `_AnalyzeRequestStub` abaixo (donor fixture,
# preservado verbatim).
_AUTH_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _REQ_DOCS_TOPIC,
    _ISSUE_AUTH_TOPIC,
    _DENIAL_TOPIC,
    _NOTIFY_SLA_TOPIC,
    _JUNTA_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_AUTH_RECEIVED = "agents.events.auth.received"
_AUTH_PENDED = "agents.events.auth.pended"
_AUTH_SLA_BREACHED = "agents.events.auth.sla_breached"
_AUTH_COMPLETED = "agents.events.auth.completed"

_UT_AUDITOR = "UT_AnaliseMedicoAuditor"
_UT_PENDENCIA = "UT_DecidirPendenciaExpirada"
_UT_COORDENACAO = "UT_CoordenacaoAssume"
_UT_JUNTA = "UT_RegistrarParecerJunta"

_END_NAO_REQUER = "End_NaoRequerAutorizacao"
_END_CANCELADA = "End_CanceladaPendencia"
_END_AUTO = "End_AprovadaAutomatica"
_END_AUDITOR = "End_AprovadaAuditor"
_END_NEGADA = "End_NegadaAuditor"
_END_FUNDAMENTACAO_INCOMPLETA = "End_FundamentacaoIncompletaBloqueada"

_UT_HUMANAS_NEGATIVA = frozenset({_UT_AUDITOR, _UT_COORDENACAO, _UT_JUNTA})

# BPMN serviceTask ids of the auth ACTION workers (item-9 auth Class-A). These are the engine-side
# execution proof that replaced the structurally-dead `notifications_of_type(...)` echoes (module
# docstring finding 5). Each id was read off
# `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn` and is paired here with the
# `camunda:topic` it declares and the ONE sequence flow that can reach it — because a single worker
# topic may back MORE THAN ONE serviceTask on different branches, and asserting the wrong branch's
# id would silently prove nothing (issue_authorization is exactly that case here):
#
#   ST_EmitirAutorizacaoAuto     topic operadora.auth.issue_authorization
#                                <- Flow_GW_AutoAprovar   (GW_AutoAprovacao,
#                                   ${auto_aprovacao.recomendacao == 'AUTO_APROVAR'}) -> auto L2 only
#   ST_EmitirAutorizacaoAuditor  topic operadora.auth.issue_authorization
#                                <- Flow_GWDec_Aprovar    (GW_DecisaoAuditor,
#                                   ${decisao_auditor == 'APROVAR'})                  -> human only
#   ST_EnviarNegativaFormal      topic operadora.auth.send_denial_notice  (the ONLY task on it)
#                                <- Flow_GWDec_Negar      (${decisao_auditor == 'NEGAR'})
#   ST_NotificarRiscoSla         topic operadora.auth.notify_sla_risk     (the ONLY task on it)
#                                <- Flow_Alerta_Notify    (BT_AlertaSla, cancelActivity="false",
#                                   attachedToRef=UT_AnaliseMedicoAuditor)
#   ST_ConvocarJunta             topic operadora.auth.convene_junta       (the ONLY task on it)
#                                <- Flow_GWDec_Junta      (${decisao_auditor == 'JUNTA_MEDICA'})
_ST_EMITIR_AUTO = "ST_EmitirAutorizacaoAuto"
_ST_EMITIR_AUDITOR = "ST_EmitirAutorizacaoAuditor"
_ST_NEGATIVA_FORMAL = "ST_EnviarNegativaFormal"
_ST_NOTIFICAR_SLA = "ST_NotificarRiscoSla"
_ST_CONVOCAR_JUNTA = "ST_ConvocarJunta"
_END_RISCO_SLA_NOTIFICADO = "End_RiscoSlaNotificado"

# CORRECTION to the pre-T3.1-R2 D-07 finding (live-verified, evidence below — NOT re-asserted
# as a live xfail cause anywhere in this file): the original finding claimed D-07's 0 ceiling
# (ceilings.py fail-closed) blocks End_AprovadaAutomatica for `test_happy_path_aprovacao_
# automatica_l2`/`test_pendencia_docs_recebidos_reavalia`. Live-confirmed AFTER the
# events.publish fix (docker compose core, CIB Seven 2.1.0): both tests DO reach
# End_AprovadaAutomatica — `AnalyzeRequestWorker` (the only caller of `CeilingResolver
# .within_l2_ceiling`) is on the `_ANALYZE_TOPIC` (`operadora.auth.analyze_request`), which
# `_AUTH_WORKER_TOPICS`/`auth_probe.drain()` deliberately excludes (served only by
# `_AnalyzeRequestStub`, a donor fixture, via the SEPARATE `drain_analyze()`); these two tests
# never call `drain_analyze()`. Their `start_auth(dentro_teto_l2=True, ...)` seed survives
# untouched into `BRT_AutoApproval`'s DMN evaluation (`auth_auto_approval.dmn` reads the flat
# `dentro_teto_l2` input directly) — the D-07 ceiling computation is simply never exercised by
# either test's path. **GAP-AUTH-4 (corrigido aqui — a caracterizacao anterior estava ERRADA).**
# Isto NAO e um "config gap" que decidir o D-07 resolve: `within_l2_ceiling` NUNCA e invocado
# nesta rota (o unico chamador de CeilingResolver em AUTH e AnalyzeRequestWorker, em
# ST_PrepararDossie, na perna ANALISE_HUMANA *depois* do GW_AutoAprovacao). Definir um teto real
# no D-07 nao muda NADA aqui — BRT_AutoApproval continua consumindo `dut_atendida`/
# `dentro_teto_l2`/`rede_credenciada` SEMEADOS NO START, sem verificacao. O teto e DECORATIVO na
# rota automatica. Remedio (fora de escopo, classe MZO-040/Medical-ANS): um worker que compute
# esses fatos ANTES de BRT_AutoApproval — e o que SP-OP-REEMBOLSO-001 ja faz (GAP-REEMBOLSO-5).
# both were then blocked by `_ACTION_WORKER_KAFKA_GAP_REASON` below instead (see the PR body for
# the full writeup — this correction is evidence, not fabricated). CORRECTION-TO-THE-CORRECTION
# (item-9 auth Class-A): `test_pendencia_docs_recebidos_reavalia` was already un-xfailed by
# wave2b2 and `test_happy_path_aprovacao_automatica_l2` is un-xfailed in THIS batch — neither is
# blocked by that constant anymore.

# NEW findings, live-confirmed AFTER T3.1 R2's events.publish fix (drift, NOT the publish gap —
# these tests progress far enough now to hit a SEPARATE, pre-existing v2 gap): auth.py's action
# WorkerBase classes (IssueAuthorizationWorker/SendDenialNoticeWorker/NotifySlaRiskWorker/
# ConveneJuntaWorker/RequestDocumentsWorker's embedded `event_topic_pended` publish) never call
# `kafka.publish` — mirrors the SAME systemic pattern found in escalation.py
# (NotifyTeamWorker/NotifySupervisorWorker, see that suite's `_NOTIFY_KAFKA_GAP_REASON`).
# Consequence: `auth_probe.notifications_of_type(...)` (backed by `FakeKafkaPublisher.published`)
# can NEVER observe any of these workers' executions. This clause is STILL TRUE and re-verified
# for item-9 auth Class-A (`grep -n "kafka.publish(" src/maezo/tools/workers/auth.py` = 0 hits);
# it is why every echo below had to be RE-EXPRESSED against engine history rather than simply
# un-xfailed. The constant's SECOND clause — that `human_approved` is never set anywhere, so
# IssueAuthorizationWorker/SendDenialNoticeWorker/ConveneJuntaWorker's own internal guard always
# blocks and `numero_autorizacao` is never populated — is STALE as of f271db9 (#185); see the
# RETIRED note on the constant.
_ACTION_WORKER_KAFKA_GAP_REASON = (
    "v2 drift (finding, T3.1 R2 — NOT the events.publish gap, which this PR fixes): auth.py's "
    "action WorkerBase classes (IssueAuthorizationWorker/SendDenialNoticeWorker/"
    "NotifySlaRiskWorker/ConveneJuntaWorker/RequestDocumentsWorker's embedded event_topic_pended "
    "publish) never call kafka.publish — mirrors escalation.py's NotifyTeamWorker/"
    "NotifySupervisorWorker gap. auth_probe.notifications_of_type(...)/has_event(_AUTH_PENDED) "
    "can therefore never observe these executions; separately, human_approved (never set by any "
    "BPMN inputParameter or this suite's complete_task_as_human payloads) always blocks "
    "IssueAuthorizationWorker/SendDenialNoticeWorker/ConveneJuntaWorker's own internal guard, so "
    "numero_autorizacao is never populated either. Live-confirmed (docker compose core, CIB "
    "Seven 2.1.0) after the events.publish fix landed. Not a fixture bug; src/** fix is out of "
    "scope for this PR."
    "\n\nRETIRED (item-9 auth Class-A): this constant has ZERO pytest.mark.xfail call sites — the "
    "last 6 were removed in that batch. Exactly what closed, and what did not:\n"
    "  * CLAUSE 2 (human_approved) is FALSE as of f271db9 (#185, 'the src prerequisite of the 6 "
    "_ACTION_WORKER_KAFKA_GAP_REASON flips'): the three action-worker guards no longer demand the "
    "phantom flag. issue_authorization now accepts decisao_auditor=='APROVAR' OR the modeled L2 "
    "sanction auto_aprovacao.recomendacao=='AUTO_APROVAR' (and NEVER issues on NEGAR); "
    "send_denial_notice GUARD 2 accepts a non-blank auditor_id (ADR-0007), which the three human "
    "UTs now carry as a modeled formField; convene_junta accepts decisao_auditor=='JUNTA_MEDICA'. "
    "numero_autorizacao therefore DOES populate, and the resolved provenance is threaded back as "
    "an engine-visible human_approved variable — so this batch could assert provenance and value, "
    "not merely execution.\n"
    "  * CLAUSE 1 (kafka.publish) is STILL TRUE and re-verified at flip time: auth.py's action "
    "workers publish NO internal notification, so operadora.notifications.internal — and with it "
    "auth_probe.notifications_of_type(...) — stays structurally always-empty for this family "
    "until the kafka-producer wiring task lands. Every echo was adapted, none was 'fixed': each "
    "dead notifications_of_type(...) call was replaced by engine.activity_instances_ended(iid) "
    "membership for the exact serviceTask id carrying that worker's camunda:topic on the branch "
    "the test exercises, and each dropped FIELD assertion by engine.get_history_variable(iid, "
    "...) on the same variable the worker read or wrote. Do NOT re-point a future test at "
    "notifications_of_type for auth — it will silently assert nothing.\n"
    "Live proof of the 6 flips is orchestrator-owned and NOT claimed here."
)

# T3.1 Wave 1 remedy B (event-gap design doc §2.3): CLOSES the C1 half of the gap for the 2 tests
# below that assert `has_event(_AUTH_PENDED)` — `ST_PublishAuthPended` (a plain, boundary-free
# `operadora.events.publish` task mirroring `ST_PublishReceived`) now sits on
# `Flow_Solicitar_WaitDocs` (spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn), reached
# by BOTH the initial-pendencia route (`Flow_GW_Pendencia`) and the SOLICITAR_INFO route
# (`Flow_GWDec_SolicitarInfo`) into `ST_SolicitarDocumentos`, so `has_event(_AUTH_PENDED)` is no
# longer structurally blocked by a missing publisher. Distinct from `_ACTION_WORKER_KAFKA_GAP_
# REASON` (still the blocker for every OTHER auth action-worker test in this module — those have no
# publish task and remain out of scope for this batch). Publish task added, pending live-proof
# flip: this repo's gates run without a live CIB Seven engine (no docker/engine in this pass), so
# the marker is intentionally NOT removed here — flip only after a real `make deploy-artifacts` +
# `pytest tests/integration/processes/test_sp_op_auth_001.py -k "pendencia_docs_recebidos_reavalia
# or solicitar_info_volta_para_pendencia"` run confirms the event round-trips and no sibling
# assertion regresses (design doc §5). The `notifications_of_type("auth.request_documents")`
# execution-proof half of each original assert pair was ADAPTED to `has_event(...,
# prestador_id=...)` per the #108/#123 precedent (that internal-notification channel is
# structurally always-empty in v2's dict-first workers, ADR-0026) — see the two test bodies below.
_AUTH_PENDED_PUBLISH_ADDED_REASON = (
    "T3.1 Wave 1 remedy B (event-gap design doc §2.3, contract SP-OP-AUTH-001.md:60 'produz'): "
    "ST_PublishAuthPended now exists on Flow_Solicitar_WaitDocs — the BPMN-side gap that blocked "
    "has_event(_AUTH_PENDED) is closed (mirrors ST_PublishReceived; boundary-free per the 15/16 "
    "convention, check-bpmn-error-allowlist unaffected). Publish task added, pending live-proof "
    "flip: requires a real CIB Seven engine run (make deploy-artifacts + this suite) to confirm the "
    "event round-trips and no sibling assertion regresses before the xfail marker is removed — not "
    "flipped in this PR (no docker/engine in this pass, per the design doc's live-validation "
    "requirement, §5). LIVE-FLIPPED (wave2b2 R1 live-validation, cibseven 2.1.0): BOTH tests "
    "XPASSed strict on a real engine and the xfails were removed in-step. Engine /history "
    "right-reason evidence, both routes proven: initial-pendencia (GW_Admissibilidade -> "
    "ST_SolicitarDocumentos -> ST_PublishAuthPended -> GW_AguardarDocs -> ICE_DocsRecebidos fired) "
    "AND SOLICITAR_INFO (UT_AnaliseMedicoAuditor -> ST_SolicitarDocumentos -> ST_PublishAuthPended); "
    "a third traversal proved ICE_PrazoPendencia still fires downstream of the splice. 0 sibling "
    "regressions. "
    "RETIRED (item-9 auth Class-A): those flips were live-proven and this constant now has ZERO "
    "pytest.mark.xfail call sites — grep-confirmed; the file carries no xfail markers at all. "
    "Retained only for the module-docstring prose above."
)

# T3.1 R3 (flip, THIS branch): the ERR_AUTH_DENIAL_INCOMPLETE guard is IMPLEMENTED and now
# LIVE-VERIFIED end-to-end. `SendDenialNoticeWorker.execute()` (auth.py) raises the spec-modeled
# WorkerBpmnError ERR_AUTH_DENIAL_INCOMPLETE when a NEGAR lacks any of justificativa_clinica /
# cid10_referencia / fundamentacao_dut (checked BEFORE the human_approved guard); the code is in
# `auth.AUTH_BPMN_ERROR_ALLOWLIST`, wired into this suite's `auth_probe` harness above, so the
# harness dispatches it as a real bpmnError (caught by BE_NegativaIncompleta ->
# End_FundamentacaoIncompletaBloqueada) instead of demoting it to an incident. The guard + its
# ordering + the PHI redaction are UNIT-PROVEN (tests/unit/tools/workers/test_auth_denial_guard.py,
# test_phi_vars.py). The prior strict-xfail (retained pending a live engine) XPASSED against a real
# CIB Seven 2.1.0 engine (isolated capped-heap stack) — the boundary catch, the blocked notice, and
# the empty-incident contract were all confirmed end-to-end — so the marker is REMOVED here; this
# now runs as a normal passing test (see PR body for the live run evidence).


# ---------------------------------------------------------------------------
# Fake worker para operadora.auth.analyze_request (dossie Rafael) — donor fixture, verbatim.
# O worker real de analyze_request pode nao estar registrado neste contexto (em producao e
# servido pelo agente Rafael); usamos um stub que completa a task sem logica real.
# ---------------------------------------------------------------------------


class _AnalyzeRequestStub:
    """Stub de worker para operadora.auth.analyze_request (Rafael / dossie)."""

    def __init__(self, transport: CibSevenWorkerTransport, worker_id: str) -> None:
        self._transport = transport
        self._worker_id = worker_id

    async def drain_analyze(self) -> None:
        """Consome e completa todas as tasks de analyze_request pendentes."""
        subs = [TopicSubscription(_ANALYZE_TOPIC, 10_000)]
        for _ in range(10):
            tasks = await self._transport.fetch_and_lock(
                self._worker_id, subs, max_tasks=5, async_response_timeout_ms=1_000
            )
            if not tasks:
                return
            for task in tasks:
                await self._transport.complete(task.task_id, self._worker_id, {})


@dataclass
class AuthEngineProbe:
    """Driva os workers reais de Phase-1 (auth) contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    analyze_stub: _AnalyzeRequestStub
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
        await drain_topics(self.transport, self.harness, self.worker_id, _AUTH_WORKER_TOPICS, rounds=rounds)

    async def drain_analyze(self) -> None:
        """Serve operadora.auth.analyze_request (stub Rafael)."""
        await self.analyze_stub.drain_analyze()


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de autorizacao da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_ADMISS, _DMN_AUTO, _DMN_SLA, name="SP-OP-AUTH-001-qa")


@pytest_asyncio.fixture
async def auth_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[AuthEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-1 de auth.

    T1.10 wave: completions são emit-before-complete contra o sink durável REAL da lane
    (PostgresAuditSink, migrações aplicadas) — um harness sem sink agora recusa completar.
    """
    worker_id = f"qa-auth-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T3.1 (auth-denial-hardening): ERR_AUTH_DENIAL_INCOMPLETE is catchable by SP-OP-AUTH-001's own
    # boundaryEvent (BE_NegativaIncompleta on ST_EnviarNegativaFormal, errorRef
    # Error_AuthDenialIncompleta) ONLY when it's in the harness's allowlist (harness.py
    # `_bpmn_error_allowlist` — the PRODUCTION default is empty pending the boundary-proof gate).
    # This wires the ONE code SendDenialNoticeWorker raises AND the BPMN declares a matching boundary
    # for (`auth.AUTH_BPMN_ERROR_ALLOWLIST`), mirroring the escalation suite's own
    # ERR_EVENT_PUBLISH_FAILED wiring and what a gate-proven production allowlist for auth would hold.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=AUTH_BPMN_ERROR_ALLOWLIST,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    register_auth_workers(harness, kafka)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in
    # this BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    analyze_stub = _AnalyzeRequestStub(transport, worker_id)
    probe = AuthEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        analyze_stub=analyze_stub,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_guia(prefix: str = "GUIA-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_auth(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key AUTH-amh-{guia} e payload canonico."""

    async def _start(**overrides: Any) -> dict[str, Any]:
        guia = overrides.pop("numero_guia_tiss", _unique_guia())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_guia_tiss": guia,
            "beneficiario_pseudo_id": "PSEUDO-PACIENTE-TESTE-001",
            "prestador_id": "PRESTADOR-TESTE-001",
            "codigo_procedimento_tuss": "40301010",  # sintetico: consulta medica
            "categoria_procedimento": "consulta",
            "carater_atendimento": "eletivo",
            "valor_estimado_brl": "180.00",
            "documentos_refs": "{}",
            "requer_autorizacao": True,
            "documentacao_completa": True,
            "beneficiario_ativo": True,
            "carencia_cumprida": True,
            "dut_atendida": True,
            "dentro_teto_l2": True,
            "rede_credenciada": True,
        }
        variables.update(overrides)
        business_key = f"AUTH-amh-{guia}"
        return await engine.start_by_key("SP-OP-AUTH-001", business_key, variables)

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


async def _assert_no_denial_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que End_NegadaAuditor nao existe sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    if _END_NEGADA in ended:
        human_tasks_in_history = ended & _UT_HUMANAS_NEGATIVA
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: End_NegadaAuditor atingido para instancia {iid} "
            f"SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_NEGATIVA}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de negativa — violacao do L0 hard (ADR-0005)."
        )


# ===========================================================================
# INVARIANTE L0 — Phase-1 DoD deliverable
# ===========================================================================


async def test_invariant_nenhum_caminho_automatizado_produz_negativa(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (Phase-1 DoD): prova que NENHUM caminho automatizado produz negativa."""
    results = []

    inst_a = await start_auth(
        dut_atendida=True, dentro_teto_l2=True, rede_credenciada=True, carater_atendimento="eletivo"
    )
    await auth_probe.drain()
    ended_a = await _await_end(engine, inst_a["id"])
    assert _END_NEGADA not in ended_a, (
        f"Auto-aprovacao L2 NAO deveria atingir End_NegadaAuditor. ended={ended_a}"
    )
    await _assert_no_denial_without_human_task(engine, inst_a["id"])
    results.append(("auto_approve_l2", "ok"))

    inst_b = await start_auth(dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    active_b = await engine.instance_is_active(inst_b["id"])
    assert active_b, "Instancia deve estar ativa aguardando decisao do auditor"
    ended_b = await engine.activity_instances_ended(inst_b["id"])
    assert _END_NEGADA not in ended_b, (
        f"Com analise pendente, nao deve haver End_NegadaAuditor. ended={ended_b}"
    )
    await _assert_no_denial_without_human_task(engine, inst_b["id"])
    results.append(("human_analysis_pending", "ok"))

    inst_c = await start_auth(dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ut_c = await engine.await_user_task(inst_c["id"], _UT_AUDITOR)
    assert "medico-auditor" in ut_c.candidate_groups
    await engine.complete_task_as_human(
        ut_c.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Procedimento sintetico nao coberto (teste L0)",
            "cid10_referencia": "Z00.0",
            "fundamentacao_dut": "DUT item 3.1 — exclusao sintetica para teste",
            "auditor_id": "auditor-sintetico-teste",
        },
    )
    await auth_probe.drain()
    ended_c = await _await_end(engine, inst_c["id"])
    await _assert_no_denial_without_human_task(engine, inst_c["id"])
    assert _END_NEGADA in ended_c, f"Negativa humana deve atingir End_NegadaAuditor. ended={ended_c}"
    # item-9 auth Class-A — the L0 leg of this invariant. The donor's three asserts rode
    # `auth_probe.notifications_of_type("auth.send_denial_notice")`, a channel that is
    # structurally always-empty in v2 (SendDenialNoticeWorker never calls kafka.publish —
    # re-verified at flip time). Re-expressed against the ENGINE, assert-for-assert, with NO
    # field dropped:
    #   1. ramo ALCANCADO (was: `assert denials`) -> ST_EnviarNegativaFormal no historico. NB:
    #      activity_instances_ended NAO filtra `finished`, entao prova ALCANCE, nao conclusao —
    #      as provas de VALOR abaixo é que sao load-bearing (GK-auth finding 3).
    #      That is the ONLY serviceTask in the BPMN carrying camunda:topic
    #      `operadora.auth.send_denial_notice`, and its only inbound flow is Flow_GWDec_Negar
    #      (`${decisao_auditor == 'NEGAR'}` off GW_DecisaoAuditor) — the exact branch this leg
    #      drives. No other branch can put it in history.
    #   2. `denials[0]["decisao_auditor"] == "NEGAR"` -> the engine's own record of the SAME
    #      variable the worker read. The echo was a copy the worker handed the fake publisher;
    #      the history variable is the engine's, and it is the value GW_DecisaoAuditor actually
    #      routed on.
    #   3. `denials[0]["auditor_id"] == "auditor-sintetico-teste"` -> likewise. This field is not
    #      decorative here: post-f271db9 it IS the human-provenance evidence
    #      SendDenialNoticeWorker's GUARD 2 consumes (`bool(auditor_id)`), so asserting its exact
    #      value asserts the identity the L0 guard accepted.
    #   4. STRENGTHENED (new, no donor equivalent): `human_approved is True` in history. The
    #      worker writes that variable ONLY on the branch that actually TRANSMITS the denial
    #      (auth.py `notice = {... "human_approved": True ...}`); the refusal branch returns
    #      `{status: blocked_by_guard, error_code: ERR_DENIAL_NOT_HUMAN}` with no such key, and
    #      RETURNS rather than raises — so the token reaches End_NegadaAuditor either way and
    #      reaching the terminal alone would NOT prove the guard passed. This assert closes that
    #      hole: it proves the negativa was transmitted WITH human provenance, which is precisely
    #      what this L0 invariant exists to protect. Nothing else on this instance's path writes
    #      `human_approved` (IssueAuthorizationWorker is unreachable on a NEGAR branch;
    #      ConveneJuntaWorker deliberately never writes it; the events.publish handler only
    #      outputs event_published/event_publish_best_effort_failure/event_topic).
    assert _ST_NEGATIVA_FORMAL in ended_c, (
        f"send_denial_notice (ST_EnviarNegativaFormal) deve ter executado apos negativa humana. "
        f"ended={ended_c}"
    )
    assert await engine.get_history_variable(inst_c["id"], "decisao_auditor") == "NEGAR"
    assert await engine.get_history_variable(inst_c["id"], "auditor_id") == "auditor-sintetico-teste"
    assert await engine.get_history_variable(inst_c["id"], "human_approved") is True, (
        "negativa transmitida deve carregar proveniencia humana (GUARD 2 aprovado, nao bloqueado)"
    )
    results.append(("negativa_humana_com_ut", "ok"))

    inst_d = await start_auth(beneficiario_ativo=False, dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ended_d = await engine.activity_instances_ended(inst_d["id"])
    assert _END_NEGADA not in ended_d, (
        f"Beneficiario inativo NAO deve produzir negativa automatica (L0 hard). ended={ended_d}"
    )
    await _assert_no_denial_without_human_task(engine, inst_d["id"])
    results.append(("beneficiario_inativo_segue_analise", "ok"))

    inst_e = await start_auth(carencia_cumprida=False, dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ended_e = await engine.activity_instances_ended(inst_e["id"])
    assert _END_NEGADA not in ended_e, (
        f"Carencia nao cumprida NAO deve produzir negativa automatica (L0 hard). ended={ended_e}"
    )
    await _assert_no_denial_without_human_task(engine, inst_e["id"])
    results.append(("carencia_nao_cumprida_segue_analise", "ok"))

    assert all(status == "ok" for _, status in results), f"Falhas: {results}"


# ===========================================================================
# Happy paths
# ===========================================================================


_AUTH_CEILING_D07_REASON = (
    "VALUE-GATED (D-07, financeiro) — NAO e defeito de engenharia. Desde a mitigacao GAP-AUTH-4 "
    "no chokepoint de emissao (item-9 auth Class-A), IssueAuthorizationWorker consulta "
    "CeilingResolver.within_l2_ceiling no CANAL AUTOMATICO antes de emitir. A matriz de "
    "governanca traz `authorization_approval.max_value_brl: 0` em spec/policies/autonomy/"
    "{L0-core,tenants-amh}.yaml — 0 significa 'aprovacao automatica NAO autorizada ate o D-07 ser "
    "decidido' — e within_l2_ceiling e fail-closed em teto 0 (False mesmo para value_cents == 0). "
    "Logo a rota automatica RECUSA emitir por decisao de governanca deliberada: o processo atinge "
    "End_AprovadaAutomatica e publica desfecho=aprovada_automatica, mas nao ha numero_autorizacao "
    "(status=blocked_by_guard, ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED, motivo_bloqueio_teto="
    "TETO_NAO_AUTORIZA, dentro_teto_l2=False no historico). O canal HUMANO nao e afetado — "
    "decisao_auditor=APROVAR emite normalmente acima do teto automatico (e para isso que a "
    "revisao humana existe), pinado por 5 testes unitarios. FLIP quando o D-07 definir um teto "
    "real para o tenant: a emissao automatica passa a funcionar, limitada por esse teto. "
    "SUPERSEDED / AGORA DUPLAMENTE GATEADO (portao de criterios GAP-AUTH-4): esta instancia nao "
    "chega mais sequer a End_AprovadaAutomatica. `ST_ValidateAutoApprovalCriteria` roda ANTES de "
    "BRT_AutoApproval e a DMN v0.2.0 nao le mais dut_atendida/dentro_teto_l2/rede_credenciada — "
    "os tres seeds desta fixture sao IGNORADOS. Os quatro criterios computados sao false "
    "(financeiro: teto 0/D-07; tecnico e regulatorio: fontes DRAFT nao ratificadas em "
    "spec/processes/dmn/auth-criteria-ratification.yaml; contratual: SEM_REGRA_RATIFICADA), logo "
    "r99 resolve ANALISE_HUMANA e o token vai para ST_PrepararDossie -> UT_AnaliseMedicoAuditor. "
    "O processo PARA na User Task humana: `_await_end` esgota as tentativas e o assert de "
    "End_AprovadaAutomatica falha. FLIP so quando D-07 definir teto >= R$180 **E** as fontes "
    "clinicas/regulatorias/contratuais forem ratificadas — nao mais so o D-07. NAO LIVE-PROVEN "
    "pelo autor desta mudanca (sem engine): a razao acima e derivada estruturalmente do modelo."
)


@pytest.mark.xfail(reason=_AUTH_CEILING_D07_REASON, strict=True)
async def test_happy_path_aprovacao_automatica_l2(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Aprovacao automatica L2: DUT ok + teto ok + rede ok => End_AprovadaAutomatica.

    End_AprovadaAutomatica IS reached, and the `notifications_of_type("auth.issue_authorization")`
    echo is re-expressed engine-side (see body). XFAILED (strict) since the GAP-AUTH-4 mitigation:
    the auto channel now consults the tenant teto and the shipped matrix sets
    `authorization_approval.max_value_brl: 0`, so issuance is refused BY GOVERNANCE — see
    `_AUTH_CEILING_D07_REASON`. This fixture seeds `valor_estimado_brl: "180.00"`, so the marker
    flips when D-07 sets a teto >= R$180 (not merely "any real teto").
    """
    inst = await start_auth(
        dut_atendida=True, dentro_teto_l2=True, rede_credenciada=True, carater_atendimento="eletivo"
    )
    iid = inst["id"]

    await auth_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_AUTO in ended, f"Deve atingir End_AprovadaAutomatica. ended={ended}"
    assert _END_NEGADA not in ended
    assert not await engine.list_user_tasks(iid), "Aprovacao automatica nao cria User Tasks"

    assert auth_probe.has_event(_AUTH_RECEIVED)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_automatica")

    # item-9 auth Class-A. Old: `issued = auth_probe.notifications_of_type("auth.issue_
    # authorization"); assert issued` — dead channel (IssueAuthorizationWorker never calls
    # kafka.publish). BRANCH DISCRIMINATION MATTERS HERE: `operadora.auth.issue_authorization`
    # backs TWO serviceTasks. `ST_EmitirAutorizacaoAuto` is reachable ONLY via Flow_GW_AutoAprovar
    # (`${auto_aprovacao.recomendacao == 'AUTO_APROVAR'}` off GW_AutoAprovacao) — the modeled L2
    # route this test drives; `ST_EmitirAutorizacaoAuditor` is reachable ONLY via Flow_GWDec_Aprovar
    # (`${decisao_auditor == 'APROVAR'}` off GW_DecisaoAuditor), which needs a human User Task.
    # Asserting the auto id AND the absence of the auditor id proves the L2 route specifically —
    # a bare "some issue_authorization task ran" would not.
    assert _ST_EMITIR_AUTO in ended, (
        f"issue_authorization deve ter executado em ST_EmitirAutorizacaoAuto (rota L2). ended={ended}"
    )
    assert _ST_EMITIR_AUDITOR not in ended, (
        f"rota auto-L2 NAO passa por ST_EmitirAutorizacaoAuditor (rota humana). ended={ended}"
    )
    # The worker ISSUED rather than blocking: `numero_autorizacao` is written ONLY by
    # IssueAuthorizationWorker's success branch (the guard-block branch returns
    # {status: blocked_by_guard, error_code: ERR_DENIAL_NOT_HUMAN} and no number). On this route
    # the sanction channel is the DMN's own result. LIVE-PROVEN DEFECT + FIX (item-9 auth Class-A):
    # the worker used to require `auto_aprovacao` to be a Mapping, but that variable is an engine
    # Object (BRT_AutoApproval mapDecisionResult=singleResult) and fetchAndLock does not
    # deserialize it — so the gateway routed here on the DMN sanction while the worker refused,
    # completing End_AprovadaAutomatica with NO numero_autorizacao. Fixed by a task-local
    # inputParameter flattening ${auto_aprovacao.recomendacao} into a String for THIS task only
    # (activity-local => not start-seedable); the Mapping branch is kept belt-and-braces.
    # GK-auth finding 4 — DIAGNOSTIC ORDER: `numero_autorizacao` is absent on the guard-block
    # branch, so reading it first raises EngineRestError (opaque) instead of AssertionError. The
    # worker writes `status` on BOTH branches ("authorized" vs "blocked_by_guard", auth.py:333/347
    # vs :365), so assert it FIRST — a blocked issuance then names itself.
    assert await engine.get_history_variable(iid, "status") == "authorized", (
        "IssueAuthorizationWorker deve ter EMITIDO (status=authorized), nao bloqueado pelo guard"
    )
    assert await engine.get_history_variable(iid, "numero_autorizacao"), (
        "IssueAuthorizationWorker deve ter emitido (numero_autorizacao em historia), nao bloqueado"
    )
    # L0/ADR-0007 provenance, threaded back by the worker: the auto-L2 route records
    # human_approved=False — TRUTHFUL (no human decided) and never fabricated. Asserting False
    # (not just "present") is what keeps an automated issuance from ever masquerading as a human
    # one in the audit trail.
    assert await engine.get_history_variable(iid, "human_approved") is False, (
        "emissao auto-L2 deve registrar human_approved=False (nenhum humano decidiu, ADR-0007)"
    )

    auto_facts = [
        e["payload"]
        for e in auth_probe.events_on(_AUTH_COMPLETED)
        if e["payload"].get("desfecho") == "aprovada_automatica"
    ]
    assert auto_facts, "Deve haver um fato auth.completed com desfecho=aprovada_automatica"
    # Live channel (ST_PublishAprovadaAuto's event_payload_vars carries numero_autorizacao) —
    # unchanged donor assert, now reachable because the f271db9 guard rework lets the worker issue.
    assert auto_facts[0].get("numero_autorizacao"), "numero_autorizacao ausente do fato aprovada_automatica"


async def test_happy_path_aprovada_pelo_auditor(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Analise humana: dossie preparado; auditor aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Deve atingir End_AprovadaAuditor. ended={ended}"
    assert _END_NEGADA not in ended
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")

    # item-9 auth Class-A. Old: `issued = auth_probe.notifications_of_type("auth.issue_
    # authorization"); assert issued` — dead channel. Same two-serviceTask hazard as the auto-L2
    # test, mirrored: here the HUMAN id must be present and the AUTO id absent.
    # `ST_EmitirAutorizacaoAuditor` (topic operadora.auth.issue_authorization) is reachable ONLY
    # via Flow_GWDec_Aprovar, `${decisao_auditor == 'APROVAR'}` off GW_DecisaoAuditor — the flow
    # this test's `complete_task_as_human(ut.id, {"decisao_auditor": "APROVAR"})` selects.
    assert _ST_EMITIR_AUDITOR in ended, (
        f"issue_authorization deve ter executado em ST_EmitirAutorizacaoAuditor. ended={ended}"
    )
    assert _ST_EMITIR_AUTO not in ended, (
        f"rota humana NAO passa por ST_EmitirAutorizacaoAuto (rota L2 auto). ended={ended}"
    )
    # GK-auth finding 4 — DIAGNOSTIC ORDER: `numero_autorizacao` is absent on the guard-block
    # branch, so reading it first raises EngineRestError (opaque) instead of AssertionError. The
    # worker writes `status` on BOTH branches ("authorized" vs "blocked_by_guard", auth.py:333/347
    # vs :365), so assert it FIRST — a blocked issuance then names itself.
    assert await engine.get_history_variable(iid, "status") == "authorized", (
        "IssueAuthorizationWorker deve ter EMITIDO (status=authorized), nao bloqueado pelo guard"
    )
    assert await engine.get_history_variable(iid, "numero_autorizacao"), (
        "IssueAuthorizationWorker deve ter emitido (numero_autorizacao em historia), nao bloqueado"
    )
    # L0/ADR-0007 provenance: on the human channel the guard resolves via decisao_auditor ==
    # 'APROVAR' (auth.py: `human_approved = process_vars.get("human_approved") is True or decisao
    # == "APROVAR"`) and the worker threads True back. This is the assert that distinguishes a
    # human-sanctioned authorization from the auto-L2 one above (which records False).
    assert await engine.get_history_variable(iid, "human_approved") is True, (
        "emissao pelo auditor deve registrar human_approved=True (canal humano, ADR-0007)"
    )

    auditor_facts = [
        e["payload"]
        for e in auth_probe.events_on(_AUTH_COMPLETED)
        if e["payload"].get("desfecho") == "aprovada_auditor"
    ]
    assert auditor_facts, "Deve haver um fato auth.completed com desfecho=aprovada_auditor"
    assert auditor_facts[0].get("numero_autorizacao"), "numero_autorizacao ausente do fato aprovada_auditor"


async def test_happy_path_negada_pelo_auditor(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Negativa pelo auditor: UT completa com NEGAR + campos obrigatorios => End_NegadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Exame sintetico nao indicado no protocolo clinico (teste)",
            "cid10_referencia": "Z13.9",
            "fundamentacao_dut": "DUT 2024 item 15.3 — exclusao sintetica para teste",
            "auditor_id": "dr-auditor-sintetico-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended, f"Deve atingir End_NegadaAuditor. ended={ended}"
    assert _END_AUTO not in ended
    assert _END_AUDITOR not in ended

    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")

    negada_facts = [
        e for e in auth_probe.events_on(_AUTH_COMPLETED) if e["payload"].get("desfecho") == "negada_auditor"
    ]
    assert negada_facts, "Fato auth.completed negada_auditor ausente"
    fact = negada_facts[0]["payload"]
    assert "justificativa_clinica" not in fact, f"PHI (justificativa_clinica) vazou no fato: {fact!r}"
    assert not any(("clinic" in k.lower()) or ("cid10" in k.lower()) for k in fact), fact
    assert fact.get("numero_guia_tiss"), "referencia (numero_guia_tiss) deve permanecer no fato"
    assert fact.get("_business_key"), "payload_ref (_business_key) deve permanecer no fato"

    # item-9 auth Class-A. Old block (all five asserts rode the always-empty
    # `auth_probe.notifications_of_type("auth.send_denial_notice")`):
    #     assert denials; d = denials[0]
    #     assert d["decisao_auditor"] == "NEGAR"
    #     assert d["auditor_id"] == "dr-auditor-sintetico-001"
    #     assert d["business_key"]; assert d["numero_guia_tiss"]
    # Recovery, field by field — nothing dropped:
    #   * execution proof -> ST_EnviarNegativaFormal in engine history. Only serviceTask on topic
    #     `operadora.auth.send_denial_notice`; only inbound flow is Flow_GWDec_Negar
    #     (`${decisao_auditor == 'NEGAR'}`).
    #   * decisao_auditor / auditor_id -> engine history variables (the engine's record of the
    #     exact values the worker read; auditor_id is what GUARD 2 consumes post-f271db9).
    #   * `d["business_key"]` and `d["numero_guia_tiss"]` (payload_ref + guia reference survive
    #     the egress) are ALREADY asserted above on the LIVE events.publish channel, against the
    #     same instance's auth.completed fact: `fact.get("_business_key")` and
    #     `fact.get("numero_guia_tiss")`. Those two donor obligations are covered there, on a
    #     channel that actually carries data, so they are not re-asserted here.
    assert _ST_NEGATIVA_FORMAL in ended, (
        f"send_denial_notice (ST_EnviarNegativaFormal) deve ter executado apos negativa humana. ended={ended}"
    )
    assert await engine.get_history_variable(iid, "decisao_auditor") == "NEGAR"
    assert await engine.get_history_variable(iid, "auditor_id") == "dr-auditor-sintetico-001"
    assert await engine.get_history_variable(iid, "human_approved") is True, (
        "negativa transmitida deve carregar proveniencia humana (GUARD 2 aprovado, nao bloqueado)"
    )
    # FINDING 3 (module docstring), donor assertion RESTORED — it was documented-not-asserted only
    # because the notification `d` was never observable. It is assertable engine-side:
    # `SendDenialNoticeWorker.execute()` returns `redact_phi_vars(notice)`, and the harness writes
    # a worker's whole return dict back as process variables (`_handle` ->
    # `transport.complete(..., dict(out_vars))`), so the one-way class token OVERWRITES the raw
    # clinical value this test's User Task submitted. Reading it back from engine history proves
    # the redaction where it actually matters — in the general-zone variable store (ADR-0006,
    # GAP-XPHI-1) — rather than in a copy handed to a fake publisher. Nothing downstream rewrites
    # it: ST_PublishNegada's handler only outputs event_published/…/event_topic.
    assert await engine.get_history_variable(iid, "cid10_referencia") == REDACTED_PHI, (
        "PHI clinico deve sair redigido (uma via) da variavel de engine apos send_denial_notice"
    )
    assert await engine.get_history_variable(iid, "justificativa_clinica") == REDACTED_PHI
    assert await engine.get_history_variable(iid, "fundamentacao_dut") == REDACTED_PHI

    await _assert_no_denial_without_human_task(engine, iid)


async def test_negativa_incompleta_bloqueada_pelo_guard(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """GAP-AUTH-2 (defesa-em-profundidade): NEGAR com fundamentacao INCOMPLETA nao vira negativa."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Procedimento sintetico nao indicado (teste GAP-AUTH-2)",
            "cid10_referencia": "Z00.0",
            "auditor_id": "auditor-sintetico-incompleto-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)

    assert _END_FUNDAMENTACAO_INCOMPLETA in ended, (
        f"Negativa incompleta deve ser capturada em {_END_FUNDAMENTACAO_INCOMPLETA}. ended={ended}"
    )
    assert _END_NEGADA not in ended, (
        f"Negativa com fundamentacao incompleta NAO pode atingir End_NegadaAuditor. ended={ended}"
    )
    assert _END_AUDITOR not in ended and _END_AUTO not in ended

    denials = auth_probe.notifications_of_type("auth.send_denial_notice")
    assert not denials, f"send_denial_notice NAO deve transmitir negativa incompleta: {denials!r}"
    assert not auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor"), (
        "auth.completed(negada_auditor) NAO deve ser publicado para negativa incompleta"
    )

    open_incidents = await engine.incidents(iid)
    assert not open_incidents, (
        f"ERR_AUTH_DENIAL_INCOMPLETE deve ser capturado pelo boundary (sem incidente): {open_incidents!r}"
    )

    await _assert_no_denial_without_human_task(engine, iid)


async def test_nao_requer_autorizacao(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """requer_autorizacao=false => DMN retorna NAO_REQUER => fim imediato sem User Task."""
    inst = await start_auth(requer_autorizacao=False)
    iid = inst["id"]

    await auth_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_NAO_REQUER in ended, f"Deve atingir End_NaoRequerAutorizacao. ended={ended}"
    assert not await engine.list_user_tasks(iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="nao_requer_autorizacao")


# ===========================================================================
# Inelegibilidade nao produz negativa automatica (L0)
# ===========================================================================


async def test_inelegibilidade_roteia_para_humano_nao_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """beneficiario_ativo=false => DMN retorna SEGUE_ANALISE; inelegibilidade nao nega automaticamente."""
    inst = await start_auth(beneficiario_ativo=False, dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGADA not in ended, "Inelegibilidade nao deve gerar negativa automatica"
    await _assert_no_denial_without_human_task(engine, iid)


# ===========================================================================
# Pendencia de documentacao
# ===========================================================================


async def test_pendencia_docs_recebidos_reavalia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """documentacao_completa=false => pended publicado; msg.auth.docs_received => reavalia."""
    inst = await start_auth(
        documentacao_completa=False,
        dut_atendida=True,
        dentro_teto_l2=True,
        rede_credenciada=True,
    )
    iid = inst["id"]

    await auth_probe.drain()

    # T3.1 Wave 1 remedy B (#108/#123 has_event adaptation): the old execution-proof assert
    # (`notifications_of_type("auth.request_documents")`) is structurally always-empty in v2
    # (dict-first worker, ADR-0026 — never reaches the internal notifications channel). Folded
    # into this single has_event() call with `prestador_id` matched from payload, which ALSO
    # proves request_documents ran (ST_PublishAuthPended sits immediately downstream on the same
    # token, so the event firing proves the worker's token flowed through PENDENTE_DOCUMENTACAO).
    assert auth_probe.has_event(_AUTH_PENDED, prestador_id="PRESTADOR-TESTE-001"), (
        "auth.pended deve ser publicado (ST_PublishAuthPended) apos request_documents"
    )
    # wave2b2 belt-and-suspenders (verifier flag: prestador_id is an INPUT-known field — prove
    # THIS instance's token ran request_documents AND the new publish task, engine-side):
    ended_pended = await engine.activity_instances_ended(iid)
    assert "ST_SolicitarDocumentos" in ended_pended, "request_documents deve ter executado nesta instancia"
    assert "ST_PublishAuthPended" in ended_pended, "ST_PublishAuthPended deve ter executado nesta instancia"

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.auth.docs_received",
        "businessKey": business_key,
        "processVariables": {"documentacao_completa": {"value": True, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.auth.docs_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await auth_probe.drain()
    # GAP-AUTH-4 (portao de criterios): a reavaliacao apos os documentos passa agora por
    # ST_ValidateAutoApprovalCriteria antes de BRT_AutoApproval. Com teto 0 (D-07) e as fontes
    # clinicas/regulatorias/contratuais DRAFT/nao ratificadas, os quatro criterios sao false ->
    # r99 -> ANALISE_HUMANA -> ST_PrepararDossie -> UT_AnaliseMedicoAuditor. O objetivo DESTE
    # teste (documentos recebidos REAVALIAM a admissibilidade) e provado pelo alcance da
    # reavaliacao, nao pelo terminal automatico — que nao e mais alcancavel enquanto nada estiver
    # ratificado. NAO LIVE-PROVEN pelo autor desta mudanca (sem engine): derivado do modelo.
    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert "ST_ValidateAutoApprovalCriteria" in ended, (
        f"a reavaliacao deve passar pelo portao de criterios. ended={ended}"
    )
    assert _END_AUTO not in ended, (
        "com teto 0 (D-07) e fontes nao ratificadas, NADA auto-aprova — a reavaliacao roteia para "
        f"analise humana. ended={ended}"
    )


async def test_pendencia_expira_decisao_humana(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Prazo de pendencia expira => UT_DecidirPendenciaExpirada criada para medico-auditor."""
    inst = await start_auth(documentacao_completa=False)
    iid = inst["id"]

    await auth_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_PENDENCIA)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_pendencia": "cancelar_guia"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CANCELADA in ended, f"cancelar_guia => End_CanceladaPendencia. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="cancelada_pendencia")


# ===========================================================================
# Timers de SLA
# ===========================================================================


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Timer alerta SLA (BT_AlertaSla) nao-interruptivo: notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_auth(dut_atendida=False, carater_atendimento="urgencia")
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    # item-9 auth Class-A. Old: `sla_alerts = auth_probe.notifications_of_type(
    # "auth.notify_sla_risk"); assert sla_alerts` — dead channel (NotifySlaRiskWorker never calls
    # kafka.publish; it is also the one worker in this batch with NO guard, so execution is the
    # whole obligation and no field assert was dropped). `ST_NotificarRiscoSla` is the ONLY
    # serviceTask carrying `operadora.auth.notify_sla_risk`, and its only inbound flow is
    # Flow_Alerta_Notify from BT_AlertaSla — the NON-INTERRUPTING boundary timer
    # (cancelActivity="false") attached to UT_AnaliseMedicoAuditor that this test fires by hand.
    # `End_RiscoSlaNotificado` is asserted too: the alert branch must run to its own terminal,
    # which — together with the surviving open-UT assert below — is what "nao-interruptivo"
    # actually means (a parallel token completed while the User Task stayed open).
    ended_sla = await engine.activity_instances_ended(iid)
    # GK-auth finding 5: prova de IDENTIDADE do worker — `alert_to` só é escrito por
    # NotifySlaRiskWorker (auth.py:581); sem isto, o unico sinal seria "a task completou".
    assert await engine.get_history_variable(iid, "alert_to") == "coordenacao-auditoria-medica", (
        "NotifySlaRiskWorker (nao apenas 'algo') deve ter servido o topico notify_sla_risk"
    )
    assert _ST_NOTIFICAR_SLA in ended_sla, (
        f"notify_sla_risk (ST_NotificarRiscoSla) deve ter executado no alerta de SLA. ended={ended_sla}"
    )
    assert _END_RISCO_SLA_NOTIFICADO in ended_sla, (
        f"ramo do alerta deve terminar em End_RiscoSlaNotificado. ended={ended_sla}"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnalise (interruptivo): UT_AnaliseMedicoAuditor cancelada; UT_CoordenacaoAssume criada."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    assert auth_probe.has_event(_AUTH_SLA_BREACHED), "auth.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-auditoria-medica" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR not in open_keys, "UT_AnaliseMedicoAuditor deve ser cancelada (interruptivo)"


async def test_dmn_auth_sla_urgencia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """carater_atendimento=urgencia => DMN auth_sla retorna sla_analise=PT2H."""
    inst = await start_auth(
        dut_atendida=False,
        carater_atendimento="urgencia",
        categoria_procedimento="consulta",
    )
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    assert job.activity_id == "BT_AlertaSla"

    job_sla = await engine.await_timer_job(iid, "BT_SlaAnalise")
    assert job_sla.activity_id == "BT_SlaAnalise"


# ===========================================================================
# Coordenacao assume (SLA breach) e decide
# ===========================================================================


async def test_coordenacao_assume_e_aprova(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(ut_coord.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Coordenacao aprova => End_AprovadaAuditor. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")


async def test_coordenacao_assume_e_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao nega => End_NegadaAuditor com UT humana no historico."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Prazo esgotado — decisao da coordenacao (sintetico)",
            "cid10_referencia": "Z00.1",
            "fundamentacao_dut": "DUT sintetico para teste",
            "auditor_id": "coordenacao-sintetica-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended
    await _assert_no_denial_without_human_task(engine, iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")


# ===========================================================================
# Junta medica (DRAFT)
# ===========================================================================


async def test_junta_medica_parecer_aprova(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Auditor -> JUNTA_MEDICA; convene_junta executado; junta aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "JUNTA_MEDICA"})
    await auth_probe.drain()

    # item-9 auth Class-A. Old: `juntas = auth_probe.notifications_of_type("auth.convene_junta");
    # assert juntas` — dead channel. `ST_ConvocarJunta` is the ONLY serviceTask carrying
    # `operadora.auth.convene_junta`, and its only inbound flow is Flow_GWDec_Junta
    # (`${decisao_auditor == 'JUNTA_MEDICA'}` off GW_DecisaoAuditor) — the exact literal this test
    # submits. `junta_group` is the value proof that the worker CONVENED rather than blocking:
    # ConveneJuntaWorker writes it only on its success branch (the guard-block branch returns
    # {status: blocked_by_guard, error_code: ERR_DENIAL_NOT_HUMAN} and RETURNS, so the token would
    # still reach UT_RegistrarParecerJunta and the terminal alone would prove nothing). Note the
    # worker deliberately does NOT write `human_approved` here (it runs BEFORE the junta's own User
    # Task; a worker-persisted True would poison the downstream denial guard) — so provenance is
    # asserted via the decision literal + junta_group, never via a flag that must not exist yet.
    ended_junta = await engine.activity_instances_ended(iid)
    assert _ST_CONVOCAR_JUNTA in ended_junta, (
        f"convene_junta (ST_ConvocarJunta) deve ter executado. ended={ended_junta}"
    )
    assert await engine.get_history_variable(iid, "decisao_auditor") == "JUNTA_MEDICA"
    assert await engine.get_history_variable(iid, "junta_group") == "junta-medica", (
        "ConveneJuntaWorker deve ter convocado (junta_group em historia), nao bloqueado pelo guard"
    )

    ut_junta = await engine.await_user_task(iid, _UT_JUNTA)
    assert "junta-medica" in ut_junta.candidate_groups

    await engine.complete_task_as_human(ut_junta.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Junta aprova => End_AprovadaAuditor. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")


async def test_junta_medica_parecer_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Junta nega => End_NegadaAuditor com UT_RegistrarParecerJunta no historico (invariante)."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "JUNTA_MEDICA"})
    await auth_probe.drain()

    ut_junta = await engine.await_user_task(iid, _UT_JUNTA)
    await engine.complete_task_as_human(
        ut_junta.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Parecer unanime da junta: nao indicado (sintetico)",
            "cid10_referencia": "Z01.9",
            "fundamentacao_dut": "DUT sintetico junta para teste",
            "auditor_id": "junta-sintetica-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended
    await _assert_no_denial_without_human_task(engine, iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")


# ===========================================================================
# SOLICITAR_INFO => volta para pendencia
# ===========================================================================


async def test_solicitar_info_volta_para_pendencia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Auditor -> SOLICITAR_INFO => request_documents reexecutado; aguarda docs."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "SOLICITAR_INFO"})
    await auth_probe.drain()

    # T3.1 Wave 1 remedy B (#108/#123 has_event adaptation): see test_pendencia_docs_recebidos_
    # reavalia above — same fold of the dead notifications_of_type("auth.request_documents")
    # execution-proof assert into this single has_event() call (prestador_id matched from
    # payload); ST_PublishAuthPended is on Flow_Solicitar_WaitDocs, reached via BOTH the
    # SOLICITAR_INFO route (Flow_GWDec_SolicitarInfo) exercised here and the initial-pendencia
    # route (Flow_GW_Pendencia) exercised there.
    assert auth_probe.has_event(_AUTH_PENDED, prestador_id="PRESTADOR-TESTE-001"), (
        "auth.pended deve ser publicado (ST_PublishAuthPended) apos SOLICITAR_INFO"
    )
    # wave2b2 belt-and-suspenders (see test_pendencia_docs_recebidos_reavalia): engine-history
    # containment for THIS instance — SOLICITAR_INFO route through the new publish task:
    ended_pended = await engine.activity_instances_ended(iid)
    assert "ST_SolicitarDocumentos" in ended_pended, "request_documents deve ter reexecutado nesta instancia"
    assert "ST_PublishAuthPended" in ended_pended, "ST_PublishAuthPended deve ter executado nesta instancia"

    assert await engine.instance_is_active(iid), "Instancia deve estar ativa aguardando docs"


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_guia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    Nao depende de `auth_probe.drain()` progredir alem do primeiro publish — sobrevive ao
    gap de `operadora.events.publish` acima (nao marcado xfail, ao contrario dos demais).
    """
    guia = "GUIA-TESTE-IDEM-001"
    business_key = f"AUTH-amh-{guia}"

    first = await start_auth(numero_guia_tiss=guia)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]
