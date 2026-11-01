# Runbook: DevOps Stack — Local Dev + Staging Promotion

**Audience:** Platform engineers, SRE, on-call  
**Last updated:** 2026-06-12  
**Applies to:** Maezo Healthcare Plan, all environments

---

## Table of Contents

1. [Local development stack](#1-local-development-stack)
2. [Terraform workflow](#2-terraform-workflow)
3. [Helm / Kubernetes deployment](#3-helm--kubernetes-deployment)
4. [Staging promotion flow](#4-staging-promotion-flow)
5. [Observability access](#5-observability-access)
6. [Alert runbooks](#6-alert-runbooks)

---

## 1. Local development stack

### Prerequisites

- Docker Desktop 4.x+, `docker compose` v2
- Python 3.12, `make`

### Starting the core stack

```bash
cp .env.example .env          # fill in LLM_GENERAL_API_KEY at minimum
make dev-stack                # equivalent to: docker compose --profile core up -d
```

Services started:
| Service | URL | Purpose |
|---|---|---|
| PostgreSQL | localhost:5433 | Agent state, memory, audit |
| CIB Seven | localhost:8080 | BPMN governance engine |
| HAPI FHIR R4 | localhost:8081 | FHIR R4 canonical store |
| Kafka | localhost:9092 | CDC + agent events |

All services have healthchecks. Wait for `docker compose ps` to show all as `healthy` before running tests.

### Starting the observability stack

```bash
docker compose --profile observability up -d
# Access:
#   Grafana:    http://localhost:3000  (admin / maezo-dev)
#   Prometheus: http://localhost:9090
#   OTel:       localhost:4317 (gRPC), localhost:4318 (HTTP)
```

> **D12-02 / R-030 (2026-09-05): dashboard auto-provisioning now works in local dev.**
> `deploy/observability/grafana-provisioning/dashboards/dashboards.yaml` is a Grafana file
> provider whose `path` is `/etc/grafana/provisioning/dashboards/json` — the SECOND mount
> `docker-compose.yml` declares on the `grafana` service
> (`./deploy/observability/dashboards:/etc/grafana/provisioning/dashboards/json:ro`, kept
> deliberately separate from the first mount so the dashboard JSON never shadows
> `dashboards.yaml` itself). `deploy/observability/grafana-provisioning/datasources/
> datasources.yaml` provisions the `Prometheus` datasource (`uid: prometheus`) every panel
> references. The three versioned dashboards — `worker-runtime.json`, `agentes.json`,
> `dlq-lifecycle.json` (`deploy/observability/dashboards/`) — should appear under the "Maezo"
> folder automatically on `docker compose --profile observability up -d`; no manual import
> step is needed any more. Content is provisional until `D12-01-a` (SLO document) exists — see
> that gap. `tests/unit/deploy/test_grafana_dashboards.py` fences the provisioning wiring and
> every panel's PromQL against the measured metric inventory.

### Running everything

```bash
docker compose --profile core --profile observability up -d
```

### Tearing down

```bash
docker compose --profile core --profile observability down    # keep volumes
docker compose --profile core --profile observability down -v # destroy volumes
```

### Common local issues

| Symptom | Cause | Fix |
|---|---|---|
| CIB Seven takes >90s | JVM cold start | Wait; healthcheck retries 10x at 20s |
| HAPI FHIR not ready | DB migrations running | Wait for `/fhir/metadata` to return 200 |
| Kafka port conflict | Another Kafka on 9092 | `docker ps` to find it; stop it |

---

## 2. Terraform workflow

### Structure

```
deploy/terraform/
  modules/
    aurora-postgres/   # Aurora PostgreSQL 16 + per-tenant CMK
    ecr/               # ECR repo maezo-agent with lifecycle policy
    eks-cluster/       # EKS (create or reference existing; default=reference)
    github-oidc/       # OIDC trust + scoped IAM roles; mirrors amh-data-platform ADR-022
    secrets/           # Secrets Manager shells (BLOCKED items) + MSK data source
  envs/
    staging-sa-east-1/  # References existing amh-data-platform EKS + VPC
    prod-amh-sa-east-1/ # Dedicated EKS cluster per ADR-0004 isolation
```

### What is owned vs referenced

| Resource | Owned by Maezo | Referenced from amh-data-platform |
|---|---|---|
| EKS cluster | Optional (create_cluster=true) | Yes (default) |
| VPC + subnets | No | Yes (data sources by tag) |
| MSK Serverless | No | Yes (Secrets Manager data source: `debezium/msk-bootstrap-servers`) |
| S3 lake | No | Their bucket |
| Aurora PostgreSQL 16 | **Yes** | No |
| ECR repo | **Yes** | No |
| GitHub OIDC roles | **Yes** (scoped) | OIDC provider singleton referenced |
| Secrets Manager secrets | **Yes** (shells) | No |
| KMS CMKs | **Yes** (per-tenant) | No |

### First apply (staging)

```bash
cd deploy/terraform/envs/staging-sa-east-1

# Configure backend (shared S3 state bucket from amh-data-platform bootstrap)
terraform init \
  -backend-config="bucket=amh-terraform-state-sa-east-1" \
  -backend-config="key=maezo/staging-sa-east-1/terraform.tfstate" \
  -backend-config="region=sa-east-1"

cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — fill in aws_account_id, owner_email, eks_cluster_name

terraform plan
terraform apply
```

### GitHub Actions integration

CI runs `terraform validate` on all modules and envs (no backend, no credentials needed). Apply is performed by the `maezo-github-deploy-{env}` OIDC role on push to main or `workflow_dispatch`.

**Do not store AWS credentials in GitHub Actions secrets.** OIDC only.

### Blocked secrets

Three secrets are shell-only (name/ARN exist; value is empty) pending credential delivery:
- `maezo/{env}/whatsapp/waba-token` — WABA onboarding
- `maezo/{env}/llm/api-keys` — LLM contract
- `maezo/{env}/tasy/oracle` — Tasy integration agreement

When credentials are ready: populate via AWS Console or `aws secretsmanager put-secret-value` — never via Terraform.

---

## 3. Helm / Kubernetes deployment

### Chart: `deploy/helm/maezo-tenant`

Per-tenant chart. Each tenant gets its own namespace (`maezo-{tenant_id}`).

> **DB-7 — first install / DR reinstall is TWO-PHASE.** The `alembic` migrations run as a Helm
> `pre-install,pre-upgrade` hook (`job-migrations.yaml`, weight `-5`) that mounts the Aurora DSN via
> `secretKeyRef`. That Secret is materialised by the ESO **ExternalSecret** `aurora-master-credentials`
> — a *normal* (non-hook) resource, which Helm applies **after** all pre-install hooks. So on a genuine
> first install the Secret does not exist when the hook pod starts → `CreateContainerConfigError` → the
> pod never reaches Running (`backoffLimit` cannot consume a pod that never ran) → the Job trips
> `activeDeadlineSeconds` → `helm --atomic` **rolls the whole tenant back**. Install the secret plumbing
> **first**, wait for ESO to sync, *then* run the full release. (An `await-db-secret` init container on
> the Job also polls the Secret as defense-in-depth, but it cannot conjure a Secret whose ExternalSecret
> has not been applied yet — hence this ordering. On a routine **upgrade** the Secret already exists, so
> both phases are effectively instant. ESO running cluster-wide is a §6.2 prerequisite — see below.)

```bash
# First install / DR reinstall (staging, AMH tenant) — TWO-PHASE (DB-7)

# Phase 1 — bootstrap the ESO secret plumbing with the migrations hook DISABLED so it
# does not fire before the Aurora Secret exists.
helm upgrade --install maezo-amh deploy/helm/maezo-tenant/ \
  -f deploy/helm/maezo-tenant/values-amh.yaml \
  --namespace maezo-amh --create-namespace \
  --set migrations.enabled=false

# Block until ESO composes + syncs the Aurora DSN Secret. The ExternalSecret object is
# ALWAYS named aurora-master-credentials (only its TARGET Secret name varies per tenant).
kubectl wait --for=condition=Ready \
  externalsecret/aurora-master-credentials \
  -n maezo-amh --timeout=180s

# Phase 2 — full atomic release; the migrations hook now finds the synced Secret.
helm upgrade --install maezo-amh deploy/helm/maezo-tenant/ \
  -f deploy/helm/maezo-tenant/values-amh.yaml \
  --namespace maezo-amh \
  --atomic --timeout 10m --wait

# Check rollout — one Deployment PER AGENT (agent-{name}); there is no single
# "agent-runtime" resource. `.Values.agents` in the values file is the source of
# truth (deployment-agent-runtime.yaml renders `name: agent-{{ $agent.name }}` per
# enabled entry). values-amh.yaml currently enables helena, rafael, marina
# (gustavo/lucas are enabled:false stubs — add them here when flipped on).
for a in helena rafael marina; do
  kubectl rollout status deployment/agent-$a -n maezo-amh
done
kubectl rollout status deployment/webhook-receiver -n maezo-amh
kubectl rollout status deployment/worker-daemon -n maezo-amh
kubectl rollout status deployment/fhir-sync -n maezo-amh
# NOTE: there is NO standalone gateway Deployment to check — `gateway.enabled` is
# false (the PEP/pseudonymizer/audit gateway is an in-process library imported by
# the runtime pods, ADR-0006; the orphaned Deployment was disabled — no runnable
# `maezo.gateway` module exists).
```

> On **upgrades** (Secret already present from a prior sync) a single
> `helm upgrade --install … --atomic --wait` is sufficient — the pre-sequence above is only
> load-bearing for a first install or a DR reinstall into an empty namespace. The CD pipeline
> (`.github/workflows/cd.yml`) always runs both phases so first-install and upgrade share one path.

### NetworkPolicy zones (ADR-0006)

Two zones are enforced via Kubernetes NetworkPolicy:

**General Zone**:
- Applies to: the general-zone `agent-{name}` Deployments (helena today; gustavo/lucas when enabled — `securityZone: general` in values), webhook-receiver, worker-daemon
- Egress: named LLM endpoints (Anthropic) + MSK + intra-namespace + DNS
- PHI is pseudonymized before leaving the gateway (in-process library in these pods)

**PHI Zone**:
- Applies to: the PHI-zone `agent-{name}` Deployments (rafael, marina — `securityZone: phi`, pod label `maezo.io/phi-zone: "true"`) and fhir-sync (pod label `maezo.io/phi-zone: "phi"`)
- Egress: ONLY to explicitly named BR-resident endpoints
- Default: all egress denied until `networkPolicy.phiZone.brResidentEndpoints` is configured
- Adding an endpoint requires compliance review and PR approval from CODEOWNERS

### External Secrets Operator

ESO must be installed in the cluster before deploying this chart:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets-operator \
  --create-namespace
```

BLOCKED ExternalSecrets (whatsapp, llm, tasy) will stay in `SecretSyncedError` state until the Secrets Manager secret values are populated. This is expected and does not block other services.

---

## 4. Staging promotion flow

```
feature branch → PR → CI green (lint, type, unit, artifact-validation, terraform-validate, helm-lint) 
  → code review → merge to main
  → CI on main → integration tests → auto-deploy to staging (GitHub Actions + OIDC)
  → staging smoke tests → manual promotion approval
  → prod deploy via workflow_dispatch
```

### Promoting a prompt or model change

Per ADR-0009, prompt/model changes must pass the eval gate:

1. Open PR with changes to `src/maezo/agents/` or `src/maezo/runtime/`
2. CI `evals` job runs automatically (path filter)
3. If eval score < 0.70 → CI fails → do not merge
4. Nightly eval (03:00 BRT) catches regressions on main

---

## 5. Observability access

### Dev (local)

- Grafana: http://localhost:3000 (admin / maezo-dev)
- Prometheus: http://localhost:9090
- Dashboards pre-loaded from `deploy/observability/dashboards/`

### Staging / Prod

Access via Amazon Managed Grafana (AMG) — SSO via AWS IAM Identity Center.  
Prometheus datasource: Amazon Managed Prometheus (AMP) workspace.  
Traces: AWS X-Ray (configured via ADOT in `config/otel-collector.yaml`).

Dashboard IDs:
| Dashboard | UID |
|---|---|
| Helena employee | `maezo-helena-employee` |
| Gateway PEP/audit | `maezo-gateway` |
| Engine (CIB Seven) | `maezo-engine` |

### Metric catalog

The canonical metric catalog lives in `src/maezo/runtime/metrics.py` (ADR-0010).
It is the **single source of truth** for all metric names, label schemas, and types.

| Domain | Metric prefix | Emitter service (future PR) |
|---|---|---|
| Agent runtime | `maezo_conversation_*`, `maezo_escalation_*`, `maezo_response_*`, `maezo_eval_*`, `maezo_hitl_*`, `maezo_llm_*` | each `agent-{name}` Deployment (port 8000) |
| Gateway / PEP | `maezo_gateway_pep_*`, `maezo_gateway_pseudonymizer_*`, `maezo_gateway_audit_*` | `maezo_gateway_pep_decision_total` **IMPLEMENTED** (R-085, 2026-10-30 — `gateway.seams._base.gate`); `maezo_gateway_pseudonymizer_*` and `maezo_gateway_audit_*` **NOT YET IMPLEMENTED** (RUNBOOK-PHANTOM-METRICS, 2026-09-03). Would be emitted **in-process** by the runtime pods that import the `maezo.gateway` library (each `agent-{name}`, worker-daemon) — scraped from those pods' `/metrics` (port 8000); there is no standalone gateway service |
| Engine / BPMN | `cibseven_process_*`, `cibseven_user_task_*` | `webhook` / engine worker (port 8002) |

Scrape targets are configured in `config/prometheus.yml`.  All labels are bounded
and PHI-free per ADR-0010 (no conversation_id, beneficiario, CPF in labels).
See `tests/unit/runtime/test_metrics.py` for the stability contract test suite.

---

## 6. Alert runbooks

> Commands below use two placeholders: `{tenant}` (tenant id, e.g. `amh`) and
> `{agent}` (the agent name from the alert's `agent` label — one of `helena`,
> `rafael`, `marina`, `gustavo`, `lucas`). Each agent runs as its own Deployment
> named `agent-{agent}`; there is no single shared `agent-runtime` resource
> (`values.yaml` `.Values.agents` is the source of truth).

### DLQ alert

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoDLQRateHigh` rule exists in
`deploy/observability/alert-rules.yml` (RUNBOOK-PHANTOM-ALERTS-5-MORE, verified 2026-09-03:
`grep -n 'alert: MaezoDLQRateHigh' deploy/observability/alert-rules.yml` — 0 hits). The rule
belongs in `deploy/observability/alert-rules.yml` (owner-gated). The closest shipped rules today
are `MaezoDeadLetterBacklog` (`maezo_dead_letter_queue_size > 0` for 10m, warning) and
`MaezoDeadLetterGrowth` (`rate(maezo_dead_letter_queue_size[5m]) > 5`, critical) — same family,
different name/threshold, and both read a metric the rules file's own comment calls synthetic
pending a real Kafka DLQ exporter (`alert-rules.yml:118-120`). Meanwhile, operators should treat
`MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` as the live signal for this condition, and use
the steps below as manual triage guidance once either fires.  
**Alert:** `MaezoDLQRateHigh`  
**Meaning:** A Kafka Dead Letter Queue is accumulating messages (failed processing).  
**Steps:**
1. `kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep -i '\[error'`
   (structlog renderiza o nível em MINÚSCULAS, entre colchetes — `[error    ]`; `grep ERROR`
   nunca casou. O token de nível é emitido por `structlog.processors.add_log_level`, pinado por
   `tests/unit/platform/test_observability_bootstrap.py::test_an_error_line_carries_its_severity_so_level_triage_works`.)
2. Check CIB Seven for failed external tasks: `GET /engine-rest/external-task?errorMessageLike=%25`
3. Re-process DLQ: replay from the DLQ topic using the kafka-consumer CLI in the pod

### Kafka lag

**Status:** PLANNED — NOT YET IMPLEMENTED as an ALERT. No `MaezoKafkaConsumerLagHigh` rule exists
in `deploy/observability/alert-rules.yml` (verified 2026-09-05: `grep -n 'alert:
MaezoKafkaConsumerLagHigh' deploy/observability/alert-rules.yml` — 0 hits). The rule (and any
dashboard) belongs there and remains owner-gated / D12-02 scope — not added by this note.

**R-085 (owner decision 2026-10-31, issue #1084):** lag is now alerted as a STALLED CONSUMER, not
as an absolute count: `MaezoKafkaConsumerStalled` (group `maezo_kafka`) fires when a group has lag
AND its committed offset did not advance in the window (no business threshold; window is a
TODO-OWNER parameter). Runbook: [`alerts/MaezoKafkaConsumerStalled.md`](alerts/MaezoKafkaConsumerStalled.md).
The absolute-lag `MaezoKafkaConsumerLagHigh` below stays PLANNED.

**Alert:** `MaezoKafkaConsumerStalled` — IMPLEMENTADO (R-085). Backlog with zero progress for the
window; severity `warning` (ticket).

**Drift correction (GAP D4-03, round-5, 2026-09-05):** this section previously claimed "no
consumer-lag metric is scraped anywhere in this repo today" (RUNBOOK-PHANTOM-ALERTS-5-MORE,
verified 2026-09-03). That is now STALE: `deploy/observability/prometheus.yml` job
`kafka-exporter` (added by #327 / ALERTS-WITHOUT-METRICS-b, R-056, 2026-09-04) scrapes
danielqsj/kafka_exporter v1.7.0 (`docker-compose.yml` service `kafka-exporter`, no
`--group.filter` set — every consumer group on the broker is discovered and exported), which
exposes **`kafka_consumergroup_lag{consumergroup,topic,partition}`** and
**`kafka_consumergroup_lag_sum{consumergroup,topic}`** (summed across partitions) alongside the
`kafka_topic_partition_current_offset` series the `maezo_dead_letter_derived` recording-rule
group already reads. No `record:`/`alert:` rule reads either lag series yet — this note only
disclaims the "0 scraped anywhere" claim, it does not wire an alert.

**Measurement procedure — consumer lag per topic (run BEFORE any worker-runtime posture change,
per D4-03's own recommendation):**

```promql
# Instant lag, per consumer group + topic (already pre-summed across partitions):
kafka_consumergroup_lag_sum{consumergroup="<group>"}

# Same reading, derived from the per-partition series (equivalent; use if you need the
# per-partition breakdown too):
sum by (consumergroup, topic) (kafka_consumergroup_lag{consumergroup="<group>"})

# Trend over the last 30 minutes, to distinguish a transient blip from sustained starvation
# before touching src/maezo/runtime/worker_runtime/settings.py's lock/poll/task-count posture:
avg_over_time(kafka_consumergroup_lag_sum{consumergroup="<group>"}[30m])
```

Local/dev: Prometheus at `http://localhost:9090` (`make dev-observability`), or query the
exporter directly: `curl -s http://localhost:9308/metrics | grep kafka_consumergroup_lag_sum`
(exporter port per `docker-compose.yml`'s `kafka-exporter` service). Kubernetes: the same PromQL
against the in-cluster Prometheus/AMP, once the `kafka-exporter` scrape job is deployed there too
(today it is dev/CI-only — no `deploy/helm/**kafka-exporter**` template exists; extending it to a
tenant cluster is a separate, undone piece of work).

**Decision note (D4-03):** `worker_runtime/settings.py`'s posture (`WORKER_LOCK_DURATION_MS=
30_000`, `WORKER_POLL_INTERVAL_MS=5_000`, `WORKER_MAX_TASKS_PER_POLL=10`) is UNCHANGED by this
gap, deliberately. The register's own concern — "99+ topicos num unico worker-daemon" starving a
cold topic behind a busy one — is a hypothesis about production-scale topic fan-out that this
repo's dev/CI stack (a handful of topics, one worker replica) cannot exercise realistically. The
measurement above is now POSSIBLE (it was not, before the kafka-exporter wiring); it has not been
RUN against a representative topic count, so there is no `kafka_consumergroup_lag_sum` reading
yet to justify a specific new value for any of the three knobs. Changing worker posture without
that reading would be exactly the "guess, then hope" `_positive`/`_validate_long_poll_fits_
client_timeout` validators in `settings.py` already refuse to accept for internally-inconsistent
values — this note is the same discipline applied to cross-topic fairness, which those validators
cannot check.  
**Alert:** `MaezoKafkaConsumerLagHigh`  
**Meaning:** Event processing is behind; agents may receive delayed CDC events.  
**Steps:**
1. Check the affected agent's pod count: `kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=agent-{agent}`
2. If pod count < desired: `kubectl describe deployment agent-{agent} -n maezo-{tenant}`
3. Scale up if resource-bound: `kubectl scale deployment agent-{agent} -n maezo-{tenant} --replicas=N`

### PEP deny spike

**Status:** IMPLEMENTADO (R-085 / F-090, 2026-10-30) — `deploy/observability/alert-rules.yml`
(grupo `maezo_governanca_agentes`), sobre `maezo_gateway_pep_decision_total{tenant,principal,
decision,layer,reason,enforced}`, emitido por `gateway.seams._base.gate` (um incremento por decisao
do PEP de efeitos). Le SO recusas `enforced="true"`: enquanto o manifesto de aprovacoes estiver
DRAFT (toda classe nega em sombra), o alerta nao tem o que disparar — por desenho. Runbook
dedicado: [`alerts/MaezoPEPDenySpike.md`](alerts/MaezoPEPDenySpike.md). O PEP e uma biblioteca
in-process: `maezo_gateway_pep_*` e raspado do `/metrics` dos pods de runtime.  
**Alert:** `MaezoPEPDenySpike`  
**Meaning:** The Policy Enforcement Point is denying > 1 req/s — attack, misconfiguration, or policy regression.  
**Steps:**
1. Check the PEP audit log — the gateway/PEP runs in-process inside the runtime
   pods (no standalone gateway Deployment): `for a in helena rafael marina; do kubectl logs -n maezo-{tenant} deploy/agent-$a | grep pep_deny; done; kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep pep_deny`
2. Identify which action is being denied (action_id in log)
3. If regression: revert the last autonomy policy change via `git revert` + PR
4. If attack: consider enabling rate limiting at the webhook-receiver

### Audit lag

**Status:** **REMOVED** by owner decision (R-085, issue #1084, 2026-10-31: "MaezoAuditLagHigh:
REMOVED"). There is no lag to measure in the current design (R-085/R-102 reconciliation,
2026-10-31). No `MaezoAuditLagHigh` rule exists in
`deploy/observability/alert-rules.yml`, and no audit-lag metric is emitted anywhere in `src/`. The
audit trail is not consumed asynchronously: `PostgresAuditSink` (`src/maezo/gateway/audit_postgres.py`)
writes each record synchronously, inside the caller's path, and fails closed (ADR-0007,
ADR-0027 Postgres-first). There is no Kafka audit consumer, so "consumer lag" has no source. The
owner decided to keep it REMOVED; do not re-add it without a new decision.  
**Alert:** `MaezoAuditLagHigh`  
**Meaning (as originally planned):** the ADR-0007 non-repudiation guarantee (every gateway PEP
decision is durably audited) is at risk. In the synchronous design that risk shows up as a failed
write, not as lag.  
**What operators check instead:**
1. Audit write failures: `AuditPersistenceError` (`src/maezo/gateway/audit_postgres.py`) is raised to
   the caller on any durability failure — grep the runtime pods' logs for it:
   `kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep AuditPersistenceError` (and
   `deploy/worker-daemon`). The action that needed the audit row did not proceed (fail-closed).
2. Chain integrity: `verify_chain` (`src/maezo/gateway/audit_postgres.py`, async, per `tenant_id`)
   re-verifies the hash chain of the tenant's audit trail.
3. Any persistent write failure or chain break is a compliance-relevant gap — open an incident and
   notify the compliance/audit team (ADR-0007).

### Service down

**Status:** PLANNED — NOT YET IMPLEMENTED. Neither `MaezoAgentRuntimeDown` nor `MaezoFhirSyncDown`
exists in `deploy/observability/alert-rules.yml` — there is no `up{job=...}`-based rule at all
today (RUNBOOK-PHANTOM-ALERTS-5-MORE, verified 2026-09-03: `grep -n 'alert: MaezoAgentRuntimeDown\|alert:
MaezoFhirSyncDown' deploy/observability/alert-rules.yml` — 0 hits). The rule belongs in
`deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should watch
`up{job=...}` directly in Prometheus/Grafana, or run `kubectl get pods` for the affected
component, rather than rely on an alert.  
**Alert:** `MaezoAgentRuntimeDown` / `MaezoFhirSyncDown`  
**Meaning:** Prometheus has been unable to scrape the named job for > 1 minute — the
service is unreachable (crashed, crash-looping, network-partitioned, or never started).  
**Steps:**
1. Check pod status: `kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component={agent-runtime|fhir-sync}`
2. If `CrashLoopBackOff`: `kubectl logs -n maezo-{tenant} <pod> --previous` to see the failure before the last restart (see also `MaezoPodCrashLooping`, [Crash loop](#crash-loop))
3. If `Pending`/`ImagePullBackOff`: `kubectl describe pod -n maezo-{tenant} <pod>` for scheduling/image errors
4. `MaezoAgentRuntimeDown` is critical — all agent conversations fail while down. `MaezoFhirSyncDown` is critical — FHIR reads serve stale data while down.
5. Once healthy, confirm recovery: `up{job="maezo-agent-runtime"}` / `up{job="maezo-fhir-sync"}` back to 1

### Escalation rate

**Status:** IMPLEMENTADO (R-085 / F-090, 2026-10-30) — `deploy/observability/alert-rules.yml`
(grupo `maezo_governanca_agentes`). Runbook dedicado:
[`alerts/MaezoEscalationRateHigh.md`](alerts/MaezoEscalationRateHigh.md).  
**Alert:** `MaezoEscalationRateHigh`  
**Meaning:** An agent is escalating > 30% of its conversations to a human for a given
tenant over a 1-hour window.  
**Context:** Built on `maezo_agent_desfecho_total` (CC-09; desfecho `escalado_humano` over every
desfecho of the agent, `sum by (agent_id)` on both sides, `increase(...[1h])`). The
`maezo_escalation_total` / `maezo_conversation_total` pair this section used to describe never
existed and was NOT created: the CC-09 counter already carries the same fact. Applies to the
agents whose closed vocabulary declares `escalado_humano` (Helena, Lucas). **R-085 (owner
2026-10-31):** aggregated `by (agent_id, environment)`, confirmed by a short 5m window at the same
30%, and guarded by a minimum volume on the 1h window (short window and volume are TODO-OWNER
parameters P5/P6 in `alert-rules.yml`). **Limit:** no tenant label (needs a scrape-time tenant
label); it measures turns (desfechos), not conversations.  
**Steps:**
1. Identify the agent and environment from the alert's `agent_id`/`environment` labels (no tenant label — see the Limit above; find the tenant from the Prometheus/AMP workspace the alert came from)
2. Check recent eval scores for the agent — see [Eval score drop](#eval-score-drop)
3. Check recent prompt/model/config changes: `git log --oneline -- src/maezo/agents/{agent}/`
4. Sample a handful of the escalated conversations in the audit trail for a common failure pattern
5. If caused by a regression: roll back the change. If it reflects a genuine shift in case mix, treat as expected and adjust the baseline.

### SLA breach

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoUserTaskSLABreach` rule exists in
`deploy/observability/alert-rules.yml`, and no User-Task-SLA metric is emitted anywhere in
`src/maezo/` today (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03: `grep -n 'alert:
MaezoUserTaskSLABreach' deploy/observability/alert-rules.yml` — 0 hits; the full declared-metric
list in `src/maezo/runtime/metrics.py` carries no User-Task/SLA series). The rule belongs in
`deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should track RN259
deadlines via the CIB Seven Tasklist UI/cockpit directly (see [HITL pending](#hitl-pending)) —
there is no automated compliance alert for this today.  
**Alert:** `MaezoUserTaskSLABreach`  
**Meaning:** A User Task in CIB Seven exceeded its RN259 deadline. Compliance event.  
**Steps:**
1. This is a **compliance event** — notify the medical audit team immediately
2. Access CIB Seven cockpit and identify the overdue task
3. Escalate to human auditor manually
4. Open a compliance incident ticket
5. Root-cause: was it a system failure or a process design issue?

### Eval score drop

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoEvalScoreDrop` rule exists in
`deploy/observability/alert-rules.yml` (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03:
`grep -n 'alert: MaezoEvalScoreDrop' deploy/observability/alert-rules.yml` — 0 hits), nor is a
golden-eval-score metric emitted to Prometheus by anything in `src/maezo/` — the nightly eval
artifact this section's steps reference lives in GitHub Actions, not the metrics pipeline. The
rule belongs in `deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should
check the nightly eval artifact in GitHub Actions directly (step 1 below) rather than rely on an
alert.  
**Alert:** `MaezoEvalScoreDrop`  
**Meaning:** An agent's golden dataset score has fallen below 0.70.  
**Steps:**
1. Check the nightly eval artifact in GitHub Actions
2. Compare with the last passing run to identify which test cases regressed
3. Review recent `src/maezo/agents/{agent}/` changes
4. If caused by a model change: roll back `runtime/inference/settings.py` to the last approved model config
5. Do not promote any changes until eval score recovers above 0.85

### A2A budget

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoA2ATaskBudgetExceeded` rule exists in
`deploy/observability/alert-rules.yml`, and no A2A-delegation-spend metric is emitted anywhere in
`src/maezo/` — nor does `maezo_llm_cost_usd_total` (named in step 6 below) exist; only
`maezo_llm_tokens_total` (token COUNTS, not cost) is a real, emitted metric
(RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03: `grep -n 'alert:
MaezoA2ATaskBudgetExceeded' deploy/observability/alert-rules.yml` — 0 hits; `grep -rn
'maezo_llm_cost_usd_total\|a2a.*budget' src/maezo/` — 0 hits). The rule belongs in
`deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should watch
`maezo_llm_tokens_total` (rate, by agent+model) as an imperfect proxy for spend, and grep
worker/agent logs for delegation-chain depth per the steps below.  
**Alert:** `MaezoA2ATaskBudgetExceeded`  
**Meaning:** An agent's A2A (agent-to-agent) delegation is spending > $0.10/min, indicating a runaway task loop.  
**Context:** A2A budget is $0.50 per conversation window (configurable in `inference_routing.yaml`). The alert fires when the spend rate exceeds $6/hr on a single agent+model_id.

**First checks:**
1. Identify the agent and model: check `{{ $labels.agent }}` and `{{ $labels.model_id }}` from the alert
2. Check active delegations: `kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep -E "(a2a_delegation|chain_depth)" | tail -20`
3. Look for infinite loops: grep for tasks with excessive tool invocations in the last 10 min

**Remediation:**
1. **Halt the agent immediately:** `kubectl scale deployment agent-{agent} -n maezo-{tenant} --replicas=0` (temporary)
2. Review the A2A delegation chain: check `src/maezo/agents/{agent}/a2a_routing.yaml` for misconfigured target agents
3. Increase `max_chain_depth` guard or add cost ceiling checks in the delegation handler
4. Review recent tool or prompt changes that may have introduced the loop
5. After fix, scale back up: `kubectl scale deployment agent-{agent} -n maezo-{tenant} --replicas=N`
6. Monitor `maezo_llm_cost_usd_total` over the next hour to confirm normal spend

**Prevention:**
- Add cost bounds to every A2A task definition in the autonomy policy
- Log delegation chain depth and abort if depth exceeds safe threshold
- Run periodic delegation graph validation in CI

### A2A budget exhausted

**Status:** IMPLEMENTADO — REGRA E EMISSOR EXISTEM, EVENTO INALCANCAVEL HOJE (R-085, 2026-10-30) —
nenhum handler de producao subdelega, entao a guarda nunca recusa dentro do dispatcher; o "budget
A2A" de ADR-0010 em custo (USD) continua NAO IMPLEMENTADO. Regra em
`deploy/observability/alert-rules.yml` (grupo `maezo_governanca_agentes`). ADR-0010 exige o alerta "budget A2A estourado"; a guarda e a
3 de ADR-0003 (`a2a.delegation.Budget.charge`). Le
`maezo_a2a_guard_refusal_total{target,guard="budget_exhausted"}`, emitido pelo ramo
`DelegationError` de `a2a.dispatcher.DelegationDispatcher._finish_handler`. Qualquer recusa na
janela dispara (`> 0`): o ADR nomeia o evento, nao uma taxa. Distinto do
`MaezoA2ATaskBudgetExceeded` acima (gasto em USD), que continua PLANEJADO por falta de metrica
de custo. Runbook dedicado: [`alerts/MaezoA2ABudgetExhausted.md`](alerts/MaezoA2ABudgetExhausted.md).  
**Alert:** `MaezoA2ABudgetExhausted`  
**Meaning:** A delegation chain ran out of its per-chain budget and a sub-delegation was rejected.  

### HITL approval drop

**Status:** IMPLEMENTADO, DARK (R-085, owner decision 2026-10-31, issue #1084 Rodada 2) — group
`maezo_hitl_l1` in `deploy/observability/alert-rules.yml`, over `maezo_l1_decision_total{tenant,
agent_id,action_class,decisao}`, emitted ONLY by the engine-history poller
(`src/maezo/runtime/worker_runtime/engine_metrics.py`, flag `MAEZO_L1_DECISION_POLLER`, default
off). Without the poller there is no series and the alert has nothing to evaluate. Runbook:
[`alerts/MaezoHITLApprovalRateDrop.md`](alerts/MaezoHITLApprovalRateDrop.md).  
**Alert:** `MaezoHITLApprovalRateDrop` — re-added with a real emitter. History: it was **REMOVED**
(predeploy obs-wiring) because it divided by `maezo_hitl_approved_total`, which had no emitter; the
old 50% / 10m / presented > 0.1/min parameters had no source and are NOT reused.  
**Meaning:** for one `(tenant, agent_id, action_class)`, the share of L1 decisions that executed the
action over the last 7 days fell more than k binomial standard errors below its own 28-day
baseline, with a minimum volume. Windows, k and the minimum volume are TODO-OWNER parameters
(P8–P10). Severity `warning` (ticket): quality/drift, not user impact.  
**Context:** the L1 dossiers do not recommend, so this measures case mix and dossier quality, not
human-agent agreement. The decision is read from engine history, so every channel is seen
(Tasklist today; the portal/D6 relay later — issue #1145). Never emit it a second time at the
relay or the portal.

### HITL pending

**Status:** PLANNED — NOT YET IMPLEMENTED. Neither `MaezoHITLPendingTooLong` nor
`MaezoBpmnUserTaskDecisionStalled` exists in `deploy/observability/alert-rules.yml`, and neither
metric named below (`cibseven_user_task_pending_total`, `maezo_bpmn_usertask_decision_total`) is
emitted anywhere in `src/maezo/` today (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03:
`grep -n 'alert: MaezoHITLPendingTooLong\|alert: MaezoBpmnUserTaskDecisionStalled'
deploy/observability/alert-rules.yml` and `grep -rn 'cibseven_user_task_pending_total\|
maezo_bpmn_usertask_decision_total' src/maezo/` both 0 hits). Both rules belong in
`deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should check the CIB
Seven cockpit/Tasklist UI directly per the steps below — there is no automated queue-age alert
today.  
**Alert:** `MaezoHITLPendingTooLong` or `MaezoBpmnUserTaskDecisionStalled`  
**Meaning:** Human tasks have been pending in the queue for > 30 minutes (SLA breach risk).  
**Context:** This alert has two related triggers:
- `MaezoHITLPendingTooLong`: age-based (holds for 30+ min)
- `MaezoBpmnUserTaskDecisionStalled`: stall-based (no auditor decisions in 1h despite backlog)

**Metrics involved:**
- `cibseven_user_task_pending_total`: gauge of pending tasks per candidate_group (human queue)
- `maezo_bpmn_usertask_decision_total`: counter of completed auditor decisions
- RN259 SLA: P1 resolution must complete within 10 business days; P2/P3 within 20/30 days

**First checks:**
1. Access CIB Seven cockpit: `http://{cockpit-url}/cockpit/default/dashboard`
2. Filter tasks by `candidate_group` (from alert label)
3. Check task age and priority: identify if any are P1 (highest urgency)
4. Verify the engine is running: there is no `auditor-console` (or any `console`)
   Deployment in this chart — `deploy/helm/maezo-tenant/templates/` defines no such
   resource. No candidato `e9689e7d`, humanos completam User Tasks diretamente pela UI Tasklist
   do CIB Seven, apoiada pelo StatefulSet `cibseven`: `kubectl get statefulset cibseven -n
   maezo-{tenant}`. A transição prospectiva para portal próprio pertence ao pacote separado
   ADR-0049/DL-0048, ainda pendente de aterrissagem e verificação; esta referência não a ratifica.

**If tasks are old but the engine is healthy:**
1. Check operator shift schedule — verify on-call auditor is scheduled
2. Restart CIB Seven if hung: `kubectl rollout restart statefulset/cibseven -n maezo-{tenant}`
3. Note: CIB Seven runs as a single-replica StatefulSet (`statefulset-cibseven.yaml`) —
   there is no auditor-specific replica count to scale independently of the engine

**If the engine is down or tasks are accumulating:**
1. Check engine logs: `kubectl logs -n maezo-{tenant} statefulset/cibseven | tail -50`
2. Verify database connectivity: CIB Seven's `CIBSEVEN_DATABASE_URL` (from its own
   Secret, see `statefulset-cibseven.yaml`) points at the shared Aurora Postgres —
   check via `kubectl exec -n maezo-{tenant} -it statefulset/cibseven -- sh -c 'echo $CIBSEVEN_DATABASE_URL'`
   then test connectivity from any agent pod (e.g. `agent-helena`), which shares network access to Aurora
3. Escalate to the CIB Seven support team if DB is unreachable
4. Notify medical audit lead: SLA breach is imminent

**Prevention:**
- Monitor CIB Seven engine health in CI/CD: add a daily task-aging report to the metrics dashboard
- Configure Slack alerts for P1 queue backlog > 2 tasks
- Ensure 24/7 auditor coverage during business hours

### LLM cost spike

**Status:** PLANNED — NOT YET IMPLEMENTED. Neither `MaezoLLMCostSpike` nor
`MaezoLLMTokenRateHigh` exists in `deploy/observability/alert-rules.yml`
(RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03: `grep -n 'alert: MaezoLLMCostSpike\|
alert: MaezoLLMTokenRateHigh' deploy/observability/alert-rules.yml` — 0 hits). Of the two metrics
named below, only `maezo_llm_tokens_total` is real and emitted (`src/maezo/runtime/metrics.py:119`,
called from `src/maezo/runtime/inference/providers.py`'s `_emit_llm_token_usage`);
`maezo_llm_cost_usd_total` does not exist anywhere in
`src/maezo/` (`grep -rn maezo_llm_cost_usd_total src/maezo/` — 0 hits) — there is no USD-cost
series today, only token counts. Both rules belong in `deploy/observability/alert-rules.yml`
(owner-gated). Meanwhile, operators can watch `maezo_llm_tokens_total` (rate, by agent+model) in
Grafana as the only real signal; cost tracking requires deriving spend from token counts and the
provider's published per-token price out of band.  
**Alert:** `MaezoLLMCostSpike` or `MaezoLLMTokenRateHigh`  
**Meaning:** LLM token consumption or spend rate has exceeded budgeted thresholds.  
**Context:** 
- `MaezoLLMCostSpike`: hourly spend > $5.00 per tenant (configurable in `values-amh.yaml`)
- `MaezoLLMTokenRateHigh`: token rate > 50k tokens/min (early warning before cost alert)

**Metrics involved:**
- `maezo_llm_cost_usd_total`: counter of cumulative LLM spend (labels: agent, tenant, model_id)
- `maezo_llm_tokens_total`: counter of cumulative tokens (labels: agent, tenant, model_id, token_type)

**First checks:**
1. Identify which agent and model: check `{{ $labels.agent }}` and `{{ $labels.model_id }}`
2. Check recent traffic: `kubectl top nodes -n maezo-{tenant}` and `kubectl top pods -n maezo-{tenant}`
3. Review model config: `cat src/maezo/runtime/inference_routing.yaml | grep -A5 {{ $labels.model_id }}`

**If cost spike is sudden (not correlated with traffic):**
1. Check for prompt bloat: did a recent agent prompt update add extra context/examples?
2. Verify model tier hasn't been accidentally upgraded in a PR (check `git log --oneline -p src/maezo/runtime/inference/settings.py`)
3. Check if frontier model (e.g., Claude 3.5 Opus) was enabled unintentionally in the routing rules

**If token rate is high due to legitimate traffic:**
1. Assess if traffic surge is expected (e.g., scheduled batch job, marketing campaign launch)
2. Consider dynamic scaling: add cost-aware rate limiting to the agent or scale down replicas
3. Negotiate higher budget or model downgrade if sustained

**Cost recovery:**
1. Roll back recent model/prompt changes if they caused the spike
2. Temporarily disable expensive features (e.g., long-context retrieval) to reduce token usage
3. Review the inference routing weights: favor cheaper models (e.g., Haiku) for routine tasks
4. Update the cost budget in `values-amh.yaml` and open a ticket for procurement

### Trace sampler stalled

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoTraceSamplerStalled` rule exists in
`deploy/observability/alert-rules.yml` (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03:
`grep -n 'alert: MaezoTraceSamplerStalled' deploy/observability/alert-rules.yml` — 0 hits).
`otelcol_exporter_sent_spans` (named below) is the OTel Collector's own self-instrumentation
metric, not something `src/maezo/` emits — it exists once the collector is deployed and scraped,
independent of this rule. The rule belongs in `deploy/observability/alert-rules.yml`
(owner-gated). Meanwhile, operators should check the collector directly per the steps below.  
**Alert:** `MaezoTraceSamplerStalled`  
**Meaning:** The OpenTelemetry collector has exported zero spans for > 5 minutes, indicating the trace pipeline is broken.  
**Context:** 
- OTel sampler config: `OTEL_TRACES_SAMPLER=parentbased_traceidratio` (10% head-sampling by default)
- Retention: 7 days (dev, local Prometheus) / 30 days (prod, Amazon Managed Prometheus)
- A zero export rate during business hours suggests collector restart loop, pipeline misconfiguration, or PHI filter bug
- **Namespace:** in staging/prod the collector is a shared cluster resource in the
  **`observability`** namespace, NOT the per-tenant `maezo-{tenant}` namespace — the
  chart's own OTel endpoint config confirms this:
  `deploy/helm/maezo-tenant/values-amh.yaml` sets
  `observability.otel.endpoint: http://otel-collector.observability.svc.cluster.local:4317`.
  (`configmap-otel-collector.yaml` in this chart only ships the collector's *config*
  into the tenant namespace for reference/sync — the collector Deployment itself is
  provisioned outside this chart, same "referenced not owned" pattern as the shared
  EKS cluster/VPC.) All `kubectl` commands below target `-n observability`. In local
  dev (docker-compose) there is no Kubernetes namespace at all — use
  `docker compose logs otel-collector` instead.

**Metrics involved:**
- `otelcol_exporter_sent_spans`: counter emitted by the OTel collector (scraped from localhost:8888)

**First checks:**
1. Check OTel collector status: `kubectl get deployment otel-collector -n observability`
2. Check for restart loops: `kubectl get events -n observability | grep -i otel`
3. Review collector logs: `kubectl logs -n observability deploy/otel-collector | tail -50`

**If collector is restarting:**
1. Check memory limits: `kubectl describe deployment otel-collector -n observability | grep -A5 resources`
2. Increase memory if OOM: update `deploy/helm/maezo-tenant/values-amh.yaml` → `otelCollector.resources.limits.memory`
3. Check disk space on the node: `kubectl describe node $(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')`

**If collector is running but exports are zero:**
1. Check the phi_safety_net processor config: `kubectl get configmap otel-collector-config -n observability -o yaml | grep -A10 phi_safety`
2. Verify the processor isn't dropping all spans due to overly aggressive filtering
3. Check if the exporter is correctly configured: `kubectl logs -n observability deploy/otel-collector | grep -i "exporter\|otlp"`

**If the exporter endpoint is unreachable:**
1. Verify AMP (Amazon Managed Prometheus) workspace is online: check the AWS console
2. Verify ADOT credentials and ROLE_ARN are correct in the pod spec
3. Test connectivity from the collector pod: `kubectl exec -n observability deploy/otel-collector -- curl -v https://aps-workspaces.us-east-1.amazonaws.com/...`

**Recovery:**
1. Restart the collector: `kubectl rollout restart deployment otel-collector -n observability`
2. Monitor the exporter rate: `kubectl logs -n observability deploy/otel-collector -f | grep sent_spans`
3. Verify traces are appearing in the Grafana trace datasource within 2 minutes

### Collector drop rate

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoCollectorDropRateHigh` rule exists in
`deploy/observability/alert-rules.yml` (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified
2026-09-03: `grep -n 'alert: MaezoCollectorDropRateHigh' deploy/observability/alert-rules.yml` —
0 hits). `otelcol_processor_dropped_spans` (named below), like the trace-sampler metric above, is
the OTel Collector's own self-instrumentation series, not something `src/maezo/` emits. The rule
belongs in `deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should
check collector memory/drop metrics directly per the steps below.  
**Alert:** `MaezoCollectorDropRateHigh`  
**Meaning:** The OpenTelemetry collector is dropping > 10 spans/second at the processor layer (memory pressure).  
**Context:** Span drops are typically triggered by the `memory_limiter` processor when the collector reaches its memory ceiling. A high drop rate indicates the trace volume exceeds the collector's capacity. Same namespace caveat as above — the collector runs in `observability`, not `maezo-{tenant}`.

**Metrics involved:**
- `otelcol_processor_dropped_spans`: counter emitted by the OTel collector (processor layer)

**First checks:**
1. Check collector memory usage: `kubectl top pod -n observability -l app=otel-collector`
2. Check the current memory limit: `kubectl get pod -n observability -l app=otel-collector -o yaml | grep -A2 resources`
3. Review the memory_limiter config: `kubectl get configmap otel-collector-config -n observability -o yaml | grep -A10 memory_limiter`

**If collector is at memory limit:**
1. Increase the memory limit in `deploy/helm/maezo-tenant/values-amh.yaml`:
   ```yaml
   otelCollector:
     resources:
       limits:
         memory: 512Mi  # increase from 256Mi
   ```
2. Redeploy: `helm upgrade maezo-{tenant} deploy/helm/maezo-tenant -f values-amh.yaml`

**If memory increase doesn't help:**
1. Lower the sampling rate: `OTEL_TRACES_SAMPLER_ARG=0.05` (5% instead of 10%)
2. Disable expensive trace processors (e.g., full span attribution) temporarily
3. Add span filtering to drop low-priority traces (e.g., health checks) at the receiver level

**Long-term:**
1. Analyze trace volume growth: are there new instrumentation points or increased conversation volume?
2. Implement tail-sampling: use the `tail_sampling` processor to keep only traces matching high-priority patterns (e.g., errors, high-latency requests)
3. Increase OTLP batch size in the exporter to reduce export overhead

### pgvector scale

**Status:** **REMOVED** — e sem objeto. Estes dois alertas nunca existiram em
`deploy/observability/alert-rules.yml` e a metrica `maezo_memory_rowcount` nunca existiu em
`src/maezo/` (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verificado 2026-09-03). Desde 2026-09-04 nao ha
mais nem o que medir: **DU-01-b** (decisao do dono R-005) removeu a camada semantica —
`0009_drop_pgvector` dropou `agent_memory.embedding vector(1536)` e a extensao `vector`, o
`docker-compose.yml` passou para a imagem `postgres:16` e o parameter group do Aurora perdeu o
token `pgvector`. Nao ha indice ivfflat/HNSW neste repositorio e nunca houve
(`grep -rn 'ivfflat|hnsw' src/maezo` -> 0 hits), entao o teto de ~5M embeddings do ADR-0002
tambem ficou sem sujeito.

A secao nao e' apagada de proposito: um operador que encontre a mencao ao teto em ADR-0002
§Negativas precisa achar aqui o registro de que ela caducou. Se ADR-0002 §3 for des-suspenso um
dia — ato exclusivo do dono, ver a emenda DRAFT em
`docs/adr/0047-emenda-adr0002-secao3-camada-semantica-suspensa.md` — o procedimento abaixo volta a
valer, e os dois alertas precisam entao ser ESCRITOS em `deploy/observability/alert-rules.yml`
(owner-gated) junto com a metrica que os alimentaria.  
**Alert:** `MaezoMemoryRowcountApproachingCeiling` or `MaezoMemoryRowcountCritical`  
**Meaning:** `agent_memory` estaria se aproximando do teto de escala do indice vetorial —
condicao que nao pode ocorrer enquanto a camada semantica estiver suspensa.  
**Metrics involved:** nenhuma. `maezo_memory_rowcount` nunca foi emitida, e
`src/maezo/tools/mcp_memory/server.py` nao escreve em `agent_memory` (as duas ferramentas recusam
fail-closed — GAP-DU-01-a).

**First checks (a camada EPISODICA continua existindo; o que sumiu foi a coluna vetorial):**
1. Contagem de linhas (o escopo e' o tenant, nao o agente — qualquer pod de agente tem
   conectividade; `agent-helena` como exemplo representativo):
   `kubectl exec -n maezo-{tenant} -it deploy/agent-helena -- psql -U maezo -d maezo -c "SELECT COUNT(*) FROM agent_memory;"`
2. Indices existentes: `kubectl exec -n maezo-{tenant} -it deploy/agent-helena -- psql -U maezo -d maezo -c "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE tablename='agent_memory';"`
3. Confirmar que a coluna vetorial de fato saiu:
   `psql -c "SELECT count(*) FROM information_schema.columns WHERE table_name='agent_memory' AND column_name='embedding';"` (esperado: 0)

**Procedimento historico de escala do indice vetorial (INAPLICAVEL hoje — nao ha indice).**
Preservado porque e' o conteudo que teria de ser reexecutado se a camada voltar:
- dimensionamento: `lists = ceil(sqrt(row_count))` (5M linhas -> 224; 10M -> 316), com o indice
  originalmente previsto para ~5M linhas em `lists=100`;
- 80% do teto (4M linhas): planejar re-index na proxima janela de manutencao;
- 90% do teto (4,5M linhas): mitigar com `SET ivfflat.probes = 2` e re-index de emergencia via
  `ALTER INDEX ... SET (lists = ...)` + `REINDEX INDEX CONCURRENTLY` (nao bloqueia leitura/escrita),
  acompanhando `pg_stat_progress_create_index`;
- estrategia de longo prazo: teto pratico de 3M linhas por tenant, HNSW como alternativa de maior
  recall e maior custo de build, TTL/arquivamento de memoria antiga e particionamento por tenant.

### Crash loop

**Status:** PLANNED — NOT YET IMPLEMENTED as a GENERAL (every-container) rule. No
`MaezoPodCrashLooping` rule exists in `deploy/observability/alert-rules.yml`. Since R-085 (owner
decision 2026-10-31, "remover/rebasear os 2 falsos crash loop") the two shipped crash-loop rules
`MaezoWorkerCrashLoop`/`MaezoAgentCrashLoop` read exactly this signal —
`increase(kube_pod_container_status_restarts_total[15m]) >= 3` — for the `worker-daemon` and
`agent-runtime` containers only. For any other container, watch
`kube_pod_container_status_restarts_total` directly.  
**Alert:** `MaezoPodCrashLooping`  
**Meaning:** A container has restarted ≥ 3 times in the last 15 minutes — the standard
CrashLoopBackOff signature. This is the general-purpose detector for the "fails on
every start" class of bug (a `command` pointing at a non-runnable module, an unhandled
startup exception, a fail-closed missing-secret/config error) that would otherwise be
invisible until someone happened to run `kubectl get pods`.  
**Context:** Relies on `kube_pod_container_status_restarts_total`, the standard
Kubernetes container-restart counter from kube-state-metrics — assumed cluster-provided
(same assumption `podmonitor.yaml` already makes for prometheus-operator; both ship
together in kube-prometheus-stack on tenant clusters that run the operator).

**Steps:**
1. Identify the pod/container from the alert labels (`{{ $labels.namespace }}/{{ $labels.pod }}/{{ $labels.container }}`)
2. Get the failure reason from before the last restart: `kubectl logs -n {namespace} {pod} -c {container} --previous`
3. Common causes seen on this platform:
   - A Deployment `command` pointing at a package with no runnable `__main__.py`
   - A fail-closed startup error (e.g. a required secret/env var missing, such as `HmacKeyNotConfiguredError`)
   - An unhandled `pydantic.ValidationError` at settings load
4. Fix the root cause and redeploy — `kubectl delete pod` only resets the restart counter, it does not fix the crash
5. Confirm recovery: the pod's restart count stops climbing and `MaezoAgentRuntimeDown` / `MaezoFhirSyncDown` (if either was co-firing) clears

### Worker task failures

**Status:** PLANNED — NOT YET IMPLEMENTED. Neither `MaezoWorkerTaskFailureRateHigh` nor
`MaezoWorkerTaskBpmnError` exists in `deploy/observability/alert-rules.yml`
(RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03: `grep -n 'alert:
MaezoWorkerTaskFailureRateHigh\|alert: MaezoWorkerTaskBpmnError'
deploy/observability/alert-rules.yml` — 0 hits) — unlike most sections in this document, the
underlying metric `maezo_worker_task_total` (named below) IS real and emitted
(`src/maezo/tools/workers/harness.py:99`, `src/maezo/platform/observability.py:562`); only the
alert RULE is missing, the same shape as the DLQ gap above. The closest shipped rules,
[`MaezoWorkerCrashLoop`](#maezoworkercrashloop)/[`MaezoSLAWorkerErrorRateHigh`](#maezoslaworkererratehigh),
read a DIFFERENT metric (`maezo_worker_error_count_total`/`maezo_worker_execution_time_seconds`,
from `WorkerBase.run()`, not `WorkerHarness._handle`'s dispatch-outcome counter) — related
family, not a substitute; in particular neither reads the `outcome="bpmn_error"` label this
section's `ERR_*_NOT_HUMAN` no-denial-guard check (step 3) depends on. Both rules belong in
`deploy/observability/alert-rules.yml` (owner-gated). Meanwhile, operators should query
`maezo_worker_task_total{outcome="bpmn_error"}` directly in Grafana/Prometheus and grep
worker-daemon logs per the steps below.  
**Alert:** `MaezoWorkerTaskFailureRateHigh` or `MaezoWorkerTaskBpmnError`  
**Meaning:** The external-task worker harness (worker-daemon) is reporting failures or throwing BPMN errors on a topic.  
**Context:**
- `maezo_worker_task_total` (labels: tenant, topic, outcome) counts every dispatch outcome of `WorkerHarness._handle` (GAP-XOBS-4)
- `outcome="failed"`: generic handler failure reported to the engine for retry — a sustained rate means the handler or a dependency is broken
- `outcome="bpmn_error"`: a `WorkerBpmnError` thrown to the engine — **includes ERR_*_NOT_HUMAN no-denial-guard firings** (an automated path attempted an adverse effect without the required human decision, ADR-0005/0008)

**Steps:**
1. Identify the topic and tenant from the alert labels
2. Check worker-daemon logs: `kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep -E "{topic}|WorkerFailure|WorkerBpmnError" | tail -30`
3. For `MaezoWorkerTaskBpmnError`: check the error code. If it matches `ERR_*_NOT_HUMAN`, treat as an **incident** — a no-denial guard fired; find the process instance and audit how an automated value reached a human-only decision variable
4. For ordinary declared business errors (BPMN error boundary flows), confirm the process design expects them at this rate
5. For `failed`: check the handler's dependency (Kafka, engine REST, DB) health; the engine retries per its `retries`/`retryTimeout` config, so fix the dependency and the backlog drains itself
6. Check CIB Seven for stuck external tasks: `GET /engine-rest/external-task?errorMessageLike=%25`

### ANS submission failed

**Status:** PLANNED — NOT YET IMPLEMENTED. No `MaezoAnsSubmissionFailed` rule exists in
`deploy/observability/alert-rules.yml`, and `maezo_ans_submission_outcome_total` (named below,
claimed emitted by the `ans_submit` worker) does not exist anywhere in `src/maezo/`
(RUNBOOK-PHANTOM-ALERTS-REMAINING-14, verified 2026-09-03: `grep -n 'alert:
MaezoAnsSubmissionFailed' deploy/observability/alert-rules.yml` and `grep -rn
maezo_ans_submission_outcome_total src/maezo/` both 0 hits). This is a compliance-relevant gap:
[`MaezoLifecycleJobFailed`](#maezolifecyclejobfailed) is the closest shipped alert in spirit
(also a regulatory/compliance CronJob-failure signal, ADR-0020/ADR-0029) but reads an unrelated
metric (`kube_job_status_failed{job_name=~"lifecycle-.*"}`) and does not cover ANS submission
outcomes. The rule belongs in `deploy/observability/alert-rules.yml` (owner-gated). Meanwhile,
operators should check the ans_submit worker logs and CIB Seven cockpit directly per the steps
below — there is no automated compliance alert for a rejected/late ANS submission today.  
**Alert:** `MaezoAnsSubmissionFailed`  
**Meaning:** A regulatory ANS periodic submission was rejected (`nacked`) or missed its deadline (`late`). Compliance event.  
**Context:** `maezo_ans_submission_outcome_total` (labels: tenant, process_key, outcome) is emitted by the `ans_submit` worker for the SP-OP-ANS-SUBMIT / SP-OP-ANS-CRON cycles (RN124/SIP, RN209, RN388, RN424/TISS, DIOPS).

**Steps:**
1. This is a **compliance-relevant event** — notify the regulatory team
2. Identify the process_key and outcome from the alert labels
3. Check the ans_submit worker logs: `kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep ans_submit | tail -30`
4. For `nacked`: inspect the ANS gateway rejection reason and the submission payload; correct and resubmit within the regulatory window
5. For `late`: identify why the cycle ran late (crashed cron process, stuck User Task, engine downtime) and document the delay for the compliance record
6. Confirm the corresponding SP-OP-ANS-CRON-001-* process instances are healthy in CIB Seven cockpit

### Alertas implementados

As secoes abaixo (esta nota + os alertas dos grupos `maezo_sla`/`maezo_crash_loop`/`maezo_dead_letter`/`maezo_lifecycle`; os de governanca, HITL L1 e Kafka tem secao propria acima) cobrem os alertas REALMENTE existentes em
`deploy/observability/alert-rules.yml` (RUNBOOK-PHANTOM-ALERTS-REMAINING-14, gap reverso: os
alertas *shipped* nao tinham nenhuma secao de runbook). Cada secao e derivada ESTRITAMENTE da
regra (`expr`, `for`, `labels`, `annotations`) — nenhum limiar ou procedimento foi inventado alem
do que a regra e o padrao das secoes acima ja sustentam. `annotations.runbook_url`: NENHUMA das 8
regras carrega esse campo hoje (`grep -n runbook_url deploy/observability/alert-rules.yml` — 0
hits); dito explicitamente em cada secao abaixo em vez de omitido em silencio.

### MaezoSLAWorkerLatencyHigh

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_sla`). Revisado em R-085 (2026-10-31).  
**Alert:** `MaezoSLAWorkerLatencyHigh`  
**Expr (forma):** `histogram_quantile(0.95, sum by (le, worker, topic) (rate(maezo_worker_execution_time_seconds_bucket[5m]))) > 5` **and** volume minimo por (worker, topic) em 5m (P1)
**For:** `5m`
**Labels:** `severity: warning`, `category: sla`, `component: workers`
**Runbook:** [`alerts/MaezoSLAWorkerLatencyHigh.md`](alerts/MaezoSLAWorkerLatencyHigh.md). Expr completa e racional no runbook dedicado; parametros sem fonte marcados TODO-OWNER em `alert-rules.yml`.

**Resposta do operador:**
1. Identificar `worker`/`topic` pelos labels do alerta e checar `kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep {topic}` para latencia de dependencia (engine REST, DB, Kafka).
2. Correlacionar com `MaezoSLAWorkerErrorRateHigh` (mesmo topic) — latencia alta seguida de erro costuma ser a MESMA causa.
3. Se sustentado: investigacao de performance, nao incidente de disponibilidade.

### MaezoSLAAgentErrorRateHigh

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_sla`). Revisado em R-085 (2026-10-31: "erro de agente -> warning, corrigir +Inf/unidades").  
**Alert:** `MaezoSLAAgentErrorRateHigh`  
**Expr (forma):** turnos falhos / (turnos falhos + turnos com desfecho) por `agent` `> 0.01` **and** volume minimo de turnos em 5m (P2) — nunca +Inf, razao em [0, 1]
**For:** `5m`
**Labels:** `severity: warning`, `category: sla`, `component: agents`
**Runbook:** [`alerts/MaezoSLAAgentErrorRateHigh.md`](alerts/MaezoSLAAgentErrorRateHigh.md). Expr completa e racional no runbook dedicado; parametros sem fonte marcados TODO-OWNER em `alert-rules.yml`.

**Resposta do operador:**
1. Identificar o agente pelo label `agent` e checar `kubectl logs -n maezo-{tenant} deploy/agent-{agent} | grep -i '\[error'`.
2. Checar saude do provedor LLM (rate limit, timeout, erro 5xx).
3. Se o container estiver reiniciando, ver [`MaezoAgentCrashLoop`](#maezoagentcrashloop) (reinicio de container, outro sinal).

### MaezoSLAWorkerErrorRateHigh

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_sla`). Revisado em R-085 (2026-10-31: unidades + volume minimo).  
**Alert:** `MaezoSLAWorkerErrorRateHigh`  
**Expr (forma):** `sum by (tenant, topic) (rate(maezo_worker_task_total{outcome=~"failed|incident"}[5m])) / sum by (tenant, topic) (rate(maezo_worker_task_total[5m])) > 0.05` **and** volume minimo de despachos em 5m (P3)
**For:** `5m`
**Labels:** `severity: warning`, `category: sla`, `component: workers`
**Runbook:** [`alerts/MaezoSLAWorkerErrorRateHigh.md`](alerts/MaezoSLAWorkerErrorRateHigh.md). Expr completa e racional no runbook dedicado; parametros sem fonte marcados TODO-OWNER em `alert-rules.yml`.

**Resposta do operador:**
1. Identificar `tenant`/`topic` pelos labels do alerta; `kubectl logs -n maezo-{tenant} deploy/worker-daemon | grep {topic}` para o traceback.
2. `bpmn_error` (recusa desenhada, guarda `ERR_*`) NAO entra no numerador; um `ERR_*_NOT_HUMAN` e incidente proprio — ver [Worker task failures](#worker-task-failures).
3. Se o container tambem reinicia, ver [`MaezoWorkerCrashLoop`](#maezoworkercrashloop).

### MaezoWorkerCrashLoop

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_crash_loop`). REBASEADO em R-085 (2026-10-31): antes era uma taxa de erro (`rate(maezo_worker_error_count_total[5m]) > 0.01`), agora e reinicio de container.  
**Alert:** `MaezoWorkerCrashLoop`  
**Expr:** `sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total{container="worker-daemon"}[15m])) >= 3`
**For:** nenhum
**Labels:** `severity: warning` (P11: promover a critical = dono), `category: crash_loop`, `component: workers`
**Runbook:** [`alerts/MaezoWorkerCrashLoop.md`](alerts/MaezoWorkerCrashLoop.md). Limiar = assinatura CrashLoopBackOff de [Crash loop](#crash-loop). Sem serie no ECS dev (sem kube-state-metrics).

**Resposta do operador:**
1. `kubectl logs -n {namespace} {pod} -c worker-daemon --previous` para o erro anterior ao ultimo restart.
2. `kubectl rollout history deployment/worker-daemon -n {namespace}`; reverter se for regressao.

### MaezoAgentCrashLoop

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_crash_loop`). REBASEADO em R-085 (2026-10-31): antes contava turnos falhos (`sum by (agent) (rate(maezo_agent_errors_total[1m])) > 0`), agora e reinicio de container.  
**Alert:** `MaezoAgentCrashLoop`  
**Expr:** `sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total{container="agent-runtime"}[15m])) >= 3`
**For:** nenhum
**Labels:** `severity: warning` (P11: promover a critical = dono), `category: crash_loop`, `component: agents`
**Runbook:** [`alerts/MaezoAgentCrashLoop.md`](alerts/MaezoAgentCrashLoop.md). Sem serie no ECS dev (sem kube-state-metrics).

**Resposta do operador:**
1. `kubectl logs -n {namespace} {pod} -c agent-runtime --previous` para a excecao antes do ultimo restart.
2. A taxa de turnos falhos (sem reinicio) e `MaezoSLAAgentErrorRateHigh`, nao este alerta.

### MaezoDeadLetterBacklog

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_dead_letter`). Revisado em R-085 (2026-10-31: "DLQ por chegadas novas").  
**Alert:** `MaezoDeadLetterBacklog`  
**Expr:** `sum by (topic, environment) (increase(kafka_topic_partition_current_offset{topic=~".*\\.dlq"}[10m])) > 0`
**For:** nenhum (evento "nao deveria acontecer")
**Labels:** `severity: warning`, `category: dead_letter`, `component: messaging`
**Runbook:** [`alerts/MaezoDeadLetterBacklog.md`](alerts/MaezoDeadLetterBacklog.md). Antes lia `maezo_dead_letter_queue_size > 0` — offset MONOTONICO, que disparava para sempre depois da primeira mensagem. Janela = P4 (TODO-OWNER). So ha serie onde ha kafka_exporter (compose/CI).

**Resposta do operador:**
1. Ver [DLQ alert](#dlq-alert) acima — triagem de logs de erro, external tasks no CIB Seven, replay do topico DLQ.
2. O alerta limpa sozinho uma janela depois da ultima chegada.

### MaezoDeadLetterGrowth

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_dead_letter`).
Mesma metrica sintetica de `MaezoDeadLetterBacklog` acima.  
**Alert:** `MaezoDeadLetterGrowth`  
**Expr:** `rate(maezo_dead_letter_queue_size[5m]) > 5`
**For:** `5m`
**Labels:** `severity: critical`, `category: dead_letter`, `component: messaging`
**Annotations:**
- `summary`: "Dead-letter queue growing rapidly"
- `description`: "Dead-letter queue is growing at {{ $value }}/s. This indicates a systemic consumer failure. Pause producer if necessary and investigate root cause immediately."
- `runbook_url`: ausente na regra.

**Resposta do operador:**
1. Este e `critical`, ao contrario de `MaezoDeadLetterBacklog` (`warning`) — crescimento rapido indica falha SISTEMICA de consumidor, nao so um backlog estatico; priorizar sobre o alerta irmao se ambos dispararem juntos.
2. Considerar pausar o produtor (a regra o sugere explicitamente na `description`) enquanto se investiga a causa raiz do consumidor.
3. Mesma ressalva de metrica sintetica do alerta acima.

### MaezoLifecycleJobFailed

**Status:** IMPLEMENTADO — `deploy/observability/alert-rules.yml` (grupo `maezo_lifecycle`, T2.9).
A propria regra documenta que as tres subcommands (`expurgo-working`/`verify-erasure`/
`audit-retention`) hoje recusam e saem com codigo 78 POR DESIGN (stub fail-closed T2.8) — o
alerta torna essa recusa visivel no MESMO caminho de alerta dos demais, em vez de exigir
`kubectl get jobs` manual. Depende de `kube_job_status_failed`
(kube-state-metrics) — a propria regra registra que NAO ha scrape target configurado em
`prometheus.yml` ainda, entao a regra nao tem serie para avaliar ate esse scrape existir (nao
dispara falso-positivo; so nao tem o que alertar ainda).  
**Alert:** `MaezoLifecycleJobFailed`  
**Expr:** `kube_job_status_failed{job_name=~"lifecycle-.*"} > 0`
**For:** `5m`
**Labels:** `severity: warning`, `category: lifecycle`, `component: lifecycle`
**Annotations:**
- `summary`: "Lifecycle CronJob failed: {{ $labels.job_name }}"
- `description`: "Kubernetes Job {{ $labels.job_name }} (a lifecycle-* CronJob — expurgo-working/verify-erasure/audit-retention) reported a failed status. Every lifecycle subcommand is an intentional fail-closed refusal today (exit 78, T2.8/T2.9) — this confirms the failure is visible beyond `kubectl get jobs`. Check pod logs for the precise blocker (ADR-0020/ADR-0029 for audit-retention; the DPO legal-bases/retention matrix, plus the thread_id->fhir_patient_id mapping gap for verify-erasure, for the other two)."
- `runbook_url`: ausente na regra.

**Resposta do operador:**
1. Identificar `job_name` pelo label do alerta; `kubectl logs -n maezo-{tenant} job/{job_name}` para o motivo exato da recusa fail-closed.
2. A recusa em si e ESPERADA hoje (T2.8) — o alerta confirma visibilidade, nao indica regressao; nao tratar como incidente a menos que o motivo da recusa tenha mudado.
3. Confirmar que o kube-state-metrics scrape target existe em `prometheus.yml` antes de assumir que a ausencia de disparo significa "sem falhas" — a propria regra avisa que pode nao ter serie para avaliar.
