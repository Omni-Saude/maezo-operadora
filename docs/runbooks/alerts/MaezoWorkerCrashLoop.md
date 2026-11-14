# Alert Runbook: MaezoWorkerCrashLoop

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-10-31
**Applies to:** worker-daemon (Helm Deployment `worker-daemon`, container `worker-daemon`)
**Alert source:** `deploy/observability/alert-rules.yml` (group `maezo_crash_loop`)
**Decision:** R-085, owner decision 2026-10-31 (issue #1084, Rodada 2 "Observabilidade"):
"remover/rebasear os 2 falsos crash loop".

---

## Table of Contents

1. [Trigger](#1-trigger)
2. [What changed and why](#2-what-changed-and-why)
3. [First 5 minutes](#3-first-5-minutes)
4. [Mitigation](#4-mitigation)
5. [False-alarm checks](#5-false-alarm-checks)

---

## 1. Trigger

```yaml
- alert: MaezoWorkerCrashLoop
  expr: |
    sum by (namespace, pod, container) (
      increase(kube_pod_container_status_restarts_total{container="worker-daemon"}[15m])
    ) >= 3
  labels:
    severity: warning
    category: crash_loop
```

The container `worker-daemon` (`deploy/helm/maezo-tenant/templates/deployment-worker-daemon.yaml`) restarted at least 3 times in 15 minutes: the
CrashLoopBackOff signature that `docs/runbooks/devops-stack.md` §"Crash loop" already fixes. The
series comes from kube-state-metrics (`kube-state-metrics` scrape job in
`deploy/observability/prometheus.yml`). There is no kube-state-metrics in ECS dev, so the alert
has no series there: it does not fire falsely, it has nothing to look at.

Severity is `warning` until the owner promotes it (parameter **P11, TODO-OWNER**). With the
Alertmanager routing, `warning` opens a ticket and `critical` pages.

## 2. What changed and why

The previous rule was `rate(maezo_worker_error_count_total[5m]) > 0.01` for 5m, `critical`. That is an error RATE (per worker, topic and raw error type), not a crash loop: it fired on
designed refusals and duplicated the error-rate alert, while a container that really restarted in
a loop could go unseen. The error rate stays covered by `MaezoSLAWorkerErrorRateHigh`; this alert now reads what its
name says.

## 3. First 5 minutes

1. Read `namespace`/`pod`/`container` from the alert labels.
2. `kubectl get pods -n <namespace> <pod>` — confirm `CrashLoopBackOff` and the restart count.
3. `kubectl logs -n <namespace> <pod> -c worker-daemon --previous` — the failure before the last restart.
4. `kubectl describe pod -n <namespace> <pod>` — OOMKilled, failed probes, missing secret/config.

## 4. Mitigation

- Fail-closed startup error (missing secret/env var, settings `ValidationError`): fix the
  configuration and redeploy.
- Regression in a recent release: `kubectl rollout undo deployment/<deployment> -n <namespace>`.
- `kubectl delete pod` only resets the restart counter; it does not fix the crash.

## 5. False-alarm checks

- A deliberate rolling restart does not reach 3 restarts of the same container in 15 minutes.
- If kube-state-metrics was itself restarted, `increase()` handles the counter reset; a spike right
  after a kube-state-metrics restart that does not match `kubectl get pods` is not a crash loop.
