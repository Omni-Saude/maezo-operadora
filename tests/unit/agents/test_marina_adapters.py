"""Unit tests for `maezo.agents.marina.adapters.FhirServerReader`."""

from __future__ import annotations

from unittest.mock import AsyncMock

from maezo.agents.marina.adapters import FhirServerReader
from maezo.tools.mcp_fhir.server import FhirServer


async def test_read_patient_summary_delegates_to_read_resource() -> None:
    server = FhirServer()
    server.read_resource = AsyncMock(return_value={"resourceType": "Patient", "id": "p1"})  # type: ignore[method-assign]

    reader = FhirServerReader(server)
    result = await reader.read_patient_summary("p1")

    server.read_resource.assert_awaited_once_with("Patient", "p1")
    assert result == {"resourceType": "Patient", "id": "p1"}
