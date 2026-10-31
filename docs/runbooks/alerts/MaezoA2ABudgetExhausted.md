# Alert Runbook: MaezoA2ABudgetExhausted

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-10-30
**Applies to:** agent runtime pods that host A2A delegation handlers (`agent-{name}`)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_governanca_agentes` (R-085 / F-090)
**Requirement source:** ADR-0010 ("Alertas: budget A2A estourado"); guard definition ADR-0003

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
- alert: MaezoA2ABudgetExhausted
  expr: |
    sum by (target) (
      increase(maezo_a2a_guard_refusal_total{guard="budget_exhausted"}[5m])
    ) > 0
```

`maezo_a2a_guard_refusal_total{target,guard}` is incremented once per delegation whose handler
raised a structural `DelegationError` (`maezo.a2a.dispatcher.DelegationDispatcher._finish_handler`).
`guard` is closed: `budget_exhausted` (ADR-0003 guard 3, `Budget.charge`), `max_hops`, `cyclic`,
`outro`. ADR-0010 names the event itself as the alert condition, so any budget refusal fires;
no rate threshold was invented. This is NOT the USD-based `MaezoA2ATaskBudgetExceeded` described
in `docs/runbooks/devops-stack.md` — that one stays PLANNED (no cost metric exists).

**Alcance atual — estruturalmente inalcancavel hoje.** Nenhum handler de producao subdelega:
nao ha chamada a `DelegationEnvelope.extend` em `src/`, e todo `.delegate()` parte de um worker
originador com `Budget(tokens=64)` novo e `DelegationEnvelope.root`. A serie fica em 0 ate existir
subdelegacao real; recusas de guarda na construcao do root (no worker) NAO sao contadas. A regra e o
emissor existem para quando a subdelegacao existir. TODO-OWNER.

## 2. What this means in business terms

A delegation chain ran out of its per-chain budget and the sub-delegation was rejected
(`RejectionReason.ANTI_LOOP`). The originating case does not silently continue: the dispatcher
writes a FAILED outcome audit row and a `rejected` fact. Repeated exhaustion usually means a
delegation loop or a budget too small for a legitimate chain.

## 3. First 5 minutes

```promql
sum by (target, guard) (increase(maezo_a2a_guard_refusal_total[1h]))
```

```bash
kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep -E "a2a|delegation" | tail -50
```

Check the A2A audit trail rows with `decision_basis = "A2A:handler_error:anti_loop"` for the
affected target.

## 4. Triage tree

```
Is max_hops / cyclic also rising for the same target?
├── YES → delegation loop: a handler is re-delegating in a cycle. Roll back the last change to
│         that agent's delegation module.
└── NO  → budget too small for a legitimate chain, or a new chain shape. Do not raise the budget
          on-call; open an owner ticket (budget values live in the agents' delegation modules).
```

## 5. Mitigation

- Loop after a deploy: roll back per [`cd-rollback.md`](../cd-rollback.md).
- Never disable or bypass the guard: it is the ADR-0003 structural protection.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing exists; notify the
agent-runtime owner.

## 7. False-alarm checks

- Unit/integration traffic against a shared environment can deliberately exercise the guard;
  check whether the delegation task ids belong to a test run.
