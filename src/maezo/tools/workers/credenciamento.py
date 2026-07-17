"""Worker: credenciamento (SP-OP-CRED-001).

(Des)credenciamento de Prestador/Rede.
Two adverse directions: register_decred (L1) and register_cred_denial.
Guards: ERR_DECRED_NOT_HUMAN, ERR_CRED_DENIAL_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

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


def assess_admissibility(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify admissibility and route — NEVER produces NEGAR/DESCREDENCIAR.

    Evaluates TWO chained deployed decision tables (ADR-0028/T1.5): `cred_admissibility`
    (direcao, tipo_prestador, documentacao_completa, licenca_valida, dentro_criterios_rede,
    indicio_irregularidade_sinalizado) -> `roteamento` in {CLERICAL_CREDENCIAR, SEGUE_ANALISE,
    PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}; ONLY when it returns `SEGUE_ANALISE` (non-terminal
    — proceed to human-track routing), chained into `cred_route` (direcao, tipo_prestador,
    origem_solicitacao, indicio_irregularidade_sinalizado) -> `roteamento` in
    {ANALISE_CREDENCIAMENTO, ANALISE_DESCREDENCIAMENTO, ANALISE_HUMANA} — the FINAL routing.
    The old Python conflated both tables' output domains into a single hand-forked if/elif
    ladder (its own `roteamento` sometimes held `cred_admissibility` values, sometimes
    `cred_route` values, matching neither table's real rule order). Both tables live-verified
    against the compose engine before cutover (5/5 existing test scenarios reproduced exactly,
    plus a new `CLERICAL_CREDENCIAR` case). Adverse-adjacent (provider credentialing) — per
    ADR-0028 §7, policy-guardian review recommended before this cutover is considered cleared.
    """
    direcao = variables.get("direcao", "credenciamento")
    tipo_prestador = variables.get("tipo_prestador", "")
    licenca_valida = variables.get("licenca_valida", False)
    documentacao_completa = variables.get("documentacao_completa", False)
    dentro_criterios = variables.get("dentro_criterios_rede", False)
    indicio_irregular = variables.get("indicio_irregularidade_sinalizado", False)

    dmn_transport = require_dmn(dmn, "operadora.cred.check_network_criteria")

    adm_rows, adm_version = evaluate_sync(
        dmn_transport,
        "cred_admissibility",
        {
            "direcao": direcao,
            "tipo_prestador": tipo_prestador,
            "documentacao_completa": bool(documentacao_completa),
            "licenca_valida": bool(licenca_valida),
            "dentro_criterios_rede": bool(dentro_criterios),
            "indicio_irregularidade_sinalizado": bool(indicio_irregular),
        },
    )
    adm_row = first_row(adm_rows, "cred_admissibility", variables)
    roteamento = str(adm_row.get("roteamento", "ANALISE_HUMANA"))
    motivo = str(adm_row.get("motivo", ""))
    route_version = None

    if roteamento == "SEGUE_ANALISE":
        route_rows, route_version = evaluate_sync(
            dmn_transport,
            "cred_route",
            {
                "direcao": direcao,
                "tipo_prestador": tipo_prestador,
                "origem_solicitacao": variables.get("origem_solicitacao", ""),
                "indicio_irregularidade_sinalizado": bool(indicio_irregular),
            },
        )
        route_row = first_row(route_rows, "cred_route", variables)
        roteamento = str(route_row.get("roteamento", roteamento))
        motivo = str(route_row.get("motivo", motivo))

    logger.info(
        "cred_assess_admissibility",
        prestador_id=variables.get("prestador_id"),
        roteamento=roteamento,
        motivo=motivo,
        dmn_admissibility_version=adm_version.version,
        dmn_route_version=route_version.version if route_version else None,
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
    """Register the SP-OP-CRED-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `assess_admissibility` (`cred_admissibility` +
    `cred_route`, T1.5 cutover) via `functools.partial`; no other function here evaluates a
    DMN table.
    """
    del kafka  # unused — no credenciamento.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    harness.register_worker(FunctionWorker("operadora.cred.verify_credentials", validate_cred))
    harness.register_worker(
        FunctionWorker(
            "operadora.cred.check_network_criteria", functools.partial(assess_admissibility, dmn=dmn)
        )
    )
    harness.register_worker(FunctionWorker("operadora.cred.check_prior_notice", notify_prestador))
    harness.register_worker(
        FunctionWorker("operadora.cred.register_descredenciamento", register_descredenciamento)
    )
    harness.register_worker(FunctionWorker("operadora.cred.register_cred_denial", register_cred_denial))
