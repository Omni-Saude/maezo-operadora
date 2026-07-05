"""MCP FHIR — in-process MCP server for HAPI FHIR R4 (ADR-0022).

Exposes 2 tools:
- read_resource(resource_type, id) -> resource_dict
- search_resources(resource_type, params) -> bundle_dict

Uses httpx.AsyncClient for REST calls to HAPI FHIR.
Implements ADR-0022 (MCP in-process boot) and ADR-0006 (PHI two zones).
"""

from maezo.tools.mcp_fhir.server import FhirServer

__all__ = ["FhirServer"]
