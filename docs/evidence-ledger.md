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
A PR body only "cites" a task ID via an explicit marker — a `Task:`/`Tasks:` line or a
`[T<phase>.<n>]` bracket — never via a bare prose mention (T0.5 gate-precision follow-up).

Test hash convention: `sha256:` of the sorted `PASSED`/`FAILED` result lines from
`pytest <test file> -v --tb=no -p no:cacheprovider` — reproducible by anyone who
checks out the cited commit and re-runs the same test file, and stable across
machines since it excludes timing/header noise.

| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | Evidence (path:line) | Test hash | Status |
|---|---|---|---|---|---|---|---|
| T0.5 | 2026-07-16 | gates-engineer (R2) | adversarial-verifier (R2) | 410bc01699c6ff1b275801da81b4f2d1626b39d0 | check_evidence_ledger.py:65,72,77,121-176,250,260; evidence-ledger.yml:23-24,50-53; injection-safe, 9 bypass attempts rejected | sha256:04abcdef4b3ff27711e03b0fe1c1ff20f5e8ea46fcfa8e5dd2ec918cfd7702c7 | verified (merged #26) |
| T0.1 | 2026-07-16 | docs-hygienist (R3) | adversarial-verifier (R3) | ce06673 | SECURITY.md:31-40 vs security.yml:23,45,66 + dependabot.yml; root-doc sweep clean for 553/15-15/Production/real-engine | sha256:fdde36225887612caf755d282cd6a60d1bac0f7e0a802a35a015952020469e8a | verified (merged #22) |
| T0.2 | 2026-07-16 | docs-hygienist (R3) | adversarial-verifier (R3) | 03fa181 | acceptance-grep 0 hits (docs/+spec/ excl. prompts); SP-OP-ANS-SUBMIT-001 bpmn:47 + lgpd_dsr_routing.dmn:29 doc-text-only; 26 nao-portado annotations | sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | verified (merged #23) |
| T0.3 | 2026-07-16 | runtime-spine-engineer (R2) | adversarial-verifier (R2) | 57d49a8 | src/maezo/agents/__init__.py:36-92, Makefile:26, docs/reports/T0.3-agent-yaml-drift.md; fail-closed loader probes all held | sha256:ca7f9bdf062d24ba6e504cf485857ef6df988bf1f8c0ad3b68199b57134e5220 | verified (merged #25) |
| T0.4 | 2026-07-16 | infra-deployer (R2) | adversarial-verifier (R2) | 1730949 | docker-compose.yml:136,164,167,204-223; otel-collector.yaml:36-58; prometheus.yml:57-64; Makefile:26-31 | sha256:f655cdd069646ff04899a5a66b4cf75ae94cae52b1702c60ba2221e69bb77a35 | verified (merged #24) |
| T0.6 | 2026-07-16 | sme-liaison (R2) | adversarial-verifier (R2) | 5176374 | docs/sme-dispatch/ 0 fabrications, 38/38 paths valid, 3 drift flags confirmed true | sha256:56832a48e31b1a870b1a4b08ffcdf5404c833606822cca12e1392715e67d7b11 | verified (merged #27) — dispatch blocked(external: SME roster) |
| T2.1 | 2026-07-16 | gates-engineer (R2) | adversarial-verifier (R1) | f385d94 | validation cli.py:138-196,199-222; crossref.py:73-112; policy.py:96-171; 8 mutations + XXE + frozen-tamper all exit 1 | sha256:ee6f47640441b093e3195abc2ff1eb4cb2d7f7efd0442ee615e30c01a3905350 | verified (merged #31) |
| T2.3 | 2026-07-16 | gates-engineer (R2) | adversarial-verifier (R2) | d473451 | ci.yml:259-283,376-395,515-537,602-624 three-bucket/two-phase fail-closed guards; cov-fail-under=85 (87.09%); cycle-1 exit-2 fail-open fixed | sha256:8d0531c239cb04fa4f9d2a35d42c7176d3ad316599d09c4a02e17f3b379c63f9 | verified (merged #29) |
| T2.4 | 2026-07-16 | security-hardener (R2) | adversarial-verifier (R2) | c2ac275 | security.yml gitleaks job + .gitleaks.toml (2 anchored entries); checksum matches upstream byte-for-byte (partial: gitleaks) | sha256:df9132db61a86eeab3d0c6da457700c4a4b217d5dc063a6bfdb0d725fdf14a85 | verified (merged #28) |
| T2.5 | 2026-07-16 | compliance-analyst (R1) | adversarial-verifier (R1) | a7db399 | docs/compliance/rn-currency-review.md + 9 independently re-verified sources; SIP-extinction finding held at verify-SME (draft) | — | verified-as-draft (merged #30) — SME confirmation blocked(external) |
| T1.8 | 2026-07-16 | policy-guardian (R1) | adversarial-verifier (R1) | ca23263 | docs/adr/0025-pep-policy-unification.md + docs/design/T1.9-ceiling-enforcement.md; verdict REVISE with 4 revisions applied (design phase) | — | design verified (merged #32) — implementation pending |
| T1.9 | 2026-07-16 | policy-guardian (R1) | adversarial-verifier (R1) | ca23263 | docs/adr/0025-pep-policy-unification.md + docs/design/T1.9-ceiling-enforcement.md; verdict REVISE with 4 revisions applied (design phase) | — | design verified (merged #32) — implementation pending |
| T0.5 (gate precision fix) | 2026-07-17 | gates-engineer (R2) | — | 80b34c8981b98fd363bf94b9568bafc0146a94d9 | scripts/ci/check_evidence_ledger.py:21-24,49-51,72-96,115-129 | sha256:3913d29409889d790bc4934cfffef30adae0b2628c56b64ec643e93a97fb9316 | implemented — unverified |
| T1.10 | 2026-07-17 | audit-persistence-engineer (R2) | adversarial-verifier (R1, cycle 1: REVISE — core claims VERIFIED, defect H1 fixed) | 09d87cc75806663a8eb4c941c3585aea9469960b | gateway/audit_postgres.py (PostgresAuditSink.emit fail-closed + per-tenant advisory lock; verify_chain); gateway/audit.py (GENESIS_PREV_HASH sentinel; tenant_id/agent_version/dmn_versions non-null; canonicalize_jsonb — H1 fix: hash the jsonb-round-trip-stable form; verifier's -0.0 repro + 18-payload sweep green, tamper detection re-proven on same payloads); migrations/env.py SET-search_path + missing-commit fixes; test_audit_postgres.py kill-test (real compose postgres, 200 records, SIGKILL after 20 confirmed, restart, 0 lost, chain verifies — re-run post-H1, unchanged); docs/adr/0027-audit-transport-postgres-first.md | sha256:e3baf7785747dec553c2087ad6a8a03bd8a3ca537b909efc35a9e1536b9e5f4d | implemented — H1 fixed, awaiting re-verification |
| T1.1 | 2026-07-16 | runtime-spine-engineer (R1) | — | a0427d298ccd1ae1612e0b6fa8cbfc0354339240 | docs/design/T1.1-runtime-spine.md | — | design drafted — pending R1 verification |
| T1.2 | 2026-07-16 | runtime-spine-engineer (R1) | — | a0427d298ccd1ae1612e0b6fa8cbfc0354339240 | docs/adr/0026-worker-standardization.md | — | design drafted (ADR-0026 draft) — pending R1 verification |
| T1.3 (artifact fix: SP-OP-AUTH-001 gateway) | 2026-07-17 | process-engine-specialist (R2) | — | 21e6cfdb6d42ba07ebd2ac66cbf25e317dfb3ab2 | spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:542-552 (Flow_GWDec_Aprovar conditionExpression added; gateway default="Flow_GWDec_Invalida" unchanged); live docker-compose cibseven:2.1.0 full-tree redeploy — 20/20 process defs (16 BPMN incl. SP-OP-AUTH-001 v2) + 55/55 decision defs (54 DMN) succeed, 0 rejected; idempotent re-run (0 new); make validate-artifacts OK 0 errors; full suite 716 passed | sha256:e8c97893a7944f58a9e7e0712bd7037aac8b29c7571058b5f913ad9de2c6172b | implemented — unverified (pending R1 adversarial verification) |
