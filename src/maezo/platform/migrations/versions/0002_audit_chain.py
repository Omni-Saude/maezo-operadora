"""Audit chain — hash-linked, append-only, anti-fork (ADR-0007, DL-0018).

ADR-0007: Every effect on the world is recorded in a SHA-256 hash chain.
DL-0018: The chain is NON-PARTITIONED — UNIQUE(prev_record_hash) is enforced
globally, ensuring a single, unforgeable audit trail. Retention is handled
via DELETE by age (not partitioning), preserving the global UNIQUE constraint.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the audit_chain table — append-only hash-linked audit trail."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            timestamp       timestamptz NOT NULL DEFAULT now(),
            tenant_id       text NOT NULL,
            agent_id        text NOT NULL,
            agent_version   text NOT NULL,
            action          text NOT NULL,
            decision        text NOT NULL,
            input_hash      text NOT NULL,
            decision_basis  jsonb NOT NULL DEFAULT '{}'::jsonb,
            dmn_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
            model_id        text,
            prompt_version  text,
            record_hash     text NOT NULL,
            prev_record_hash text,
            created_at      timestamptz NOT NULL DEFAULT now(),

            -- DL-0018: UNIQUE(prev_record_hash) global — anti-fork enforcement.
            -- A non-partitioned table is REQUIRED because Postgres enforces
            -- that partitioned-table UNIQUE constraints MUST include the
            -- partition key. Partitioning by ts would weaken anti-fork to
            -- (prev_record_hash, ts) + trigger (non-atomic under READ COMMITTED).
            -- This global UNIQUE prevents concurrent forks atomically.
            CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_chain_tenant
            ON audit_chain (tenant_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_chain_agent
            ON audit_chain (tenant_id, agent_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_chain_timestamp
            ON audit_chain (timestamp DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_chain_record_hash
            ON audit_chain (record_hash)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_chain CASCADE")
