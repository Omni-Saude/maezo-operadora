"""Unit tests for maezo.tools.mcp_fhir (ADR-0022)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# FHIR Server: read_resource, search_resources
# ---------------------------------------------------------------------------


def test_fhir_tools_registered() -> None:
    """FHIR server should expose exactly 2 tools: read_resource, search_resources."""
    from maezo.tools.mcp_fhir import FhirServer

    server = FhirServer()

    tools = server.list_tools()

    assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}: {tools}"
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"read_resource", "search_resources"}


def test_fhir_settings_defaults() -> None:
    """FhirSettings should default to http://localhost:8081/fhir."""
    from maezo.tools.mcp_fhir.server import FhirSettings

    settings = FhirSettings()

    assert settings.base_url == "http://localhost:8081/fhir"


@pytest.mark.asyncio
async def test_fhir_mock_read_patient() -> None:
    """read_resource('Patient', '123') should GET /fhir/Patient/123 and return parsed JSON."""
    from maezo.tools.mcp_fhir.server import FhirServer

    server = FhirServer()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "resourceType": "Patient",
            "id": "123",
            "name": [{"family": "Teste", "given": ["Paciente"]}],
        }
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.read_resource("Patient", "123")

    assert result["resourceType"] == "Patient"
    assert result["id"] == "123"
    assert result["name"][0]["family"] == "Teste"
    mock_client.get.assert_awaited_once()
    call_args = mock_client.get.call_args
    assert "/Patient/123" in call_args[0][0]


@pytest.mark.asyncio
async def test_fhir_mock_search_resources() -> None:
    """search_resources should GET /fhir/Patient with query params and return bundle."""
    from maezo.tools.mcp_fhir.server import FhirServer

    server = FhirServer()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "resourceType": "Bundle",
            "entry": [{"resource": {"resourceType": "Patient", "id": "1"}}],
        }
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.search_resources("Patient", {"name": "Teste"})

    assert result["resourceType"] == "Bundle"
    mock_client.get.assert_awaited_once()
    call_args = mock_client.get.call_args
    assert "/Patient" in call_args[0][0]
