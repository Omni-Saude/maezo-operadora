# Alert Runbook: MaezoSLAWorkerLatencyHigh

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** worker-daemon (compose service `worker-runtime`, Helm Deployment `worker-daemon`)
**Alert source:** `deploy/observability/alert-rules.yml:18-33` (group `maezo_sla`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §3.1, §6 row 1

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

**Code:** `deploy/observability/alert-rules.yml:18-33`

```yaml
- alert: MaezoSLAWorkerLatencyHigh
  expr: |
    histogram_quantile(0.95,
      rate(maezo_worker_execution_time_seconds_bucket[5m])
    ) > 5
  for: 5m
  labels:
    severity: warning
    category: sla
    component: workers
```

p95 of `maezo_worker_execution_time_seconds` (labels `worker`, `topic`) exceeds 5 seconds,
sustained for 5 minutes. **Coverage caveat (verify before trusting a page or its silence):** this
metric is emitted only by `WorkerBase.run()` (`src/maezo/tools/workers/base.py:203-213`) — raw
`harness.register()` handlers (`events`, plus `programa.py`'s `stratify_risk`/`stop_processing`/
`proactive_contact`/`notify_sla_risk`, and the raw handlers in `recurso.py`/`lgpd.py`/
`escalation.py`) never emit it, so a slowdown confined to those topics will not move this
metric at all. See `docs/observability/SLO.md` §2/§3.1 for the full trace and the recommended
migration to `maezo_worker_task_duration_seconds` (full coverage, no gap).

## 2. What this means in business terms

Every worker/topic pair backs an external task in one of the 15 SP-OP-* BPMN processes
(`docs/processes/catalog.md`) — the 17 domain bootstraps registered by
`src/maezo/tools/workers/bootstrap.py` (adequacao, ans_cron, ans_submit, auth, cancel, contas,
credenciamento, escalation, events, fraude, inadimplencia, lgpd, nip, pagto, programa, recurso,
reembolso). The alert's `worker`/`topic` labels tell you exactly which one is slow — check them
before assuming impact. A slow external task means: BPMN process instances are stuck waiting on
that task longer than usual, which delays whatever regulatory-deadline clock that process is
running against (e.g. RN259 authorization SLA for `SP-OP-AUTH-001`, LGPD's 15-day DSR window for
`SP-OP-LGPD-DSR-001`). This is a performance/SLA risk, not yet a data-integrity risk.

## 3. First 5 minutes

```bash
# 1. Read the alert labels first — worker + topic tell you WHICH process is affected.
#    (Prometheus/Alertmanager UI, or `curl -s http://localhost:9090/api/v1/alerts | jq`.)

# 2. Local dev — logs and health:
docker compose logs -f worker-runtime
curl -sf http://localhost:8020/healthz   # immediate 200 once the process is up
curl -sf http://localhost:8020/readyz    # reflects kafka/engine/workers/harness + audit_sink_ready

# 3. Kubernetes (staging/prod) — logs, rollout, pod status:
kubectl logs -n maezo-{tenant} deploy/worker-daemon -f
kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=worker-daemon
kubectl rollout status deploy/worker-daemon -n maezo-{tenant}

# 4. Filter logs to the specific topic named in the alert:
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -E "{topic}|worker_executing|worker_failed" | tail -50

# 5. Check the engine itself is healthy — a slow/degraded CIB Seven is a common root cause
#    (external-task fetch-and-lock round trips through it):
curl -s http://cibseven:8080/engine-rest/engine | jq '.'
curl -s "http://cibseven:8080/engine-rest/process-instance?processDefinitionKey=SP-OP-{X}-001&active=true" | jq 'length'
```

## 4. Triage tree

```
Is /readyz on worker-daemon green?
├── NO (audit_sink_ready red) → Postgres audit sink is the bottleneck, not the worker logic.
│                                See docs/runbooks/audit-recovery.md; verify chain reachability:
│                                uv run python -m maezo.gateway.audit_postgres --dsn "$DATABASE_URL" --tenant {tenant}
├── YES → Is the SAME topic slow for every replica, or just one pod?
│         ├── One pod only → likely a stuck/leaked resource on that replica.
│         │                  kubectl logs --previous, then kubectl delete pod to force reschedule.
│         └── All replicas → check the downstream dependency the worker calls:
│                             - CIB Seven (§3 step 5) — engine-side slowness propagates here.
│                             - HAPI FHIR (fhir-sync-backed workers): curl http://hapi-fhir:8080/fhir/metadata
│                             - Kafka publish latency (workers that call operadora.events.publish):
│                               docker compose exec kafka kafka-consumer-groups \
│                                 --bootstrap-server kafka:29092 --describe --all-groups
└── Is this a RAW-handler topic (events, stratify_risk, stop_processing, proactive_contact,
    notify_sla_risk, or a recurso/lgpd/escalation raw handler)?
    └── YES → this metric CANNOT reflect that topic's latency (§1 coverage caveat). The alert
              firing on a DIFFERENT topic while a raw-handler topic is actually slow is a real
              blind spot — cross-check with `maezo_worker_task_duration_seconds{topic="..."}`
              (T1.1, full coverage, docs/observability/SLO.md §2) if available in your Grafana.
```

## 5. Mitigation

- **Bad recent deploy of the worker-daemon image:** roll back per
  [`cd-rollback.md`](../cd-rollback.md) (`helm rollback`).
- **Engine-side slowness (CIB Seven):** no in-repo automated mitigation exists; escalate per §6 —
  this is a process-engineering / infra concern, not something a worker-daemon restart fixes.
- **Postgres audit sink degraded:** see [`audit-recovery.md`](../audit-recovery.md) §4 (recovery
  posture) — do NOT restart the daemon repeatedly hoping it self-heals; a red `audit_sink_ready`
  is a fail-closed gate by design (ADR-0007), not a transient glitch to route around.
- **Do not** disable or widen this alert's threshold as a workaround — that is an owner-gated
  change to `deploy/observability/alert-rules.yml` (slice D12-01-b), not an on-call action.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo on-call/PagerDuty roster or Alertmanager routing
config exists for platform/SRE infrastructure alerts (only `spec/processes/dmn/escalation_routing.dmn`
exists, and it routes **agent-conversation** escalations to human staff — e.g. `falha_tecnica` →
group `atendimento-humano`, ack SLA `PT4H`, resolution `PT24H` (rule `r6`,
`spec/processes/dmn/escalation_routing.dmn:73-81`) — a business-process concern, not
an SRE paging concern; there is no DMN or contract mapping a Prometheus alert name to a human
team). Until a service owner defines that mapping, treat this as: page the platform/SRE rotation
that owns `worker-daemon`; if the underlying process is one with a regulatory deadline (auth,
LGPD DSR, NIP, ANS submissions), also notify the compliance/audit contact per
`docs/runbooks/engine-processes.md` §6.

## 7. False-alarm checks

- Confirm the alert's `worker`/`topic` labels are for a `WorkerBase`-wrapped topic, not a raw
  handler (§1) — if raw, the METRIC may be stale/absent-of-signal rather than genuinely slow.
- A single slow outlier task (e.g. one large batch document) can push p95 over 5s for one
  `[5m]` window without a systemic problem — check whether the `for: 5m` sustained condition
  was met on genuinely repeated slow executions, not one artifact.
- Confirm CIB Seven wasn't mid-restart/cold-JVM-start (`docker compose ps` — CIB Seven is
  documented as "the slow one — JVM cold start" in `docs/runbooks/worker-runtime.md` §5).
