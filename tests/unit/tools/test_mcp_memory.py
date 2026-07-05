"""Unit tests for maezo.tools.mcp_memory (ADR-0002, ADR-0022)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Memory Server: store_episodic, recall_semantic
# ---------------------------------------------------------------------------


def test_memory_tools_registered() -> None:
    """Memory server should expose exactly 2 tools: store_episodic, recall_semantic."""
    from maezo.tools.mcp_memory import MemoryServer

    server = MemoryServer()

    tools = server.list_tools()

    assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}: {tools}"
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"store_episodic", "recall_semantic"}


def test_memory_settings_defaults() -> None:
    """MemorySettings should default to localhost:5432 for PostgreSQL/pgvector."""
    from maezo.tools.mcp_memory.server import MemorySettings

    settings = MemorySettings()

    assert settings.database_url is not None
    assert "postgresql" in settings.database_url
    assert settings.memory_embedding_dim == 1536


@pytest.mark.asyncio
async def test_memory_store_episodic() -> None:
    """store_episodic(agent_id, event) should store the event and return an event_id."""
    from maezo.tools.mcp_memory.server import MemoryServer

    server = MemoryServer()

    # Mock the internal connection pool and connection
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    # Patch _get_pool directly (London School: mock the collaborator)
    with patch.object(server, "_get_pool", AsyncMock(return_value=mock_pool)):
        result = await server.store_episodic(
            agent_id="agent-auth-1",
            event={
                "type": "decision",
                "action": "auto_approval",
                "payload": {"process_id": "proc-123"},
            },
        )

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "event_id" in result
    assert result["agent_id"] == "agent-auth-1"
    assert result["event"]["type"] == "decision"


@pytest.mark.asyncio
async def test_memory_recall_semantic() -> None:
    """recall_semantic(query) should return semantically similar memories."""
    from maezo.tools.mcp_memory.server import MemoryServer

    server = MemoryServer()

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "content": "Previous auto-approval for similar procedure",
                "metadata": "{}",
                "similarity": 0.95,
            },
            {
                "id": 2,
                "content": "Related DUT evaluation",
                "metadata": "{}",
                "similarity": 0.82,
            },
        ]
    )

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    # Patch _get_pool directly (London School: mock the collaborator)
    with patch.object(server, "_get_pool", AsyncMock(return_value=mock_pool)):
        results = await server.recall_semantic("autorizacao procedimento similar")

    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) == 2
    assert results[0]["similarity"] == 0.95
    assert results[0]["content"] == "Previous auto-approval for similar procedure"


@pytest.mark.asyncio
async def test_memory_store_episodic_validation() -> None:
    """store_episodic should raise ValueError for empty agent_id."""
    from maezo.tools.mcp_memory.server import MemoryServer

    server = MemoryServer()

    with pytest.raises(ValueError, match="agent_id"):
        await server.store_episodic("", {"type": "test"})
