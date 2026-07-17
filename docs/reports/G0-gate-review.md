# G0 Gate Review — Phase 0 (Truth Reset) Exit

> **Gate:** G0 (V2-COMPLETION-PLAN §4, "PHASE 0 — Truth Reset"). Exit criteria:
> zero false claims greppable; dead paths fixed; SME dispatch receipts; evidence-ledger
> CI live; every T0.x acceptance criterion carries independent verification with a ledger row.
> **Reviewer charter:** `regulatory-gatekeeper` + `security-gatekeeper` (tier R1, phase-exit
> authority — can BLOCK G0). Author ≠ verifier: none of the work under review was authored here.
> **Position:** `origin/main` HEAD = `579e8f9` (`docs(ledger): backfill verified rows for
> waves 0-1 … (#33)`) — the expected ledger-backfill merge.
> **Method:** every criterion reproduced at HEAD with `path:line` / command output. A claim in
> a PR body or ledger row is evidence to CHECK, not to accept.

## Overall verdict: **CONDITIONAL-PASS (external: SME dispatch receipts pending human roster)**

The gate's **structural integrity holds**: the evidence-ledger CI gate is live and fail-closed
(reproduced in 3 modes), `validate-artifacts` is a real fail-closed gate (mutation-tested),
the CI three-bucket collection guard fails closed, gitleaks is checksum-verified, and the SME
dispatch package contains **zero fabricated receipts** (the one instant-BLOCK trigger — absent).
The core B9/B8 false-claim and dead-path patterns G0 targeted are remediated with integrity.

The verdict is **CONDITIONAL, not clean PASS**, because the literal G0 bar — "zero false claims
greppable" and "dead paths fixed" — is not fully met at HEAD: **four residual documentation-truthfulness
defects survive** (2 false-claim-class, 2 live-dead-pointer-class). None is a gate-integrity /
warn-and-pass regression, none touches money or L0 paths, and two arise from later Phase-2 merges
landing on `main` ahead of this retroactive review — so BLOCK is disproportionate. They are recorded
below as **mandatory conditions (C1–C4)** that must be closed to convert this to a clean PASS.

---

## Per-criterion verdicts

### Criterion 1 — Zero false claims greppable — **CONDITIONAL (core PASS; 2 residual defects)**

**Reproduced clean (the B9-targeted patterns):**
- `553` — 3 hits, all in `PLANS.md:537,544,570`, all **truthful corrective annotations**
  ("Actual test count verified as 588 (not 553)", "Corrected from 553"). Not false claims.
- `15/15` — `README.md:16` badge is honestly labelled `milestones-15/15_self--certified` /
  alt `"15/15 self-certified (unverified)"`; `PLANS.md:3,543` annotate it "unverified and
  self-certified". `docs/archive/HANDOFF.yaml:63` is archived history. No live overclaim.
- `Production/Stable` / `1.0.0` — **0 hits**. `pyproject.toml:3` = `version = "0.2.0"`,
  `pyproject.toml:8` = `"Development Status :: 3 - Alpha"`. Correct.
- CodeQL "active" — `README.md:129` truthfully states *"CodeQL Disabled (Advanced Security
  unavailable) · dependency review Disabled"*; `security.yml:36-64` has both jobs commented
  out with honest rationale. No false "CodeQL active" claim. SECURITY.md↔security.yml consistent
  (ledger T0.1 evidence reproduces).

**Residual defect C1 (false-claim-class, highest priority).** `docs/processes/catalog.md:12-23`
lists 12 processes with Status `modelado (suite integracao real-engine)`, asserting a real-engine
integration suite. Reality: **`tests/` contains zero integration tests** (`find tests -path
'*integration*' -name '*.py'` → 0; `tests/integration/` does not exist; the porting of the suite
is task **T3.1**, not yet done). This is a B9-class overclaim in the *compliance-backbone* doc,
greppable via `real-engine` / `integracao real`. It survived because T0.1's target-pattern list and
T0.2's dead-path scope did not include it. Not runtime-affecting, but must be corrected (e.g.
`modelado (suite de integração pendente — T3.1)`).

**Residual defect C2 (stale-count).** `README.md:12,48,76,127` claim **588** tests; my run
`env -u VIRTUAL_ENV uv run --extra dev python -m pytest -q` → **`716 passed, 1 warning`**. The badge
is stale by ~128 tests. The drift is a *conservative undercount* introduced by Phase-2 merges
(T2.1/T2.3/T2.4) that landed on `main` before this retroactive G0 review; 588 was truthful at
T0.1's commit `ce06673`. Non-blocking but no longer truthful — refresh to 716.

### Criterion 2 — Dead paths fixed — **CONDITIONAL (docs/spec intent met; 2 root-file dead pointers)**

- `grep -rn "src/maezo/processes" docs/ spec/ --exclude-dir=prompts` → **3 hits, all in
  `docs/sme-dispatch/README.md:47,57,58`**, each **explicitly labelling the paths DEAD** and
  redirecting SMEs to canonical `spec/` ("**Those paths are dead** — the real, canonical …").
  Truthful corrective mentions (added by the later T0.6, not live pointers) — they pass the
  survivor-judgment test. *Note:* this means the **literal T0.2 acceptance-grep (0 hits) no longer
  holds at HEAD** — a later task reintroduced the string in a corrective context; the row was
  accurate at its own commit `03fa181`.
- `src/maezo/processes` and `src/maezo/policies` confirmed **nonexistent** (`ls` → No such file);
  canonical `spec/processes/{bpmn,dmn}` + `spec/policies/autonomy` exist. `Makefile:38` mention is
  a truthful historical comment (OK).

**Residual defect C3 (live dead pointer).** `PROJECT.md:43-44` presents a directory-structure table
row `| src/maezo/processes/ | BPMN SP-OP + DMN (artefatos validados em CI) |` and
`| src/maezo/policies/autonomy/ | Matriz L0–L3 |` — pointing at directories that **do not exist**.
Canonical is `spec/processes/` / `spec/policies/autonomy/`.

**Residual defect C4 (live dead pointer).** `CONTRIBUTING.md:29` instructs contributors *"Modele em
`src/maezo/processes/bpmn/` …"* — a nonexistent path; should be `spec/processes/bpmn/`. This will
actively misdirect a new contributor.

C3/C4 are the B8/B14 dead-reference class; they escaped because T0.2's grep scope was `docs/ spec/`,
not root files. If gating a later phase I would treat live dead pointers in contributor-facing docs
more strictly; at G0 they are mandatory-fix conditions, not a BLOCK.

### Criterion 3 — SME dispatch receipts — **PASS (blocked-external, exemplary integrity)**

- `docs/sme-dispatch/` = 6 role packages (`dpo`, `financas`, `juridico`, `medico-auditor`, `po`,
  `regulatorio`) + `tracker.md` + `README.md`. Matches spec.
- `tracker.md` = 16 rows (15 DRAFT + 1 FINAL); **every** row reads `prepared — awaiting roster
  (blocked external)`, Receipt = `none`, Redline rounds = `0`, Signoff = `no`. Lines 10-11:
  *"No contract below has been sent to anyone; 'receipt' and 'redline rounds' are consequently all
  zero/none — recording anything else would be fabrication."*
- **No fabricated receipts exist and none are claimed** — the single instant-BLOCK trigger is
  absent. The tracker further flags `SP-OP-ESCALATION-001` as FINAL-without-signoff and **refuses to
  wave it through** (§"Signoff-absent flag") — the opposite of self-certification.
- Contract statuses spot-checked (4): `SP-OP-AUTH-001`=DRAFT, `SP-OP-REEMBOLSO-001`=DRAFT,
  `SP-OP-FRAUDE-001`=DRAFT, `SP-OP-ESCALATION-001`=FINAL — all match tracker rows and `catalog.md`.
- This criterion is **legitimately `blocked(external)`** on the human roster (per V2-COMPLETION-PLAN
  §7 / risk register). Its blocked status is what makes the overall verdict CONDITIONAL, not the
  dispatch team's failure — the package is complete and honest.

### Criterion 4 — Evidence-ledger CI live — **PASS**

- `evidence-ledger.yml` + `scripts/ci/check_evidence_ledger.py`: **no `continue-on-error`, no
  `|| true`, no soft-fail**; least-privilege `permissions: contents: read`; untrusted PR ref/body
  passed via `env:` (never shell-interpolated → injection-safe); fail-closed contract explicit
  (`evaluate()` returns `ok=False` when a detected task ID has no row or the ledger is unreadable).
- **Reproduced the checker locally (charter modes a/b/c + control d):**
  - (a) `--branch t0.2-dead-refs --pr-body "Task: T0.2"` + matching row → **EXIT 0** ("row(s) found").
  - (b) `--branch t9.9-fake-task --pr-body ""` (no row) → **EXIT 1** ("Missing … row(s) … T9.9").
  - (c) `--ledger-path docs/NONEXISTENT-ledger.md` while a task is detected → **EXIT 1**
    ("could not read ledger … failing closed").
  - (d) control: no task ID → **EXIT 0** ("ledger entry not required").
- **Ledger completeness:** `docs/evidence-ledger.md` carries verified rows for **T0.1–T0.6** (plus
  Phase-1/2 rows) with commit SHAs + verifier identities. All six T0.x SHAs (`410bc01 ce06673 03fa181
  57d49a8 1730949 5176374`) **exist on `main`** with matching task-ID commit subjects.
- **Spot-verified T0.3 end-to-end:** `find src/maezo/agents -name agent.yaml` → **0** (no
  duplicates); `spec/agents` holds **11** canonical `agent.yaml`; `src/maezo/agents/__init__.py`
  loader is **fail-closed** (raises `FileNotFoundError` if `spec/agents/` missing);
  `docs/reports/T0.3-agent-yaml-drift.md` present. Row claims reproduce.
- **Spot-verified T0.2:** cited SHA `03fa181` exists; row's "0 hits" was accurate at that commit —
  the 3 corrective hits at HEAD were added later by T0.6 (see C2 note above), not a T0.2 defect.

### Criterion 5 — Gate-integrity sweep (security-gatekeeper hat) — **PASS (no always-pass residue)**

- **`validate-artifacts` is REAL and fail-closed.** `make validate-artifacts` on clean tree →
  `[validate] OK — 0 errors`. **Mutation test:** I appended invalid XML to
  `spec/processes/dmn/recurso_sla.dmn` → validator **EXIT 1** with 2 real errors ("malformed XML …
  line 107" + "camunda:decisionRef 'recurso_sla' does not match any dmn:decision id … broken or
  corrupted cross-reference"); restored, tree clean. It is not the always-pass stub. *(The
  `Makefile:39` comment "still fail-soft during greenfield" is STALE post-T2.1 — behavior is
  fail-closed.)* The `ci.yml:96-97` lane runs `make validate-artifacts` with no soft-fail wrapper.
- **Ledger gate — no `continue-on-error`** (workflow + script; verified in Criterion 4).
- **`ci.yml` three-bucket collection guard is fail-closed** (`ci.yml:255-283`): collect exit 0 →
  run lane; exit 4/5 → visible skip; **anything else (e.g. 2 = import/collection error) →
  `::error::COLLECTION BROKEN … exit 1`**. The comment documents that verification cycle 1 caught a
  fail-open two-bucket version that routed every nonzero exit to a silent green — now fixed.
- **gitleaks checksum-verified** (`security.yml:90-107`): pinned `8.30.1`, downloaded then
  `echo "${GITLEAKS_SHA256}  ${ARCHIVE}" | sha256sum --check --strict -`, `--exit-code 1`.
- **No warn-and-pass regression in any Phase-0 deliverable.** (The `content-signoff-gate`
  greenfield stub at `ci.yml:101` is **T2.2 / Phase-2**, out of G0 scope, and honestly labelled —
  recorded under residual risks, not a G0 finding.)

---

## Residual-risk register — what G0 does **NOT** cover

G0 attests only to **Truth Reset** (honest docs, dead-path repair, ledger discipline, dispatch
readiness). It is **not** a statement that the repository is healthy or deployable. The following
verified-open defects (V2-COMPLETION-PLAN §1) are **Phase-1/2 scope** and remain live at HEAD —
this sign-off must not be read around them:

| Ref | Residual risk (reproduced at HEAD) | Owning phase |
|---|---|---|
| B1 | Runtime spine absent — LLM `raise NotImplementedError` at `src/maezo/runtime/inference.py:104`; no dispatch loop | Phase 1 (T1.1/T1.6/T1.7) |
| B3 | Reimbursement ceiling bypass still in worker code — `dentro_teto_l2` computed at `reembolso.py:206` (T1.9 ledger = **design-only**, implementation pending) | Phase 1 (T1.9) |
| B4 | PEP does **not** load `spec/policies/autonomy/*.yaml` (no such load in `gateway/pep.py`); T1.8 ledger = **design-only** | Phase 1 (T1.8) |
| B5 | DMN layer dead at runtime; workers re-implement rules in Python | Phase 1 (T1.4/T1.5) |
| B6 | Agent graphs are stubs — `agents/rafael/graph.py` (50 ln), `agents/helena/graph.py` (69 ln) | Phase 1 (T1.11/T1.12) |
| B7 | AuditSink in-memory only — `gateway/audit.py:74` "in-memory chain"; Postgres chain unused | Phase 1 (T1.10) |
| B2 (partial) | `validate-signoff` still a greenfield stub (`ci.yml:101` "exit 0") | Phase 2 (T2.2) |
| B9 (partial) | **Zero integration tests / zero evals** in `tests/` (drives false-claim C1) | Phase 3 (T3.1/T3.2) |
| B10–B13 | Fraud `len(evidencia)*10` placeholder; RN currency verified-as-draft pending SME; deploy blocked (placeholder ECR/secrets); 13/16 workers bypass `WorkerBase` | Phase 2/4 |
| ext | **SME sign-offs pending human roster** — dispatch `blocked(external)`; 15/16 contracts DRAFT | ext (Rodrigo) |

---

## Conditions to convert CONDITIONAL-PASS → clean PASS

- **C1** — `docs/processes/catalog.md:12-23`: remove/qualify the `suite integracao real-engine`
  Status claim for the 12 processes (no integration suite exists until T3.1).
- **C2** — `README.md:12,48,76,127`: refresh the stale `588` test count to the reproduced **716**.
- **C3** — `PROJECT.md:43-44`: repoint `src/maezo/processes/` / `src/maezo/policies/autonomy/`
  to `spec/…` (dead pointers).
- **C4** — `CONTRIBUTING.md:29`: repoint `src/maezo/processes/bpmn/` → `spec/processes/bpmn/`.

C1–C4 are documentation-truthfulness fixes (a fast `docs-hygienist` R3 follow-up). None blocks the
gate's *structural* integrity; all four are required for a clean "zero false claims greppable /
dead paths fixed" attestation.

---

## Sign-off

**Verdict: CONDITIONAL-PASS (external: SME dispatch receipts pending human roster; conditions C1–C4
open).** The Phase-0 machinery — evidence-ledger CI, real fail-closed `validate-artifacts`,
fail-closed CI collection guard, checksum-verified gitleaks, and a zero-fabrication SME dispatch
package — is sound and independently reproduced. Four bounded documentation-truthfulness residuals
(C1–C4) must be closed before a clean PASS. No fabricated receipts, no warn-and-pass regression, no
money/L0-path weakening was found.

*Reviewed by:* **regulatory-gatekeeper + security-gatekeeper (tier R1)**, per V2-COMPLETION-PLAN §3.
*Author ≠ verifier: none of the reviewed work was authored by this reviewer; every criterion was
reproduced at `origin/main` HEAD `579e8f9`.*
*Date:* 2026-07-17
