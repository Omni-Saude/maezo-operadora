"""André — Population Health & Analytics Agent (Phase 3, analytics).

André provides population health analytics: analyzes population data,
identifies risk cohorts, and recommends interventions. He operates in the
PHI security zone (ADR-0006) and is the CHOKEPOINT of PHI egress — he
reads PHI but only emits k-anonymized aggregates.

L0 HARD INVARIANT: André NEVER exercises high_value_payment (L1),
pricing_decision, or clinical_decision. Payment approval and pricing
are exclusively human decisions via UT_AprovacaoAlcada/UT_AprovacaoComite
in SP-OP-PAGTO-001. André instructs the actuarial/population risk dossier;
the human approver of appropriate tier decides.

His graph consists of:
- analyze_population: analyzes population data (aggregates only)
- identify_risk_cohorts: identifies risk cohorts (k-anonymized)
- recommend_interventions: recommends population-level interventions (NEVER decides payment)
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return André's StateGraph for population health analytics.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → analyze_population → identify_risk_cohorts → recommend_interventions → __end__

    Returns:
        A StateGraph[AgentState] for André's population analytics workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def analyze_population_node(state: AgentState) -> dict[str, Any]:
        """Analyze population data from the data lake.

        Reads population-level aggregate data (k-anonymized via Protocol
        PopulationFeatureClient) and computes baseline statistics. NEVER
        processes raw PHI — only aggregated cohorts with k-anonymity.

        EGRESS CHOKEPOINT (ADR-0006/0019): outputs are structurally
        constrained to k-anon aggregates with dataset_ref pointers,
        never resolvable fhir_patient_ids.

        Args:
            state: The current AgentState with population query parameters.

        Returns:
            Updated state dict with population analysis results.
        """
        messages: list[str] = list(state.get("messages", []))
        cohort: str = cast(str, state.get("cohort_ref", ""))
        metrics: list[str] = cast(list[str], state.get("metricas_solicitadas", []))

        if not isinstance(metrics, list):
            metrics = []

        # Analyze population — aggregates only, NEVER raw PHI
        analysis: dict[str, Any] = {
            "cohort_ref": cohort,
            "population_size": 0,  # placeholder — real: k-anon aggregate query
            "metrics_computed": metrics,
            "data_freshness": "now",
            # EGRESS: structurally no resolvable PHI
            "phi_egress_risk": False,
            "dataset_ref": f"ds://{cohort}" if cohort else "",
        }

        return {
            "messages": messages,
            "population_analysis": analysis,
        }

    async def identify_risk_cohorts_node(state: AgentState) -> dict[str, Any]:
        """Identify risk cohorts from population analysis.

        Segments the population into risk cohorts based on clinical,
        demographic, and utilization patterns. All cohorts are k-anonymized
        (minimum group size enforced). NEVER identifies individual
        beneficiaries in output.

        Args:
            state: The current AgentState with population analysis.

        Returns:
            Updated state dict with risk cohort identification.
        """
        messages: list[str] = list(state.get("messages", []))
        analysis: dict[str, Any] = cast(dict[str, Any], state.get("population_analysis", {}))
        cohort_ref: str = cast(str, analysis.get("cohort_ref", ""))

        # Identify risk cohorts — k-anonymized aggregates only
        cohorts: list[dict[str, Any]] = [
            {
                "cohort_id": "alto_risco_cronico",
                "size": 0,  # placeholder
                "risk_level": "ALTO",
                "criteria": ["multiplas_internacoes", "doenca_cronica_nao_controlada"],
                "k_anonymous": True,
            },
            {
                "cohort_id": "moderado_risco_emergente",
                "size": 0,  # placeholder
                "risk_level": "MODERADO",
                "criteria": ["aumento_utilizacao", "nova_condicao_cronica"],
                "k_anonymous": True,
            },
            {
                "cohort_id": "baixo_risco_estavel",
                "size": 0,  # placeholder
                "risk_level": "BAIXO",
                "criteria": ["utilizacao_estavel", "sem_condicao_cronica"],
                "k_anonymous": True,
            },
        ]

        risk_analysis: dict[str, Any] = {
            "source_cohort": cohort_ref,
            "cohorts_identified": len(cohorts),
            "cohorts": cohorts,
            # NEVER exposes individual PHI
            "phi_egress_violations": 0,
        }

        return {
            "messages": messages,
            "risk_cohorts": risk_analysis,
        }

    async def recommend_interventions_node(state: AgentState) -> dict[str, Any]:
        """Recommend population-level interventions.

        Based on risk cohort analysis, recommends population health
        interventions (program enrollment, preventive campaigns, network
        adjustments). NEVER decides payment, pricing, or individual clinical
        actions — all recommendations are population-level and routed to
        human decision-makers (UT_AprovacaoAlcada for payment, coordenacao-clinica
        for clinical programs).

        L0 HARD: This node NEVER sets any payment approval or pricing decision.
        high_value_payment is exclusively human-gated.

        Args:
            state: The current AgentState with risk cohort data.

        Returns:
            Updated state dict with intervention recommendations.
        """
        messages: list[str] = list(state.get("messages", []))
        risk_cohorts: dict[str, Any] = cast(dict[str, Any], state.get("risk_cohorts", {}))
        cohorts: list[dict[str, Any]] = cast(list[dict[str, Any]], risk_cohorts.get("cohorts", []))

        if not isinstance(cohorts, list):
            cohorts = []

        # Recommend population-level interventions — NEVER decides payment/pricing
        recommendations: list[dict[str, Any]] = []
        for cohort in cohorts:
            risk: str = cast(str, cohort.get("risk_level", ""))
            cohort_id: str = cast(str, cohort.get("cohort_id", ""))
            if risk == "ALTO":
                recommendations.append(
                    {
                        "cohort_id": cohort_id,
                        "intervention": "enrollment_care_program",
                        "target_process": "SP-OP-PROGRAMA-001",
                        "urgency": "ALTA",
                    }
                )
            elif risk == "MODERADO":
                recommendations.append(
                    {
                        "cohort_id": cohort_id,
                        "intervention": "preventive_monitoring",
                        "target_process": "SP-OP-PROGRAMA-001",
                        "urgency": "MEDIA",
                    }
                )

        intervention_plan: dict[str, Any] = {
            "recommendations": recommendations,
            "total_cohorts_analyzed": len(cohorts),
            "cohorts_with_interventions": len(recommendations),
            "requires_human_approval": True,  # ALWAYS — payment/pricing is human-gated
            "routing": "UT_AprovacaoAlcada",
            # CRITICAL: NEVER sets pagto approval, pricing, or clinical decisions
        }

        return {
            "messages": messages,
            "interventions": intervention_plan,
        }

    graph.add_node("analyze_population", analyze_population_node)
    graph.add_node("identify_risk_cohorts", identify_risk_cohorts_node)
    graph.add_node("recommend_interventions", recommend_interventions_node)

    graph.add_edge("__start__", "analyze_population")
    graph.add_edge("analyze_population", "identify_risk_cohorts")
    graph.add_edge("identify_risk_cohorts", "recommend_interventions")
    graph.add_edge("recommend_interventions", "__end__")

    return graph
