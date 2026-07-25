"""SP-OP-PAGTO-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase-2 (13-family
process-suite port, V2-COMPLETION-PLAN §3). pagto.py IS one of ADR-0028's 9 DMN-migrated modules
(`pagto_admissibility`/`pagto_alcada`/`pagto_sla` are engine-native `businessRuleTask`s,
`camunda:decisionRef=...`) — it also imports `CeilingResolver` (T1.9), same "auto-approval ceiling"
family as auth.py/reembolso.py.

Implementa o invariante L1 (no-adverse, high_value_payment) do contrato
docs/processes/contracts/SP-OP-PAGTO-001.md contra o engine real (ADR-0011: SEM mock de engine).
Cada teste:

1. inicia a instancia via REST com business key `PAGTO-amh-{ordem_pagamento_id}`;
2. drena as external tasks com o `pagto_probe` (workers reais de pagto + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que a liberacao de alto valor passa
   pela UT humana com tier-match — no-adverse / high_value_payment L1).

Dados sinteticos obvios: ordem `ORDEM-TESTE-NNNN`, prestador `prov:teste-0001`, conta
`conta:teste-origem`, tenant `amh` (mirrors donor exactly — no new fake data invented). Business
key `PAGTO-amh-{ordem}`. Process key: SP-OP-PAGTO-001 (exato — nao alterar).

## Invariante L1 (DoD deliverable) — high_value_payment / no-adverse

test_nenhum_pagamento_acima_teto_auto_libera:
  Varredura de combinacoes de input da DMN pagto_alcada (valor x seed dentro_teto_l2 x
  tipo_pagamento). A instancia NUNCA atinge End_PagamentoLiberadoHumano sem que UT_AprovacaoAlcada
  / UT_CoordenacaoAlcada tenha sido completada por humano com decisao_pagamento=APROVAR e tier
  compativel — esta metade do invariante e verdadeira e NAO xfailed em espirito (a "prova
  negativa" `_assert_no_adverse_without_human_task` nunca falha em nenhuma combinacao testada). O
  teste como um TODO e marcado xfail apenas porque sua SEGUNDA metade (auto-liberacao abaixo do
  teto e funcao SO do valor computado, independente do seed) quebra por um gap v2 genuino — ver
  FINDING 1 abaixo. Assercoes mantidas BYTE-IDENTICAS ao donor (regra 1 do charter).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.pagto`/`harness`/`events`/`dmn_transport`;
    `FakeKafkaPublisher` moved to `harness.py` (T1.1); donor's `phase0.py` ->
    `maezo.tools.workers.events.register_events_workers` (T3.1 R2, same fix auth.py/cancel.py
    already document — see FINDING 5).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `engine` fixture reused from the shared `conftest.py` (NOT redefined locally, unlike the
    donor's own module, which self-contained even `engine`) — `deploy_artifacts`/`pagto_probe`/
    `start_pagto` stay LOCAL to this file, mirroring cancel.py's self-contained-fixture STRUCTURE
    and conftest.py's own stated hoisting boundary ("only `engine` + `drain_topics` are identical
    across families").
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — topics wrapped in `TopicSubscription`, `async_response_timeout_ms`
    instead of the donor's bare `lock_duration_ms` + `asyncio.sleep(delay)` loop) — see
    `conftest.py`.
  - **NEW port-specific adaptation (neither auth.py nor cancel.py needed this): the `dmn` seam.**
    pagto.py's `route_aprovacao`/`assess_admissibility` are ADR-0028-migrated — they call
    `dmn_transport.require_dmn(dmn, topic)` unconditionally, which RAISES `DmnEvaluationError`
    (a `RuntimeError`) when `dmn is None` (`dmn_transport.py:363-375`). Omitting this seam would
    make the FIRST `operadora.pagto.calculate_facts` dispatch fail on every single test (the
    harness classifies `DmnEvaluationError` as transient and retries it into an eventual incident,
    silently stalling the whole suite at the second external task). `pagto_probe` therefore builds
    a real `CibSevenDmnTransport(CIBSEVEN_BASE_URL)` and passes it as `register_pagto_workers(
    harness, kafka, dmn=dmn_transport)` — mirroring production wiring EXACTLY
    (`maezo/runtime/worker_runtime/service.py:278`: `state.dmn_transport =
    CibSevenDmnTransport(...)`), not a test-only shortcut.

FINDINGS (see PR body / evidence-ledger for full detail):

  0. CORRECTION to the brief handed into this port (verify-before-citing discipline, mirroring
     auth.py's own self-correction note): the brief claimed D-07 (tenant ceiling `0`, fail-closed
     `CeilingResolver`) applies to pagto the same way it blocks auth's/reembolso's auto-approval.
     VERIFIED FALSE by reading the actual policy files: `spec/policies/autonomy/L0-core.yaml:21`
     sets `high_value_payment: { level: L1, params: { threshold_brl: 100000 } }` — a REAL,
     non-zero R$100,000 ceiling — and `spec/policies/autonomy/tenants-amh.yaml` overrides ONLY
     `authorization_approval` (line 6-7) and `reembolso_auto_approval` (line 8-9) to
     `max_value_brl: 0`; it does NOT touch `high_value_payment` at all. `CeilingResolver.
     within_l2_ceiling(tenant="amh", action="high_value_payment", param="threshold_brl", ...)`
     therefore resolves against a real, non-zero ceiling and CAN return `True` for values at or
     below R$100,000 — D-07 does NOT block pagto's auto-release path. No `_CEILING_D07_REASON`
     xfail is used anywhere in this file (same outcome as auth.py, whose 2 auto-approval tests
     turned out to be blocked by something else entirely, for a different reason — its own
     "CORRECTION" comment block).

  1. **THE REAL ceiling gap (genuine v2 regression, NOT D-07) — blocks
     `test_nenhum_pagamento_acima_teto_auto_libera` (`_CALCULATE_FACTS_CEILING_NOT_PROPAGATED_
     REASON`).** `route_aprovacao` (bound to `operadora.pagto.calculate_facts`,
     `pagto.py:138-211`) COMPUTES `dentro_teto_l2` via `CeilingResolver.within_l2_ceiling` — but
     its return dict is `{"faixa_valor":..., "grupo_aprovador":..., "tier_minimo":...}`
     (`pagto.py:202-211`): it never returns `dentro_teto_l2` as an output variable. Confirmed
     against `tests/unit/tools/workers/test_pagto.py`, whose own `route_aprovacao` unit tests
     never assert a `dentro_teto_l2` key in the result either. Meanwhile `BRT_AlcadaRouting`
     (`camunda:decisionRef="pagto_alcada"`, BPMN lines 180-205) is a NATIVE `businessRuleTask` —
     no `camunda:type="external"`/`camunda:topic` (confirmed by grep: only `camunda:decisionRef`
     appears at lines 126/181/254, none paired with an external-task topic) — evaluated directly
     by the engine's own DMN engine using whatever `dentro_teto_l2` process variable is ALREADY
     set. Since nothing between process start and `BRT_AlcadaRouting` ever overwrites
     `dentro_teto_l2`, the engine's routing decision uses the RAW, un-recomputed START seed —
     `route_aprovacao`'s own internal REST evaluation of `pagto_alcada` (via `evaluate_sync`) is
     REDUNDANT and its `faixa_valor`/`grupo_aprovador`/`tier_minimo` result is immediately
     overwritten by `BRT_AlcadaRouting`'s own, later `camunda:outputParameter` mapping (BPMN
     lines 195-201) before `GW_Faixa` (which reads the NESTED `${pagto_alcada.faixa_valor}` set by
     the NATIVE task, not route_aprovacao's flat vars) ever evaluates. Net effect: the
     `CeilingResolver`'s fail-closed computation inside `route_aprovacao` has NO EFFECT WHATSOEVER
     on the live-engine routing decision — a payment within the R$100k ceiling but with a
     `dentro_teto_l2=False` SEED will NOT auto-release (the DMN's `DENTRO_TETO_L2` row requires
     `dentro_teto_l2=true` LITERALLY; the row instead falls through to the conservative catch-all,
     `ANALISE_HUMANA`/`comite-financeiro`) — contradicting the donor's own documented design intent
     ("o worker calculate_facts o RECOMPUTA... e devolve o booleano COMPUTADO ao engine ANTES de
     BRT_AlcadaRouting avaliar" — donor module docstring, this suite's own opening comment). This
     is a genuine, src/**-scope defect (out of scope to fix in this PR — porting only); the sole
     concrete manifestation in the donor suite is `test_nenhum_pagamento_acima_teto_auto_libera`'s
     2 of 20 combinations (`valor=5_000_000` [R$50k, below teto] × seed `dentro_teto_l2=False` ×
     both `tipo_pagamento` values) that assert auto-release happens "independente do seed" — those
     specifically fail to reach `End_PagamentoLiberadoAutomatico`. The OTHER half of this same
     test — the L1 no-adverse-without-human invariant itself (`assert not adversos` /
     `_assert_no_adverse_without_human_task`) — is untouched by this gap and holds for every one
     of the 20 combinations (no seed/value combination can ever reach
     `End_PagamentoLiberadoHumano` automatically; that terminal is only reachable through the
     GATED `release_high_value_payment` worker, which always requires a human
     `decisao_pagamento=APROVAR` + tier-match). Per constraint 1 (byte-identical L0/L1 assertion
     strength), the FULL donor assertion body — including the auto-release-independent-of-seed
     half — is preserved verbatim below and the whole test is marked
     `xfail(strict=True)` rather than weakened.

  2. **Missing worker registrations — blocks the entire human-alcada branch
     (`_PAGTO_ALCADA_WORKER_MISSING_REASON`)** — UPDATED, t2.5-p2b-round2. The BPMN declares 8
     external-task topics via `camunda:type="external"` (grep-confirmed: `operadora.events.publish`
     ×7 call sites + `operadora.pagto.{validate_payment_data, calculate_facts,
     release_low_value_payment, prepare_approval_dossier, notify_sla_risk,
     release_high_value_payment, register_payment_refusal}`). `register_pagto_workers` originally
     implemented only 6 topics, 2 of which — `operadora.pagto.assess_admissibility` and
     `operadora.pagto.publish_completed` — have NO matching BPMN service task at all (orphan
     registrations: `BRT_PagtoAdmissibility` is a NATIVE businessRuleTask, and every
     `pagto.completed` publish routes through the generic `operadora.events.publish`, not a
     dedicated topic — harmless, simply never dispatched for this BPMN; UNCHANGED). t2.5-p2b-round2
     CLOSED 2 of the 3 originally-missing BPMN-declared topics: `operadora.pagto.notify_sla_risk`
     and `operadora.pagto.register_payment_refusal` now have real implementing functions (mirror
     the proven inadimplencia.py/cancel.py/fraude.py/adequacao.py dict-first idiom for the
     SLA-alert worker; `register_payment_refusal` mirrors `recurso.register_desistencia`'s
     dual-channel refuse-if-no-human GUARD pattern — decisao_pagamento==RECUSAR OR
     decisao_admissibilidade==DEVOLVER, both requiring aprovador_id+justificativa_recusa). Only
     `operadora.pagto.prepare_approval_dossier` remains unimplemented (Andre A2A-gated, explicitly
     out of scope for that PR). `_PAGTO_WORKER_TOPICS` below is built from what
     `register_pagto_workers` ACTUALLY registers. Consequence: `ST_PrepareApprovalDossier` STILL
     sits on the ONLY path from `BRT_PagtoSla` to `UT_AprovacaoAlcada` (BPMN: `BRT_AlcadaRouting`
     -> `GW_Faixa` -default-> `BRT_PagtoSla` -> `ST_PrepareApprovalDossier` -> `UT_AprovacaoAlcada`)
     — with no worker to claim it, EVERY test needing `UT_AprovacaoAlcada` (the entire
     value-driven-candidate-group / human-approval / tier-match / SLA-coordenacao surface — the
     process's OWN "coracao" per its BPMN documentation) STILL stalls indefinitely;
     `await_user_task`/`_await_end` time out (`EngineRestError`/a stuck `ended` set missing the
     expected terminal) — these tests keep the `_PAGTO_ALCADA_WORKER_MISSING_REASON` xfail
     (reason text updated, mark/strict unchanged). The DEVOLVER branch of
     `GW_ResolucaoAdmissibilidade` (default), UNLIKE the dossier-gated branch, is NO LONGER
     blocked by a missing worker — `ST_RegisterPaymentRefusal` does not sit downstream of
     `ST_PrepareApprovalDossier` at all — see `_PAGTO_REGISTER_REFUSAL_BUILT_REASON` (a
     HIGH-CONFIDENCE full-pass candidate, flagged for live-engine verification). Two of the
     affected donor tests (`test_coordenacao_seguir_analise_refaz_dossie_...`,
     `test_coordenacao_omite_decisao_cai_no_default_...`) ALSO assert
     `notifications_of_type("pagto.prepare_approval_dossier")`/`("pagto.notify_sla_risk")` — even
     setting the still-open dossier-registration gap aside, these would face the SAME systemic
     Kafka-publish gap as every other dict-first module (`register_pagto_workers` itself: `del
     kafka  # unused — no pagto.py worker declares a Kafka dependency`, unchanged, `notify_sla_risk`/
     `register_payment_refusal` included) — a doubly-compounded block, but the PRIMARY/first-hit
     cause for all still-`_PAGTO_ALCADA_WORKER_MISSING_REASON`-marked tests remains the
     `prepare_approval_dossier` registration gap (the process never even reaches the point where
     the second gap would matter).

  3. Kafka-publish systemic gap (cross-family fact, `grep -rn "kafka.publish(" src/maezo/tools/
     workers/*.py` = exactly ONE call site, `events.py:247`): generic-publish-topic domain events
     (`pagto.received`/`routed`/`sla_breached`/`completed`, all via `operadora.events.publish` ->
     `register_events_workers`) DO work and ARE asserted green in the 3 unmarked tests below.
     pagto.py's own registered functions (8 after t2.5-p2b-round2, was 6) are dict-first
     (ADR-0026 §2a) and NEVER call `kafka.publish` directly — matching cancel.py's
     `_WORKER_KAFKA_GAP_REASON` class; `notify_sla_risk`/`register_payment_refusal` deliberately
     mirror this SAME no-Kafka idiom (a dedicated systemic Kafka-seam task is queued, out of scope
     here). None of the 3 tests left unmarked in this file assert a direct (non-generic-publish)
     worker notification, so this gap is fully absorbed into FINDING 2 above rather than needing
     its own separate `_REASON` constant for THOSE tests. NOTE (t2.5-p2b-round2): this gap does
     NOT apply to `test_admissibilidade_devolver_registra_recusa_humana`
     (`_PAGTO_REGISTER_REFUSAL_BUILT_REASON`) either — its assertions check only the engine's
     `ended` activity-history set (via the already-fixed generic `operadora.events.publish`
     path), never a kafka-backed `notifications_of_type(...)` call — which is exactly why that
     one test is flagged as a HIGH-CONFIDENCE full-pass candidate rather than merely "still
     blocked, narrower reason" like the dossier-gated tests.

  4. Fact #4 (notification_bridge: CONTAS->RECURSO, CONTAS->FRAUDE, FRAUDE->CRED, FRAUDE->CANCEL,
     FRAUDE->INADIMPLENCIA) — N/A for pagto (neither source nor target of any of the 5 rules);
     confirmed the donor suite has zero cross-process-handoff assertions for this family.

  5. `operadora.events.publish` gap, FIXED (T3.1 R2) — `pagto_probe` registers
     `maezo.tools.workers.events.register_events_workers`, mirroring auth.py/cancel.py.

  6. Cross-process handoff seam (test_cross_process_handoff_seam.py) — OUT OF SCOPE; confirmed the
     donor pagto suite contains no such assertion (no `find_active_instances`/cross-process
     reference anywhere in the donor file) — nothing to note-and-skip here.

  7. Minor additional observation (NOT tested here — no donor test exercises this path, so no new
     test is authored for it; porting-only discipline): `PagtoError` (raised by `validate_pagto`
     for `ERR_PAGTO_ORDEM_INVALIDA` and by `release_high_value_payment` for
     `ERR_PAYMENT_RELEASE_NOT_HUMAN`) is a plain `Exception` with duck-typed `.code`/`.message` —
     NOT a `harness.WorkerBpmnError` subclass. Per `FunctionWorker.execute()`'s own docstring
     (`base.py:255-270`), such "coded" exceptions are ALWAYS reclassified into a `ValueError` ->
     `failure(retries=0)` (an incident) before the harness's `_handle` ever sees them — meaning
     the BPMN's own `BE_PagtoOrdemInvalida` boundary event (`errorRef=Error_PagtoOrdemInvalida`,
     the ONE error in this BPMN with a matching boundary — `Error_PaymentReleaseNotHuman` has NO
     boundary event anywhere, grep-confirmed) can never actually fire from pagto.py's real code
     path: an invalid `ordem_pagamento_id` always becomes an open incident, never a clean
     `End_PagtoOrdemInvalida` termination. Independent of findings 1/2 above.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.pagto import register_pagto_workers

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_DMN_ADMISSIBILITY = _REPO / "spec/processes/dmn/pagto_admissibility.dmn"
_DMN_ALCADA = _REPO / "spec/processes/dmn/pagto_alcada.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/pagto_sla.dmn"

# External task topics do contrato SP-OP-PAGTO-001 (§Topicos) — cross-checked against
# `camunda:topic` grep on the BPMN (module docstring FINDING 2).
_PUBLISH_TOPIC = "operadora.events.publish"
_VALIDATE_TOPIC = "operadora.pagto.validate_payment_data"
_ASSESS_ADMISSIBILITY_TOPIC = "operadora.pagto.assess_admissibility"
_CALCULATE_FACTS_TOPIC = "operadora.pagto.calculate_facts"
_RELEASE_LOW_TOPIC = "operadora.pagto.release_low_value_payment"
_RELEASE_HIGH_TOPIC = "operadora.pagto.release_high_value_payment"
_NOTIFY_SLA_TOPIC = "operadora.pagto.notify_sla_risk"
_REGISTER_REFUSAL_TOPIC = "operadora.pagto.register_payment_refusal"
_PUBLISH_COMPLETED_TOPIC = "operadora.pagto.publish_completed"

# Topicos servidos pelos workers REAIS registrados no harness (drain generico) — construido a
# partir do que `register_pagto_workers` REALMENTE registra, NAO do que o BPMN declara (module
# docstring FINDING 2 documents the mismatch in both directions). UPDATED t2.5-p2b-round2:
#   - `_ASSESS_ADMISSIBILITY_TOPIC`/`_PUBLISH_COMPLETED_TOPIC` are registered but have NO matching
#     BPMN service task (orphan — harmless, simply never dispatched for this BPMN; unchanged).
#   - `_NOTIFY_SLA_TOPIC`/`_REGISTER_REFUSAL_TOPIC` are NOW registered (t2.5-p2b-round2 closed
#     these 2 of the 3 originally-missing topics) — INCLUDED in this list, so the drain genuinely
#     fetches/completes both external tasks now.
#   - `operadora.pagto.prepare_approval_dossier` is STILL declared by the BPMN with NO registered
#     worker (Andre A2A-gated, explicitly out of scope for t2.5-p2b-round2) — deliberately NOT in
#     this list: subscribing to a topic nothing handles would make `harness._handle` report an
#     immediate "no handler registered" incident the moment the engine dispatches it; omitting it
#     instead leaves the task simply unclaimed (the instance stalls cleanly at that service task,
#     which is exactly the behavior FINDING 2's still-xfailed tests below observe/expect).
_PAGTO_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _VALIDATE_TOPIC,
    _ASSESS_ADMISSIBILITY_TOPIC,
    _CALCULATE_FACTS_TOPIC,
    _RELEASE_LOW_TOPIC,
    _RELEASE_HIGH_TOPIC,
    _NOTIFY_SLA_TOPIC,
    _REGISTER_REFUSAL_TOPIC,
    _PUBLISH_COMPLETED_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato §Topicos).
_PAGTO_RECEIVED = "agents.events.pagto.received"
_PAGTO_ROUTED = "agents.events.pagto.routed"
_PAGTO_SLA_BREACHED = "agents.events.pagto.sla_breached"
_PAGTO_COMPLETED = "agents.events.pagto.completed"

# User Task definition keys (BPMN).
_UT_APROVACAO = "UT_AprovacaoAlcada"
_UT_COORDENACAO = "UT_CoordenacaoAlcada"
_UT_ANALISE_ADMISS = "UT_AnaliseAdmissibilidade"

# Business rule task activity ids (gate de admissibilidade precede a escada de alcada).
_BRT_ADMISSIBILITY = "BRT_PagtoAdmissibility"
_BRT_ALCADA = "BRT_AlcadaRouting"

# Decision-definition keys avaliadas pelos business rule tasks (ADR-0012).
_DEC_ADMISSIBILITY = "pagto_admissibility"
_DEC_ALCADA = "pagto_alcada"

# End events do processo.
_END_LIBERADO_HUMANO = "End_PagamentoLiberadoHumano"
_END_LIBERADO_AUTOMATICO = "End_PagamentoLiberadoAutomatico"
_END_RECUSADO_HUMANO = "End_PagamentoRecusadoHumano"
_END_RISCO_SLA = "End_RiscoSlaNotificado"

# Boundary timers (em UT_AprovacaoAlcada).
_BT_ALERTA = "BT_AlertaSlaPagto"
_BT_SLA_APROVACAO = "BT_SlaAprovacao"

# User Tasks humanas que podem produzir o desfecho adverso (L1 hard guard).
_UT_HUMANAS = frozenset({_UT_APROVACAO, _UT_COORDENACAO})

# O terminal ADVERSO (so via UT humana + tier-match — high_value_payment L1 hard).
_ENDS_ADVERSOS = frozenset({_END_LIBERADO_HUMANO})

# Valor de decisao humana que autoriza o efeito adverso (setado SO na User Task — L1 hard).
_DECISAO_APROVAR = "APROVAR"

# Teto L2 de auto-liberacao: high_value_payment.threshold_brl=100000 (L0-core.yaml, VERIFIED
# non-zero — module docstring FINDING 0) = 10_000_000 centavos.
_TETO_L2_CENTS = 10_000_000

# Campos obrigatorios de uma liberacao humana (APROVAR — defesa em profundidade do worker).
_CAMPOS_APROVACAO_BASE: dict[str, Any] = {
    "decisao_pagamento": _DECISAO_APROVAR,
    "justificativa_aprovacao": "Fundamentacao sintetica da liberacao de alto valor (teste L1)",
    "valor_aprovado_cents": 120_000_000,
    "aprovador_id": "aprovador-financeiro-sintetico-002",
    "aprovador_tier": 2,
}

# FINDING 1 (module docstring): route_aprovacao (operadora.pagto.calculate_facts) never returns
# `dentro_teto_l2` as an output variable, so BRT_AlcadaRouting (a NATIVE businessRuleTask) always
# evaluates pagto_alcada against the RAW, un-recomputed seed — the CeilingResolver's fail-closed
# computation never reaches the live routing decision. Blocks ONLY
# test_nenhum_pagamento_acima_teto_auto_libera's "auto-release is independent of the seed" half.
_CALCULATE_FACTS_CEILING_NOT_PROPAGATED_REASON = (
    "v2 regression (module docstring FINDING 1, NOT D-07 — that gap does not apply to pagto's "
    "high_value_payment ceiling, verified non-zero R$100,000 in both L0-core.yaml and "
    "tenants-amh.yaml): route_aprovacao (pagto.py:138-211, topic operadora.pagto.calculate_facts) "
    "computes dentro_teto_l2 via CeilingResolver.within_l2_ceiling but never returns it as an "
    "output variable, unlike the donor's calculate_facts worker (which recomputes AND writes it "
    "back before BRT_AlcadaRouting evaluates, per this suite's own module-docstring quote of the "
    "donor's design intent). BRT_AlcadaRouting (camunda:decisionRef='pagto_alcada', BPMN lines "
    "180-205) is a NATIVE businessRuleTask (no camunda:type=external/topic) that therefore reads "
    "whatever dentro_teto_l2 process variable is ALREADY set — i.e. the raw START seed, since "
    "nothing else touches it — so a payment within the R$100k ceiling with seed "
    "dentro_teto_l2=False fails to auto-release (falls to the ANALISE_HUMANA/comite-financeiro "
    "catch-all instead), contradicting the donor's assertion that auto-release is a function of "
    "valor alone. The L1 no-adverse-without-human half of this same test (no seed/value "
    "combination ever reaches End_PagamentoLiberadoHumano automatically) is untouched by this gap "
    "and holds for all 20 combinations; only the auto-release-independent-of-seed assertions for "
    "valor<=teto + seed=False break. src/** fix is out of scope for this port."
)

# FINDING 2 (module docstring): register_pagto_workers implements only 6 of the 8 BPMN-declared
# operadora.pagto.* external-task topics — operadora.pagto.{prepare_approval_dossier,
# notify_sla_risk, register_payment_refusal} have NO implementing worker (confirmed by
# register_pagto_workers' own trailing comment, pagto.py:376-377). ST_PrepareApprovalDossier sits
# on the ONLY path from BRT_PagtoSla to UT_AprovacaoAlcada, so every test needing that User Task
# (the process's entire value-driven-candidate-group/human-approval/tier-match/SLA-coordenacao
# surface) stalls indefinitely; the DEVOLVER admissibility branch similarly stalls at
# ST_RegisterPaymentRefusal.
_PAGTO_ALCADA_WORKER_MISSING_REASON = (
    "v2 registration gap (module docstring FINDING 2), UPDATED t2.5-p2b-round2: the BPMN "
    "declares operadora.pagto.{prepare_approval_dossier,notify_sla_risk,register_payment_"
    "refusal} as external-task topics (camunda:type='external'); register_pagto_workers now "
    "implements 2 of these 3 (notify_sla_risk, register_payment_refusal — t2.5-p2b-round2). Only "
    "`prepare_approval_dossier` remains unimplemented (Andre A2A-gated, explicitly out of scope "
    "for that PR). ST_PrepareApprovalDossier is the ONLY path from BRT_PagtoSla to "
    "UT_AprovacaoAlcada (BPMN: BRT_AlcadaRouting -> GW_Faixa -default-> BRT_PagtoSla -> "
    "ST_PrepareApprovalDossier -> UT_AprovacaoAlcada) — with no worker to claim it, the external "
    "task sits unclaimed forever and this test's await_user_task/_await_end call times out "
    "(EngineRestError) or observes a stuck `ended` set missing the expected terminal, since "
    "_PAGTO_WORKER_TOPICS deliberately does not subscribe to a topic with no registered handler "
    "(see its own comment). This STILL blocks every test needing UT_AprovacaoAlcada (the entire "
    "value-driven-candidate-group / human-approval / tier-match / SLA-coordenacao surface). The "
    "GW_ResolucaoAdmissibilidade DEVOLVER default branch (ST_RegisterPaymentRefusal) is NO "
    "LONGER blocked by a missing worker (t2.5-p2b-round2 registered register_payment_refusal, "
    "which does not sit downstream of ST_PrepareApprovalDossier at all) — see "
    "`_PAGTO_REGISTER_REFUSAL_BUILT_REASON` for the ONE test on that branch, which now has a "
    "narrower/different (or possibly no remaining) blocker. src/** fix (implementing "
    "prepare_approval_dossier) is out of scope for this PR."
)

# t2.5-p2b-round2: register_payment_refusal is now BUILT and registered. Unlike every OTHER
# _PAGTO_ALCADA_WORKER_MISSING_REASON-marked test, `test_admissibilidade_devolver_registra_
# recusa_humana` reaches ST_RegisterPaymentRefusal via the ADMISSIBILIDADE/DEVOLVER channel
# (UT_AnaliseAdmissibilidade -> GW_ResolucaoAdmissibilidade -> ST_RegisterPaymentRefusal), which
# is NOT downstream of the still-missing ST_PrepareApprovalDossier/UT_AprovacaoAlcada at all --
# it is reachable directly from GW_Admissibilidade without ever touching the dossier gate. None
# of this test's assertions touch a kafka-backed notification (`_ENDS_ADVERSOS`/`ended`-set
# checks only, via the ALREADY-FIXED generic operadora.events.publish path) -- so, unlike every
# other xfail in this file, this one has NO KNOWN remaining src-side blocker after
# t2.5-p2b-round2. HIGH-CONFIDENCE full-pass candidate: flagged prominently in the round-2 report
# for a human to verify live and then REMOVE this xfail (a strict=True xfail that unexpectedly
# passes is itself a hard failure -- XPASS(strict) -- so leaving this mark in place is a
# deliberate, policy-mandated choice pending that live-proof, not an oversight).
_PAGTO_REGISTER_REFUSAL_BUILT_REASON = (
    "built, pending live-proof flip (t2.5-p2b-round2): pagto.py's `register_payment_refusal` is "
    "now implemented and registered on `operadora.pagto.register_payment_refusal` (dual human "
    "channel: decisao_pagamento==RECUSAR OR decisao_admissibilidade==DEVOLVER, both requiring "
    "aprovador_id+justificativa_recusa -- refuses ERR_PAYMENT_REFUSAL_NOT_HUMAN otherwise, "
    "mirrors recurso.register_desistencia's refuse-if-no-human pattern). This test's DEVOLVER "
    "path (UT_AnaliseAdmissibilidade -> GW_ResolucaoAdmissibilidade -> ST_RegisterPaymentRefusal) "
    "does NOT depend on ST_PrepareApprovalDossier/UT_AprovacaoAlcada (the still-open "
    "prepare_approval_dossier gap) at all, and none of its assertions touch a kafka-backed "
    "notification -- structurally, this test has NO remaining known blocker. Kept as strict "
    "xfail per policy (mark/strict unchanged pending live-engine proof, NOT flipped here) -- "
    "if it genuinely passes against a live engine, REMOVE this xfail (an unexpected XPASS under "
    "strict=True is itself a CI failure)."
)


# ---------------------------------------------------------------------------
# EngineProbe para pagto (espelha AuthEngineProbe / CancelEngineProbe)
# ---------------------------------------------------------------------------


@dataclass
class PagtoEngineProbe:
    """Driva os workers reais de pagto contra o engine CIB Seven."""

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
        await drain_topics(self.transport, self.harness, self.worker_id, _PAGTO_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + as 3 DMN de processo (pagto_admissibility + pagto_alcada + pagto_sla)."""
    return await engine.deploy(_BPMN, _DMN_ADMISSIBILITY, _DMN_ALCADA, _DMN_SLA, name="SP-OP-PAGTO-001-qa")


@pytest_asyncio.fixture
async def pagto_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[PagtoEngineProbe]:
    """Probe que serve as external tasks com os workers reais de pagto."""
    worker_id = f"qa-pagto-worker-{uuid.uuid4().hex[:8]}"
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
    # ADR-0028 T1.5 seam (module docstring PORT NOTES): route_aprovacao/assess_admissibility
    # require a real dmn transport or every dispatch raises DmnEvaluationError. Mirrors
    # production wiring exactly (worker_runtime/service.py:278).
    dmn_transport = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    register_pagto_workers(harness, kafka, dmn=dmn_transport)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in this
    # BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (t2.5-p2b-round2, mirrors the #93 inadimplencia template / fraude.py's/
    # adequacao.py's fixtures): todo topico operadora.pagto.* registrado no harness DEVE estar na
    # lista de drain — falha AQUI, explicita, se um worker novo ficar fora.
    pagto_registered = {t for t in harness.registered_topics if t.startswith("operadora.pagto.")}
    missing_from_drain = pagto_registered - set(_PAGTO_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_PAGTO_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = PagtoEngineProbe(
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


def _unique_ordem(prefix: str = "ORDEM-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_pagto(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key PAGTO-amh-{ordem} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (ORDEM-TESTE-NNNN, tenant amh, ponteiros
    pseudonimizados; valores em centavos int64/long — NUNCA number). Os FATOS DE ROTEAMENTO
    (valor_pagamento_cents, dentro_teto_l2) sao seeded como variaveis de start (o BPMN documenta
    ambos como "pre-resolvido"; ver module docstring FINDING 1 sobre como dentro_teto_l2
    efetivamente permanece o seed cru na avaliacao NATIVA de BRT_AlcadaRouting).

    Default = valor R$ 1.200.000 (acima do teto L2) -> faixa ALCADA_L2 -> UT humana.
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        ordem = overrides.pop("ordem_pagamento_id", _unique_ordem())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "ordem_pagamento_id": ordem,
            "prestador_id": "prov:teste-0001",
            "tipo_pagamento": "prestador_rede",
            "valor_pagamento_cents": 120_000_000,
            "moeda": "BRL",
            "competencia": "2026-05",
            "data_vencimento": "2026-06-30",
            "conta_origem_ref": "conta:teste-origem",
            "instrumento_pagamento": "ted",
            "dados_pagamento_validos": True,
            "lastro_confirmado": True,
            "dentro_teto_l2": False,
            "duplicidade_suspeita": False,
        }
        variables.update(overrides)
        business_key = f"PAGTO-amh-{ordem}"
        return await engine.start_by_key("SP-OP-PAGTO-001", business_key, variables)

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
    """INVARIANTE L1 (high_value_payment / no-adverse): prova que o terminal adverso so existe com
    UT humana.

    Consulta a historia do engine: se End_PagamentoLiberadoHumano esta no historico, existe pelo
    menos uma User Task humana (_UT_HUMANAS) tambem no historico. Prova negativa: se o terminal
    adverso nao esta no historico -> ok por vacuidade. Valor alto NUNCA auto-libera — so roteia
    ao humano.
    """
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE L1 VIOLADA (high_value_payment): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas: {ended}. Isto indica auto-liberacao de alto valor — violacao L1."
        )


async def _drive_to_aprovacao(engine: EngineRest, probe: PagtoEngineProbe, iid: str) -> Any:
    """Drena ate UT_AprovacaoAlcada surgir (dossie montado pelos workers reais)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_APROVACAO)


def _aprovacao_vars(**extra: Any) -> dict[str, Any]:
    """Monta as variaveis de uma liberacao humana completa (APROVAR), com tier compativel."""
    vars_ = dict(_CAMPOS_APROVACAO_BASE)
    vars_.update(extra)
    return vars_


async def _drive_to_coordenacao(engine: EngineRest, probe: PagtoEngineProbe, iid: str) -> Any:
    """Drena ate UT_AprovacaoAlcada, dispara BT_SlaAprovacao (job execution, NUNCA sleep) e retorna
    a UT_CoordenacaoAlcada (coordenacao-financeira) criada apos o estouro de SLA."""
    await _drive_to_aprovacao(engine, probe, iid)
    job = await engine.await_timer_job(iid, _BT_SLA_APROVACAO)
    await engine.execute_job(job.id)
    await probe.drain()
    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-financeira" in ut_coord.candidate_groups
    return ut_coord


# ===========================================================================
# INVARIANTE L1 — DoD deliverable (varredura de inputs da DMN pagto_alcada)
# ===========================================================================


@pytest.mark.xfail(reason=_CALCULATE_FACTS_CEILING_NOT_PROPAGATED_REASON, strict=True)
async def test_nenhum_pagamento_acima_teto_auto_libera(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """INVARIANTE L1 (high_value_payment): NENHUM pagamento acima do teto auto-libera.

    Varredura de combinacoes de input da DMN pagto_alcada:
      valor_pagamento_cents in {5MM cents (R$ 50k, abaixo teto), 30MM (R$ 300k, L1), 120MM (R$ 1.2MM,
        L2), 500MM (R$ 5MM, L3), 2_000MM (R$ 20MM, acima do maior tier -> comite)}
      x seed de dentro_teto_l2 in {True, False}   (INERTE — provamos que o seed nao influencia)
      x tipo_pagamento in {prestador_rede, reembolso_beneficiario}
    (5 * 2 * 2 = 20 combinacoes).

    CEILING (GAP-PAGTO-3, donor intent): dentro_teto_l2 e RECOMPUTADO pelo worker calculate_facts
    (valor <= high_value_payment.threshold_brl) e devolvido ao engine antes de BRT_AlcadaRouting.
    O roteamento e funcao SO do valor COMPUTADO. v2 REGRESSION (module docstring FINDING 1): o
    worker v2 nunca devolve dentro_teto_l2 — BRT_AlcadaRouting (NATIVO) le o seed cru. As
    assercoes abaixo permanecem BYTE-IDENTICAS ao donor (constraint 1); o xfail documenta ONDE
    exatamente a segunda metade quebra (2 das 20 combinacoes).
    """
    valores = [
        _TETO_L2_CENTS // 2,  # R$ 50k — abaixo do teto
        30_000_000,  # R$ 300k — ALCADA_L1
        120_000_000,  # R$ 1.2MM — ALCADA_L2
        500_000_000,  # R$ 5MM — ALCADA_L3
        2_000_000_000,  # R$ 20MM — acima do maior tier -> comite (ANALISE_HUMANA)
    ]
    tetos = [True, False]
    tipos = ["prestador_rede", "reembolso_beneficiario"]
    checked = 0

    for valor, dentro_teto, tipo in itertools.product(valores, tetos, tipos):
        inst = await start_pagto(
            valor_pagamento_cents=valor,
            dentro_teto_l2=dentro_teto,
            tipo_pagamento=tipo,
        )
        iid = inst["id"]
        await pagto_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        # NENHUMA combinacao pode atingir o terminal adverso sem decisao humana.
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L1 VIOLADO (high_value_payment): valor={valor} seed_dentro_teto={dentro_teto} "
            f"tipo={tipo} atingiu terminal(is) adverso(s) {adversos} automaticamente. ended={ended}"
        )
        # Auto-liberacao clerical e funcao SO do valor COMPUTADO (valor <= teto), independente do
        # seed: acima do teto NUNCA auto-libera (nem com seed true — o worker sobrescreve); abaixo
        # do teto auto-libera (nem o seed false segura — prova de que o booleano e recomputado).
        if valor <= _TETO_L2_CENTS:
            assert _END_LIBERADO_AUTOMATICO in ended, (
                f"valor={valor} <= teto deve auto-liberar (clerical) INDEPENDENTE do seed "
                f"dentro_teto_l2={dentro_teto} (recomputado pelo worker). ended={ended}"
            )
        else:
            assert _END_LIBERADO_AUTOMATICO not in ended, (
                f"CEILING VIOLADO: valor={valor} > teto auto-liberou com seed "
                f"dentro_teto_l2={dentro_teto} — o seed furou o teto (recompute quebrado). ended={ended}"
            )
        # Invariante de historia (prova formal).
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 20, f"Esperava 20 combinacoes varridas; varri {checked}"


# ===========================================================================
# GAP-PAGTO-1 — gate de admissibilidade (pagto_admissibility) PRECEDE a escada de alcada
# ===========================================================================


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_gate_admissibilidade_precede_alcada_no_happy_path(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """O gate pagto_admissibility roda ANTES de pagto_alcada (ADR-0012 / GAP-PAGTO-1).

    Ordem seed valida (dados validos, lastro confirmado, sem duplicidade) -> admissivel
    (SEGUE_ROTEAMENTO): AMBOS os business rule tasks executam, o de admissibilidade ANTES do de
    alcada (prova pela historia de decision-instance do engine), e o fluxo segue normalmente ate a
    UT de aprovacao value-driven.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    assert "aprovacao-financeira-l2" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _BRT_ADMISSIBILITY in ended, "o gate de admissibilidade deve ter executado"
    assert _BRT_ALCADA in ended, "a escada de alcada deve ter executado no caminho admissivel"

    # Prova de ORDEM via historia de decision-instance: admissibilidade avaliada antes da alcada.
    decisions = await engine.history_decision_instances(instance_id=iid)
    by_key: dict[str, str] = {}
    for d in decisions:
        key = str(d.get("decisionDefinitionKey", ""))
        # Guarda a 1a (mais antiga) avaliacao de cada decisao.
        by_key.setdefault(key, str(d.get("evaluationTime", "")))
    assert _DEC_ADMISSIBILITY in by_key, "pagto_admissibility deve constar na historia de decisoes"
    assert _DEC_ALCADA in by_key, "pagto_alcada deve constar na historia de decisoes (caminho admissivel)"
    assert by_key[_DEC_ADMISSIBILITY] <= by_key[_DEC_ALCADA], (
        f"gate de admissibilidade deve avaliar ANTES da alcada; "
        f"admissibility={by_key[_DEC_ADMISSIBILITY]} alcada={by_key[_DEC_ALCADA]}"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_duplicidade_suspeita_roteia_humano_nunca_alcada(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """duplicidade_suspeita=true -> analise humana; NUNCA alcanca a escada de alcada nem auto-libera.

    O gate bloqueia o roteamento: a instancia cria UT_AnaliseAdmissibilidade (coordenacao-financeira),
    NAO executa BRT_AlcadaRouting, NAO publica pagto.routed e nao atinge nenhum terminal adverso/auto.
    """
    inst = await start_pagto(
        valor_pagamento_cents=120_000_000, dentro_teto_l2=False, duplicidade_suspeita=True
    )
    iid = inst["id"]

    await pagto_probe.drain()
    ut = await engine.await_user_task(iid, _UT_ANALISE_ADMISS)
    assert "coordenacao-financeira" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _BRT_ADMISSIBILITY in ended, "o gate de admissibilidade deve ter executado"
    assert _BRT_ALCADA not in ended, "duplicidade suspeita NAO pode alcancar a escada de alcada (gate)"
    assert not (ended & _ENDS_ADVERSOS), "duplicidade suspeita nunca produz terminal adverso automatico"
    assert _END_LIBERADO_AUTOMATICO not in ended, "duplicidade suspeita NUNCA auto-libera (clerical)"
    assert not pagto_probe.events_on(_PAGTO_ROUTED), "sem roteamento de alcada quando nao admissivel"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_lastro_nao_confirmado_nunca_auto_libera_clerical(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """lastro_confirmado=false NUNCA auto-libera — nem pelo caminho clerical L2 (o gate bloqueia).

    Prova-mestra do guardrail: uma ordem ABAIXO do teto com dentro_teto_l2=true AUTO-LIBERARIA pelo
    caminho clerical se chegasse a escada de alcada (DENTRO_TETO_L2 -> ST_ReleaseLowValue). Como o
    lastro nao esta confirmado, o gate de admissibilidade a desvia para analise humana: NAO atinge
    End_PagamentoLiberadoAutomatico, NAO executa BRT_AlcadaRouting, cria UT_AnaliseAdmissibilidade.
    """
    inst = await start_pagto(valor_pagamento_cents=85_000, dentro_teto_l2=True, lastro_confirmado=False)
    iid = inst["id"]

    await pagto_probe.drain()
    ut = await engine.await_user_task(iid, _UT_ANALISE_ADMISS)
    assert "coordenacao-financeira" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_LIBERADO_AUTOMATICO not in ended, (
        "lastro nao confirmado NUNCA auto-libera pelo caminho clerical (gate de admissibilidade)"
    )
    assert _BRT_ALCADA not in ended, "sem lastro confirmado NAO pode alcancar a escada de alcada"
    assert "ST_ReleaseLowValue" not in ended, "release_low_value_payment NUNCA invocado sem lastro"
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_admissibilidade_devolver_registra_recusa_humana(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Humano decide DEVOLVER na analise de admissibilidade -> devolucao para revisao (nunca libera).

    O default conservador do gateway de resolucao (DEVOLVER) reusa o terminal de recusa humana
    (register_payment_refusal -> End_PagamentoRecusadoHumano). Nenhuma liberacao; nenhum terminal
    adverso automatico. t2.5-p2b-round2: register_payment_refusal is NOW built/registered on this
    topic -- see `_PAGTO_REGISTER_REFUSAL_BUILT_REASON` (HIGH-CONFIDENCE full-pass candidate,
    live-proof needed).
    """
    inst = await start_pagto(
        valor_pagamento_cents=120_000_000, dentro_teto_l2=False, duplicidade_suspeita=True
    )
    iid = inst["id"]

    await pagto_probe.drain()
    ut = await engine.await_user_task(iid, _UT_ANALISE_ADMISS)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_admissibilidade": "DEVOLVER",
            "justificativa_recusa": "Duplicidade confirmada pelo analista (teste) — devolver para revisao",
            "aprovador_id": "analista-financeiro-sintetico-003",
        },
    )
    await pagto_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_RECUSADO_HUMANO in ended, f"DEVOLVER => End_PagamentoRecusadoHumano. ended={ended}"
    assert _BRT_ALCADA not in ended, "DEVOLVER nunca alcanca a escada de alcada"
    assert not (ended & _ENDS_ADVERSOS), "DEVOLVER nunca produz terminal adverso"
    assert "ST_ReleaseHighValue" not in ended, "DEVOLVER nunca libera pagamento"


# ===========================================================================
# Value-driven candidate group routing (coracao do processo)
# ===========================================================================


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
@pytest.mark.parametrize(
    ("valor", "grupo_esperado", "faixa_esperada"),
    [
        (30_000_000, "aprovacao-financeira-l1", "ALCADA_L1"),
        (120_000_000, "aprovacao-financeira-l2", "ALCADA_L2"),
        (500_000_000, "aprovacao-financeira-l3", "ALCADA_L3"),
        (2_000_000_000, "comite-financeiro", "ANALISE_HUMANA"),
    ],
)
async def test_valor_dirige_candidate_group(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
    valor: int,
    grupo_esperado: str,
    faixa_esperada: str,
) -> None:
    """O valor do pagamento dirige o candidateGroups da User Task (binding value-driven, unico).

    A DMN pagto_alcada classifica a faixa e emite grupo_aprovador; a UT_AprovacaoAlcada o consome via
    camunda:candidateGroups="${pagto_alcada.grupo_aprovador}". Cada faixa de valor roteia para o grupo
    de tier correspondente; o catch-all conservador (acima do maior tier) -> comite-financeiro.
    """
    inst = await start_pagto(valor_pagamento_cents=valor, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    assert grupo_esperado in ut.candidate_groups, (
        f"valor={valor} deve rotear para candidateGroup {grupo_esperado}; "
        f"candidate_groups={ut.candidate_groups}"
    )
    # pagto.routed publicado carregando faixa_valor + grupo_aprovador (sem valor cru obrigatorio).
    assert pagto_probe.has_event(_PAGTO_ROUTED, faixa_valor=faixa_esperada, grupo_aprovador=grupo_esperado)

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Roteamento nunca produz terminal adverso (so cria a UT)"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_catchall_conservador_acima_do_maior_tier(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Valor acima do maior tier configurado -> catch-all conservador -> comite-financeiro.

    Defesa do risco #5 do phase3-plan: pagamento acima do maior tier NUNCA auto-libera; sobe ao
    comite (tier mais alto). A UT existe com candidateGroups=comite-financeiro; nenhum terminal adverso
    automatico.
    """
    inst = await start_pagto(valor_pagamento_cents=5_000_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    assert "comite-financeiro" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Caminho clerical L2 (abaixo do teto) — auto-liberacao neutra
# ===========================================================================


async def test_abaixo_do_teto_auto_libera_clerical(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Pagamento abaixo do teto L2 (valor <= teto E dentro_teto_l2=true) auto-libera (clerical L2).

    End_PagamentoLiberadoAutomatico (neutro); pagto.completed desfecho=liberado_automatico. Nenhuma
    User Task adversa; release_high_value_payment NUNCA invocado. Analogo a auth_auto_approval.
    NAO bloqueado por FINDING 1: aqui o seed dentro_teto_l2=true JA e o valor que BRT_AlcadaRouting
    (nativo) precisa ler cru — o gap so importa quando o seed diverge do valor computado.
    """
    inst = await start_pagto(valor_pagamento_cents=85_000, dentro_teto_l2=True)
    iid = inst["id"]

    await pagto_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_LIBERADO_AUTOMATICO in ended, f"DENTRO_TETO_L2 => auto-liberacao clerical. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS), "Caminho clerical L2 nunca atinge o terminal adverso de alcada"
    assert "ST_ReleaseHighValue" not in ended, "DENTRO_TETO_L2 nunca invoca release_high_value_payment"
    assert pagto_probe.has_event(_PAGTO_COMPLETED, desfecho="liberado_automatico")


# ===========================================================================
# Happy paths (decisao humana em UT_AprovacaoAlcada)
# ===========================================================================


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_happy_path_aprovar_libera_humano(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Aprovador completa APROVAR (com todos os campos + tier compativel) => liberacao humana.

    release_high_value_payment executado (guard satisfeito, tier-match ok); End_PagamentoLiberadoHumano;
    pagto.completed desfecho=liberado_humano carregando aprovador_id+tier na trilha de auditoria.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    assert "aprovacao-financeira-l2" in ut.candidate_groups
    # Aprovador de tier 2 (compativel com ALCADA_L2).
    await engine.complete_task_as_human(ut.id, _aprovacao_vars())
    await pagto_probe.drain()

    ended = await _await_end(engine, iid)
    # PROVA: End_PagamentoLiberadoHumano so existe porque a UT humana foi completada com APROVAR + tier.
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_LIBERADO_HUMANO in ended, f"Aprovacao humana => End_PagamentoLiberadoHumano. ended={ended}"
    assert "ST_ReleaseHighValue" in ended, "release_high_value_payment deve ter executado"
    assert pagto_probe.has_event(
        _PAGTO_COMPLETED,
        desfecho="liberado_humano",
        aprovador_id="aprovador-financeiro-sintetico-002",
        aprovador_tier=2,
    ), "pagto.completed adverso deve carregar aprovador_id+tier (trilha de auditoria ADR-0007)"


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_happy_path_recusar_humano(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Aprovador completa RECUSAR => recusa/devolucao para revisao (neutro; NAO e glosa).

    register_payment_refusal executado; End_PagamentoRecusadoHumano; pagto.completed
    desfecho=recusado_humano. release_high_value_payment NUNCA invocado.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_pagamento": "RECUSAR",
            "justificativa_recusa": "Lastro inconsistente (teste) — devolver para revisao",
            "aprovador_id": "aprovador-financeiro-sintetico-002",
        },
    )
    await pagto_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_RECUSADO_HUMANO in ended, f"RECUSAR => End_PagamentoRecusadoHumano. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert "ST_ReleaseHighValue" not in ended, "RECUSAR nunca libera o pagamento"
    assert pagto_probe.has_event(_PAGTO_COMPLETED, desfecho="recusado_humano")


# ===========================================================================
# Tier-match guard (a verificacao EXTRA — exclusiva deste processo)
# ===========================================================================


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_tier_insuficiente_nao_libera(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Aprovador de tier insuficiente para a faixa => guard recusa (ERR_PAYMENT_RELEASE_NOT_HUMAN).

    Mesmo que a UT seja completada com APROVAR + campos completos, se aprovador_tier < tier minimo da
    faixa (ex.: aprovador tier 1 sobre faixa ALCADA_L3), o worker release_high_value_payment recusa
    liberar (defesa em profundidade alem do roteamento). A instancia NAO atinge
    End_PagamentoLiberadoHumano.
    """
    inst = await start_pagto(valor_pagamento_cents=500_000_000, dentro_teto_l2=False)  # ALCADA_L3
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    assert "aprovacao-financeira-l3" in ut.candidate_groups
    # Aprovador de tier 1 (insuficiente para ALCADA_L3 — tier minimo 3).
    await engine.complete_task_as_human(ut.id, _aprovacao_vars(aprovador_tier=1))
    await pagto_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_LIBERADO_HUMANO not in ended, (
        "Liberacao com tier insuficiente NAO pode atingir End_PagamentoLiberadoHumano (tier-match guard)"
    )
    assert not pagto_probe.has_event(_PAGTO_COMPLETED, desfecho="liberado_humano"), (
        "pagto.completed adverso NAO deve publicar com tier insuficiente"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_aprovar_exige_campos(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """APROVAR sem justificativa/valor/aprovador/tier => guard recusa (defesa em profundidade).

    O engine despacha release_high_value_payment; o worker lanca ERR_PAYMENT_RELEASE_NOT_HUMAN; a
    instancia NAO atinge End_PagamentoLiberadoHumano.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut = await _drive_to_aprovacao(engine, pagto_probe, iid)
    # Completa com APROVAR mas SEM os campos obrigatorios.
    await engine.complete_task_as_human(ut.id, {"decisao_pagamento": _DECISAO_APROVAR})
    await pagto_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_LIBERADO_HUMANO not in ended, (
        "Liberacao sem campos obrigatorios NAO pode atingir End_PagamentoLiberadoHumano (guard do worker)"
    )
    assert not pagto_probe.has_event(_PAGTO_COMPLETED, desfecho="liberado_humano")
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# SLA estourado -> coordenacao humana assume (NUNCA auto-libera por timeout)
# ===========================================================================


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_sla_estourado_coordenacao_assume_nunca_auto_libera(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """SLA de aprovacao estourado => coordenacao financeira humana assume (NUNCA auto-libera).

    O timer interruptivo BT_SlaAprovacao (disparado via job execution, NUNCA sleep) publica
    pagto.sla_breached e cria UT_CoordenacaoAlcada (coordenacao-financeira). A aprovacao continua
    HUMANA e tier-adequada — nenhum desfecho adverso automatico. Inverte o anti-pattern
    Task_AutoApprove/timeout. GAP-PAGTO-5: a coordenacao decide `decisao_coordenacao=assumir_aprovacao`
    (campo DISTINTO de `decisao_pagamento`) para tomar a decisao de merito AGORA — roteado por
    GW_DecisaoCoordenacao a GW_DecisaoPagamento (mesmos campos de UT_AprovacaoAlcada).
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut_coord = await _drive_to_coordenacao(engine, pagto_probe, iid)

    # pagto.sla_breached publicado; UT_CoordenacaoAlcada (coordenacao-financeira) criada.
    assert pagto_probe.has_event(_PAGTO_SLA_BREACHED), "SLA estourado deve publicar pagto.sla_breached"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA NUNCA auto-libera (coordenacao humana assume)"
    await _assert_no_adverse_without_human_task(engine, iid)

    # A coordenacao humana (tier compativel) ASSUME a decisao agora (assumir_aprovacao) e decide
    # APROVAR -> liberacao continua HUMANA.
    await engine.complete_task_as_human(ut_coord.id, _aprovacao_vars(decisao_coordenacao="assumir_aprovacao"))
    await pagto_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_LIBERADO_HUMANO in ended, (
        f"Coordenacao humana assumir_aprovacao+APROVAR => End_PagamentoLiberadoHumano. ended={ended}"
    )


# ---------------------------------------------------------------------------
# GAP-PAGTO-5: GW_DecisaoCoordenacao roteia decisao_coordenacao (distinto de decisao_pagamento).
#
# UT_CoordenacaoAlcada (estouro de SLA) decide decisao_coordenacao ademais de decisao_pagamento.
# GW_DecisaoCoordenacao (logo apos a UT) roteia cada valor de forma DISTINTA:
#   assumir_aprovacao -> GW_DecisaoPagamento (decisao_pagamento, preenchida na mesma UT, roteia
#                         normalmente — inclusive um eventual APROVAR, sempre humano + tier-match);
#   prorrogar_prazo    -> reabre UT_AprovacaoAlcada (prazo estendido; boundary timers rearmados);
#   seguir_analise     -> refaz o dossie (ST_PrepareApprovalDossier) antes de reabrir
#                         UT_AprovacaoAlcada (default/catch-all conservador).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_coordenacao_prorrogar_prazo_reabre_ut_aprovacao_alcada(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """decisao_coordenacao=prorrogar_prazo => reabre UT_AprovacaoAlcada (NAO GW_DecisaoPagamento).

    Distinto de assumir_aprovacao: nao decide agora, so estende o prazo. decisao_pagamento NAO e
    exigida nesta completude (o ramo nao passa por GW_DecisaoPagamento). Fecha a instancia depois
    completando a UT_AprovacaoAlcada reaberta com APROVAR, provando que o loop-back de fato reabriu
    uma User Task funcional (nao um dead-end).
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut_coord = await _drive_to_coordenacao(engine, pagto_probe, iid)
    await engine.complete_task_as_human(ut_coord.id, {"decisao_coordenacao": "prorrogar_prazo"})
    await pagto_probe.drain()

    reaberta = await engine.await_user_task(iid, _UT_APROVACAO)
    assert reaberta.id != ut_coord.id, "prorrogar_prazo deve reabrir uma UT_AprovacaoAlcada nova"

    await engine.complete_task_as_human(reaberta.id, _aprovacao_vars())
    await pagto_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_LIBERADO_HUMANO in ended, (
        f"prorrogar_prazo deve reabrir UT_AprovacaoAlcada funcional. ended={ended}"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_coordenacao_seguir_analise_refaz_dossie_e_reabre_ut_aprovacao_alcada(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """decisao_coordenacao=seguir_analise (default) => refaz o dossie -> reabre UT_AprovacaoAlcada.

    Distinto de assumir_aprovacao e de prorrogar_prazo: passa de novo por ST_PrepareApprovalDossier
    (prepare_approval_dossier roda >= 2x: dossie inicial + reentrada) antes de UT_AprovacaoAlcada
    reabrir. Duplamente bloqueado (module docstring FINDING 2): alem de nunca alcancar
    UT_AprovacaoAlcada pela primeira vez, prepare_approval_dossier tampouco existe como worker
    Python — mesmo que existisse, o padrao dict-first do modulo nao chama kafka.publish (finding 3).
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut_coord = await _drive_to_coordenacao(engine, pagto_probe, iid)
    dossies_antes = len(pagto_probe.notifications_of_type("pagto.prepare_approval_dossier"))

    await engine.complete_task_as_human(ut_coord.id, {"decisao_coordenacao": "seguir_analise"})
    await pagto_probe.drain()

    reaberta = await engine.await_user_task(iid, _UT_APROVACAO)
    dossies_depois = len(pagto_probe.notifications_of_type("pagto.prepare_approval_dossier"))
    assert dossies_depois >= dossies_antes + 1, (
        "seguir_analise deve refazer o dossie (ST_PrepareApprovalDossier) antes de reabrir a UT — "
        f"antes={dossies_antes} depois={dossies_depois}"
    )

    await engine.complete_task_as_human(reaberta.id, _aprovacao_vars())
    await pagto_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_LIBERADO_HUMANO in ended, (
        f"seguir_analise deve reabrir UT_AprovacaoAlcada funcional. ended={ended}"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_coordenacao_omite_decisao_cai_no_default_seguir_analise(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Completar UT_CoordenacaoAlcada SEM decisao_coordenacao NAO pode quebrar o engine
    (ENGINE-16004 / "Unknown property"/500) — mirror do regression test de GAP-ADEQ-3.

    O hardening default-init (ST_PublishSlaBreach seta decisao_coordenacao="" antes da UT) garante
    que GW_DecisaoCoordenacao avalie as condicoes contra "" e caia no default conservador
    (seguir_analise) -> refaz o dossie -> reabre UT_AprovacaoAlcada. Provamos rota limpa: dossie
    refeito (>=+1), UT reaberta funcional, sem terminal adverso sem humano.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    ut_coord = await _drive_to_coordenacao(engine, pagto_probe, iid)
    dossies_antes = len(pagto_probe.notifications_of_type("pagto.prepare_approval_dossier"))
    assert dossies_antes >= 1

    # Completa a UT SEM decisao_coordenacao (nem decisao_pagamento): a exata omissao da colisao.
    await engine.complete_task_as_human(ut_coord.id, {})
    await pagto_probe.drain()

    # Sem 500: o gateway roteou pelo default (seguir_analise) -> ST_PrepareApprovalDossier de novo.
    await engine.await_user_task(iid, _UT_APROVACAO)
    dossies_depois = len(pagto_probe.notifications_of_type("pagto.prepare_approval_dossier"))
    assert dossies_depois >= dossies_antes + 1, (
        "omitir decisao_coordenacao deve cair no default seguir_analise (refaz o dossie), nunca 500 — "
        f"antes={dossies_antes} depois={dossies_depois}"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PAGTO_ALCADA_WORKER_MISSING_REASON, strict=True)
async def test_alerta_sla_nao_interruptivo_notifica(
    engine: EngineRest,
    pagto_probe: PagtoEngineProbe,
    start_pagto: Callable[..., Any],
) -> None:
    """Timer NAO-interruptivo de alerta notifica coordenacao-financeira; a UT segue aberta.

    BT_AlertaSlaPagto (disparado via job execution) aciona notify_sla_risk -> End_RiscoSlaNotificado;
    a aprovacao continua aberta (alerta informativo, nenhum desfecho adverso por timeout). Duplamente
    bloqueado (module docstring FINDING 2): alem de nunca alcancar UT_AprovacaoAlcada, notify_sla_risk
    tampouco tem worker registrado.
    """
    inst = await start_pagto(valor_pagamento_cents=120_000_000, dentro_teto_l2=False)
    iid = inst["id"]

    await _drive_to_aprovacao(engine, pagto_probe, iid)
    job = await engine.await_timer_job(iid, _BT_ALERTA)
    await engine.execute_job(job.id)
    await pagto_probe.drain()

    assert pagto_probe.notifications_of_type("pagto.notify_sla_risk"), (
        "alerta nao-interruptivo deve notificar coordenacao-financeira"
    )
    ended = await engine.activity_instances_ended(iid)
    assert _END_RISCO_SLA in ended, "alerta nao-interruptivo => End_RiscoSlaNotificado"
    assert not (ended & _ENDS_ADVERSOS), "alerta informativo nunca produz terminal adverso"
    # A UT de aprovacao continua aberta (nao-interruptivo nao cancela a tarefa).
    open_tasks = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_APROVACAO in open_tasks, "alerta nao-interruptivo nao cancela a UT de aprovacao"
    await _assert_no_adverse_without_human_task(engine, iid)
