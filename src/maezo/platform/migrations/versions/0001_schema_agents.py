"""Schema agents — agent_checkpoints, agent_memory, pgvector extension.

ADR-0002: 3-layer agent state.
DL-0017: pgvector extension in public (type/operator is DB-global, NOT per-tenant).
The search_path "tenant, public" ensures the pgvector type is visible while tables
land in the tenant schema.

Revision ID: 0001
Revises:
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
    # pgvector extension — lives in 'public' (DL-0017).
    # Type/operator must be DB-global; per-tenant schemas can reference it
    # because search_path includes 'public'.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")

    # LangGraph checkpointer table (ADR-0002, working layer).
    # Schema matches langgraph-checkpoint-postgres expectations.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            thread_id         text NOT NULL,
            checkpoint_ns     text NOT NULL DEFAULT '',
            checkpoint_id     text NOT NULL,
            parent_checkpoint_id text,
            type              text,
            checkpoint        jsonb NOT NULL,
            metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)

    # LangGraph checkpoint writes (channel values at each checkpoint).
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_checkpoint_writes (
            thread_id         text NOT NULL,
            checkpoint_ns     text NOT NULL DEFAULT '',
            checkpoint_id     text NOT NULL,
            task_id           text NOT NULL,
            idx               integer NOT NULL,
            channel           text NOT NULL,
            type              text,
            value             jsonb,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)

    # Agent episodic memory (ADR-0002, episodic layer).
    # Partitioned by fhir_patient_id for LGPD erasure cascade.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         text NOT NULL,
            agent_id          text NOT NULL,
            thread_id         text NOT NULL,
            fhir_patient_id   text,
            event_type        text NOT NULL,
            payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
            embedding         vector(1536),
            created_at        timestamptz NOT NULL DEFAULT now()
        )
    """)

    # Indexes for agent_memory
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_memory_tenant_agent
            ON agent_memory (tenant_id, agent_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_memory_fhir_patient
            ON agent_memory (fhir_patient_id)
            WHERE fhir_patient_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_memory_thread
            ON agent_memory (thread_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_memory_created_at
            ON agent_memory (created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_memory CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_checkpoint_writes CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_checkpoints CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector CASCADE")
