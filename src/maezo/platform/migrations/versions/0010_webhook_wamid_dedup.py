"""driver_idempotency repurposed as the WhatsApp inbound/outbound dedup registry.

GAP `DRIVER-IDEMPOTENCY-ORPHAN-TABLE` — owner decision R-073 (2026-09-04): option **2,
`REPROPOR` the table "na mesma migracao que declara o novo uso". This IS that migration.

WHY A MIGRATION AT ALL, when `0003_a2a_idempotency.py:61-72` already creates the table with the
shape the dedup needs (`key text PRIMARY KEY`, `tenant`, `expires_at`, `created_at`, plus
`ix_driver_idempotency_expires`)? Because between 0003 and today the table had ZERO readers and
ZERO writers in `src/` — its only consumer was the LGPD retention inventory
(`platform/lifecycle/erasure_plan.py`, `SEM_COLUNA_DE_TITULAR`) — and the ADR that named it
(ADR-0024) describes drivers (`InboundDriver`/`ResumeDriver`) that were never built. A repurpose
that lives only in Python would leave the DATABASE saying nothing about what the rows mean. So
this migration writes the new use INTO the schema (SQL `COMMENT ON`, readable by any DBA with
`\\d+ driver_idempotency`) and adds the ONE column the new use needs that 0003 has no equivalent
of.

WHAT IS ADDED, and why each item is not decoration:

1.  `status text NOT NULL DEFAULT 'processed'` + `ck_driver_idempotency_status`. The dedup is a
    CLAIM/COMMIT protocol, not a single-shot insert: `claim` writes a `'pending'` row BEFORE the
    Helena turn runs, and `mark_processed` seals it AFTER the beneficiary was actually answered.
    Without the two states, a receiver killed mid-turn would leave a row that suppresses Meta's
    redelivery of a message that was never answered — the dedup would CAUSE the silent loss it
    exists to prevent. A `'pending'` row older than the in-flight lease is re-claimable
    (`platform/driver_idempotency.py::CLAIM_SQL`), which is exactly the recovery path.
    DEFAULT `'processed'` (terminal), not `'pending'`: any pre-existing row (there are none in
    any environment — the table has never been written to — but a DEFAULT must still be chosen
    for a hypothetical one) must be treated as a settled key, never as a re-claimable in-flight
    claim.
2.  `ix_driver_idempotency_pending` — PARTIAL on `status = 'pending'`, keyed by `created_at`.
    This is the index a re-drive scan ("which claims never sealed?") needs, and the ack-then-queue
    entry leg R-072 adopts: the durable claim row IS the queue entry. Partial and NOT unique: a
    unique index here would be a second, redundant PK.
3.  `ix_driver_idempotency_expires` is RE-ASSERTED (`IF NOT EXISTS`, a no-op wherever 0003 ran)
    so this migration is self-contained about the expiry index the TTL sweep depends on, instead
    of leaving it as an inherited assumption of a migration written for a different feature.
4.  `COMMENT ON TABLE`/`COMMENT ON COLUMN` — the repurpose declaration R-073 asks for, in the
    schema itself.

NOT DONE HERE, deliberately: the table is NOT dropped and NOT recreated (option 1 `DROP` was
rejected by the owner), no column of 0003 changes type or nullability, and no data is written.
`a2a_idempotency` (the sibling created by the same 0003) is NOT touched at all — a different
protocol, a different owner.

PHI (LGPD): the `key` column carries `wa:{leg}:{tenant}:hk1_{hmac}` — the `wamid` is passed
through the SAME vault-keyed `Pseudonymizer` the conversation id uses (ADR-0035), because a raw
`wamid` base64-encodes the counterpart phone number in its own payload. So the table stays
`SEM_COLUNA_DE_TITULAR` (`platform/lifecycle/erasure_plan.py`): no column of it can be resolved
back to a beneficiary without `PHI_HMAC_KEY`.

Revision ID: 0010
Revises: 0008
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
#
# CHAIN NOTE (2026-09-04): revision `0009` is claimed by a SEPARATE, concurrently-open change
# (the pgvector migration, PR #320) that is not on `main` at this file's base commit `bd84403`.
# This file therefore takes `0010` as its id and revises `0008` — the real head of the chain in
# THIS tree, so `alembic upgrade head` is valid here today. Whichever of the two lands SECOND
# must re-point its `down_revision` at the other so the chain stays linear; the linear-chain test
# (`tests/unit/platform/test_migration_0010_webhook_wamid_dedup.py`) fails loudly on a fork, which
# is the mechanism that forces that edit instead of trusting a merge to notice.
revision: str = "0010"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. The claim/commit state column (see the module docstring, item 1).
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE driver_idempotency
            ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'processed'
    """)

    # Postgres has no `ADD CONSTRAINT IF NOT EXISTS`; the guarded DO block is the idempotent
    # equivalent, so re-running this migration on an already-upgraded schema is a no-op rather
    # than a `duplicate_object` error.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_driver_idempotency_status'
                  AND conrelid = 'driver_idempotency'::regclass
            ) THEN
                ALTER TABLE driver_idempotency
                    ADD CONSTRAINT ck_driver_idempotency_status
                    CHECK (status IN ('pending', 'processed'));
            END IF;
        END
        $$
    """)

    # ------------------------------------------------------------------
    # 2. Re-drive / ack-then-queue scan index — PARTIAL on the in-flight state.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_driver_idempotency_pending
            ON driver_idempotency (created_at)
            WHERE status = 'pending'
    """)

    # ------------------------------------------------------------------
    # 3. Expiry index, re-asserted (created by 0003; no-op where it already exists).
    # ------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_driver_idempotency_expires
            ON driver_idempotency (expires_at)
    """)

    # ------------------------------------------------------------------
    # 4. The repurpose declaration, in the schema (R-073).
    # ------------------------------------------------------------------
    op.execute("""
        COMMENT ON TABLE driver_idempotency IS
            'Registro duravel de deduplicacao do canal WhatsApp (gap WEBHOOK-WAMID-DEDUP, '
            'decisao do dono R-071/R-073, 2026-09-04). Reaproveita a tabela criada por '
            '0003_a2a_idempotency para os drivers da ADR-0024, que nunca foram construidos. '
            'Escritores: maezo.platform.driver_idempotency.PostgresDriverIdempotencyRegistry '
            '(perna de entrada, webhooks/whatsapp/app.py) e o guarda de saida em '
            'tools/mcp_whatsapp/server.py::WhatsAppServer.send_message.'
    """)
    op.execute("""
        COMMENT ON COLUMN driver_idempotency.key IS
            'wa:{leg}:{tenant}:hk1_{hmac} — pseudonimo COM CHAVE (ADR-0035) do wamid, nunca o '
            'wamid cru (que carrega o telefone da contraparte em base64). leg = inbound|outbound.'
    """)
    op.execute("""
        COMMENT ON COLUMN driver_idempotency.status IS
            'pending = entrega reivindicada e ainda em voo; processed = beneficiario ja '
            'respondido. Linha pending mais velha que o lease e re-reivindicavel.'
    """)
    op.execute("""
        COMMENT ON COLUMN driver_idempotency.expires_at IS
            'Fim da janela de dedup (TTL). Depois disso a mesma chave e tratada como nova '
            'entrega. Varrida por idade pelo indice ix_driver_idempotency_expires.'
    """)


def downgrade() -> None:
    # Drops EXACTLY what `upgrade()` created — never the table itself (0003 owns it) and never
    # the expiry index (0003 owns that too; item 3 above only re-asserted it).
    op.execute("COMMENT ON COLUMN driver_idempotency.expires_at IS NULL")
    op.execute("COMMENT ON COLUMN driver_idempotency.status IS NULL")
    op.execute("COMMENT ON COLUMN driver_idempotency.key IS NULL")
    op.execute("COMMENT ON TABLE driver_idempotency IS NULL")
    op.execute("DROP INDEX IF EXISTS ix_driver_idempotency_pending")
    op.execute("ALTER TABLE driver_idempotency DROP CONSTRAINT IF EXISTS ck_driver_idempotency_status")
    op.execute("ALTER TABLE driver_idempotency DROP COLUMN IF EXISTS status")
