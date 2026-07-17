# SME dispatch — DRAFT process contracts (T0.6)

**Task:** T0.6 (V2-COMPLETION-PLAN, Plan §3 roster). **Owner (packaging):** sme-liaison agent.
**Status of actual dispatch:** `blocked(external)` — pending SME roster (names/emails) from
Rodrigo. Packaging is complete; sending mail/creating tickets/inviting reviewers is not.

This directory packages the 15 DRAFT process contracts in `docs/processes/contracts/` for
subject-matter-expert (SME) review, plus a retro-verification ask for the one FINAL contract
(`SP-OP-ESCALATION-001`) that currently has no signoff artifact on file. It does **not** perform
the review itself, does **not** contact any reviewer, and does **not** approve, sign off, or mark
any contract FINAL. Those are exclusively human actions.

## Why this exists

Every DRAFT contract in `docs/processes/contracts/` carries a line like:

> `DRAFT — requires human review (...) before any deploy`

That requirement has been sitting unactioned. This dispatch turns "requires human review" into a
concrete, per-role package: which contract, which reviewer role, which `spec/processes/` artifacts
it governs, and what specific questions the reviewer needs to answer. It is the longest external
pole in the V2 completion plan — SME turnaround is out of the orchestrator's control — so it starts
now, packaged and ready, even though the roster to actually send it to does not exist yet.

## What's in this directory

| Path | Contents |
|---|---|
| `README.md` | This file — process, redline protocol, signoff spec |
| `tracker.md` | One row per contract (16 total): status, assigned roles, dispatch status, receipt date, redline rounds, signoff file present |
| `medico-auditor/PACKAGE.md` | Contracts assigned to the médico-auditor (medical auditor) role |
| `juridico/PACKAGE.md` | Contracts assigned to the jurídico (legal) role |
| `dpo/PACKAGE.md` | Contracts assigned to the DPO (data protection officer) role |
| `regulatorio/PACKAGE.md` | Contracts assigned to the regulatório (ANS regulatory affairs) role |
| `financas/PACKAGE.md` | Contracts assigned to the finanças (finance) role |
| `po/PACKAGE.md` | Contracts assigned to the PO (product owner) role |

A contract can appear in more than one role's package — most do. Each package lists, per assigned
contract: the canonical contract path, the `spec/processes/` artifacts it governs (BPMN + DMN,
with corrected paths — see "On artifact paths" below), specific review questions, an expected
turnaround ask, and a pointer to the signoff instructions in this file.

## Scope note: what this dispatch does NOT touch

Per the task charter, this packaging work does not edit:
- The contracts themselves (`docs/processes/contracts/*.md`) — content and paths inside them are
  owned by a separate, concurrent task (T0.2, `dead-references`) fixing the `src/maezo/processes/...`
  → `spec/processes/...` path drift. This dispatch computes the correct `spec/` paths independently
  for its own packages, but does not rewrite the contracts.
- `docs/review-queue.md` — the pre-existing clinical/regulatory review register, owned by T0.2
  this wave.
- Code or policy of any kind.
- `docs/prompts/V2-COMPLETION-PLAN.md`.

## On artifact paths

The contracts (as currently written) still reference `src/maezo/processes/bpmn/...` and
`src/maezo/processes/dmn/...` in several places. **Those paths are dead** — the real, canonical
BPMN/DMN artifacts live under `spec/processes/bpmn/` and `spec/processes/dmn/`. Every path cited in
the six role packages in this directory was verified against the actual file listing at HEAD
`e1c0b34` (`ls spec/processes/bpmn/`, `ls spec/processes/dmn/`), not copied from contract prose.
Where a contract names a DMN that has no corresponding file in `spec/processes/dmn/`, the package
says so explicitly (e.g. "não portado" or "build gap — no file found") instead of inventing a path.
Known cases:

- **FRAUDE-001's 7 reference-scoring DMNs** (`upcoding_complexity_ceiling`,
  `unbundling_partial_bundles`, `phantom_no_diagnosis`, `phantom_suspicious_prefix`,
  `frequency_zscore_threshold`, `provider_peer_deviation`, `risk_thresholds`) were named in the
  contract's "inverte o anti-padrão" section as DMNs to be **ported** (copied, adapted, and
  corrected from `number` to `integer`/`double` typing per field) from the read-only reference
  repo. As of T2.7 (artifact phase) they now exist byte-faithful under `spec/processes/dmn/` (one
  `.dmn` file per name above). They are **ported, wiring pending** — no longer "não portado" — in
  the médico-auditor and jurídico packages: each is a deliberate orphan
  (`spec/processes/dmn/orphans-allowlist.yaml`, justification "pending fraud-scoring wiring — T2.7
  phase 2 after T1.4") until `operadora.fraude.score_indicators` is wired (T2.7 phase 2, after the
  T1.4 DMN-evaluation ADR lands); content is unreviewed and flagged for SME
  (médico-auditor/finanças) sign-off before that wiring — see the T2.7 PR body for reported
  divergences between the ported tables, the contract's L0-hard no-blocking-output invariant, and
  the v1 donor worker's actual call signatures.
- **REEMBOLSO-001** cites DMNs `reembolso_admissibility` and `reembolso_auto_approval`; only
  `reembolso_coverage.dmn`, `reembolso_calculo.dmn`, and `reembolso_sla.dmn` exist in
  `spec/processes/dmn/`. `reembolso_coverage.dmn` is the likely renamed artifact for
  `reembolso_admissibility` (same input `cobertura_prevista` shape) but the name doesn't match
  1:1 — flagged for confirmation, not assumed. `reembolso_auto_approval` has no file at all — flagged
  as a build gap, not invented.
- **CANCEL-001**'s contract header cites `SP-OP-CANCEL-001_Cancelamento_Contratual.bpmn`; the actual
  file in `spec/processes/bpmn/` is `SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` (basename drift,
  one word short). Flagged for confirmation that it's the same artifact.

None of the above blocks packaging — they're exactly the kind of thing SME/engineering review
should catch, and it's better to flag them now than to paper over them with an invented path.

## The redline protocol

1. **A reviewer receives their role's `PACKAGE.md`** (once the roster exists and dispatch is
   un-blocked) plus links to the canonical contracts.
2. **The reviewer comments directly on the contract**, using either:
   - a **PR review** against `docs/processes/contracts/<CONTRACT-ID>.md` (preferred — inline
     comments, suggested edits, visible diff history), or
   - an **annotated copy** of the contract (tracked-changes doc or equivalent) returned to the
     task owner, who transcribes accepted changes into a PR.
3. **Redlines come back as a PR** against the contract file, same as any other change to
   `docs/processes/contracts/`. The PR body should reference which reviewer role(s) requested the
   change and why (e.g. "RN 259 → RN 566 per regulatório review, see comment thread").
4. **A contract only moves DRAFT → FINAL when every assigned role has an approving signoff file on
   record** for the version actually reviewed (see below). Partial sign-off (some roles approved,
   others pending or requesting changes) keeps the contract DRAFT.
5. **No agent — this one included — ever creates, edits, or backdates a signoff file.** Signoff is
   an exclusively human act. An agent may remind a human that a signoff is missing or stale; it may
   never supply one on their behalf (constraint: no self-certification).

## Signoff artifact spec

Each reviewer role's approval (or requested-changes verdict) for a given contract is recorded as
its own YAML file:

```
docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml
```

Since a contract typically needs multiple roles, and a role may review a contract more than once
across redline rounds, the file holds a **list** of signoff records (one per reviewer per round) —
never a single overwritten record, so the history of review rounds stays intact:

```yaml
# docs/processes/contracts/signoffs/SP-OP-AUTH-001.signoff.yaml
- reviewer_name: "<full name of the human reviewer>"
  role: "medico-auditor"   # one of: medico-auditor | juridico | dpo | regulatorio | financas | po
  date: "2026-MM-DD"
  contract_version_reviewed: "v0.1.0"   # must match the contract's own **Status:** version line
  verdict: "needs-changes"   # approved | needs-changes
  notes: "Free-text rationale, citations checked, or the specific redline requested."
- reviewer_name: "<full name>"
  role: "juridico"
  date: "2026-MM-DD"
  contract_version_reviewed: "v0.2.0"
  verdict: "approved"
  notes: "RN 259/395/424 confirmed current against ANS text as of this date; no further changes requested."
```

Rules:
- `contract_version_reviewed` must match the version the contract carried **at the time of
  review** (contracts carry a `**Status:** DRAFT (vX.Y.Z)` line) — a signoff against a stale
  version does not count toward promotion once the contract has changed materially.
- A contract is eligible for DRAFT → FINAL promotion only when, for its **current** version, every
  role listed in `tracker.md` for that contract has a record with `verdict: approved`. Any
  `needs-changes` record blocks promotion until superseded by a new `approved` record against the
  redlined version.
- These files are created and committed by the human reviewer (or on their explicit behalf, via a
  PR they review and approve) — never fabricated, inferred, or pre-filled by an agent. Reviewer
  name, date, and verdict are the human's attestation, not a template default.
- `SP-OP-ESCALATION-001` is FINAL but has **no signoff file on record** (see `tracker.md`). Per the
  no-self-certification rule, this cannot be waved through retroactively without a human review —
  it needs a **retro-verification** pass (confirm the SLA values that are still DRAFT per
  `docs/review-queue.md`, and only then have the reviewing roles produce the signoff files that
  should have existed before it shipped as FINAL).

## RN currency — a cross-cutting flag for every reviewer

Several contracts cite ANS resolutions (RN 388, RN 259, RN 412, among others) that the orchestrator
sweep flagged as **possibly superseded** by later consolidations (RN 483, RN 566, RN 593). This
dispatch does not attempt to resolve which citation is current — that systematic pass is T2.5's
job. Every reviewer package below still asks the assigned role to flag, contract-by-contract,
whether the RN cited is the one they'd expect to see in force today. Catching it locally, in the
review round for a specific contract, complements (rather than substitutes for) T2.5's sweep.

## Expected turnaround (proposed, non-binding until the roster confirms)

- **Standard round:** 10 business days from receipt of a role's package.
- **Retro-verification (SP-OP-ESCALATION-001 SLA values only):** 3 business days — narrow scope,
  already-shipped process, existing behavior in production is the fallback if unconfirmed.
- Turnaround is a proposal for the human dispatch owner to negotiate with each SME; it is not a
  commitment made on any reviewer's behalf.

## Current blocker

**SME roster (names, emails, role mapping) is not available.** Actual dispatch — sending the
packages below to named humans — is `blocked(external)` pending that roster from Rodrigo. This
packaging can be, and has been, completed without it. See `tracker.md` for the per-contract
dispatch status, all currently "prepared — awaiting roster (blocked external)".
