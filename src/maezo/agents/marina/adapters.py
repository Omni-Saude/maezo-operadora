"""Generic `PatientSummaryReader` adapter over `maezo.tools.mcp_fhir.server.FhirServer`.

v2's `FhirServer` is a generic HAPI FHIR R4 client (`read_resource(resource_type, id)`/
`search_resources(resource_type, params)`) — NOT the v1 donor's dedicated
`mcp-fhir.read_patient_summary` tool (declared in `spec/agents/marina/agent.yaml`'s `tools:`
list). This adapter is the thin shim that lets Marina's `gather` node consume the generic client
through the `graph.PatientSummaryReader` Protocol shape: a single `Patient` resource read stands
in for the donor's richer "patient summary" composite.

NAO CONSTRUIDO POR NENHUMA RAIZ HOJE, e isso e um fato, nao uma pendencia escondida (WP
FHIR-TOOL-SURFACE-PARITY): marina esta AUSENTE de
`gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT`, entao nenhum composition root lhe injeta um
seam `fhir` e o ramo FHIR do `gather` dela nunca roda em producao; e `build_fhir_seam` embrulha
apenas os shims de rafael/valentina, de modo que ESTA classe nao e instanciada por `src/`.
Registrado na cerca `tests/unit/gateway/test_fhir_tool_surface_parity.py`
(`_INVOKED_WITHOUT_GATED_SEAM`), porque liga-la ACENDERIA uma leitura de PHI que hoje nao
existe — ALARGAMENTO de superficie, decisao do dono. A leitura ja e TIPADA
(`tools/mcp_fhir/typed_reads.py`) para que, no dia em que for ligada, ela nasca fail-closed.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_fhir.server import FhirServer
from maezo.tools.mcp_fhir.typed_reads import read_patient_resource


class FhirServerReader:
    """Adapta a leitura TIPADA de `Patient` ao Protocol `graph.PatientSummaryReader`.

    Mesma forma (e mesmo motivo) do shim de Valentina: `typed_reads.read_patient_resource`
    RECUSA um `resourceType` diferente de `Patient` em vez de encaminha-lo como resumo (VAL-02).
    """

    def __init__(self, server: FhirServer) -> None:
        self._server = server

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        return await read_patient_resource(self._server, patient_id)
