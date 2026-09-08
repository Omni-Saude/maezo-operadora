# Alert Runbook: MaezoSLAWorkerErrorRateHigh

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** worker-daemon (compose service `worker-runtime`, Helm Deployment `worker-daemon`)
**Alert source:** `deploy/observability/alert-rules.yml:56-73` (group `maezo_sla`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §3.1, §6 row 3

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

**Code:** `deploy/observability/alert-rules.yml:56-73`

```yaml
- alert: MaezoSLAWorkerErrorRateHigh
  expr: |
    (
      sum by (worker, topic) (rate(maezo_worker_error_count_total[5m]))
      /
      sum by (worker, topic) (rate(maezo_worker_execution_time_seconds_count[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    category: sla
    component: workers
```

More than 5% of worker executions error out over a 5-minute window. Unlike the agent pair
(`MaezoSLAAgentErrorRateHigh`), this metric pair **is** live in production — but only for
`WorkerBase`-wrapped topics (`src/maezo/tools/workers/base.py:203-236`). Raw-handler topics
(`events`, `programa.py`'s `stratify_risk`/`stop_processing`/`proactive_contact`/
`notify_sla_risk`, and the raw handlers in `recurso.py`/`lgpd.py`/`escalation.py`) contribute to
**neither** the numerator nor the denominator — an error spike confined to those topics is
invisible to this alert. Full trace: `docs/observability/SLO.md` §2.

Both sides are wrapped in `sum by (worker, topic) (...)` (the same vector-matching fix the sibling
`MaezoSLAAgentErrorRateHigh` already used, `ALERT-COUNTER-LABELS`/R-063): the numerator's raw
`error_type` label is aggregated away so PromQL's default vector matching on `/` finds an operand
on both sides. As a direct consequence, the fired alert's own labels carry only `worker`/`topic`
— **not** `error_type` — and the `description` annotation no longer references
`{{ $labels.error_type }}`. The failure mode (which `error_type`) is still available, just not on
the alert itself: read it from the raw `maezo_worker_error_count_total` series (which still
carries the label) or from the worker logs for that `worker`/`topic` pair (§3, §4 below).

## 2. What this means in business terms

Same worker/topic → SP-OP process mapping as `MaezoSLAWorkerLatencyHigh` (§2 there). An elevated
error rate means external tasks are failing and retrying (or opening incidents) more than
normal — per the harness's retry-ownership design (`src/maezo/tools/workers/harness.py`), the
**engine** (CIB Seven), not the client, decides whether a failure gets re-delivered or opens an
incident. A sustained 5% error rate risks incidents piling up on CIB Seven's cockpit, which stall
the affected BPMN processes until a human resolves them.

## 3. First 5 minutes

```bash
# 1. Read the alert labels — worker/topic tell you WHERE. The alert's own labels do NOT carry
#    error_type (aggregated away by the sum-by fix); pull error_type from the raw
#    maezo_worker_error_count_total series or the worker's own logs for that worker/topic.

# 2. Local dev:
docker compose logs -f worker-runtime | grep -E "worker_failed|worker_error"
curl -sf http://localhost:8020/readyz

# 3. Kubernetes:
kubectl logs -n maezo-{tenant} deploy/worker-daemon -f
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -E "{topic}|WorkerFailure|WorkerBpmnError" | tail -30

# 4. Check for engine-side incidents opening (retries exhausted -> engine incident):
curl -s "http://cibseven:8080/engine-rest/incident?processDefinitionKey=SP-OP-{X}-001" | jq '.'

# 5. Postgres audit sink — a red audit_sink_ready blocks the daemon from accepting work at all,
#    which can masquerade as an error-rate spike on whatever it was mid-processing:
curl -sf http://localhost:8020/readyz | jq '.checks.audit_sink_ready // .'
```

## 4. Triage tree

```
Once error_type is pulled from the raw metric/logs (§3 step 1 — it is NOT on the fired alert's
own labels post-aggregation): is it consistent (one exception class), or mixed?
├── ONE class (e.g. a specific ValueError/PermissionError) →
│   Guard/validation errors (*NotHumanError, ValueError family) ALWAYS report retries=0 by
│   design (harness.py design §9) — an immediate incident, not a transient blip. Check if this
│   reflects a genuine upstream data problem (bad process variables) vs a code regression.
├── MIXED / transient-looking (RuntimeError, OSError, TimeoutError, ConnectionError, httpx.HTTPError) →
│   Likely a downstream dependency issue:
│   - CIB Seven reachability: curl http://cibseven:8080/engine-rest/engine
│   - HAPI FHIR reachability (fhir-sync-backed workers): curl http://hapi-fhir:8080/fhir/metadata
│   - Kafka reachability (publish-side workers): docker compose logs kafka | tail -50
└── Is the affected topic a RAW handler (see §1 coverage caveat)?
    └── YES → this alert CANNOT see that topic's errors. If support/user reports suggest a raw-
              handler topic is failing while this alert stays quiet, that is the coverage gap,
              not evidence of health.
```

## 5. Mitigation

- **Bad recent deploy:** roll back per [`cd-rollback.md`](../cd-rollback.md).
- **Upstream dependency down (CIB Seven / HAPI / Kafka):** no in-repo automated mitigation for
  the dependency itself; the worker-daemon's own retry/incident semantics (engine-owned) already
  handle transient failures — do not add ad hoc retry logic around this.
- **Engine incidents piling up:** these require human resolution via the CIB Seven cockpit
  (`http://cibseven:8080/app/cockpit/`, dev) or the equivalent AWS-hosted dashboard in
  staging/prod (`docs/runbooks/engine-processes.md` "Dashboard & monitoring").
- **Audit sink degraded:** see [`audit-recovery.md`](../audit-recovery.md) — a red
  `audit_sink_ready` is fail-closed by design (ADR-0007); do not attempt to bypass it.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing/on-call roster exists for
platform alerts; `spec/processes/dmn/escalation_routing.dmn` governs agent-conversation
escalation to humans, not SRE paging (see the same note in
[`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md) §6). Page the platform/SRE
rotation owning worker-daemon; for a process with a regulatory deadline (auth, LGPD DSR, NIP,
ANS submissions), also notify compliance per `docs/runbooks/engine-processes.md` §6.

## 7. False-alarm checks

- Confirm the affected topic is `WorkerBase`-wrapped, not a raw handler (§1) — an alert firing on
  one topic while a raw-handler topic is the real problem is a genuine blind spot, not a
  false alarm in the usual sense, but also not evidence that THIS topic is unhealthy.
- A brief burst of `bpmn_error`-classified failures (a modeled, expected BPMN error path) does
  NOT increment `maezo_worker_error_count_total` — only `WorkerBase.run()`'s unhandled-exception
  path does (`base.py:226-236`); so this alert, when it does fire, is never a false positive from
  ordinary modeled business errors.
- Check whether the spike correlates with a CIB Seven restart/redeploy window (cold start —
  `docs/runbooks/worker-runtime.md` §5) before treating as a code regression.
