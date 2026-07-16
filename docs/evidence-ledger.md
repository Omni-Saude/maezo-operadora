# Evidence Ledger

Per Plan ground rule 3 (V2-COMPLETION-PLAN), every completed task appends a row here:
task ID, commit SHA, author agent+tier, verifier agent+tier, evidence path:line, a
test-output hash, and a status. Status is one of `implemented — unverified` (the
author's own claim, not yet independently checked), `verified` (an independent
verifier reproduced the acceptance criteria), or `blocked(external)`. A task ID is
never "done" on the strength of its author's claim alone — see
`src/maezo/platform/validation/cli.py:104,121` for the always-pass-unconditionally
anti-pattern this ledger exists to make structurally impossible to repeat.

CI (`.github/workflows/evidence-ledger.yml` / `scripts/ci/check_evidence_ledger.py`)
enforces that any PR whose branch name or body cites a task ID carries a row for it
here at HEAD; the row is added by this table, not by the check.

| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | Evidence (path:line) | Test hash | Status |
|---|---|---|---|---|---|---|---|
| T0.5 | 2026-07-16 | gates-engineer (R2) | — | PENDING_SHA | tests/unit/ci/test_check_evidence_ledger.py:1 | PENDING_HASH | implemented — unverified |
