# Runbook: Helena — Health Navigator agent

**Audience:** Operations, medical audit, product  
**Last updated:** 2026-06-12  
**Applies to:** Maezo Healthcare Plan Phase 0

---

## Table of Contents

1. [Graph architecture](#1-graph-architecture)
2. [Escalation triggers & motivo_categoria](#2-escalation-triggers--motivo_categoria)
3. [KPIs & observability](#3-kpis--observability)
4. [Golden dataset & promotion gates](#4-golden-dataset--promotion-gates)
5. [Troubleshooting escalation rate](#5-troubleshooting-escalation-rate)

---

## 1. Graph architecture

**Code:** `src/maezo/agents/helena/graph.py`

Helena is a LangGraph-based triage and navigation agent. Her conversation flow follows five states:

```
receive → identify → classify → {inform | schedule | escalate} → respond
```

### State nodes

| Node | Input | Output | Purpose |
|------|-------|--------|---------|
| `receive` | Raw message (pseudonymized) | `message_body`, `conversation_id` | Entry point; establish context |
| `identify` | Phone hash + message | `patient_summary` from FHIR | Load patient demographics + coverage from canonical store |
| `classify` | Message text + patient context | `intent`, `population`, `symptom_input`, `dmn_table` | LLM extracts intent + population; DMN lookup table selected |
| (DMN branch) | Symptom input | `dmn_decision`, `red_flag`, `prioridade` | Deterministic rule evaluation (ADR-0012) |
| `{inform\|schedule\|escalate}` | DMN output + intent | `next_kind`, `escalation_motivo` | Route based on decision |
| `respond` | Final decision | `response_text` | LLM generates reply text (no diagnosis) |

### Checkpoint & recovery

State is checkpointed **between nodes** (ADR-0002). If the process crashes mid-conversation, resumption loads the last checkpoint and continues from that node without re-executing prior decisions.

**Checkpoint location:** PostgreSQL `checkpoints` table (via LangGraph's persistence)

---

## 2. Escalation triggers & motivo_categoria

**Code:** `src/maezo/agents/helena/agent.yaml` (lines 33–40) + `graph.py` (lines 13–18)

When Helena detects a trigger condition, she escalates to the human triage team via `SP-OP-ESCALATION-001`. The escalation carries a `motivo_categoria` (reason category) for audit.

### The 5 triggers

| Trigger | Motivo categoria | Condition | Escalation level |
|---------|------------------|-----------|------------------|
| **Red flag (DMN)** | `red_flag_clinico` | DMN table returns `red_flag=true` | Always escalate |
| **Clinical question** | `intencao_clinica` | Intent = "clinical_question"; asking for diagnosis/treatment | Always escalate (L0 hard) |
| **Human request** | `solicitacao_humano` | Intent = "human_request" or frustration after 2 failed attempts | Always escalate |
| **Tool failure** | `falha_tecnica` | Tool timeout, Kafka down, FHIR 503, or logic loop detected | Always escalate |
| **Psychosocial risk** | `risco_psicossocial` | Any mention of suicide/self-harm (including subtle references) | Always escalate (always-on, conservative) |

**Implementation in graph:**

```python
# graph.py classify node detects intent and population
# DMN node evaluates the symptom input for the population

if dmn_decision.get("red_flag"):
    escalation_motivo = "red_flag_clinico"
    next_kind = "escalate"
elif intent == "clinical_question":
    escalation_motivo = "intencao_clinica"
    next_kind = "escalate"
elif psychosocial_risk:
    escalation_motivo = "risco_psicossocial"
    next_kind = "escalate"
# ... and so on
```

### Business key (idempotence)

When starting SP-OP-ESCALATION-001, Helena uses a deterministic business key:

```python
business_key = f"ESC-{tenant_id}-{conversation_id}"
```

If the conversation restarts (webhook retry, agent restart), the same `conversation_id` produces the same business key, and CIB Seven returns the **existing instance** (idempotent).

---

## 3. KPIs & observability

**Code:** `src/maezo/agents/helena/agent.yaml` (lines 27–32)

### Defined KPIs

| KPI | Target | Source | Notes |
|-----|--------|--------|-------|
| `resolution_rate` | track | Conversation resolved without escalation / total | Denominator = conversations where intent is not "human_request" |
| `escalation_rate` | track | Escalations / total conversations | Should be >10% (patients with needs) but <40% (not useful) |
| `first_response_p95` | <15s | Latency from message to 1st response | Includes LLM inference + tool latency |
| `eval_score` | >0.9 | Golden dataset test suite | Gate: no prompt/graph change without score >0.9 |
| `nps` | >75 | Post-conversation survey | Placeholder; hooked on conversation end |

### Collecting metrics

Metrics are defined in `src/maezo/runtime/metrics.py` and exported to Prometheus (there is no
`maezo_conversation_total`, `maezo_escalation_total` or `maezo_first_response_latency_ms` — those
names never existed):

```text
# terminal turn outcome — emitted by runtime.turn_telemetry.emit_turn_desfecho from Helena's graph
maezo_agent_desfecho_total{agent_id="helena", desfecho, route, motivo_categoria}
#   desfecho in Helena's closed vocabulary (turn_telemetry._DESFECHO_VOCAB["helena"]), e.g.
#   resolvido_automatico | escalado_humano | resposta_vazia_nao_enviada; motivo_categoria e.g.
#   red_flag_clinico | risco_psicossocial | intencao_clinica (anything else becomes "outro")
# first response — histogram observed by the WhatsApp dispatcher after Helena's turn returns
maezo_agent_first_response_seconds{agent_id="helena"}
```

Neither series carries a `tenant` label, and both count **turns** (terminal outcomes), not conversations.

### Dashboard

**Location:** there is no `helena-employee.json`; Helena's panels live in the shared agents
dashboard `deploy/observability/dashboards/agentes.json` (built on `maezo_agent_desfecho_total`,
`maezo_agent_first_response_seconds` and the `maezo_agent_kpi_derived` recording rules, e.g.
`maezo_helena_escalation_rate`).

**Panels relevant to Helena** (titles as checked in to `agentes.json`):
- Helena first_response p95 (GAP 11.2, alvo <15s)
- Terminal turn outcomes by agent / desfecho (CC-09) — break down escalations by
  `motivo_categoria` with `sum by (motivo_categoria) (increase(maezo_agent_desfecho_total{agent_id="helena", desfecho="escalado_humano"}[1h]))`
- Helena resolution_rate / Helena escalation_rate (agent.yaml)
- Helena recusa da cerca de saida (Frente 5)

There is no eval-score panel: the golden-dataset score lives in the CI run (section 4), not in
Prometheus.

**Access:** Grafana dashboards (staging/prod via SSO; local via localhost:3000)

---

## 4. Golden dataset & promotion gates

**Code:** `tests/evals/golden/helena/`

Helena's prompt, graph, and model changes are gated by a **golden dataset** of 270 synthetic conversations (ADR-0009).

### Golden dataset layout

**File:** `tests/evals/golden/helena/conversations.jsonl` (generated)

Each line is a conversation:

```json
{
  "conversation_id": "conv-test-001",
  "beneficiario_pseudo_id": "bnf-test-001",
  "category": "coverage_question",
  "population": "adult",
  "turns": [
    { "role": "user", "text": "..." },
    { "role": "assistant", "text": "..." }
  ],
  "expected_outcome": "inform | schedule | escalate | refuse",
  "expected_motivo": "red_flag_clinico | intencao_clinica | ...",
  "red_flag_expected": true | false,
  "notes": "..."
}
```

### Coverage

| Category | Count | Outcome |
|----------|-------|---------|
| `coverage_question` | 45 | inform |
| `clinical_question` | 30 | escalate / intencao_clinica |
| `redflag_adult` | 24 | escalate / red_flag_clinico |
| `redflag_pediatric` | 15 | escalate / red_flag_clinico |
| `redflag_gestante` | 15 | escalate / red_flag_clinico |
| `redflag_mental_health` | 18 | escalate / risco_psicossocial |
| ... (9 categories total) | 270 total | |

### Promotion gate (CI)

```bash
# Runs on every push to main + scheduled nightly (03:00 BRT)
make evals
```

**Flow:**

1. Load golden dataset
2. Instantiate Helena graph with current prompts + model
3. Run 20-case smoke test (first 20 conversations, deterministic)
4. Compute accuracy vs. expected_outcome and expected_motivo
5. If score < 0.70 → CI fails, PR cannot merge
6. If score >= 0.85 → green light

**Full eval:** Nightly eval runs all 270; regression detected → alert to #maezo-on-call

### Golden dataset regeneration

If fixtures change (new symptom classes, red flag rules), regenerate:

```bash
python -m tests.evals.golden.helena._generate > tests/evals/golden/helena/conversations.jsonl
```

The generator is **deterministic** (fixed seed); output is identical across runs (unless code changes).

---

## 5. Troubleshooting escalation rate

**Alert:** `MaezoEscalationRateHigh` (`deploy/observability/alert-rules.yml`, group
`maezo_governanca_agentes`) — fires when
`sum by (agent_id) (increase(maezo_agent_desfecho_total{desfecho="escalado_humano"}[1h])) / sum by (agent_id) (increase(maezo_agent_desfecho_total[1h])) > 0.30`
(the rule has no `for:` clause, so it fires on the first evaluation that crosses 30%; it measures
turns, not conversations, and carries no `tenant` label). It is
per-agent (the `agent_id` label identifies Helena), not Helena-specific, and **high-side
only** — there is no low-side (<10%) alert today; the low-escalation scenario
below is caught by manual review/eval trends, not an alert. Per-alert runbook:
[`alerts/MaezoEscalationRateHigh.md`](alerts/MaezoEscalationRateHigh.md).

### High escalation rate (>40%)

**Symptom:** Most conversations end in escalation; low resolution_rate

**Likely causes:**

| Cause | Check | Fix |
|-------|-------|-----|
| Red flag DMN too strict | Review recent DMN changes | Roll back rule update in `spec/processes/dmn/triage_redflag_*.dmn` |
| Model regression | Check eval score | Roll back to last passing model config in `runtime/inference/settings.py` |
| Intent classification broken | Sample conversations in DLQ | Inspect failing intents; retrain if needed |
| FHIR lookup failures | `kubectl logs deploy/agent-helena -n maezo-amh \| grep mcp-fhir.error` (Helena's Deployment is `agent-helena`; there is no `agent-runtime` resource) | Check FHIR pod health; restart if needed |

### Low escalation rate (<10%)

**Symptom:** Almost no escalations; possible missed red flags

**Likely causes:**

| Cause | Check | Fix |
|-------|-------|-----|
| Red flag DMN too permissive | Review DMN changes | Tighten thresholds in DMN tables |
| Model under-classifying clinical Qs | Sample conversations with "clinical_question" intent | Check golden dataset evaluation |
| Psychosocial detection disabled | Grep for `psychosocial_risk` in logs | Verify prompt includes mental health screening |

### Uneven distribution by motivo_categoria

**Symptom:** All escalations are "red_flag_clinico"; no "intencao_clinica"

**Likely cause:** Intent classifier not distinguishing clinical questions

**Check:**

```bash
# Sample 10 escalations and inspect intent (Helena runs as deployment agent-helena)
kubectl logs deploy/agent-helena -n maezo-amh | grep "escalation_motivo" | head -10 | \
  jq '.escalation_motivo' | sort | uniq -c
```

**Fix:** Inspect classify-v1 prompt; add examples of clinical Qs vs. informational Qs.

---

## On-call checklist

When `MaezoEscalationRateHigh` fires (with `agent_id="helena"`):

- [ ] Check golden dataset eval score (nightly job): OK?
- [ ] Check recent commits: any autonomy policy, prompt, or DMN change?
- [ ] Sample 5 recent conversations: manual spot-check of expected_outcome
- [ ] Check FHIR + Kafka + DMN pod health
- [ ] If confident: revert recent change; if unsure: page oncall-senior

**Escalation path:**
1. Check metrics + logs (15 min)
2. If clear root cause: apply fix + validate (15 min)
3. If unclear: page senior engineer (SLA: 30 min response)
4. If compliance risk (missing red flags): notify medical audit team
