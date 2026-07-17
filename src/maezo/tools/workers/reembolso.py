"""SP-OP-REEMBOLSO-001 Worker — Reembolso ao Beneficiario.

External tasks for beneficiary reimbursement processing.
Negativa-like L0 hard: send_reembolso_denial is GUARDED by
ERR_REEMBOLSO_DENIAL_NOT_HUMAN — denying or reducing reimbursement
only materializes after human decision in UT_AnaliseReembolso
or UT_RevisaoAuditorMedico.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from maezo.tools.workers.ceilings import CeilingResolver

logger = structlog.get_logger(__name__)

# Governance ceiling for reembolso L2 auto-approval (design T1.9 §2.3). The teto VALUE
# lives in the autonomy matrix (`reembolso_auto_approval.max_value_brl`, L0-core.yaml:24 +
# tenants-amh.yaml overlay), resolved via the SAME loader the PEP uses. With the D-07
# placeholder `max_value_brl: 0`, every within-table request routes to ANALISE_HUMANA.
_CEILING_ACTION = "reembolso_auto_approval"
_CEILING_PARAM = "max_value_brl"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ReembolsoDenialNotHumanError(PermissionError):
    """Raised when send_reembolso_denial is called without human authorization.

    Guard ERR_REEMBOLSO_DENIAL_NOT_HUMAN — the worker MUST refuse to
    communicate a denial/reduction unless decisao_reembolso in
    {NEGAR, APROVAR_PARCIAL} was set by a human with analista_id.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_REEMBOLSO_DENIAL_NOT_HUMAN: denial/reduction requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class ReembolsoProtocoloInvalidoError(ValueError):
    """Raised when protocolo/guia is inconsistent (ERR_REEMBOLSO_INVALID_PROTOCOLO)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"ERR_REEMBOLSO_INVALID_PROTOCOLO: {detail}" if detail else "ERR_REEMBOLSO_INVALID_PROTOCOLO"
        )


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class ReembolsoInput:
    """Input for reembolso processing."""

    tenant_id: str = ""
    protocolo_reembolso: str = ""
    numero_guia_tiss: str | None = None
    beneficiario_pseudo_id: str = ""
    matricula_beneficiario: str = ""
    tipo_reembolso: str = ""
    codigo_procedimento_tuss: str = ""
    categoria_procedimento: str = ""
    valor_solicitado_cents: int = 0
    data_atendimento: str = ""
    data_solicitacao: str = ""
    cid10: str | None = None
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)
    cobertura_prevista: bool = False
    documentacao_completa: bool = False
    dentro_prazo: bool = False
    beneficiario_ativo: bool = False
    carencia_cumprida: bool = False
    dentro_tabela: bool = False
    dentro_teto_l2: bool = False
    requer_avaliacao_clinica: bool = False


@dataclass
class ReembolsoValidationResult:
    """Output of validate_reembolso — pre-resolved facts."""

    valid: bool = False
    cobertura_prevista: bool = False
    documentacao_completa: bool = False
    dentro_prazo: bool = False
    beneficiario_ativo: bool = False
    carencia_cumprida: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class ReembolsoCalculoResult:
    """Output of calculate_value — arithmetic only, never decision."""

    valor_calculado_tabela_cents: int = 0
    valor_solicitado_cents: int = 0
    dentro_tabela: bool = False
    dentro_teto_l2: bool = False
    multiplo_tabela_aplicado: float = 1.0
    fonte_tabela: str = ""


@dataclass
class ReembolsoAutoApprovalResult:
    """Output of auto_approve_or_route — DMN reembolso_auto_approval."""

    recomendacao: str = "ANALISE_HUMANA"
    motivo: str = ""


@dataclass
class ReembolsoDenialInput:
    """Input for send_reembolso_denial — the gated adverse effect."""

    decisao_reembolso: str = ""
    justificativa: str = ""
    fundamentacao_contratual: str = ""
    valor_reembolso_aprovado_cents: int = 0
    valor_solicitado_cents: int = 0
    analista_id: str = ""
    auditor_id: str = ""
    cid10_referencia: str = ""
    parecer_auditor: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def validate_reembolso(input_data: ReembolsoInput) -> ReembolsoValidationResult:
    """Validate reembolso inputs and pre-resolve facts.

    Checks: cobertura_prevista, documentacao_completa, dentro_prazo,
    beneficiario_ativo, carencia_cumprida. Facts only — never decides
    NEGAR/APROVAR_PARCIAL.
    """
    logger.info(
        "reembolso.validate_reembolso.start",
        tenant_id=input_data.tenant_id,
        protocolo_reembolso=input_data.protocolo_reembolso,
    )

    errors: list[str] = []

    if not input_data.protocolo_reembolso.strip():
        errors.append("protocolo_reembolso ausente")
        raise ReembolsoProtocoloInvalidoError("protocolo_reembolso ausente")

    if not input_data.codigo_procedimento_tuss.strip():
        errors.append("codigo_procedimento_tuss ausente")

    if input_data.valor_solicitado_cents <= 0:
        errors.append("valor_solicitado_cents invalido")

    result = ReembolsoValidationResult(
        valid=len(errors) == 0,
        cobertura_prevista=input_data.cobertura_prevista,
        documentacao_completa=input_data.documentacao_completa,
        dentro_prazo=input_data.dentro_prazo,
        beneficiario_ativo=input_data.beneficiario_ativo,
        carencia_cumprida=input_data.carencia_cumprida,
        errors=errors,
    )

    logger.info(
        "reembolso.validate_reembolso.complete",
        valid=result.valid,
        errors=errors,
    )
    return result


def check_coverage(input_data: ReembolsoInput) -> dict[str, Any]:
    """Check if the procedure is covered by the contract/segmentation.

    Resolves cobertura_prevista flag. Never decides NEGAR —
    absent coverage routes to ANALISE_HUMANA.
    """
    logger.info(
        "reembolso.check_coverage.start",
        codigo_procedimento_tuss=input_data.codigo_procedimento_tuss,
        tipo_reembolso=input_data.tipo_reembolso,
    )

    cobertura_prevista = input_data.cobertura_prevista

    result = {
        "cobertura_prevista": cobertura_prevista,
        "codigo_procedimento_tuss": input_data.codigo_procedimento_tuss,
        "tipo_reembolso": input_data.tipo_reembolso,
        "categoria_procedimento": input_data.categoria_procedimento,
    }

    logger.info("reembolso.check_coverage.complete", cobertura_prevista=cobertura_prevista)
    return result


def calculate_value(
    input_data: ReembolsoInput,
    resolver: CeilingResolver | None = None,
) -> ReembolsoCalculoResult:
    """Calculate the reimbursement value — DMN reembolso_calculo.

    Computes valor_calculado_tabela_cents, dentro_tabela, dentro_teto_l2.
    Pure arithmetic — NEVER decides to pay or deny.
    The DMN says how much would be due; the eventual reduction
    (APROVAR_PARCIAL) is a HUMAN decision.

    ``dentro_teto_l2`` is COMPUTED from the tenant governance ceiling
    (``reembolso_auto_approval.max_value_brl``) via the CeilingResolver — the inbound
    ``input_data.dentro_teto_l2`` is NEVER read on this path (design T1.9 §2.3, defect B3).
    ``resolver`` is injectable for tests; the default resolves the ceiling from the real
    ``spec/policies/autonomy`` matrix.
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    logger.info(
        "reembolso.calculate_value.start",
        protocolo_reembolso=input_data.protocolo_reembolso,
        valor_solicitado_cents=input_data.valor_solicitado_cents,
    )

    # Compute table value based on procedure category
    # In production, this consults the TUSS table / tenant config
    valor_calculado = _compute_table_value(
        input_data.codigo_procedimento_tuss,
        input_data.categoria_procedimento,
        input_data.tipo_reembolso,
        input_data.valor_solicitado_cents,
    )

    dentro_tabela = input_data.valor_solicitado_cents <= valor_calculado
    # COMPUTE the ceiling fact from policy — compares the reference-table value (centavos)
    # against `reembolso_auto_approval.max_value_brl` (per L0-core.yaml:28). Ceiling 0
    # (D-07) or any config problem => False => the request routes to ANALISE_HUMANA.
    dentro_teto = resolver.within_l2_ceiling(
        tenant=input_data.tenant_id,
        action=_CEILING_ACTION,
        param=_CEILING_PARAM,
        value_cents=valor_calculado,
    )

    result = ReembolsoCalculoResult(
        valor_calculado_tabela_cents=valor_calculado,
        valor_solicitado_cents=input_data.valor_solicitado_cents,
        dentro_tabela=dentro_tabela,
        dentro_teto_l2=dentro_teto,
        multiplo_tabela_aplicado=1.0,
        fonte_tabela="TUSS-REFERENCIA",
    )

    logger.info(
        "reembolso.calculate_value.complete",
        valor_calculado=result.valor_calculado_tabela_cents,
        dentro_tabela=result.dentro_tabela,
    )
    return result


def auto_approve_or_route(
    calculo: ReembolsoCalculoResult,
    requer_avaliacao_clinica: bool,
) -> ReembolsoAutoApprovalResult:
    """Auto-approve or route to human — DMN reembolso_auto_approval.

    AUTO_APROVAR only with: dentro_tabela=true AND dentro_teto_l2=true
    AND requer_avaliacao_clinica=false.
    NO saida de negativa/reducao — those are always human.
    Catch-all -> ANALISE_HUMANA.
    """
    logger.info(
        "reembolso.auto_approve_or_route.start",
        dentro_tabela=calculo.dentro_tabela,
        dentro_teto_l2=calculo.dentro_teto_l2,
        requer_avaliacao_clinica=requer_avaliacao_clinica,
    )

    if calculo.dentro_tabela and calculo.dentro_teto_l2 and not requer_avaliacao_clinica:
        recomendacao = "AUTO_APROVAR"
        motivo = "Dentro da tabela, dentro do teto L2, sem avaliacao clinica necessaria"
    else:
        recomendacao = "ANALISE_HUMANA"
        if requer_avaliacao_clinica:
            motivo = "Requer avaliacao clinica (procedimento alta complexidade/OPME ou CID sensivel)"
        elif not calculo.dentro_tabela:
            motivo = "Valor solicitado acima da tabela de referencia"
        elif not calculo.dentro_teto_l2:
            motivo = "Valor acima do teto de auto-aprovacao L2 do tenant"
        else:
            motivo = "Catch-all conservador"

    result = ReembolsoAutoApprovalResult(recomendacao=recomendacao, motivo=motivo)

    logger.info(
        "reembolso.auto_approve_or_route.complete",
        recomendacao=result.recomendacao,
    )
    return result


def notify_beneficiario(
    beneficiario_pseudo_id: str,
    protocolo_reembolso: str,
    status: str = "",
) -> dict[str, Any]:
    """Notify beneficiario about reimbursement status."""
    logger.info(
        "reembolso.notify_beneficiario",
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        protocolo_reembolso=protocolo_reembolso,
        status=status,
    )

    return {
        "notified": True,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "protocolo_reembolso": protocolo_reembolso,
        "status": status,
    }


def process_payment(
    protocolo_reembolso: str,
    valor_cents: int,
    beneficiario_pseudo_id: str,
) -> dict[str, Any]:
    """Issue reimbursement payment (CNAB/conciliation).

    Only executes for approved reimbursements (APROVAR or AUTO_APROVAR).
    """
    logger.info(
        "reembolso.process_payment.start",
        protocolo_reembolso=protocolo_reembolso,
        valor_cents=valor_cents,
    )

    import hashlib
    import time

    comprovante_ref = f"PAY-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12].upper()}"

    result = {
        "payment_issued": True,
        "protocolo_reembolso": protocolo_reembolso,
        "valor_cents": valor_cents,
        "comprovante_pagamento_ref": comprovante_ref,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
    }

    logger.info("reembolso.process_payment.complete", comprovante_ref=comprovante_ref)
    return result


def send_reembolso_denial(denial_input: ReembolsoDenialInput) -> dict[str, Any]:
    """Send reimbursement denial/reduction — GUARDED adverse effect.

    ERR_REEMBOLSO_DENIAL_NOT_HUMAN: MUST refuse if:
    - decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}
    - Missing justificativa, fundamentacao_contratual
    - Missing analista_id (or auditor_id for clinical merit)
    - If clinical merit: missing cid10_referencia or parecer_auditor
    """
    logger.info(
        "reembolso.send_reembolso_denial.start",
        decisao_reembolso=denial_input.decisao_reembolso,
        analista_id=denial_input.analista_id,
    )

    missing: list[str] = []

    if denial_input.decisao_reembolso not in ("NEGAR", "APROVAR_PARCIAL"):
        missing.append("decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}")
    if not denial_input.justificativa.strip():
        missing.append("justificativa")
    if not denial_input.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")

    # Human identifier: analista_id or auditor_id
    has_human = bool(denial_input.analista_id.strip() or denial_input.auditor_id.strip())
    if not has_human:
        missing.append("analista_id (or auditor_id)")

    # APROVAR_PARCIAL requires a valid reduced value
    if denial_input.decisao_reembolso == "APROVAR_PARCIAL":
        if denial_input.valor_reembolso_aprovado_cents <= 0:
            missing.append("valor_reembolso_aprovado_cents")
        elif denial_input.valor_reembolso_aprovado_cents >= denial_input.valor_solicitado_cents:
            missing.append("APROVAR_PARCIAL: valor aprovado >= valor solicitado (nao e reducao)")

    if missing:
        raise ReembolsoDenialNotHumanError(missing_fields=missing)

    return {
        "denial_sent": True,
        "decisao_reembolso": denial_input.decisao_reembolso,
        "valor_solicitado_cents": denial_input.valor_solicitado_cents,
        "valor_reembolso_aprovado_cents": denial_input.valor_reembolso_aprovado_cents,
        "analista_id": denial_input.analista_id or denial_input.auditor_id,
    }


def publish_completed(
    event_type: str = "reembolso.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish reembolso completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "reembolso.publish_completed",
        event_type=event_type,
        desfecho=desfecho,
    )

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_table_value(
    codigo_procedimento_tuss: str,
    categoria_procedimento: str,
    tipo_reembolso: str,
    valor_solicitado_cents: int,
) -> int:
    """Compute reference table value for a procedure.

    In production, consults TUSS table and tenant-specific multipliers.
    For now, returns a reasonable reference value based on category.
    """
    # Stub multipliers by category (cents)
    base_values: dict[str, int] = {
        "consulta": 35000,  # R$ 350.00
        "exame_simples": 8000,  # R$ 80.00
        "exame_especial": 45000,  # R$ 450.00
        "terapia": 15000,  # R$ 150.00
        "internacao": 500000,  # R$ 5,000.00
        "opme": 300000,  # R$ 3,000.00
        "alta_complexidade": 800000,  # R$ 8,000.00
    }

    base = base_values.get(categoria_procedimento.lower(), valor_solicitado_cents)

    # Urgencia/emergencia may have higher multipliers
    if tipo_reembolso in ("urgencia_emergencia", "fora_rede"):
        base = int(base * 1.5)

    # Return max of base or solicited (for within-table check)
    # For a real implementation, this would be the exact table value
    return max(base, 0)
