"""MCP Memory — in-process MCP server for agent memory (ADR-0002).

Exposes 2 tools (GAP-DU-01-a: both currently REFUSE fail-closed — see
`maezo.tools.mcp_memory.server` module docstring for why):
- store_episodic(agent_id, event) -> raises EpisodicMemoryUnavailableError
- recall_semantic(query) -> raises SemanticMemoryUnavailableError

The intended 3-layer memory architecture (not fully wired):
1. Working: LangGraph checkpointer (managed by runtime)
2. Episodic: PostgreSQL, table `agent_memory` (0001:64-74), partitioned by tenant_id +
   fhir_patient_id — this server has zero writers into it
3. Semantic: SUSPENSA. `agent_memory.embedding vector(1536)` e a extensao `vector` foram
   removidas por `0009_drop_pgvector` (GAP-DU-01-b, decisao do dono R-005): a camada foi
   desenhada e nunca consumida. ADR-0002 §3 fica suspenso ate existir consumidor — nao negado;
   emenda DRAFT em `docs/adr/0042-emenda-adr0002-secao3-camada-semantica-suspensa.md`
"""

from maezo.tools.mcp_memory.server import MemoryServer

__all__ = ["MemoryServer"]
