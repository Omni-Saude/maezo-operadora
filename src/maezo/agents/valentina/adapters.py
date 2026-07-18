"""Generic `PatientSummaryReader` adapter over `maezo.tools.mcp_fhir.server.FhirServer`.

v2's `FhirServer` is a generic HAPI FHIR R4 client (`read_resource(resource_type, id)`/
`search_resources(resource_type, params)`) — NOT the v1 donor's dedicated
`mcp-fhir.read_patient_summary` tool (declared in `spec/agents/valentina/agent.yaml`'s `tools:`
list), and not wired through any PEP/ToolRegistry gateway yet (T2.4 gap, disclosed in
`graph.py`'s module docstring — same labeled boundary as `agents/rafael/adapters.py`). This
adapter is the thin shim that lets Valentina's `gather` node consume the generic client through
the `graph.PatientSummaryReader` Protocol shape: a single `Patient` resource read stands in for
the donor's richer "patient summary" composite (a real multi-resource summary is a follow-up,
not built here — `gather` is best-effort, runs ONLY after the consent chokepoint, and never
blocks routing on what it returns).
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_fhir.server import FhirServer


class FhirServerReader:
    """Adapts `FhirServer.read_resource` to the `graph.PatientSummaryReader` Protocol."""

    def __init__(self, server: FhirServer) -> None:
        self._server = server

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        return await self._server.read_resource("Patient", patient_id)
