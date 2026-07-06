"""Beatriz — Chronic Disease Care Agent (Phase 3, PROGRAMA).

Beatriz manages chronic disease care programs: assesses eligibility,
designs care plans, and monitors adherence. She operates in the PHI
security zone (ADR-0006), processing PHI only after the consent gate.

L0 HARD INVARIANT: Beatriz NEVER exercises clinical_decision. Program
discharge (alta/desligamento clínico) is exclusively a human clinical
decision via UT_DecisaoClinica in SP-OP-PROGRAMA-001. The adverse effect
register_program_discharge is guarded by ERR_PROGRAM_DISCHARGE_NOT_HUMAN
in the engine — never triggered by Beatriz's graph.

Her graph consists of:
- assess_eligibility: assesses program eligibility (consent-gated, NEVER denies care)
- design_care_plan: designs the care plan and risk stratification dossier
- monitor_adherence: monitors program adherence (NEVER decides discharge)
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return Beatriz's StateGraph for chronic disease care programs.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → assess_eligibility → design_care_plan → monitor_adherence → __end__

    Returns:
        A StateGraph[AgentState] for Beatriz's care program workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def assess_eligibility_node(state: AgentState) -> dict[str, Any]:
        """Assess beneficiary eligibility for care programs.

        Verifies consent (CHOKEPOINT: fail-closed if no consent), evaluates
        clinical criteria, and stratifies risk. NEVER denies care or decides
        discharge — only produces eligibility assessment for the human clinician.

        Args:
            state: The current AgentState with beneficiary data.

        Returns:
            Updated state dict with eligibility assessment.
        """
        messages: list[str] = list(state.get("messages", []))
        beneficiary_id: str = cast(str, state.get("beneficiario_pseudo_id", ""))
        consent_active: bool = cast(bool, state.get("consentimento_ativo", False))

        # Consent chokepoint: fail-closed, NEVER processes PHI without consent
        if not consent_active:
            eligibility: dict[str, Any] = {
                "beneficiario_pseudo_id": beneficiary_id,
                "eligible": False,
                "reason": "sem_consentimento_ativo",
                "requires_human": True,
                "routing": "FAIL_CLOSED_CONSENT",
            }
        else:
            # Assess clinical eligibility — NEVER denies care
            risco: str = cast(str, state.get("risco_estratificado", "BAIXO"))
            programa_id: str = cast(str, state.get("programa_id", ""))

            eligibility = {
                "beneficiario_pseudo_id": beneficiary_id,
                "programa_id": programa_id,
                "eligible": True,
                "risco_estratificado": risco,
                "requires_human": risco in ("ALTO", "CRITICO"),
                "routing": ("ANALISE_HUMANA" if risco in ("ALTO", "CRITICO") else "ENROLL"),
                # CRITICAL: NEVER sets decisao_programa (discharge)
            }

        return {
            "messages": messages,
            "eligibility": eligibility,
        }

    async def design_care_plan_node(state: AgentState) -> dict[str, Any]:
        """Design the care plan dossier for the beneficiary.

        Assembles the clinical dossier: risk stratification, care plan steps,
        monitoring schedule, and clinical references. NEVER decides discharge
        or clinical outcomes — the plan is an INSTRUCTION for the human clinician
        who makes the final decision in UT_DecisaoClinica.

        Args:
            state: The current AgentState with eligibility data.

        Returns:
            Updated state dict with care plan design.
        """
        messages: list[str] = list(state.get("messages", []))
        eligibility: dict[str, Any] = cast(dict[str, Any], state.get("eligibility", {}))
        beneficiary_id: str = cast(str, eligibility.get("beneficiario_pseudo_id", ""))
        programa_id: str = cast(str, eligibility.get("programa_id", ""))
        risco: str = cast(str, eligibility.get("risco_estratificado", "BAIXO"))
        is_eligible: bool = cast(bool, eligibility.get("eligible", False))

        if not is_eligible:
            care_plan: dict[str, Any] = {
                "beneficiario_pseudo_id": beneficiary_id,
                "plan_created": False,
                "reason": "not_eligible",
                "routing": "TERMINAL_NEUTRO",
            }
        else:
            # Design care plan — instructs, NEVER decides clinical outcomes
            care_plan = {
                "beneficiario_pseudo_id": beneficiary_id,
                "programa_id": programa_id,
                "plan_created": True,
                "risco_estratificado": risco,
                "steps": ["avaliacao_inicial", "meta_terapeutica", "monitoramento"],
                "references": [],
                "requires_clinical_review": risco in ("ALTO", "CRITICO"),
                # CRITICAL: NEVER sets decisao_programa (discharge)
            }

        return {
            "messages": messages,
            "care_plan": care_plan,
        }

    async def monitor_adherence_node(state: AgentState) -> dict[str, Any]:
        """Monitor beneficiary adherence to the care program.

        Tracks program participation, clinical indicators, and flags potential
        issues for human review. NEVER decides discharge — any apparent discharge
        criteria are escalated to the human clinician via UT_DecisaoClinica.

        L0 HARD: This node NEVER sets decisao_programa = DESLIGAR_CLINICO.
        Clinical discharge is exclusively a human decision.

        Args:
            state: The current AgentState with care plan data.

        Returns:
            Updated state dict with adherence monitoring results.
        """
        messages: list[str] = list(state.get("messages", []))
        care_plan: dict[str, Any] = cast(dict[str, Any], state.get("care_plan", {}))
        eligibility: dict[str, Any] = cast(dict[str, Any], state.get("eligibility", {}))
        beneficiary_id: str = cast(str, eligibility.get("beneficiario_pseudo_id", ""))
        risco: str = cast(str, care_plan.get("risco_estratificado", "BAIXO"))
        plan_created: bool = cast(bool, care_plan.get("plan_created", False))

        if not plan_created:
            monitoring: dict[str, Any] = {
                "beneficiario_pseudo_id": beneficiary_id,
                "monitoring_active": False,
                "reason": "no_active_plan",
                "requires_human": False,
            }
        else:
            # Monitor adherence — NEVER decides discharge
            # Apparent discharge criteria → escalate to human
            sinal_alta: bool = cast(bool, state.get("sinal_alta_aparente", False))
            criterio_alta_aparente: bool = risco == "BAIXO" and sinal_alta

            monitoring = {
                "beneficiario_pseudo_id": beneficiary_id,
                "monitoring_active": True,
                "risco_atual": risco,
                "criterio_alta_aparente": criterio_alta_aparente,
                "requires_human": criterio_alta_aparente or risco in ("ALTO", "CRITICO"),
                "routing": (
                    "UT_DecisaoClinica"
                    if criterio_alta_aparente or risco in ("ALTO", "CRITICO")
                    else "MONITORAR"
                ),
                # CRITICAL: NEVER sets decisao_programa = DESLIGAR_CLINICO
            }

        return {
            "messages": messages,
            "adherence": monitoring,
        }

    graph.add_node("assess_eligibility", assess_eligibility_node)
    graph.add_node("design_care_plan", design_care_plan_node)
    graph.add_node("monitor_adherence", monitor_adherence_node)

    graph.add_edge("__start__", "assess_eligibility")
    graph.add_edge("assess_eligibility", "design_care_plan")
    graph.add_edge("design_care_plan", "monitor_adherence")
    graph.add_edge("monitor_adherence", "__end__")

    return graph
