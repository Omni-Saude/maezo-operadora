"""Worker: adequacao (SP-OP-ADEQUACAO-001).

Adequacao Geografica de Rede — mostly L3 monitoring with one human-gated adverse effect.
Guard: ERR_FALLBACK_COMMITMENT_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_FALLBACK_COMMITMENT_NOT_HUMAN = "ERR_FALLBACK_COMMITMENT_NOT_HUMAN"
ERR_ADEQUACAO_CELULA_INVALIDA = "ERR_ADEQUACAO_CELULA_INVALIDA"

# Decision values
DECISAO_COMPROMISSO_FALLBACK = "COMPROMISSO_FALLBACK"


# ---------------------------------------------------------------
# measure_gap — geo-analysis (FACT, no decision)
# ---------------------------------------------------------------


def measure_gap(variables: dict[str, Any]) -> dict[str, Any]:
    """Measure geographic coverage gap (FACT only — NEVER decides commitment).

    Computes: tempo_acesso_apurado_min, distancia_apurada_km,
    prestadores_disponiveis, cobertura_geo_suficiente.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    # Placeholder: real implementation queries geo-location / network DB
    tempo_acesso = 45  # minutes
    distancia = 15.5  # km
    prestadores = variables.get("prestadores_disponiveis", 3)
    cobertura_suficiente = prestadores >= 2
    dados_completos = bool(regiao and especialidade)

    logger.info(
        "adequacao_measure_gap",
        regiao_saude=regiao,
        especialidade=especialidade,
        tempo_acesso_min=tempo_acesso,
        distancia_km=distancia,
        prestadores=prestadores,
    )

    return {
        "tempo_acesso_apurado_min": tempo_acesso,
        "distancia_apurada_km": distancia,
        "prestadores_disponiveis": prestadores,
        "cobertura_geo_suficiente": cobertura_suficiente,
        "dados_geo_completos": dados_completos,
    }


# ---------------------------------------------------------------
# route_remediation — classify gap and route (no commitment decision)
# ---------------------------------------------------------------


def route_remediation(variables: dict[str, Any]) -> dict[str, Any]:
    """Classify gap severity and route remediation — NEVER commits to fallback.

    DMN-like: adequacao_gap + adequacao_remediation_routing.
    """
    tempo = variables.get("tempo_acesso_apurado_min", 0)
    distancia = variables.get("distancia_apurada_km", 0.0)
    prestadores = variables.get("prestadores_disponiveis", 0)
    cobertura = variables.get("cobertura_geo_suficiente", False)
    dados_completos = variables.get("dados_geo_completos", True)

    # Determine gap severity
    if not dados_completos:
        gap_adequacao = "GAP_CRITICO"
        motivo = "dados geográficos insuficientes"
    elif cobertura and tempo <= 30 and distancia <= 20.0:
        gap_adequacao = "CONFORME"
        motivo = "rede dentro dos parâmetros RN 259"
    elif cobertura and tempo <= 60:
        gap_adequacao = "GAP_LEVE"
        motivo = "leve desvio nos tempos de acesso"
    elif prestadores >= 1 and tempo <= 90:
        gap_adequacao = "GAP_MODERADO"
        motivo = "desvio moderado — encaminhar credenciamento"
    else:
        gap_adequacao = "GAP_CRITICO"
        motivo = "sem cobertura adequada na região"

    # Route based on gap
    if gap_adequacao == "CONFORME" or gap_adequacao == "GAP_LEVE":
        roteamento = "MONITORAR"
    elif gap_adequacao == "GAP_MODERADO":
        roteamento = "ENCAMINHAR_CREDENCIAMENTO"
    else:
        roteamento = "ANALISE_HUMANA"

    logger.info(
        "adequacao_route_remediation",
        gap_adequacao=gap_adequacao,
        roteamento=roteamento,
    )

    return {
        "gap_adequacao": gap_adequacao,
        "roteamento_remediacao": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# notify_coordenacao — neutral notification
# ---------------------------------------------------------------


def notify_coordenacao(variables: dict[str, Any]) -> dict[str, Any]:
    """Notify network coordination about gap (NEUTRAL, informational)."""
    regiao = variables.get("regiao_saude", "")
    gap = variables.get("gap_adequacao", "")

    logger.info(
        "adequacao_notify_coordenacao",
        regiao_saude=regiao,
        gap=gap,
    )

    return {"notificacao_enviada": True}


# ---------------------------------------------------------------
# execute_remediation — handoff to CRED-001 (NEUTRAL)
# ---------------------------------------------------------------


def execute_remediation(variables: dict[str, Any]) -> dict[str, Any]:
    """Initiate credentialing to close the gap (handoff to CRED-001 — NEUTRAL).

    This is L3 — buscar prestador nao e adverso.
    """
    logger.info(
        "adequacao_execute_remediation",
        regiao_saude=variables.get("regiao_saude"),
        especialidade=variables.get("especialidade"),
    )

    return {
        "handoff_credenciamento": True,
        "processo_destino": "SP-OP-CRED-001",
    }


# ---------------------------------------------------------------
# register_fallback_commitment — GATED adverse effect
# ---------------------------------------------------------------


def register_fallback_commitment(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a financial fallback commitment (livre escolha/reembolso).

    GUARDED: ERR_FALLBACK_COMMITMENT_NOT_HUMAN.
    This is the ONLY adverse effect in this L3-majority process.
    """
    decisao = variables.get("decisao_remediacao", "")
    tipo_fallback = variables.get("tipo_fallback", "")
    justificativa = variables.get("justificativa_fallback", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")
    responsavel_id = variables.get("responsavel_id", "")

    errors: list[str] = []

    if decisao != DECISAO_COMPROMISSO_FALLBACK:
        errors.append(f"decisao_remediacao != {DECISAO_COMPROMISSO_FALLBACK} (got: {decisao!r})")
    if not tipo_fallback:
        errors.append("tipo_fallback ausente")
    if not justificativa:
        errors.append("justificativa_fallback ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 259)")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")

    if errors:
        logger.error(
            "adequacao_fallback_guard_rejected",
            errors=errors,
        )
        raise AdequacaoError(ERR_FALLBACK_COMMITMENT_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "adequacao_fallback_commitment_registered",
        tipo_fallback=tipo_fallback,
        responsavel_id=responsavel_id,
    )

    return {
        "compromisso_fallback_registrado": True,
        "estimativa_custo_cents": variables.get("estimativa_custo_cents", 0),
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class AdequacaoError(Exception):
    """Worker guard error for adequacao adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
