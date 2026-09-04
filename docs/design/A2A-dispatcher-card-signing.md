# A2A Dispatcher + Agent-Card Signing — Recon & Ratification-Ready Design (M5/P3)

> **Promotion header (added 2026-07-25).** This document promotes the R1 recon-agent's design
> (originally written to an ephemeral scratchpad) into the tracked repo. The orchestrator
> **ratified the W1→W4 build-wave sequence (§5) on 2026-07-25**: W1 (this branch,
> `t2.4-a2a-w1-card-signing` — the card-signing slice) is cleared for autonomous build; W2/W3
> remain future waves gated on W1 landing; W4's T-F half rides W2 and its T-G signed-Card half
> rides W1, but W4's T-G **cert/service-account half stays ADR-gated** (see below).
>
> **Policy questions deferred to their own ADRs (do not build against these until ratified):**
> - **T-G service-account/cert identity** (§4 row 1 / §7.1) — issuance mechanism, per-tenant/
>   per-agent-version granularity, rotation policy, and verification chokepoint are all
>   **DESIGN-GAP**. This needs a **new ADR** before any cert-issuance code is written. Card-signing
>   (this branch) delivers only the *signed-Agent-Card* half of T-G, not the cert half.
> - **Card-signing key management** (§4 row 2) is NOT ADR-gated — it follows the existing
>   `gateway.pseudonymizer` injected-key precedent. Only the **real** vault/KMS key population is
>   external/blocked (§6); the injection seam itself (`card_signer_from_key`) is buildable now and
>   is part of this branch.
> - **Card `version` semantics** (§4 row 3) is a small, load-bearing decision made *inside* W1
>   (not a new ADR) — see the implementation note in `src/maezo/a2a/card.py` for the exact choice
>   and rationale.
> - **ADR-0015 refresh** (§4 row 4) and **ADR-0022/ToolRegistry decoupling** (§4 row 5) are
>   housekeeping / explicitly out-of-scope-confirmations, not blockers.
>
> The rest of this document is the R1 agent's recon output, preserved verbatim below (only this
> header block was added; no other text was altered). It is the ratified reference design for the
> W1 build landing on this branch — re-verifiable donor citations at
> `/Users/familia/code/Maezo-Healthcare-Plan/src/maezo/a2a/` and v2 citations at `main` @ `f0b7164`.

---

**Agent:** R1 RECON+DESIGN · **Date:** 2026-07-25 · **Basis:** fresh clone of `main` @ `f0b7164` +
donor repo `Maezo-Healthcare-Plan` (VERIFIED accessible, read-only) + ADR-0032/0015/0007/0003/0022 +
`docs/design/audit-emit-path-wiring.md` + `docs/reports/T2.4-a2a-agent-card-signing-gap.md`.

Unblocks **T-F** (A2A delegation audit) and **T-G** (signed identity). NO commits — design only.

**Verify-vs-reconstruct posture:** the donor implementation is FULLY VISIBLE on this machine
(`/Users/familia/code/Maezo-Healthcare-Plan/src/maezo/a2a/`). Every donor claim below is cited to a
donor file:line and was read directly — nothing is reconstructed from memory. v2 claims cite the
fresh clone.

---

## 1. Component inventory — HAVE (v2) vs NEED (port/build)

### 1.1 What v2 has today (4 inert files, ZERO production call sites — ADR-0032)

| v2 symbol | File:line | Shape | Gap vs a real dispatcher |
|---|---|---|---|
| `AgentCard` | `src/maezo/a2a/card.py:15-68` | **Plain, MUTABLE class**. Fields: `agent_id`, `capabilities: list[str]`, `endpoint`, `public_key: str`. `to_dict`/`from_dict`. `from_definition()` named in docstring (`card.py:6-7`) but **NOT implemented**. | Not frozen; NO `version`/`tenant`/`security_zone`/`skills`/`accepted_task_types`/`federation_layer`/`signature`; NO `signing_payload()`; `public_key` is stored-but-unused (no sign/verify). |
| `A2ARegistry` | `src/maezo/a2a/registry.py:21-105` | Dict `(tenant, agent_id) -> AgentCard`. `register(tenant, card)` takes tenant as a **separate arg**. `lookup`/`list_capabilities`/`list_agents`. | **NO `verifier` gate** — nothing to gate (no card carries a signature). Tenant is a call arg, not baked into the card. |
| `AntiLoopGuard` | `src/maezo/a2a/anti_loop.py:25-63` | `validate_chain(chain)`: max_depth (default 3) checked BEFORE cycle (`anti_loop.py:54-63`). Invariant: acyclic + depth ≤ max_depth. Raises `MaxDepthExceededError`/`CyclicDelegationError`. | Standalone validator over a `list[str]`; **not tied to any envelope**. Donor folds these guards INTO the envelope (see §1.3). Redundant once the envelope lands. |

Missing entirely from v2: `dispatcher.py`, `delegation.py`, `facts.py`, `idempotency.py`,
`assembly.py`, `CardSigner`, `CardSignatureError` (confirmed ADR-0032:27-32; T2.4 report §"What v2
has today").

### 1.2 What the donor has (VERIFIED — target shape to port)

Donor `src/maezo/a2a/` = 7 files (vs v2's 4). Public surface from `__init__.py:1-95`:

| Donor component | File:line | Verified shape |
|---|---|---|
| `AgentCard` (frozen) | `registry.py:44-166` | `@dataclass(frozen=True, slots=True)`. Fields: `agent_id, version, tenant, security_zone, capabilities: frozenset, skills: frozenset, accepted_task_types: frozenset, federation_layer="L3", endpoint, queue_ref, signature: str\|None=None`. `__post_init__` validates agent_id/tenant/federation_layer. `is_signed` prop, `accepts(task_type)`, `signed_copy(sig)`, `from_definition(...)`. |
| `signing_payload()` | `registry.py:85-108` | Canonical bytes: `json.dumps(payload, sort_keys=True, separators=(",",":"))` over every field EXCEPT `signature`, with sets `sorted()` (order-independent) + a `"scheme": "v1"` tag. Mirrors the audit-chain canonicalization. |
| `CardSigner` | `registry.py:183-236` | HMAC-SHA256 (std-lib `hmac`+`sha256`). `__init__` **fail-closed on empty key** (`CardSignatureError`, `:199-204`). `_digest` → `"v1:<hex>"`. `sign` (idempotent), `verify` (constant-time `hmac.compare_digest`, `signature is None`→False, unknown scheme→False fail-closed), `require_valid` (raise-on-invalid). |
| `CardSignatureError` / `RegistryError` | `registry.py:36-41` | `RegistryError(LookupError)`; `CardSignatureError(RegistryError)`. |
| `AgentCardRegistry` | `registry.py:239-291` | `__init__(*, verifier: CardSigner\|None=None)`. `register(card)` → if verifier injected, `require_valid` BEFORE admission (fail-closed chokepoint). `lookup(agent_id,*,tenant)`, `get`, `list_cards`, `__contains__`, `__len__`. Tenant baked into card. |
| `DelegationEnvelope` (frozen) | `delegation.py:81-208` | Fields incl. `task_id, task_type, origin, target, tenant, delegation_chain: tuple, budget: Budget, payload_ref, max_hops=3, deadline, payload_meta`. `root()` + `extend()` apply anti-loop **structurally**: Guard1 acyclic (`:189-192`), Guard2 max_hops (`:194-197`), Guard3 budget (`:198`). `_looks_like_phi` guard on payload_ref (`:116-119`, `:217-220`). |
| `Budget` (frozen) | `delegation.py:44-78` | `tokens, time_ms, cost_per_hop=1`. `charge()` → new Budget or `BudgetExhaustedError`. |
| Anti-loop errors | `delegation.py:28-41` | `DelegationError(ValueError)` → `CyclicDelegationError`, `MaxHopsExceededError`, `BudgetExhaustedError`. |
| `DelegationDispatcher` | `dispatcher.py:148-319` | `delegate(envelope) -> DelegationResult`. Order: idempotency(Guard4) → validate(expiry/registry-lookup/accepts/handler) → **audit BEFORE effect** → emit `requested` → route to handler → emit `completed`. Rejection = structured `DelegationResult` (never raises to caller). In-memory `_inflight` (asyncio.Lock) OR durable `IdempotencyStore`. |
| `DelegationResult` / `RejectionReason` / `HandlerOutput` / `AgentHandler` | `dispatcher.py:40-102` | `RejectionReason`: unknown_target/task_type_not_accepted/no_handler/expired/anti_loop. `HandlerOutput(output_ref, meta)`. `AgentHandler = Callable[[Envelope], Awaitable[HandlerOutput]]`. |
| `FactProducer` + topics | `dispatcher.py:43-54`, `facts.py:18-103` | Kafka fact emit, partitioned by tenant. Topics `agents.events.delegation.{requested,completed,rejected}`. `DelegationFact` PHI-free (`facts.py:36-70`): task_id/type/chain/reason/output_ref only. |
| `IdempotencyStore` / `PostgresIdempotencyStore` / `StoredResult` | `idempotency.py:84-238` | Protocol `claim_or_get`/`complete`. Postgres: `INSERT...ON CONFLICT DO NOTHING` under `pg_advisory_xact_lock`, `setup=` search_path (fix #55), poll-until-done. Terminal states pending/done. |
| `build_agent_cards` / `build_dispatcher` / `card_signer_from_key` / `RouterInferenceProvider` | `assembly.py:42-192` | Production assembly. `card_signer_from_key(key)` → `None` if no key (dev fail-safe) else `CardSigner` (`:140-151`) — the **vault/KMS injection seam**. `build_dispatcher(..., verifier=)` wires the registry gate (`:177-184`). |
| Per-agent handlers/originators | `agents/{rafael,helena,carolina,andre,marina,valentina,beatriz,fernando,gustavo,lucas}/delegation.py` | e.g. `rafael/delegation.py:91 make_rafael_handler` (target adapter envelope→graph→output_ref), `helena/delegation.py:53 build_auth_analysis_envelope` + `:105 delegate_auth_analysis` (originator). |
| Test suites | `tests/unit/a2a/{test_card_signing,test_registry,test_anti_loop,test_dispatcher,test_idempotency,test_idempotency_store,test_assembly}.py` + `fakes.py` | `test_card_signing.py` = **22 test functions** (one parametrized ×10 → ~31 cases; the "20-test" figure in T2.4/memory is approximate). |

### 1.3 Reconciliation notes (donor design ≠ v2 file layout)

- **Anti-loop lives in TWO places**: v2 has a standalone `AntiLoopGuard` (`anti_loop.py`, over a
  `list[str]`); the donor has **no standalone anti_loop module** — guards are folded into
  `DelegationEnvelope.root/extend` + `Budget` (`delegation.py`). Donor `test_anti_loop.py` imports
  from `maezo.a2a` (the envelope). **Decision needed**: port the envelope-baked guards (donor design,
  ADR-0015 §Envelope) and either deprecate `AntiLoopGuard` or keep it as a thin façade.
- **`CyclicDelegationError` name collision**: v2 `anti_loop.py:17` AND donor `delegation.py:32` both
  define it (v2 subclasses `ValueError`; donor subclasses `DelegationError(ValueError)`). The port
  must land ONE canonical definition (donor's, in `delegation.py`) and repoint `__init__.py`.

---

## 2. Dependency-surface friction (the real cost — donor imports ≠ v2 modules)

The donor files are **not drop-in**. Every donor import was checked against the v2 clone:

| Donor import | Donor file | v2 reality | Port action |
|---|---|---|---|
| `from maezo.gateway.audit import AuditLog, AuditRecord, hash_input` | `dispatcher.py:27` | v2 HAS `hash_input` (`audit.py:51`) + `AuditRecord` (`audit.py:133`) but **NO `AuditLog`** (v2 has `AuditSink` + `PostgresAuditSink`). | **Rewrite** the dispatcher's audit seam (see §3). |
| `AuditRecord(agent_id, agent_version, tenant, tool, input_hash, decision_basis, autonomy_level)` | `dispatcher.py:282-297` | v2 `AuditRecord` has a **DIFFERENT shape** (`audit.py:179-190`): `agent_id, tenant_id, agent_version, action, decision, details: dict, dmn_versions, model_id, prompt_version, prev_hash, record_hash`. No `tool`/`decision_basis`/`autonomy_level`. | **Rewrite** `_audit_delegation` to build v2's record (`tenant_id`, `action=f"a2a.delegate:{target}"`, `decision`, `details={...}`). |
| `self._audit.record(record)` (append) | `dispatcher.py:298` | v2 emit seam is `emit_once(record, *, dedup_key) -> str` (Protocol `AuditStartSink`, `transport.py:449-460`; satisfied by `PostgresAuditSink`). | **Rewrite** to `emit_once` with a per-delegation dedup key (see §3). |
| `from maezo.runtime.checkpoint import normalize_conn_string, schema_for_tenant` | `idempotency.py:37` | v2 has `schema_for_tenant` in **`gateway.audit_postgres:85`** and `normalize_dsn` (`:100`, NOT `normalize_conn_string`). `runtime.checkpoint` only has `Checkpointer`. | **Repoint** import to `gateway.audit_postgres`; rename `normalize_conn_string`→`normalize_dsn`. Mechanical. |
| `from maezo.runtime.harness import load_agent_definition_merged, AgentDefinition` | `assembly.py:27`, `registry.py:24` | v2 has **no `load_agent_definition_merged`**; agent defs load via `AgentLoader().load_by_id()` (`agents/__init__.py:180`) from **`spec/agents/<id>/agent.yaml`** (NOT `src/maezo/agents/`). v2 `AgentDefinition` (`agents/__init__.py:131`, Pydantic) has `id` (not `agent_id`), `security_zone`, `a2a` (already-parsed dict, `:272`), `prompt_versions` — **NO `agent_version`, NO `.raw`**. | **Rewrite** `from_definition`/`build_agent_cards` to v2's `AgentLoader`; source `a2a` from `definition.a2a`; DECIDE the card `version` (v2 has no `agent_version` on the definition — harness only sets a `f"{id}@v0"` default at graph-build, `harness.py:170`). |
| `from maezo.runtime.inference import InferenceRouter, InferenceRequest, Message, Role, SecurityZone, TaskKind` | `assembly.py:28-35` | v2 `runtime.inference` has a **DIFFERENT abstraction** (`InferenceProvider`/`BaseInferenceProvider`/`AnthropicInferenceProvider`, `inference.py:129-390`). **No `InferenceRouter`/`TaskKind`/`SecurityZone` enum/`resolve_spec`.** | `RouterInferenceProvider` (`assembly.py:42-92`) does NOT port — only needed for REAL agent handlers (W3+). Drop from W1/W2; redesign against v2's provider seam in W3. |
| `import asyncpg` | `idempotency.py:35` | v2 already uses asyncpg in `audit_postgres.py`. | OK — align pool/`setup=` pattern with v2's `PostgresAuditSink`. |
| agent.yaml `a2a:` data | `assembly.build_agent_cards` | **All 10 v2 agents + `_template` already carry an `a2a:` block** (verified). e.g. `spec/agents/rafael/agent.yaml`: `capabilities:[prior_authorization_analysis]`, `skills:[tiss_auth_analysis, medico_auditor_dossier]`, `accepted_task_types:[authorization.analyze]`, `queue_ref:agents.tasks.rafael`. | **No data gap** — card derivation has real inputs today. |

**Bottom line:** `delegation.py` + `facts.py` port ~clean (std-lib + self-contained). `registry.py`
(card+signer+registry) ports clean EXCEPT `from_definition`. `dispatcher.py` needs an **audit-seam
rewrite** (biggest single friction). `idempotency.py` needs a mechanical import repoint. `assembly.py`
needs `from_definition`/`build_agent_cards` reworked to `AgentLoader`+`spec/agents`, and
`RouterInferenceProvider` deferred.

---

## 3. Integration surface

### 3.1 Where the dispatcher plugs in (intended delegation edges — from v2 graph disclosures)

Every v2 agent graph discloses the missing edge (each independently — ADR-0032:38-42):

| Originator → Target | task_type | Disclosure (v2) | Donor proof edge |
|---|---|---|---|
| Helena → **Rafael** | `authorization.analyze` | `rafael/graph.py:48` | YES — `helena/delegation.py` + `rafael/delegation.py` (canonical, matches `spec/agents/rafael` `accepted_task_types`) |
| ? → **Carolina** | `operadora.cred.prepare_dossier` | `carolina/graph.py:122` | `carolina/delegation.py` |
| ? → **Andre** | payment-approval dossier (`pagto_dossier`) | `andre/graph.py:151,184` | `andre/delegation.py` |
| ? → **Marina** | `operadora.contas/recurso/reembolso.*` | `marina/graph.py:113` | `marina/delegation.py` |
| ? → **Valentina** | `care.stratify` / `care.enroll` | `valentina/graph.py:103` | `valentina/delegation.py` |
| ? → **Beatriz** | `fraude.investigate` (inbound handler) | `beatriz/graph.py:88` | `beatriz/delegation.py` |
| ? → **Fernando/Gustavo** | caller/A2A-supplied `intencao`/state | `fernando/graph.py:67`, `gustavo/graph.py:97` | `fernando`/`gustavo/delegation.py` |

**W3 proof edge = Helena→Rafael `authorization.analyze`** — the only edge with BOTH originator and
target adapters in the donor, and whose target `accepted_task_types` already matches v2's
`spec/agents/rafael/agent.yaml`.

### 3.2 Interaction with the FENCED process-start chokepoint (ADR-0007/T-C2)

`start_process_idempotent` (`tools/mcp_cibseven/transport.py:560`) is the SOLE agent→**process-start**
chokepoint: audit-before-effect via `emit_once` on `AuditStartSink` (`transport.py:449-460`), dedup
key `f"{tenant}:start:{process_key}:{business_key}"` (`:549-557`), PHI-safe provenance
(`AgentDecisionProvenance`, `:463-502`).

**A2A delegation is a DISTINCT effect surface** — agent→agent, not agent→process-start. The design
doc classifies it as **§2.1 site 5, "the second effect surface"** (`audit-emit-path-wiring.md:76`,
`:387-388`, `:627`). **T-F does NOT need a new chokepoint and does NOT ride the fence**: it reuses the
SAME `emit_once` idempotent audit seam, instrumenting the dispatcher's `_execute` path. This is a
clean fit because delegation is ALREADY `task_id`-idempotent (Guard 4) — the audit dedup_key is
naturally `f"{tenant}:a2a:{kind}:{task_id}"`, mirroring `start_dedup_key`. So the port's dispatcher
audit rewrite (§2) targets `emit_once`, not `AuditLog.record`.

### 3.3 Interaction with the audit identity (AUDIT_AGENT_ID → T-G)

- Today: `AUDIT_AGENT_ID = "operadora-worker"` (plain string, `tools/workers/harness.py:111`) is the
  audited identity for **worker completions**. The donor dispatcher audits delegation with
  `agent_id=envelope.origin` (`dispatcher.py:283`) — the chain ORIGINATOR, not AUDIT_AGENT_ID.
- **T-G** replaces "the interim stable-string identity" (`audit-emit-path-wiring.md:389-391`) with a
  **signed/cert-backed identity** (ADR-0007:10 "service account + certificado; Agent Card assinado").
- **Card-signing (W1) delivers the signed-Agent-Card half**: a verified `AgentCard` cryptographically
  binds `agent_id`+`version`+`tenant`+`security_zone` (`signing_payload`, `registry.py:95-108`). The
  audited `agent_id` becomes trustworthy exactly when the dispatcher only `lookup`s verified cards
  (registry `verifier=` gate). The **service-account/cert half is a separate ADR** (§4).

---

## 4. ADR / policy questions

| Question | Status | Needs |
|---|---|---|
| **T-G service-account/cert identity** (issuance: who mints? per-tenant? per-agent-version? rotation? verification chokepoint: at dispatch / audit-emit / PEP?) | **DESIGN-GAP** — ADR-0007:10 states it in ONE clause with zero elaboration (ADR-0032:67-73). | **NEW ADR** (issuance mechanism + verification chokepoint). Blocks the cert half of T-G. NOT autonomously buildable. |
| **Card-signing key management** (vault/KMS seam) | Seam EXISTS in donor (`card_signer_from_key`, `assembly.py:140-151`) — mirrors `gateway.pseudonymizer` injected-key contract. Key material = blocked external secret (§6.2). | **Follows existing precedent** (pseudonymizer). No new ADR for the seam; provisioning the real key = external/infra. |
| **Card `version` source** (ADR-0007 "sob-qual-versao" — donor uses the AgentDefinition hash) | v2's `AgentDefinition` has NO `agent_version`/`.raw` (`agents/__init__.py:131-165`); harness sets `f"{id}@v0"` default at graph-build (`harness.py:170`). | **Small design decision** inside W1 (compute a stable hash of the definition, or adopt the harness default). Precedent exists; no new ADR. |
| **ADR-0015 refresh** (its §Decisao describes the port as already-present) | ADR-0032 already corrected the STATUS claim; the design remains the valid target. | When the port lands, a follow-up ADR should flip ADR-0015's "implementado" framing to "implemented by PR #NNN". Housekeeping, not a blocker. |
| **ADR-0003** (Kafka facts + 4 anti-loop guards) | Accepted; the donor implements it faithfully. Topics need registering in `config/topic_registry.yaml` (validate-artifacts). | No new ADR; W2/W4 must add the 3 topics to the registry. |
| **ADR-0022 (register_tools/ToolRegistry)** | Accepted; RATIFIES `register_tools` as "load-bearing — intocavel" (`0022:60-61`). **ORTHOGONAL to A2A** — the dispatcher does not touch ToolRegistry/register_tools. | **SEPARATE from this program.** The memory note (ADR-0022 stale re: a real ToolRegistry) belongs to whichever task lands the real `ToolRegistry` (ADR-0016 PEP+audit+scrub boundary), NOT this A2A wave. Do not couple. |

---

## 5. Build-wave decomposition (multi-PR sequence)

Large program → 4 waves. Each wave is independently mergeable behind the zero-trust gates.

### W1 — Card-signing slice (self-contained; NO dispatcher needed)
- **Files (new):** `a2a/registry.py` rewrite → frozen `AgentCard` + `signing_payload` + `CardSigner`
  + `CardSignatureError` + `RegistryError` + `AgentCardRegistry(verifier=)`. Update `a2a/card.py`
  (fold into registry.py per donor, or keep a thin re-export) + `a2a/__init__.py`.
- **Depends on:** nothing new. Std-lib `hmac`/`hashlib` only. `from_definition` needs v2 `AgentLoader`
  (already present) + a `version` decision (§4).
- **Tests:** port `test_card_signing.py` (22 funcs/~31 cases) + `test_registry.py`. Adapt
  `build_agent_cards` tests to `spec/agents` real yaml (data already present).
- **Live-engine needs:** NONE (unit/engine-free — T2.4 report §"Tudo unit/engine-free").
- **Verification:** all 22 signing tests green; tamper/wrong-key/empty-key/unknown-scheme fail-closed;
  registry admits-only-signed under verifier, backward-compatible without.
- **Breaking-change note:** frozen AgentCard is a breaking change to the current `AgentCard.__init__`/
  `to_dict`/`from_dict` and `A2ARegistry.register(tenant, card)` signature (T2.4 report items 1,3) —
  but there are ZERO production call sites (ADR-0032), so blast radius = the 4 a2a files + their
  tests only.

### W2 — Envelope + dispatcher + facts + idempotency (the runtime)
- **Files (new):** `a2a/delegation.py` (envelope+Budget+guards), `a2a/facts.py` (facts+topics),
  `a2a/dispatcher.py` (**audit seam rewritten to `emit_once`** + v2 `AuditRecord` shape),
  `a2a/idempotency.py` (import repointed to `gateway.audit_postgres`), `a2a/assembly.py`
  (`build_agent_cards`/`build_dispatcher`/`card_signer_from_key` — MINUS `RouterInferenceProvider`).
  Reconcile `AntiLoopGuard`/`CyclicDelegationError` collision (§1.3). Register 3 topics in
  `config/topic_registry.yaml`.
- **Depends on:** W1 (registry+cards), `gateway.audit` (`AuditRecord`/`hash_input`/emit seam).
- **Tests:** port `test_dispatcher.py`, `test_idempotency.py`, `test_assembly.py`, `test_anti_loop.py`
  (envelope guards), `fakes.py` (adapt `RecordingSink.emit`→v2 `emit_once` fake).
- **Live-engine needs:** in-memory idempotency path = none. `test_idempotency_store.py`
  (`PostgresIdempotencyStore`) needs a **live Postgres** + an `a2a_idempotency` table migration
  (mirror `PostgresAuditSink` schema-per-tenant).
- **Verification:** dispatcher routes/rejects/replays; audit-before-effect ordering; `emit_once` dedup
  on re-delivery; anti-loop guards fire structurally; facts PHI-free.

### W3 — Wire ONE real edge end-to-end (Helena→Rafael, proof of life)
- **Files (new):** `agents/helena/delegation.py` (originator: build envelope + `delegate`),
  `agents/rafael/delegation.py` (target handler envelope→graph→output_ref). Redesign
  `RouterInferenceProvider` against v2's `InferenceProvider` seam (`inference.py:390`). Wire the
  dispatcher into the agent-runtime bring-up (`runtime/agent_runtime/service.py`).
- **Depends on:** W2 + W1. Rafael's graph (exists) + `spec/agents/rafael` a2a block (exists).
- **Tests:** port `test_rafael_a2a_delegation.py` + Helena originator test. Assert output_ref is a
  business-key REFERENCE, never a coverage decision (donor HARD GUARDRAIL, `rafael/delegation.py:19`).
- **Live-engine needs:** the handler runs Rafael's graph; end-to-end proof may want the live inference
  provider (or the PhiZoneMock for the PHI zone). No BPMN engine for the A2A hop itself.
- **Verification:** a real `authorization.analyze` delegation flows Helena→dispatcher→Rafael→output_ref
  with audit + facts emitted; anti-loop + idempotency observed on a live-ish path.

### W4 — T-F audit wiring + T-G identity binding
- **T-F (buildable-now):** confirm the dispatcher's `emit_once` call (from W2) is the site-5 effect
  surface; add the audited-refusal path on rejections; make the agent daemon's `audit_sink_ready`
  readiness cover A2A (fail-closed). Files: `dispatcher.py` (already emits), `service.py` readiness.
- **T-G signed-Card half (buildable-now on top of W1):** flip production assembly to inject
  `verifier=card_signer_from_key(key)` so the dispatcher only `lookup`s verified cards; bind the
  audited `agent_id` to the verified card identity.
- **T-G cert/service-account half (ADR-GATED):** BLOCKED on the new identity ADR (§4). Not in this
  wave's autonomous scope.
- **Tests:** delegation-audit assertions (dedup, PHI-free details, chain link); verifier-gated build.
- **Live-engine needs:** durable audit assertions want live Postgres (chain verify).

**Dependency graph:** W1 → W2 → W3 → W4. W1 is fully independent and shippable first (closes the T2.4
signing gap with no dispatcher). W4's T-F rides W2; W4's T-G signed-half rides W1; W4's cert-half waits
on an ADR.

---

## 6. Buildable-now vs ADR-gated vs external

| Bucket | Items |
|---|---|
| **Buildable-now (autonomous)** | W1 (entire card-signing slice — engine-free). W2 (runtime port + audit-seam rewrite + import repoints; in-memory paths engine-free). W3 (Helena→Rafael edge). W4 T-F (delegation audit via `emit_once`) + W4 T-G signed-Card half (verifier gate). The `version`-source and AntiLoopGuard-reconciliation micro-decisions (§1.3/§4). |
| **ADR-gated (ratify first)** | **T-G service-account/cert identity** — needs a NEW ADR (issuance mechanism + verification chokepoint) before build; ADR-0007:10 is a single unelaborated clause (ADR-0032:67-73). |
| **External / infra (SME)** | Real card-signing key + service-account cert **provisioning in vault/KMS** (blocked secret, §6.2). Live Postgres + `a2a_idempotency` table migration for durable-path tests (W2/W4). Real inference provider credentials for the live W3 proof. |

---

## 7. Open decisions to ratify (top policy questions)

1. **NEW ADR for T-G identity issuance/verification** — who mints the service-account cert, at what
   granularity (per-tenant? per-agent-version?), rotation policy, and WHERE it is verified (dispatch
   vs audit-emit vs PEP). Hard blocker for the cert half of T-G. (ADR-0032:67-73.)
2. **Card `version` semantics** — adopt a content-hash of the v2 `AgentDefinition` (ADR-0007
   "sob-qual-versao") vs the harness `f"{id}@v0"` default. Small but load-bearing (it is signed).
3. **Anti-loop consolidation** — deprecate the standalone `AntiLoopGuard` in favor of the
   envelope-baked guards (donor design), resolving the `CyclicDelegationError` name collision.
4. **Audit-seam contract for site 5** — ratify `emit_once` dedup_key = `f"{tenant}:a2a:{kind}:{task_id}"`
   and the v2 `AuditRecord` field mapping (`action=f"a2a.delegate:{target}"`, `decision`, PHI-safe
   `details`) as the T-F wiring shape.
5. **ADR-0022 / ToolRegistry is OUT of scope** — confirm it stays decoupled from this program (belongs
   to the real-ToolRegistry task, not the A2A dispatcher wave).

---

## 8. Path to the full design doc
This file IS the design doc. Next step for a build orchestrator: lift §5 waves into 4 PR charters,
attach the §2 friction table as the per-file port checklist, and open the T-G identity ADR (§7.1)
before W4's cert half. All donor citations are re-verifiable at
`/Users/familia/code/Maezo-Healthcare-Plan/src/maezo/a2a/` and v2 citations at `main` @ `f0b7164`.

---

## 9. W3 — Helena→Rafael edge (built 2026-07-25)

**Branch:** `t2.4-a2a-w3-helena-rafael-edge` off `t2.4-a2a-w2-dispatcher` @ `6750a96`. Basis for the
R1 design behind this build: an ephemeral recon note at
`scratchpad/a2a-w3-design.md` (R1 RECON+DESIGN, 2026-07-25) — this section promotes its load-bearing
findings into the tracked doc; the ephemeral file itself is not part of the repo.

### 9.1 Headline finding — the inference seam W2 deferred COLLAPSES (no router, no adapter)

W2's `assembly.py` deliberately dropped `RouterInferenceProvider` with a "needs redesign in W3"
note. The W3 recon found the opposite of a build task: **v2 needs no router and no adapter at all**.
`RafaelGraph` (`agents/rafael/graph.py:293-310`) was already written to consume v2's
`runtime.inference.InferenceProvider` FACADE directly — `await self._llm.generate(prompt, *,
phi=True)` (`graph.py:566`) — not the donor's graph-local `agents.<id>.contracts.InferenceProvider`
(`complete(task_kind, system, messages, *, zone, response_schema)`) that `RouterInferenceProvider`
existed to bridge. Both ends of that bridge are absent in v2; there is nothing to adapt. PHI
fail-closed routing is already native: `generate(phi=True)` raises `PhiZoneRoutingError` when the
active provider isn't `phi_capable` (`inference.py:484-491`) — the donor's `_PhiNotConfiguredProvider`
sentinel is redundant against it. **W3's rafael handler injects a v2 `InferenceProvider` straight
into `rafael.graph.build(config)`.** No `RouterInferenceProvider` was ported; none should be in any
future wave either — this line item is now closed, not deferred.

### 9.2 Scope actually built — in-process (Option A), test/driver-invoked, not a live daemon consumer

Per-file, additive/changed:

| File | Status | What it does |
|---|---|---|
| `agents/helena/delegation.py` | NEW/additive | Originator: `build_auth_analysis_envelope` + `delegate_auth_analysis`. Ports ~verbatim from the donor (`Maezo-Healthcare-Plan src/maezo/agents/helena/delegation.py`) — the donor's `from maezo.a2a import Budget, DelegationEnvelope` already resolves against W2's real public surface, so this is a straight port (English-translated docstrings/comments, PT-BR domain terms kept), not a rewrite. Helena's triage graph (`agents/helena/graph.py`) is untouched. |
| `agents/rafael/delegation.py` | NEW/additive | Target: `state_from_envelope` (routes through `graph.new_rafael_state`, the T1.11 input-boundary gate — NOT a raw cast) + `make_rafael_handler(inference, *, dmn, cibseven, audit_sink, fhir=None, agent_version=...)`, which compiles the REAL `rafael.graph.build(config)` graph and returns an `async handler(envelope) -> HandlerOutput`. REWRITE, not a port: the donor's handler imported a `contracts.py`/`RafaelGraph(inference, tools)` shape that does not exist in v2; v2's `build(config)` fail-closes without `dmn`/`cibseven`/`audit_sink` (`graph.py:648-668`), a larger dependency surface than the donor's inference+tools. |
| `runtime/agent_runtime/a2a_composition.py` | NEW/additive | `build_auth_delegation_dispatcher(settings)` — Option-A in-process composition: `build_agent_cards(tenant, ["helena","rafael"])` + `{"rafael": make_rafael_handler(...)}` + a real `PostgresAuditSink` (T-F) + a real `PostgresIdempotencyStore` (when `DATABASE_URL` set) + `build_dispatcher(...)`. Reuses `agent_runtime.service._build_tool_deps` for rafael's dmn/cibseven/audit_sink/fhir — deliberately NOT a second, divergent dep-construction path. No production Kafka producer exists anywhere in the platform yet (confirmed — grep for a real `KafkaProducer`/bootstrap wiring returns nothing outside tests); a construction-time no-op `KafkaLike` stands in, clearly labeled, until a live Kafka seam lands (facts are observability, not the T-F audit surface — see §9.3). |
| `runtime/agent_runtime/service.py` | additive | New OPTIONAL readiness check `a2a_dispatcher_ready`, gated to `agent_id in ("helena", "rafael")` only (the two parties to this one edge — deliberately NOT forced on every unrelated agent replica, keeping W3 to its one-edge scope). Construction-only, mirrors `graph_loaded`: proves `build_auth_delegation_dispatcher(settings)` assembles; never calls `.delegate()`, never executes a turn. |
| `agents/helena/graph.py`, `agents/rafael/graph.py`, `a2a/{dispatcher,delegation,registry,facts,idempotency,assembly}.py` | UNCHANGED | W3 consumes W1+W2's public surface as-is; adds no new guard/audit/dispatch code. |

**The honest nuance (do not over-claim):** `runtime/agent_runtime/service.py` is still a health-only
daemon that executes no turns (`agent_graph_execution_not_performed_here`, its own log line) — there
is no live Kafka-consume loop and no cross-pod delegation consumer. The `a2a_dispatcher_ready` check
proves the edge *assembles*; the actual `await dispatcher.delegate(envelope)` call in this build is
driven by the W3 test suite / a thin driver, not by a supervised daemon loop. Remote cross-pod A2A
transport (queue_ref/endpoint, HTTP/mTLS) remains explicitly out of scope (S5/S8, unchanged from §5).

### 9.3 T-F becomes REAL — two distinct audit surfaces, do not conflate

W2 proved the dispatcher's `_audit_delegation` → `emit_once` call only against `FakeAuditSink`. W3
wires a REAL `PostgresAuditSink` on the edge and drives `delegate_auth_analysis(...)` against live
Postgres, so the `a2a.delegate:rafael` row is actually persisted into the audit hash chain on a real
delegation — this is the T-F "becomes real" moment, not just another unit proof.

Two chain links exist on a full run and must be told apart:
1. **The A2A delegation audit** (dispatcher `_audit_delegation`, `action="a2a.delegate:rafael"`,
   `dedup_key=f"{tenant}:a2a:delegate:{task_id}"`) — this IS T-F / site-5, W3's target. Fires
   strictly BEFORE the dispatcher calls `handlers["rafael"]`.
2. **Rafael's own process-start audit** (`start_process_idempotent(..., audit_sink=...)`, the T-C2
   fence inside `graph.py`'s `start_process` node) — a SEPARATE chain link, already covered by
   `test_rafael_auth_dossier.py`. The W3 integration test asserts specifically on (1)'s
   `action`+`dedup_key`, never conflating it with (2).

W4 residual (unchanged from §5): daemon-level `audit_sink_ready` fail-closed readiness for A2A, and
the T-G verifier-gated registry (`build_dispatcher(..., verifier=card_signer_from_key(...))`) so the
audited `agent_id=envelope.origin` binds to a cryptographically verified card identity. Neither is
built in W3.

### 9.4 Guardrail (unchanged, re-confirmed)

`HandlerOutput.output_ref` is always `f"process://{business_key}"` (`AUTH-{tenant}-{numero_guia_tiss}`)
— never a coverage decision. `rafael/delegation.py`'s handler doesn't even forward the dossier in its
`HandlerOutput.meta` (only `route`/`desfecho`/`process_started` strings) — stricter than the donor,
which is otherwise mirrored. `graph.py`'s own structural guardrail (`_build_dossier`'s
`decisao_cobertura` is always `None`) is the thing that makes this true regardless of what the
handler forwards; the W3 test suite asserts both independently.

---

## 10. W4 — enforcement & finalization (built 2026-07-25)

**Branch:** `t2.4-a2a-w4-tg-enforce-tf-final` off `t2.4-a2a-w3-helena-rafael-edge` @ `8d166ad`.
Closes the four items §9's "W4 residual" flagged, plus the tracked W2 audit-completeness gap and a
cosmetic DSN nit.

**T-G signed-Card enforcement (the security posture change).** W3 deliberately left
`build_auth_delegation_dispatcher` calling `build_dispatcher(...)` with no `verifier=` — cards came
back unsigned and the registry admitted them unconditionally (the W1 dev fail-safe path). W4 flips
the PRODUCTION composition: it resolves `signer = card_signer_from_key(card_signing_key_from_env())`
once, signs the Cards with it (`build_agent_cards(..., signer=signer)`), and injects the SAME signer
as `build_dispatcher(..., verifier=signer)` — symmetric HMAC, so the one key both signs and verifies.
The dev/test asymmetry from W1 is preserved exactly: `MAEZO_A2A_CARD_SIGNING_KEY` unset/empty ->
`signer=None` -> unsigned Cards, no verifier, unchanged Phase-0 behavior; present -> every Card is
signed AND the registry fail-closes on anything that isn't validly signed under that same key. The
underlying fail-closed gate (`A2ARegistry(verifier=...).register`) already existed and was already
fully unit-tested from W1 (`test_card_signing.py`); W4's job was wiring the composition root to
actually use it. New coverage: `tests/unit/a2a/test_assembly.py` (unsigned/tampered/wrong-key
rejected at `build_dispatcher`, so no dispatcher object is ever returned — "cannot dispatch" is
true by construction; a validly-signed card admits and dispatches) plus a LIVE-PG end-to-end proof
in `test_a2a_edge_live_pg.py` exercising the exact `card_signing_key_from_env`/`card_signer_from_key`
seam the composition calls, against a real `PostgresAuditSink`.

**T-F terminal-outcome audit (closes the W2 completeness gap).** Today only the pre-execution ALLOW
audit fires; when the target's handler raises a `DelegationError` after being allowed to run (e.g. a
cyclic sub-delegation), the dispatcher emitted a `rejected` Kafka fact but no second audit row — an
ALLOWed-then-internally-failed delegation left no terminal audit record distinguishing it from one
still silently in flight. `DelegationDispatcher._audit_delegation_outcome` (new) emits a SECOND,
terminal `emit_once` call. **Follow-up (LOW-1): this fires on BOTH terminal outcomes, not the
handler-error path only.** The original scoping (handler-error only, success left to the
`completed` Kafka fact) was reconsidered: the `completed` fact is ephemeral OBSERVABILITY, not the
durable T-F audit, so a successful delegation recorded only the pre-exec ALLOW in the durable chain
— indistinguishable, in that chain, from one that was allowed then crashed / is still in flight.
The success branch now emits a terminal `decision="COMPLETED"` outcome row (no `detail`),
restoring the symmetry the failure branch (`decision="FAILED"`, plus the exception message as
`detail`) already had. Exactly one terminal outcome fires per `task_id` (success XOR failure), so
the single `:outcome` dedup key never collides between the two. `action` gets a `:outcome`
suffix (`f"a2a.delegate:{target}:outcome"`), a TERMINAL-outcome `decision`
(`COMPLETED`/`FAILED`, distinct from the pre-exec ALLOW/DENY admission vocabulary so a chain reader
never mistakes it for a second admission decision), and
`dedup_key = f"{tenant}:a2a:delegate:{task_id}:outcome"` (`a2a_audit_outcome_dedup_key`) — distinct
from the pre-exec row's key so `emit_once` never collapses the two. Same PHI discipline as the
existing `_audit_delegation`: `details` excludes `payload_meta`, carries only task_id/task_type/
chain/target/payload_ref/decision_basis plus the structural exception message (itself PHI-free —
`CyclicDelegationError`/`MaxHopsExceededError`/`BudgetExhaustedError` messages only ever name
agent_ids and counts).

**T-F daemon readiness (finalization, honestly scoped).** The agent-runtime daemon is still,
unchanged from W3, a health-only scaffold with no live delegation-consumer loop (no Kafka-consume
loop, no cross-pod dispatch) — W4 does NOT fabricate one. What IS real: (1) the dispatcher's own
audit-before-effect ordering was ALREADY fail-closed — `_audit_delegation` runs, and can raise,
BEFORE the handler is ever invoked, so an audit sink that cannot durably persist already prevents
the handler from running (no code change; proved by a new unit test that makes `FakeAuditSink.
always_fail` and asserts the handler is never called and the exception propagates). (2) A NEW,
ADDITIVE readiness check, `a2a_audit_sink_ready` (`agent_runtime/service.py`), mirrors
`worker_runtime`'s T-D `audit_sink_ready` gate: a bounded `check_ready()` probe (new
`AgentRuntimeSettings.dep_connect_timeout_s`, same 5s default as the worker daemon) against the SAME
audit sink the A2A composition would use, reported red until proven reachable. Deliberately kept
SEPARATE from the pre-existing `a2a_dispatcher_ready` check (which stays construction-only, unchanged
— no existing test's semantics are broken) and, unlike the worker daemon's own gate, is a
boot-time-only snapshot rather than a live per-`/readyz` re-probe: there is no fetch/consume rotation
here for it to gate entry into, so a live re-probe would add cost without changing any actual
traffic-admission decision. If/when a real delegation-consumer is wired, it should condition on this
bit before pulling work — this build stops at making the bit honestly available.

**Structural-field PHI-scrub assessment (payload_ref only; task_type/origin/target untouched).**
Assessed adding defense-in-depth validation for the reported gap: a CPF embedded in a URI-style
`payload_ref` bypasses `_looks_like_phi`'s whole-string check (it requires the ENTIRE string to be
digits+separators). Rejected a bare-digit-run substring scan (any embedded 11/14-digit run) as
UNSAFE: legitimate FHIR/process resource ids are sometimes purely numeric and of arbitrary length,
so this would false-positive-reject legitimate references. Implemented instead a NARROWLY-scoped
substring check for the CPF/CNPJ **canonically-punctuated** shape only
(`\d{3}\.\d{3}\.\d{3}-\d{2}` / `\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}`), which a URI/reference scheme
essentially never produces by chance — this closes the concrete example in the charter (a
dot-and-dash-formatted CPF interpolated into a reference string) without touching bare numeric ids.
Fail-closed (raises `DelegationError`, same as the existing guard), not a silent scrub. `task_type`/
`origin`/`target` were assessed and deliberately left alone: they are not free-form strings a caller
could embed PHI into — `origin`/`target` are agent ids resolved against the Card registry and
`task_type` against `accepted_task_types`, both closed vocabularies enforced elsewhere in the
dispatch path, not text a caller can extend with a URI.

**Nit:** `test_a2a_edge_live_pg.py`'s hardcoded default DSN said port 5642 while the independently
R1-verified live run used 5643 (`docs/evidence-ledger.md`'s t2.4/W3 row flagged this exact mismatch).
Fixed the hardcoded fallback to 5643; `MAEZO_TEST_A2A_EDGE_DATABASE_URL` still overrides it for any
other local setup (this build's own test run used a dedicated, non-colliding 5645 via that override,
so it never touched the port-5642/5643 ambiguity at all).

> **EMENDA 2026-09-04 (gap `LIVE-SUITES-SILENT-SKIP-AUDIT`).** O nit acima reconciliou 5642→5643,
> mas **nenhuma das duas portas jamais foi servida** por este repositório (sem serviço no compose,
> sem service container no CI, sem comando de bring-up documentado): com o stack do próprio projeto
> de pé e sem env exportada, os 7 testes de `test_a2a_edge_live_pg.py` reportavam `COULD NOT VERIFY`
> — um default que ninguém serve é um skip silencioso, não uma prova. O fallback passou a ser o
> Postgres do `docker-compose.yml`
> (`postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5433}/maezo`), com
> `MAEZO_TEST_A2A_EDGE_DATABASE_URL` ainda vencendo. O isolamento continua vindo do schema
> `a2aw3<hex>` por execução, não do número da porta. Medido no stack isolado: `7 passed` contra
> 5433. Cerca de regressão: `tests/unit/ci/test_live_suite_defaults_are_served.py`.
