"""SP-OP-NIP-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase-2 (13-family
process-suite port). nip.py is one of the "6 typed-I/O modules with new dict-boundary entry
functions (ADR-0026 §2b)" per bootstrap.py's own categorization — this port mirrors cancel's
dict-boundary/typed-I/O pattern (typed dataclass inputs, dict-boundary entry functions,
self-contained fixtures) more closely than auth's WorkerBase pattern.

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-NIP-001.md) contra o engine real
(ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `NIP-amh-{numero_nip_ans}`;
2. drena as external tasks com o `nip_probe` (workers reais Phase-2 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que MANTER_NEGATIVA passa pela UT
   humana).

Dados sinteticos obvios: NIP `NIP-TESTE-NNNN`, protocolo `PROTO-TESTE-0001`, beneficiario
pseudonimizado `BEN-TESTE-001`, tenant `amh`. Business key `NIP-amh-{nip}`. Referencias de
negativa original sinteticas (`AUTH-amh-GUIA-TESTE-0001`).
Process key: SP-OP-NIP-001 (exato — nao alterar).

## Invariante L0 (DoD deliverable)

test_manter_negativa_so_via_user_task_humana:
  Varredura de TODAS as combinacoes de input das DMNs nip_classification / nip_routing / nip_sla.
  A instancia NUNCA atinge End_NipNegativaMantida sem que UT_RevisaoJuridicaNip / UT_CoordenacaoNip
  tenha sido completada por humano com decisao_nip=MANTER_NEGATIVA. A prova e feita consultando a
  historia do engine (history/activity-instance): se End_NipNegativaMantida esta no historico sem
  nenhuma UT humana -> falha (guardrail L0).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.nip`/`harness`/`events`. `phase0.py` does not exist on
    v2 main (`register_phase0_workers`/donor's `FakeKafkaPublisher` moved to `harness.py`, T1.1;
    `register_events_workers`, T3.1 R2, replaces `register_phase0_workers` for the generic
    `operadora.events.publish` topic — see `events.py`).
  - the shared `engine` fixture + `drain_topics()` helper come from the ALREADY-MERGED
    `conftest.py` (hoisted there because the construction contract is byte-identical across every
    ported family) — this file does NOT redefine `engine`/`_engine_available()`/`_BASE_URL` the
    way the donor did (the donor's own docstring predates that hoist); `deploy_artifacts`,
    `NipEngineProbe`, `start_nip` stay LOCAL/self-contained (probe construction is NOT identical
    across families — mirrors cancel's own convention, `conftest.py` module docstring).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5); DMN filenames confirmed by
    grep (`camunda:decisionRef="nip_classification|nip_routing|nip_sla"`,
    SP-OP-NIP-001_Resposta_NIP.bpmn:136,167,212) against `spec/processes/dmn/*.dmn`.
  - `_NIP_WORKER_TOPICS` is built from what `register_nip_workers` (nip.py) ACTUALLY registers —
    NOT copied from the donor's 5-topic list — cross-checked against the BPMN's declared
    `camunda:topic` values (registry-drift finding below; mirrors cancel's finding 2+2b style).
  - `test_worker_submit_response_recusa_negativa_sem_humano`: v2's dict-boundary entry function is
    `submit_response_entry(variables: dict, *, kafka=None) -> dict` (nip.py) — NOT donor's
    `make_submit_response_handler(kafka) -> Callable[[ExternalTask], ...]` (that donor factory
    name/shape does not exist on v2 main). Adapted to call the entry function directly with a
    plain `variables` dict (no `ExternalTask`). The guard ALSO changed shape (verify each, port
    rule 1): v2's guard raises `NipNegativaNotHumanError` (a `PermissionError` subclass — the
    harness routes any `PermissionError` straight to an engine incident, `harness.py` `_handle`),
    not donor's `WorkerBpmnError(error_code=...)` (a modeled BPMN error routed to a boundary
    catch). The 4 guard scenarios (a-d) are preserved verbatim; only the exception type asserted
    and the "success" shape changed to match what v2's function actually returns (a
    `{"submitted": True, "decisao_nip": ..., "revisor_id": ..., "status": "filed"}` dict — v2's
    `submit_to_ans` does not itself call `kafka.publish` nor compute a `submit_id`, unlike the
    donor's handler; that indirection is the SAME `operadora.events.publish`-adjacent finding
    below (`_NIP_WORKER_KAFKA_GAP_REASON`), not re-litigated here since this test intentionally
    does not touch the engine).

FINDINGS (see PR body / evidence-ledger for full detail):

  1. `operadora.events.publish` gap, ALREADY FIXED on main (T3.1 R2): `nip_probe` registers
     `maezo.tools.workers.events.register_events_workers` (mirrors escalation/auth/cancel's own
     composition) — every `ST_Publish*` service task in this BPMN (nip.received/classified/
     deadline_risk/breached/completed) flows and is asserted green below.

  2. REGISTRY DRIFT — RECONCILED in src (t2.5-p2b-nip-mechanical): 2 sub-findings, both fixed
     (mirrors #93/e4e3ed1's inadimplencia reconciliation style — that PR is this fix's template):

     2a. RESOLVED. `register_nip_workers` (nip.py) used to register 8 `operadora.nip.*` topics
         but the BPMN only declared `camunda:topic` service tasks for 4 of them
         (instruct_dossier/submit_response/handoff_ans_submit/notify_deadline_risk;
         publish_completed folds into the generic events.publish). `classify_nip`/`route_nip`
         (superseded by the NATIVE DMN businessRuleTasks BRT_Classificacao/BRT_Roteamento —
         `camunda:decisionRef="nip_classification"`/`"nip_routing"`,
         SP-OP-NIP-001_Resposta_NIP.bpmn:136,167 — the engine evaluates the deployed `.dmn`
         directly, no `camunda:topic` was ever declared for these) and `review_juridico`/
         `notify_beneficiario` (NO BPMN consumer at all — zero hits anywhere in spec/) were
         DELETED (functions + entry functions + registrations + unit tests; `classify_nip`'s
         `_resolve_*_group` helpers went with it). `register_nip_workers` now registers EXACTLY
         5 `operadora.nip.*` topics (nip.py:415-438) — verified via the unit-test drift guard
         `test_register_nip_workers_matches_bpmn_topics_exactly`
         (tests/unit/tools/workers/test_nip.py).

     2b. RESOLVED (built; SUPERSEDED for 2 of its 3 sites — see 2c below). The BPMN used to
         declare THREE service tasks on `operadora.nip.notify_deadline_risk`
         (`ST_NotificarRiscoPrazo`, `ST_NotificarRiscoRevisao`, `ST_SolicitarInfoNip` — the
         SOLICITAR_INFO branch reuses this same topic per its own BPMN comment "reusa o canal de
         notificacao regulatoria") — `register_nip_workers` registers
         `notify_deadline_risk`/`notify_deadline_risk_entry` (nip.py, UNCHANGED) for it, mirroring
         `cancel.notify_sla_risk`/`contas.notify_sla_risk`/`inadimplencia.notify_sla_risk`/
         `auth.NotifySlaRiskWorker`'s notify-only, non-adverse, fail-safe pattern.
         `_NIP_WORKER_TOPICS` below still INCLUDES `notify_deadline_risk` — a drift-guard
         assertion in `nip_probe` (mirrors cancel's pattern) still fails loudly if a future
         `register_nip_workers` change silently adds/removes an `operadora.nip.*` registration
         without this file's topic list being updated to match. `ST_SolicitarInfoNip` is the ONLY
         BPMN site left on this topic since 2c (below); it never set
         `event_topic_deadline_risk`/`sla_breach_task_name` (both defaulted to `""`) and is
         unrelated to `agents.events.nip.deadline_risk` — the worker is NOT orphaned by 2c.

     2c. RESOLVED (T3.1 event-gap-nip-alerts — this pass; BUILT, PENDING LIVE-PROOF FLIP, see
         `_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON` below). `ST_NotificarRiscoPrazo` and
         `ST_NotificarRiscoRevisao` (SP-OP-NIP-001_Resposta_NIP.bpmn) were CONVERTED IN PLACE
         from `operadora.nip.notify_deadline_risk` to the generic `operadora.events.publish`
         worker — contract SP-OP-NIP-001.md:111 "produz" `agents.events.nip.deadline_risk`, which
         previously had only a dangling `event_topic_deadline_risk` inputParameter and no
         publisher (finding 3's `has_event(_NIP_DEADLINE_RISK)` blocker). Unlike the cancel/auth/
         reembolso/inadimplencia Wave-1/wave-4 remedy-B batches (each a SPLICE: a new ST_Publish*
         task added alongside the original dict-first worker, which keeps its own separate
         business action), this is a straight in-place conversion: `notify_deadline_risk_entry`
         never did anything beyond a structlog line at these two call sites (no real side effect
         to preserve), so no splice was needed — see the BPMN task's own documentation for the
         full rationale. `event_payload_vars` = `tenant_id,numero_nip_ans,classificacao,
         sla_breach_task_name` (business-keys-only, no PHI; mirrors `ST_PublishBreach`'s sibling
         payload plus the pre-existing `sla_breach_task_name` literal as the elaboracao/revisao
         discriminator). ZERO src/**.py edits (nip.py untouched) — `register_nip_workers` keeps
         registering `operadora.nip.notify_deadline_risk` because `ST_SolicitarInfoNip` (2b) still
         consumes it; `_NIP_WORKER_TOPICS`/the drift guard therefore need NO change. Not flipped
         in this pass (no docker/live engine here) — see `_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON`
         for the exact residual risk and flip procedure.

  3. Kafka-publish gap (systemic, SAME class as auth/cancel/escalation residuals — ledger row
     "T3.1 (events.publish fix)") — STILL OPEN, unchanged by t2.5-p2b-nip-mechanical: every
     ADR-0026 dict-boundary entry function nip.py registers under an `operadora.nip.*` topic —
     `instruct_dossier_entry`, `submit_response_entry`, `notify_deadline_risk_entry` (new, BUILT
     the SAME way — `del kafka  # unused`, deliberately matching this convention rather than
     fixing the systemic gap, which is out of scope here), `handoff_ans_submit_entry` — never
     calls `kafka.publish`; `register_nip_workers`'s own docstring says so explicitly. The donor's
     equivalent handlers published a per-worker notification (`operadora.notifications.internal`)
     from INSIDE the handler; v2's entry functions only RETURN output variables loaded back onto
     the process instance by the harness's `complete` call. `nip_probe.notifications_of_type(...)`
     over any of these topics can therefore never observe an execution, even though the worker
     itself runs/completes correctly against the live engine (this suite's flow-level assertions
     for the same tests generally DO pass — see `_NIP_WORKER_KAFKA_GAP_REASON` below for the exact
     tests this blocks). Distinguish this from `agents.events.nip.*` topics (published via the
     GENERIC, WORKING `operadora.events.publish` handler, `events.py`) — those DO work and are
     asserted un-xfailed throughout this suite. NOTE (SUPERSEDED by 2c above): the 2
     `has_event(_NIP_DEADLINE_RISK)` assertions (`test_prazo_nip_dispara_alerta_nao_interruptivo`,
     `test_prazo_ancora_em_data_recebimento_nip_nao_em_attach_da_ut`) are no longer blocked by this
     finding — `agents.events.nip.deadline_risk` now has a real publisher
     (`ST_NotificarRiscoPrazo`/`ST_NotificarRiscoRevisao` converted to `operadora.events.publish`,
     2c) independent of the still-open dict-first `notify_deadline_risk_entry` gap. Both tests
     were retagged to `_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON` (not flipped — pending live-proof).

  4. `ERR_NIP_PROTOCOLO_INVALIDO` never reaches its BPMN boundary catch (nip-specific, NOT the
     generic kafka-publish gap): nip.py's `NipProtocoloInvalidoError` (nip.py:47-54) is a
     `ValueError` subclass, NOT `WorkerBpmnError`. `WorkerHarness._handle` (harness.py:916-943 vs
     :952-954) reports a `WorkerBpmnError` as a real `bpmnError` (subject to the harness's
     `bpmn_error_allowlist`) but ALWAYS routes `ValueError`-family exceptions straight to
     `_report_failure`/`transport.handle_failure` (an unconditional engine incident, retries=0) —
     the allowlist is never even consulted for `ValueError`. The BPMN's three boundary events
     (`BE_NipProtocoloInvalidoManter`/`Conceder`/`NaoAssist`,
     SP-OP-NIP-001_Resposta_NIP.bpmn:408-411, 442-445, 477-480) only catch a `bpmnError` matching
     `errorEventDefinition@errorRef="Error_NipProtocoloInvalido"` — they can NEVER be triggered by
     `handoff_ans_submit_entry`'s `NipProtocoloInvalidoError`. A blank `protocolo_ans` therefore
     opens an unrecoverable engine incident on `ST_HandoffAns{Manter,Conceder,NaoAssistencial}`
     instead of routing to the neutral terminal `End_NipProtocoloInvalido` — the instance never
     completes. See `_PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON` below (3 tests). NOTE:
     `Error_NipNegativaNotHuman` (BPMN line 27) is likewise declared but has NO matching boundary
     event ANYWHERE in the BPMN — but no donor test in this file depends on a boundary catch for
     that guard (the relevant test only asserts NEGATIVE outcomes — the adverse terminal is never
     reached — which holds regardless of whether the guard surfaces as an incident or a caught
     bpmnError), so this asymmetry needs no xfail of its own.

  5. Missing anchor fail-safe for `data_recebimento_nip_iso` (nip-specific regression vs the
     donor): the donor's `instruct_dossier` worker defaulted a blank/garbage
     `data_recebimento_nip_iso` to today's date (UTC) BEFORE it could reach `BRT_NipSla`'s FEEL
     evaluation. v2's `assemble_response`/`instruct_dossier_entry` (nip.py:206-238, 446-454)
     carries NO such validation/defaulting — `NipInput.data_recebimento_nip_iso` (nip.py:73) is
     stored and returned completely unvalidated, and no BPMN `outputParameter`/`inputParameter`
     defaults it either (grep confirms zero hits for a default expression on this variable
     anywhere in SP-OP-NIP-001_Resposta_NIP.bpmn). `nip_sla.dmn`'s FEEL expression
     (nip_sla.dmn:64,74,84,94) is `date and time(data_recebimento_nip_iso + "T00:00:00")` — a
     blank string yields `"T00:00:00"` and `"not-a-date"` yields `"not-a-dateT00:00:00"`, NEITHER
     of which `date and time(...)` can parse; `BRT_NipSla` fails DMN evaluation and opens an
     incident BEFORE `UT_RevisaoJuridicaNip` is ever created — exactly the catastrophic failure
     mode this test exists to prove is fail-safe. See `_ANCHOR_FAILSAFE_MISSING_REASON` below.

  Static-analysis confirmation (no live engine needed, verified directly against the deployed
  `.dmn` XML): `nip_classification.dmn`/`nip_routing.dmn`/`nip_sla.dmn`'s rule outputs and
  `typeRef`s were traced by hand against every DMN-shape test below (`test_nip_dmn_*`,
  `test_dmn_typeref_allowlist`, and the two `test_grupo_humano_dmn_decide_candidate_groups_*`
  tests) — all confirmed to hold exactly as asserted; none are xfailed.
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

from maezo.platform.notification_bridge import NotificationBridge, build_cibseven_process_starter
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.nip import (
    NipNegativaNotHumanError,
    handoff_ans_submit,
    register_nip_workers,
    submit_response_entry,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn"
_DMN_CLASS = _REPO / "spec/processes/dmn/nip_classification.dmn"
_DMN_ROUTING = _REPO / "spec/processes/dmn/nip_routing.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/nip_sla.dmn"
_BPMN_ANS_SUBMIT = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_DMN_ANS_CALENDAR = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_DMN_ANS_SLA = _REPO / "spec/processes/dmn/ans_sla.dmn"
_DMN_ANS_ADMISS = _REPO / "spec/processes/dmn/ans_submission_admissibility.dmn"
_DMN_ANS_RETRY = _REPO / "spec/processes/dmn/ans_retry_policy.dmn"
_PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

# External task topics do contrato SP-OP-NIP-001 REALMENTE registrados por `register_nip_workers`
# (nip.py) — construida a partir do registro real, NAO copiada do donor (finding 2 acima).
_PUBLISH_TOPIC = "operadora.events.publish"
_INSTRUCT_TOPIC = "operadora.nip.instruct_dossier"
_SUBMIT_TOPIC = "operadora.nip.submit_response"
_HANDOFF_TOPIC = "operadora.nip.handoff_ans_submit"
_PUBLISH_COMPLETED_TOPIC = "operadora.nip.publish_completed"

# Registered by register_nip_workers SINCE t2.5-p2b-nip-mechanical (mirrors inadimplencia's own
# #93/e4e3ed1 reconciliation, cited in that suite's module docstring finding 1 — the sync-comment
# style below is the SAME convention): finding 2b's missing worker
# (notify_deadline_risk/notify_deadline_risk_entry, nip.py) was implemented — the drift-guard
# below would have caught a stale drain list on the first real-engine run, as designed.
# T3.1 event-gap-nip-alerts (finding 2c, this pass): ST_NotificarRiscoPrazo/ST_NotificarRiscoRevisao
# were CONVERTED IN PLACE to operadora.events.publish (zero src/**.py edits — nip.py, this
# constant, and _NIP_WORKER_TOPICS below are all UNCHANGED). ST_SolicitarInfoNip is now the SOLE
# remaining BPMN site on this topic — register_nip_workers still registers it for that reason, so
# the worker is not orphaned. NOT yet live-proven in THIS worktree (no docker/live engine here) —
# see `_NOTIFY_DEADLINE_RISK_UNREGISTERED_REASON` (historical — the prior 3-site gap) and
# `_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON` (current — the 2c publish-task-added state) below.
_NOTIFY_DEADLINE_RISK_TOPIC = "operadora.nip.notify_deadline_risk"  # t2.5-p2b-nip-mechanical sync

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). classify_nip/
# route_nip (superseded by native DMN businessRuleTasks) and review_juridico/notify_beneficiario
# (no BPMN consumer) were DELETED from nip.py (t2.5-p2b-nip-mechanical) — removed from this list
# to match; NOT copied from the donor (finding 2 acima).
_NIP_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _INSTRUCT_TOPIC,
    _SUBMIT_TOPIC,
    _NOTIFY_DEADLINE_RISK_TOPIC,
    _HANDOFF_TOPIC,
    _PUBLISH_COMPLETED_TOPIC,
]

# Topico interno de notificacoes (harness.py FakeKafkaPublisher convention).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-NIP-001.md) — publicados via operadora.events.publish (finding 1).
_NIP_RECEIVED = "agents.events.nip.received"
_NIP_CLASSIFIED = "agents.events.nip.classified"
_NIP_DEADLINE_RISK = "agents.events.nip.deadline_risk"
_NIP_BREACHED = "agents.events.nip.breached"
_NIP_COMPLETED = "agents.events.nip.completed"

# User Task definition keys (BPMN)
_UT_ELABORAR = "UT_ElaborarRespostaNip"
_UT_REVISAO = "UT_RevisaoJuridicaNip"
_UT_COORDENACAO = "UT_CoordenacaoNip"

# End events do processo
_END_NEGATIVA_MANTIDA = "End_NipNegativaMantida"
_END_RESOLVIDA_FAVORAVEL = "End_NipResolvidaFavoravel"
_END_NAO_ASSISTENCIAL = "End_NipNaoAssistencialRespondida"
# Terminal NEUTRO compartilhado pelos boundary catches de protocolo-ANS-invalido
# (BE_NipProtocoloInvalidoManter/Conceder/NaoAssist) — NUNCA alcancavel em v2 (finding 4).
_END_PROTOCOLO_INVALIDO = "End_NipProtocoloInvalido"

# User Tasks humanas que podem produzir MANTER_NEGATIVA (L0 hard guard)
_UT_HUMANAS_MANTER = frozenset({_UT_REVISAO, _UT_COORDENACAO})

# ---------------------------------------------------------------------------
# xfail reasons — um por classe de root-cause (module docstring findings 3/4/5).
# ---------------------------------------------------------------------------

_NIP_WORKER_KAFKA_GAP_REASON = (
    "v2 systemic drift (T3.1 finding 3 — same class as auth/cancel/escalation residuals, ledger "
    "row 'T3.1 (events.publish fix)'): nip.py's ADR-0026 dict-boundary entry functions "
    "(instruct_dossier_entry/submit_response_entry/notify_deadline_risk_entry/"
    "handoff_ans_submit_entry — t2.5-p2b-nip-mechanical DELETED classify_nip_entry/route_nip_"
    "entry/review_juridico_entry/notify_beneficiario_entry as orphan registrations, and BUILT "
    "notify_deadline_risk_entry the SAME way, `del kafka  # unused`, deliberately matching this "
    "convention rather than fixing it) never call kafka.publish — register_nip_workers's own "
    "docstring says so explicitly. The donor's equivalent handlers published a "
    "per-worker notification from INSIDE the handler; v2's entry functions only RETURN output "
    "variables. nip_probe.notifications_of_type(...) over an operadora.nip.* topic can therefore "
    "never observe an execution, even though the worker itself runs/completes correctly against "
    "the live engine and this test's OTHER (flow-level, agents.events.nip.* via the generic "
    "operadora.events.publish worker) assertions would pass. Fix belongs to the Kafka-producer "
    "wiring task, not this port (STILL true after t2.5-p2b-nip-mechanical)."
)

_PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON = (
    "nip-specific gap (module docstring finding 4 — distinct from the generic events.publish/"
    "kafka-publish gaps): nip.py's NipProtocoloInvalidoError (nip.py:47-54) is a ValueError "
    "subclass, NOT WorkerBpmnError. WorkerHarness._handle (harness.py:916-943 vs :952-954) "
    "reports a WorkerBpmnError as a real bpmnError (subject to bpmn_error_allowlist) but ALWAYS "
    "routes ValueError-family exceptions straight to _report_failure/transport.handle_failure (an "
    "unconditional engine incident) — the allowlist is never consulted for ValueError. The "
    "BPMN's three boundary events (BE_NipProtocoloInvalidoManter/Conceder/NaoAssist, "
    "SP-OP-NIP-001_Resposta_NIP.bpmn:408-411,442-445,477-480) only catch a bpmnError matching "
    "errorRef=Error_NipProtocoloInvalido — they can never be triggered by "
    "handoff_ans_submit_entry's NipProtocoloInvalidoError. A blank protocolo_ans therefore opens "
    "an unrecoverable engine incident on ST_HandoffAns{Manter,Conceder,NaoAssistencial} instead "
    "of routing to the neutral terminal End_NipProtocoloInvalido — the instance never completes. "
    "src/** fix (raising a gate-proven WorkerBpmnError instead) is out of scope for this port."
)

_NOTIFY_DEADLINE_RISK_UNREGISTERED_REASON = (
    "nip-specific gap (module docstring finding 2b) — BUILT, PENDING LIVE-PROOF FLIP "
    "(t2.5-p2b-nip-mechanical): register_nip_workers (nip.py) now registers "
    "notify_deadline_risk/notify_deadline_risk_entry for operadora.nip.notify_deadline_risk "
    "(mirrors cancel.notify_sla_risk/contas.notify_sla_risk/inadimplencia.notify_sla_risk/"
    "auth.NotifySlaRiskWorker's notify-only, non-adverse, fail-safe pattern) — the prior root "
    "cause ('registers NO handler at all') no longer holds. Three distinct BPMN service tasks "
    "route through this topic (SP-OP-NIP-001_Resposta_NIP.bpmn: ST_NotificarRiscoPrazo:272-273, "
    "ST_NotificarRiscoRevisao:358-359, and ST_SolicitarInfoNip:518-519, which reuses the same "
    "topic for the SOLICITAR_INFO branch) — all three are now fetchable/completable, and the "
    "ST_SolicitarInfoNip MAIN-token deadlock this finding also flagged (the SOLICITAR_INFO branch "
    "never reaching GW_AguardarInfo/ICE_InfoRecebida because the external task was never even "
    "drained) is structurally resolved as a byproduct. STILL XFAIL (strict, unflipped) because "
    "this worktree has no live-engine access to prove it end-to-end (HARD CONSTRAINT: no docker/"
    "live engine) — the worker + registration + unit coverage (test_notify_deadline_risk_* / "
    "test_register_nip_workers_matches_bpmn_topics_exactly, tests/unit/tools/workers/test_nip.py) "
    "is mechanical/unit-verified only, never run against CIB Seven. Two INDEPENDENT residual "
    "risks even on a live run: (1) test_prazo_nip_dispara_alerta_nao_interruptivo additionally "
    "asserts nip_probe.has_event(_NIP_DEADLINE_RISK) — a REAL agents.events.nip.deadline_risk "
    "Kafka publish — which notify_deadline_risk_entry deliberately does NOT perform (`del kafka "
    "# unused`, matching every other nip.py entry function; see _NIP_WORKER_KAFKA_GAP_REASON, "
    "finding 3, STILL open); (2) untested FEEL/engine wiring specifics (candidateGroups, timer "
    "boundary semantics) that only a live CIB Seven run can confirm. FLIP CANDIDATE: re-run this "
    "suite against a live engine; if green, remove this xfail from the affected test (and, "
    "separately, close finding 3 if has_event(_NIP_DEADLINE_RISK) is still required to pass). "
    "LIVE-ADJUDICATED (wave2b2 R1 live-validation, cibseven 2.1.0): test_solicitar_info_aguarda_"
    "e_retoma XPASSed strict on a real engine (the ST_SolicitarInfoNip main-token deadlock is "
    "GONE: SOLICITAR_INFO -> drain -> GW_AguardarInfo wait -> msg.nip.info_recebida correlation "
    "-> resume, all live) and its xfail was removed in-step. The other two tests were RETAGGED to "
    "_NIP_WORKER_KAFKA_GAP_REASON: --runxfail runs proved the worker executes and completes live "
    "on all three BPMN sites (engine logs worker_executing -> worker_completed -> "
    "audit_emit_once_persisted for operadora.nip.notify_deadline_risk) and the ONLY failing "
    "asserts are the dead kafka-channel observations (notifications_of_type / has_event(_NIP_"
    "DEADLINE_RISK) — agents.events.nip.deadline_risk exists in the BPMN only as a DANGLING "
    "event_topic_deadline_risk inputParameter on the worker tasks, never as a generic "
    "ST_Publish*/event_topic, so no publisher exists on any path). Constant retained for the "
    "docstring prose above; no test references it anymore.\n"
    "SUPERSEDED (T3.1 event-gap-nip-alerts, this pass, module docstring finding 2c): the "
    "'DANGLING event_topic_deadline_risk inputParameter... no publisher exists on any path' "
    "premise above no longer holds for ST_NotificarRiscoPrazo/ST_NotificarRiscoRevisao — both "
    "were converted in place to operadora.events.publish (see the BPMN task's own documentation "
    "and _NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON below). This constant is kept verbatim as the "
    "historical record of the 3-site dangling-param gap; the two tests that referenced it were "
    "retagged again, to _NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON."
)

# T3.1 event-gap-nip-alerts remedy B, CONVERT-IN-PLACE variant (module docstring finding 2c;
# contract SP-OP-NIP-001.md:111 "produz" agents.events.nip.deadline_risk): CLOSED the
# has_event(_NIP_DEADLINE_RISK) blocker for the 2 prazo tests above.
# ST_NotificarRiscoPrazo/ST_NotificarRiscoRevisao (SP-OP-NIP-001_Resposta_NIP.bpmn) now route
# through the generic, WORKING operadora.events.publish handler instead of the dict-first
# operadora.nip.notify_deadline_risk (which never called kafka.publish at these two sites — see
# _NIP_WORKER_KAFKA_GAP_REASON/finding 3, still open for every OTHER operadora.nip.* topic in this
# module). Unlike cancel/auth/reembolso/inadimplencia's own Wave-1/wave-4 remedy-B batches (each a
# SPLICE), this was a straight CONVERT-IN-PLACE. ST_SolicitarInfoNip (finding 2b) still consumes
# operadora.nip.notify_deadline_risk so the worker is not orphaned.
# RETIRED (recurso-flip convention, part4 R1 live validation): the constant
# _NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON below carried the pre-flip xfail(strict) reason. Both
# tests XPASS(strict) live (engine cibseven 2.1.0): ST_NotificarRiscoRevisao COMPLETED
# canceled=False carrying event_topic=agents.events.nip.deadline_risk on BK
# NIP-amh-NIP-TESTE-<run>; has_event(numero_nip_ans, sla_breach_task_name=_UT_REVISAO) matched.
# Markers removed; the constant is retired to this comment (no test references it anymore).

_ANCHOR_FAILSAFE_MISSING_REASON = (
    "nip-specific gap (module docstring finding 5 — regression vs the donor): the donor's "
    "instruct_dossier worker defaulted a blank/garbage data_recebimento_nip_iso to today's date "
    "(UTC) before it could reach BRT_NipSla's FEEL evaluation. v2's assemble_response/"
    "instruct_dossier_entry (nip.py:206-238,446-454) carries NO such validation/defaulting — "
    "NipInput.data_recebimento_nip_iso (nip.py:73) is stored and returned completely unvalidated, "
    "and no BPMN outputParameter/inputParameter defaults it either (grep confirms zero hits for a "
    "default expression on this variable anywhere in SP-OP-NIP-001_Resposta_NIP.bpmn). "
    "nip_sla.dmn's FEEL expression (nip_sla.dmn:64,74,84,94) is "
    "'date and time(data_recebimento_nip_iso + \"T00:00:00\")' — a blank string yields "
    "'T00:00:00' and 'not-a-date' yields 'not-a-dateT00:00:00', neither of which date and time(...) "
    "can parse; BRT_NipSla fails DMN evaluation and opens an incident BEFORE "
    "UT_RevisaoJuridicaNip is ever created — exactly the catastrophic failure mode this test "
    "exists to prove is fail-safe. await_user_task(iid, _UT_REVISAO) times out. src/** fix "
    "(porting the donor's anchor-defaulting logic into instruct_dossier/instruct_dossier_entry) "
    "is out of scope for this port."
)


# ---------------------------------------------------------------------------
# EngineProbe para NIP (espelha CancelEngineProbe/AuthEngineProbe; self-contained per donor's own
# convention — "NAO edita o conftest.py compartilhado" para a construcao especifica do probe).
# ---------------------------------------------------------------------------


@dataclass
class NipEngineProbe:
    """Driva os workers reais de Phase-2 (NIP) contra o engine CIB Seven."""

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
        await drain_topics(self.transport, self.harness, self.worker_id, _NIP_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de NIP da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_CLASS, _DMN_ROUTING, _DMN_SLA, name="SP-OP-NIP-001-qa")


@pytest_asyncio.fixture
async def nip_probe(engine: EngineRest, audit_sink: Any, audit_tenant: str) -> AsyncIterator[NipEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-2 de NIP."""
    worker_id = f"qa-nip-worker-{uuid.uuid4().hex[:8]}"
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
    register_nip_workers(harness, kafka)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in
    # this BPMN routes through — mirrors auth/cancel/escalation's own composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors cancel's pattern, finding 2 above): todo topico operadora.nip.*
    # registrado no harness DEVE estar na lista de drain — falha AQUI, explicita, se um worker
    # novo ficar fora (ou se notify_deadline_risk deixar de estar ausente sem este arquivo ser
    # atualizado).
    nip_registered = {t for t in harness.registered_topics if t.startswith("operadora.nip.")}
    missing_from_drain = nip_registered - set(_NIP_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_NIP_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = NipEngineProbe(
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


def _unique_nip(prefix: str = "NIP-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_nip(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key NIP-amh-{nip} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (NIP-TESTE-NNNN, tenant amh). Os fatos de entrada
    das DMNs (classificacao_nip, tema_nip, contesta_negativa, documentacao_suficiente) sao seeded
    como variaveis de start (o teste e o agente de origem; mesma tecnica de auth/cancel).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        nip = overrides.pop("numero_nip_ans", _unique_nip())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_nip_ans": nip,
            "protocolo_ans": "PROTO-TESTE-0001",
            "beneficiario_pseudo_id": "BEN-TESTE-001",
            # GAP-NIP-1: os timers usam timeDate absoluto = data_recebimento_nip_iso + duracao
            # (BRT_NipSla/DMN nip_sla). Uma data FIXA no passado ficaria obsoleta assim que o
            # relogio real passa dela: data_recebimento_nip_iso+P10D (o maior prazo,
            # nao-assistencial) cairia no passado e o boundary interruptivo dispararia
            # IMEDIATAMENTE ao attach da UT — quebrando todo teste que espera chegar a
            # UT_ElaborarRespostaNip/UT_RevisaoJuridicaNip ANTES do estouro. Ancora calculada
            # relativa a "agora" (+14 dias — folga confortavel acima do maior prazo P10D) para que
            # o default nunca "expire" (engine-deployabilidade — ver nip_sla.dmn). Testes que
            # PRECISAM disparar o timer usam execute_job (nunca dependem do relogio real).
            "data_recebimento_nip_iso": (datetime.now(UTC) + timedelta(days=14)).date().isoformat(),
            "documentos_refs": "[]",
            "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            # fatos pre-resolvidos por worker (seeded — DMNs os consomem como entrada)
            "classificacao_nip": "assistencial",
            "tema_nip": "negativa_cobertura",
            "contesta_negativa": True,
            "documentacao_suficiente": True,
            "origem_a2a": False,
            # decisao_nip e setado pelo humano na User Task (UT_RevisaoJuridicaNip/UT_CoordenacaoNip)
            # antes de GW_DecisaoNip; inicializa-se vazio no start para que o identificador seja
            # SEMPRE resolvivel pelo engine ao avaliar a condicao do gateway (defesa em profundidade
            # contra ENGINE-16004 "Cannot resolve identifier"; o default flow trata o vazio ->
            # RESPONDER_NAO_ASSISTENCIAL). Nenhuma DMN o produz (L0 hard).
            "decisao_nip": "",
        }
        variables.update(overrides)
        business_key = f"NIP-amh-{nip}"
        return await engine.start_by_key("SP-OP-NIP-001", business_key, variables)

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


async def _assert_no_manter_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que End_NipNegativaMantida nao existe sem UT humana.

    Consulta a historia do engine para verificar que:
    1. Se End_NipNegativaMantida esta no historico -> pelo menos uma das User Tasks humanas
       (_UT_HUMANAS_MANTER) tambem esta no historico.
    2. Prova negativa: se End_NipNegativaMantida nao esta no historico -> ok por vacuidade.

    Esta e a prova de que NAO existe caminho automatizado que mantenha a negativa NIP.
    """
    ended = await engine.activity_instances_ended(iid)
    if _END_NEGATIVA_MANTIDA in ended:
        human_tasks_in_history = ended & _UT_HUMANAS_MANTER
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: End_NipNegativaMantida atingido para instancia {iid} "
            f"SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_MANTER}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de manutencao de negativa — violacao do L0 hard "
            "(ADR-0005)."
        )


async def _drive_to_revisao(engine: EngineRest, probe: NipEngineProbe, iid: str) -> Any:
    """Drena ate UT_RevisaoJuridicaNip surgir (dossie de Gustavo preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_REVISAO)


async def _drive_to_elaborar(engine: EngineRest, probe: NipEngineProbe, iid: str) -> Any:
    """Drena ate UT_ElaborarRespostaNip surgir."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ELABORAR)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs das DMNs)
# ===========================================================================


async def test_manter_negativa_so_via_user_task_humana(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado mantem a negativa NIP.

    Varredura de TODAS as combinacoes de input das DMNs nip_classification / nip_routing / nip_sla:
      classificacao_nip in {assistencial, nao_assistencial}
      tema_nip in {negativa_cobertura, prazo_atendimento, reembolso, rede, cobranca, <desconhecido>}
      contesta_negativa in {true, false}
      documentacao_suficiente in {true, false}
    (2 * 6 * 2 * 2 = 48 combinacoes).

    Para cada instancia que percorre ate estabilizar:
    - Se atingiu End_NipNegativaMantida -> a historia do engine DEVE conter uma UT humana
      (UT_RevisaoJuridicaNip / UT_CoordenacaoNip). Como nenhuma combinacao completa a UT
      automaticamente, NENHUMA instancia deve atingir End_NipNegativaMantida nesta varredura.
    - A manutencao da negativa so nasce com decisao_nip=MANTER_NEGATIVA setado por humano (testado
      a parte).

    A prova e feita via historia do engine (history/activity-instance).
    """
    classificacoes = ["assistencial", "nao_assistencial"]
    temas = [
        "negativa_cobertura",
        "prazo_atendimento",
        "reembolso",
        "rede",
        "cobranca",
        "tema_xyz_desconhecido",
    ]
    bools = [True, False]
    checked = 0

    for classificacao, tema, contesta, docs in itertools.product(classificacoes, temas, bools, bools):
        inst = await start_nip(
            classificacao_nip=classificacao,
            tema_nip=tema,
            contesta_negativa=contesta,
            documentacao_suficiente=docs,
        )
        iid = inst["id"]
        await nip_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        # NENHUMA combinacao pode atingir o terminal adverso sem decisao humana.
        assert _END_NEGATIVA_MANTIDA not in ended, (
            f"L0 VIOLADO: classificacao={classificacao} tema={tema} contesta={contesta} "
            f"docs={docs} atingiu End_NipNegativaMantida automaticamente. ended={ended}"
        )
        # Invariante de historia (prova formal).
        await _assert_no_manter_without_human_task(engine, iid)
        checked += 1

    assert checked == 48, f"Esperava 48 combinacoes varridas; varri {checked}"


async def test_inelegibilidade_roteia_para_humano_nunca_auto_nega(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """documentacao_suficiente=false => nip_routing -> PENDENTE_INFO/REVISAO_JURIDICA -> humano.

    Fluxo chega a UT_RevisaoJuridicaNip (juridico-regulatorio) — NAO a End_NipNegativaMantida nem a
    qualquer fim sem User Task. Inelegibilidade/sem base roteia, nunca auto-nega.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="prazo_atendimento",
        contesta_negativa=False,
        documentacao_suficiente=False,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    assert "juridico-regulatorio" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended, "Inelegibilidade nao deve manter negativa automaticamente (L0)"
    await _assert_no_manter_without_human_task(engine, iid)


async def test_tema_desconhecido_fail_safe_juridico(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """tema_nip desconhecido (linha catch-all) => classifica ASSISTENCIAL_CONTESTA_NEGATIVA + juridico.

    nip_classification/nip_routing roteiam para juridico-regulatorio (revisao); nunca classifica
    como NAO_ASSISTENCIAL respondida automaticamente. Fluxo chega a UT_RevisaoJuridicaNip.
    """
    # Mesmo com classificacao_nip=nao_assistencial, tema desconhecido => catch-all (assistencial-contesta).
    inst = await start_nip(
        classificacao_nip="nao_assistencial",
        tema_nip="tema_xyz_desconhecido",
        contesta_negativa=False,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    assert "juridico-regulatorio" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_NAO_ASSISTENCIAL not in ended, (
        "Tema desconhecido nunca vira nao-assistencial respondida auto (L0)"
    )
    assert _END_NEGATIVA_MANTIDA not in ended
    await _assert_no_manter_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_NIP_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_negativa_mantida_pelo_humano(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Humano completa MANTER_NEGATIVA com campos obrigatorios => End_NipNegativaMantida.

    submit_response executado (guard satisfeito); nip.completed desfecho=negativa_mantida; revisor_id
    na trilha de auditoria; handoff a ANS-SUBMIT. Este e o UNICO caminho ao terminal adverso.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    assert "juridico-regulatorio" in ut.candidate_groups

    # Dossie de Gustavo deve ter sido preparado pelo worker real
    dossiers = nip_probe.notifications_of_type("nip.instruct_dossier")
    assert dossiers, "Worker instruct_dossier (Gustavo) deve ter sido executado"

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "MANTER_NEGATIVA",
            "fundamentacao_regulatoria": "Exclusao contratual fundamentada (teste L0)",
            "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            "texto_resposta_nip": "Resposta sintetica mantendo a negativa (teste)",
            "revisor_id": "revisor-sintetico-001",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    # PROVA: End_NipNegativaMantida so existe porque a UT humana foi completada com MANTER_NEGATIVA.
    await _assert_no_manter_without_human_task(engine, iid)
    assert _END_NEGATIVA_MANTIDA in ended, f"Manter humano deve atingir End_NipNegativaMantida. ended={ended}"
    assert nip_probe.has_event(_NIP_COMPLETED, desfecho="negativa_mantida")

    # HARD GUARDRAIL: submit_response REGISTRA decisao do humano — verificar campos.
    submits = nip_probe.notifications_of_type("nip.submit_response")
    assert submits, "Worker submit_response deve ser executado apos manter humano"
    s = submits[0]
    assert s["decisao_nip"] == "MANTER_NEGATIVA"
    assert s["revisor_id"] == "revisor-sintetico-001"

    # Handoff a ANS-SUBMIT executado com payload de filing.
    handoffs = nip_probe.notifications_of_type("nip.handoff_ans_submit")
    assert handoffs, "Worker handoff_ans_submit deve ser executado"
    assert handoffs[0]["revisor_id"] == "revisor-sintetico-001"


async def test_happy_path_resolvida_favoravel(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Humano completa CONCEDER => End_NipResolvidaFavoravel; nip.completed desfecho=resolvida_favoravel."""
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "CONCEDER",
            "texto_resposta_nip": "Resposta sintetica concedendo o pleito (teste)",
            "revisor_id": "revisor-sintetico-002",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_RESOLVIDA_FAVORAVEL in ended, f"CONCEDER => End_NipResolvidaFavoravel. ended={ended}"
    assert _END_NEGATIVA_MANTIDA not in ended
    assert nip_probe.has_event(_NIP_COMPLETED, desfecho="resolvida_favoravel")
    await _assert_no_manter_without_human_task(engine, iid)


async def test_happy_path_nao_assistencial_respondida(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """NIP nao-assistencial (cobranca) com docs => ELABORAR_RESPOSTA -> nucleo-ans autora e confirma.

    O humano elabora a minuta e na revisao confirma RESPONDER_NAO_ASSISTENCIAL =>
    End_NipNaoAssistencialRespondida; mesmo no caminho clerical o envio passa por User Task
    (nunca auto-submete).
    """
    inst = await start_nip(
        classificacao_nip="nao_assistencial",
        tema_nip="cobranca",
        contesta_negativa=False,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    # roteamento ELABORAR_RESPOSTA -> UT_ElaborarRespostaNip (nucleo-ans)
    ut_elaborar = await _drive_to_elaborar(engine, nip_probe, iid)
    assert "nucleo-ans" in ut_elaborar.candidate_groups
    await engine.complete_task_as_human(
        ut_elaborar.id,
        {"texto_resposta_nip": "Minuta clerical sintetica (teste)", "decisao_nip": "ENCAMINHAR_REVISAO"},
    )

    # Minuta encaminhada a revisao -> UT_RevisaoJuridicaNip confirma o envio
    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "decisao_nip": "RESPONDER_NAO_ASSISTENCIAL",
            "texto_resposta_nip": "Resposta clerical confirmada (teste)",
            "revisor_id": "revisor-sintetico-003",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NAO_ASSISTENCIAL in ended, f"=> End_NipNaoAssistencialRespondida. ended={ended}"
    assert _END_NEGATIVA_MANTIDA not in ended
    assert nip_probe.has_event(_NIP_COMPLETED, desfecho="nao_assistencial_respondida")
    await _assert_no_manter_without_human_task(engine, iid)


# ===========================================================================
# Boundary-error catches (finding 4, GAP-NIP-6, dangling-catch prevention) — guard TECNICO na
# correlacao com ANS-SUBMIT deveria terminar LIMPO; em v2 ele NUNCA alcanca o boundary catch
# (NipProtocoloInvalidoError e ValueError, nao WorkerBpmnError — ver
# _PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON). Um teste por ramo (MANTER/CONCEDER/NAO_ASSISTENCIAL)
# porque cada um instancia um serviceTask/boundaryEvent DISTINTO (ST_HandoffAnsManter/Conceder/
# NaoAssistencial), mesmo handler `handoff_ans_submit_entry` serve as tres.
# ===========================================================================


@pytest.mark.xfail(reason=_PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON, strict=True)
async def test_protocolo_ans_em_branco_manter_termina_em_protocolo_invalido(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """protocolo_ans="   " (presente porem em branco) + MANTER_NEGATIVA => ST_HandoffAnsManter
    lanca ERR_NIP_PROTOCOLO_INVALIDO (guard de correlacao, nunca merito — decisao_nip ja foi
    fixada pela UT humana). O boundary catch BE_NipProtocoloInvalidoManter captura o erro e
    termina a instancia LIMPO em End_NipProtocoloInvalido — NUNCA em End_NipNegativaMantida (o
    guard dispara ANTES de ST_PublishNegativaMantida publicar nip.completed)."""
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
        protocolo_ans="   ",
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "MANTER_NEGATIVA",
            "fundamentacao_regulatoria": "Exclusao contratual fundamentada (teste GAP-NIP-6)",
            "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            "texto_resposta_nip": "Resposta sintetica mantendo a negativa (teste)",
            "revisor_id": "revisor-sintetico-nip6-manter",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_PROTOCOLO_INVALIDO in ended, (
        f"protocolo_ans em branco (ramo MANTER_NEGATIVA) deve terminar LIMPO em "
        f"{_END_PROTOCOLO_INVALIDO} (boundary catch BE_NipProtocoloInvalidoManter), nao travar "
        f"silenciosamente sem end event. ended={ended}"
    )
    assert _END_NEGATIVA_MANTIDA not in ended, (
        "Guard de correlacao nunca deve deixar a instancia alcancar o terminal adverso"
    )
    # submit_response JA rodou (precede o handoff na sequencia) — mas o handoff foi recusado
    # ANTES de publicar, entao nip.completed (negativa_mantida) NUNCA foi emitido.
    assert nip_probe.notifications_of_type("nip.submit_response"), (
        "submit_response precede o handoff no flow — deve ter executado"
    )
    assert not nip_probe.notifications_of_type("nip.handoff_ans_submit"), (
        "Guard recusa ANTES de publicar o registro do handoff"
    )
    assert not nip_probe.has_event(_NIP_COMPLETED, desfecho="negativa_mantida")
    await _assert_no_manter_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON, strict=True)
async def test_protocolo_ans_em_branco_conceder_termina_em_protocolo_invalido(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """protocolo_ans="   " + CONCEDER => ST_HandoffAnsConceder lanca ERR_NIP_PROTOCOLO_INVALIDO.
    O boundary catch BE_NipProtocoloInvalidoConceder termina a instancia LIMPO no MESMO terminal
    compartilhado End_NipProtocoloInvalido — nao em End_NipResolvidaFavoravel."""
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
        protocolo_ans="   ",
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "CONCEDER",
            "texto_resposta_nip": "Resposta sintetica concedendo o pleito (teste GAP-NIP-6)",
            "revisor_id": "revisor-sintetico-nip6-conceder",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_PROTOCOLO_INVALIDO in ended, (
        f"protocolo_ans em branco (ramo CONCEDER) deve terminar LIMPO em "
        f"{_END_PROTOCOLO_INVALIDO} (boundary catch BE_NipProtocoloInvalidoConceder). ended={ended}"
    )
    assert _END_RESOLVIDA_FAVORAVEL not in ended
    assert _END_NEGATIVA_MANTIDA not in ended
    assert not nip_probe.notifications_of_type("nip.handoff_ans_submit"), (
        "Guard recusa ANTES de publicar o registro do handoff"
    )
    assert not nip_probe.has_event(_NIP_COMPLETED, desfecho="resolvida_favoravel")
    await _assert_no_manter_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PROTOCOLO_INVALIDO_NOT_BPMN_ERROR_REASON, strict=True)
async def test_protocolo_ans_em_branco_nao_assistencial_termina_em_protocolo_invalido(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """protocolo_ans="   " + RESPONDER_NAO_ASSISTENCIAL => ST_HandoffAnsNaoAssistencial lanca
    ERR_NIP_PROTOCOLO_INVALIDO. O boundary catch BE_NipProtocoloInvalidoNaoAssist termina a
    instancia LIMPO no MESMO terminal compartilhado — nao em End_NipNaoAssistencialRespondida."""
    inst = await start_nip(
        classificacao_nip="nao_assistencial",
        tema_nip="cobranca",
        contesta_negativa=False,
        documentacao_suficiente=True,
        protocolo_ans="   ",
    )
    iid = inst["id"]

    ut_elaborar = await _drive_to_elaborar(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut_elaborar.id,
        {
            "texto_resposta_nip": "Minuta clerical sintetica (teste GAP-NIP-6)",
            "decisao_nip": "ENCAMINHAR_REVISAO",
        },
    )

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "decisao_nip": "RESPONDER_NAO_ASSISTENCIAL",
            "texto_resposta_nip": "Resposta clerical confirmada (teste GAP-NIP-6)",
            "revisor_id": "revisor-sintetico-nip6-naoassist",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_PROTOCOLO_INVALIDO in ended, (
        f"protocolo_ans em branco (ramo RESPONDER_NAO_ASSISTENCIAL) deve terminar LIMPO em "
        f"{_END_PROTOCOLO_INVALIDO} (boundary catch BE_NipProtocoloInvalidoNaoAssist). ended={ended}"
    )
    assert _END_NAO_ASSISTENCIAL not in ended
    assert _END_NEGATIVA_MANTIDA not in ended
    assert not nip_probe.notifications_of_type("nip.handoff_ans_submit"), (
        "Guard recusa ANTES de publicar o registro do handoff"
    )
    assert not nip_probe.has_event(_NIP_COMPLETED, desfecho="nao_assistencial_respondida")
    await _assert_no_manter_without_human_task(engine, iid)


async def test_grupo_humano_dmn_decide_candidate_groups_nao_assistencial(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """NAO_ASSISTENCIAL + docs => nip_routing.grupo_humano='nucleo-ans' -> candidateGroups EXATO.

    Prova que candidateGroups de UT_ElaborarRespostaNip vem de ${grupo_humano} (variavel flat
    promovida por outputParameter em BRT_Roteamento a partir de roteamento_nip.grupo_humano —
    root-cause fix do ENGINE-16004/500 em ST_PublishClassified/complete; ver nota em BRT_Roteamento
    do BPMN) — nao mais do literal estatico "regulatorio-ans,nucleo-ans". Se ainda fosse estatico,
    "regulatorio-ans" tambem apareceria aqui; o teste prova que NAO aparece.
    """
    inst = await start_nip(
        classificacao_nip="nao_assistencial",
        tema_nip="cobranca",
        contesta_negativa=False,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_elaborar(engine, nip_probe, iid)
    assert ut.candidate_groups == frozenset({"nucleo-ans"}), (
        f"nip_routing (NAO_ASSISTENCIAL+docs) deve resolver grupo_humano=nucleo-ans EXATO "
        f"(DMN-decidido, GAP-NIP-2); candidate_groups={ut.candidate_groups}"
    )


async def test_grupo_humano_dmn_decide_candidate_groups_assistencial_outro(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """ASSISTENCIAL_OUTRO + docs => nip_routing.grupo_humano='regulatorio-ans' -> candidateGroups EXATO.

    Segunda linha da DMN prova que o valor MUDA por combinacao de input — nao seria possivel com
    um candidateGroups estatico. tema_nip='prazo_atendimento' + contesta_negativa=False classifica
    ASSISTENCIAL_OUTRO (nip_classification); nip_routing roteia ELABORAR_RESPOSTA + regulatorio-ans.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="prazo_atendimento",
        contesta_negativa=False,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_elaborar(engine, nip_probe, iid)
    assert ut.candidate_groups == frozenset({"regulatorio-ans"}), (
        f"nip_routing (ASSISTENCIAL_OUTRO+docs) deve resolver grupo_humano=regulatorio-ans EXATO "
        f"(DMN-decidido, GAP-NIP-2); candidate_groups={ut.candidate_groups}"
    )

    # UT_RevisaoJuridicaNip permanece candidateGroups ESTATICO mesmo apos uma rodada de
    # ELABORAR_RESPOSTA que resolveu grupo_humano=regulatorio-ans (nao deve "vazar" para a
    # revisao juridica — ver nota em BRT_Roteamento/UT_RevisaoJuridicaNip do BPMN).
    await engine.complete_task_as_human(
        ut.id,
        {"texto_resposta_nip": "Minuta sintetica (teste GAP-NIP-2)", "decisao_nip": "ENCAMINHAR_REVISAO"},
    )
    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO)
    assert ut_revisao.candidate_groups == frozenset({"juridico-regulatorio", "medico-auditor"}), (
        "UT_RevisaoJuridicaNip DEVE permanecer juridico-regulatorio,medico-auditor mesmo apos "
        f"grupo_humano ter resolvido regulatorio-ans na rodada de elaboracao; "
        f"candidate_groups={ut_revisao.candidate_groups}"
    )


# ===========================================================================
# Manter exige campos / worker guard
# ===========================================================================


async def test_manter_negativa_exige_campos_obrigatorios(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """MANTER_NEGATIVA sem fundamentacao/referencia/revisor_id => worker guard recusa.

    O engine despacha submit_response; o worker lanca ERR_NIP_NEGATIVA_NOT_HUMAN (defesa em
    profundidade); a instancia NAO atinge End_NipNegativaMantida (fica com incidente no worker).
    Espelha test_aceitar_glosa_exige_campos de CONTAS / test_negar_exige_campos de AUTH.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    # Completa com MANTER_NEGATIVA mas SEM os campos obrigatorios.
    await engine.complete_task_as_human(ut.id, {"decisao_nip": "MANTER_NEGATIVA"})
    await nip_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended, (
        "Manter sem campos obrigatorios NAO pode atingir End_NipNegativaMantida (guard do worker)"
    )
    submits = nip_probe.notifications_of_type("nip.submit_response")
    assert not submits, "submit_response NAO deve transmitir manter-negativa sem campos obrigatorios"
    assert not nip_probe.has_event(_NIP_COMPLETED, desfecho="negativa_mantida")
    await _assert_no_manter_without_human_task(engine, iid)


async def test_worker_submit_response_recusa_negativa_sem_humano() -> None:
    """Invocacao direta do entry function submit_response_entry sem decisao humana.

    Unit-style sobre o handler real (SEM engine — nao depende do nip_probe/engine, roda mesmo sem o
    dev-stack): com decisao_nip=MANTER_NEGATIVA e campos ausentes => NipNegativaNotHumanError
    (ERR_NIP_NEGATIVA_NOT_HUMAN); nao transmite. Com todos os campos setados por humano => transmite
    e carrega revisor_id. CONCEDER nunca e bloqueado pelo guard.

    ADAPTED (port rule 1, verify each register_*/entry-function shape on v2 main): v2's
    `submit_response_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary, no
    `ExternalTask`) replaces donor's `make_submit_response_handler(kafka) ->
    Callable[[ExternalTask], ...]`; the guard raises `NipNegativaNotHumanError`
    (`PermissionError` subclass) rather than `WorkerBpmnError(error_code=...)` — see module
    docstring finding 4. The 4 guard scenarios (a-d) are preserved verbatim; only the exception
    type asserted and the "success" shape changed to match what v2's function actually returns
    (v2's submit_to_ans does not itself call kafka.publish nor compute a submit_id — see
    _NIP_WORKER_KAFKA_GAP_REASON).
    """
    kafka = FakeKafkaPublisher()

    # (a) MANTER_NEGATIVA sem nenhum campo -> recusa
    with pytest.raises(NipNegativaNotHumanError) as exc_a:
        submit_response_entry({"decisao_nip": "MANTER_NEGATIVA"}, kafka=kafka)
    assert "ERR_NIP_NEGATIVA_NOT_HUMAN" in str(exc_a.value)

    # (b) MANTER_NEGATIVA com fundamentacao mas SEM revisor_id -> recusa
    with pytest.raises(NipNegativaNotHumanError) as exc_b:
        submit_response_entry(
            {
                "decisao_nip": "MANTER_NEGATIVA",
                "fundamentacao_regulatoria": "x",
                "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            },
            kafka=kafka,
        )
    assert "ERR_NIP_NEGATIVA_NOT_HUMAN" in str(exc_b.value)
    assert not kafka.published, "Nenhum registro deve ser publicado quando o guard recusa"

    # (c) decisao humana completa de manter -> transmite e carrega revisor_id
    result_c = submit_response_entry(
        {
            "decisao_nip": "MANTER_NEGATIVA",
            "fundamentacao_regulatoria": "Exclusao contratual fundamentada (teste)",
            "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            "revisor_id": "revisor-sintetico-001",
            "texto_resposta_nip": "Resposta sintetica mantendo a negativa (teste)",
            "tenant_id": "amh",
            "numero_nip_ans": "NIP-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_c["submitted"] is True
    assert result_c["decisao_nip"] == "MANTER_NEGATIVA"
    assert result_c["revisor_id"] == "revisor-sintetico-001"

    # (d) CONCEDER nunca e bloqueado pelo guard (nao e efeito adverso)
    kafka2 = FakeKafkaPublisher()
    result_d = submit_response_entry(
        {
            "decisao_nip": "CONCEDER",
            "texto_resposta_nip": "Concede o pleito (teste)",
            "revisor_id": "revisor-sintetico-002",
            "tenant_id": "amh",
            "numero_nip_ans": "NIP-TESTE-CONCEDER",
        },
        kafka=kafka2,
    )
    assert result_d["submitted"] is True
    assert result_d["decisao_nip"] == "CONCEDER"


async def test_resposta_final_nunca_gerada_por_dmn(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """ELABORAR_RESPOSTA => submit_response NUNCA executa antes da User Task humana.

    Para qualquer combinacao que produza roteamento=ELABORAR_RESPOSTA, o fluxo chega a uma User Task
    de elaboracao (nenhuma DMN escreve texto_resposta_nip ou decisao_nip); submit_response so corre
    apos a revisao humana setar revisor_id.
    """
    inst = await start_nip(
        classificacao_nip="nao_assistencial",
        tema_nip="cobranca",
        contesta_negativa=False,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut_elaborar = await _drive_to_elaborar(engine, nip_probe, iid)
    assert "nucleo-ans" in ut_elaborar.candidate_groups

    # Antes de qualquer decisao humana, submit_response NAO pode ter corrido.
    submits = nip_probe.notifications_of_type("nip.submit_response")
    assert not submits, "submit_response nunca corre antes da User Task humana (texto e humano)"
    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended
    await _assert_no_manter_without_human_task(engine, iid)


# ===========================================================================
# Classificacao e roteamento
# ===========================================================================


async def test_dmn_nip_classification_assistencial_contesta_negativa(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """classificacao_nip=assistencial, tema=negativa_cobertura, contesta=true => revisao juridica.

    nip_classification retorna ASSISTENCIAL_CONTESTA_NEGATIVA + juridico-regulatorio; nip.classified
    publicado com prazo_dias; o fluxo chega a UT_RevisaoJuridicaNip.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    assert "juridico-regulatorio" in ut.candidate_groups
    assert nip_probe.has_event(_NIP_CLASSIFIED), "nip.classified deve ser publicado"
    assert nip_probe.has_event(_NIP_RECEIVED), "nip.received deve ser publicado"


@pytest.mark.xfail(reason=_NIP_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_a2a_start_via_nip_instruct(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    deploy_artifacts: str,
) -> None:
    """Delegacao A2A nip.instruct (Helena/intake -> Gustavo) inicia a instancia por mensagem.

    msg.nip.instruct correlaciona/inicia a business key; instancia inicia (nip.received); convoca
    instruct_dossier (Gustavo monta dossie, nao decide merito).
    """
    nip = _unique_nip("NIP-A2A")
    business_key = f"NIP-amh-{nip}"
    correlate_payload = {
        "messageName": "msg.nip.instruct",
        "businessKey": business_key,
        "processVariables": {
            "tenant_id": {"value": "amh", "type": "String"},
            "numero_nip_ans": {"value": nip, "type": "String"},
            "protocolo_ans": {"value": "PROTO-TESTE-A2A", "type": "String"},
            "beneficiario_pseudo_id": {"value": "BEN-TESTE-001", "type": "String"},
            # GAP-NIP-1: ancora do timeDate absoluto — usar futuro dinamico (ver start_nip).
            "data_recebimento_nip_iso": {
                "value": (datetime.now(UTC) + timedelta(days=14)).date().isoformat(),
                "type": "String",
            },
            "documentos_refs": {"value": "[]", "type": "String"},
            "referencia_negativa_original": {"value": "AUTH-amh-GUIA-TESTE-0001", "type": "String"},
            "classificacao_nip": {"value": "assistencial", "type": "String"},
            "tema_nip": {"value": "negativa_cobertura", "type": "String"},
            "contesta_negativa": {"value": True, "type": "Boolean"},
            "documentacao_suficiente": {"value": True, "type": "Boolean"},
            "origem_a2a": {"value": True, "type": "Boolean"},
            # decisao_nip inicializado vazio (setado pelo humano antes de GW_DecisaoNip; ver start_nip)
            "decisao_nip": {"value": "", "type": "String"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Start por msg.nip.instruct falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    active = await engine.find_active_instances(business_key)
    assert active, "Mensagem nip.instruct deve iniciar uma instancia"

    await nip_probe.drain()
    dossiers = nip_probe.notifications_of_type("nip.instruct_dossier")
    assert dossiers, "instruct_dossier (Gustavo) deve ser convocado no start A2A"
    assert nip_probe.has_event(_NIP_RECEIVED), "nip.received deve ser publicado no start A2A"


# ===========================================================================
# Prazos / SLA (HARD — DRAFT/verify regulatorio)
# ===========================================================================


# T3.1 event-gap-nip-alerts FLIPPED (live-proven, part4 R1 validation, engine cibseven 2.1.0):
# ST_NotificarRiscoRevisao (converted in place to operadora.events.publish, finding 2c) COMPLETED
# canceled=False on BK NIP-amh-NIP-TESTE-<run> carrying event_topic=agents.events.nip.deadline_risk;
# has_event(_NIP_DEADLINE_RISK, numero_nip_ans, sla_breach_task_name=_UT_REVISAO) matched. Prior
# xfail(_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON, strict) removed on XPASS(strict).
async def test_prazo_nip_dispara_alerta_nao_interruptivo(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Timer BT_AlertaPrazoRevisao (nao-interruptivo): ST_NotificarRiscoRevisao recebe task; UT segue aberta.

    nip.deadline_risk publicado (countdown WD.3). A User Task de revisao segue aberta (nao-interruptivo).
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    await _drive_to_revisao(engine, nip_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaPrazoRevisao")
    await engine.execute_job(job.id)
    await nip_probe.drain()

    # T3.1 event-gap-nip-alerts (has_event adaptation, mirrors the #108/#123/cancel+auth Wave-1
    # precedent): the old execution-proof assert (`notifications_of_type("nip.notify_deadline_
    # risk")`) is now structurally dead — ST_NotificarRiscoRevisao was converted in place to
    # operadora.events.publish (module docstring finding 2c), so notify_deadline_risk_entry no
    # longer runs at this site at all. Folded into a single has_event() call with
    # sla_breach_task_name/numero_nip_ans matched from the declared payload (this task's
    # event_payload_vars), which ALSO proves this SPECIFIC alert (revisao phase, not elaboracao)
    # fired for this instance.
    numero_nip_ans = await engine.get_variable(iid, "numero_nip_ans")
    assert nip_probe.has_event(
        _NIP_DEADLINE_RISK, numero_nip_ans=numero_nip_ans, sla_breach_task_name=_UT_REVISAO
    ), "nip.deadline_risk deve ser publicado (ST_NotificarRiscoRevisao, countdown WD.3)"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAO in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task de revisao"


async def test_prazo_nip_estourado_coordenacao_assume_interruptivo(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Timer interruptivo BT_PrazoRevisaoEstourado: cancela UT_RevisaoJuridicaNip; cria UT_CoordenacaoNip.

    nip.breached publicado (metrica de compliance — exposicao a sancao). NAO ha auto-resposta por
    timeout (inversao do anti-padrao) — a decisao continua humana.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    await _drive_to_revisao(engine, nip_probe, iid)

    job = await engine.await_timer_job(iid, "BT_PrazoRevisaoEstourado")
    await engine.execute_job(job.id)
    await nip_probe.drain()

    assert nip_probe.has_event(_NIP_BREACHED), "nip.breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert (
        "coordenacao-regulatorio" in ut_coord.candidate_groups
        or "juridico-regulatorio" in ut_coord.candidate_groups
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAO not in open_keys, "UT_RevisaoJuridicaNip deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended, "Estouro de prazo nunca auto-mantem negativa (L0)"
    await _assert_no_manter_without_human_task(engine, iid)


async def test_coordenacao_assume_e_mantem(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Prazo estourado; coordenacao assume e MANTER_NEGATIVA => End_NipNegativaMantida com UT humana.

    Prova que mesmo no caminho de escalonamento a manutencao passa por UT humana (invariante).
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    await _drive_to_revisao(engine, nip_probe, iid)
    job = await engine.await_timer_job(iid, "BT_PrazoRevisaoEstourado")
    await engine.execute_job(job.id)
    await nip_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_nip": "MANTER_NEGATIVA",
            "fundamentacao_regulatoria": "Prazo esgotado — coordenacao mantem fundamentada (sintetico)",
            "referencia_negativa_original": "AUTH-amh-GUIA-TESTE-0001",
            "texto_resposta_nip": "Resposta sintetica (coordenacao)",
            "revisor_id": "coordenacao-sintetica-001",
        },
    )
    await nip_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGATIVA_MANTIDA in ended
    await _assert_no_manter_without_human_task(engine, iid)
    assert nip_probe.has_event(_NIP_COMPLETED, desfecho="negativa_mantida")


async def test_dmn_nip_sla_assistencial_prazo_mais_curto(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """classificacao=ASSISTENCIAL_CONTESTA_NEGATIVA => nip_sla resolve prazo assistencial; timers existem.

    Prova indireta: os timers BT_AlertaPrazoRevisao / BT_PrazoRevisaoEstourado usam ${sla.*} da DMN.
    Se os jobs de timer existem, a DMN resolveu as duracoes corretamente.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    await _drive_to_revisao(engine, nip_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_AlertaPrazoRevisao")
    assert job_alerta.activity_id == "BT_AlertaPrazoRevisao"
    job_prazo = await engine.await_timer_job(iid, "BT_PrazoRevisaoEstourado")
    assert job_prazo.activity_id == "BT_PrazoRevisaoEstourado"


# T3.1 event-gap-nip-alerts FLIPPED (live-proven, part4 R1 validation, engine cibseven 2.1.0):
# same as test_prazo_nip_dispara_alerta_nao_interruptivo above — ST_NotificarRiscoRevisao COMPLETED
# canceled=False carrying event_topic=agents.events.nip.deadline_risk; strengthened has_event
# (numero_nip_ans, sla_breach_task_name=_UT_REVISAO) matched. Prior
# xfail(_NIP_DEADLINE_RISK_PUBLISH_ADDED_REASON, strict) removed on XPASS(strict).
async def test_prazo_ancora_em_data_recebimento_nip_nao_em_attach_da_ut(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """GAP-NIP-1: os boundary timers de prazo ancoram em `data_recebimento_nip_iso`, NAO no
    instante em que `UT_RevisaoJuridicaNip` foi criada (attach da activity).

    Tecnica (output-vars + execute_job, sem sleep): `data_recebimento_nip_iso` e seedado como
    variavel de START (output-var que controla a ancora) com um valor no FUTURO distante (hoje +
    30 dias) — bem separado do instante real de attach da UT (que ocorre poucos segundos apos o
    start, ou seja, "hoje"). BRT_NipSla (DMN nip_sla) computa `prazo_resposta_absoluto_iso` /
    `sla_alerta_absoluto_iso` = `data_recebimento_nip_iso` + duracao regulatoria (classificacao
    ASSISTENCIAL_CONTESTA_NEGATIVA => P5D/P3D). Os boundary timers usam `timeDate` sobre esses
    campos (BT_PrazoRevisaoEstourado / BT_AlertaPrazoRevisao).

    A PROVA: consultamos o `dueDate` real do job de timer no engine (SEM dispara-lo ainda) e
    comparamos com os dois calculos possiveis:
      (a) ancora correta:    data_recebimento_nip_iso + duracao  (esperado, ~30 dias no futuro)
      (b) ancora incorreta:  "agora" (attach da UT) + duracao    (comportamento legado, GAP-NIP-1)
    O `dueDate` deve bater com (a) (tolerancia generosa p/ parsing de timezone) e estar MUITO
    longe de (b) (tolerancia >= 20 dias) — se a regressao reintroduzir `timeDuration` relativo ao
    attach, o `dueDate` real cairia perto de "agora" e a asserção (a) falharia.

    Em seguida, disparamos os dois timers via `execute_job` (nunca sleep) e confirmamos o
    comportamento observavel de sempre: alerta nao-interruptivo notifica sem cancelar a UT; o
    prazo interruptivo cancela `UT_RevisaoJuridicaNip` e cria `UT_CoordenacaoNip` — provando que
    o mecanismo `timeDate` e engine-deployavel de ponta a ponta, nao so a ancora do `dueDate`.
    """
    anchor_date = (datetime.now(UTC) + timedelta(days=30)).date()
    anchor_iso = anchor_date.isoformat()
    anchor_midnight = datetime.combine(anchor_date, datetime.min.time())

    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
        data_recebimento_nip_iso=anchor_iso,
    )
    iid = inst["id"]

    await _drive_to_revisao(engine, nip_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_AlertaPrazoRevisao")
    job_prazo = await engine.await_timer_job(iid, "BT_PrazoRevisaoEstourado")
    assert job_alerta.due_date, "dueDate do job de alerta deve estar presente (timeDate absoluto)"
    assert job_prazo.due_date, "dueDate do job de prazo deve estar presente (timeDate absoluto)"

    due_alerta = datetime.fromisoformat(job_alerta.due_date).replace(tzinfo=None)
    due_prazo = datetime.fromisoformat(job_prazo.due_date).replace(tzinfo=None)

    # ASSISTENCIAL_CONTESTA_NEGATIVA => nip_sla: prazo=P5D, alerta=P3D (rule r_assistencial_contesta).
    expected_prazo_anchor_based = anchor_midnight + timedelta(days=5)
    expected_alerta_anchor_based = anchor_midnight + timedelta(days=3)
    # Comportamento legado (GAP-NIP-1, pre-fix): timeDuration relativo ao attach da UT (~agora).
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    attach_based_prazo = now_naive + timedelta(days=5)
    attach_based_alerta = now_naive + timedelta(days=3)

    tolerance = timedelta(hours=6)  # generoso p/ parsing de fuso/millis do dueDate do engine
    far_enough = timedelta(days=20)  # separacao minima p/ distinguir ancora correta vs legada

    assert abs(due_prazo - expected_prazo_anchor_based) < tolerance, (
        f"BT_PrazoRevisaoEstourado.dueDate={due_prazo} nao bate com a ancora "
        f"data_recebimento_nip_iso+P5D={expected_prazo_anchor_based} (GAP-NIP-1)"
    )
    assert abs(due_alerta - expected_alerta_anchor_based) < tolerance, (
        f"BT_AlertaPrazoRevisao.dueDate={due_alerta} nao bate com a ancora "
        f"data_recebimento_nip_iso+P3D={expected_alerta_anchor_based} (GAP-NIP-1)"
    )
    assert abs(due_prazo - attach_based_prazo) > far_enough, (
        "BT_PrazoRevisaoEstourado.dueDate esta perto do attach da UT (+P5D) — regressao GAP-NIP-1: "
        "o timer voltou a ancorar no attach da activity, nao em data_recebimento_nip_iso"
    )
    assert abs(due_alerta - attach_based_alerta) > far_enough, (
        "BT_AlertaPrazoRevisao.dueDate esta perto do attach da UT (+P3D) — regressao GAP-NIP-1: "
        "o timer voltou a ancorar no attach da activity, nao em data_recebimento_nip_iso"
    )

    # Dispara os dois timers via job execution (nunca sleep) — confirma o mecanismo timeDate
    # de ponta a ponta, alem da prova de ancora acima.
    await engine.execute_job(job_alerta.id)
    await nip_probe.drain()
    # T3.1 event-gap-nip-alerts (finding 2c): strengthened with sla_breach_task_name/numero_nip_ans
    # matched from the declared payload (ST_NotificarRiscoRevisao's event_payload_vars) — proves
    # this SPECIFIC alert (revisao phase) fired for this instance, not just topic presence.
    numero_nip_ans = await engine.get_variable(iid, "numero_nip_ans")
    assert nip_probe.has_event(
        _NIP_DEADLINE_RISK, numero_nip_ans=numero_nip_ans, sla_breach_task_name=_UT_REVISAO
    ), "nip.deadline_risk deve publicar (ST_NotificarRiscoRevisao, alerta nao-interruptivo)"
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAO in open_keys, "Timer nao-interruptivo nao deve cancelar UT_RevisaoJuridicaNip"

    await engine.execute_job(job_prazo.id)
    await nip_probe.drain()
    assert nip_probe.has_event(_NIP_BREACHED), "nip.breached deve publicar (prazo interruptivo estourado)"
    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert ut_coord.task_definition_key == _UT_COORDENACAO
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAO not in open_keys, "Timer interruptivo deve cancelar UT_RevisaoJuridicaNip"
    await _assert_no_manter_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_ANCHOR_FAILSAFE_MISSING_REASON, strict=True)
async def test_anchor_failsafe_ancora_ausente_ou_lixo_nao_derruba_o_processo(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """GAP-NIP-1 fail-safe (worker instruct_dossier): ancora em branco/lixo -> hoje (UTC).

    Sem o fail-safe, um start path que nao seede `data_recebimento_nip_iso` valido tem dois modos
    de falha catastroficos com os timers timeDate:
      - "" / lixo -> BRT_NipSla falha no parse FEEL (`date and time("T00:00:00")`) -> incidente;
        a User Task nunca e criada ("UT_RevisaoJuridicaNip nao apareceu" — exatamente a falha do
        seam NIP->ANS-SUBMIT no CI);
      - anchor default no passado -> boundary interruptivo dispara NO ATTACH e cancela a UT.

    Este teste PROVA no engine real que o worker (ST_InstruirDossie roda em todo caminho ANTES de
    BRT_NipSla) intercepta ambos:
      (a) start com ancora EM BRANCO -> UT_RevisaoJuridicaNip APARECE; a variavel de processo
          foi corrigida para hoje (YYYY-MM-DD, UTC); os jobs de timer existem com dueDate no
          FUTURO (sem insta-fire) e ~hoje+P5D;
      (b) start com ancora LIXO ("not-a-date") -> mesmo comportamento.

    NAO cobre data VALIDA no passado: essa dispara o timer imediatamente DE PROPOSITO (a NIP
    realmente estourou; mascarar seria esconder sancao ANS) — semantica coberta pelo teste de
    ancora acima.
    """
    today = datetime.now(UTC).date()
    today_midnight = datetime.combine(today, datetime.min.time())

    # (a) ancora EM BRANCO
    inst_blank = await start_nip(data_recebimento_nip_iso="")
    iid_blank = inst_blank["id"]
    await _drive_to_revisao(engine, nip_probe, iid_blank)  # UT aparece = sem incidente/insta-fire

    seeded = await engine.get_variable(iid_blank, "data_recebimento_nip_iso")
    assert seeded == today.isoformat(), f"fail-safe deve seedar hoje UTC (YYYY-MM-DD); engine tem {seeded!r}"

    job_prazo = await engine.await_timer_job(iid_blank, "BT_PrazoRevisaoEstourado")
    assert job_prazo.due_date, "dueDate deve existir (timeDate absoluto)"
    due = datetime.fromisoformat(job_prazo.due_date).replace(tzinfo=None)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    assert due > now_naive, f"fail-safe hoje+P5D deve estar no futuro (sem insta-fire); due={due}"
    assert due <= today_midnight + timedelta(days=5, hours=27), (
        f"deadline fail-safe deve ser ~hoje+P5D (folga p/ fuso); due={due}"
    )

    # (b) ancora LIXO (nunca pode chegar ao FEEL da DMN)
    inst_garbage = await start_nip(data_recebimento_nip_iso="not-a-date")
    iid_garbage = inst_garbage["id"]
    await _drive_to_revisao(engine, nip_probe, iid_garbage)

    seeded_garbage = await engine.get_variable(iid_garbage, "data_recebimento_nip_iso")
    assert seeded_garbage == today.isoformat(), (
        f"lixo deve cair no fail-safe hoje UTC; engine tem {seeded_garbage!r}"
    )
    await _assert_no_manter_without_human_task(engine, iid_blank)
    await _assert_no_manter_without_human_task(engine, iid_garbage)


# ===========================================================================
# Solicitacao de informacao
# ===========================================================================


async def test_solicitar_info_aguarda_e_retoma(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """UT_RevisaoJuridicaNip completa com SOLICITAR_INFO => aguarda; msg.nip.info_recebida retoma.

    Fluxo aguarda no event gateway; a info adicional correlacionada (business key) retoma para
    dossie/elaboracao/revisao; nenhum desfecho adverso enquanto a info nao chega.
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_nip": "SOLICITAR_INFO", "revisor_id": "revisor-sintetico-004"}
    )
    await nip_probe.drain()

    # A instancia aguarda no event gateway; nenhum desfecho adverso ainda.
    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended, "SOLICITAR_INFO nunca mantem negativa (L0)"
    assert await engine.instance_is_active(iid), "Instancia deve aguardar a info adicional"

    # Correlaciona msg.nip.info_recebida (a info chegou).
    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.nip.info_recebida",
        "businessKey": business_key,
        "processVariables": {"documentacao_suficiente": {"value": True, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.nip.info_recebida falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await nip_probe.drain()
    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGATIVA_MANTIDA not in ended, "Retomada por info nunca auto-mantem negativa (L0)"
    await _assert_no_manter_without_human_task(engine, iid)


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_nip(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa."""
    nip = "NIP-TESTE-IDEM-001"
    business_key = f"NIP-amh-{nip}"

    first = await start_nip(numero_nip_ans=nip)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]


# ===========================================================================
# Handoff a ANS-SUBMIT
# ===========================================================================


@pytest.mark.xfail(reason=_NIP_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_handoff_ans_submit_apos_decisao_humana(
    engine: EngineRest,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
) -> None:
    """Apos CONCEDER humano, handoff_ans_submit executa com payload de filing (protocolo/numero/revisor).

    O filing legalmente vinculante e responsabilidade de SP-OP-ANS-SUBMIT-001 — NIP-001 nao transmite
    diretamente a ANS. NOTA (Step 3 fact 3 / notification_bridge): a SPEC-WIRED handoff a
    SP-OP-ANS-SUBMIT-001 via origem_envio="nip_filing" NAO e runtime-wired
    (notification_bridge.py registra exatamente 5 regras, nenhuma delas SP-OP-ANS-SUBMIT-001, e a
    ponte nao e instanciada por nenhum consumidor rodando — docs/design/T2.6-ans-submission-rescope.md
    §1.5/T2.6-7). Este teste, porem, so verifica que o worker nip.py COMPUTA/RETORNA as variaveis de
    handoff corretas (testavel, em-processo) — NUNCA que uma segunda instancia SP-OP-ANS-SUBMIT-001
    realmente inicia (isso exigiria a ponte, que nunca sera exercitada aqui); por isso NAO herda um
    xfail proprio para essa lacuna. O UNICO motivo deste teste falhar em v2 e o gap 3 do docstring do
    modulo (nip_probe.notifications_of_type nunca observa handoff_ans_submit_entry, que nao chama
    kafka.publish).
    """
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "CONCEDER",
            "texto_resposta_nip": "Concede o pleito (teste)",
            "revisor_id": "revisor-sintetico-005",
        },
    )
    await nip_probe.drain()
    await _await_end(engine, iid)

    handoffs = nip_probe.notifications_of_type("nip.handoff_ans_submit")
    assert handoffs, "handoff_ans_submit deve ser executado apos decisao humana"
    h = handoffs[0]
    assert h["numero_nip_ans"]
    assert h["revisor_id"] == "revisor-sintetico-005"
    assert h["origem_envio"] == "nip_filing"


# ===========================================================================
# T2.6-7 — end-to-end: NIP handoff -> live NotificationBridge -> real SP-OP-ANS-SUBMIT-001
# start (pending live proof; xfail-strict, no docker in this dev environment).
# ===========================================================================

_T267_NIP_END_TO_END_PENDING_LIVE_PROOF_REASON = (
    "T2.6-7 (docs/design/T2.6-ans-submission-rescope.md §1.5/§5): notification_bridge.py now "
    "registers a nip.handoff_ans_submit -> SP-OP-ANS-SUBMIT-001 rule with a deterministic "
    "business key (ANSSUB-{tenant}-nipfiling-{numero_nip_ans}) and a fenced starter "
    "(build_cibseven_process_starter -> start_process_idempotent, ADR-0007/T-C2) — both fully "
    "unit-proven against fakes in tests/unit/platform/test_notification_bridge.py. This test "
    "drives the SAME pipeline against the REAL engine end-to-end (NIP concluded -> handoff "
    "computed -> fed into a live NotificationBridge instance -> a genuinely NEW "
    "SP-OP-ANS-SUBMIT-001 instance). It intentionally still bypasses two separate, "
    "already-disclosed gaps rather than re-litigating them here: (a) handoff_ans_submit_entry "
    "does not itself call kafka.publish yet (nip.py module docstring finding 3 / "
    "_NIP_WORKER_KAFKA_GAP_REASON, xfailed independently on test_handoff_ans_submit_apos_"
    "decisao_humana above) — this test calls the pure handoff_ans_submit(...) function directly "
    "with the NIP instance's own known synthetic values instead of observing a published event; "
    "(b) no production consumer in src/ instantiates NotificationBridge against "
    "operadora.notifications.internal in a running daemon — this test constructs the bridge "
    "manually, standing in for that (not-yet-built) consumer. Kept xfail-strict because this "
    "agent has no docker/live-engine access to actually run and prove it end-to-end (task "
    "instruction: 'no docker here' / 'pending live proof') — un-xfail only after a real R1 run "
    "against a live CIB Seven + Postgres stack confirms it, per this design's own R2-build/"
    "R1-verify tiering."
)


@pytest_asyncio.fixture
async def deploy_nip_and_submit_artifacts(engine: EngineRest) -> str:
    """Deploya SP-OP-NIP-001 (BPMN + 3 DMN) + SP-OP-ANS-SUBMIT-001 (BPMN + 4 DMN) juntos, para
    que `engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)` consulte uma definition
    genuinamente deployada (mirrors `deploy_cron_and_submit_artifacts`,
    `test_sp_op_ans_cron_001.py`)."""
    return await engine.deploy(
        _BPMN,
        _DMN_CLASS,
        _DMN_ROUTING,
        _DMN_SLA,
        _BPMN_ANS_SUBMIT,
        _DMN_ANS_CALENDAR,
        _DMN_ANS_SLA,
        _DMN_ANS_ADMISS,
        _DMN_ANS_RETRY,
        name="SP-OP-NIP-001-ans-submit-seam-qa",
    )


@pytest.mark.xfail(reason=_T267_NIP_END_TO_END_PENDING_LIVE_PROOF_REASON, strict=True)
async def test_nip_handoff_end_to_end_starts_ans_submit_via_live_bridge(
    engine: EngineRest,
    deploy_nip_and_submit_artifacts: str,
    nip_probe: NipEngineProbe,
    start_nip: Callable[..., Any],
    audit_sink: Any,
) -> None:
    """A concluded NIP (CONCEDER), fed through a live `NotificationBridge` with a real fenced
    starter, starts a genuinely NEW SP-OP-ANS-SUBMIT-001 instance — the T2.6-7 acceptance
    criterion ("integration: bridge instantiated by the running consumer, end-to-end NIP handoff
    actually starts the SUBMIT process")."""
    inst = await start_nip(
        classificacao_nip="assistencial",
        tema_nip="negativa_cobertura",
        contesta_negativa=True,
        documentacao_suficiente=True,
    )
    iid = inst["id"]

    ut = await _drive_to_revisao(engine, nip_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_nip": "CONCEDER",
            "texto_resposta_nip": "Concede o pleito (teste, T2.6-7 seam)",
            "revisor_id": "revisor-sintetico-t267",
        },
    )
    await nip_probe.drain()
    await _await_end(engine, iid)

    # Stand-in for the still-missing Kafka-publish leg (gap (a), see xfail reason): compute the
    # handoff payload the SAME way the worker would, from the instance's own known synthetic
    # values (start_nip's canonical numero_nip_ans/protocolo_ans, the human's revisor_id is NOT
    # part of handoff_ans_submit's own signature — matches nip.py:327-355 exactly).
    numero_nip_ans = inst.get("businessKey", "").removeprefix("NIP-amh-")
    handoff_payload = handoff_ans_submit(
        numero_nip_ans=numero_nip_ans,
        protocolo_ans="PROTO-TESTE-0001",
        decisao_nip="CONCEDER",
        data_recebimento_nip_iso="2026-07-10",
    )
    handoff_payload["tenant_id"] = "amh"  # boundary: not part of handoff_ans_submit's own output

    # Stand-in for the still-missing live consumer (gap (b)): construct the bridge with the REAL
    # fenced starter (never a raw engine call) and feed it the handoff directly.
    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL)
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    before_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)
    try:
        results = await bridge.on_event(event_type="nip.handoff_ans_submit", payload=handoff_payload)
    finally:
        await transport.close()
    after_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)

    assert results[0].handoff_triggered is True
    novas = after_submit - before_submit
    assert len(novas) == 1, (
        "end-to-end: o handoff da ponte deveria ter iniciado exatamente 1 instancia real de "
        f"SP-OP-ANS-SUBMIT-001; novas={novas}"
    )


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_nip_dmn_sem_saida_de_decisao() -> None:
    """Nenhuma DMN do processo possui coluna de saida de negativa/decisao_nip (L0 hard).

    Varredura estatica do XML das 3 DMNs: nenhum <output> chama-se decisao_nip; nenhum outputEntry
    e um TOKEN de decisao de merito (MANTER_NEGATIVA/NEGAR/CONCEDER/DENY) — distinto do rotulo de
    CLASSIFICACAO ASSISTENCIAL_CONTESTA_NEGATIVA, que apenas roteia ao grupo humano (nao decide o
    merito). roteamento de nip_routing e EXATAMENTE {ELABORAR_RESPOSTA, PENDENTE_INFO, REVISAO_JURIDICA}.
    """
    from xml.etree import ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    # Tokens de DECISAO DE MERITO proibidos como SAIDA de DMN (comparacao por valor inteiro, nao
    # substring — ASSISTENCIAL_CONTESTA_NEGATIVA e um rotulo de classificacao/roteamento, nao de
    # merito; ver §15-§16 do contrato). Tambem checamos o token solto "MANTER_NEGATIVA".
    forbidden_tokens = {"MANTER_NEGATIVA", "NEGAR", "DENY", "CONCEDER", "RESPONDER_NAO_ASSISTENCIAL"}
    for dmn_path in (_DMN_CLASS, _DMN_ROUTING, _DMN_SLA):
        tree = ET.parse(dmn_path)
        root = tree.getroot()
        # Nenhum output chamado decisao_nip / texto_resposta_nip.
        for out in (e for e in root.iter() if _local(e.tag) == "output"):
            name = out.get("name") or ""
            assert name not in ("decisao_nip", "texto_resposta_nip"), (
                f"{dmn_path.name}: DMN nao pode ter output '{name}' (L0 — merito/texto so nasce na UT humana)"
            )
        # Nenhum outputEntry e um token de decisao de merito (valor exato, ignorando aspas/case).
        for oe in (e for e in root.iter() if _local(e.tag) == "outputEntry"):
            text_el = next((c for c in oe if _local(c.tag) == "text"), None)
            val = (text_el.text or "").strip().strip('"').upper() if text_el is not None else ""
            assert val not in forbidden_tokens, (
                f"{dmn_path.name}: outputEntry e token de decisao de merito '{val}' — "
                "nenhuma DMN decide o merito (L0)"
            )

    # roteamento de nip_routing: dominio exato.
    tree = ET.parse(_DMN_ROUTING)
    root = tree.getroot()
    roteamentos: set[str] = set()
    last_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_first_output = val
    assert roteamentos == {"ELABORAR_RESPOSTA", "PENDENTE_INFO", "REVISAO_JURIDICA"}, (
        f"dominio de roteamento inesperado: {roteamentos} (L0)"
    )
    assert last_first_output == "REVISAO_JURIDICA", (
        "row catch-all de nip_routing deve rotear a REVISAO_JURIDICA (fail-safe)"
    )


def test_nip_dmn_classification_catchall_juridico() -> None:
    """A row catch-all (ultima) de nip_classification roteia a juridico-regulatorio (fail-safe).

    Tema desconhecido nunca vira NAO_ASSISTENCIAL: a catch-all classifica ASSISTENCIAL_CONTESTA_NEGATIVA
    + juridico-regulatorio.
    """
    from xml.etree import ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    tree = ET.parse(_DMN_CLASS)
    root = tree.getroot()
    rules = [e for e in root.iter() if _local(e.tag) == "rule"]
    assert rules, "nip_classification deve ter rules"
    last = rules[-1]
    outputs = [c for c in last if _local(c.tag) == "outputEntry"]
    # outputs[0]=classificacao, outputs[2]=grupo_revisor

    def _text(entry: Any) -> str:
        text_el = next((c for c in entry if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text, "outputEntry sem <text>"
        return str(text_el.text).strip().strip('"')

    classificacao = _text(outputs[0])
    grupo = _text(outputs[2])
    assert classificacao == "ASSISTENCIAL_CONTESTA_NEGATIVA", "catch-all nao vira NAO_ASSISTENCIAL (L0)"
    assert grupo == "juridico-regulatorio", "catch-all roteia a juridico-regulatorio (fail-safe)"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}.

    Nenhuma coluna usa "number". Varredura estatica das 3 DMNs.
    """
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_CLASS, _DMN_ROUTING, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double/integer)"
