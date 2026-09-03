# Alert Runbook: MaezoWorkerCrashLoop

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** worker-daemon (compose service `worker-runtime`, Helm Deployment `worker-daemon`)
**Alert source:** `deploy/observability/alert-rules.yml:81-95` (group `maezo_crash_loop`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §3.1, §6 row 4

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

**Code:** `deploy/observability/alert-rules.yml:81-95`

```yaml
- alert: MaezoWorkerCrashLoop
  expr: |
    rate(maezo_worker_error_count_total[5m]) > 0.01
  for: 5m
  labels:
    severity: critical
    category: crash_loop
    component: workers
```

**Naming mismatch, verified — read the expr, not just the alert name or the comment above it.**
The rule's own comment (`alert-rules.yml:80`) says "Worker crash-loop: > 3 restarts in 5
minutes," but the `expr` is an **error-rate** threshold (`rate(maezo_worker_error_count_total[5m])
> 0.01`), not a pod-restart-count check — there is no `kube_pod_container_status_restarts_total`
or equivalent in this expression. This alert is effectively a lower-severity duplicate of
`MaezoSLAWorkerErrorRateHigh` (same metric, a lower rate threshold — `0.01`/s vs the SLA alert's
5%-of-executions ratio; the two are not directly comparable units, but both key off the same
counter), labeled `critical` instead of `warning`. Same PARTIAL coverage caveat as the other
worker alerts (§1 of [`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md)) — raw
handlers never increment `maezo_worker_error_count_total`, so a genuine restart-loop confined to
a raw-handler topic would not be caught by this expression either way.

## 2. What this means in business terms

Same as [`MaezoSLAWorkerErrorRateHigh.md`](MaezoSLAWorkerErrorRateHigh.md) §2 — the
worker/topic labels identify the affected SP-OP process family via
`src/maezo/tools/workers/bootstrap.py`'s 17 domain bootstraps. Because this alert is `critical`
and named "crash loop," on-call should additionally check whether the **pod itself** is actually
restarting (§3) — that is a materially worse situation (total unavailability for that
worker-daemon replica) than an elevated-but-non-zero error rate the name implies but the
expression does not actually measure.

## 3. First 5 minutes

```bash
# 1. Check whether the pod is ACTUALLY restarting (the alert name's claim) — the expr alone
#    cannot tell you this:
kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=worker-daemon
#    Look at RESTARTS column. If 0 and stable, this is an error-rate spike, not a real crash loop.

# 2. If restarting — get the pre-crash logs:
kubectl logs -n maezo-{tenant} <pod> --previous

# 3. If NOT restarting — same triage as the error-rate alert:
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -E "worker_failed|worker_exhausted_retries" | tail -30

# 4. Local dev:
docker compose ps worker-runtime     # check for repeated "Restarting" state
docker compose logs -f worker-runtime
```

## 4. Triage tree

```
Is the pod's RESTARTS count actually climbing (kubectl get pods, §3 step 1)?
├── YES → genuine crash loop. Get pre-crash logs (--previous). Common causes:
│         - Unhandled exception in worker startup (bootstrap.py's 17-registration composition —
│           one bad `register_<domain>_workers` call can crash the whole daemon at boot).
│         - OOMKilled — kubectl describe pod for the exact reason.
│         - Liveness probe failing repeatedly (/healthz on port 8000) — check probe config
│           (docs/runbooks/worker-runtime.md §3: initialDelaySeconds 20, periodSeconds 30).
└── NO → this is the error-rate signal, not a real crash loop (§1 naming mismatch). Follow
    MaezoSLAWorkerErrorRateHigh.md's triage tree instead — same underlying metric.
```

## 5. Mitigation

- **Genuine crash loop from a bad deploy:** roll back per [`cd-rollback.md`](../cd-rollback.md)
  immediately — this is total unavailability for the affected replica(s), higher urgency than an
  error-rate blip.
- **OOMKilled:** check `resources.limits.memory` in `deploy/helm/maezo-tenant/templates/
  deployment-worker-daemon.yaml` against actual usage; a limits change is an owner-gated `deploy/`
  edit, not an on-call action — escalate rather than hand-edit.
- **Error-rate signal (not a real restart loop):** follow
  [`MaezoSLAWorkerErrorRateHigh.md`](MaezoSLAWorkerErrorRateHigh.md) §5.
- **Root-cause fix for the naming/expr mismatch itself:** flag to whoever holds the `deploy/`
  slice (D12-01-b) — either fix the comment to match the expr, or fix the expr to actually count
  restarts (e.g. `increase(kube_pod_container_status_restarts_total{...}[5m]) > 3`, which itself
  would need the same kube-state-metrics scrape target `MaezoLifecycleJobFailed` is waiting on —
  see that runbook). Not an on-call action during an active page.

## 6. Escalation

**DRAFT/verify — dono do serviço.** Same gap noted throughout this alert set — no in-repo
Alertmanager routing/on-call roster; see
[`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md) §6 for the DMN-vs-SRE-paging
distinction. Given the `critical` severity label, page the platform/SRE rotation immediately
regardless of whether §4 resolves to "real crash loop" or "error-rate signal" — triage happens
after paging, not before, for a `critical`-labeled alert.

## 7. False-alarm checks

- **Always** check `kubectl get pods` RESTARTS first (§3 step 1) — if flat, this is not the crash
  loop the name and `critical` severity imply, it is the same error-rate signal
  `MaezoSLAWorkerErrorRateHigh` already covers at `warning`. Downgrade your own urgency
  accordingly, but still investigate — a `0.01`/s error rate sustained 5 minutes is still real.
- Confirm the affected topic is `WorkerBase`-wrapped (§1) before concluding "no errors" from a
  quiet alert on a raw-handler topic.
