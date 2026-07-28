"""SP-OP-ADEQUACAO-001 — suite de integracao executavel (engine CIB Seven REAL) + estatica.

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2
(13-family process-suite port). adequacao.py IS one of ADR-0028's T1.5-migrated modules (it
imports `DmnTransport`/`evaluate_sync`/`first_row`/`require_dmn` from `dmn_transport.py` and
threads a `dmn` seam into `route_remediation`) — this is the FIRST process-integration suite in
this tree to wire a REAL `CibSevenDmnTransport` against the live engine (no prior T1.5-migrated
family had its process-suite ported yet).

Implementa o test-spec do W5 contra o engine real (ADR-0011: SEM mock de engine). Cada teste de
engine:

1. inicia a instancia via REST com business key `ADEQ-amh-{regiao}-{especialidade}-{ciclo}`;
2. drena as external tasks com o `adequacao_probe` (workers reais Phase-3 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine.

Os testes de engine fazem `pytest.skip` (via a fixture `engine` compartilhada, conftest.py) quando
o CIB Seven esta indisponivel; os testes ESTATICOS (varredura do XML das DMN/BPMN) NAO precisam de
engine e rodam em qualquer lugar (incluindo o lane unit / CI rapido).

Dados sinteticos obvios (reused verbatim from the donor — nao inventados): regiao
`REGIAO-TESTE-NNNN`, especialidade `cardiologia`, tenant `amh`, ciclo `2026-Q2`. Business key
`ADEQ-amh-{regiao}-{especialidade}-{ciclo}`. Process key: SP-OP-ADEQUACAO-001 (exato — nao
alterar).

## Invariante no-adverse (DoD deliverable) — adequacao_fallback_commitment (ADR-0018, num
## processo L3)

test_nenhum_caminho_automatizado_firma_compromisso_fallback:
  Varredura de combinacoes de input da DMN adequacao_gap/routing. A instancia NUNCA atinge
  End_CompromissoFallbackHumano sem que UT_DecisaoFallback / UT_CoordenacaoRede tenha sido
  completada por humano. Os caminhos L3 (CONFORME / MONITORAR / ENCAMINHAR_CREDENCIAMENTO) atingem
  terminais NEUTROS autonomamente (sem User Task). A prova e feita consultando a historia do
  engine. NOTE (documented, NOT a weakening — the assertion body is byte-identical to the donor;
  UPDATED t2.5-p2b-round2): for the scenario that routes to GAP_CRITICO/ANALISE_HUMANA, the
  instance never fully drains in v2 (FINDING 1 below — `_MISSING_DOSSIER_WORKER_REASON`, still
  open: `prepare_remediation_dossier` remains Andre A2A-gated), so `End_CompromissoFallbackHumano`
  is unreachable for a STRONGER reason than the donor anticipated (not just "gated behind a human
  task" but "gated behind a human task that is itself currently unreachable via the engine"). The
  GAP_LEVE/MONITORAR scenario is NO LONGER stuck (t2.5-p2b-round2 registered
  `update_monitoring_plan`) — it now genuinely completes to `End_MonitoramentoAtualizado`. The
  invariant still holds either way — vacuously for the still-stuck ANALISE_HUMANA scenarios,
  genuinely for CONFORME/GAP_LEVE-MONITORAR/ENCAMINHAR_CREDENCIAMENTO — so this test is left
  UNMARKED (no concrete per-assertion failure to xfail).

WorkerHarness NAO retorna output variables de roteamento ao engine: os workers observam/ecoam/
publicam. Por isso o TESTE seed os fatos apurados (tempo_acesso_apurado_min, distancia_apurada_km,
prestadores_disponiveis, cobertura_geo_suficiente, dados_geo_completos, tipo_carater) como
variaveis de START — a DMN adequacao_gap/routing computa gap/roteamento a partir deles (mesma
tecnica de test_sp_op_cancel_001.py / test_sp_op_contas_001.py). FINDING 3 below documents that
this technique is INVALIDATED for the geo-facts specifically (measure_gap overwrites them).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.adequacao`/`dmn_transport`/`events`/`harness`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5). The donor's single
    `adequacao_gap.dmn` bundled BOTH decision tables (`adequacao_gap` +
    `adequacao_remediation_routing`) in one file; v2 deploys them as TWO separate artifacts
    (`adequacao_gap.dmn` / `adequacao_remediation_routing.dmn`, each independently
    `camunda:decisionRef`'d from `BRT_AdequacaoGap`/`BRT_RemediationRouting` — grep-confirmed). The
    2 static DMN-shape tests (`test_adequacao_gap_sem_saida_que_compromete`,
    `test_dmn_typeref_allowlist`) are adapted to parse both files and merge/iterate — mechanical
    adaptation, assertions unchanged (the typeRef test gains an extra file, `adequacao_remediation_
    routing.dmn`, vs the donor's 2 — STRENGTHENED coverage, not weakened).
  - `engine`/`CIBSEVEN_BASE_URL` fixtures HOISTED to the shared `conftest.py` (port rule 3) — the
    donor's own local `_engine_available()`/`engine` fixture/`_BASE_URL` are dropped in favor of
    the conftest's byte-identical equivalents (mirrors cancel.py/auth.py).
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`).
  - DMN evaluation seam (ADR-0028/T1.5, NEW vs the donor which predates the cutover): v2's
    `route_remediation(variables, *, dmn: DmnTransport | None = None)` requires a `dmn` seam
    threaded through `register_adequacao_workers(harness, kafka, dmn=...)`. This suite wires a
    REAL `CibSevenDmnTransport(CIBSEVEN_BASE_URL)` against the SAME live engine the worker harness
    talks to (no fake/mock — ADR-0011).
  - `_ADEQUACAO_WORKER_TOPICS` is built from what `register_adequacao_workers` ACTUALLY registers
    (7 `operadora.adequacao.*` topics + the generic `operadora.events.publish`, after
    t2.5-p2b-round2 added `update_monitoring_plan`/`notify_sla_risk` — originally 5), cross-checked
    against the BPMN's 8 declared `operadora.adequacao.*`-namespaced service-task topics — the
    remaining 1-topic mismatch (`prepare_remediation_dossier`, Andre A2A-gated) IS FINDING 1 below
    (not a fixture bug to silently correct).
  - D-07 ceilings gap does NOT apply here — VERIFIED (`grep -n "CeilingResolver\\|within_l2_
    ceiling" src/maezo/tools/workers/adequacao.py` returns nothing); not cited anywhere below.
  - notification_bridge.py's 5 rules (CONTAS/FRAUDE/CRED/CANCEL/INADIMPLENCIA) do not name
    adequacao as source or target; the donor suite has no cross-process handoff assertion either
    (only a pass-through `network_change_ref` seed variable, never asserted/consumed) — N/A here,
    not fabricated.
  - `register_fallback_commitment`'s guard (`ERR_FALLBACK_COMMITMENT_NOT_HUMAN`) already has a
    dedicated unit-level guard suite (`tests/unit/tools/workers/test_adequacao.py::
    test_fallback_commitment_rejects_*`). Unlike cancel.py/auth.py, the donor's adequacao suite has
    NO analogous "call the entry function directly, no engine" test — so none is fabricated here
    (port fidelity: mirror the donor exactly, do not invent new tests beyond it).

FINDINGS (grep/read-verified against v2 `main` this branch — see PR body / evidence-ledger for
full detail):

  1. REGISTRATION GAP (`_MISSING_DOSSIER_WORKER_REASON` / `_MISSING_MONITORING_WORKER_REASON`) —
     UPDATED, t2.5-p2b-round2: `register_adequacao_workers` (adequacao.py) originally registered
     FunctionWorkers for exactly 5 of the 8 `operadora.adequacao.*` topics this BPMN declares —
     `measure_coverage`, `calculate_gap`, `notify_rede`, `start_credenciamento`,
     `register_fallback_commitment`. THREE topics had NO implementing function at all:
     `update_monitoring_plan` (ST_UpdateMonitoringPlanL3, BPMN line 198 — first step of the
     GAP_LEVE/MONITORAR L3 branch), `prepare_remediation_dossier` (ST_PrepareRemediationDossier,
     BPMN line 252 — the SOLE entry point into the ANALISE_HUMANA branch, i.e. UT_DecisaoFallback
     and, transitively, its SLA-breach escalation UT_CoordenacaoRede), and `notify_sla_risk`
     (ST_NotificarRiscoSla, BPMN line 288 — the non-interruptive SLA-alert boundary timer's
     target, not exercised by any donor test in this ported subset). t2.5-p2b-round2 CLOSED TWO
     of these three: `update_monitoring_plan` and `notify_sla_risk` now have real implementing
     functions (mirror inadimplencia.py/cancel.py/fraude.py's proven dict-first idiom) and are
     INCLUDED in `_ADEQUACAO_WORKER_TOPICS`. `prepare_remediation_dossier` remains registration-
     gapped — Andre A2A-gated, explicitly out of scope for that PR — its topic is still EXCLUDED
     from `_ADEQUACAO_WORKER_TOPICS` (mirrors cancel.py's drift-guard convention: only ACTUALLY-
     registered topics are drained) — `adequacao_probe.drain()` never even attempts to fetch that
     external task; it sits pending forever. This remains a MISSING-WORKER gap, categorically
     DIFFERENT from the systemic `operadora.events.publish`/`kafka.publish` gaps documented for
     other T3.1 families (facts #1/#5 of the phase-2 charter) — no amount of Kafka-producer
     wiring fixes it; `prepare_remediation_dossier` needs an actual implementing function (e.g.
     the Andre A2A dossier handoff the BPMN documentation describes) before the ANALISE_HUMANA
     branch (9 of the remaining ported tests) becomes reachable. The GAP_LEVE/MONITORAR branch (1
     test, `_MISSING_MONITORING_WORKER_REASON`) is no longer blocked by a MISSING-WORKER gap —
     see Finding 3 for why it still xfails (systemic Kafka-publish gap instead).

  2. DMN-INPUT-OVERRIDE DRIFT (`_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON`, 3 tests): `measure_gap`
     (adequacao.py:38-69) is an explicitly-labeled placeholder ("real implementation queries
     geo-location / network DB", line 47) that OVERWRITES every geo-fact the donor's fixture seeds
     at process start, before `BRT_AdequacaoGap` ever evaluates the DMN: `tempo_acesso_apurado_min`/
     `distancia_apurada_km` are hardcoded to 45/15.5 (lines 48-49); `cobertura_geo_suficiente` is
     DERIVED as `prestadores_disponiveis >= 2` (line 51, ignoring any seeded value);
     `dados_geo_completos` is DERIVED as `bool(regiao_saude and especialidade)` (line 52) — ALWAYS
     `True` in this suite, since `start_adequacao` always supplies non-empty `regiao_saude`/
     `especialidade` (required for the business key). Combined with the ALREADY-DOCUMENTED
     `adequacao_gap` rule-order divergence (adequacao.py:90-99; unit-proven,
     `tests/unit/tools/workers/test_adequacao.py::test_adequacao_gap_conforme_now_leve`): the
     deployed table's `GAP_LEVE` row precedes `CONFORME`'s for `tipo_carater="eletivo"` whenever
     `tempo<=60`/`distancia<=50` — BOTH always true given the hardcoded 45/15.5. This makes (a)
     `dados_geo_completos=False` untestable via any start-seeded scenario (always overwritten
     True — the "dados incompletos -> ANALISE_HUMANA" DMN branch is unreachable through the live
     `measure_gap` worker); (b) `gap_adequacao=CONFORME` unreachable for `tipo_carater="eletivo"`
     (this suite's fixture default) whenever `prestadores_disponiveis>=2` — GAP_LEVE always wins
     first; (c) `gap_adequacao=GAP_MODERADO` reachable ONLY via `prestadores_disponiveis` EXACTLY
     `1` (`cobertura_geo_suficiente` derives `false` only when `prestadores<2`), not `2` as the
     donor's GAP_MODERADO scenario used (now yields GAP_LEVE instead). This is a real-
     implementation-vs-fixture mismatch that PREDATES this port (measure_gap was always a
     placeholder) — needs either a fixture seam (inject the apurado facts directly) or a real
     geo-lookup implementation before these DMN-routing-dependent scenarios are reachable as
     originally designed.

  3. TOTAL Kafka-publish gap for this family (documented, NOT the primary blocker for any test
     here) — UPDATED, t2.5-p2b-round2: `adequacao.py`'s `register_adequacao_workers` explicitly
     discards its `kafka` argument (`del kafka  # unused — no adequacao.py worker declares a
     Kafka dependency`) — EVERY function this module registers (`measure_gap`, `route_remediation`,
     `notify_coordenacao`, `execute_remediation`, `register_fallback_commitment`, and — new in
     t2.5-p2b-round2 — `update_monitoring_plan`/`notify_sla_risk`, which deliberately mirror the
     SAME dict-first, no-Kafka idiom rather than add worker-side publishing) returns a plain dict
     and never calls `kafka.publish`. `adequacao_probe.notifications_of_type(...)` will therefore
     ALWAYS return an empty list for any `adequacao.*`-typed notification, in EVERY test — the
     systemic fact #1 carve-out ("family-specific direct-kafka calls DON'T work") applies TOTALLY
     here, not partially as in cancel.py/auth.py (where SOME action workers still ran, just
     without publishing). For `update_monitoring_plan` specifically, this IS now the primary
     blocker for `test_l3_gap_leve_monitora_sem_user_task` (registration gap closed,
     t2.5-p2b-round2 — see Finding 1): the `_END_MONITORAMENTO`/no-user-task assertions are
     expected to PASS now that the branch genuinely completes, but the
     `notifications_of_type("adequacao.update_monitoring_plan")` assertion still fails on this
     gap — `_MISSING_MONITORING_WORKER_REASON`'s text is updated accordingly ("built, pending
     live-proof flip"), mark/strict unchanged per xfail policy. For every OTHER
     `notifications_of_type(...)` call site in this ported subset, a MORE fundamental blocker
     (FINDING 1's remaining `prepare_remediation_dossier` gap, or FINDING 2) still precedes it, so
     this gap is recorded here so the live verifier does not mistake a future FINDING-1/2 fix for
     a full green: these assertions will keep failing until this family also gets Kafka-producer
     wiring (same follow-up task as cancel/auth/escalation's residual action-worker gap; a
     dedicated systemic Kafka-seam task is queued, NOT fixed in t2.5-p2b-round2). Domain events
     via the GENERIC `operadora.events.publish` path (`has_event(...)`
     assertions) are UNAFFECTED and DO work (fact #1, FIXED) — `register_events_workers` is wired
     into `adequacao_probe` exactly like cancel.py/auth.py.

  4. Two BPMN `bpmn:error` declarations are DEAD (documented, zero test impact): `Error_
     AdequacaoCelulaInvalida` (`ERR_ADEQUACAO_CELULA_INVALIDA`, BPMN line 14) and `Error_
     FallbackCommitmentNotHuman` (`ERR_FALLBACK_COMMITMENT_NOT_HUMAN`, BPMN line 15) are both
     declared at the top of the BPMN but neither has an `errorRef` consumer anywhere in the process
     body (grep-confirmed: no `boundaryEvent`/`endEvent` references either). `ERR_ADEQUACAO_
     CELULA_INVALIDA` is also never RAISED by any function in adequacao.py (defined, line 27,
     unused) — the donor suite does not exercise it either. `ERR_FALLBACK_COMMITMENT_NOT_HUMAN` IS
     raised (`register_fallback_commitment`'s guard, `AdequacaoError`), but `AdequacaoError` is a
     plain "coded" `Exception` (`.code`/`.message`), never a `WorkerBpmnError` — `FunctionWorker.
     execute()` (base.py) reclassifies it straight to `ValueError`, which `harness.py`'s `_handle`
     routes to `failure(retries=0)`, i.e. a fail-closed INCIDENT, never a modeled `bpmnError`/
     boundary-catch route. This is the CORRECT no-adverse behavior (guard rejection always
     surfaces as a human-visible incident) even though the BPMN's own declared error is unused —
     so this suite's harness leaves `bpmn_error_allowlist` at its default empty `frozenset()`
     (nothing to allowlist; unlike cancel.py's `ERR_CANCEL_MANTER_NOT_HUMAN`/auth.py's `ERR_AUTH_
     DENIAL_INCOMPLETE`, which DO have a matching boundary event).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
import pytest_asyncio

from maezo.tools.workers.adequacao import register_adequacao_workers
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn"
_DMN_GAP = _REPO / "spec/processes/dmn/adequacao_gap.dmn"
_DMN_ROUTING = _REPO / "spec/processes/dmn/adequacao_remediation_routing.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/adequacao_sla.dmn"

# External task topics do contrato SP-OP-ADEQUACAO-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_MEASURE_TOPIC = "operadora.adequacao.measure_coverage"
_CALC_GAP_TOPIC = "operadora.adequacao.calculate_gap"
_NOTIFY_REDE_TOPIC = "operadora.adequacao.notify_rede"
_START_CRED_TOPIC = "operadora.adequacao.start_credenciamento"
_REGISTER_FALLBACK_TOPIC = "operadora.adequacao.register_fallback_commitment"

# t2.5-p2b-round2 CLOSED 2 of the 3 registration gaps: `update_monitoring_plan`/`notify_sla_risk`
# now have real implementing functions in adequacao.py (mirrors inadimplencia.py/cancel.py/
# fraude.py's proven dict-first, informational-only idiom for the SLA-alert worker) and are
# INCLUDED in `_ADEQUACAO_WORKER_TOPICS` below. `prepare_remediation_dossier` is now a REGISTERED
# DL-0033 local stub (Andre A2A real delegation still deferred), but it is deliberately NOT included
# in `_ADEQUACAO_WORKER_TOPICS` — this probe does not drain it, keeping the engine xfails valid
# pending a dedicated live-engine proof that would flip them (the drift guard excludes it explicitly).
_UPDATE_MON_TOPIC = "operadora.adequacao.update_monitoring_plan"
_PREPARE_DOSSIER_TOPIC = "operadora.adequacao.prepare_remediation_dossier"  # DL-0033 stub (not drained here)
_NOTIFY_SLA_TOPIC = "operadora.adequacao.notify_sla_risk"

# Topicos REALMENTE servidos pelos workers registrados no harness (drain generico). Cross-checked
# against the BPMN's 8 declared operadora.adequacao.* topics in the `adequacao_probe` fixture's
# drift-guard below. `_PREPARE_DOSSIER_TOPIC` is NOW drained (t2-dossier-a2a / #178, DL-0033 real
# wiring): `prepare_remediation_dossier` is a real worker that delegates to Andre when the signed
# dispatcher is available and otherwise fail-neutral-completes with a disclosed gap marker
# (DL-0037) — either way the external task COMPLETES and the instance drains, so FINDING 1's
# dossier xfails flip to real passes (t2.5-p2b-round2 already closed update_monitoring/notify_sla).
_ADEQUACAO_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _MEASURE_TOPIC,
    _CALC_GAP_TOPIC,
    _NOTIFY_REDE_TOPIC,
    _START_CRED_TOPIC,
    _REGISTER_FALLBACK_TOPIC,
    _UPDATE_MON_TOPIC,
    _NOTIFY_SLA_TOPIC,
    _PREPARE_DOSSIER_TOPIC,
]

# Topico interno de notificacoes (harness.py) — always empty for this family (FINDING 3).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-ADEQUACAO-001.md) — via o worker generico events.publish.
_ADEQ_RECEIVED = "agents.events.adequacao.received"
_ADEQ_GAP_DETECTED = "agents.events.adequacao.gap_detected"
_ADEQ_SLA_BREACHED = "agents.events.adequacao.sla_breached"
_ADEQ_COMPLETED = "agents.events.adequacao.completed"

# User Task definition keys (BPMN).
_UT_DECISAO = "UT_DecisaoFallback"
_UT_COORDENACAO = "UT_CoordenacaoRede"

# End events do processo.
_END_CONFORME = "End_AdequacaoConforme"
_END_MONITORAMENTO = "End_MonitoramentoAtualizado"
_END_ENCAMINHADA = "End_RemediacaoEncaminhada"
_END_COMPROMISSO = "End_CompromissoFallbackHumano"

# User Tasks humanas que podem produzir o desfecho adverso (no-adverse guard).
_UT_HUMANAS = frozenset({_UT_DECISAO, _UT_COORDENACAO})

# Terminais NEUTROS alcancaveis sem User Task (caminhos L3).
_ENDS_NEUTROS_L3 = frozenset({_END_CONFORME, _END_MONITORAMENTO, _END_ENCAMINHADA})

# O UNICO terminal ADVERSO (so via UT humana — adequacao_fallback_commitment L1).
_END_ADVERSO = _END_COMPROMISSO

# Campos obrigatorios de uma decisao adversa humana (COMPROMISSO_FALLBACK) — synthetic, reused
# verbatim from the donor.
_CAMPOS_FALLBACK = {
    "tipo_fallback": "reembolso_garantido",
    "justificativa_fallback": "Fundamentacao sintetica do compromisso (teste no-adverse)",
    "referencia_regulatoria": "RN 259 — DRAFT/verify (teste)",
    "responsavel_id": "gestao-rede-sintetico-001",
    "tier": "senior",
}

# FINDING 1 — RESOLVED (worker) / PARTIAL (test asserts): ST_PrepareRemediationDossier now has a
# real implementing worker (t2-dossier-a2a, PR #178, DL-0033 real wiring): it delegates to Andre
# `analytics.population` when the signed dispatcher is available and otherwise fail-neutral-completes
# with a disclosed gap marker (DL-0037) — either way the external task COMPLETES and the instance
# drains, so `_PREPARE_DOSSIER_TOPIC` is now in `_ADEQUACAO_WORKER_TOPICS` and UT_DecisaoFallback/
# UT_CoordenacaoRede are reachable again. Verified live (local CIB Seven 2.1.0): 4 of the 9 former
# dossier xfails now PASS as real tests. The remaining 5 (re-marked below) are NOT blocked by the
# worker anymore — they still assert the pre-#178 dead `notifications_of_type("adequacao.<worker>")`
# kafka echo (returns [] now), which needs adaptation to engine-side evidence (activity-history /
# has_event with the completed desfecho) per the #134-141 event-gap precedent. Distinct, narrower
# follow-up; the human-decision branch itself is live.
_MISSING_DOSSIER_WORKER_REASON = (
    "PARTIAL (t2-dossier-a2a #178): `prepare_remediation_dossier` is now a REAL worker and the "
    "external task drains (UT branch live — 4 sibling dossier tests flipped to real passes). This "
    'test still fails ONLY on a dead `notifications_of_type("adequacao.<worker>")` assert (the '
    "pre-#178 kafka echo the worker no longer emits — returns []); it needs adaptation to "
    "engine-side evidence (activity-history / has_event completed-desfecho, per the #134-141 "
    "event-gap precedent) before it flips. NOT a missing-worker gap; narrower assert-adaptation "
    "follow-up tracked in PLANS.md §0.5.2 item-9 bucket-1."
)

# t2.5-p2b-round2: `update_monitoring_plan` is now BUILT and registered (was previously the same
# MISSING-WORKER gap as `_MISSING_DOSSIER_WORKER_REASON`). This test STILL xfails, but on a
# DIFFERENT, narrower gap now: `End_MonitoramentoAtualizado` is genuinely reachable (the branch
# completes — `update_monitoring_plan` -> `notify_rede` -> generic `operadora.events.publish` ->
# End), so the `_END_MONITORAMENTO`/no-user-task assertions are expected to PASS now. The test
# still fails (xfail stays correct) on its `notifications_of_type("adequacao.update_monitoring_
# plan")` assertion: `update_monitoring_plan` mirrors the family's dict-first/`del kafka` idiom
# (same as every other adequacao.py function) and never calls `kafka.publish` — Finding 3's TOTAL
# Kafka-publish gap now applies to THIS test directly. Blocks 1 test below.
_MISSING_MONITORING_WORKER_REASON = (
    "built, pending live-proof flip (t2.5-p2b-round2): adequacao.py's `update_monitoring_plan` is "
    "now implemented and registered on `operadora.adequacao.update_monitoring_plan` (closes the "
    "prior MISSING-WORKER gap — ST_UpdateMonitoringPlanL3 is genuinely fetched/completed by this "
    "suite's drain now, `_ADEQUACAO_WORKER_TOPICS` includes it) — `End_MonitoramentoAtualizado` "
    "and the no-user-task assertions are expected to pass. This test still xfails on Finding 3's "
    "TOTAL Kafka-publish gap instead: `update_monitoring_plan` is dict-first and never calls "
    "`kafka.publish` (mirrors every other adequacao.py function's proven idiom -- this port "
    "deliberately does NOT add worker-side Kafka publishing, per the queued systemic Kafka-seam "
    "task), so `notifications_of_type('adequacao.update_monitoring_plan')` (backed by "
    "`FakeKafkaPublisher.published`) is still always empty. Expect this to flip only alongside "
    "the systemic Kafka-producer-wiring fix, not independently -- live-proof needed against a "
    "real engine before removing this xfail."
)

# FINDING 2 — measure_gap (adequacao.py:38-69) overwrites the donor's seeded geo-facts before the
# DMN ever reads them, changing which gap_adequacao/roteamento_remediacao is actually reachable.
# Blocks 3 tests below (distinct from the registration gaps above: the DMN mis-routes, it isn't
# just blocked further downstream).
_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON = (
    "v2 behavioral drift in `measure_gap` (adequacao.py lines 38-69), NOT a registration gap: the "
    "donor's fixture technique — seeding tempo_acesso_apurado_min/distancia_apurada_km/"
    "cobertura_geo_suficiente/dados_geo_completos as START variables so the deployed DMN reads "
    "them directly (mirrors test_sp_op_cancel_001.py/test_sp_op_contas_001.py) — is invalidated "
    "for these specific facts: `ST_MeasureCoverage` runs BEFORE `BRT_AdequacaoGap` and its real "
    "(explicitly placeholder) implementation OVERWRITES every one of them first. "
    "tempo_acesso_apurado_min/distancia_apurada_km are hardcoded to 45/15.5 (lines 48-49, "
    "'Placeholder: real implementation queries geo-location / network DB'); "
    "cobertura_geo_suficiente is DERIVED as prestadores_disponiveis >= 2 (line 51, ignoring any "
    "seeded value); dados_geo_completos is DERIVED as bool(regiao_saude and especialidade) (line "
    "52) — ALWAYS True in this suite (start_adequacao always supplies non-empty regiao_saude/"
    "especialidade for the business key). Combined with the ALREADY-DOCUMENTED adequacao_gap "
    "rule-order divergence (adequacao.py lines 90-99; unit-proven, "
    "tests/unit/tools/workers/test_adequacao.py::test_adequacao_gap_conforme_now_leve): the "
    "deployed table's GAP_LEVE row precedes CONFORME's for tipo_carater='eletivo' whenever "
    "tempo<=60/distancia<=50 — BOTH always true given the hardcoded 45/15.5. Consequences: (a) "
    "dados_geo_completos=False can never reach the DMN (always overwritten True) — the "
    "ANALISE_HUMANA-via-incomplete-data branch is untestable via any start-seeded scenario; (b) "
    "gap_adequacao=CONFORME is unreachable for tipo_carater='eletivo' (this suite's fixture "
    "default) whenever prestadores_disponiveis>=2 — GAP_LEVE's row always wins first; (c) "
    "gap_adequacao=GAP_MODERADO requires prestadores_disponiveis EXACTLY 1 (cobertura_geo_"
    "suficiente derives false only when prestadores<2), not 2 as this donor scenario used "
    "(prestadores=2 now yields GAP_LEVE instead). Not a registration gap — a real-implementation-"
    "vs-test-fixture mismatch that predates this port (measure_gap was always a placeholder), "
    "needing either a fixture seam (inject the apurado facts directly) or a real geo-lookup "
    "implementation before these DMN-routing-dependent scenarios are reachable as designed."
)


# ---------------------------------------------------------------------------
# EngineProbe para adequacao (espelha CancelEngineProbe; self-contained fixtures)
# ---------------------------------------------------------------------------


@dataclass
class AdequacaoEngineProbe:
    """Driva os workers reais de Phase-3 (adequacao) contra o engine CIB Seven."""

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
        """ALWAYS [] for this family (FINDING 3) — no adequacao.py worker calls kafka.publish."""
        return [v for (_t, v, _k) in self._captured if v.get("type") == ntype]

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(
            self.transport, self.harness, self.worker_id, _ADEQUACAO_WORKER_TOPICS, rounds=rounds
        )


# ---------------------------------------------------------------------------
# Fixtures (self-contained — override de modulo; NAO edita conftest.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de adequacao da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_GAP, _DMN_ROUTING, _DMN_SLA, name="SP-OP-ADEQUACAO-001-qa")


@pytest_asyncio.fixture
async def adequacao_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[AdequacaoEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-3 de adequacao.

    Monta: `CibSevenWorkerTransport` (REST real) + `WorkerHarness` + `register_adequacao_workers`
    (com o seam `dmn=` de um `CibSevenDmnTransport` REAL, ADR-0028/T1.5) +
    `register_events_workers` (generic publish, T3.1 R2) + `FakeKafkaPublisher`.
    """
    worker_id = f"qa-adequacao-worker-{uuid.uuid4().hex[:8]}"
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
    # ADR-0028/T1.5: route_remediation evaluates adequacao_gap + adequacao_remediation_routing via
    # the REAL deployed engine (never a mock, ADR-0011) — same live engine `engine`/`transport`
    # already talk to.
    dmn_transport = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    register_adequacao_workers(harness, kafka, dmn=dmn_transport)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in this
    # BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors cancel.py's fixture, verbatim intent): todo topico operadora.adequacao.*
    # registrado no harness DEVE estar na lista de drain — falha AQUI, explicita, se um worker
    # novo ficar fora. Also documents FINDING 1: after t2.5-p2b-round2, 7 of the 8 BPMN-declared
    # topics are registered (only `prepare_remediation_dossier` remains gapped), so this passes
    # NATURALLY (nothing registered is left undrained).
    adequacao_registered = {t for t in harness.registered_topics if t.startswith("operadora.adequacao.")}
    # DL-0033 (t5): operadora.adequacao.prepare_remediation_dossier is now a REGISTERED local stub,
    # but this probe deliberately does NOT drain it (keeps the engine xfails valid pending a dedicated
    # live-engine proof that would flip them). Exclude it explicitly; the guard still catches any
    # OTHER registered-but-undrained worker.
    missing_from_drain = adequacao_registered - set(_ADEQUACAO_WORKER_TOPICS) - {_PREPARE_DOSSIER_TOPIC}
    assert not missing_from_drain, (
        f"_ADEQUACAO_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = AdequacaoEngineProbe(
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
        await dmn_transport.close()


def _unique_regiao(prefix: str = "REGIAO-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_adequacao(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key ADEQ-amh-{regiao}-{especialidade}-{ciclo}.

    Os fatos apurados por worker (tempo/distancia/contagem/cobertura/dados_geo_completos) sao
    seeded como variaveis de start: o WorkerHarness nao retorna output vars ao engine no sentido
    literal, entao o teste (agente de origem) seed os fatos que a DMN adequacao_gap le — MAS ver
    FINDING 2 (`_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON`): `measure_gap` reescreve boa parte
    destes fatos antes da DMN avaliar.

    Default = gap critico (sem prestador) -> ANALISE_HUMANA (User Task).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        regiao = overrides.pop("regiao_saude", _unique_regiao())
        especialidade = overrides.get("especialidade", "cardiologia")
        ciclo = overrides.get("ciclo_avaliacao", "2026-Q2")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "regiao_saude": regiao,
            "especialidade": especialidade,
            "ciclo_avaliacao": ciclo,
            "gatilho": "mudanca_rede",
            "network_change_ref": "STUB-CRED-network_changed-0001",
            "tipo_carater": "eletivo",
            # fatos apurados (seeded — workers os observam/ecoam, a DMN os le; ver FINDING 2)
            "tempo_acesso_apurado_min": 200,
            "distancia_apurada_km": 80.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        }
        variables.update(overrides)
        business_key = f"ADEQ-amh-{regiao}-{especialidade}-{ciclo}"
        return await engine.start_by_key("SP-OP-ADEQUACAO-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE no-adverse: prova que End_CompromissoFallbackHumano nao existe sem UT humana.

    Consulta a historia do engine: se o terminal adverso esta no historico, exige >= 1 User Task
    humana (_UT_HUMANAS) tambem no historico. Prova negativa: se o terminal adverso nao esta no
    historico -> ok por vacuidade.
    """
    ended = await engine.activity_instances_ended(iid)
    if _END_ADVERSO in ended:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE no-adverse VIOLADA (adequacao_fallback_commitment): terminal adverso "
            f"{_END_ADVERSO} atingido para instancia {iid} SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. Atividades historicas: {ended}. "
            "Isto indica um caminho automatizado de compromisso de fallback — violacao do no-adverse."
        )


async def _drive_to_decisao(engine: EngineRest, probe: AdequacaoEngineProbe, iid: str) -> Any:
    """Drena ate UT_DecisaoFallback surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_DECISAO)


async def _drive_to_coordenacao(engine: EngineRest, probe: AdequacaoEngineProbe, iid: str) -> Any:
    """Drena ate UT_DecisaoFallback, dispara BT_SlaRemediacao via execute_job (NUNCA sleep) e drena
    ate UT_CoordenacaoRede surgir (GAP-ADEQ-3: caminho de estouro de SLA — decisao_coordenacao so
    existe/e lida a partir daqui)."""
    await _drive_to_decisao(engine, probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaRemediacao")
    await engine.execute_job(job.id)
    await probe.drain()
    return await engine.await_user_task(iid, _UT_COORDENACAO)


# ===========================================================================
# INVARIANTE no-adverse — DoD deliverable (varredura de inputs da DMN)
# ===========================================================================


async def test_nenhum_caminho_automatizado_firma_compromisso_fallback(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """INVARIANTE no-adverse: NENHUM caminho automatizado firma o compromisso de fallback.

    Varredura de combinacoes apuradas que cobrem CONFORME / GAP_LEVE / GAP_MODERADO / GAP_CRITICO e
    dados_geo_completos true/false. Para cada instancia que estabiliza sem completar User Task:
    NENHUMA atinge End_CompromissoFallbackHumano. Prova via historia do engine.

    NOTE (documented, NOT a weakening — assertion body byte-identical to donor): given FINDING 1/2
    (module docstring), several of the cenarios below never fully drain in v2 (the instance gets
    stuck at a service task with no registered worker, or is DMN-rerouted to a different branch
    than the donor intended) — the invariant still holds for every cenario, genuinely for the ones
    that DO complete (CONFORME/GAP_MODERADO-as-intended) and vacuously for the ones that get stuck
    upstream of any adverse terminal. Left UNMARKED: no single assertion here has a concrete
    failure to xfail.
    """
    cenarios = [
        # (tipo_carater, tempo_min, dist_km, prestadores, cobertura, dados_completos)
        ("eletivo", 30, 10.0, 5, True, True),  # CONFORME -> MONITORAR -> neutro
        ("eletivo", 75, 20.0, 3, True, True),  # GAP_LEVE -> MONITORAR -> neutro
        ("eletivo", 120, 40.0, 2, False, True),  # GAP_MODERADO -> ENCAMINHAR_CRED -> neutro
        ("urgencia_emergencia", 90, 30.0, 1, False, True),  # GAP_CRITICO -> ANALISE_HUMANA (UT)
        ("eletivo", 30, 10.0, 5, True, False),  # dados incompletos -> ANALISE_HUMANA (UT)
        ("eletivo", 75, 20.0, 0, True, True),  # sem prestador -> GAP_CRITICO -> ANALISE_HUMANA (UT)
    ]
    checked = 0
    for carater, tempo, dist, prest, cob, dados in cenarios:
        inst = await start_adequacao(
            tipo_carater=carater,
            tempo_acesso_apurado_min=tempo,
            distancia_apurada_km=dist,
            prestadores_disponiveis=prest,
            cobertura_geo_suficiente=cob,
            dados_geo_completos=dados,
        )
        iid = inst["id"]
        await adequacao_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        assert _END_ADVERSO not in ended, (
            f"no-adverse VIOLADO: cenario carater={carater} tempo={tempo} dist={dist} prest={prest} "
            f"cobertura={cob} dados={dados} atingiu {_END_ADVERSO} automaticamente. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == len(cenarios)


async def test_gap_critico_roteia_para_humano_nao_firma_compromisso(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Gap critico (sem prestador) => adequacao_gap=GAP_CRITICO -> routing=ANALISE_HUMANA.

    Fluxo chega a UT_DecisaoFallback (gestao-rede) — NAO a End_CompromissoFallbackHumano.
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, adequacao_probe, iid)
    assert "gestao-rede" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_ADVERSO not in ended, "Gap critico nao deve firmar compromisso automaticamente"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON, strict=True)
async def test_dados_incompletos_roteia_para_humano(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """dados_geo_completos=false => routing catch-all -> ANALISE_HUMANA (nunca decide sem dados).

    v2: `measure_gap` recomputa dados_geo_completos=bool(regiao_saude and especialidade), que e
    SEMPRE True neste fixture (regiao/especialidade nunca vazios) — o seed abaixo (dados_geo_
    completos=False) e sobrescrito ANTES da DMN avaliar. A instancia e re-roteada para MONITORAR
    (nao ANALISE_HUMANA) e trava la (FINDING 1, `_MISSING_MONITORING_WORKER_REASON`) — mas a causa
    RAIZ documentada aqui e o override do measure_gap, nao o worker faltante.
    """
    inst = await start_adequacao(
        tipo_carater="eletivo",
        tempo_acesso_apurado_min=30,
        distancia_apurada_km=10.0,
        prestadores_disponiveis=5,
        cobertura_geo_suficiente=True,
        dados_geo_completos=False,
    )
    iid = inst["id"]
    ut = await _drive_to_decisao(engine, adequacao_probe, iid)
    assert "gestao-rede" in ut.candidate_groups
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Caminhos L3 autonomos (sem User Task)
# ===========================================================================


@pytest.mark.xfail(reason=_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON, strict=True)
async def test_l3_conforme_atinge_neutro_sem_user_task(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Rede conforme (dentro RN 259) => adequacao_gap=CONFORME -> MONITORAR -> End_AdequacaoConforme.

    Caminho L3 autonomo: NENHUMA User Task; adequacao.completed desfecho=conforme.

    v2: CONFORME e INALCANCAVEL para tipo_carater="eletivo" com measure_gap's hardcoded tempo=45/
    distancia=15.5 (ambos dentro dos thresholds de GAP_LEVE, que precede CONFORME no FIRST-hit da
    adequacao_gap) — a instancia cai em GAP_LEVE/MONITORAR em vez disso, e trava la (FINDING 1,
    `_MISSING_MONITORING_WORKER_REASON`, que tambem se aplicaria mesmo se a rota estivesse certa).
    """
    inst = await start_adequacao(
        tipo_carater="eletivo",
        tempo_acesso_apurado_min=30,
        distancia_apurada_km=10.0,
        prestadores_disponiveis=5,
        cobertura_geo_suficiente=True,
        dados_geo_completos=True,
    )
    iid = inst["id"]
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_CONFORME in ended, f"Deve atingir End_AdequacaoConforme (L3). ended={ended}"
    assert not await engine.list_user_tasks(iid), "Caminho conforme L3 nao cria User Task"
    assert adequacao_probe.has_event(_ADEQ_RECEIVED)
    assert adequacao_probe.has_event(_ADEQ_COMPLETED, desfecho="conforme")
    assert not adequacao_probe.notifications_of_type("adequacao.register_fallback_commitment"), (
        "Caminho conforme NUNCA firma compromisso (L3)"
    )


@pytest.mark.xfail(reason=_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON, strict=True)
async def test_l3_gap_moderado_encaminha_credenciamento_sem_user_task(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Gap moderado => routing=ENCAMINHAR_CREDENCIAMENTO -> handoff CRED -> End_RemediacaoEncaminhada.

    Caminho L3 autonomo (handoff a SP-OP-CRED-001 NAO e adverso): NENHUMA User Task;
    adequacao.completed desfecho=encaminhada_credenciamento; gap_detected publicado.

    v2: GAP_MODERADO exige prestadores_disponiveis EXATAMENTE 1 (cobertura_geo_suficiente deriva
    false so quando prestadores<2) — este cenario usa prestadores=2, que agora produz GAP_LEVE
    (nao GAP_MODERADO) e trava no worker faltante de monitoramento (FINDING 1) em vez de alcancar
    End_RemediacaoEncaminhada.
    """
    inst = await start_adequacao(
        tipo_carater="eletivo",
        tempo_acesso_apurado_min=120,
        distancia_apurada_km=40.0,
        prestadores_disponiveis=2,
        cobertura_geo_suficiente=False,
        dados_geo_completos=True,
    )
    iid = inst["id"]
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_ENCAMINHADA in ended, f"Deve atingir End_RemediacaoEncaminhada (L3). ended={ended}"
    assert not await engine.list_user_tasks(iid), "Handoff a credenciamento L3 nao cria User Task"
    assert adequacao_probe.notifications_of_type("adequacao.start_credenciamento")
    assert adequacao_probe.has_event(_ADEQ_COMPLETED, desfecho="encaminhada_credenciamento")
    assert adequacao_probe.has_event(_ADEQ_GAP_DETECTED, gap_adequacao="GAP_MODERADO")


@pytest.mark.xfail(reason=_MISSING_MONITORING_WORKER_REASON, strict=True)
async def test_l3_gap_leve_monitora_sem_user_task(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Gap leve => routing=MONITORAR -> atualiza plano + notifica rede -> End_MonitoramentoAtualizado.

    v2: o roteamento da DMN esta CORRETO aqui (GAP_LEVE, coincidentemente inalterado pelo override
    de measure_gap — ver FINDING 2) mas ST_UpdateMonitoringPlanL3 nao tem worker registrado
    (FINDING 1) — a instancia nunca completa.
    """
    inst = await start_adequacao(
        tipo_carater="eletivo",
        tempo_acesso_apurado_min=75,
        distancia_apurada_km=20.0,
        prestadores_disponiveis=3,
        cobertura_geo_suficiente=True,
        dados_geo_completos=True,
    )
    iid = inst["id"]
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_MONITORAMENTO in ended, f"Deve atingir End_MonitoramentoAtualizado (L3). ended={ended}"
    assert not await engine.list_user_tasks(iid), "Caminho monitorar L3 nao cria User Task"
    assert adequacao_probe.notifications_of_type("adequacao.update_monitoring_plan")
    assert adequacao_probe.notifications_of_type("adequacao.notify_rede")
    assert adequacao_probe.has_event(_ADEQ_COMPLETED, desfecho="monitoramento_atualizado")


# ===========================================================================
# Happy path adverso (so via humano) + handoff via decisao humana
# ===========================================================================


async def test_happy_path_compromisso_fallback_humano(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Gap critico => UT_DecisaoFallback; humano COMPROMISSO_FALLBACK => End_CompromissoFallbackHumano.

    register_fallback_commitment executado (guard satisfeito); adequacao.completed
    desfecho=compromisso_fallback_humano; responsavel_id na trilha. UNICO caminho ao terminal adverso.
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, adequacao_probe, iid)
    assert "gestao-rede" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_remediacao": "COMPROMISSO_FALLBACK",
            "estimativa_custo_cents": 1500000,
            **_CAMPOS_FALLBACK,
        },
    )
    await adequacao_probe.drain()

    ended = await _await_end(engine, iid)
    # PROVA: End_CompromissoFallbackHumano so existe porque a UT humana foi completada.
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_ADVERSO in ended, f"Compromisso humano deve atingir {_END_ADVERSO}. ended={ended}"
    assert adequacao_probe.has_event(_ADEQ_COMPLETED, desfecho="compromisso_fallback_humano")

    # Engine-side (o kafka-echo `notifications_of_type` morreu pos-#178): o serviceTask do worker
    # register_fallback_commitment EXECUTOU apos a decisao humana. A trilha de accountability
    # (decisao_remediacao/responsavel_id/tier, _CAMPOS_FALLBACK) foi setada pelo humano acima; o
    # desfecho `compromisso_fallback_humano` (has_event acima) so e alcancado por este caminho.
    assert "ST_RegisterFallbackCommitment" in ended, (
        "register_fallback_commitment (ST_RegisterFallbackCommitment) deve executar apos a decisao humana"
    )


async def test_humano_encaminhar_cred_nao_firma_compromisso(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Gap critico => UT; humano ENCAMINHAR_CRED => End_RemediacaoEncaminhada (sem compromisso)."""
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, adequacao_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_remediacao": "ENCAMINHAR_CRED"})
    await adequacao_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENCAMINHADA in ended, (
        f"ENCAMINHAR_CRED humano deve atingir End_RemediacaoEncaminhada. ended={ended}"
    )
    assert _END_ADVERSO not in ended, "ENCAMINHAR_CRED nao firma compromisso de fallback"
    assert not adequacao_probe.notifications_of_type("adequacao.register_fallback_commitment")


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """SLA de remediacao estoura => publica sla_breached => UT_CoordenacaoRede (humano assume).

    NUNCA auto-compromisso por timeout: o timer interruptivo re-roteia para a coordenacao humana.
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    assert "coordenacao-rede" in coord.candidate_groups
    assert adequacao_probe.has_event(_ADEQ_SLA_BREACHED)
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_assume_e_firma_compromisso_fallback_humano(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """End_CompromissoFallbackHumano-only-via-UT (GAP-ADEQ-2): PROVA que o terminal adverso e
    alcancavel pela SEGUNDA via humana (UT_CoordenacaoRede), nao so por UT_DecisaoFallback direta
    (ja coberta por test_happy_path_compromisso_fallback_humano).

    SLA de remediacao estoura (UT_DecisaoFallback cancelada); UT_CoordenacaoRede assume; a
    coordenacao humana (grupo coordenacao-rede) completa com decisao_coordenacao=assumir_decisao
    (a coordenacao decide AGORA — GW_DecisaoCoordenacao roteia para GW_DecisaoRemediacao, GAP-ADEQ-3)
    + decisao_remediacao=COMPROMISSO_FALLBACK + campos obrigatorios (GAP-ADEQ-1, #94: SEM tier, que
    nenhuma UT coleta) => register_fallback_commitment executado (guard satisfeito) =>
    End_CompromissoFallbackHumano. Mesma tecnica de
    test_sp_op_cred_001.py::test_coordenacao_assume_e_descredencia (a decisao adversa NUNCA muda de
    natureza por estouro de SLA — a UT de coordenacao herda os MESMOS campos obrigatorios).

    NB (re-land #110/#120): decisao_coordenacao=assumir_decisao e OBRIGATORIO no contrato
    (coordenador-disposition) para esta via; sem ele a UT caia no default seguir_analise. O
    hardening de default-init (ST_PublishSlaBreach) garante que a OMISSAO nao quebra o engine
    (coberto por test_coordenacao_omite_decisao_cai_no_default_seguir_analise).
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    assert "coordenacao-rede" in coord.candidate_groups

    # PROVA (antes de completar): o terminal adverso ainda NAO foi atingido so pelo estouro de SLA.
    ended_antes = await engine.activity_instances_ended(iid)
    assert _END_ADVERSO not in ended_antes, (
        "Estouro de SLA sozinho NUNCA firma o compromisso — a decisao continua humana (UT_CoordenacaoRede)"
    )

    await engine.complete_task_as_human(
        coord.id,
        {
            "decisao_coordenacao": "assumir_decisao",
            "decisao_remediacao": "COMPROMISSO_FALLBACK",
            "estimativa_custo_cents": 2_000_000,
            "responsavel_id": "coordenacao-rede-sintetica-001",
            "tipo_fallback": "livre_escolha",
            "justificativa_fallback": "Prazo esgotado — coordenacao firma compromisso (sintetico, teste)",
            "referencia_regulatoria": "RN 259 — DRAFT/verify (teste)",
        },
    )
    await adequacao_probe.drain()

    ended = await _await_end(engine, iid)
    # PROVA: End_CompromissoFallbackHumano so existe porque a UT_CoordenacaoRede humana foi concluida.
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_ADVERSO in ended, (
        f"Compromisso via coordenacao humana deve atingir {_END_ADVERSO}. ended={ended}"
    )
    assert adequacao_probe.has_event(_ADEQ_COMPLETED, desfecho="compromisso_fallback_humano")

    # Engine-side (kafka-echo morto pos-#178): register_fallback_commitment executou apos a decisao
    # da coordenacao. responsavel_id=coordenacao-* foi input humano; o desfecho compromisso_fallback_
    # humano (has_event acima) prova o caminho.
    assert "ST_RegisterFallbackCommitment" in ended, (
        "register_fallback_commitment (ST_RegisterFallbackCommitment) deve executar "
        "apos a decisao da coordenacao"
    )


# ===========================================================================
# GAP-ADEQ-3 — GW_DecisaoCoordenacao roteia decisao_coordenacao (3 disposicoes)
# ===========================================================================
#
# UT_CoordenacaoRede (estouro de SLA) decide decisao_coordenacao ademais de decisao_remediacao.
# GW_DecisaoCoordenacao (logo apos a UT) roteia cada valor de forma DISTINTA:
#   assumir_decisao -> GW_DecisaoRemediacao (decisao_remediacao, preenchida na mesma UT, roteia
#                       normalmente — inclusive um eventual COMPROMISSO_FALLBACK, sempre humano);
#   prorrogar_prazo  -> reabre UT_DecisaoFallback (prazo estendido; boundary timers rearmados);
#   seguir_analise   -> refaz o dossie (ST_PrepareRemediationDossier) antes de reabrir
#                       UT_DecisaoFallback (default/catch-all conservador).


async def test_coordenacao_assumir_decisao_roteia_para_gw_decisao_remediacao(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """decisao_coordenacao=assumir_decisao => decisao_remediacao (mesma UT) roteia normalmente.

    Prova com ENCAMINHAR_CRED (nao-adverso): a coordenacao assume e decide agora -> handoff a
    credenciamento -> End_RemediacaoEncaminhada (mesmo desfecho que UT_DecisaoFallback produziria).
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    await engine.complete_task_as_human(
        coord.id,
        {"decisao_coordenacao": "assumir_decisao", "decisao_remediacao": "ENCAMINHAR_CRED"},
    )
    await adequacao_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENCAMINHADA in ended, (
        f"assumir_decisao + ENCAMINHAR_CRED deve atingir End_RemediacaoEncaminhada. ended={ended}"
    )
    assert _END_ADVERSO not in ended, "ENCAMINHAR_CRED nao firma compromisso de fallback"
    # Engine-side (kafka-echo morto pos-#178): o serviceTask start_credenciamento (L3) executou.
    assert "ST_StartCredenciamentoL3" in ended
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_prorrogar_prazo_reabre_ut_decisao_fallback(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """decisao_coordenacao=prorrogar_prazo => reabre UT_DecisaoFallback (NAO GW_DecisaoRemediacao).

    Distinto de assumir_decisao: nao decide agora, so estende o prazo. decisao_remediacao NAO e
    exigida nesta completude (o ramo nao passa por GW_DecisaoRemediacao). Fecha a instancia depois
    completando a UT_DecisaoFallback reaberta com MONITORAR_OK (neutro), provando que o loop-back
    de fato reabriu uma User Task funcional (nao um dead-end).
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    await engine.complete_task_as_human(coord.id, {"decisao_coordenacao": "prorrogar_prazo"})
    await adequacao_probe.drain()

    reaberta = await engine.await_user_task(iid, _UT_DECISAO)
    assert "gestao-rede" in reaberta.candidate_groups or "coordenacao-rede" in reaberta.candidate_groups

    await engine.complete_task_as_human(reaberta.id, {"decisao_remediacao": "MONITORAR_OK"})
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_MONITORAMENTO in ended, (
        f"prorrogar_prazo deve reabrir UT_DecisaoFallback funcional. ended={ended}"
    )
    assert _END_ADVERSO not in ended
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_seguir_analise_refaz_dossie_e_reabre_ut_decisao_fallback(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """decisao_coordenacao=seguir_analise (default) => refaz o dossie -> reabre UT_DecisaoFallback.

    Distinto de assumir_decisao e de prorrogar_prazo: passa de novo por
    ST_PrepareRemediationDossier (prepare_remediation_dossier roda >= 2x: dossie inicial + reentrada)
    antes de UT_DecisaoFallback reabrir.
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    # Engine-side (kafka-echo morto pos-#178): conta EXECUCOES de ST_PrepareRemediationDossier no
    # historico (activity_instance_count NAO deduplica, ao contrario de activity_instances_ended) —
    # e exatamente a re-execucao do dossie no loop-back seguir_analise que este teste prova.
    dossies_antes = await engine.activity_instance_count(iid, "ST_PrepareRemediationDossier")
    assert dossies_antes >= 1, "dossie inicial deve ter sido preparado antes de UT_DecisaoFallback"

    await engine.complete_task_as_human(coord.id, {"decisao_coordenacao": "seguir_analise"})
    await adequacao_probe.drain()

    reaberta = await engine.await_user_task(iid, _UT_DECISAO)
    dossies_depois = await engine.activity_instance_count(iid, "ST_PrepareRemediationDossier")
    assert dossies_depois >= dossies_antes + 1, (
        "seguir_analise deve refazer o dossie (ST_PrepareRemediationDossier) antes de reabrir a UT — "
        f"antes={dossies_antes} depois={dossies_depois}"
    )

    await engine.complete_task_as_human(reaberta.id, {"decisao_remediacao": "ENCAMINHAR_CRED"})
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_ENCAMINHADA in ended, (
        f"seguir_analise deve reabrir UT_DecisaoFallback funcional. ended={ended}"
    )
    assert _END_ADVERSO not in ended
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_omite_decisao_cai_no_default_seguir_analise(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """REGRESSAO da colisao #110xteste-do-#108 (revert #120): completar UT_CoordenacaoRede SEM
    decisao_coordenacao NAO pode quebrar o engine (ENGINE-16004 / "Unknown property"/500).

    Este e o cenario EXATO que derrubou o main: um humano completa UT_CoordenacaoRede sem setar
    decisao_coordenacao. O hardening default-init (ST_PublishSlaBreach seta decisao_coordenacao=""
    antes da UT) garante que GW_DecisaoCoordenacao avalie as condicoes contra "" e caia no default
    conservador (seguir_analise) -> refaz o dossie -> reabre UT_DecisaoFallback. Provamos rota limpa:
    dossie refeito (>=+1), UT reaberta funcional, desfecho neutro, sem terminal adverso sem humano.
    """
    inst = await start_adequacao(prestadores_disponiveis=0, dados_geo_completos=True)
    iid = inst["id"]

    coord = await _drive_to_coordenacao(engine, adequacao_probe, iid)
    # Engine-side (kafka-echo morto pos-#178): conta EXECUCOES historicas de ST_PrepareRemediationDossier.
    dossies_antes = await engine.activity_instance_count(iid, "ST_PrepareRemediationDossier")
    assert dossies_antes >= 1

    # Completa a UT SEM decisao_coordenacao (nem decisao_remediacao): a exata omissao da colisao.
    await engine.complete_task_as_human(coord.id, {})
    await adequacao_probe.drain()

    # Sem 500: o gateway roteou pelo default (seguir_analise) -> ST_PrepareRemediationDossier de novo.
    reaberta = await engine.await_user_task(iid, _UT_DECISAO)
    dossies_depois = await engine.activity_instance_count(iid, "ST_PrepareRemediationDossier")
    assert dossies_depois >= dossies_antes + 1, (
        "omitir decisao_coordenacao deve cair no default seguir_analise (refaz o dossie), nunca 500 — "
        f"antes={dossies_antes} depois={dossies_depois}"
    )

    await engine.complete_task_as_human(reaberta.id, {"decisao_remediacao": "MONITORAR_OK"})
    await adequacao_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_MONITORAMENTO in ended, (
        f"omissao de decisao_coordenacao deve reabrir UT_DecisaoFallback funcional. ended={ended}"
    )
    assert _END_ADVERSO not in ended
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_celula(
    engine: EngineRest,
    adequacao_probe: AdequacaoEngineProbe,
    start_adequacao: Callable[..., Any],
) -> None:
    """Mesmo business key (regiao x especialidade x ciclo): nao criar 2a instancia ativa.

    Nao depende de `adequacao_probe.drain()` progredir alem do primeiro publish — sobrevive a
    qualquer um dos gaps documentados acima (nao marcado xfail, ao contrario dos demais).
    """
    regiao = "REGIAO-TESTE-IDEM-001"
    business_key = f"ADEQ-amh-{regiao}-cardiologia-2026-Q2"

    first = await start_adequacao(regiao_saude=regiao)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_adequacao_gap_sem_saida_que_compromete() -> None:
    """O dominio de gap_adequacao da DMN adequacao_gap e EXATAMENTE
    {CONFORME, GAP_LEVE, GAP_MODERADO, GAP_CRITICO}; o roteamento da segunda decisao e EXATAMENTE
    {MONITORAR, ENCAMINHAR_CREDENCIAMENTO, ANALISE_HUMANA}.

    Nenhum valor COMPROMETER/GARANTIR/CONTRATAR; e cada tabela tem row catch-all conservadora.
    Varredura estatica do XML (nao precisa de engine).

    ADAPTED (port rule 1, mechanical only): the donor's v1 `adequacao_gap.dmn` bundled BOTH
    decision tables in one file; v2 deploys them as two separate artifacts
    (`adequacao_gap.dmn` / `adequacao_remediation_routing.dmn`, each independently
    `camunda:decisionRef`'d — grep-confirmed against the BPMN). Parses BOTH and merges their
    `<decision>` elements — assertions unchanged.
    """
    decisions: dict[str, ET.Element] = {}
    for dmn_path in (_DMN_GAP, _DMN_ROUTING):
        tree = ET.parse(dmn_path)
        for d in tree.getroot().iter():
            if _local(d.tag) == "decision":
                decisions[d.get("id")] = d
    assert "adequacao_gap" in decisions
    assert "adequacao_remediation_routing" in decisions

    def _first_output_values(decision: ET.Element) -> tuple[set[str], str | None]:
        vals: set[str] = set()
        last: str | None = None
        for rule in (e for e in decision.iter() if _local(e.tag) == "rule"):
            outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
            assert outputs, "cada rule deve ter outputEntry"
            text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
            assert text_el is not None and text_el.text
            val = text_el.text.strip().strip('"')
            vals.add(val)
            last = val
        return vals, last

    gap_vals, gap_last = _first_output_values(decisions["adequacao_gap"])
    assert gap_vals == {"CONFORME", "GAP_LEVE", "GAP_MODERADO", "GAP_CRITICO"}, (
        f"dominio de gap_adequacao inesperado: {gap_vals}"
    )
    assert gap_last == "GAP_CRITICO", "row catch-all de adequacao_gap deve ser GAP_CRITICO (conservador)"

    rot_vals, rot_last = _first_output_values(decisions["adequacao_remediation_routing"])
    assert rot_vals == {"MONITORAR", "ENCAMINHAR_CREDENCIAMENTO", "ANALISE_HUMANA"}, (
        f"dominio de roteamento_remediacao inesperado: {rot_vals}"
    )
    assert rot_last == "ANALISE_HUMANA", "row catch-all de routing deve ser ANALISE_HUMANA (conservador)"

    # Nenhuma saida (de qualquer coluna) firma/autoriza compromisso financeiro.
    blob = " ".join(gap_vals | rot_vals)
    for proibido in ("COMPROMETER", "GARANTIR", "CONTRATAR", "GARANTIR_REEMBOLSO"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na adequacao_gap (no-adverse)"


def test_adequacao_sla_sem_saida_adversa() -> None:
    """adequacao_sla so produz prazos ISO + fonte_regulatoria — nenhuma saida adversa; catch-all existe."""
    tree = ET.parse(_DMN_SLA)
    root = tree.getroot()

    rules = [e for e in root.iter() if _local(e.tag) == "rule"]
    assert rules, "adequacao_sla deve ter rules"
    for rule in rules:
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert len(outputs) >= 3, "cada rule deve ter sla_remediacao + sla_alerta + fonte_regulatoria"
        for out in outputs[:2]:  # prazos sao ISO 8601
            text_el = next((c for c in out if _local(c.tag) == "text"), None)
            assert text_el is not None and text_el.text
            val = text_el.text.strip().strip('"')
            for proibido in ("COMPROMETER", "GARANTIR", "CONTRATAR"):
                assert proibido not in val, f"saida adversa proibida '{proibido}' na adequacao_sla"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}.

    Nenhuma coluna usa "number". distancia -> double; tempo/contagem -> integer. Varredura estatica.

    ADAPTED (port rule 1, strengthened not weakened): iterates all 3 DMN artifacts this process
    deploys (donor's v1 file bundled adequacao_gap + adequacao_remediation_routing together and
    iterated over 2 paths; v2 deploys 3 separate files, so this checks one MORE file than donor).
    """
    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_GAP, _DMN_ROUTING, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double/integer)"


# ===========================================================================
# BPMN — estrutura (sem engine; varredura estatica do XML)
# ===========================================================================


def test_bpmn_compromisso_so_apos_user_task_humana() -> None:
    """ESTATICO: End_CompromissoFallbackHumano so e alcancavel cruzando uma userTask humana.

    Prova de alcancabilidade direta a partir do startEvent, tratando cada userTask humana como
    semi-absorvente (so escapa por boundary event). Se o terminal adverso for alcancavel sem
    conclusao humana -> falha. Espelha test_no_denial_consolidated (engine-free). Puramente
    estatico — NAO depende de quais workers estao registrados (FINDING 1 nao se aplica aqui).
    """
    bpmn_ns = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
    camunda_ns = "{http://camunda.org/schema/1.0/bpmn}"
    proc = ET.parse(_BPMN).getroot().find(f"{bpmn_ns}process")
    assert proc is not None

    starts = [e.get("id", "") for e in proc.iter(f"{bpmn_ns}startEvent")]
    human_uts = {
        ut.get("id", "")
        for ut in proc.iter(f"{bpmn_ns}userTask")
        if (ut.get(f"{camunda_ns}candidateGroups") or "").strip()
    }
    assert human_uts, "o corpo deve ter >= 1 userTask humana (o gate existe)"

    flows: dict[str, list[str]] = {}
    for sf in proc.iter(f"{bpmn_ns}sequenceFlow"):
        src, tgt = sf.get("sourceRef"), sf.get("targetRef")
        if src and tgt:
            flows.setdefault(src, []).append(tgt)
    boundary_of: dict[str, list[str]] = {}
    for be in proc.iter(f"{bpmn_ns}boundaryEvent"):
        att = be.get("attachedToRef")
        if att and be.get("id"):
            boundary_of.setdefault(att, []).append(be.get("id", ""))

    seen: set[str] = set()
    stack = list(starts)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if node in human_uts:
            stack.extend(boundary_of.get(node, []))
        else:
            stack.extend(flows.get(node, []))

    assert _END_ADVERSO not in seen, (
        f"{_END_ADVERSO} alcancavel SEM cruzar uma userTask humana — viola o no-adverse (ADR-0018)"
    )
    # Os terminais neutros L3 SAO alcancaveis sem User Task (caminho autonomo).
    assert _ENDS_NEUTROS_L3 & seen, "ao menos um terminal neutro L3 deve ser alcancavel autonomamente"


def test_gw_decisao_coordenacao_roteia_tres_disposicoes() -> None:
    """ESTATICO (GAP-ADEQ-3): GW_DecisaoCoordenacao roteia decisao_coordenacao em 3 ramos distintos.

    UT_CoordenacaoRede nao flui mais direto a GW_DecisaoRemediacao: passa por um gateway dedicado
    que le `decisao_coordenacao` (contrato SP-OP-ADEQUACAO-001.md, secao "Roteamento de
    decisao_coordenacao"). assumir_decisao -> GW_DecisaoRemediacao; prorrogar_prazo ->
    UT_DecisaoFallback; seguir_analise (default/catch-all) -> ST_PrepareRemediationDossier.
    """
    bpmn_ns = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
    proc = ET.parse(_BPMN).getroot().find(f"{bpmn_ns}process")
    assert proc is not None

    gateways = {g.get("id"): g for g in proc.iter(f"{bpmn_ns}exclusiveGateway")}
    assert "GW_DecisaoCoordenacao" in gateways, "GW_DecisaoCoordenacao ausente (GAP-ADEQ-3)"
    gw = gateways["GW_DecisaoCoordenacao"]

    flows = {sf.get("id"): sf for sf in proc.iter(f"{bpmn_ns}sequenceFlow")}

    # UT_CoordenacaoRede -> GW_DecisaoCoordenacao (nao mais direto a GW_DecisaoRemediacao).
    ut_coord_gw_flow = flows.get("Flow_UTCoord_GW")
    assert ut_coord_gw_flow is not None
    assert ut_coord_gw_flow.get("sourceRef") == "UT_CoordenacaoRede"
    assert ut_coord_gw_flow.get("targetRef") == "GW_DecisaoCoordenacao", (
        "UT_CoordenacaoRede deve fluir para GW_DecisaoCoordenacao, nao direto a GW_DecisaoRemediacao"
    )

    outgoing_ids = [c.text for c in gw if _local(c.tag) == "outgoing"]
    default_flow_id = gw.get("default")
    assert default_flow_id, "GW_DecisaoCoordenacao deve ter um default (catch-all conservador)"

    targets_by_condition: dict[str, str] = {}
    for fid in outgoing_ids:
        sf = flows[fid]
        cond_el = next((c for c in sf if _local(c.tag) == "conditionExpression"), None)
        if cond_el is not None and cond_el.text:
            targets_by_condition[cond_el.text.strip()] = sf.get("targetRef", "")
        else:
            assert fid == default_flow_id, f"{fid}: sem condicao mas nao e o default do gateway"

    assert targets_by_condition == {
        "${decisao_coordenacao == 'assumir_decisao'}": "GW_DecisaoRemediacao",
        "${decisao_coordenacao == 'prorrogar_prazo'}": "UT_DecisaoFallback",
    }, f"roteamento inesperado de decisao_coordenacao: {targets_by_condition}"

    default_sf = flows[default_flow_id]
    assert default_sf.get("sourceRef") == "GW_DecisaoCoordenacao"
    assert default_sf.get("targetRef") == "ST_PrepareRemediationDossier", (
        "seguir_analise (default) deve rotear para ST_PrepareRemediationDossier (refaz o dossie)"
    )

    # GW_DecisaoRemediacao agora e alcancado pela UT_CoordenacaoRede SOMENTE via GW_DecisaoCoordenacao
    # (Flow_GWCoord_Assumir) — nunca mais diretamente por Flow_UTCoord_GW.
    gw_remediacao_incoming = {
        c.text
        for gwr in proc.iter(f"{bpmn_ns}exclusiveGateway")
        if gwr.get("id") == "GW_DecisaoRemediacao"
        for c in gwr
        if _local(c.tag) == "incoming"
    }
    assert "Flow_UTCoord_GW" not in gw_remediacao_incoming
    assert "Flow_GWCoord_Assumir" in gw_remediacao_incoming
