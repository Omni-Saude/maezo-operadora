"""MCP FHIR server — FHIR R4 Client for HAPI FHIR.

Provides read_resource(resource_type, id) and search_resources(resource_type, params).
Uses httpx.AsyncClient for async HTTP calls to the HAPI FHIR server.

Per ADR-0006, FHIR resources containing PHI must only be read through
the ToolRegistry gateway (PEP + audit + scrub-PHI).

Tools:
- read_resource(resource_type, id) -> resource_dict
- search_resources(resource_type, params) -> bundle_dict
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class FhirSettings(BaseSettings):
    """Configuration for the HAPI FHIR client.

    Environment variables prefixed with FHIR_ (default).
    """

    model_config = {"env_prefix": "FHIR_", "extra": "ignore"}

    base_url: str = "http://localhost:8081/fhir"


class FhirServer:
    """MCP server for FHIR R4 — in-process (ADR-0022).

    Wraps the HAPI FHIR REST API with async httpx calls.
    Tools are registered via register_tools() for integration
    with the ToolRegistry (ADR-0016).

    Usage:
        server = FhirServer()
        patient = await server.read_resource("Patient", "123")
        results = await server.search_resources("Patient", {"name": "Teste"})
    """

    def __init__(self, settings: FhirSettings | None = None) -> None:
        """Initialize the FHIR server.

        Args:
            settings: Optional FhirSettings; defaults to localhost:8081/fhir.
        """
        self._settings = settings or FhirSettings()
        logger.info(
            "fhir_server_initialized",
            base_url=self._settings.base_url,
        )

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "read_resource",
                "description": "Read a FHIR R4 resource by type and ID.",
            },
            {
                "name": "search_resources",
                "description": "Search FHIR R4 resources with query parameters.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register FHIR tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("read_resource", self.read_resource)
        registry.register("search_resources", self.search_resources)
        logger.info("fhir_tools_registered", count=2)

    async def read_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        """Read a FHIR R4 resource by type and ID.

        GET /fhir/{resource_type}/{resource_id}

        Args:
            resource_type: The FHIR resource type (e.g., 'Patient', 'Observation').
            resource_id: The logical ID of the resource.

        Returns:
            The FHIR resource as a dict.

        Raises:
            httpx.HTTPStatusError: If the HAPI FHIR server returns an error.
        """
        url = f"{self._settings.base_url}/{resource_type}/{resource_id}"

        logger.info("fhir_read_resource", resource_type=resource_type, resource_id=resource_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return data

    async def search_resources(
        self,
        resource_type: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Search FHIR R4 resources with query parameters.

        GET /fhir/{resource_type}?param1=value1&param2=value2

        Args:
            resource_type: The FHIR resource type to search.
            params: Optional query parameters (e.g., {'name': 'Teste'}).

        Returns:
            A FHIR Bundle as a dict.

        Raises:
            httpx.HTTPStatusError: If the HAPI FHIR server returns an error.
        """
        url = f"{self._settings.base_url}/{resource_type}"

        logger.info("fhir_search_resources", resource_type=resource_type, params=params)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params or {})
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return data
