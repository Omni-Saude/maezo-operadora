# Alert Runbook: MaezoHITLApprovalRateDrop

**Audience:** Agent managers, the human approvers' coordinators, on-call (ticket, not page)
**Last updated:** 2026-10-31
**Applies to:** the L1 approval User Tasks read by the engine-history poller
(`src/maezo/runtime/worker_runtime/engine_metrics.py`, `L1_DECISION_TASKS`)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_hitl_l1`
**Decision:** R-085, owner decision 2026-10-31 (issue #1084, Rodada 2 "Observabilidade"): L1 human
approval metric from a poller of engine history, one decision field per task, closed labels, alert
on a drop against its own baseline measured in days, severity `warning`. ADR-0010 promises the
signal ("queda de aprovacao humana").

---

## 1. Trigger

For each `(tenant, agent_id, action_class)`, the alert fires when all of these hold:

- the share of decisions over the last **7 days** that executed the L1 action is lower than the
  share in the **28-day baseline ending 7 days ago**, minus **3 binomial standard errors**
  (`sqrt(p * (1 - p) / n)`, where `p` is the baseline share and `n` the current decision count);
- there were at least **30** decisions in the current window.

The window lengths (P8), k = 3 (P9) and the minimum of 30 (P10) are **parameters marked TODO-OWNER**
in the rule file. No number here comes from a business source; the owner replaces them after
seeing the baseline variance. No `for:`, because at a resolution of days it adds nothing.

"Executed the L1 action" means the token the guarded worker requires to act:

| Task (process) | action_class | Approval token(s) | Other declared tokens |
|---|---|---|---|
| `UT_AprovacaoAlcada` (SP-OP-PAGTO-001) | `high_value_payment` | `APROVAR` | `RECUSAR`, `SOLICITAR_INFO`, `CANCELAR` |
| `UT_RevisarEnvio` (SP-OP-ANS-SUBMIT-001) | `ans_official_submission` | `APROVAR_ENVIO` | `CORRIGIR_PENDENCIA`, `ADIAR_ENVIO` |
| `UT_AnaliseDescredenciamento` (SP-OP-CRED-001) | `provider_decredentialing` | `DESCREDENCIAR` | `MANTER`, `SOLICITAR_INFO` |
| `UT_RevisaoJuridicaNip` (SP-OP-NIP-001) | `nip_response` | `CONCEDER`, `RESPONDER_NAO_ASSISTENCIAL` | `MANTER_NEGATIVA` (L0 hard, human only), `SOLICITAR_INFO` |
| `UT_DecisaoFallback` (SP-OP-ADEQUACAO-001) | `adequacao_fallback_commitment` | `COMPROMISSO_FALLBACK` | `MONITORAR_OK`, `ENCAMINHAR_CRED`, `SOLICITAR_INFO` |

The denominator is every decision carrying a BPMN token. `sem_decisao` (the task ended without
completion: interrupting timer or instance cancellation) and `desconhecido` (a value outside the
declared vocabulary) are excluded.

**Open owner question (analysis A.3, item 1):** whether `SOLICITAR_INFO`/`CORRIGIR_PENDENCIA`
should count against approval. Until the owner defines the outcome classes, they count as "not
approved".

## 2. What this means (and does not mean)

The L1 dossiers do NOT recommend (Andre's dossier sets `decisao_pagamento: None`; the dossier
"instrui, nunca a substitui"). So a drop is **not** "humans disagree with the agent more". It is a
change in case mix, in upstream admissibility, or in dossier quality. It is a quality/drift
signal, routed as a ticket.

## 3. Preconditions (why the alert may have no data)

- The poller ships dark: `MAEZO_L1_DECISION_POLLER=true` on ONE worker-daemon replica per tenant
  (two replicas count twice).
- The worker daemon must be scraped. In ECS dev, `otel-collector-ecs-dev.yaml` scrapes only
  `maezo-agentes`, `maezo-receptor` and the collector.
- The 28-day baseline with a 7-day offset needs about 35 days of retention: AMP yes, the local
  Prometheus (15 days by default) no.
- The engine must keep `HistoricDetail` (history level FULL) longer than the poll interval.

## 4. First checks

1. `sum by (decisao) (increase(maezo_l1_decision_total{agent_id="<agent>", action_class="<class>"}[7d]))`
   — which tokens grew.
2. `maezo_engine_metrics_poll_total{outcome="erro"}` — a stalled or failing poller reads as "no
   decisions", not as a drop.
3. Compare with the case mix upstream (admissibility outcomes of the same process) before touching
   the agent.

## 5. Escalation

Agent manager of `agent_id` plus the coordinator of the approver group of the task. Never change an
approval rule or an autonomy level in reaction to this alert: the L1 decision stays human
(ADR-0005/ADR-0008).
