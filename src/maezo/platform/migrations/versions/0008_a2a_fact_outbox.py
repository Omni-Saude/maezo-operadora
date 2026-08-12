"""A2A delegation-fact transactional OUTBOX — the durable replacement for `_NoopKafkaProducer`.

**The defect this table closes.** `runtime/agent_runtime/a2a_composition.py` wires BOTH A2A
composition roots with `FactProducer(kafka_producer or _NoopKafkaProducer())`, and
`_NoopKafkaProducer.send` is literally `return None`. Every `agents.events.delegation.{requested,
completed,rejected}` fact this platform has ever produced was DROPPED on the floor at the moment of
emission — no queue, no retry, no record that it existed. The audit chain (T-F, `audit_chain`) is
unaffected and was never the thing at risk; what was lost is the entire observability/reactive
surface ADR-0003 declares. This table is where those facts land instead, and
`maezo.a2a.outbox_relay` is what later moves them to a real broker.

**Why an outbox and not "just construct a real Kafka producer".** A direct producer call inside
`DelegationDispatcher._emit` is a network effect in the middle of a delegation: it either blocks
the delegation on broker availability or it drops facts on broker unavailability — the two
failure modes the noop chose between by always choosing "drop". An outbox row is a database write
to the SAME Postgres the delegation already depends on (`_execute` performs
`_audit_delegation` -> `emit_once` BEFORE any fact is emitted, so the delegation's liveness is
already bound to this database), and delivery becomes a separate, restartable, at-least-once
process.

--------------------------------------------------------------------------------------------
`payload bytea`, not `jsonb` — and this one is load-bearing
--------------------------------------------------------------------------------------------
`FactProducer.emit` hands the producer `DelegationFact.to_value()`: a canonicalized
(`sort_keys=True`, `separators=(",", ":")`) UTF-8 JSON encoding. The outbox stores those BYTES
verbatim so the relay delivers to the broker exactly what a direct producer would have delivered.
Round-tripping through `jsonb` would normalize key order and whitespace, which is invisible today
and NOT invisible under ADR-0039 (envelope signing, Proposed): a signature computed over a byte
sequence does not survive a re-serialization. Storing bytes keeps the outbox transparent to any
future signing decision instead of silently foreclosing it.

The fact carries NO PHI — `maezo.a2a.facts.DelegationFact` is `task_id`/chain/type/route/reason
only, and `facts.build_fact` explicitly refuses to let `meta` enter the fact ("reserved for
extension; never enters the fact (avoids accidental PHI)"). This is why a payload column is
correct HERE and was correct to REFUSE in `0007_amh_inbox` for the opposite reason: there the
payload was AMH-owned record data and this repo is not its custodian (consume-not-duplicate,
ADR-0013/0019); here the payload is a fact THIS repo produces and must deliver verbatim, and you
cannot deliver a digest to a topic.

--------------------------------------------------------------------------------------------
`tenant` is a COLUMN, not the schema — and it is deliberately not a claim filter
--------------------------------------------------------------------------------------------
Same distinction `0007_amh_inbox` draws between `amh_tenant` and the Postgres schema: the SCHEMA a
row lives in is the DEPLOYMENT's tenant (search_path, DL-0017); the `tenant` COLUMN is the FACT's
own `DelegationFact.tenant`, which is `DelegationEnvelope.tenant`.

Those two can legitimately differ in exactly one place, and it is a place worth keeping rather
than refusing: an envelope addressed to a FOREIGN tenant fails `A2ARegistry.lookup` and is
rejected (`RejectionReason.UNKNOWN_TARGET`), and `_execute` emits a `rejected` fact carrying that
foreign tenant. Refusing to store it would discard the record of a cross-tenant delegation attempt
— which is this deployment's own security-relevant event, in this deployment's own schema. So the
column records the fact's tenant honestly and the drain scan does NOT filter on it: the schema
already scopes the relay, and a tenant filter would strand exactly those rows undelivered forever.

--------------------------------------------------------------------------------------------
`dedup_key` is the CONSUMER's key and is deliberately NOT UNIQUE
--------------------------------------------------------------------------------------------
Shape: `{tenant}:a2a:delegate:{task_id}:{kind}` — the same construction as
`dispatcher.a2a_audit_dedup_key` (`{tenant}:a2a:delegate:{task_id}`) and
`a2a_audit_outcome_dedup_key` (`...:outcome`), extended with the fact KIND because one delegation
legitimately produces a `requested` AND a terminal `completed`/`rejected`.

It is indexed and NOT unique, mirroring `0007_amh_inbox`'s `idempotency_key` decision and for a
stronger reason: this is an AT-LEAST-ONCE pipeline whose dedup point is the CONSUMER. A UNIQUE
constraint here would convert a legitimate re-emission (the dispatcher's durable-idempotency
`_poll_until_done` budget can expire and let a second replica re-execute — `idempotency.py`'s own
documented best-effort fallback) into an `asyncpg.UniqueViolationError` raised out of
`FactProducer.emit`, i.e. out of `DelegationDispatcher.delegate` — a dropped DELEGATION to protect
a duplicate FACT. The duplicate is what the key exists to let the consumer collapse; it is not an
error to be raised at the producer.

--------------------------------------------------------------------------------------------
Claim discipline: `pending -> claimed (leased) -> delivered`, crash-safe by lease EXPIRY
--------------------------------------------------------------------------------------------
    pending      enqueued, never claimed (or a failed attempt released back).
    claimed      leased by one relay worker until `claim_expires_at`. AFTER that instant the row
                 is claimable again by anyone, WITHOUT any recovery process running.
    delivered    terminal. `delivered_at` is set; the row leaves the partial drain index.

At-least-once falls directly out of that: the relay publishes to the broker and THEN marks
delivered. A crash in between leaves a `claimed` row whose lease expires, so it is re-claimed and
RE-PUBLISHED. The duplicate is expected and is what `dedup_key` is for. The reverse ordering
(mark-then-publish) would be at-most-once and would silently lose facts, which is the defect this
table exists to remove.

The CHECK constraints are BICONDITIONAL for the same reason `0007_amh_inbox` gives: a
one-directional check admits a row that carries a `delivered_at` while sitting in `pending` — a
row an operator reads as delivered and the relay reads as pending.

--------------------------------------------------------------------------------------------
RETENTION IS NOT DECIDED HERE. It is a DBA question (MZO-060).
--------------------------------------------------------------------------------------------
There is no TTL column, no `expires_at`, and no purge job — deliberately, and this is the one
thing a reviewer should NOT read as an oversight. `delivered` rows accumulate monotonically and
something must eventually remove them; what that something is (partition-by-month + DETACH, a
`DELETE ... WHERE delivered_at < now() - interval`, or an archival copy) is an operational
decision with backup/restore and PITR consequences that belongs to the DBA review, not to this
migration.

Two constraints the DBA needs stated rather than discovered:
  * The drain index is PARTIAL (`WHERE status <> 'delivered'`), so index size already tracks
    IN-FLIGHT work rather than history — retention pressure is on heap/backup size, not on the
    hot path. This buys time; it does not answer the question.
  * ADR-0039 (Proposed) records that A2A idempotency retention DOMINATES signature validity — a
    retention window chosen for this outbox must not be reasoned about independently of the
    `a2a_idempotency` window, because a fact redelivered after its idempotency row was purged is
    a fact whose replay semantics changed. Whatever window is chosen here should be chosen with
    that one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # a2a_fact_outbox — transactional outbox for delegation facts (ADR-0003)
    # ------------------------------------------------------------------
    # DDL is unqualified (schema-per-tenant; env.py sets search_path) and idempotent
    # (IF NOT EXISTS), mirroring a2a_idempotency / driver_idempotency (0003), audit_emit_dedup
    # (0005) and amh_inbox (0007).
    op.execute("""
        CREATE TABLE IF NOT EXISTS a2a_fact_outbox (
            -- Claim ORDER. A bigserial (not a uuid) because the relay drains oldest-first and
            -- the surrogate is never carried onto the wire — the broker sees `payload` only.
            id                bigserial PRIMARY KEY,

            -- The FACT's own tenant (DelegationFact.tenant), NOT the deployment schema's tenant.
            -- See the module docstring for why those can differ and why that is kept, not refused.
            tenant            text NOT NULL,

            -- Consumer-side deduplication key: `{tenant}:a2a:delegate:{task_id}:{kind}`.
            -- Indexed, NOT unique -- module docstring.
            dedup_key         text NOT NULL,

            -- Destination + partitioning, exactly as `FactProducer.emit` supplies them.
            topic             text NOT NULL,
            partition_key     text,

            -- The canonicalized `DelegationFact.to_value()` BYTES, stored verbatim (never jsonb).
            payload           bytea NOT NULL,

            -- Lifecycle.
            status            text NOT NULL DEFAULT 'pending',
            attempts          integer NOT NULL DEFAULT 0,
            created_at        timestamptz NOT NULL DEFAULT now(),
            claimed_at        timestamptz,
            claim_expires_at  timestamptz,
            claimed_by        text,
            delivered_at      timestamptz,
            -- Last publish failure, for triage. A relay-side error STRING (broker/timeout), never
            -- a payload and never an upstream body -- same posture as amh_inbox's quarantine
            -- columns (ADR-0037 prohibition #5).
            last_error        text,

            CONSTRAINT ck_a2a_fact_outbox_status
                CHECK (status IN ('pending', 'claimed', 'delivered')),

            -- Biconditional: a delivery timestamp exists IF AND ONLY IF the row is delivered.
            CONSTRAINT ck_a2a_fact_outbox_delivered_at
                CHECK ((status = 'delivered') = (delivered_at IS NOT NULL)),

            -- Biconditional: a lease exists IF AND ONLY IF the row is claimed. This is what makes
            -- expiry-based recovery safe -- a delivered or released row can never retain a lease
            -- that would keep it out of a `claim_expires_at < now()` sweep.
            CONSTRAINT ck_a2a_fact_outbox_claim_lease
                CHECK ((status = 'claimed') = (claim_expires_at IS NOT NULL)),

            CONSTRAINT ck_a2a_fact_outbox_attempts
                CHECK (attempts >= 0)
        )
    """)

    # The drain scan ("what is not delivered yet, oldest first?"). PARTIAL on purpose, for the
    # same reason 0007's pending index is: an outbox grows monotonically, so a full index would
    # keep every delivered row forever in the index the relay reads on every batch. Rows leave
    # this index when they reach the terminal state, so it stays proportional to UNDELIVERED work.
    # `id` is in the key so the ORDER BY (created_at, id) tiebreak is index-ordered too.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_fact_outbox_undelivered
            ON a2a_fact_outbox (created_at, id)
            WHERE status <> 'delivered'
    """)

    # Operational lookup ("did fact X ever get enqueued / how many copies exist?"). Non-unique --
    # see the module docstring; a UNIQUE here would raise out of a delegation.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_fact_outbox_dedup
            ON a2a_fact_outbox (tenant, dedup_key)
    """)


def downgrade() -> None:
    # Honest inverse: `upgrade()` creates exactly one table plus two indexes ON that table, and
    # dropping the table drops its indexes and constraints with it. Nothing else was created, so
    # nothing else is dropped -- and nothing pre-existing is touched.
    op.execute("DROP TABLE IF EXISTS a2a_fact_outbox CASCADE")
