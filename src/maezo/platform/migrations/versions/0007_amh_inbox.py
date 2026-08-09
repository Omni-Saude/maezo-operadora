"""AMH durable inbox — the settlement table `WorkItemSource.ack` needs to stop lying (MZO-060).

**Why this table exists, in one sentence.** ADR-0037 XRD-10 states that "offsets Kafka nunca
representam conclusao de negocio", and `maezo.ports.work_items.WorkItemSource.ack` may return
success ONLY on DURABLE settlement — so a committed Kafka offset is not an `ack`, and the only
thing that CAN be one is a committed row in the payer's own database. That row is `amh_inbox`.

**Dark by construction.** Nothing reads or writes this table today. The MZO-050b consumer that
will (`maezo.adapters.amh` has no `consumer.py` — see that package's docstring for the four
blockers) is separately gated, and `maezo.platform.integrations.amh_inbox`, the typed repository
that speaks this schema, REFUSES to construct until a DBA ratifies
`spec/policies/amh/inbox-ratification.yaml`. This migration is therefore the SCHEMA half of a
change whose OPERATIONAL half is a human gate: the DDL is reviewable now (see
`docs/reviews/mzo-060-dba-review-packet.md`), and applying it activates nothing.

--------------------------------------------------------------------------------------------
Dedup key: `(contract_manifest_digest, event_id)` — verbatim from XRD-10
--------------------------------------------------------------------------------------------
XRD-10 names the deduplication key literally: "deduplicacao `{contract_manifest_digest,
event_id}`". It is the PRIMARY KEY here for that reason and no other. Two consequences worth
stating rather than leaving a reader to infer:

  * `contract_manifest_digest` is IN the key, so the same `event_id` republished under a NEW
    contract manifest is a DIFFERENT row. That is the intended semantics — a contract major bump
    with a dual-publish window (ADR-0037 "janelas de dual-publish/dual-read de no minimo 30 dias")
    legitimately re-delivers the same business event under a new manifest, and collapsing those
    onto one row would make the second manifest's delivery silently invisible.
  * `amh_tenant` is NOT in the key. Adding it would WEAKEN the dedup: a replay of one `event_id`
    stamped with a different tenant would become an accepted second row instead of a detected
    collision. It is a NOT NULL column and the repository treats a dedup-key hit whose stored
    `amh_tenant` differs from the incoming one as a contract violation, not as a duplicate.

`idempotency_key` is stored (NOT NULL) and indexed but is deliberately NOT unique: the pinned
contract (`config/integrations/amh/contracts.lock.json`) declares the FIELD but declares no
uniqueness SCOPE for it, and a UNIQUE constraint would be this repo inventing contract semantics
the owner has not published (ADR-0037 XRD-04: the AMH is the sole editor of the canonical
contract). If the owner publishes that scope, the follow-up is a `UNIQUE (amh_tenant,
idempotency_key)` in a later migration — see the DBA packet's open-decision list.

--------------------------------------------------------------------------------------------
PHI posture: no subject-linkable column, therefore no erasure cascade target
--------------------------------------------------------------------------------------------
ADR-0037 immutable prohibition #5 forbids PHI and raw source identifiers in keys, logs, traces,
metrics and quarantine metadata. Every column below is either a digest, a closed vocabulary
token, an opaque correlation reference, a timestamp or a counter. FIVE of the canonical envelope's
28 fields are DELIBERATELY ABSENT and must stay absent:

    protected_source_record_ref   raw source-record reference (prohibition #5, verbatim)
    portable_subject_ref          the subject reference itself (XRD-05, DPO/Legal-gated)
    amh_mpi_ref                   master-patient-index reference
    beneficiary_ref               beneficiary reference
    consent_decision_ref          names a decision taken FOR ONE SUBJECT -> subject-linkable

The event body is not a sixth entry on that list, because it was never an envelope field:
`maezo.ports.envelope.CanonicalEnvelope` has no `payload`, and this table has NO PAYLOAD COLUMN at
all — see the next section for why that is a boundary decision rather than an oversight.

`tests/unit/platform/test_migration_0007_amh_inbox.py` pins that absence structurally, and
`maezo.platform.integrations.amh_inbox._stored_row` — the one function that projects an envelope
onto these columns — is tested with a planted sentinel to prove no subject field reaches a bind
parameter.

**No payload bytes here, and that is a boundary decision, not an omission.** The 0004
custody/erasure precedent (`custody_bundles` + `erasure_log`) exists for data this repo IS the
custodian of. The AMH is the lake of record (ADR-0019) and the ADR-0013 principle that survives
into ADR-0037 is consume-not-duplicate: "o Maezo nunca e producer nem detentor de copia de
registro". Persisting the event body here would make this repository a second copy of record of
AMH-owned data, which is the thing the boundary exists to prevent. `payload_hash` (the contract's
own sha256 over the canonical JSON form, per the pin's `payload_hash_canonicalization`) is kept
INSTEAD: it is a non-reversible digest, it is not a subject identifier, and it is what lets a
replay carrying MUTATED content be detected rather than silently deduplicated.

Consequence, stated for the DPO/DBA rather than left implicit: an LGPD art. 18 VI erasure request
has NO cascade target in this table. There is no row to delete, no column to null, and no
re-identification path from a settled row back to a data subject without the AMH's own mapping
(which only the AMH holds — XRD-05). The erasure coverage section of the DBA packet says this in
the form a reviewer can check column by column.

--------------------------------------------------------------------------------------------
Lifecycle: RECEIVED -> [PROCESSED] -> SETTLED, or -> QUARANTINED. Both ends terminal.
--------------------------------------------------------------------------------------------
    RECEIVED     the dedup row committed; the payer core has NOT yet absorbed the event.
    PROCESSED    optional intermediate marker (the core absorbed it; the ack has not been given).
    SETTLED      DURABLE settlement. This — and only this — is what licences `ack` to return ok.
    QUARANTINED  the event is not absorbable; a quarantine referral was recorded. Carries ONLY a
                 closed `PortFailureReason` token and the pinned quarantine TOPIC NAME. Never the
                 payload, never an upstream error body, never a subject reference (prohibition #5
                 + `maezo.ports.work_items.WorkItemSource.nack`).

SETTLED and QUARANTINED are terminal and mutually exclusive; the repository enforces monotonic
transitions with conditional UPDATEs (`WHERE status = ...`), never a trigger — a trigger would put
business rules in a place no unit test in this repo can reach.

The CHECK constraints below are BICONDITIONAL on purpose (`(status = 'SETTLED') = (settled_at IS
NOT NULL)`), not one-directional. A one-directional check permits a row that claims a settlement
timestamp while sitting in RECEIVED — i.e. a row an operator would read as settled and the
repository would read as pending. The reason `quarantine_reason` is CHECK-constrained to the
`maezo.ports.errors.PortFailureReason` value set is the same defence one level down: an
out-of-band writer cannot stamp a free-text reason that the closed taxonomy could never produce.
`tests/unit/platform/test_migration_0007_amh_inbox.py` asserts the DDL's checked set EQUALS the
enum, so the two cannot drift.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # amh_inbox — durable settlement of AMH->Maezo boundary events (ADR-0037 XRD-10)
    # ------------------------------------------------------------------
    # DDL is unqualified (schema-per-tenant; env.py sets search_path) and idempotent
    # (IF NOT EXISTS), mirroring a2a_idempotency / driver_idempotency (0003) and
    # audit_emit_dedup (0005).
    op.execute("""
        CREATE TABLE IF NOT EXISTS amh_inbox (
            -- Dedup key, verbatim from ADR-0037 XRD-10.
            contract_manifest_digest text NOT NULL,
            event_id                 text NOT NULL,

            -- Tenant / contract scope. `amh_tenant` is the CONTRACT's tenant field; the
            -- Postgres schema this row lives in is the DEPLOYMENT's tenant (env.py). The
            -- mapping between the two is MZO-020 (DPO/Legal-gated) and is NOT decided here.
            amh_tenant               text NOT NULL,
            legal_entity             text NOT NULL,

            -- Which port this row settles. Payer-core vocabulary, NOT a wire value: binding
            -- the DDL to pinned topic names would make a contract major bump a schema change.
            inbox_stream             text NOT NULL,

            event_type               text NOT NULL,
            canonical_schema_version text NOT NULL,
            idempotency_key          text NOT NULL,

            -- Provenance that carries no record identity: a closed product vocabulary, the
            -- source ENTITY name (a table/stream name, not a row id) and the source's opaque
            -- ordering position. `source_vendor`/`source_instance`/`source_tenant` are omitted
            -- as source-system identifiers with no settlement role.
            source_product           text NOT NULL,
            source_entity            text NOT NULL,
            source_position_kind     text NOT NULL,
            source_position_value    text NOT NULL,

            -- Integrity + replay accounting. `replay_count` is the ENVELOPE's own field (how
            -- many times the PRODUCER replayed it); `redelivery_count` is THIS inbox's fact
            -- (how many times we saw the dedup key). They are different questions and a single
            -- column could answer neither honestly.
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

            -- Observability joins. Opaque references the payer core never parses.
            correlation_id           text NOT NULL,
            trace_id                 text NOT NULL,

            CONSTRAINT pk_amh_inbox
                PRIMARY KEY (contract_manifest_digest, event_id),

            CONSTRAINT ck_amh_inbox_status
                CHECK (status IN ('RECEIVED', 'PROCESSED', 'SETTLED', 'QUARANTINED')),

            CONSTRAINT ck_amh_inbox_stream
                CHECK (inbox_stream IN ('work_item', 'consent')),

            -- Biconditional: a settlement timestamp exists IF AND ONLY IF the row is settled.
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

            -- Closed refusal taxonomy — the exact value set of
            -- `maezo.ports.errors.PortFailureReason`. Defence in depth against an out-of-band
            -- writer stamping a free-text reason; drift from the enum fails a unit test.
            CONSTRAINT ck_amh_inbox_quarantine_reason_vocabulary
                CHECK (quarantine_reason IS NULL OR quarantine_reason IN (
                    'consent_required',
                    'purpose_denied',
                    'scope_not_supported',
                    'below_committed_k',
                    'individual_dimension',
                    'invalid_request',
                    'not_found',
                    'not_authenticated',
                    'rate_limited',
                    'timeout',
                    'upstream_unavailable',
                    'contract_violation'
                )),

            CONSTRAINT ck_amh_inbox_replay_count
                CHECK (replay_count >= 0),

            CONSTRAINT ck_amh_inbox_redelivery_count
                CHECK (redelivery_count >= 1)
        )
    """)

    # Pending scan (the consumer's "what have I received but not settled?" query). PARTIAL on
    # purpose: an inbox grows monotonically, so a full index on (amh_tenant, received_at) would
    # keep every settled row forever in the index the scan reads. Rows leave this index when they
    # reach a terminal state, so it stays proportional to IN-FLIGHT work, not to history.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_amh_inbox_pending
            ON amh_inbox (amh_tenant, received_at)
            WHERE status IN ('RECEIVED', 'PROCESSED')
    """)

    # Operational lookup by the envelope's own idempotency key. Non-unique — see the module
    # docstring: the contract declares the field but not its uniqueness scope.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_amh_inbox_idempotency
            ON amh_inbox (amh_tenant, idempotency_key)
    """)

    # Quarantine triage. Also partial: quarantined rows are the rare tail, and a full index would
    # charge every ordinary INSERT for a query that only ever reads the exceptions.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_amh_inbox_quarantined
            ON amh_inbox (amh_tenant, quarantined_at DESC)
            WHERE status = 'QUARANTINED'
    """)


def downgrade() -> None:
    # Honest inverse: `upgrade()` creates exactly one table plus three indexes ON that table, and
    # dropping the table drops its indexes and constraints with it. Nothing else was created, so
    # nothing else is dropped -- and nothing pre-existing is touched.
    op.execute("DROP TABLE IF EXISTS amh_inbox CASCADE")
