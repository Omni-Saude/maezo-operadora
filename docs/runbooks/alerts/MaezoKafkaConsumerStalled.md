# Alert Runbook: MaezoKafkaConsumerStalled

**Audience:** Platform engineers, SRE, on-call (ticket, not page)
**Last updated:** 2026-10-31
**Applies to:** every Kafka consumer group seen by the kafka_exporter (`kafka-exporter` scrape job)
**Alert source:** `deploy/observability/alert-rules.yml`, group `maezo_kafka`
**Decision:** R-085, owner decision 2026-10-31 (issue #1084, Rodada 2): "lag Kafka como consumidor
parado".

---

## 1. Trigger

```yaml
- alert: MaezoKafkaConsumerStalled
  expr: |
    (
      sum by (consumergroup, topic, environment) (kafka_consumergroup_lag_sum) > 0
    )
    and on (consumergroup, topic, environment)
    (
      sum by (consumergroup, topic, environment) (increase(kafka_consumergroup_current_offset_sum[15m])) == 0
    )
  labels:
    severity: warning
```

The group has a backlog (`lag > 0`) and its committed offset did not move in the window. This is an
invariant with no business threshold. An absolute lag number would need a freshness objective that
does not exist (CDC/Kafka is outside `docs/observability/SLO.md`). The 15-minute window is
parameter **P7 (TODO-OWNER)**: it must exceed the measured p99 processing time per message of the
slowest consumer, or a long but healthy message would read as a stall.

It replaces the planned absolute-lag alert `MaezoKafkaConsumerLagHigh` (still not shipped; see
`docs/runbooks/devops-stack.md` §"Kafka lag").

Where it has data: the kafka_exporter runs in local compose/CI only. ECS dev has no exporter.

## 2. First 5 minutes

1. Identify the consuming service from `consumergroup`.
2. Check that service's pods/tasks are running and its logs for errors (`[error` in lowercase).
3. A consumer stuck on one poison message: check the matching `.dlq` topic and
   `MaezoDeadLetterBacklog`.
4. Lag growing with a moving offset is NOT this alert (the consumer is slow, not stalled); measure
   it with the PromQL in `devops-stack.md` §"Kafka lag" before changing worker posture.

## 3. Mitigation

Restart the consumer only after reading its last error. If one message blocks the partition, route
it to the DLQ through the service's own path; never skip offsets by hand.
