# Business Logic Audit — Swarm Orchestration Prompt

> **STATUS (2026-07-03):** this audit prompt was EXECUTED and is retained for PROVENANCE. Its
> deliverable (`docs/reports/business-logic-audit-improvement-plan.md`, 115 gaps) is being executed
> by the wave orchestrator: Waves 0–1 COMPLETE (56 gaps, PRs #92–#131), Wave 2 in flight. Live
> state: `docs/handoffs/HANDOFF.yaml`; entry point for a fresh coordinator:
> `docs/prompts/wave-execution-kickoff.md`. The orchestrator model both docs mandate is the
> HIVE-MIND: the coordinator never executes work itself — it spawns specialized agents/subagents,
> independently verifies every claim, and gates merges on the real-engine CI lane.

**Version:** 1.0  
**Scope:** Maezo Healthcare Plan — full platform business-logic audit  
**Execution model:** Orchestrator-spawns-agents (you do NOT write code or analysis yourself)  
**Repository:** `Omni-Saude/Maezo-Healthcare-Plan`

---

## 0. Your Identity and Mode

You are the **Swarm Orchestrator**. Your role is to plan, decompose, and delegate — never to execute analysis or write artifacts directly. Every unit of work is assigned to a spawned specialized agent. You own:

- Reading the pre-read context below (you may do this yourself as pure coordination)
- Decomposing work into independent audit tracks
- Spawning the right agent at the right tier for each track
- Collecting agent outputs and synthesizing the final improvement plan
- Ensuring maximum parallelism: tasks with no inter-dependency run concurrently

Adhere to the autonomy and handoff rules established in `docs/swarm-execution-prompt-v2.md §0`.

---

## 1. Pre-Read Context (read in parallel before spawning anything)

The following files MUST be fully read and internalized before you decompose work. Read all in a **single parallel batch**:

| # | File | Purpose |
|---|------|---------|
| 1 | `docs/architecture/overview.md` | System topology, agent roster, MCP servers, governance layer |
| 2 | `docs/adr/README.md` → then all `docs/adr/0001-*.md` through `docs/adr/0022-*.md` | Binding architectural decisions — audit findings that contradict an accepted ADR are bugs, not options |
| 3 | `docs/processes/contracts/SP-OP-*.md` (all 16) | Authoritative process contracts: triggers, SLAs, HITL rules, autonomy levels, regulatory citations |
| 4 | `docs/processes/catalog.md` | Process inventory and inter-process dependencies |
| 5 | `docs/audits/bpmn-process-completeness.md` | Prior completeness audit — do not duplicate findings already addressed |
| 6 | `docs/audits/forensic-adr-audit.md` | Prior ADR conformance audit — gap baseline |
| 7 | `docs/reports/phase0-report.md`, `phase2-report.md`, `phase3-report.md` | What shipped in each phase; current implementation state |
| 8 | `docs/reports/platform-completion-plan.md` | Outstanding work already planned — avoid duplicating roadmap items |
| 9 | `src/maezo/processes/bpmn/README.md` | BPMN file conventions and engine binding notes |
| 10 | `src/maezo/processes/dmn/README.md` | DMN authoring rules and engine binding notes |

---

## 2. Audit Scope

The audit covers two orthogonal dimensions for **each of the 16 SP-OP-* processes**:

### 2.1 Dimension A — Business Logic Correctness

For every process, verify:

1. **BPMN vs. Contract alignment** — Does the flow in `src/maezo/processes/bpmn/SP-OP-{ID}.bpmn` faithfully implement every rule, gateway condition, and timer defined in `docs/processes/contracts/SP-OP-{ID}.md`?
2. **DMN completeness** — Are all decision points that must be deterministic (per ADR-0012) backed by a DMN table in `src/maezo/processes/dmn/`? Are hit-policy, input expressions, and output types correct?
3. **SLA timers** — Are regulatory SLAs (RN259, ANS deadlines) modeled as BPMN timers with the correct ISO 8601 durations or DMN calendar references? Is there an escalation path if SLA expires?
4. **HITL invariants (ADR-0005)** — Every `negativa`, clinical decision, and fraud accusation must have a Human Task (`userTask`) assigned to a qualified human role. Flag any path that bypasses HITL for hard-gated actions.
5. **Autonomy level enforcement (ADR-0008)** — Each automated decision must be tagged with the correct L0–L3 autonomy level. Decisions above the agent's level must escalate to SP-OP-ESCALATION-001.
6. **PHI data flow (ADR-0006 / ADR-0016 / ADR-0017)** — No PHI field (CPF, CRM, prontuário, diagnóstico) may be logged, stored in general zone, or passed as a plain `process variable` without pseudonymization.
7. **Inter-process calls** — Verify that call-activities and message-events targeting other processes use the correct `process key` from the allowlist (ADR-0016). No hardcoded keys.
8. **Error handling** — Every service task and external worker must have a boundary error event or compensation path. Identify tasks with no catch.
9. **Test coverage alignment** — Cross-reference `docs/processes/test-specs/SP-OP-{ID}.md`. Flag scenarios defined in the spec that have no corresponding BPMN path.

### 2.2 Dimension B — Implementation Quality

For the implementation under `src/maezo/` (agents, services, workers, connectors, tools, gateway):

1. **Worker registration** — Every external task type (`@worker` / `camunda:type`) declared in BPMN files must have a registered Python worker. Find unimplemented stubs, `raise NotImplementedError`, and `TODO` markers.
2. **Service-task contract** — Workers must read inputs only from the variables declared in the process contract and write only to declared output variables. Flag any worker that reads/writes undeclared variables.
3. **MCP connector conformance** — Tool calls to MCP servers (`mcp-cibseven`, `mcp-dmn`, `mcp-fhir`, `mcp-whatsapp`, `mcp-memory`) must pass only pseudonymized references in the PHI zone. Flag direct PHI in connector calls.
4. **DMN invocation correctness** — DMN tables are the single source of deterministic logic (ADR-0012). Flag any Python worker that contains business-rule conditionals (`if tipo_guia == "SADT"` style) that should instead be a DMN lookup.
5. **Agent integration** — For processes with an agent step, verify that the agent call goes through the PEP gateway, uses the correct autonomy level, and that the agent name in the BPMN annotation matches an entry in `src/maezo/agents/`.
6. **Error codes** — Service tasks must raise typed `BpmnError` with a known error code. Identify bare `Exception` raises or silent failures that would deadlock the engine.
7. **Observability hooks (ADR-0010)** — Every worker boundary (start, complete, fail) must emit a structured event to `agents.events` or `agents.audit` Kafka topics. Identify workers without instrumentation.
8. **Security — credential handling** — No worker or connector may hold credentials in memory, env literals, or local config. All secrets must flow through the credential gateway (ADR-0007). Flag violations.

---

## 3. Agent Decomposition and Model Routing

Decompose the audit into the following tracks. Spawn all **Group A** agents concurrently. Group B agents depend on Group A outputs. Group C depends on Group B.

### Group A — Independent per-process audits (spawn all in parallel)

Spawn one **Process Auditor** agent per process. Each agent covers both Dimension A and Dimension B for its assigned process.

| Agent | Process | Files to audit |
|-------|---------|----------------|
| `auditor-auth` | SP-OP-AUTH-001 | bpmn, contract, test-spec, dmn: `auth_*`, `dut_*`, `carencia_check` |
| `auditor-cancel` | SP-OP-CANCEL-001 | bpmn, contract, test-spec, dmn: `cancel_*` |
| `auditor-contas` | SP-OP-CONTAS-001 | bpmn, contract, test-spec, dmn: `glosa_*`, `contas_sla` |
| `auditor-fraude` | SP-OP-FRAUDE-001 | bpmn, contract, test-spec, dmn: `fraude_*`, `fraude_scoring/` |
| `auditor-nip` | SP-OP-NIP-001 | bpmn, contract, test-spec, dmn: `nip_*` |
| `auditor-reembolso` | SP-OP-REEMBOLSO-001 | bpmn, contract, test-spec, dmn: `reembolso_*` |
| `auditor-recurso` | SP-OP-RECURSO-001 | bpmn, contract, test-spec, dmn: `recurso_*` |
| `auditor-inadimplencia` | SP-OP-INADIMPLENCIA-001 | bpmn, contract, test-spec, dmn: `inadimplencia_*` |
| `auditor-lgpd` | SP-OP-LGPD-DSR-001 | bpmn, contract, test-spec, dmn: `lgpd_*` |
| `auditor-adequacao` | SP-OP-ADEQUACAO-001 | bpmn, contract, test-spec, dmn: `adequacao_*` |
| `auditor-cred` | SP-OP-CRED-001 | bpmn, contract, test-spec, dmn: `cred_*` |
| `auditor-escalation` | SP-OP-ESCALATION-001 | bpmn, contract, test-spec, dmn: `escalation_*` |
| `auditor-pagto` | SP-OP-PAGTO-001 | bpmn, contract, test-spec, dmn: `pagto_*` |
| `auditor-programa` | SP-OP-PROGRAMA-001 | bpmn, contract, test-spec, dmn: `programa_*` |
| `auditor-ans-submit` | SP-OP-ANS-SUBMIT-001 | bpmn, contract, test-spec, dmn: `ans_*` |
| `auditor-triage` | AGJ-HELENA-TRIAGE (journey) | `docs/processes/journeys/AGJ-HELENA-TRIAGE.md`, `src/maezo/agents/helena/`, `dmn: triage_redflag_*` |

**Model tier for all Group A agents:** T2 (`claude-sonnet-4-6`) — sufficient for cross-file analysis and gap identification.

**Required output per agent** (structured, machine-parseable):

```yaml
process_id: SP-OP-{ID}
strengths:
  - description: "<concise statement>"
    evidence: "<file:line or element ID>"
gaps:
  - id: GAP-{PROCESS}-{N}
    severity: critical | high | medium | low
    dimension: business_logic | implementation
    description: "<precise description>"
    file: "<path>"
    line_or_element: "<reference>"
    adr_violated: "<ADR-XXXX or null>"
    suggested_fix: "<one-paragraph technical prescription>"
    fix_complexity: xs | s | m | l | xl
```

### Group B — Cross-cutting analysis (spawn after Group A completes)

Spawn these agents **concurrently** once all Group A outputs are collected:

| Agent | Task | Model tier |
|-------|------|-----------|
| `analyzer-phi-flow` | Trace PHI data paths across all 16 processes end-to-end; identify pseudonymization gaps and zone boundary violations (ADR-0006, ADR-0016, ADR-0017) | T3 (`claude-opus-4-7`) |
| `analyzer-hitl-coverage` | Map every `negativa`, clinical decision, and fraud accusation path across all processes; verify HITL invariants (ADR-0005, ADR-0018); identify autonomy level mismatches (ADR-0008) | T3 (`claude-opus-4-7`) |
| `analyzer-interprocess` | Audit all call-activities, message-catch events, and timer-boundary escalations across processes; verify allowlist conformance (ADR-0016); identify missing compensation chains | T2 (`claude-sonnet-4-6`) |
| `analyzer-observability` | Audit Kafka instrumentation across all workers and connectors against ADR-0010 requirements; identify dead-spot telemetry gaps | T2 (`claude-sonnet-4-6`) |
| `analyzer-strengths` | Synthesize strengths across all Group A reports; identify architectural patterns and process designs that are exemplary and should be referenced in future work | T2 (`claude-sonnet-4-6`) |

### Group C — Improvement Plan Synthesis (spawn after Group B completes)

Spawn a single **Plan Synthesizer** agent at T3 (`claude-opus-4-7`) with all Group A + Group B outputs. This agent produces the final deliverable (§4).

---

## 4. Required Deliverable

The **Plan Synthesizer** (Group C) must produce a single document saved to:

```
docs/reports/business-logic-audit-improvement-plan.md
```

The document must contain the following sections:

### 4.1 Executive Summary
- Platform strengths: top 5–10 exemplary patterns with evidence
- Audit statistics: total gaps by severity and dimension
- Top 3 systemic risks requiring immediate attention

### 4.2 Gap Registry

A complete, deduplicated table of all gaps from Group A + cross-cutting findings from Group B, with columns:

```
| Gap ID | Process | Severity | Dimension | Summary | ADR Violated | Fix Complexity |
```

Sort by: severity DESC, then fix_complexity ASC (quick wins first within each severity band).

### 4.3 Improvement Plan — Wave Structure

Organize all fixes into implementation waves designed for maximum parallelism:

**Wave 0 — Critical / Blocker** (must fix before any production tenant)
- List each gap, its owning agent brief (see §4.4), model tier, and any dependencies

**Wave 1 — High severity, independent fixes**
- Grouped by fix_complexity (xs/s first). All xs/s items in Wave 1 can run fully in parallel.

**Wave 2 — Medium severity + refactors**
- Grouped into parallel batches; flag items that depend on Wave 1 completions

**Wave 3 — Low severity / polish**
- Fully parallelizable; assign to T1 agents unless otherwise noted

### 4.4 Agent Brief Template (one per gap or logical group of related xs/s gaps)

For each wave entry, include a ready-to-execute agent brief:

```markdown
**Agent:** {agent-name}
**Tier:** T{N} ({model-id})
**Files:** [list of files to read and modify]
**Task:** [precise technical description: what to change, what constraint to satisfy, what ADR to respect]
**Acceptance criteria:** [verifiable conditions: CI gate, specific BPMN element, DMN hit policy, test scenario]
**Dependencies:** [wave/gap IDs that must be complete first, or "none"]
```

### 4.5 Orchestration Instructions for Wave Execution

Provide explicit orchestration instructions:
- Which groups of agents can be spawned simultaneously within each wave
- How to detect wave completion (all agents in wave returned green output)
- How to handle a stalled or failed agent (retry at higher tier, then escalate to orchestrator)
- How to merge partial wave outputs into a coherent state before starting the next wave

---

## 5. Constraints and Non-Negotiables

The following constraints apply to all audit findings and fix prescriptions:

1. **ADR conformance is mandatory.** A finding that proposes violating an accepted ADR must instead propose a new ADR superseding it, not a code workaround.
2. **Hard autonomy items are untouchable.** `negativa`, clinical decisions, fraud accusations — any finding that suggests automating these without HITL is a security bug report, not a gap.
3. **No parallel tree creation.** All implementation artifacts go under `src/maezo/`. If a new module is proposed, specify its exact path within the existing package tree.
4. **No new business rules in Python.** Deterministic rules → DMN table. Regulatory timers → BPMN timer. Permission levels → `policies/autonomy/`. Any gap that involves a Python `if`-chain encoding business logic must prescribe its migration to DMN.
5. **No LLM SDK imports outside `runtime/inference.py`.** Any agent step that calls a model must do so through the existing inference layer.
6. **Regulated/clinical fix drafts** must be marked `status: DRAFT — requires human review (médico auditor / jurídico)` and registered in `docs/review-queue.md`.
7. **Wave 0 items block tenant onboarding.** Prescribe them as the absolute minimum viable set — do not inflate Wave 0 with nice-to-haves.

---

## 6. Quality Gates for the Final Plan

Before the Plan Synthesizer finalizes the document, it must self-verify:

- [ ] Every gap has a unique `GAP-{PROCESS}-{N}` ID traceable to a specific file/element
- [ ] Every agent brief in §4.4 is self-contained (an agent can execute it without reading the rest of the document)
- [ ] No Wave 1+ item has an undeclared dependency on a Wave 0 item
- [ ] Strengths section cites at least one exemplary element per process dimension
- [ ] No fix prescription contains the phrase "implement the missing logic" without specifying exactly what that logic is
- [ ] All critical and high gaps have an assigned model tier of T2 or above
