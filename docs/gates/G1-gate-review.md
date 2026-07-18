# G1 Gate Review — Phase 1 (Runtime Spine) Exit

> **Gate:** G1 (V2-COMPLETION-PLAN §4, "PHASE 1 — Runtime Spine"). Exit criteria, **verbatim**:
> - Plan §4 (l.110): *"**G1 exit:** one full flow (Helena→escalation, Rafael→dossier) runs
>   end-to-end on local compose against real CIB Seven; B3/B4 closed with property tests;
>   adversarial-verifier reproduces E2E independently."*
> - Execution-prompt §PHASE-GATE CHECKLIST (l.69): *"**G1:** Helena→escalation and Rafael→dossier
>   E2E on real CIB Seven via compose; PEP loads YAML (property-tested); ceiling bypass gone (test
>   proves ANALISE_HUMANA at `max_value_brl:0`); audit chain persists across kill-test."*
> **Reviewer charter:** `adversarial-verifier` (R1) acting as the phase-exit gatekeeper — phase-exit
> authority, can BLOCK G1. Author ≠ verifier: none of the work under review was authored here; every
> criterion is reproduced with fresh evidence (command output / live instance IDs / `path:line`). A
> claim in a PR body or evidence-ledger row is evidence to CHECK, not to accept (zero-trust; the
> repo's self-certification history is the threat model).
> **Position:** review base = `origin/main` `9939502` (`feat(workers): port the missing
> operadora.events.publish worker … (#61)`). Main moved during the review (see Ledger audit §
> "post-base movement"); the delta was diff-audited — **none of the G1-attested surfaces changed**
> (no `agents/helena|rafael`, `tools/workers`, `spec/`, `gateway/` policy/audit files in
> `9939502..c93d0cf`), so the evidence below holds for the current tip.
> **Method:** dedicated compose project `g1gate`, `MAEZO_PG_HOST_PORT=5591`, engine host-port
> remapped `18101:8080` via a scratchpad override (base 8080 held by a sibling agent); postgres
> (pgvector/pg16) + cibseven `2.1.0`. Tests via `uv sync --extra dev` then `env -u VIRTUAL_ENV uv
> run --extra dev python -m pytest …` with `ENGINE_REST_URL`/`CIBSEVEN_BASE_URL` pointed at the
> remapped engine. The review session was interrupted mid-flight by a spend-limit outage; on resume
> the entire state was re-audited zero-trust and **no pre-outage live claim was carried over
> un-re-verified** (the property/kill-test batteries were re-run post-resume; the E2E flows first
> succeeded post-resume). Live-engine note for reproducers: the shared build host ran 3–5 sibling
> `cibseven` JVMs inside one ~3.8 GiB Docker VM; the first uncapped engine boot took hours under
> swap exhaustion and was eventually OOM-killed (exit 137, `oom=true`) — the engine was then
> recreated with `JAVA_OPTS: -Xms128m -Xmx640m -XX:MaxMetaspaceSize=320m` and became ready in ~50 s,
> after which every live result below was produced in one session against that capped-heap engine.

## Overall verdict: **CONDITIONAL-PASS (all §4 gate criteria PASS with live evidence; conditions C1–C2 are verification-bookkeeping required by plan ground rules 2–3)**

Every criterion in the plan-§4 G1 exit line and the execution-prompt checklist is **independently
reproduced and PASSES with fresh live evidence**: both E2E flows ran on a fresh compose CIB Seven
(8/8 acceptance tests green; real instance IDs below, re-asserted directly against the engine REST
by this reviewer), B4 and B3 are closed with their property batteries (80 tests, run twice), the
audit chain survives the SIGKILL kill-test (24/24, run twice), and the events.publish worker (#61)
live-completes the formerly-blocking `ST_PublishRequested` with zero incidents and the loud
`kafka=None` no-producer log. The independent-reproduction criterion is satisfied by this review
itself (author ≠ verifier).

The verdict is **CONDITIONAL, not clean PASS**, for exactly two reasons, neither a gate-bar miss:
(C1) the evidence-ledger rows for the tasks this gate attests (T1.11's three rows and the
T3.1/events.publish row) still read `implemented — unverified` — this review supplies the missing
independent reproduction, but the plan's ground rule 3 requires the ledger rows themselves be
upgraded citing it; and (C2) T1.5 (engine-side DMN cutover), a Phase-1 task whose transport the
attested flows ride, remains `implemented — unverified` beyond the subset these flows exercised.
Per the coordinator's scoping rule the gate criteria are §4's line and are not re-scoped here —
C1–C2 enforce the plan's own verification discipline for a phase EXIT, nothing more.

---

## Per-criterion verdicts

### Criterion 1 — Helena→escalation runs E2E on local compose against real CIB Seven — **PASS (live)**

Ran `tests/integration/agents/test_helena_escalation.py` against the fresh `g1gate` engine
(cibseven 2.1.0 at `:18101`, spec/ BPMN+DMN deployed idempotently by the suite's own
`_deploy_spec_artifacts` conftest). **6/6 PASSED** (part of the 8-test run, 158 s). The suite runs
Helena's REAL graph (`agents.helena.graph.build`) with the REAL `CibSevenDmnTransport`
(engine-side `triage_redflag_*` + `escalation_routing`) and REAL `CibSevenHttpTransport`; only the
LLM/WhatsApp seams are deterministic fakes (per the suite's charter — the acceptance is the
engine's reaction, not LLM quality).

**Live instances (engine history API, captured by this reviewer):**
- red-flag: instance **`1e3222fb-8276-11f1-a35f-2660d0923ba4`**, businessKey
  `ESC-amh-wa:amh:t111-redflag-52839dc0`, state **ACTIVE**.
- psychosocial (gatilho 5, intent looks administrative): **`235dd688-8276-11f1-a35f-2660d0923ba4`**
  ACTIVE.
- classifier fail-closed (malformed JSON → falha_tecnica): **`37fe4bb9-8276-…`**; (LLM exception):
  **`4b53f064-8276-…`** — both ACTIVE, both routed `atendimento-humano` (escalation_routing r6).
- CPF-leak regression probe: **`607136af-8276-…`** — engine variable set proven free of every
  fragment of the offending value; `resumo_contexto` carries the class token only.
- non-red-flag turn: **no instance** (active-instance query for its business key asserted empty).

**Direct re-assertion (this reviewer's own REST calls, independent of the test code):** on
`1e3222fb…`: `GET /process-instance/{id}/variables` → `motivo_categoria=red_flag_clinico`,
`severidade=grave`; `GET /task?processInstanceId=…` → task `2187ddf4-8276-11f1-a35f-2660d0923ba4`
"Assumir e tratar escalonamento" (`UT_TratarEscalonamento`); `GET /task/{id}/identity-links?type=
candidate` → **`['plantao-clinico']`**. Matches the acceptance target exactly
(red_flag_clinico/grave → escalation_routing r1 → P1 plantão clínico).

### Criterion 2 — Rafael→dossier runs E2E on local compose against real CIB Seven — **PASS (live)**

Ran `tests/integration/agents/test_rafael_auth_dossier.py` in the same session. **2/2 PASSED.**
Rafael's REAL graph + REAL `CibSevenDmnTransport` (`auth_admissibility`/`auth_sla`/
`auth_auto_approval` engine-side) + REAL `CibSevenHttpTransport`; the BPMN's own
`ST_PrepararDossie` was serviced by the REAL, unmodified `AnalyzeRequestWorker`
(`register_auth_workers`) through a live worker probe.

**Live instances:**
- human-review path: instance **`60f29bdf-8276-11f1-a35f-2660d0923ba4`**, businessKey
  `AUTH-amh-IT-RAFAEL-409ef6c6`, state **ACTIVE**; in-suite assertions `route == "human_auditor"`,
  `dossier["decisao_cobertura"] is None` (L0 structural guardrail) held.
- auto_approve path: instance **`756067b3-8276-11f1-a35f-2660d0923ba4`**, businessKey
  `AUTH-amh-IT-RAFAEL-AUTO-409ef6c6` — `route == "auto_approve"`, process started, human task never
  touched, `decisao_cobertura` still `None`.

**Direct re-assertion:** on `60f29bdf…`: task **`74f793ab-8276-11f1-a35f-2660d0923ba4`** =
**"Analise do medico auditor"** (`UT_AnaliseMedicoAuditor`), candidate groups
**`['medico-auditor']`**. The gate's "Rafael dossier task appears for medico-auditor" target is met
as a real engine User Task. Code-level L0 corroboration (read, not trusted from claims):
`Route = Literal["auto_approve","human_auditor"]` — no deny variant (`agents/rafael/graph.py:79`);
`dentro_teto_l2` consumed via `bool(state.get(…))`, never computed (`graph.py:293`); `_route`
fail-safes to human (`graph.py:368-370`).

### Criterion 3 — B4 closed with property tests (PEP loads `spec/policies/autonomy/*.yaml`) — **PASS**

Ran `tests/unit/gateway/test_pep_policy_unification.py` — **all passed** (twice: pre- and
post-outage, identical results). Key reproductions:
- `test_every_yaml_action_resolvable_by_pep` — `build_pep()` loads the real matrix; every action
  resolves to a `Decision`. The test binds `REAL_CORE`/`FROZEN_FILE` via `resolve_spec_dir()` —
  the **real** `spec/policies/autonomy/{L0-core,_hard_frozen,tenants-amh}.yaml`, not fixtures.
- `test_hard_frozen_set_equals_yaml_frozen_set_equals_code` — code `HARD_ACTIONS` ==
  `_hard_frozen.yaml` == `L0-core.yaml` `hard:true` set.
- `test_unknown_action_denies_fail_closed` — unknown action → DENY.
- `test_every_hard_action_denies[…]` — all 5 hard actions DENY under 3 agent contexts, **including
  `nip_manter_negativa` and `contract_termination`** (the two B4 named as missing).
- `test_retired_pt_names_now_deny[…]` — 14 retired PT names DENY (vocabulary migrated, not aliased).
- missing/malformed/empty/frozen-mismatch core → **refuses to start** (fail-closed boot).

**B4 closed.** (Ledger T1.8 `verified — B4 closed` #36 — independently re-reproduced here.)

### Criterion 4 — B3 closed with property tests (ceiling bypass gone; ANALISE_HUMANA at `max_value_brl:0`) — **PASS**

Ran `tests/unit/sec/test_dentro_teto_source.py` + `tests/unit/tools/workers/test_ceilings.py` —
**all passed** (twice; the combined B3/B4 battery = **80 passed**). Key reproductions:
- `test_no_code_path_originates_dentro_teto_except_resolver` — AST arch-scan over the imported
  `reembolso`/`auth`/`pagto` modules: nothing originates the ceiling fact outside
  `CeilingResolver.within_l2_ceiling`; the scanner is proven to **bite** the exact pre-fix bypass
  snippets (`dentro_teto = True`, `process_vars.get("dentro_teto_l2")`) and not to false-positive.
- `test_ceiling_zero_is_fail_closed` — with `max_value_brl: 0`, `within_l2_ceiling(value_cents=0)`
  and `(…=1)` are both **False** → route to human (ANALISE_HUMANA). The real tenant overlay
  (`tenants-amh.yaml`) ships `authorization_approval.max_value_brl: 0`, so this is the production
  posture — corroborated live by Criterion 2's human-review path, which reached
  `UT_AnaliseMedicoAuditor` under exactly that tenant ceiling.
- missing action/param, negative, non-numeric, bool ceiling, unloadable matrix, unknown overlay
  action — all fail-closed.

**B3 closed.** (Ledger T1.9 `verified — B3 closed` #43 — independently re-reproduced; D-07 ceiling
*values* remain `blocked(external)`, orthogonal to the bypass closure.)

### Criterion 5 — Audit chain persists across kill-test — **PASS (live)**

Ran `tests/unit/gateway/test_audit_postgres.py` against the live `g1gate` postgres
(`MAEZO_TEST_DATABASE_URL=postgresql://maezo:maezo@127.0.0.1:5591/maezo`; note for reproducers:
the DSN must be IPv4 — `localhost` resolves IPv6-first and can exceed the fixture's 2 s probe under
host load). **24/24 passed** on the post-outage re-run (23 passed / 1 env-skip pre-outage — the
kill-test passed in BOTH runs). The acceptance test
**`test_kill_test_process_crash_loses_zero_committed_records` PASSED**: writer subprocess SIGKILLed
mid-stream, fresh writer restarted, **zero committed records lost, chain verifies with no gaps**.
Corroborating: tamper detection (incl. the jsonb-sweep genuine-tamper case), same-tenant concurrent
serialization without fork, cross-tenant chain isolation, non-null versioning columns, fail-closed
on missing table and unreachable DB. **B7 durability holds.** (Ledger T1.10
`verified — B7 closed`, merged #41.)

### Criterion 6 — events.publish (#61): ST_PublishRequested COMPLETES + kafka=None loud log — **PASS (live)**

**Code wiring (read):** `register_events_workers` is member **17/17** of `ALL_WORKER_BOOTSTRAPS`
(`tools/workers/bootstrap.py:59`), composed by `register_all_workers` (`bootstrap.py:71-82`) which
the worker-runtime daemon calls with **`kafka=None` default** — production runs the loud
no-producer path; the handler never fabricates `event_published=True` and completes with
`event_published=False` (no BPMN gateway reads the marker — grep 0).

**Live probe (this reviewer's own script, REAL `register_events_workers` + REAL
`register_escalation_workers`, kafka=None):** started SP-OP-ESCALATION-001 instance
**`99b19614-8276-11f1-a35f-2660d0923ba4`** (businessKey `ESC-G1PROBE-fc8aa9ee`) and drove it with
the real handlers:
- **`ST_PublishRequested` COMPLETED** — history activity-instance endTime set. The formerly-blocking
  `"no handler registered for topic 'operadora.events.publish'"` incident does **not** occur.
- **incidents on the instance = 0**.
- the loud log fired: `event_publish_no_producer topic=agents.events.escalation.requested
  business_key=ESC-G1PROBE-fc8aa9ee process_instance_id=99b19614-… — kafka=None (no producer
  wired…); event NOT published, completing with event_published=False`.
- Bonus corroboration: the same probe run drained the Rafael auto-approve instance's own pending
  publish task (`756067b3-8276-…`, topic `agents.events.auth.received`) through the same worker with
  the same loud marker — the generic worker services real BPMN publish steps across families.

### Criterion 7 — adversarial-verifier reproduces E2E independently — **PASS (this review)**

Satisfied by criteria 1, 2 and 6 above: this reviewer (not the author of any reviewed change)
brought up its own fresh stack, deployed `spec/` artifacts, ran the acceptance suites, and
re-asserted the outcomes directly against the engine REST with its own calls and captured its own
instance IDs. Author ≠ verifier holds for every criterion.

---

## Ledger audit — G1-dependency verification status (base `9939502`; movement audited to `c93d0cf`)

| Task | Role in G1 | Ledger status at review | Independent check here |
|---|---|---|---|
| T1.1 | worker spine (B1) | `verified` (#50) | live harness used by criteria 1/2/6 probes — worked against real engine |
| T1.2 | worker standardization (B13) | `verified` (#52) | `register_all_workers` 17/17 composition confirmed |
| T1.3 | BPMN/DMN deploy | `verified` (#37,#40) | live: spec deploy idempotent via the same `EngineDeployClient` path |
| T1.4 | DMN eval ADR | `verified`/ratified (#46) | ADR present; engine-side eval exercised live |
| **T1.5** | engine-side DMN cutover | **`implemented — unverified`** | **partially exercised live** — `triage_redflag_*`, `escalation_routing`, `auth_admissibility/auth_sla/auth_auto_approval` evaluated engine-side in criteria 1–2; the other 9 modules' tables NOT covered here → **condition C2** |
| T1.6 | entrypoints | `verified` (#51) | — |
| T1.7 | LLM providers | `verified` (#47) | phi=True discipline asserted on every graph LLM call in the suites |
| T1.8 | PEP loads YAML (B4) | `verified — B4 closed` (#36) | **PASS re-reproduced** (Criterion 3) |
| T1.9 | ceiling bypass (B3) | `verified — B3 closed` (#43) | **PASS re-reproduced** (Criterion 4) |
| T1.10 | audit persistence (B7) | `verified — B7 closed` (#41) | **PASS re-reproduced — live kill-test ×2** (Criterion 5) |
| **T1.11** | Helena/Rafael graphs (B6) | **`implemented — unverified`** (3 rows) | **NOW independently reproduced** (criteria 1–2, live instance IDs) → row upgrade = **condition C1** |
| **T3.1** (events.publish) | events.publish worker | **`implemented — unverified`** | **NOW independently reproduced** (Criterion 6 live probe) → row upgrade = **condition C1** |
| T3.1 (phase-1 suites) | ported process suites | `implemented — unverified` | Phase-3 scope (spine: T3.1→G3); not a G1 gate input — residual note only |
| T1.12 | remaining 8 graphs | 1/8 merged post-base (fernando, `c93d0cf`, R1-verified fix row); others in open PRs (#68,#70,#71) | out of G1's §4 bar (spine places T1.12 before G3, not G1) — residual note |

**Post-base movement (fetched during review):** `25df60f`(#69), `b458f8e`(#63), `36ca80b`(#73) —
CI/security; `d80fbe1`(#66) — T2.6 design (R1-verified row); `175c9f0`(#72) — deps ceiling;
`c93d0cf`(#67) — fernando T1.12. Diff-audited `9939502..c93d0cf`: **no change to any G1-attested
surface** (Helena/Rafael graphs, workers, policies, audit, `spec/`) — the only `src/` deltas are
`agents/fernando/*` and an `agent_runtime/service.py` readiness detail. G1 evidence therefore
transfers to the current tip unchanged.

---

## Residual-risk register — what G1 does **NOT** cover

G1 attests Phase-1 runtime-spine exit only. Verified-open, out-of-G1-scope items at review time:
T1.12 in progress (7/8 graphs unmerged); T3.1 phase-1 process suites carry 36 disclosed `xfail`
findings (notify/kafka drift, cancel topic drift, PHI-egress gap — Phase 3); B10 fraud scoring
content flagged for SME; B11 deploy blocked (placeholder ECR / 6 secrets — Phase 4); B12 RN currency
`verified-as-draft` pending SME; SME contract sign-offs `blocked(external)`; T2.4 remainder
`implemented — unverified` with CI lanes budget-blocked. None gates G1 per plan §4/§5.

---

## Conditions to convert CONDITIONAL-PASS → clean PASS

- **C1 (owner: orchestrator / ledger keeper, R3 mechanical; unblock trigger: merge of this gate
  record).** Upgrade the evidence-ledger rows for T1.11 (base row + 2 fix-cycle rows) and
  T3.1/events.publish from `implemented — unverified` to `verified`, citing this review's live
  reproduction (instance IDs `1e3222fb…`, `235dd688…`, `37fe4bb9…`, `4b53f064…`, `607136af…`,
  `60f29bdf…`, `756067b3…`, `99b19614…` at engine `g1gate` cibseven 2.1.0, review base `9939502`).
  Ground rule 3: the reproduction exists; the ledger must say so.
- **C2 (owner: adversarial-verifier R1; unblock trigger: R1 verification spawn on T1.5).** Complete
  T1.5's independent verification (the 10-module cutover, the 75-case golden parity, the disclosed
  `ans_cron` no-cutover block and the divergences flagged for finance/SME) and upgrade its row. The
  G1 flows already live-prove the Helena/Rafael DMN subset engine-side; C2 covers the remaining
  nine modules' tables, which are Phase-1 scope and carry money-path routing.

Neither condition re-scopes the §4 gate line — both enforce the plan's ground rules 2–3 (author ≠
verifier; ledger rows for every done task) for a phase exit.

---

## Sign-off

**Verdict: CONDITIONAL-PASS (all §4 G1 criteria PASS with live evidence; conditions C1–C2 open —
ledger-row upgrades and T1.5 verification).** Helena→escalation and Rafael→dossier ran end-to-end on
a fresh compose CIB Seven with real instance IDs re-asserted by this reviewer; B4 and B3 are closed
with their property batteries (80 tests, run twice); the audit chain survived the SIGKILL kill-test
twice; events.publish live-completes the formerly-blocking first activity with zero incidents and
the loud kafka=None marker; and the independent reproduction demanded by the gate is this review.
No warn-and-pass regression, no money/L0-path weakening, and no fabricated instance IDs.

*Reviewed by:* **adversarial-verifier (tier R1)**, per V2-COMPLETION-PLAN §3.
*Author ≠ verifier: none of the reviewed work was authored by this reviewer; every criterion was
reproduced at review base `origin/main` `9939502` on a dedicated fresh stack.*
*Date:* 2026-07-18
