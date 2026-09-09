# Maezo completion plan — consolidated with verified orphan recovery

## 1. Source preservation, baseline, and delivery objective

This plan replaces the shorter second plan. It incorporates all substantive requirements from the saved [first plan](/Users/familia/Downloads/plan.md), adds the orphan-worktree findings, and preserves its research references, architecture decisions, implementation details, and acceptance criteria.

Source fingerprint: `d0218f1dff955bd09fe84300121c92412d91c0b739fdc847c71c50f9794e71c7`.

| Original section | Preserved here | Additions |
|---|---|---|
| Verified baseline and delivery objective | §1 | Complete inventory of the three orphan roots |
| Specialist execution and independent gates | §2 | Explicit recovery-evidence checks and stale-verifier handling |
| Recovery and platform hardening | §3 | Four independent R6 branches, uncommitted repair, legacy-document reconciliation |
| Custom portal product and technical specification | §4 | All benchmarks, audiences, interfaces, security and transaction details retained |
| Infrastructure, validation and completion gates | §5 | Recovery acceptance and worktree-preservation gates |

The program combines gap-closure v6, the interrupted fleet-hardening program, and the authorized custom portal. Original finding IDs, dependencies, and evidence remain traceable when duplicate work is consolidated.

### Verified recovery baseline

These are inspection results, to be refreshed before execution:

| Area | Evidence | Consequence |
|---|---|---|
| Main | `fe91b912`; main CI and real-engine integration succeeded | Initial integration baseline |
| Gap-closure train | `r6/train-1`, `2db4bd7d`, unmerged | Historical static approval does not cover its interrupted engine verification |
| Fleet train | `fleet2/train-b`, `4cb387ab`, unmerged | Earlier engine run failed two Helena escalation tests; repaired tip lacks completed verification |
| Recovery artifacts | Commits, durable reports and transcripts survive; temporary scratchpads are empty | Recover historical evidence, then reproduce current behavior |
| Registers | 260 gaps; 287 decision entries, A=86/B=189/C=12 | Reconcile actual inventories, not stale summary counts |
| Human workflows | 43 User Tasks; 38 lack `formData` | Build a complete versioned task/form catalog |
| GitHub governance | Four required checks; production environment lacks reviewer protection | Enforce additional release gates and repair governance separately |

All **83 registered worktrees** in the requested roots were inspected for ancestry, outstanding commits, working-tree changes, and non-cache files outside version control.

| Root | Findings | Required disposition |
|---|---|---|
| `maezo-fleet2-wt` | 52 worktrees; every committed tip is contained in main or `fleet2/train-b`; train B has 147 commits outside main | Recover component evidence and verify the assembled train |
| `maezo-r6-wt` | 30 worktrees; train 1 has 52 commits outside main; four independent branches contain 28 distinct additional commits outside both trains and main | Recover the train and all four branches separately |
| `maezo-wt` | One worktree, `remediation-docs`; no commits outside main; nine extra documents, three identical and six different | Preserve provenance and reconcile the differing historical documents |

The four independent R6 branches are:

- `adr-batch`: 13 outstanding commits.
- `agent-small-fixes`: eight outstanding commits plus an uncommitted outcome-vocabulary test; its verifier checkout predates the author tip.
- `r009-phi-scrub-prereqs`: one outstanding commit with unresolved verification findings.
- `r228-programa-gate`: six outstanding commits requiring gate repairs.

`r093-inad-event` remains at the main baseline. `wamid-outbound` remains at the train baseline. Neither branch name establishes completed implementation.

The legacy checkout contains older ADR-0040, handoff, execution-plan, owner-decision, and gap-register documents. All 215 legacy gap IDs exist in the current 260-entry register; compared legacy notes, evidence, and source text remain present. Current execution-plan and owner-decision versions contain additions without removing legacy lines. Preserve these artifacts as historical evidence; do not restore stale versions over current documents.

Preserve the uncommitted terminal-outcome test, unrelated `.codex/` files, and all exclusive historical evidence. Ancestry proves commit containment; tests and semantic review must prove retained behavior.

**Delivery means:** all agent-executable work completed and independently verified, both interrupted programs reconciled, the custom portal implemented, and deployment demonstrated through reproducible evidence. Production activation additionally requires actual human approvals, credentials, and operational prerequisites.

Autonomous engineering and product-design authorization does not substitute for a physician’s decision, DPO signature, or approval of an unbuilt production release.

## 2. Specialist execution and independent gateways

Use only bounded specialist mandates. Never delegate to a `general-purpose` role.

| Responsibility | Routing |
|---|---|
| Security, PHI, financial/process semantics, human gateway, engine transactions, semantic integration | `gpt-6-astra`, high/xhigh |
| Specified implementation, frontend, evaluations, tooling, documentation | `gpt-5.6-sol`, high |
| Single-file mechanical lookups | `gpt-5.6-luna`; never inventories or safety judgments |
| Final assurance | Two fresh Astra specialists, neither involved in implementation nor intermediate verification |

The user authorizes expanding the specialist pool according to ready work and actual capacity (2026-09-09). Respect the runtime concurrency limit, including root and nested agents; this session currently exposes four simultaneous slots. The total pool may grow across successive waves, without assigning general-purpose roles. Serialize engine suites; do not run the full unit suite alongside the engine. Verify live slots before claiming a newly staffed team.

Execution scheduling is readiness-based within these limits. The wave and delivery orders express real dependencies; they do not require unrelated qualified source work to wait for CI or a serialized service lane. Use isolated worktrees and disjoint ownership, freeze shared interfaces before parallel consumer work, and dispatch the next ready specialist when a slot becomes free. ROOT retains shared ledger/integration/GitHub ownership and the exclusive engine/full-unit queue. The execution prompt's “Safe parallel dispatch” section defines this scheduling procedure without reducing acceptance requirements.

Every specialist brief must specify:

- Exact baseline SHA, owned paths, gap IDs, and applicable contracts, ADRs, and DLs.
- Current reproduction, required resulting behavior, and affected interfaces.
- Forbidden scope, preserved behavior, and human-dependent boundaries.
- Positive, negative, failure, recovery, and propagation tests.
- Evidence deliverables: commit, command, environment, result, and artifact digest.
- Stop-and-report conditions for conflicting contracts or unavailable external prerequisites.

The work-package cycle is:

**Propagation map → reproduced RED evidence → implementation and regression fence → ledger → independent adversarial verifier → third-specialist repair → original verifier’s delta → integration review → successful CI → merge → main verification → repository maintenance.**

Verifiers cannot implement their own recommendations. Changed commits invalidate affected approvals. Historical reports must identify their exact reviewed revision; stale verifier worktrees do not approve newer author tips.

The orchestrator independently checks diffs, SHAs, commands, results, evidence links, and ledger preservation. Tests must demonstrate behavior, including meaningful adversarial mutations; symbol presence and documentation matches are insufficient.

Keep coordination in existing `PLANS.md`, RUNBOOK, CHECKPOINT, evidence-ledger, and handoff artifacts. Store reproducible reports durably. Temporary directories cannot be the sole evidence source. Follow the prompts’ language conventions for commits, ledger, RUNBOOK, and the English owner report.

## 3. Recovery and platform-hardening sequence

### Wave 0 — Preserve work and reconstruct the executable backlog

1. Record all 83 worktree paths, branches, SHAs, dirty-file hashes, evidence locations, and dispositions. Preserve uncommitted repairs and differing historical documents before integration or cleanup.
2. Freeze and hash audit/register inputs. Recover useful briefs and reports from surviving transcripts, clearly labeled historical.
3. Reconcile every gap, decision entry, fleet finding, and successor regression to its reproduction, component commit, train, verifier, and remaining dependency.
4. Deduplicate implementation across programs while retaining every original ID and required assurance/triage field.
5. Recreate missing reproducible engine scripts from the tracked stack. Use one explicit lock owner; teardown is permitted only after that process acquired the lock.
6. Establish baseline results with the intended `uv run` environment. The four observed local failures arose from missing `python` on PATH; their complete module passed under `uv run`. This does not establish a fresh full-suite pass.
7. Separate GitHub alarm failures from successful main CI. Resolve the confirmed CODEOWNERS-access failure without weakening checks.
8. Reconcile legacy documents by content and provenance. Commit containment alone never authorizes deleting exclusive evidence or assuming behavior survived integration.

### Wave 1 — Repair and finish R6 train 1

Recover its existing nine components instead of rebuilding them.

- **LGPD execution:** reject malformed request types deterministically, including unhashable values; prevent rejected raw values from reaching logs; refuse without task completion, success responses, or unintended persistence.
- **Webhook privacy:** cover rendered exception traces and nested error fields, not just explicit message-ID fields. Preserve deduplication, retry, and acknowledgement behavior.
- **Identity disclosure:** distinguish separate ServiceAccounts from actual application audit identities. Keep the absent network-change daemon declared dormant until implemented and verified.
- **Evidence integrity:** correct stale counts, duplicate identifiers, CODEOWNED lists, and overstated verification claims.
- **Product decision:** explicitly amend the earlier Tasklist-only choice through the decision log and portal architecture ADR. Preserve its history.

Run the full collected integration set, including LGPD and escalation, against the repaired train. Renew independent approval and require successful real-engine CI before merge.

### Wave 2 — Finish fleet train B on the new main

Integrate the verified R6 result into fleet train B. Preliminary analysis identified 30 overlapping files and 15 conflicted files; resolve conflicts semantically.

- Preserve both programs’ agent-graph changes, evaluation assertions, and evidence.
- Reprove Helena malformed-classifier and classifier-exception paths through an actual human task.
- Preserve sender-aware leak checks while incorporating the nine journey evaluations.
- Complete FHIR surface parity, exception handling, ingress boundaries, dossier handling, protocol compatibility, idempotent messaging, and remaining propagation checks.
- Preserve line endings and prove zero loss of ledger entries and intended behavior.

Run the full real-engine suite on the assembled commit. Renew train approval and merge only after CI succeeds.

### Wave 3 — Recover independent branches and close successor findings

Process all four independent R6 branches against the combined main, including the uncommitted test and stale verifier evidence. Complete unstarted INAD and outbound-WAMID work.

| Work package | Required completion |
|---|---|
| R-009 privacy prerequisites | Reject absent/malformed manifests, partial promotion attempts, invalid drain intervals, and placeholder provisioning evidence; eliminate vacuous passes and false coverage claims |
| R-228 population gate | Reject arbitrary unsigned YAML parameters and documentation-only consumer references; require a canonical ratified parameter enforced at the actual publication seam |
| R-199 evidence manifests | Reject duplicate YAML keys; preserve blank signatures/dispositions until supplied; distinguish build-time checks from runtime enforcement |
| A2A requested facts | Preserve interrupted repairs and close the durable claim-before-publication loss window through an enlisted transaction and durable outbox |
| Financial typing | Enforce exact integer centavos through transport, including pre-shaped engine-variable dictionaries |
| Remaining privacy | Cover outbound WAMID, refusal paths, dossier narrative zones, and obsolete identity references |
| Tooling | Close constructor-alias bypasses, ledger collisions/append defects, script-lint omissions, and stale citation/count mechanisms |
| Observability | Prove alert-label matching, exporter wiring, and dashboard population using emitted metrics |
| Remaining inventory | Complete every reproducible agent-executable finding across both programs and the newly discovered regressions |

A2A acceptance requires crash recovery, eventual publication, and stable deduplication. Do not claim exactly-once transport across independent systems.

Reconcile the live-evaluation credential interface: CI supplies a different variable from the one live tests inspect. Require nonzero collection and visible execution results. Missing credentials cannot produce an unqualified evaluation pass.

Preserve unratified and dormant boundaries, including privacy promotion, passive L0 actions, and unpublished population consumers. Complete implementation boundaries, schemas, negative tests, and review packages without inventing clinical, actuarial, retention, regulatory, or privacy values.

Execute remaining work in dependency order: regressions → security → contracts/specification → observability/evaluations → maintenance. Recompute completion counts from item-level evidence after each train.

## 4. Custom portal: preserved research and implementation specification

### Product research and experience

Build one pt-BR Maezo portal with separate employee, beneficiary, and provider experiences.

Retain all three researched design patterns:

| Benchmark | Maezo adaptation |
|---|---|
| [Guidewire activity assignment](https://docs.guidewire.com/cloud/cc/202511/cloudapibf/cloudAPI/topics/141-Framework/01_activities/c_assigning-activities.html) | Group queues, explicit ownership, claim/reassignment behavior |
| [Backbase banking operations](https://www.backbase.com/solutions/banking-operations) | Shared case workspace containing evidence, preparation, and human decisions |
| [Airbus operations control](https://www.skywise.com/en/digital-solutions/planning-operations-control) | Operational visibility and coordinated handling of exceptions |

These references inform interaction design; Maezo contracts remain the source of business rules.

| Audience | Capabilities |
|---|---|
| Atendimento and supervision | Intake, assigned/group queues, escalation, handoff tracking |
| Medical auditors, clinical coordination, medical board | AUTH, clinical reviews, program decisions |
| Accounts, appeals, reimbursement, finance | CONTAS, RECURSO, REEMBOLSO, PAGTO within actual authority |
| Network management and legal | Credentialing, decredentialing, network remediation |
| Regulatory and legal | NIP, ANS submissions, corrections, NACK, cancellation, delinquency |
| Fraud investigation | Restricted evidence, custody, authorized human conclusions |
| DPO/privacy reviewers | Identity-verified data-subject requests and permitted dispositions |
| Tenant administrators and operational observers | Membership and operational health; no implicit clinical or PHI access |
| Beneficiaries | Their requests, status, outstanding documents, communications, receipts |
| Providers | Their guides, network requests, accounts/glosas, appeals, receipts |

Retain the BPMN’s 27 static candidate-group identifiers. Resolve four dynamic group expressions server-side from engine state and intersect them with authenticated membership.

Employee navigation: **Overview · My work · Team queues · Cases · Documents · Operations · Administration**.

The case workspace contains summary, deadline, current owner, dossier, evidence provenance, permitted decision form, and chronological history. Decisions require explicit review and return a receipt. Exclude bulk clinical, adverse, or financial approval.

Cover all 43 human tasks, including coordination takeovers, across 15 workflow families. The timer-started ANS cron process appears in operational monitoring without a manual-start shortcut.

### Frontend and public interfaces

- React, TypeScript, and Vite under `src/maezo/portal/web`.
- FastAPI BFF under `src/maezo/portal/api`, deployed separately from agent runtimes.
- Accessible shared components, semantic tables/forms, visible focus, textual status indicators, and responsive layouts.
- WCAG 2.2 AA acceptance with keyboard and real screen-reader validation; automated scans alone do not establish conformance. [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- No PHI in URLs, browser analytics, persistent browser storage, or offline caches.
- Refresh visible queues every ten seconds. Display freshness and dependency failures. Decision validation reads authoritative engine state.

Versioned `/api/v1/portal` APIs cover:

- Session identity and entitlements.
- Eligible tasks, task snapshots, claim/release, and decision submission.
- Command status and receipts.
- Subject-scoped cases, history, document requests, and attachments.
- Role-appropriate operational summaries.

Define immutable `HumanPrincipal`, `TaskSnapshot`, `TaskDecision`, and `HumanCommandReceipt` contracts. Generate the TypeScript client from OpenAPI.

Key forms by process definition/version and task definition. Allow only contract-approved inputs and outcomes. The browser cannot supply arbitrary engine variables, actor IDs, tenant IDs, or approval tiers.

Transmit monetary centavos as validated decimal integer strings across the browser boundary, preserving backend integer precision. Regulatory timers remain engine-owned.

For PAGTO admissibility, expose read-only `lastro_origem`, `lastro_decisor_id`, duplication evidence, and the contract’s `PROSSEGUIR`/`DEVOLVER` decision. Evidence of an obligation must not become confirmation of that obligation.

### Human authentication and authorization

Introduce a dedicated human Cognito client using OIDC authorization code with PKCE. Existing machine-to-machine configuration does not establish human identity. Keep tokens server-side; issue opaque Secure/HttpOnly/SameSite=Lax session cookies with CSRF and Origin protection. [AWS app-client guidance](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html)

Bind tenant through deployment and authorized membership. Bind identities to immutable issuer/subject values. Verify beneficiary/provider relationships before granting record access; login alone is insufficient.

Implement a separate, always-enforcing human gateway with:

- Typed task transport and gateway factory.
- Dedicated human credential partition and workload identity.
- Role, assignment, process-version, and evidence-revision checks.
- Engine permissions denying agent credentials human-task mutations.
- Human provenance distinct from the service performing the request.

Hard autonomy restrictions remain enforced. Agent permissions must not expand to support portal actions.

### Atomic decisions, audit, and recovery

Implement a Java engine-command extension in the pinned CIB Seven 2.1.0 image, using its plugin extension mechanism. The atomic command behavior below is a Maezo implementation requirement and must be proven through real-engine tests. [CIB engine plugins](https://docs.cibseven.org/manual/2.1/user-guide/process-engine/process-engine-plugins/)

Within one engine transaction:

1. Validate the signed human command.
2. Check current task/evidence revision, assignment, and allowed inputs.
3. Write permitted variables and complete the task.
4. Persist the command receipt.

Use `(tenant, task_id, command_id)` plus payload digest for idempotency. Identical retries return the receipt; conflicting reuse or stale decisions return conflict. Prove races against timers, reassignment, and other task clients.

Persist audit intent and a dedicated human-command outbox in one tenant transaction before dispatch. Preserve audit-chain locking and integrity when extending transaction enlistment. Reconcile uncertain outcomes against engine receipts. BFF acceptance must never appear as completed execution.

Secure engine REST and update every affected runtime caller. The development testchannel proxy must not become the production portal backend.

### Portal delivery order

1. Architecture ADR, role/task catalog, API schemas.
2. Human authentication, gateway, engine extension, audit recovery.
3. Employee queues and case workspace.
4. AUTH, ESCALATION, and PAGTO vertical journeys.
5. Remaining workflow families and all task forms.
6. Beneficiary/provider identity binding, scoped views, intake, document exchange.
7. Accessibility, usability, performance, browser-to-engine validation.

Reuse typed process entrypoints and idempotent starts for intake. Keep the deliberately dormant TISS bridge dormant unless separately authorized and implemented.

## 5. Infrastructure, validation, and completion gates

### Deployment architecture

Use ECS/Fargate in `sa-east-1`, following the recorded deployment decision. Reconcile older Helm/EKS-oriented CD without provisioning both architectures.

Complete:

- Reproducible application, frontend, and engine-extension images with pinned dependencies, SBOMs, and signatures.
- Separate tenant/environment identities, networks, secrets, and database access.
- Human identity configuration, TLS, restricted engine access, and secret rotation.
- Durable production Kafka; the ephemeral development broker is unsuitable.
- Reviewed migrations, backup/restore, alert delivery, dashboards, and rollback.
- Cross-repository infrastructure changes through the owning Terraform state, preserving Aurora security-group ownership.

Deployment order:

**Infrastructure prerequisites → database migrations → engine/auth bootstrap → workers and gateways → agent runtime → portal → synthetic smoke journeys.**

Validate staging before production. Promote identical tested image digests. Roll back application images without deleting audit records or blindly reversing data migrations.

### Required verification

Every affected component and assembled train requires:

- Lint, typing, unit tests, artifact/signoff validation, architecture fences, and secret scanning.
- Real PostgreSQL, Kafka, FHIR, and CIB integration where applicable.
- All ten agent journeys; distinguish deterministic evaluations, live evaluations, engine integration, and browser tests.
- Positive and negative form coverage for all 43 human tasks.
- Cross-tenant/cross-role denial, forged identity, agent-token denial, and CSRF protection.
- Double-clicks, competing reviewers, timer races, stale evidence, reassignment, restart, and timeout reconciliation.
- Audit failure preventing effects; no fabricated completion; no PHI in rendered errors.
- Accessibility scans, keyboard journeys, and VoiceOver checks.
- Load and recovery tests with workload and measurements recorded; no invented production-SLO certification.
- Deployment identity/network checks, migration compatibility, backup restoration, and rollback.
- Zero-loss ledger reconciliation and CI on the actual integrated revision.

Require successful integration and applicable evaluation/frontend jobs even when GitHub has not made them mechanically required. No new skips, xfails, disabled fences, weakened rules, or reduced assertions may conceal failures.

### Final independent assurance and handoff

After final integration:

- Two fresh R1 gatekeepers independently review all security-critical packages and a seeded sample of at least 20% of the remainder.
- Both recalculate completion counts and inspect actual main commits, tests, deployment evidence, and outstanding dependencies.
- Material disagreement reopens affected work.
- Both must approve before the completion report.

Update both programs’ durable reports, registers, evidence, handoffs, and successor prompt. Preserve the first plan’s research links and this consolidation mapping in durable coordination during execution.

Prune worktrees only after exclusive changes and evidence are safely retained, integrated behavior is verified, and the worktree is no longer required. Preserve unrelated protected branches and worktrees.

Track genuine human prerequisites, including R-051 on September 14, R-137/DPO and related sessions on September 19, clinical/actuarial approvals, privacy ratification, production credentials, and approval of the exact release revision. Revalidate their status and dates at execution. Continue independent engineering while these remain pending.

Existing explicit owner-review requirements remain in force; prepare concrete review packages without fabricating approval records.

**Execution boundary:** this plan was prepared in Plan Mode. The user subsequently enabled execution mode and requested saving this plan and its execution prompt. That save operation does not establish implementation, merge, provisioning, deployment, or cleanup completion. The next orchestrator must execute this plan under its active session mode and the authorization boundaries above. The saved first plan remains unchanged.


### Mandatory post-merge repository maintenance

After every merge and verification on refreshed main, execute CONTRIBUTING.md’s maintenance procedure. Inventory all repository worktree roots and exact local/remote refs, preserve WIP, open PRs, dirty or unique ignored evidence and pinned tooling, and prove containment before removing clean inactive merged worktrees and branches. Recover orphan changes in isolation and preserve squash-recovery evidence. Record removals and protected exceptions in the existing coordination artifacts; never force cleanup after a refusal. Refresh references with --no-prune and verify surviving heads and local changes at the end.
