# Alert Runbook: MaezoPEPDenySpike

**Audience:** Platform engineers, SRE, on-call, security
**Last updated:** 2026-10-30
**Applies to:** every runtime pod that imports `maezo.gateway` (each `agent-{name}`, `worker-daemon`)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_governanca_agentes` (R-085 / F-090)
**Threshold source:** `docs/runbooks/devops-stack.md` §6 "PEP deny spike" (> 1 req/s)

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
- alert: MaezoPEPDenySpike
  expr: |
    sum by (tenant) (
      rate(maezo_gateway_pep_decision_total{decision="deny", enforced="true"}[5m])
    ) > 1
  for: 5m
```

`maezo_gateway_pep_decision_total{tenant,principal,decision,layer,reason,enforced}` is
incremented once per effect-PEP decision by `maezo.gateway.seams._base.gate` (the single
chokepoint every gated seam calls) through `maezo.platform.observability.record_pep_decision`.
`decision`/`layer`/`reason`/`enforced` are closed vocabularies (an unknown layer or reason becomes
`outro`); `tenant`/`principal` are composition-bound bounded tokens validated at `SeamContext`
construction, not closed sets. No PHI, no call argument, no business identifier (ADR-0006).

**Only ENFORCED denies count.** While the action-approvals manifest
(`spec/policies/autonomy/action-approvals.yaml`) is not ratified, every class denies in SHADOW
(`enforced="false"`, logged as `WOULD_DENY`) and this alert has nothing to fire on — by design.
The `for: 5m` and the `[5m]` window follow the convention of the `maezo_sla` group in the same
file; they are not sourced from a business document (TODO-OWNER).

## 2. What this means in business terms

The Policy Enforcement Point is blocking more than one effect per second on a tenant. Per the
runbook source this is an attack, a misconfiguration, or a policy regression. Blocked effects
fail closed: the affected agent or worker takes its declared denial shape (human escalation,
declared gap, DMN route unavailable), so beneficiaries see degraded automation, never an
unauthorised action.

## 3. First 5 minutes

```promql
# Which principal / layer / reason is producing the denies?
sum by (principal, layer, reason) (
  rate(maezo_gateway_pep_decision_total{decision="deny", enforced="true"}[5m])
)
```

```bash
# The per-call shadow/enforcement log line carries the operation token (never a label):
kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep effect_seam
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep effect_seam
```

## 4. Triage tree

```
Which layer dominates?
├── L0_CATALOGO (OPERACAO_DESCONHECIDA) → code calling an uncatalogued operation: regression in
│   a recent deploy. Roll back.
├── L1_CAPACIDADE (TOOL_NAO_DECLARADA / PROCESS_KEY_NAO_PERMITIDA) → agent.yaml allowlist or a
│   prompt change made the agent attempt an undeclared tool. Roll back the agent change.
├── L2_AUTONOMIA (HUMANO_REQUERIDO / AUTONOMIA_NEGADA) → an agent is attempting L0/L1 actions
│   autonomously. Expected to be refused; a spike means prompt/model drift. Check eval scores.
├── L5_RATIFICACAO → manifest/approval state changed (e.g. a class flipped to enforcing without
│   its approval rows). Check the last change to spec/policies/autonomy/.
└── single principal, any layer → possible abuse/runaway loop of that agent; correlate with
    maezo_effect_rate_limited_total.
```

## 5. Mitigation

- Regression from a deploy: roll back per [`cd-rollback.md`](../cd-rollback.md).
- Policy regression: revert the last autonomy policy change via `git revert` + PR (never edit
  the policy in place; `hard` items are untouchable).
- Suspected attack: consider rate limiting at the webhook receiver; do NOT relax the PEP.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing exists. Security-relevant:
notify the security team when a single principal dominates or the reason is
`TOOL_NAO_DECLARADA`.

## 7. False-alarm checks

- A manifest flip to `enforcing` for a class whose callers were already denied in shadow will
  convert shadow denies into enforced ones; compare with the `enforced="false"` series from the
  hour before the flip.
