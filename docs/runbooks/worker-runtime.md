# Runbook: Worker Runtime — start/stop/health, `RUNTIME_MODE`, local engine bring-up

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-08-11
**Applies to:** Maezo Healthcare Plan, all environments

> **Naming note (read this first).** The same binary is called two different things in two
> different places: the Kubernetes/Helm resource is named **`worker-daemon`**
> (`deploy/helm/maezo-tenant/templates/deployment-worker-daemon.yaml`), while the local
> `docker-compose.yml` service for the identical code is named **`worker-runtime`**. Both run
> `command: ["python", "-m", "maezo.runtime.worker_runtime"]` from the same image
> (`deploy/Dockerfile`) — same code (`src/maezo/runtime/worker_runtime/service.py`), same
> `command:`, different deployment-surface name. This runbook uses "worker daemon" for the
> role and calls out the compose-vs-Helm resource name explicitly wherever the distinction
> matters (mirrors the naming clarification already established in `docs/runbooks/gateway.md`
> for the gateway library vs. Deployment).

---

## Table of Contents

1. [What the worker daemon does](#1-what-the-worker-daemon-does)
2. [Start / stop / health — local (docker compose)](#2-start--stop--health--local-docker-compose)
3. [Start / stop / health — Kubernetes](#3-start--stop--health--kubernetes)
4. [`RUNTIME_MODE` — fail-closed contract](#4-runtime_mode--fail-closed-contract)
5. [Local engine bring-up (CIB Seven) — full recipe](#5-local-engine-bring-up-cib-seven--full-recipe)
6. [Logs and metrics — where they land](#6-logs-and-metrics--where-they-land)

---

## 1. What the worker daemon does

**Code:** `src/maezo/runtime/worker_runtime/service.py`

The worker daemon runs the `WorkerHarness` fetch-and-lock loop against CIB Seven's external-task
API, publishes domain events/notifications to Kafka, and (per T1.10/T-D) requires a durable
Postgres audit sink before it will accept any work — see
[`audit-recovery.md`](audit-recovery.md) for the audit-chain side of that contract. It does
**not** process raw PHI (`maezo.io/phi-zone: "false"` label in the Helm template) — only process
identifiers/refs.

## 2. Start / stop / health — local (docker compose)

**Code:** `docker-compose.yml` (service `worker-runtime`, profile `app`)

```bash
# Bring up core (postgres/cibseven/hapi-fhir/kafka) first — worker-runtime depends_on: cibseven
# (condition: service_healthy)
make dev-stack                                          # = docker compose --profile core up -d

# Then the app-profile daemons, including worker-runtime:
docker compose --profile core --profile app up -d --build worker-runtime

# Health:
curl -sf http://localhost:8020/healthz   # immediate 200 once the process is up
curl -sf http://localhost:8020/readyz    # reflects kafka/engine/workers/harness + audit_sink_ready

# Stop (keep volumes):
docker compose --profile core --profile app stop worker-runtime
# Stop and remove the container:
docker compose --profile core --profile app down worker-runtime
```

Local compose env for `worker-runtime` (`docker-compose.yml`): `TENANT_ID=amh`,
`WORKER_ID=maezo-worker-local`, `CIBSEVEN_BASE_URL=http://cibseven:8080/engine-rest`,
`KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, `HEALTH_PORT=8000` (mapped to host `8020`). Note there is
**no `DATABASE_URL`** set for this service in `docker-compose.yml` — unlike the Helm
`worker-daemon` Deployment (§3), the local compose service has no durable audit sink configured
by default, so `/readyz`'s `audit_sink_ready` gate will stay red and the daemon will **not** enter
the fetch-and-lock rotation until you export `DATABASE_URL` pointing at the compose `postgres`
service and re-run migrations for the target tenant schema (see
[`audit-recovery.md`](audit-recovery.md) §2 for the migration command). This is a real,
verified gap in the local dev compose file as of this writing, not a simplification for this
runbook — confirm by re-reading the `worker-runtime` service block in `docker-compose.yml`.

## 3. Start / stop / health — Kubernetes

**Code:** `deploy/helm/maezo-tenant/templates/deployment-worker-daemon.yaml`,
`service-worker-daemon.yaml`

```bash
kubectl get deploy/worker-daemon -n maezo-{tenant}
kubectl rollout status deploy/worker-daemon -n maezo-{tenant}
kubectl logs -n maezo-{tenant} deploy/worker-daemon -f
kubectl get pods -n maezo-{tenant} -l app.kubernetes.io/component=worker-daemon

# Restart (e.g. to pick up a ConfigMap/Secret change):
kubectl rollout restart deploy/worker-daemon -n maezo-{tenant}

# Scale:
kubectl scale deploy/worker-daemon -n maezo-{tenant} --replicas=N
```

`livenessProbe`: `GET /healthz` on container port `8000`, `initialDelaySeconds: 20`,
`periodSeconds: 30`. `readinessProbe`: `GET /readyz` on the same port,
`initialDelaySeconds: 10`, `periodSeconds: 10`.

`WORKER_ID` is populated per-replica from the pod's own `metadata.name` via the Downward API
(each replica needs a distinct id — the engine uses it as the external-task lock owner; two
replicas sharing an id would fight over the same lock). `DATABASE_URL` is injected from the
Aurora `ExternalSecret` (`.Values.aurora.secretName`, key `database-url`) — **required**: absent
`DATABASE_URL` means no `PostgresAuditSink` is built, no `WorkerHarness` is built, and the daemon
never enters the fetch-and-lock rotation (`/readyz` stays red on `audit_sink_ready`) — this is
the fail-closed posture: an un-auditable daemon must never accept effect-producing work.

For a bad-release rollback of the worker-daemon Deployment itself (wrong image tag, bad Helm
values), use [`cd-rollback.md`](cd-rollback.md) — that runbook is generic to any
`maezo-tenant` Deployment via `helm rollback`, and is not duplicated here.

## 4. `RUNTIME_MODE` — fail-closed contract

**Code:** `src/maezo/runtime/agent_runtime/a2a_composition.py`
(`WORKER_RUNTIME_MODE_ENV_VAR`, `worker_runtime_mode_from_env()`, `_require_signer_or_fail_closed`)

`RUNTIME_MODE` gates whether the worker daemon's A2A dossier-delegation edges (the
Carolina/Andre handoffs — `_DOSSIER_EDGE_AGENT_IDS = ("carolina", "andre")`) will accept an
**unsigned** Agent Card when no signing key is configured. It does **not** gate the rest of the
daemon's operation (fetch-and-lock, audit emission, Kafka publish all run regardless).

```python
def worker_runtime_mode_from_env() -> str:
    return os.environ.get(WORKER_RUNTIME_MODE_ENV_VAR, "").strip() or "production"
```

- **Absent or blank → `"production"`.** This is the opposite default from the webhook receiver
  (`src/maezo/platform/webhooks/whatsapp/settings.py`: `runtime_mode` field defaults to
  `"local"` via pydantic). The worker daemon's fail-closed direction is deliberately the other
  way: an unset `RUNTIME_MODE` must never silently become the permissive mode.
- **Only `"local"` is non-production** (`_LOCAL_RUNTIME_MODE = "local"`, exact string match —
  any other value, including typos, resolves to production behavior).
- In production mode, an absent signing key makes `_require_signer_or_fail_closed` **raise** —
  the daemon refuses to compose the dossier-delegation dispatcher rather than accept unsigned
  Cards. In local/dev mode with no key, unsigned Cards are permitted (dev ergonomics only).

**Verified gap:** neither the Helm `worker-daemon` Deployment template nor the local
`docker-compose.yml` `worker-runtime` service sets `RUNTIME_MODE` at all (grep both files — zero
hits). That means **both** the production Helm deployment and local `docker compose` bring-up
resolve to `"production"` for this gate by default. To exercise the dev/local signer-optional
path locally, export it explicitly before bringing the daemon up:

```bash
RUNTIME_MODE=local docker compose --profile core --profile app up -d worker-runtime
```

Contrast with `AGENT_RUNTIME_MODE: local`, which **is** set explicitly for the `agent-runtime`
compose service (`docker-compose.yml`) — the worker-runtime service does not have an equivalent
line. See `src/maezo/a2a/signing.py` (`CardSigner`) / `docs/design/A2A-dispatcher-card-signing.md`
and [`a2a-key-rotation.md`](a2a-key-rotation.md) for what "signing key configured" means in
practice today.

## 5. Local engine bring-up (CIB Seven) — full recipe

**Code:** `docker-compose.yml`, `Makefile` (`dev-stack`, `test-integration`)

```bash
# Core services only (postgres, cibseven, hapi-fhir, kafka):
make dev-stack                    # = docker compose --profile core up -d

# Wait for health (all four have healthchecks; CIB Seven is the slow one — JVM cold start):
docker compose ps                 # watch for "healthy" on all core services

# Run the historical path-scoped target against the live engine:
make test-integration             # = uv run pytest tests/integration -q -m integration

# Tear down. Two forms, NOT equivalent:
docker compose --profile core down       # stops containers, KEEPS named volumes (pgdata, ...)
docker compose --profile core down -v    # stops containers AND destroys volumes
```

O alvo `make test-integration` continua limitado a `tests/integration/`. A lane CI é mais ampla:
coleta globalmente `tests -m "integration and not chaos"`, porque também existem suítes live-PG
e live-CIB sob `tests/unit/`; a lane `chaos` executa separadamente `-m chaos`. A união das duas
lanes cobre o conjunto global `-m integration`, sem interseção. A lane unitária usa
`-m "not integration"`. Antes de subir serviços, cada lane live grava um manifesto JSON schema 2
da coleção com rótulos públicos opacos, fingerprints das identidades completas, hash da fonte,
política de xfail e eventual companion derivado da guarda canônica `mutation_active`. A execução
grava outro JSON seguro com as fases setup/call/teardown e prova de entrada no corpo. O JUnit bruto
fica em staging privado: a validação confronta suas identidades completas com o manifesto
independente antes de projetar `classname`/`name` e publicar o fingerprint de correlação
`junit_identity_sha256`. Só então o XML redigido e o relatório de validação podem ser publicados.
Os quatro artefatos precisam concordar exatamente; caso ausente/substituído/duplicado, fonte
alterada, relatório parcial, interrupção, skip ordinário, xfail sem fase call e qualquer XPASS
falham fechado. Artefatos anteriores e temporários privados são removidos antes da execução e não
podem sustentar um resultado verde. Strict-xfails só são aceitos quando o corpo entrou e falhou
como esperado. Os seis companions canônicos continuam skips explícitos quando a mutação opt-in
não foi ativada e ficam declarados como obrigação de RED separado; texto livre contendo
`MAEZO_CHAOS_MUTATE` não cria um companion. Razões estruturadas são redigidas com o contexto
completo das chaves, inclusive JSON/repr citado e valores com espaços; parâmetros nunca aparecem
em claro nos JSONs, nos casos de validação ou no JUnit publicado.

**The `down -v` consideration between integration runs.** Per `docs/design/T3.3-chaos-resilience.md`
(§1.3), CI's `integration` job (`.github/workflows/ci.yml`) does: `docker compose --profile core
up -d postgres kafka cibseven` → wait for readiness of all three → run the global normal
integration-marker set → `docker compose --profile core down -v`. It always tears down **with**
`-v`. CI gets a fresh
runner every time either way, but a local iteration loop reuses the same Docker host across runs,
which is where this matters in practice: numerous rows in `docs/evidence-ledger.md` (e.g. "Probe
engine torn down clean after (`docker compose down -v`)", "docker -p td_sink down -v after")
follow the same pattern for local/throwaway stacks. A plain `down` (no `-v`) leaves the `pgdata`
volume — and CIB Seven's own embedded process-definition/history state — around, so a second run
against the same compose project can inherit schema or process-definition state from the prior
run instead of starting from genesis. If you are iterating locally and see unexplained state (an
old process-definition version, audit-chain rows from a previous run, a migration that "already
ran"), tear down **with** `-v` before the next `up`:

```bash
docker compose --profile core down -v     # matches CI's own teardown
```

`MAEZO_PG_HOST_PORT` overrides the Postgres host port (default `5433` locally, to avoid
clobbering another local Postgres on `5432`; CI pins it to `5432` explicitly). Internal
container-to-container traffic always uses `5432` regardless.

## 6. Logs and metrics — where they land

**Code:** `src/maezo/runtime/worker_runtime/service.py` (structlog `logger` calls throughout),
`deploy/helm/maezo-tenant/templates/service-worker-daemon.yaml`,
`deploy/helm/maezo-tenant/templates/podmonitor.yaml`

- **Logs:** structured JSON via `structlog` (e.g. `logger.info("worker_runtime_running")`,
  `logger.error("audit_sink_build_failed", exc_info=True)`) to stdout. Locally:
  `docker compose logs -f worker-runtime`. In Kubernetes: `kubectl logs -n maezo-{tenant}
  deploy/worker-daemon -f`. There is no in-repo log-aggregation backend (no Loki/CloudWatch Logs
  shipping config found under `deploy/`) wired for application logs as of this writing — the
  OTel collector (`deploy/observability/otel-collector.yaml`) does define a `logs` pipeline
  (`receivers: [otlp]` → `exporters: [debug]`), but its exporter is `debug` only, i.e. it prints
  to the collector's own output rather than shipping anywhere durable. Treat `kubectl logs`/
  `docker compose logs` as the log surface until that changes.
- **Metrics:** `/metrics` on container port `8000`. Scraped in-cluster by the
  `worker-daemon-metrics` headless Service (`prometheus.io/scrape: "true"` annotations for
  non-operator clusters) and by the `worker-daemon` `PodMonitor`
  (`jobLabel: maezo.io/scrape-job`, matching the pod label `maezo.io/scrape-job:
  "maezo-worker-daemon"` set in the Deployment template). The worker daemon emits
  `maezo_gateway_*` metrics **in-process** (it imports `maezo.gateway`, same as every
  `agent-{name}` Deployment — see `docs/runbooks/gateway.md`'s deployment-model note); the
  canonical metric catalog is `src/maezo/runtime/metrics.py` per ADR-0010 (per
  `docs/runbooks/devops-stack.md` §5's metric-catalog table). Locally: Grafana at
  `http://localhost:3000`, Prometheus at `http://localhost:9090`
  (`make dev-observability` = `docker compose --profile core --profile observability up -d`).
