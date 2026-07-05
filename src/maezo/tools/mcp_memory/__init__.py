"""MCP Memory — in-process MCP server for agent memory (ADR-0002).

Exposes 2 tools:
- store_episodic(agent_id, event) -> event_dict
- recall_semantic(query) -> list[memory_dict]

Implements the 3-layer memory architecture:
1. Working: LangGraph checkpointer (managed by runtime)
2. Episodic: PostgreSQL (event log partitioned by agent_id)
3. Semantic: pgvector embeddings (queryable by similarity)
"""

from maezo.tools.mcp_memory.server import MemoryServer

__all__ = ["MemoryServer"]
