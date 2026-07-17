"""Worker: credenciamento (SP-OP-CRED-001).

(Des)credenciamento de Prestador/Rede.
Two adverse directions: register_decred (L1) and register_cred_denial.
Guards: ERR_DECRED_NOT_HUMAN, ERR_CRED_DENIAL_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_DECRED_NOT_HUMAN = "ERR_DECRED_NOT_HUMAN"
ERR_CRED_DENIAL_NOT_HUMAN = "ERR_CRED_DENIAL_NOT_HUMAN"
ERR_CRED_INVALID_PRESTADOR = "ERR_CRED_INVALID_PRESTADOR"

# Decision values
DECISAO_DESCREDENCIAR = "DESCREDENCIAR"
DECISAO_NEGAR_CREDENCIAMENTO = "NEGAR_CREDENCIAMENTO"
DECISAO_APROVAR_CREDENCIAMENTO = "APROVAR_CREDENCIAMENTO"
DECISAO_MANTER = "MANTER"


# ---------------------------------------------------------------
# validate_cred — verify provider credentials (FACT, never deny)
# ---------------------------------------------------------------


def validate_cred(variables: dict[str, Any]) -> dict[str, Any]:
    """Verify professional registration/CNES validity — FACT only.

    NEVER decides to deny or de-credential. TASY write DROP (ADR-0013).
    """
    documentos = variables.get("documentos_refs", {})

    # Factual check: are documents present and license supposedly valid?
    # In real implementation, this queries external registries.
    licenca_valida = True  # placeholder — real: query CRM/CNES
    documentacao_completa = isinstance(documentos, dict) and len(documentos) > 0

    logger.info(
        "cred_validate_cred",
        prestador_id=variables.get("prestador_id"),
        licenca_valida=licenca_valida,
        documentacao_completa=documentacao_completa,
    )

    return {
        "licenca_valida": licenca_valida,
        "documentacao_completa": documentacao_completa,
    }


# ---------------------------------------------------------------
# assess_admissibility — classify (NEVER deny/decred)
# ---------------------------------------------------------------


def assess_admissibility(variables: dict[str, Any]) -> dict[str, Any]:
    """Classify admissibility and route — NEVER produces NEGAR/DESCREDENCIAR.

    DMN-like: cred_admissibility + cred_route (inverted from reference).
    """
    direcao = variables.get("direcao", "credenciamento")
    licenca_valida = variables.get("licenca_valida", False)
    documentacao_completa = variables.get("documentacao_completa", False)
    dentro_criterios = variables.get("dentro_criterios_rede", False)
    indicio_irregular = variables.get("indicio_irregularidade_sinalizado", False)

    if indicio_irregular:
        roteamento = "ANALISE_DESCREDENCIAMENTO"
        motivo = "indicio de irregularidade sinalizado — humano decide"
    elif not documentacao_completa:
        roteamento = "PENDENTE_DOCUMENTACAO"
        motivo = "documentacao incompleta"
    elif not licenca_valida or not dentro_criterios:
        roteamento = "ANALISE_HUMANA"
        motivo = "licenca ou criterios de rede — requer analise humana"
    elif direcao == "descredenciamento":
        roteamento = "ANALISE_DESCREDENCIAMENTO"
        motivo = "descredenciamento em analise humana"
    elif direcao == "credenciamento":
        roteamento = "ANALISE_CREDENCIAMENTO"
        motivo = "credenciamento em analise humana"
    else:
        # Catch-all conservador
        roteamento = "ANALISE_HUMANA"
        motivo = "direcao ambigua — requer analise humana"

    logger.info(
        "cred_assess_admissibility",
        prestador_id=variables.get("prestador_id"),
        roteamento=roteamento,
        motivo=motivo,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# notify_prestador — neutral notification
# ---------------------------------------------------------------


def notify_prestador(variables: dict[str, Any]) -> dict[str, Any]:
    """Notify provider about the (de)credentialing process (NEUTRAL)."""
    prestador_id = variables.get("prestador_id", "")
    logger.info("cred_notify_prestador", prestador_id=prestador_id)

    return {
        "notificacao_previa_feita": True,
    }


# ---------------------------------------------------------------
# register_decred — GATED adverse effect A (descredenciamento)
# ---------------------------------------------------------------


def register_decred(variables: dict[str, Any]) -> dict[str, Any]:
    """Register provider de-credentialing.

    GUARDED: ERR_DECRED_NOT_HUMAN.
    Requires decisao_cred == DESCREDENCIAR from human + required fields.
    """
    return _register_descredenciamento(variables)


def _register_descredenciamento(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate guard and register de-credentialing."""
    decisao = variables.get("decisao_cred", "")
    responsavel_id = variables.get("responsavel_id", "")
    fundamentacao = variables.get("fundamentacao", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")
    comprovacao_notif = variables.get("comprovacao_notificacao_previa", "")
    tem_benef = variables.get("tem_beneficiarios_vinculados", False)
    plano_substituicao = variables.get("plano_substituicao", "")

    errors: list[str] = []

    if decisao != DECISAO_DESCREDENCIAR:
        errors.append(f"decisao_cred != {DECISAO_DESCREDENCIAR} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 567)")
    if not comprovacao_notif:
        errors.append("comprovacao_notificacao_previa ausente (RN 567)")
    if tem_benef and not plano_substituicao:
        errors.append("plano_substituicao ausente (ha beneficiarios vinculados, RN 567)")

    if errors:
        logger.error(
            "cred_decred_guard_rejected",
            errors=errors,
            prestador_id=variables.get("prestador_id"),
        )
        raise CredError(ERR_DECRED_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "cred_prestador_descredenciado",
        prestador_id=variables.get("prestador_id"),
        responsavel_id=responsavel_id,
    )

    return {
        "descredenciamento_registrado": True,
        "network_changed": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
    }


register_descredenciamento = _register_descredenciamento


# ---------------------------------------------------------------
# register_cred_denial — GATED adverse effect B (negativa de credenciamento)
# ---------------------------------------------------------------


def register_cred_denial(variables: dict[str, Any]) -> dict[str, Any]:
    """Register denial of credentialing application.

    GUARDED: ERR_CRED_DENIAL_NOT_HUMAN.
    """
    decisao = variables.get("decisao_cred", "")
    responsavel_id = variables.get("responsavel_id", "")
    fundamentacao = variables.get("fundamentacao", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")

    errors: list[str] = []

    if decisao != DECISAO_NEGAR_CREDENCIAMENTO:
        errors.append(f"decisao_cred != {DECISAO_NEGAR_CREDENCIAMENTO} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 566)")

    if errors:
        logger.error(
            "cred_denial_guard_rejected",
            errors=errors,
            prestador_id=variables.get("prestador_id"),
        )
        raise CredError(ERR_CRED_DENIAL_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "cred_credenciamento_negado",
        prestador_id=variables.get("prestador_id"),
        responsavel_id=responsavel_id,
    )

    return {
        "credenciamento_negado": True,
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class CredError(Exception):
    """Worker guard error for credenciamento adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   validate_cred        -> operadora.cred.verify_credentials     (exact spec match)
#   assess_admissibility -> operadora.cred.check_network_criteria (spec match: RN 566 criteria
#                           classification)
#   notify_prestador     -> operadora.cred.check_prior_notice     (spec match: RN 567 prior-notice
#                           dispatch)
#   register_decred (alias register_descredenciamento)
#     -> operadora.cred.register_descredenciamento (exact spec match, GUARDED)
#   register_cred_denial -> operadora.cred.register_cred_denial     (exact spec match, GUARDED)
# Spec topics with NO implementing function today (gap, not fabricated here):
# register_credenciamento, notify_doc_pendente, prepare_dossier, notify_sla_risk.
# ---------------------------------------------------------------


def register_credenciamento_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-CRED-001 function workers on `harness`."""
    del kafka, seams  # unused — no credenciamento.py worker declares a Kafka/other seam dependency
    harness.register_worker(FunctionWorker("operadora.cred.verify_credentials", validate_cred))
    harness.register_worker(FunctionWorker("operadora.cred.check_network_criteria", assess_admissibility))
    harness.register_worker(FunctionWorker("operadora.cred.check_prior_notice", notify_prestador))
    harness.register_worker(
        FunctionWorker("operadora.cred.register_descredenciamento", register_descredenciamento)
    )
    harness.register_worker(FunctionWorker("operadora.cred.register_cred_denial", register_cred_denial))
