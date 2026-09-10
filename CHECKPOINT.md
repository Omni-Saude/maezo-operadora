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
