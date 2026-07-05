# Runbook: Gateway — PEP, pseudonymization, audit trail

**Audience:** Security, compliance, platform engineers  
**Last updated:** 2026-07-04  
**Applies to:** Maezo Healthcare Plan Phase 0+

> **Deployment model — the gateway is an in-process library, not a service.**
> `maezo.gateway` (PEP, pseudonymizer, audit) is imported and executed **inside**
> the runtime pods — each `agent-{name}` Deployment and the worker-daemon — via
> the runtime harness/tool wiring. There is **no standalone gateway
> Deployment/Service**: `gateway.enabled` is `false` in the chart (the orphaned
> `deployment-gateway.yaml` was disabled — the package has no runnable
> `__main__`). Consequences for operators:
>
> - `kubectl` commands target the runtime workloads (`deploy/agent-helena`,
>   `deploy/agent-rafael`, `deploy/agent-marina`, `deploy/worker-daemon`), never
>   `deploy/gateway`.
> - `maezo_gateway_*` metrics are emitted in-process and scraped from the runtime
>   pods' `/metrics` (port 8000, PodMonitor `agent-runtime`), not from a gateway pod.
> - "Restart the gateway" = restart the runtime Deployments that embed it.

---

## Table of Contents

1. [Policy Enforcement Point (PEP)](#1-policy-enforcement-point-pep)
2. [Decision flow (allow/deny/require_human)](#2-decision-flow)
3. [Pseudonymization](#3-pseudonymization)
4. [Audit JSONL & verification](#4-audit-jsonl--verification)
5. [Hard items (immutable actions)](#5-hard-items-immutable-actions)
6. [Tenant overlays](#6-tenant-overlays)
7. [PEP deny spike response](#7-pep-deny-spike-response)

---

## 1. Policy Enforcement Point (PEP)

**Code:** `src/maezo/gateway/pep.py`

The PEP is the sole decision point for agent tool calls (ADR-0005/0008). Every tool invocation goes through:

1. **Allowlist check:** Is the tool in `agent.yaml`'s `tools` list?
2. **Autonomy matrix lookup:** What is the L0–L3 level for this action?
3. **Hard item check:** Is this action frozen in code (immutable)?
4. **Decision:** ALLOW | DENY | REQUIRE_HUMAN

**Classes:**

```python
class Decision(StrEnum):
    ALLOW = "allow"              # Agent executes directly
    DENY = "deny"                # Agent cannot execute; audit trail
    REQUIRE_HUMAN = "require_human"  # Agent prepares dossier; human approves
```

**Input:** `ToolCall` (agent_id, tenant, tool, action, autonomy_context, ...)  
**Output:** `PolicyDecision` (decision, action, level, reason, hard)

---

## 2. Decision flow

**Code:** `src/maezo/gateway/pep.py` (lines 1–20 in docstring)

### Step 1: Allowlist check

```python
# In agent.yaml:
tools:
  - mcp-dmn.evaluate
  - mcp-fhir.read_patient_summary
  - mcp-cibseven.start_process
```

If tool is not listed → **DENY** (no audit, returns immediately).

### Step 2: Autonomy matrix

The gateway loads two layers (paths are settings defaults — `AUTONOMY_CORE_PATH` /
`AUTONOMY_OVERLAY_PATH` in `src/maezo/runtime/{agent_runtime,worker_runtime}/settings.py`):

- **L0-core:** `src/maezo/policies/autonomy/L0-core.yaml` — platform baseline; changes only via code review
- **Tenant overlay:** `src/maezo/policies/autonomy/tenants-{tenant}.yaml` (e.g. `tenants-amh.yaml`) — per-tenant refinements

(`src/maezo/policies/autonomy/_hard_frozen.yaml` is a third, CI-only artifact: the
frozen list of `hard` items that `make validate-artifacts` cross-checks — see §5.)

The overlay **can only elevate restrictions** (L0 → L1 → L2 → L3); cannot lower a hard item.

### Step 3: Level resolution

Merge rule: If tenant overlay raises the restriction level, use that; else use L0-core.

Example:
- L0-core: `clinical_decision` = L0 (hard, deny)
- Tenant overlay tries: `clinical_decision` = L2 (override)
- Result: **Remains L0** (hard items cannot be overridden)

### Step 4: Decision by level

```python
if level == L0 and hard:
    return DENY  # Never allow hard L0 actions
elif level == L0:
    return REQUIRE_HUMAN  # Non-hard L0: human executes
elif level in (L1, L2, L3):
    return ALLOW if level >= L2 else REQUIRE_HUMAN
```

---

## 3. Pseudonymization

**Code:** `src/maezo/gateway/pseudonymizer.py`

All text input containing PHI (patient name, CPF, phone) is pseudonymized **before** reaching the agent inference or tool context.

### Pseudonymization contract

**Input:** Raw structured identifiers and/or free text  
**Output:** Deterministic surrogate tokens (`[TYPE_<hex>]`), with the token↔value
link recorded in the `SurrogateStore` for gateway-internal de-pseudonymization

```python
from maezo.gateway.pseudonymizer import FieldType, InMemorySurrogateStore, Pseudonymizer

# Production: Pseudonymizer.from_vault(store) — reads the HMAC key from the
# PHI_HMAC_KEY env (ESO-synced from Secrets Manager; fail-closed if absent).
ps = Pseudonymizer(hmac_key=b"...", store=InMemorySurrogateStore())

token = ps.pseudonymize_value("amh", FieldType.CPF, "123.456.789-00")
# token = "[CPF_<hex>]" — deterministic per (tenant, field_type, value)

sanitized = ps.pseudonymize_text("amh", "Olá, meu nome é João Silva, CPF 123.456.789-00")
# PHI matches replaced by the SAME tokens the structured fields would get

original = ps.depseudonymize("amh", FieldType.CPF, token)  # gateway-internal only
```

### Key management

The pseudonymizer secret is **injected at container startup** from Secrets Manager via External Secrets Operator (ESO). It is **never** stored in code or git.

The Helm chart defines the secret as `maezo-phi-hmac` (see `deploy/helm/maezo-tenant/templates/externalsecret.yaml`):

```yaml
# ExternalSecret: PHI pseudonymizer HMAC key (ADR-0006 — gap #12 vault injection)
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: phi-hmac-key
spec:
  target:
    name: maezo-phi-hmac  # Secret name
  data:
    - secretKey: phi-hmac-key  # Key within the secret
      remoteRef:
        key: "<secretPathPrefix>/phi/hmac-key"
```

To inject this secret into a deployment:

```yaml
env:
  - name: PHI_HMAC_KEY
    valueFrom:
      secretKeyRef:
        name: maezo-phi-hmac
        key: phi-hmac-key
```

---

## 4. Audit JSONL & verification

**Code:** `src/maezo/gateway/audit.py`

Every DENY and REQUIRE_HUMAN decision emits an `AuditRecord` to an append-only JSONL trail with hash-chain integrity (ADR-0007).

### Audit record schema

```json
{
  "agent_id": "helena",
  "agent_version": "helena@v0",
  "tenant": "amh",
  "tool": "mcp-cibseven.start_process",
  "input_hash": "sha256hex",
  "decision_basis": "autonomy_level=L0;hard=true",
  "autonomy_level": "L0",
  "dmn_versions": { "triage_redflag_adult": "dmn-v1" },
  "model_id": "claude-3.5-sonnet",
  "prompt_version": "system-v1",
  "human_approver": "dr.auditor@amh",
  "ts": "2026-06-12T15:30:45.123Z",
  "prev_record_hash": "sha256hex_of_previous",
  "record_hash": "sha256hex_of_this_record"
}
```

**Key fields:**

- `input_hash` — SHA-256 of tool input (PHI never in plaintext)
- `decision_basis` — Why this decision was made
- `prev_record_hash` — Pointer to previous record (forms hash chain)
- `record_hash` — This record's hash; computed from canonical payload

### Hash chain verification

Each record's `record_hash` is the SHA-256 of the record's canonical JSON (with `prev_record_hash` included but `record_hash` excluded). To verify integrity:

```python
from maezo.gateway.audit import AuditRecord

record = AuditRecord(...)
computed_hash = record.compute_hash()
assert record.record_hash == computed_hash, "Tampered record detected!"
assert computed_hash in chain, "Record missing from chain!"
```

**Tamper-evidence:** Altering any field (agent_id, decision, timestamp, etc.) breaks the chain at that record and all subsequent records.

### Audit sinks

The audit trail is written to multiple sinks:

1. **JSONL file** (append-only, local filesystem)
2. **Kafka topic** (optional; `agents.audit`)
3. **PostgreSQL** (optional; `audit_log` table for compliance query)

All sinks receive the full chain; the filesystem is the source of truth.

### Inspect audit trail

```bash
# Show last 10 records
tail -10 /mnt/audit/gateway-$(date +%Y%m%d).jsonl

# Verify chain integrity — there is NO `maezo.gateway.audit_verify` CLI module;
# the verification logic ships as library functions in maezo.gateway.audit
# (`parse_jsonl()` + `verify_chain()`). Invoke them directly:
python -c '
import sys
from maezo.gateway.audit import parse_jsonl, verify_chain
records = parse_jsonl(sys.argv[1])
ok = verify_chain(records)
print(len(records), "records - chain", "OK" if ok else "BROKEN")
sys.exit(0 if ok else 1)
' /mnt/audit/gateway-20260612.jsonl
```

---

## 5. Hard items (immutable actions)

**Code:** `src/maezo/gateway/pep.py` (`HARD_ACTIONS`)

Five actions are coded as immutable (cannot be downgraded by config):

```python
HARD_ACTIONS: frozenset[str] = frozenset(
    {
        "clinical_decision",      # Diagnosis/treatment plans
        "authorization_denial",   # Coverage denials
        "nip_manter_negativa",    # NIP MANTER_NEGATIVA — coverage-denial class, frozen in
                                  # parity with authorization_denial (GAP-NIP-3/GAP-XHITL-3)
        "fraud_accusation",       # Fraud allegations
        "contract_termination",   # Member disenrollment
    }
)
```

**Enforcement:** The PEP loads both L0-core and tenant overlay, then cross-checks: if an action is in HARD_ACTIONS and the overlay tries to elevate its level (L0 → L2), the PEP raises `PolicyError` and rejects the override.

**Tenant overlay validation in CI:**

```bash
# Runs on PR; fails if overlay violates hard items
make validate-artifacts
```

**Source file:** `src/maezo/policies/autonomy/_hard_frozen.yaml` documents which actions are hard (frozen) and why (compliance/regulation references). It is the CI-verified frozen list; `pep.HARD_ACTIONS` is the in-image source of truth.

---

## 6. Tenant overlays

**Code:** `src/maezo/gateway/pep.py` (`load_matrix` + `AutonomyMatrix`)

Each tenant can define an autonomy overlay to **refine** L0-core for specific
actions — elevate restrictions or pin tenant parameters (e.g. value ceilings);
never lower a hard item. Overlay files live next to the core:
`src/maezo/policies/autonomy/tenants-{tenant}.yaml` (resolved via
`AUTONOMY_OVERLAY_PATH`, defaulting to `tenants-<tenant>.yaml` beside the core).

### Overlay structure (the real AMH overlay)

```yaml
# src/maezo/policies/autonomy/tenants-amh.yaml
version: 1
tenant: amh
overrides:
  authorization_approval:
    params: { max_value_brl: 0 }   # D-07 open: initial ceiling pending AMH board
  reembolso_auto_approval:
    params: { max_value_brl: 0 }   # D-07 open: reimbursement auto-approval ceiling
```

### Loading & merging

```python
from maezo.gateway.pep import load_matrix

matrix = load_matrix(
    "src/maezo/policies/autonomy/L0-core.yaml",
    tenant="amh",
    overlay_path="src/maezo/policies/autonomy/tenants-amh.yaml",
)
# Merge enforces in code that the overlay never downgrades a `hard`
# item nor loosens a non-hard one (HARD_ACTIONS cross-check built in).
```

**Validation:**
- If overlay tries to downgrade a hard action → `PolicyError`
- If overlay references unknown action → warning logged; action ignored
- If overlay level is invalid (typo) → validation fails at load time

### What overlays cannot change

Tenant overlays **cannot:**
- Downgrade hard items (L0 hard stays L0 hard)
- Add new hard items (only code can define hard actions)
- Override agent allowlists (only `agent.yaml` controls tool access)

---

## 7. PEP deny spike response

**Alert:** `MaezoPEPDenySpike` — more than 1 DENY decision per second  
**Severity:** High (indicates attack, misconfiguration, or policy regression)

### Investigation

The PEP runs in-process in the runtime pods (see header note) — grep those, not a
gateway Deployment:

```bash
# 1. Check recent PEP denies (last 5 min) across the runtime workloads
for d in agent-helena agent-rafael agent-marina worker-daemon; do
  kubectl logs deploy/$d -n maezo-amh --since=5m | grep "pep_deny"
done

# 2. Extract decision_basis to identify which action(s) are blocked
for d in agent-helena agent-rafael agent-marina worker-daemon; do
  kubectl logs deploy/$d -n maezo-amh | grep "pep_deny"
done | jq '.decision_basis' | sort | uniq -c | sort -rn

# 3. Identify if this is an attack or a policy change
# - Spike after deployment → likely policy regression
# - Spike with no code change → possible attack (repeated tool calls with wrong permissions)
```

### Immediate mitigation

**If policy regression:**

```bash
# 1. Identify the commit that changed autonomy policy
git log --oneline -20 src/maezo/policies/autonomy/

# 2. Revert (if safe) or apply hotfix
git revert <commit_hash>
git push origin main

# 3. The gateway library reloads policy on pod restart — restart the runtime
#    Deployments that embed it (there is no deploy/gateway):
for d in agent-helena agent-rafael agent-marina worker-daemon; do
  kubectl rollout restart deploy/$d -n maezo-amh
done
```

**If attack (no code change):**

```bash
# 1. Check source agent (agent_id in deny logs)
# 2. Suspend agent's allowlist temporarily (remove tool from agent.yaml)
# 3. Alert security team + medical audit team
# 4. Investigate intent logs + conversation history
```

### Root causes

| Pattern | Likely cause | Fix |
|---------|--------------|-----|
| Spike after deploy | Autonomy policy change in tenant overlay | Revert overlay YAML |
| Sustained low-level denies | Normal behavior (human-required actions) | Baseline; no fix |
| Denies from one agent only | Agent misconfiguration or compromised | Inspect agent allowlist |
| Denies to one action | Action was moved to L0-hard in validation | Check if intentional; adjust if not |

---

## Monitoring & dashboards

**Dashboard:** `/deploy/observability/dashboards/gateway.json`

**Key metrics** (canonical catalog: `src/maezo/runtime/metrics.py` — there is ONE
PEP decision counter with a `decision` label, not separate per-decision metrics;
emitted in-process by the runtime pods, scraped from their `/metrics` port 8000):

| Metric | Query |
|--------|-------|
| Total decisions/s | `rate(maezo_gateway_pep_decision_total[5m])` |
| Deny rate | `rate(maezo_gateway_pep_decision_total{decision="deny"}[5m])` |
| Require-human rate | `rate(maezo_gateway_pep_decision_total{decision="require_human"}[5m])` |
| L2 spot-check sample rate | `rate(maezo_gateway_pep_l2_review_sampled_total[5m])` |
| Audit trail lag | `max by (tenant) (maezo_gateway_audit_lag_seconds)` — a **gauge** (seconds behind), not a histogram; `MaezoAuditLagHigh` fires at > 120s for 3m |

**Logs:**

All PEP decisions (ALLOW, DENY, REQUIRE_HUMAN) are logged with decision_basis. Audit records are written to JSONL immediately.
