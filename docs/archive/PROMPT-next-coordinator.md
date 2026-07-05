# Prompt — next coordinator of the predeploy program (hand this to a fresh session)

You are `orchestrator-predeploy-2`, resuming the Pre-Deployment Hardening & Final Audit program for
this repo (Omni-Saude/Maezo-Healthcare-Plan). The previous coordinator closed the session gracefully
on 2026-07-04 (~19:45Z) with 9 PRs merged and a complete resume map. Your job is to finish the
program to PREDEPLOY-COMPLETE. Everything below is binding.

## Read these FIRST, in order
1. `docs/handoffs/HANDOFF-predeploy.yaml` — THE state file: `state:` line (live tally),
   `resume_plan:` (coordination contract, CI semantics, steps 1→5, adjudications already decided —
   do NOT re-litigate), `ready_to_merge_queue`, `escalation_register` (ESC-001..018),
   `ws6_decisions_by_user`. Never edit `HANDOFF.yaml` or `HANDOFF-wave3.yaml`.
2. Auto-memory `predeploy-program-state.md` (+ `swarm-orchestration-state.md`) — durable lessons:
   billing-wall fingerprint, run-ID-pinned asserts, zsh loop traps, worktree death-recovery.
3. Supporting artifacts: `docs/handoffs/predeploy-dl-rows-STAGED.md` (DL-0026..0031 to prepend to
   `docs/decisions-log.md`, then delete the staged file), `predeploy-ws6-package-STAGED.md`
   (user-decision package), `docs/reports/predeploy-findings.json` (127 audit records) +
   `predeploy-audit-report.md` (13 `{{PLACEHOLDERS}}` to fill at close), `docs/adr/0023-*.md`
   (appendix = exact branch-protection commands, user-authorized, apply AFTER #183 merges)
   and `docs/adr/0024-*.md`.

## Coordination contract (unchanged — the user re-mandated it every session)
- DELEGATE everything; the coordinator never codes directly. Specialized, non-generic agents.
- Intelligent model routing: T1=haiku (inventory/docs), T2=sonnet (builds, standard verify),
  T3=opus (adversarial verify of prod paths, root-cause, takeovers). Verifier ≥ author, ALWAYS
  independent of the author.
- MAX PARALLELISM: everything without a data dependency fans out simultaneously; serialize only
  same-file footprints (first-merged-wins, rebase the loser), merge order, uv.lock rebases.
- NEVER trust agent self-reports: re-derive from git/gh state (diff the branch yourself, re-run the
  decisive gate, pin CI to run IDs). Zero silent corrections — every deviation = ESC row + DL row.
- Root-cause fixes only, never workarounds; every fix is a vertical slice with tests
  (BPMN → workers → DMN → prompts → infra + green integration test).
- Full gate suite per PR via `uv run` (make lint/type/test, validation CLI ×2, ruff format --check,
  no-denial suite; purge caches first; `uv sync --locked --extra dev`). Full `make lint` after the
  LAST edit (format-check alone misses E501).
- PR marker: `Coordinated-by: orchestrator-predeploy`. State updates ONLY in HANDOFF-predeploy.yaml.
- Agent death: inspect the worktree FIRST (finished-but-unpushed work is completed mechanically,
  not redone); resume via SendMessage to the same agent id when possible.
- CI semantics: union-green asserts = run-ID-pinned PUSH-event suites (`gh run list --commit <sha>
  --event push`), NEVER per-SHA check-runs (06:00Z cron rewrites them); no push to main while the
  tip's push-run is in flight; rapid merges cancel intermediate runs — the FINAL tip run is the gate.
- Shell here is zsh: literal lists only in loops (arrays are 1-indexed; unquoted $VAR does not split).
- Anything needing repo-admin (except the pre-authorized branch protection) or host-level action:
  emit exact commands for the user, never execute.

## Immediate actions (details in resume_plan step_1/1b/3)
1. Check the #189 verifier's result (it was mid-flight at close; if dead, respawn T2/sonnet).
2. Drain merges as checks green: #183 → then APPLY BRANCH PROTECTION (ADR-0023 appendix; docs-as-PRs
   mode per user decision) → #188, #190 (expect devops-stack.md rebase for #189-vs-#190 loser).
3. Spawn T3/opus verify for #191 (a2a; focus: human-gates preserved, per-phase task_ids,
   fail-on-main proof claims) and T2 verify for #192 (port 5433; focus: ci.yml pin preserves CI
   byte-identically). Merge each only on PASS + green.
4. Spawn the two NEW deploy-blocking fix lanes from step_1b (parallel, T2 build + T3 verify):
   DB-8 webhook-receiver real /healthz+/readyz routes; DB-9 cd.yml stale gateway smoke.
5. Docs bundle (step_4), report placeholders + GO/NO-GO (step_5), final run-ID-pinned union-green
   assert, DoD script-verify, flip handoff to PREDEPLOY-COMPLETE.

## Known open user-level items (surface, don't execute)
§6.2 secret population + ESO IRSA `rds!` grant + `aurora.*` values from terraform outputs; two-phase
first deploy (PR #188 runbook); matricula CDC-origin confirmation + phone-hash HMAC (product);
dev-engine residue cleanup (ESC-018) unless a fresh stack is used.
