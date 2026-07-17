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

**Two loci of the same conflict (both must be covered):**
1. `audit_chain` hash records (deleted by `retention.py`) — the substrate a sealed `bundle_root` needs to
   be reconstructable/verifiable. Even though a hash "isoladamente não é dado pessoal apagável"
   (ADR-0020:89), deleting it breaks the custody projection.
2. Raw evidence in the S3 PHI-zone under Object-Lock (ADR-0020 item 4) — the personal data itself, subject
   both to LGPD erasure AND to litigation-hold preservation.

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
- **Verdict:** RECOMMEND, pending DPO/jurídico ratification of the governance rules.

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
- **Fail-closed default (constraint 2):** until the registry + governance exist, the safe posture is that
  the scheduled DELETE **must not run destructively on records that could be under hold**. Since no hold
  registry exists to prove a record is *not* held, a fail-closed reading argues the unconditional DELETE
  (`retention.py:143`) **should not be scheduled in production** until Option C is built — it cannot
  currently distinguish held from unheld records. **DPO/orchestrator to confirm** whether the scheduled
  DELETE is wired to any cron today (not found on `main`; `retention.py` provides the query but no
  scheduler was located).

## 4. Recommendation

**Adopt Option C (hold-registry carve-out with deferred reconciliation)**, amending ADR-0020 item 6 to
make the retention side (DL-0018 / `retention.py`) **hold-aware and consistent with the erasure side item 6
already commits to**. Concretely, for ratification:

1. Ratify that **legal-hold takes precedence over the routine 5-year DELETE** (preservation floor beats
   retention ceiling) — but **only until the hold lifts**, then reconcile (delete) once the 5-year floor is
   also met.
2. Ratify that the same holds gate **both** the `audit_chain` DELETE **and** the S3 Object-Lock release,
   and the `mpi_id<->fhir_patient_id` surrogate drop (ADR-0020:93).
3. Require a **legal-hold registry** (net-new; absent on `main`) as the precondition before the scheduled
   `retention.py` DELETE may run in production. Until then, **do not schedule the destructive DELETE**
   (fail-closed).
4. Define the **governance** (DPO/jurídico): who places/lifts a hold, on what scope (case id / tenant /
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
- Whether the `retention.py` DELETE is (or will be) wired to a scheduler in production — **not found on
  `main`**; confirm with orchestrator/infra before any go-live.
- Exact LGPD article letters (Art. 11, II alínea; Art. 16 scope) — **requires jurídico confirmation**.
