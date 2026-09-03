"""MCP Memory — in-process MCP server for agent memory (ADR-0002).

Exposes 2 tools (GAP-DU-01-a: both currently REFUSE fail-closed — see
`maezo.tools.mcp_memory.server` module docstring for why):
- store_episodic(agent_id, event) -> raises EpisodicMemoryUnavailableError
- recall_semantic(query) -> raises SemanticMemoryUnavailableError

The intended 3-layer memory architecture (not fully wired):
1. Working: LangGraph checkpointer (managed by runtime)
2. Episodic: PostgreSQL, table `agent_memory` (0001:64-74), partitioned by tenant_id +
   fhir_patient_id — this server has zero writers into it
3. Semantic: pgvector embeddings on `agent_memory.embedding` (0001:72) — no embedding
   provider is wired, so no similarity search is possible today
"""

from maezo.tools.mcp_memory.server import MemoryServer

__all__ = ["MemoryServer"]
