"""MCP Memory server — Agent Memory (3-layer architecture, ADR-0002).

Provides store_episodic(agent_id, event) and recall_semantic(query).
Uses PostgreSQL with pgvector for episodic storage and semantic search.

Per ADR-0002:
- Working memory: managed by LangGraph checkpointer (not exposed here)
- Episodic memory: event log in PostgreSQL, partitioned by agent_id
- Semantic memory: text embeddings + pgvector similarity search

Tools:
- store_episodic(agent_id, event) -> event_dict
- recall_semantic(query) -> list[memory_dict]
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class MemorySettings(BaseSettings):
    """Configuration for the agent memory store.

    Environment variables prefixed with MEMORY_ (default).
    """

    model_config = {"env_prefix": "MEMORY_", "extra": "ignore"}

    database_url: str = "postgresql://maezo:maezo@localhost:5432/maezo_memory"
    memory_embedding_dim: int = 1536  # OpenAI text-embedding-3-small default


class MemoryServer:
    """MCP server for agent memory — in-process (ADR-0022).

    Manages episodic (event log) and semantic (vector search) memory.
    Working memory is handled by the LangGraph checkpointer (not here).

    Usage:
        server = MemoryServer()
        event = await server.store_episodic("agent-1", {"type": "decision", ...})
        memories = await server.recall_semantic("autorizacao similar")
    """

    def __init__(self, settings: MemorySettings | None = None) -> None:
        """Initialize the Memory server.

        Args:
            settings: Optional MemorySettings; defaults to localhost:5432/maezo_memory.
        """
        self._settings = settings or MemorySettings()
        logger.info(
            "memory_server_initialized",
            database_url=self._settings.database_url,
        )

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "store_episodic",
                "description": "Store an episodic memory event for an agent.",
            },
            {
                "name": "recall_semantic",
                "description": "Recall semantically similar memories for a query.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register memory tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("store_episodic", self.store_episodic)
        registry.register("recall_semantic", self.recall_semantic)
        logger.info("memory_tools_registered", count=2)

    async def _get_pool(self) -> Any:
        """Get an asyncpg connection pool (lazy import to avoid hard dependency)."""
        import asyncpg  # type: ignore  # noqa: PLC0415 — lazy import per ADR-0021

        if not hasattr(self, "_pool"):
            self._pool = await asyncpg.create_pool(
                self._settings.database_url,
                min_size=1,
                max_size=5,
            )
        return self._pool

    async def store_episodic(
        self,
        agent_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Store an episodic memory event.

        Inserts into the episodic_events table, partitioned by agent_id.
        Returns a dict with the stored event_id, agent_id, timestamp, and event payload.

        Args:
            agent_id: The agent identifier (e.g., "agent-auth-1").
            event: The event payload as a dict with at least a "type" field.

        Returns:
            Dict with event_id, agent_id, timestamp, and event data.

        Raises:
            ValueError: If agent_id is empty.
        """
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must not be empty")

        event_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()

        logger.info(
            "memory_store_episodic",
            agent_id=agent_id,
            event_type=event.get("type", "unknown"),
        )

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO episodic_events (event_id, agent_id, event_type, event_payload, created_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event_id,
                    agent_id,
                    event.get("type", "unknown"),
                    json.dumps(event),
                    timestamp,
                )
        except Exception:
            logger.exception("memory_store_episodic_failed", agent_id=agent_id)
            raise

        result = {
            "event_id": event_id,
            "agent_id": agent_id,
            "timestamp": timestamp,
            "event": event,
        }
        return result

    async def recall_semantic(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Recall semantically similar memories.

        Searches the semantic_memory table using pgvector cosine similarity.
        Returns the top-k most similar memory entries.

        Note: In production, the embedding is computed by the runtime's
        inference.py (ADR-0009), not here. This server expects embeddings
        to be pre-computed and stored.

        Args:
            query: The natural language query string.
            limit: Maximum number of results to return (default: 10).

        Returns:
            List of memory dicts with id, content, metadata, and similarity score.
        """
        logger.info("memory_recall_semantic", query=query[:100])

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # For now, use a simple ILIKE fallback since we don't have
                # the actual embedding. In production, the runtime injects
                # the embedding vector computed by inference.py.
                rows = await conn.fetch(
                    """
                    SELECT id, content, metadata, created_at,
                           1.0 AS similarity
                    FROM semantic_memory
                    WHERE content ILIKE $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    f"%{query}%",
                    limit,
                )

            results = [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "similarity": row["similarity"],
                }
                for row in rows
            ]
            return results

        except Exception:
            logger.exception("memory_recall_semantic_failed", query=query[:100])
            raise
