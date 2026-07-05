"""Create agents schema + pgvector extension

ADR-0002: Working memory layer (LangGraph checkpointer) lives in schema `agents`.
DL-0017: pgvector extension MUST be created in `public` (vector type is DB-global).
         search_path must include `public` so pgvector operators (<->, <=>) are visible.

Revision ID: 0001
Revises: None
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DL-0017: pgvector MUST be in public — the vector type and operators are DB-global.
    # Creating in public ensures asyncpg register_vector and <->/<=> operators work
    # regardless of tenant search_path.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")

    # ADR-0002: Working memory layer — LangGraph checkpointer + agent state.
    op.execute("CREATE SCHEMA IF NOT EXISTS agents")

    # LangGraph checkpoint tables (langgraph-checkpoint-postgres expects these).
    # We create them explicitly so Alembic owns the schema.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agents.checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint BYTEA NOT NULL,
            metadata BYTEA,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS agents.checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            value BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS agents.checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agents.checkpoint_blobs")
    op.execute("DROP TABLE IF EXISTS agents.checkpoint_writes")
    op.execute("DROP TABLE IF EXISTS agents.checkpoints")
    op.execute("DROP SCHEMA IF EXISTS agents")
    op.execute("DROP EXTENSION IF EXISTS vector")
