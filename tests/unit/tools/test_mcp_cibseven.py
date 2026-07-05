"""Unit tests for maezo.tools.mcp_cibseven — CIB Seven MCP server.

ADR-0001: CIB Seven governance backbone.
ADR-0022: MCP servers register tools IN-PROCESS at boot.

GREEN phase: CibSevenMcpServer registers 4 tools in the ToolRegistry.
"""

from __future__ import annotations

import pytest

from maezo.tools.mcp_cibseven.server import CibSevenConfig, CibSevenMcpServer


class FakeToolRegistry:
    """Fake ToolRegistry for testing MCP server registration (ADR-0022)."""

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


class TestCibSevenConfig:
    """CibSevenConfig — connection configuration."""

    def test_minimal_config(self) -> None:
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        assert config.base_url == "http://cibseven:8080/engine-rest"
        assert config.timeout == 30.0
        assert config.max_retries == 3

    def test_frozen_prevents_mutation(self) -> None:
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError from dataclasses
            config.base_url = "http://other:8080"  # type: ignore[misc]


class TestCibSevenMcpServer:
    """CibSevenMcpServer — in-process MCP server for CIB Seven."""

    def test_server_initialization(self) -> None:
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        assert server._config == config

    def test_registers_four_tools(self) -> None:
        """GREEN: register_tools registers exactly 4 tools."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)

        assert registry.tool_count == 4

    def test_registered_tool_names(self) -> None:
        """GREEN: tools have the expected names."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)

        names = registry.tool_names
        assert "cibseven.start_process" in names
        assert "cibseven.complete_task" in names
        assert "cibseven.evaluate_dmn" in names
        assert "cibseven.query_instances" in names

    def test_complete_task_has_phi_fields(self) -> None:
        """complete_task handler is marked with phi_fields=['variables']."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)

        for tool in registry.tools:
            if tool["name"] == "cibseven.complete_task":
                assert tool["phi_fields"] == ["variables"]
                break
        else:
            pytest.fail("cibseven.complete_task not registered")

    def test_handlers_are_callable(self) -> None:
        """All registered handlers should be callable (async functions)."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)

        for tool in registry.tools:
            assert callable(tool["handler"]), f"{tool['name']} handler is not callable"

    def test_double_registration_does_not_raise(self) -> None:
        """Registering twice should be safe (idempotent for the caller)."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        registry = FakeToolRegistry()

        server.register_tools(registry)
        server.register_tools(registry)  # Second call

        # Each registration adds tools; caller is responsible for idempotency
        # but we don't error on double registration
        assert registry.tool_count == 8

    @pytest.mark.asyncio
    async def test_close_is_safe(self) -> None:
        """close() is safe to call even without an owned client."""
        config = CibSevenConfig(base_url="http://cibseven:8080/engine-rest")
        server = CibSevenMcpServer(config)
        await server.close()  # Should not raise
