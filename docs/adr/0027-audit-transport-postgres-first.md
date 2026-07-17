# ADR-0027: Audit Transport — Postgres-First (amends ADR-0007's Kafka assumption) [T1.10]

**Status:** Accepted — ratified by Rodrigo (repo owner) 2026-07-17, recorded as DL-0026 · **Data:** 2026-07-17 · **Area:** Auditoria / Transporte de Eventos
Owner: audit-persistence-engineer. Defects: B7 (AuditSink in-memory only) / B1 (no Kafka client exists).
Scope: transport decision for the ADR-0007 audit tuple. Does not touch `decision_basis`/`dmn_versions` semantics (T1.5) or the PEP (T1.8, out of scope for this module).

---

## Contexto

ADR-0007 ("Identidade de agente, auditoria e nao-repudio", Accepted 2026-07-05) decides that every
effect on the world is recorded as `(agent_id, agent_version, tenant, tool, input_hash,
decision_basis, dmn_versions, model_id, prompt_version, timestamp)` written to **"Postgres
append-only + topico `agents.audit`"** — i.e. a dual-write to Postgres AND Kafka, in the same
sentence, with no sequencing or fallback semantics specified (docs/adr/0007-agent-identity-audit-non-repudiation.md:11-13).

At the time ADR-0007 was written this was a reasonable target-state description. It has not been
implemented as such, and cannot be today:

- **No Kafka client exists anywhere in `src/`.** `grep -rn "aiokafka\|KafkaProducer" src/` returns
  zero hits outside `pyproject.toml`'s dependency declaration. This is catalogued as defect **B1**
  in `docs/prompts/V2-COMPLETION-PLAN.md:25`: *"No runtime spine: no entrypoint, no external-task
  dispatch loop, no BPMN deploy, no Kafka client; LLM raises `NotImplementedError`"*. `aiokafka` is
  a `pyproject.toml` dependency with zero call sites.
- **The audit chain itself was never durable.** `AuditSink` (`gateway/audit.py`) was an in-memory
  Python list; every process restart forked the chain from genesis. Catalogued as defect **B7**
  (`docs/prompts/V2-COMPLETION-PLAN.md:31`): *"AuditSink in-memory only; audit-chain schema exists
  unused"*. The `audit_chain` table (`platform/migrations/versions/0002_audit_chain.py`) has existed
  since 2026-07-05 with nothing writing to it.
- **DL-0018** (2026-06-14) already litigated the *storage* shape of `audit_chain` in Postgres terms
  specifically — non-partitioned, `UNIQUE(prev_record_hash)`, atomicity under concurrent writers —
  with zero mention of Kafka as part of that durability guarantee. The append-only, tamper-evident
  property ADR-0007 actually cares about ("cadeia completa e reproduzivel") is a property of the
  **durable store**, not of the transport that reaches it.

Building a real Kafka producer integration (topic provisioning, delivery semantics, DLQ, retry
policy, and — critically — deciding what "the audit chain" even means when two independently-lagging
consumers, Postgres and Kafka, can disagree about what has been durably audited) is a substantial
scope on its own, and doing it against a runtime that has **no other Kafka producers/consumers
wired up at all** (B1) would mean building and testing it in isolation, disconnected from the rest
of the eventing story that B1's resolution will eventually define.

Meanwhile, audit durability cannot wait: T1.10's kill-test acceptance criterion (process crash must
lose zero flushed audit records, chain hash verified across restart) needs a durable sink *now*, and
Postgres is already the platform's committed durability layer for every other stateful component
(`agent_checkpoints`/`agent_memory`, `a2a_idempotency`/`driver_idempotency`, `custody_bundles`,
`erasure_log` — all in the same `platform/migrations/` tree, all schema-per-tenant per
`platform/migrations/env.py`).

## Decisao

**Postgres is the sole durability guarantee for the audit chain until a Kafka transport is
separately designed and ratified.** `PostgresAuditSink` (`gateway/audit_postgres.py`, this PR)
writes every `AuditRecord` straight through to the tenant's `audit_chain` table on `emit()` —
fail-closed (a write failure raises to the caller; the action is never treated as audited if it
was not durably persisted) — with no Kafka leg, dual-write, or best-effort fan-out.

This **amends** ADR-0007's transport clause: read "Postgres append-only + topico `agents.audit`"
as "Postgres append-only **now**; Kafka mirroring **deferred**, re-entry criteria below." Every
other part of ADR-0007 (the audit tuple's fields, non-repudiation via signed identity, the
`decision_basis` structure, HITL approver recording) is unchanged.

**Kafka re-entry criteria** — Kafka is reintroduced as an *additional* transport (mirroring the
already-durable Postgres chain, not replacing it as the durability guarantee) when:

1. B1 lands a real Kafka client and at least one other producer/consumer pair is running against
   it in this runtime (so the audit topic is not the first and only thing exercising the client),
   **and**
2. a consumer of `agents.audit` actually exists with a stated purpose (e.g. `amh-data-platform`
   ingestion, real-time alerting, or an external SIEM) — building a producer with no consumer is
   scope without a requirement, **and**
3. the dual-write consistency question is answered in its own ADR: what happens when Postgres
   commits and the Kafka publish fails (or vice versa if Kafka ever becomes primary)? Given
   `PostgresAuditSink`'s fail-closed contract, the natural shape is Postgres-commit-then-publish
   (Kafka becomes a downstream mirror of the durable chain, published *after* the transactional
   commit that already succeeded — e.g. via a `LISTEN/NOTIFY` or outbox-pattern relay reading
   `audit_chain`), not a synchronous dual-write inside `emit()`'s transaction. That design is out
   of scope for this ADR and for T1.10.

## Consequencias

**Positivas:**
- Audit durability lands now, using infrastructure (Postgres, schema-per-tenant, advisory locks)
  the platform already runs and every other stateful table already depends on — no new
  operational surface.
- `PostgresAuditSink`'s fail-closed contract is simpler to reason about and to verify (kill-test,
  `verify_chain()`) without a second, independently-lagging transport in the loop.
- Removes a currently-false claim from the codebase: ADR-0007 described a Kafka leg that has never
  existed in `src/`; this ADR makes the documented architecture match what is actually built and
  verifiable.

**Negativas (aceitas):**
- No real-time/streaming consumer of the audit trail exists yet (e.g. for `amh-data-platform`
  ingestion or alerting) — anything that wanted to tail `agents.audit` as an event stream must poll
  `audit_chain` instead until Kafka re-entry. Acceptable: no such consumer exists today either
  (B1), so nothing regresses; this is a deferred capability, not a removed one.
- If/when Kafka is reintroduced, the dual-write consistency design (outbox/relay vs. synchronous
  publish) is new work, not a trivial "add the producer back" — flagged explicitly above as a
  re-entry criterion, not hand-waved.

## Supersedes

Amends ADR-0007 (transport clause only; does not supersede it). No other ADR affected.
