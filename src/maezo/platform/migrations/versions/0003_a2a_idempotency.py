"""A2A + driver idempotency — durable Postgres-backed deduplication.

ADR-0015: A2A idempotency (Guard 4) — task_id deduplication for agent-to-agent
delegation, structured as claim_or_get/complete with StoredResult.

ADR-0024: Driver idempotency — InboundDriver/ResumeDriver deduplication by key
with TTL-backed expiry. Separated from a2a_idempotency because the protocol
is different (is_processed/mark_processed vs claim_or_get/complete), and the
columns (task_type/origin/target) are delegation-specific.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # a2a_idempotency — A2A delegation dedup (ADR-0015, Guard 4)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS a2a_idempotency (
            task_id          text NOT NULL,
            tenant           text NOT NULL,
            task_type        text NOT NULL,
            origin           text NOT NULL,
            target           text NOT NULL,
            status           text NOT NULL DEFAULT 'processing',
            result           jsonb,
            created_at       timestamptz NOT NULL DEFAULT now(),
            completed_at     timestamptz,

            PRIMARY KEY (task_id, tenant)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_idempotency_tenant
            ON a2a_idempotency (tenant)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_idempotency_status
            ON a2a_idempotency (status)
            WHERE status = 'processing'
    """)

    # ------------------------------------------------------------------
    # driver_idempotency — InboundDriver/ResumeDriver dedup (ADR-0024)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS driver_idempotency (
            key             text PRIMARY KEY,
            tenant          text NOT NULL,
            expires_at      timestamptz NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_driver_idempotency_expires
            ON driver_idempotency (expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS driver_idempotency CASCADE")
    op.execute("DROP TABLE IF EXISTS a2a_idempotency CASCADE")
