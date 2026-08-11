"""Gated population/actuarial seam — action class `leitura_populacional` (C2, design §6.1).

Satisfies `andre/graph.py:316`'s `PopulationFeatureClient` Protocol (`actuarial_risk`,
`population_metrics`, both returning a k-anon `CohortAggregate`).

SHIPS UNWIRED, AND THE MANIFEST SAYS SO. The concrete lake client is PORT-PENDING (WB.4 —
`mcp-datalake` is not in the repo, and `spec/agents/andre/agent.yaml` deliberately declares no id
for it), so `build_agent_seams` produces NO `population` key today and Andre's `build(config)`
keeps treating the seam's absence as a disclosed gap note. This wrapper therefore exists but
chokes nothing yet, and `action-approvals.yaml`'s two `leitura_populacional` surfaces stay
`choked: false`. Flipping them would be a claim of shadow evidence that no line of telemetry
backs — exactly what I-10 forbids ("a class with zero shadow lines is not approvable").

The wrapper is built and TESTED now anyway, on purpose: the day WB.4 lands, the registry has a
gated constructor to return and the composition roots need no edit. That is the difference
between a seam that is unwired and one that is unwritten.

`cohort_id` and `features` never enter a decision (I-3) — and note they are not PHI to begin
with: `CohortAggregate`'s type carries no resolvable patient id by construction
(`andre/graph.py:285-300`), which is exactly why `read_phi_data` is the WRONG autonomy name for
this class and why the catalogue leaves `autonomy_action=None` (Q-4) so L-2 records an honest
`VOCABULARIO_PENDENTE` instead of inventing vocabulary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.gateway.seams._base import GatedSeam, SeamContext, gate

if TYPE_CHECKING:  # pragma: no cover - types only; no runtime `maezo.agents` import at module scope
    from maezo.agents.andre.graph import CohortAggregate

_OP_ACTUARIAL_RISK = "population.actuarial_risk"
_OP_POPULATION_METRICS = "population.population_metrics"


class GatedPopulationFeatureClient(GatedSeam):
    """Protocol-preserving decorator over a `PopulationFeatureClient`."""

    __slots__ = ()

    async def actuarial_risk(self, cohort_id: str, *, features: list[str]) -> CohortAggregate:
        await gate(self._seam, _OP_ACTUARIAL_RISK)
        risk: CohortAggregate = await self._inner.actuarial_risk(cohort_id, features=features)
        return risk

    async def population_metrics(self, cohort_id: str, *, features: list[str]) -> CohortAggregate:
        await gate(self._seam, _OP_POPULATION_METRICS)
        metrics: CohortAggregate = await self._inner.population_metrics(cohort_id, features=features)
        return metrics


def gate_population(inner: Any, seam: SeamContext) -> GatedPopulationFeatureClient:
    """The ONLY sanctioned way to build one (called from `gateway.tool_registry`)."""
    return GatedPopulationFeatureClient(inner, seam=seam)


__all__ = ["GatedPopulationFeatureClient", "gate_population"]
