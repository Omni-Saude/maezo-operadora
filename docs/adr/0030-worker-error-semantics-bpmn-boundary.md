# ADR-0030: Worker error semantics vs BPMN error-boundary catches — modeled `WorkerBpmnError`, incident everywhere else [T3.1]

**Status:** Proposed (2026-07-18) · **Data:** 2026-07-18 · **Area:** Orquestracao (runtime spine)

> Draft for the orchestrator/gatekeeper's ratification. Ground rule 2 (author ≠ verifier): authored by
> process-engine-architect (R1); must be R1-verified before it moves to Accepted. Every citation below
> was re-pinned against `main = e4e3ed1`. **Docs-only:** this ADR changes no `src/`, `tests/`, or
> `spec/` file; it *decides* and *schedules* the migration, which later tasks implement.

## Contexto

### The defect class, as it actually is (the brief's framing corrected against the tree)

The T3.1 brief describes the defect as: *"`FunctionWorker.execute` reclassifies domain exceptions into
`ValueError` → `failure(retries=0)` → incident, so every BPMN error boundary event modeled on external
tasks can NEVER fire — dead code — and the phase-2 suites document this with strict xfails."* Verified
against the tree, that framing is **partly wrong in three load-bearing ways**, and the ADR is grounded
in the corrected reality (hard rule: ground in repo, report the discrepancy):

1. **The modeled-boundary mechanism already exists.** A worker signals a BPMN error by raising
   `WorkerBpmnError(error_code)` (`src/maezo/tools/workers/harness.py:129-138`). The harness's dispatch
   ladder (`harness.py:883-965`) catches it (`:916`) and, **iff** the code is in the harness's
   `bpmn_error_allowlist` (`:917`), calls `transport.handle_bpmn_error(...)` (`:919-924`) — a real
   engine `bpmnError` with `errorCode`, which fires the modeled boundary catch. Three families already
   raise it: `auth.py:380` (`ERR_AUTH_DENIAL_INCOMPLETE`), `cancel.py:357,367`
   (`ERR_CANCEL_MANTER_NOT_HUMAN`), `events.py:262` (`ERR_EVENT_PUBLISH_FAILED`). So Option A is not a
   greenfield proposal — it is **already the partially-built architecture**; the decision is whether to
   *complete* it or *retreat* from it.

2. **`FunctionWorker` does not reclassify `WorkerBpmnError`.** `FunctionWorker.execute`
   (`base.py:284-294`) reclassifies to `ValueError` **only** exceptions that are (a) not in
   `_HARNESS_CLASSIFIED` (`base.py:233-241`: `PermissionError`/`ValueError`/`RuntimeError`/`OSError`/
   `TimeoutError`/`ConnectionError`) **and** (b) expose *string* `.code` **and** *string* `.message`
   (`base.py:290-293`). `WorkerBpmnError` has neither `.code` nor `.message` (only `.error_code`), so it
   passes through **untouched** — a `FunctionWorker`-wrapped function *can* raise a `WorkerBpmnError` and
   reach `handle_bpmn_error` (the `cancel` family does exactly this: `confirm_maintained_decision`,
   `FunctionWorker`-registered at `cancel.py:743`, raises `WorkerBpmnError` at `:357`/`:367`).

3. **The real, systemic blocker is not the reclassification — it is the empty production allowlist plus
   the missing boundary-proof gate.** The runtime daemon constructs the harness with
   `bpmn_error_allowlist=frozenset()` (`runtime/worker_runtime/service.py:288-301`, the empty set at
   `:300`, with the explicit comment *"No bpmnError code is gate-proven yet (T1.1 §9 open Q-3 …); every
   `WorkerBpmnError` demotes to a fail-closed incident until a code is added here with proof"*). With an
   empty allowlist, **every** `WorkerBpmnError` — auth, cancel, events — is demoted to
   `failure(retries=0)` in production (`harness.py:925-943`). `AUTH_BPMN_ERROR_ALLOWLIST` (`auth.py:60`)
   exists but is **never imported into `service.py`** — it is used only by the auth integration test
   (`tests/integration/processes/test_sp_op_auth_001.py:312`). The CI-side "boundary-proof gate" that
   would compute the allowlist from `spec/**` and prove every raised code has a matching boundary
   (harness.py:48-51; ADR-0026 §5; T1.1 design §9) is **not implemented** — still a TODO.

So there are **two** ways a modeled boundary catch on an external task is dead code today, and the
migration must address both:

- **Layer 1 — no signal.** The nine `FunctionWorker`-exclusive families (programa, credenciamento,
  fraude, pagto, adequacao, contas, recurso, inadimplencia — plus nip/reembolso/ans_submit) raise
  **coded domain exceptions** for their modeled business-outcome errors, never `WorkerBpmnError`. Those
  demote to an incident by one of two paths, both terminating at `harness.py:952-954` (`except
  ValueError → failure(retries=0)`):
  - `.code/.message` `Exception` subclasses (adequacao/credenciamento/fraude/inadimplencia/pagto/
    programa — named in `base.py:255-269`) → `base.py:289-293` reclassifies to `ValueError`. E.g.
    `pagto.py:70` raises `PagtoError(ERR_PAGTO_ORDEM_INVALIDA, …)` (class `pagto.py:347`, `.code`
    `:351`); `programa.py:55` raises `ProgramaError(ERR_PROGRAMA_NO_CONSENT, …)`.
  - typed-I/O modules (nip/recurso/reembolso/ans_submit, ADR-0026 §census) whose errors subclass
    `ValueError`/`PermissionError` directly → the harness's own `except ValueError`/`except
    PermissionError` branch. Either way the `errorCode` never reaches the engine as a `bpmnError`.
  None of these eight families even import `WorkerBpmnError` (grep = 0).
- **Layer 2 — signal raised but not gate-proven.** The three families that *do* raise `WorkerBpmnError`
  (auth/cancel/events) still demote to incident in production because the allowlist is empty
  (`service.py:300`).

### The BPMN error-boundary catches — the heart of this ADR

Enumerated across `spec/processes/bpmn/**` (`bpmn:boundaryEvent` containing a
`bpmn:errorEventDefinition`, attached to a `bpmn:serviceTask` with `camunda:type="external"`). **24
error-boundary catches on external tasks, across 12 process families.** (Brief discrepancy:
adequacao / contas / fraude have **zero** such catches — they carry only timer/message boundaries or
throw-side error ends; and there are catches in families the brief's list omitted: auth, ans, cancel,
escalation, nip, reembolso.)

| Family | Process | External task — `camunda:topic` | `errorCode` | Boundary → routes to | Worker raises it today? | Group |
|---|---|---|---|---|---|---|
| auth | SP-OP-AUTH-001 | `operadora.auth.send_denial_notice` | `ERR_AUTH_DENIAL_INCOMPLETE` | `BE_NegativaIncompleta` → `End_FundamentacaoIncompletaBloqueada` | **YES** (`auth.py:380`) | **G1** |
| cancel | SP-OP-CANCEL-001 | `operadora.cancel.confirm_maintained_decision` | `ERR_CANCEL_MANTER_NOT_HUMAN` | `BE_ManterNaoConfirmado` → `End_ManterNaoConfirmado` | **YES** (`cancel.py:357,367`) | **G1** |
| escalation | SP-OP-ESCALATION-001 | `operadora.events.publish` (×5 tasks) | `ERR_EVENT_PUBLISH_FAILED` | fail-safe routes (BRT/service/user/gateway/end) | **YES** (`events.py:262`) | **G1** |
| escalation | SP-OP-ESCALATION-001 | `operadora.escalation.notify_team` / `notify_supervisor` (×3 tasks) | `ERR_ESC_NOTIFY_FAILED` | fallback → supervisor → user task | NO (Kafka/notify gap) | G2-fs |
| ans | SP-OP-ANS-SUBMIT-001 | `regulatorio.anssubmit.submit` | `ERR_ANS_PROTOCOLO_NACK` | `BE_SubmitNack` → `SUB_RetryEnvio` (retry) | NO (coded → incident) | G2-fs † |
| cred | SP-OP-CRED-001 | `operadora.cred.verify_credentials` | `ERR_CRED_INVALID_PRESTADOR` | → `End_CredPrestadorInvalido` | NO | G2-val |
| cred | SP-OP-CRED-001 | `operadora.cred.register_descredenciamento` | `ERR_DECRED_NOT_HUMAN` | → `End_DecredBloqueadoNaoHumano` | NO | G2-guard |
| cred | SP-OP-CRED-001 | `operadora.cred.register_cred_denial` | `ERR_CRED_DENIAL_NOT_HUMAN` | → `End_CredGuardBloqueadoNaoHumano` | NO | G2-guard |
| inadimplencia | SP-OP-INADIMPLENCIA-001 | `operadora.inadimplencia.register_contract_suspension` | `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | → `End_SuspensaoBloqueadaNaoHumano` | NO | G2-guard |
| lgpd | SP-OP-LGPD-DSR-001 | `operadora.lgpd.verify_identity` | `ERR_DSR_IDENTITY_UNVERIFIED` | → `ST_PublishIdentidadeInverificavel` | NO (WorkerBase, no raise) | G2-val |
| nip | SP-OP-NIP-001 | `operadora.nip.handoff_ans_submit` (×3 tasks) | `ERR_NIP_PROTOCOLO_INVALIDO` | → `End_NipProtocoloInvalido` | NO | G2-val |
| pagto | SP-OP-PAGTO-001 | `operadora.pagto.validate_payment_data` | `ERR_PAGTO_ORDEM_INVALIDA` | `BE_PagtoOrdemInvalida` → `End_PagtoOrdemInvalida` | NO (`pagto.py:70`) | G2-val |
| programa | SP-OP-PROGRAMA-001 | `operadora.programa.check_consent` | `ERR_PROGRAMA_NO_CONSENT` | → `ST_PublishConsentBlocked` | NO (`programa.py:55`) | G2-val |
| recurso | SP-OP-RECURSO-001 | `operadora.recurso.request_documents` / `analyze_request` (×2 tasks) | `ERR_RECURSO_INVALID_GLOSA` | → `End_RecursoGlosaInvalidaOrigem` | NO | G2-val |
| reembolso | SP-OP-REEMBOLSO-001 | `operadora.reembolso.check_coverage` | `ERR_REEMBOLSO_INVALID_PROTOCOLO` | → `End_ReembolsoProtocoloInvalido` | NO | G2-val |

**Per-family catch count:** escalation 8, cred 3, nip 3, recurso 2, auth 1, ans 1, cancel 1,
inadimplencia 1, lgpd 1, pagto 1, programa 1, reembolso 1 = **24**. **Distinct `(topic, errorCode)`
signals: 15.** Zero catches on `userTask`/`callActivity`; one error boundary on a non-external
`subProcess` (`ans` `BE_RetryEsgotado`, out of scope). † `ans`/`SP-OP-ANS-SUBMIT-001` is under active
**decommission** (DL-0028/DL-0029, RN 639/2025 extinguished the SIP obligation) — its catch is likely
mooted by that re-scope, not migrated.

**Group legend.** **G1** = worker already raises the correct `WorkerBpmnError`; needs only allowlist
wiring (+ gate). **G2-val** = origin/consistency validation catch (fail-safe, non-adverse: "the worker
never decides; it only signals bad-at-source data"). **G2-guard** = an L0 `*_NOT_HUMAN` guard that the
BPMN author modeled to a **neutral** terminal (blocks the adverse action, terminates cleanly — never
performs it). **G2-fs** = technical fail-safe (retry / notify-fallback). Every catch in the table was
*deliberately modeled* with a routed, non-adverse target — the presence of the boundary is itself the
process author's declaration that an incident is the **wrong** outcome here (e.g. `SP-OP-PAGTO-001`'s
own boundary documentation: *"Antes: sem captura, o incidente travava a mainline … Agora: captura →
`End_PagtoOrdemInvalida` (fail-safe NAO adverso)"*).

### Prior decisions this ADR extends or refines

- **ADR-0026 §Decisao 5** already chose this mechanism: *"`bpmnError` is a per-code opt-in allowed only
  where the T1.1 §9 boundary-proof gate shows a matching error boundary in every consuming BPMN; an
  unmodeled `bpmnError` silently ends the process with no incident (live-proven on CIB Seven 2.1.0) …"*
  It left **two** items open: the boundary-proof gate (unbuilt) and *which* codes are gate-proven (T1.1
  open Q-3). It also placed the classifier as a `classify_worker_error(exc)` helper "in the dispatcher";
  the tree instead put the coded-exception→`ValueError` reclassification **inside** `FunctionWorker`
  (`base.py:284-294`) and the `bpmnError`/guard/transient routing inside the **harness ladder**
  (`harness.py:916-965`). This ADR is grounded in the **as-built** split, not the ADR-0026 sketch.
- **ADR-0008 (autonomy L0):** an L0 hard action (negativa, descredenciamento, suspensão, fraud
  accusation) must **never** be performed automatically; retrying an L0 guard could drive one. This ADR
  preserves that invariant under both options (a modeled boundary routes to a **neutral** terminal /
  human queue — it never performs the adverse action).
- **ADR-0018 (no-denial structural, DL-0013):** negativa-like processes carry a fail-safe-to-User-Task
  structure with a worker guard `ERR_*_NOT_HUMAN`. The G2-guard catches are the *modeled* half of that
  structure; today the worker half fires an incident instead of the modeled neutral terminal.
- **ADR-0007 (non-repudiation) + the audit-emit wave.** PR #97 (branch `t1.10-audit-emit-harness`, task
  T-C, **OPEN**) wires the harness **success** path to emit a PHI-safe `AuditRecord` **before**
  `complete` (`docs/design/audit-emit-path-wiring.md` §4.2). The design **deliberately excludes** the
  `bpmnError`/`failure`/incident paths from audit scope (§2.1 row 2: *"control-flow signals to the
  engine … not world effects; already fail-closed to a human via incident"*), deferring them to task
  **T-E** ("Audited-refusal", R2). PR #97's own disclosure confirms failure/bpmnError/incident paths are
  unaudited by design today. This ADR must not contradict that deferral — and does not (see Decisao §4).

### The phase-2 xfail evidence — a second brief discrepancy, reported

The brief states the phase-2 suites *"document this with strict xfails"* that would flip under the fix.
Verified against the reconciler worktree (`…/agent-a41120b110d4b7c41/tests/integration/processes/`,
branch `t3.1-process-suites-phase2-land`): **zero of the 19 strict-xfails are this defect.** All 19
(cancel 8, auth 8, escalation 3) are the **separate "Kafka-producer gap"** (`_WORKER_KAFKA_GAP_REASON`,
`_ACTION_WORKER_KAFKA_GAP_REASON`, `_NOTIFY_KAFKA_GAP_REASON`) — entry functions / action workers never
call `kafka.publish`, so notification assertions can't observe an execution; the string `ValueError`
appears nowhere in those files. The modeled-boundary tests instead **PASS today** — but only because
each fixture wires a **test-only, non-empty** allowlist (`test_sp_op_cancel_001.py:244`
`frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"})`, `…auth…:312` `AUTH_BPMN_ERROR_ALLOWLIST`,
`…escalation…:227` `frozenset({"ERR_EVENT_PUBLISH_FAILED"})`) that production
(`service.py:300 frozenset()`) does not. Auth even **removed** a former strict-xfail on its boundary
path after it XPASSED against a real CIB Seven 2.1.0 engine. Consequence for the migration plan: **no
existing xfail flips** under either option; the honest testability delta is (i) the three G1 suites drop
their inline allowlist and assert against production wiring, and (ii) the G2 families gain new
boundary-catch tests (the remaining phase-2 suites) that today would be forced to assert *incident*.

### The tension

Every one of the 24 catches was modeled on purpose, with a non-adverse routed target, in an
ANS-regulated, fail-closed context. Yet in production all 24 are dead: 21 because the worker never
raises `WorkerBpmnError`, all 24 because the allowlist is empty. We must decide whether the intended
posture for a modeled worker error is a **routed BPMN outcome** (complete the half-built mechanism) or
an **incident** (retreat and delete the models), and draw a non-vague line for which errors are which.

## Decisao

**Adopt Option A — modeled `WorkerBpmnError` — with the modeled-vs-incident line drawn *exactly* at the
BPMN boundary-catch declaration.** This is the recommended option; it completes the mechanism ADR-0026
§5 already chose and that auth/cancel/events already build against, and it keeps every non-modeled
failure incident-fail-closed. In one sentence: **a worker raises `WorkerBpmnError(code)` if and only if
`code` is declared as a `bpmn:error@errorCode` on an error boundary event attached to that worker's
external task in *every* consuming process (proven by the boundary-proof gate); everything else —
unexpected faults, bad/immutable input, and L0 guards with *no* modeled boundary — stays
incident-fail-closed.**

This subsumes the only defensible part of a hybrid: the modeled-vs-incident criterion is not "case by
case" — it is a mechanical property of the spec (*"is there a matching boundary catch?"*), computed and
enforced by CI, not a per-error judgement call.

### 1. The exception contract

- A worker signals a **modeled business-outcome / fail-safe error** by raising
  `WorkerBpmnError(error_code, message="")` (`harness.py:129-138`) — the existing type, unchanged. The
  `error_code` **must** be a spec-declared boundary `errorCode` for that worker's topic (§2).
- A worker signals a **non-modeled failure** exactly as today: an L0 guard as `PermissionError`
  (`*NotHumanError`) → incident; bad/immutable input as `ValueError` → incident; a transient infra fault
  as `RuntimeError`/`OSError`/`TimeoutError`/`ConnectionError`/`httpx.HTTPError` → engine-computed retry.
  The harness ladder (`harness.py:944-965`) already routes these correctly; **no change**.
- `WorkerBpmnError` is **exempt from `FunctionWorker`'s reclassification** — it already is (it exposes no
  `.code`/`.message`, `base.py:290-293`). A `FunctionWorker`-wrapped entry function is a first-class
  place to raise it (cancel already does). No new base-class or dispatcher surface is needed.
- **Fail-closed by construction, preserved:** a `WorkerBpmnError` whose code is **not** gate-proven is
  demoted to `failure(retries=0)` with a loud log (`harness.py:925-943`), never a silent scope-end. So
  even a mis-modeled code degrades to an incident, never to the CIB Seven 2.1.0 silent-drop hazard
  (`harness.py:46`). Option A never weakens the fail-closed default; it *widens* the set of proven codes
  that route instead of incidenting.

### 2. errorCode taxonomy discipline (ADR-0026 §5, made enforceable)

- **Single source of truth = the BPMN.** The set of legal `WorkerBpmnError` codes is derived from
  `spec/processes/bpmn/**` — every `bpmn:error@errorCode` on an error boundary event attached to an
  external task — **never** a hand-maintained list (mirrors ADR-0026 §2b: topics come from spec, not a
  hand-list).
- **The boundary-proof gate** (T1.1 §9, the unbuilt follow-up — this ADR makes it a hard precondition,
  not an optional nicety): a CI static check that (a) computes the allowlist from spec; (b) fails if any
  worker raises a `WorkerBpmnError(code)` whose `code` is not spec-declared on that worker's topic's
  boundary in **every** consuming process; (c) fails if any spec-declared boundary `errorCode` on an
  external task has **no** worker raising it (the dead-model regression this ADR closes). The runtime
  `bpmn_error_allowlist` (`service.py`) is then populated **from the gate's output**, replacing
  `frozenset()`.
- Codes stay **stable and semantic** (`ERR_<DOMAIN>_<CONDITION>`), matching the existing convention;
  reuse across tasks sharing a topic is fine (nip's `ERR_NIP_PROTOCOLO_INVALIDO` ×3, recurso's
  `ERR_RECURSO_INVALID_GLOSA` ×2 already do this).

### 3. How the harness distinguishes a modeled bpmnError

No new distinction is needed — the existing ladder already is the distinction: `WorkerBpmnError` +
allowlist → `handle_bpmn_error` (real `bpmnError`, boundary fires); everything else → incident/retry
(`harness.py:916-965`). Option A's only runtime change is **populating the allowlist from the gate**
instead of leaving it empty. Because PR #97 (T-C) emits its audit record on the **success** path only
(after `handler(task)` returns, before `complete`), a task that raises `WorkerBpmnError` never reaches
the T-C emit — it lands in the `except WorkerBpmnError` branch — so **Option A does not create an
un-audited *world effect*** (the raise happens before any effect; handler-purity precondition P1,
audit-wiring design MUST-FIX 1, guarantees no mid-handler effect preceded it).

### 4. Audit reconciliation (ADR-0007 / T-E) — no contradiction, scheduled precisely

- **A modeled `bpmnError` transition is a control-flow routing signal, not an ADR-0007 "efeito no
  mundo."** The audit-emit design already classifies it so (§2.1 row 2) and defers it to T-E. This ADR
  **agrees** and does not require a synchronous audit at the bpmnError transition to be non-repudiation-
  complete: the routed target is either a **neutral terminal** (no adverse effect to record) or a
  downstream worker / human task whose *own* effects are audited at their own chokepoints (T-C for
  worker `complete`; HITL for user tasks). The forbidden direction — an engine world-effect with no
  audit row — remains structurally impossible.
- **The T-E deferral is currently safe** precisely because the allowlist is empty (`service.py:300`): no
  `bpmnError` fires in production today, so there is nothing to audit. Option A changes that. Therefore
  this ADR **schedules T-E as a hard co-requisite of populating the allowlist for any *business-outcome*
  code** — the G2-guard family (`ERR_*_NOT_HUMAN` blocking an adverse action) and the denial-block
  (`ERR_AUTH_DENIAL_INCOMPLETE`): before such a code goes live in the production allowlist, T-E must
  land so the *refusal/routing decision* ("this instance was blocked from denying / suspending and
  routed to a neutral terminal") is recorded per ADR-0007. For **technical fail-safe** codes
  (`ERR_EVENT_PUBLISH_FAILED`, `ERR_ESC_NOTIFY_FAILED`, and the `G2-val` origin-validation catches),
  T-E is desirable but **not** a hard blocker — they route to retry/fallback/technical terminals, not
  regulated outcomes. This respects the deferral (no contradiction) while closing the one gap Option A
  would otherwise open.

### 5. Refinement of ADR-0026 §5 (recorded, not a silent override)

ADR-0026 §5 states, in the same paragraph, both *"guard errors (`ERR_*_NOT_HUMAN`) → incident, never
`bpmnError`"* **and** *"`bpmnError` is a per-code opt-in where the boundary-proof gate shows a matching
boundary."* For a guard code that **is** boundary-modeled (the four G2-guard rows), those two rules
conflict. This ADR resolves the conflict in favour of the **gate rule**: a guard code with a matching
modeled boundary → `WorkerBpmnError` routed to its modeled **neutral** terminal; a guard code with **no**
modeled boundary (e.g. auth's `ERR_DENIAL_NOT_HUMAN`, deliberately un-allowlisted at `auth.py:58-59`;
cancel's `ERR_CANCELLATION_NOT_HUMAN`, declared-uncaught) → **incident**, unchanged. The L0 invariant
holds identically either way — neither path performs the adverse action. This is a refinement, not a
reversal: ADR-0026's blanket "guards → incident" was too coarse for guards the process author modeled a
clean terminal for.

## Consequencias

**Positivas:**
- The 24 deliberately-modeled boundary catches become live (fail-safe retries fire, origin-validation
  routes to clean terminals, guard-blocks route to neutral terminals) instead of every worker error
  collapsing to an operator incident — the correct, author-declared posture, and less on-call noise for
  outcomes that are *expected business events*, not faults.
- **Zero BPMN edits.** Option A is a `src/` + wiring migration; the models are already correct. Contrast
  Option B, which deletes 24 modeled safety paths.
- Completes, rather than abandons, the mechanism ADR-0026 §5 chose and auth/cancel/events already build
  against; removes the auth-test-only allowlist divergence from production.
- The boundary-proof gate closes a whole **regression class**: a spec boundary catch with no worker
  raising its code fails CI (dead-model detection), and an unmodeled `WorkerBpmnError` fails CI (silent-
  scope-end prevention) — structural, not vigilance-based.
- Fail-closed is preserved and even hardened: unproven codes still incident; the empty-allowlist silent
  failure mode is replaced by a *proven* allowlist.

**Negativas (aceitas):**
- Real `src/` change across ~10 G2 families (raise `WorkerBpmnError` for the modeled condition instead
  of / translated from the coded domain exception), several on money/PHI-adjacent paths (pagto, contas-
  adjacent recurso, inadimplencia) — mechanical but non-zero blast radius; staged by tier (Migration).
- A new CI gate (boundary-proof) to build and maintain — but ADR-0026 §5 and T1.1 §9 already committed
  to it; this ADR only makes it a precondition.
- Two demotion mechanisms remain in the code (`FunctionWorker`→`ValueError` for coded-non-modeled
  errors; harness ladder for the rest). Accepted: Option A does not require unifying them — a G2 family
  keeps raising its `ValueError`/`PermissionError` coded errors for its *non-modeled* conditions and adds
  a `WorkerBpmnError` only for the spec-declared boundary condition.
- Enabling business-outcome codes is gated behind T-E landing (audit reconciliation §4) — a sequencing
  cost, not a blocker for the fail-safe/validation codes.

## Migration plan (ordered, tiered — implemented by later tasks; this ADR is docs-only)

**No `spec/**` BPMN file changes.** Worker (`src/maezo/tools/workers/**`), the runtime wiring
(`service.py`), a new CI gate, and (later) audit are what move.

- **Tier 0 — boundary-proof gate + G1 activation (no worker-logic change).** Build the CI gate (§2);
  wire `service.py` to populate `bpmn_error_allowlist` from the gate output (drop `frozenset()`,
  `service.py:300`). This alone activates the **G1** codes — `ERR_AUTH_DENIAL_INCOMPLETE`,
  `ERR_CANCEL_MANTER_NOT_HUMAN`, `ERR_EVENT_PUBLISH_FAILED` — because their workers already raise them.
  Co-requisite: **T-E audit** must land before `ERR_AUTH_DENIAL_INCOMPLETE` (a denial-block, business
  outcome) is enabled (§4); `ERR_EVENT_PUBLISH_FAILED` (technical fail-safe) and
  `ERR_CANCEL_MANTER_NOT_HUMAN` may enable with T-E scheduled-not-blocking (cancel's neutral terminal is
  non-adverse). *Testability:* the three phase-2 G1 suites drop their inline test-only allowlist
  (`test_sp_op_{cancel,auth,escalation}_001.py:244/312/227`) and assert against production wiring.
- **Tier 1 — G2-fs technical fail-safe (lowest risk, non-regulated outcomes).** escalation
  `ERR_ESC_NOTIFY_FAILED` (make `NotifyTeamWorker`/`NotifySupervisorWorker` raise it on notify failure —
  entangled with the Kafka-producer gap the 19 xfails track, so co-schedule); ans `ERR_ANS_PROTOCOLO_NACK`
  **only if** SP-OP-ANS-SUBMIT-001 survives the DL-0028/0029 decommission (else drop the catch as part of
  that re-scope, which is Option-B-style pruning scoped to a *decommissioned* process, not the live ones).
- **Tier 2 — G2-val origin/consistency validation (fail-safe, non-adverse).** cred
  `ERR_CRED_INVALID_PRESTADOR`, lgpd `ERR_DSR_IDENTITY_UNVERIFIED`, nip `ERR_NIP_PROTOCOLO_INVALIDO`,
  pagto `ERR_PAGTO_ORDEM_INVALIDA`, programa `ERR_PROGRAMA_NO_CONSENT`, recurso `ERR_RECURSO_INVALID_GLOSA`,
  reembolso `ERR_REEMBOLSO_INVALID_PROTOCOLO`. Worker change: raise `WorkerBpmnError(code)` for the
  spec-declared condition instead of the coded domain exception (e.g. `pagto.py:70`, `programa.py:55`).
  Keep the coded exceptions for non-modeled conditions.
- **Tier 3 — G2-guard L0 guard-blocks (regulated outcomes — audit-gated).** cred `ERR_DECRED_NOT_HUMAN`
  & `ERR_CRED_DENIAL_NOT_HUMAN`, inadimplencia `ERR_CONTRACT_SUSPENSION_NOT_HUMAN`. **Hard-gated on T-E**
  (§4): these record a *refusal to perform an adverse L0 action* and must be non-repudiable before going
  live. Worker raises `WorkerBpmnError` routed to the modeled neutral terminal; ADR-0008 invariant
  preserved (no adverse action performed).

**Which xfails flip:** none of the 19 existing phase-2 xfails (they are the Kafka gap, not this defect —
reported above). The observable test deltas are the Tier-0 suites dropping their inline allowlists, and
the Tier 1-3 families gaining boundary-catch tests (in the remaining phase-2 suites) that assert the
catch fires + routes to its neutral terminal, replacing an *incident* assertion. **Which BPMN files
change:** none (except the ans decommission, owned by T2.6). **Which worker files change:** the ~10 G2
families' modules (`credenciamento/inadimplencia/lgpd/nip/pagto/programa/recurso/reembolso/escalation`,
ans conditionally), `service.py` (wiring), plus the new gate and T-E.

## Rejected alternatives

- **Option B — accept incident-as-fail-closed for all domain failures + prune the dead catches.**
  Rejected. (a) It would delete 24 deliberately-modeled, non-adverse safety paths — including the
  no-denial-structural neutral terminals (ADR-0018/DL-0013) that *prevent* an incomplete denial or an
  un-humanised suspension from proceeding, and the escalation fail-safe retries/fallbacks that keep human
  escalation flowing when a notify fails. Replacing those with an incident is *worse* for the regulated
  outcome, not more conservative: an incident **stalls** the instance for operator triage where the author
  modeled a clean, ANS-safe terminal (see SP-OP-PAGTO-001's own boundary doc: incidents "travava a
  mainline … instância stuck"). (b) It would also *revert* the already-built, R1-reviewed, real-engine-
  XPASSED auth/cancel/events `WorkerBpmnError` mechanism. (c) The only "pruning precedent" in the repo,
  ADR-0029, is *audit-chain* pruning and stands for the opposite of a silent delete — a prune must be
  **accountable, signed, and provable** (no prune without re-anchor); wholesale deletion of modeled
  regulatory paths has no such discipline and no ratified basis. Option B is defensible *only* for a
  process being decommissioned for an extinguished obligation (the ans/SIP case, DL-0028/0029) — captured
  as a scoped exception in Tier 1, not a global posture.
- **Naive hybrid ("case by case").** Rejected: the brief forbids a vague per-error judgement. The crisp
  criterion (§Decisao: *modeled boundary declared in spec → `WorkerBpmnError`; else incident*) is the
  hybrid done right — the line is a mechanical spec property enforced by CI, so it needs no human
  case-by-case call.
- **Do-nothing (keep `service.py:300 frozenset()`).** Rejected: it permanently strands 24 modeled paths
  as dead code, leaves the auth-test-only allowlist diverging from production (a latent "passes in test,
  incidents in prod" trap), and leaves ADR-0026 §5's committed gate unbuilt.
- **Move the classifier to a dispatcher `classify_worker_error` helper (the ADR-0026 §5 sketch).**
  Rejected as unnecessary churn: the as-built split (`FunctionWorker` reclassification + harness ladder)
  already yields the correct routing; Option A needs only the allowlist source and the `WorkerBpmnError`
  raises, not a dispatcher refactor.

## Supersedes

—  Refines **ADR-0026 §Decisao 5** (guard-vs-`bpmnError` classification; boundary-proof gate promoted from
follow-up to precondition) and extends **ADR-0008** (autonomy guards) and **ADR-0018** (no-denial
structural). Complements **ADR-0007** and the audit-emit-path-wiring design (schedules **T-E** as the
co-requisite for business-outcome codes; does not contradict its current deferral).
