# Alert Runbook: MaezoAgentCrashLoop

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** agent runtime (`agent-{helena,rafael,marina,...}` Deployments)
**Alert source:** `deploy/observability/alert-rules.yml:98-111` (group `maezo_crash_loop`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §3.2, §6 row 5

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

**Code:** `deploy/observability/alert-rules.yml:98-111`

```yaml
- alert: MaezoAgentCrashLoop
  expr: |
    rate(maezo_agent_errors_total[1m]) > 0
  for: 2m
  labels:
    severity: critical
    category: crash_loop
    component: agents
```

**This alert cannot fire on real agent errors today**, for the identical reason documented in
[`MaezoSLAAgentErrorRateHigh.md`](MaezoSLAAgentErrorRateHigh.md) §1: `maezo_agent_errors_total`
(`src/maezo/runtime/metrics.py:56-60`) is a `Counter` with **no production incrementer anywhere
in `src/`** — only `tests/unit/runtime/test_metrics.py:36` exercises it directly. `rate(...)`
over a permanently-`0` counter is `0`, so `> 0` never matches. Full trace:
`docs/observability/SLO.md` §2, §3.2.

**If paged, the pod-restart signal is real regardless of this metric — check `kubectl get pods`
first (§3), because "crash loop" in the alert NAME may still be true even though the metric
behind the alert's EXPRESSION cannot detect it.** Do not assume "the metric can't fire, so this
page is noise" without that check — a genuinely crash-looping agent pod produces zero
`maezo_agent_errors_total` samples (the process dies before emitting anything), so if this alert
somehow fired, treat it as suspicious and verify via Kubernetes directly rather than trusting or
dismissing the metric either way.

## 2. What this means in business terms

An agent (Helena, Rafael, Marina, or another agent from `README.md`'s agent table) restarting
repeatedly means that agent is completely unavailable — every beneficiary conversation routed to
it fails outright (not degraded, absent). For Helena specifically (health navigator / triage),
this is a patient-facing availability incident, not just an internal metric.

## 3. First 5 minutes

```bash
# 1. Check actual pod restart status — this is the ground truth regardless of the metric issue:
kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=agent-{agent}

# 2. If restarting, get pre-crash logs:
kubectl logs -n maezo-{tenant} <pod> --previous -c {container}

# 3. Check liveness/readiness probe status and recent events:
kubectl describe pod -n maezo-{tenant} <pod>

# 4. Local dev:
docker compose ps agent-runtime
docker compose logs -f agent-runtime
curl -sf http://localhost:8010/healthz
curl -sf http://localhost:8010/readyz

# 5. Check for OOM or resource-limit issues:
kubectl top pod -n maezo-{tenant} -l app.kubernetes.io/component=agent-{agent}
```

## 4. Triage tree

```
Is kubectl get pods RESTARTS actually climbing?
├── YES → genuine crash loop, independent of whether the alert's own metric could see it.
│         Common causes:
│         - Startup failure in build_readiness_checks() (agent_runtime/service.py:162) —
│           a required seam (checkpointer, A2A signer, effect-policy) failing hard at boot
│           rather than just leaving /readyz red.
│         - OOMKilled — kubectl describe pod for the exact reason; check LLM client memory use
│           under load.
│         - RUNTIME_MODE fail-closed refusal (see docs/runbooks/worker-runtime.md §4's analogous
│           worker-daemon gate — agent_runtime shares a2a_composition.py's
│           _require_signer_or_fail_closed) raising instead of degrading gracefully.
└── NO → the alert fired without a real restart loop. Given §1's dead-metric finding, this is
    unexpected — re-verify the metric wiring claim against current `src/` (grep
    "collector.errors\|record_agent_turn" for a new emitter) before dismissing as noise; if
    confirmed still dead, this was very likely a manual test/silence exercise.
```

## 5. Mitigation

- **Genuine crash loop from a bad deploy:** roll back per [`cd-rollback.md`](../cd-rollback.md)
  immediately — patient-facing total unavailability, do not wait for full root-cause first.
- **OOMKilled:** flag `resources.limits.memory` in
  `deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml` for an owner-gated `deploy/`
  fix; not an on-call edit.
- **RUNTIME_MODE / A2A signer fail-closed refusal:** confirm intentional per
  [`a2a-key-rotation.md`](../a2a-key-rotation.md) before treating as a bug — a missing signing
  key in production mode is a designed refusal (ADR-0003/0007), not a defect to patch around.
- **Do not** attempt to fix the dead-metric gap (§1) as an incident-response action — that is a
  code change (owner decision per `docs/observability/SLO.md` §3.2), out of scope for an
  in-the-moment mitigation.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing/on-call roster exists (same
gap noted throughout this set — see [`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md)
§6 for the DMN-vs-SRE-paging distinction). Given `critical` severity and patient-facing impact,
page the platform/SRE rotation owning agent-runtime immediately; if the affected agent is Helena
(clinical triage-adjacent), also notify operations/medical-audit per
`docs/runbooks/helena.md`.

## 7. False-alarm checks

- If `kubectl get pods` shows a stable RESTARTS count (not climbing), and no corroborating
  `agent_turn_completed` error-shaped log lines appear (§3 of
  [`MaezoSLAAgentErrorRateHigh.md`](MaezoSLAAgentErrorRateHigh.md)), this is very likely a
  test/manual-trigger fire given §1's dead-metric finding — but do not close it out without the
  pod-restart check, since that check is independent of the metric defect.
- A single pod restart during a rolling deploy is expected — confirm this isn't mid-rollout
  (`kubectl rollout status deploy/agent-{agent} -n maezo-{tenant}`) before escalating as an
  incident.
