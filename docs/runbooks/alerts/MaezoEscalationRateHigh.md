# Alert Runbook: MaezoEscalationRateHigh

**Audience:** Agent managers, operations, on-call
**Last updated:** 2026-10-31
**Applies to:** conversational agents whose closed desfecho vocabulary declares `escalado_humano`
(today Helena and Lucas — `maezo.runtime.turn_telemetry._DESFECHO_VOCAB`)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_governanca_agentes` (R-085 / F-090)
**Threshold source:** `docs/runbooks/devops-stack.md` §6 "Escalation rate" (> 30% over 1 hour)

---

## Table of Contents

1. [Trigger](#1-trigger)
2. [What this means in business terms](#2-what-this-means-in-business-terms)
3. [First 5 minutes](#3-first-5-minutes)
4. [Triage tree](#4-triage-tree)
5. [Mitigation](#5-mitigation)
6. [Escalation](#6-escalation)
7. [False-alarm checks](#7-false-alarm-checks)

---

## 1. Trigger

```yaml
- alert: MaezoEscalationRateHigh
  expr: |
    (
      sum by (agent_id, environment) (increase(maezo_agent_desfecho_total{desfecho="escalado_humano"}[1h]))
      /
      sum by (agent_id, environment) (increase(maezo_agent_desfecho_total[1h]))
    ) > 0.30
    and on (agent_id, environment)
    (
      sum by (agent_id, environment) (increase(maezo_agent_desfecho_total{desfecho="escalado_humano"}[5m]))
      /
      sum by (agent_id, environment) (increase(maezo_agent_desfecho_total[5m]))
    ) > 0.30
    and on (agent_id, environment)
    (
      sum by (agent_id, environment) (increase(maezo_agent_desfecho_total[1h])) >= 10
    )
```

Source metric: `maezo_agent_desfecho_total` (CC-09), one sample per terminal turn, emitted by
`runtime.turn_telemetry.emit_turn_desfecho`. The denominator counts every desfecho of the agent,
including the shared technical-failure token, same rule as the `maezo_agent_kpi_derived`
recording rules.

**R-085 (owner decision 2026-10-31, issue #1084):** "volume minimo + 2 janelas, agregado por
agente e ambiente".
- The 30% / 1h threshold stays the sourced value (`devops-stack.md` §6). A SHORT window (5m) must
  also be above 30%, so the alert confirms the condition is current and clears quickly after
  recovery (multi-window pattern of `SLO.md` §5). The 5m length is parameter **P5 (TODO-OWNER)**.
- Minimum volume: at least 10 desfechos in the 1h window — parameter **P6 (TODO-OWNER)**; one
  escalation out of one turn no longer reads 100%.
- Aggregated by `agent_id` and `environment` (a scrape-time label of the ECS collector,
  `otel-collector-ecs-dev.yaml`; absent in local compose, where it collapses to one group). A
  per-tenant split still needs a scrape-time tenant label (owner decision).
- It still measures TURNS (one desfecho per terminal turn), not conversations.

## 2. What this means in business terms

More than 30% of an agent's turns in the last hour (summed across tenants, see §1) ended in a human handoff
(SP-OP-ESCALATION-001). Helena's `escalation_rate` KPI is "track" (neither too high — useless —
nor too low — risky; `spec/agents/helena/agent.yaml`); this alert is the operational ceiling the
runbook declares, not a KPI target.

## 3. First 5 minutes

```promql
maezo_helena_escalation_rate
sum by (agent_id, motivo_categoria) (increase(maezo_agent_desfecho_total{desfecho="escalado_humano"}[1h]))
```

## 4. Triage tree

```
Did a prompt/model/config change ship in the last hours?
├── YES → likely regression. Compare eval results; roll back the change.
└── NO  → genuine case-mix shift (campaign, outage on a data source the agent depends on).
          Check `motivo_categoria`; a single dominant motivo points at the dependency.
```

## 5. Mitigation

- Regression: roll back per [`cd-rollback.md`](../cd-rollback.md).
- Data-source outage driving escalations: fix the dependency; escalations are the safe path and
  must not be suppressed.
- Never lower escalation by changing routing rules on-call: escalation policy is an owner
  decision (DMN / autonomy policy).

## 6. Escalation

**DRAFT/verify — dono do serviço.** Notify the agent's human manager and the operations team that
staffs SP-OP-ESCALATION-001 queues (the human queue absorbs the extra volume).

## 7. False-alarm checks

- Low-volume hours: a handful of turns makes the ratio noisy. Check the denominator
  (`sum by (agent_id) (increase(maezo_agent_desfecho_total[1h]))`) before acting.
