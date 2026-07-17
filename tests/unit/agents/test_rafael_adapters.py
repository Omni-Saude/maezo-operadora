"""Unit tests for `maezo.agents.rafael.adapters.FhirServerReader`."""

from __future__ import annotations

from unittest.mock import AsyncMock

from maezo.agents.rafael.adapters import FhirServerReader
from maezo.tools.mcp_fhir.server import FhirServer


async def test_read_patient_delegates_to_read_resource() -> None:
    server = FhirServer()
    server.read_resource = AsyncMock(return_value={"resourceType": "Patient", "id": "p1"})  # type: ignore[method-assign]

    reader = FhirServerReader(server)
    result = await reader.read_patient("p1")

    server.read_resource.assert_awaited_once_with("Patient", "p1")
    assert result == {"resourceType": "Patient", "id": "p1"}


async def test_search_coverage_delegates_to_search_resources_with_beneficiary_param() -> None:
    server = FhirServer()
    server.search_resources = AsyncMock(  # type: ignore[method-assign]
        return_value={"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Coverage"}}]}
    )

    reader = FhirServerReader(server)
    result = await reader.search_coverage("p1")

    server.search_resources.assert_awaited_once_with("Coverage", {"beneficiary": "Patient/p1"})
    assert result == [{"resource": {"resourceType": "Coverage"}}]


async def test_search_coverage_empty_bundle_returns_empty_list() -> None:
    server = FhirServer()
    server.search_resources = AsyncMock(return_value={"resourceType": "Bundle"})  # type: ignore[method-assign]

    reader = FhirServerReader(server)
    result = await reader.search_coverage("p1")

    assert result == []
