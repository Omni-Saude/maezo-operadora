"""Gated FHIR read seam — action class `leitura_phi_clinica` (C2, design §6.1).

Satisfies, structurally and simultaneously, every FHIR Protocol the graphs declare:
`rafael/graph.py:98` `FhirReader` (`read_patient` + `search_coverage`), `gustavo/graph.py:212`
`FhirReader` and `andre/graph.py:277` / `carolina/graph.py:197` (`read_patient` alone), and
`marina/graph.py:190` / `valentina/graph.py:185` / `beatriz/graph.py:147` `PatientSummaryReader`
(`read_patient_summary`). One wrapper covers all six because they are structural (R-7): the
adapters underneath are `agents/rafael/adapters.py::FhirServerReader`,
`agents/valentina/adapters.py`, `agents/marina/adapters.py`.

`read_coverage` is declared because the catalogue declares `mcp-fhir.read_coverage` (helena's
`agent.yaml` names it) even though no Protocol requires it today. It DELEGATES, so an inner
adapter that does not implement it raises exactly the `AttributeError` the raw adapter would have
raised — the wrapper adds no capability it cannot honour.

THE PATIENT ID NEVER ENTERS A DECISION. `patient_id` is a parameter of these methods and an
argument of the inner call; it is not a field `EffectCall` has (I-3). A denial therefore folds
into the graphs' existing gap note (`rafael/graph.py:350`: `f"cobertura FHIR indisponivel: {exc}"`)
carrying only bounded tokens — which is the `LACUNA_DECLARADA` shape §6.1 declares for this class.
"""

from __future__ import annotations

from typing import Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

_OP_READ_PATIENT = "fhir.read_patient"
_OP_READ_PATIENT_SUMMARY = "fhir.read_patient_summary"
_OP_READ_COVERAGE = "fhir.read_coverage"
_OP_SEARCH_COVERAGE = "fhir.search_coverage"


class GatedFhirReader(GatedSeam):
    """Protocol-preserving decorator over any injected FHIR read adapter."""

    __slots__ = ()

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        await gate(self._seam, _OP_READ_PATIENT)
        resource: dict[str, Any] = await self._inner.read_patient(patient_id)
        return resource

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        await gate(self._seam, _OP_READ_PATIENT_SUMMARY)
        summary: dict[str, Any] = await self._inner.read_patient_summary(patient_id)
        return summary

    async def read_coverage(self, patient_id: str) -> Any:
        await gate(self._seam, _OP_READ_COVERAGE)
        return await self._inner.read_coverage(patient_id)

    async def search_coverage(self, patient_id: str) -> Any:
        await gate(self._seam, _OP_SEARCH_COVERAGE)
        return await self._inner.search_coverage(patient_id)


def gate_fhir(inner: Any, seam: SeamContext) -> GatedFhirReader:
    """The ONLY sanctioned way to build one (called from `gateway.tool_registry`)."""
    return GatedFhirReader(inner, seam=seam)


__all__ = ["GatedFhirReader", "gate_fhir"]
