# Graceful pause — 2026-09-10

User requested immediate continuity and GitHub preservation because the session budget was nearly exhausted. Product completion is NOT claimed. All eight specialists are stopped (seven completed; one errored); all executing specialists report no owned live workloads. Do not retry the flagged workspace task through another agent. No new heavy test or deployment was started during shutdown.

## Resume entry point and source refs

Read this checkpoint before historical status text. Continue the product-first execution plan at local `docs/prompts/maezo-product-first-execution-plan.md`; canonical contracts and accepted ADRs still govern. Verify the actual worktree, branch, HEAD, status and remote refs before every mutation. Preserve the intentionally dirty ROOT checkout.

| Purpose | GitHub branch / commit | Acceptance |
|---|---|---|
| Main, PR #362 merged | `main` / `c5479e706c0dbfdf7d312ed82d7208ab6c72e5ad` | Portable verification consumers merged; clean main worktree verified |
| Integrated C27 plus main | `completion/product-c27-auth-lifecycle` / `31fba289370d59b26df77e383cb465e6a6e7b5b9` before this handoff commit | Source-reviewed composition; real runtime/deployment acceptance pending |
| Staff census producer | `completion/product-staff-census-producer` / `b6befb27eb07d89760a2509832fa68a192e77a1a` | Unreviewed, incomplete WIP, remotely preserved |
| CONTENT1 production | `completion/product-content1-production` / `3c4158515816ff7f3bad8d3bdb28a29cb6b9cb0b` | Unreviewed, incomplete WIP, remotely preserved |
| Actual inbox body UI | `completion/product-content1-read-ui` / `356f2c6273137a791e8f542859bff9df3d508f07` | Source approved and included in C26/C27 |
| AUTH source donor | `completion/product-auth-effect-authority` / `484d591bfddcefd6b67c27ab068d79a7df6f2fac` | Reader-ceiling amendment included in C27, not in donor |
| Approved CI preparation | `completion/ci-static-contract-oracles` / `d067bf35e8a0bba8a02663cfdde344b49b39a1d0` | Approved deltas still need product composition |

PR #361 remains draft at `520d5ffa6b796680ec687253c8cfa68a218307f7`; PR #363 remains draft at `8dcc683c072aa8417b2f20b930f15fa712faa300`. Their heads are older than the preserved product branch. Preservation does not mean approval or merge. Refresh GitHub state on resume.

Physical worktrees under `/Users/familia/code/maezo-completion-wt/`:
- Main: `successor-main-verified-dd5`, branch `main`.
- Product: `product-c16-auth-read-catalog`, local branch `completion/product-c17-document-bridge`, published to `completion/product-c27-auth-lifecycle`.
- Census: `product-e05-staff-exact-case`, branch `completion/product-staff-census-producer`.
- CONTENT1 production: `product-e05-intake-case-navigation`, branch `completion/product-content1-production`.
- ROOT `/Users/familia/code/maezo-operadora`: `completion/root-coordination-preserved-20260909`, HEAD `4c3e69f5fd17f7202e75157f563d7e0cdfcc31e9`, intentional local coordination changes; do not reset, clean or switch blindly.

## Accepted source and evidence limits

C24 `6e6ac6487a959b94041e5582ca0e2a0df9560169` integrates staff list backend/native/UI and expiry fixes. C25 `89d05eb5a7ad1379d9c568eec80c6236c7a6f4e8` integrates staff deployment source and provider-lock/mock fixes: validate and 32 mocked Terraform cases passed; no cloud apply. C26 `23b68773ee3abc8da59a05b8b1d6c4573db6cedf` integrates actual PHI inbox content read. C27 `0f4a65b224aaede948eb28f486876bb5a73fbaf4` integrates AUTH lifecycle and reader-ceiling repairs; independent combined source review approved. Offline Java compilation passed; this does not prove installed native authority or engine behavior. Main merge into product `31fba289` preserved all prior product paths and added the ten accepted main files without conflict.

No browser-to-engine AUTH/ESCALATION/PAGTO journey, staging deployment or final gate is accepted by this pause. No global percentage should be inferred from tests, merged PRs, read bindings or source presence.

## Next execution, in order

1. Refresh refs/PRs and read private frozen evidence; restore specialist ownership without new worktrees when clean released ones suffice. Keep source authors, independent reviewers, distinct repairers and SAME reviewers separate.
2. Compose approved CI delta from `520d5ffa..d067bf35`. Preserve the newer credential-boundary test from `b82d0078`: nine HTTPX seams and two exact credential AST assignments. Eleven other paths matched the old base at last inspection. Do not overwrite current source with whole old files. E501 is already fixed; old wide CI failures are not proof that those repairs remain absent. No broad rerun until the candidate addresses applicable known causes.
3. Census WIP: 16 initial focal cases passed; 513-case source parsing timed out. One finite diagnostic ended at 7.290s, dominated by canonicalization; its PID/process group is absent. Four parser controls pass; configuration fixture fails strict tuple/list validation. Type/native checks and real-engine succession coverage are pending. Complete approved C1/C2 retained-generation/source contracts, review source independently, then compose. Do not repeat the large test without a changed hypothesis or repair.
4. CONTENT1 production WIP: 12 new files, focused 22 tests passed, local Ruff/format/mypy receipts retained; no independent source approval. Remaining issues: partial-construction resource cleanup, indirect SET ROLE privilege qualification, authenticated key currentness/revocation source, current requester-peer authority, actual TLS proof. Shared conditional identity revocation and private listener lifecycle are not authored/composed. Reuse the original identity store and C27 AUTH lifecycle; never introduce a second authority store or trust forwarded certificate headers.
5. CONTENT1 P1-R1 remains unresolved: persist stable immutable requester identity before FIRST freeze, deduplicate and recover by requester ID after lost first acknowledgement. No fabricated head or receipt store.
6. DX1 document intake/upload/request/download/response contract work is only partial external notes. Complete trace to existing custody, case identity, process starts and real response recovery. PF1 beneficiary/provider/guide source/forms and RC1 actual outcome/receipt authority are also remaining product building.
7. Continue staff case/dossier/history/operations/admin, ownership controls, AUTH → ESCALATION → PAGTO and remaining decision paths, beneficiary/provider journeys, Tasy/datalake adapters, coherent BPMN/DMN/MCP/workers, staging and durable Kafka. Review BPMN reuse and BPMNDI before adding processes; verify downstream impact. Essential construction repairs stay in scope; separable repair backlog may be assigned to human/LLM joint development.
8. D7 successor runtime mandate remains unexecuted. Never rerun the old rejected launcher or silently increase timeout. Runtime, browser accessibility, actual authority/PHI installation, deployment/recovery and two fresh independent final gates remain required.

## Evidence custody and maintenance

Private evidence root: `/Users/familia/code/maezo-completion-evidence/product-first-20260910/`. Do not upload raw PHI, credentials or private evidence to GitHub.

Key packets: `product-c24-staff-list-composition`, `product-c25-staff-deployment-composition`, `product-c26-inbox-content-composition`, `product-c27-auth-lifecycle-composition` (includes MAIN-SYNC), `product-c26-c27-composition-review`, `e05-staff-census-producer/PAUSE.json`, `e04-content1-production-source`, `e04-dx1-construction-paused/HANDOFF.md`, `native-review/e04-content1-private-ingress-contract`, `ci-staff-httpx-scoped-seam-root-review`.

ROOT tracked diff and private continuity files are archived under `graceful-pause-20260910/` with hashes. Frozen original evidence and historical scripts remain intact. New executable consumers should use portable replacements; do not preserve obsolete folders merely to satisfy hardcoded consumers.

Post-PR362 maintenance uses `main-landing-inventory/pr362-postmerge`, `pr362-candidate-qualification` and `pr362-postmerge-final-metadata`. Last full premerge census: 493 existing registered worktrees; four standalone repositories had no newly stranded reachable commits. No worktrees were created or deleted in this turn. Five qualified safe candidates are deliberately retained during the user-requested pause; 21 concrete remaining executable consumers affect ten targets, with replacement batches and exact source witnesses recorded. Next smallest replacement is pr357 source-witnesses. Three other unpinned targets need finite private custody rehoming. No destructive cleanup is authorized by a mere containment guess. Complete final inventory receipt before claiming final census; on timeout retain partial output and do not silently retry.

This checkpoint is a restart contract, not product approval. Engineering and external owner/clinical/financial/regulatory/DPO approvals remain distinct. Resume only when the user resumes the paused execution.

Final maintenance receipt: fetch completed; canonical final metadata inventory exited 0 within its 20-second bound. Main worktree remained clean and aligned with origin/main. Zero removals.

## Express preservation and cleanup — 2026-09-10

The user authorized an express commit/cleanup pass and lower-cost independent checks. Product implementation and heavy validation remain paused. This section supersedes earlier statements that no directories were removed or that ROOT coordination remains uncommitted.

- ROOT coordination, plan and workspace setup were committed and published on `completion/root-coordination-preserved-20260909`; one credential-like historical documentation value was omitted from the public copy, original preserved privately.
- Nine historical tracked/untracked source snapshots are now committed on the recovery branches below and verified remotely. Their original working directories and indexes were not modified. These are preservation snapshots, not independent source approvals; do not merge them wholesale or reintroduce superseded repairs. Compare each against the integrated candidate and existing source approvals before admitting any unique delta.
- Five independently qualified worktrees removed normally; four local branches removed with `git branch -d`; one remote merged branch (`completion/portable-verification-consumers`) deleted with an exact-SHA lease. Other candidate remote branches were absent and were not touched. Registry now has 488 worktrees, down from 493. No forced worktree removal, reset, clean or prune was used.
- Main remains `c5479e706c0dbfdf7d312ed82d7208ab6c72e5ad`. No product PR was merged during this express pass.
- Cleanup verifier used `gpt-5.6-luna` for bounded independent read-only checks. Candidate HEAD/tree/status, ancestry, no open PR, ignored-file boundaries, locks and path-scoped process handles were checked; previous executable-pin proof was reused within its scope.
- Full final metadata inventory hit its 20-second limit and was not retried. Exact after-worktree/refs/remote-head/PR receipts and all five candidate-specific checks are preserved separately; do not claim a fresh exhaustive audit of all 488 worktrees or all ignored artifacts.

### Concrete merge blockers and cheaper-model handoff

Current PR #361 is draft/BLOCKED at `520d5ffa`; PR #363 is draft/UNSTABLE at `8dcc683c`. Both have failed lint/type/unit, artifact, release-capability-floor and flip-path-review checks; PR363 evidence-ledger check was cancelled. The preserved product candidate is newer than these PR heads. Existing failures must be reconciled against exact candidate code before requesting new broad CI; no bypass or fabricated owner approval.

Next bounded preparation: apply the already independently approved CI delta `520d5ffa..d067bf35` to the current product branch, preserving the newer nine HTTPX seams/two credential-assignment coverage from `b82d0078`. Eleven other paths matched the old base at last inspection. Recheck this before applying. A different reviewer should confirm the exact composition and applicable focused checks; only then update the PR candidate and run required CI once. Do not repeat the old wide failures against an unchanged candidate.

A lower-cost specialist may verify exact refs/blobs, map existing evidence to gates, check generated-contract drift, and execute a named focused check. Give it one pinned base/candidate, owned paths, existing evidence and explicit completion criterion. It must report failures and unknowns, cannot replace runtime/CI evidence with its opinion, and cannot approve its own edits. Preserve the required distinct author/reviewer/repair/SAME-review roles and final gate eligibility. No broad engine/database work is authorized by this express handoff alone.

Census and CONTENT1 production WIPs retain the unresolved construction and source-review requirements listed above. Security-sensitive authority, PHI lifecycle and real TLS/engine qualification still require the applicable independent review and runtime evidence. Missing beneficiary/provider features remain construction backlog rather than mere merge administration.

### Historical WIP recovery refs

| Original worktree | GitHub recovery branch | Snapshot |
|---|---|---|
| `cycle2-composition-gate-g1-repair` | `preservation/express-20260910-cycle2-composition-gate-g1-repair` | `1e2cd9a683536ae43d97a90017a784132fbe34dd` |
| `cycle4-controller-stage2` | `preservation/express-20260910-cycle4-controller-stage2` | `dfd5e9b125d2550772ec1fc913ee51848d0e2180` |
| `portal-atomic-engine-verify` | `preservation/express-20260910-portal-atomic-engine-verify` | `d9e12e11cb3d8a913d0ad5a1d0abbc3b0c73dd9b` |
| `portal-transaction-delta-verify` | `preservation/express-20260910-portal-transaction-delta-verify` | `6b1f9d8619397d3bd8f52919a33403b05655041a` |
| `strategy-c1-security-ui-review` | `preservation/express-20260910-strategy-c1-security-ui-review` | `a69c995cf2582f60946fca7b01e84e5ee19c2dda` |
| `strategy-c1-ui` | `preservation/express-20260910-strategy-c1-ui` | `33f767f9bce6e8da14f654503cb559b2a5380ad2` |
| `successor-d7-startup-stage-observation` | `preservation/express-20260910-successor-d7-startup-stage-observation` | `f6cd5662f1355524e739195af8980d149b3395dd` |
| `agent-small-fixes` | `preservation/express-20260910-agent-small-fixes` | `791bc92d2d43327ccc1bf817688b55adc8c159ae` |
| `remediation-docs` | `preservation/express-20260910-remediation-docs` | `a3c320e62e601c0b30e59afebe06a6ca0f04c904` |

Private receipts: `/Users/familia/code/maezo-completion-evidence/product-first-20260910/express-cleanup-20260910/`. The synthetic reviewer `form_key` scanner finding was independently classified as a business identifier; no global suppression or source change was added. Dependency directories and ignored private evidence remain local. The earlier paused product plan and acceptance limits otherwise remain in force.

## Current continuity update — all-worktree WIP preservation, 2026-09-10

Fresh bounded read-only Git status inspection completed for all 488 registered worktrees and the four standalone repositories (492 locations), in 13.85 seconds, without missing paths, errors or timeouts. This successful source-status inventory supersedes the earlier timed-out full metadata attempt for WIP discovery only; it does not claim a new exhaustive ignored-evidence audit.

All 22 discovered uncommitted source files match the nine published preservation snapshots byte-for-byte, with exact GitHub ref checks. Two untracked node_modules dependency links remain excluded; private ignored evidence/caches remain local. All four standalone repositories are clean. Historical WIP worktrees intentionally remain unchanged and can still show edits: the commits live on recovery branches with their original HEAD as parent. No distinct staged source version was present in the fresh status results.

The current mutable PLANS, PROJECT, CHECKPOINT, RUNBOOK, SESSION, docs/plan and both current orchestration prompts now point here. The immutable graceful handoff packets, package manifests, CONTINUE-2026-09-09, NEXT-ORCHESTRATOR-HANDOFF and PENDING-TASKS historical snapshots remain unchanged. New current-file hashes and prior bytes are preserved in the separate continuity-update receipt.

WIP-only execution prompt: [maezo-wip-preservation-only.md](docs/prompts/maezo-wip-preservation-only.md). It authorizes source snapshots and remote verification without lint/build/test/runtime/product-release gates. Secret handling, non-destructive preservation and exact remote verification remain required. This permission does not approve incomplete code for main or waive product acceptance gates.

Evidence: `express-cleanup-20260910/ALL-WIP-FRESH.json`, `CURRENT-WIP-BYTE-VERIFICATION.json`, `HISTORICAL-WIP-PRESERVATION.json`, and `continuity-update/`. Product build/merge blockers and recovery branch table remain in the preceding express section. No feature, engine, database, cloud deployment or new broad CI execution was started by this preservation pass.
