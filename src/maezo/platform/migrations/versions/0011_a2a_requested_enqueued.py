"""PLAN-W3-A2A: atomic admission marker, not a delivery acknowledgement.

No backfill: a legacy claim cannot establish that a requested fact existed.
No FK or retention changes. A valid replay may recognize the old outbox bytes
or enqueue the missing intent in the admission transaction (ADR-0039).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE a2a_idempotency ADD COLUMN requested_enqueued_at timestamptz")
    op.execute(
        "COMMENT ON COLUMN a2a_idempotency.requested_enqueued_at IS "
        "'Atomic requested enqueue or legacy recognition time; not broker delivery'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE a2a_idempotency DROP COLUMN requested_enqueued_at")
