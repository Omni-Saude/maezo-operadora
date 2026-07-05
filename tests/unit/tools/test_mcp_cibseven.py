"""Unit tests for maezo.tools.mcp_cibseven (ADR-0022)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_mcp_cibseven_tools_registered() -> None:
    """MCP CIB Seven server should expose exactly 3 tools: start_process, get_task, complete_task."""
    from maezo.tools.mcp_cibseven import CibSevenServer

    server = CibSevenServer()

    tools = server.list_tools()

    assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}: {tools}"

    tool_names = {t["name"] for t in tools}
    assert tool_names == {"start_process", "get_task", "complete_task"}


def test_mcp_cibseven_settings_defaults() -> None:
    """CibSevenSettings should default to http://localhost:8080/engine-rest."""
    from maezo.tools.mcp_cibseven.server import CibSevenSettings

    settings = CibSevenSettings()

    assert settings.url == "http://localhost:8080/engine-rest"


@pytest.mark.asyncio
async def test_start_process_makes_correct_http_call() -> None:
    """start_process should POST to the correct CIB Seven REST endpoint."""
    from maezo.tools.mcp_cibseven.server import CibSevenServer

    server = CibSevenServer()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id": "proc-123", "definitionId": "def-1"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.start_process("my_process", {"var1": "val1"})

    assert result == "proc-123"
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert "/process-definition/key/my_process/start" in call_args[0][0]


@pytest.mark.asyncio
async def test_get_task_makes_correct_http_call() -> None:
    """get_task should GET from the correct CIB Seven REST endpoint."""
    from maezo.tools.mcp_cibseven.server import CibSevenServer

    server = CibSevenServer()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id": "task-1", "name": "Review"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.get_task("task-1")

    assert result == {"id": "task-1", "name": "Review"}
    mock_client.get.assert_awaited_once()
    call_args = mock_client.get.call_args
    assert "/task/task-1" in call_args[0][0]


@pytest.mark.asyncio
async def test_complete_task_makes_correct_http_call() -> None:
    """complete_task should POST to the correct CIB Seven REST endpoint."""
    from maezo.tools.mcp_cibseven.server import CibSevenServer

    server = CibSevenServer()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.complete_task("task-1", {"approved": True})

    assert result is None
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert "/task/task-1/complete" in call_args[0][0]
