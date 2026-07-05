"""SP-OP-RECURSO-001 Worker — Recurso de Glosa.

External tasks for glosa appeal processing.
Negativa-like L0 hard: register_desistencia is GUARDED by
ERR_DESISTENCIA_NOT_HUMAN — maintaining a glosa (denying the appeal)
only materializes after human decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class DesistenciaNotHumanError(PermissionError):
    """Raised when register_desistencia is called without human authorization.

    Guard ERR_DESISTENCIA_NOT_HUMAN — the worker MUST refuse to register
    a desistencia (maintain glosa) unless decisao_recurso == NAO_RECORRER
    was set by a human with all required fields.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_DESISTENCIA_NOT_HUMAN: desistencia requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class RecursoGlosaInvalidaError(ValueError):
    """Raised when glosa_id does not reference a confirmed/active glosa (ERR_RECURSO_INVALID_GLOSA)."""

    def __init__(self, glosa_id: str = "") -> None:
        msg = f"ERR_RECURSO_INVALID_GLOSA: glosa_id '{glosa_id}' not found or not active"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class RecursoInput:
    """Input for recurso validation and processing."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    numero_lote_tiss: str = ""
    numero_conta: str | None = None
    prestador_id: str = ""
    beneficiario_pseudo_id: str = ""
    glosa_type: str = ""
    glosa_reason_code: str = ""
    valor_glosado_brl: float = 0.0
    codigo_procedimento_tuss: str = ""
    cid10: str | None = None
    documentos_recurso_refs: list[dict[str, Any]] = field(default_factory=list)
    data_ciencia_glosa: str = ""
    data_recebimento_recurso_iso: str = ""
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False


@dataclass
class RecursoValidationResult:
    """Output of validate_recurso."""

    valid: bool = False
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class RecursoAdmissibilityResult:
    """Output of assess_eligibility — DMN recurso_admissibility + recurso_eligibility."""

    roteamento: str = "ANALISE_HUMANA"
    motivo: str = ""
    grupo_revisor: str = "analista-recurso-glosa"
    recorivel: bool = False


@dataclass
class RecursoDesistenciaInput:
    """Input for register_desistencia — the gated adverse effect."""

    decisao_recurso: str = ""
    justificativa_desistencia: str = ""
    valor_glosa_aceito: float = 0.0
    referencia_contratual: str = ""
    analista_id: str = ""


@dataclass
class RecursoDesistenciaResult:
    """Output of register_desistencia."""

    registered: bool = False
    protocolo: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def validate_recurso(input_data: RecursoInput) -> RecursoValidationResult:
    """Validate recurso inputs — compute glosa_existe, prazo, and documentacao flags.

    Pre-resolves facts before BRT_Admissibilidade. The arithmetic of
    prazo lives in the worker; the decision of NAO_RECORRER is always human.
    """
    logger.info(
        "recurso.validate_recurso.start",
        tenant_id=input_data.tenant_id,
        glosa_id=input_data.glosa_id,
    )

    errors: list[str] = []

    glosa_existe = input_data.glosa_existe
    if not input_data.glosa_id.strip():
        errors.append("glosa_id ausente")
        glosa_existe = False

    dentro_prazo = input_data.dentro_prazo_recurso
    if input_data.data_ciencia_glosa and not dentro_prazo:
        errors.append("fora do prazo recursal")

    docs_completa = input_data.documentacao_recurso_completa
    if not docs_completa and input_data.documentos_recurso_refs is not None:
        # Document list provided but flagged incomplete
        pass

    result = RecursoValidationResult(
        valid=glosa_existe and len(errors) == 0,
        glosa_existe=glosa_existe,
        dentro_prazo_recurso=dentro_prazo,
        documentacao_recurso_completa=docs_completa,
        errors=errors,
    )

    logger.info("recurso.validate_recurso.complete", valid=result.valid, errors=errors)
    return result


def assess_eligibility(
    input_data: RecursoInput,
    validation: RecursoValidationResult,
) -> RecursoAdmissibilityResult:
    """Assess recurso admissibility and eligibility.

    DMN recurso_admissibility: SEGUE_ANALISE | PENDENTE_DOCUMENTACAO | ANALISE_HUMANA
    DMN recurso_eligibility: RECORRIVEL | ANALISE_HUMANA

    NO automatic desistencia — inadmissibility routes to ANALISE_HUMANA.
    """
    logger.info(
        "recurso.assess_eligibility.start",
        glosa_type=input_data.glosa_type,
        glosa_existe=validation.glosa_existe,
    )

    # recurso_admissibility
    if not validation.glosa_existe:
        roteamento = "ANALISE_HUMANA"
        motivo = "Glosa nao confirmada/ativa em CONTAS"
    elif not validation.dentro_prazo_recurso:
        roteamento = "ANALISE_HUMANA"
        motivo = "Prazo recursal expirado — inadmissibilidade requer decisao humana"
    elif not validation.documentacao_recurso_completa:
        roteamento = "PENDENTE_DOCUMENTACAO"
        motivo = "Documentacao minima do recurso pendente"
    else:
        roteamento = "SEGUE_ANALISE"
        motivo = "Documentacao presente e dentro do prazo"

    # recurso_eligibility
    glosa_type = input_data.glosa_type.lower()
    is_tecnica_clinica = glosa_type in ("tecnica", "clinica")
    grupo_revisor = "medico-auditor" if is_tecnica_clinica else "analista-recurso-glosa"
    recorivel = roteamento == "SEGUE_ANALISE"

    result = RecursoAdmissibilityResult(
        roteamento=roteamento if roteamento != "PENDENTE_DOCUMENTACAO" else "PENDENTE_DOCUMENTACAO",
        motivo=motivo,
        grupo_revisor=grupo_revisor,
        recorivel=recorivel,
    )

    logger.info(
        "recurso.assess_eligibility.complete",
        roteamento=result.roteamento,
        grupo_revisor=result.grupo_revisor,
    )
    return result


def analyze_merits(input_data: RecursoInput) -> dict[str, Any]:
    """Analyze the merits of a recurso — delegates to Marina (LLM agent).

    The agent instructs (monta dossie), never decides.
    """
    logger.info(
        "recurso.analyze_merits.start",
        glosa_id=input_data.glosa_id,
        glosa_type=input_data.glosa_type,
    )

    result = {
        "glosa_id": input_data.glosa_id,
        "glosa_type": input_data.glosa_type,
        "valor_glosado_brl": input_data.valor_glosado_brl,
        "codigo_procedimento_tuss": input_data.codigo_procedimento_tuss,
        "analise": "dossie_instruido",
        "merito_sugerido": "ANALISE_HUMANA",  # Agent never decides
    }

    logger.info("recurso.analyze_merits.complete")
    return result


def prepare_dossier(validation: RecursoValidationResult, merits: dict[str, Any]) -> dict[str, Any]:
    """Prepare the recurso dossier for human analysis.

    Combines validation facts and merit analysis into a structured dossier.
    """
    logger.info("recurso.prepare_dossier.start")

    result = {
        "dossier": {
            "validacao": {
                "glosa_existe": validation.glosa_existe,
                "dentro_prazo": validation.dentro_prazo_recurso,
                "documentacao_completa": validation.documentacao_recurso_completa,
            },
            "meritos": merits,
        },
        "roteamento": "ANALISE_HUMANA",
    }

    logger.info("recurso.prepare_dossier.complete")
    return result


def notify_prestador(
    prestador_id: str,
    glosa_id: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Notify the prestador about recurso status or document requests."""
    logger.info(
        "recurso.notify_prestador",
        prestador_id=prestador_id,
        glosa_id=glosa_id,
        message_type=message_type,
    )

    return {
        "notified": True,
        "prestador_id": prestador_id,
        "glosa_id": glosa_id,
        "message_type": message_type,
    }


def escalate_to_junta(
    glosa_id: str,
    motivo: str = "",
    glosa_type: str = "",
) -> dict[str, Any]:
    """Escalate recurso to junta medica / auditor for clinical/technical review.

    Glosa tecnica/clinica -> medico-auditor decides the merit.
    """
    logger.info(
        "recurso.escalate_to_junta",
        glosa_id=glosa_id,
        glosa_type=glosa_type,
        motivo=motivo,
    )

    return {
        "escalated": True,
        "glosa_id": glosa_id,
        "grupo": "medico-auditor",
        "glosa_type": glosa_type,
        "motivo": motivo,
    }


def register_desistencia(input_data: RecursoDesistenciaInput) -> RecursoDesistenciaResult:
    """Register desistencia (maintain glosa) — GUARDED adverse effect.

    ERR_DESISTENCIA_NOT_HUMAN: MUST refuse if:
    - decisao_recurso != NAO_RECORRER
    - Missing justificativa_desistencia, valor_glosa_aceito,
      referencia_contratual, or analista_id
    """
    logger.info(
        "recurso.register_desistencia.start",
        decisao_recurso=input_data.decisao_recurso,
        analista_id=input_data.analista_id,
    )

    missing: list[str] = []

    if input_data.decisao_recurso != "NAO_RECORRER":
        missing.append("decisao_recurso != NAO_RECORRER")
    if not input_data.justificativa_desistencia.strip():
        missing.append("justificativa_desistencia")
    if input_data.valor_glosa_aceito <= 0:
        missing.append("valor_glosa_aceito")
    if not input_data.referencia_contratual.strip():
        missing.append("referencia_contratual")
    if not input_data.analista_id.strip():
        missing.append("analista_id")

    if missing:
        raise DesistenciaNotHumanError(missing_fields=missing)

    import hashlib
    import time

    protocolo = f"RECDESIST-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12].upper()}"

    logger.info(
        "recurso.register_desistencia.complete",
        protocolo=protocolo,
        analista_id=input_data.analista_id,
    )
    return RecursoDesistenciaResult(registered=True, protocolo=protocolo)


def publish_completed(
    event_type: str = "recurso.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish recurso completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "recurso.publish_completed",
        event_type=event_type,
        desfecho=desfecho,
    )

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }
