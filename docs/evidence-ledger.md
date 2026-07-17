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

Test hash convention: `sha256:` of the sorted `PASSED`/`FAILED` result lines from
`pytest <test file> -v --tb=no -p no:cacheprovider` — reproducible by anyone who
checks out the cited commit and re-runs the same test file, and stable across
machines since it excludes timing/header noise.

| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | Evidence (path:line) | Test hash | Status |
|---|---|---|---|---|---|---|---|
| T0.5 | 2026-07-16 | gates-engineer (R2) | — | 410bc01699c6ff1b275801da81b4f2d1626b39d0 | tests/unit/ci/test_check_evidence_ledger.py:1 | sha256:04abcdef4b3ff27711e03b0fe1c1ff20f5e8ea46fcfa8e5dd2ec918cfd7702c7 | implemented — unverified |
| T1.8 | 2026-07-16 | policy-guardian (R1) | — | a9623285a3a9dc89eb72743c1b3d176e44e144c9 | src/maezo/gateway/pep.py:71 (HARD_ACTIONS frozen 5); src/maezo/gateway/pep.py:287 (load_matrix); src/maezo/gateway/pep.py:336 (build_pep fail-closed factory) | sha256:0445f32b7de310dd04b714204ee509e3df41d14e40ac37275b00a5218253fa43 | implemented — unverified |
