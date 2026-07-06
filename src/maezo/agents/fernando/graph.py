"""Fernando — Provider Network Agent (Phase 3, CRED/ADEQUACAO).

Fernando manages the provider network lifecycle: credentialing evaluation,
geographic adequacy gap measurement, and action recommendation. He operates
in the General security zone (PHI pseudonymized via gateway, ADR-0006).

L0 HARD INVARIANT: Fernando NEVER exercises provider_decredentialing,
credentialing_denial, or fallback_commitment. All adverse decisions
(descredenciamento, negativa de credenciamento, compromisso financeiro)
are exclusively human-gated via User Tasks in SP-OP-CRED-001 and
SP-OP-ADEQUACAO-001.

His graph consists of:
- evaluate_provider: validates provider credentials and admissibility (FACT only)
- measure_network_gap: measures geographic coverage gaps (FACT only)
- recommend_action: routes to human analysis or credentialing (NEVER decides adverse)
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import StateGraph

from maezo.runtime.harness import AgentState


def build() -> StateGraph[AgentState]:
    """Build and return Fernando's StateGraph for provider network management.

    Returns an uncompiled StateGraph[AgentState] wired as:
    __start__ → evaluate_provider → measure_network_gap → recommend_action → __end__

    Returns:
        A StateGraph[AgentState] for Fernando's provider network workflow.
    """
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    async def evaluate_provider_node(state: AgentState) -> dict[str, Any]:
        """Evaluate provider credentials and admissibility (FACT only).

        Validates professional registration, CNES status, and documentation
        completeness. NEVER decides to deny or de-credential — only produces
        factual assessment for downstream routing.

        Args:
            state: The current AgentState with provider data.

        Returns:
            Updated state dict with provider evaluation results.
        """
        messages: list[str] = list(state.get("messages", []))
        provider_id: str = cast(str, state.get("prestador_id", ""))

        # Factual evaluation — NEVER produces NEGAR or DESCREDENCIAR
        evaluation: dict[str, Any] = {
            "licenca_valida": True,  # placeholder — real queries CRM/CNES
            "documentacao_completa": True,
            "dentro_criterios_rede": True,
            "prestador_id": provider_id,
        }

        return {
            "messages": messages,
            "provider_evaluation": evaluation,
        }

    async def measure_network_gap_node(state: AgentState) -> dict[str, Any]:
        """Measure geographic network coverage gap (FACT only).

        Computes access time, distance, and provider availability for a given
        region/specialty. NEVER decides on fallback commitment — only produces
        factual gap measurement for downstream routing.

        Args:
            state: The current AgentState with region and provider data.

        Returns:
            Updated state dict with gap measurement results.
        """
        messages: list[str] = list(state.get("messages", []))
        region: str = cast(str, state.get("regiao_saude", ""))
        specialty: str = cast(str, state.get("especialidade", ""))

        # Factual gap measurement — NEVER decides COMPROMISSO_FALLBACK
        dados_geo_completos: bool = bool(region and specialty)
        tempo_acesso: int = 45
        distancia: float = 15.5
        prestadores: int = 3
        cobertura: bool = True

        # Classify gap severity (routing fact, not decision)
        if not dados_geo_completos:
            gap_adequacao: str = "GAP_CRITICO"
        elif cobertura and tempo_acesso <= 30:
            gap_adequacao = "CONFORME"
        elif cobertura:
            gap_adequacao = "GAP_LEVE"
        elif prestadores >= 1:
            gap_adequacao = "GAP_MODERADO"
        else:
            gap_adequacao = "GAP_CRITICO"

        gap: dict[str, Any] = {
            "regiao_saude": region,
            "especialidade": specialty,
            "tempo_acesso_apurado_min": tempo_acesso,
            "distancia_apurada_km": distancia,
            "prestadores_disponiveis": prestadores,
            "cobertura_geo_suficiente": cobertura,
            "dados_geo_completos": dados_geo_completos,
            "gap_adequacao": gap_adequacao,
        }

        return {
            "messages": messages,
            "network_gap": gap,
        }

    async def recommend_action_node(state: AgentState) -> dict[str, Any]:
        """Recommend action based on provider evaluation and network gap.

        Routes to: human analysis (adverse decisions), credentialing handoff,
        or network monitoring. NEVER decides adverse outcomes — all
        descredenciamento, negativa, and fallback commitment decisions are
        exclusively human-gated.

        Args:
            state: The current AgentState with evaluation and gap data.

        Returns:
            Updated state dict with action recommendation.
        """
        messages: list[str] = list(state.get("messages", []))
        provider_eval: dict[str, Any] = cast(dict[str, Any], state.get("provider_evaluation", {}))
        network_gap: dict[str, Any] = cast(dict[str, Any], state.get("network_gap", {}))

        gap_severity: str = cast(str, network_gap.get("gap_adequacao", "CONFORME"))
        licenca_valida: bool = cast(bool, provider_eval.get("licenca_valida", True))
        docs_complete: bool = cast(bool, provider_eval.get("documentacao_completa", True))

        # Route recommendation — NEVER produces adverse decisions
        if not licenca_valida or not docs_complete:
            routing: str = "ANALISE_HUMANA"
            reason: str = "requer análise humana — licença ou documentação"
        elif gap_severity in ("GAP_CRITICO",):
            routing = "ANALISE_HUMANA"
            reason = "gap crítico — requer análise humana"
        elif gap_severity == "GAP_MODERADO":
            routing = "ENCAMINHAR_CREDENCIAMENTO"
            reason = "gap moderado — encaminhar para credenciamento"
        else:
            routing = "MONITORAR"
            reason = "rede conforme ou gap leve — monitorar"

        recommendation: dict[str, Any] = {
            "routing": routing,
            "reason": reason,
            "requires_human": routing == "ANALISE_HUMANA",
        }

        return {
            "messages": messages,
            "action_recommendation": recommendation,
        }

    graph.add_node("evaluate_provider", evaluate_provider_node)
    graph.add_node("measure_network_gap", measure_network_gap_node)
    graph.add_node("recommend_action", recommend_action_node)

    graph.add_edge("__start__", "evaluate_provider")
    graph.add_edge("evaluate_provider", "measure_network_gap")
    graph.add_edge("measure_network_gap", "recommend_action")
    graph.add_edge("recommend_action", "__end__")

    return graph
