"""Integration tests for maezo.tools.mcp_cibseven — against real CIB Seven.

ADR-0001: CIB Seven governance backbone.
ADR-0022: MCP servers register tools IN-PROCESS.

Requires: docker-compose profile core running (cibseven on 8080).
Run: make dev-stack && uv run pytest tests/integration -m integration
"""

from __future__ import annotations

import os
import socket

import pytest

from maezo.tools.mcp_cibseven.server import CibSevenConfig, CibSevenMcpServer


def _check_cibseven() -> bool:
    """Check if CIB Seven is available for integration tests."""
    host = os.environ.get("CIBSEVEN_HOST", "localhost")
    port = int(os.environ.get("CIBSEVEN_PORT", "8080"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


requires_cibseven = pytest.mark.skipif(
    not _check_cibseven(),
    reason="CIB Seven not available — start with: make dev-stack",
)


class FakeToolRegistry:
    """Fake ToolRegistry for testing MCP server registration."""

    def __init__(self) -> None:
        self._tools: list[dict] = []

    def register(
        self,
        name: str,
        action: str,
        handler,
        *,
        phi_fields: list[str] | None = None,
    ) -> None:
        self._tools.append(
            {
                "name": name,
                "action": action,
                "handler": handler,
                "phi_fields": phi_fields or [],
            }
        )

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tool_names(self) -> list[str]:
        return [t["name"] for t in self._tools]


@pytest.mark.integration
class TestCibSevenIntegration:
    """Integration tests for CibSevenMcpServer with real CIB Seven."""

    @requires_cibseven
    @pytest.mark.asyncio
    async def test_query_instances_returns_data(self) -> None:
        """query_instances should return a list from the CIB Seven REST API."""
        config = CibSevenConfig(base_url="http://localhost:8080/engine-rest")
        server = CibSevenMcpServer(config)

        try:
            result = await server._query_instances(max_results=10)
            assert isinstance(result, list)
        finally:
            await server.close()

    @requires_cibseven
    @pytest.mark.asyncio
    async def test_register_and_call_tool(self) -> None:
        """Registered tool handlers should be callable against real CIB Seven."""
        config = CibSevenConfig(base_url="http://localhost:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)

        # Verify tools are registered
        assert registry.tool_count == 4

        try:
            # Query instances — should work against the CIB Seven REST API
            query_tool = None
            for tool in registry.tools:
                if tool["name"] == "cibseven.query_instances":
                    query_tool = tool
                    break

            assert query_tool is not None
            result = await query_tool["handler"](max_results=5)
            assert isinstance(result, list)
        finally:
            await server.close()
