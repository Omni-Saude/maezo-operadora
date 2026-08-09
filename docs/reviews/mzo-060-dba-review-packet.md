# MZO-060 — DBA review packet: the AMH durable inbox (`amh_inbox`, migration `0007`)

**Status:** awaiting DBA review. Nothing in this packet is an approval, and nothing in this
repository has been approved by anyone on the strength of it. The ratification fields at the end
are PENDING placeholders; only a DBA may fill them in.

**What is being asked of the reviewer:** approve (or reject, or amend) the schema of one new
table and three indexes, plus the operational procedure for applying and rolling them back. That
is the whole ask. No consumer, no data flow and no runtime behaviour is being switched on by this
review — see "The two switches" below.

| Item | Reference |
|---|---|
| Work package | MZO-060 (ADR-0037 XRD-10) |
| Migration | `src/maezo/platform/migrations/versions/0007_amh_inbox.py` (`down_revision = "0006"`) |
| Repository | `src/maezo/platform/integrations/amh_inbox.py` |
| Ratification artifact | `spec/policies/amh/inbox-ratification.yaml` (DRAFT) |
| Tests | `tests/unit/platform/test_migration_0007_amh_inbox.py`, `tests/unit/platform/integrations/test_amh_inbox.py`, `tests/unit/platform/integrations/test_amh_inbox_live_pg.py` |
| Gating ADR clauses | ADR-0037 XRD-10 (inbox + dedup key), XRD-04 (contract ownership), XRD-05 (identity, DPO-gated), immutable prohibitions #4 and #5 |

---

## 1. Why this table exists

`maezo.ports.work_items.WorkItemSource.ack` may return success ONLY when settlement is DURABLE.
ADR-0037 XRD-10 states outright that *"offsets Kafka nunca representam conclusao de negocio"* — so
committing a Kafka offset is not an acknowledgement, and the only artefact that can be one is a
committed row in the payer's own database.

This is not a theoretical gap. It is the recorded reason the AMH adapter (MZO-050a) shipped
**without** a consumer: `maezo/adapters/amh/__init__.py` names four blockers, and blocker #1 is
this table's absence. Until it exists, any `AmhEventConsumer` whose `ack` returned
`PortResult.ok(None)` would be a seam lying about its own result — the DL-0038 defect this
repository has already paid for once, relocated from egress to intake.

## 2. The two switches — what this review does and does not activate

The inbox is dark, and stays dark, behind **two independent gates**. Both must be thrown, and this
review only concerns the first.

| Switch | Thrown by | State today |
|---|---|---|
| **A. Schema exists** | applying migration `0007` in an environment | not applied anywhere |
| **B. Repository may operate** | a DBA editing `spec/policies/amh/inbox-ratification.yaml` | `ratificado: false`, `dba_review: PENDING` |

Applying the migration on its own activates nothing, and it is worth being exact about WHY, because
an earlier draft of this packet overstated it.

* **`spec/policies/amh/inbox-ratification.yaml` is the switch.** It ships as a DRAFT with every
  human field an explicit placeholder.
* **`build_amh_inbox_repository` is the digest-binding path.** It calls `load_inbox_ratification`
  FIRST and lets the refusal propagate, so a caller that checks nothing still gets nothing out of
  an unratified deployment. That loader is the only place an approval is bound to a FILE and to the
  exact bytes of migration `0007`.
* **`InboxRatification` is a capability, not an oracle.** It refuses to exist unless `ratificado`
  is the boolean `true` and `dba_review` is `APPROVED` — but it is a dataclass, so a caller CAN
  build one by hand without reading any artifact. Its `__post_init__` checks those two human
  switches and nothing else.
* **`PostgresAmhInbox.__init__` is the second lock, and it re-checks four properties**, not two:
  the two switches (which catches an instance forged past `__post_init__` via `object.__setattr__`)
  plus `migration_revision` and `migration_sha256`, the latter re-derived from the migration file
  on every construction. This closes the hole the bullet above opens: until this re-check existed,
  `InboxRatification(migration_revision="0001", migration_sha256="deadbeef", …)` produced a
  repository that recorded and SETTLED rows against a live PostgreSQL 16.14 — an `ack` licensed by
  an "approval" covering DDL nobody had reviewed. Pinned by
  `test_amh_inbox_live_pg.py::test_a_hand_built_ratification_cannot_settle_rows_on_a_real_server`.

The guarantee the reviewer can rely on is therefore: **no repository can operate against migration
`0007` unless a DBA edited the artifact AND the DDL on disk still hashes to what they approved** —
and every route to a repository, sanctioned or hand-rolled, is checked against the second half.

Separately, **nothing consumes the inbox at all**: MZO-050b (the consumer) is a separately gated
work package, `maezo.adapters.amh` still ships no `consumer.py`, and no composition root —
`worker_runtime` included — imports the repository module.

---

## 3. The DDL, as executed

### 3.1 What migration `0007` emits

```sql
CREATE TABLE IF NOT EXISTS amh_inbox (
    -- Dedup key, verbatim from ADR-0037 XRD-10.
    contract_manifest_digest text NOT NULL,
    event_id                 text NOT NULL,

    -- Tenant / contract scope.
    amh_tenant               text NOT NULL,
    legal_entity             text NOT NULL,

    -- Which port this row settles (payer-core vocabulary, not a wire value).
    inbox_stream             text NOT NULL,

    event_type               text NOT NULL,
    canonical_schema_version text NOT NULL,
    idempotency_key          text NOT NULL,

    -- Provenance that carries no record identity.
    source_product           text NOT NULL,
    source_entity            text NOT NULL,
    source_position_kind     text NOT NULL,
    source_position_value    text NOT NULL,

    -- Integrity + replay accounting.
    payload_hash             text NOT NULL,
    replay_count             integer NOT NULL DEFAULT 0,
    redelivery_count         integer NOT NULL DEFAULT 1,

    occurred_at              timestamptz NOT NULL,
    ingested_at              timestamptz NOT NULL,

    -- Lifecycle.
    status                   text NOT NULL DEFAULT 'RECEIVED',
    received_at              timestamptz NOT NULL DEFAULT now(),
    last_seen_at             timestamptz NOT NULL DEFAULT now(),
    processed_at             timestamptz,
    settled_at               timestamptz,
    quarantined_at           timestamptz,
    quarantine_reason        text,
    quarantine_topic         text,

    -- Observability joins (opaque references the payer core never parses).
    correlation_id           text NOT NULL,
    trace_id                 text NOT NULL,

    CONSTRAINT pk_amh_inbox
        PRIMARY KEY (contract_manifest_digest, event_id),
    CONSTRAINT ck_amh_inbox_status
        CHECK (status IN ('RECEIVED', 'PROCESSED', 'SETTLED', 'QUARANTINED')),
    CONSTRAINT ck_amh_inbox_stream
        CHECK (inbox_stream IN ('work_item', 'consent')),
    CONSTRAINT ck_amh_inbox_settled_at
        CHECK ((status = 'SETTLED') = (settled_at IS NOT NULL)),
    CONSTRAINT ck_amh_inbox_processed_at
        CHECK (status <> 'PROCESSED' OR processed_at IS NOT NULL),
    CONSTRAINT ck_amh_inbox_quarantined_at
        CHECK ((status = 'QUARANTINED') = (quarantined_at IS NOT NULL)),
    CONSTRAINT ck_amh_inbox_quarantine_reason
        CHECK ((status = 'QUARANTINED') = (quarantine_reason IS NOT NULL)),
    CONSTRAINT ck_amh_inbox_quarantine_topic
        CHECK (status = 'QUARANTINED' OR quarantine_topic IS NULL),
    CONSTRAINT ck_amh_inbox_quarantine_reason_vocabulary
        CHECK (quarantine_reason IS NULL OR quarantine_reason IN (
            'consent_required', 'purpose_denied', 'scope_not_supported',
            'below_committed_k', 'individual_dimension', 'invalid_request',
            'not_found', 'not_authenticated', 'rate_limited',
            'timeout', 'upstream_unavailable', 'contract_violation'
        )),
    CONSTRAINT ck_amh_inbox_replay_count
        CHECK (replay_count >= 0),
    CONSTRAINT ck_amh_inbox_redelivery_count
        CHECK (redelivery_count >= 1)
);

CREATE INDEX IF NOT EXISTS ix_amh_inbox_pending
    ON amh_inbox (amh_tenant, received_at)
    WHERE status IN ('RECEIVED', 'PROCESSED');

CREATE INDEX IF NOT EXISTS ix_amh_inbox_idempotency
    ON amh_inbox (amh_tenant, idempotency_key);

CREATE INDEX IF NOT EXISTS ix_amh_inbox_quarantined
    ON amh_inbox (amh_tenant, quarantined_at DESC)
    WHERE status = 'QUARANTINED';
```

DDL is **unqualified** and idempotent (`IF NOT EXISTS`), matching every migration in this chain:
the platform is schema-per-tenant and `platform/migrations/env.py` sets `search_path` per
connection (DL-0017). Apply per tenant with `alembic -x tenant=<schema> upgrade head`.

### 3.2 What Postgres materialised (verbatim `pg_dump --schema-only`, PostgreSQL 16.14)

```sql
CREATE TABLE public.amh_inbox (
    contract_manifest_digest text NOT NULL,
    event_id text NOT NULL,
    amh_tenant text NOT NULL,
    legal_entity text NOT NULL,
    inbox_stream text NOT NULL,
    event_type text NOT NULL,
    canonical_schema_version text NOT NULL,
    idempotency_key text NOT NULL,
    source_product text NOT NULL,
    source_entity text NOT NULL,
    source_position_kind text NOT NULL,
    source_position_value text NOT NULL,
    payload_hash text NOT NULL,
    replay_count integer DEFAULT 0 NOT NULL,
    redelivery_count integer DEFAULT 1 NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    ingested_at timestamp with time zone NOT NULL,
    status text DEFAULT 'RECEIVED'::text NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    settled_at timestamp with time zone,
    quarantined_at timestamp with time zone,
    quarantine_reason text,
    quarantine_topic text,
    correlation_id text NOT NULL,
    trace_id text NOT NULL,
    CONSTRAINT ck_amh_inbox_processed_at CHECK (((status <> 'PROCESSED'::text) OR (processed_at IS NOT NULL))),
    CONSTRAINT ck_amh_inbox_quarantine_reason CHECK (((status = 'QUARANTINED'::text) = (quarantine_reason IS NOT NULL))),
    CONSTRAINT ck_amh_inbox_quarantine_reason_vocabulary CHECK (((quarantine_reason IS NULL) OR (quarantine_reason = ANY (ARRAY['consent_required'::text, 'purpose_denied'::text, 'scope_not_supported'::text, 'below_committed_k'::text, 'individual_dimension'::text, 'invalid_request'::text, 'not_found'::text, 'not_authenticated'::text, 'rate_limited'::text, 'timeout'::text, 'upstream_unavailable'::text, 'contract_violation'::text])))),
    CONSTRAINT ck_amh_inbox_quarantine_topic CHECK (((status = 'QUARANTINED'::text) OR (quarantine_topic IS NULL))),
    CONSTRAINT ck_amh_inbox_quarantined_at CHECK (((status = 'QUARANTINED'::text) = (quarantined_at IS NOT NULL))),
    CONSTRAINT ck_amh_inbox_redelivery_count CHECK ((redelivery_count >= 1)),
    CONSTRAINT ck_amh_inbox_replay_count CHECK ((replay_count >= 0)),
    CONSTRAINT ck_amh_inbox_settled_at CHECK (((status = 'SETTLED'::text) = (settled_at IS NOT NULL))),
    CONSTRAINT ck_amh_inbox_status CHECK ((status = ANY (ARRAY['RECEIVED'::text, 'PROCESSED'::text, 'SETTLED'::text, 'QUARANTINED'::text]))),
    CONSTRAINT ck_amh_inbox_stream CHECK ((inbox_stream = ANY (ARRAY['work_item'::text, 'consent'::text])))
);

ALTER TABLE ONLY public.amh_inbox
    ADD CONSTRAINT pk_amh_inbox PRIMARY KEY (contract_manifest_digest, event_id);

CREATE INDEX ix_amh_inbox_idempotency ON public.amh_inbox USING btree (amh_tenant, idempotency_key);
CREATE INDEX ix_amh_inbox_pending ON public.amh_inbox USING btree (amh_tenant, received_at) WHERE (status = ANY (ARRAY['RECEIVED'::text, 'PROCESSED'::text]));
CREATE INDEX ix_amh_inbox_quarantined ON public.amh_inbox USING btree (amh_tenant, quarantined_at DESC) WHERE (status = 'QUARANTINED'::text);
```

### 3.3 Key decisions embedded in the DDL

**The dedup key is `(contract_manifest_digest, event_id)`, verbatim from XRD-10.** Two
consequences the reviewer should agree with explicitly:

* `contract_manifest_digest` is IN the key, so the same `event_id` republished under a NEW manifest
  is a DIFFERENT row. That is intended: ADR-0037 mandates dual-publish/dual-read windows of at
  least 30 days for every contract major, and collapsing those onto one row would make the second
  manifest's delivery silently invisible.
* `amh_tenant` is NOT in the key. Adding it would WEAKEN dedup — a replay of one `event_id` stamped
  with a different tenant would become an accepted second row instead of a detected collision. It
  is a `NOT NULL` column, and the repository reports a dedup-key hit whose stored `amh_tenant`
  differs as a `CONFLICT`, not a duplicate.

**`idempotency_key` is indexed but NOT unique.** The pinned contract
(`config/integrations/amh/contracts.lock.json`) declares the field but declares **no uniqueness
scope** for it. A `UNIQUE` constraint would be this repository inventing contract semantics the
owner has not published, which XRD-04 reserves to the AMH. See open decision **D-1**.

**Terminal-state CHECKs are biconditional** (`(status = 'SETTLED') = (settled_at IS NOT NULL)`), not
one-directional. A one-directional check permits a row that carries a settlement timestamp while
sitting in `RECEIVED` — a row an operator reads as settled and the repository reads as pending.

**`quarantine_reason` is CHECK-constrained to a closed 12-value vocabulary** — the exact value set
of `maezo.ports.errors.PortFailureReason`. This is defence in depth against a writer that is not
this repository. A unit test asserts the DDL's checked set EQUALS the enum, so the two cannot
drift silently in either direction.

**Lifecycle monotonicity is enforced by conditional `UPDATE` predicates, not by a trigger.** A
trigger would put business rules where no unit test in this repository can reach them. Every
transition is `UPDATE ... WHERE ... AND status IN (<non-terminal>) RETURNING status`; a zero-row
result is then disambiguated by reading the current status, so the caller learns
`ALREADY` / `REFUSED_TERMINAL` / `NOT_FOUND` rather than an ambiguous "no rows".

---

## 4. Index plan and rationale

The write path is INSERT-heavy (one row per delivered event) and the read paths are few and
known. Every index below is charged to every INSERT, so each one has to earn its place.

| Index | Serves | Shape | Why this shape |
|---|---|---|---|
| `pk_amh_inbox` (`contract_manifest_digest`, `event_id`) | the dedup probe on every single delivery — `INSERT ... ON CONFLICT`, `SELECT ... FOR UPDATE`, every lifecycle transition | unique btree, mandatory | It IS the dedup key (XRD-10). Every statement the repository issues is keyed on it, so it is both the constraint and the only access path the hot path needs. |
| `ix_amh_inbox_pending` (`amh_tenant`, `received_at`) `WHERE status IN ('RECEIVED','PROCESSED')` | the consumer's "what have I received but not settled?" scan | **partial** btree | An inbox grows monotonically and is never pruned by its own logic. A FULL index on this predicate would retain every settled row forever in the index the scan reads; partial keeps it proportional to IN-FLIGHT work. Rows leave the index on reaching a terminal state. Verified by `EXPLAIN`: `Index Scan using ix_amh_inbox_pending`. |
| `ix_amh_inbox_idempotency` (`amh_tenant`, `idempotency_key`) | operational lookup by the envelope's own idempotency key (incident triage: "did we ever see this key?") | full btree, **non-unique** | Not on the hot path, but it is the question an on-call engineer asks first when the AMH says it published something the payer never acted on. Non-unique for the XRD-04 reason above. Candidate for removal if D-1 resolves to a UNIQUE constraint, which would subsume it. |
| `ix_amh_inbox_quarantined` (`amh_tenant`, `quarantined_at DESC`) `WHERE status = 'QUARANTINED'` | quarantine triage / referral reconciliation against the AMH quarantine topics | **partial** btree | Quarantined rows are the rare tail. A full index would charge every ordinary INSERT for a query that only ever reads the exceptions. |

**Deliberately NOT created (see D-2):** there is no index on `settled_at` or on `received_at`
alone. Retention/pruning of terminal rows is work package MZO-130, which is human-gated on
retention/legal-hold approval; building an index sized for a policy nobody has ratified would be
speculative. The reviewer may prefer to add it now to avoid a later `CREATE INDEX CONCURRENTLY`
on a large table — that is D-2, and it is the reviewer's call, not the author's.

**Partial-index write caveat, stated rather than hidden.** A status transition changes whether a
row satisfies `ix_amh_inbox_pending`'s predicate, so that `UPDATE` cannot be HOT-optimised and
must maintain the index. That is the price of keeping the pending index proportional to in-flight
work rather than to history, and it is charged once per event (at settlement), not per read.

**Row width.** 18 `text` + 2 `integer` + 7 `timestamptz` columns; the text values are digests
(64 chars), opaque references and closed-vocabulary tokens. No large object, no `jsonb`, no
`bytea`. Expect a narrow, TOAST-free heap row; the table's growth is driven by event volume
alone.

---

## 5. Lock and contention analysis

### 5.1 The migration itself

`0007` is **purely additive**: one `CREATE TABLE` plus three `CREATE INDEX` on that same,
brand-new, empty relation. It issues no `ALTER TABLE`, and it names no pre-existing table anywhere
in its executable DDL (asserted by
`tests/unit/platform/test_migration_0007_amh_inbox.py::test_no_pre_existing_table_is_touched`).

Empirical lock inventory, captured inside the migration's own transaction (`pg_locks` filtered to
the migrating backend):

```
 locktype |              object               |        mode         | granted
----------+-----------------------------------+---------------------+---------
 relation | amh_inbox                         | AccessExclusiveLock | t
 relation | amh_inbox                         | ShareLock           | t
 relation | ix_amh_inbox_*                    | AccessExclusiveLock | t
 relation | pg_toast_<new>                    | AccessExclusiveLock | t
 relation | pg_class (+ its indexes)          | AccessShareLock     | t
```

Every `AccessExclusiveLock` is on a relation **this transaction just created**, so no other
session can be holding or waiting on any of them. The only locks touching pre-existing objects
are `AccessShareLock` on system catalogs. Practical consequence for a live database:

* **No table in the existing schema is locked, at any level.** Application traffic against
  `audit_chain`, `a2a_idempotency`, `custody_bundles` etc. is unaffected.
* **No long-running operation.** There is no data to rewrite, no constraint to validate against
  existing rows and no index to build over existing rows — the table is empty at index-creation
  time, so `CREATE INDEX` (non-concurrent) is the correct and cheapest choice.
* **The one serialisation point is the alembic version row** (`<tenant>_alembic_version`), taken
  `ROW EXCLUSIVE` for the duration. Two concurrent migration runs against the same tenant schema
  serialise there, which is the intended behaviour.
* **Expected duration:** milliseconds per tenant schema. A `lock_timeout` is unnecessary but
  harmless; a `statement_timeout` should not be needed.

### 5.2 Runtime contention, once a consumer exists (MZO-050b, separately gated)

Worth reviewing now because it is a property of THIS schema:

* `record` runs `INSERT ... ON CONFLICT DO NOTHING`, and on conflict a `SELECT ... FOR UPDATE`
  followed by an increment — a **row-level** lock on one dedup key inside one short transaction.
  There is no advisory lock and no table-level lock. Two replicas processing different events
  never contend; two replicas processing the SAME event contend on exactly that one row.
* **`ON CONFLICT DO NOTHING` does not return early on an in-flight conflict — it WAITS.** An
  earlier draft of this packet said the opposite, and the correction matters to a DBA more than the
  original claim did. Postgres implements `ON CONFLICT` with *speculative insertion*: when the
  arbiter index finds a conflicting tuple belonging to a transaction that is still in progress, the
  inserting backend BLOCKS on that transaction's xid until it commits or aborts. Measured on
  PostgreSQL 16.14 with a writer deliberately holding its transaction open for 5s: the second
  `record` call waited ~4.4s and then resolved as `DUPLICATE` (the first writer committed) or
  `RECORDED` (it rolled back). Two replicas racing the same event therefore serialise; they do not
  both fail fast.
* **`InboxRecordOutcome.CONCURRENT` is therefore DEFENSIVE, not the outcome of that race.** At
  READ COMMITTED the follow-up `SELECT` takes a fresh snapshot and necessarily sees the row the
  INSERT just waited for, so the "insert declined AND row invisible" state is not reachable via
  concurrency. Raising the isolation level does not reach it either: at REPEATABLE READ and
  SERIALIZABLE the INSERT itself raises `could not serialize access due to concurrent update`
  (`ExecCheckTupleVisible`) rather than silently declining — verified on the same server. What
  remains reachable is the row being DELETED between the two statements, which no code in this
  repository does. The branch is kept so that a future isolation-level or SQL change fails honestly
  instead of silently, and the docstring now says so rather than claiming a race it does not have.
* **The wait is UNBOUNDED, and that is the operational disclosure in this section.** The asyncpg
  pool is created with `min_size`/`max_size`/`setup` only — **no `command_timeout`** — and nothing
  in this repository issues `SET lock_timeout` or `SET statement_timeout`, so both are the server
  default (verified `'0'` on the repository's own pooled session). The ceiling on a `record` call
  is therefore *another replica's transaction duration*, including the pathological case of a
  replica that inserted and then stalled. Whether to cap it, and at what value, is **decision D-6
  in §8** — deliberately left to the reviewer rather than chosen unilaterally in code, because the
  right number depends on the deployment's connection budget and on how a capped call is retried.
* Lifecycle transitions are single-row `UPDATE`s keyed on the primary key.
* `pending` is a bounded (`LIMIT`) index scan; it takes no locks beyond MVCC snapshot semantics.
* **Quarantine is terminal, so a second `nack` carrying a DIFFERENT reason cannot overwrite the
  first.** That is intended, but it used to be invisible: the call returned `ALREADY` and dropped
  the caller's reason. `mark_quarantined` now returns `InboxQuarantineResult`, which carries the
  reason and topic ACTUALLY stored alongside the outcome (`reason_diverged` is the one-line test),
  and logs the divergence. No extra query is charged for it — the values ride along on the
  `RETURNING` clause and on the status read that already disambiguated the zero-row `UPDATE`.

---

## 6. Rollback procedure

`downgrade()` is one statement, and it drops exactly what `upgrade()` created:

```sql
DROP TABLE IF EXISTS amh_inbox CASCADE;
```

Indexes and constraints belong to the table and are dropped with it. Nothing pre-existing is
touched, and there is no `DROP INDEX` and no `ALTER TABLE` anywhere in the file.

**Procedure (per tenant schema):**

```bash
# 1. Confirm the current head for this tenant.
alembic -x tenant=<schema> current

# 2. Roll back 0007 only.
alembic -x tenant=<schema> downgrade 0006

# 3. Verify. The version table is created UNQUALIFIED under the migration's search_path, so it
#    lands INSIDE the tenant schema and is named "<schema>_alembic_version" there. A psql session
#    has a different search_path, so the query must be schema-qualified (or set the path first).
psql -c "SELECT to_regclass('<schema>.amh_inbox');"                    -- expect NULL
psql -c 'SELECT version_num FROM "<schema>"."<schema>_alembic_version";' -- expect 0006
#    equivalently:
psql -c 'SET search_path TO "<schema>"; SELECT version_num FROM "<schema>_alembic_version";'
```

**Running `alembic` at all.** `alembic.ini` ships `sqlalchemy.url =
postgresql+asyncpg://maezo:maezo@postgres:5432/maezo`; the host `postgres` is the compose service
name and resolves ONLY inside the compose network, so these commands are run from inside the app
container (or with `sqlalchemy.url` overridden for the target database).

**Data-loss statement, plainly.** Rolling back destroys the settlement facts recorded in
`amh_inbox`. Because the row is what makes an `ack` durable, rolling back while a consumer is
running would mean previously-settled events could be re-delivered and re-processed. The correct
order is therefore always: **stop the consumer first, then roll back.** While the inbox is dark
(both switches unthrown, which is today's state) there is nothing to lose — the table is empty and
no code can write to it.

**Reversibility is proven, not asserted.** `upgrade head` → `downgrade 0006` → `upgrade head` was
executed against a real PostgreSQL 16.14 (see §9), and
`tests/unit/platform/integrations/test_amh_inbox_live_pg.py::test_upgrade_downgrade_upgrade_roundtrip`
re-runs that cycle — including asserting that 0006's own tables survive the downgrade — whenever a
Postgres is available.

---

## 7. Erasure and custody coverage (LGPD / ADR-0020 / ADR-0037 #5)

### 7.1 The claim

**An LGPD art. 18 VI erasure request has NO cascade target in this table.** There is no row to
delete, no column to null, and no re-identification path from a settled row back to a data subject
without the AMH's own mapping — which only the AMH holds (XRD-05). Below is the column-by-column
basis for that claim so a reviewer can check it rather than take it.

### 7.2 What is stored, and why each column is not subject-linkable

| Column(s) | Nature | Subject-linkable? |
|---|---|---|
| `contract_manifest_digest`, `payload_hash` | sha256 digests | No — non-reversible; `payload_hash` digests a whole event body per the pin's declared canonicalisation |
| `event_id`, `idempotency_key`, `correlation_id`, `trace_id` | opaque contract/observability references the payer core never parses | No — they identify an EVENT and a trace, not a person |
| `amh_tenant`, `legal_entity` | tenancy of the operator | No |
| `inbox_stream`, `event_type`, `canonical_schema_version`, `source_product` | closed/versioned vocabulary | No |
| `source_entity` | the source system's ENTITY (table/stream) name | No — a schema-object name, not a row identifier |
| `source_position_kind`, `source_position_value` | the source's opaque ordering position | No — a change-sequence marker |
| `replay_count`, `redelivery_count` | counters | No |
| `occurred_at`, `ingested_at`, `received_at`, `last_seen_at`, `processed_at`, `settled_at`, `quarantined_at` | timestamps | No |
| `status`, `quarantine_reason`, `quarantine_topic` | closed lifecycle + refusal vocabulary, pinned topic name | No |

### 7.3 What is deliberately NOT stored

**FIVE envelope fields are excluded by construction, and there is no payload column at all.** Those
are two different facts and an earlier draft ran them together as "six envelope fields".
`maezo.ports.envelope.CanonicalEnvelope` has **28** fields; `payload` is not one of them, so the
event body is not an excluded field — it is data this repository never receives and never has a
column for.

`_stored_row` in the repository is the single function that reads envelope fields. It reads **16**
of the 28 and fills **18 columns**: 17 from those 16 (`source_position` supplies both
`source_position_kind` and `source_position_value`) plus `inbox_stream`, which comes from the
caller's `stream` argument rather than from the envelope. None of the five below is among them:

| Excluded envelope field | Reason |
|---|---|
| `protected_source_record_ref` | raw source-record reference — ADR-0037 immutable prohibition #5, verbatim |
| `portable_subject_ref` | the subject reference itself — XRD-05, DPO/Legal-gated (DL-0040/DL-0042 scope) |
| `amh_mpi_ref` | master-patient-index reference |
| `beneficiary_ref` | beneficiary reference |
| `consent_decision_ref` | names a decision taken FOR ONE SUBJECT, hence subject-linkable |

**No payload bytes are stored, and that is a boundary decision rather than an omission.** The 0004
custody/erasure precedent (`custody_bundles` + `erasure_log`) exists for data this repository IS
the custodian of. The AMH is the lake of record (ADR-0019), and the ADR-0013 principle that
survives into ADR-0037 is consume-not-duplicate: *"o Maezo nunca e producer nem detentor de copia
de registro"*. Persisting the event body here would make this table a second copy of record of
AMH-owned data — the precise thing the boundary exists to prevent. `payload_hash` is kept instead:
non-reversible, not a subject identifier, and the mechanism by which a replay carrying MUTATED
content is DETECTED (`InboxRecordOutcome.CONFLICT`) rather than silently deduplicated.

**Consequence for erasure, stated for the DPO as well as the DBA.** Because the inbox holds no
subject reference, an erasure cascade cannot and need not reach it. Conversely, that is what makes
the dedup fact SAFE to retain after an erasure elsewhere in the platform: if erasure deleted inbox
rows, a previously-settled event could be re-delivered and re-processed, re-materialising the
subject's data the erasure had just removed. Retention of these rows is therefore an
erasure-PROTECTING property, not an erasure-evading one. Retention duration itself remains MZO-130
(human-gated on retention/legal-hold approval) and is out of scope here.

**Quarantine metadata carries no payload.** A quarantine referral records only a closed
`PortFailureReason` token and the pinned quarantine TOPIC NAME — never the payload, never an
upstream error body, never a subject reference (prohibition #5, and `WorkItemSource.nack`'s own
contract).

### 7.4 How this is kept true

* `tests/unit/platform/test_migration_0007_amh_inbox.py` fails if any forbidden identifier appears
  in the emitted DDL, and fails if the expected column set shrinks (non-vacuity).
* `tests/unit/platform/integrations/test_amh_inbox.py` plants a sentinel in all five excluded
  envelope fields and asserts it reaches no bind parameter — and separately asserts that
  `CanonicalEnvelope` still has no `payload` field and `_INSERT_COLUMNS` no payload column, so the
  two claims above cannot quietly become one.
* `tests/unit/platform/integrations/test_amh_inbox_live_pg.py` reads the persisted rows back OUT of
  Postgres and asserts the sentinel is absent there too.

---

## 8. Open decisions for the reviewer

These are genuinely the DBA's calls. The migration has never been applied in any environment, so
amending it before ratification is legitimate and costs nothing — that is the point of reviewing
it while it is dark. Any amendment invalidates the digest binding automatically (§9), which is the
intended behaviour: a changed migration must be re-reviewed.

| # | Decision | Author's position |
|---|---|---|
| **D-1** | Should `idempotency_key` be UNIQUE (`amh_tenant`, `idempotency_key`)? | **Not yet.** The pinned contract declares no uniqueness scope for the field, and asserting one would be this repository inventing contract semantics XRD-04 reserves to the AMH. If the contract owner publishes that scope, add the constraint in a follow-up migration and drop `ix_amh_inbox_idempotency`, which it subsumes. Concretely, what "not yet" costs today: **two different `event_id`s sharing one `idempotency_key` are both RECORDED and are independently settleable** — the key is a triage INDEX, not a dedup mechanism. Deduplication is `(contract_manifest_digest, event_id)` and nothing else. |
| **D-2** | Add a retention index (e.g. on `settled_at`) now, or later under `CONCURRENTLY`? | **Author deferred it**, because the retention policy it would serve (MZO-130) is human-gated and unratified. The counter-argument — that adding it later means `CREATE INDEX CONCURRENTLY` on a large table — is legitimate and is the reviewer's to weigh. |
| **D-3** | `text` vs `varchar(n)` for the digest/reference columns. | **`text`.** Matches every existing table in this chain (0002–0005), and Postgres stores them identically; a length cap would encode an assumption about reference formats that XRD-05 has not settled. |
| **D-4** | Should terminal rows be partitioned or archived out (e.g. monthly range partitioning on `received_at`)? | **Not at this scale, not yet.** Partitioning would complicate the primary-key/dedup guarantee (the partition key would have to join the PK). Revisit with MZO-130 and real volume figures. |
| **D-5** | Per-tenant application vs a single shared schema. | Follows the existing platform convention unchanged (schema-per-tenant, `alembic -x tenant=<schema>`). Flagged only so the reviewer confirms the operational runbook covers every tenant schema. |
| **D-6** | Should the inbox pool impose a `command_timeout` (and/or a session `lock_timeout`) on the unbounded speculative-insertion wait described in §5.2? | **Author set nothing, on purpose.** See below — this is the reviewer's number to pick, not the author's. |

### D-6 in full — the unbounded wait

**The property.** When two replicas `record` the SAME event, the second one's `INSERT ... ON
CONFLICT DO NOTHING` blocks on the first one's transaction until it commits or aborts (§5.2). The
pool is created with `min_size=1, max_size=10, setup=<search_path>` and **no `command_timeout`**;
no `SET lock_timeout` or `SET statement_timeout` is issued anywhere, so both are the server default
(verified `'0'` — unlimited — on the repository's own pooled session). A replica that inserts and
then stalls can hold every other replica's matching `record` call for as long as it stalls, up to
exhausting the 10-connection pool.

**Why no timeout was set in code.** A cap is a *policy*, and picking one silently would decide two
things that are not the author's to decide: how long a legitimate settlement transaction may take
in this deployment, and what a capped call does next (an `asyncio.TimeoutError` from
`command_timeout` aborts the call mid-transaction and must be turned into a retry the consumer
understands — that is MZO-050b design, and inventing it here would prejudge it).

**Suggested range, offered as input.** `lock_timeout` in the **2s–10s** band is the usual shape for
a short OLTP write path like this one: comfortably above a healthy `record` (single-digit
milliseconds) and well below the pool-exhaustion horizon. `command_timeout` on the pool would be a
blunter instrument — it bounds the whole call rather than just the lock acquisition — so if only
one is chosen, `lock_timeout` is the more precise. **Trade-off:** too low turns ordinary contention
between replicas processing the same redelivery into spurious failures the consumer must retry
(and each retry re-enters the same queue); too high, or absent, means one stalled writer's
transaction is the real ceiling. Setting either is a code change plus an update to this decision
and to `test_amh_inbox_live_pg.py::test_this_repositorys_own_pool_declares_no_lock_or_statement_timeout`,
which currently pins the "unset" state so it cannot change unnoticed.

---

## 8b. One consequence for MZO-050b, recorded now rather than discovered later

Not a schema question — flagged here because it is a decision the DBA's ratification makes
reachable, and because the fix, if anyone reaches for the wrong one, is an architecture change.

The consumer's settlement surface is the `AmhInbox` Protocol in
`src/maezo/platform/integrations/amh_inbox.py`, i.e. under `maezo.platform`. `maezo.adapters.amh`
is fenced against importing any `maezo.*` outside `maezo.ports` and its own package
(`tests/unit/adapters/amh/test_adapter_purity.py`, `_ALLOWED_MAEZO_PREFIXES`), and that fence walks
the AST, so an `if TYPE_CHECKING:` import does not evade it either. **The MZO-050b consumer can
therefore never IMPORT `AmhInbox`.**

This is not a blocker, and it must not be treated as a reason to relax the fence:

* **Composition-root injection works today.** `Protocol` is STRUCTURAL. A composition root builds a
  `PostgresAmhInbox` and hands it to the consumer; the consumer annotates the collaborator with a
  shape it is allowed to name, and the object satisfies it without either side importing the other.
* **The follow-up, if MZO-050b wants the annotation to name this exact surface**, is to declare the
  Protocol port-side under `maezo/ports/` — where the adapter IS allowed to import from — and have
  `PostgresAmhInbox` satisfy it. That is a port addition, reviewable on its own merits.
* **What is never the answer** is adding `maezo.platform` to `_ALLOWED_MAEZO_PREFIXES`. The fence
  is what keeps the AMH adapter free of SQL, brokers and platform internals; widening it for a type
  annotation would trade the boundary for a convenience.

---

## 9. Verification evidence available to the reviewer

Executed against a **live, disposable PostgreSQL 16.14** (`pgvector/pgvector:pg16`, random host
port, discarded afterwards) in the authoring worktree on 2026-08-09:

| Check | Result |
|---|---|
| `alembic upgrade head` (0001→0007) | applied; `amh_inbox` + 3 indexes + PK created |
| `alembic downgrade 0006` | `DROP TABLE IF EXISTS amh_inbox CASCADE`; `to_regclass` → NULL; version row → `0006`; 0006's own tables intact |
| `alembic upgrade head` (re-apply) | `amh_inbox` restored; version row → `0007`; all 4 index/PK entries present |
| `alembic upgrade 0006:0007 --sql` (offline) | emits the same DDL inside `BEGIN;`/version-update |
| `pg_dump --schema-only --table=amh_inbox` | §3.2 above, verbatim |
| Lock inventory during migration | §5.1 above — every `AccessExclusiveLock` on a newly-created relation only |
| Repository behaviour on live PG | `RECORDED` → `DUPLICATE` (count 1→2) → `CONFLICT` on mutated `payload_hash` (count unchanged) → `CONFLICT` on cross-tenant replay; `PROCESSED`→`SETTLED`→`ALREADY`; quarantine of a settled row `REFUSED_TERMINAL`; settle of a quarantined row `REFUSED_TERMINAL`; settle of an unrecorded event `NOT_FOUND`; `pending` excludes terminal rows and is tenant-scoped |
| Speculative-insertion contention (§5.2) | writer A holds an uncommitted insert; B's `record` BLOCKS on it (~4.4s against a 5s hold) and then resolves `DUPLICATE` if A committed / `RECORDED` if A rolled back — never `CONCURRENT`. At REPEATABLE READ and SERIALIZABLE the same sequence raises `could not serialize access due to concurrent update` from the INSERT itself |
| Pool timeout disclosure (D-6) | `SHOW lock_timeout` / `SHOW statement_timeout` on the repository's OWN pooled session → `'0'` / `'0'`; the pool is created with no `command_timeout` |
| Digest binding as a repository property | a hand-built `InboxRatification(migration_revision="0001", migration_sha256="deadbeef", …)` — which `__post_init__` accepts — is refused by `PostgresAmhInbox.__init__` (`migration_revision_mismatch`, then `migration_digest_mismatch` once the revision is corrected), and accepted with the real digest. Before the re-check it built a repository that `record`ed and `mark_settled` **applied** on this server |
| Divergent quarantine referral | a second `nack` carrying `TIMEOUT` for a row already quarantined `CONTRACT_VIOLATION` returns `ALREADY` **with `stored_reason='contract_violation'` and `reason_diverged=True`**; the row read back from Postgres is unchanged |
| CHECK constraints vs out-of-band writes | free-text `quarantine_reason`, unknown `status`, unknown `inbox_stream`, `SETTLED` with NULL `settled_at`, `settled_at` with non-`SETTLED` status, negative `replay_count` — all rejected, each by its named constraint |
| PHI sentinel read back from Postgres | absent from every column of every persisted row |
| `EXPLAIN` on the pending scan | `Index Scan using ix_amh_inbox_pending` |
| Packaged-deployment probe | wheel built, installed into a disposable venv with no repo checkout: the ratification artifact resolves package-adjacent (`site-packages/maezo/spec/policies/amh/…`), the migration digest computes from `site-packages/maezo/platform/migrations/versions/0007_amh_inbox.py`, and the shipped DRAFT still refuses (`not_ratified`) |
| Gate suite | `pytest tests/unit` **5152 passed / 41 skipped WITH a reachable Postgres**; **5130 passed / 63 skipped without one** (same 5193 total — the 22-test live file skips loudly rather than passing). Plus `ruff check`, `ruff format --check`, `mypy --strict`, `make validate-artifacts`, `verify-amh-contract-pin`, `check-bpmn-error-allowlist`, `check-start-process-fence` — all green |

**Reading the two test counts.** The higher count is not a better run, it is a DIFFERENT one: the
22 tests in `test_amh_inbox_live_pg.py` need a Postgres on the suite's dedicated port (default
`5647`, override `MAEZO_TEST_AMH_INBOX_DATABASE_URL`). CI without a database reports the lower
count and 22 extra skips, each naming the missing server. A reviewer checking these numbers should
confirm which of the two they are looking at before concluding anything is missing.

**Packaging note for the release engineer.** `spec/policies/amh` is force-included into the wheel
(`pyproject.toml`), on the same precedent as the autonomy matrix (ADR-0025 D2) and the AMH contract
pin. Without it, a container would find no artifact and the inbox could never be activated even
AFTER a DBA ratifies — the deploy-bricking failure mode MZO-050a hit and fixed for the pin.

`tests/unit/platform/integrations/test_amh_inbox_live_pg.py` re-runs every live check above and
**skips loudly** (never silently passes) when no Postgres is reachable; the module docstring
carries the one-line `docker run` command.

---

## 10. The ratification act

Ratifying is a **YAML edit**. It requires no code change, no new deployment of code, and no
migration edit. The exact steps:

**Step 1 — review.** Read §3 (DDL), §4 (indexes), §5 (locks), §6 (rollback), §7 (erasure) and
decide D-1..D-6 in §8. If any decision changes the DDL, amend
`src/maezo/platform/migrations/versions/0007_amh_inbox.py` first and re-run the proof in §9.

**Step 2 — compute the digest of the migration you are approving.**

```bash
shasum -a 256 src/maezo/platform/migrations/versions/0007_amh_inbox.py
```

This binds the approval to the exact bytes reviewed. If the migration is edited afterwards, the
digest stops matching and the loader refuses again until a DBA re-reviews and re-digests. Do not
copy a digest from anywhere else — compute it.

**Step 3 — edit `spec/policies/amh/inbox-ratification.yaml`.** Replace every `PENDING-*`
placeholder with a real value (the loader refuses any value that still looks like a placeholder —
a half-filled ratification is not a ratification) and flip the three switch fields:

```yaml
status: RATIFIED            # was DRAFT
ratificado: true            # was false — must be the YAML BOOLEAN, not the string "true"
dba_review: APPROVED        # was PENDING

dba_reviewer: <your name and role>
dba_review_date: <YYYY-MM-DD>
evidence_ref: <evidence-ledger row id for this ratification>
notes: <your notes / any conditions attached to the approval>

migration_revision: "0007"
migration_sha256: <the 64-hex digest from step 2>
review_packet: docs/reviews/mzo-060-dba-review-packet.md
```

**Step 4 — apply the migration** per tenant schema: `alembic -x tenant=<schema> upgrade head`.

**Step 5 — record the ratification** in `docs/evidence-ledger.md`.

### Who may do this

**Only a DBA.** No agent, no orchestrator and no automated gatekeeper may fill in these fields or
flip `ratificado`. Fabricating an approval in this repository is a compliance event, not a merge
conflict.

### Ratification record — TO BE COMPLETED BY THE REVIEWING DBA

| Field | Value |
|---|---|
| Reviewing DBA | **PENDING** |
| Review date | **PENDING** |
| Verdict (APPROVED / REJECTED / APPROVED WITH AMENDMENTS) | **PENDING** |
| D-1 (`idempotency_key` UNIQUE) | **PENDING** |
| D-2 (retention index now vs later) | **PENDING** |
| D-3 (`text` vs `varchar(n)`) | **PENDING** |
| D-4 (partitioning) | **PENDING** |
| D-5 (per-tenant rollout runbook) | **PENDING** |
| D-6 (`lock_timeout` / `command_timeout` on the inbox pool) | **PENDING** |
| `migration_sha256` approved | **PENDING** |
| Conditions attached | **PENDING** |
| Evidence-ledger row | **PENDING** |

*Even after this table is filled in, the inbox does not operate until
`spec/policies/amh/inbox-ratification.yaml` itself is edited. This packet records the reasoning;
that file is the switch.*
