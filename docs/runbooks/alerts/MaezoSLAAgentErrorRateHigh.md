# Alert Runbook: MaezoSLAAgentErrorRateHigh

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** agent runtime (`agent-{helena,rafael,marina,...}` Deployments)
**Alert source:** `deploy/observability/alert-rules.yml:36-53` (group `maezo_sla`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §3.2, §6 row 2

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

## 1. Trigger — READ THIS BEFORE ACTING ON A PAGE

**Code:** `deploy/observability/alert-rules.yml:36-53`

```yaml
- alert: MaezoSLAAgentErrorRateHigh
  expr: |
    (
      rate(maezo_agent_errors_total[5m])
      /
      rate(maezo_tool_calls_total[5m])
    ) > 0.01
  for: 5m
  labels:
    severity: critical
    category: sla
    component: agents
```

**This alert cannot fire on real agent errors today.** Both `maezo_agent_errors_total` and
`maezo_tool_calls_total` are `Counter`s defined at `src/maezo/runtime/metrics.py:50-60`, but
**no production code path anywhere in `src/` increments either one** — the only callers of
`get_metrics_collector()` (`agent_runtime/service.py:640`, `gateway/service.py:106`,
`worker_runtime/service.py:849`, `webhooks/whatsapp/app.py:55,87`) use it solely to hand the
registry to the `/metrics` HTTP endpoint. Both counters sit at a permanent `0`, so
`rate(...)` is `0/0` — PromQL never emits a sample for that expression, and `> 0.01` never
matches. Full trace: `docs/observability/SLO.md` §2, §3.2.

**If you are paged on this alert, the FIRST question is not "what is causing agent errors" —
it is "how did this fire at all," because on `main` as audited it structurally should not.**
Possible explanations, in order of likelihood: (a) a manual `amtool alert` test/silence
exercise, (b) the alert rule or metric wiring changed since this runbook was last updated —
re-check `alert-rules.yml:36-53` and `metrics.py` against this runbook's claims before trusting
either, (c) a genuinely new code path started incrementing these counters (grep
`collector.errors\|collector.tool_calls` in `src/` to confirm) and the underlying agent-error
signal is real.

## 2. What this means in business terms — IF the signal turns out to be real

Agent errors mean a conversational agent (Helena, Rafael, Marina, and the other AI agents listed
in `README.md`'s agent table) is failing tool calls or LLM calls mid-conversation. Per the
platform's HITL no-denial invariant, an agent error should never itself produce an adverse
decision — but a sustained agent failure means beneficiaries are not getting served (dropped
conversations, failed escalations, stalled clinical triage flows like Helena's).

## 3. First 5 minutes

```bash
# 1. Confirm this is not a false-positive from §1 first — check the raw series:
#    (Prometheus UI) rate(maezo_agent_errors_total[5m]) and rate(maezo_tool_calls_total[5m])
#    If both are flat 0 outside the alert's own evaluation, this is very likely §1's dead-metric
#    case and NOT a real agent outage — cross-check step 2 before paging further.

# 2. Real signal cross-check — the structured log line agent turns actually emit
#    (observability.py:405-438, `agent_turn_completed`) is NOT in Prometheus, but IS in
#    stdout/kubectl logs:
kubectl logs -n maezo-{tenant} deploy/agent-{agent} --since=10m | grep -E "agent_turn_completed|ERROR|exc_info"

# 3. Local dev:
docker compose logs -f agent-runtime
curl -sf http://localhost:8010/healthz
curl -sf http://localhost:8010/readyz

# 4. Kubernetes:
kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=agent-{agent}
kubectl logs -n maezo-{tenant} deploy/agent-{agent} -f

# 5. LLM provider health (a common real root cause for agent tool-call failures):
kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep -iE "anthropic|bedrock|rate.?limit|5[0-9]{2}" | tail -30
```

## 4. Triage tree

```
Are rate(maezo_agent_errors_total[5m]) and rate(maezo_tool_calls_total[5m]) both flat 0
in the Prometheus UI (not just at alert-eval time)?
├── YES → §1's dead-metric case. This is almost certainly a false/test fire, NOT a real outage.
│         Do NOT treat as critical without a corroborating signal from step 2 above (structured
│         logs) or a user/support report of failed conversations.
└── NO (counters ARE moving) → the wiring changed since this runbook was written; re-verify §1's
    claim against current `src/` before proceeding, then treat as a REAL error-rate alert:
    ├── Is `/readyz` red on the affected agent-{name} Deployment? → dependency-level failure
    │   (checkpointer/A2A signer/effect-policy — see build_readiness_checks(),
    │   src/maezo/runtime/agent_runtime/service.py:162)
    └── Is /readyz green but errors climbing? → check LLM provider health (step 5 above) and
        recent prompt/model/config changes: `git log --oneline -- src/maezo/agents/{agent}/`
```

## 5. Mitigation

- **Bad recent agent deploy/config:** roll back per [`cd-rollback.md`](../cd-rollback.md).
- **LLM provider outage/rate-limiting:** no in-repo automated failover exists (single-provider,
  ADR-0009) — escalate per §6; there is no mitigation action available in this repo beyond
  waiting out the provider incident or a manual provider-config change (owner-gated,
  `deploy/`/secrets).
- **Do not** treat this alert as actionable on its own without the §1/§3 cross-checks — acting on
  a phantom signal wastes on-call time that a real incident needs.
- **Root fix, not a workaround (owner decision, tracked in `docs/observability/SLO.md` §3.2):**
  wire a real production emitter to `collector.errors`/`.tool_calls`, or extend
  `record_agent_turn` (`observability.py:405`) to also emit a metric. That is a code change
  outside this runbook's scope — flag it, don't attempt it ad hoc during an incident.

## 6. Escalation

**DRAFT/verify — dono do serviço.** Same gap as every other alert in this set: no on-call/
Alertmanager routing config exists in-repo, and `spec/processes/dmn/escalation_routing.dmn`
governs agent-conversation escalation to humans (business process), not SRE alert paging. Until
an owner defines the mapping, treat as: page the platform/SRE rotation owning agent-runtime; if
conversations are visibly failing for beneficiaries, also notify the medical-audit/operations
contact per `docs/runbooks/helena.md`.

## 7. False-alarm checks

- **Always** run the §1/§3-step-1 dead-metric cross-check FIRST — this is the single most likely
  false-alarm cause for this specific alert, and it is a repo-verified fact as of this writing,
  not a guess.
- If real: a single restarted pod briefly shows `up{job=...}==0` then recovers — confirm the
  error window overlaps a deploy/restart before treating as a genuine LLM/tool failure.
