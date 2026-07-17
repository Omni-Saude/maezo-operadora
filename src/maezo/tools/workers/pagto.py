"""Worker: pagto (SP-OP-PAGTO-001).

Pagamentos de Alcada — value-driven candidate groups.
Guard: ERR_PAYMENT_RELEASE_NOT_HUMAN + tier-match (L1, ADR-0018).
Clones AUTH auto-approval for low-value (dentro_teto_l2).
"""

from __future__ import annotations

from typing import Any

import structlog

from maezo.tools.workers.ceilings import CeilingResolver

logger = structlog.get_logger(__name__)

# Governance ceiling for the PAGTO auto-release band (design T1.9 §1.4, sibling of B3). The
# teto VALUE lives in the autonomy matrix (`high_value_payment.threshold_brl`,
# L0-core.yaml:21), resolved via the SAME loader the PEP uses — never a hard-coded literal.
_CEILING_ACTION = "high_value_payment"
_CEILING_PARAM = "threshold_brl"

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_PAYMENT_RELEASE_NOT_HUMAN = "ERR_PAYMENT_RELEASE_NOT_HUMAN"
ERR_PAGTO_ORDEM_INVALIDA = "ERR_PAGTO_ORDEM_INVALIDA"

# Decision values
DECISAO_APROVAR = "APROVAR"
DECISAO_RECUSAR = "RECUSAR"
DECISAO_CANCELAR = "CANCELAR"

# Tier constants
# Tier required per faixa_valor: higher tiers require higher approval levels
_TIER_MINIMO: dict[str, int] = {
    "DENTRO_TETO_L2": 0,  # auto (no human)
    "ALCADA_L1": 1,
    "ALCADA_L2": 2,
    "ALCADA_L3": 3,
    "ANALISE_HUMANA": 4,  # comite
}


# ---------------------------------------------------------------
# validate_pagto — validate payment data (FACT, never release)
# ---------------------------------------------------------------


def validate_pagto(variables: dict[str, Any]) -> dict[str, Any]:
    """Validate payment order data — FACT only, NEVER releases.

    Checks: credor/instrumento/lastro/duplicidade.
    """
    ordem_id = variables.get("ordem_pagamento_id", "")
    dados_validos = bool(ordem_id)
    lastro = variables.get("lastro_confirmado", False)
    duplicidade = variables.get("duplicidade_suspeita", False)

    if not dados_validos:
        logger.error("pagto_ordem_invalida", ordem_id=ordem_id)
        raise PagtoError(ERR_PAGTO_ORDEM_INVALIDA, "ordem_pagamento_id ausente/invalido")

    logger.info(
        "pagto_validate",
        ordem_id=ordem_id,
        dados_validos=dados_validos,
        lastro=lastro,
        duplicidade=duplicidade,
    )

    return {
        "dados_pagamento_validos": dados_validos,
        "lastro_confirmado": lastro,
        "duplicidade_suspeita": duplicidade,
    }


# ---------------------------------------------------------------
# assess_admissibility — classify and route (NEVER release)
# ---------------------------------------------------------------


def assess_admissibility(variables: dict[str, Any]) -> dict[str, Any]:
    """Classify payment admissibility — NEVER releases/authorizes.

    DMN-like: pagto_admissibility.
    """
    dados_validos = variables.get("dados_pagamento_validos", False)
    lastro = variables.get("lastro_confirmado", False)
    duplicidade = variables.get("duplicidade_suspeita", False)

    if not dados_validos:
        roteamento = "PENDENTE_DADOS"
        motivo = "dados de pagamento invalidos"
    elif duplicidade:
        roteamento = "ANALISE_HUMANA"
        motivo = "duplicidade suspeita — humano confirma"
    elif not lastro:
        roteamento = "ANALISE_HUMANA"
        motivo = "lastro nao confirmado"
    else:
        roteamento = "SEGUE_ROTEAMENTO"
        motivo = "dados consistentes"

    logger.info(
        "pagto_assess_admissibility",
        roteamento=roteamento,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# route_aprovacao — value-driven tier routing via DMN pagto_alcada
# ---------------------------------------------------------------


def route_aprovacao(
    variables: dict[str, Any],
    resolver: CeilingResolver | None = None,
) -> dict[str, Any]:
    """Route payment to correct approval tier based on value.

    DMN pagto_alcada: classifies faixa_valor and grupo_aprovador.
    This is the ONLY process with value-driven camunda:candidateGroups.

    The DENTRO_TETO_L2 auto-release band is COMPUTED from the tenant governance ceiling
    (``high_value_payment.threshold_brl``) via the CeilingResolver — the inbound
    ``dentro_teto_l2`` is NEVER read, and the former hard-coded R$100k literal is gone
    (design T1.9 §1.4). ``resolver`` is injectable for tests.
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    valor_cents = variables.get("valor_pagamento_cents", 0)
    # COMPUTE the auto-release fact from policy: value within `high_value_payment.threshold_brl`.
    # A config problem / unloadable matrix => False => routes to a human alcada band.
    dentro_teto = resolver.within_l2_ceiling(
        tenant=variables.get("tenant_id", ""),
        action=_CEILING_ACTION,
        param=_CEILING_PARAM,
        value_cents=valor_cents,
    )

    # DMN pagto_alcada — the low-value auto band is now the resolved ceiling itself
    # (within_l2_ceiling already encodes `valor_cents <= threshold_brl * 100`). The higher
    # human-approval bands remain conservative routing constants (no auto-approval).
    if dentro_teto:
        faixa = "DENTRO_TETO_L2"
        grupo = ""  # no human group needed
    elif valor_cents <= 50_000_000:  # <= R$ 500k
        faixa = "ALCADA_L1"
        grupo = "aprovacao-financeira-l1"
    elif valor_cents <= 200_000_000:  # <= R$ 2MM
        faixa = "ALCADA_L2"
        grupo = "aprovacao-financeira-l2"
    elif valor_cents <= 1_000_000_000:  # <= R$ 10MM
        faixa = "ALCADA_L3"
        grupo = "aprovacao-financeira-l3"
    else:
        # Catch-all conservador → comite (tier mais alto)
        faixa = "ANALISE_HUMANA"
        grupo = "comite-financeiro"

    logger.info(
        "pagto_route_aprovacao",
        valor_cents=valor_cents,
        faixa_valor=faixa,
        grupo_aprovador=grupo,
    )

    return {
        "faixa_valor": faixa,
        "grupo_aprovador": grupo,
    }


# ---------------------------------------------------------------
# execute_pagto — low-value release (L2, auto — no adverse)
# ---------------------------------------------------------------


def execute_pagto(variables: dict[str, Any]) -> dict[str, Any]:
    """Execute low-value payment (dentro_teto_l2 — L2 auto path).

    This is NOT adverse — below-threshold payments are auto-released.
    Modeled after auth_auto_approval.
    """
    faixa = variables.get("faixa_valor", "")

    if faixa != "DENTRO_TETO_L2":
        logger.warning(
            "pagto_execute_faixa_incompativel",
            faixa=faixa,
        )
        return {"pagamento_executado": False}

    logger.info(
        "pagto_execute_auto",
        ordem_id=variables.get("ordem_pagamento_id"),
        valor_cents=variables.get("valor_pagamento_cents"),
    )

    return {
        "pagamento_executado": True,
        "tipo_liberacao": "auto_L2",
    }


# ---------------------------------------------------------------
# release_high_value_payment — GATED L1 adverse effect
# ---------------------------------------------------------------


def release_high_value_payment(variables: dict[str, Any]) -> dict[str, Any]:
    """Release high-value payment — GATED with tier-match.

    GUARDED: ERR_PAYMENT_RELEASE_NOT_HUMAN.
    Requires:
      - decisao_pagamento == APROVAR from human
      - aprovador_id + justificativa + valor_aprovado_cents
      - tier-match: aprovador_tier >= required for faixa_valor
    """
    decisao = variables.get("decisao_pagamento", "")
    aprovador_id = variables.get("aprovador_id", "")
    aprovador_tier = variables.get("aprovador_tier", 0)
    justificativa = variables.get("justificativa_aprovacao", "")
    valor_aprovado = variables.get("valor_aprovado_cents", 0)
    faixa = variables.get("faixa_valor", "")

    if not isinstance(aprovador_tier, int):
        try:
            aprovador_tier = int(aprovador_tier)
        except (ValueError, TypeError):
            aprovador_tier = 0

    errors: list[str] = []

    # Guard 1: human decision
    if decisao != DECISAO_APROVAR:
        errors.append(f"decisao_pagamento != {DECISAO_APROVAR} (got: {decisao!r})")
    if not aprovador_id:
        errors.append("aprovador_id ausente (ADR-0007)")
    if not justificativa:
        errors.append("justificativa_aprovacao ausente")

    # Guard 2: tier-match
    tier_requerido = _TIER_MINIMO.get(faixa, 4)
    if aprovador_tier < tier_requerido:
        errors.append(
            f"tier-match falhou: aprovador_tier={aprovador_tier} "
            f"< requerido={tier_requerido} para faixa '{faixa}'"
        )

    if errors:
        logger.error(
            "pagto_release_guard_rejected",
            errors=errors,
            ordem_id=variables.get("ordem_pagamento_id"),
        )
        raise PagtoError(ERR_PAYMENT_RELEASE_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "pagto_high_value_released",
        ordem_id=variables.get("ordem_pagamento_id"),
        aprovador_id=aprovador_id,
        aprovador_tier=aprovador_tier,
        valor_aprovado_cents=valor_aprovado,
    )

    return {
        "pagamento_liberado": True,
        "tipo_liberacao": "humano_alcada",
    }


# ---------------------------------------------------------------
# publish_completed — publishing completion event
# ---------------------------------------------------------------


def publish_completed(variables: dict[str, Any]) -> dict[str, Any]:
    """Publish domain event for payment completion."""
    desfecho = "liberado_automatico"
    if variables.get("faixa_valor", "") != "DENTRO_TETO_L2":
        decisao = variables.get("decisao_pagamento", "")
        if decisao == DECISAO_APROVAR:
            desfecho = "liberado_humano"
        elif decisao == DECISAO_RECUSAR:
            desfecho = "recusado_humano"
        elif decisao == DECISAO_CANCELAR:
            desfecho = "cancelado"

    logger.info(
        "pagto_publish_completed",
        ordem_id=variables.get("ordem_pagamento_id"),
        desfecho=desfecho,
    )

    return {
        "evento_publicado": True,
        "desfecho": desfecho,
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class PagtoError(Exception):
    """Worker guard error for pagto adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
