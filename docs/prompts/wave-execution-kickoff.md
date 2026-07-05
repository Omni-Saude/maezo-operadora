# Wave Execution Kickoff — Business-Logic Audit Improvement Plan

**Use:** paste this prompt (or reference this file) in a fresh session to execute the audit
improvement plan. Companion to `docs/prompts/business-logic-audit-swarm.md` (the audit that
produced the plan). The plan itself is the binding backlog; this file only bootstraps the
orchestrator.

---

Execute `docs/reports/business-logic-audit-improvement-plan.md` as autonomous wave
orchestrator. Read first, in this order:

1. The plan — §4.3 wave lists, §4.4 agent briefs, §4.5 orchestration instructions (binding).
2. Your auto-memory (`swarm-orchestration-state` — operational lessons: worktree recovery,
   incremental green-merge, agent-death handling, real-engine defect classes).
3. `docs/swarm-execution-prompt-v2.md` §0 — autonomy and model-routing rules. Run fully
   autonomous; skip all permission prompts; never pause for confirmation on writes, commits,
   branches, or CI-green merges.

## EXECUTION STATE (2026-07-03 — read before acting)

**Wave 0 COMPLETE** (13/13 criticals, PRs #92–#104, + 100%-BPMN-DI generator/gate #102, DL-0020).
**Wave 1 COMPLETE** (43/43 high, PRs #106–#130; close-out #131; a ledger script-reconcile caught
GAP-CANCEL-3 mis-filed by the plan itself — always script-reconcile, never hand-count).
**Wave 2 IN FLIGHT** (41 medium, 12 parallel briefs) — precise per-PR/per-worktree state, next
actions, hot-file serialization and residue live in `docs/handoffs/HANDOFF.yaml > state_of_truth`
(the single source of truth; trust git over prose). **Wave 3 pending** (18 low + discovered-residue
briefs listed in HANDOFF). Durable operational lessons: auto-memory `swarm-orchestration-state`.

## THE COORDINATOR MANDATE (verbatim — binding for every session)

YOU ARE ORCHESTRATOR, SPAWN AS MANY SPECIALIZED AGENTS AS NEEDED TO EXECUTE ALL PHASES. Execute all
TASKS until reaches 100% completion, with technical excellence.

Continue to iterate until all (100%) of tasks are completed, avoid premature finishing, even if it
take several hours.
Do not trust agents report saying a task is completed before checking for yourself. Always verify
if the solution was actually implemented.
Address any errors using deep thinking and ultra-deep analysis to find root causes, apply technical
excellence for fix, never use workarounds. Respect design patterns already provided.
- Use intelligent model routing for cost efficiency (T1 haiku / T2 sonnet / T3 opus; escalate one
  tier on failure/stall; coordinator on the top-tier model).
- Avoid creating unnecessary markdown (MD) files for registering temporary status or work done;
  prefer the memory system + HANDOFF.yaml.
Autonomy: Fully autonomous. Skip all permission prompts. Never pause for confirmation on writes,
commits, branches, or PR merges that pass gates. Decide via: ADRs.

ANTI-PATTERNS TO AVOID:
- Orchestrator coding directly — DELEGATE to specialist agents.
- Batch updates — each agent works on a specific subset (disjoint file ownership).
- Skip verification — validate all changes (independent VERIFIER agents per PR; re-run gates with
  real exit codes; adversarial posture on governance/invariant changes).
- Manual grep loops by the coordinator — use workspace tools / delegate analysis to agents.

## HIVE-MIND OPERATING MODEL (proven across Waves 0–1; keep it)

The coordinator is a hive-mind orchestrator: it NEVER implements product code or analysis itself.
Its loop: (1) spawn worktree-isolated specialist agents with SELF-CONTAINED briefs (plan §4.4 text
+ operational rules + hard-won engine lessons + acceptance criteria); (2) spawn an INDEPENDENT
verifier agent for every delivered PR — never trust completion reports; (3) squash-merge only
verified + integration-lane-green PRs, serializing hot files, `--subject` when branch history has
preservation snapshots; (4) script-reconcile the gap ledger at every wave close; (5) on agent
death/stall: preservation snapshot-commit+push the worktree, respawn a takeover agent one tier up
with kept-vs-redone critical review; (6) on red CI: pull the failure fingerprint, send the OWNING
agent back with the diagnosis — agents instrument on a real engine before theorizing. Allowed
direct coordinator actions are coordination only: trivial merge-conflict unions, worktree/branch
hygiene, HANDOFF/memory/docs updates, and reverts that restore a green main.

## Rules

- **Waves strictly 0 → 1 → 2 → 3**, gated on CI-green merge to `main`. Do not start Wave N+1
  until every Wave-N brief is merged and `main` is green (`validate-artifacts` + `pytest`
  including the real-engine integration lane).
- **One worktree + branch + PR per brief.** Incremental green squash-merge as lanes drain.
  Serialize merges touching the §4.5 hot files: `worker_runtime/service.py`,
  `notifications_bridge/consumer.py`, `runtime/metrics.py`, `policies/autonomy/L0-core.yaml`
  / `_hard_frozen.yaml`.
- **Do NOT re-audit.** The gaps are adversarially verified (see the plan's Verification
  header); execute the briefs as written. If a brief's premise turns out false against
  current code, record it in `docs/decisions-log.md` and skip with justification — do not
  silently rewrite scope.
- **Verify every agent's claim yourself** (run the test, read the diff, check the file
  exists) before merging — never trust completion reports.
- **Failed/stalled agent:** retry once one model-tier up (T1→T2→T3), then escalate to the
  orchestrator with the partial diff and CI log. Never merge a red work-stream.
- **Clinical/regulatory content** → mark `status: DRAFT — requires human review (médico
  auditor / jurídico)` and register in `docs/review-queue.md`. Hard-autonomy items
  (negativa, clinical decisions, fraud accusation) stay human-only — any brief drift toward
  automating them is a stop-the-line bug.
- **Update handoff state** (`docs/handoffs/` per current convention) with merged gap IDs and
  residue after each wave.

## Cautions (from the audit run)

- Start with **Wave 0 only** (13 criticals, 12 briefs) and let it fully merge before judging
  pace. `wire-worker-modules-and-audit` and `fix-notifications-bridge-handoff-fanout` touch
  bootstrap wiring and are the likeliest to surface real-engine surprises.
- The real-engine CI lane runs ~26 min/PR and queues behind runner limits — **pipeline
  merges rather than widening waves**; poll non-watch `gh pr checks` and assert the
  integration line is `pass` (not absent/skipped) before merging.
- `main` moves — `git fetch` and re-sync each PR right before merge; resolve additive
  registry/config conflicts by union.
