"""Generic `FhirReader` adapter over `maezo.tools.mcp_fhir.server.FhirServer`.

v2's `FhirServer` is a generic HAPI FHIR R4 client (`read_resource(resource_type, id)`/
`search_resources(resource_type, params)`) — NOT the v1 donor's dedicated `read_patient`/
`search_coverage` methods. CORRIGIDO (WP FHIR-TOOL-SURFACE-PARITY): a alegacao anterior
("nao wireado por nenhum gateway PEP/ToolRegistry ainda, T2.4 gap") e FALSA hoje — este e o
adaptador que `gateway/tool_registry.py::build_fhir_seam` embrulha em
`gateway/seams/fhir.py::GatedFhirReader` para todo agente cujo `_FHIR_ADAPTER_BY_AGENT` diz
`read_patient` (rafael, gustavo, andre), de modo que cada chamada passa por
`decide_effect(operation="fhir.read_patient"|"fhir.search_coverage")` sob o principal do agente.
Este adaptador e o shim fino que deixa o no `gather` do Rafael consumir o cliente generico pela
forma do Protocol `graph.FhirReader`; as duas leituras sao TIPADAS
(`tools/mcp_fhir/typed_reads.py`) e recusam um `resourceType` inesperado.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_fhir.server import FhirServer
from maezo.tools.mcp_fhir.typed_reads import read_patient_resource, search_coverage_entries


class FhirServerReader:
    """Adapta as leituras TIPADAS de `Patient`/`Coverage` ao Protocol `graph.FhirReader`.

    As duas leituras passam por `tools/mcp_fhir/typed_reads.py` (VAL-02): o shim promete UM
    `Patient` e um `Bundle` de `Coverage`, e agora RECUSA qualquer outro `resourceType`. Na perna
    da cobertura a verificacao vale ainda mais — sem ela um corpo que nao e Bundle vira `[]`, que
    e indistinguivel de "beneficiario sem cobertura ativa" e entra no dossie como fato.
    """

    def __init__(self, server: FhirServer) -> None:
        self._server = server

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        return await read_patient_resource(self._server, patient_id)

    async def search_coverage(self, patient_id: str) -> Any:
        return await search_coverage_entries(self._server, patient_id)
