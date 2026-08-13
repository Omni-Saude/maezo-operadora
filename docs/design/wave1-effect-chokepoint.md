# Wave-1 DESIGN — the unified per-call effect chokepoint (W1 / P0)

> Documento de design da Onda 1 (Hardening §0.8) — autorado 2026-08-10 por agente R1 (sequential
> thinking), recuperado do transcript do agente em 2026-08-11; landa via PR da Onda 1.

**Author:** Wave-1 Designer (R1, security/engine-path lane) · **Date:** 2026-08-10
**Repo state:** `main` @ `3680a0b` · **Scope:** design only — zero code edits, zero commits.
**Vehicle:** ADR-0034 revisit precondition + ADR-0037 clause XRD-09. This is an EVOLUTION of the
ratified architecture. There is no parallel design here, and nothing below flips enforcement.

Every code claim carries a re-derived `file:line` verified in this session against the working
tree. Where a scout report conflicts with what the tree says, the tree wins and the conflict is
recorded in §0.

---

## 0. Premises verified, and seven premises REFUTED

### 0.1 Verified (build on these)

| Claim | Verified anchor |
| --- | --- |
| `ActionExecutionGateway` exists, ships DENYING, SHADOW-only | `src/maezo/gateway/action_execution.py:596-659` (`evaluate`), `:676-711` (`evaluate_worker_task`), manifest `spec/policies/autonomy/action-approvals.yaml:73` (`status: DRAFT`), `:99` (`modo: shadow`) |
| Exactly ONE worker dispatch path, gateway already in it | `src/maezo/tools/workers/harness.py:1576-1600` (`_evaluate_action_gate` → `evaluate_worker_task`), `:1633-1652` (enforcement branch), reached only from `_handle` `:1602`, which is reached only from `_run_handle` `:1362` ← `_spawn` `:1353` ← `run` `:1259` |
| Enforcement is a DATA act today (worker side) | `action_execution.py:435-436` (`modo` literal), `:470-471` (`status` literal), `Decision.enforced` `:174-181` |
| `start_process_idempotent` is the proven audit-before-effect pattern | `src/maezo/tools/mcp_cibseven/transport.py:1052-1202`: strict-gate seam check `:1130-1133`, durable claim BEFORE effect `:1136-1140`, strict dedup `:1145-1152`, idempotency probe `:1165`, effect `:1177`, orphan-claim announcement `:1187-1194` |
| 9 of 10 agent graphs start processes through that fence; beatriz does not | callers: `helena/graph.py:673`, `valentina:648`, `carolina:626`, `fernando:560`, `gustavo:697`, `lucas:639`, `marina:704`, `rafael:509`, `andre:986`; plus `tools/workers/{inadimplencia.py:722, contas.py:596,750, fraude.py:741}` and `platform/notification_bridge.py:1034` |
| ALL other agent-side effects are ungated, and the graphs say so | `agents/rafael/graph.py:40-46` ("v2 has no `ToolRegistry`/PEP gateway wiring for agent tool calls yet, T2.4 gap"), `agents/beatriz/graph.py:81-84`, `agents/marina/graph.py:105`, `agents/valentina/graph.py:95`, `agents/andre/graph.py:147`, `agents/carolina/graph.py:107` |
| Agent effects flow through injected `Protocol` seams, not concrete classes | e.g. `agents/rafael/graph.py:98-104` (`FhirReader` Protocol), `agents/helena/graph.py:144` (`WhatsAppSender`), `agents/andre/graph.py:316` (`PopulationFeatureClient`), `tools/mcp_cibseven/transport.py:138-186` (`CibSevenTransport` Protocol) |
| A Protocol-decorator over a seam is an ESTABLISHED in-repo pattern | `tools/workers/cibseven_engine.py:59-130` — `FreshClientCibSevenTransport` is a full `CibSevenTransport` decorator, already allowlisted in the start-process CI fence (`scripts/ci/check_start_process_fence.py:78-83`) |
| A per-agent capability allowlist ALREADY EXISTS AS DATA and is unenforced at runtime | `spec/agents/rafael/agent.yaml` `tools:` (6 ids), `process_keys: [SP-OP-AUTH-001]`, `autonomy_actions:` (5 L0-core names); `spec/agents/helena/agent.yaml:8-9,20-36`; parsed into `AgentDefinition.tools/process_keys/autonomy_actions` at `src/maezo/agents/__init__.py:145-159` |
| A tool-level action vocabulary is ALREADY RATIFIED in the autonomy matrix | `spec/policies/autonomy/L0-core.yaml:34-47`: `query_decision_engine` L3, `start_compliance_process` L2, `correlate_process_message` L2, `query_process_status` L3, `read_phi_data` L3, `send_beneficiary_message` L3, `send_beneficiary_template` L2, `read_write_memory` L3, `erase_patient_memory` L1 |
| `PEP.evaluate` exists, is sync, is fail-closed, and has zero runtime callers | `src/maezo/gateway/pep.py:412` (`evaluate`), `:336` (`build_pep`), `:71` (`HARD_ACTIONS` code-frozen); used only as a readiness probe at `runtime/agent_runtime/service.py:454` → check `:154-164` |
| CI already validates `mcp-<server>.<action>` tool ids against real server dirs | `src/maezo/platform/validation/agent_def.py:41-75` |
| The static-fence precedent works and is wired into CI | `scripts/ci/check_start_process_fence.py:106-188`, run by `.github/workflows/ci.yml:110-111` (`make check-start-process-fence`); siblings `check_bpmn_error_allowlist.py` (`:103-104`), `verify_amh_contract_pin.py` (`:118-119`) |

### 0.2 REFUTED premises (findings, not obstacles)

**R-1 — Scout B: "Process-key allowlist ENFORCED (`process_allowlist.py:120-165`)". FALSE.**
`ProcessAllowlist.ensure_allowed` has **zero** callers in `src/`. Exhaustive grep for
`process_allowlist|ensure_allowed|ProcessAllowlist|KNOWN_PROCESS_KEYS` across `src/` returns only
the module itself plus one prose mention in `tools/workers/base.py:70`; every functional reference
is in `tests/unit/tools/test_process_allowlist.py`. `start_process_idempotent`
(`transport.py:1052-1202`) never validates the process key. PLANS §0.8's W1 row
("`ProcessAllowlist` (ADR-0016) com zero importers de produção") is CORRECT; the scout is wrong.
**Design consequence:** ADR-0016's allowlist is not a layer we can lean on — it is a layer this
design must *install*, and installing it is part of closing W1, not an assumption.

**R-2 — Scout D: "`RealAnsGatewayTransport` … env-gated by `MAEZO_ANS_GATEWAY_TRANSPORT` … ctor
:259, timeout 300s". FALSE on both halves.** No `MAEZO_ANS_GATEWAY*` string exists anywhere in
`src/` (full `MAEZO_*` env census run this session; see §2.6). Selection is *injection*-based and
fail-closed: `resolve_ans_gateway` (`tools/workers/ans_gateway.py:286-299`) returns
`RefusingAnsGatewayTransport` when the seam is `None`, which is what the production composition
root passes. `RealAnsGatewayTransport.__init__` (`:263-266`) stores `base_url`/`auth_token` and
contacts nothing; `.submit` (`:269-283`) raises `AnsGatewayUnavailableError` unconditionally.
**Design consequence:** the ANS leg is *already* the shape this design generalizes (fail-closed
seam resolution, mock unreachable in prod by construction). It is a template, not a hole.

**R-3 — Scout A: "one wrapper layer at composition root could gate all six seams". TRUE only if
"composition root" means FOUR roots, and the naive reading is fatal.** See §2.1/§4 — the only
agent path that executes a turn in production builds its own deps and never calls
`_build_tool_deps`.

**R-4 — Scout A: "helena has NO `start_process` node". FALSE** (already adjudicated by the
orchestrator; re-verified): `agents/helena/graph.py:87` imports the fence and `:673` calls it.

**R-5 — Stale governance line references that the approvers will read.**
`spec/policies/autonomy/action-approvals.yaml:299` cites
`"src/maezo/tools/mcp_cibseven/transport.py:560"` as `start_process_idempotent`; so does
`scripts/ci/check_start_process_fence.py:6`. Line 560 is inside `FakeCibSevenTransport`
(`transport.py:472-565`) — the real function is at `:1052`. A Médica/ANS/Security approver
following the manifest's own reference lands on a test double. **Wave-1 must correct both
references** (a docs/data fix, no behaviour change).

**R-7 — Scout A's DMN line inventory counts the wrong thing (and the truth HELPS us).** Scout A
lists "DMN eval ×10+ … marina heaviest (8+: 501,508,518,530,536; 572,588,600)" and "rafael
378/394/448". Those are call sites of a **private per-graph helper**, not transport calls: the
actual `self._dmn.evaluate(...)` appears **exactly once per graph** — `rafael/graph.py:546-548`
(`async def _evaluate_dmn` at `:546`, transport call at `:548`), `marina/graph.py:804-806`
(`:804` / `:806`). Verified by grepping `_dmn\.evaluate` per file. **Design consequence:** the
effective agent-side DMN surface is 1 site × 10 graphs, not 10+ scattered sites, and it is already
funnelled behind a named helper — further evidence that the seam-decorator approach (Candidate B)
matches how these graphs are actually built. The same likely holds for other seams; the wrapper
count is bounded by the number of *Protocols*, not by the number of *call sites*, which is exactly
why this design touches zero node code.

**R-6 — `transport.py`'s module docstring advertises an enforcement point that was deliberately
deleted.** `transport.py:11` and `:19` describe "`CibSevenServer` (tool-registration/allowlist/
metrics …)" and say "`CibSevenServer.start_process` below is the enforcement" — but
`tests/unit/tools/test_mcp_cibseven.py:46-48` asserts `CibSevenServer` is NOT importable and NOT
in `__all__`, and `:348` repeats the claim. A reader auditing the effect plane from this docstring
would conclude an allowlist enforcement point exists. **Wave-1 must correct it.** This is the same
class of defect as R-1: the *documentation* of the effect-authorization plane is ahead of the code,
which is precisely how W1 stayed invisible.

---

## 1. STEP 1 — Invariants the chokepoint must preserve

Each invariant is stated as a property, with the existing anchor it must not break and the
mechanism this design uses to hold it. These are the acceptance criteria for the whole wave.

**I-1 Audit-before-effect (ADR-0007; T-C2).** No external effect may occur without a durable
record preceding it, for every class that already has that property. Anchors:
`transport.py:1136-1140` (claim before start; `AuditPersistenceError` propagates and fails the
turn) and `harness.py:1659-1665` (audit emitted, THEN `complete`). *Mechanism:* the PEP never
moves an existing audit emission; it only ADDS a pre-effect refusal record on denial
(`harness._audit_guard_refusal` `:1528-1572` is the exact shape) and, for classes declared
`audita_antes: true`, an approval-provenance record emitted through the SAME sink before the
delegated call. The PEP must be *unable* to produce "effect happened, nothing recorded" in any
ordering — including PEP-internal failure (§1 I-4).

**I-2 Fail-closed on missing seams.** A seam that is absent must never silently degrade into an
un-gated call. Anchors: `rafael/graph.py:672-681` (missing `audit_sink` → `ValueError` at build),
`platform/webhooks/service.py:96-101` (no `DATABASE_URL` → dispatcher refuses to construct →
`/webhook` 501), `ans_gateway.py:286-299`. *Mechanism:* `build_agent_seams` (§5) raises at
composition time if any declared seam cannot be wrapped; the composition roots already isolate
build failures into red readiness, so the failure mode is "replica not ready", never "replica
ready and ungated".

**I-3 PHI never enters a policy or audit payload.** Anchors: `action_execution.py:56-57` and
`:714-729` (`_log_decision` emits only bounded tokens; `_is_bounded_token` `:150-152`,
`_is_bounded_topic` `:727-729`), ADR-0037 proibição imutável 5 (`0037-…:210-211`), ADR-0006 zones.
*Mechanism:* the `EffectCall` value object (§5.2) is **structurally incapable** of carrying free
text: every field is either a bounded token matching `_TOKEN_RE` / the dotted-topic regex, or an
already-computed non-PHI hash. Payloads are passed to the wrapped seam by reference, never into the
decision core. Pinned by a fence test in the shape of `action_execution.py`'s existing token
discipline and by the `ports` purity precedent (`src/maezo/ports/__init__.py` §Invariant 5,
AST-fenced by `tests/unit/ports/test_ports_purity.py`).

**I-4 The PEP itself must be total.** A gateway bug must never crash a care path, and must never
fail OPEN once enforcing. Anchor: `evaluate_worker_task`'s double-guarded fallback
(`action_execution.py:695-711`: on internal error, resolve `mode` from the cached manifest and
return `_fail_closed_decision`). *Mechanism:* one decision function, the same posture, reused
verbatim by the agent path. Corollary: a PEP internal error under `enforcing` DENIES; under
`shadow` it is a log line only.

**I-5 Human-gate flips are DATA, never code.** Anchor: `action-approvals.yaml:31-45` (the exact
human act), loader `action_execution.py:459-516`. *Mechanism:* progressive per-class enforcement is
expressed as two new **data** fields (§7.3), read by the existing loader shape; no Python change is
required to flip any class, and no Python change can flip one.

**I-6 L0-hard guards stay INDEPENDENT of the PEP (defense in depth).** ADR-0018's structural
no-denial enforcement and the 10 `ERR_*_NOT_HUMAN` worker guards, the `PRODUCTION_BPMN_ERROR_
ALLOWLIST` (`runtime/worker_runtime/service.py:179`), the `HARD_ACTIONS` frozenset
(`gateway/pep.py:71`) and the process-start fence must all keep refusing with the PEP removed.
XRD-09 states this in terms: the chokepoint is "ADICIONAL ao enforcement estrutural BPMN/HITL do
ADR-0018 — nunca substituto" (`0037-…:153-154`). *Mechanism:* the PEP is a *pre-*dispatch layer
that can only subtract permission; no guard is refactored to call it, and §8's mutation matrix
proves each guard independently with the PEP neutralized.

**I-7 Zero behaviour change while inert.** Anchor: the existing parity proof is named in
`harness.py:1583-1585` — "runs the choked path with the gateway neutralized and with it live,
asserting identical transport outcomes". *Mechanism:* the agent-side wrappers get the identical
proof obligation, per seam: same return value, same exception type, same call count, same ordering,
with the gateway neutralized vs live. This is the Phase-0 exit criterion (§9).

**I-8 Tenant isolation.** Anchors: audit dedup keys embed tenant (`transport.py:687`
`start_dedup_key(tenant_id, …)`), per-tenant advisory-locked transactions
(`gateway/audit_postgres.py`), `PEP` is built per tenant (`build_pep(tenant=…)`,
`agent_runtime/service.py:454`). *Mechanism:* `EffectCall.tenant` is mandatory and non-defaultable;
the registry is constructed per (tenant, agent) and cannot be shared across tenants; the manifest
cache key must include tenant once tenant overlays are permitted (today it does not — the manifest
is global; recorded as open question Q-8).

**I-9 Engine-path latency budget.** The decision must be pure in-process dict lookups plus one log
line. Anchor: `action_approvals` is `lru_cache`d (`action_execution.py:578-593`), `PEP.evaluate` is
sync and matrix-local (`pep.py:412-450`), `CeilingResolver` reads a cached matrix. *Budget
proposed:* ≤ 1 ms p99 added per gated call, **zero** added network I/O on read classes. Any layer
that would add I/O (consent point-read, per-call audit on read classes) is explicitly OUT of the
default path and requires the per-class `audita_antes` / `consentimento_exigido` flag plus SRE
sign-off (Q-5, Q-9).

**I-10 Shadow evidence must be collectable BEFORE any flip, for every class.** Anchor: the
manifest's own `superficies[].choked` documentation field (`action-approvals.yaml:119-125`) exists
precisely because five surfaces produce NO shadow evidence today
(`comunicacao_beneficiario`'s WhatsApp surface `:263-265`, both `leitura_phi_clinica` surfaces
`:315-320`, `delegacao_a2a` `:334-336`, the agent-initiated start `:299-301`). *Mechanism:* Phase 0
must flip every `choked: false` to `choked: true` **as an observed fact**, and the evidence packet
(§9.2) must show ≥ N days of telemetry per class before that class is eligible for a flip. A class
with zero shadow lines is not approvable.

**I-11 The chokepoint must be INEVITABLE, not merely available.** XRD-09's word is "inevitável"
(`0037-…:152`). A design that offers a gated path is worth nothing if an ungated path remains
constructible. *Mechanism:* §8's static fence on *construction and import* of raw effect classes,
plus a per-composition-root boot assertion that every seam handed to a graph is a gated instance.

**I-12 The revisit precondition's OWN terms must be honoured.** ADR-0034 `:78-85` demands, if the
chokepoint returns: `evaluate(ToolCall)` with **auditoria de recusa** + the **L2-ALLOW branch**,
and that `L2ReviewSampler` + a concrete persistent `ReviewQueue` be built **as part of this
chokepoint** (`sample_rate` config/env-gated, non-PHI `sample_key`, best-effort enqueue that never
blocks), plus "uma nova ADR ratificando a re-introducao". ADR-0037 `:239-244` claims XRD-09 as the
ratification vehicle for the *chokepoint*; it says nothing about sampling. *Mechanism:* §5.6
specifies the sampling seam inside the decision core; §10 Q-3 asks the owner whether it ships in
Wave 1 or is amended out by the new ADR. **A design that ignores this clause does not satisfy the
vehicle it claims to ride.**

---

## 2. STEP 2 — Adversary model

Not "who is malicious" — "what shape of change, made in good faith or otherwise, restores an
ungated effect". Each entry names the concrete surface in this tree and the defeating mechanism.

**A-1 A new graph node calls a transport directly.**
Reachable today: every graph already holds `self._cibseven` (a raw `CibSevenTransport`) and calls
`correlate_message` / `get_process_status` on it with no fence (`transport.py:151-161` Protocol,
`:370`/`:403` HTTP impl). Only `start_process_instance` is statically fenced
(`check_start_process_fence.py:68`). *Defeat:* the injected object IS the gated decorator, so the
"direct" call is already choked; plus the fence extends to forbid constructing/importing the raw
transport outside the registry (§8).

**A-2 A new worker topic outside the harness.** Worker effects reach the engine through
`harness._handle` `:1602`, but the *other* engine leg is `tools/workers/cibseven_engine.py`'s
`FreshClientCibSevenTransport`, injected directly into function workers — its own docstring says it
covers "the residual ADR-0001 leg that `tools/workers/harness.py` does NOT cover"
(`cibseven_engine.py:4-7`). A new worker that takes `engine=` and calls `correlate_message` is
ungated. *Defeat:* the worker composition root resolves `engine=` through the same registry;
`FreshClientCibSevenTransport` becomes the *inner* transport of a gated decorator, not the injected
object. The fence's existing allowlist entry for it (`check_start_process_fence.py:81`) stays, and a
new construction fence adds it to the "raw, must be wrapped" set.

**A-3 An MCP tool added without registration.** `agent.yaml`'s `tools:` list is validated for
*server existence* only (`platform/validation/agent_def.py:64-75`) — never for action existence,
and never enforced at runtime. A new `mcp_*` server with a `send`/`write` method is reachable the
moment someone injects it. *Defeat:* the registry resolves seams by **declared tool id**; a seam
whose operation id is not in the agent's `tools:` list is refused at composition (fail-closed), and
the validation gate is extended to require every `mcp-<server>.<action>` id to exist in the
registry's operation catalogue (§8.4).

**A-4 An agent seeded with a raw client.** The concrete, live instance of this: the Helena webhook
path. `platform/webhooks/service.py:103-109` constructs `InferenceProvider()`,
`CibSevenDmnTransport`, `CibSevenHttpTransport`, `WhatsAppServer`, `PostgresAuditSink` itself and
hands them to `HelenaDispatcher`, which calls `agents.helena.graph.build({...})` **directly**
(`platform/webhooks/whatsapp/dispatch.py:165-176`) — bypassing `runtime.harness.Harness.create_graph`
and `_build_tool_deps` entirely. `a2a_composition.py:242` and `:340` likewise construct their own
`InferenceProvider()`. *Defeat:* §8's construction fence makes "construct a raw effect class outside
the registry module" a CI failure, and the boot assertion (§5.5) makes it a red readiness check at
runtime. This adversary is the reason the design is NOT "decorate `_build_tool_deps`".

**A-5 Test doubles leaking into production composition.** Production modules currently EXPORT
doubles: `FakeCibSevenTransport` (`transport.py:472`), `FakeWorkerTransport` (`harness.py:895`),
`FakeAuditSink` (`harness.py:1048`), `FakeKafkaPublisher` (`harness.py:1022`),
`LabeledMockAnsGatewayTransport` (`ans_gateway.py:198`), `PhiZoneMockProvider`
(`runtime/inference.py:795` = `class PhiZoneMockProvider`). The ANS seam already solves this
correctly *by construction*
(`resolve_ans_gateway:286-299` — prod injects nothing, so prod gets the refusing transport, and the
mock is unreachable without an explicit injection). *Defeat:* generalize that posture — the registry
resolves *unwired* seams to a Refusing implementation, never to a mock; the fence forbids importing
any `Fake*`/`*Mock*` symbol from a production composition-root module.

**A-6 Env-var bypass — the class the MZO-040 gatekeeper already caught once.** The existing fence is
narrow and real: an `MAEZO_ACTION_APPROVALS_PATH`-sourced manifest declaring `enforcing` resolves to
`shadow_override` unless `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1`
(`action_execution.py:443-457`, `:394-396`). **But it has a hole this design must close:**
`_manifest_default_path()` (`:326-328`) resolves through `resolve_spec_dir()`
(`agents/__init__.py:65-98`), which honours **`MAEZO_SPEC_DIR`** as authoritative. Setting
`MAEZO_SPEC_DIR=/tmp/evil` swaps `action-approvals.yaml`, `L0-core.yaml`, `_hard_frozen.yaml` AND
every `agent.yaml` **in one variable**, with `override_sourced=False` — so the override fence never
fires, no `action_approvals_manifest_path_overridden` error line is emitted, and a forged
`status: RATIFICADO` + `modo: enforcing` manifest would **ALLOW** every class. Today this is inert
(nothing enforces, and an ALLOW changes nothing). The instant the agent-side chokepoint enforces,
`MAEZO_SPEC_DIR` becomes a single-variable, silent, total authorization bypass. *Defeat:* §5.7 —
policy-source pinning: in production mode, refuse to resolve policy artefacts through a
`MAEZO_SPEC_DIR` override unless a companion explicit flag is set, and emit the same loud
`error`-level provenance line the path override already emits. Owner/security decision (Q-6).

**A-7 A compromised or hallucinating LLM planning step chooses an unsanctioned tool.** Today the
graphs are statically wired — there is no LLM-chosen tool dispatch (`_llm.generate` returns text
that nodes parse; e.g. `rafael/graph.py:39` notes the single `phi=True` dossier call). The exposure
is *forward-looking* and is exactly what PLANS §0.8 Onda 2 warns about ("NÃO ligar LLM mais capaz em
produção antes da Onda 1 enforçar"). *Defeat:* the capability layer (agent `tools:` allowlist) is
evaluated per call with the agent id as principal, so a future planner can only reach the seams its
`agent.yaml` declares; the operation catalogue is closed; unknown operation → DENY. Also: the
existing input-boundary discipline (`new_helena_state`'s keyword-only constructor,
`dispatch.py:191-198`) is the model — the PEP must not accept a caller-supplied action class.

**A-8 Un-attributed call — the seam is gated but the principal is forged.** A wrapper that trusts a
caller-supplied `agent_id` gates nothing. *Defeat:* the principal is bound at **construction** of
the gated seam (closure over `(tenant, agent_id)`), never a parameter of the call. Same technique
`_ScopedWhatsAppSender` already uses to bind a recipient for exactly one turn
(`dispatch.py:100-118`) — including its hash-mismatch refusal at `:110-117`.

**A-9 The manifest is deleted or corrupted after the flip.** Handled today for the worker path by a
CI fence test (`action_execution.py:38-44` names
`tests/unit/sec/test_action_execution_fence.py::test_shipped_manifest_exists_and_is_wellformed`),
but the *runtime* residual is real: a deleted deployed file downgrades enforcement to
`MODE_UNRESOLVED`, which never enforces (`:436-442`, `Decision.enforced` `:181`). *Defeat:* Wave 1
adds a readiness check `effect_policy_enforcing` that goes RED when a class the operator believes is
enforcing resolves to `unresolved`/`shadow` — turning a silent downgrade into a visible one. (It
must NOT make enforcement fail-open; it makes the downgrade *loud*.)

**A-10 The `lru_cache` makes a rollback slower than the incident.** `action_approvals` caches
per-path for process lifetime (`:578-593`; the docstring is explicit: "takes effect on the next
daemon restart"). A bad flip cannot be rolled back by editing data alone. *Defeat:* Q-7 proposes a
bounded TTL re-read (fail-closed on re-read failure: keep the last good view, never widen), or an
operational commitment that rollback = `kubectl rollout restart`. Either is acceptable; leaving it
undecided is not.

**A-11 Two composition roots drift.** `a2a_composition.py:74` imports `_build_tool_deps` **from**
`agent_runtime/service.py` specifically to avoid a second construction path (docstring `:13-18`);
`platform/webhooks/service.py:89-90` merely says it "mirrors" it, and does not use it. Drift already
happened. *Defeat:* one registry module, zero mirrors, enforced statically (§8).

**A-12 Denial becomes a care-safety incident.** A DENY on a *read* seam that a node treats as
best-effort is safe (degrades to a disclosed gap note — `rafael/graph.py:44-48`). A DENY on a node
that treats the seam as required is a stalled care request. This is the *inverse* adversary: the PEP
harming the patient. *Defeat:* the class taxonomy (§7) pins, per class, the declared denial shape,
and §8's mutation proofs assert that shape live. `auth_criteria`'s posture is the precedent quoted
in `action_execution.py:31-34`: on a worker path "a raise … becomes an external-task incident and an
incident STALLS a care request".

---

## 3. STEP 3 — Candidate designs

Four candidates, each described in the same five terms.

### Candidate A — Decorate at the composition root (`_build_tool_deps`)

Wrap each of the six seams in `runtime/agent_runtime/service.py:288-365`, feeding
`ActionExecutionGateway`.

* **Unification:** reuses `evaluate_worker_task` with a synthetic action ref per seam op.
* **Migration cost:** lowest imaginable — ~1 file, ~40 lines, zero node changes.
* **Bypass surface:** catastrophic (see §4 C-A1).
* **Testability:** good — the existing readiness checks already exercise `_build_tool_deps`.
* **Blast radius:** near zero, because it is near-vacuous.

### Candidate B — A central `ToolRegistry` all deps resolve through

A new module (`maezo.gateway.tool_registry`) is the ONLY sanctioned constructor of effect seams.
It returns gated decorators (Protocol-preserving) built over the raw classes, with the decision core
inside. Every composition root calls it. A static fence forbids constructing/importing the raw
classes elsewhere; a boot assertion verifies gatedness.

* **Unification:** the registry and the harness call ONE `decide()` function whose ratification leg
  is `ActionExecutionGateway`; the worker path keeps its topic-keyed entry point unchanged.
* **Migration cost:** medium — 1 new package, 4 composition roots re-pointed, 1 new CI gate; zero
  node changes (structural typing).
* **Bypass surface:** the residual is "someone writes a new raw client from scratch" — reduced to
  the `httpx.AsyncClient` construction fence (§8.2).
* **Testability:** high — decision core is pure and total; wrappers are parity-testable per seam.
* **Blast radius:** contained; inert build is provable.

### Candidate C — Generalize the harness: move agent effects onto a dispatch bus

Agent nodes stop awaiting seams and instead emit typed effect *intents* onto a bus that the harness
(or an agent-side twin) dispatches, gating at the single dispatch point.

* **Unification:** perfect in principle — literally the same code path as workers.
* **Migration cost:** very high — every effectful node in 10 graphs is rewritten.
* **Bypass surface:** small once complete.
* **Testability:** every graph test is rewritten; parity with today is not provable.
* **Blast radius:** total.

### Candidate D — Gate inside the leaf classes (`FhirServer.read_resource`, `WhatsAppServer.send_message`, …)

Put the check at the bottom, where the HTTP call is made
(`mcp_fhir/server.py:88`,`:118`; `mcp_whatsapp/server.py:111`; `transport.py:341`,`:370`,`:403`).

* **Unification:** the gateway becomes literally unavoidable for those classes.
* **Migration cost:** low.
* **Bypass surface:** any *other* implementation of the same Protocol.
* **Testability:** medium.
* **Blast radius:** medium — leaf classes are used by tests everywhere.

---

## 4. STEP 4 — Counterexample hunt (per candidate, against §2)

### Against Candidate A

**C-A1 (FATAL, A-4).** `_build_tool_deps` feeds only `_load_agent_graph`
(`agent_runtime/service.py:386-390`) inside a daemon that, by its own log line, never executes a
turn: `agent_graph_execution_not_performed_here` (`:542-548`), and the module docstring says live
execution happens in "the webhook receiver's own in-process dispatch" (`:40-42`). That receiver
builds its own deps (`platform/webhooks/service.py:103-109`) and calls `build({...})` directly
(`dispatch.py:165-176`). **Candidate A gates exactly the traffic that does not exist and misses
100% of live agent effects.** Not repairable by "also wrap in webhooks/service.py" — that is
Candidate B with two copies, i.e. A-11 by construction.

**C-A2 (A-11).** `a2a_composition.py:242,:340` constructs `InferenceProvider()` independently of
`_build_tool_deps`; the LLM seam would be ungated on the A2A/dossier edges.

**C-A3 (A-1).** Nothing prevents the next `_build_tool_deps` edit from adding a raw seam. The
protection is a convention, and W1 exists because a convention was the protection.

### Against Candidate C

**C-C1 (FATAL, I-3).** An intent bus must serialize the effect payload. Helena's WhatsApp send
carries the **raw recipient number**, deliberately closed over for one turn and never persisted
(`dispatch.py:100-118`, and the module contract quoted at `:101-102`: "never stored on
`HelenaState`, never persisted past this call"). The inference seam carries the prompt, with
`phi=True` routing (`runtime/inference.py:1021-1033` = `InferenceProvider.generate`'s signature +
the `phi:` arg doc; `PhiZoneRoutingError`). A bus turns both into
serialized, in-flight, potentially persisted payloads — a direct collision with ADR-0006 and with
ADR-0037's proibição 5. Repairing it means a second PHI-safe envelope design; that is Onda 2/MZO-070
work, not a chokepoint.

**C-C2 (FATAL, I-7).** Parity while inert is unprovable: rewriting every effectful node changes the
thing whose invariance you are trying to demonstrate. The evidence packet the approvers need
(§9.2) would be evidence about a system that no longer exists.

**C-C3.** LangGraph checkpointing interacts: intents crossing an `ainvoke` boundary become
checkpointed state (`dispatch.py:180-186` compiles checkpoint-enabled and threads
`conversation_id`), so effect payloads land in `checkpoint_blobs`. That is the exact row the
keyed-pseudonym discipline at `dispatch.py:177-183` was built to keep PHI-safe.

### Against Candidate D

**C-D1 (FATAL as sole mechanism, A-1/A-3).** Agents do not call the leaf classes. They call injected
Protocol adapters: `FhirServerReader` (`agents/rafael/adapters.py`), Valentina's own copy
(`agents/valentina/adapters.py`), Marina's (`agents/marina/adapters.py`), Lucas's
`WhatsAppServerSender` (`agents/lucas/adapters.py`), Helena's (`agents/helena/adapters.py`). The
Protocol is the seam; the leaf is one implementation of it. A new adapter over raw `httpx` satisfies
`FhirReader` (`rafael/graph.py:98-104`) and is completely ungated.

**C-D2 (layering).** It inverts the dependency the gateway module explicitly protects:
`action_execution.py:144-146` — "`maezo.gateway` must not depend on `maezo.tools` (the dependency
runs the other way — the harness imports `gateway.audit`). Duplicating one regex keeps the layering
clean." Putting policy in `maezo.tools` re-opens that.

**C-D3 (no principal).** `FhirServer.read_resource` has no `agent_id` and no `tenant`; it cannot
evaluate a per-agent `tools:` allowlist or a tenant overlay. The most it can enforce is "somebody,
somewhere, is allowed" — which is not authorization.

**C-D4 (blast radius on tests).** Every unit test that constructs a leaf class would need a policy
context, converting an inert build into a repo-wide test migration.

### Against Candidate B (the chosen one — holes found and closed)

**C-B1 (A-11 → CLOSED by §8.1).** A registry is just a function; a composition root can ignore it.
Closed by the *construction/import* fence, which converts "should call the registry" into a CI
failure, exactly as `check_start_process_fence.py` converted "should call the fence".

**C-B2 (A-1, residual → REDUCED by §8.2).** A determined author can write a new raw `httpx` client
and skip the registry entirely. Reduced (not eliminated) by fencing
`httpx.AsyncClient(`/`httpx.Client(` construction outside an allowlist, and by the
`effect_seams_gated` boot assertion, which catches anything actually injected into a graph.
Honest residual: an effect performed *inside a node body* with a locally constructed client would
evade the seam assertion and be caught only by the httpx fence. Recorded, not hidden.

**C-B3 (A-8 → CLOSED).** Principal must be closure-bound, not a call parameter. Design constraint,
pinned by a fence test asserting no gated wrapper's public method accepts an `agent_id`/`tenant`
argument.

**C-B4 (A-6 → CLOSED 2026-08-12; was PARTIALLY CLOSED pending a human).** `MAEZO_SPEC_DIR` is a
total policy-source substitution that the existing override fence does not see. §5.7 specifies the
closure; Q-6 was the owner/security decision, because refusing `MAEZO_SPEC_DIR` in production could
break a legitimate deployment pattern (Helm's `AGENT_DEFINITION_PATH` mount is a *different*
mechanism — `agent_runtime/service.py:281-285` — so the risk of breakage is low, but it was not
mine to decide). **The owner ratified refuse-to-load** (§5.7 item 1, `PLANS.md` §0.8): production
refuses the variable, explicit local runtime keeps it, the companion bypass is removed.

**C-B5 (A-12 → CLOSED by design, proven by §8.5).** Per-class declared denial shapes plus mutation
proofs. Without this, the first flip is a care incident.

**C-B6 (I-12 → OPEN).** Candidate B as described does not, by itself, discharge ADR-0034's
sampler clause. §5.6 adds the seam; Q-3 puts the build/defer decision to the owner.

---

## 5. STEP 5 — The chosen design, and why it survives

### 5.0 Choice

**Candidate B**, specified concretely as: *one sanctioned seam-resolution module
(`maezo.gateway.tool_registry`) producing Protocol-preserving gated decorators, backed by a single
total decision core (`maezo.gateway.effect_pep.decide`) that composes the already-ratified layers,
whose ratification leg is the existing `ActionExecutionGateway`, made inevitable by an AST
construction/import fence and a per-root boot assertion.*

It survives because every fatal counterexample against the alternatives is structural, and B's
holes are all closable by mechanisms this repo has already proven: a Protocol decorator
(`cibseven_engine.py:59-130`), an AST CI fence (`check_start_process_fence.py`), a fail-closed seam
resolver (`ans_gateway.py:286-299`), a closure-bound principal (`dispatch.py:100-118`), and a
data-only ratification record (`action-approvals.yaml`).

### 5.1 Module layout (new code, all under `maezo.gateway` — layering preserved)

```
src/maezo/gateway/
  action_execution.py        # UNCHANGED core; +additive loader support for §7.3 data fields
  effect_pep.py              # NEW — the decision core: EffectCall, EffectDecision, decide()
  effect_classes.py          # NEW — closed operation catalogue: seam-op id -> (autonomy_action,
                             #        action_class, denial shape, audita_antes, consent flag)
  tool_registry.py           # NEW — the ONLY sanctioned constructor of effect seams
  seams/
    fhir.py  whatsapp.py  cibseven.py  dmn.py  inference.py  population.py  a2a.py
                             # NEW — one Protocol-preserving decorator per seam
```

`maezo.gateway` continues to not import `maezo.tools` at module scope for *policy* purposes; the
registry imports concrete transports **inside** its factory functions (the branch-local import
idiom already used at `agent_runtime/service.py:296-302` and `:332`), keeping the policy core clean
and AST-fenceable.

### 5.2 `EffectCall` — the value object (I-3 by construction)

Fields, all bounded, all non-PHI, none free-text:

| field | type | source | note |
| --- | --- | --- | --- |
| `tenant` | bounded token | closure | `_is_bounded_token` (`action_execution.py:150`) |
| `principal` | bounded token | closure | `agent_id` or `worker:<topic>`; never a call arg (A-8) |
| `operation` | dotted token | catalogue | e.g. `fhir.read_patient`, `whatsapp.send_message`, `cibseven.correlate_message`, `inference.generate`, `dmn.evaluate`, `population.actuarial_risk`, `a2a.delegate`, `cibseven.start_process` |
| `action_ref` | dotted token | derived | worker: `task.topic` verbatim (unchanged). agent: `agente.<operation>` |
| `autonomy_action` | L0-core name | catalogue | one of `L0-core.yaml:34-47` etc. |
| `process_key` | `SP-OP-*` or None | call | validated by `_PROCESS_KEY_PATTERN` (`process_allowlist.py:63`) — bounded, non-PHI |
| `value_cents` | int or None | call | for the ceiling leg only; a number, never a payload |
| `phi_zone` | enum | agent.yaml `security_zone` | `general`/`phi` (ADR-0006) |

**No payload, no prompt, no recipient, no business key, no patient id ever enters `EffectCall`.**
The wrapper holds the real arguments and passes them to the inner seam only after ALLOW.

### 5.3 The decision ladder (most-restrictive-wins, total, never raises)

```
decide(call) -> EffectDecision(allow, enforced, reason, action_class, layer)
  L-0 catalogue     unknown operation                      -> DENY  OPERACAO_DESCONHECIDA
  L-1 capability    operation not in agent.yaml `tools`    -> DENY  TOOL_NAO_DECLARADA
                    process_key not in agent.yaml
                    `process_keys` ∩ KNOWN_PROCESS_KEYS    -> DENY  PROCESS_KEY_NAO_PERMITIDA   [closes R-1]
  L-2 autonomy      PEP.evaluate(autonomy_action)          -> DENY / REQUIRE_HUMAN  (pep.py:412)
  L-3 ceiling       CeilingResolver (L2 params)            -> DENY  TETO_EXCEDIDO   (ceilings.py:134)
  L-4 consent       ConsentDecisionSource point-read       -> DENY  CONSENTIMENTO_AUSENTE  [flagged, §Q-5]
  L-5 ratification  ActionExecutionGateway.evaluate(class)  (action_execution.py:620-659)
  L-6 sampling      L2ReviewSampler on an L2 ALLOW          [ADR-0034 :78-85, §Q-3]
```

`enforced` is computed **separately** from `allow` (§7.3) so a class can be evaluated long before it
can block. Layers L-0..L-4 are *structural* policy that already exists as ratified data; L-5 is the
*human* gate. A DENY at any layer records which layer produced it — an approver reading shadow
telemetry must be able to tell "no human has signed this class yet" (`APROVACAO_PENDENTE`) apart
from "this agent was never allowed to do this at all" (`TOOL_NAO_DECLARADA`). The existing reason
enum already models exactly this distinction (`action_execution.py:198-207`).

### 5.4 The wrappers

One decorator per Protocol, structurally satisfying it so **no node code changes**. Shape (all
seven identical):

```
class GatedFhirReader:                        # satisfies agents/rafael/graph.py:98 FhirReader
    def __init__(self, inner, *, ctx: SeamContext): ...      # ctx = (tenant, agent_id, catalogue)
    async def read_patient(self, patient_id):
        d = decide(EffectCall(operation="fhir.read_patient", ...))    # no patient_id in the call
        if d.enforced and not d.allow:  -> declared denial shape for the class (§7)
        # audita_antes classes: emit the pre-effect record HERE, before delegating (I-1)
        return await self._inner.read_patient(patient_id)
```

Denial shape is **per class**, taken from the catalogue, never invented at the call site (§7).
Precedents mirrored: the decorator shape from `cibseven_engine.py:59-130`; the refusal-audit shape
from `harness._audit_guard_refusal` (`:1528-1572`); the "gate seams checked before anything durable
is written" ordering from `transport.py:1130-1133`.

### 5.5 Inevitability: registry + boot assertion

`tool_registry.build_agent_seams(*, settings, agent_id) -> dict[str, Any]` becomes the single
constructor. The four agent-side roots call it and nothing else:
`runtime/agent_runtime/service.py:288` (`_build_tool_deps` becomes a thin delegation),
`runtime/agent_runtime/a2a_composition.py:222,:242,:340`,
`platform/webhooks/service.py:88-127`, `platform/integrations/notifications_bridge.py:309-330`
(+ `platform/notification_bridge.py:1034`'s start path). The worker root
(`runtime/worker_runtime/service.py`) resolves `engine=`/`dmn=` the same way.

Each root gains one readiness check, `effect_seams_gated`, asserting every seam in the dict is a
gated instance — modelled on the existing check family (`agent_runtime/service.py:132-270`), red on
failure, never fatal to liveness. Rationale: the CI fence catches source-level bypasses; this
catches a *runtime* one (a monkeypatch, a plugin, a config-driven factory).

### 5.6 The ADR-0034 sampling clause (I-12)

`decide()` exposes exactly the hook ADR-0034 `:78-85` names: on an L2-level ALLOW, call
`L2ReviewSampler(sample_rate)` with a **non-PHI `sample_key`** (a hash already computed for the
audit dedup key — e.g. `start_dedup_key`'s shape at `transport.py:687`), and enqueue best-effort
into a concrete persistent `ReviewQueue` that **never blocks and never fails the call**. Both are
specified here; whether they are BUILT in Wave 1 is Q-3.

### 5.7 Policy-source pinning (closes A-6)

Two additions, both narrow:
1. `resolve_spec_dir()`-sourced policy loads record their provenance; when `MAEZO_SPEC_DIR` is set
   AND the runtime mode is production, the effect-policy loaders emit the same loud `error` line
   the path override emits (`action_execution.py:546-553`) and — per Q-6 — either refuse
   enforcement (mirroring `MODE_SHADOW_OVERRIDE`, `:443-457`) or refuse to load.
   **RESOLVED 2026-08-12 (Q-6, owner): REFUSE TO LOAD — the stronger of the two options.** The
   refusal lives at `maezo.agents.resolve_spec_dir()` (the single T0.3 chokepoint, so the closure
   is by construction rather than per-root opt-in) and raises `SpecDirOverrideRefusedError`
   whenever the variable is set outside an EXPLICITLY local runtime; an absent runtime mode is
   PRODUCTION (ADR-0039 Q7 / Train-F default). The weak evaluate-only form, and the
   `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT` companion that lifted it, are REMOVED. The
   only sanctioned future exception is an immutable digest-allowlisted bundle, which is NOT built.
   Record: `PLANS.md` §0.8.
2. A content digest of the four policy artefacts (`action-approvals.yaml`, `L0-core.yaml`,
   `_hard_frozen.yaml`, the tenant overlay) is logged at boot and exposed on `/readyz`, so an
   operator can compare deployed policy against the reviewed commit. Precedent: the AMH
   migration-digest / ratification-YAML pattern (`platform/integrations/amh_inbox.py:894-901`,
   gated by `spec/policies/amh/inbox-ratification.yaml`) and `verify_amh_contract_pin.py`.

### 5.8 What this design does NOT do (deliberate non-goals)

* Does not move, weaken, or refactor any L0-hard guard, the BPMN-error allowlist, the no-denial
  structural enforcement, or `start_process_idempotent`'s internals (I-6).
* Does not flip anything. Ships fully inert (I-7).
* Does not classify the ~80 unmapped worker topics — `action-approvals.yaml:350-353` states that
  classifying them is a human decision, "não inferência de agente". This design preserves that and
  makes the shadow telemetry the worklist, exactly as `:358-360` intends.
* Does not fill any approval block. `action-approvals.yaml:47`: "NO AGENT MAY FILL ANY BLOCK BELOW."

---

## 6. STEP 6 — Action-class taxonomy for progressive enforcement

Five rungs, ordered by increasing harm-on-denial and increasing harm-on-permit. A class may only be
flipped when every class on a lower rung has been enforcing without incident for the agreed window.
`existing` = class already declared in `action-approvals.yaml`; `NEW` = must be added as data.

### 6.1 The ladder

| Rung | Class (manifest name) | Surfaces (re-derived) | L0-core action | Denial-mutation proof shape |
| --- | --- | --- | --- | --- |
| **C0 — inert / internal read** | `avaliacao_dmn` (NEW) | **ONE `self._dmn.evaluate` transport call site per graph**, behind a private `_evaluate_dmn` helper — rafael `graph.py:546-548`, marina `:804-806` (see R-7; helena/lucas/andre sites must be re-derived the same way — Scout A's numbers are helper call sites); worker DMN evals via `tools/workers/dmn_transport.py` | `query_decision_engine` L3 | Flip class → unapproved+enforcing in a test manifest; assert the node takes its **declared DMN-unavailable path** (ADR-0028 fail-closed → human), never a fabricated favourable outcome. No engine mutation, no audit row required. |
| | `consulta_processo` (NEW) | `cibseven.get_process_status` (`transport.py:403`), `find_active_instance` (`:265`), `find_any_instance` (`:294`); worker `cancel.resolve_facts` / `inadimplencia.resolve_facts` | `query_process_status` L3 | Denied read must **not** be readable as "no active instance" — the anti-dupla-terminação query must fail closed (skip), matching today's missing-engine behaviour. This is the highest-risk C0 item and must be proven explicitly. |
| **C1 — outbound notification** | `comunicacao_beneficiario` (existing, `:248-270`) | WhatsApp sends: fernando `graph.py:520`, helena `:712`, lucas `:548,:594`; the live `_ScopedWhatsAppSender` (`dispatch.py:100-118`); leaf `mcp_whatsapp/server.py:111`; workers `programa.proactive_contact`, `lgpd.send_response`, `lgpd.request_additional_proof` | `send_beneficiary_message` L3 / `send_beneficiary_template` L2 | Denied send → the turn ends on its declared escalation/HITL path, the beneficiary is not silently dropped, and **no raw recipient appears in any log line** (I-3). Worker side: audited refusal + incident, `retries=0` (`harness.py:1635-1651`). |
| **C2 — PHI read / model egress** | `leitura_phi_clinica` (existing, `:308-325`) | FHIR reads ×8: rafael `graph.py:348,:355`; andre `:749`; beatriz `:369`; carolina, gustavo, valentina via their adapters; leaves `mcp_fhir/server.py:88,:118` | `read_phi_data` L3 | Denied read degrades to the **disclosed gap note** every graph already documents (`rafael/graph.py:44-48`) — never a fabricated fact, never a blocked routing decision. Assert zero PHI in the denial telemetry. |
| | `leitura_populacional` (NEW) | andre `graph.py:708` (actuarial_risk), `:720` (population_metrics), Protocol at `:316` | (needs a name — see Q-4; nearest is `read_phi_data`, but k-anon aggregates are not PHI) | Denied → cohort dossier records an explicit gap; k-suppression posture unchanged. |
| | `inferencia_llm` (NEW, split by zone) | 15 call sites via `self._llm.generate(prompt, phi=…)`; provider `runtime/inference.py:1021-1033` (= `InferenceProvider.generate` signature + `phi:` arg doc), `PhiZoneRoutingError` | (none today — Q-4) | Denied → the node's existing LLM-unavailable path. Must prove the `phi=True` zone routing still fail-closes independently of the PEP (I-6). |
| **C3 — engine mutation** | `inicio_processo_regulatorio` (existing, `:272-306`) | agent starts ×9 (§0.1 list) + worker starts (contas/adequacao/fraude/inadimplencia/nip/ans_cron) | `start_compliance_process` L2 | Denied → **no instance exists in the engine**, an audited refusal row exists, and the external task lands as an incident with `retries=0`. This is the incident-shape live proof (§9.3). Also the first place the **per-agent `process_keys`** layer bites (R-1 closure). |
| | `correlacao_processo` (NEW) | `cibseven.correlate_message` (`transport.py:370`; Protocol `:151`) — today reachable raw from every graph | `correlate_process_message` L2 | Denied → no message correlated; the process waits at its receive task; incident visible. |
| | `delegacao_a2a` (existing, `:327-341`) | `a2a/dispatcher.py:277` `delegate`; edges `a2a_composition.py:266,:357`; worker dossier seams (`adequacao.py:916`, `credenciamento.py:776`, `pagto.py:1094`, cancel) | (none today — Q-4) | Denied → the delegating side degrades exactly as it does when the dossier seam is absent (documented "degrade gracefully"), never a fabricated dossier. |
| **C4 — adverse / money / regulatory** | `autorizacao_emissao` (existing `:129-141`) · `negativa_notificacao` (`:143-159`) · `submissao_regulatoria_ans` (`:161-183`) · `pagamento_emissao` (`:185-203`) · `vinculo_contratual_mudanca` (`:205-228`) · `acusacao_fraude_registro` (`:230-246`) | the 20 worker topics already mapped at `action-approvals.yaml:363-395` | `authorization_approval` L2 · `authorization_denial` L0-hard · `ans_official_submission` L1 · `high_value_payment` L1 · `contract_termination` L0-hard / `provider_decredentialing` L1 · `fraud_accusation` L0-hard | **Two-sided proof.** (a) Denied → audited refusal + fail-closed incident + human route. (b) **PEP neutralized → the L0-hard `NOT_HUMAN` guard still refuses** (I-6, defense in depth). Both directions are required before any C4 flip. |

### 6.2 Worker-side coverage note

All ~110 `operadora.*` topics plus the 6 `regulatorio.anssubmit.*` topics already traverse
`harness._handle` `:1602`. Rung assignment for the ~80 currently-unmapped topics is **not made
here** — `action-approvals.yaml:350-353` reserves it for a human. What this design adds is that the
shadow telemetry now covers agent seams too, so the worklist the manifest describes at `:358-360`
becomes complete.

### 6.3 The "unmapped" default is the hardest governance question

Under `modo: enforcing` today, an unmapped topic DENIES (`REASON_ACTION_UNMAPPED`,
`action_execution.py:647`; manifest `:355-357` states this is intentional and cites XRD-09's "ação
desconhecida … negam"). With the agent side choked, "unmapped" would also cover every seam
operation not yet catalogued. Flipping globally with an incomplete map is a platform outage.
Hence §7.3's per-class enforcement dimension and Q-2's explicit ramp decision.

---

## 7. STEP 7 — Data-model changes (all additive; the flip stays a data act)

### 7.1 `mapeamento_acoes` (new manifest section)

`mapeamento_topicos` stays **byte-unchanged** (26 entries, `:362-395`) and keeps its meaning:
external-task topic → class. A sibling `mapeamento_acoes` maps agent-side action refs
(`agente.fhir.read_patient`, `agente.whatsapp.send_message`, …) → class. The loader merges them
into one `action_ref -> class` map and **refuses the whole manifest on a collision** — the same
posture as `_RefusingDuplicatesLoader` (`action_execution.py:342-391`): a governance record may not
be silently shadowed. Both namespaces already satisfy `_is_bounded_topic`'s dotted-token regex
(`:727-729`), so telemetry needs no change.

### 7.2 `superficies[].choked` becomes an observed fact

Five entries currently read `choked: false` (`:265`, `:301`, `:317`, `:320`, `:336`). Phase 0 turns
all five true **because the wiring landed**, and the fence test must assert the field matches
reality (today the loader never reads it — `:119-125` calls it documentation). Making it verifiable
is what lets an approver trust `I-10`.

### 7.3 Per-class enforcement — the mechanism that makes the flip PROGRESSIVE

Today `modo` is global (`:99`) and `Decision.enforced` is `mode == MODE_ENFORCING`
(`action_execution.py:174-181`). Flipping it with one class approved would DENY every other class
and every unmapped topic. **Progressive enforcement by action class is therefore impossible with
today's data model** — this is a load-bearing gap, not a nicety.

Additive fix, two fields, zero new semantics:

```yaml
modo: shadow                      # UNCHANGED global ceiling: shadow here => nothing enforces, ever
enforcement_padrao_nao_mapeado: shadow   # NEW root key; terminal state must be `enforcing` (XRD-09)
acoes:
  inicio_processo_regulatorio:
    enforcement: shadow           # NEW per-class: shadow | enforcing (absent => shadow)
    …
```

`Decision.enforced` becomes `global modo == enforcing AND class enforcement == enforcing`
(unmapped refs use `enforcement_padrao_nao_mapeado`). Properties preserved:
* a global `shadow` still means **nothing** enforces (the safe direction, `:87-88`);
* every typo still resolves to a non-enforcing state (`:90-95`);
* `status: DRAFT` still makes every ALLOW unreachable (`:467-475`);
* approval remains all-three-domains, set-equality enforced (`:290-298`).

**Deviation to disclose:** `enforcement_padrao_nao_mapeado: shadow` during the ramp is a temporary,
explicit deviation from XRD-09's literal "ação/política desconhecida … negam" (`0037-…:154`). It
must be flipped to `enforcing` as the terminal act of the rollout, and the manifest must say so in
its own comments. This is Q-2 and it belongs to the humans.

---

## 8. STEP 8 — CI static fence spec

New gate `scripts/ci/check_effect_chokepoint_fence.py`, built exactly like
`check_start_process_fence.py`: pure `scan_tree(src_dir)` core + thin `main`, unparseable file =
fail-closed violation (`:106-116`), non-vacuity counter (`:155`, `:163`), wired into
`.github/workflows/ci.yml` next to `:110-111` via a `make` target. The existing start-process gate
stays **unchanged and separate** (it protects a different invariant and its passing history is
evidence).

### 8.1 Reject: construction of a raw effect class outside the registry

`ast.Call` whose resolved name is any of:

`CibSevenHttpTransport`, `FreshClientCibSevenTransport`, `CibSevenDmnTransport`, `FhirServer`,
`WhatsAppServer`, `AnthropicInferenceProvider`, `InferenceProvider`, `AioKafkaEventsProducer`,
`FactProducer`, `DelegationDispatcher`, `build_dispatcher`, `RealAnsGatewayTransport`,
`FhirServerReader`, `WhatsAppServerSender`, `ValentinaFhirServerReader`, `LucasWhatsAppServerSender`

**Allowlist (relative to `src/maezo`):** `gateway/tool_registry.py`, `gateway/seams/*.py`, plus each
class's own defining module (a `def`/`class` is not a `Call`, so definitions never trip it — the
same reasoning as `check_start_process_fence.py:28-29`).

*Current would-be violations, all intended targets:* `runtime/agent_runtime/service.py:305,:306,
:316,:321,:334,:336,:342,:348,:356,:364`; `runtime/agent_runtime/a2a_composition.py:242,:340`;
`platform/webhooks/service.py:103-108`; `platform/integrations/notifications_bridge.py:324-325`;
`runtime/worker_runtime/service.py:306,:326,:348,:594,:604,:605`.

### 8.2 Reject: a hand-rolled transport

* `ast.Call` named `AsyncClient` / `Client` where the attribute chain roots at `httpx`, outside an
  allowlist of the five modules that legitimately own an HTTP client today
  (`tools/mcp_cibseven/transport.py`, `tools/mcp_fhir/server.py`, `tools/mcp_whatsapp/server.py`,
  `tools/workers/dmn_transport.py`, `tools/workers/ans_gateway.py`) — mirroring
  `FORBIDDEN_PATH_SUBSTRING` (`check_start_process_fence.py:72`) but on the constructor.
* `ast.Constant` string literals containing a known effect REST path fragment
  (`/message/`, `/Patient/`, `/messages` for the Graph API, `/decision-definition/key`), outside the
  same allowlist. Keeps the existing `process-definition/key` rule untouched in its own gate.

### 8.3 Reject: policy-plane imports and env reads outside the gateway

* Any `ast.Import`/`ast.ImportFrom` of `maezo.tools.mcp_whatsapp`, `maezo.tools.mcp_fhir`,
  `maezo.tools.mcp_cibseven.transport` (names other than the Protocols/value types),
  `maezo.tools.workers.dmn_transport`, `maezo.runtime.inference` (concrete providers) outside the
  registry + the adapters that legitimately wrap them + tests.
* Any `os.environ` read of `MAEZO_SPEC_DIR`, `MAEZO_ACTION_APPROVALS_PATH`,
  `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT` outside `maezo/gateway/` and
  `maezo/agents/__init__.py` (A-6).

### 8.4 Reject: test doubles reachable from a production composition root

Any import of a name matching `Fake*`, `*Mock*`, `Noop*` inside the composition-root module set
(`runtime/agent_runtime/*.py`, `runtime/worker_runtime/*.py`, `platform/webhooks/service.py`,
`platform/integrations/notifications_bridge.py`, `gateway/tool_registry.py`). Known intentional
exception to declare explicitly, not silently: `_NoopKafkaProducer`
(`a2a_composition.py:186-195`) is the documented default for agent-runtime facts — allowlist it by
name with the rationale, never by pattern.

### 8.5 Non-vacuity and completeness assertions (the gate must prove it is doing work)

1. The registry constructs **every** class in §8.1's list at least once (mirrors `fence_called_in`,
   `check_start_process_fence.py:155,:177-178`).
2. Every `mcp-<server>.<action>` tool id declared in any `spec/agents/*/agent.yaml` resolves to an
   operation in `effect_classes.py`'s catalogue — extending
   `platform/validation/agent_def.py:64-75` from "server exists" to "operation exists".
3. Every operation in the catalogue maps to an action class present in `action-approvals.yaml`
   `acoes`, and every `superficies[].choked` value matches whether that surface's operation is in
   the catalogue (§7.2).
4. Every class has a declared denial shape and at least one mutation test (§6.1) — a class with no
   mutation proof fails the gate.

---

## 9. STEP 9 — Rollout

### 9.1 Phase 0 — build inert (agent-executable, no human gate)

Ship the registry, wrappers, catalogue, fence, boot assertion, and the two additive manifest fields
with **every** class `enforcement: shadow` and global `modo: shadow`, `status: DRAFT`. Exit criteria:

* **Parity, per seam.** Gateway-neutralized vs gateway-live produce identical outcomes — the exact
  proof shape already used for the worker path (`harness.py:1583-1585`).
* **Telemetry, per class.** Every class emits `action_execution_gateway_shadow` lines with a
  bounded, non-PHI payload; `choked: false` is gone from the manifest (§7.2).
* **Fence green and non-vacuous** (§8.5).
* **R-5/R-6 documentation corrections landed** (stale `transport.py:560` refs; the deleted
  `CibSevenServer` enforcement claim at `transport.py:11,:19,:348`).
* **No flip attempted. No approval block touched.**

### 9.2 Phase 1 — the shadow evidence packet (input to Médica / ANS / Security)

Extends `docs/reviews/mzo-040-approval-packet.md`. Per action class:

1. Observation window and volume: line counts by `decision` × `reason` × `tenant` (all bounded
   tokens — the packet contains no PHI by construction).
2. **The would-deny list**: which real traffic would have been blocked, and at which layer
   (capability / autonomy / ceiling / consent / ratification). An approver must see whether a flip
   blocks work or blocks nothing.
3. Unmapped-ref census — the worklist the manifest anticipates at `:358-360`.
4. The declared denial shape per class + a pointer to its passing mutation test (§6.1).
5. The defense-in-depth proof for C4: the L0-hard guard refuses with the PEP neutralized (I-6).
6. Disclosed residuals, named: C-B2 (a node-local raw client evades the seam assertion), A-10
   (cache/restart rollback latency), the XRD-10/MZO-060 claim-vs-start atomicity window
   (`transport.py:1112-1117`), and — if unclosed — A-6/`MAEZO_SPEC_DIR`.
7. **Governance precondition, stated up front:** PLANS §0.8 Onda 1 requires
   "rulesets/branch protection ATIVOS ANTES do aceite da ratificação", because the manifest is only
   trustworthy with server-side enforcement. The current absence is recorded at
   `action-approvals.yaml:51-57` and in evidence-ledger row `mzo-000`. **Owner action, 10 minutes,
   blocks acceptance.**

### 9.3 Phase 2 — the incident-shape live proof (awaits the owner's "go")

One class, `inicio_processo_regulatorio`, on a **local CIB Seven** with a test manifest, must
demonstrate, live:

* the external task is refused with `retries=0` and produces a **visible engine incident**
  (`harness.py:1647-1651`);
* the audited refusal row exists **before** the refusal is reported
  (`_audit_guard_refusal:1545-1553`), chain-linked;
* **no process instance was created** — verified against real engine history, not a mock;
* the refusal record and every log line carry only bounded tokens;
* the demoted-variables path behaves as documented (`harness.py:1706-1719`);
* with the PEP neutralized, the independent guard still refuses (I-6).

Local-engine iteration per the recorded ops lesson (~2 min/suite vs ~1.5 h blind CI).

### 9.4 Phase 3 — per-class flips (HUMAN, data-only)

For each class, in rung order C0 → C1 → C2 → C3 → C4:
1. Approvers fill their three domain blocks per `action-approvals.yaml:31-45` (a partial fill is
   refused, `:39-41` / loader `:247-269`).
2. `status: RATIFICADO` (once, at the first flip).
3. `modo: enforcing` (once, at the first flip — it is the ceiling, not the switch).
4. Set that class's `enforcement: enforcing`. **This is the per-class switch.**
5. Verify against telemetry: event name must become `action_execution_gateway_enforced` and `mode`
   must read `enforcing` — the manifest's own warning at `:90-95` exists because a failed flip looks
   identical to a working shadow deployment from the outside.
6. Soak for the agreed window; only then advance a rung.
7. **Terminal act:** `enforcement_padrao_nao_mapeado: enforcing`, restoring XRD-09 literally.

### 9.5 Rollback

Set the class's `enforcement: shadow` (data, one line). Effective on restart because of the
`lru_cache` (`action_execution.py:578-593`) — see Q-7. Global panic switch: `modo: shadow` disables
enforcement for every class at once, in one line, with no code change. No rollback path requires a
code change or a redeploy of the gateway module — which is the property the whole MZO-040 design
was built to have (`action_execution.py:17-23`).

---

## 10. Open questions — for the owner and the human approvers (I must not decide these)

**Q-1 — Per-class enforcement field.** Does adding `acoes.<class>.enforcement` (§7.3) satisfy
governance, or must each per-class flip be its own reviewed PR against the manifest? *(Recommend:
the field, plus branch protection + required CODEOWNERS so each flip IS a reviewed PR.)*

**Q-2 — The unmapped-ref default during the ramp.** XRD-09 says unknown denies (`0037-…:154`).
Enforcing that from day one, with ~80 unmapped topics and a fresh agent-seam namespace, is a
platform outage. Approve `enforcement_padrao_nao_mapeado: shadow` as an explicit, time-boxed
deviation with a mandated terminal flip? Who owns the deadline?

**Q-3 — ADR-0034's L2 sampling clause (I-12).** ADR-0034 `:78-85` requires `L2ReviewSampler` + a
concrete persistent `ReviewQueue` to be built **as part of** the chokepoint, plus "uma nova ADR
ratificando a re-introducao". Build in Wave 1, or amend the clause in the new ADR with reasons?
*(Note: ADR-0037 XRD-09 ratifies the CHOKEPOINT, not the sampler — the clause is not
self-discharging.)*

**Q-4 — Missing L0-core action names.** Three seams have no ratified autonomy action:
LLM inference, A2A delegation, and population/actuarial reads (`L0-core.yaml` has no entry for any
of them). Add them to the matrix — which is an ADR-0008/0025 vocabulary change requiring the
`_hard_frozen.yaml` cross-check — or route them under existing names? Vocabulary decisions are
human (`pep.py:12-18`: "no PT↔EN alias layer — an alias map is a second vocabulary and a fail-open
surface").

**Q-5 — The consent leg (L-4).** XRD-09 names consent explicitly (`0037-…:152-153`). Today
`ConsentDecisionSource` exists only as a port (`src/maezo/ports/consent.py`) with no adapter, and
MZO-070 is gated on the AMH consent contract. Ship L-4 declared-but-unwired (a class flagged
`consentimento_exigido: true` DENIES until an adapter exists — honest and fail-closed), or defer the
leg entirely and record XRD-09 as partially implemented?

**Q-6 — Close the `MAEZO_SPEC_DIR` bypass (A-6). ANSWERED 2026-08-12 — FAIL-CLOSED.** Refuse
enforcement (or refuse to load) when policy artefacts resolve through `MAEZO_SPEC_DIR` in
production mode? This is a security decision with a deployment blast radius; the alternative is
that one env var silently substitutes the entire policy plane once enforcement is live.
*Owner's ratification, verbatim:* "Ratifique Q-6 como fail-closed: produção deve recusar
`MAEZO_SPEC_DIR`, permitindo-o apenas em runtime local explícito; remova o companion bypass e
aceite futuras exceções somente por bundle imutável com digest permitido." Implemented per §5.7
item 1; recorded in `PLANS.md` §0.8.

**Q-7 — Manifest cache TTL vs rollback latency (A-10).** Accept "rollback = restart", or add a
bounded TTL re-read that never widens permission on a failed re-read? SRE/owner call.

**Q-8 — Per-tenant policy.** `action_approvals` is cached by path only (`:578-593`) and the manifest
has no tenant dimension, while `PEP` is per-tenant (`build_pep(tenant=…)`) and overlays exist
(`tenants-amh.yaml`). Is one global ratification record correct for a multi-tenant deployment, or
does ratification need a tenant axis?

**Q-9 — Latency budget sign-off (I-9).** Confirm ≤ 1 ms p99 added per gated call with **zero** added
network I/O on read classes; and confirm that any class flagged `audita_antes: true` (which adds a
durable write before the effect) is acceptable on the engine path.

**Q-10 — Médica-specific: should PHI reads be gated at all in year one?** A denied FHIR read
degrades to a dossier gap note (`rafael/graph.py:44-48`), which routes more cases to a human. That
is safe but increases human load. Médica should decide whether C2 belongs in the ramp or stays
shadow-only indefinitely.

**Q-11 — Per-agent `process_keys` enforcement re-opens a recorded SME question.** Enforcing L-1
means Rafael can start only `SP-OP-AUTH-001` (`spec/agents/rafael/agent.yaml`), which is strictly
narrower than today's global `DEFAULT_ALLOWED_PROCESS_KEYS` (all 15,
`process_allowlist.py:60`). The comment block at `process_allowlist.py:46-59` records an SME
recommendation to restrict agent-startable keys to `{SP-OP-ESCALATION-001, SP-OP-LGPD-DSR-001}` and
deliberately leaves it undecided. Closing W1 forces the question. **Owner/SME.**

**Q-12 — Owner action, blocking.** Branch protection + required CODEOWNERS on `main`
(PLANS §0.8 Onda 0/1; `action-approvals.yaml:51-57`; evidence-ledger `mzo-000`). Ratification
acceptance should not be scheduled before this lands.

---

## Appendix A — Anchor index (every file:line cited, re-derived 2026-08-10)

`gateway/action_execution.py`: 17-23, 31-34, 38-44, 56-57, 144-146, 150-152, 174-181, 198-207,
236-239, 247-269, 290-298, 305-323, 326-328, 342-391, 394-396, 399-457, 459-516, 519-575, 578-593,
596-659, 662-664, 676-711, 714-729 ·
`gateway/pep.py`: 12-18, 71, 336, 391, 412-450 ·
`tools/workers/harness.py`: 79-81, 152, 895, 1022, 1048, 1132, 1204, 1259, 1353, 1362, 1396,
1492-1518, 1528-1572, 1576-1600, 1602-1652, 1659-1665, 1706-1719 ·
`tools/mcp_cibseven/transport.py`: 11, 19, 138-186, 248, 265, 294, 341, 348, 370, 403, 472-565,
652-684, 687, 855-886, 898, 933, 951, 1052-1202 ·
`tools/workers/cibseven_engine.py`: 4-7, 59-130 ·
`tools/workers/ans_gateway.py`: 160-196, 198-247, 249-283, 286-299 ·
`tools/process_allowlist.py`: 21-42, 46-59, 60, 63, 66-79, 120-145, 149-164 ·
`tools/mcp_fhir/server.py`: 36, 88, 111, 118, 141 · `tools/mcp_whatsapp/server.py`: 39, 111, 145 ·
`runtime/agent_runtime/service.py`: 40-42, 132-270, 288-365, 386-390, 441-548 ·
`runtime/agent_runtime/a2a_composition.py`: 13-24, 74, 186-195, 222, 242, 254, 266, 340, 346, 357 ·
`platform/webhooks/service.py`: 50-60, 88-127 ·
`platform/webhooks/whatsapp/dispatch.py`: 100-118, 123-145, 147-176, 177-198 ·
`platform/integrations/notifications_bridge.py`: 309-330 · `platform/notification_bridge.py`: 1034 ·
`platform/validation/agent_def.py`: 41-75 · `runtime/harness.py`: 45-72, 121-179 ·
`runtime/inference.py`: 441, 616-628 · `agents/__init__.py`: 65-98, 131-164 ·
`agents/rafael/graph.py`: 39-48, 98-104, 348, 355, 509, 546-548, 657-694 ·
`agents/marina/graph.py`: 105, 704, 804-806 · `agents/lucas/graph.py`: 548, 594, 639 ·
`agents/fernando/graph.py`: 520, 560 ·
`agents/helena/graph.py`: 87, 144, 673, 712, 760, 796, 866 ·
`agents/beatriz/graph.py`: 75-95, 369, 430-432 · `agents/andre/graph.py`: 71-72, 139-147, 274-330,
598, 708, 720, 749, 818, 852, 891, 986, 1324 · other graph starts: valentina 648, carolina 626,
gustavo 697 ·
`ports/__init__.py`: 1-52 · `scripts/ci/check_start_process_fence.py`: 6, 28-29, 61-83, 106-188 ·
`.github/workflows/ci.yml`: 59-61, 103-104, 110-111, 118-119 ·
`spec/policies/autonomy/action-approvals.yaml`: 31-45, 47, 51-57, 59-65, 73, 87-99, 104-125,
129-341, 343-395 · `spec/policies/autonomy/L0-core.yaml`: 5-47 ·
`spec/agents/rafael/agent.yaml` (whole) · `spec/agents/helena/agent.yaml`: 7-36 ·
`docs/adr/0034-…`: 22-28, 30-38, 40-44, 68-85, 87-96 ·
`docs/adr/0037-…`: 138-141, 142-146, 147-149, 151-157, 159-163, 170-173, 203-214, 239-244, 258-261,
280-281 · `docs/adr/0006-phi-two-zones.md`: 1-20 · `PLANS.md`: 262 (W1), 274-297 (Ondas 0-3).
