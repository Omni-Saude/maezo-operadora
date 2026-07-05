"""MCP CIB Seven server — Camunda/CIB Seven REST integration.

Provides an async interface to the CIB Seven process engine
(Compatible with Camunda 7 REST API). Per ADR-0022, this runs
in-process alongside the agent runtime — no separate deployment.

Tools:
- start_process(process_key, variables) -> process_instance_id
- get_task(task_id) -> task_dict
- complete_task(task_id, variables) -> None
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class CibSevenSettings(BaseSettings):
    """Configuration for the CIB Seven REST API.

    Environment variables prefixed with CIBSEVEN_ (default).
    """

    model_config = {"env_prefix": "CIBSEVEN_", "extra": "ignore"}

    url: str = "http://localhost:8080/engine-rest"


class CibSevenServer:
    """MCP server for CIB Seven process engine — in-process (ADR-0022).

    Wraps the CIB Seven REST API with async httpx calls.
    Tools are registered via register_tools() for integration
    with the ToolRegistry (ADR-0016).

    Usage:
        server = CibSevenServer()
        proc_id = await server.start_process("auth_process", {"var": "val"})
        task = await server.get_task("task-123")
        await server.complete_task("task-123", {"approved": True})
    """

    def __init__(self, settings: CibSevenSettings | None = None) -> None:
        """Initialize the CIB Seven server.

        Args:
            settings: Optional CibSevenSettings; defaults to localhost:8080/engine-rest.
        """
        self._settings = settings or CibSevenSettings()
        logger.info(
            "cibseven_server_initialized",
            url=self._settings.url,
        )

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "start_process",
                "description": "Start a CIB Seven process instance by process key with optional variables.",
            },
            {
                "name": "get_task",
                "description": "Retrieve a CIB Seven user task by its ID.",
            },
            {
                "name": "complete_task",
                "description": "Complete a CIB Seven user task with optional variables.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register CIB Seven tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("start_process", self.start_process)
        registry.register("get_task", self.get_task)
        registry.register("complete_task", self.complete_task)
        logger.info("cibseven_tools_registered", count=3)

    async def start_process(
        self,
        process_key: str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Start a new process instance.

        POST /process-definition/key/{process_key}/start

        Args:
            process_key: The BPMN process definition key.
            variables: Optional map of process variables.

        Returns:
            The process instance ID.

        Raises:
            httpx.HTTPStatusError: If the CIB Seven REST API returns an error.
        """
        url = f"{self._settings.url}/process-definition/key/{process_key}/start"
        payload: dict[str, object] = {}
        if variables:
            payload["variables"] = {k: {"value": v, "type": "String"} for k, v in variables.items()}

        logger.info("cibseven_start_process", process_key=process_key, url=url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        instance_id: str = data["id"]
        logger.info("cibseven_process_started", instance_id=instance_id)
        return instance_id

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Retrieve a user task by its ID.

        GET /task/{task_id}

        Args:
            task_id: The CIB Seven task ID.

        Returns:
            Task details as a dict.

        Raises:
            httpx.HTTPStatusError: If the CIB Seven REST API returns an error.
        """
        url = f"{self._settings.url}/task/{task_id}"

        logger.debug("cibseven_get_task", task_id=task_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return data

    async def complete_task(
        self,
        task_id: str,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Complete a user task.

        POST /task/{task_id}/complete

        Args:
            task_id: The CIB Seven task ID to complete.
            variables: Optional map of process variables to set on completion.

        Raises:
            httpx.HTTPStatusError: If the CIB Seven REST API returns an error.
        """
        url = f"{self._settings.url}/task/{task_id}/complete"
        payload: dict[str, object] = {}
        if variables:
            payload["variables"] = {k: {"value": v, "type": "String"} for k, v in variables.items()}

        logger.info("cibseven_complete_task", task_id=task_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
