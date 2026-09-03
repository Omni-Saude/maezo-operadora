# SLO — Maezo Operadora (Worker Runtime, Agent Runtime, Gateway)

**Status:** DRAFT — every SLO target/window/error-budget number below is a PROPOSAL for the
service owner to verify, not an agreed contract. Nothing in this document has been ratified by
an ADR or signed off by a service owner. Where a number is inherited from a generic
Google-SRE-workbook template rather than derived from this codebase's own traffic, that is
stated explicitly next to the number.

**Audience:** Platform engineers, SRE, on-call — companion to
[`docs/runbooks/README.md`](../runbooks/README.md) and the 8 alert runbooks under
[`docs/runbooks/alerts/`](../runbooks/alerts/).

**Last updated:** 2026-09-02

**Scope:** the three workloads the shipped alert rules (`deploy/observability/alert-rules.yml`)
actually cover — worker-runtime, agent-runtime, and the gateway (PEP/audit/pseudonymizer). It
does NOT cover CIB Seven (the BPMN engine itself), Kafka/broker health, or Postgres — those have
no dedicated Maezo-authored metrics today either, but are out of this gap's scope
(GAP-D12-01-a / audit gateway gvr-d12).

**Why this document exists:** `docs/audits/maezo-deep-audit` finding D12-03 — no SLO or
error-budget document existed anywhere in `docs/` before this file, while
`deploy/observability/alert-rules.yml` ships 8 static-threshold alerts with `runbook_url: null`
(0 of 8 annotations set — `grep -c runbook deploy/observability/alert-rules.yml` = 0). Adding
`runbook_url` values is a separate, owner-gated slice (D12-01-b, `deploy/` is out of scope for
this document). This document, plus the 8 runbook files it links to, is what makes that future
`runbook_url` value point at something real.

---

## Table of Contents

1. [How to read this document](#1-how-to-read-this-document)
2. [Metric emitter trace — every metric the 8 alerts reference](#2-metric-emitter-trace--every-metric-the-8-alerts-reference)
3. [Golden signals × workload — SLI definitions](#3-golden-signals--workload--sli-definitions)
4. [Proposed SLOs (DRAFT — verify with the service owner)](#4-proposed-slos-draft--verify-with-the-service-owner)
5. [Burn-rate multiwindow alerting — proposed replacement for the static thresholds](#5-burn-rate-multiwindow-alerting--proposed-replacement-for-the-static-thresholds)
6. [Existing alert → SLI → runbook map](#6-existing-alert--sli--runbook-map)
7. [Threshold origin — what is hardcoded without an owner](#7-threshold-origin--what-is-hardcoded-without-an-owner)

---

## 1. How to read this document

Every SLI below is traced to a real metric name and, where that metric has a production
emitter, the exact file:line that increments/observes it. Three outcomes are possible for each
metric, stated explicitly rather than assumed:

- **LIVE** — a real code path in `src/` calls the Prometheus client on every relevant event.
- **REGISTERED, NEVER INCREMENTED** — the metric object exists in the `CollectorRegistry`
  (so it will show up in `/metrics` as a flat value, usually `0`), but no production code path
  ever calls `.inc()`/`.observe()` on it. A PromQL alert built on such a metric will never fire
  on real traffic — this is different from, and more dangerous than, an absent series, because
  the alert LOOKS wired but is silently blind.
- **DOES NOT EXIST** — no `Counter`/`Histogram`/`Gauge` with that name is defined anywhere in
  `src/`, and (for external metrics like `kube_job_status_failed`) no Prometheus scrape target
  produces it either.

## 2. Metric emitter trace — every metric the 8 alerts reference

| Metric | Defined | Emitted by | Called from | Coverage |
|---|---|---|---|---|
| `maezo_worker_execution_time_seconds` (Histogram, labels `worker,topic`) | `src/maezo/runtime/metrics.py:63-68` | `record_worker_execution()` — `src/maezo/platform/observability.py:211-229` | `WorkerBase.run()` success path — `src/maezo/tools/workers/base.py:203-213` | **PARTIAL.** Only workers wrapped in `WorkerBase`/`FunctionWorker` emit this. Raw `harness.register()` handlers (the `events` module, plus `stratify_risk`/`stop_processing`/`proactive_contact`/`notify_sla_risk` in `programa.py`, and the equivalent raw handlers in `recurso.py`/`lgpd.py`/`escalation.py`) bypass `WorkerBase.run()` entirely and never emit it — documented in-repo at `src/maezo/tools/workers/programa.py:461-470` ("a raw handler bypasses `WorkerBase.run` entirely, so `record_worker_execution`/`record_worker_error`... never fire for these... topics"). |
| `maezo_worker_error_count_total` (Counter, labels `worker,topic,error_type`) | `metrics.py:70-75` | `record_worker_error()` — `observability.py:232-251` | `WorkerBase.run()` failure path — `base.py:226-236`; also `src/maezo/tools/workers/auth.py:774` for guard-refusal codes (`auth.py:318-319`) | **PARTIAL** — same raw-handler gap as above. |
| `maezo_tool_calls_total` (Counter, no labels) | `metrics.py:50-54` | — | — | **REGISTERED, NEVER INCREMENTED.** No `.inc()` call found anywhere in `src/` outside `metrics.py`'s own class body. The only callers of `get_metrics_collector()` (`src/maezo/runtime/agent_runtime/service.py:640`, `src/maezo/gateway/service.py:106`, `src/maezo/runtime/worker_runtime/service.py:849`, `src/maezo/platform/webhooks/whatsapp/app.py:55,87`) use it only to hand `.registry` to the `/metrics` HTTP endpoint — none touches `.tool_calls`. Only exercised directly by `tests/unit/runtime/test_metrics.py:32-33`. |
| `maezo_agent_errors_total` (Counter, no labels) | `metrics.py:56-60` | — | — | **REGISTERED, NEVER INCREMENTED.** Same evidence as `maezo_tool_calls_total` above; test-only exercise at `tests/unit/runtime/test_metrics.py:36`. |
| `maezo_agent_latency_seconds` (Histogram, no labels) | `metrics.py:44-48` | — | — | **REGISTERED, NEVER INCREMENTED.** Not referenced by any of the 8 alerts today, but listed here because it is the third leg of the same dead per-agent metric trio. |
| `maezo_dead_letter_queue_size` | not defined anywhere | — | — | **DOES NOT EXIST.** `grep -rn maezo_dead_letter src/` = 0 hits. `alert-rules.yml:118-120` says so itself: *"Note: Uses a synthetic metric — replace with actual Kafka DLQ metric from the OTel Collector's JMX exporter or Kafka Exporter when available."* No JMX exporter or Kafka Exporter scrape job exists in `deploy/observability/prometheus.yml` (5 jobs: `maezo-app`, `maezo-workers`, `otel-collector`, `otel-collector-metrics`, `prometheus` — none is a Kafka/JMX target). |
| `kube_job_status_failed{job_name=~"lifecycle-.*"}` | external (`kube-state-metrics`) | — | — | **DOES NOT EXIST IN-CLUSTER TODAY.** `alert-rules.yml:165-169` documents the same gap: *"requires a kube-state-metrics scrape target in prometheus.yml (not yet configured)... it does not fire falsely, it simply has nothing to alert on yet."* Confirmed: no `kube-state-metrics` job in `prometheus.yml`. |
| `maezo_worker_task_total` / `maezo_worker_task_duration_seconds` (T1.1, GAP-XOBS-4) | `metrics.py:82-94` | `record_worker_task_outcome()` — `observability.py:254-280`, wrapped defensively by `_emit_worker_task_outcome()` — `src/maezo/tools/workers/harness.py:1098-1122` | `WorkerHarness._handle()` `finally` block, **every** topic regardless of handler shape — `harness.py:1805-1809` | **FULL.** Fires unconditionally for every external-task dispatch (`WorkerBase`-wrapped AND raw handlers alike) — not referenced by any of the 8 shipped alerts, but recommended below (§3, §5) as the basis for worker-runtime SLIs precisely because it has no coverage gap. |
| `maezo_gateway_*`, `maezo_escalation_total`, `maezo_conversation_total`, `maezo_process_sla_breach_total` | — | — | — | **DOES NOT EXIST.** These names appear in `docs/runbooks/devops-stack.md` §6 ("Alert runbooks") and `docs/runbooks/engine-processes.md` §6 as if they were live — `grep -rn "maezo_gateway_\|maezo_escalation_total\|maezo_conversation_total\|maezo_process_sla_breach_total" src/` returns 0 hits, and `grep -rln "prometheus_client\|Counter(\|Histogram(\|Gauge(" src/maezo/gateway/` also returns 0 hits (no Prometheus metric object of any name is defined in the gateway package). This is a **pre-existing documentation-vs-reality gap outside this task's scope** (those two runbook sections describe an aspirational observability surface for alerts — `MaezoPEPDenySpike`, `MaezoAuditLagHigh`, `MaezoAgentRuntimeDown`, `MaezoEscalationRateHigh`, `MaezoUserTaskSLABreach`, `MaezoEvalScoreDrop`, and others — that have no corresponding Prometheus rule in `deploy/observability/alert-rules.yml`, which ships only the 8 alerts this document is about). Flagged here, not fixed here — see §3's gateway row and the report to the orchestrator for a possible follow-up gap. |

**Where the 8 alerts actually reach a human, mechanically.** `deploy/observability/prometheus.yml`
loads `alert-rules.yml` via `rule_files` (dev/local Prometheus, `deploy/observability/` compose
profile `observability`). For staging/prod, ADR-0014 says the *same* YAML is meant to upload to
Amazon Managed Prometheus (AMP) as a `rule_group_namespace` via
`deploy/terraform/modules/observability/main.tf:55-65`. That Terraform resource reads its content
from a variable populated by `file("${path.root}/../../../../deploy/observability/alert-rules.yaml")`
(`variables.tf:49`, and repeated in `main.tf`'s own comments) — note the **`.yaml`** extension.
The real file on disk is `deploy/observability/alert-rules.yml` (**`.yml`**). If that
`file()` call is ever wired into a live `terraform apply` unchanged, it fails to find the file.
This module is marked `BLOCKED: terraform apply requires AWS credentials` in its own header
comment and has not been applied (per the hardening-program handoff notes), so the mismatch has
not yet caused a live failure — but it means the AMP upload path, as currently written, is not
provably reachable from the real rule file. This is a `deploy/`/`terraform/` finding and is
**out of scope for this document to fix** (owner-gated); it is recorded here because it directly
answers "where does this alert actually fire in prod," which no other doc states.

## 3. Golden signals × workload — SLI definitions

Each SLI is marked with the same LIVE / REGISTERED-NEVER-INCREMENTED / DOES-NOT-EXIST vocabulary
from §1. A "DRAFT/verify — dono do serviço" SLO is proposed only where the underlying SLI is
LIVE; where it is not, that is stated as a gap instead of a fabricated number.

### 3.1 Worker runtime (`worker-daemon` / compose `worker-runtime`)

| Signal | SLI | PromQL | Coverage |
|---|---|---|---|
| Latency | p95 external-task handler duration | `histogram_quantile(0.95, sum(rate(maezo_worker_task_duration_seconds_bucket[5m])) by (le, tenant, topic))` | **LIVE** (`maezo_worker_task_total`/`_duration_seconds`, §2) |
| Traffic | dispatch rate | `sum(rate(maezo_worker_task_total[5m])) by (tenant, topic)` | **LIVE** |
| Errors | dispatch outcome error ratio | `sum(rate(maezo_worker_task_total{outcome=~"failed\|incident"}[5m])) by (tenant, topic) / sum(rate(maezo_worker_task_total[5m])) by (tenant, topic)` | **LIVE** — `outcome` is the closed vocabulary `WORKER_TASK_OUTCOMES` = `{completed, bpmn_error, failed, incident}` (`harness.py:99`); `bpmn_error` is deliberately excluded from the error ratio because a modeled BPMN error is a designed business outcome, not a platform fault (ADR-0030) — DRAFT/verify with the service owner whether `bpmn_error` should count toward the budget for specific topics. |
| Saturation | external-task backlog / lock-queue depth | *no metric exists* | **DOES NOT EXIST.** Neither CIB Seven's own external-task queue depth nor the harness's in-flight/fetch-and-lock concurrency is exported as a Prometheus metric. `WorkerHarness` has an internal semaphore (`design §8`) but nothing observes its depth. Flagged as a genuine SLI gap, not proposed with a fabricated number. |

The two currently-shipped alerts for this workload (`MaezoSLAWorkerLatencyHigh`,
`MaezoSLAWorkerErrorRateHigh`, `MaezoWorkerCrashLoop`) are built on
`maezo_worker_execution_time_seconds` / `maezo_worker_error_count_total` instead — the
**PARTIAL**-coverage M11 metrics (§2), not the FULL-coverage T1.1 metrics recommended above. This
is a concrete, owner-decidable recommendation: **migrate the worker-runtime alert basis from the
M11 per-worker metrics to the T1.1 dispatch-outcome metrics**, so raw-handler topics (`events`,
`stratify_risk`, `stop_processing`, `proactive_contact`, `notify_sla_risk`, and the raw handlers
in `recurso`/`lgpd`/`escalation`) are covered too.

### 3.2 Agent runtime (`agent-{helena,rafael,marina,...}`)

| Signal | SLI | PromQL | Coverage |
|---|---|---|---|
| Latency | p95 agent-turn latency | *no metric exists* | **DOES NOT EXIST.** `maezo_agent_latency_seconds` is REGISTERED, NEVER INCREMENTED (§2). The only production signal for turn shape today is the structured log line `agent_turn_completed` (`observability.py:405-438`, counts + a hashed conversation digest, PHI-safe by construction) — a log line, not a metric, and it is not shipped anywhere queryable: the OTel Collector's `logs` pipeline exporter is `debug`-only (`deploy/observability/otel-collector.yaml:71-74`; no Loki/CloudWatch Logs sink is wired — same gap `docs/runbooks/worker-runtime.md` §6 documents for worker-daemon logs). |
| Traffic | agent turns / tool calls per second | *no metric exists* | **DOES NOT EXIST.** `maezo_tool_calls_total` is REGISTERED, NEVER INCREMENTED (§2). |
| Errors | agent error ratio | *no metric exists* | **DOES NOT EXIST.** `maezo_agent_errors_total` is REGISTERED, NEVER INCREMENTED (§2) — this is the metric BOTH `MaezoSLAAgentErrorRateHigh` and `MaezoAgentCrashLoop` are built on. Both alerts are therefore evaluating a numerator (and, for the SLA alert, a denominator: `maezo_tool_calls_total`) that is always `0` in production, which means: `rate(maezo_agent_errors_total[5m])` is always `0` (`MaezoAgentCrashLoop` can never fire), and `0/0` in PromQL yields no sample for `MaezoSLAAgentErrorRateHigh` (the `>` comparison never matches, so it never fires either) — **both alerts are structurally incapable of firing on real agent errors today**, not merely mis-thresholded. |
| Saturation | concurrent conversations / checkpointer load | *no metric exists* | **DOES NOT EXIST.** |

**Recommendation (DRAFT/verify — dono do serviço):** before any burn-rate SLO can be built for
agent-runtime, the owner must decide whether to (a) wire real production callers to
`collector.errors`/`.tool_calls`/`.latency` (the metric objects already exist, only the call
sites are missing), or (b) extend `record_agent_turn` (`observability.py:405`) to also emit a
Prometheus histogram/counter alongside its structured-log line, mirroring how
`record_worker_task_outcome` does both audit-adjacent bookkeeping and metric emission for
workers. Until one of those lands, `MaezoSLAAgentErrorRateHigh` and `MaezoAgentCrashLoop` should
be treated as **non-functional**, not merely under-tuned.

### 3.3 Gateway (in-process PEP/audit/pseudonymizer library — no standalone Deployment)

Per `docs/runbooks/gateway.md` (lines 7-19), `maezo.gateway` is imported and executed **inside**
each `agent-{name}` Deployment and the `worker-daemon` — there is no standalone `gateway`
Deployment/Service in the shipped chart (`gateway.enabled: false`). Its dedicated `/healthz`,
`/readyz`, `/metrics` app (`docs/runbooks/gateway.md:25-26`) is a health-only entrypoint that
adds no gateway-specific business metric of its own.

| Signal | SLI | PromQL | Coverage |
|---|---|---|---|
| Latency | PEP decision latency | *no metric exists* | **DOES NOT EXIST.** |
| Traffic | PEP decisions per second | *no metric exists* | **DOES NOT EXIST.** |
| Errors | PEP deny rate / audit emit failure rate | *no metric exists* | **DOES NOT EXIST.** |
| Saturation | audit consumer lag | *no metric exists* | **DOES NOT EXIST.** |

**This is the largest gap in the document.** `grep -rln "prometheus_client\|Counter(\|Histogram(\|Gauge(" src/maezo/gateway/` returns zero files — no Prometheus metric object of any kind is
defined in the gateway package, LIVE or dead. None of the 8 shipped alerts reference a
gateway-workload signal (all 8 are worker/agent/lifecycle-scoped), so this gap does not affect
today's alert→runbook mapping (§6) — but it means `docs/runbooks/devops-stack.md`'s
"PEP deny spike" / "Audit lag" alert-runbook sections (§6 of that file, `MaezoPEPDenySpike` /
`MaezoAuditLagHigh`) describe alerts that have no corresponding Prometheus rule anywhere in this
repo. Recorded here as an observation for the service owner; not remediated by this document
(out of scope for GAP-D12-01-a, which is about the 8 alerts that DO exist).

## 4. Proposed SLOs (DRAFT — verify with the service owner)

Only proposed where §3 shows a **LIVE** SLI. Windows and targets below follow the Google SRE
Workbook's standard shape (30-day rolling window, error-budget-based multiwindow burn alerts) as
a generic starting template — none of these numbers come from this codebase's own historical
traffic (no dashboard or historical query was run to derive them), so every target is marked
DRAFT/verify.

| Workload | SLI | Proposed SLO target | Window | Error budget | Status |
|---|---|---|---|---|---|
| Worker runtime | % of dispatches with `outcome != failed` (excl. `bpmn_error`) | 99.5% | 30d rolling | 0.5% (≈ 3h 39m/30d) | **DRAFT/verify — dono do serviço.** Inherited from the generic SRE-workbook 99.5% template, not derived from Maezo traffic. |
| Worker runtime | p95 `maezo_worker_task_duration_seconds` | ≤ 5s | 30d rolling | n/a (latency SLO, not ratio) | **DRAFT/verify — dono do serviço.** The 5s figure is carried over from the existing static alert threshold (`alert-rules.yml:22`) for continuity, NOT independently re-derived — see §7 for why that number itself has no documented origin. |

Agent-runtime and gateway SLOs are **not proposed** — proposing a target for a signal with no
LIVE SLI (§3.2, §3.3) would be exactly the "constant score" / fabricated-number anti-pattern this
program's non-negotiables forbid. Those rows stay empty until an owner decision (§3.2's
recommendation) lands a real emitter.

## 5. Burn-rate multiwindow alerting — proposed replacement for the static thresholds

The two LIVE worker-runtime SLIs above support the standard Google SRE Workbook 4-alert
multiwindow burn-rate shape (long window catches slow burn, short window confirms so a single
short spike doesn't page). Shown here as the PromQL shape that would replace
`MaezoSLAWorkerLatencyHigh`/`MaezoSLAWorkerErrorRateHigh`'s static `> 5` / `> 0.05` thresholds —
**not wired into `deploy/observability/alert-rules.yml`** (that file is owner-gated, slice
D12-01-b; this is the template for whoever holds that slice to adopt or reject).

```promql
# Error-budget burn rate for the worker-runtime error-ratio SLI (§3.1, §4).
# budget = 1 - target = 1 - 0.995 = 0.005

# Fast burn (page): consuming the 30d budget in < ~2 days if sustained.
# 14.4x burn rate over 1h, confirmed over 5m — mirrors the SRE Workbook's page-worthy pair.
(
  sum(rate(maezo_worker_task_total{outcome=~"failed|incident"}[1h])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_total[1h])) by (tenant, topic)
) > (14.4 * 0.005)
and
(
  sum(rate(maezo_worker_task_total{outcome=~"failed|incident"}[5m])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_total[5m])) by (tenant, topic)
) > (14.4 * 0.005)

# Slow burn (ticket, not page): 6x burn rate over 6h, confirmed over 30m.
(
  sum(rate(maezo_worker_task_total{outcome=~"failed|incident"}[6h])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_total[6h])) by (tenant, topic)
) > (6 * 0.005)
and
(
  sum(rate(maezo_worker_task_total{outcome=~"failed|incident"}[30m])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_total[30m])) by (tenant, topic)
) > (6 * 0.005)
```

```promql
# Latency-SLO burn proxy for maezo_worker_task_duration_seconds (§3.1, §4): fraction of
# requests in a window exceeding the 5s target, evaluated the same multiwindow way.
(
  sum(rate(maezo_worker_task_duration_seconds_bucket{le="5"}[1h])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_duration_seconds_count[1h])) by (tenant, topic)
) < 0.995
and
(
  sum(rate(maezo_worker_task_duration_seconds_bucket{le="5"}[5m])) by (tenant, topic)
  /
  sum(rate(maezo_worker_task_duration_seconds_count[5m])) by (tenant, topic)
) < 0.995
```

The `14.4` / `6` burn-rate multipliers and the `1h+5m` / `6h+30m` window pairs are the SRE
Workbook's own reference constants for a 99.5%-class SLO — not re-derived from Maezo's traffic
volume (no capacity/QPS baseline exists to validate them against). **DRAFT/verify — dono do
serviço** before adoption; a service with materially different traffic shape may need different
multipliers.

## 6. Existing alert → SLI → runbook map

| # | Alert (`alert-rules.yml:line`) | Metric | SLI (§3) | Coverage | Runbook |
|---|---|---|---|---|---|
| 1 | `MaezoSLAWorkerLatencyHigh` (:18) | `maezo_worker_execution_time_seconds` | Worker runtime — Latency | PARTIAL | [`alerts/MaezoSLAWorkerLatencyHigh.md`](../runbooks/alerts/MaezoSLAWorkerLatencyHigh.md) |
| 2 | `MaezoSLAAgentErrorRateHigh` (:36) | `maezo_agent_errors_total` / `maezo_tool_calls_total` | Agent runtime — Errors | **NONE (dead metrics — cannot fire)** | [`alerts/MaezoSLAAgentErrorRateHigh.md`](../runbooks/alerts/MaezoSLAAgentErrorRateHigh.md) |
| 3 | `MaezoSLAWorkerErrorRateHigh` (:56) | `maezo_worker_error_count_total` / `maezo_worker_execution_time_seconds_count` | Worker runtime — Errors | PARTIAL | [`alerts/MaezoSLAWorkerErrorRateHigh.md`](../runbooks/alerts/MaezoSLAWorkerErrorRateHigh.md) |
| 4 | `MaezoWorkerCrashLoop` (:81) | `maezo_worker_error_count_total` | Worker runtime — Errors (crash-loop framing) | PARTIAL | [`alerts/MaezoWorkerCrashLoop.md`](../runbooks/alerts/MaezoWorkerCrashLoop.md) |
| 5 | `MaezoAgentCrashLoop` (:98) | `maezo_agent_errors_total` | Agent runtime — Errors (crash-loop framing) | **NONE (dead metric — cannot fire)** | [`alerts/MaezoAgentCrashLoop.md`](../runbooks/alerts/MaezoAgentCrashLoop.md) |
| 6 | `MaezoDeadLetterBacklog` (:121) | `maezo_dead_letter_queue_size` | — (no workload defined yet) | **NONE (metric does not exist)** | [`alerts/MaezoDeadLetterBacklog.md`](../runbooks/alerts/MaezoDeadLetterBacklog.md) |
| 7 | `MaezoDeadLetterGrowth` (:138) | `maezo_dead_letter_queue_size` | — | **NONE (metric does not exist)** | [`alerts/MaezoDeadLetterGrowth.md`](../runbooks/alerts/MaezoDeadLetterGrowth.md) |
| 8 | `MaezoLifecycleJobFailed` (:171) | `kube_job_status_failed` | — (K8s lifecycle CronJobs, not a golden-signal workload) | **NONE (scrape target not configured)** | [`alerts/MaezoLifecycleJobFailed.md`](../runbooks/alerts/MaezoLifecycleJobFailed.md) |

5 of 8 alerts have no functioning metric behind them today (dead counters, a synthetic metric
with no emitter, or an unconfigured scrape target). This is the honest state as of this
document's writing — it is *why* D12-03 (no SLO document) and D12-01 (no runbook links) compound
into a real operational risk: an on-call engineer paged by one of these 5 has no working signal
to look at even before reaching a runbook.

## 7. Threshold origin — what is hardcoded without an owner

The audit (gateway `gvr-d12`) flagged the p95 5s / 1% error thresholds as hardcoded without a
documented owner. Reproduced here:

- **p95 > 5s** (`MaezoSLAWorkerLatencyHigh`, `alert-rules.yml:22`: `histogram_quantile(0.95, ...) > 5`) —
  the comment at `alert-rules.yml:17` labels it "Worker latency SLA: p95 > 5s for 5 minutes" with
  no citation. `grep -n "5s\|p95" docs/adr/0010-agent-observability.md docs/adr/0014-observability-platform-amp-amg.md`
  returns **zero** hits in either ADR — neither document that motivated the observability stack
  specifies this number. No other `docs/adr/*.md` file was found (broader repo search) defining
  it either. **Origin: unattributed constant in the rule file itself. DRAFT/verify — dono do
  serviço.**
- **> 1% agent error rate** (`MaezoSLAAgentErrorRateHigh`, `alert-rules.yml:42`: `> 0.01`) — same
  pattern: the comment at `alert-rules.yml:35` says "Agent error rate SLA: > 1% error rate
  sustained for 5 minutes" with no citation, and no ADR defines it. It is also, per §2/§3.2,
  built on a metric pair that is never incremented in production — so this threshold cannot even
  be exercised against real data today. **Origin: unattributed constant. DRAFT/verify — dono do
  serviço.**
- **> 5% worker error rate** (`MaezoSLAWorkerErrorRateHigh`, `alert-rules.yml:62`: `> 0.05`) — same
  pattern, same absence of ADR citation. **Origin: unattributed constant. DRAFT/verify — dono do
  serviço.**
- **> 3 restarts / 5 min → crash-loop*** — the `MaezoWorkerCrashLoop` comment (`alert-rules.yml:80`)
  says "> 3 restarts in 5 minutes" but the actual `expr` (`alert-rules.yml:83`) is
  `rate(maezo_worker_error_count_total[5m]) > 0.01` — an error-rate threshold, not a restart
  count; the comment and the implementation do not match. Neither number is ADR-sourced.
  **DRAFT/verify — dono do serviço**, and the comment/expr mismatch itself is worth a follow-up
  fix in the (owner-gated) `deploy/` file.
- **DLQ > 0 for 10m / growth > 5/min** (`MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth`,
  `alert-rules.yml:123,140`) — moot until `maezo_dead_letter_queue_size` exists (§2); the
  thresholds themselves are unattributed round numbers with no ADR citation either.
- **`kube_job_status_failed > 0` for 5m** (`MaezoLifecycleJobFailed`, `alert-rules.yml:173`) — this
  one is NOT an arbitrary tuning choice: every lifecycle subcommand refuses by design (exit 78,
  T2.8 fail-closed stub — see `docs/runbooks/alerts/MaezoLifecycleJobFailed.md`), so `> 0` is the
  only sensible threshold once the scrape target exists. No owner decision needed on the number
  itself, only on wiring the kube-state-metrics scrape target.
