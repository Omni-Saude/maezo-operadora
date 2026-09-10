# Execute the consolidated Maezo completion plan

> **Current continuity — 2026-09-10, WIP preservation complete:** [Current checkpoint](https://github.com/Omni-Saude/maezo-operadora/blob/completion/product-c27-auth-lifecycle/CHECKPOINT.md) supersedes historical scheduling/status below. Fresh status inspection covered 488 registered worktrees plus four standalone repositories; all 22 discovered uncommitted source files exactly match nine remotely verified recovery commits. Use `docs/prompts/maezo-wip-preservation-only.md` for a lower-cost WIP-only pass. Product implementation and release gates remain paused/pending. Frozen historical handoffs retain their original bytes; they are evidence, not current dispatch instructions.

> **Session paused at user request — 2026-09-10T01:19:37.373948+00:00.** Current resume state: [HANDOFF.md](/Users/familia/code/maezo-completion-evidence/successor-graceful-handoff-20260910/HANDOFF.md). All specialists returned; full completion remains unproved. Resume only on a new user instruction. Read the new `final-ready.json` receipt before using this snapshot. Historical states below are preserved for provenance and do not supersede this handoff.


## Mission

Work in `/Users/familia/code/maezo-operadora`.

Execute `docs/plan.md` completely: recover both interrupted orchestrators,
preserve their exclusive work, close platform gaps, implement the custom
portal, and validate deployment with independently reproduced evidence.

The plan is the approved engineering specification. Do not shorten its
scope, discard research, substitute workarounds, or stop after planning.

If `docs/plan.md` is missing, stop before implementation and recover the
complete consolidated plan from the preceding conversation. The file
`/Users/familia/Downloads/plan.md` is the FIRST plan, not the consolidated
final version; it lacks the full orphan-recovery additions.

## Read and establish truth

Read AGENTS.md and PLANS.md first. Then progressively load:

- docs/plan.md
- RUNBOOK.md and CHECKPOINT.md
- docs/prompts/NEXT-ORCHESTRATOR-HANDOFF.md
- docs/prompts/PENDING-TASKS-EXECUTION-PROMPT.md
- docs/prompts/maezo-gap-closure-prompt-v6.md
- docs/prompts/maezo-fleet-hardening-finish-prompt.md

Inspect relevant repository memory and surviving orchestrator reports.
Consult applicable contracts, ADRs, decisions-log entries, and audit findings
before changing each subsystem. Do not load the entire specification.

Treat every historical SHA, count, approval, and test result as a recovery
lead. Verify current repository and remote state before relying on it.

## Preserve interrupted work

Inventory all worktrees under:

- /Users/familia/code/maezo-fleet2-wt/
- /Users/familia/code/maezo-r6-wt/
- /Users/familia/code/maezo-wt/

The previous inspection found 83 registered worktrees:

- Fleet2: 52, all committed tips contained in main or fleet2/train-b.
- R6: 30, including train-1 and four independent branches.
- Legacy: one remediation-docs checkout with historical extra documents.

Explicitly recover:
r6/adr-batch, r6/agent-small-fixes, r6/r009-phi-scrub-prereqs,
and r6/r228-programa-gate.

Preserve the uncommitted test in agent-small-fixes:
tests/unit/agents/test_terminal_nodes_emit_desfecho.py.

Its verifier checkout was older than the author tip.
r093-inad-event and wamid-outbound were baseline-only, not completed work.

Record paths, SHAs, dirty-file hashes, evidence locations, and dispositions
in existing coordination artifacts. Preserve exclusive untracked and ignored
documents. Do not reset, clean, delete, overwrite, or prune orphan work before
proving its changes and evidence are safely retained.

Commit ancestry proves containment, not preservation of behavior.

## Specialized orchestration

Never delegate to general-purpose agents.

Use tightly bounded specialists:
- Astra high/xhigh: security, PHI, financial/process semantics, gateway,
  transactions, integration, independent assurance.
- Sol high: specified implementation, frontend, evaluations, tooling, docs.
- Luna: isolated mechanical lookups only.

The user authorizes the maximum useful safe specialist concurrency. Discover the actual runtime ceiling at session startup and after a supported capacity change; do not treat a historical four-agent observation as a policy limit. The user-configured global setting requests up to eight spawned specialist threads (`agents.max_concurrent_threads_per_session = 8`, excluding ROOT). Respect the effective user configuration; do not add a redundant project override that would silently cap a later global increase. This is a configurable starting ceiling, not a mandatory staffing count or proof of active capacity: raise it when ready independent work, coordination and runtime support justify it; otherwise use all admitted slots that have useful work. A runtime refusal is authoritative for that session. Record requested, admitted and active counts separately; never bypass a refusal with unmanaged agent processes. Count nested agents according to the hosting runtime and avoid nested delegation unless ownership and capacity are explicitly allocated. Use specialized roles only.
Serialize real-engine suites; do not run full unit and engine suites together.

Each brief must specify baseline SHA, owned paths, finding IDs, contracts,
required behavior, prohibited scope, acceptance tests, and evidence outputs.

For every work package:
propagation map → reproduced RED evidence → implementation and fence →
ledger → independent adversarial verification → separate repair specialist →
original verifier delta → integration review → successful CI → merge →
verification on main → repository maintenance under CONTRIBUTING.md.

Verifiers cannot implement their own recommendations. A changed commit
invalidates affected approvals. Independently inspect actual commands,
outputs, diffs, SHAs, and artifact digests.

Reserve two fresh Astra final gatekeepers uninvolved in implementation
and intermediate verification.

## Safe parallel dispatch

Treat the execution sequence as a dependency order, not a global barrier between unrelated work packages. Keep the available concurrent slots occupied with bounded specialists when independent, ready tasks exist; include root and nested agents in the runtime limit. Check live agent status before dispatching and do not claim a queued mandate is active.

Before each long CI or service run, select ready work using the actual source dependency graph and record ownership in PLANS.md. Every concurrent author uses an isolated worktree at an exact SHA, disjoint owned paths, frozen input contracts and a separate evidence directory. Never edit another active agent's checkout or a source currently under validation. Shared API/schema changes require an agreed frozen contract before independent consumers proceed; missing contracts are a dependency, not permission to invent them.

Parallelize source implementation, focal offline tests, contract/form mapping, frontend work against existing OpenAPI, image-runner implementation and finite evidence-consumer work when their inputs are qualified. A pending integration run blocks acceptance of that package, not unrelated engineering. Distinguish source qualification, actual service acceptance and deployment throughout.

ROOT exclusively schedules real-engine/service lanes, full-unit coverage runs and global ledger validation. Engine and full-unit runs remain mutually exclusive. ROOT also owns integration conflict resolution, shared ledger appends, GitHub mutations and post-merge maintenance. Specialists may prepare ledger additions in their packets; they do not append competing rows to the integration branch.

When an author freezes a package, use a distinct verifier and immediately dispatch the freed author to another independent ready package. Preserve the original verifier for any delta, use a third specialist for requested repairs, and reserve the two fresh final gatekeepers. Prioritize a ready critical-path prerequisite, then a separate product or infrastructure lane; do not fill slots with duplicate audits, repeated passing tests or speculative scaffolding merely to appear busy.

Keep a short ready/running/dependency queue in existing PLANS.md, with exact baseline, owned paths, assigned specialist, next acceptance step and resource requirements. Re-evaluate it when a packet, CI result or user instruction arrives. Merge only after every applicable technical check completes; parallel preparation never grants advance approval to an unknown combined revision.


### Throughput controls — user-authorized upgrade, 2026-09-09

**2026-09-10 execution amendment — user-approved shortest-time strategy.** Keep at most four implementation work packages active and at most two source-approved packages awaiting composition. Several specialists may work on disjoint owned paths within one cohesive package, subject to the total admitted runtime slots and independent-review separation. When the approved queue reaches two, prioritize integration/repair over starting another dependent implementation. Freeze one broad-validation candidate when it unlocks an operational proof or includes up to three compatible approved deltas, whichever occurs first; do not modify that candidate while its checks run. Start with a short actual decision transaction smoke before broad engine suites, explicitly separate from packaged runtime/browser acceptance. Observe CI at dependency transitions or five-minute intervals when needed, while continuing ready work. Record waiting by cause and revisit WIP after two candidates; do not infer global completion from the 75-object traceability index. These limits refine readiness scheduling below and do not remove any independent, technical, human or maintenance gate.

- Dispatch by ready dependency and exclusive ownership, not document wave number. Start the next ready specialist as soon as a slot is actually released; prioritize a prerequisite on the delivery critical path and keep a separate product or infrastructure lane progressing. A blocked human, remote CI or service gate does not block independent source work.
- Size a mandate as a cohesive bounded behavior, including its owned propagation and regression fence. Do not split mechanical preparation, type definitions, implementation and local tests into sequential agent handoffs when one specialist can safely own them. Separate contracts only when another concurrent consumer needs a shared interface frozen, or authority/security semantics require an independent decision. Author/verifier separation and distinct repair author remain mandatory.
- Reuse an already verified immutable packet for its exact scope. Within one execution, record its digest and verification receipt once; repeat bulk hashing or passing tests only for changed bytes, custody uncertainty, an affected dependency, a required independent reviewer check, or an explicit acceptance requirement. Never relabel old results as validation of a changed candidate.
- Group ready disjoint approved deltas into a frozen integration candidate, then run its required broad checks once. New changes or failures trigger only the affected earlier checks plus the required final candidate gates. Preserve individual findings and source approvals while avoiding a separate broad suite for every intermediate merge.
- ROOT retains shared ledger writes, integration decisions, GitHub mutations and scheduling of the shared local engine/full-unit/global-ledger lane. Delegate bounded read-only preparation, provenance tables and focal validation to specialists where useful; ROOT still independently checks evidence before accepting it. ROOT ownership does not require all preparatory work to execute serially in ROOT.
- Shared-host engine and full-unit suites remain mutually exclusive; independent remote CI, offline focal tests and source work do not acquire that lane. Do not increase local heavy-process parallelism merely because more remote reasoning threads are available.
- After a repeated service failure, obtain new bounded diagnostic information before another expensive run. Preserve the failing evidence and acceptance thresholds. Do not spend an unchanged full run to rediscover an already established failure, or add audit-only rounds that cannot resolve the next decision.
- Keep the ready/running/dependency queue short in PLANS.md, naming the next deliverable, owner and blocking gate. Track time spent waiting for runtime capacity, source/review dependencies and service execution separately; use those observations to remove avoidable scheduling delays without weakening product or independent acceptance.

## Execution sequence

Follow docs/plan.md:

1. Preserve work, recover evidence, reconcile every register and finding.
2. Repair and verify r6/train-1.
3. Semantically integrate and verify fleet2/train-b.
4. Finish the four independent branches, unstarted work, and remaining gaps.
5. Implement the complete custom portal and its human authorization,
   atomic engine-command, audit, and recovery architecture.
6. Complete ECS/Fargate infrastructure, staging validation, and release gates.
7. Obtain both final independent approvals and produce the evidence report.

Preserve all portal benchmarks, role definitions, interface contracts,
security boundaries, infrastructure decisions, and tests from the plan.

Never invent business rules, ratification values, human signatures,
credentials, or production approval. Preserve hard human-only autonomy
boundaries and explicit owner-review requirements. Prepare concrete review
packages and continue independent engineering while human prerequisites wait.

## Acceptance

Run all applicable repository checks, real-engine integration, evaluations,
browser journeys, accessibility checks, security tests, and recovery tests.

Require nonzero evaluation collection and explicit credential-dependent
results. Missing credentials cannot become an unqualified pass.

Do not conceal failures with mocks, skips, xfails, weaker assertions,
disabled fences, documentation-only checks, or premature completion claims.

Require CI on the actual integrated revision and prove zero ledger loss.

Both final gatekeepers must review every security-critical package and a
seeded sample of at least 20% of the remainder, independently recalculate
completion counts, and approve before the completion report.

## Durable continuity

Maintain PLANS.md, RUNBOOK.md, CHECKPOINT.md, evidence ledger, and existing
handoffs. Do not create parallel tracking systems.

Checkpoint after every package and train, and before context exhaustion:
exact SHA, worktree, completed evidence, unresolved findings, running command
or session, engine-lock owner, and the next executable step.

Store evidence durably; temporary scratchpads cannot be its sole location.

Use the prompts’ Portuguese conventions for commits and operational records;
deliver the owner report in English.

Continue autonomously within authorization. Report genuine external blockers
precisely, and never label incomplete or unverified work complete.


## Mandatory repository maintenance after each merge

Follow the versioned CONTRIBUTING.md maintenance procedure after every merge and main verification. Refresh with --no-prune, inventory exact worktree/ref/PR state, protect active work and pinned tool inputs, and archive recovery evidence before removing only reviewed clean inactive merged worktrees and branches. Preserve or recover orphan changes in isolation. Record before/after identities and dispositions in PLANS.md, RUNBOOK.md and CHECKPOINT.md. A failed non-force removal preserves the candidate; it never authorizes force cleanup.

Before invoking any merge command, including --auto, inspect all applicable technical checks. GitHub may merge immediately when its required subset passes even while additional applicable checks are running; auto-merge is not a promise to wait for those jobs. Record the exact tested tree and any explicitly bounded evidence inheritance.
