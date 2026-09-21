# ADR-0020 Amendment (DRAFT) — Retention vs Legal-Hold Conflict Decision

**Targets:** ADR-0020 item 6 (`docs/adr/0020-custody-chain.md:84-96`) — the retention-5y-vs-erasure-vs-
legal-hold interaction, which ADR-0020 itself marks **DRAFT — requires human review (jurídico/
regulatório/DPO)**.
**Status:** `Proposed — requires DPO + orchestrator ratification` (no self-certification; constraint 1).
**Author:** compliance-analyst (R1). **Deliverable type:** decision draft for ratification. This
document does NOT edit ADR-0020, `retention.py`, or any code (charter: writes confined to
`docs/compliance/`).
**Legal framing note:** LGPD article references below are cited where verifiable and flagged
"requires jurídico confirmation" where the exact letter/paragraph is uncertain (constraint 3). No legal
conclusion is asserted — the recommendation is for DPO/jurídico ratification.

## 1. Conflict statement (grounded in code + decisions on `main`)

Two ratified/implemented mechanisms collide, and the collision is currently resolved **the wrong way in
code** relative to ADR-0020's stated intent:

- **Retention (ceiling):** `RetentionManager.retention_query()` emits an **unconditional**
  `DELETE FROM audit_chain WHERE ts < cutoff` at the 5-year cutoff, with **no legal-hold predicate**.
  Evidence: `src/maezo/platform/retention.py:132-143`. Backed by DL-0018 (5-year `audit_chain`
  retention, scheduled DELETE, non-partitioned; `docs/decisions-log.md:20`).
- **Legal-hold (floor/preservation):** ADR-0020 item 6 states that an LGPD erasure request colliding with
  an active legal-hold is "**resolvido a favor do hold**" during the regulatory period, with erasure
  "**diferido/reconciliado ao fim do hold**" (`docs/adr/0020-custody-chain.md:88-92`). The CustodyBundle
  (`src/maezo/gateway/custody.py:36+`) seals a `bundle_root` (Merkle over ordered `record_hash`es) back
  into the audit chain **before** the human fraud decision — so the evidentiary value depends on those
  `audit_chain` rows continuing to exist.

**The contradiction:** the scheduled DELETE (retention.py) will destroy `audit_chain` rows once
`ts < cutoff` **even if a sealed CustodyBundle under an active legal-hold references them**. There is
**no legal-hold registry anywhere in the codebase** — `grep -rn "legal_hold" src/` returns **zero hits**.
So the code today implements "**retention wins, destructively**," directly contradicting ADR-0020 item 6
("hold wins"). Deleting held evidence during pending/anticipated litigation is a **spoliation** risk.

**Three loci of the same conflict (all must be covered):**
1. `audit_chain` hash records (deleted by `retention.py`) — the substrate a sealed `bundle_root` needs to
   be reconstructable/verifiable. Even though a hash "isoladamente não é dado pessoal apagável"
   (ADR-0020:89), deleting it breaks the custody projection.
2. Raw evidence in the S3 PHI-zone under Object-Lock (ADR-0020 item 4) — the personal data itself, subject
   both to LGPD erasure AND to litigation-hold preservation.
3. `portal_document.object` — the sealed PHI document ciphertext of the portal document plane
   (`src/maezo/gateway/documents/schema.sql:21-28`), added 2026-09-20 by the owner's ratification of the
   WP-J1-04 §3 item-2 fold. The owner migration sets **no retention** (no retention column, no lifecycle
   job), so the plane's de-facto default is retain-indefinitely — the same LGPD Art. 16 tension this
   amendment analyses, now with a code locus of its own. Its retention decision is **not** ratified
   anywhere: `spec/policies/phi/document-custody-lifecycle.yaml` declares `retencao.decidido_aqui: false`
   and points here instead of restating a number, and ADR-0020 item 6's "5+ anos" stays **DRAFT/verify**
   pending jurídico/regulatório/DPO sign-off (`docs/adr/0020-custody-chain.md:84-96`).

## 2. Options considered

### Option A — Retention wins (unconditional DELETE)
The scheduled DELETE runs at cutoff regardless of holds (**current code behaviour**).
- **For:** simplest; bounds `audit_chain` growth; satisfies the LGPD Art. 16 elimination duty on schedule.
- **Against (decisive):** destroys evidence under an active legal-hold → **spoliation** exposure in
  judicial/administrative/regulatory proceedings; breaks the ADR-0020 custody guarantee (a sealed
  `bundle_root` becomes unverifiable if its referenced rows are gone); **contradicts ADR-0020 item 6**.
- **Verdict:** REJECT. Fail-open against the evidentiary obligation.

### Option B — Hold wins (blanket, indefinite block)
Any active hold blocks the DELETE entirely and indefinitely for the affected records.
- **For:** never destroys held evidence; trivially satisfies litigation-hold.
- **Against:** unbounded `audit_chain` growth; **over-retention is itself an LGPD violation** — Art. 16
  imposes an **elimination duty once the conservation purpose ends** (*requires jurídico confirmation of
  Art. 16 scope*). A blanket, never-reconciled hold keeps personal data past its lawful conservation
  window. Also loses the "reconcile at end of hold" that ADR-0020 item 6 already commits to.
- **Verdict:** REJECT. Fail-open against the minimization/elimination duty.

### Option C — Hold-registry carve-out (deferred reconciliation) — **RECOMMENDED**
Introduce a **legal-hold registry** (absent today). The scheduled DELETE consults it and **skips records
under an active hold**; skipped records are **deferred**, not retained forever. When the hold lifts **AND**
the 5-year retention floor is met, the deferred records are **reconciled** (deleted) on the next cycle.
- **For:**
  - Preserves evidence for the exact duration a hold requires — no spoliation.
  - Honours the LGPD Art. 16 elimination duty as soon as the lawful conservation purpose (the hold) ends —
    no indefinite over-retention.
  - **Consistent with ADR-0020 item 6** ("erasure diferido/reconciliado ao fim do hold") and with item 6's
    surrogate-drop rule ("o surrogate `mpi_id<->fhir_patient_id` só é dropado quando não houver legal-hold
    ativo que dele dependa", `adr/0020:93`).
  - Covers both loci (§1): the same hold registry gates the `audit_chain` DELETE **and** the S3
    Object-Lock legal-hold release.
- **Against / cost:**
  - Requires building the hold registry + making `retention.py`'s DELETE hold-aware (today it is a bare
    `DELETE ... WHERE ts < cutoff` — `retention.py:143`). This is net-new work.
  - Requires a defined process for **who places/lifts a hold** and on what scope (case id, tenant,
    time-range, subject) — a DPO/jurídico governance question, not just code.
- **Amendment (chain integrity, see §3-bis):** the carve-out alone is NOT sufficient — any prune (even of
  fully-unheld, past-retention records) severs `verify_chain()`'s genesis-anchored contiguity and makes
  the surviving chain verify as corrupted. Option C therefore additionally requires that **every prune be
  paired with a re-anchoring / signed-checkpoint mechanism** (a checkpoint record sealed into the chain
  whose hash commits to the pruned prefix, and verifier support for that anchor) **before deletion is
  ever enabled** — no prune without re-anchor, fail-closed.
- **Verdict:** RECOMMEND (with the §3-bis re-anchoring precondition), pending DPO/jurídico ratification
  of the governance rules.

## 3. LGPD Art. 16 vs litigation-hold analysis (for DPO/jurídico ratification)

**All article references below require jurídico confirmation of exact letter/paragraph (constraint 3).**

- **LGPD Art. 16** (elimination after end of processing, with conservation exceptions): both the 5-year
  audit retention and a legal-hold plausibly fit **Art. 16, I — cumprimento de obrigação legal ou
  regulatória** as an authorized conservation. This is what lets a hold **override an erasure request**
  during the authorized window. BUT Art. 16's baseline is an **elimination duty** once the conservation
  purpose ends — which is why the hold cannot be indefinite (defeats Option B) and must reconcile at
  end-of-hold (Option C).
- **Litigation / preservation hold** (duty to preserve evidence relevant to anticipated or pending
  proceedings): the LGPD basis to keep **sensitive/health** data for this is plausibly **Art. 11, II —
  hipóteses de tratamento de dado sensível sem consentimento** (including exercício regular de direitos em
  processo judicial/administrativo/arbitral) and, for non-sensitive data, **Art. 7, VI (exercício regular
  de direitos em processo)**. *The exact Art. 11, II alínea requires jurídico confirmation.* Under these,
  a hold overrides the routine retention ceiling (keep **longer**) and overrides an Art. 18-VI erasure
  request (the erasure right is **not absolute** — it yields to conservation obligations under Art. 16 and
  to exercício regular de direitos). *Confirm with jurídico.*
- **Hash-vs-PHI distinction (ADR-0020 item 6):** the `audit_chain` carries hashes + structured
  `decision_basis`, not raw PHI (`audit_postgres.py:45-47`). ADR-0020 argues "hashes não são,
  isoladamente, dado pessoal apagável." If jurídico confirms that, the `audit_chain` DELETE is less about
  the *titular's* erasure right and more about the **evidentiary integrity** of the custody projection —
  which still argues for the carve-out (don't delete rows a sealed bundle under hold depends on). The raw
  PHI under S3 Object-Lock is where the titular's erasure right actually bites, and item 4/6 already route
  that to the hold.
- **Fail-closed default (constraint 2) — the scheduler IS wired; only the entrypoint is missing:** the
  destructive DELETE is **scheduled-in-manifest and ratified, but unimplemented**. Precisely: the Helm
  CronJob `lifecycle-audit-retention`
  (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml:7,54` + `values.yaml:556-559`, schedule
  `"0 5 1 * *"` — monthly, day 1, 05:00) runs `python -m maezo.platform.lifecycle audit-retention`, and
  DL-0018 (`docs/decisions-log.md:20`) ratifies the DELETE as *agendado*. BUT the entrypoint module
  `maezo.platform.lifecycle` **does not exist on `main`** (importing it raises `ModuleNotFoundError`,
  so the CronJob pod would crash at start), and `retention_query()` has **zero callers** anywhere in
  the tree. This RAISES the urgency of the fail-closed recommendation rather than lowering it: with the
  decision ratified and the manifest live, **the moment someone implements the missing module per
  DL-0018, the destructive DELETE goes live monthly** — with no hold predicate (§1) and no chain
  re-anchoring (§3-bis). The fail-closed posture is therefore: the
  `maezo.platform.lifecycle audit-retention` entrypoint must NOT be implemented (and the CronJob must
  not be treated as operational) until Option C's hold registry AND the §3-bis re-anchoring mechanism
  exist — today's ModuleNotFound crash is, accidentally, the only thing keeping the destructive path
  closed.

## 3-bis. Chain-integrity gap: DELETE-by-age severs `verify_chain()` contiguity (orthogonal to legal-hold)

**Verifier-found gap, independent of spoliation.** Even with zero active holds, running the routine
DELETE-by-age would put the audit chain into a state that **fails its own integrity verification**:

- Both verifiers anchor at genesis. The in-memory `AuditSink.verify_chain()` walks the chain starting
  from `prev = GENESIS_PREV_HASH` and fails on the first record whose `prev_hash` does not match
  (`src/maezo/gateway/audit.py:298-330` — after a prune, the oldest *surviving* record still points at
  a deleted predecessor, so verification fails **at index 0**). The Postgres `verify_chain()` does the
  same structurally: it seeds its walk with `by_prev.get(GENESIS_PREV_HASH)`
  (`src/maezo/gateway/audit_postgres.py:380`) — with the genesis record deleted, the walk starts empty
  and every surviving row is reported "unreachable from genesis" (`audit_postgres.py:396-406`),
  i.e. the pruned chain is indistinguishable from a **gap/fork corruption**.
- **UNIQUE-preservation ≠ chain-contiguity.** DL-0018's non-partitioned design protects the
  `UNIQUE(prev_record_hash)` **anti-fork** guarantee (`src/maezo/platform/migrations/versions/`
  `0002_audit_chain.py:44-50` — the rationale is explicitly about atomically preventing concurrent
  forks). That constraint says nothing about contiguity-from-genesis after rows are deleted: a pruned
  chain still satisfies UNIQUE while failing verification. The two properties are orthogonal, and
  DL-0018 addressed only the first.
- **Consequence for Option C (amendment):** a hold-aware DELETE alone is NOT sufficient. **Any prune
  must be paired with a re-anchoring / signed-checkpoint mechanism** — e.g. before deletion, seal a
  checkpoint record into the chain whose content commits to the pruned prefix (last pruned
  `record_hash` and/or a Merkle root over the pruned range, mirroring the ADR-0020 `bundle_root`
  idiom), and teach `verify_chain()` to accept an authenticated checkpoint as the walk's anchor in
  place of `GENESIS_PREV_HASH`. **No prune without re-anchor — fail-closed:** until the checkpoint
  mechanism exists and both verifiers understand it, deletion must not be enabled at all, because a
  post-prune chain would (correctly) verify as corrupted, destroying the evidentiary value ADR-0020
  exists to protect.

## 4. Recommendation

**Adopt Option C (hold-registry carve-out with deferred reconciliation)**, amending ADR-0020 item 6 to
make the retention side (DL-0018 / `retention.py`) **hold-aware and consistent with the erasure side item 6
already commits to**. Concretely, for ratification:

1. Ratify that **legal-hold takes precedence over the routine 5-year DELETE** (preservation floor beats
   retention ceiling) — but **only until the hold lifts**, then reconcile (delete) once the 5-year floor is
   also met.
2. Ratify that the same holds gate **all** of: the `audit_chain` DELETE, the S3 Object-Lock release, the
   `mpi_id<->fhir_patient_id` surrogate drop (ADR-0020:93), **and the retention/lifecycle of
   `portal_document.object`** (locus 3, §1 — added by the 2026-09-20 WP-J1-04 §3 item-2 fold; the plane
   currently sets no retention, `src/maezo/gateway/documents/schema.sql:21-28`).
3. Require a **legal-hold registry** (net-new; absent on `main`) as a precondition before the scheduled
   DELETE may run in production. The scheduler is already wired and the decision ratified
   (CronJob `lifecycle-audit-retention`, `cronjob-lifecycle.yaml:7,54` + `values.yaml:556-559`;
   DL-0018) — only the `maezo.platform.lifecycle` entrypoint is missing. Until BOTH preconditions
   (this item and item 4) are met, **do not implement that entrypoint / do not enable the job**
   (fail-closed — see §3 last bullet).
4. Require a **chain re-anchoring / signed-checkpoint mechanism** (§3-bis) as the second precondition:
   every prune must first seal a checkpoint committing to the pruned prefix, and `verify_chain()`
   (both `audit.py` and `audit_postgres.py` variants) must accept the authenticated checkpoint as its
   anchor. **No prune without re-anchor.**
5. Define the **governance** (DPO/jurídico): who places/lifts a hold, on what scope (case id / tenant /
   subject / time-range), and the audit trail for hold placement/release (itself an `audit_chain` event).

**Ownership / boundaries:**
- **DPO + jurídico + regulatório:** ratify §3 legal analysis (confirm Art. 16 / Art. 11-II / Art. 7-VI
  references), the retention period (DL-0018's 5y is DRAFT on the number), and the hold governance.
- **Orchestrator:** ratify the ADR-0020 item-6 amendment and sequence the registry build (audit-persistence
  / infra lane) — compliance-analyst does not implement it.
- **This document changes no code and no ADR.** It is a decision draft, `Proposed`, pending DPO +
  orchestrator ratification.

## 5. Open items blocking ratification

- `blocked(external: DPO designation)` — no encarregado to ratify the legal analysis.
- Retention period (5y) is DRAFT pending jurídico/regulatório sign-off (DL-0018; ADR-0020:86).
- The DELETE scheduler is **already wired and ratified but unimplemented**: Helm CronJob
  `lifecycle-audit-retention` (`cronjob-lifecycle.yaml:7,54`; `values.yaml:556-559`, `"0 5 1 * *"`)
  invokes the **nonexistent** `maezo.platform.lifecycle` module (ModuleNotFound today);
  `retention_query()` has zero callers. Orchestrator/infra must gate the module's implementation on
  this amendment's two preconditions (hold registry §4.3 + re-anchoring §4.4) — implementing it first
  activates the destructive monthly DELETE (see §3 last bullet, §3-bis).
- Exact LGPD article letters (Art. 11, II alínea; Art. 16 scope) — **requires jurídico confirmation**.
