"""MCP CIB Seven server — in-process tool registration (ADR-0022).

ADR-0001: CIB Seven is governance-only — BPMN, User Tasks HITL, DMN.
ADR-0022: MCP servers register tools IN-PROCESS at boot via register_tools(registry).

This server exposes CIB Seven REST API as tools via httpx:
- start_process_instance: Start a BPMN process by key
- complete_user_task: Complete a User Task (HITL)
- evaluate_dmn: Evaluate a DMN decision table
- query_process_instances: Query running process instances
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class ToolRegistry(Protocol):
    """Protocol for the ToolRegistry — PEP + audit + scrub-PHI frontier (ADR-0016)."""

    def register(
        self,
        name: str,
        action: str,
        handler: Any,
        *,
        phi_fields: list[str] | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class CibSevenConfig:
    """CIB Seven connection configuration."""

    base_url: str  # e.g., http://cibseven:8080/engine-rest
    timeout: float = 30.0
    max_retries: int = 3


class CibSevenMcpServer:
    """In-process MCP server exposing CIB Seven governance tools.

    ADR-0022: register_tools(registry) is the ONLY integration point.
    No stdio/SSE framing — tools are registered directly in the ToolRegistry
    during agent-runtime bring-up.

    Exposed tools:
    - cibseven.start_process: Start a BPMN process instance
    - cibseven.complete_task: Complete a User Task (HITL step)
    - cibseven.evaluate_dmn: Evaluate a DMN decision table
    - cibseven.query_instances: Query running process instances
    """

    def __init__(self, config: CibSevenConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=httpx.Timeout(self._config.timeout),
            )
        return self._client

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register CIB Seven tools in the ToolRegistry.

        Called by build_tool_invoker() during agent-runtime boot (ADR-0022 §4).

        Each tool is registered with:
        - name: Tool identifier (e.g., "cibseven.start_process")
        - action: The CIB Seven REST API action
        - handler: Async callable that executes the API call
        - phi_fields: Fields that may contain PHI (for scrubbing)
        """
        registry.register(
            name="cibseven.start_process",
            action="POST /process-definition/key/{key}/start",
            handler=self._start_process,
        )

        registry.register(
            name="cibseven.complete_task",
            action="POST /task/{task_id}/complete",
            handler=self._complete_task,
            phi_fields=["variables"],
        )

        registry.register(
            name="cibseven.evaluate_dmn",
            action="POST /decision-definition/key/{key}/evaluate",
            handler=self._evaluate_dmn,
        )

        registry.register(
            name="cibseven.query_instances",
            action="GET /process-instance",
            handler=self._query_instances,
        )

        logger.info(
            "cibseven tools registered",
            extra={"base_url": self._config.base_url, "tool_count": 4},
        )

    async def _start_process(
        self, *, process_key: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Start a BPMN process instance by process definition key.

        Args:
            process_key: The BPMN process definition key.
            variables: Optional initial process variables.

        Returns:
            The created process instance (id, definitionId, etc.).
        """
        client = await self._get_client()
        payload: dict[str, Any] = {}
        if variables:
            payload["variables"] = {k: {"value": v, "type": "String"} for k, v in variables.items()}

        response = await client.post(
            f"/process-definition/key/{process_key}/start",
            json=payload,
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _complete_task(
        self, *, task_id: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Complete a User Task (HITL step).

        Args:
            task_id: The CIB Seven task ID.
            variables: Optional task completion variables.

        Returns:
            Task completion result.
        """
        client = await self._get_client()
        payload: dict[str, Any] = {}
        if variables:
            payload["variables"] = {k: {"value": v, "type": "String"} for k, v in variables.items()}

        response = await client.post(
            f"/task/{task_id}/complete",
            json=payload,
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _evaluate_dmn(
        self, *, decision_key: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Evaluate a DMN decision table.

        Args:
            decision_key: The DMN decision definition key.
            variables: Input variables for the decision table.

        Returns:
            Decision result (list of rule outputs).
        """
        client = await self._get_client()
        payload: dict[str, Any] = {}
        if variables:
            payload["variables"] = {k: {"value": v, "type": "String"} for k, v in variables.items()}

        response = await client.post(
            f"/decision-definition/key/{decision_key}/evaluate",
            json=payload,
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _query_instances(
        self,
        *,
        process_key: str | None = None,
        active: bool = True,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Query running process instances.

        Args:
            process_key: Optional filter by process definition key.
            active: If True, only return active instances.
            max_results: Maximum number of results.

        Returns:
            List of process instances.
        """
        client = await self._get_client()
        params: dict[str, Any] = {"maxResults": max_results}
        if process_key:
            params["processDefinitionKey"] = process_key
        if not active:
            params["suspended"] = "true"

        response = await client.get("/process-instance", params=params)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the HTTP client if owned by this server."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
