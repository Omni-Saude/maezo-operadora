"""Generic `PatientSummaryReader` adapter over `maezo.tools.mcp_fhir.server.FhirServer`.

v2's `FhirServer` is a generic HAPI FHIR R4 client (`read_resource(resource_type, id)`/
`search_resources(resource_type, params)`) — NOT the v1 donor's dedicated
`mcp-fhir.read_patient_summary` tool (declared in `spec/agents/valentina/agent.yaml`'s `tools:`
list). CORRIGIDO (WP FHIR-TOOL-SURFACE-PARITY): a alegacao "nao wireado por nenhum gateway
PEP/ToolRegistry ainda (T2.4 gap)" e FALSA hoje — este e o adaptador que
`gateway/tool_registry.py::build_fhir_seam` embrulha em `gateway/seams/fhir.py::GatedFhirReader`
para todo agente cujo `_FHIR_ADAPTER_BY_AGENT` diz `read_patient_summary` (valentina, carolina),
entao a leitura passa por `decide_effect(operation="fhir.read_patient_summary")` sob o principal
do agente. This adapter is the thin shim that lets Valentina's `gather` node consume the generic
client through the `graph.PatientSummaryReader` Protocol shape: a single `Patient` resource read
stands in for the donor's richer "patient summary" composite.

VAL-02, A FORMA REAL E AGORA VERIFICADA. O "resumo" desta build e UMA leitura de `Patient` — nao
um composto Patient+Observation+Condition. Isso esta dito no comentario do proprio `agent.yaml`
(camada de spec, onde um planejamento de capacidade olha) e nao so aqui; e a leitura agora e
TIPADA (`tools/mcp_fhir/typed_reads.py::read_patient_resource`), recusando fail-closed qualquer
`resourceType` diferente de `Patient` em vez de encaminha-lo como resumo. Um resumo
multi-recurso de verdade continua sendo follow-up (ALARGAMENTO de leitura, decisao do dono) —
`gather` e best-effort, roda SO depois do chokepoint de consentimento e nunca bloqueia
roteamento pelo que devolve.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_fhir.server import FhirServer
from maezo.tools.mcp_fhir.typed_reads import read_patient_resource


class FhirServerReader:
    """Adapta a leitura TIPADA de `Patient` ao Protocol `graph.PatientSummaryReader`.

    A leitura passa por `tools/mcp_fhir/typed_reads.py::read_patient_resource` e nao pelo
    `read_resource` generico: o shim promete UM `Patient` e agora RECUSA (fail-closed) qualquer
    outro `resourceType` que o servidor devolva, em vez de encaminhar PHI de forma desconhecida
    para `summary_facts` -> prompt `phi=True` -> dossie (VAL-02).
    """

    def __init__(self, server: FhirServer) -> None:
        self._server = server

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        return await read_patient_resource(self._server, patient_id)
