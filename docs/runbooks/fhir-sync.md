# Runbook: FHIR Sync — Tasy CDC integration & canonical store

**Audience:** Integration engineers, DBAs, on-call  
**Last updated:** 2026-06-12  
**Applies to:** Maezo Healthcare Plan Phase 0+

---

## Table of Contents

1. [Overview](#1-overview)
2. [CDC topics consumed](#2-cdc-topics-consumed)
3. [Tasy → FHIR mapping](#3-tasy--fhir-mapping)
4. [Dead Letter Queue inspection & replay](#4-dead-letter-queue-inspection--replay)
5. [Tasy simulator](#5-tasy-simulator)
6. [Known caveats](#6-known-caveats)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Overview

**Code:** `src/maezo/platform/integrations/fhir_sync/`

Maezo syncs patient, coverage, and authorization data from Tasy (legacy EHR) to HAPI FHIR R4 (canonical store) via Kafka CDC. This is the source of truth for patient demographics and insurance eligibility in Phase 0 and 1.

**Consumer group:** `fhir-sync-{tenant}` (e.g., `fhir-sync-amh`)  
**Commit strategy:** Manual, after FHIR upsert confirmed  
**DLQ:** `cdc.amh.tasy.fhir-sync.dlq`

### Where `KAFKA_BOOTSTRAP_SERVERS` comes from (per environment)

The consumer resolves its broker address from the `KAFKA_BOOTSTRAP_SERVERS` env
var and **falls back to `localhost:9092` when unset**
(`consumer.py::run_fhir_sync`) — that fallback only ever works for a process
running directly on a dev host next to the compose stack. In every containerized
environment the variable must be injected explicitly:

| Environment | Value | Injected by |
|---|---|---|
| Host-side dev/tests (process on your machine) | `localhost:9092` (default) | — (EXTERNAL listener published by docker-compose) |
| docker-compose container (e.g. tasy-simulator) | `kafka:29092` | `docker-compose.yml` service `environment` — INTERNAL listener; `kafka:9092` does NOT work container-to-container |
| Staging/prod (Kubernetes) | MSK Serverless bootstrap string (IAM auth, port 9098) | `deployment-fhir-sync.yaml` env `KAFKA_BOOTSTRAP_SERVERS` ← `secretKeyRef` `maezo-kafka-config`/`bootstrap-servers`, which the ExternalSecret (`externalsecret.yaml`) syncs from AWS Secrets Manager `debezium/msk-bootstrap-servers` (amh-data-platform's MSK — we consume, never duplicate) |

There is **no `kafka` Service in the cluster** — production Kafka is MSK
Serverless, so any hardcoded `kafka:9092`/`kafka:29092` address in a kubectl
command is wrong by construction. If the fhir-sync pod logs show connection
attempts to `localhost:9092`, the env injection is missing/broken — check
`kubectl get secret maezo-kafka-config -n maezo-amh` (the ExternalSecret stays in
`SecretSyncedError` until the upstream Secrets Manager value is populated) and the
pod's rendered env.

---

## 2. CDC topics consumed

**Code:** `src/maezo/platform/integrations/fhir_sync/consumer.py` (lines 29–41)

| Topic | Table | FHIR Resource | Notes |
|-------|-------|---------------|-------|
| `cdc.amh.tasy.pessoa_fisica` | PESSOA_FISICA (patient) | Patient | Demographics, CPF, phone, insurance card |
| `cdc.amh.tasy.convenio_paciente` | CONVENIO_PACIENTE (coverage) | Coverage | Insurance plan, validity, status |
| `cdc.amh.tasy.autorizacao_convenio` | AUTORIZACAO_CONVENIO (authorization) | (skip v0) | Future: CoverageEligibilityResponse in Phase 1 |

**Envelope format** (CDC via Debezium):

```json
{
  "op": "c" | "u" | "d",
  "before": { ... },
  "after": { "NR_SEQ_PACIENTE": 12345, "NM_PACIENTE": "...", ... },
  "ts_ms": 1718181600000
}
```

- `op=c`: insert
- `op=u`: update
- `op=d`: delete (syncer skips; no DELETE in canonical store, only status changes)

---

## 3. Tasy → FHIR mapping

**Code:** `src/maezo/platform/integrations/fhir_sync/mapper.py`

### PESSOA_FISICA → Patient

| Tasy column | FHIR path | Notes |
|-----------|-----------|-------|
| `NR_SEQ_PACIENTE` | identifier[system=urn:oid:2.16.840.1.113883.13.236] | Conditional key for update |
| `NM_PACIENTE` | name[0].text | Display name |
| `DT_NASCIMENTO` | birthDate | ISO 8601 (YYYY-MM-DD) |
| `IE_SEXO` | gender | M→male, F→female, I→other, default→unknown |
| `NR_CPF` | identifier[system=urn:oid:2.16.840.1.113883.13.60] | Brazilian CPF |
| `NR_CARTEIRA_CONV` | identifier[system=https://maezo.health/sid/carteira] | Insurance card # |
| `NR_CELULAR` | telecom[system=phone, use=mobile] | E.164 format |
| `tenant` | meta.tag[system=https://maezo.health/sid/tenant] | Tenant isolation |

**Conditional update key:**
```
identifier where system=urn:oid:2.16.840.1.113883.13.236 and value={NR_SEQ_PACIENTE}
```

### CONVENIO_PACIENTE → Coverage

| Tasy column | FHIR path | Notes |
|-----------|-----------|-------|
| `NR_SEQ_CONVENIO_PACIENTE` | identifier[system=https://maezo.health/sid/convenio-paciente] | Coverage ID |
| `CD_CONVENIO` | payor[0] | Insurance org |
| `CD_PLANO` | class[type=plan] | Plan code (ANS) |
| `NR_CARTEIRA` | subscriberId | Card number |
| `DT_VALIDADE` | period.end | Coverage end date |
| `IE_TIPO_COBERTURA` | class[type=group] | A/H/AH (outpatient/hosp/both) |
| `IE_ATIVO` | status | S→active, N→cancelled |
| `CD_CONTRATO` | contract[0].identifier | Contract ref |
| `NR_SEQ_PACIENTE` | beneficiary.reference | Patient reference (key point: conditional 404 if patient not yet synced) |
| `tenant` | meta.tag[system=https://maezo.health/sid/tenant] | Tenant isolation |

**Key caveat:** Coverage contains a **conditional reference** to Patient (by NR_SEQ_PACIENTE). If the Patient CDC event arrives **after** the Coverage event, the initial upsert will fail with 404 (beneficiary not found). See [Known caveats](#6-known-caveats).

---

## 4. Dead Letter Queue inspection & replay

**Code:** `src/maezo/platform/integrations/fhir_sync/consumer.py` (lines 135–146)

DLQ topic: `cdc.amh.tasy.fhir-sync.dlq`

### Inspect DLQ

**Local (docker-compose):** `kafka-console-consumer` is not installed in the
fhir-sync image (`deploy/Dockerfile`'s runtime stage is `python:3.12-slim` +
`libpq5` only — no Java/Kafka CLI tools). Exec into the `kafka` container itself
instead, using the **INTERNAL** listener (`kafka:29092`) — `kafka:9092` is the
**EXTERNAL** listener, advertised as `localhost:9092` and reachable only from the
host machine, not from inside another container (see the dual-listener comments in
`docker-compose.yml`):

```bash
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic cdc.amh.tasy.fhir-sync.dlq \
  --from-beginning \
  --max-messages 10 \
  --property print.key=true \
  --property print.timestamp=true
```

**Staging/prod (Kubernetes):** the same tooling gap applies to the deployed
fhir-sync image, and there is no in-cluster `kafka` service — production Kafka is
**MSK Serverless** (referenced from amh-data-platform). Run a temporary debug pod
with a Kafka-CLI image, sourcing the bootstrap servers from the
`maezo-kafka-config` Secret (synced by the ExternalSecret in
`deploy/helm/maezo-tenant/templates/externalsecret.yaml` from AWS Secrets Manager
key `debezium/msk-bootstrap-servers` — the same Secret the fhir-sync Deployment
injects as `KAFKA_BOOTSTRAP_SERVERS`):

```bash
BOOTSTRAP=$(kubectl get secret maezo-kafka-config -n maezo-amh \
  -o jsonpath='{.data.bootstrap-servers}' | base64 -d)

kubectl run kafka-debug --rm -i --restart=Never -n maezo-amh \
  --image=confluentinc/cp-kafka:7.7.0 --quiet -- \
  kafka-console-consumer \
    --bootstrap-server "$BOOTSTRAP" \
    --topic cdc.amh.tasy.fhir-sync.dlq \
    --from-beginning \
    --max-messages 10 \
    --property print.key=true \
    --property print.timestamp=true
```

> MSK Serverless uses IAM auth (port 9098) — the debug pod additionally needs the
> IAM client-config properties and NetworkPolicy egress to the MSK CIDR; if
> blocked, inspect the DLQ from a bastion/CI runner with MSK access instead.

**DLQ payload:**

```json
{
  "original_topic": "cdc.amh.tasy.convenio_paciente",
  "original_partition": 0,
  "original_offset": 12345,
  "envelope": { "op": "u", "after": { ... } },
  "error": "404 beneficiary not found",
  "tenant": "amh"
}
```

### Diagnosis

Common errors and fixes:

| Error | Cause | Fix |
|-------|-------|-----|
| `404 beneficiary not found` | Coverage arrived before Patient | Replay after Patient synced (check HAPI Patient search) |
| `Invalid identifier format` | Malformed CPF or phone | Inspect `envelope.after` for non-numeric characters |
| `Connection refused to FHIR` | HAPI down or unreachable | Check HAPI pod: `kubectl logs deploy/hapi-fhir -n maezo-amh` |
| `Kafka consumer lag` | Syncer slow; network saturation | Scale syncer replicas; check network policy |

### Replay DLQ

**Manual replay (via standalone script):**

```python
# Pseudo-code; see tests/integration/test_fhir_sync.py for real example
from maezo.platform.integrations.fhir_sync.consumer import process_single_message
from maezo.platform.integrations.fhir_sync.hapi_client import HapiFhirClient

client = HapiFhirClient("http://hapi-fhir:8080/fhir")
envelope = { "op": "u", "after": { "NR_SEQ_PACIENTE": 12345, ... } }

try:
    resource = await process_single_message(
        envelope=envelope,
        topic="cdc.amh.tasy.convenio_paciente",
        fhir_client=client,
        tenant_id="amh"
    )
    print(f"Replayed: {resource['id']}")
except Exception as e:
    print(f"Failed: {e}")
```

**Automatic replay in consumer:**

The syncer will retry DLQ messages on restart if the underlying cause (missing Patient, network) is fixed. No DLQ → main topic routing is built-in; manual review is required.

---

## 5. Tasy simulator

**Code:** `src/maezo/platform/integrations/tasy_simulator/`

For development and integration testing, a deterministic Tasy CDC simulator generates synthetic patient/coverage records.

### Running the simulator

#### Docker compose

```yaml
# docker-compose.yml includes (abridged; kafka:29092 is the INTERNAL listener —
# container-to-container. kafka:9092 is EXTERNAL/host-only and will NOT work
# from inside a container):
services:
  tasy-simulator:
    build:
      context: .
      dockerfile: deploy/tasy_simulator.Dockerfile
    profiles: [simulator]
    environment:
      KAFKA_BOOTSTRAP_SERVERS: kafka:29092   # INTERNAL listener
      TASY_SIM_SEED: "0"   # deterministic
      TASY_SIM_LOOP: "0"   # one-shot in CI; "1" for continuous/interactive
```

To run specific scenarios, add e.g. `TASY_SIM_SCENARIOS: "base,pediatric,psychosocial"`
(not set in the checked-in compose file — absent means all scenarios).

Start:
```bash
docker compose --profile simulator up tasy-simulator
```

### Env vars

| Var | Default | Purpose |
|-----|---------|---------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka endpoint |
| `TASY_SIM_SEED` | (none; random) | Deterministic RNG seed (int) |
| `TASY_SIM_SCENARIOS` | (all) | CSV of scenario names; absent = all |
| `TASY_SIM_LOOP` | `0` | "1" = continuous loop (dev); "0" = one-shot (CI) |

### Scenarios

**Code:** `src/maezo/platform/integrations/tasy_simulator/fixtures.py`

| Scenario | Count | Coverage |
|----------|-------|----------|
| `base` | 10 patients + 10 coverages | Adult demographics, basic insurance |
| `pediatric` | 5 patients + 5 coverages | Children (age < 18) |
| `psychosocial` | 3 patients + 3 coverages | Mental health flags (for red flag testing) |
| `red_flag_indicators` | 7 patients | Conditions triggering triage red flags |

All data is clearly synthetic (e.g., `Patient Simulator NNN`, invalid CPFs) and never contains real PHI.

### Determinism contract

```python
# Same seed → identical JSONL output
python -m maezo.platform.integrations.tasy_simulator.main \
  TASY_SIM_SEED=42 > conv_1.jsonl

python -m maezo.platform.integrations.tasy_simulator.main \
  TASY_SIM_SEED=42 > conv_2.jsonl

# conv_1.jsonl == conv_2.jsonl (sha256)
```

Determinism is **not** a guarantee of reproducibility across code versions; seed bumps in CI when fixtures change.

---

## 6. Known caveats

### Coverage references Patient (conditional 404 until synced)

**Issue:** FHIR Coverage.beneficiary is a conditional reference to Patient by `NR_SEQ_PACIENTE`. If a Coverage CDC event arrives before the corresponding Patient event, the Coverage upsert fails with 404.

**Symptom:** DLQ contains `cdc.amh.tasy.convenio_paciente` events with `"error": "404 beneficiary not found"`.

**Mitigation:**
1. **Expected behavior:** Replay DLQ after Patient sync completes (Kafka ordering is per-partition; Cross-partition ordering is not guaranteed). Check HAPI Patient existence: `GET /fhir/Patient?identifier=urn:oid:2.16.840.1.113883.13.236|12345`.
2. **Long-term:** Phase 1 will implement a dead-letter processor that retries failed Coverage upserts on a schedule (exponential backoff).

### Tasy updates are not deletes

The syncer skips DELETE operations (`op=d`). In production, Tasy deletions are rare (audit trail required); status changes are used instead. If a patient record is deleted in Tasy, their FHIR Patient remains (marked inactive via status = false if needed).

### No subscription model for agent queries

FHIR Sync is one-way (Tasy → HAPI). Agents query HAPI only through the gated read-only FHIR seam (`gateway/seams/fhir.py::GatedFhirReader`), with the `mcp-fhir.*` id each agent's own `agent.yaml` declares. There is no publish-subscribe link (no event stream for agent notification on patient updates); staleness is acceptable in Phase 0.

Helena does **not** query FHIR — a claim this section carried until WP FHIR-TOOL-SURFACE-PARITY (NEW-09/GAP-TRIAGE-5). No node of `src/maezo/agents/helena/graph.py` holds a FHIR reader, helena is absent from `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT` (the only place that fills `deps["fhir"]`), and her `agent.yaml` no longer declares `mcp-fhir.read_patient_summary`/`mcp-fhir.search_coverage`. The single `mcp-fhir.*` id left in it, `mcp-fhir.read_coverage`, is an owner-gated catalogue pin with no call site and no seam. Staleness of a Patient/Coverage record therefore cannot surface in a Helena conversation; the agents this section is about are the ones listed in `_FHIR_ADAPTER_BY_AGENT`.

---

## 7. Troubleshooting

| Issue | Check | Fix |
|-------|-------|-----|
| FHIR Sync consumer not consuming | Broker connectivity | `kubectl logs deploy/fhir-sync -n maezo-amh \| grep -i kafka` |
| Lag stuck at high offset | Slow FHIR upserts | Scale FHIR-Sync replicas; check HAPI CPU/memory |
| DLQ growing | Persistent error (404, timeout) | Inspect DLQ, root-cause, replay |
| Patient data not visible in agent queries | Sync lag or HAPI not ready | `GET /fhir/metadata` should return 200; check FHIR-Sync replica count |
| Simulator produces no events | Kafka unreachable or profile not enabled | `docker compose --profile simulator up` (not just `up`) |

**Health check:**

```bash
# Consumer lag — local (docker-compose): the CLI lives in the kafka container,
# and containers must use the INTERNAL listener kafka:29092 (kafka:9092 is
# EXTERNAL/host-only — see §4).
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server kafka:29092 \
  --group fhir-sync-amh \
  --describe

# Consumer lag — staging/prod (Kubernetes/MSK): the fhir-sync image has no Kafka
# CLI tools; use a debug pod with the bootstrap servers from the
# maezo-kafka-config Secret (see §4 "Inspect DLQ" for the full pattern).
BOOTSTRAP=$(kubectl get secret maezo-kafka-config -n maezo-amh \
  -o jsonpath='{.data.bootstrap-servers}' | base64 -d)
kubectl run kafka-debug --rm -i --restart=Never -n maezo-amh \
  --image=confluentinc/cp-kafka:7.7.0 --quiet -- \
  kafka-consumer-groups \
    --bootstrap-server "$BOOTSTRAP" \
    --group fhir-sync-amh \
    --describe
```

**Logs to monitor:**

```bash
kubectl logs -f deploy/fhir-sync -n maezo-amh | grep -E "fhir_sync|ERROR|DLQ"
```
