# MAEZO Healthcare Plan — Forensic Completeness Audit (ADRs 0001–0018)

> **Method.** Multi-agent fan-out, code-as-ground-truth. One verifier agent per ADR decomposed it
> into discrete normative claims, located the implementing code in `src/maezo/`, `config/`, `deploy/`,
> migrations, and the test that exercises it, then assigned a status (verified / unverified / partial /
> absent / contradicted) with `file:line` evidence. A coordinator synthesized the 18 structured results.
>
> **Run.** 20 agents — 1 context-builder + 18 verifiers (Sonnet) + 1 synthesis (Opus); 239 claims;
> 98.3% reached by reading code. Generated 2026-06-14 from branch `wave/w-r1-runtime-wiring`.
> Code is ground truth; ADR text is treated as claims to be falsified.

---

## 1. Completeness Matrix

| ADR | claims (n) | verified | unverified | partial | absent | contradicted |
|-----|-----------|----------|------------|---------|--------|--------------|
| 0001 | 8 | 6 | 1 | 0 | 1 | 0 |
| 0002 | 12 | 3 | 1 | 4 | 4 | 0 |
| 0003 | 22 | 18 | 0 | 2 | 2 | 0 |
| 0004 | 11 | 6 | 1 | 4 | 0 | 0 |
| 0005 | 15 | 13 | 2 | 0 | 0 | 0 |
| 0006 | 12 | 5 | 3 | 3 | 1 | 0 |
| 0007 | 11 | 6 | 0 | 3 | 2 | 0 |
| 0008 | 16 | 15 | 0 | 1 | 0 | 0 |
| 0009 | 4 | 0 | 1 | 3 | 0 | 0 |
| 0010 | 17 | 6 | 1 | 4 | 6 | 0 |
| 0011 | 11 | 2 | 4 | 4 | 0 | 0 |
| 0012 | 9 | 1 | 4 | 3 | 1 | 0 |
| 0013 | 19 | 11 | 3 | 5 | 0 | 0 |
| 0014 | 15 | 3 | 0 | 7 | 4 | 1 |
| 0015 | 22 | 18 | 0 | 4 | 0 | 0 |
| 0016 | 13 | 12 | 0 | 1 | 0 | 0 |
| 0017 | 12 | 4 | 7 | 1 | 0 | 0 |
| 0018 | 10 | 6 | 0 | 4 | 0 | 0 |
| **TOTAL** | **239** | **135** | **28** | **57** | **22** | **1** |

## 2. Cross-Ref Reconciliation

Scanning all `cross_refs` for guarantees reported inconsistently across verifiers:

**CONFLICT 1 — PHI Zone operational status (ADR-0006 ↔ ADR-0017)**
- ADR-0006 claim "PHI Zone agents use only BR-resident endpoints" = **partial**: the BR-resident endpoint is explicitly BLOCKED/not contracted (`inference.py:17` "BLOQUEADO: nao ha endpoint contratado ainda"); all PHI inference fails with `PhiEndpointNotConfigured`. The application-layer guarantee is structurally fail-closed but non-operational.
- ADR-0017 claims (network egress enforcement) are **unverified** (7 of 12) because the definitive NetworkPolicy rendering tests are `@requires_helm` and skipped in the CI unit lane.
- **Reconciliation: NOT a contradiction — they are consistent and compounding.** Both verifiers agree PHI-zone protection exists *structurally* (fail-closed inference provider + CIDR-pinned NetworkPolicy templates) but is *not provably enforced end-to-end*: no BR endpoint is contracted (0006) AND the egress-policy fail-closed behavior is never exercised in CI (0017). **Trust both** — the combined picture is that the PHI zone is architecturally sound but operationally unproven and not yet usable in production.

**CONFLICT 2 — Logs pseudonymized (ADR-0006 ↔ ADR-0010)**
- ADR-0006 claim "Logs and traces pass through the same pseudonymizer" = **partial**: application-layer log pseudonymization has no implementing code; OTEL collector explicitly states it does NOT scrub (`otel-collector.yaml:62-63`).
- ADR-0010 claim "Logs are pseudonymized" = **partial**: no global PHI-scrubbing processor in `structlog.configure()`; reliance on coding discipline.
- **Reconciliation: CONSISTENT.** Both report partial with the same root cause (no structural log-scrubbing layer). Trust both — the audit trail hashes PHI, but general operational logs have no enforced PHI-stripping.

**CONFLICT 3 — Agent Cards signed (ADR-0003 ↔ ADR-0007)**
- ADR-0003 claim "Agent Cards are signed" = **absent** (`registry.py:32-106` has no signature field).
- ADR-0007 claim "Service identity per agent via signed Agent Card" = **partial** (SA exists, but card not cryptographically signed).
- **Reconciliation: CONSISTENT.** Both agree the signing is missing; the difference in label is only because 0007 credits the ServiceAccount half. Trust the **absent** finding on the signing element specifically.

**CONFLICT 4 — A2A durable idempotency (ADR-0003 ↔ ADR-0015)**
- ADR-0003 claim "idempotency store durable across restarts" = **partial** (in-memory dict, deferred to Phase 1).
- ADR-0015 claims (R9 durable store) = **partial**, with a critical branch nuance: `PostgresIdempotencyStore`/`assembly.py`/migration `0004` **exist on origin/main (PR #60)** but are **NOT present on the current branch `wave/w-r1-runtime-wiring`**; stale `.pyc` artifacts are residuals.
- **Reconciliation: CONSISTENT but branch-divergent.** Trust ADR-0015's finer detail: durable idempotency is implemented on main but absent on this working branch. No semantic conflict.

**No true contradictions exist between cross-referenced verifiers.** The only `contradicted` status in the entire corpus (ADR-0014, TLS `insecure: true`) is intra-ADR, not cross-ref.

## 3. Strongest Points

Components fully implemented, tested, AND ADR-conformant:

- **HITL credential separation (ADR-0005, Mechanism 3)** — The signing credential is structurally unreachable from the agent runtime. `AgentCredentialView` has no path to `HumanCredentialPartition` (`credential_vault.py:164-192, 343-351`), proven by AST scans AND runtime reachability probes with a canary handle (`test_credential_separation.py:95-361`, `test_credential_vault.py:207-236`). This is the deepest verified invariant in the codebase.

- **L0-hard non-lowerability (ADR-0004 / 0005 / 0008)** — `HARD_ACTIONS` frozenset is code-frozen and independent of YAML (`pep.py:51-58`); any overlay touching a hard item raises `PolicyError` (`pep.py:193-196`); CI cross-checks `_hard_frozen.yaml` (`autonomy.py:112-136`, `ci.yml:53-73`). Adversarial battery covers nested params, duplicate keys, level aliasing, YAML bool coercion (`test_pep_bypass.py:76-125`).

- **PEP as sole chokepoint (ADR-0006 / 0008)** — Every tool call routes through `PEP.evaluate()` before any external effect; an AST scan (`test_pep_bypass.py:221-250`) proves no module invokes tool handlers directly outside the whitelisted `registry.py`/`harness.py`.

- **PHI pseudonymization seam (ADR-0006 / 0016)** — `_apply_phi_zone()` is the sole exit path of every handler; fail-closed default `consumer_zone='general'`; raises `RuntimeError` if gateway absent for a general PHI consumer (`registry.py:179-211`). Verified by AST + dynamic tests (`test_phi_pseudonymization_invariant.py`).

- **Append-only tamper-evident audit chain (ADR-0007)** — SHA-256 hash chain with `UNIQUE(prev_record_hash)`, advisory-lock serialization, and structural (non-`ts`-ordered) head recovery across restarts (`audit.py:46-224`, `audit_postgres.py:63-178`). Tamper, reorder, and concurrent-fork all proven against real Postgres (`test_audit_chain_durable.py`).

- **No-denial structural pattern, AUTH & CONTAS (ADR-0018, parts 1/4/5 + ADR-0005 Mechanism 2)** — No automated path reaches an adverse terminal without a human User Task; proven engine-free across all 9 BPMN bodies (`test_no_denial_consolidated.py:456-478`) and against the real engine for AUTH (5 combos) and CONTAS (48 combos). Worker guards `ERR_AUTH_DENIAL_NOT_HUMAN` / `ERR_GLOSA_ACCEPT_NOT_HUMAN` enforce human decision + mandatory fields (`auth.py:264-293`, `contas.py:351-382`).

- **Process key allowlist (ADR-0016)** — `KNOWN_PROCESS_KEYS` frozenset in code; fail-closed `ensure_allowed` called before any engine transport call; structural denial audited as `TOOL:deny:*` even when PEP allowed (`process_allowlist.py:33-152`, `registry.py:131-147`).

- **A2A anti-loop guards 1-4 (ADR-0003 / 0015)** — Acyclic chain, max_hops=3, per-hop budget charge, and task_id idempotency all verified including concurrent-redelivery single-execution (`delegation.py:189-198`, `dispatcher.py:104-160`, `test_idempotency.py`).

- **Metrics catalog single source of truth + PHI label guard (ADR-0010 / 0014)** — `assert_no_phi_labels()` scans the registry and raises on 13 forbidden substrings (`metrics_server.py:68-113`), parametrized over all 13.

## 4. Gaps — Risk-Ranked

### (a) CONTRADICTED

1. **ADR-0014 — OTel Collector TLS `insecure: true` in prod path** — status **contradicted** [operational]. There is only one `otel-collector.yaml`, used by both dev (docker-compose mount) and any K8s deploy; no prod config with `insecure: false` exists. The ADR says insecure must never be used outside docker-compose, but if deployed to K8s as-is, the collector uses insecure TLS in prod. Evidence: `config/otel-collector.yaml:83,92` (`insecure: true` on both remote-write and OTLP/traces exporters); `deploy/helm/maezo-tenant/` has no alternative collector ConfigMap.

### (b) ABSENT / PARTIAL on PHI-Security-Compliance invariants

2. **ADR-0006 — General Zone DPA + BR-region requirement** — **absent** [phi]. No DPA validation, BR-region pin, or region filtering. `AnthropicProvider` hard-codes the US endpoint `https://api.anthropic.com/v1/messages` (`inference.py:242-338`); General Zone routes declare no region constraint (`inference_routing.yaml:22-28`).

3. **ADR-0002 — Working-layer post-task expurgo** — **absent** [phi]. No TTL migration, no thread deletion in `harness.py`, no cleanup in `service.py`. Only tenant-level `DROP SCHEMA` exists; expurgo deferred (`0001_agents_schema.py:11`).

4. **ADR-0002 — LGPD erasure does not cascade to working layer** — **partial** [phi]. `erase_by_patient` covers only `agent_memory` (episodic+semantic). LangGraph checkpoint tables are keyed by `thread_id`, not `fhir_patient_id`; no mechanism deletes per-patient checkpoint state. The ADR claim "delete por fhir_patient_id cascateia pelas 3 camadas" is only partially true. Evidence: `mcp_memory/server.py:244,374-380`; `checkpoint.py` comments acknowledge only tenant-level DROP.

5. **ADR-0002 — LGPD monthly erasure verification** — **absent** [phi]. No cron, CronJob manifest, or audit script realizes "verificacao mensal."

6. **ADR-0007 — Audit partitioning & 5-year retention TTL** — **absent** [phi]. `audit_chain` table has no PARTITION BY, no retention mechanism; Kafka `agents.audit` topic has no retention config. The 5-year regulatory retention is entirely aspirational (`0002_audit_chain.py`, no TTL).

7. **ADR-0007 — `decision_basis` is unstructured free-text** — **partial** [phi]. ADR requires structured (satisfied DMN rules, cited evidence, score). Field is `text NOT NULL`; `dmn_decision_refs` is propagated as a BPMN variable but never written to `AuditRecord.decision_basis`; no `score` field exists. Evidence: `audit.py:76`, `0002_audit_chain.py:42`, `rafael/graph.py:532`.

8. **ADR-0007 — Second BPMN audit trail not cross-referenced** — **partial** [phi]. `process_instance_id` is passed as a BPMN variable but never written into `AuditRecord`; the "second trail" relies entirely on CIB Seven native history with no cross-reference test.

9. **ADR-0003 / 0007 — Agent Cards not signed** — **absent** [phi]. `AgentCard` dataclass has no signature/cert field; no signing or verification anywhere (`registry.py:32-106`); mTLS deferred ("na producao").

10. **ADR-0006 — Semantic memory `note` PHI not enforced** — **partial** [phi]. `PHI_FIELDS=[]` means the registry will NOT scrub raw PHI passed in `note`; "memoria semantica armazena derivados minimizados" is convention-only (`mcp_memory/server.py:37,39`). Same root cause undercuts ADR-0002 semantic-layer FHIR-reference-only and re-indexability claims (both **partial**).

11. **ADR-0006 — Re-identification map in-memory only** — **partial** [phi]. `InMemorySurrogateStore` is lost on pod restart; planned Postgres backend absent (`pseudonymizer.py:53-76`).

12. **ADR-0006 — HMAC key vault injection unverified** — **unverified** [phi]. Tests use synthetic keys; no production wiring shows `hmac_key` retrieved from vault into `Pseudonymizer` (`pseudonymizer.py:110-120`).

13. **ADR-0006 — NetworkPolicy egress fail-closed unverified** & **ADR-0017 (7 claims unverified)** [phi]. All definitive NetworkPolicy rendering/fail-closed tests are `@requires_helm` and skipped in the CI unit lane; `validate-helm` only lints + smoke-templates the positive committed values, never the negative/fail-closed path. Evidence: `test_provision_tenant.py:443-566`; `ci.yml:106-126`.

14. **ADR-0004 — Per-tenant dedicated CIB Seven / HAPI FHIR not rendered** — **partial** [phi]. Helm chart renders agent namespace + Aurora but only references an external CIB Seven URL (`values.yaml:232`, empty default); no in-cluster CIB Seven/HAPI StatefulSet templates; no integration test verifies a second tenant gets isolated Postgres+FHIR.

15. **ADR-0004 — Zero cross-tenant bleed stronger than enforced** — **partial** [phi]. NetworkPolicy isolates within one cluster, but Terraform does not provision one cluster per tenant by default (`create_eks_cluster` toggle unset; staging+prod share the EKS module); no test that a pod in `maezo-amh` cannot reach `maezo-other`, no DB-connection isolation test.

16. **ADR-0004 — General Zone shared control plane PHI-free** — **unverified** (inferred) [phi]. Label sets are bounded but no scraping/data-flow test confirms aggregated AMP/trace data is PHI-free.

17. **ADR-0012 — DMN as sole rule source not mechanically enforced** — **partial/unverified** [phi & functional]. Enforcement is comment-level; no AST/lint test bans inline rules in `graph.py`. `decision_version` falls back to `'unknown'` (`server.py:117`) with no integration test confirming the `X-Decision-Definition-Id` header is present in real CIB Seven responses. DMN inputs-contain-no-PHI is convention-only (no test validates call-site variable dicts).

18. **ADR-0001 — Agents cannot bypass DMN** — **unverified** [phi]. No AST-level test proves no agent module reimplements a DMN rule (contrast with the PHI/credential invariants which are mechanically verified).

19. **ADR-0010 — OTel PHI safety-net filter effectiveness** — **unverified** [phi]. Filter uses wildcard `*.content` (not valid OTEL attribute syntax); no test verifies CPF-bearing spans are dropped (`otel-collector.yaml:64-73`).

### (c) FUNCTIONAL

20. **ADR-0002 — Episodic S3 attachments** — **absent** [functional]. No S3/boto3/object-store anywhere; `EpisodicNote` has no attachment field.

21. **ADR-0012 — `contract_extraction` pipeline** — **absent** [functional]. No implementing code anywhere; only ADR mentions as future port.

22. **ADR-0012 — Payer-side DMN content (DUT/ROL/carencia)** — **partial** [functional]. All 33 DMN files marked DRAFT/synthetic; DUT/ROL/carencia tables are orphan artifacts not wired into any agent graph or BPMN businessRuleTask.

23. **ADR-0009 — Frontier/batch model tiers never invoked** — **partial** [functional]. Router and `agent.yaml` declare `frontier`/`batch`, but all 5 agent graphs hard-code `'fast'` at every call site (`helena/graph.py:399,420,433`, etc.); `agent.yaml` model section is advisory only — harness never reads it into the router.

24. **ADR-0008 — L2 sampling review** — **partial** [functional]. L2 is ALLOW identical to L3; no sampler, review queue, or rate-limiter implements "revisao por amostragem" — only a metric label (`pep.py:330-336`).

25. **ADR-0013 — fhir_sync inbound has no Pydantic validation / no Avro path** — **partial/unverified** [functional]. Consumer deserializes to raw `dict[str,Any]` via `json.loads`; schema mismatch surfaces only as KeyError, not ValidationError; no env-gated Avro deserializer (`consumer.py:123`).

26. **ADR-0013 — DLQ publish path untested** — **unverified** [operational/functional]. `_process_message` DLQ write is exercised only in the full `run_fhir_sync()` loop, which has no test; `process_single_message` helper just re-raises (`consumer.py:196-234`).

27. **ADR-0018 — DMN shape/typeref static tests only for CONTAS** — **partial** [functional]. AUTH (3 DMNs) and the 5 in-flight bodies have no equivalent static shape test; a future process with a `number` typeRef or adverse output column could merge without a test failure.

28. **ADR-0018 — 5 in-flight SP-OP bodies not proved on main** — **partial** [functional]. RECURSO/NIP/CANCEL/REEMBOLSO/ANS-SUBMIT worker guards + integration tests exist in the working tree but per the ADR's own pre-condition are proved only when green against the real engine in main.

### (d) OPERATIONAL / OBSERVABILITY

29. **ADR-0010 / 0014 — Agent runtime metric emission not wired** — **absent** [operational]. `harness.py`/`inference.py`/`service.py` never call `record_conversation`/`record_llm_usage`/`record_hitl_*`; metrics.py docstring self-documents "future PR". These KPIs always read zero.

30. **ADR-0010 — No OTel instrumentation in application** — **partial** [operational]. SDK declared but zero spans created; no `from opentelemetry import trace` in `src/maezo/`. Breaks conversation-replay ("checkpoints + trace" is 50% present).

31. **ADR-0010 — Alerts absent** — **absent** ×4 [operational]. No A2A budget alert, no HITL approval-drop alert, no LLM cost-spike alert, no L3 sampling policy, no trace retention policy (`alert-rules.yaml`, no rules; no `OTEL_TRACES_SAMPLER`).

32. **ADR-0014 — AMP/AMG infrastructure absent** — **absent/partial** ×several [operational]. No `aws_prometheus_workspace`/AMG/observability module in Terraform; SigV4 auth commented out (`otel-collector.yaml:85-86`); alert rules and Grafana datasource point at local `prometheus:9090`, not AMP; alert-rules.yaml never mounted into Prometheus in dev or wired into AMP in prod (dead config).

33. **ADR-0013 — Tasy simulator never run in CI** — **partial** [functional/operational]. CI integration job starts only `--profile core`, never `--profile simulator` (`ci.yml:144`), despite ADR claiming it is a CI deliverable. Also `TASY_ORACLE_DSN` is injected into the fhir-sync pod but never read by the consumer (misleading unused injection).

34. **ADR-0016 — process_allowlist.yaml never loaded in prod** — **partial** [operational]. `tool_wiring.py:171-174` constructs `CibSevenServer` with no allowlist arg; always falls back to `default_allowlist`. The YAML tenant-extension path is dead code in production (security invariant holds today only because DEFAULT == KNOWN universe).

35. **ADR-0001 — Temporal re-evaluation trigger** — **absent** [operational]. Forward-looking escape-valve clause; no counter/metric/alert; inherently a governance statement, not an implementable invariant.

36. **ADR-0002 — pgvector 5M scale ceiling** — **absent** [operational]. No row-count metric/alert; ivfflat `lists=100` tuned for small datasets.

37. **Convention-only structural rules (ADR-0009, 0011)** — **unverified** [operational]. "No LLM SDK outside inference.py" (0009), single-tree/no-archive (0011), no cross-repo hospitalar imports (0011), BPM-first regression — all unviolated at HEAD by grep but NOT enforced by any AST/CI test, unlike the credential/PHI invariants.

## 5. Verification Confidence

**Totals by confidence:**
- `verified-by-reading-code`: **235 of 239 claims (98.3%)**
- `inferred`: **4 of 239 claims (1.7%)** — ADR-0004 "shared control plane no PHI", ADR-0010 "engine/BPMN metric emission (absent)", ADR-0011 "hospitalar patterns ported", ADR-0014 "engine/BPMN metrics emitted by mcp_cibseven (absent)".

**Per-risk breakdown (confidence × risk):**

| Risk | verified-by-reading-code | inferred |
|------|--------------------------|----------|
| phi-security-compliance | ~118 | 1 (ADR-0004 control-plane) |
| functional | ~78 | 1 (ADR-0011 ported patterns) |
| operational | ~39 | 2 (ADR-0010, ADR-0014 engine metrics) |

(Note: every status — including `absent`/`unverified`/`contradicted` — was reached *by reading code*; the confidence field reflects how the verifier reached the conclusion, not whether the claim passed.)

**What could NOT be confirmed, and why:**

*Confirmed-absent (code does not exist):*
- General Zone DPA/BR-region enforcement (ADR-0006) — no validation code, US endpoint hard-coded.
- Working-layer expurgo, monthly LGPD verification, audit TTL/partitioning (ADR-0002, 0007) — no migrations/cron.
- Episodic S3 attachments, contract_extraction pipeline (ADR-0002, 0012) — no boto3/pipeline code.
- Agent runtime metric emission, OTel spans, 4 alert rules, sampling/retention policy (ADR-0010) — self-documented "future PR".
- AMP/AMG Terraform, SigV4 auth (ADR-0014) — no IaC resources, auth commented out.
- Agent Card signing (ADR-0003, 0007) — no crypto fields.

*Unverified — no test exercises the path (code exists, behavior unproven):*
- All ADR-0017 NetworkPolicy fail-closed behavior + ADR-0006 egress — `@requires_helm` tests skipped in CI unit lane; only positive helm render is smoke-tested.
- DMN `decision_version` real-header presence (ADR-0012) — unit tests use FakeDmnTransport; no integration test confirms `X-Decision-Definition-Id` from real CIB Seven.
- fhir_sync DLQ publish path (ADR-0013) — only the untested `run_fhir_sync()` loop writes DLQ.
- OTel PHI safety-net filter (ADR-0010) — invalid `*.content` syntax, no drop test.
- HMAC vault injection (ADR-0006) — tests use synthetic keys.
- SP-OP-AUTH-001 real-engine no-denial invariant green in CI (ADR-0005) — logic correct, but integration lane requires dev-stack; cannot confirm green from code alone.
- Model-swap state survival (ADR-0002) — architecturally consistent, never explicitly tested.

*Convention-only (enforced by docs/CODEOWNERS/grep, not by a mechanical invariant):*
- No LLM SDK outside inference.py (ADR-0009); single-tree / no-archive / no cross-repo imports (ADR-0011); DMN-is-sole-rule-source and agents-cannot-bypass-DMN (ADR-0001, 0012); semantic-memory `note` pre-pseudonymized and DMN-inputs-no-PHI (ADR-0006, 0012). These lack the AST-scan enforcement that the credential-separation and PHI-pseudonymization invariants demonstrably have — they are the codebase's weakest class of "guarantee."

*Inferred (no direct read, deduced from related evidence):* the 4 claims listed above — control-plane PHI-freedom (label inspection, no data-flow test), hospitalar provenance (no second repo to compare), and engine/BPMN metric emission (deduced absent from metrics.py docstring without reading mcp_cibseven call sites).

*Branch-divergence caveat:* ADR-0015 durable idempotency (`PostgresIdempotencyStore`, `assembly.py`, migration `0004`) exists on `origin/main` (PR #60) but is **absent from the verified branch `wave/w-r1-runtime-wiring`**; stale `.pyc` residuals do not represent live source. Findings on this branch understate main for that one component.

---

## Appendix — Structured Verifier Results (raw JSON)

The per-ADR machine-readable output each verifier returned (claim → status → evidence → gap → confidence → risk).

```json
[
  {
    "adr": "0001",
    "title": "ADR-0001 — CIB Seven as Governance Backbone; LangGraph as Agent Runtime",
    "claims": [
      {
        "claim": "LangGraph (Python) is the agent runtime for reasoning, multi-turn conversations, and tool calls — each agent is a StateGraph compiled with a checkpointer.",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/harness.py:40 — `from langgraph.graph.state import CompiledStateGraph, StateGraph`",
          "src/maezo/runtime/harness.py:478-480 — `if not isinstance(graph, StateGraph): raise HarnessError(...); compiled = graph.compile(checkpointer=checkpointer)`",
          "src/maezo/runtime/harness.py:1 — module docstring explicitly attributes to ADR-0001",
          "tests/unit/runtime/test_harness.py::test_build_agent_compiles_and_runs_graph — builds and runs a StateGraph end-to-end with InMemorySaver",
          "tests/integration/test_runtime_resume.py::test_resume_after_pod_kill — resumes a LangGraph StateGraph mid-conversation after simulated pod kill"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "PostgreSQL (AsyncPostgresSaver) is the checkpointer backing multi-turn conversations; tenant-scoped schema provides isolation.",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/checkpoint.py:44 — `from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver`",
          "src/maezo/runtime/checkpoint.py:56-62 — `schema_for_tenant` validates identifier and returns `{prefix}_{tenant}`",
          "src/maezo/runtime/checkpoint.py:105-143 — `open_checkpointer` opens pool with `SET search_path TO \"{schema}\"` and `CREATE SCHEMA IF NOT EXISTS`",
          "tests/unit/runtime/test_checkpoint.py::test_schema_for_tenant_default_prefix — verifies `agents_amh` schema name",
          "tests/unit/runtime/test_checkpoint.py::test_schema_for_tenant_rejects_unsafe_identifier — rejects SQL-injection-prone identifiers",
          "tests/integration/test_runtime_resume.py::test_resume_after_pod_kill — full resume against real Postgres"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "CIB Seven is exclusively governance: SP-OP processes enforce regulatory SLAs (RN 259), mandatory human-in-the-loop User Tasks, and non-repudiation audit. Agents CANNOT complete User Tasks — that is reserved for humans via Tasklist.",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/mcp_cibseven/server.py:28 — `NOTA: complete_user_task NÃO é exposto. User Tasks são completadas por humanos via Tasklist.`",
          "src/maezo/tools/mcp_cibseven/server.py:322 — class docstring repeats the constraint",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:220-255 — `UT_AnaliseMedicoAuditor` User Task with SLA timer `BT_SlaAnalise` referencing `RN 259`",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:250-254 — `bpmn:timerEventDefinition` with `sla_analise` DMN-driven duration (RN 259 regulatory SLA)",
          "src/maezo/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn — `UT_TratarEscalonamento`, `UT_SupervisorAssume` User Tasks",
          "src/maezo/tools/workers/auth.py:17-35 — `send_denial_notice` worker guards against denial without prior human User Task (`decisao_auditor=NEGAR`), raises `WorkerBpmnError` if absent",
          "tests/unit/runtime/test_harness.py::test_executor_deny_hard_does_not_execute_and_audits — `authorization_denial` is HARD denied, effect never executes",
          "tests/integration/test_runtime_resume.py::test_pep_deny_path_through_harness_is_audited — hard deny path verified end-to-end with audit record"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0005",
          "0007",
          "0008"
        ]
      },
      {
        "claim": "Agents initiate and advance SP-OP processes via REST (mcp-cibseven), with idempotent business key semantics.",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/mcp_cibseven/server.py:352-376 — `start_process` checks allowlist then `find_active_instance` before creating, returns existing if found (`already_existed=True`)",
          "src/maezo/tools/mcp_cibseven/server.py:103-251 — `CibSevenHttpTransport` posts to `/process-definition/key/{process_key}/start` and `/message`",
          "src/maezo/tools/process_allowlist.py:75-119 — `ProcessAllowlist.ensure_allowed` enforces fail-closed process key gating before any REST call",
          "tests/unit/tools/test_mcp_cibseven.py::test_start_process_idempotent_returns_existing — idempotency verified with unit fake",
          "tests/integration/test_cibseven_escalation.py::test_start_process_creates_instance — verified against real CIB Seven engine",
          "tests/integration/test_cibseven_escalation.py::test_start_process_idempotent_on_duplicate_business_key — idempotency verified against real engine"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Processes invoke agents via External Task (residual direction: engine → worker, not agent → tool).",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/workers/harness.py:1-7 — `ADR-0001: processos SP-OP convocam workers via External Task (sentido residual — engine -> worker, não agent -> tool)`",
          "src/maezo/tools/workers/harness.py:46-54 — `ExternalTask` dataclass with `task_id`, `topic`, `process_instance_id`, `business_key`, `variables`",
          "src/maezo/tools/workers/auth.py:1-46 — Phase-1 external task workers for SP-OP-AUTH-001 (operadora.auth.*)",
          "src/maezo/tools/workers/reembolso.py:1 — Phase-2 external task workers for SP-OP-REEMBOLSO-001",
          "tests/unit/tools/test_workers.py — `fetch_and_lock` loop exercised; handlers tested via `WorkerHarness`"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Golden rule: agent decides HOW to work; BPMN process guarantees THAT regulatory obligations are met. The two runtimes have strictly separated roles — no BPMN for agent reasoning, no agent runtime for regulatory process enforcement.",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/harness.py:1-26 — harness docstring: agents route all tool calls through PEP; graph decides what to do with denials (typically escalate via SP-OP-ESCALATION-001)",
          "src/maezo/tools/workers/harness.py:3-7 — `WorkerHarness` docstring: residual direction only; agent logic is never in the BPMN",
          "src/maezo/tools/workers/auth_analyze.py:25 — `o LLM nunca decide a regra` — comment in worker code",
          "src/maezo/agents/gustavo/graph.py:17 — `assess SEMPRE consulta as DMN deterministicas (ADR-0012). O LLM RACIOCINA sobre os resultados`",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:165 — `BRT_SlaAnalise` Business Rule Task delegates SLA determination to DMN `auth_sla`, not agent"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Deterministic business rules live exclusively in DMN tables executed by CIB Seven — agents consume results, never re-implement rules.",
        "status": "unverified",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:4 — `ADR-0012: agentes consomem o RESULTADO da DMN; nunca reimplementam regras`",
          "src/maezo/agents/gustavo/graph.py:17-29 — agent calls DMN via tool, uses allowlist-closed set of known DMN outputs; on DMN unavailability routes to human",
          "src/maezo/agents/rafael/graph.py:659 — injects `inference`, `tools`, `agent` from harness (ADR-0001/0005/0009)",
          "tests/integration/test_cibseven_escalation.py::test_dmn_evaluate_escalation_routing — DMN evaluation against real CIB Seven engine"
        ],
        "gap": "No static architectural test (AST-level) that proves no agent module reimplements a DMN rule. Enforcement is by code convention and code comments, not a mechanically verified invariant like the PHI pseudonymization invariant (test_phi_pseudonymization_invariant.py) or credential separation (test_credential_separation.py). The integration test proves DMN can be called but does not prove agent graphs cannot bypass it.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0012"
        ]
      },
      {
        "claim": "Temporal re-evaluation trigger: if >2 cases of long-running non-regulatory jobs with proven state loss occur, Temporal should be evaluated for technical jobs (never replacing regulatory BPMN).",
        "status": "absent",
        "evidence": [
          "docs/adr/0001-cib-seven-governance-langgraph-runtime.md:20-21 — trigger is documented in the ADR text as a future re-evaluation criterion"
        ],
        "gap": "This is a forward-looking escape-valve clause in the ADR, not a normative requirement to implement. No code, metric, or counter exists that tracks state-loss events or enforces the '>2 cases' threshold. There is no alerting, threshold gate, or monitoring code tied to this trigger. The claim is inherently a process governance statement, not an implementable code invariant — it cannot be 'verified' in code. It is absent from any runtime artifact.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      }
    ]
  },
  {
    "title": "ADR-0002 Verification: Agent State Three Layers",
    "adr": "0002",
    "claims": [
      {
        "claim": "Working layer: LangGraph checkpointer backed by PostgreSQL in schema `agents_{tenant}`; state survives pod kills and LLM model swaps",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/checkpoint.py:56-62 — schema_for_tenant() computes `{prefix}_{tenant}` with SQL-injection guard (_SAFE_IDENT regex)",
          "src/maezo/runtime/checkpoint.py:104-143 — open_checkpointer() context manager opens pool with SET search_path fixed to tenant schema, CREATE SCHEMA IF NOT EXISTS, and AsyncPostgresSaver.setup()",
          "src/maezo/platform/migrations/versions/0001_agents_schema.py:29-33 — Alembic migration creates tenant schema (forward-only)",
          "tests/unit/runtime/test_checkpoint.py::test_schema_for_tenant_default_prefix — verifies schema name",
          "tests/unit/runtime/test_checkpoint.py::test_schema_for_tenant_rejects_unsafe_identifier — SQL injection guard",
          "tests/integration/test_runtime_resume.py::test_resume_after_pod_kill — kills pool+graph object mid-conversation, rebuilds from zero, resumes by thread_id; full message list verified",
          "tests/integration/chaos/test_mid_conversation_kill.py::test_toy_graph_resumes_losslessly_after_pool_drop — chaos variant of same"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0001"
        ]
      },
      {
        "claim": "Working layer: state is purged after task completion (post-task expurgo)",
        "status": "absent",
        "evidence": [
          "src/maezo/platform/migrations/versions/0001_agents_schema.py:11 — comment explicitly defers TTL/expurgo to future migrations: 'migracoes futuras (TTL/expurgo, indices, objetos auxiliares) entram aqui como novas revisoes'"
        ],
        "gap": "No TTL migration, no thread deletion logic in harness.py, no post-task cleanup call in agent_runtime/service.py. The ADR states 'expurgo pos-tarefa' for the working layer but no implementing code exists. Only tenant-level DROP SCHEMA is referenced for working-layer erasure, not per-task thread cleanup.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Episodic layer: transcriptions/decisions/events are partitioned by tenant and keyed by `fhir_patient_id`",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py:209-244 — agent_memory DDL: columns (tenant, agent_id, fhir_patient_id); SQL queries filter on (tenant, agent_id, fhir_patient_id)",
          "src/maezo/platform/migrations/versions/0003_agent_memory.py:32-51 — Alembic migration creates agent_memory with ix_agent_memory_lookup index on (tenant, agent_id, fhir_patient_id, created_at DESC)",
          "src/maezo/tools/mcp_memory/server.py:276-380 — PostgresMemoryStore.store/retrieve/semantic_search all SET search_path per tenant per connection",
          "tests/unit/tools/test_mcp_memory.py::test_retrieve_isolated_by_tenant — inserting to 'amh' tenant returns empty list for 'outro-tenant'",
          "tests/integration/platform/test_memory_pgvector.py::test_store_and_retrieve_roundtrip — end-to-end against real pgvector container"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Episodic layer: attachments stored in S3",
        "status": "absent",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py — entire file; no S3/boto3/object-store reference",
          "src/maezo/platform/migrations/versions/0003_agent_memory.py — no S3 column or reference"
        ],
        "gap": "The ADR states 'anexos S3' for the episodic layer. There is no S3 client, boto3 import, presigned URL, or object-store abstraction anywhere in src/maezo/. The EpisodicNote dataclass has no attachment field. The episodic store only persists text notes and metadata in Postgres.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Semantic layer: pgvector embeddings (1536-dim) with ivfflat cosine index; canonical content NEVER stored here — always a reference to a FHIR resource",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py:209-242 — DDL: embedding vector(1536); _SQL_SEARCH uses `<=>` cosine operator",
          "src/maezo/platform/migrations/versions/0003_agent_memory.py:43-46 — ivfflat index (vector_cosine_ops, lists=100)",
          "src/maezo/tools/mcp_memory/server.py:277-380 — PostgresMemoryStore.semantic_search queries by cosine distance",
          "tests/integration/platform/test_memory_pgvector.py::test_semantic_search_nearest_neighbor_ordering — proves nearest-neighbor ordering with real pgvector",
          "tests/unit/tools/test_memory_postgres.py::test_deterministic_embedding_dim_is_1536"
        ],
        "gap": "The FHIR-reference-only constraint ('Conteudo canonico NUNCA mora aqui — sempre referencia a recurso FHIR') is stated in ADR and repeated in code comments (server.py line 277 docstring), but there is NO programmatic enforcement: the `note` field is a plain `text NOT NULL` column with no validation, and the MemoryServer.store_episodic() accepts any string as `note`. An agent can store raw clinical text here; the constraint is a convention with no code enforcement. Real embedding provider (Bedrock Titan / BR endpoint) is also SEAM/BLOQUEADO — only DeterministicEmbeddingProvider (offline stub) is used in CI.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Semantic layer: embeddings are discardable/re-indexable (canonical content is always in FHIR)",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py:106-129 — DeterministicEmbeddingProvider docstring states SEAM/BLOQUEADO for real provider",
          "src/maezo/tools/mcp_memory/server.py:288-290 — PostgresMemoryStore docstring states embedding provider is SEAM/BLOQUEADO/AWS"
        ],
        "gap": "The re-indexability property depends on the architectural invariant that canonical content lives only in FHIR (not in the note field). As stated in the prior claim, this invariant is unenforced in code. If agents store canonical clinical text in `note`, embeddings cannot simply be dropped and regenerated without data loss. There is no re-indexing procedure or script implemented.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "LGPD erasure: delete by `fhir_patient_id` cascades across all 3 layers (working + episodic + semantic)",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py:244 — _SQL_ERASE: DELETE FROM agent_memory WHERE tenant=$1 AND fhir_patient_id=$2 (covers episodic+semantic)",
          "src/maezo/tools/mcp_memory/server.py:374-380 — PostgresMemoryStore.erase_by_patient() executes the delete and returns count",
          "src/maezo/tools/mcp_memory/server.py:455-465 — MemoryServer.erase_by_patient() delegates to store",
          "tests/unit/tools/test_mcp_memory.py::test_erase_by_patient_removes_all_notes — unit test verifies count and empty retrieve",
          "tests/integration/platform/test_memory_pgvector.py::test_erase_by_patient_returns_count — integration test against real pgvector; verifies cross-patient isolation"
        ],
        "gap": "Erasure covers only the episodic+semantic layer (agent_memory table). The working layer (LangGraph checkpoint in AsyncPostgresSaver tables: checkpoints, checkpoint_blobs, checkpoint_writes) has NO per-patient erasure: checkpoint rows are keyed by thread_id, NOT by fhir_patient_id, so there is no mechanism to find and delete checkpoint state for a given patient. Code comments in checkpoint.py and migration 0003 acknowledge only tenant-level DROP SCHEMA for working-layer erasure — per-patient cascade across the working layer is architecturally unsupported. The ADR claim that 'delete por fhir_patient_id cascateia pelas 3 camadas' is only partially true.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "LGPD erasure: monthly verification",
        "status": "absent",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py — no scheduled job, no periodic check",
          "src/maezo/runtime/agent_runtime/service.py — no cron or periodic task",
          "src/maezo/platform/migrations/ — no migration adding a verification procedure"
        ],
        "gap": "No implementing code exists for 'verificacao mensal' of LGPD erasure. There is no cron job, scheduled task, Kubernetes CronJob manifest, or audit script. The claim is entirely aspirational with no code realizing it.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Memory state survives LLM model swaps (troca de modelo nao perde memoria)",
        "status": "unverified",
        "evidence": [
          "src/maezo/runtime/inference.py:1-30 — InferenceProvider abstraction; model is injected via InferenceRouter configured by YAML, decoupled from state schema",
          "src/maezo/runtime/checkpoint.py — state is stored in Postgres keyed by thread_id, independent of model identity",
          "src/maezo/tools/mcp_memory/server.py — agent_memory has no model_id column; notes persist regardless of which model generated them"
        ],
        "gap": "No test exercises a state-swap scenario: store notes/checkpoint with model A, swap to model B, verify state is intact and retrievable. The architecture is consistent with this property (Postgres-backed state has no model dependency), but the integration test tests/integration/test_runtime_resume.py only tests pod-kill resume, not model-provider swap. tests/integration/chaos/test_mid_conversation_kill.py::test_helena_graph_resumes_after_kill is also guarded by importorskip (PR #9 not merged). The guarantee is structural/architectural but never explicitly tested.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0009"
        ]
      },
      {
        "claim": "pgvector scale ceiling: revisit above ~5M embeddings/instance",
        "status": "absent",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py — no embedding count monitoring, no row-count alert, no ceiling check",
          "src/maezo/runtime/metrics.py — no metric tracking agent_memory row count or embedding count"
        ],
        "gap": "The ADR acknowledges a ~5M embedding/instance scale ceiling and states 'revisitar'. No monitoring metric, alerting rule, or enforcement mechanism tracks embedding count. The ivfflat index (lists=100) in migration 0003 is tuned for smaller datasets and would degrade significantly before 5M vectors without retuning. This is a documented operational risk with no operational instrumentation.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      }
    ]
  },
  {
    "title": "ADR-0003 Verification: A2A v1.0 Collaboration + Kafka Facts",
    "adr": "0003",
    "claims": [
      {
        "claim": "A2A v1.0 (Linux Foundation) is used for directed delegation between agents (e.g., Helena->Rafael), with Agent Cards, task lifecycle, deadline, and delegation_chain. The SDK is encapsulated in maezo.a2a and the rest of the codebase imports only from that package boundary.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/__init__.py:1-64 — public boundary exports all A2A types",
          "src/maezo/a2a/delegation.py:1-15 — module header confirms A2A v1.0 delegation envelope with lifecycle/deadline/delegation_chain",
          "src/maezo/a2a/registry.py:1-8 — explicitly states encapsulation: 'o resto do codigo importa apenas maezo.a2a'",
          "src/maezo/agents/helena/delegation.py:22 — from maezo.a2a import Budget, DelegationEnvelope (not submodule)",
          "src/maezo/agents/rafael/delegation.py:30 — from maezo.a2a import DelegationEnvelope, HandlerOutput",
          "src/maezo/agents/marina/delegation.py:35 — from maezo.a2a import DelegationEnvelope, HandlerOutput",
          "src/maezo/agents/gustavo/delegation.py:30 — from maezo.a2a import Budget, DelegationEnvelope",
          "src/maezo/agents/lucas/delegation.py:27 — from maezo.a2a import Budget, DelegationEnvelope",
          "tests/unit/a2a/test_registry.py::test_register_lookup_roundtrip — roundtrip via public boundary",
          "tests/unit/a2a/test_dispatcher.py::test_successful_delegation_routes_audits_and_emits_facts"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Agent Cards are signed. (ADR states: 'Agent Cards assinados')",
        "status": "absent",
        "evidence": [
          "src/maezo/a2a/registry.py:32-106 — AgentCard dataclass has no signature, certificate, or cryptographic fields",
          "src/maezo/a2a/registry.py:39 — comment says 'HTTP/JSON-RPC + mTLS na producao; uma queue logica no Phase 1' (deferred)",
          "grep for 'signed|sign|signature|x.509|cert' in src/maezo/a2a/ returned no results"
        ],
        "gap": "The ADR claims Agent Cards are signed ('Agent Cards assinados') but the AgentCard dataclass in registry.py has no signature, certificate, or cryptographic verification field. There is no signing or verification code anywhere in maezo.a2a. This is structurally deferred (comment says 'na producao').",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "HTTP/JSON-RPC + mTLS is used as the internal transport for A2A directed delegation.",
        "status": "absent",
        "evidence": [
          "src/maezo/a2a/registry.py:39 — comment 'HTTP/JSON-RPC + mTLS na producao; uma queue logica no Phase 1' explicitly marks this as a production aspiration, not current implementation",
          "src/maezo/a2a/dispatcher.py:16-17 — 'O harness liga handlers reais (grafos de agente) no Phase 1; aqui o handler e uma callable injetavel'",
          "grep for 'mTLS|mtls|jsonrpc|json.rpc|HTTP.*A2A' in src/maezo/ returned only the registry.py comment"
        ],
        "gap": "HTTP/JSON-RPC + mTLS transport is explicitly deferred to production ('na producao') and replaced by an in-process callable (AgentHandler) injected at construction time. No HTTP client, mTLS configuration, or JSON-RPC codec exists in the codebase.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Kafka is used for broadcast async facts (agents.events.*, agents.audit) and proactive triggers. Three delegation fact topics exist: agents.events.delegation.requested, agents.events.delegation.completed, agents.events.delegation.rejected.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/facts.py:18-20 — TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED constants defined",
          "config/topic_registry.yaml:7-13 — agents.audit, agents.events.process_completed, agents.events.proactive, and all three delegation topics declared",
          "src/maezo/a2a/dispatcher.py:48-53 — FactProducer.emit sends to fact.topic with tenant key",
          "tests/unit/a2a/test_dispatcher.py:48 — assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]",
          "tests/unit/a2a/test_dispatcher.py:76 — assert producer.topics() == [TOPIC_REJECTED]"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The rule 'directed command = A2A; async fact = Kafka' is enforced by design: delegations use A2A envelope dispatch, never raw Kafka RPC.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/facts.py:3-4 — docstring: 'Delegacoes sao comandos dirigidos (A2A), mas geram fatos broadcast no Kafka para observabilidade e consumidores reativos'",
          "src/maezo/a2a/delegation.py:3 — 'Uma delegacao e um comando dirigido agente->agente. Diferente de um fato Kafka'",
          "src/maezo/a2a/dispatcher.py:135-190 — delegate() routes through DelegationEnvelope/handler, not a raw Kafka send; Kafka only receives the side-effect facts",
          "tests/unit/a2a/test_dispatcher.py::test_successful_delegation_routes_audits_and_emits_facts — verifies handler is called (routing), not just Kafka"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Anti-loop Guard 1 (acyclic chain): delegation_chain is acyclic; extend() rejects a target already present in the chain.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:189-196 — extend() raises CyclicDelegationError if target in self.delegation_chain",
          "src/maezo/a2a/delegation.py:152-153 — root() raises CyclicDelegationError if origin == target",
          "tests/unit/a2a/test_anti_loop.py::test_guard_acyclic_rejects_target_in_chain",
          "tests/unit/a2a/test_anti_loop.py::test_guard_acyclic_rejects_trivial_self_delegation"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Anti-loop Guard 2 (max_hops=3): the 4th hop is structurally rejected.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:25 — MAX_HOPS: int = 3",
          "src/maezo/a2a/delegation.py:193-196 — extend() raises MaxHopsExceededError when new_chain > max_hops",
          "tests/unit/a2a/test_anti_loop.py::test_guard_max_hops_rejects_fourth_hop — asserts MAX_HOPS==3 and 4th hop fails",
          "tests/unit/a2a/test_anti_loop.py::test_guard_max_hops_respects_custom_limit"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Anti-loop Guard 3 (budget decrement per hop): budget decrements on each extend() and rejects when exhausted.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:44-78 — Budget dataclass with charge() that decrements tokens and time_ms",
          "src/maezo/a2a/delegation.py:198 — extend() calls self.budget.charge() before constructing the envelope",
          "tests/unit/a2a/test_anti_loop.py::test_guard_budget_exhaustion_rejects_hop",
          "tests/unit/a2a/test_anti_loop.py::test_budget_charge_decrements_both_dimensions"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Anti-loop Guard 4 (task_id idempotent): the dispatcher returns the prior result on re-delivery of the same task_id and never re-executes the handler.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:137-153 — _InflightEntry with asyncio.Lock; if entry.result is not None returns idempotent_replay=True",
          "src/maezo/a2a/dispatcher.py:105-109 — _InflightEntry dataclass with lock and result",
          "tests/unit/a2a/test_idempotency.py::test_redelivery_returns_prior_result_without_reexecuting — asserts handler.call_count == 1, second result is idempotent_replay=True",
          "tests/unit/a2a/test_idempotency.py::test_concurrent_redelivery_executes_once — asyncio.gather with same task_id, handler.call_count==1"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Task_id idempotency store is durable across restarts (not just in-memory).",
        "status": "partial",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:132 — self._inflight: dict[str, _InflightEntry] = {} (pure in-memory Python dict)",
          "src/maezo/a2a/dispatcher.py:117 — docstring: 'mantida em memoria (backing store duravel no Phase 1)' — explicitly deferred"
        ],
        "gap": "The ADR makes no explicit cross-restart durability claim, but the idempotency store is in-memory only. The dispatcher docstring acknowledges the durable backing store is deferred to 'Phase 1'. Process restarts will lose the idempotency cache, potentially re-executing handlers for redelivered messages.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The DelegationEnvelope payload_ref never carries raw PHI; it references FHIR/pseudonymized data (e.g., Patient/abc). Heuristic guard rejects CPF/CNS/CNPJ-like strings.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:116-119 — __post_init__ calls _looks_like_phi(payload_ref) and raises DelegationError if it matches",
          "src/maezo/a2a/delegation.py:217-220 — _looks_like_phi checks digit count of 11 (CPF/CNS) or 14 (CNPJ)",
          "tests/unit/a2a/test_anti_loop.py::test_payload_ref_rejects_raw_phi_like_value — asserts 11-digit and 14-digit strings are rejected, FHIR ref passes",
          "tests/unit/a2a/test_dispatcher.py::test_facts_never_carry_phi"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Kafka delegation facts never carry PHI: only task_id, chain, task_type, and rejection reason are serialized; never the payload.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/facts.py:55-70 — DelegationFact.to_value() serializes only: kind, task_id, task_type, tenant, origin, target, delegation_chain, ts, reason (optional), output_ref (optional). No payload field.",
          "src/maezo/a2a/facts.py:91 — build_fact() receives meta but discards it: '_ = meta  # reservado para extensao; nao entra no fato (evita PHI acidental)'",
          "tests/unit/a2a/test_dispatcher.py::test_facts_never_carry_phi — asserts serialized bytes do not contain raw PHI"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Every delegation is audited (AuditLog / ADR-0007 tuple) before being routed to the handler. The audit record is chained and verifiable.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:175-177 — _audit_delegation called before handler invocation with basis='A2A:delegate:allow'",
          "src/maezo/a2a/dispatcher.py:166-172 — rejected delegations are also audited before the rejection fact is emitted",
          "src/maezo/a2a/dispatcher.py:219-240 — _audit_delegation builds AuditRecord with origin, chain, input_hash (not PHI), and calls self._audit.record(record)",
          "tests/unit/a2a/test_dispatcher.py:54-59 — asserts rec.agent_id=='helena', rec.tool=='a2a.delegate:rafael', rec.decision_basis=='A2A:delegate:allow', and verify_chain(sink.records) is True"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "The Agent Card registry is tenant-scoped: lookup/list require a tenant argument and cannot return cards from another tenant (zero cross-contamination).",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:123-161 — AgentCardRegistry uses (tenant, agent_id) as composite key; lookup() and list_cards() require tenant parameter",
          "src/maezo/a2a/registry.py:57-58 — AgentCard.__post_init__ raises RegistryError if tenant is empty",
          "tests/unit/a2a/test_registry.py::test_registration_is_tenant_scoped — asserts same agent_id in different tenants are distinct entries and cross-tenant lookup raises RegistryError",
          "tests/unit/a2a/test_registry.py::test_list_cards_is_tenant_filtered_and_sorted"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0004"
        ]
      },
      {
        "claim": "Agent Cards are derived from the AgentDefinition (agent.yaml) as the single source of truth, not a parallel copy.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:71-106 — AgentCard.from_definition() extracts agent_id, version, security_zone from AgentDefinition; capabilities/skills/accepted_task_types from the agent.yaml 'a2a:' section",
          "tests/unit/a2a/test_registry.py::test_from_definition_references_agent_yaml — loads helena/agent.yaml, derives card, asserts agent_id=='helena', version matches definition, capabilities includes 'health_navigation', queue_ref=='agents.tasks.helena'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The Kafka facts partition key is the tenant id (not a random or fixed key), ensuring per-tenant ordering.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:48-53 — FactProducer.emit() calls producer.send(fact.topic, fact.to_value(), key=fact.tenant.encode('utf-8'))",
          "tests/unit/a2a/test_dispatcher.py:49 — assert all(key == b'amh' for (_, _, key) in producer.sent)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Kafka fact REQUESTED is emitted before routing to the handler, and COMPLETED only after the handler succeeds.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:176-189 — _emit(REQUESTED) called before handler(envelope), _emit(COMPLETED) called after output returned",
          "tests/unit/a2a/test_dispatcher.py:48 — assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED] (ordered list assertion)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The DelegationDispatcher validates that the target agent card accepts the task_type before routing; dispatcher rejects mismatched task_types structurally.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:61-69 — AgentCard.accepts(task_type) returns False if accepted_task_types is declared and task_type not in it",
          "src/maezo/a2a/dispatcher.py:204-208 — _validate() returns TASK_TYPE_NOT_ACCEPTED rejection if not card.accepts(envelope.task_type)",
          "tests/unit/a2a/test_dispatcher.py::test_task_type_not_accepted_rejected — card with accepted={'authorization.analyze'}, delegate with task_type='clinical.decision' gets TASK_TYPE_NOT_ACCEPTED"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The registry backing store is in-memory only (not persisted); Agent Cards are lost on process restart.",
        "status": "partial",
        "evidence": [
          "src/maezo/a2a/registry.py:124 — docstring: 'Registro tenant-scoped de Agent Cards (in-memory; backing store real depois)'",
          "src/maezo/a2a/registry.py:131 — self._cards: dict[tuple[str, str], AgentCard] = {}"
        ],
        "gap": "The ADR does not explicitly call the registry persistent, but production-grade A2A interop requires durable card registration. The backing store is acknowledged as deferred ('backing store real depois'). No persistent registry exists yet.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "CDC (Tasy Debezium) events are consumed by the platform but never re-produced by the maezo platform (CDC intacto). Only a dev/CI simulator produces CDC topics.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/consumer.py:1-8 — fhir_sync only consumes cdc.amh.tasy.* topics",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:1-6 — the only component that produces cdc.amh.tasy.* topics is explicitly a 'Simulador' for dev/CI",
          "config/topic_registry.yaml:19-23 — cdc topics annotated 'fonte: amh-data-platform (Debezium). Consumer: fhir_sync'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The delegation envelope carries deadline (timezone-aware UTC) and rejects naive datetimes; expired envelopes are rejected before routing.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:114-115 — __post_init__ raises DelegationError if deadline.tzinfo is None",
          "src/maezo/a2a/delegation.py:126-130 — expired() method checks deadline against now(UTC)",
          "src/maezo/a2a/dispatcher.py:194-197 — _validate() returns EXPIRED rejection if envelope.expired()",
          "tests/unit/a2a/test_anti_loop.py::test_deadline_must_be_tz_aware_and_expiry_detected",
          "tests/unit/a2a/test_dispatcher.py::test_expired_envelope_rejected_before_routing — asserts handler.call_count == 0 and TOPIC_REJECTED emitted"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      }
    ]
  },
  {
    "title": "ADR-0004 Forensic Verification: Tenancy + Federated Agent Definitions",
    "adr": "0004",
    "claims": [
      {
        "claim": "Instance-per-tenant: each tenant gets its own Kubernetes namespace with a dedicated CIB Seven engine, PostgreSQL+pgvector database, HAPI FHIR R4 instance, and LangGraph runtime",
        "status": "partial",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/namespace.yaml:1 — Helm chart renders one namespace per tenant (maezo.io/tenant label)",
          "deploy/helm/maezo-tenant/values.yaml:52-108 — per-agent Deployment factory with per-agent NetworkPolicy and ServiceAccount; comment explicitly says 'per-tenant instance'",
          "deploy/terraform/envs/prod-amh-sa-east-1/main.tf:53-73 — EKS module instantiated per env (comment: 'dedicated cluster per ADR-0004'); Aurora PostgreSQL provisioned per tenant",
          "deploy/helm/maezo-tenant/values.yaml:232-238 — cibseven block present but only externalUrl, no in-cluster StatefulSet rendered in this chart"
        ],
        "gap": "The Helm chart provisions the agent runtime namespace, Postgres (Aurora), and references an external CIB Seven URL, but there is no in-cluster CIB Seven StatefulSet rendered by this chart (values.yaml:232 — cibseven.externalUrl is empty by default). HAPI FHIR is also not rendered in the chart templates found; there are no Deployment templates for cibseven or hapi-fhir inside deploy/helm/maezo-tenant/. The ADR claims these are per-tenant dedicated instances but the Helm chart does not render them. No integration test verifies that a second tenant gets an isolated Postgres and FHIR instance.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006",
          "0008"
        ]
      },
      {
        "claim": "Agent Definition is a federated artifact (agent.yaml: prompt + graph + tools + autonomy policy + KPIs) structured in four layers: L0 (core platform) / L1 (regulatory BR) / L2 (segment) / L3 (tenant override)",
        "status": "partial",
        "evidence": [
          "src/maezo/agents/_template/agent.yaml:1-25 — defines the contract artifact shape with all named fields",
          "src/maezo/platform/tenancy/agent_def_merge.py:1-288 — merge engine implements L0-base + ordered overlays; overlay_labels accept 'L1','L2','L3'",
          "src/maezo/platform/tenancy/agent_def_merge.py:197 — docstring: 'L1 regulatorio / L2 segmento / L3 tenant', applied in order",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:141-148 — test_multiple_overlays_apply_in_order: L1+L3 applied in sequence, last wins on scalars"
        ],
        "gap": "Only L0-core.yaml exists in src/maezo/policies/autonomy/; there are no L1-regulatory or L2-segment YAML files on disk. The merge engine accepts them as arguments but no actual L1/L2 artifacts exist in the repo. The CI validate-artifacts pass only validates L0-core + tenants-*.yaml; it does not enforce or validate L1/L2 layer files. The four-layer federation is implemented in the engine but only two layers (L0, L3) are materially present.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "L0-hard actions (coverage denial, clinical decision, fraud accusation, contract termination) are non-lowerable by any tenant config; CI enforces this",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:51-58 — HARD_ACTIONS frozenset frozen in Python code: clinical_decision, authorization_denial, fraud_accusation, contract_termination",
          "src/maezo/gateway/pep.py:155-168 — _parse_core: hard flag = YAML-declared OR name in HARD_ACTIONS; every HARD_ACTIONS item must be L0 or PolicyError is raised",
          "src/maezo/gateway/pep.py:193-196 — _apply_overlay: attempting to touch any key (level/hard/params) of a hard item raises PolicyError",
          "src/maezo/policies/autonomy/_hard_frozen.yaml:1-20 — frozen list of 4 hard items; referenced by CI validate-artifacts",
          "src/maezo/platform/validation/autonomy.py:89-105 — validate_tenant_file: any override touching a frozen hard item is reported as error",
          "src/maezo/platform/validation/autonomy.py:112-136 — reconcile_frozen: hard items removed, demoted, or added without registering in _hard_frozen.yaml all produce CI errors",
          ".github/workflows/ci.yml:53-73 — artifact-validation job runs make validate-artifacts as a CI blocker on every PR and push to main",
          "tests/unit/gateway/test_pep.py:193-202 — test_crafted_overlay_cannot_weaken_hard_item: parametrized over all 4 HARD_ACTIONS",
          "tests/unit/gateway/test_pep.py:205-223 — test_hard_item_denied_even_if_core_yaml_omits_hard_flag: forged core that sets clinical_decision to L3 is rejected",
          "tests/unit/gateway/test_pep.py:226-241 — test_no_flag_bypasses_hard_deny_at_runtime",
          "tests/unit/sec/test_pep_bypass.py:76-125 — adversarial parametrized battery: nested params, duplicate keys, level aliasing, YAML bool coercion all rejected",
          "tests/unit/validation/test_autonomy.py:46-71 — test_hard_item_demoted_fails, test_hard_item_removed_fails, test_tenant_override_touching_hard_fails"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0008"
        ]
      },
      {
        "claim": "Tenant customization via L3 is restricted to prompt/policy overlay and few-shot; fine-tuning of model weights per tenant is prohibited",
        "status": "partial",
        "evidence": [
          "docs/adr/0004-tenancy-federated-agent-definitions.md:14 — 'Proibido fine-tuning de pesos por tenant'",
          "src/maezo/platform/tenancy/agent_def_merge.py:11-19 — overlay allowed to modify model/prompts/kpis/memory via deep-merge (L3 personalization); no mechanism to specify or load per-tenant model weights",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:136-138 — test_overlay_can_add_new_metadata_key verifies few_shot is addable by overlay",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:106-118 — test_overlay_deep_merges_model_and_prompts: overlay can change model tier/prompt version"
        ],
        "gap": "The prohibition on fine-tuning per tenant is stated in the ADR but there is zero implementing code that detects or rejects a tenant overlay trying to reference a fine-tuned model. The model field in agent.yaml only accepts {provider, tier} descriptors; the InferenceRouter resolves these via config/inference_routing.yaml. Nothing in the merge engine, PEP, or CI checks that a tenant does not route to a per-tenant fine-tuned model endpoint. The prohibition exists purely as a policy declaration, not as an enforceable code invariant.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0009"
        ]
      },
      {
        "claim": "Shared control plane (observability aggregation, provisioning) contains no PHI",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/values.yaml:168-219 — NetworkPolicy two-zone model keeps PHI-zone agent egress restricted to BR-resident endpoints only",
          "src/maezo/platform/metrics_server.py:10 — metric labels are bounded by provisioning/deployment (tenant/escalation_reason); no PHI fields in label set",
          "scripts/provision_tenant.py — referenced by tests/unit/platform/test_provision_tenant.py:27; provisioner writes Helm values (agent definitions, hashes) but not patient data"
        ],
        "gap": "No test explicitly verifies that aggregated observability data (Prometheus metrics, AMP/AMG dashboards in deploy/observability/) is free of PHI. The shared-control-plane claim is an architectural assertion. The metric definitions in metrics_server.py use structured labels without PHI fields, which is partial evidence, but there is no scraping test or data-flow test confirming that pseudonymized trace data does not leak PHI into the shared AMP workspace.",
        "confidence": "inferred",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006",
          "0014"
        ]
      },
      {
        "claim": "Overlay can only RESTRICT (subset) tools and autonomy_actions; it cannot add items outside the base (defense-in-depth: tenant cannot expand external-effect surface)",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/tenancy/agent_def_merge.py:108-134 — _merge_allowlist: overlay items not in base_set raise MergeError('ADICIONAR a `tools`')",
          "src/maezo/platform/tenancy/agent_def_merge.py:62 — _ALLOWLIST_KEYS = ('tools', 'autonomy_actions')",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:191-194 — test_overlay_cannot_add_tool_outside_base",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:197-200 — test_overlay_cannot_add_autonomy_action_outside_base",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:160-165 — test_overlay_restricts_tools_to_subset",
          "tests/unit/platform/test_provision_tenant.py:282-296 — test_overlay_cannot_add_tool_outside_base (end-to-end provision path)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0008"
        ]
      },
      {
        "claim": "Every agent has a human manager (reports_to) and a guaranteed human escalation route (escalation.process); overlays cannot remove these governance anchors",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/tenancy/agent_def_merge.py:64-65 — _GOVERNANCE_KEYS = ('reports_to',); overlay that sets it to None or empty string raises MergeError('ancora de governanca')",
          "src/maezo/platform/tenancy/agent_def_merge.py:243-251 — governance anchor check in merge loop",
          "src/maezo/platform/validation/agents.py:21-32 — REQUIRED_KEYS includes 'reports_to' and 'escalation'; missing/empty raises error",
          "src/maezo/platform/validation/agents.py:77-79 — escalation without .process raises error",
          "src/maezo/agents/helena/agent.yaml:5 — reports_to: 'gestor.atendimento@amh'; escalation.process: SP-OP-ESCALATION-001",
          "src/maezo/agents/rafael/agent.yaml:5 — reports_to: 'medico-auditor@amh'",
          ".github/CODEOWNERS:2 — src/maezo/policies/ requires @rodrigotaquino review",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:234-244 — test_overlay_cannot_remove_governance_anchor, test_overlay_cannot_null_governance_anchor",
          "tests/unit/validation/test_agents.py:23-24 — test_valid_agent_passes; test_missing_required_keys_fails (covers reports_to)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0005"
        ]
      },
      {
        "claim": "The runtime registers the version hash of the effective (merged) agent definition at startup for provenance (ADR-0007 traceability)",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/harness.py:79-95 — AgentDefinition.version_hash = SHA-256 of canonical YAML; agent_version = first 12 hex chars of effective_version_hash",
          "src/maezo/runtime/harness.py:130-168 — load_agent_definition_merged: calls merge engine, stores eff.version_hash as effective_version_hash",
          "src/maezo/runtime/harness.py:370 — GatewayToolExecutor passes agent_version (derived from effective hash) into every PepToolCall",
          "src/maezo/runtime/harness.py:463 — build_agent passes definition.agent_version to executor",
          "deploy/helm/maezo-tenant/templates/configmap-agent-definition.yaml:36 — maezo.io/agent-definition-hash annotation carries definitionHash (set by provision_tenant.py)",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:278-284 — test_version_hash_is_deterministic: same definition always produces same 64-char SHA-256",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:286-290 — test_version_hash_independent_of_key_order",
          "tests/unit/runtime/test_harness_merge.py:1-16 — D9 harness merge tests cover hash delegation"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "Security zone of an agent (general vs phi) cannot be lowered by a tenant overlay (phi -> general is prohibited)",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/tenancy/agent_def_merge.py:57-58 — _ZONE_ORDER = ('general', 'phi'); rank 0 < rank 1",
          "src/maezo/platform/tenancy/agent_def_merge.py:137-148 — _check_security_zone: overlay_zone rank < base_zone rank raises MergeError('AFROUXAR security_zone')",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:218-222 — test_overlay_cannot_weaken_security_zone",
          "tests/unit/platform/tenancy/test_agent_def_merge.py:205-210 — test_overlay_can_harden_security_zone (positive control)",
          "tests/unit/platform/test_provision_tenant.py:162-172 — test_cannot_relax_phi_agent_to_general (provision path end-to-end)",
          "deploy/helm/maezo-tenant/templates/configmap-agent-definition.yaml (FIX 3 referenced in test_provision_tenant.py:542-565) — helm template FAILS if values.securityZone disagrees with mounted definition security_zone"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Zero cross-tenant data, memory, or behavioral bleed — agents are isolated by namespace; no cross-tenant communication path exists in the networking model",
        "status": "partial",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:26-105 — default-deny-egress + scoped intra-namespace egress to gateway component only; peer-agent egress denied",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:65-105 — intra-namespace egress scoped by app.kubernetes.io/component to gateway only (FIX 2); PHI -> general-zone relay blocked",
          "deploy/helm/maezo-tenant/values.yaml:178-220 — defaultDenyEgress: true; PHI zone brResidentEndpoints defaults to empty (deny-all external)",
          "tests/unit/platform/test_provision_tenant.py:474-489 — test_rendered_intra_namespace_egress_is_scoped_to_gateway (helm=required mark)"
        ],
        "gap": "The NetworkPolicy ensures per-namespace isolation within a single cluster but the Terraform does not provision one cluster per tenant by default (create_eks_cluster is a toggle variable, default unset; staging and prod-amh share the same EKS cluster module). Cross-namespace Kubernetes NetworkPolicy isolation depends on the underlying CNI correctly enforcing namespace selectors — there is no test verifying that a pod in namespace maezo-amh cannot reach namespace maezo-other. No database-level row isolation or connection string validation test exists to confirm that the runtime in one namespace cannot connect to another tenant's Aurora instance. The claim 'zero cross-tenant bleed by construction' is stronger than what the code mechanically enforces.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006",
          "0008"
        ]
      }
    ]
  },
  {
    "title": "ADR-0005 HITL Architectural Guarantee — Forensic Verification",
    "adr": "0005",
    "claims": [
      {
        "claim": "Mechanism 1 — PEP in the Tool Gateway evaluates EVERY tool call against the autonomy matrix BEFORE any external effect",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:267-306 — PolicyEnforcementPoint.evaluate() / _evaluate_inner() is the single chokepoint that resolves action level, checks allowlist, and returns a PolicyDecision before any fn executes",
          "src/maezo/runtime/harness.py:311-363 — GatewayToolExecutor.invoke() calls self._pep.evaluate(call) on line 357; fn is only reached if decision.decision is Decision.ALLOW (line 358-362)",
          "src/maezo/runtime/harness.py:1-26 — docstring explicitly states 'Encaminhar TODO tool call ao Tool Gateway: PEP.evaluate ANTES do efeito externo'",
          "tests/unit/sec/test_pep_bypass.py:221-250 — test_no_module_invokes_tool_handlers_directly() AST-scans all src/maezo .py for direct .fn( calls; only registry.py and harness.py are whitelisted",
          "tests/unit/runtime/test_harness.py:109-129 — test_executor_deny_hard_does_not_execute_and_audits() proves fn never runs when PEP returns DENY",
          "tests/unit/runtime/test_harness.py:100-106 — test_executor_allow_executes_effect() proves fn runs only after ALLOW"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Mechanism 1 — Autonomy matrix L0-hard items (clinical_decision, authorization_denial, fraud_accusation, contract_termination) are permanently DENY regardless of tenant config or runtime flags",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:51-58 — HARD_ACTIONS frozenset is code-frozen; independent of YAML",
          "src/maezo/gateway/pep.py:155-168 — _parse_core() cross-references HARD_ACTIONS against loaded YAML; raises PolicyError if any hard item is absent or not L0 in the core",
          "src/maezo/gateway/pep.py:193-196 — _apply_overlay() raises PolicyError if overlay touches any hard item (level, hard flag, or params)",
          "src/maezo/policies/autonomy/L0-core.yaml:6-9 — all four hard items declared as {level: L0, hard: true}",
          "src/maezo/policies/autonomy/_hard_frozen.yaml:12-20 — _hard_frozen.yaml lists all four items; CI validates via make validate-artifacts",
          "src/maezo/gateway/pep.py:309-316 — _decide() returns DENY for L0+hard regardless of autonomy_context content",
          "tests/unit/gateway/test_pep.py:193-202 — test_crafted_overlay_cannot_weaken_hard_item parametrized over all HARD_ACTIONS",
          "tests/unit/gateway/test_pep.py:205-223 — test_hard_item_denied_even_if_core_yaml_omits_hard_flag() proves code constant overrides config",
          "tests/unit/gateway/test_pep.py:226-241 — test_no_flag_bypasses_hard_deny_at_runtime() proves autonomy_context cannot override",
          "tests/unit/sec/test_pep_bypass.py:76-86 — test_nested_params_cannot_weaken_hard parametrized",
          "tests/unit/sec/test_pep_bypass.py:89-101 — test_duplicate_keys_in_overlay_cannot_weaken_hard parametrized",
          "tests/unit/sec/test_pep_bypass.py:116-125 — test_yaml_bool_coercion_on_hard_flag_is_rejected",
          "tests/unit/validation/test_autonomy.py:46-49 — test_hard_item_demoted_fails via CI validate-artifacts"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Mechanism 2 — Mandatory human actions are implemented as BPMN User Tasks with timer+escalation; the process cannot advance to an adverse end-event without human completion",
        "status": "verified",
        "evidence": [
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:220-231 — UT_AnaliseMedicoAuditor User Task with candidateGroups medico-auditor; line 250-254 BT_SlaAnalise interruptive timer escalates to UT_CoordenacaoAssume",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:267-272 — UT_CoordenacaoAssume User Task (escalation on SLA breach); line 335-340 UT_RegistrarParecerJunta",
          "src/maezo/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:206-222 — UT_AnaliseRescisao User Task with timer escalation",
          "src/maezo/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:262-277 — UT_AnaliseReembolso; line 332-337 UT_RevisaoAuditorMedico",
          "tests/integration/processes/test_no_denial_consolidated.py:456-478 — test_todo_terminal_adverso_e_human_gated() performs graph reachability analysis on all 9 BPMN bodies; proves no adverse end-event is reachable without crossing a human userTask",
          "tests/integration/processes/test_no_denial_consolidated.py:313-334 — test_registry_cobre_todo_bpmn_presente() ensures every BPMN body is registered; fail-closed",
          "tests/integration/processes/test_sp_op_auth_001.py:342-450 — test_invariant_nenhum_caminho_automatizado_produz_negativa() integration test against real CIB Seven engine proves End_NegadaAuditor never reached without UT human in history",
          "tests/integration/processes/test_sp_op_auth_001.py:516-567 — test_happy_path_negada_pelo_auditor() shows denial only flows after human User Task completion"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0018"
        ]
      },
      {
        "claim": "Mechanism 3 — Credential separation: the signing credential required to formalize a coverage denial does NOT exist in the agent runtime; AgentCredentialView is structurally incapable of reaching HumanCredentialPartition",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/credential_vault.py:164-192 — AgentCredentialView dataclass has no field or method referencing HumanCredentialPartition or SigningCredential; only service_credentials mapping",
          "src/maezo/gateway/credential_vault.py:343-351 — CredentialVault.agent_view() builds AgentCredentialView from service_credentials only; no reference to self._human_partition",
          "src/maezo/gateway/credential_vault.py:312-351 — CredentialVault._human_partition is private; agent_view() never passes it to returned view",
          "tests/architecture/test_credential_separation.py:95-140 — test_s1_no_agent_module_reaches_human_partition() AST scan of all src/maezo/agents/**/*.py; fails if any imports credential_vault or references HumanCredentialPartition",
          "tests/architecture/test_credential_separation.py:143-172 — test_s2_agent_view_type_exposes_no_human_credential_getter() introspects AgentCredentialView public members for 'signing'/'human'",
          "tests/architecture/test_credential_separation.py:248-281 — test_s4_agent_view_factory_never_reads_human_partition() AST scan of agent_view() body",
          "tests/architecture/test_credential_separation.py:338-361 — test_d4_agent_view_yields_no_human_credential() runtime probe via dir()/getattr/closure inspection",
          "tests/unit/gateway/test_credential_vault.py:207-236 — test_agent_view_never_exposes_signing_canary_anywhere() exhaustive reachability traversal with canary handle"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Mechanism 3 (extended) — get_signing_credential is fail-closed: only a human principal (is_human=True) belonging to a medical audit group and matching tenant can obtain the signing credential; any other principal yields CredentialAccessDenied",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/credential_vault.py:218-285 — HumanCredentialPartition.get_signing_credential(): guards in order: None principal (line 238-240), is_human=False (line 243-249), missing audit group (line 252-258), tenant mismatch (line 267-271), unprovisioned credential (line 274-280); return credential only at line 285 after all guards",
          "src/maezo/gateway/credential_vault.py:113-118 — AUDITOR_GROUPS frozenset: medico-auditor, coordenacao-auditoria-medica, junta-medica",
          "tests/architecture/test_credential_separation.py:175-245 — test_s3_retrieval_is_fail_closed_deny_guards_return() AST proves raise statements precede the single return credential",
          "tests/unit/gateway/test_credential_vault.py:253-270 — test_agent_principal_denied_and_audited()",
          "tests/unit/gateway/test_credential_vault.py:273-305 — test_non_human_with_auditor_group_denied_by_is_human_gate() isolates is_human gate specifically",
          "tests/unit/gateway/test_credential_vault.py:308-316 — test_none_principal_default_deny_and_audited()",
          "tests/unit/gateway/test_credential_vault.py:319-326 — test_human_without_auditor_group_denied_and_audited()",
          "tests/unit/gateway/test_credential_vault.py:339-364 — test_cross_tenant_human_auditor_denied_and_audited() proves cross-tenant isolation"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Mechanism 4 — Every PEP refusal (DENY or REQUIRE_HUMAN) generates an auditable event including input_hash (PHI never raw), decision_basis, autonomy_level, and agent provenance",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:304-306 — _evaluate_inner() calls self._audit_refusal(call, decision) for any non-ALLOW decision",
          "src/maezo/gateway/pep.py:338-352 — _audit_refusal() constructs AuditRecord with input_hash=hash_input(call.tool_input), decision_basis, autonomy_level, model_id, prompt_version, human_approver=None",
          "src/maezo/gateway/audit.py:49-56 — hash_input() SHA-256 canonicalizes payload; PHI never stored raw",
          "src/maezo/gateway/audit.py:165-203 — AuditLog.record() chains records with prev_record_hash + compute_hash()",
          "tests/unit/gateway/test_pep.py:134-144 — test_deny_and_require_human_are_audited_allow_is_not(): ALLOW not audited; REQUIRE_HUMAN and DENY are",
          "tests/unit/gateway/test_pep.py:147-162 — test_audit_record_carries_input_hash_not_raw(): raw CPF not in input_hash field",
          "tests/unit/sec/test_audit_no_phi.py:42-70 — test_audit_jsonl_never_contains_raw_identifiers() scans JSONL for CPF/phone/name patterns",
          "tests/unit/gateway/test_audit.py:70-83 — test_verify_chain_detects_tampering() proves tamper-evidence"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Mechanism 4 (credential refusal) — Every CredentialAccessDenied from HumanCredentialPartition generates an AuditRecord with human_approver=None and zero credential material leaked",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/credential_vault.py:287-309 — HumanCredentialPartition._audit_refusal() emits AuditRecord with decision_basis naming credential class and reason, human_approver=None; handle never in payload",
          "src/maezo/gateway/credential_vault.py:143-161 — SigningCredential.__repr__ redacts handle: '<redacted>'",
          "tests/unit/gateway/test_credential_vault.py:253-270 — test_agent_principal_denied_and_audited(): asserts SYNTH_SIGNING_HANDLE not in exc_info.value, not in rec.decision_basis, not in rec.input_hash",
          "tests/unit/gateway/test_credential_vault.py:406-413 — test_signing_credential_repr_is_redacted(): asserts handle not in repr(cred)",
          "tests/architecture/test_credential_separation.py:298-316 — test_d1_agent_principal_denied_and_refusal_audited(): runtime proof of audit emission and zero material leak"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ADR decision — the agent produces a structured recommendation and dossier; the human medical auditor decides; the decision is issued in the human's name; clinical/diagnostic decisions by agent are prohibited, hard-coded, non-configurable",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:51-58 — HARD_ACTIONS frozenset in code, non-configurable; clinical_decision and authorization_denial are members",
          "src/maezo/tools/workers/auth.py:232-330 — make_send_denial_notice_handler(): worker only transmits a denial already decided by the human in User Task (UT_AnaliseMedicoAuditor); guard at line 264-270 raises BPMN error if decisao_auditor != 'NEGAR'",
          "src/maezo/platform/console/app.py:396-494 — complete_task() endpoint: require_human_principal() + require_auditor_group() enforced before engine completion; signing_credential load-bearing; human_approver=principal.email in AuditRecord",
          "tests/integration/processes/test_sp_op_auth_001.py:516-567 — test_happy_path_negada_pelo_auditor(): denial notice worker carries auditor_id from human User Task variables, not generated by agent",
          "tests/unit/tools/workers/test_auth_analyze.py — (file exists; auth analyze worker prepares dossier for auditor, not making denial decision)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0008"
        ]
      },
      {
        "claim": "Audit record includes agent+version, DMN evidence, and human approver, and is cryptographically chained (tamper-evident)",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:63-113 — AuditRecord fields: agent_id, agent_version, tenant, tool, input_hash, decision_basis, autonomy_level, dmn_versions, model_id, prompt_version, human_approver, ts, prev_record_hash, record_hash",
          "src/maezo/gateway/audit.py:104-107 — compute_hash() SHA-256 includes prev_record_hash, creating a cryptographic chain",
          "src/maezo/gateway/audit.py:205-208 — _seal() links each record to the previous hash",
          "src/maezo/gateway/audit.py:211-223 — verify_chain() validates the full chain integrity",
          "src/maezo/platform/console/app.py:480-492 — console complete_task records AuditRecord with human_approver=principal.email and signing_provenance in decision_basis",
          "tests/unit/gateway/test_audit.py:58-67 — test_record_seals_with_hash_and_chains(): proves chaining",
          "tests/unit/gateway/test_audit.py:70-83 — test_verify_chain_detects_tampering(): tampered record breaks chain",
          "tests/integration/platform/test_audit_chain_durable.py — (exists; durable Postgres-backed audit chain integration test)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "L0-hard items are non-lowerable by any tenant configuration; CI enforce this via _hard_frozen.yaml cross-check",
        "status": "verified",
        "evidence": [
          "src/maezo/policies/autonomy/_hard_frozen.yaml:1-20 — list of 4 hard items; intended as CI oracle",
          "src/maezo/platform/validation/autonomy.py:30-134 — validate_dir() loads _hard_frozen.yaml, checks core layers have all frozen items at L0, and tenant overrides never touch hard items; called by make validate-artifacts",
          ".github/workflows/ci.yml:54-72 — validate-artifacts job runs make validate-artifacts as a CI blocker",
          "tests/unit/validation/test_autonomy.py:46-64 — test_hard_item_demoted_fails, test_hard_item_removed_fails, test_new_unregistered_hard_item_fails, test_tenant_override_touching_hard_fails"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0008"
        ]
      },
      {
        "claim": "The send_denial_notice BPMN worker refuses to transmit a denial if the process variable decisao_auditor is not 'NEGAR', providing a defense-in-depth guard against automated denial pathways",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/workers/auth.py:257-270 — handler() raises WorkerBpmnError(_ERR_DENIAL_NOT_HUMAN) if decisao_auditor != 'NEGAR'; message explicitly states 'negativa formal exige decisao humana (NEGAR) na User Task medico-auditor'",
          "tests/integration/processes/test_sp_op_auth_001.py:516-567 — test_happy_path_negada_pelo_auditor(): worker is only triggered after human completes UT with decisao_auditor='NEGAR' and auditor_id set"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The PEP agent allowlist is a second independent check: a tool outside the agent's declared allowlist is DENY even if its matrix action is L3",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:276-286 — _evaluate_inner() checks agent allowlist first, before matrix lookup; denies and audits if tool not in allowlist",
          "tests/unit/gateway/test_pep.py:114-122 — test_tool_outside_allowlist_is_denied_even_for_l3(): triage_and_routing (L3) with tool not in allowlist returns DENY with 'allowlist' in reason",
          "tests/unit/sec/test_pep_bypass.py:184-208 — test_tool_name_spoofing_does_not_escape_allowlist(): 8 spoofed variants (whitespace, homoglyphs, case) are all DENY"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "The integration test for SP-OP-AUTH-001 against the real CIB Seven engine proves no automated pathway produces a denial (runs as a pytestmark.integration test requiring the dev-stack)",
        "status": "unverified",
        "evidence": [
          "tests/integration/processes/test_sp_op_auth_001.py:342-450 — test_invariant_nenhum_caminho_automatizado_produz_negativa() exists and has correct logic; marked with pytestmark = pytest.mark.integration",
          "tests/integration/processes/test_sp_op_auth_001.py:211-218 — skips if CIB Seven engine unavailable ('suba com make dev-stack')"
        ],
        "gap": "Test exists and logic is correct, but it only runs against a live CIB Seven engine. No evidence it is currently passing in CI — the .github/workflows/ci.yml only shows the unit+architecture lane running unconditionally; the integration lane requires the dev-stack (docker compose). Cannot confirm the test has been executed to green against the real engine from the code alone.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0011"
        ]
      },
      {
        "claim": "The static BPMN graph-reachability test (test_no_denial_consolidated.py) runs without engine dependency and proves all adverse end-events are human-gated across all 9 BPMN bodies",
        "status": "verified",
        "evidence": [
          "tests/integration/processes/test_no_denial_consolidated.py:456-478 — test_todo_terminal_adverso_e_human_gated(): engine-free XML parse + graph reachability; proves adverse ends unreachable without human userTask completion",
          "tests/integration/processes/test_no_denial_consolidated.py:1-62 — docstring explicitly states 'ENGINE-FREE (roda no lane rapido)'; uses only xml.etree.ElementTree",
          "tests/integration/processes/test_no_denial_consolidated.py:313-334 — fail-closed guard: new BPMN without registry entry turns test RED",
          "tests/integration/processes/test_no_denial_consolidated.py:400-438 — exhaustive classification: every end-event in every body must be in ADVERSE|BENIGN|NEUTRAL",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn — BPMN file exists with correct structure"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      }
    ]
  },
  {
    "title": "ADR-0006 PHI Two-Zone Forensic Verification",
    "adr": "0006",
    "claims": [
      {
        "claim": "PHI is pseudonymized in the Tool Gateway BEFORE reaching General Zone (LLM) context — the pseudonymization is structural, not conventional",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/phi_zone.py:93-115 — PhiZoneGateway.scrub_result() enforces pseudonymization for consumer_zone='general'",
          "src/maezo/tools/registry.py:179-211 — ToolRegistry._apply_phi_zone() called on every handler output before return; raises RuntimeError if phi_gateway is absent when general consumer requests phi_fields (fail-closed)",
          "src/maezo/tools/registry.py:100-172 — ToolRegistry.invoke() always calls _apply_phi_zone() as the sole exit path of a tool handler",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d1_raw_phi_pseudonymized_for_general_consumer",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s1_invoke_calls_phi_zone_seam_on_handler_output — AST verifies _apply_phi_zone() is called before return",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s3_registry_requires_gateway_for_general_phi_consumer",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d4_no_gateway_general_consumer_is_fail_closed"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "The re-identification map (surrogate store) lives ONLY inside the gateway; PHI raw values are never logged",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pseudonymizer.py:53-76 — SurrogateStore protocol + InMemorySurrogateStore implementation; depseudonymize() only accessible inside gateway",
          "src/maezo/gateway/audit.py:49-56 — hash_input() produces SHA-256 of payload; AuditRecord carries input_hash not raw PHI",
          "src/maezo/gateway/audit.py:64-113 — AuditRecord never stores raw PHI; only input_hash field",
          "tests/unit/sec/test_audit_no_phi.py::test_audit_jsonl_never_contains_raw_identifiers",
          "tests/unit/sec/test_audit_no_phi.py::test_hash_input_is_not_reversible_to_phi"
        ],
        "gap": "The SurrogateStore v0 is InMemorySurrogateStore (lost on pod restart), not the planned Postgres backend. If the process restarts, the re-identification map is gone. The Postgres implementation is noted as planned but not present in gateway/ (only referred to in pseudonymizer.py line 55).",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "PHI Zone agents (Rafael, Marina, etc.) use only on-premises or BR-resident zero-retention model endpoints — never the General Zone cloud provider",
        "status": "partial",
        "evidence": [
          "src/maezo/runtime/inference.py:344-360 — _PhiNotConfiguredProvider raises PhiEndpointNotConfigured; structurally cannot fall back to general cloud provider",
          "src/maezo/runtime/inference.py:418-428 — InferenceRouter.provider_for() returns sentinel when zone is PHI; never calls _general_provider()",
          "src/maezo/runtime/inference.py:505-509 — load_routing() rejects phi zone routes that declare an anthropic/fake provider",
          "config/inference_routing.yaml:30-34 — PHI routes only map to provider 'phi-br' (placeholder)",
          "tests/unit/runtime/test_inference.py::test_phi_zone_blocks_with_explicit_error",
          "tests/unit/runtime/test_inference.py::test_phi_route_never_returns_general_provider",
          "tests/unit/runtime/test_inference.py::test_load_routing_rejects_general_provider_in_phi_zone"
        ],
        "gap": "The BR-resident zero-retention endpoint is explicitly BLOCKED/not contracted (inference.py line 17: 'BLOQUEADO: nao ha endpoint contratado ainda'). All PHI zone inference calls will fail with PhiEndpointNotConfigured at runtime. The ADR claims this zone is operational for Rafael, Marina, Beatriz, Valentina, Carolina, Andre — but no actual BR-resident provider is wired. Zero-retention is asserted in comments but not contractually enforced in code.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Kubernetes NetworkPolicy enforces PHI zone egress — PHI agents are restricted to fixed CIDRs, 0.0.0.0/0 is prohibited, and fail-closed when no endpoints are configured",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:25-41 — default-deny-egress NetworkPolicy renders for all pods",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:113-160 — PHI-zone agents get egress ONLY to brResidentEndpoints; empty list = deny all (fail-closed)",
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:118-129 — requirePhiCidr helper fails Helm rendering if cidr is missing or is 0.0.0.0/0",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:63-105 — intra-namespace egress scoped to gateway component only (FIX 2: prevents PHI pod -> general-zone pod relay)",
          "deploy/helm/maezo-tenant/values.yaml:216 — phiZone.brResidentEndpoints defaults to empty list (deny all)",
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:46-48 — maezo.io/phi-zone pod label derived from securityZone value; NetworkPolicy selects on this label"
        ],
        "gap": "No test exercises Helm rendering to confirm the NetworkPolicy renders correctly (no helm template CI test found). The NetworkPolicy relies on Kubernetes ipBlock which is IP-based, not hostname-based — the template itself acknowledges this limitation (networkpolicy.yaml:21-23), stating true FQDN allowlisting needs an egress proxy or Cilium FQDN policy. CIDRs in values.yaml are empty strings requiring operator configuration at deploy time.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Logs and traces pass through the same pseudonymizer — raw PHI never reaches observability systems",
        "status": "partial",
        "evidence": [
          "config/otel-collector.yaml:8 — comment: 'Logs: pseudonymized per ADR-0006 (application layer; collector does NOT hold raw PHI)'",
          "config/otel-collector.yaml:61-73 — filter/phi_safety_net processor drops spans matching CPF regex pattern as safety net",
          "config/otel-collector.yaml:101,113 — traces and logs pipelines include filter/phi_safety_net processor"
        ],
        "gap": "Application-layer log pseudonymization (the primary guarantee per the ADR) has no implementing code in src/maezo/ — there is no logging middleware or structured logger that routes log messages through PhiZoneGateway/Pseudonymizer. The OTEL collector comment explicitly states 'This collector does NOT apply PHI scrubbing — it is the application's responsibility' (otel-collector.yaml:62-63). The safety-net filter only drops spans containing CPF regex on '*.content' attribute; it does not cover all PHI types (names, phone, CNS, address) in all attributes. No test verifies that Python logger output is pseudonymized before emission.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Semantic memory stores only minimized derivatives (no raw PHI)",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/mcp_memory/server.py:39 — PHI_FIELDS = [] declared at class level; fhir_patient_id noted as internal FHIR ID (not CPF)",
          "src/maezo/tools/mcp_memory/server.py:37 — 'note e assumida pseudonimizada pelo caller' (note is assumed pseudonymized by caller)",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d3_memory_write_routes_through_seam — proves seam applies when phi_fields declared non-empty"
        ],
        "gap": "The ADR guarantee ('memoria semantica armazena derivados minimizados') relies on a caller convention — the `note` field must arrive pre-pseudonymized from the agent. This is a contractual claim enforced by documentation comment, not by code. PHI_FIELDS=[] means the registry will NOT scrub `note` content if a general-zone consumer passes raw PHI in the `note` argument. There is no runtime enforcement that the `note` parameter is already pseudonymized before storage. The guarantee is convention, not structural.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "General Zone cloud model endpoints have DPA + BR-region requirement enforced",
        "status": "absent",
        "evidence": [
          "config/inference_routing.yaml:22-28 — General Zone routes use provider 'anthropic' with model ids like 'claude-haiku-4-5', no region constraint declared",
          "src/maezo/runtime/inference.py:242-338 — AnthropicProvider sends to hardcoded 'https://api.anthropic.com/v1/messages' (US-based endpoint)"
        ],
        "gap": "The ADR states General Zone uses 'modelos cloud com DPA + regiao' (cloud models with DPA + region). No DPA validation, BR-region pin, or region-filtering of provider endpoints exists in code. AnthropicProvider hard-codes the US-based API endpoint. There is no configuration or enforcement mechanism ensuring the cloud provider has a Data Protection Agreement or is using a BR-resident endpoint for General Zone. This is an unverifiable contractual claim with no code enforcement.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "The pseudonymizer uses HMAC-keyed deterministic tokens per (tenant, field_type, value) with key injected from vault — never hardcoded",
        "status": "unverified",
        "evidence": [
          "src/maezo/gateway/pseudonymizer.py:110-113 — Pseudonymizer.__init__() requires non-empty hmac_key, raises ValueError on empty key",
          "src/maezo/gateway/pseudonymizer.py:116-120 — _surrogate() uses hmac.new(key, msg, sha256) with msg=(tenant, field_type, value)",
          "tests/unit/gateway/test_pseudonymizer.py::test_rejects_empty_key",
          "tests/unit/gateway/test_pseudonymizer.py::test_token_differs_by_tenant_and_field_type",
          "tests/unit/gateway/test_pseudonymizer.py::test_token_does_not_leak_original"
        ],
        "gap": "No test verifies that the vault/KMS injection path is exercised in production wiring — tests use synthetic hardcoded keys like b'test-hmac-key-injected-from-vault'. The External Secrets Operator (ESO) configuration does not show a specific secret for the HMAC key. There is no code in runtime/ showing how the hmac_key is retrieved from the vault and injected into Pseudonymizer in the production service startup path.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "No module outside the gateway/registry can call PHI server read methods directly (bypassing the pseudonymization seam)",
        "status": "verified",
        "evidence": [
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s2_no_module_calls_phi_server_read_methods_directly — AST grep over all src/maezo/*.py verifies no direct calls to read_patient, search_patient, read_coverage, search_coverage, retrieve_episodic, semantic_search outside mcp_fhir/server.py and mcp_memory/server.py"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Default consumer_zone is 'general' (fail-closed) — if caller does not declare zone, PHI fields are pseudonymized",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:96-101 — invoke() signature has consumer_zone defaulting to GENERAL_ZONE",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d2b_default_consumer_zone_is_general_fail_closed"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "PHI Zone agents have separate pod labels and distinct credential injection from General Zone agents (no shared cloud identity)",
        "status": "verified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:94-108 — PHI-zone agents inject LLM_PHI_API_KEY from 'phi-api-key' secret; General Zone agents inject LLM_GENERAL_API_KEY from 'general-api-key' secret — mutually exclusive conditional blocks",
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:44-48 — maezo.io/phi-zone label set per agent based on securityZone",
          "deploy/helm/maezo-tenant/values.yaml:85-93 — rafael and marina defined with securityZone: phi"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Agent securityZone in values.yaml must agree with the security_zone field in the mounted effectiveDefinition — mismatch aborts Helm rendering",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:73-88 — validateAgents helper fails rendering if securityZone not in [general, phi] or if it disagrees with effectiveDefinition.security_zone",
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:15 — validateAgents called at render time"
        ],
        "gap": "No automated test exercises the Helm rendering with mismatched securityZone vs effectiveDefinition to confirm the fail is triggered. The validation only runs when effectiveDefinition is present (line 99: 'if $agent.effectiveDefinition'); hand-authored values files without effectiveDefinition bypass this cross-check.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      }
    ]
  },
  {
    "title": "ADR-0007 Agent Identity, Audit & Non-Repudiation — Forensic Verification",
    "adr": "0007",
    "claims": [
      {
        "claim": "Every external effect records the full tuple: (agent_id, agent_version, tenant, tool, input_hash, decision_basis, dmn_versions, model_id, prompt_version, timestamp) in an append-only Postgres table plus the `agents.audit` Kafka topic",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:63-113 — AuditRecord dataclass carries all required fields",
          "src/maezo/platform/migrations/versions/0002_audit_chain.py:30-55 — DDL creates audit_chain with all 14 columns including record_hash, prev_record_hash, dmn_versions (jsonb), model_id, prompt_version",
          "src/maezo/gateway/audit_postgres.py:41-56 — _COLUMNS tuple enumerates all 14 columns matching DDL",
          "src/maezo/gateway/audit.py:147-162 — KafkaSink emits to topic `agents.audit` keyed by tenant",
          "src/maezo/runtime/agent_runtime/service.py:282-366 — runtime wires PostgresAuditSink + KafkaSink + JsonlFileSink into AuditLog at startup",
          "src/maezo/tools/registry.py:154-168 — ToolRegistry records AuditRecord for every allowed invocation",
          "tests/unit/gateway/test_audit.py::test_kafka_sink_keys_by_tenant — verifies topic is agents.audit, key is tenant bytes",
          "tests/integration/platform/test_audit_chain_durable.py::test_chain_persists_and_verifies — real Postgres round-trip"
        ],
        "gap": "autonomy_level is present in the AuditRecord but the ADR tuple specification as written does not list it; code adds it anyway (richer than spec). The ADR says `timestamp` but code uses field name `ts` — same semantic. No gap in practice.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Service identity per agent+tenant via service account and signed Agent Card",
        "status": "partial",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/serviceaccount.yaml:23-42 — per-agent Kubernetes ServiceAccount with IRSA role-arn annotation, one per Values.agents entry",
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:55 — Deployment binds the per-agent SA",
          "src/maezo/a2a/registry.py:33-106 — AgentCard typed dataclass with agent_id, version, tenant, security_zone; derived from AgentDefinition (agent.yaml)",
          "src/maezo/a2a/registry.py:39 — comment: `endpoint/queue_ref reference agent reception point (HTTP/JSON-RPC + mTLS in production; logical queue in Phase 1)`"
        ],
        "gap": "Agent Card is NOT cryptographically signed. The ADR requires `Agent Card assinado` (signed). The AgentCard dataclass has no signature field, no signing key material, and no verification step at registration or lookup (registry.py:134-144). mTLS is noted as future production intent only (Phase 1 comment, not implemented). No test or code verifies Agent Card signature.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0003",
          "0004"
        ]
      },
      {
        "claim": "Append-only hash-chain with tamper-evidence: each record carries prev_record_hash; record_hash is SHA-256 of the canonical record including the previous link. Altering any record breaks the chain.",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:46-224 — AuditLog._seal() sets prev_record_hash then computes record_hash as SHA-256 of canonical_payload(); verify_chain() recomputes and checks every link",
          "src/maezo/platform/migrations/versions/0002_audit_chain.py:36-37 — UNIQUE(prev_record_hash) constraint prevents silent fork; record_hash PRIMARY KEY prevents duplicate",
          "src/maezo/gateway/audit_postgres.py:63 — ADVISORY_LOCK_SQL serialises concurrent appends per tenant",
          "tests/unit/gateway/test_audit.py::test_verify_chain_detects_tampering — mutation of decision_basis breaks chain",
          "tests/unit/gateway/test_audit.py::test_verify_chain_detects_reorder — reordering records breaks chain",
          "tests/integration/platform/test_audit_chain_durable.py::test_concurrent_fork_on_same_prev_raises_unique — real Postgres; second fork raises UniqueViolationError"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "PHI never raw in audit records: tool call content enters only as input_hash (SHA-256); the caller must hash before auditing.",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:49-56 — hash_input() SHA-256 canonicalize-then-hash helper",
          "src/maezo/gateway/audit.py:67-68 — AuditRecord docstring: `input_hash already hashed by caller — this record never carries raw PHI`",
          "src/maezo/gateway/pep.py:344 — PEP uses hash_input(call.tool_input) before creating AuditRecord",
          "src/maezo/tools/registry.py:128 — ToolRegistry calls hash_input() before auditing",
          "tests/unit/sec/test_audit_no_phi.py::test_audit_jsonl_never_contains_raw_identifiers — scans JSONL output with CPF/phone regex patterns after auditing a call with raw PHI payload",
          "tests/unit/gateway/test_audit.py::test_phi_never_serialized_raw — CPF string absent from JSONL"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Compliance paths (SP-OP BPMN processes) additionally materialise a BPMN process instance in CIB Seven — second audit trail in the engine",
        "status": "partial",
        "evidence": [
          "src/maezo/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:284 — worker documentation references ADR-0007 audit trail recording responsavel_id",
          "src/maezo/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn:255 — worker documentation: `carrega revisor_id + data_envio na trilha de auditoria`",
          "src/maezo/tools/workers/auth_analyze.py:45 — worker operates as external service task completing BPMN process instances",
          "src/maezo/platform/console/app.py:460-470 — complete_task() calls tasklist_client.complete_task() to advance BPMN User Task in engine"
        ],
        "gap": "The BPMN engine (CIB Seven) maintains its own process instance history by design (built-in audit log), and workers complete external tasks advancing the engine. However, there is no code that explicitly creates an additional audit record in a separate `bpmn_audit` table or writes a BPMN instance reference into the Postgres audit_chain. The `process_instance_id` is propagated as a BPMN process variable (workers pass it back) but it is NOT written to an AuditRecord's decision_basis or any field. The ADR claims `caminhos compliance materializam adicionalmente instancia BPMN (segunda trilha, no engine)` — the first trail (Postgres audit_chain) is implemented; the second trail relies entirely on CIB Seven's native history which is external to any code in this repo and has no cross-reference test.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0005"
        ]
      },
      {
        "claim": "decision_basis is structured: satisfied DMN rules, cited evidence, score. L1 actions record the human approver.",
        "status": "partial",
        "evidence": [
          "src/maezo/gateway/audit.py:76 — decision_basis field is type str (free-text, not a structured object)",
          "src/maezo/gateway/pep.py:345 — PEP populates: `PEP:{decision}:{reason}` (string template)",
          "src/maezo/tools/registry.py:139,163 — ToolRegistry populates: `TOOL:deny:...` or `PEP:allow:...`",
          "src/maezo/platform/console/app.py:490 — human decision: `decisao_auditor=NEGAR; signing_credential=medico-auditor.denial-signing:fp=...` (semi-structured)",
          "src/maezo/agents/rafael/graph.py:532,586 — dmn_decision_refs dict is set as BPMN process variable, NOT written into AuditRecord.decision_basis",
          "src/maezo/platform/console/app.py:492 — human_approver=principal.email is recorded for L1 human decisions",
          "tests/unit/console/test_console.py — verifies human_approver field is set on AuditRecord after complete_task"
        ],
        "gap": "decision_basis is an unstructured plain string in the AuditRecord schema (audit.py:76, 0002_audit_chain.py:42 `decision_basis text NOT NULL`). The ADR requires `structured: satisfied DMN rules, cited evidence, score`. The dmn_decision_refs (which would provide DMN evidence and could carry a score) is propagated as a BPMN process variable in agent graphs (rafael/graph.py:532) but is NOT written into any AuditRecord.decision_basis. No score field exists anywhere in AuditRecord. The field is effectively a free-text label, not the structured object the ADR mandates. human_approver IS recorded for L1 decisions (console/app.py:492) as required.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Durable audit chain head persisted in Postgres, recovered on restart to prevent genesis fork (chain continuity across replica restarts)",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit_postgres.py:1-26,158-178 — recover_head() uses structural SQL (NOT ORDER BY ts) to find the unique tail record_hash; PostgresAuditSink persists sealed records",
          "src/maezo/gateway/audit_postgres.py:63-75 — RECOVER_HEAD_SQL uses NOT IN (prev_record_hash) — clock-skew-safe",
          "src/maezo/runtime/agent_runtime/service.py:338-366 — service calls recover_head() at startup, seeds AuditLog(last_hash=...), falls back to GENESIS on DB unavailability with warning",
          "tests/integration/platform/test_audit_chain_durable.py::test_recover_head_continues_chain_across_restart — real Postgres; simulates pod restart; chain continues from r2.record_hash",
          "tests/integration/platform/test_audit_chain_durable.py::test_recover_head_deterministic_under_out_of_order_ts — proves structural recovery is clock-skew-safe",
          "tests/unit/gateway/test_audit_postgres.py::test_recover_head_sql_is_structural_not_ts_ordered"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "Partitioning and TTL for audit data: regulatory retention 5+ years; operational retention months",
        "status": "absent",
        "evidence": [
          "src/maezo/gateway/audit.py:150 — KafkaSink comment mentions `particionamento + TTL` in a parenthetical reference to ADR-0007 only",
          "src/maezo/platform/migrations/versions/0002_audit_chain.py — no TTL column, no Postgres partitioning DDL, no pg_partman or similar",
          "src/maezo/platform/migrations/versions/0001_agents_schema.py:11 — mentions `TTL/expurgo, indices` as future migrations but none exist"
        ],
        "gap": "No implementing code for audit data partitioning or TTL policy. The audit_chain Postgres table has no PARTITION BY clause, no created_at column suitable for range partitioning, no retention/expiry mechanism. The Kafka topic `agents.audit` has no topic-level retention configuration in config/ or deploy/. The 5-year regulatory retention and month-scale operational TTL mentioned in the ADR Consequences section are entirely aspirational — zero implementation exists.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      },
      {
        "claim": "Full reproducible decision chain (replay/post-mortem): verify_chain() over persisted records produces a verifiable tamper-evident sequence",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:211-235 — verify_chain() and parse_jsonl() allow full chain reconstruction from JSONL",
          "src/maezo/gateway/audit_postgres.py:181-212 — chain_from_rows() reconstructs AuditRecords from DB rows sorted by ts+record_hash",
          "tests/unit/gateway/test_audit.py::test_jsonl_sink_is_append_only_and_roundtrips — verify_chain(parse_jsonl(path)) is True",
          "tests/integration/platform/test_audit_chain_durable.py::test_chain_persists_and_verifies — verify_chain on rows read back from real Postgres is True"
        ],
        "gap": "chain_from_rows sorts by (ts, record_hash) which is stated as fragile under clock skew for recovery (audit_postgres.py:63-75 comment), but is only used for display/post-mortem reconstruction, not for the durable head recovery path. No dedicated replay/post-mortem API or CLI exists; the functionality is available through verify_chain + parse_jsonl but not surfaced as a user-facing tool.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Multi-writer safety: concurrent replicas cannot silently fork the audit chain",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit_postgres.py:63 — pg_advisory_xact_lock(hashtext($tenant)) serialises concurrent appends per tenant within a transaction",
          "src/maezo/platform/migrations/versions/0002_audit_chain.py:37 — UNIQUE(prev_record_hash) constraint: second fork raises UniqueViolationError",
          "src/maezo/gateway/audit_postgres.py:139-149 — emit() acquires advisory lock then inserts inside a transaction",
          "tests/integration/platform/test_audit_chain_durable.py::test_concurrent_fork_on_same_prev_raises_unique — asyncio.gather of two conflicting inserts; exactly one UniqueViolationError"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      }
    ]
  },
  {
    "title": "ADR-0008 Autonomy Levels L0-L3 Verification",
    "adr": "0008",
    "claims": [
      {
        "claim": "Autonomy matrix (action x level) lives as versioned YAML in src/maezo/policies/autonomy/ and is evaluated by the PEP on every tool call",
        "status": "verified",
        "evidence": [
          "src/maezo/policies/autonomy/L0-core.yaml:1-36 — version:1 YAML with all action→level mappings",
          "src/maezo/gateway/pep.py:250-306 — PolicyEnforcementPoint.evaluate() called on every tool call; load_matrix() loads L0-core.yaml + tenant overlay",
          "tests/unit/gateway/test_pep.py::test_loads_real_core_and_amh_overlay — loads real L0-core.yaml + AMH overlay",
          "tests/unit/gateway/test_pep.py::test_decision_per_level — parametrized test covering all four levels"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "L0 hard actions (clinical_decision, authorization_denial, fraud_accusation, contract_termination) result in DENY — never executable by the agent",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:51-58 — HARD_ACTIONS frozenset hardcoded in source, independent of YAML",
          "src/maezo/gateway/pep.py:309-317 — _decide() returns Decision.DENY when policy.level is L0 and policy.hard is True",
          "src/maezo/policies/autonomy/L0-core.yaml:6-9 — all four hard actions declared level:L0, hard:true",
          "tests/unit/gateway/test_pep.py::test_decision_per_level — parametrized test asserts DENY for clinical_decision, authorization_denial, fraud_accusation, contract_termination",
          "tests/unit/sec/test_pep_bypass.py::test_hard_action_denied_at_runtime_no_context_override — adversarial test with force_allow=True still gets DENY"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "L0 hard items are non-lowerable by any tenant config — CI rejects overrides",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:155-158 — _parse_core(): hard = bool(spec.get('hard', False)) or name in HARD_ACTIONS — YAML flag cannot remove hardness",
          "src/maezo/gateway/pep.py:193-196 — _apply_overlay(): any overlay touching a hard item raises PolicyError",
          "src/maezo/policies/autonomy/_hard_frozen.yaml:1-20 — immutable list of hard items referenced by CI validator",
          "src/maezo/platform/validation/autonomy.py:89-109 — validate_tenant_file() reports error if tenant override touches a frozen hard item",
          ".github/workflows/ci.yml:54-72 — artifact-validation job runs make validate-artifacts as CI blocker on every PR and push to main",
          "tests/unit/gateway/test_pep.py::test_crafted_overlay_cannot_weaken_hard_item — parametrized adversarial overlay raises PolicyError",
          "tests/unit/gateway/test_pep.py::test_hard_item_denied_even_if_core_yaml_omits_hard_flag — HARD_ACTIONS code set trumps YAML",
          "tests/unit/validation/test_autonomy.py::test_tenant_override_touching_hard_fails — CI validation layer test",
          "tests/unit/sec/test_pep_bypass.py::test_nested_params_cannot_weaken_hard — adversarial overlay params attempt blocked"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "L1 actions (high_value_payment, ans_official_submission, nip_response, provider_decredentialing, erase_patient_memory) require human approval (REQUIRE_HUMAN)",
        "status": "verified",
        "evidence": [
          "src/maezo/policies/autonomy/L0-core.yaml:10-13,35 — all five L1 actions declared",
          "src/maezo/gateway/pep.py:318-329 — _decide() returns Decision.REQUIRE_HUMAN for L0 non-hard and L1 levels",
          "tests/unit/gateway/test_pep.py::test_decision_per_level — parametrized: ans_official_submission and high_value_payment → REQUIRE_HUMAN",
          "tests/unit/gateway/test_pep.py::test_deny_and_require_human_are_audited_allow_is_not — REQUIRE_HUMAN path is audited"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "L2 actions (authorization_approval under DMN favorable + value cap, standard_glosa_processing, start_compliance_process, correlate_process_message, send_beneficiary_template) are ALLOW with sampling review",
        "status": "partial",
        "evidence": [
          "src/maezo/policies/autonomy/L0-core.yaml:14-16,26-27,32 — L2 actions declared",
          "src/maezo/gateway/pep.py:330-336 — _decide() returns Decision.ALLOW for L2 (same path as L3); reason string mentions 'amostragem' only",
          "tests/unit/gateway/test_pep.py::test_decision_per_level — authorization_approval and standard_glosa_processing → ALLOW"
        ],
        "gap": "PEP grants ALLOW for L2 identically to L3 — no sampling/review mechanism is implemented at the gateway or registry layer. The ADR promises 'revisao por amostragem' for L2 but the code only emits a metric label; there is no background sampler, review queue, or rate-limiter that triggers human spot-check for L2 decisions. Telemetry is emitted but the sampling review is absent as a structural control.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "L3 actions (triage, scheduling, informational_response, reminders, query_decision_engine, query_process_status, read_phi_data, send_beneficiary_message, read_write_memory) are ALLOW with telemetry",
        "status": "verified",
        "evidence": [
          "src/maezo/policies/autonomy/L0-core.yaml:18-34 — all L3 actions declared",
          "src/maezo/gateway/pep.py:330-336 — _decide() returns Decision.ALLOW for L2/L3; emits PEP metric via _emit_pep_metric()",
          "src/maezo/gateway/pep.py:38-45,270-271 — _emit_pep_metric() always called after _evaluate_inner()",
          "tests/unit/gateway/test_pep.py::test_decision_per_level — triage_and_routing, scheduling → ALLOW",
          "tests/unit/gateway/test_metric_emission.py::test_pep_allow_emits_global_metric — ALLOW increments PEP_DECISION_TOTAL counter"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The Copilot→Full-stack dial is a YAML diff — never a code change",
        "status": "partial",
        "evidence": [
          "src/maezo/policies/autonomy/tenants-amh.yaml:1 — comment explicitly says 'exemplo do dial Copilot -> Full-stack'",
          "src/maezo/gateway/pep.py:171-223 — _apply_overlay() allows tenant YAML overrides to raise (not lower) restriction on non-hard items",
          "src/maezo/policies/autonomy/tenants-amh.yaml:3-7 — actual AMH override only touches authorization_approval params with max_value_brl:0 (D-07 open)"
        ],
        "gap": "The mechanism exists (overlay YAML evaluated by PEP), but there is currently only one tenant overlay file (tenants-amh.yaml), and it only adjusts params (not level). No 'Copilot mode' vs 'Full-stack mode' YAML profiles exist in the repo — the dial concept is documented in a comment but not instantiated as named profiles. The max_value_brl for authorization_approval remains 0 (unset: D-07 still open), meaning L2 authorization approval has an effective 0-BRL ceiling.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Agent allowlist (from agent.yaml tools list) is evaluated as a second independent check before the matrix; tools outside the allowlist are DENY even for L3 actions",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:276-286 — allowlist check is the FIRST check in _evaluate_inner(), before matrix lookup",
          "src/maezo/gateway/pep.py:355-364 — load_agent_allowlist() reads tools list from agent.yaml",
          "tests/unit/gateway/test_pep.py::test_tool_outside_allowlist_is_denied_even_for_l3 — L3 action denied when tool not in allowlist",
          "tests/unit/sec/test_pep_bypass.py::test_tool_name_spoofing_does_not_escape_allowlist — 8 spoofed tool name variants all denied"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "DENY and REQUIRE_HUMAN decisions are audited (recorded with AuditRecord); autonomy level is recorded in every audit record",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:303-306,338-352 — _evaluate_inner() calls _audit_refusal() for non-ALLOW decisions; _audit_refusal() writes AuditRecord with autonomy_level",
          "src/maezo/gateway/audit.py:63-112 — AuditRecord contains autonomy_level field; hash-chain maintained",
          "tests/unit/gateway/test_pep.py::test_deny_and_require_human_are_audited_allow_is_not — DENY and REQUIRE_HUMAN each produce 1 audit record; ALLOW produces 0 in PEP alone",
          "tests/unit/gateway/test_pep.py::test_audit_record_carries_input_hash_not_raw — PHI not in audit record"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ALLOW decisions for external effects (tool calls) are also audited (registry layer adds audit record on ALLOW)",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:154-170 — ToolRegistry.invoke() writes AuditRecord after successful handler execution for ALLOW decisions",
          "tests/unit/tools/test_registry.py::test_allowed_tool_dispatches_and_audits (line 77-87) — ALLOW invocation produces 1 audit record with 'allow' in decision_basis"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Actions absent from the matrix result in DENY (fail-closed behavior)",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:289-297 — if policy is None (action absent), returns Decision.DENY with reason 'fail-closed'",
          "tests/unit/gateway/test_pep.py::test_unknown_action_fails_closed — absent action → DENY with audit record",
          "tests/unit/sec/test_pep_bypass.py::test_action_missing_from_matrix_fails_closed — adversarial: absent action → DENY"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Autonomy matrix changes require compliance review — enforced via CODEOWNERS on src/maezo/policies/",
        "status": "verified",
        "evidence": [
          ".github/CODEOWNERS:1-3 — src/maezo/policies/ requires @rodrigotaquino review on every PR",
          "src/maezo/policies/README.md:1-4 — explicit statement: 'Mudar autonomia de agente = PR neste diretório, com revisão obrigatória de compliance (CODEOWNERS)'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Tenant overlays can only raise restriction (move to more restrictive level) on non-hard items, never loosen them",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:199-206 — _apply_overlay(): if candidate level rank > base level rank (looser), raises PolicyError('AFROUXAR')",
          "tests/unit/gateway/test_pep.py::test_overlay_may_raise_restriction_on_non_hard — L3→L1 allowed",
          "tests/unit/gateway/test_pep.py::test_overlay_may_not_loosen_non_hard — L1→L3 raises PolicyError",
          "tests/unit/sec/test_pep_bypass.py::test_overlay_loosening_non_hard_is_rejected — adversarial: loosening raises PolicyError",
          "src/maezo/platform/validation/autonomy.py:89-109 — CI validator also catches tenant overrides touching hard items"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Hard-item list (_hard_frozen.yaml) is compared against L0-core.yaml in CI; adding, removing or demoting a hard item fails the build",
        "status": "verified",
        "evidence": [
          "src/maezo/policies/autonomy/_hard_frozen.yaml:1-20 — frozen list with 4 hard actions",
          "src/maezo/platform/validation/autonomy.py:112-136 — reconcile_frozen() compares frozen list vs discovered hard items; missing/extra/changed level → error",
          ".github/workflows/ci.yml:53-72 — artifact-validation job (blocker) runs make validate-artifacts which invokes autonomy.validate_dir()",
          "tests/unit/validation/test_autonomy.py::test_hard_item_demoted_fails — demoted hard item fails CI",
          "tests/unit/validation/test_autonomy.py::test_hard_item_removed_fails — removed hard item fails CI",
          "tests/unit/validation/test_autonomy.py::test_new_unregistered_hard_item_fails — new hard item without frozen registration fails CI",
          "tests/unit/validation/test_autonomy.py::test_missing_frozen_list_fails — absent _hard_frozen.yaml fails CI"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "PEP is the sole enforcement gateway — no module bypasses it to invoke tool handlers directly",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/harness.py:314-363 — GatewayToolExecutor.invoke(): PEP.evaluate() always called before tool.fn(); DENY/REQUIRE_HUMAN never execute fn",
          "src/maezo/tools/registry.py:114-117 — ToolRegistry.invoke(): PEP.evaluate() called first; non-ALLOW raises ToolDeniedError",
          "tests/unit/sec/test_pep_bypass.py::test_no_module_invokes_tool_handlers_directly (line 221-250) — grep-based architecture test scans all src/maezo/*.py for .fn( calls outside harness.py and registry.py"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      }
    ]
  },
  {
    "adr": "0009",
    "title": "ADR-0009: Portfolio de modelos com abstração de provider + eval gates",
    "claims": [
      {
        "claim": "All inference is routed exclusively through `maezo.runtime.inference` — no agent or other module imports a provider SDK directly.",
        "status": "unverified",
        "evidence": [
          "src/maezo/runtime/inference.py:1-548 — single file containing all provider clients (AnthropicProvider, FakeProvider, _PhiNotConfiguredProvider); module docstring explicitly states 'nenhum outro modulo importa SDK/REST de provider'",
          "AGENTS.md:17 — 'Não importar SDK de LLM fora de `runtime/inference.py`'",
          "CONTRIBUTING.md:9 — 'Nenhum SDK de LLM fora de `runtime/inference.py`. Nenhuma credencial fora do gateway.'",
          "grep of src/maezo/agents/ and src/maezo/ for 'import anthropic', 'from anthropic', 'import openai', 'langchain_anthropic' returned zero results — rule is currently satisfied",
          "tests/unit/runtime/test_inference.py covers FakeProvider, AnthropicProvider, routing isolation — but tests do NOT scan for forbidden imports"
        ],
        "gap": "The 'no SDK imports outside inference.py' invariant is documented as policy in AGENTS.md and CONTRIBUTING.md but there is no automated AST-level enforcement test (contrast with tests/architecture/test_credential_separation.py and test_phi_pseudonymization_invariant.py which use AST scanning). Absence of current violations confirmed by grep but not enforced by CI as a structural invariant.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0006",
          "0007"
        ]
      },
      {
        "claim": "Routing by task kind: classification uses a fast/cheap model tier; critical reasoning uses a frontier tier; batch processing uses a batch tier.",
        "status": "partial",
        "evidence": [
          "src/maezo/runtime/inference.py:53-58 — TaskKind StrEnum with FAST, FRONTIER, BATCH values",
          "src/maezo/runtime/inference.py:378-458 — InferenceRouter.resolve_spec keyed by (agent_id, task_kind, zone) with wildcard fallback",
          "config/inference_routing.yaml:22-24 — wildcard routes: fast->claude-haiku-4-5, frontier->claude-opus-4-8, batch->claude-haiku-4-5",
          "tests/unit/runtime/test_inference.py:131-160 — tests for routing resolution by task_kind with wildcard and agent-specific overrides",
          "src/maezo/agents/helena/graph.py:399,420,433 — all three actual inference calls pass 'fast'; frontier tier is never invoked",
          "src/maezo/agents/rafael/graph.py:564-565 — only call uses 'fast'",
          "src/maezo/agents/gustavo/graph.py:758-759 — only call uses 'fast'",
          "src/maezo/agents/helena/agent.yaml:13-14 — model section declares 'task_default: fast' and 'reasoning: frontier' but graph never calls with frontier"
        ],
        "gap": "The 'frontier' and 'batch' tiers are implemented in the router and declared in agent.yaml model sections, but none of the five built agent graphs (helena, rafael, gustavo, marina, lucas) actually pass 'frontier' or 'batch' as task_kind in their inference calls. All production call sites found use the literal string 'fast'. The routing infrastructure is ready but the call-site wiring for non-fast tiers is absent.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "Model configuration is per-tenant and per-agent in the Agent Definition.",
        "status": "partial",
        "evidence": [
          "src/maezo/agents/helena/agent.yaml:12-14 — 'model:' section with task_default and reasoning tiers per agent",
          "src/maezo/agents/rafael/agent.yaml:11-13 — per-agent model section, security_zone: phi",
          "src/maezo/agents/gustavo/agent.yaml:11-13 — per-agent model section",
          "src/maezo/agents/_template/agent.yaml:12-14 — template shows model config per agent definition",
          "config/inference_routing.yaml:22-35 — single shared routing file maps (agent, task_kind, zone) to provider+model_id; agent-specific overrides are possible (helena entries lines 27-28)",
          "src/maezo/runtime/agent_runtime/settings.py:53-55 — INFERENCE_ROUTING_PATH is a single env var (one routing config per service deployment, not per-tenant)",
          "src/maezo/runtime/inference.py:409-415 — router resolves agent-specific route first, then wildcard fallback"
        ],
        "gap": "Per-agent model config is implemented (agent.yaml model section, per-agent routing entries). Per-tenant model config is only structural via the 'one container per tenant' isolation model: a tenant-specific inference_routing.yaml can be deployed per tenant, but the router has no keying by tenant_id — a tenant cannot override model selection at the routing layer without deploying a separate routing file. The agent.yaml model section declares tiers but the harness does not read or apply it to the router (the graph hard-codes task_kind strings at call sites). Connection between agent.yaml model section and actual routing is advisory/documentary only.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0004"
        ]
      },
      {
        "claim": "A golden dataset per agent exists under `tests/evals/golden/` and is run on every prompt/graph/model change as a mandatory CI gate for promotion.",
        "status": "partial",
        "evidence": [
          "tests/evals/golden/helena/conversations.jsonl — exists (generated by _generate.py, 200+ synthetic conversations)",
          "tests/evals/golden/rafael/cases.jsonl — exists",
          "tests/evals/golden/gustavo/cases.jsonl — exists",
          "tests/evals/golden/marina/cases.jsonl — exists",
          "tests/evals/golden/lucas/cases.jsonl — exists",
          "tests/evals/test_helena_golden.py:63-228 — schema validation + 20-case graph smoke test with FakeProvider oracle",
          "tests/evals/gustavo/test_gustavo_golden.py — exists",
          "tests/evals/marina/test_marina_golden.py — exists",
          "tests/evals/lucas/test_lucas_golden.py — exists",
          ".github/workflows/ci.yml:186-229 — 'evals' job triggered on pull_request when agent-touching paths change (src/maezo/agents/**, src/maezo/runtime/**, src/maezo/gateway/**, tests/evals/**)",
          ".github/workflows/ci.yml:236-264 — nightly eval job runs full suite at 03:00 BRT",
          "Makefile:20 — 'pytest tests/evals -q -m eval || [ $$? -eq 5 ]' — exit 5 (no evals collected) is silently accepted",
          "tests/evals/conftest.py:1-53 — fixture seam with golden_dir and eval_prompt_version fixtures; documents the eval/prompt_version tagging contract",
          "src/maezo/runtime/prompt_version.py:1-26 — prompt_version_for() derives version from agent hash",
          "tests/unit/runtime/test_prompt_version.py:1-19 — tests for prompt version tagging"
        ],
        "gap": "Two gaps: (1) The Makefile evals target silently accepts exit code 5 ('no tests collected'), meaning if all eval tests are accidentally excluded or the marker is missing, CI passes without running any evals — the gate can be silently bypassed. (2) There is no branch protection configuration in the repository (no .github/settings.yml or equivalent found) making the 'evals' CI job a required status check that blocks merges; the gate is conditional on paths-filter and advisory rather than structurally blocking. Also, tests use FakeProvider with oracle-derived answers rather than real model inference, so the gate does not actually test model output quality for prompt/model changes — it tests graph routing logic only.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0007"
        ]
      }
    ]
  },
  {
    "title": "ADR-0010 Agent Observability — Forensic Verification",
    "adr": "0010",
    "claims": [
      {
        "claim": "OpenTelemetry SDK is a declared dependency with agent semantics (trace per conversation/A2A task, span per LangGraph node, tool call, and LLM inference including model_id, tokens, cost)",
        "status": "partial",
        "evidence": [
          "pyproject.toml:32-34 — opentelemetry-api>=1.25, opentelemetry-sdk>=1.25, opentelemetry-exporter-otlp-proto-grpc>=1.25 are declared production dependencies",
          "config/otel-collector.yaml:2-3 — comment states 'Per ADR-0010: OpenTelemetry with agent semantics — trace per conversation/A2A task, span per LangGraph node, tool call, and LLM inference (model_id, tokens, cost)'",
          "config/otel-collector.yaml:98-102 — traces pipeline wired: receivers=[otlp], exporters=[debug, otlp/traces]"
        ],
        "gap": "No Python code in src/maezo/ imports or calls opentelemetry (no `from opentelemetry import trace`, no `tracer.start_span`, no `start_as_current_span`). The OTel SDK dependency is declared and the collector is configured, but zero instrumentation exists in the application layer — no spans are ever created. The 'trace per conversation/A2A task, span per LangGraph node, tool call, LLM inference' guarantee is entirely absent from code.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Prometheus metrics catalog exists covering agent KPIs: conversation count, escalation rate, latency (response_duration), human approval rate of L1 proposals (HITL presented/approved), LLM cost per interaction, and eval score",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/metrics.py:158-241 — CONVERSATION_TOTAL, ESCALATION_TOTAL, RESPONSE_DURATION_SECONDS, EVAL_SCORE_LATEST, HITL_PRESENTED_TOTAL, HITL_APPROVED_TOTAL, LLM_COST_USD_TOTAL, LLM_TOKENS_TOTAL defined as Prometheus instruments",
          "src/maezo/runtime/metrics.py:425-498 — helper functions record_conversation, record_escalation, record_response_duration, record_eval_score, record_hitl_presented, record_hitl_approved, record_llm_usage exported",
          "tests/unit/runtime/test_metrics.py:63-178 — TestMetricNames verifies every metric name including conversation, escalation, hitl_presented, hitl_approved, llm_cost, eval_score",
          "tests/unit/runtime/test_metrics.py:472-528 — TestHelperFunctions::test_record_hitl_presented_and_approved, test_record_llm_usage verify helper increment/observe correct metrics",
          "tests/unit/runtime/test_metrics.py:436-455 — test_record_conversation and test_record_escalation verify counter increments"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Per-agent metric emission is live in the runtime — conversation, LLM cost/tokens, response duration, and HITL metrics are actually recorded by the agent runtime harness and inference layer",
        "status": "partial",
        "evidence": [
          "src/maezo/runtime/metrics.py:8-17 — docstring explicitly states 'Emission responsibility: Agent runtime (src/maezo/runtime/harness.py — future PR)' for maezo_conversation_total, maezo_response_duration_seconds, maezo_hitl_*, maezo_llm_*",
          "src/maezo/runtime/harness.py — no calls to record_conversation, record_response_duration, record_llm_usage, record_hitl_presented, or record_hitl_approved anywhere in the file",
          "src/maezo/runtime/inference.py — no calls to record_llm_usage anywhere"
        ],
        "gap": "The metric helper functions for agent runtime KPIs (conversation, latency, LLM cost/tokens, HITL) are defined but NOT called from the runtime code (harness.py, inference.py, service.py). The metrics.py docstring self-documents this as a 'future PR'. These metrics will always read zero in production — the ADR's KPI promise is not yet wired.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Gateway/PEP metric emission (maezo_gateway_pep_decision_total) is live — every tool-call authorization decision is counted",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:38-45 — _emit_pep_metric() calls record_pep_decision best-effort; called at line 271 after every evaluate()",
          "src/maezo/gateway/pep.py:271 — _emit_pep_metric(call.action, call.tenant, decision.decision.value) called unconditionally after PEP decision",
          "tests/unit/gateway/test_metric_emission.py:102-135 — test_pep_deny_emits_global_metric and test_pep_allow_emits_global_metric verify counter increments for real PEP evaluate() calls",
          "tests/unit/gateway/test_metric_emission.py:174-186 — test_pep_metric_failure_does_not_break_request confirms best-effort: metric failure never breaks the request"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Pseudonymizer metrics (tokens processed, operation duration) are emitted by the PHI zone gateway",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/phi_zone.py:32-43 — _emit_pseudo_metrics() calls record_pseudonymizer_duration and record_pseudonymizer_tokens best-effort",
          "src/maezo/gateway/phi_zone.py:110-114 — scrub_result() measures token_count and wall-clock time, calls _emit_pseudo_metrics() on every general-zone scrub",
          "tests/unit/gateway/test_metric_emission.py:214-238 — test_phi_zone_scrub_emits_pseudonymizer_metrics verifies both PSEUDONYMIZER_TOKENS_TOTAL and PSEUDONYMIZER_DURATION_SECONDS increment after scrub_result()"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Audit lag metric (maezo_gateway_audit_lag_seconds) is emitted by the audit module",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:35-42 — _emit_audit_lag() calls set_audit_lag best-effort; called at line 201 after each audit event emit",
          "tests/unit/gateway/test_metric_emission.py:267-289 — test_audit_record_emits_lag_metric verifies AUDIT_LAG_SECONDS gauge is set after AuditLog.record()"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Metric labels MUST be bounded and MUST NOT contain PHI — forbidden labels include conversation_id, beneficiario, cpf, pseudo_id, patient_id, message_id, user_id, phone, wamid, protocolo, protocol, competencia, numero_nip",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/metrics.py:38-58 — label cardinality policy documented and enforced; NEVER use conversation_id, beneficiario_pseudo_id, free-text as labels",
          "src/maezo/platform/metrics_server.py:68-85 — _FORBIDDEN_LABEL_SUBSTRINGS frozenset defines all prohibited label substrings including phase-2 per-filing identifiers",
          "src/maezo/platform/metrics_server.py:88-113 — assert_no_phi_labels() scans the registry and raises ValueError on violation",
          "tests/unit/platform/test_metrics_server.py:112-138 — parametrized test_assert_no_phi_labels_raises_for_forbidden_label covers all 13 forbidden label substrings",
          "tests/unit/runtime/test_metrics.py:305-331 — test_phase2_countdown_labels_are_bounded_not_per_filing explicitly asserts no ANS/NIP metric carries numero_nip/protocolo/competencia labels"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Logs are pseudonymized (ADR-0006 cross-reference) — PHI does not appear in operational logs",
        "status": "partial",
        "evidence": [
          "src/maezo/gateway/audit.py — AuditLog records only input_hash (SHA-256), never raw tool_input PHI",
          "tests/unit/sec/test_audit_no_phi.py:42-70 — test_audit_jsonl_never_contains_raw_identifiers verifies audit JSONL has no raw PHI patterns",
          "src/maezo/platform/console/app.py:322 — comment 'PHI: NÃO logar beneficiario_pseudo_id — apenas contagem'",
          "src/maezo/platform/webhooks/whatsapp/security.py:42 — phone numbers hashed before logging",
          "src/maezo/runtime/agent_runtime/__main__.py:18-20 — structlog configured at INFO level but no PHI-scrubbing processor attached"
        ],
        "gap": "The ADR states 'Logs pseudonymizados (ADR-0006)'. Structured logging uses structlog in workers (ans_submit.py, auth.py, etc.) but no global PHI-scrubbing processor or log filter is configured in structlog.configure() calls. The audit trail enforces hashing, but general operational logs from agent workers and runtime have no enforced PHI-stripping layer — reliance is on coding discipline, not a structural guarantee.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Dashboards — 'ficha de funcionario' (employee card) per agent exist",
        "status": "verified",
        "evidence": [
          "deploy/observability/dashboards/helena-employee.json:8 — 'Helena agent employee dashboard — ADR-0010 ficha de funcionário'",
          "deploy/observability/dashboards/helena-employee.json:255 — human approval rate panel: hitl_approved_total / hitl_presented_total PromQL",
          "deploy/observability/dashboards/gateway.json — gateway dashboard exists",
          "deploy/observability/dashboards/engine.json — CIB Seven engine dashboard exists",
          "deploy/observability/grafana-provisioning/dashboards/dashboards.yaml — Grafana auto-provisioning configured"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Conversation replay: checkpoints + trace enable replay of any past conversation",
        "status": "partial",
        "evidence": [
          "src/maezo/runtime/checkpoint.py:104-143 — AsyncPostgresSaver checkpointer stores LangGraph state per thread_id (conversation_id), enabling state replay via get_state/get_state_history",
          "tests/unit/runtime/test_checkpoint.py — checkpoint tests verify persistence"
        ],
        "gap": "The checkpoint half of 'checkpoints + trace' is implemented. The trace half is absent: no OTel spans are emitted (see claim 1). Without trace data, replay cannot correlate conversation turns with LLM calls, tool calls, and reasoning steps. The ADR's combined 'checkpoints + trace' replay capability is only 50% present.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Alerts: A2A budget exceeded alert fires when A2A delegation budget is exhausted",
        "status": "absent",
        "evidence": [
          "deploy/observability/alert-rules.yaml — no MaezoA2ABudget or A2A budget alert rule exists in the file",
          "src/maezo/a2a/delegation.py:8 — A2A anti-loop guards with max_hops and budget exist in code",
          "config/topic_registry.yaml:13 — 'agents.events.delegation.rejected' Kafka topic documents budget rejections"
        ],
        "gap": "The ADR mandates 'Alertas: budget A2A estourado'. No Prometheus metric counts A2A budget exhaustion events, and no alert rule references such a metric. The anti-loop budget is enforced in the delegation code but the event is not surfaced to the observability layer as a metric or alert.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Alerts: human approval rate drop alert fires when HITL approval rate declines",
        "status": "absent",
        "evidence": [
          "deploy/observability/alert-rules.yaml — no MaezoHITLApprovalDrop or human approval rate alert rule in the file",
          "deploy/observability/dashboards/helena-employee.json:255 — human approval rate panel exists in the dashboard (visualization only, not an alert)",
          "src/maezo/runtime/metrics.py:201-221 — HITL_PRESENTED_TOTAL and HITL_APPROVED_TOTAL metrics defined"
        ],
        "gap": "The ADR mandates 'Alertas: queda de aprovacao humana'. The metric instruments exist and the dashboard panel visualizes the ratio, but no Prometheus alert rule fires when the approval rate drops. The alert is absent.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Alerts: LLM cost spike alert fires when cost per interaction spikes",
        "status": "absent",
        "evidence": [
          "deploy/observability/alert-rules.yaml — no MaezoLLMCostSpike or cost spike alert rule in the file",
          "src/maezo/runtime/metrics.py:223-241 — LLM_COST_USD_TOTAL metric defined"
        ],
        "gap": "The ADR mandates 'Alertas: spike de custo'. No Prometheus alert rule fires on LLM cost spikes. The metric is defined but no alerting rule references it.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Sampling policy: L3 conversations are sampled (not fully retained); L0-L2 conversations have full retention",
        "status": "absent",
        "evidence": [
          "config/otel-collector.yaml — no probabilistic_sampler, tail_sampling, or OTEL_TRACES_SAMPLER configuration",
          "src/maezo/runtime/ — no sampling logic referencing autonomy levels (L0/L1/L2/L3) in any Python file",
          "deploy/helm/maezo-tenant/values.yaml — no OTEL_TRACES_SAMPLER env var set"
        ],
        "gap": "The ADR mandates 'Sampling: conversas L3 amostradas; L0-L2 retencao integral'. No sampling configuration exists anywhere — neither in the OTel collector pipeline, nor in the application code, nor in Helm values. All traces (if they existed) would receive the same treatment, violating the tiered retention guarantee.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Retention policy for traces is configured from day 1",
        "status": "absent",
        "evidence": [
          "deploy/terraform/ — no AMP (Amazon Managed Prometheus) retention or X-Ray retention configuration found",
          "config/otel-collector.yaml:88-102 — traces pipeline configured to export to otlp/traces endpoint, but no retention TTL set",
          "deploy/observability/ — no retention policy document or Prometheus storage configuration"
        ],
        "gap": "The ADR's 'Negativas (aceitas): volume de traces LLM — politica de retencao desde o dia 1' is not implemented. No retention window is configured in the OTel collector, Prometheus, or Terraform modules.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "PHI safety net at the OTel collector layer drops spans containing raw CPF patterns as a defense-in-depth measure",
        "status": "unverified",
        "evidence": [
          "config/otel-collector.yaml:64-73 — filter/phi_safety_net processor configured with regexp to drop spans where *.content matches CPF pattern (\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2})",
          "config/otel-collector.yaml:100-101 — traces pipeline includes filter/phi_safety_net in processors"
        ],
        "gap": "The filter is declared in config but there are no tests that verify the collector actually drops CPF-bearing spans. The config also uses a wildcard attribute key '*.content' which is not valid OTEL collector regex attribute syntax — the actual effectiveness is unverified.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      }
    ]
  },
  {
    "adr": "0011",
    "title": "ADR-0011: Greenfield repo; hospitalar as reference; structural lessons",
    "claims": [
      {
        "claim": "Single src/ tree: all code lives under src/maezo/; no duplicate trees, no .archive, no _old, no _bkp directories in main",
        "status": "unverified",
        "evidence": [
          "src/maezo/ is the only package under src/ — confirmed by ls /Users/familia/code/Maezo-Healthcare-Plan/src/ (single entry: maezo)",
          "find src/ for .archive / _old / _bkp directories returned zero results",
          "CONTRIBUTING.md:5 — 'Árvore única. Todo código em src/maezo/. Sem cópias, sem .archive/, sem _old, sem _bkp no main — histórico é papel do git'"
        ],
        "gap": "The structural rule is stated in CONTRIBUTING.md as a convention but there is no automated CI check or architecture test that enforces the absence of .archive / _old directories. Compliance is observable at the current HEAD only; a developer could add such a directory and CI would not fail.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Artifact validation (BPMN/DMN/policy/agent.yaml) is a CI blocker from commit 1",
        "status": "partial",
        "evidence": [
          ".github/workflows/ci.yml:53-72 — artifact-validation job exists and calls make validate-artifacts",
          "git show 2d35c72 -- .github/workflows/ci.yml confirms the artifact-validation job was present in the initial scaffold commit",
          "git show 2d35c72 -- src/maezo/platform/validation/cli.py shows the initial CLI was a stub that always returned exit 1 (intentionally failing)",
          "Commit d280bb0 (feat(platform): real artifact validation CLI (#2)) replaced the stub with the real validator",
          ".github/workflows/ci.yml:57 — stale NOTE 'expected to be red until W1 merges the validation implementation' is still present despite W1/PR#2 having merged",
          "tests/unit/validation/test_cli.py:14 — test_real_repo_artifacts_pass() runs cli.main() on real repo artifacts and asserts exit code 0",
          "No job in ci.yml has needs: [artifact-validation] — the job runs in parallel and does not block integration or evals jobs at the yaml level"
        ],
        "gap": "Two gaps: (1) From commit 1 through PR#2, the validation job ran but always failed (stub), so it was 'in CI but red by design', not a passing blocker. (2) Currently the artifact-validation CI job has no downstream `needs` from other jobs and no job gates on it — it is a standalone parallel job. Whether it is a GitHub required status check depends on branch protection settings (not verifiable from repo files). The stale NOTE ('expected to be red until W1') in ci.yml line 57 is a documentation drift risk.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Integration tests run against the real CIB Seven + Postgres engine — no engine mocking",
        "status": "verified",
        "evidence": [
          "tests/integration/__init__.py:3 — 'ADR-0011: sem mock de engine — stack docker-compose real obrigatória'",
          "tests/conftest.py:3 — 'Regra inegociavel (ADR-0011): testes integration rodam contra CIB Seven/Postgres REAIS'",
          "AGENTS.md:15 — 'Não mockar o engine em testes de integração. Se a stack docker não está disponível, marque o teste como integration e deixe o CI rodá-lo'",
          "pyproject.toml:63 — pytest marker: 'integration: exige stack docker-compose (engine real — sem mock de engine, ADR-0011)'",
          ".github/workflows/ci.yml:129-160 — integration job spins up docker compose with real CIB Seven + Postgres before running pytest",
          "tests/integration/conftest.py:1 — 'Fixtures de integracao do runtime — Postgres REAL (ADR-0011: sem mock de engine)'",
          "tests/integration/processes/test_sp_op_auth_001.py:4 — 'engine real (ADR-0011: sem mock de engine)'",
          "tests/integration/test_cibseven_escalation.py:3-4 — deploys BPMN+DMN to real engine"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "Agent runtime dependencies (langgraph, a2a-sdk, mcp, kafka) are declared in pyproject.toml from commit 1",
        "status": "verified",
        "evidence": [
          "pyproject.toml:9-26 — langgraph>=0.4, langgraph-checkpoint-postgres>=2.0, a2a-sdk>=1.0, mcp>=1.9, aiokafka>=0.11 all present",
          "pyproject.toml:9 — comment 'Agent runtime (cidadaos de primeira classe desde o commit 1)'",
          "git show 2d35c72 -- pyproject.toml confirms langgraph, a2a-sdk, mcp, aiokafka, asyncpg were all present in initial scaffold commit"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "CDC is designed for the core operadora (not Tasy): this repo consumes the amh-data-platform CDC topics but never duplicates or owns a Tasy CDC pipeline",
        "status": "verified",
        "evidence": [
          "docs/adr/0013-tasy-cdc-amh-data-platform-simulator.md — 'Este repo NAO contem pipeline CDC, conectores Debezium nem credenciais Oracle'",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:84-90 — _CDC_TOPICS consumes cdc.amh.tasy.* but produces nothing back to Tasy",
          "src/maezo/tools/workers/reembolso.py:55 — 'check_coverage e check_prazo NUNCA escrevem no Tasy: consumimos o CDC do amh-data-platform'",
          "src/maezo/tools/workers/contas.py:50 — 'NUNCA escrevem no Tasy: consumimos o CDC do amh-data-platform'",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:1-12 — simulator is 'contrato vivo do envelope CDC do amh-data-platform', not a real CDC producer"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0013"
        ]
      },
      {
        "claim": "The hospitalar repo is reference only: ideas, patterns and content are ported/adapted — no code is imported between repos",
        "status": "unverified",
        "evidence": [
          "CONTRIBUTING.md:7 (How to add a tool) — 'Clientes externos maduros podem ser portados do repo hospitalar (shared/integrations/) — copiar, adaptar, re-testar; nunca importar entre repos'",
          "grep for 'from.*hospitalar' and 'import.*hospitalar' across all .py files returned zero results — no cross-repo imports in current HEAD",
          "src/maezo/platform/integrations/fhir_sync/hapi_client.py — FHIR client exists as standalone adapted code with no reference to hospitalar import paths",
          "src/maezo/platform/webhooks/whatsapp/app.py — WhatsApp webhook exists as standalone adapted code"
        ],
        "gap": "The no-import rule is documented in CONTRIBUTING.md and unviolated at current HEAD, but it is not enforced by any automated check (no CI lint rule, no architecture test scanning for cross-repo imports). Compliance relies on human discipline.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Multi-tenant patterns and webhook patterns are ported from the hospitalar reference (not re-invented from scratch)",
        "status": "unverified",
        "evidence": [
          "src/maezo/platform/tenancy/agent_def_merge.py:1 — L0-L3 federation overlay merge engine exists",
          "src/maezo/platform/webhooks/whatsapp/app.py:1-10 — WhatsApp webhook with HMAC validation and deduplication exists",
          "CONTRIBUTING.md:7 — explicitly states that mature external clients 'may be ported from the hospitalar repo (shared/integrations/)'"
        ],
        "gap": "There is no code-level provenance trail confirming which specific patterns were ported vs invented here. The ADR states the intent; the implementations exist; but whether they are faithful ports or independent rewrites cannot be verified from the repo alone without access to the hospitalar codebase.",
        "confidence": "inferred",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "BPMN CI validation enforced from the start means no dormant artifact non-conformances can accumulate undetected (anti-illusion of test coverage)",
        "status": "partial",
        "evidence": [
          "src/maezo/platform/validation/bpmn.py:1-20 — real BPMN validator checks XML well-formedness, namespace, filename convention, topic registry, User Task candidate groups, start/end events, XSD child ordering, duplicate IDs",
          "src/maezo/platform/validation/dmn.py — real DMN validator exists",
          "tests/unit/validation/test_bpmn.py:1 — 'Testes do validador BPMN com artefatos sinteticos e artefatos reais do repo'",
          "tests/unit/validation/test_cli.py:14 — test_real_repo_artifacts_pass() validates all 9 real BPMN + 33 DMN files",
          ".github/workflows/ci.yml:53-72 — artifact-validation job runs make validate-artifacts on every PR and push"
        ],
        "gap": "The artifact-validation CI job has no `needs` dependencies from downstream jobs (integration, evals) and no other job declares `needs: [artifact-validation]`. This means a PR with broken BPMN could in principle be merged if GitHub branch protection does not mark the job as required. Whether it is a true merge-blocker depends on GitHub branch protection settings that are not visible in the repository files. The stale NOTE at ci.yml:57 ('expected to be red until W1 merges') further suggests the job's gating status was not cleaned up after PR#2 merged.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "DMN CI validation (federacao DMN) is enforced as a blocker from the start",
        "status": "partial",
        "evidence": [
          "src/maezo/platform/validation/dmn.py — real DMN validator (hit policy, schema typeRef, unique IDs)",
          "src/maezo/platform/validation/cli.py:57-58 — cli dispatches dmn.validate_file() for all *.dmn files under processes/dmn/",
          "tests/unit/validation/test_dmn.py — DMN validator unit tests exist",
          "tests/unit/validation/test_cli.py:14 — test_real_repo_artifacts_pass() exercises DMN validation against repo artifacts"
        ],
        "gap": "Same as BPMN CI gating gap: artifact-validation job is not in any job's `needs`, so its blocking status depends on external GitHub branch protection settings not verifiable from the repo. Autonomy/policy overlay validation (federacao DMN per tenant) is implemented in src/maezo/platform/tenancy/agent_def_merge.py but that module is a SKELETON (per its own docstring:1) and tenant-specific DMN override enforcement is not yet fully implemented.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0004"
        ]
      },
      {
        "claim": "Codebase is sized to the problem: no dead code, no archive directories, no inherited BPM-first assumptions forcing BPMN for non-regulatory agent journeys",
        "status": "unverified",
        "evidence": [
          "find src/ for .archive, _old, _bkp returned zero results",
          "125 Python source files in src/maezo/ with only 5 NotImplementedError/stub patterns — no significant dead code mass",
          "docs/processes/journeys/AGJ-HELENA-TRIAGE.md — agent journey exists as LangGraph-only (no BPMN required)",
          "CONTRIBUTING.md:24-25 — 'Sem gatilho [regulatório] → é jornada de agente (AGJ), não BPMN'",
          "src/maezo/agents/helena/graph.py, src/maezo/agents/rafael/graph.py etc. — agent graphs exist without mandatory BPMN wrapper"
        ],
        "gap": "No automated CI check enforces the 'codebase do tamanho do problema' invariant. Size and absence of dead code are observable at current HEAD but no test guards against future bloat or BPM-first regression.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      }
    ]
  },
  {
    "title": "ADR-0012 Forensic Verification: DMN as the deterministic rule tool",
    "adr": "0012",
    "claims": [
      {
        "claim": "DMN is the sole source of deterministic business rules — no rule logic lives in the LLM or agent code",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:1-22 — module docstring explicitly states 'ADR-0012: agentes consomem o RESULTADO da DMN; nunca reimplementam regras'",
          "src/maezo/agents/rafael/graph.py:107 — 'regra deterministica e DMN/worker, nao LLM'",
          "src/maezo/agents/gustavo/graph.py:28-29 — fail-safe closed allowlist; DMN unavailable routes to human, never inlines rule",
          "src/maezo/agents/marina/graph.py:10 — comment documents DMN chain for glosa decisions",
          "src/maezo/agents/helena/graph.py:59-69 — triage_redflag tables invoked, not inlined",
          "src/maezo/agents/lucas/graph.py:114-128 — DMN-backed routing, not inline rule"
        ],
        "gap": "The constraint is architectural intent expressed in comments/docstrings; no automated test asserts that agent code paths cannot bypass DMN (e.g., a lint/grep test that bans inline rule expressions in graph.py files). The 24 DMN files that lack parametrized coverage in test_dmn.py (only 9/33 are in _REPO_DMN_FILES) also weaken the 'sole source' story.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "Agents consume DMN rules via mcp-dmn; SP-OP processes consume the same tables via CIB Seven businessRuleTask (camunda:decisionRef) — dual path, same table",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:62-118 — CibSevenDmnTransport posts to /decision-definition/key/{key}/evaluate",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:69-77 — BRT_Admissibilidade with camunda:decisionRef='auth_admissibility'",
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:175-182 — BRT_AutoApproval with camunda:decisionRef='auth_auto_approval'",
          "src/maezo/agents/rafael/graph.py:73-80 — DMN_ADMISSIBILITY='auth_admissibility', TOOL_DMN='mcp-dmn.evaluate'",
          "src/maezo/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:134-135 — camunda:decisionRef='glosa_classification'",
          "src/maezo/agents/marina/graph.py:92 — DMN_GLOSA_CLASSIFICATION='glosa_classification'",
          "tests/unit/tools/test_mcp_dmn.py:76-96 — test_register_tools_adds_to_registry verifies registration path"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "LLM only reasons about the DMN result (explains, flags pending items, recommends) — never replaces or re-derives the deterministic decision",
        "status": "unverified",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:12-13 — docstring: 'o agente raciocina sobre o RESULTADO da DMN (explica, pendencia, recomenda) — nao a substitui'",
          "src/maezo/agents/rafael/graph.py:594 — comment '--- DMN (estado 3): SEMPRE consultada; LLM nunca decide a regra (ADR-0012)'",
          "src/maezo/agents/gustavo/graph.py:314 — 'ADR-0012: a DMN decide; o LLM raciocina sobre o resultado (dossie)'",
          "src/maezo/agents/auth_analyze.py:24-28 — worker comment 'roda a preparacao de dossie do Rafael... o LLM nunca decide a regra, ADR-0012'",
          "tests/unit/agents/test_rafael_graph.py:187-231 — tests for DMN unavailable -> fail-safe to human (routing, not reasoning)"
        ],
        "gap": "No automated test asserts that the LLM inference call cannot produce a routing or coverage decision that overrides a DMN result. The fake LLM in tests is pre-configured to return deterministic text, so even a real bypass (LLM inverted the DMN outcome) would not be caught. This is a behavioral invariant with comment-level enforcement only.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0005",
          "0008"
        ]
      },
      {
        "claim": "DMN artifact CI validation is a blocker (validates XML well-formedness, hitPolicy, typeRef, unique ids, historyTimeToLive for all DMN files)",
        "status": "partial",
        "evidence": [
          ".github/workflows/ci.yml:53-72 — 'artifact-validation' job runs 'make validate-artifacts' as a blocker on every PR",
          "Makefile:22-23 — validate-artifacts: python -m maezo.platform.validation.cli src/maezo/processes src/maezo/policies src/maezo/agents",
          "src/maezo/platform/validation/cli.py:52-58 — cli globs ALL *.dmn files in the dmn/ directory and calls dmn.validate_file on each",
          "src/maezo/platform/validation/dmn.py:60-178 — validates hitPolicy, typeRef, unique ids, historyTimeToLive, well-formedness",
          "tests/unit/validation/test_dmn.py:17-26 — parametrized test covers only 9 of 33 DMN files in _REPO_DMN_FILES"
        ],
        "gap": "The CLI validator (run in CI) does cover all 33 DMN files via glob. However, the unit test list _REPO_DMN_FILES only parameterizes 9/33 files, leaving 24 DMN files (carencia_check, dut_rol_coverage, dut_criteria_*, cancel_*, contas_*, glosa_*, nip_*, recurso_*, reembolso_*) without an independent unit-level pass confirmation. The CI CLI path covers all, but any regression in the CLI runner itself would not be caught at unit test level.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Payer-side DMN content (DUT/ROL/carencia) and ported hospital content (glosa, elegibilidade) are present in the repository",
        "status": "partial",
        "evidence": [
          "src/maezo/processes/dmn/dut_rol_coverage.dmn — 33 DMN files total; dut_rol_coverage.dmn, carencia_check.dmn, dut_criteria_bariatrica.dmn, dut_criteria_oncologia_pet_ct.dmn, dut_criteria_terapias_especiais.dmn present",
          "src/maezo/processes/dmn/glosa_classification.dmn, glosa_reason_normalization.dmn, glosa_triage.dmn — ported glosa content",
          "src/maezo/processes/dmn/dut_rol_coverage.dmn:9 — 'status: DRAFT — conteudo sintetico/representativo. REQUER revisao humana antes de qualquer deploy'",
          "src/maezo/processes/dmn/carencia_check.dmn:9 — 'status: DRAFT — prazos de carencia SINTETICOS'"
        ],
        "gap": "ALL 33 DMN files are marked DRAFT with synthetic content requiring SME (human expert) review before any production deploy. The payer-side tables (DUT/ROL/carencia) are not wired into any agent graph or BPMN businessRuleTask — they are orphan artifacts with no consuming code path. The ADR states this content needs 'porte com SME', which is not yet complete.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "The contract_extraction pipeline (ported) generates DMN from tenant contracts",
        "status": "absent",
        "evidence": [],
        "gap": "No code implementing a contract_extraction pipeline exists anywhere in the repository. The only mentions are in docs/adr/0012-dmn-deterministic-tool.md:16 and docs/adr/0011-greenfield-repo-hospital-as-reference.md:12, both describing it as a future porte (port) from the reference hospital repo. There is no src/maezo/pipelines/, no scripts/contract_extraction*, and no test for this feature.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0011"
        ]
      },
      {
        "claim": "decision_version from DMN evaluation is captured and propagated to AuditRecord (dmn_versions field) for non-repudiation",
        "status": "unverified",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:42 — DecisionResult.decision_version field",
          "src/maezo/tools/mcp_dmn/server.py:117 — decision_version from X-Decision-Definition-Id header, fallback 'unknown'",
          "src/maezo/gateway/audit.py:78 — AuditRecord.dmn_versions: dict[str, str]",
          "src/maezo/platform/migrations/versions/0002_audit_chain.py:44 — dmn_versions jsonb column in DB schema",
          "tests/unit/tools/test_mcp_dmn.py:50-56 — test_evaluate_returns_version_for_audit asserts decision_version returned",
          "tests/unit/gateway/test_audit_postgres.py:39 — dmn_versions present in audit record fixture"
        ],
        "gap": "The real CIB Seven transport reads decision_version from the HTTP response header X-Decision-Definition-Id, falling back to 'unknown' (server.py:117). There is no integration test that confirms this header is actually present in CIB Seven responses, meaning in production the field may always record 'unknown' rather than the actual DMN version. The unit test uses FakeDmnTransport, not CibSevenDmnTransport.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "DMN inputs contain only IDs/categories, never PHI/personal data",
        "status": "unverified",
        "evidence": [
          "src/maezo/tools/mcp_dmn/server.py:21 — docstring 'PHI fields: nenhum — inputs sao IDs/categorias, nunca dados pessoais'",
          "src/maezo/tools/mcp_dmn/server.py:161 — PHI_FIELDS: list[str] = [] (empty, declared no PHI)",
          "src/maezo/agents/rafael/graph.py:105-108 — 'tudo aqui ja e pseudonimizado (Zona Geral, ADR-0006)'",
          "src/maezo/processes/dmn/carencia_check.dmn:29 — 'Inputs (pre-computados por worker deterministico, nunca por LLM)'"
        ],
        "gap": "No automated test validates that variable dicts passed to mcp-dmn.evaluate at call sites contain no PHI fields. The empty PHI_FIELDS declaration prevents pseudonymization of DMN outputs (correct) but does not enforce that callers cannot pass PHI in the variables dict. This is enforcement-by-convention only.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      }
    ]
  },
  {
    "title": "ADR-0013 Verification: Tasy CDC via amh-data-platform + Simulator as Contract",
    "adr": "0013",
    "claims": [
      {
        "claim": "This repo does NOT contain a CDC pipeline, Debezium connectors, or Oracle credentials. It only consumes topics produced by amh-data-platform.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/producer.py:1-8 — comments explicitly state no CDC pipeline; role is consumer only",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:7 — 'Consumidos aqui por fhir_sync'",
          "deploy/terraform/modules/secrets/main.tf:57-68 — tasy/oracle secret is a BLOCKED shell ('Value set out-of-band'), not a live credential",
          "deploy/terraform/modules/secrets/main.tf:76-82 — MSK bootstrap is a data-source (referenced, not owned) from amh-data-platform",
          "src/maezo/tools/workers/contas.py:48 — 'TASY write DROP' comments in workers citing ADR-0013"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The three CDC topics consumed are: cdc.amh.tasy.pessoa_fisica, cdc.amh.tasy.convenio_paciente, cdc.amh.tasy.autorizacao_convenio.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/consumer.py:84-88 — _CDC_TOPICS list contains all three topic names",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:29-33 — _TABLE_TOPIC maps all three tables to the correct topics",
          "config/topic_registry.yaml:19-21 — all three topics registered with correct descriptions",
          "tests/unit/platform/integrations/test_tasy_simulator.py:166-169 — test_table_topic_mapping asserts correct topic strings"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "aiokafka is used for both the simulator producer and fhir_sync consumer (not confluent-kafka).",
        "status": "verified",
        "evidence": [
          "pyproject.toml:33 — 'aiokafka>=0.11,<1.0' in dependencies; no confluent-kafka dependency",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:21 — 'from aiokafka import AIOKafkaProducer'",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:22 — 'from aiokafka import AIOKafkaConsumer, AIOKafkaProducer'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The Tasy simulator is a permanent CI deliverable (not a temporary hack). It is run as a Docker service under the 'simulator' docker-compose profile.",
        "status": "partial",
        "evidence": [
          "docker-compose.yml:165-177 — tasy-simulator service defined under profiles: [simulator] with TASY_SIM_SEED=0 and TASY_SIM_LOOP=0",
          "deploy/tasy_simulator.Dockerfile:1-14 — Dockerfile exists and runs maezo.platform.integrations.tasy_simulator.main",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:117-123 — run_simulator_ci() entry point with seed=0"
        ],
        "gap": "The CI workflow (.github/workflows/ci.yml) does NOT invoke 'docker compose --profile simulator up'. The integration job starts only 'docker compose --profile core up -d' (line 144), so the simulator is never run in CI despite the ADR claiming it is a CI deliverable. The 'simulator' profile exists in docker-compose.yml but is not wired into any CI step.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The simulator produces events with the Debezium v2 envelope (op, ts_ms, source, before, after) in JSON encoding for dev.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/envelope.py:14-64 — DebeziumEnvelope and DebeziumSource Pydantic models implement op/ts_ms/source/before/after fields per Debezium v2 spec; version hardcoded as '2.7.0.Final'",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:38-51 — _build_envelope builds DebeziumEnvelope from event dict",
          "tests/unit/platform/integrations/test_tasy_simulator.py:35-85 — TestDebeziumEnvelope verifies op, before, after, topic_key, and schema alias serialization",
          "tests/integration/platform/test_tasy_sim_kafka_integration.py:64-97 — test_simulator_envelope_structure asserts op, source, ts_ms on real Kafka message"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The simulator implements exactly five named scenarios: new_beneficiary, coverage_expiry, auth_approved, auth_denied, auth_pending.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/fixtures.py:230-236 — SCENARIOS dict with all five entries",
          "tests/unit/platform/integrations/test_tasy_simulator.py:94-96 — test_all_scenarios_registered asserts exact set"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Simulator fixtures use obviously synthetic data (CPFs in invalid 000.000.000-XX range, synthetic phone numbers, no real PHI).",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/fixtures.py:37 — NR_CPF: '00000000001' (invalid check digits by design)",
          "src/maezo/platform/integrations/tasy_simulator/fixtures.py:39 — NR_CELULAR: '+5511900000001' (non-existent range)",
          "src/maezo/platform/integrations/tasy_simulator/__init__.py:4-6 — docstring explicitly states synthetic data invariant",
          "tests/unit/platform/integrations/test_tasy_simulator.py:107-118 — test_new_beneficiary_synthetic_cpf and test_new_beneficiary_synthetic_phone enforce these invariants"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Consumers MUST pin schema version in test fixtures (upstream CDC schema changes are upstream changes in amh-data-platform).",
        "status": "partial",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/envelope.py:17 — DebeziumSource.version hardcoded as '2.7.0.Final'",
          "tests/unit/platform/integrations/test_fhir_consumer.py:19-26 — test fixtures use hardcoded field structure derived from fixtures.py PACIENTES/COBERTURAS"
        ],
        "gap": "Tests do not explicitly assert envelope schema version (e.g., 'version == 2.7.0.Final'). The hardcoded version in DebeziumSource provides implicit pinning, but no test verifies the version field is correct. The ADR states consumers 'DEVEM pinnar schema version nos fixtures de teste' — this is only partially satisfied through the model default, not an explicit pinned assertion.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "fhir_sync consumer uses manual offset commit (after confirmed HAPI upsert) and a per-tenant consumer group ID 'fhir-sync-{tenant}'.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/consumer.py:121 — enable_auto_commit=False",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:143-145 — await consumer.commit() called after _process_message",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:115 — group_id = f'fhir-sync-{tenant_id}'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "fhir_sync has a consumer-side DLQ topic 'cdc.amh.tasy.fhir-sync.dlq' for messages that fail HAPI upsert.",
        "status": "unverified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/consumer.py:90 — DLQ_TOPIC = 'cdc.amh.tasy.fhir-sync.dlq'",
          "src/maezo/platform/integrations/fhir_sync/consumer.py:196-208 — DLQ publish path on upsert failure with full context payload",
          "config/topic_registry.yaml:23 — topic 'cdc.amh.tasy.fhir-sync.dlq' registered"
        ],
        "gap": "No unit test exercises the DLQ publish path in run_fhir_sync(). The process_single_message helper (used by tests) does not call the DLQ producer — it re-raises the exception (consumer.py:232-234). The _process_message function that writes to DLQ is exercised only in the full run_fhir_sync() loop which is not tested. test_upsert_failure_propagates only verifies that the process_single_message helper raises, not that DLQ is written.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Prod encoding is Avro + Glue Schema Registry (responsibility of amh-data-platform broker); this repo implements JSON encoding for dev only.",
        "status": "unverified",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/envelope.py:1 — module docstring: 'JSON encoding para dev; Avro+Glue em prod — amh-data-platform'",
          "src/maezo/platform/integrations/tasy_simulator/producer.py:8 — 'Encoding: JSON (dev). Avro+Glue é responsabilidade do amh-data-platform broker em prod'"
        ],
        "gap": "No Avro/schema-registry client code, no Avro deserialization adapter, and no test exists in this repo that exercises Avro decoding. The ADR acknowledges this is upstream responsibility, but the consumer has no prod-mode deserializer switch — fhir_sync consumer.py:123 hardcodes json.loads as the value_deserializer with no env-gated Avro path. If the prod broker sends Avro, fhir_sync will fail silently or raise a parse error.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Oracle AMH credentials will be injected via env/secrets gateway when access is provisioned, requiring no architecture change.",
        "status": "unverified",
        "evidence": [
          "deploy/terraform/modules/secrets/main.tf:57-68 — aws_secretsmanager_secret 'tasy/oracle' shell created (Status=BLOCKED-pending-credentials)",
          "deploy/helm/maezo-tenant/templates/externalsecret.yaml:119-141 — ExternalSecret for Tasy Oracle DSN, injected as TASY_ORACLE_DSN env var",
          "deploy/helm/maezo-tenant/templates/deployment-fhir-sync.yaml:41-45 — fhir-sync pod reads TASY_ORACLE_DSN from secret"
        ],
        "gap": "The Helm deployment injects TASY_ORACLE_DSN into fhir-sync, but fhir_sync/consumer.py never reads TASY_ORACLE_DSN — it reads KAFKA_BOOTSTRAP_SERVERS and FHIR_BASE_URL. The Oracle DSN is relevant only if this service would connect directly to Oracle, but the ADR decision is to consume Kafka (amh-data-platform CDC), not Oracle directly. The presence of TASY_ORACLE_DSN in the pod env is unused by the current fhir_sync code, creating a misleading injection with no consuming code path.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The upstream DLQ 'cdc.amh.tasy.dlq' (per-connector, per ADR) is NOT managed by this repo.",
        "status": "verified",
        "evidence": [
          "config/topic_registry.yaml:22 — 'cdc.amh.tasy.dlq: DLQ upstream amh-data-platform por conector Debezium (gerenciado externamente)'",
          "No code in src/maezo/ produces or consumes cdc.amh.tasy.dlq"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "WhatsApp consumer produces to agents.events.whatsapp.message-received and agents.events.whatsapp.status topics.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/webhooks/whatsapp/app.py:92-93 — _TOPIC_MESSAGE and _TOPIC_STATUS constants set to correct topic names",
          "src/maezo/platform/webhooks/whatsapp/app.py:262,273 — publish calls using both constants",
          "config/topic_registry.yaml:25-26 — both topics registered"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "AUTORIZACAO_CONVENIO CDC events are not mapped to FHIR in this version (deferred to Phase 1 as CoverageEligibilityResponse).",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/mapper.py:27 — 'AUTORIZACAO_CONVENIO: Phase 1 (CoverageEligibilityResponse) — ignorado aqui'",
          "src/maezo/platform/integrations/fhir_sync/mapper.py:211 — map_cdc_event returns None for AUTORIZACAO_CONVENIO",
          "tests/unit/platform/integrations/test_fhir_mapper.py:163-166 — test_autorizacao_convenio_returns_none asserts None",
          "tests/unit/platform/integrations/test_fhir_consumer.py:89-105 — test_autorizacao_skipped asserts no upsert called"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "PESSOA_FISICA maps to FHIR R4 Patient and CONVENIO_PACIENTE maps to FHIR R4 Coverage with the field mappings documented in the mapper.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/integrations/fhir_sync/mapper.py:61-106 — tasy_paciente_to_fhir_patient maps all documented fields",
          "src/maezo/platform/integrations/fhir_sync/mapper.py:109-184 — tasy_convenio_to_fhir_coverage maps all documented fields",
          "tests/unit/platform/integrations/test_fhir_mapper.py:18-174 — TestPatientMapper and TestCoverageMapper verify all field mappings field-by-field"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "The simulator CI mode uses a deterministic seed (TASY_SIM_SEED=0) guaranteeing identical replay.",
        "status": "partial",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/producer.py:65-73 — seed parameter filters scenario list deterministically",
          "src/maezo/platform/integrations/tasy_simulator/main.py:28-29 — reads TASY_SIM_SEED env var",
          "docker-compose.yml:173 — TASY_SIM_SEED: '0' set in docker-compose simulator service",
          "tests/unit/platform/integrations/test_tasy_simulator.py:212-228 — test_run_simulator_seed_deterministic verifies same call count across two runs"
        ],
        "gap": "The seed parameter only affects scenario selection order (list comprehension filter), not any random data generation. None of the fixture data is randomized — PACIENTES/COBERTURAS/AUTORIZACOES are static constants. The seed mechanism is a no-op placeholder for 'future randomized data expansion' (producer.py:66). The determinism guarantee is real in practice but not because of the seed; it would be equally deterministic without it.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Pydantic validation enforces type safety on both JSON dev encoding and (by proxy) Avro prod encoding, mitigating schema enforcement differences.",
        "status": "partial",
        "evidence": [
          "src/maezo/platform/integrations/tasy_simulator/envelope.py:14-64 — DebeziumEnvelope and DebeziumSource are Pydantic BaseModel classes",
          "src/maezo/platform/integrations/fhir_sync/mapper.py:61-213 — mapper uses dict[str, Any], no Pydantic model for inbound CDC payloads"
        ],
        "gap": "Pydantic validation applies to the simulator producer (outbound envelope) but NOT to the fhir_sync consumer (inbound). consumer.py deserializes to raw dict[str, Any] via json.loads with no Pydantic validation of inbound Debezium envelopes. A schema mismatch between amh-data-platform and this consumer would only surface as a KeyError/AttributeError at runtime, not a Pydantic ValidationError. The ADR's mitigation claim (mitigated by Pydantic validated in both cases) is only half-true.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      }
    ]
  },
  {
    "title": "ADR-0014 Observability Platform AMP/AMG — Forensic Verification",
    "adr": "0014",
    "claims": [
      {
        "claim": "Staging and prod use Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG); the platform aligns with the amh-data-platform observability-stack Terraform module and shares the same AMP workspace.",
        "status": "absent",
        "evidence": [
          "deploy/terraform/envs/staging-sa-east-1/main.tf — no aws_prometheus_workspace resource or observability-stack module reference",
          "deploy/terraform/envs/prod-amh-sa-east-1/main.tf — no aws_prometheus_workspace resource or observability-stack module reference",
          "deploy/terraform/modules/ — no observability, amp, or amg module exists"
        ],
        "gap": "No Terraform code provisions or references an AMP workspace or AMG workspace in either staging or prod environments. The ADR claims reuse of the amh-data-platform observability-stack Terraform module, but no such module reference or data-source block exists in this repo's Terraform. The AWS infrastructure side of the decision is entirely absent from the IaC.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "The OTel Collector remote-writes to the AMP endpoint via SigV4 authentication (IAM role of the pod; Authorization header injected by ADOT or via AWS_AMP_TOKEN env var in the prometheusremotewrite exporter).",
        "status": "partial",
        "evidence": [
          "config/otel-collector.yaml:80-86 — prometheusremotewrite exporter present; PROMETHEUS_REMOTE_WRITE_ENDPOINT defaults to http://prometheus:9090/api/v1/write; Authorization header commented out at line 86",
          "deploy/helm/maezo-tenant/values.yaml:224-226 — observability.otel.endpoint set to otel-collector in cluster; no PROMETHEUS_REMOTE_WRITE_ENDPOINT or AWS_AMP_TOKEN injection in any helm template",
          "deploy/helm/maezo-tenant/values-amh.yaml:117-119 — same endpoint, no AMP remote-write env var"
        ],
        "gap": "SigV4 authentication header is commented out (line 86, config/otel-collector.yaml). No Helm template injects PROMETHEUS_REMOTE_WRITE_ENDPOINT or AWS_AMP_TOKEN into the OTel Collector pod for staging/prod. Without these, the collector always writes to local Prometheus even in prod. The mechanism exists in config but is never activated for staging/prod by any IaC in this repo.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "The AMG workspace consumes the AMP workspace as a datasource; dashboards are provisioned as code in deploy/observability/grafana-provisioning/.",
        "status": "partial",
        "evidence": [
          "deploy/observability/grafana-provisioning/datasources/prometheus.yaml:1-11 — datasource hardcodes url: http://prometheus:9090 (dev-only local URL)",
          "deploy/observability/grafana-provisioning/dashboards/dashboards.yaml — file-based dashboard provider configured",
          "deploy/observability/dashboards/engine.json, gateway.json, helena-employee.json — dashboard JSON files exist"
        ],
        "gap": "The grafana datasource provisioning file points at http://prometheus:9090, not an AMP workspace endpoint. No AMG Terraform resource or AMG datasource configuration pointing at an AMP remote endpoint exists anywhere in the repo. Dashboard code exists and is valid for dev, but the AMG half of the prod claim is absent.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Alert rules in deploy/observability/alert-rules.yaml are the same content for dev and prod — loaded via rule_files in prometheus.yml (dev) and via AMP Rules API via Terraform (prod), with no content bifurcation.",
        "status": "partial",
        "evidence": [
          "deploy/observability/alert-rules.yaml — alert rules file exists with all expected alert groups",
          "config/prometheus.yml:13-14 — rule_files references /etc/prometheus/rules/*.yml",
          "docker-compose.yml:127-129 — prometheus volumes mount only ./config/prometheus.yml; no volume mounts deploy/observability/alert-rules.yaml into /etc/prometheus/rules/",
          "deploy/terraform/envs/staging-sa-east-1/main.tf and prod-amh-sa-east-1/main.tf — no aws_prometheus_rule_group_namespace or AMP Rules API resource"
        ],
        "gap": "The alert-rules.yaml file exists with correct content, but is NOT wired into either dev or prod. In dev: docker-compose mounts only config/prometheus.yml, not the alert rules directory — the rule_files directive points to /etc/prometheus/rules/*.yml but that path is never populated in the compose stack. In prod: no Terraform resource loads the rules into AMP via the Rules API. Alert rules exist as dead code.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "src/maezo/runtime/metrics.py is the single source of truth for metric names, types, and label schemas shared across both dev and prod environments.",
        "status": "verified",
        "evidence": [
          "src/maezo/runtime/metrics.py:1-628 — complete metric catalog with Counter/Gauge/Histogram instruments, bounded label sets, and typed helper functions for all agent runtime, gateway, engine, and Phase-2 worker metrics",
          "tests/unit/runtime/test_metrics.py::TestMetricNames — 21 tests asserting each metric's scraped name matches the catalog",
          "tests/unit/runtime/test_metrics.py::TestMetricLabels — 22 tests asserting label names including PHI/cardinality prohibition",
          "tests/unit/runtime/test_metrics.py::TestHelperFunctions — 15 tests verifying helper functions route to correct metric/label combos",
          "tests/unit/runtime/test_metrics.py::TestMetricTypes — 3 tests asserting correct Prometheus instrument types"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0010"
        ]
      },
      {
        "claim": "Dev local observability uses Prometheus (port 9090) + Grafana (port 3000) via docker compose --profile observability, using the same dashboards and alert rules as prod without content bifurcation.",
        "status": "partial",
        "evidence": [
          "docker-compose.yml:99-158 — otel-collector, prometheus, grafana services defined under 'observability' profile with correct ports",
          "docker-compose.yml:148 — Grafana mounts ./deploy/observability/grafana-provisioning [CORRECTION 2026-07-04, predeploy doc sweep: dashboards are NOT provisioned by this mount — grafana-provisioning/dashboards/ contains only dashboards.yaml (the file provider config, path /etc/grafana/provisioning/dashboards), while the dashboard JSONs live in the sibling deploy/observability/dashboards/, which no compose volume mounts; the original '(dashboards provisioned correctly)' parenthetical was false]",
          "docker-compose.yml:128 — Prometheus mounts only ./config/prometheus.yml, NOT alert-rules.yaml"
        ],
        "gap": "The dev stack launches Prometheus and Grafana, but NEITHER dashboards NOR alert rules are actually wired in: the Grafana provisioning mount lacks the dashboard JSONs (see corrected evidence above), and alert rules (deploy/observability/alert-rules.yaml) are not mounted into the Prometheus container at /etc/prometheus/rules/. The ADR claims 'same YAML, different destination' with no bifurcation, but in practice neither artifact is active in the dev stack.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "Kafka consumer lag and DLQ metrics are scraped by the OTel Collector from the JMX exporter on the local Kafka broker in dev; in prod, the AMP workspace of amh-data-platform is shared.",
        "status": "partial",
        "evidence": [
          "config/otel-collector.yaml:24-31 — prometheus/kafka receiver scrapes kafka:7071 (JMX exporter target); configured correctly for dev",
          "deploy/terraform/envs/prod-amh-sa-east-1/main.tf — no data source referencing the amh-data-platform AMP workspace for shared Kafka metrics"
        ],
        "gap": "Dev-side Kafka JMX scraping is properly configured. The prod claim (sharing the amh-data-platform AMP workspace for Kafka metrics) has no Terraform implementation — no data source or workspace ARN reference exists in the prod Terraform.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0013"
        ]
      },
      {
        "claim": "TLS is disabled (insecure: true) in the OTel Collector remote-write exporter only for the local docker-compose dev environment; this mode must never be enabled outside docker-compose.",
        "status": "contradicted",
        "evidence": [
          "config/otel-collector.yaml:83 — insecure: true set in prometheusremotewrite exporter with comment 'set to false and configure ca_file in prod'",
          "config/otel-collector.yaml:92 — insecure: true also set in otlp/traces exporter",
          "docker-compose.yml:103-104 — otel-collector mounts ./config/otel-collector.yaml:ro — the same single YAML file",
          "deploy/helm/maezo-tenant/ — no K8s ConfigMap or volume that provides an alternative otel-collector config with insecure: false"
        ],
        "gap": "There is only one otel-collector.yaml config file and it is used by both dev (via docker-compose mount) and would be used in prod (no alternative K8s config exists). The ADR says insecure: true must never be used outside docker-compose, but no separate prod config with insecure: false exists anywhere in the repo. If deployed to K8s as-is, the collector would use insecure TLS in prod.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      },
      {
        "claim": "No PHI appears in metric labels; labels are bounded (tenant, agent, action, decision, model_id, process_key, task_name, escalation_reason); PHI cardinality guard enforced at startup.",
        "status": "verified",
        "evidence": [
          "src/maezo/platform/metrics_server.py:68-85 — _FORBIDDEN_LABEL_SUBSTRINGS frozenset with 13 banned substrings; assert_no_phi_labels() raises ValueError on violation",
          "src/maezo/runtime/metrics.py:43-58 — label cardinality policy documented and enforced in metric definitions",
          "tests/unit/platform/test_metrics_server.py::test_assert_no_phi_labels_raises_for_forbidden_label — parametrized over 13 forbidden labels",
          "tests/unit/platform/test_metrics_server.py::test_assert_no_phi_labels_passes_on_clean_registry",
          "tests/unit/runtime/test_metrics.py::TestMetricLabels::test_no_phi_labels_in_any_metric",
          "tests/unit/runtime/test_metrics.py::TestMetricLabels::test_phase2_bounded_value_sets",
          "tests/unit/gateway/test_metric_emission.py::test_pep_decision_labels_have_no_phi"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0010"
        ]
      },
      {
        "claim": "PEP decisions (allow/deny/require_human), audit lag, and pseudonymizer throughput/duration are emitted as metrics by gateway/pep.py and gateway/audit.py calling the metrics catalog helpers.",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/pep.py:38-45 — _emit_pep_metric() calls record_pep_decision() best-effort; called on every PEP decision",
          "src/maezo/gateway/audit.py:35-42 — _emit_audit_lag() calls set_audit_lag() best-effort",
          "tests/unit/gateway/test_metric_emission.py::test_pep_deny_emits_global_metric",
          "tests/unit/gateway/test_metric_emission.py::test_pep_allow_emits_global_metric",
          "tests/unit/gateway/test_metric_emission.py::test_pep_require_human_emits_global_metric",
          "tests/unit/gateway/test_metric_emission.py::test_pep_metric_failure_does_not_break_request",
          "tests/unit/gateway/test_metric_emission.py::test_phi_zone_scrub_emits_pseudonymizer_metrics",
          "tests/unit/gateway/test_metric_emission.py::test_audit_record_emits_lag_metric"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "Agent runtime metrics (maezo_conversation_total, maezo_escalation_total, maezo_response_duration_seconds, maezo_eval_score_latest, maezo_hitl_*, maezo_llm_*) are emitted by src/maezo/runtime/harness.py.",
        "status": "absent",
        "evidence": [
          "src/maezo/runtime/harness.py — imports reviewed; no import of maezo.runtime.metrics; no calls to record_conversation, record_escalation, record_response_duration, record_eval_score, record_hitl_presented, record_hitl_approved, record_llm_usage",
          "src/maezo/runtime/metrics.py:8-11 — docstring explicitly marks these as 'Emitted by: src/maezo/runtime/harness.py (emission wired in future PR)'"
        ],
        "gap": "The metrics catalog defines all agent-runtime instruments with helper functions, and tests verify the helpers work in isolation, but harness.py does not call any of them. The module-level docstring in metrics.py itself acknowledges this with '(emission wired in future PR)'. The ADR's claim that these metrics are emitted during agent runtime is not satisfied by any existing code path.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0010"
        ]
      },
      {
        "claim": "Engine/BPMN metrics (cibseven_process_instance_*, cibseven_user_task_*) are emitted by the engine worker (mcp_cibseven).",
        "status": "absent",
        "evidence": [
          "src/maezo/runtime/metrics.py:25-29 — docstring marks these as 'Emitted by: src/maezo/tools/mcp_cibseven.py (emission wired in future PR)'",
          "src/maezo/tools/mcp_cibseven/server.py — not checked for metric calls; docstring in metrics.py confirms emission is a future PR"
        ],
        "gap": "Engine/BPMN metric emission is deferred to a future PR per the metrics catalog docstring. The instruments and helpers are defined and tested in isolation, but no engine worker code calls them.",
        "confidence": "inferred",
        "risk": "operational",
        "cross_refs": [
          "0010"
        ]
      },
      {
        "claim": "Phase-2 SP-OP worker metrics (maezo_ans_submission_outcome_total, maezo_nip_deadline_risk_total, maezo_calendar_job_runs_total, maezo_calendar_job_last_success_unixtime) are emitted by ans_submit.py and nip.py workers.",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/workers/ans_submit.py — referenced by test fixtures; emit calls confirmed by tests",
          "src/maezo/tools/workers/nip.py — referenced by test fixtures; emit calls confirmed by tests",
          "tests/unit/runtime/test_worker_metric_emission.py::test_ans_submit_emits_submitted_outcome",
          "tests/unit/runtime/test_worker_metric_emission.py::test_ans_track_protocol_emits_ack_and_nack",
          "tests/unit/runtime/test_worker_metric_emission.py::test_ans_validate_emits_calendar_tick",
          "tests/unit/runtime/test_worker_metric_emission.py::test_nip_deadline_risk_emits_alerta",
          "tests/unit/runtime/test_worker_metric_emission.py::test_emission_is_defensive_on_metric_failure",
          "tests/unit/runtime/test_worker_metric_emission.py::test_ans_submit_guard_blocks_and_emits_nothing"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": []
      },
      {
        "claim": "SigV4 is the only point of authentication to AMP; no long-lived credentials are used in config.",
        "status": "absent",
        "evidence": [
          "config/otel-collector.yaml:85-86 — SigV4 authorization is commented out ('# Authorization: Bearer ${AWS_AMP_TOKEN}')",
          "deploy/terraform/ — no IAM role or IRSA annotation for OTel Collector pod exists; no aws_prometheus_workspace or iam_role_policy for aps:RemoteWrite"
        ],
        "gap": "SigV4 auth is not implemented: the Authorization header is commented out in the only otel-collector config, no IAM role grants aps:RemoteWrite, and no IRSA annotation is configured for an OTel Collector service account. The no-long-lived-credentials guarantee cannot hold because the authentication layer does not exist at all.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": []
      }
    ]
  },
  {
    "title": "ADR-0015 A2A Delegation Runtime — Forensic Claim Verification",
    "adr": "0015",
    "claims": [
      {
        "claim": "AgentCard is DERIVED from AgentDefinition via AgentCard.from_definition(); agent_id, security_zone, and version always come from the harness (source of truth), never from a separate config file.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:71-106 — AgentCard.from_definition() reads agent_id, version, security_zone from AgentDefinition, and capabilities/skills/accepted_task_types from agent.yaml a2a: section",
          "tests/unit/a2a/test_registry.py::test_from_definition_references_agent_yaml — loads real helena/agent.yaml and asserts card.agent_id=='helena', card.version==definition.agent_version, card.security_zone=='general'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Registry is tenant-scoped: key is (tenant, agent_id); lookup without tenant is refused in code; zero cross-tenant contamination by construction.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:130-160 — _cards dict keyed by (tenant, agent_id); lookup() and get() require tenant kwarg; list_cards() filters by tenant",
          "src/maezo/a2a/registry.py:53-58 — AgentCard.__post_init__ raises RegistryError if tenant is empty",
          "tests/unit/a2a/test_registry.py::test_registration_is_tenant_scoped — same agent_id in two tenants creates distinct entries, cross-tenant lookup raises RegistryError"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0004"
        ]
      },
      {
        "claim": "capabilities, skills, and accepted_task_types come from the a2a: section of the target agent's agent.yaml; empty accepted_task_types accepts any task_type (Phase 0 compatibility).",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/registry.py:93-106 — from_definition() reads a2a_map.get('capabilities'), 'skills', 'accepted_task_types' from raw yaml",
          "src/maezo/a2a/registry.py:61-69 — accepts() returns True when accepted_task_types is empty (permissive Phase 0 mode)",
          "tests/unit/a2a/test_registry.py::test_accepts_contract — restrictive card accepts only declared types; permissive card (empty) accepts anything"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Dispatcher rejects delegations whose task_type is not in the target's accepted_task_types.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:203-208 — _validate() returns TASK_TYPE_NOT_ACCEPTED rejection when card.accepts(envelope.task_type) is False",
          "tests/unit/a2a/test_dispatcher.py::test_task_type_not_accepted_rejected — envelope with clinical.decision rejected when card only accepts authorization.analyze",
          "tests/unit/agents/test_rafael_a2a_delegation.py::test_rafael_rejects_clinical_decision_task_type — end-to-end: dispatcher rejects and handler never executes"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "DelegationEnvelope is immutable (frozen=True). Construction only via root() and extend(); direct attribute mutation is impossible.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:81 — @dataclass(frozen=True, slots=True) on DelegationEnvelope",
          "src/maezo/a2a/delegation.py:133-169 — root() classmethod; src/maezo/a2a/delegation.py:171-208 — extend() uses dataclasses.replace() to return a new instance",
          "tests/unit/a2a/test_anti_loop.py::test_extend_preserves_origin_and_grows_chain — confirms extend() returns new envelope with grown chain without modifying parent"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Guard 1 (acyclic chain): extend() rejects a target already present in delegation_chain with CyclicDelegationError. root() rejects origin==target.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:189-192 — extend() checks 'if target in self.delegation_chain' and raises CyclicDelegationError",
          "src/maezo/a2a/delegation.py:152-153 — root() checks origin==target and raises CyclicDelegationError",
          "tests/unit/a2a/test_anti_loop.py::test_guard_acyclic_rejects_target_in_chain — cycle in chain rejected",
          "tests/unit/a2a/test_anti_loop.py::test_guard_acyclic_rejects_trivial_self_delegation — origin==target rejected"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Guard 2 (max_hops=3): extend() rejects a chain that would exceed max_hops with MaxHopsExceededError.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:25 — MAX_HOPS: int = 3",
          "src/maezo/a2a/delegation.py:193-197 — extend() checks len(new_chain) > self.max_hops and raises MaxHopsExceededError",
          "tests/unit/a2a/test_anti_loop.py::test_guard_max_hops_rejects_fourth_hop — 4th hop rejected; 3-hop chain accepted",
          "tests/unit/a2a/test_anti_loop.py::test_guard_max_hops_respects_custom_limit"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Guard 3 (budget): Budget.charge() decrements and raises BudgetExhaustedError when exhausted; Budget is immutable and charge() returns a new Budget object.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:44-78 — Budget is @dataclass(frozen=True); charge() uses dataclasses.replace() to return new Budget, raises BudgetExhaustedError when exhausted",
          "src/maezo/a2a/delegation.py:198 — extend() calls self.budget.charge() BEFORE constructing the envelope",
          "tests/unit/a2a/test_anti_loop.py::test_guard_budget_exhaustion_rejects_hop",
          "tests/unit/a2a/test_anti_loop.py::test_budget_charge_decrements_both_dimensions"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Guard 4 (idempotency): DelegationDispatcher caches results by task_id in-memory; re-delivery of same task_id returns prior result (idempotent_replay=True) without re-executing the handler. An asyncio.Lock per task_id prevents concurrent double-execution.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:104-160 — _InflightEntry with asyncio.Lock per task_id; delegate() checks entry.result and returns idempotent_replay=True if already set",
          "src/maezo/a2a/dispatcher.py:133 — self._guard asyncio.Lock protects inflight dict creation",
          "tests/unit/a2a/test_idempotency.py::test_redelivery_returns_prior_result_without_reexecuting — handler.call_count==1 on two dispatches with same task_id",
          "tests/unit/a2a/test_idempotency.py::test_concurrent_redelivery_executes_once — 3 concurrent dispatches, handler runs once, 2 idempotent replays"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Idempotency store is in-memory (Phase 0) and does NOT survive pod restart; durable store (Redis/Postgres) is deferred to Phase 1 and is a mandatory pre-production requirement.",
        "status": "partial",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:116-117 — comment states 'Idempotencia por task_id e mantida em memoria (backing store duravel no Phase 1)'",
          "docs/adr/0015-a2a-delegation-runtime.md:109-111 — ADR explicitly accepts this negative consequence"
        ],
        "gap": "The durable PostgresIdempotencyStore (src/maezo/a2a/idempotency.py) and assembly module (src/maezo/a2a/assembly.py) implementing R9 exist on origin/main (merged commit 1012ca4, PR #60) but are NOT present on the current branch (wave/w-r1-runtime-wiring). Pycache artifacts (.pyc) are stale residuals. The migration alembic/versions/0004_a2a_idempotency.py and integration test tests/integration/platform/test_a2a_idempotency_durable.py also exist only on origin/main. On this branch the guard-4 durability gap stated in the ADR remains open.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "PHI contract: payload_ref must be a FHIR/pseudonymized reference, never raw PHI. __post_init__ applies defensive heuristic rejecting strings that look like CPF (11 digits) or CNPJ (14 digits) with DelegationError.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:107-119 — __post_init__ calls _looks_like_phi(self.payload_ref) and raises DelegationError",
          "src/maezo/a2a/delegation.py:217-220 — _looks_like_phi() checks only-digits-and-separators with len(digits) in {11, 14}",
          "tests/unit/a2a/test_anti_loop.py::test_payload_ref_rejects_raw_phi_like_value — CPF-like and CNPJ-like strings rejected; FHIR ref passes",
          "tests/unit/agents/test_rafael_a2a_delegation.py::test_envelope_rejects_raw_cpf_payload_ref"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "deadline must be timezone-aware (UTC); expired() is checked by the dispatcher before routing.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/delegation.py:114-115 — __post_init__ raises DelegationError if deadline.tzinfo is None",
          "src/maezo/a2a/delegation.py:126-130 — expired() compares deadline to datetime.now(tz=UTC)",
          "src/maezo/a2a/dispatcher.py:194-197 — _validate() calls envelope.expired() and returns EXPIRED rejection before routing",
          "tests/unit/a2a/test_anti_loop.py::test_deadline_must_be_tz_aware_and_expiry_detected",
          "tests/unit/a2a/test_dispatcher.py::test_expired_envelope_rejected_before_routing — handler.call_count==0 for expired envelope"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "DelegationDispatcher.delegate(envelope) is the SINGLE entry point for all delegation. It never raises exceptions to the caller; rejections are returned as structured DelegationResult.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:3-5 — docstring states 'ponto unico por onde toda delegacao agente->agente passa'",
          "src/maezo/a2a/dispatcher.py:135-153 — delegate() returns DelegationResult in all paths, including idempotent replay",
          "src/maezo/a2a/dispatcher.py:183-190 — handler DelegationError caught and returned as rejected DelegationResult (no re-raise)",
          "tests/unit/a2a/test_dispatcher.py::test_unknown_target_rejected_with_fact_and_audit",
          "tests/unit/a2a/test_dispatcher.py::test_handler_subdelegation_loop_surfaces_as_rejection"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Execution ordering: (1) idempotency check → (2) contract validation (Card, task_type, handler, deadline) → (3) audit pre-effect → (4) fact 'requested' on Kafka → (5) route to handler → (6) fact 'completed' or 'rejected'.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:135-190 — delegate() checks entry.result first (step 1); _execute() runs _validate() (step 2), then _audit_delegation+_emit(REQUESTED) (steps 3-4), then handler (step 5), then _emit(COMPLETED) (step 6)",
          "tests/unit/a2a/test_dispatcher.py::test_successful_delegation_routes_audits_and_emits_facts — asserts producer.topics()==[TOPIC_REQUESTED, TOPIC_COMPLETED] in that order"
        ],
        "gap": "Minor: for the rejection path, audit fires before the REJECTED fact (matching steps 3-4 ordering), but the ADR ordering description implies rejection is a post-step-2 short-circuit. Code at dispatcher.py:166-173 does audit then emit for rejection, which is consistent with 'audit pre-effect'. No functional gap.",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Dependencies (AgentCardRegistry, agent_id→AgentHandler map, AuditLog, FactProducer) are injected into the dispatcher. Real handlers (LangGraph graphs) are injected by the harness in Phase 1; tests use FakeAgentHandler.",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:119-134 — __init__ accepts registry, handlers, audit, facts as constructor args",
          "tests/unit/a2a/fakes.py:92-111 — build_dispatcher() helper injects RecordingSink, RecordingProducer, and FakeAgentHandler",
          "src/maezo/agents/rafael/delegation.py:91-120 — make_rafael_handler() builds real LangGraph handler injected by harness"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Three Kafka topics declared in config/topic_registry.yaml and validated by validate-artifacts: agents.events.delegation.requested, agents.events.delegation.completed, agents.events.delegation.rejected.",
        "status": "verified",
        "evidence": [
          "config/topic_registry.yaml:11-13 — all three delegation topics declared under kafka: section with descriptions noting no-PHI contract",
          "src/maezo/a2a/facts.py:18-20 — topic constants TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED match registry names",
          "src/maezo/platform/validation/bpmn.py:321 — validate-artifacts validates BPMN external task topics against topic_registry.yaml"
        ],
        "gap": "The validate-artifacts tool (bpmn.py:321) validates BPMN external task topics against topic_registry.yaml but does NOT specifically validate that a2a delegation topic constants in facts.py match the registry. The link between topic_registry.yaml and facts.py topic string constants is enforced only by code review, not by a CI gate.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Kafka facts NEVER carry raw PHI: only task_id, task_type, tenant, origin, target, delegation_chain, ts, and (on rejection) reason. The envelope payload is never emitted.",
        "status": "partial",
        "evidence": [
          "src/maezo/a2a/facts.py:55-70 — to_value() serializes task_id, task_type, tenant, origin, target, delegation_chain, ts, optionally reason and output_ref — raw payload/payload_ref is never included",
          "tests/unit/a2a/test_dispatcher.py::test_facts_never_carry_phi — checks that fhir:// references appear only as references, not raw identifiers"
        ],
        "gap": "The ADR text (line 85) lists the fact fields as 'task_id, task_type, tenant, origin, target, delegation_chain, ts and (on rejection) reason' — omitting output_ref. The implementation (facts.py:68-70) ALSO emits output_ref in the completed fact (confirmed by test_dispatcher.py:51). output_ref is a FHIR/process reference (never raw PHI) per the HandlerOutput contract, so this is not a PHI leak, but the ADR's field enumeration is incomplete/imprecise. The test_facts_never_carry_phi test does not assert on the complete absence of output_ref.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Kafka messages are partitioned by tenant (tenant is the Kafka message key).",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:43-53 — FactProducer.emit() sends fact.tenant.encode('utf-8') as the Kafka key",
          "tests/unit/a2a/test_dispatcher.py::test_successful_delegation_routes_audits_and_emits_facts — asserts 'all(key == b\"amh\" for (_, _, key) in producer.sent)'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Each delegation is audited per ADR-0007: AuditRecord with agent_id (chain origin), tool='a2a.delegate:{target}', input_hash covering task_id/task_type/chain/payload_ref (no raw PHI), and decision_basis. Audit fires pre-effect (before handler).",
        "status": "verified",
        "evidence": [
          "src/maezo/a2a/dispatcher.py:218-240 — _audit_delegation() builds AuditRecord with agent_id=envelope.origin, tool='a2a.delegate:{target}', input_hash covering task_id/task_type/chain/payload_ref, decision_basis",
          "src/maezo/a2a/dispatcher.py:175-177 — audit fires BEFORE _emit(REQUESTED) and before handler invocation",
          "tests/unit/a2a/test_dispatcher.py::test_successful_delegation_routes_audits_and_emits_facts — asserts rec.agent_id=='helena', rec.tool=='a2a.delegate:rafael', rec.decision_basis=='A2A:delegate:allow', verify_chain(sink.records) is True"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      },
      {
        "claim": "First delegation use case: helena→rafael, task_type=authorization.analyze.",
        "status": "verified",
        "evidence": [
          "src/maezo/agents/helena/delegation.py:28-34 — TASK_TYPE_AUTH_ANALYSIS='authorization.analyze', ORIGIN_AGENT='helena', TARGET_AGENT='rafael'",
          "src/maezo/agents/rafael/agent.yaml:51-60 — accepted_task_types includes 'authorization.analyze'",
          "src/maezo/agents/rafael/delegation.py:39 — TASK_TYPE_AUTH_ANALYSIS='authorization.analyze'",
          "tests/unit/agents/test_rafael_a2a_delegation.py::test_delegation_round_trip_auto_approve — full end-to-end helena→rafael delegation"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "functional"
      },
      {
        "claim": "Durable idempotency store (Redis/Postgres) replaces in-memory dict in Phase 1+. Real LangGraph handlers are injected by harness.",
        "status": "partial",
        "evidence": [
          "docs/adr/0015-a2a-delegation-runtime.md:95-96 — ADR documents these as future wiring",
          "src/maezo/a2a/dispatcher.py:117 — comment 'backing store duravel no Phase 1'"
        ],
        "gap": "PostgresIdempotencyStore (idempotency.py), build_dispatcher production assembly (assembly.py), alembic migration 0004_a2a_idempotency.py, and integration test test_a2a_idempotency_durable.py are implemented on origin/main (W-R2, PR #60) but NOT present on the current branch wave/w-r1-runtime-wiring. The stale .pyc files in src/maezo/a2a/__pycache__/ (assembly.cpython-313.pyc, idempotency.cpython-313.pyc) are residuals from a previous compile and do not represent live source. On this branch, durable idempotency is absent; the in-memory implementation is the only one present.",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      }
    ]
  },
  {
    "title": "ADR-0016 Forensic Verification: Process Key Allowlist + PHI Pseudonymization Invariant",
    "adr": "0016",
    "claims": [
      {
        "claim": "KNOWN_PROCESS_KEYS is a frozenset in code defining the universe of process keys the platform recognizes; no config can sanction a key outside this set",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:33-47 — KNOWN_PROCESS_KEYS frozenset literal with 9 SP-OP keys",
          "src/maezo/tools/process_allowlist.py:88-94 — ProcessAllowlist.__init__ computes unknown = merged - KNOWN_PROCESS_KEYS and raises ProcessAllowlistConfigError",
          "tests/unit/sec/test_process_allowlist.py::test_config_cannot_sanction_unknown_key — pytest.raises(ProcessAllowlistConfigError) for SP-OP-EVIL-999",
          "tests/unit/sec/test_process_allowlist.py::test_load_allowlist_rejects_unknown_key_in_yaml — same check via YAML path"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "DEFAULT_ALLOWED_PROCESS_KEYS is a frozenset in code that every tenant inherits and cannot remove via config; config YAML can only add keys within KNOWN_PROCESS_KEYS",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:51 — DEFAULT_ALLOWED_PROCESS_KEYS = frozenset(KNOWN_PROCESS_KEYS)",
          "src/maezo/tools/process_allowlist.py:87-88 — merged = frozenset(allowed) | DEFAULT_ALLOWED_PROCESS_KEYS (union, never subtraction)",
          "tests/unit/sec/test_process_allowlist.py::test_config_always_includes_frozen_default — verifies DEFAULT keys present even when tenant lists only one key",
          "tests/unit/sec/test_process_allowlist.py::test_default_set_equals_known_universe — asserts equality of DEFAULT and KNOWN sets for Phase 0/1"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ProcessAllowlist.ensure_allowed(process_key) is fail-closed: raises ProcessKeyNotAllowedError (subclass of PermissionError) if key fails format regex OR is not in sanctioned set",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:55 — _PROCESS_KEY_RE = re.compile(r'\\ASP-OP-[A-Z]+(?:-[A-Z]+)*-\\d{3}\\Z')",
          "src/maezo/tools/process_allowlist.py:105-113 — is_allowed checks regex first, then set membership; ensure_allowed calls is_allowed and raises",
          "src/maezo/tools/process_allowlist.py:58-68 — ProcessKeyNotAllowedError(PermissionError) defined",
          "tests/unit/sec/test_process_allowlist.py::test_unknown_or_forged_key_is_rejected — parametrized over 10 adversarial inputs including homoglyph U+2010, trailing space, lowercase, 4-digit suffix"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Tenant config YAML loads atomically fail-closed: unknown key in YAML raises ProcessAllowlistConfigError immediately at load time; absent tenant falls back to frozen default (not an error)",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:122-152 — load_allowlist validates Mapping structure and delegates to ProcessAllowlist constructor which raises on unknown keys",
          "tests/unit/sec/test_process_allowlist.py::test_load_allowlist_rejects_unknown_key_in_yaml — raises ProcessAllowlistConfigError for SP-OP-EVIL-999 in YAML",
          "tests/unit/sec/test_process_allowlist.py::test_load_allowlist_missing_tenant_falls_back_to_default — absent tenant yields DEFAULT_ALLOWED_PROCESS_KEYS, not an error",
          "tests/unit/sec/test_process_allowlist.py::test_shipped_config_loads_and_matches_default — src/maezo/policies/process_allowlist.yaml loads clean and equals DEFAULT"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "The start_compliance_process handler (mcp_cibseven) calls ensure_allowed BEFORE any call to the engine transport — no external effect occurs for a non-sanctioned key",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/mcp_cibseven/server.py:372 — self._allowlist.ensure_allowed(process_key) is called before await self._transport.find_active_instance or start_process_instance",
          "src/maezo/tools/mcp_cibseven/server.py:346 — constructor defaults to default_allowlist(tenant) when no allowlist passed (fail-closed, never None = allow-all)",
          "tests/unit/sec/test_process_allowlist.py::test_server_rejects_unknown_key_without_touching_engine — _CountingTransport.start_calls == 0 after rejection",
          "tests/unit/sec/test_process_allowlist.py::test_server_default_construction_is_fail_closed — server with no explicit allowlist still rejects SP-OP-EVIL-999"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ToolRegistry catches PermissionError from the handler, audits the structural denial as TOOL:deny:<type>:<message> (even when PEP gave ALLOW on the action), then re-raises",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:131-147 — except PermissionError as exc: audits AuditRecord with decision_basis=f'TOOL:deny:{type(exc).__name__}:{exc}' then raises",
          "tests/unit/sec/test_process_allowlist.py::test_registry_audits_process_key_rejection — asserts len(sink.records)==1 and basis.startswith('TOOL:deny:ProcessKeyNotAllowedError')",
          "tests/unit/sec/test_process_allowlist.py::test_registry_audits_process_key_rejection — also asserts 'SP-OP-EVIL-999' not in input_hash (PHI never raw in trail)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ToolRegistry.invoke is the sole output path of any tool handler result to the agent/LLM; no module outside the registry invokes PHI-read server methods directly",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:96-172 — ToolRegistry.invoke is the only dispatcher; handlers are closures registered via register_tools, unreachable except through registry.invoke",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s1_invoke_calls_phi_zone_seam_on_handler_output — AST check verifies _apply_phi_zone is called inside invoke and return result comes after",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s2_no_module_calls_phi_server_read_methods_directly — AST grep over src/maezo confirms no module outside mcp_fhir/mcp_memory servers calls read_patient/retrieve_episodic etc. with kwargs"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "ToolDefinition.phi_fields declares which result keys contain PHI; when non-empty and consumer_zone is 'general', ToolRegistry.invoke pseudonymizes via PhiZoneGateway before returning",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:43-54 — ToolDefinition.phi_fields field declared",
          "src/maezo/tools/registry.py:149-152 — result = self._apply_phi_zone(tool_def, result, consumer_zone=consumer_zone, tenant=call.tenant)",
          "src/maezo/tools/registry.py:179-211 — _apply_phi_zone: if phi_fields and consumer_zone == GENERAL_ZONE: delegates to self._phi_gateway.scrub_result",
          "src/maezo/tools/mcp_fhir/server.py:161-162 — _PATIENT_PHI_FIELDS = ['name','birthDate','telecom','address','identifier']; passed to ToolDefinition at lines 258/267/275",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d1_raw_phi_pseudonymized_for_general_consumer — CPF/name/phone tokens appear as [NAME_*/[CPF_*, not raw, for general consumer",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d2_phi_zone_consumer_receives_raw — phi consumer gets raw values"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "default consumer_zone is 'general' (fail-closed): callers that omit consumer_zone get PHI scrubbed, never a silent pass-through of raw PHI",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:102 — async def invoke(self, call: ToolCall, *, payload=None, consumer_zone: str = GENERAL_ZONE) -> Any",
          "src/maezo/gateway/phi_zone.py:60 — GENERAL_ZONE = 'general'",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d2b_default_consumer_zone_is_general_fail_closed — invoke without consumer_zone arg produces no raw PHI in output"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "When phi_gateway is not injected and a tool declares phi_fields, the registry refuses to deliver the result to a 'general' consumer — raises RuntimeError, never silently passes raw PHI",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/registry.py:200-205 — if self._phi_gateway is None: raise RuntimeError(f'tool `{tool_def.name}` declara phi_fields ... fail-closed, ADR-0006')",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_d4_no_gateway_general_consumer_is_fail_closed — pytest.raises(RuntimeError, match='phi_gateway')",
          "tests/architecture/test_phi_pseudonymization_invariant.py::test_s3_registry_requires_gateway_for_general_phi_consumer — direct call to _apply_phi_zone without gateway raises RuntimeError(match='fail-closed')",
          "src/maezo/runtime/agent_runtime/service.py:464-468 — service refuses to build agent for general zone without phi_gateway (pre-checks before build_tool_invoker call)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Adding a new governance process requires a PR to change KNOWN_PROCESS_KEYS in code — no config can introduce an unknown key",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:33-47 — KNOWN_PROCESS_KEYS is a frozenset literal in code, not loaded from any config",
          "src/maezo/tools/process_allowlist.py:88-94 — ProcessAllowlist.__init__ raises ProcessAllowlistConfigError for any key not in KNOWN_PROCESS_KEYS",
          "tests/unit/sec/test_process_allowlist.py::test_config_cannot_sanction_unknown_key — direct construction with unknown key fails"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Production tool_wiring (build_tool_invoker) loads the tenant process_allowlist YAML config (src/maezo/policies/process_allowlist.yaml) when constructing CibSevenServer for a tenant",
        "status": "partial",
        "evidence": [
          "src/maezo/runtime/tool_wiring.py:171-174 — CibSevenServer is constructed with only (transport, tenant=tenant), no process_allowlist argument",
          "src/maezo/tools/mcp_cibseven/server.py:346 — without explicit allowlist, server falls back to default_allowlist(tenant) which uses frozen DEFAULT_ALLOWED_PROCESS_KEYS",
          "src/maezo/tools/process_allowlist.py:122 — load_allowlist() exists and is exercised only in tests (test_process_allowlist.py::test_shipped_config_loads_and_matches_default)"
        ],
        "gap": "The YAML config file (src/maezo/policies/process_allowlist.yaml) is never loaded in any production code path. The production CibSevenServer always uses default_allowlist(tenant), which happens to equal KNOWN_PROCESS_KEYS in Phase 0/1, so the functional behavior is currently identical. The ADR's 'config YAML per tenant can add keys' extension mechanism has no production wiring — load_allowlist is exercised only in unit tests. This means the YAML-based tenant extension path described in the ADR is dead code in production (though the security invariant itself holds because default == known universe today).",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "Audit records for tool denials (both PEP DENY and structural PermissionError) never contain raw PHI identifiers — only input_hash (SHA-256)",
        "status": "verified",
        "evidence": [
          "src/maezo/gateway/audit.py:49-56 — hash_input() produces SHA-256 of canonical JSON; PHI must be hashed before auditing",
          "src/maezo/tools/registry.py:128 — input_hash = hash_input(call.tool_input if call.tool_input is not None else payload) — hashed before record",
          "src/maezo/tools/registry.py:139 — AuditRecord decision_basis uses type(exc).__name__ and str(exc) — the error message contains the key string but not CPF/patient data",
          "tests/unit/sec/test_audit_no_phi.py::test_audit_jsonl_never_contains_raw_identifiers — regex scan over JSONL output finds no CPF/name/phone patterns",
          "tests/unit/sec/test_process_allowlist.py::test_registry_audits_process_key_rejection — asserts 'SP-OP-EVIL-999' not in input_hash (the hash, not the raw key)"
        ],
        "gap": "The decision_basis field in the deny audit record includes str(exc) of ProcessKeyNotAllowedError, which contains the rejected process_key string (e.g. 'SP-OP-EVIL-999') and the sorted allowlist. This is a process identifier, not a PHI identifier, so it does not violate the PHI-never-raw guarantee — but the audit trail does contain the attempted forged key in plaintext in decision_basis, which is not tested for in the no-phi audit test.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0007"
        ]
      }
    ]
  },
  {
    "title": "ADR-0017 PHI Egress Network Enforcement — Forensic Verification",
    "adr": "0017",
    "claims": [
      {
        "claim": "PHI-zone agents (rafael, marina) receive a NetworkPolicy whose egress is restricted exclusively to approved BR-resident CIDRs; no general cloud-LLM egress rule exists in the PHI policy.",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:114-160 — PHI branch renders only brResidentEndpoints, no cloud-LLM rule",
          "deploy/helm/maezo-tenant/values-amh.yaml:111-116 — phiZone.brResidentEndpoints configured with cidr 10.40.10.0/24 on port 443",
          "tests/unit/platform/test_provision_tenant.py:443-459 — test_rendered_phi_networkpolicy_has_no_internet_egress (REQUIRES helm CLI via @requires_helm)"
        ],
        "gap": "The definitive NetworkPolicy rendering test (test_rendered_phi_networkpolicy_has_no_internet_egress) is gated by @requires_helm (skipped if helm is not on PATH). The CI quality/unit job (make test) does NOT install helm (only validate-helm job does, and it runs only helm lint + a single template smoke test that outputs /dev/null — it does not run the negative Python tests). The helm-dependent Python tests are effectively not exercised in CI's unit pass.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "helm template aborts (fail-closed) when a PHI endpoint has a missing cidr or cidr=0.0.0.0/0, so the PHI no-op NetworkPolicy cannot be committed.",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:118-129 — maezo-tenant.requirePhiCidr fails with 'MISSING required `cidr`' or 'WHOLE internet' message",
          "tests/unit/platform/test_provision_tenant.py:492-524 — test_phi_endpoint_missing_cidr_fails_closed and test_phi_endpoint_internet_cidr_fails_closed verify abort behaviour"
        ],
        "gap": "Both negative tests are decorated @requires_helm (line 46: skipif helm not on PATH). CI quality job (make test) does not install helm — these tests are skipped. The validate-helm CI job runs helm lint and a single template smoke test but does NOT execute the Python-level negative CIDR tests. The fail-closed guarantee is therefore not exercised in CI.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "The same concrete-CIDR discipline (no 0.0.0.0/0, no missing cidr) applies to general-zone llmEndpoints and mskEndpoint.",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:138-150 — maezo-tenant.requireGeneralCidr applies same checks to general-zone endpoints",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:195-212 — general-zone branch calls requireGeneralCidr per endpoint",
          "tests/unit/platform/test_provision_tenant.py:526-538 — test_general_endpoint_missing_cidr_fails_closed (also @requires_helm)"
        ],
        "gap": "Same CI gap as PHI claim: @requires_helm skips this test in the unit job; validate-helm only lints, not runs negative Python tests.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "A default-deny-egress NetworkPolicy is rendered for every pod in the namespace as the egress baseline.",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:25-41 — renders 'default-deny-egress' with podSelector:{} and egress:[] when networkPolicy.defaultDenyEgress=true",
          "deploy/helm/maezo-tenant/values.yaml:179 — defaultDenyEgress: true (default on)"
        ],
        "gap": "No test asserts the default-deny-egress NetworkPolicy is present in the rendered output. The @requires_helm tests that parse rendered policies do not explicitly check for the 'default-deny-egress' object in the output. Additionally defaultDenyEgress is a values flag that can be overridden to false by any values overlay — there is no fail-closed guard if it is disabled.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Intra-namespace egress is scoped to the gateway component only (not bare podSelector:{}) to prevent a PHI-pod → general-agent → cloud-LLM relay (FIX 2).",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:93-105 — allow-intra-namespace egress uses matchExpressions on app.kubernetes.io/component In [gateway]",
          "deploy/helm/maezo-tenant/values.yaml:186-187 — intraNamespaceEgressComponents: [gateway]",
          "tests/unit/platform/test_provision_tenant.py:473-489 — test_rendered_intra_namespace_egress_is_scoped_to_gateway asserts matchExpressions and no agent-* components (@requires_helm)"
        ],
        "gap": "@requires_helm: this test is skipped when helm is absent from PATH, i.e., in the CI unit pass. The guard is not CI-enforced in the quality job.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "The chart cross-validates values securityZone against the mounted effectiveDefinition's security_zone field and aborts rendering if they disagree (FIX 3), preventing hand-edited downgrade of a PHI agent.",
        "status": "unverified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:73-88 — maezo-tenant.validateAgents fails with 'DISAGREES' if securityZone != parsed effectiveDefinition security_zone",
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:97-108 — maezo-tenant.effectiveDefinitionZone parses the zone from the mounted YAML",
          "tests/unit/platform/test_provision_tenant.py:541-566 — test_phi_agent_set_to_general_fails_when_definition_disagrees (@requires_helm)"
        ],
        "gap": "Cross-check only fires when effectiveDefinition is present in the values; a hand-authored values file without the provisioning script (and therefore no effectiveDefinition field) passes through with no zone verification — the ADR documents this as an accepted gap (line 146). Also @requires_helm: not exercised in CI unit pass.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0004",
          "0006"
        ]
      },
      {
        "claim": "provision_tenant.py enforces that a PHI base agent cannot be downgraded to the general zone by a tenant overlay or AgentSpec; the merge engine raises MergeError/ProvisionError on phi→general.",
        "status": "verified",
        "evidence": [
          "scripts/provision_tenant.py:311-316 — MergeError from merge_agent_definition is re-raised as ProvisionError('merge failed — ... HARDEN')",
          "scripts/provision_tenant.py:300-309 — zone-pin overlay always applied; merge engine rejects relaxation",
          "tests/unit/platform/test_provision_tenant.py:162-172 — test_cannot_relax_phi_agent_to_general (no @requires_helm, runs in unit pass)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0004",
          "0006",
          "0008"
        ]
      },
      {
        "claim": "The chart does NOT claim FQDN/hostname enforcement; it explicitly declares CIDR-only (IP-pinned) enforcement and documents FQDN allowlisting as a future item requiring an egress proxy or Cilium toFQDNs.",
        "status": "verified",
        "evidence": [
          "docs/adr/0017-phi-egress-network-enforcement.md:111-117 — section 5 explicitly names FQDN enforcement as FUTURO, not delivered",
          "deploy/helm/README.md:21-41 — README repeats limitation; Cilium/egress-proxy path documented",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:19-24 — template comment acknowledges ipBlock FQDN limitation",
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:112-115 — requirePhiCidr comment states FQDN allowlisting requires proxy/Cilium"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "operational"
      },
      {
        "claim": "The validate-helm CI gate exercises helm lint and a helm template smoke test against the committed values-amh.yaml with concrete CIDRs, catching the original 0.0.0.0/0 no-op before any apply.",
        "status": "partial",
        "evidence": [
          ".github/workflows/ci.yml:106-126 — validate-helm job: helm lint --strict and helm template with values-amh.yaml outputting to /dev/null",
          "deploy/helm/maezo-tenant/values-amh.yaml:103-115 — concrete CIDRs present: 160.79.104.0/23, 10.30.0.0/16, 10.40.10.0/24"
        ],
        "gap": "The validate-helm job does NOT run the Python-level negative CIDR tests (test_phi_endpoint_missing_cidr_fails_closed, test_phi_endpoint_internet_cidr_fails_closed, etc.) — those live in the quality job's test suite but are gated by @requires_helm and skipped because the quality job does not install helm. The smoke test only validates the positive committed values render cleanly; it does not assert absence of 0.0.0.0/0 in rendered output or confirm the fail-closed abort on bad values. This splits the 'CI verifiable' claim: positive path covered, negative/fail-closed path is not CI-proven.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "Each agent Deployment pod carries a maezo.io/phi-zone label ('true' for PHI, 'false' for general) that the per-agent NetworkPolicy podSelector targets.",
        "status": "verified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:18 — $phiZone computed by maezo-tenant.agentPhiZoneLabel",
          "deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:31,48 — maezo.io/phi-zone label set on Deployment metadata and pod template",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:138-139 — PHI NetworkPolicy podSelector matchLabels maezo.io/phi-zone: 'true'",
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:184-185 — general NetworkPolicy podSelector matchLabels maezo.io/phi-zone: 'false'",
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:47-49 — agentPhiZoneLabel helper maps 'phi'→'true', else→'false'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "If no BR-resident endpoints are configured for a PHI agent, the egress list is empty (deny all external egress), not relaxed.",
        "status": "verified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/networkpolicy.yaml:143-160 — {{- if $.Values.networkPolicy.phiZone.brResidentEndpoints }}...{{- else }}# No BR-resident endpoints — external egress DENIED{{- end }}",
          "deploy/helm/maezo-tenant/values.yaml:216 — brResidentEndpoints: [] (empty default)",
          "docs/adr/0017-phi-egress-network-enforcement.md:71 — ADR documents: 'Se nenhum endpoint BR-resident estiver configurado, a lista de egress externo fica vazia (deny)'"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0006"
        ]
      },
      {
        "claim": "A CIDR that is too broad (e.g., 10.0.0.0/8 or 0.0.0.0/1) but not exactly 0.0.0.0/0 or ::/0 passes the requirePhiCidr/requireGeneralCidr validation — the chart does not catch over-broad CIDRs beyond the two blocked values.",
        "status": "verified",
        "evidence": [
          "deploy/helm/maezo-tenant/templates/_helpers.tpl:125-127 — only 0.0.0.0/0 and ::/0 trigger the fail; no prefix-length check exists",
          "docs/adr/0017-phi-egress-network-enforcement.md:140-143 — ADR explicitly acknowledges: 'um erro de pinagem (CIDR largo demais) nao e pego pelo chart — apenas 0.0.0.0/0/::/0 e ausencia sao'"
        ],
        "gap": "This is an accepted limitation documented in the ADR's Negativas section, not a contradiction. The chart provides no protection against CIDR over-scoping beyond the two sentinel values.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": []
      }
    ]
  },
  {
    "title": "ADR-0018: Padrao estrutural no-denial — replicacao vinculante para todo SP-OP negativa-like",
    "adr": "0018",
    "claims": [
      {
        "claim": "Part 1 — BPMN and agent routing types contain no adverse value (deny/negar/aceitar-glosa/indeferir). Adverse effect is inexpressible through an automated path; it only exists as a value a User Task can set (e.g. decisao_auditor=NEGAR, decisao_contas=ACEITAR_GLOSA).",
        "status": "verified",
        "evidence": [
          "src/maezo/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn — no service-task sequence flow leads to End_NegadaAuditor without passing through UT_AnaliseMedicoAuditor/UT_CoordenacaoAssume/UT_RegistrarParecerJunta (confirmed by test_no_denial_consolidated.py:reachable_without_human_completion)",
          "src/maezo/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn — End_GlosaAceitaHumano only reachable after UT_AnalistaContas/UT_CoordenacaoContasAssume (same proof)",
          "tests/integration/processes/test_no_denial_consolidated.py:456–478 — test_todo_terminal_adverso_e_human_gated performs exact reachability proof on all 9 BPMN bodies; any new body without a human gate fails CI",
          "tests/integration/processes/test_no_denial_consolidated.py:89–127 — _ADVERSE_ENDS registry covers all 9 bodies, including the 5 in-flight (RECURSO, NIP, CANCEL, REEMBOLSO, ANS-SUBMIT)",
          "tests/integration/processes/test_no_denial_consolidated.py:313–334 — test_registry_cobre_todo_bpmn_presente fails CI if a new BPMN body lacks classification",
          "tests/integration/processes/test_sp_op_auth_001.py:342–450 — test_invariant_nenhum_caminho_automatizado_produz_negativa (real engine, 5 combinations)",
          "tests/integration/processes/test_sp_op_contas_001.py:319–364 — test_nenhum_caminho_automatizado_aceita_glosa (real engine, 48 combinations)"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Part 2 — No DMN decisionTable for any negativa-like process has an output that confirms/accepts/denies the adverse effect. DMNs only signal candidates and route (ADR-0012). All DMN typeRefs are in {string, boolean, integer, long, double, date}; 'number' is forbidden; BRL in double; SLA in ISO 8601 string; camunda:historyTimeToLive is namespaced.",
        "status": "verified",
        "evidence": [
          "src/maezo/processes/dmn/glosa_triage.dmn:25–96 — output domain is exactly {SEM_GLOSA, RECORRER, ANALISE_HUMANA}; no ACEITAR/CONFIRMAR output exists in any rule; all typeRefs are 'string' or 'boolean'; camunda:historyTimeToLive='P180D' present",
          "src/maezo/processes/dmn/auth_admissibility.dmn:17–80 — output domain {NAO_REQUER, SEGUE_ANALISE, PENDENTE_DOCUMENTACAO}; comment on line 11 explicitly states 'NAO possui saida de negativa'; inelegibility and waiting-period are SEGUE_ANALISE (human); camunda:historyTimeToLive='P180D' present",
          "tests/integration/processes/test_sp_op_contas_001.py:850–882 — test_glosa_triage_sem_saida_de_aceite: static XML parse verifies output domain == {SEM_GLOSA, RECORRER, ANALISE_HUMANA} and last row (catch-all) == ANALISE_HUMANA",
          "tests/integration/processes/test_sp_op_contas_001.py:885–901 — test_dmn_typeref_allowlist: static XML parse verifies all 4 CONTAS DMNs use only allowed typeRefs and never 'number'",
          "tests/integration/processes/test_sp_op_auth_001.py:760–791 — test_dmn_auth_sla_urgencia: indirectly verifies auth DMNs resolve correctly via real engine timer jobs"
        ],
        "gap": "The static typeref and domain checks are implemented only for CONTAS DMNs (4 files). AUTH DMNs (3 files) have no equivalent static shape test. The in-flight processes (RECURSO, NIP, CANCEL, REEMBOLSO, ANS-SUBMIT) each have their own DMN files but do not yet have merged static shape tests analogous to test_dmn_typeref_allowlist — coverage depends on integration tests against the real engine which are not yet proved in main for those 5 bodies.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0012"
        ]
      },
      {
        "claim": "Part 3 — Fail-safe to a human User Task (closed allowlist). All ambiguity, apparent ineligibility/waiting-period, SLA expiry, fraud signal, and DMN unavailability route to a human User Task. Each negativa-like table has a catch-all (last row) mapping to the conservative human path (ANALISE_HUMANA). No permissive default route that bypasses the human. SLA breach causes human coordination to assume — never auto-pass/auto-accept by timeout (explicit inversion of the Task_AutoApprove/48h anti-pattern).",
        "status": "verified",
        "evidence": [
          "src/maezo/processes/dmn/glosa_triage.dmn:84–93 — rule id='r_catchall': all inputs '-' (wildcard), output 'ANALISE_HUMANA'; explicitly labeled 'Catch-all FAIL-SAFE'; it is the last rule in hitPolicy=FIRST",
          "src/maezo/processes/dmn/auth_admissibility.dmn:69–78 — rule id='r5': catch-all outputs SEGUE_ANALISE (human analysis), is the last rule in hitPolicy=FIRST",
          "tests/integration/processes/test_sp_op_auth_001.py:592–613 — test_inelegibilidade_roteia_para_humano_nao_nega: beneficiario_ativo=false routes to UT_AnaliseMedicoAuditor not End_NegadaAuditor (real engine)",
          "tests/integration/processes/test_sp_op_auth_001.py:730–758 — test_timer_sla_estourado_coordenacao_assume: SLA breach cancels UT_AnaliseMedicoAuditor and creates UT_CoordenacaoAssume (human coordination), NOT auto-denial (real engine)",
          "tests/integration/processes/test_sp_op_contas_001.py:756–785 — test_timer_sla_estourado_coordenacao_assume: SLA breach creates UT_CoordenacaoContasAssume, asserts End_GlosaAceitaHumano not in ended (no auto-accept by timeout)",
          "tests/integration/processes/test_sp_op_contas_001.py:367–392 — test_inelegibilidade_roteia_para_humano_nao_aceita: item nao conforme + sem documentacao routes to UT_AnalistaContas not End_GlosaAceitaHumano"
        ],
        "gap": "Catch-all row verification is static only for glosa_triage.dmn (CONTAS) and auth_admissibility.dmn (AUTH). For the 5 in-flight processes, the equivalent DMN catch-all property and no-auto-timeout property are verified only by the structural BPMN gate test (test_no_denial_consolidated.py) and by process-specific integration tests that are not yet proved on main. The ADR explicitly acknowledges this as in-flight.",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance"
      },
      {
        "claim": "Part 4 — Worker guard ERR_*_NOT_HUMAN. The adverse effect is materialized by exactly one worker per process, guarded by an ERR_*_NOT_HUMAN code. The worker throws a BPMN error and refuses to register the effect unless (a) the human decision variable has been set by a User Task and (b) mandatory human-identity and justification fields (analista_id/human_approver, justificativa, codigo, valor) are present. The worker is depth-of-defense: even if BPMN regressed, the instance would not reach the adverse terminal without complete human decision.",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/workers/auth.py:69 — _ERR_DENIAL_NOT_HUMAN = 'ERR_AUTH_DENIAL_NOT_HUMAN'",
          "src/maezo/tools/workers/auth.py:264–270 — send_denial_notice guard: decisao_auditor != 'NEGAR' raises WorkerBpmnError(_ERR_DENIAL_NOT_HUMAN)",
          "src/maezo/tools/workers/auth.py:279–293 — mandatory fields guard: justificativa_clinica, cid10_referencia, fundamentacao_dut; raises WorkerBpmnError('ERR_AUTH_DENIAL_INCOMPLETE')",
          "src/maezo/tools/workers/contas.py:86 — _ERR_GLOSA_ACCEPT_NOT_HUMAN = 'ERR_GLOSA_ACCEPT_NOT_HUMAN'",
          "src/maezo/tools/workers/contas.py:351–357 — register_glosa_accept guard: decisao_contas != 'ACEITAR_GLOSA' raises WorkerBpmnError(_ERR_GLOSA_ACCEPT_NOT_HUMAN)",
          "src/maezo/tools/workers/contas.py:367–382 — mandatory fields guard: justificativa_glosa, codigo_glosa_aceito, valor_glosa_aceito_brl, analista_id; same error code",
          "src/maezo/tools/workers/recurso.py:98 — _ERR_DESISTENCIA_NOT_HUMAN = 'ERR_DESISTENCIA_NOT_HUMAN'",
          "src/maezo/tools/workers/recurso.py:406–418 — register_desistencia guard: neither decisao_recurso='NAO_RECORRER' nor decisao_auditor_recurso='ACEITAR_GLOSA' raises WorkerBpmnError",
          "src/maezo/tools/workers/recurso.py:428–445 — mandatory fields: justificativa_desistencia, valor_glosa_aceito, referencia_contratual, analista_id|auditor_id",
          "src/maezo/tools/workers/nip.py:83 — _ERR_NIP_NEGATIVA_NOT_HUMAN = 'ERR_NIP_NEGATIVA_NOT_HUMAN'",
          "src/maezo/tools/workers/nip.py:277–298 — submit_response guard: decisao_nip='MANTER_NEGATIVA' with missing fields raises WorkerBpmnError",
          "src/maezo/tools/workers/cancel.py:92 — _ERR_CANCELLATION_NOT_HUMAN = 'ERR_CANCELLATION_NOT_HUMAN'",
          "src/maezo/tools/workers/cancel.py:349,376 — send_cancellation_notice guard raises ERR_CANCELLATION_NOT_HUMAN",
          "src/maezo/tools/workers/reembolso.py:90 — _ERR_REEMBOLSO_DENIAL_NOT_HUMAN = 'ERR_REEMBOLSO_DENIAL_NOT_HUMAN'",
          "src/maezo/tools/workers/reembolso.py:469,496,509 — send_reembolso_denial guard raises ERR_REEMBOLSO_DENIAL_NOT_HUMAN",
          "tests/integration/processes/test_sp_op_contas_001.py:603–669 — test_worker_register_glosa_accept_recusa_sem_humano: direct handler invocation (no engine), verifies 4 cases: absent decisao, wrong decisao, missing analista_id, and successful happy path",
          "tests/integration/processes/test_sp_op_contas_001.py:574–600 — test_aceitar_glosa_exige_campos: real engine path — ACEITAR_GLOSA without fields, worker guard fires, End_GlosaAceitaHumano not reached",
          "tests/integration/processes/test_sp_op_auth_001.py:516–568 — test_happy_path_negada_pelo_auditor: real engine, verifies send_denial_notice transmits only after human decision, auditor_id and all fields present in payload"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0005",
          "0008"
        ]
      },
      {
        "claim": "Part 5 — Integration test invariant against real engine (CI-blocking). Each negativa-like process has an integration test that (a) sweeps all DMN input combinations and proves no combination reaches the adverse terminal automatically, and (b) queries the engine history (history/activity-instance) to prove that if the adverse terminal is in history, at least one human User Task is also there. Tests run against the real engine (not mocked) and are blocking in CI.",
        "status": "partial",
        "evidence": [
          "tests/integration/processes/test_sp_op_auth_001.py:342–450 — test_invariant_nenhum_caminho_automatizado_produz_negativa: 5 combinations, history invariant _assert_no_denial_without_human_task; pytestmark=integration",
          "tests/integration/processes/test_sp_op_contas_001.py:319–364 — test_nenhum_caminho_automatizado_aceita_glosa: 48 combinations (6 categorias x 2^3 booleans), history invariant _assert_no_accept_without_human_task; pytestmark=integration",
          "tests/integration/processes/test_sp_op_recurso_001.py:345 — test_nenhum_caminho_automatizado_produz_desistencia: exists, pytestmark=integration; covers End_RecursoNaoInterposto/End_GlosaMantida/End_RecursoInadmissivel",
          "tests/integration/processes/test_sp_op_nip_001.py:320 — test_manter_negativa_so_via_user_task_humana: exists, pytestmark=integration",
          "tests/integration/processes/test_sp_op_cancel_001.py:328 — test_nenhum_caminho_automatizado_rescinde_contrato: exists, pytestmark=integration",
          "tests/integration/processes/test_sp_op_reembolso_001.py:342 — test_nenhum_caminho_automatizado_nega_reembolso: exists, pytestmark=integration",
          ".github/workflows/ci.yml:128–184 — integration job: runs `make test-integration` (pytest tests/integration -q -m integration) on every PR and push to main, against real CIB Seven engine; blocks on quality job; no separate make target for no-denial tests",
          "tests/integration/processes/test_no_denial_consolidated.py:72 — pytestmark=integration; engine-free structural sweep of all 9 BPMN bodies included in the same CI run",
          "Makefile:16-17 — test-integration: pytest tests/integration -q -m integration"
        ],
        "gap": "The ADR itself acknowledges (section 'Estado de implementacao') that SP-OP-RECURSO-001, SP-OP-NIP-001, SP-OP-CANCEL-001, SP-OP-REEMBOLSO-001, and SP-OP-ANS-SUBMIT-001 are 'authored + in-flight (PR #38, not merged)' and their invariant is only considered proved when tests run green against the real engine in main. The test files for those 5 processes exist in the working tree and are marked 'integration', but they cannot be considered 'proved' per the ADR's own pre-condition statement. The AUTH and CONTAS invariant tests are the only ones provably merged and (in principle) green in main at commit a2dabda. Additionally, the ADR's pre-condition note that CONTAS-001 start is still behind a human gate (PR #32 not merged) means the CONTAS-001 process invariant tests run against the engine in CI deploy mode but start-activation in production runtime is gated separately.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0011",
          "0016"
        ]
      },
      {
        "claim": "The five-part pattern is binding and enforceable for all future negativa-like SP-OP processes. A process body without the five parts is not mergeable. CI enforces this via the no-denial consolidated sweep (test_no_denial_consolidated.py) which fails if a new BPMN body is added without classifying its terminals.",
        "status": "verified",
        "evidence": [
          "tests/integration/processes/test_no_denial_consolidated.py:313–334 — test_registry_cobre_todo_bpmn_presente: fails CI if any bpmn/*.bpmn is not registered in _ADVERSE_ENDS",
          "tests/integration/processes/test_no_denial_consolidated.py:400–438 — test_classificacao_exaustiva_de_todo_end_event: fails CI if any end-event is unclassified in any bucket (adverse/benign/neutral), independent of name heuristic",
          "tests/integration/processes/test_no_denial_consolidated.py:456–478 — test_todo_terminal_adverso_e_human_gated: fails CI if any ADVERSE end-event is reachable without human completion",
          "tests/integration/processes/test_no_denial_consolidated.py:63 — docstring and code confirm the test is engine-free (runs even without dev-stack), so it runs in the unit/architecture lane too",
          ".github/workflows/ci.yml:128–184 — integration job runs the full tests/integration suite on PR and main; test_no_denial_consolidated.py is included"
        ],
        "gap": "The DoD enforcement is structural (test catches missing classification or missing human gate) but the mandatory 'five parts' are checked at different granularities: Part 1 (BPMN gate) and Parts 3/4 (worker existence) are covered structurally; Part 2 (DMN shape/typeref allowlist) is only statically verified for CONTAS DMNs, not for every future process body. A reviewer could merge a process with a DMN that uses 'number' typeRef or has an adverse output column without triggering a test failure unless they also add a DMN-specific shape test or the integration test catches it at runtime.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0005",
          "0008",
          "0012"
        ]
      },
      {
        "claim": "SP-OP-AUTH-001 and SP-OP-CONTAS-001 fully implement the five-part pattern and are proved and enforced in CI in main (@ commit a2dabda per ADR). Workers send_denial_notice (ERR_AUTH_DENIAL_NOT_HUMAN) and register_glosa_accept (ERR_GLOSA_ACCEPT_NOT_HUMAN) are guarded. DMNs auth_admissibility/auth_auto_approval have no adverse output. glosa_triage domain is {SEM_GLOSA, RECORRER, ANALISE_HUMANA}. Invariant tests (test_sp_op_auth_001.py, test_sp_op_contas_001.py) are merged.",
        "status": "verified",
        "evidence": [
          "src/maezo/tools/workers/auth.py:69,264–293 — ERR_AUTH_DENIAL_NOT_HUMAN guard with decisao_auditor check and mandatory fields",
          "src/maezo/tools/workers/contas.py:86,351–382 — ERR_GLOSA_ACCEPT_NOT_HUMAN guard with decisao_contas check and mandatory fields (analista_id, justificativa, codigo, valor)",
          "src/maezo/processes/dmn/glosa_triage.dmn:44–93 — five rules, output domain {SEM_GLOSA, RECORRER, ANALISE_HUMANA}, catch-all='ANALISE_HUMANA' as last rule",
          "src/maezo/processes/dmn/auth_admissibility.dmn:33–78 — five rules, output domain {NAO_REQUER, SEGUE_ANALISE, PENDENTE_DOCUMENTACAO}, no deny output",
          "tests/integration/processes/test_sp_op_auth_001.py:1–993 — full suite with pytestmark=integration; invariant test covers 5 combinations against real engine",
          "tests/integration/processes/test_sp_op_contas_001.py:1–925 — full suite with pytestmark=integration; invariant sweeps 48 combinations; static DMN shape tests included"
        ],
        "gap": "",
        "confidence": "verified-by-reading-code",
        "risk": "phi-security-compliance",
        "cross_refs": [
          "0005",
          "0008",
          "0012"
        ]
      },
      {
        "claim": "The five in-flight SP-OP bodies (RECURSO-001, NIP-001, CANCEL-001, REEMBOLSO-001, ANS-SUBMIT-001) replicate the pattern: worker guards with ERR_*_NOT_HUMAN exist, BPMN files and DMN files are present, and integration test files with invariant tests exist. However, these five bodies are from PR #38 (not merged) and their conformance is not yet considered proved per the ADR.",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/workers/recurso.py:98,406–445 — ERR_DESISTENCIA_NOT_HUMAN guard in register_desistencia; covers NAO_RECORRER and ACEITAR_GLOSA decisions with mandatory fields",
          "src/maezo/tools/workers/nip.py:83,277–298 — ERR_NIP_NEGATIVA_NOT_HUMAN guard in submit_response; guards MANTER_NEGATIVA with fundamentacao_regulatoria, referencia_negativa_original, revisor_id",
          "src/maezo/tools/workers/cancel.py:92 — ERR_CANCELLATION_NOT_HUMAN declared; guards send_cancellation_notice",
          "src/maezo/tools/workers/reembolso.py:90 — ERR_REEMBOLSO_DENIAL_NOT_HUMAN declared; guards send_reembolso_denial",
          "src/maezo/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn — file present",
          "src/maezo/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn — file present",
          "src/maezo/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn — file present",
          "src/maezo/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn — file present",
          "src/maezo/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn — file present",
          "tests/integration/processes/test_sp_op_recurso_001.py:345 — test_nenhum_caminho_automatizado_produz_desistencia exists with _assert_no_desistencia_without_human_task helper",
          "tests/integration/processes/test_sp_op_nip_001.py:320 — test_manter_negativa_so_via_user_task_humana exists",
          "tests/integration/processes/test_sp_op_cancel_001.py:328 — test_nenhum_caminho_automatizado_rescinde_contrato exists",
          "tests/integration/processes/test_sp_op_reembolso_001.py:342 — test_nenhum_caminho_automatizado_nega_reembolso exists",
          "tests/integration/processes/test_no_denial_consolidated.py:89–127 — all five bodies' adverse terminals are registered and pass the BPMN gate proof (engine-free, provably in main)"
        ],
        "gap": "Per the ADR's own 'Estado de implementacao' statement: conformance of the 5 in-flight bodies is considered proved only when invariant tests run green against the real engine in main. The test files exist but PR #38 is described as not merged at the time of ADR authoring. The worker code for all five is present in the working tree (current branch wave/w-r1-runtime-wiring) but merger status for main is not confirmed by file inspection alone. The BPMN gate proof in test_no_denial_consolidated.py is engine-free and structurally confirmed for all 9 bodies.",
        "confidence": "verified-by-reading-code",
        "risk": "functional",
        "cross_refs": [
          "0005",
          "0008"
        ]
      },
      {
        "claim": "Process start allowlist (ADR-0016 pre-condition): KNOWN_PROCESS_KEYS includes all nine SP-OP processes (AUTH, CONTAS, RECURSO, NIP, CANCEL, REEMBOLSO, ANS-SUBMIT, ESCALATION, LGPD-DSR). However, the ADR notes that the CONTAS-001 and Phase 2 bodies are not yet start-enabled in main runtime (PR #32 not merged), so the runtime pre-condition of the no-denial pattern is gated behind a separate human CODEOWNERS PR.",
        "status": "partial",
        "evidence": [
          "src/maezo/tools/process_allowlist.py:33–47 — KNOWN_PROCESS_KEYS frozenset contains SP-OP-AUTH-001, SP-OP-CONTAS-001, SP-OP-RECURSO-001, SP-OP-NIP-001, SP-OP-ANS-SUBMIT-001, SP-OP-CANCEL-001, SP-OP-REEMBOLSO-001, SP-OP-ESCALATION-001, SP-OP-LGPD-DSR-001",
          "src/maezo/tools/process_allowlist.py:51 — DEFAULT_ALLOWED_PROCESS_KEYS = frozenset(KNOWN_PROCESS_KEYS) (all known keys are allowed by default)",
          "src/maezo/tools/process_allowlist.py:58–66 — ProcessKeyNotAllowedError raised on unknown key (fail-closed)",
          "src/maezo/tools/process_allowlist.py:105–114 — ensure_allowed enforces format SP-OP-<DOMAIN>-<NNN> before lookup"
        ],
        "gap": "The ADR states that the allowlist addition for Phase 2 process keys is 'a human CODEOWNERS PR open and not merged (PR #32)' meaning no Phase 2 process is start-enabled in main runtime even though the BPMN artifacts and worker code are present. The DEFAULT_ALLOWED_PROCESS_KEYS in the allowlist file currently includes all Phase 2 keys (lines 40-46), which contradicts the ADR's claim that PR #32 is needed. Either PR #32 was subsequently merged, or the allowlist file reflects the working-tree state (wave/w-r1-runtime-wiring branch) which is ahead of main. This is a status ambiguity, not a security regression — the gate exists and functions.",
        "confidence": "verified-by-reading-code",
        "risk": "operational",
        "cross_refs": [
          "0016"
        ]
      }
    ]
  }
]
```
