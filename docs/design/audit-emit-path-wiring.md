# Design: Audit-Emit Path Wiring — closing the "no live effect writes the durable chain" gap

**Status:** Proposed (design only — no `src/` changes, no PR, no ledger entry) · **Date:** 2026-07-18
· **Area:** Auditoria / Non-repudiation (ADR-0007, L0) · **Author role:** R1 audit-persistence architect
· **Verified against:** working tree at `0b5e1b9` (origin/main `e8116ba`, `git fetch origin` clean)

> **L0 / non-repudiation territory.** This wires the ADR-0007 invariant — *every effect on the world
> is recorded* — into a live decision path. It is designed with the rigor of a fail-closed money/PHI
> path: no un-audited effect may ever be silently accepted. The L0-touching sub-tasks are flagged
> **R1-authored + R1-verified** in §7.

---

## 1. Gap Evidence

ADR-0007 (`docs/adr/0007-agent-identity-audit-non-repudiation.md:11-13`) requires that *"Todo efeito no
mundo registra `(agent_id, agent_version, tenant, tool, input_hash, decision_basis, dmn_versions,
model_id, prompt_version, timestamp)`"*. The **sink** for that exists and is durable/fail-closed
(T1.10), and the **provenance plumbing** exists (T1.5/ADR-0028). What does **not** exist is any live
call path that constructs and persists an `AuditRecord` from a real decision.

### 1.1 `emit()` has ZERO production callers

```
$ grep -rn "\.emit(" src/
src/maezo/gateway/audit_postgres.py:210:  `AuditSink.emit()` contract) and returns the record hash.   # <- docstring only
```

The single hit is a **docstring line** in `PostgresAuditSink.emit`'s own body
(`src/maezo/gateway/audit_postgres.py:210`). No worker, no agent node, no PEP path calls `.emit(...)`.
The in-memory `AuditSink.emit` (`src/maezo/gateway/audit.py:279`) and the durable
`PostgresAuditSink.emit` (`src/maezo/gateway/audit_postgres.py:202`) are both defined and unit-tested,
and reachable from **tests only** (`tests/unit/gateway/_audit_killtest_writer.py:39`,
`tests/unit/gateway/test_audit.py`, `tests/unit/gateway/test_audit_postgres.py`).

### 1.2 `AuditRecord(...)` has exactly one `src/` construction site — the READ path

```
$ grep -rn "AuditRecord(" src/
src/maezo/gateway/audit_postgres.py:317:    return AuditRecord(   # _row_to_record: reconstructs a record FROM a stored row to re-hash it
```

`_row_to_record` (`audit_postgres.py:309-330`) is the *verification* decoder — it rebuilds a record
from a stored `audit_chain` row so `verify_chain` can recompute its hash. It is a **read/decode**
site, never a write. No production code constructs an `AuditRecord` from a decision.

### 1.3 The `dmn_versions` docstring already names this exact gap

`AuditRecord.dmn_versions`'s own docstring (`src/maezo/gateway/audit.py:148-169`) states the
precondition T1.5 closed (workers now call the real engine and get a `DmnVersion`), then flags the
residual: *"no production code path yet constructs an `AuditRecord` FROM a worker/agent decision at
all (`emit()` has no caller in `src/` today — grep confirms it) — that end-to-end worker-to-audit-chain
wiring is a separate, larger integration task."* ADR-0028 §4
(`docs/adr/0028-dmn-evaluation-engine-side.md:137-149`) likewise says the `dmn_versions` provenance
"becomes real … feeds T1.10 records" but does **not** itself wire the write. The DMN-versions test
(`tests/unit/gateway/test_audit_dmn_versions.py:12-16`) is explicit that it proves only that the schema
is *populatable*, not that any caller writes it.

**Conclusion (verified):** the durable chain is inert. `audit_chain` (migration
`0002_audit_chain.py:26`) has existed since 2026-07-05 with nothing writing to it (ADR-0027
`docs/adr/0027-audit-transport-postgres-first.md:28-29`). This design wires the first — and, for the
dominant effect surface, the only — production writer.

---

## 2. Emission-Point Decision

### 2.1 The candidate effect-producing sites

| # | Candidate | What it is | Verdict |
|---|---|---|---|
| 1 | **Worker harness `_handle` success path** (`tools/workers/harness.py:821-834`, the `complete` call at `:823`) | The residual engine→worker leg (ADR-0001): the ONLY place the daemon changes engine/world state — every SP-OP-* task completion, money release, ANS submission, LGPD action flows through exactly one `complete` per task | **CHOSEN — single R1 chokepoint** |
| 2 | Harness `bpmnError` / `failure` paths (`harness.py:835-884`) | Control-flow signals to the engine (retry / incident / boundary), not world effects; already fail-closed to a human via incident | Deferred to R2 (audited-refusal, additive) |
| 3 | **PEP decision point** (`gateway/pep.py:412` `PEP.evaluate`) | Policy decision (ALLOW/DENY/REQUIRE_HUMAN) | **REJECTED** — `PEP.evaluate` has **zero runtime callers** (grep: only `build_pep()` is called, at boot, as a readiness probe — `agent_runtime/service.py:239`, `gateway/service.py:79`). Nothing to hook. Wiring PEP *into* the request path is itself an unbuilt task the `pep.py:34-40` scope note defers |
| 4 | **DMN transport** (`tools/workers/dmn_transport.py:238` `evaluate`) | A decision-support *read* — one task may evaluate 2+ tables (e.g. `pagto`: `pagto_admissibility` + `pagto_alcada`) | **REJECTED as emit point** (would double-count and audit non-effects); **CHOSEN as the `dmn_versions` capture point** — see §3 |
| 5 | **Agent graph effect nodes** (`agents/*/graph.py`) | Agents produce dossiers / routing / proposals; L0-hard forbids them from denying/releasing/discharging (`beatriz/graph.py:9-11`, `pep.py:71-79`). The real effect happens later when the engine runs an external task → site 1 | Agent-side *process-start* + *A2A delegation* are real effects but a **separate, smaller** chokepoint — deferred to R2 |

### 2.2 Why site 1 is the correct single chokepoint

- **Completeness.** ADR-0001 makes the worker harness the sole conduit for engine→worker effects.
  Every consuming process advances only when its external task is `complete`d. One `complete` call per
  task (`harness.py:823`), reached exactly once per `_handle` (the harness *"completes/fails/reports
  EXACTLY ONCE per task"* — `harness.py:22-25`). Auditing there captures every effect, once.
- **No gap.** Placing the emit immediately *before* `complete` on the success path means the audit
  write gates the effect (§4).
- **No double.** The harness owns retry; the engine re-delivers the **same** external task on lock
  expiry / failure-with-retries (`harness.py:53-55`, `:539`). Re-delivery re-enters `_handle`, so the
  emit is naturally at-risk of running twice for one logical effect. An idempotency guard keyed on the
  external-task identity closes this (§4.3). Note in-process `WorkerBase` retry (`base.py:131-189`,
  default `max_retries=1` for the runtime path — `base.py:266`) re-runs `execute()` *before* the
  handler returns, so it never reaches the single post-return emit/complete — only engine re-delivery
  can double-fire, and that is what the guard covers.
- **Provenance is in reach.** The DMN `DmnVersion` is produced inside the handler (§3); a per-task
  collector makes it available at the emit point without changing worker signatures.

**Agent identity note (ADR-0007 tuple).** Workers are deterministic BPMN handlers, not LLM agents, so
`model_id`/`prompt_version` are `None`; `agent_id`/`agent_version` must resolve to a **stable service
identity**, not the ephemeral `worker_id` (pod name, distinct per replica — `settings.py:8-10`).
Recommendation: `agent_id = "operadora-worker"` (or the SP-OP module owning the topic), `agent_version
= importlib.metadata.version("maezo-operadora")` (`0.2.0` today, `pyproject.toml:3`) or an injected
`MAEZO_APP_VERSION`. The `action` (ADR-0007 "tool") = `task.topic`. The signed service-account/cert
identity ADR-0007 also asks for (mTLS + signed Agent Card) is orthogonal identity hardening, out of
this task's scope (flagged R2, §7 T-G).

---

## 3. `decision_basis` + `dmn_versions` dataflow (PHI-safe)

### 3.1 The dataflow gap today

Worker functions evaluate DMN and **discard the version**. E.g. `pagto.route_aprovacao`
(`tools/workers/pagto.py:181-211`) does `rows, version = evaluate_sync(...)` then returns only
`{faixa_valor, grupo_aprovador, tier_minimo}` — `version` never leaves the function. The harness
(`_handle`) sees only the returned output dict; the `DmnVersion` is trapped. `evaluate_sync`
(`dmn_transport.py:330`) runs the evaluation inside `asyncio.run(...)` on a worker OS thread
(`asyncio.to_thread`, `harness.py:611`), so it cannot simply return "up the stack" to the async
harness.

### 3.2 Mechanism: a mutable-container ContextVar collector (chosen)

Add a module-level `ContextVar` holding a **mutable dict** used as a per-task collector:

```python
# tools/workers/dmn_transport.py (or a small new tools/workers/_audit_ctx.py)
_DMN_AUDIT_COLLECTOR: ContextVar[dict[str, Any] | None] = ContextVar("dmn_audit_collector", default=None)

def _record_dmn_version(version: DmnVersion) -> None:
    collector = _DMN_AUDIT_COLLECTOR.get()
    if collector is not None:                      # None = not in an audited dispatch (tests, tools)
        collector[version.key] = version.to_audit_dict()   # {"version": N, "id": ..., "deploymentId": ...}
```

- **Write point:** `DmnTransport.evaluate` (both `CibSevenDmnTransport.evaluate` and `FakeDmnTransport`)
  — or, less invasively, `evaluate_sync` (`dmn_transport.py:330-344`) — calls `_record_dmn_version(version)`
  on every successful evaluation. This is the ONE place all ~11 migrated tables funnel through
  (`ADR-0028` migration table, `0028…md:174-186`), so a single hook covers `pagto_alcada`,
  `pagto_admissibility`, `glosa_*`, `recurso_*`, `inadimplencia_status`, `adequacao_gap`, etc.
- **Scope point:** `WorkerHarness._handle` sets a fresh collector before dispatch and reads it after:

```python
# harness.py::_handle, success path
collector: dict[str, Any] = {}
token = _DMN_AUDIT_COLLECTOR.set(collector)
try:
    out_vars = await handler(task)                 # DMN evals mutate `collector` in place
finally:
    _DMN_AUDIT_COLLECTOR.reset(token)
# ... build AuditRecord(dmn_versions=dict(collector), ...) then emit_once() then complete()
```

**Why a mutable container survives the thread boundary.** `asyncio.to_thread` runs the sync handler
under `contextvars.copy_context()` — the copy shares the *same dict object* the var is bound to (only
the var→object binding is copied, not the object). Mutations inside the thread (`collector[key] = ...`)
are therefore visible to `_handle` after `handler(task)` returns. A ContextVar holding an *immutable*
value would not work here; the mutable-dict container is deliberate. (Raw-handler modules like
`events.py` run in the same context and work trivially.)

**Rejected alternative — thread `dmn_versions` through return values.** Changing the
`fn(variables) -> dict` boundary (or the `FunctionWorker` contract, `base.py:231-282`) to also return
provenance touches ~11 functions across 9 modules, several on the **money path** (`pagto`, `contas`,
`recurso`) — high blast radius on L0-adjacent code, and it would leak audit metadata into BPMN process
variables. Rejected.

### 3.3 PHI discipline — what is safe to persist

The chain is **durable** and long-lived (regulatory 5+ years, ADR-0007 Consequencias). It must carry
**no raw PHI and no resolvable business identifiers**. The graphs already enforce a class-token /
k-anon / no-resolvable-PHI egress discipline (`agents/andre/graph.py:71-74,1153-1165`,
`events.py:47-51` flags even `numero_guia_tiss` as sensitive). The emit path must honour the same rule:

- **`dmn_versions`** — `{key: {"version": int, "id": str, "deploymentId": str}}`. These are
  **class tokens** (decision-definition ids/versions), never clinical values. Safe. Fail-closed by
  construction: `CibSevenDmnTransport` raises rather than emit `"unknown"` (`dmn_transport.py:229-234`,
  ADR-0028 §2).
- **`decision_basis` (`AuditRecord.details`)** — build from **routing/enum outputs only**
  (`roteamento`, `faixa_valor`, `grupo_aprovador`, `desfecho`, `tier_minimo` — all bounded tokens) plus
  a **one-way hash of the raw inputs**: `details["input_sha256"] = hash_input(task.variables)` using the
  existing `hash_input` helper (`audit.py:51-61`). The raw PHI-bearing `task.variables` are hashed,
  **never stored**. Do **not** blindly dump `task.variables` or `out_vars` into `details`.
- **`input_hash` column** — the schema derives it as `hash_input(details)` (`audit.py:217-224`,
  `audit_postgres.py:234`), so `details` must already be PHI-free; embedding `input_sha256` inside
  `details` binds the record to the exact tool input without persisting it. Canonicalization + hashing
  is handled by `AuditRecord.__post_init__` (`audit.py:192-215`) — `details`/`dmn_versions` pass through
  `canonicalize_jsonb` so the write-hash equals the verify-hash (the H1 fix, `audit.py:64-105`).

**Curation is a per-worker allowlist.** A small `decision_basis` builder should select an explicit set
of non-PHI output keys per topic (mirroring `pick_fields`, `base.py:285-299`), defaulting to
"identifiers hashed, only enumerable tokens in the clear" — never a passthrough. This is an L0 concern
(§7 T-C).

---

## 4. Fail-closed ordering (the ADR-0007 invariant)

### 4.1 The two-resource problem

The **effect** (engine advances the process) is committed by the engine, in the engine's own
transaction, via a REST `complete` (`harness.py:823` → `CibSevenWorkerTransport.complete`,
`harness.py:323-329`). The **audit** is a row in the tenant's Postgres `audit_chain` — a *different*
system. There is no shared transaction. So we must *sequence* them, and the sequence must be
idempotent under engine re-delivery.

### 4.2 Ordering: audit-before-complete (chosen)

```
# harness.py::_handle, success path — REPLACES the current :822-823 pair
out_vars = await handler(task)                          # 1. business logic + DMN eval (collector filled)
record  = build_audit_record(task, out_vars, collector, settings)   # 2. curate PHI-safe basis (§3.3)
await audit_sink.emit_once(record, idempotency_key=effect_key(task))# 3. FAIL-CLOSED durable audit
await self._transport.complete(task.task_id, self._worker_id, out_vars)  # 4. commit the effect
outcome = "completed"
```

- **Step 3 raises → step 4 never runs.** `emit_once` inherits `emit`'s fail-closed contract (raises
  `AuditPersistenceError` on any DB failure — `audit_postgres.py:202-216,242-255`). An exception here
  propagates into `_handle`'s existing `except` ladder (`harness.py:874-884`): a `RuntimeError`-family
  DB error classifies as **transient → engine-computed retry** (the task is *not* completed → the engine
  re-delivers → we try again). The effect is therefore **never committed to the engine without a
  preceding durable audit row.** This is the ADR-0007 invariant: *an un-audited effect cannot be
  silently accepted.* ✓
- **Direction of failure is correct.** The only reachable inconsistency is "audit row written, but
  `complete` then failed" — i.e. a (transiently) *audited-but-not-yet-mechanically-applied* effect,
  which self-heals on re-delivery (§4.3). The forbidden direction — an engine effect with **no** audit
  row — is structurally impossible. Auditing slightly ahead of the mechanical commit is the fail-closed
  choice: the record attests "the platform decided Y for task X and committed to completing it"; the
  `complete` is the mechanical realization that the retry loop guarantees.

### 4.3 Idempotency: exactly-once audit per effect, atomically

To prevent a **double-audit** when step 3 succeeds but step 4 fails and the task is re-delivered, add
an idempotency guard **inside the sink's existing per-tenant advisory-lock transaction** so "audit
written" and "dedup claimed" are atomic:

```python
# audit_postgres.py — new method, reusing emit()'s transaction + advisory lock
async def emit_once(self, record: AuditRecord, *, idempotency_key: str) -> str:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(_ADVISORY_LOCK_SQL, self._tenant_id)          # per-tenant serialization
        claimed = await conn.fetchval(
            "INSERT INTO audit_emit_dedup (key, tenant, created_at) VALUES ($1, $2, now()) "
            "ON CONFLICT (key) DO NOTHING RETURNING key", idempotency_key, self._tenant_id)
        if claimed is None:
            return "ALREADY_AUDITED"      # this effect already has a chain row — no second link
        tail = await self._fetch_tail(conn)     # existing tail read (:267-288)
        record.prev_hash = tail
        record.record_hash = record._compute_hash()
        await conn.execute(INSERT_SQL, ...)      # existing chain insert (:226-241)
    return record.record_hash
```

- `effect_key(task) = f"{tenant}:{task.task_id}"`. The CIB Seven / Camunda-7 external task **id is
  stable across re-delivery** (failure-with-retries and lock-expiry re-fetch the *same* task entity, not
  a new one); a composite `f"{tenant}:{process_instance_id}:{task_id}"` is the belt-and-suspenders form.
- Because the dedup INSERT and the chain INSERT share **one transaction under the advisory lock**, a
  crash between them rolls back **both** → re-delivery re-emits cleanly (**no gap, no duplicate**). The
  dedup check is serialized with the tail read, so there is no TOCTOU.
- **New table** `audit_emit_dedup(key text PRIMARY KEY, tenant text NOT NULL, created_at timestamptz
  NOT NULL DEFAULT now())` — schema-per-tenant, migration `0005` (down_revision `0004`), DDL
  unqualified, **mirroring `driver_idempotency`** (`0003_a2a_idempotency.py`; the house durable-dedup
  pattern ADR-0024 established). Retention = a periodic `DELETE … WHERE created_at < now() - interval`
  sweep (mirrors ADR-0024 §4 and the audit retention model). `audit_chain`'s own schema is **untouched**
  (an L0 table — minimize changes; dedup lives in a sibling table).

### 4.4 Rejected ordering alternatives

- **Complete-before-audit.** If `complete` succeeds and `emit` then fails, the effect is committed on
  the engine but **unaudited** — the exact ADR-0007 violation. **Rejected.**
- **Outbox-relay now** (write audit + a "complete intent" row atomically in Postgres; a relay process
  calls `engine.complete`). Strictly more robust, but it introduces a new long-running relay, a new
  table, and a new failure mode. ADR-0027 (`0027…md:63-78`) explicitly **reserves the outbox/relay
  substrate for the deferred Kafka re-entry** (criterion 3: "Postgres-commit-then-publish … via
  LISTEN/NOTIFY or an outbox-pattern relay reading `audit_chain`"), and mandates *"no new operational
  surface"* for the Postgres-first phase (`0027…md:82-84`). Building the relay now is scope ADR-0027
  deferred. **Rejected for R1; noted as the natural R2+ evolution that also becomes the Kafka-mirror
  substrate.**
- **The engine's own transaction.** Cannot enclose the audit write — `audit_chain` is the tenant's
  Postgres, the engine commit is a separate system. Not available.

---

## 5. Concurrency & chain integrity

The harness dispatches tasks **concurrently** as tracked background tasks (`_spawn`, `harness.py:735-742`;
loop re-polls immediately — design §6). Many `_handle` coroutines can therefore call `emit_once`
concurrently for the **same tenant** (one daemon = one `settings.tenant_id`, `settings.py:25`), and
multiple daemon **replicas** may run for the same tenant.

- **Single-writer-per-tenant is already guaranteed by the sink.** `emit`/`emit_once` take
  `pg_advisory_xact_lock(hashtext(tenant))` (`audit_postgres.py:137,220`) inside the transaction that
  reads the tail and inserts, so the "read tail → compute hash → insert" critical section is atomic
  per tenant, across coroutines **and** across processes/replicas (a Postgres-level lock). Concurrent
  emits from concurrent worker tasks serialize; the chain never forks. This is exactly the property
  `tests/unit/gateway/test_audit_postgres.py:474` (`test_concurrent_emits_same_tenant_serialize_without_fork`)
  already proves for the sink in isolation; the wiring preserves it because it reuses the same
  transaction.
- **The dedup insert is inside the same lock**, so the idempotency claim can't race the tail read.
- **Belt-and-suspenders anti-fork** (`UNIQUE(prev_record_hash)`, `0002_audit_chain.py:50`; genesis
  sentinel `GENESIS_PREV_HASH`, `audit.py:37-48`) remains the fallback if the lock were ever bypassed.
- **One sink instance per daemon.** Build one `PostgresAuditSink(dsn, settings.tenant_id)` at boot,
  share it across all worker tasks (its asyncpg pool, `max_size=10`, `audit_postgres.py:192-200`,
  handles the concurrency; each emit acquires a connection + the advisory lock). The `setup=`
  search-path re-application (`audit_postgres.py:188-199`) already handles pool-connection reuse.

**Ordering caveat (documented, acceptable):** because tasks run concurrently and each takes the lock
independently, the chain order reflects *emit* order, not *business* order — two effects in flight may
land in either order in the chain. That is fine: the chain proves *integrity and completeness*
(tamper-evidence + no gaps), not a total business ordering; each record carries its own `timestamp`.

---

## 6. Test & acceptance strategy

### 6.1 Unit (fast, `FakeWorkerTransport` + `FakeDmnTransport` + fake sink)

Extend the `_handle` tests. With a fake sink recording calls:
1. **Exactly-once, right fields.** One successful task → `emit_once` called once; the record carries
   `tenant_id`, `action=task.topic`, PHI-safe `decision_basis` (assert `input_sha256` present, raw vars
   absent), and `dmn_versions` populated from `FakeDmnTransport` via the collector.
2. **Ordering.** Assert `emit_once` is recorded **before** `complete` (shared call-order log).
3. **Fail-closed.** Sink raises `AuditPersistenceError` → `complete` **not** called → a `failure`
   (retry/incident) is reported; the task is not marked completed.
4. **dmn_versions capture across the thread bridge.** A `FunctionWorker` (dispatched via
   `asyncio.to_thread`) that evaluates a `FakeDmnTransport` table → collector observed by `_handle`
   (regression test for the mutable-container ContextVar contract).
5. **Idempotency.** Simulate engine re-delivery (same `task_id`) after an emit-then-complete-fail →
   second `emit_once` returns `ALREADY_AUDITED`, writes no second link, and `complete` is retried.

### 6.2 Integration (real Postgres — mirrors ADR-0024's real-PG suite and T1.10's kill-test)

- **Acceptance (the headline).** A real `WorkerHarness` wired to a real `PostgresAuditSink` against a
  `bootstrapped_tenant` schema (`alembic upgrade head`, including migration `0005`) drives ONE task to
  completion (via `FakeWorkerTransport`, or a real engine under `docker compose --profile core`).
  **Assert:** a queryable `audit_chain` row exists for that effect with **non-null `dmn_versions`**, and
  `verify_chain(dsn, tenant).valid is True`. This is the concrete "a live worker execution produces a
  verifiable audit_chain row with non-null dmn_versions" acceptance.
- **Concurrency.** N concurrent tasks, same tenant → `verify_chain` valid, single tail (extends
  `test_concurrent_emits_same_tenant_serialize_without_fork` to the harness path).

### 6.3 Kill-test extension (the fail-closed + idempotent proof)

Extend the T1.10 kill-test harness (`_audit_killtest_writer.py` + `test_audit_postgres.py:552`) from a
bare sink writer to a **worker→emit→complete** path: SIGKILL the writer **between `emit_once` and
`complete`**. On restart, re-deliver the same task_id. **Assert:** the chain contains **exactly one**
record for the effect (dedup prevented a double), and the effect ultimately completes (no gap). This
proves end-to-end what §4 argues: zero un-audited effects, zero double-audits, across a hard crash.

### 6.4 Property tests

- **No-gap:** over any interleaving of emit/complete success/failure + re-delivery, every completed
  effect has ≥1 chain record.
- **Exactly-once:** …and no completed effect has >1 chain record (the dedup invariant).
- **Integrity:** the resulting chain always `verify_chain`-valid (no fork/tamper).

---

## 7. Implementation task breakdown (tiers)

L0 rule: anything on the audit-chain **write path** or the money-path **provenance** is
**R1-authored + R1-verified**. Wiring/infra that only fails *closed* is R2-authored + R1-verified.

### R1 — L0 invariants (R1-authored + R1-verified)

- **T-A · `emit_once` + dedup migration** (`audit_postgres.py` + new `0005_audit_emit_dedup.py`).
  Atomic dedup-claim + chain-insert inside the existing advisory-lock transaction (§4.3). `audit_chain`
  schema untouched; new sibling table mirrors `driver_idempotency` (`0003`). **L0** — touches the chain
  write path. *Verify:* concurrency + kill-test (§6.2–6.3).
- **T-B · DMN provenance capture** (`dmn_transport.py` + tiny `_audit_ctx`). Mutable-container
  ContextVar; `_record_dmn_version` hooked into `evaluate`/`evaluate_sync`; collector reset per dispatch
  (§3.2). **L0** — non-repudiation of the DMN version that drove a money decision. *Verify:* §6.1(4).
- **T-C · Harness emit wiring** (`harness.py::_handle`). Build the PHI-safe `AuditRecord` (curated
  `decision_basis` allowlist + `input_sha256`, §3.3), `emit_once` **before** `complete`, fail-closed
  ordering (§4.2). Inject `audit_sink` + effect-key builder as a harness seam. **L0** — the core "no
  un-audited effect" invariant. *Verify:* §6.1(1-3,5), §6.2, §6.3.

### R2 — additive / infra (R2-authored, R1-verified where L0-adjacent)

- **T-D · Worker-daemon Postgres wiring** (`worker_runtime/settings.py` + `service.py` + Helm).
  **Gap found:** `WorkerRuntimeSettings` has **no** Postgres DSN field (it has `cibseven_base_url`,
  `kafka_bootstrap_servers`, but no `DATABASE_URL` — `settings.py:19-60`); the worker daemon is
  currently Postgres-less. Add `database_url: SecretStr | None` (alias `DATABASE_URL`); construct the
  sink in `_bring_up_dependencies` (STEP B, `service.py:258-319`, pool lazy like the DMN transport at
  `:278`); add a **fail-closed `audit_sink_ready` readiness check** (`service.py:171-252`) with a
  bounded connectivity probe — an un-auditable daemon must not go `/readyz` green (ADR-0007). Inject the
  Aurora secret via `deployment-worker-daemon.yaml`. *L0-adjacent (fail-closed readiness) → R1-verified.*
- **T-E · Audited-refusal on failure/bpmnError paths** (`harness.py:835-884`). Record DENY /
  incident-class outcomes. Additive (failures already fail-closed to incidents), lower tier.
- **T-F · Agent-runtime effect chokepoint.** Audit process-start + A2A delegation (the second effect
  surface, §2.1 site 5), reusing `emit_once`. Separate, smaller.
- **T-G · Identity hardening (ADR-0007 signed identity).** Service-account cert / signed Agent Card for
  `agent_id`; replace the interim stable-string identity. Orthogonal, larger — explicitly **not** this
  task.

**Count:** 3 R1 (L0) + 4 R2 = **7 tasks**. The R1 three are the minimum to make the durable chain
non-inert for the dominant effect surface (worker completions) with the fail-closed + exactly-once
guarantees ADR-0007 demands; ADR-0027's outbox/Kafka relay and ADR-0007's signed identity remain
correctly deferred.

---

## References

- **ADR-0007** `docs/adr/0007-agent-identity-audit-non-repudiation.md:11-13` — the audit tuple; "todo
  efeito no mundo registra".
- **ADR-0027** `docs/adr/0027-audit-transport-postgres-first.md:52-78` — Postgres is the sole durability
  guarantee; fail-closed `emit`; Kafka/outbox-relay re-entry criteria (deferred).
- **ADR-0028** `docs/adr/0028-dmn-evaluation-engine-side.md:137-149` — `dmn_versions` provenance,
  authoritative version (never `"unknown"`), "feeds T1.10 records".
- **ADR-0024** `docs/adr/0024-durable-idempotency-resume-inbound-drivers.md` — the Postgres durable-dedup
  pattern (`PostgresDedupeStore` / `driver_idempotency`) T-A reuses.
- **Code:** `gateway/audit.py:132-250` (`AuditRecord`, `hash_input`, `canonicalize_jsonb`);
  `gateway/audit_postgres.py:202-288` (`emit`, advisory lock, `_fetch_tail`);
  `tools/workers/harness.py:802-897` (`_handle`); `tools/workers/dmn_transport.py:104,238-344`
  (`DmnVersion.to_audit_dict`, `evaluate`, `evaluate_sync`); `tools/workers/pagto.py:181-211` (version
  discarded); `runtime/worker_runtime/service.py:258-319` (bring-up); `runtime/worker_runtime/settings.py`
  (no DSN — gap); `platform/migrations/versions/0002_audit_chain.py`,`0003_a2a_idempotency.py`;
  `tests/unit/gateway/test_audit_postgres.py:474,552`, `_audit_killtest_writer.py`,
  `test_audit_dmn_versions.py`.

---

## Revision (R1 review response)

R1 adversarial review returned **REVISE**: the core mechanism (harness `_handle` chokepoint,
emit-before-complete, dedup inside the advisory-lock transaction) is confirmed **SOUND**, and the
two subtlest claims held up — the dedup key **is** stable (`task.task_id` = CIB Seven external-task
id, stable across re-delivery) and the ContextVar `dmn_versions` bridge **is** race-free
(`copy_context` shares the dict; `_spawn` isolates each `_handle`). Two must-fix holes and one
should-fix, resolved below point-by-point. §§1–7 above are preserved unchanged; this section is
authoritative where it revises them.

### Citation re-pin (hygiene): §§2–6 harness line numbers were +81 stale

§§1–7 were pinned to the working-tree checkout; harness.py on **current origin/main** is ~81 lines
longer. **Only `harness.py` refs were stale** — audit_postgres.py, audit.py, dmn_transport.py,
base.py, pagto.py, worker_runtime/{service,settings}.py, pep.py, and migrations 0002/0003 all match
what §§1–7 cite. Corrected `harness.py` edit-targets for the R2 implementer:

| §§1–7 said | origin/main | What it is |
|---|---|---|
| `:802` | **`:883`** | `async def _handle` |
| `:822` | **`:903`** | `out_vars = await handler(task)` |
| `:823` | **`:904`** | `await self._transport.complete(` (the effect) |
| `:821-834` | **`:902-915`** | success path block (emit inserts between `:903` and `:904`) |
| `:835-884` | **`:916-965`** | `except WorkerBpmnError` (`:916`) … `except Exception` (`:955`) ladder |
| `:608-611` | **`:672-692`** | `register_worker` adapter; `asyncio.to_thread` at `:692` |
| `:735-742` | **`:816-823`** | `_spawn` |
| `:539` | **`:620`** | "Idempotency is the handler's responsibility" |
| `:22-25` | **`:23`** | "completes/fails/reports EXACTLY ONCE" |
| `:53-55` | **`:53`** (unchanged) | "handlers MUST be idempotent (same task_id -> same result)" |
| `:897` | **`:978`** | `handle_task = _handle` alias |
| `:323-329` | **`:404-410`** | `CibSevenWorkerTransport.complete` |

### MUST-FIX 1 — the emit-before-complete guarantee rests on an unstated, already-fragile invariant

**Resolved.** §4.2's fail-closed property silently assumed a precondition. State it explicitly:

> **Precondition P1 (handler purity).** A worker handler's *only* externally-visible effect is the
> harness's own terminal `complete` / `bpmnError` / `failure` call (`harness.py:904` / `:916` /
> `_report_failure`). The handler body is a pure, deterministic transform of process variables
> (`fn(variables) -> dict`, `base.py:272`) that performs **no** non-idempotent external side-effect
> and mints **no** non-deterministic persisted identifier before returning.

**Why P1 is load-bearing.** Emit-before-complete's guarantee — *an emit failure → re-delivery →
re-run leaves no duplicate effect* — holds **only** under P1. If a handler performs a mid-body
external effect `E` before returning, then: emit fails *after* `E` already ran → the task is not
completed → the engine re-delivers → `E` runs **again**; the §4.3 dedup suppresses only the second
audit **row**, never the second **`E`**. A P1 violation silently converts a fail-closed audit into a
**double-effect** hazard.

**P1 holds for all 17 workers today** (every entry fn `del kafka`; `events.py` with `kafka=None`
never publishes — `events.py:47-51`; DMN calls are reads) — **but the code is scaffolded to break
it.** `ans_submit.transmit_to_ans` (`ans_submit.py:187`), documented as a legally-binding ANS
filing, **already** mints a non-deterministic identifier:

```
src/maezo/tools/workers/ans_submit.py:220:
    protocolo_ans = f"ANSPROTO-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16].upper()}"
```

`sha256(time_ns())` re-runs to a **different** `protocolo_ans`, violating the harness's own
same-`task_id`→same-result contract (`harness.py:53`). Today it is a stub (no real ANS network
call), so the "effect" is only a fabricated output variable — but T2.6 is scaffolded to make it a
real filing, at which point emit-before-complete + re-delivery would **double-file**.

**Enforcement (two layers, both R1):**
- **(a) Architecture test.** An AST/import-lint test over the 17 worker modules (mirroring the
  existing `FakeWorkerTransport` import-fence and the `del kafka` convention) asserting entry
  functions (i) do not import/call network clients (`httpx`, Kafka producers) directly, and (ii) do
  not use non-deterministic sources (`time.time_ns`/`time.time`/`random`/`uuid.uuid4`/`secrets`) to
  build any value returned as a persisted business identifier. This makes a P1 violation fail CI.
- **(b) Architectural rule.** Any handler that *genuinely must* perform a mid-handler external
  effect routes that effect through its **own audited + idempotent boundary** (a deterministic
  idempotency key + its own emit — the a2a/driver `PostgresDedupeStore` pattern, ADR-0024), never
  relying on the harness's post-return emit.
- **Named co-requisite (T-H, owned by T2.6).** `transmit_to_ans`'s `sha256(time_ns())` **is** the
  plan's **B12 / T2.6 fabricated-protocol defect** (recent commit `d80fbe1` "T2.6 ANS submission
  re-scope — SIP decommission + mandatory hardening"). It must be fixed to derive `protocolo_ans`
  **deterministically** from stable inputs (business_key + retry_attempt, or an engine-provided
  correlation id — never wall-clock) **before** `transmit_to_ans` becomes a real external effect
  under the audited path. This is a NAMED hard dependency, not a latent trap.

### MUST-FIX 2 — missing-sink fail-OPEN on an L0 invariant

**Resolved.** A `None`/absent sink that silently skips emit is a literal fail-open. The worker daemon
is confirmed Postgres-less (`worker_runtime/settings.py` has no `DATABASE_URL` — only `tenant_id:25`),
and §7 wrongly tiered the readiness gate and the None-sink behavior to R2 *after* the emit tasks.
Corrections:

- **The harness hard-fails-closed on a missing sink.** `audit_sink` becomes a **required**
  `WorkerHarness` constructor argument (not `Optional`) — the daemon **cannot construct** a harness
  without one; a missing sink fails at build time, never silently at emit time. Belt-and-suspenders:
  if the sink is somehow unavailable at dispatch, the emit raises (fail-closed, §4.2) → `complete`
  never runs → incident. **No task is ever completed without a durable audit row.**
- **`audit_sink_ready` readiness gate ELEVATED to R1-authored, fail-closed.** `/readyz` stays **red**
  until a bounded connectivity probe proves the sink's pool can reach the tenant schema (`SELECT 1`
  / confirm `audit_chain` exists). A daemon that cannot durably audit **must not enter** the
  fetch-and-lock rotation — otherwise it fetches+locks tasks it cannot complete, stalling them
  (`build_readiness_checks`, `service.py:171`; the gate slots beside `engine_reachable` /
  `workers_registered`).
- **T-D re-tiered R2 → R1, as a go-live CO-REQUISITE of T-C.** `DATABASE_URL` on
  `WorkerRuntimeSettings` + sink construction in `_bring_up_dependencies` (`service.py:258`, pool
  lazy like the DMN transport at `:278`) + the fail-closed readiness gate + the Helm Aurora-secret
  injection ship **together with** the emit wiring. **No daemon may serve effect-producing traffic
  without a live audit sink.** (T-A is annotated below: *not done-to-live without T-D*.)

### SHOULD-FIX 3 — agents call `start_process_idempotent` directly (a `_handle`-bypassing effect)

**Resolved by re-prioritization + a drift-proof chokepoint fence** (revised again after R1 review 2).
The first revision scoped T-C2 to **five** enumerated `start_process_idempotent` call sites. That
enumeration was already stale by the time it was written: current origin/main has **NINE**, and the
per-agent line numbers *moved between successive `git grep`s* (rafael `342→474`, helena `460→582`) as
more agents landed their start calls. **Enumeration is the wrong unit** — it drifts precisely because
agents keep landing. Scope T-C2 to the **single shared chokepoint** all nine (and every future) site
funnels through:

```
src/maezo/tools/mcp_cibseven/transport.py:360   async def start_process_idempotent(...)
```

**The nine agent call sites today** (point-in-time, verified on origin/main this revision — the fence,
not these numbers, is the durable target):

| Agent | Call site | `process_key` | Note |
|---|---|---|---|
| **andre** | `andre/graph.py:896` | `PROCESS_KEY_PAGTO` = **SP-OP-PAGTO-001** | **MONEY-PATH start — highest severity** |
| rafael | `rafael/graph.py:474` | SP-OP-AUTH-001 | autonomous L2 approval routing |
| helena | `helena/graph.py:582` | (auth) | LLM-decision agent |
| marina | `marina/graph.py:686` | — | LLM-decision agent |
| carolina | `carolina/graph.py:608` | — | LLM-decision agent |
| fernando | `fernando/graph.py:530` | — | LLM-decision agent |
| gustavo | `gustavo/graph.py:679` | SP-OP-ANS-SUBMIT-001 / SP-OP-NIP-001 | LLM-decision agent |
| lucas | `lucas/graph.py:622` | SP-OP-ESCALATION-001 | LLM-decision agent |
| valentina | `valentina/graph.py:630` | SP-OP-PROGRAMA-001 | LLM-decision agent |

**andre's is a money-path process start** (`PROCESS_KEY_PAGTO`, `andre/graph.py:254,898`) — the
highest-severity of the nine and the strongest argument for R1 promotion. Scoping to 5 would have left
these four (andre, gustavo, lucas, valentina) **unaudited**, reintroducing exactly the `_handle`-bypass
SHOULD-FIX 3 exists to close.

**The fence design (structural, not enumerated).** Re-scope T-C2 to make the ADR-0007 provenance tuple
and the audit emit **structural preconditions of `start_process_idempotent` itself**, so an agent
*cannot* start a process without both:

```python
# transport.py:360 — re-scoped signature (T-C2). Provenance + sink become REQUIRED params.
async def start_process_idempotent(
    transport: CibSevenTransport, *, process_key: str, business_key: str,
    variables: dict[str, Any],
    audit_sink: PostgresAuditSink,               # REQUIRED — no start without a live sink (fail-closed)
    provenance: AgentDecisionProvenance,         # REQUIRED — agent_id, agent_version, model_id,
) -> ProcessInstance:                            #            prompt_version, decision_basis (§3.3 PHI-safe)
    key = f"{tenant}:start:{process_key}:{business_key}"
    await audit_sink.emit_once(build_start_record(provenance, ...), idempotency_key=key)  # 1. audit-BEFORE-effect
    existing = await transport.find_active_instance(business_key)                         # 2. existing idempotency
    return existing if existing is not None else await transport.start_process_instance(...)
```

- **Covers all present AND future sites automatically** — a new agent physically cannot call the
  chokepoint without supplying provenance and triggering the emit (a missing-provenance call is a
  type/signature error at author time, not a silent audit gap). This is why the fence beats
  enumeration: the drift that broke the 5-list cannot reintroduce a bypass.
- **Captures the gap the worker chokepoint cannot** — the ADR-0007 tuple's `model_id` /
  `prompt_version` / `decision_basis` (the **LLM decision provenance**), which the deterministic worker
  path (T-C) structurally leaves `None`.
- **Ordering + idempotency align for free.** Emit **before** the start (same fail-closed ordering as
  §4.2); the dedup key `{tenant}:start:{process_key}:{business_key}` matches each SP-OP-* contract's
  "one active instance per business key" invariant, so a re-run dedupes **both** the start effect and
  the audit row. `start_process_idempotent` already returns an active hit unchanged
  (`already_existed=True`, `transport.py:360-380`), so the process-start effect is P1-safe (**no**
  double-effect); the emit dedup closes the double-audit window identically to §4.3.

**Residual-risk note (why T-C2 is R1-additive, not a hard blocker on T-C):** the downstream
**world-effects** (TISS-guide issuance via `operadora.auth.issue_authorization`, payment release, ANS
filing) all run as **workers** → already covered by T-C (rafael "does NOT issue the authorization
itself", `rafael/graph.py`). World-effect coverage is complete via T-C regardless; T-C2 adds the
**decision-provenance** record for the nine autonomous agent starts and fails closed on a missing sink
the same way T-C/T-D do.

### Revised task list & tiers

**R1 — L0 invariants (R1-authored + R1-verified):**

- **T-A · `emit_once` + `0005_audit_emit_dedup` migration** (atomic dedup+chain insert in the
  advisory-lock txn, §4.3). *Annotate:* **not "done-to-live" without T-D** (a live fail-closed sink).
- **T-B · DMN provenance capture** (mutable-container ContextVar collector, §3.2).
- **T-C · Worker-harness emit wiring** — emit-before-complete (`before harness.py:904`), PHI-safe
  `decision_basis` (§3.3), **`audit_sink` required (hard-fail-closed on missing sink)**, **+ P1
  precondition + the architecture test** (MUST-FIX 1a).
- **T-C2 · Agent-graph process-start emission chokepoint** — **9 sites, fenced at the shared
  `start_process_idempotent` chokepoint (`transport.py:360`)**, not enumerated (drift-proof):
  `audit_sink` + `provenance` become required params so no agent can start a process without a
  fail-closed emit. Captures LLM `model_id`/`prompt_version`/`decision_basis` for all present +
  future starts, incl. **andre's money-path SP-OP-PAGTO-001 start** (**promoted from R2**, SHOULD-FIX 3).
- **T-D · Worker-daemon Postgres wiring + fail-closed `audit_sink_ready` gate** — `DATABASE_URL` on
  settings, sink construction in bring-up, readiness gate, Helm secret (**elevated R2→R1, go-live
  co-requisite of T-C**, MUST-FIX 2).

**R1 named co-requisite (owned by T2.6):**

- **T-H · Deterministic `protocolo_ans` derivation** in `transmit_to_ans` (`ans_submit.py:220`,
  B12/T2.6) — must land **before** `transmit_to_ans` becomes a real audited external effect
  (MUST-FIX 1 co-requisite).

**R2 — additive (R2-authored, R1-verified where L0-adjacent):**

- **T-E · Audited-refusal** on the harness `failure`/`bpmnError` paths (`harness.py:916-965`).
- **T-F · A2A delegation audit** — the remaining non-`_handle` effect surface (process-start now
  covered by T-C2).
- **T-G · ADR-0007 signed service-account/cert identity** for `agent_id` (orthogonal, larger).

**Count: 5 R1 + 1 R1 co-requisite (T2.6-owned) + 3 R2 = 9 tasks** (was 7). The two must-fixes moved
T-D into R1 (fail-closed sink is now a go-live gate) and added the P1 architecture test to T-C;
should-fix promoted T-C2 to R1. World-effect coverage: **complete** across worker completions (T-C)
+ agent process-starts (T-C2), with no fail-open on a missing sink (T-D) and no double-effect trap
(P1 + T-H).

---

## R1 Design Review

**Reviewer role:** R1 adversarial design reviewer (did NOT author this doc) · **Date:** 2026-07-18
· **Verified against:** working tree at `bf6aa7a` (origin/main `e8116ba`, `git fetch origin` clean).
All source claims below were independently re-derived from the tree, not taken from the doc.

### VERDICT: **REVISE**

The **core mechanism is sound and the gap analysis is accurate** — `emit_once` (dedup + chain insert
atomic under the existing per-tenant advisory lock), audit-before-complete ordering, the mutable-
container ContextVar provenance bridge, and the PHI discipline are all correct against the real code.
The dedup key is **stable** (contra the pre-review worry). But two L0 defects must be fixed before
implementation, because the design makes a **universal** non-repudiation safety claim ("captures every
effect, once"; "no un-audited effect may EVER be silently accepted") that rests on preconditions it
neither **states** nor **enforces**, and the codebase already contains counter-examples to those
preconditions. For an L0 doc the fix is cheap and the failure mode is an unaudited or double-executed
legally-binding effect — so these are must-fix, not nits.

### Three highest-risk findings

1. **[MUST-FIX · #3] The emit-before-complete ordering rests on an UNSTATED, already-fragile
   "handlers are pure + deterministic; the sole world-effect is `complete`" invariant.** Verified TRUE
   for all 17 registered workers *today* — every `FunctionWorker` entry fn takes `kafka` and immediately
   `del kafka` ("unused — no worker declares a Kafka dependency"); `ans_submit.py:453` states outright
   *"NONE of these entry functions calls `kafka.publish` today"*; `events.py` with `kafka=None` (the only
   wiring that exists — Kafka is unwired) never publishes, it logs and returns `event_published=False`;
   `transmit_to_ans` (the "legally binding ANS filing") is a **stub** that makes no outbound call. So the
   ordering is correct **now**. BUT: (a) the design never names this as a load-bearing invariant; (b) the
   code is scaffolded to break it — the `kafka` seam is threaded through every module, `transmit_to_ans`
   is documented as a real filing "In production delegates to mcp-regdata", and `FunctionWorker` docstring
   (`base.py:239-241`) explicitly offers opt-in in-process retry "for provably-idempotent transient faults
   only"; (c) `transmit_to_ans` **already** mints a non-deterministic `protocolo_ans = sha256(time_ns)`,
   violating the harness's own stated contract (*"handlers MUST be idempotent — same task_id → same
   result"*, `harness.py:53`). The moment any handler performs a mid-handler external side effect
   (`kafka.publish`, a real ANS transmit, an MCP call), engine re-delivery after an `emit_once` failure
   re-runs that effect **twice**, and the dedup table prevents only a second audit *row*, not the second
   *effect* — the exact ADR-0007 violation the design exists to prevent. **Fix:** state the invariant
   explicitly in §4; forbid mid-handler external effects under this ordering unless the effect is itself
   idempotent+keyed on `task_id` **or** moved to a post-audit position; add a fail-closed gate/contract
   (mirroring the bpmn-error allowlist gate) so a future effecting-handler cannot silently regress the
   guarantee.

2. **[MUST-FIX · #7 + fail-closed] Missing-sink fail-OPEN hole, and the fail-closed readiness gate is
   mis-tiered.** Confirmed: `grep -rn DATABASE_URL src/maezo/runtime/worker_runtime/` → **zero hits**; the
   worker daemon is Postgres-less, so T-A/B/C cannot persist anything live without T-D. The design tiers
   T-D **R2-authored** and lists it *after* the R1 tasks — but T-D contains the `audit_sink_ready`
   readiness check, which is the **only** thing stopping an un-auditable daemon from going `/readyz`
   green and accepting work it cannot audit. That gate is an L0 invariant and must be **R1-authored**, not
   merely R1-verified. Worse, the design does not state what T-C's harness seam does when `audit_sink is
   None` (unwired/misconfigured): if it silently skips the emit and completes, every completion is
   **un-audited** — a literal fail-open. **Fix:** (a) specify that a missing/None sink is a hard fail-closed
   refusal (no emit path may be optional); (b) elevate the `audit_sink_ready` readiness gate and the
   missing-sink behavior to R1; (c) make T-D a **go-live co-requisite** of T-C, not a downstream follow-on
   (T-C must not deploy to a daemon without T-D).

3. **[SHOULD-FIX · #2] R1-only leaves the highest-autonomy decisions unaudited longest.** The chokepoint
   is honestly scoped to worker completions, but agents (`helena`, `rafael`, `marina`) perform a real,
   `_handle`-bypassing world-effect — `start_process_idempotent` (e.g. `rafael.graph.py:337-349`, the
   medico-auditor **auto_approve → start_process** leg). Under R1 that autonomous approve-and-start
   decision — arguably *more* L0-sensitive than a deterministic BPMN step — is **unaudited** until T-F (R2).
   The doc's "real effect happens later at a worker completion" is only partly true: the downstream worker
   steps get audited, but the agent's own auto-approval decision does not. **Fix:** acknowledge this risk
   ordering explicitly and either justify it or re-prioritize T-F ahead of the additive R2 items.

### Per-point findings

- **#1 GAP CLAIM — CONFIRMED, exactly as stated.** `grep -rn "\.emit(" src/` → one hit, the docstring
  line `audit_postgres.py:210`; no production caller. `grep -rn "AuditRecord(" src/` → one hit,
  `audit_postgres.py:317` (`_row_to_record`, the read/verify decode path). The durable chain is inert; the
  gap is neither overstated nor understated.

- **#2 EMISSION POINT — sound, with the scope caveat in finding 3.** (a) Bypass paths that go **unaudited
  under R1**: agent `start_process_idempotent` (helena/rafael/marina) and A2A delegation — the doc lists
  these as deferred R2 (T-F) honestly, but see finding 3 on prioritization. (b) No over-audit: one
  `complete` per `_handle` success path (`harness.py:903-904`), and a multi-step BPMN process producing
  several audit rows (one per DMN/worker decision) is **intended granularity** (each is a distinct decision
  with its own `input_hash`), not double-counting. (c) PEP rejection **CONFIRMED**: `PEP.evaluate` has
  **zero** runtime callers project-wide (`grep "\.evaluate(" src/maezo` finds none outside DMN); only
  `build_pep()` is called at boot as a readiness probe (`agent_runtime/service.py:231`). Corollary the doc
  should state: because PEP is entirely unwired at runtime, **no ALLOW/DENY policy decision is captured in
  the audited tuple** — the audited decision is the DMN/worker routing, not a PEP verdict. That is
  self-consistent, but the chain will not attest a policy decision until PEP is wired (separate unbuilt
  task). Fine to defer; worth naming.

- **#3 FAIL-CLOSED ORDERING — see finding 1.** The ordering itself is correct: `AuditPersistenceError`
  subclasses `RuntimeError` (`audit_postgres.py:77`), so an emit failure lands in `_handle`'s
  `except Exception` transient branch (`harness.py:955-965`, `_transient_types` includes `RuntimeError`)
  → `complete` never runs → engine re-delivers. The forbidden direction (effect without audit row) is
  structurally impossible for effects that *are* the completion. The defect is the unstated purity
  precondition for effects that happen *mid-handler before* emit.

- **#4 EXACTLY-ONCE / DEDUP — key is STABLE; claim CONFIRMED (this was the pre-review's top worry and it
  holds).** `task.task_id` is populated from the CIB Seven external-task JSON `"id"` (`harness.py:346,393`,
  `task_id=item["id"]`), i.e. the ExternalTask **entity** id — stable across lock-expiry re-fetch and
  failure-with-retries (same entity re-locked, not a fresh lock id per `fetchAndLock`). A BPMN loop that
  re-reaches the activity mints a *new* external-task id, which is a genuinely new effect and *should* be
  audited separately — so the dedup neither under- nor over-dedups. `emit_once` correctly nests the
  `ON CONFLICT DO NOTHING` dedup insert inside `emit`'s existing advisory-lock transaction
  (`audit_postgres.py:217-241`), making claim+chain-write atomic. **One caveat to add (shares finding 1's
  root cause):** if a handler is non-deterministic, re-delivery returns `ALREADY_AUDITED` for the *first*
  output while `complete` runs with a possibly-*different* second output — audit/effect misalignment.
  Under the stated purity+determinism invariant this vanishes; without it, it is a real hole.

- **#5 dmn_versions ContextVar dataflow — CORRECT and race-free.** `asyncio.to_thread` runs the sync
  handler under `copy_context()`, which copies the var→dict *binding* but shares the dict *object*, so
  in-thread `collector[key]=…` mutations are visible to `_handle` after `await handler(task)`. Concurrent
  tasks do **not** race: `_spawn` runs each `_handle` as its own `asyncio.create_task` (`harness.py:817`),
  so each gets an isolated context copy and its own collector; multiple DMN evals within one handler run
  sequentially in the single `to_thread` call. Minor note: the collector is **fail-open** (`default=None`
  → `_record_dmn_version` no-ops when unset, and a DMN eval that bypasses `evaluate_sync` yields empty
  `dmn_versions`). That is acceptable for provenance *enrichment*, but the doc elevates "non-null
  dmn_versions" to an acceptance criterion while the mechanism cannot *guarantee* it — call this out as
  best-effort enrichment, distinct from the fail-closed audit row itself.

- **#6 PHI — CONFIRMED safe by construction.** `DmnVersion.to_audit_dict()` returns only
  `{"version": int, "id": str, "deploymentId": str}` (`dmn_transport.py:104-106`) — class tokens, never
  clinical values. `decision_basis` = curated enum/routing tokens + `input_sha256 = hash_input(...)`
  (`audit.py:51`), raw `task.variables` hashed and never stored; `canonicalize_jsonb` in
  `AuditRecord.__post_init__` (`audit.py:192-214`) keeps write-hash == verify-hash. **Contingent on** the
  §3.3 per-worker allowlist being implemented as an explicit allowlist, never a passthrough — this is the
  live risk in T-C and must be R1-verified with a "raw vars absent" assertion (the doc already specifies
  the test; keep it a hard gate).

- **#7 INCIDENTAL GAP (DATABASE_URL) — CONFIRMED; re-tier per finding 2.** The gap is real and correctly
  identified; the sequencing/authorship is the defect.

- **#8 TASK BREAKDOWN — tiers mostly right; two corrections.** T-A/T-B/T-C correctly R1 (chain-insert,
  provenance, fail-closed ordering). Corrections: (i) T-D's `audit_sink_ready` readiness gate + the
  missing-sink fail-closed behavior are L0 and belong in R1-authored scope, not R2 (finding 2); (ii)
  consider pulling T-F (agent-decision audit) forward given the autonomy it covers (finding 3). Line-ref
  hygiene: the doc's citations are ~81 lines stale vs the reviewed tree (e.g. it cites the `complete` call
  at `harness.py:823`; it is at `:904`), because the doc was pinned to `0b5e1b9`. All **semantic** claims
  verified true, but the line-pinned references must be **re-pinned to the implementation base** before
  they are used as edit targets in an L0 change.

### Recommended first task

Build order: **T-A (`emit_once` + `0005_audit_emit_dedup` migration)** first — it is self-contained,
unit- and kill-testable with fakes, and every other task depends on the atomic dedup+chain primitive.
**But T-A must not be considered "done to live" without T-D's fail-closed sink wiring landing as a
co-requisite** (finding 2): the go-live gate is "a worker daemon with a working, fail-closed audit sink
that refuses `/readyz` when the sink is unreachable." Fold the missing-sink fail-closed behavior and the
`audit_sink_ready` gate into the R1-authored scope before any handler emit path is deployed.

---

## R1 Re-review (revision confirmation)

**Focused re-check of the "## Revision (R1 review response)" section against current origin/main
(`e8116ba`). Not a re-review of the whole design.**

### VERDICT: STILL-REVISE (one narrow, mechanical fix)

Both **MUST-FIX** items are genuinely resolved. **SHOULD-FIX 3's mechanism is right but its
enumeration is wrong**, and for an L0 completeness argument the wrong count is a real (bounded) gap.

- **MUST-FIX 1 — RESOLVED.** P1 (handler purity) is stated as an explicit, load-bearing precondition
  with a correct "why" (a mid-body effect → emit-fail → re-delivery → double-effect that the dedup
  cannot suppress). The enforcement is a real mechanism, not hand-wave: an R1 AST/import-lint arch
  test over the 17 worker modules fencing direct network clients and non-deterministic id sources
  (`time_ns`/`time`/`random`/`uuid4`/`secrets`), mirroring the existing `del kafka`/import-fence
  convention. T-H is a NAMED R1 co-requisite (T2.6-owned) requiring deterministic `protocolo_ans`
  before `transmit_to_ans` becomes a real audited effect. Verified on main: `ans_submit.py:220` is
  literally `protocolo_ans = f"ANSPROTO-{hashlib.sha256(str(time.time_ns())...)}"`. *(Impl note, not
  blocking: the arch test's simplest robust form is a blunt import/call fence, not value-flow taint
  analysis — the design's intent is satisfied either way.)*

- **MUST-FIX 2 — RESOLVED.** No path remains for a daemon to enter fetch-and-lock rotation without a
  live audit sink: `audit_sink` is now a **required** `WorkerHarness` constructor arg (missing sink →
  fails at build, not silently at emit), belt-and-suspenders emit-raises-before-complete at dispatch,
  and `audit_sink_ready` is elevated to an **R1 fail-closed** readiness gate (`/readyz` red until a
  bounded `SELECT 1`/`audit_chain`-exists probe passes, gating bring-up). T-D (DATABASE_URL + sink
  construction + gate + Helm secret) is re-tiered R2→R1 as a **go-live co-requisite of T-C**. Airtight
  at the design level.

- **SHOULD-FIX 3 — NOT FULLY RESOLVED (enumeration undercount).** Promoting the agent process-start
  chokepoint to R1 (T-C2, reusing `emit_once`, capturing the `model_id`/`prompt_version`/
  `decision_basis` the worker path leaves null) is the correct fix, and the residual (start is
  business-key-idempotent → no double-effect; downstream world-effects are worker-covered by T-C) is
  accurate. **But the doc scopes T-C2 to "the five" `start_process_idempotent` sites
  (rafael/helena/marina/carolina/fernando); current main has NINE** direct call sites — additionally
  **andre:896** (starts `PROCESS_KEY_PAGTO`, a money-path process), **gustavo:679** (LLM agent,
  assess→start_process), **lucas:622** (LLM classify → escalation start_process), and **valentina:630**
  (`PROCESS_KEY_PROGRAMA`). Scoped to 5, T-C2 leaves 4 agent decision surfaces — including a money-path
  start and two LLM decisions — **unaudited**, reintroducing exactly the bypass gap SHOULD-FIX 3
  exists to close. **Required fix (mechanical):** scope T-C2 to **all nine** call sites, or — cleaner —
  fence the single shared chokepoint `start_process_idempotent` (`transport.py:360`, through which all
  nine funnel) so an agent structurally cannot start a process without supplying decision provenance +
  emitting. Correct the count from 5→9 in §"SHOULD-FIX 3" and the T-C2 task line.

### Path to CONFIRM

Re-scope T-C2 to every `start_process_idempotent` site (enforced at the `transport.py:360`
chokepoint). That is the only unresolved item; nothing else in the revision needs changing. An R1
implementer still starts with **T-A** (`emit_once` + `0005_audit_emit_dedup` migration) — self-
contained and kill-testable — landing **with T-D** as its go-live co-requisite.

---

## EB-4 addendum — un-stubbing the CONTAS/FRAUDE handoff workers + bridge event_type reconciliation

**Date:** 2026-07-25 · **Area:** Cross-process choreography (CONTAS→RECURSO→FRAUDE) · **Branch:**
`t2.6-eb4-bridge-live` (off `t2.6-eb3-bridge-wiring`). Landed live-proven against a lean
postgres+cibseven stack (engine :18195, PG :5650, `-Xmx900m`).

### Context — the handoff was dead on BOTH paths

Two mechanisms could start a downstream process from a CONTAS/FRAUDE handoff:

1. **Dedicated in-flow BPMN service-task workers** — the CONTAS handoff of the day
   (`operadora.contas.start_recurso` / `ST_StartRecurso`, **deleted by ADR-0040**: the operadora
   does not appeal its own glosa; the CONTAS handoff is now `operadora.contas.handoff_pagamento`
   → SP-OP-PAGTO-001), `operadora.fraude.start_credenciamento` (`ST_StartCredenciamento`),
   `operadora.fraude.start_contratual` (`ST_StartContratual`). They run *synchronously inside the
   source process* with the full process-variable context. They were **STUBS** (log + a marker
   dict; NO `start_process` call).
2. **`NotificationBridge`** (Kafka choreography, `platform/notification_bridge.py`) — its 5
   pre-existing rules keyed off event_type strings (`contas.glosa_confirmed`,
   `contas.encaminhar_fraude`, `fraude.acusacao_registrada`) that **no publisher ever emits**
   (6 of 7 rules dormant; only `ans.cron_due` fires).

The events the source processes ACTUALLY emit (BPMN `operadora.events.publish` `event_topic`
inputParameter) are `agents.events.contas.completed` / `agents.events.fraude.completed`, each
carrying `payload.desfecho` (the routing outcome).

### Decision 1 — un-stub the 3 workers as the CANONICAL handoff (the live path)

Root-cause-correct per the **already-merged precedent** `inadimplencia.handoff_rescisao` (T1.10
T-C2 "10th start site"): the dedicated in-flow worker is the sanctioned way to start a downstream
process from a handoff, because it runs with full variable context and can derive the CORRECT,
contract-shaped business key. Each of the 3 workers now runs `start_process_idempotent` through the
**fenced chokepoint** (`transport.py`, this doc's T-C2) with a required `engine` + `audit_sink`
seam (threaded by `register_contas_workers` / `register_fraude_workers`, exactly as
`register_inadimplencia_workers` does), emitting the ADR-0007 start record **before** any engine
effect. Fail-closed: a missing seam or a missing business-key anchor RAISES (never a silent no-op,
never an un-audited start). Business keys are IDENTICAL to the bridge's (`RECURSO-…`, `CRED-…`,
`CANCEL-…`, `INAD-…`), so redelivery is an idempotent hit, never a divergent double-start.

### Decision 2 — bridge event_type reconciliation = OPTION (b), repoint + fail-closed anchor

The 5 dormant rules are repointed onto the REAL emitted event_type names
(`agents.events.{contas,fraude}.completed`) keyed on `payload.desfecho`
(`encaminhada_recurso` / `encaminhada_fraude` / `encaminhado_credenciamento` /
`encaminhado_contratual`). Each repointed predicate ALSO requires its business-key anchor
(`numero_guia_tiss`+`glosa_id` / `prestador_id` / `numero_contrato`) to be non-blank.

**Why the anchor requirement (no divergent-key hazard).** The real completed events carry an
intentionally MINIMAL, PHI-safe `event_payload_vars` payload that does NOT include those anchors
(or the `entidade_tipo` discriminator). Against today's payloads the anchored predicate is
**correctly DORMANT** (no start under a wrong/partial key); the rule ARMS automatically iff/when
the source BPMN enriches `event_payload_vars` with the anchor (a named `spec/` follow-up, OUT of
this task's mechanical scope). The bridge remains the decoupled Kafka mirror of the in-flow worker;
because both derive the SAME business key, a live bridge fire converges idempotently on the same
instance. Option (a) — making the source emit the bridge-consumed strings with richer payloads —
was rejected: it duplicates the now-live worker AND would push resolvable identifiers onto a Kafka
topic (a PHI-egress concern), for no additional coverage.

**Flagged residuals (honest, not force-wired):** (i) `CONTAS→FRAUDE` is Phase-3-deferred — the
current CONTAS BPMN emits no `encaminhada_fraude` desfecho and has no in-flow `start_fraude`
worker; the rule is armed but stays dormant. (ii) The bridge's live-Kafka publish/consume leg (a
publisher writing the completed event, with anchors, to `operadora.notifications.internal`) remains
the documented gap the `notifications_bridge` module already records.

### Live proof (mandatory R1)

For each of the 3 workers AND the reconciled bridge, proven against a REAL engine + REAL
`audit_chain`: source event → handoff fires → downstream instance STARTED (queried by business key
via engine REST) → ADR-0007 start row SELECTed from `audit_chain`; redelivery idempotent (same
instance, one chain link); non-matching desfecho starts nothing. Captured by
`tests/integration/platform/test_notifications_bridge_live_engine.py` (bridge + worker) and the
3 full-BPMN handoff tests in `tests/integration/processes/test_sp_op_{contas,fraude}_001.py`
(now wiring the `engine=`/`audit_sink=` seams + deploying the downstream BPMN, mirroring the
inadimplencia→CANCEL handoff test).
