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
    #
    # DU-01-b (2026-09-04, decisao do dono R-005): pgvector deixou de ser dependencia da
    # plataforma — `0009_drop_pgvector` remove a coluna `embedding` e a extensao, e a imagem do
    # compose passou de `pgvector/pgvector:pg16` para `postgres:16`. Sem a guarda abaixo o PRIMEIRO
    # passo de qualquer `alembic upgrade head` em ambiente novo morre aqui com
    # `FeatureNotSupportedError: extension "vector" is not available`, e NENHUMA migration
    # posterior pode consertar isso: a falha acontece DENTRO da 0001, antes de a 0009 existir para
    # o runner. Esta e a unica razao pela qual esta migration ja aplicada e editada in-place
    # (mesma classe de excecao ao forward-only do ADR-0011 registrada em DL-0017).
    #
    # A guarda e convergente, nao condicional-de-comportamento: num servidor COM pgvector o estado
    # final da 0001 e o mesmo de antes (extensao + coluna), e a 0009 remove os dois; num servidor
    # SEM pgvector nada e criado e os DROPs da 0009 sao no-op. Os dois caminhos chegam ao MESMO
    # head. Bancos ja migrados nao sao tocados — a 0001 nao roda de novo neles.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
            END IF;
        END
        $$
    """)

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
            created_at        timestamptz NOT NULL DEFAULT now()
        )
    """)

    # `embedding vector(1536)` era declarada INLINE no corpo de `agent_memory`, logo acima. Um tipo
    # de uma extensao ausente nao e ignoravel: a criacao da tabela inteira falharia com
    # `type "vector" does not exist`. Por isso a coluna saiu do corpo e volta aqui, condicionada a
    # extensao ter sido criada —
    # mesma guarda convergente, mesma razao (DU-01-b). A ordem da coluna na tabela muda (passa a
    # ser a ultima) e isso e irrelevante: `0009_drop_pgvector` a remove no passo seguinte, e nada
    # neste repositorio le `agent_memory` por posicao ordinal.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                EXECUTE 'ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS embedding public.vector(1536)';
            END IF;
        END
        $$
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
