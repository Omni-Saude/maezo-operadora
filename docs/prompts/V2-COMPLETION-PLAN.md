# V2 Completion Plan — maezo-operadora → Production-Ready

> **Origin:** Independent adversarial review, 2026-07-16 (supersedes the 8–12-week plan in `audit-maezo/COMPARATIVO-FINAL.md` §3–5 and the self-certified statuses in `PLANS.md`).
> **Executor:** Fable 5 orchestrator spawning **specialized** agents/subagents with intelligent model routing (see §2–§3).
> **Horizon:** 16–24 weeks. External critical paths: SME contract review, AWS credentials, 6 production secrets.
> **Prime directive:** every "done" requires independent verification with `path:line` evidence. No self-certification — this repo's history (32 commits in ~9h declaring 15/15 milestones) is the cautionary tale.

---

## 0. Ground Rules (apply to every task)

1. **Fail-closed by default.** Any gate, evaluator, or guard that cannot decide must DENY/FAIL, never warn-and-pass.
2. **Author ≠ verifier.** The agent that implements a task never verifies it. Verification is a separate spawn with read-only access.
3. **Evidence ledger.** Every completed task appends an entry to `docs/evidence-ledger.md`: task ID, commit SHA, verifier agent, evidence `path:line`, test run output hash.
4. **One source of truth for artifacts:** `spec/` is canonical for BPMN/DMN/agent.yaml/policies. No copies in `src/`.
5. **Git discipline:** one branch + PR per task ID; conventional commits; main protected; no force-push.
6. **No fabricated integrations.** If an external system (ANS webservice, WhatsApp, Tasy) can't be reached, the boundary must be an *explicitly labeled* mock behind an interface — never silent fabrication (cf. `ans_submit.py:209-212` fabricating protocols from `sha256(time_ns)`).

---

## 1. Verified Baseline (why each workstream exists)

| # | Defect (verified 2026-07-16) | Evidence |
|---|---|---|
| B1 | No runtime spine: no entrypoint, no external-task dispatch loop, no BPMN deploy, no Kafka client; LLM raises `NotImplementedError` | `runtime/inference.py:104-107`; `values.yaml:124-127`; `worker_registry` used only by tests |
| B2 | Validation & sign-off CI gates are always-pass stubs | `platform/validation/cli.py:96-121`; `Makefile:25`; `_hard_frozen.yaml:4-6` |
| B3 | Reimbursement ceiling bypass — auto-approve manufactures its own `dentro_teto_l2` while policy says `max_value_brl: 0` | `workers/reembolso.py:230,267+`; `spec/policies/autonomy/L0-core.yaml:24` |
| B4 | PEP never loads `spec/policies/autonomy/*.yaml`; hardcoded matrix; vocabulary disjoint (PT vs EN); `nip_manter_negativa`/`contract_termination` missing from deny set | `gateway/pep.py:22-28,51+` |
| B5 | DMN layer dead at runtime: `DmnServer` imported nowhere; workers re-implement rules in Python; evaluator lacks comparison operators for 14/54 tables and fails open (`{}`) | `mcp_dmn/server.py:203-205,232`; `inadimplencia.py:80`; `contas.py:271` |
| B6 | All 10 agent graphs are stubs; zero import tools/DMN/LLM; no prod code invokes any graph | `agents/rafael/graph.py:4,42`; `agents/helena/graph.py:53-58` |
| B7 | AuditSink in-memory only; audit-chain schema exists unused | `gateway/audit.py:85`; `migrations/versions/0002_audit_chain.py` |
| B8 | 15/16 contracts DRAFT, zero SME review; review queue points SMEs at 148 nonexistent `src/maezo/processes/` paths across 30 doc files | `docs/processes/contracts/*:3`; `docs/review-queue.md` |
| B9 | Zero integration tests / evals; CI integration lane expected-red by design; README falsely claims real-engine CI & active CodeQL | `ci.yml:201-204`; `README.md:127,129`; `security.yml:18-64` |
| B10 | Fraud scoring is `len(evidencia)*10` placeholder; the 7 `fraude_scoring` DMNs the contract promises were never ported | `workers/fraude.py:113-116`; `SP-OP-FRAUDE-001.md:133` |
| B11 | Never deployed: placeholder ECR account, no tfstate, commented backend, 6 secret shells blocked | `values*.yaml:16-25`; `secrets/main.tf:6-12` |
| B12 | Regulatory currency: RN 388/259/412 likely superseded (RN 483/566/593); RN 585/623 absent; no TISS version/XSD; SIB absent repo-wide | `nip` contract; `ans_submit.py:145,156` |
| B13 | 13/16 worker modules bypass `WorkerBase` (no retry/metrics); registry can't accept them | `workers/base.py:40,107-185,231` |
| B14 | agent.yaml duplicated spec/ vs src/ with drift in 6/10 agents; broken compose mounts (`config/` empty); `tasy_simulator.Dockerfile` missing | `docker-compose.yml:123,147,191-193` |

---

## 2. Model Routing Policy (token-optimization)

Route by **ambiguity × blast radius**, escalate on failure (haiku → sonnet → opus → fable):

| Tier | Model class | Use for | Never for |
|---|---|---|---|
| R0 | **Fable 5** (orchestrator only) | Plan arbitration, phase-gate reviews, cross-workstream reconciliation, kill decisions | Bulk file edits, scans |
| R1 | **Opus-class** | Architecture/ADR decisions, DMN engine strategy, PEP/policy unification design, regulatory analysis, adversarial gatekeeping, root-cause debugging | Mechanical refactors |
| R2 | **Sonnet-class** | Well-specified implementation (code + tests), test porting, CI/Terraform/Helm work, migration writing | Open-ended design |
| R3 | **Haiku-class** | Fan-out scans, dead-path fixes across 30+ files, inventories/counts, doc regeneration, formatting, citation extraction, ledger upkeep | Anything touching guards, policies, or money paths |

Rules: (a) verification agent runs one tier **above or equal to** the author's tier for B1–B7 tasks, same tier elsewhere; (b) fan-out searches always R3; synthesis of fan-out always R1+; (c) any task touching L0 invariants, financial approval, or PHI is R1 minimum with R1 verification.

## 3. Specialized Agent Roster (non-generic)

| Agent | Tier | Charter |
|---|---|---|
| `runtime-spine-engineer` | R1 design / R2 build | External-task client, dispatch loop, entrypoints, LLM providers (WS-1) |
| `process-engine-specialist` | R1/R2 | BPMN/DMN deployment to CIB Seven, engine-side evaluation, timer/SLA semantics (WS-1) |
| `policy-guardian` | R1 | PEP↔YAML unification, L0 invariants, ceilings, autonomy matrix (WS-1; sole owner of B3/B4) |
| `audit-persistence-engineer` | R2 | AuditSink → Postgres chain, versioning columns, Kafka decision (B7) |
| `agent-graph-builder` | R2 | Wire Helena/Rafael + staged rollout of remaining 8 graphs (B6) |
| `gates-engineer` | R2 | Real validate-artifacts/signoff, CI lanes, coverage thresholds (WS-2; B2/B9) |
| `security-hardener` | R2 | CodeQL, trivy, SBOM, action pinning, checksum, secrets hygiene (WS-2) |
| `compliance-analyst` | R1 | RN currency, TISS/SIB scoping, contract redlines for SMEs, LGPD/RIPD (WS-2; B8/B12) |
| `sme-liaison` | R2 | Package DRAFT contracts for humans, track sign-offs, maintain review-queue (human interface — never approves on humans' behalf) |
| `test-harness-porter` | R2 | Port v1's 50 integration tests + 44 evals from `Maezo-Healthcare-Plan` (WS-3) |
| `docs-hygienist` | R3 | Dead-path epidemic, stale counts, truth-reset edits (WS-0) |
| `infra-deployer` | R2 | terraform apply, ESO two-phase, staging bring-up (WS-4) |
| `adversarial-verifier` | R1 | Independent verification of every task; runs the Phase-3 predeploy audit; reports kill-rate |
| `regulatory-gatekeeper` / `security-gatekeeper` | R1 | Phase-gate sign-off; can block any phase exit |

---

## 4. Phases, Workstreams, Tasks

Effort: S <1d · M 1–3d · L 1–2wk · XL >2wk (agent-days, not calendar).

### PHASE 0 — Truth Reset (Week 1) — exit gate G0

| ID | Task | Fixes | Agent/Tier | Effort | Acceptance criteria |
|---|---|---|---|---|---|
| T0.1 | Correct all false/stale claims: README (553→588 tests, CodeQL "active", "integration real-engine"), PLANS.md 15/15 → "unverified", pyproject `1.0.0`/`Production/Stable` → `0.x`/`Alpha` | B9 | docs-hygienist R3 | S | grep finds zero false claims; adversarial-verifier diff review |
| T0.2 | Fix dead references: 148× `src/maezo/processes/` (30 files, incl. `docs/review-queue.md`, `catalog.md:28-29`, ESCALATION contract), 23× `tests/architecture/`, 11× `fraude_scoring` | B8/B14 | docs-hygienist R3 | M | `grep -r "src/maezo/processes" docs/ spec/` → 0 hits |
| T0.3 | Single artifact source of truth: delete `src/maezo/agents/*/agent.yaml`, loader reads `spec/agents/`; reconcile the 6 drifted files first | B14 | runtime-spine-engineer R2 | M | No duplicate agent.yaml; drift diff archived in ledger |
| T0.4 | Fix broken infra refs: compose `config/otel-collector.yaml` & `prometheus.yml` mounts → `deploy/observability/`; create otel-collector.yaml; remove or implement `tasy_simulator.Dockerfile`; fix `Makefile` validate paths | B14 | infra-deployer R2 | M | `docker compose --profile core,observability config` passes; simulator profile builds or is removed |
| T0.5 | Create `docs/evidence-ledger.md` + CI check that PRs closing a task ID carry a ledger entry | rules | gates-engineer R2 | S | CI job green on a sample PR, red without entry |
| T0.6 | Send 15 DRAFT contracts to SMEs (médico-auditor, jurídico, DPO, regulatório, finanças, PO) with corrected paths — **starts now, longest pole** | B8 | sme-liaison R2 | S (start) | Dispatch receipts logged; tracker in review-queue |

**G0 exit:** all acceptance criteria verified by adversarial-verifier; zero false claims greppable.

### PHASE 1 — Runtime Spine (Weeks 1–8) — exit gate G1 — *unblocks everything*

| ID | Task | Fixes | Agent/Tier | Effort | Acceptance criteria |
|---|---|---|---|---|---|
| T1.1 | External-task client (fetch&lock vs CIB Seven REST), long-poll loop, topic routing via `WorkerRegistry`; graceful shutdown; backoff | B1 | runtime-spine-engineer R1→R2 | XL | Integration test: engine task → worker executes → completes/fails with retry |
| T1.2 | Worker standardization: adapter so 13 function-based modules register in `WorkerRegistry` (decision: adapter vs subclassing — ADR required); retry/metrics from `WorkerBase.run()` applied to all 16 | B13 | runtime-spine-engineer R2 | L | All 16 modules registered; metrics emitted per worker; no dead registry |
| T1.3 | BPMN/DMN deployment tooling: `make deploy-artifacts` pushes `spec/processes/**` to engine; idempotent; versioned deployments | B1/B5 | process-engine-specialist R2 | M | All 16 BPMN + 54 DMN deployed to local engine; deployment listed via REST |
| T1.4 | DMN evaluation strategy (ADR): **prefer engine-side evaluation** via CIB Seven; local `mcp_dmn` demoted to dev tool — if kept, implement `>=/<=/>/<` + fail-CLOSED on no-match (14/54 tables affected) | B5 | process-engine-specialist R1 | L | Every DMN table evaluated by conformant engine in tests; no-match raises, never `{}` |
| T1.5 | Delete Python re-implementations of DMN rules in workers (`inadimplencia.py:80`, `contas.py:271`, `recurso.py:168`, `ans_submit.py:280`) — workers call DMN, single source of truth (ADR-0012 restored) | B5 | process-engine-specialist R2 | L | grep "DMN-like" → 0; workers' decisions traced to DMN evaluations in audit log |
| T1.6 | Entrypoints: `gateway/__main__.py` (FastAPI app), webhook-receiver, worker-runtime service; `[project.scripts]`; Helm `command:` aligned; enable gateway in values | B1 | runtime-spine-engineer R2 | L | `docker compose` runs app services; `/healthz` live; Helm template renders runnable pods |
| T1.7 | LLM providers: implement Anthropic + BR-resident PHI-zone provider behind `inference.py`; keep `noop` for tests; SDK added to pyproject | B1 | runtime-spine-engineer R2 | M | Non-noop provider returns completion in integration test (key from env, never committed) |
| T1.8 | **PEP loads `spec/policies/autonomy/*.yaml`**; single action vocabulary (migration table PT↔EN, one canonical set); `nip_manter_negativa` + `contract_termination` in hard-deny; tenant overlays (`tenants-amh.yaml`) | B4 | policy-guardian R1 | L | Property test: every action in YAML resolvable by PEP; hard-frozen set == YAML frozen set; unknown action → DENY |
| T1.9 | **Remove `dentro_teto` bypass** (`reembolso.py:230`); implement ceilings (port v1 `ceilings.py`); `max_value_brl` enforced from policy; same audit for `auth.py:66` and `mcp_dmn/server.py:51` echo-through | B3 | policy-guardian R1 | M | Test: within-table request with `max_value_brl:0` routes ANALISE_HUMANA; no code path can set `dentro_teto_l2=True` except policy |
| T1.10 | AuditSink persistence: write chain to Postgres per `0002_audit_chain` (incl. `agent_version`/`dmn_versions` populated); Kafka: implement client **or** amend ADR-0007 to Postgres-only (decision, not drift) | B7 | audit-persistence-engineer R2 (R1 for ADR) | L | Kill-test: process crash loses zero audit records; chain hash verified; versioning columns non-null |
| T1.11 | Wire Helena (triage → DMN `triage_redflag_*` → escalation SP-OP-ESCALATION-001) and Rafael (gather FHIR → DMN auth → dossier → human gate) as first two real graphs; harness invokes graphs (replace `Harness.create_graph` trivial graph) | B6 | agent-graph-builder R2 | XL | E2E test: WhatsApp msg → Helena → red-flag → escalation process instance in engine; Rafael dossier task appears for medico-auditor |
| T1.12 | Remaining 8 agent graphs: implement against contracts in priority order (lucas, marina, fernando, carolina, andre, beatriz, gustavo, valentina); explicit stubs deleted | B6 | agent-graph-builder R2 | XL | Per-agent E2E happy-path + guard test |

**G1 exit:** one full flow (Helena→escalation, Rafael→dossier) runs end-to-end on local compose against real CIB Seven; B3/B4 closed with property tests; adversarial-verifier reproduces E2E independently.

### PHASE 2 — Real Gates & Regulatory Substance (Weeks 2–10, parallel to WS-1) — exit gate G2

| ID | Task | Fixes | Agent/Tier | Effort | Acceptance criteria |
|---|---|---|---|---|---|
| T2.1 | Implement validation CLI for real: `bpmn.py`, `dmn.py`, `policy.py`, `agent_def.py`; XML schema parse; contract↔BPMN↔DMN cross-refs; orphan detection (12/54 DMNs currently orphaned); **fail-closed**; delete greenfield stub + the tests blessing it (`e1c0b34`) | B2 | gates-engineer R2 | L | Mutation test: corrupt a DMN ref → CI red; missing artifact → CI red |
| T2.2 | Real `validate_signoff`: `*.signoff.yaml` metadata, promoted artifacts blocked without human sign-off; wire to review-queue | B2/B8 | gates-engineer R2 | M | Promoting a DRAFT contract without signoff file fails CI |
| T2.3 | CI surgery: integration lane = skip-with-visible-marker until T3.x lands (never expected-red); coverage gate ≥85% on `src/maezo`; fix `htmlcov` cargo-cult step; recalibrate `red-main-alarm` | B9 | gates-engineer R2 | M | Main green truthfully; alarm fires only on genuine red |
| T2.4 | Security hardening: enable CodeQL + dependency-review; trivy image scan; pin GH Actions to SHAs; checksum gitleaks download; SBOM (syft) + cosign signing in cd.yml; restore A2A agent-card signing (dropped from v1) | B9 | security-hardener R2 | L | security.yml all-live; provenance attached to images |
| T2.5 | Regulatory currency pass: verify/update every RN citation (RN 388→483/2022, 259→566/2022, 412→593; add RN 585, RN 623 where applicable); resolve business-day vs calendar-day SLA question (`auth_sla.dmn:12-14`) with jurídico | B12 | compliance-analyst R1 | L | Zero `DRAFT/verify` citations remaining without SME-confirmed source; SLA basis documented per DMN |
| T2.6 | TISS/ANS substance: pin TISS version + vendor XSDs; real XSD validation in `ans_submit.validate_data`; ANS webservice integration behind explicit interface (mock clearly labeled until credentials); **SIB scoping ADR** (implement vs out-of-scope with justification) | B12 | compliance-analyst R1 + runtime-spine-engineer R2 | XL | Fabricated-protocol path deleted; submission returns real or explicitly-mocked protocol; SIB decision ratified |
| T2.7 | Fraud scoring: port the 7 `fraude_scoring` DMNs from v1 **or** amend SP-OP-FRAUDE-001 to match the routing-facts design; delete `len(evidencia)*10` heuristic | B10 | process-engine-specialist R2 | M | Contract and code agree; scoring traceable to DMN or contract amended |
| T2.8 | LGPD: reconcile contract↔worker topic divergence (SP-OP-LGPD-DSR-001 vs `lgpd.py`); RIPD/DPIA kickoff with DPO; retention vs legal-hold conflict (ADR-0020) decided | B8/B12 | compliance-analyst R1 | L | Topics match contract; RIPD tracked as external deliverable |
| T2.9 | SME review completion: track 15 contracts to FINAL; per-contract redline cycles | B8 | sme-liaison R2 | XL (calendar) | 16/16 FINAL with signoff files (or explicit deferral ADR per contract) |

**G2 exit:** gates demonstrably fail-closed (mutation-tested); regulatory citations current; ≥12/16 contracts FINAL or formally deferred; both gatekeepers sign.

### PHASE 3 — Test Harness & Adversarial Audit (Weeks 8–14) — exit gate G3

| ID | Task | Agent/Tier | Effort | Acceptance criteria |
|---|---|---|---|---|
| T3.1 | Port v1's 50 integration tests (`Maezo-Healthcare-Plan/tests/integration/`) onto the new spine; adapt fixtures, not logic | test-harness-porter R2 | XL | All 16 processes have real-engine tests; L0 no-denial invariant proven per process |
| T3.2 | Port 44 agent evals + golden datasets; wire `evals` CI lane (currently `if: false`) | test-harness-porter R2 | L | Evals lane green; regression baseline stored |
| T3.3 | Port v1 cross-process tests (handoff seams, anti-dupla-terminação) + chaos/security suites | test-harness-porter R2 | L | Suites green on compose stack |
| T3.4 | Full adversarial predeploy audit of v2 (v1 methodology: independent finders, cross-validation, kill-rate metric); findings triaged to blocking/non-blocking | adversarial-verifier R1 (fan-out R3, synthesis R1) | XL | Report in `docs/reports/`; zero unresolved deploy-blocking findings |

**G3 exit:** union-green CI including integration + evals lanes; predeploy audit closed.

### PHASE 4 — Staging & Production Readiness (Weeks 12–20+) — exit gate G4

| ID | Task | Agent/Tier | Effort | Acceptance criteria |
|---|---|---|---|---|
| T4.1 | AWS staging: real account/registry values (replace `123456789012`), backend config, `terraform apply`, tfstate remote | infra-deployer R2 | L | Staging infra live; drift-checked plan clean |
| T4.2 | Populate 6 blocked secrets (WhatsApp×3, LLM, Tasy, PHI-HMAC) via ESO; rotate dev-derived PHI keys | infra-deployer R2 + security-hardener | M | ExternalSecrets synced; no `maezo.io/blocked` annotations remain |
| T4.3 | Two-phase Helm deploy rehearsal (DB-7 choreography) + migration hook + smoke tests against staging | infra-deployer R2 | L | Clean `helm install` + upgrade + rollback demonstrated |
| T4.4 | Observability completion: Grafana dashboards (dir currently empty), Alertmanager delivery path, AMP rules applied | infra-deployer R2 | M | Alert fires end-to-end to on-call channel |
| T4.5 | Real load/chaos/DR execution in staging (the M13 claims, actually done) | adversarial-verifier R1 + infra R2 | L | Reports with measured numbers in ledger |
| T4.6 | Gatekeeper sign-offs (security, compliance, production-validator) + go/no-go with Rodrigo | gatekeepers R1 / Fable orchestrator | M | Signed G4 record |
| T4.7 | Production deploy + on-call rotation + recalibrated red-main-alarm | infra-deployer R2 | M | First tenant live behind human-in-the-loop gates |

**G4 exit:** production deploy with all L0 invariants integration-proven, secrets live, audit chain persisting, SME-signed contracts.

---

## 5. Dependency Spine (critical path)

```
T0.6 (SME dispatch) ──────────────────────────► T2.9 ─► G2
T0.2/T0.3 ─► T1.1 ─► T1.2 ─► T1.11 ─► T1.12 ─► T3.1 ─► T3.4 ─► G3 ─► T4.* ─► G4
        T1.3 ─► T1.4 ─► T1.5 ─┘
        T1.8 ─► T1.9 (policy before money paths)
T2.1/T2.2 independent start; must land before G2
T4.1 blocked externally on AWS credentials — escalate week 1
```

## 6. Risk Register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| SME review slips (external humans) | High | G2/G4 slip | Dispatch week 1 (T0.6); per-contract incremental FINALs; deferral ADRs |
| v1 integration tests assume v1 runtime shapes | Med | T3.1 rework | T1.1/T1.6 API designed against v1 test fixtures' expectations (porter reviews spine PRs) |
| AWS/billing wall recurrence | Med | G4 slip | Budget + credentials requested at kickoff; LocalStack rehearsal meanwhile |
| DMN engine-side evaluation changes worker semantics | Med | Rework in 16 workers | T1.4 ADR before T1.5 mass change; golden-output tests per table |
| Agent-generated self-certification recurs | Med | Trust collapse | Ground rules 2–3 enforced by CI (T0.5); adversarial-verifier on every PR |
| ANS webservice access unavailable | High | T2.6 partial | Explicit mock boundary + contract test; production gate requires real credential or documented waiver |

## 7. Explicitly Out of Scope (require human/external action)

AWS account + credentials; the 6 secret values; SME availability (médico-auditor, jurídico, DPO, regulatório, finanças, PO); ANS webservice credentials; DPO designation & RIPD ownership; ANVISA SaMD legal opinion (GAP-C10); D-07 ceiling values (diretoria AMH).
