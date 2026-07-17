"""Generic `FhirReader` adapter over `maezo.tools.mcp_fhir.server.FhirServer`.

v2's `FhirServer` is a generic HAPI FHIR R4 client (`read_resource(resource_type, id)`/
`search_resources(resource_type, params)`) — NOT the v1 donor's dedicated `read_patient`/
`search_coverage` methods, and not wired through any PEP/ToolRegistry gateway yet (T2.4 gap,
disclosed in `graph.py`'s module docstring). This adapter is the thin shim that lets Rafael's
`gather` node consume the generic client through the `graph.FhirReader` Protocol shape.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_fhir.server import FhirServer


class FhirServerReader:
    """Adapts `FhirServer`'s generic resource methods to the `graph.FhirReader` Protocol."""

    def __init__(self, server: FhirServer) -> None:
        self._server = server

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        return await self._server.read_resource("Patient", patient_id)

    async def search_coverage(self, patient_id: str) -> Any:
        bundle = await self._server.search_resources("Coverage", {"beneficiary": f"Patient/{patient_id}"})
        return bundle.get("entry", [])
