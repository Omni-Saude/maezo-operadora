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
