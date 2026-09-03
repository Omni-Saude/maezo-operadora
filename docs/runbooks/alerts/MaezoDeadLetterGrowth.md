# Alert Runbook: MaezoDeadLetterGrowth

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-09-02
**Applies to:** Kafka dead-letter topics (messaging layer)
**Alert source:** `deploy/observability/alert-rules.yml:138-151` (group `maezo_dead_letter`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §2, §6 row 7

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

**Code:** `deploy/observability/alert-rules.yml:138-151`

```yaml
- alert: MaezoDeadLetterGrowth
  expr: |
    rate(maezo_dead_letter_queue_size[5m]) > 5
  for: 5m
  labels:
    severity: critical
    category: dead_letter
    component: messaging
```

Same metric as [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md), so the identical fact
applies: **`maezo_dead_letter_queue_size` is not defined anywhere in `src/`** and no Kafka/JMX
exporter scrape target exists in `deploy/observability/prometheus.yml` — this alert has no series
to evaluate and cannot fire on real traffic. See that runbook's §1 for the full evidence trail
(the same `alert-rules.yml:118-120` comment covers both DLQ rules). The only difference from the
backlog alert is severity (`critical` here vs `warning` there) and threshold shape (a growth
*rate* > 5/s vs a static presence check) — both are equally inert today.

Applying a `rate()` to a `Gauge`-shaped name (`_queue_size`, not `_total`) is itself a modeling
smell worth flagging to whoever eventually wires this metric: `rate()` is designed for
monotonic counters, and a queue-size gauge can legitimately fall (successful drains) as well as
rise, so `rate()` over it does not cleanly mean "growth" the way it would for a `_total` counter.
Not a blocker for this document, but worth the future implementer's attention.

## 2. What this means in business terms

Same as [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md) §2 — this alert's `critical`
label and its "growing rapidly" framing (per the rule's own description text: "This indicates a
systemic consumer failure") signal a more urgent version of the same underlying concern: domain
events or A2A delegation facts failing to reach their consumers, at an accelerating rate rather
than a static backlog.

## 3. First 5 minutes

Identical to [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md) §3 — same absent metric,
same manual verification path (Kafka topic listing, `a2a_fact_outbox` status query, worker-daemon
publish-failure log grep). Repeated here for the "no other tab open" case:

```bash
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server kafka:29092 --describe --all-groups

psql "$DATABASE_URL" -c \
  "SELECT status, count(*) FROM a2a_fact_outbox GROUP BY status;"

kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -iE "publish.*fail|dlq|dead.letter" | tail -30
```

If the rule's description text ("Pause producer if necessary") is ever acted on for a REAL
confirmed backlog: there is no in-repo "pause producer" control (no feature flag or admin
endpoint found for this) — that description text describes an operator action this repo provides
no tooling for. Do not attempt to invent one mid-incident.

## 4. Triage tree

Same as [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md) §4 — the two alerts share one
root metric and therefore one triage path. Treat a genuine `critical`-severity signal (confirmed
via the manual checks in §3, since the metric itself cannot corroborate) as higher urgency: page
immediately rather than investigate first, per §6.

## 5. Mitigation

Same constraints as [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md) §5 — no in-repo
automated replay/purge/pause tooling exists. The root-cause fix (a real Kafka/JMX exporter scrape
target) is the same owner-gated `deploy/` change tracked in `docs/observability/SLO.md` §2.

## 6. Escalation

**DRAFT/verify — dono do serviço.** Same gap as every alert in this set (see
[`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md) §6). Given the `critical` label,
if a real accelerating backlog is confirmed via §3's manual checks, page the platform/SRE
rotation immediately — do not wait on further diagnosis given the metric itself provides none.

## 7. False-alarm checks

- Same default-suspicious posture as [`MaezoDeadLetterBacklog.md`](MaezoDeadLetterBacklog.md) §7
  — any firing of this exact alert on unmodified `alert-rules.yml`/`src/` is unexpected; verify
  the rule file and metric definitions before trusting it as a real signal.
