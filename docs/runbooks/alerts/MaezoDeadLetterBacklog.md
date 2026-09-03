# Alert Runbook: MaezoDeadLetterBacklog

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** Kafka dead-letter topics (messaging layer)
**Alert source:** `deploy/observability/alert-rules.yml:121-135` (group `maezo_dead_letter`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §2, §6 row 6

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

**Code:** `deploy/observability/alert-rules.yml:121-135`

```yaml
- alert: MaezoDeadLetterBacklog
  expr: |
    maezo_dead_letter_queue_size > 0
  for: 10m
  labels:
    severity: warning
    category: dead_letter
    component: messaging
```

**This alert cannot fire, ever, as currently wired.** `maezo_dead_letter_queue_size` is not
defined anywhere in `src/` — `grep -rn "dead_letter_queue_size\|maezo_dead_letter" src/` returns
zero hits. The rule file says so itself, in the comment immediately above it
(`alert-rules.yml:118-120`): *"Note: Uses a synthetic metric — replace with actual Kafka DLQ
metric from the OTel Collector's JMX exporter or Kafka Exporter when available."* There is also
no JMX exporter or Kafka Exporter scrape job in `deploy/observability/prometheus.yml` (its 5 jobs
are `maezo-app`, `maezo-workers`, `otel-collector`, `otel-collector-metrics`, `prometheus` — none
targets Kafka/JMX). With no series for this metric name at all, Prometheus never evaluates the
`> 0` condition — this is a "no data" rule, not a rule that is merely under-tuned. If you are
reading this because you were paged, that page did **not** come from this rule as it exists on
`main`; check the alert name in Alertmanager carefully, and treat as a manual test/silence
exercise or a rule-file change since this runbook was written.

## 2. What this means in business terms — IF a real DLQ metric is wired in the future

A dead-letter backlog means Kafka messages the platform could not process are accumulating
unconsumed — typically domain events published via `operadora.events.publish` (every SP-OP-*
BPMN process's generic event-emission topic, `src/maezo/tools/workers/events.py`) or
delegation facts from the A2A outbox relay (`a2a_fact_outbox`, see §3). Accumulating DLQ messages
mean some business event (a process transition, an agent-to-agent delegation fact) never reached
its consumer — a silent gap in the audit trail's completeness if left unaddressed.

## 3. First 5 minutes

```bash
# 1. Confirm whether ANY DLQ-shaped topic exists and has messages — there is no metric, so this
#    must be checked directly against the broker:

# Local dev — list topics, look for anything DLQ/dead-letter-shaped:
docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --list | grep -iE "dlq|dead"

# Consumer-group lag for a known topic (adapt --group to the consumer you're checking —
# fhir-sync-amh is the one documented working example, docs/runbooks/fhir-sync.md §7):
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server kafka:29092 \
  --describe --all-groups

# Staging/prod (Kubernetes/MSK) — no Kafka CLI in application images; use a debug pod
# (pattern from docs/runbooks/fhir-sync.md §7):
BOOTSTRAP=$(kubectl get secret maezo-kafka-config -n maezo-{tenant} \
  -o jsonpath='{.data.bootstrap-servers}' | base64 -d)
kubectl run kafka-debug --rm -i --restart=Never -n maezo-{tenant} \
  --image=confluentinc/cp-kafka:7.7.0 --quiet -- \
  kafka-topics --bootstrap-server "$BOOTSTRAP" --list

# 2. Check the A2A transactional outbox — a plausible real backlog source even without a
#    Kafka-native DLQ metric (delegation facts stuck un-delivered):
psql "$DATABASE_URL" -c \
  "SELECT status, count(*) FROM a2a_fact_outbox GROUP BY status;"
psql "$DATABASE_URL" -c \
  "SELECT tenant, topic, attempts, last_error, created_at FROM a2a_fact_outbox \
   WHERE status IN ('pending','claimed') AND created_at < now() - interval '10 minutes' \
   ORDER BY created_at LIMIT 20;"

# 3. Check worker-daemon / relay logs for publish failures:
kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -iE "publish.*fail|dlq|dead.letter" | tail -30
```

## 4. Triage tree

```
Did Alertmanager actually show this alert firing (not a manual test)?
├── Given §1, this should be structurally impossible on `main` as audited — re-verify the rule
│   file and metric wiring have not changed before proceeding further.
└── If a real backlog is suspected via a DIFFERENT signal (support report, a2a_fact_outbox query
    from §3 showing stuck rows, or manual Kafka topic inspection):
    ├── a2a_fact_outbox rows stuck in 'pending'/'claimed' with growing `attempts` →
    │   relay-side publish failure — check `last_error` column values, cross-reference against
    │   Kafka broker health.
    └── Kafka broker itself unreachable/unhealthy → docker compose logs kafka / kubectl logs
        for the broker; this blocks ALL publish-side workers, not just one topic.
```

## 5. Mitigation

- **No in-repo automated DLQ replay/purge tooling exists** — `alert-rules.yml`'s own description
  text ("Investigate message payloads and consumer health... Topics affected may need manual
  replay or purge") describes an operator action this repo does not provide tooling for. Do not
  invent an ad hoc script during an incident; escalate per §6 for a broker-level manual
  intervention if a real backlog is confirmed via §3.
- **a2a_fact_outbox stuck rows:** the relay (`src/maezo/a2a/outbox_relay.py`) owns claim/retry —
  do not manually `UPDATE` the table's `status` column; that bypasses the claim-lease invariant
  (`ck_a2a_fact_outbox_claim_lease`, `migrations/versions/0008_a2a_fact_outbox.py:174-177`) and
  can cause a double-delivery. Restart the relay process instead if it appears stuck.
- **Root-cause fix (owner decision, tracked in `docs/observability/SLO.md` §2):** wire a real
  Kafka Exporter or JMX exporter scrape target so this alert has a metric to evaluate — a
  `deploy/` change, owner-gated (slice D12-01-b), not an on-call action.

## 6. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing/on-call roster exists (see
[`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md) §6). If a real backlog is
confirmed via §3 despite the alert itself being non-functional, page the platform/SRE rotation
owning the messaging layer; if `a2a_fact_outbox` rows are affected, also notify whoever owns A2A
delegation flows (see [`a2a-key-rotation.md`](../a2a-key-rotation.md) for that area's contacts).

## 7. False-alarm checks

- **Default assumption:** any firing of this exact alert is suspicious given §1 — verify the rule
  file and metric definitions have not changed before treating it as real.
- A `kafka-topics --list` showing zero DLQ-shaped topics is expected — this repo has never wired
  a real DLQ topic or exporter (confirmed absence, not a check that can fail silently).
