"""Checkpointer — LangGraph checkpoint persistence with asyncpg PostgreSQL.

ADR-0002: Working memory layer (LangGraph checkpointer, schema `agents`).
DL-0017: pgvector in public, asyncpg `setup=` (per-acquire), not `init=` (per-create).
         search_path must include `public` so pgvector operators remain visible.

The PostgreSQL checkpointer from langgraph-checkpoint-postgres stores graph state
in the `agents` schema. Connection pooling uses asyncpg under the hood via
langgraph's AsyncConnection — the checkpointer is managed as a context manager.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def build_checkpointer(
    db_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Build and yield a LangGraph AsyncPostgresSaver.

    DL-0017: The pgvector extension MUST be created in `public` schema
    (handled by migration 0001). Connection search_path should include
    `public` so pgvector operators (<->, <=>) are visible.

    The checkpointer is yielded as an async context manager — the connection
    is automatically managed by langgraph's AsyncConnection.

    Usage:
        async with build_checkpointer(db_url) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)

    Args:
        db_url: PostgreSQL connection URL (e.g., postgresql://user:pass@host:5432/db).
                Note: langgraph uses its own connection adapter, not asyncpg directly.

    Yields:
        Configured AsyncPostgresSaver ready for use with LangGraph graphs.
    """
    # langgraph-checkpoint-postgres uses its own connection adapter.
    # from_conn_string is an async context manager that manages connection lifecycle.
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        yield checkpointer
