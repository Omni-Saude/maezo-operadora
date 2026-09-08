# Fleet ledger and release-floor CI repair

## Scope and revision

- Baseline: `7b34cf44ae9e531092f92994a8347fbe0116784c`.
- Tested implementation: `7201a588d56cd7deb73425dfefb3b22c4d4cac15` (tree
  `998a482a00430232eb1024e80102c61ab563a8f4`).
- Owned product/source paths: none.
- Changed implementation: `scripts/ci/check_evidence_ledger_hashes.py`,
  `scripts/ci/generate_release_floor.py`.
- New focused fence: `tests/unit/ci/test_check_evidence_ledger_supersession.py`.

## FLC-01

The stacked gate originally reran imported historical rows against moving HEAD. The two stale
claims were real historical truths, but their test files had later grown:

- `ESC-TOLERANT-LOG-RAW-VALUE`: historical 49-result claim
  `6619030886f080512cdcec3258a76b9b89ea3b32cd338b3ad33ac6d723fbe672`; current 75-result claim
  `f5f527334aacf94a2ede4a74606e5a415be874369d2e9df942408c53a1dcbcbd`.
- `EVAL-HARNESS-SENDER`: historical 7-result claim
  `2bff3d57807d04abcd0713d465961c665f8c77d7955f5de890bf055af227a0d7`; current 11-result claim
  `7fe5dedae01a5bd31a387298143dbda77471ca46b39984da26c8e91944a841ce`.

The v1 supersession marker binds the exact old row, a full ancestral Git commit, the historical
test blob and `uv.lock`. The target executes from `git archive` with a minimal credential-free
environment, plugin autoload disabled, and an active guard that attests every imported `maezo`
module is inside the archive. The source lock must be byte-identical to the tracked candidate lock,
which makes use of the current locked interpreter explicit and checkable. Historical integration
tests retain the ambient-stack/mutex refusal.

The new row executes independently at HEAD with the current recipe. There is no historical skip,
HEAD fallback, ID allowlist, or branch-name trust. Exact-row digests permit unrelated duplicate
historical IDs while rejecting ambiguous targets, duplicate edges and cycles. The old date cutoff
still selects the legacy node-id recipe only for eligible historical targets; successor rows can
never use it.

Adversarial tests reject a non-ancestor source pin, forged target row, changed test/lock blob,
duplicate successors, cycles, unknown/malformed metadata, path/import escape, live historical test
without mutex, wrong historical hash and wrong current hash. A wrong old hash explicitly reports
that HEAD fallback is refused.

## Ledger preservation

The frozen ledger has 588 physical lines, 477 parsed data rows, 1,618,623 bytes and SHA-256
`b5e659d64a277b776d413dbb378a219305084cd24b5132f9d266e77acd5720cb`. It is the literal byte
prefix of the candidate ledger. The complete ledger diff is append-only: 15 additions, zero
deletions (three 8-cell data rows followed by twelve convention lines).

## FLC-02

The release-floor subprocess timed out at its 900-second local deadline, while the same full
`pytest tests/ -q` quality run completed without assertion failures in 1242.60 seconds (12,210
passed, 14 skipped, 662 deselected, 1 xfailed). The older R6 measurement was 885.98 seconds.

The explicit bound is now 1,800 seconds. The invocation and all release-capability floors remain
unchanged. Timeout still returns `({}, -1, "TIMEOUT ...")`; `evaluate_unit_tests` rejects it before
floor comparison. No workflow, selection, skip or xfail changed.

## Verification result

- Focused suites: 136 passed.
- Ruff and strict mypy: pass.
- Row cell count: 19/19 added rows have eight cells.
- Stacked ledger gate: 20/20 historical/current proofs pass. Historical source-import attestations
  counted 25 `maezo` modules for escalation and 43 for the eval harness.
- Full unit, real-engine and GitHub CI are intentionally left to the root's serialized lanes.

Exact argv, cwd, UTC collection metadata, return codes, decisive outputs and artifact hashes are in
`MANIFEST.md` and `raw/`.
