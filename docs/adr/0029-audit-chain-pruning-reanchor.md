# ADR-0029: Audit-Chain Pruning via Signed Checkpoint Re-Anchor [T2.8]

**Status:** Proposed — requires DPO + orchestrator ratification (no self-certification)
**Data:** 2026-07-17
**Area:** Auditoria / Integridade da Cadeia (custody-chain)
Owner: audit-persistence-engineer (R1). Companion to `docs/compliance/ADR-0020-amendment-draft.md`
(legal-hold registry). Scope: the chain-integrity mechanism that would make a *lawful* prune of
`audit_chain` possible. This ADR designs it; it does NOT implement it and does NOT enable any
DELETE. Deliverable type: design draft for ratification.

---

## Contexto

DL-0018 (`docs/decisions-log.md`) ratified 5-year retention of `audit_chain` as a **scheduled
`DELETE FROM audit_chain WHERE ts < cutoff`** on a non-partitioned table. The delete is wired as a
Helm CronJob (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml` +
`values.yaml`, `lifecycle-audit-retention`, schedule `"0 5 1 * *"`) invoking
`python -m maezo.platform.lifecycle audit-retention`, and the query builder exists
(`src/maezo/platform/retention.py:132-143`). Two independent problems block that delete from being
run safely, and both must be solved before any prune is lawful:

1. **Legal-hold (spoliation).** Handled by the companion `ADR-0020-amendment-draft.md`: there is no
   legal-hold registry, so the unconditional delete would destroy evidence under an active hold.
   Out of scope here except at the interaction points (§ *Prune eligibility*).

2. **Chain-integrity (this ADR).** Even with **zero active holds**, deleting the genesis-anchored
   prefix severs the hash chain's contiguity, and BOTH verifiers then report the *surviving* chain
   as corrupted:

   - **In-memory** `src/maezo/gateway/audit.py::AuditSink.verify_chain` seeds its walk with
     `prev: str = GENESIS_PREV_HASH` (module constant `GENESIS_PREV_HASH: str = "0" * 64`, same
     file) and, for each record in order, rejects on `if record.prev_hash != prev`. After a prefix
     prune, the oldest *surviving* record still carries the `prev_hash` of a now-deleted
     predecessor, so verification fails **at index 0**.
   - **Postgres** `src/maezo/gateway/audit_postgres.py::verify_chain` (module-level coroutine)
     builds a `by_prev` map keyed on `prev_record_hash` (the same loop that detects forks, via
     `if prev in by_prev`) and seeds its walk at `by_prev.get(GENESIS_PREV_HASH)`. With the genesis
     row deleted, that seed is `None`, the walk visits nothing, and every surviving row is reported
     `"unreachable from genesis"` — indistinguishable from a gap/fork corruption.

   - **`UNIQUE(prev_record_hash)` ≠ contiguity.** The non-partitioned schema
     (`src/maezo/platform/migrations/versions/0002_audit_chain.py:27-51`) enforces
     `CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)` (`:44-50`) purely as an
     **anti-fork** guard (at most one successor per record). That property is **orthogonal** to
     genesis-contiguity: a pruned chain still satisfies `UNIQUE` while failing verification. DL-0018
     litigated only the anti-fork property, not the post-delete contiguity.

The compliance analysis (`ADR-0020-amendment-draft.md` §3-bis) concludes: *a hold-aware delete alone
is not sufficient — every prune must be paired with a re-anchoring / signed-checkpoint mechanism,
and until it exists deletion must not be enabled at all (fail-closed).* This ADR is that mechanism.

**Current fail-closed posture (this PR, T2.8):** `python -m maezo.platform.lifecycle audit-retention`
is an intentional **refusal** entrypoint (`src/maezo/platform/lifecycle/__init__.py`) — it logs the
precise blocker and exits non-zero (EX_CONFIG 78). `retention_query()` has zero production callers,
locked in CI (`tests/unit/platform/test_lifecycle.py`). Nothing below is built yet.

## Decisao

**A prune is permitted only when it atomically writes a signed *checkpoint* record that re-anchors
the surviving chain, and both verifiers understand the checkpoint as the walk's anchor. No prune
without re-anchor.** The mechanism, grounded in the existing schema and verifiers:

### 1. Prefix-only pruning

Pruning removes a **contiguous prefix from genesis** (oldest-first) — never a middle window. The
verifiers walk from a *single* anchor following *single-successor* links; only a prefix prune leaves
exactly **one** seam to bridge. A hole in the middle would create a second unbridgeable seam and is
forbidden. (Records are individually eligible by age/hold, but the *set* actually deleted in one
cycle is always the eligible prefix up to the first non-eligible record.)

### 2. The signed checkpoint record

In the same transaction as the deletion (§3 — the prefix is deleted FIRST, then the checkpoint is
inserted into the freed slot), seal a **checkpoint** row that commits to the pruned prefix. It lives
**in `audit_chain` itself** (not a side table) so it occupies the freed `GENESIS_PREV_HASH` slot
under the same `UNIQUE(prev_record_hash)` constraint — a side table would let a concurrent writer
insert a new genesis at `prev_record_hash = "0"*64` and re-fork the head. The checkpoint carries
(proposed columns, all NULL for ordinary `event` rows):

- `record_type` — `'event'` (default) | `'checkpoint'`.
- `prev_record_hash = GENESIS_PREV_HASH` — the checkpoint **becomes the new anchor**. Valid only
  because the old genesis row is deleted *earlier in the same transaction* (§3 step 3), so at INSERT
  time the `"0"*64` slot is free and `UNIQUE(prev_record_hash)` still holds (exactly one row at that
  slot). The delete-before-insert ordering is load-bearing: `uq_audit_chain_prev_hash` is
  **non-deferrable** (`0002_audit_chain.py:50`; retained on purpose — §7), so its check fires at
  statement level, and inserting the checkpoint while the old genesis still occupied the slot would
  abort with a UNIQUE violation before any delete ran.
- `pruned_last_record_hash` — the `record_hash` of the **last (newest) pruned** record, `H_k`. This
  is the **bridge**: the oldest surviving record already has `prev_record_hash = H_k` (immutable —
  it is part of that record's own hash preimage and must not be rewritten), so the checkpoint records
  `H_k` in a *separate* column rather than as its own `prev_record_hash`. No two rows share a
  `prev_record_hash` value (`H_k` is claimed only by the surviving head; the checkpoint claims
  `"0"*64`), so anti-fork is preserved.
- `pruned_count`, `pruned_ts_min`, `pruned_ts_max` — cardinality + time range of the pruned prefix.
- `pruned_merkle_root` — Merkle root over the ordered `record_hash`es of the pruned range (mirrors
  the ADR-0020 `bundle_root` idiom, `src/maezo/gateway/custody.py`), so anyone holding an out-of-band
  cold archive of the pruned rows can prove they belonged to this chain at this point.
- `prune_reason`, `authorizer` — why, and the identity/role that authorized (an accountable human,
  not the CronJob).
- `hold_attestation` — a snapshot proving the legal-hold registry (ADR-0020 amendment) showed **no
  active hold** covering `[pruned_ts_min, pruned_ts_max]` at prune time.
- `signature`, `signature_key_id` — a cryptographic signature over the checkpoint's canonical
  content. "Signed checkpoint": a checkpoint that fails signature verification is treated as tamper.
  Key custody / rotation is a governance item (§ *Open items*).

`record_hash` for the checkpoint is computed over all of the above (extending the existing
`_compute_hash()` preimage), so the checkpoint is itself tamper-evident like any record.

### 3. Atomic delete-then-checkpoint

The prefix delete **and** the checkpoint insert run in **one transaction**, under the **same
per-tenant advisory lock** `emit()` already takes
(`_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"` in
`src/maezo/gateway/audit_postgres.py`, executed as the first statement inside the transaction opened
by `PostgresAuditSink.emit` and again by `PostgresAuditSink.emit_once_status`, so it is held for the
whole insert). Ordering inside the single locked transaction:

1. **SELECT + snapshot** the prune range (the eligible genesis prefix, §1/§6).
2. **Compute** `H_k` (last pruned `record_hash`), the Merkle root, counts/ts-range, and the
   hold-attestation (§2).
3. **DELETE the prefix** — this frees the `GENESIS_PREV_HASH` slot.
4. **INSERT the checkpoint** claiming `prev_record_hash = GENESIS_PREV_HASH` and carrying
   `pruned_last_record_hash = H_k`.
5. **In-txn verify** — exactly one `GENESIS_PREV_HASH`-anchored row exists (the checkpoint) and the
   oldest surviving record's `prev_record_hash` bridges to `H_k` — then **COMMIT**; on any failure,
   **ROLLBACK**. The rollback **restores the deleted prefix** — transaction atomicity is the safety
   net that makes delete-before-insert safe: the chain is never observable half-pruned, and a failed
   checkpoint insert or failed verification leaves the chain exactly as it was.

Why delete-first (not checkpoint-first): `uq_audit_chain_prev_hash` is **non-deferrable**
(`0002_audit_chain.py:50`), so the UNIQUE check runs at statement level — inserting a checkpoint
with `prev_record_hash = "0"*64` while the old genesis row still occupies that slot would abort the
INSERT with a UNIQUE violation before the DELETE ever ran. The constraint stays non-deferrable
deliberately (§7); the ordering adapts to it, not the other way around. The advisory lock guarantees
no concurrent `emit()` can append (or insert a new genesis) anywhere between steps 1-5.

### 4. Verifier support for checkpoint-anchored heads

Both `verify_chain()` implementations gain a checkpoint-aware anchor step (behaviour unchanged when
no checkpoint exists — a chain that still has its real genesis verifies exactly as today):

- Identify the anchor: the row with `prev_record_hash == GENESIS_PREV_HASH`.
- If `record_type == 'checkpoint'`: (a) verify `record_hash` matches the recomputed hash; (b) verify
  `signature` against the trusted `signature_key_id`; (c) **bridge** — the next expected record is
  the one whose `prev_record_hash == checkpoint.pruned_last_record_hash` (Postgres:
  `by_prev.get(checkpoint.pruned_last_record_hash)`; in-memory: set the running `prev` to
  `pruned_last_record_hash` for the record after the checkpoint). Then walk survivors normally.
- Result accounting: `verified` counts the checkpoint + all survivors and must equal `total`. Empty
  survivors (everything older than 5y was pruned) → chain is `[checkpoint]`, valid iff the checkpoint
  verifies. A checkpoint whose `pruned_last_record_hash` has no surviving successor **while survivors
  exist** → invalid (broken bridge). A bad/absent signature → invalid (tamper).

### 5. Repeated prunes (checkpoint mini-chain)

Each later prune deletes the *previous* checkpoint plus the next eligible prefix and writes a **new**
checkpoint that (a) re-takes the `"0"*64` slot (previous checkpoint deleted in the same txn) and (b)
commits to the previous checkpoint's `record_hash` inside its `pruned_merkle_root` preimage — so the
checkpoints form their own verifiable chain of "what was pruned, when, by whom," and the complete
history remains provable from cold storage even across many prune cycles.

### 6. Prune eligibility (interaction with the hold registry)

A record is prunable iff **all** of the following hold: `age > 5y` (DL-0018 retention floor) **AND**
no active legal-hold covers it (ADR-0020 amendment registry) **AND** it is in the contiguous genesis
prefix up to the first non-eligible record **AND** the checkpoint for the cut is written atomically
in the same delete-then-checkpoint transaction (§3). If the oldest record is under an active hold,
the eligible prefix is empty and the cycle is a no-op (deferred, per ADR-0020-amendment Option C —
not retained forever; re-evaluated when the hold lifts).

**Hold-placed-mid-prune race (TOCTOU), acknowledged explicitly:** the hold check (feeding the
`hold_attestation`, §3 step 2) and the DELETE (§3 step 3) are distinct steps — a hold written by a
concurrent transaction between them would be silently violated by the prune. The hold-placement
path must therefore **serialize against the prune**: either hold writes acquire the **same
per-tenant advisory lock** the prune transaction holds (so a hold cannot land mid-prune), or the
prune **re-checks the hold registry between steps 2 and 3 inside the transaction** (so the
attestation is provably current at delete time). Which mechanism (or both, belt-and-suspenders) is
the final form is an **ADR-0020-amendment ratification item** — the hold registry's write path does
not exist yet — but the race itself is a design constraint of this ADR: an implementation without
one of these serializations is non-conforming.

### 7. Fork-detection timing: `UNIQUE(prev_record_hash)` stays NON-DEFERRABLE (decision)

The alternative — making `uq_audit_chain_prev_hash` `DEFERRABLE INITIALLY DEFERRED` so a
checkpoint-first ordering could work — is **rejected**. A deferrable constraint shifts fork
detection from statement time to COMMIT time, and that re-opens exactly the concurrent-genesis-fork
window DL-0018's real-engine testing proved broken: two concurrent forks both passing their checks
under READ COMMITTED (`docs/decisions-log.md` DL-0018 — `test_concurrent_fork_*: assert 0==1`; the
same reason table partitioning was rejected). The statement-level, DB-atomic anti-fork property of
the non-deferrable UNIQUE is **load-bearing** for the whole chain design (`0002_audit_chain.py:44-50`,
`audit.py:37-48` genesis-sentinel rationale) and is not weakened for the convenience of a prune
ordering. Fork detection timing therefore stays at statement level; pruning uses the
delete-then-checkpoint ordering of §3 instead.

## Consequencias

**Positivas:**
- A lawful prune becomes *possible* without destroying the evidentiary property `audit_chain` exists
  for: the surviving chain verifies from the signed checkpoint, and the pruned prefix stays provable
  via `pruned_merkle_root` + cold archive.
- Anti-fork (`UNIQUE(prev_record_hash)`) is preserved exactly — the checkpoint occupies the single
  genesis slot; no row ever shares a `prev_record_hash`.
- The block is made explicit and auditable: the checkpoint records *who* authorized *what* prune and
  the hold-registry state at the time — a prune is itself an accountable, non-repudiable event.

**Negativas (aceitas):**
- Net-new schema (checkpoint columns + migration), verifier changes in **two** implementations, and
  a signing-key custody story — non-trivial, and gated behind ratification.
- Prefix-only pruning cannot reclaim space held by a young-but-cold middle range; acceptable, since
  retention is age-based and the chain is append-only oldest-first anyway.
- Verification now depends on trusting a signing key; key compromise is a new (governed) risk surface
  that did not exist for the pure genesis-anchored chain.

**BLOCKED — implementation precondition (fail-closed):** No code in this ADR is built, and **no
DELETE may be enabled**, until BOTH (1) `docs/compliance/ADR-0020-amendment-draft.md` (legal-hold
registry) AND (2) this ADR-0029 are ratified (DPO + orchestrator). Until then,
`maezo.platform.lifecycle audit-retention` **refuses** (this PR, T2.8). Blocked ≠ done: this ADR
makes the block explicit and designs the exit; it does not take it.

## Open items blocking ratification

- **Signing-key custody / rotation** for checkpoint signatures — who holds it, how it rotates, how
  verifiers obtain the trusted public key(s). Governance + infra decision.
- **Authorizer governance** — who may authorize a prune, and the approval trail (overlaps the
  ADR-0020-amendment hold-governance question).
- Confirmation of the retention floor itself (DL-0018's 5y is DRAFT pending jurídico/regulatório).
- Ratification of the ADR-0020 amendment (legal-hold registry) — a hard precondition; this ADR's
  §6 eligibility and §2 `hold_attestation` depend on it existing.

## Supersedes

None. Complements DL-0018 (adds the re-anchor precondition the DELETE always needed) and is the
chain-integrity half of `ADR-0020-amendment-draft.md` §3-bis / §4.4. Does not amend ADR-0007.

## Nota de reancoragem 2026-09-05 (gap AF-18a) — citações por SÍMBOLO, não por número de linha

**Docs-only. Nenhuma decisão desta ADR foi alterada, adicionada ou removida, e o `Status` continua
`Proposed — requires DPO + orchestrator ratification`.** Autor: `ADR-BATCH` (R1, AGENTE); `docs/adr/`
é CODEOWNED, então isto entra por PR de revisão do dono. Edição in-loco é a convenção certa aqui e
só aqui: esta ADR é `Proposed`, e é justamente o caso que a `## Convencao seguida` da ADR-0032
(`0032:119-128`) separa do caso proibido — uma ADR `Accepted` só muda por ADR nova (foi por isso que
as sete ADRs `Accepted` da ADR-0041 não foram tocadas, e a cerca
`tests/unit/docs/test_adr_amendments.py` garante que continuem byte-idênticas).

**O defeito.** As citações de código da §2 apontavam por número de linha (`audit.py:310-344`,
`:319`, `:320-329`; `audit_postgres.py:333-410`, `:365-377`, `:380`, `:396-406`; e, na §3, a
citação do lock em `audit_postgres.py:137` "held ... at `:219`" — apenas o `:219` que a
acompanhava havia apodrecido; a própria `:137` continuava certa). Os arquivos se moveram desde
2026-07-17 e os números passaram a apontar para outro código — em 2026-09-05, `def verify_chain`
estava em `audit.py:346` e `audit_postgres.py:561`, e `:219` caía em
`PostgresAuditSink.check_ready`. O mecanismo descrito pela ADR permaneceu **intacto o tempo
todo**; o que enganava o leitor (e o auditor) eram as âncoras que de fato apodreceram — `:219` e
os dois intervalos do `verify_chain` — não `:137`, que resolvia corretamente para
`_ADVISORY_LOCK_SQL` o tempo todo.

**A correção.** Os trechos afetados passaram a citar `arquivo::Classe.metodo` / `arquivo::funcao`,
nomes de constante e trechos literais de SQL — âncoras que não se movem quando alguém insere uma
linha. `docs/reviews/adr-0029-erasure-packet.md` §5, único consumidor destas citações no repo,
recebeu o mesmo tratamento, e a "Nota de higiene para o revisor" que ele mantinha desde 2026-08-09
foi fechada (os números que ELE re-derivou naquela data também já haviam apodrecido — a prova de que
re-derivar números é remediar o sintoma).

**O que NÃO foi reancorado, de propósito:** citações a migrations aplicadas (`0002_audit_chain.py:44-50`,
`:50`, `:27-51`) e a seções da própria ADR (`§1 :64-70`). Uma migration aplicada é imutável por
desenho — o número de linha ali é uma âncora estável, e trocá-la por símbolo perderia precisão.

**Cerca.** `tests/unit/docs/test_doc_anchors_by_symbol.py` re-deriva da árvore cada símbolo citado
por esta seção e fica vermelha (a) se o símbolo sumir e (b) se uma âncora `arquivo.py:NN` reaparecer
nos trechos reancorados. É a lição do AF-18a aplicada ao próprio conserto: não basta corrigir os
números, é preciso que a próxima deriva seja descoberta por um teste e não por um auditor.
