"""MCP Memory server — Agent Memory (ADR-0002; a camada semantica esta SUSPENSA).

GAP-DU-01-a (fail-closed remediation, 2026-09): this server used to FABRICATE both of its
tools. `recall_semantic` queried a `semantic_memory` table that no Alembic migration ever
creates and hard-coded `1.0 AS similarity` on every row — a constant fake score, not a real
vector cosine search, and it used `ILIKE` text matching instead of an embedding at all.
`store_episodic` inserted into an `episodic_events` table that likewise exists in no
migration. Neither table is reachable: `src/maezo/platform/migrations/versions/0001_schema_
agents.py` creates the actual episodic relation, `agent_memory` (columns `tenant_id`,
`agent_id`, `thread_id`, `fhir_patient_id`, `event_type`, `payload`), and it has ZERO writers
anywhere in `src/` (grep-verified). Both tools now REFUSE fail-closed with a typed, documented
error instead of raising a bare `UndefinedTableError` (the pre-existing behaviour if this dead
code were ever actually invoked) or, worse, returning a fabricated similarity score. This
module has 0 production consumers today (no `register_tools` call site outside its own tests) —
the refusal is therefore a documentation/safety fix, not a behavioural regression for any live
caller.

GAP-DU-01-b (owner decision R-005, 2026-09-04) SETTLED the strategic choice this module used to
leave open, and settled it AGAINST building the semantic path: `0009_drop_pgvector` dropped
`agent_memory.embedding vector(1536)` and the `vector` extension outright, the Aurora parameter
group lost its `pgvector` token and `docker-compose.yml` moved to the plain `postgres:16` image.
So `recall_semantic`'s refusal is no longer "not wired yet" — there is no column to wire it to,
and there will not be one until ADR-0002 §3 is un-suspended by the owner (emenda DRAFT em
`docs/adr/0047-emenda-adr0002-secao3-camada-semantica-suspensa.md`). What remains open is
GAP-DU-01-a's other half — whether `store_episodic` grows the `tenant_id`/`thread_id` arguments
that would let it write `agent_memory` honestly — and that is a SEPARATE gap, deliberately not
decided here.

**Este modulo nao emite SQL nenhum.** Nem antes nem depois de 0009: as duas ferramentas recusam
antes de precisar de conexao, e a remocao da coluna nao muda uma linha de comportamento — o que
ela muda e a VERDADE das mensagens de recusa, atualizadas abaixo. `tests/unit/tools/
test_mcp_memory.py` prova as duas metades (comportamento inalterado + nenhuma referencia a
`embedding`/`vector` na superficie SQL deste modulo).

Per ADR-0002 (`docs/adr/0002-agent-state-three-layers.md`):
- Working memory: managed by LangGraph checkpointer (not exposed here)
- Episodic memory: event log in PostgreSQL, partitioned by tenant + fhir_patient_id
- Semantic memory: §3 SUSPENSO — desenhado, nunca consumido, removido por 0009 (ADR-0047 DRAFT)

Tools (surface unchanged by the GAP-DU-01-a fix — both now refuse rather than fabricate):
- store_episodic(agent_id, event) -> raises EpisodicMemoryUnavailableError
- recall_semantic(query) -> raises SemanticMemoryUnavailableError
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)

#: `store_episodic` refusal reason: the donor schema (`episodic_events`) this handler was
#: written against does not exist in any Alembic migration. The table Alembic DOES create for
#: this layer, `agent_memory` (0001:64-74), requires `tenant_id` and `thread_id` (both `text
#: NOT NULL`, 0001:66,68) — neither is part of this tool's `(agent_id, event)` signature, so
#: there is no honest value to bind them to. Writing `NULL`/a placeholder would silently
#: violate the NOT NULL constraint's intent (multi-tenant isolation, ADR-0002 "particionadas
#: por tenant") rather than honor it. Closing this gap means either widening the tool's
#: signature to carry `tenant_id`/`thread_id` (and wiring a real per-tenant `search_path` pool,
#: DL-0017) or retiring the table — GAP-DU-01-b, an owner decision.
REASON_EPISODIC_SCHEMA_DRIFT: Final[str] = "episodic_schema_drift"

#: `recall_semantic` refusal reason: there is no embedding path, and since GAP-DU-01-b there is
#: no column for one either. The donor code queried `semantic_memory` (a table no migration
#: creates) with `ILIKE` text matching and returned a hard-coded `1.0 AS similarity` on every row
#: — never a real cosine distance. The column that USED to exist, `agent_memory.embedding
#: vector(1536)`, was dropped together with the `vector` extension by
#: `0009_drop_pgvector` (owner decision R-005): it never had a writer, and ADR-0002 §3 is now
#: SUSPENDED pending a consumer (ADR-0047, DRAFT). The reason CODE is deliberately unchanged —
#: it is a stable, logged vocabulary term, and "not wired" remains exactly what is true.
#: Returning any similarity value — real-looking or not — without a real embedding would still be
#: fabrication.
REASON_SEMANTIC_SEARCH_NOT_WIRED: Final[str] = "semantic_search_not_wired"


class EpisodicMemoryUnavailableError(RuntimeError):
    """Fail-closed sentinel: no migration creates a schema `store_episodic` can honestly write.

    Raised by `MemoryServer.store_episodic` for every call that passes its own input
    validation — there is no fallback table and no partial/best-effort write. `reason` is
    `REASON_EPISODIC_SCHEMA_DRIFT` (the only reason defined today); `detail` is a
    human-readable, file-path-qualified explanation suitable for an operator-facing log field.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"episodic memory unavailable ({reason}): {detail}")


class SemanticMemoryUnavailableError(RuntimeError):
    """Fail-closed sentinel: no embedding column and no vector search path exist (DU-01-b).

    Raised by `MemoryServer.recall_semantic` for every call — there is no fallback text
    search and no constant/placeholder similarity score. `reason` is
    `REASON_SEMANTIC_SEARCH_NOT_WIRED` (the only reason defined today); `detail` is a
    human-readable, file-path-qualified explanation suitable for an operator-facing log field.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"semantic memory unavailable ({reason}): {detail}")


class MemorySettings(BaseSettings):
    """Configuration for the agent memory store.

    Environment variables prefixed with MEMORY_ (default).

    NOTE (GAP-DU-01-a): `database_url` is currently UNUSED at runtime — neither
    `store_episodic` nor `recall_semantic` opens a connection any more (both refuse
    fail-closed before needing one; the bare, hygiene-defective asyncpg pool this module used
    to open unconditionally — no `search_path` pin per DL-0017, and a default database name
    (`maezo_memory`) that matches no other component's `DATABASE_URL` — has been removed
    rather than "fixed" for a code path nothing reaches). It is kept only as documentation of
    the settings shape a future honest implementation would need; GAP-DU-01-a decides whether
    that implementation reuses the app's own `DATABASE_URL` (the house pattern — see
    `src/maezo/a2a/idempotency.py`, `src/maezo/a2a/outbox.py`) or a dedicated DSN. (GAP-DU-01-b
    settled only the SEMANTIC half — it removed the layer; it did not choose a DSN.)
    """

    model_config = {"env_prefix": "MEMORY_", "extra": "ignore"}

    database_url: str = "postgresql://maezo:maezo@localhost:5432/maezo_memory"
    #: Dimensao que a coluna removida usava. Mantida como DOCUMENTACAO do formato que uma futura
    #: implementacao honesta precisaria escolher, nao como referencia a um schema vivo — a coluna
    #: `agent_memory.embedding vector(1536)` foi removida por `0009_drop_pgvector` (DU-01-b).
    memory_embedding_dim: int = 1536


class MemoryServer:
    """MCP server for agent memory — in-process (ADR-0022).

    Both tools currently REFUSE fail-closed (GAP-DU-01-a) rather than fabricate a result:
    neither table their original implementation targeted (`episodic_events`,
    `semantic_memory`) exists in any migration, and `recall_semantic` additionally returned a
    constant `similarity: 1.0` regardless of query. See `EpisodicMemoryUnavailableError` /
    `SemanticMemoryUnavailableError` for the exact, cited reason each refuses.

    Usage:
        server = MemoryServer()
        await server.store_episodic("agent-1", {"type": "decision", ...})  # raises
        await server.recall_semantic("autorizacao similar")                # raises
    """

    def __init__(self, settings: MemorySettings | None = None) -> None:
        """Initialize the Memory server.

        Args:
            settings: Optional MemorySettings; defaults to localhost:5432/maezo_memory.
                Currently informational only — see `MemorySettings` docstring.
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

    async def store_episodic(
        self,
        agent_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Refuse to store an episodic memory event (GAP-DU-01-a — see module docstring).

        Args:
            agent_id: The agent identifier (e.g., "agent-auth-1").
            event: The event payload as a dict with at least a "type" field.

        Returns:
            Never returns — always raises.

        Raises:
            ValueError: If agent_id is empty (input validation, checked before the refusal).
            EpisodicMemoryUnavailableError: Always, for any valid input — reason
                `REASON_EPISODIC_SCHEMA_DRIFT`. No migration creates a table this call can
                honestly write to; see `REASON_EPISODIC_SCHEMA_DRIFT`'s docstring.
        """
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must not be empty")

        logger.warning(
            "memory_store_episodic_refused",
            agent_id=agent_id,
            event_type=event.get("type", "unknown"),
            reason=REASON_EPISODIC_SCHEMA_DRIFT,
        )
        raise EpisodicMemoryUnavailableError(
            REASON_EPISODIC_SCHEMA_DRIFT,
            "store_episodic: no migration creates a table this call can honestly write to. "
            "The real episodic relation is `agent_memory` "
            "(src/maezo/platform/migrations/versions/0001_schema_agents.py), which "
            "requires tenant_id and thread_id (both `text NOT NULL`) — neither is "
            "part of this tool's (agent_id, event) signature. Refusing rather than inventing a "
            "placeholder tenant/thread or writing to a table (`episodic_events`) that does not "
            "exist. Widening this signature (or retiring this tool) is GAP-DU-01-a; GAP-DU-01-b "
            "removed the SEMANTIC half only, and did not decide this one.",
        )

    async def recall_semantic(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Refuse to recall semantically similar memories (GAP-DU-01-a — see module docstring).

        Args:
            query: The natural language query string.
            limit: Maximum number of results to return (default: 10). Unused — kept in the
                signature so a future real implementation is a drop-in.

        Returns:
            Never returns — always raises.

        Raises:
            SemanticMemoryUnavailableError: Always — reason `REASON_SEMANTIC_SEARCH_NOT_WIRED`.
                There is no embedding provider wired into this module, no vector query is
                issued, and since `0009_drop_pgvector` there is no embedding column to query;
                no code path in this method returns a `similarity` value, real or fabricated.
                See `REASON_SEMANTIC_SEARCH_NOT_WIRED`'s docstring.
        """
        logger.warning(
            "memory_recall_semantic_refused",
            query=query[:100],
            reason=REASON_SEMANTIC_SEARCH_NOT_WIRED,
        )
        raise SemanticMemoryUnavailableError(
            REASON_SEMANTIC_SEARCH_NOT_WIRED,
            "recall_semantic: no embedding column and no vector search path exist. This tool "
            "used to ILIKE-match a nonexistent `semantic_memory` table and return a hard-coded "
            "`similarity: 1.0` on every row; that fabrication has been removed. The column that "
            "once backed this layer, agent_memory.embedding vector(1536), never had a writer and "
            "was dropped together with the `vector` extension by "
            "src/maezo/platform/migrations/versions/0009_drop_pgvector.py (GAP-DU-01-b, owner "
            "decision R-005). ADR-0002 section 3 is SUSPENDED pending a consumer — see the DRAFT "
            "amendment in docs/adr/0047-emenda-adr0002-secao3-camada-semantica-suspensa.md. "
            "Un-suspending it is an owner act, not a code change.",
        )


__all__ = [
    "EpisodicMemoryUnavailableError",
    "MemoryServer",
    "MemorySettings",
    "REASON_EPISODIC_SCHEMA_DRIFT",
    "REASON_SEMANTIC_SEARCH_NOT_WIRED",
    "SemanticMemoryUnavailableError",
]
