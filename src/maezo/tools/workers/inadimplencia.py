"""Worker: inadimplencia (SP-OP-INADIMPLENCIA-001).

Suspensao/Rescisao por Inadimplencia.
Guard: ERR_CONTRACT_SUSPENSION_NOT_HUMAN (L0-hard, ADR-0018).
NAO ha worker de rescisao gated AQUI — rescisao e propriedade de CANCEL-001.
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

ERR_CONTRACT_SUSPENSION_NOT_HUMAN = "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"
ERR_INAD_INVALID_CONTRATO = "ERR_INAD_INVALID_CONTRATO"

# Human decision value that allows suspension
DECISAO_SUSPENDER = "SUSPENDER"
DECISAO_ENCAMINHAR_RESCISAO = "ENCAMINHAR_RESCISAO"
DECISAO_MANTER = "MANTER"


# ---------------------------------------------------------------
# resolve_facts — pre-resolve facts (arithmetic, no decision)
# ---------------------------------------------------------------


def resolve_facts(variables: dict[str, Any]) -> dict[str, Any]:
    """Pre-resolve factual variables: mes count, value, period, purge window.

    This worker computes factual data — it NEVER makes an adverse decision.
    All monetary values in integer cents.
    """
    competencias = variables.get("competencias_em_aberto", [])
    meses_inadimplencia = len(competencias) if isinstance(competencias, list) else 0

    valor_total = variables.get("valor_total_devido_cents", 0)
    if not isinstance(valor_total, int):
        valor_total = int(valor_total) if valor_total else 0

    # Periodo minimo: DRAFT 60 dias (2 meses)
    dentro_periodo_minimo = meses_inadimplencia >= 2

    # Janela de purga: 10 dias apos notificacao (DRAFT/verify RN 593)
    dentro_janela_purga = variables.get("dentro_janela_purga", True)

    notificacao_previa_feita = variables.get("notificacao_previa_feita", False)

    logger.info(
        "inadimplencia_resolve_facts",
        meses_inadimplencia=meses_inadimplencia,
        valor_total_devido_cents=valor_total,
        dentro_periodo_minimo=dentro_periodo_minimo,
        notificacao_previa_feita=notificacao_previa_feita,
    )

    return {
        "meses_inadimplencia": meses_inadimplencia,
        "valor_total_devido_cents": valor_total,
        "dentro_periodo_minimo": dentro_periodo_minimo,
        "notificacao_previa_feita": notificacao_previa_feita,
        "dentro_janela_purga": dentro_janela_purga,
    }


# ---------------------------------------------------------------
# assess_status — classify default status (no adverse decision)
# ---------------------------------------------------------------


def assess_status(variables: dict[str, Any]) -> dict[str, Any]:
    """Classify the default status for routing — NEVER produces SUSPENDER/RESCINDIR.

    DMN-like logic: classifica o roteamento. Todos os caminhos que nao sao
    triviais falham-safe para ANALISE_HUMANA.
    """
    dentro_min = variables.get("dentro_periodo_minimo", False)
    notificacao = variables.get("notificacao_previa_feita", False)
    dentro_purga = variables.get("dentro_janela_purga", True)
    tipo_plano = variables.get("tipo_plano", "individual")

    # Coletivos always to human
    if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
        roteamento = "ANALISE_HUMANA"
        motivo = "contrato coletivo — regras do estipulante"
    elif not notificacao:
        roteamento = "PENDENTE_NOTIFICACAO"
        motivo = "notificacao previa pendente (RN 593)"
    elif dentro_purga:
        roteamento = "AGUARDA_PURGA"
        motivo = "dentro da janela de purga/cura"
    elif dentro_min:
        roteamento = "SEGUE_ANALISE"
        motivo = "periodo minimo + notificacao + purga decorrida"
    else:
        # Catch-all conservador
        roteamento = "ANALISE_HUMANA"
        motivo = "status ambíguo — requer análise humana"

    logger.info(
        "inadimplencia_assess_status",
        tipo_plano=tipo_plano,
        roteamento=roteamento,
        motivo=motivo,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# calculate_purge — determine purge window (RN 593 DRAFT)
# ---------------------------------------------------------------


def calculate_purge(variables: dict[str, Any]) -> dict[str, Any]:
    """Calculate the purge/cure window and prior notice deadlines.

    Only determines regulatory deadlines — NEVER decides to suspend/rescind.
    All deadlines DRAFT/verify against RN 593.
    """
    tipo_plano = variables.get("tipo_plano", "individual")

    # DRAFT/verify: prazos RN 593
    if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
        prazo_purga = "P30D"  # conservative for collective
        prazo_notificacao_previa = "P60D"
        periodo_minimo = "P90D"
    else:
        prazo_purga = "P10D"
        prazo_notificacao_previa = "P50D"
        periodo_minimo = "P60D"

    logger.info(
        "inadimplencia_calculate_purge",
        tipo_plano=tipo_plano,
        prazo_purga=prazo_purga,
    )

    return {
        "prazo_purga": prazo_purga,
        "prazo_notificacao_previa": prazo_notificacao_previa,
        "periodo_minimo": periodo_minimo,
        "fonte_regulatoria": "RN 593 (DRAFT/verify)",
    }


# ---------------------------------------------------------------
# notify_beneficiario — register prior notice
# ---------------------------------------------------------------


def notify_beneficiario(variables: dict[str, Any]) -> dict[str, Any]:
    """Register/dispatch prior notice to the beneficiary (RN 593).

    This is a NEUTRAL action — it informs, it does NOT suspend/rescind.
    """
    numero_contrato = variables.get("numero_contrato", "")
    matricula = variables.get("matricula_beneficiario", "")

    logger.info(
        "inadimplencia_notify_beneficiario",
        numero_contrato=numero_contrato,
        matricula=matricula,
    )

    return {
        "notificacao_previa_feita": True,
        "notificacao_previa_registrada_em": "now",  # placeholder
    }


# ---------------------------------------------------------------
# register_suspension — GATED adverse effect (L0-hard)
# ---------------------------------------------------------------


def register_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Register contract suspension for default.

    GUARDED: ERR_CONTRACT_SUSPENSION_NOT_HUMAN.
    Requires:
      - decisao_inadimplencia == SUSPENDER from human user task
      - responsavel_id present (ADR-0007)
      - fundamentacao_contratual, referencia_regulatoria
      - comprovacao_notificacao_previa, comprovacao_periodo_minimo
      - NOT ja_em_rescisao_cancel (anti-double-termination)
    """
    return _register_contract_suspension(variables)


def _register_contract_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate guard conditions and register suspension.

    Raises ERR_CONTRACT_SUSPENSION_NOT_HUMAN if any guard fails.
    """
    decisao = variables.get("decisao_inadimplencia", "")
    responsavel_id = variables.get("responsavel_id", "")
    fundamentacao = variables.get("fundamentacao_contratual", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")
    comprovacao_notif = variables.get("comprovacao_notificacao_previa", "")
    comprovacao_periodo = variables.get("comprovacao_periodo_minimo", "")
    ja_em_rescisao = variables.get("ja_em_rescisao_cancel", False)

    errors: list[str] = []

    if decisao != DECISAO_SUSPENDER:
        errors.append(f"decisao_inadimplencia != {DECISAO_SUSPENDER} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao_contratual ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente")
    if not comprovacao_notif:
        errors.append("comprovacao_notificacao_previa ausente (RN 593)")
    if not comprovacao_periodo:
        errors.append("comprovacao_periodo_minimo ausente")
    if ja_em_rescisao:
        errors.append("ja_em_rescisao_cancel=true — contrato ja em rescisao em CANCEL-001")

    if errors:
        logger.error(
            "inadimplencia_suspension_guard_rejected",
            errors=errors,
            numero_contrato=variables.get("numero_contrato"),
        )
        raise InadimplenciaError(
            ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
            "; ".join(errors),
        )

    logger.info(
        "inadimplencia_contract_suspension_registered",
        numero_contrato=variables.get("numero_contrato"),
        responsavel_id=responsavel_id,
    )

    return {
        "suspensao_registrada": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
    }


# ---------------------------------------------------------------
# register_contract_suspension — external task handler alias
# ---------------------------------------------------------------

register_contract_suspension = _register_contract_suspension


# ---------------------------------------------------------------
# handoff_rescisao — NEUTRO handoff to CANCEL-001 (DRAFT-A shipped)
# ---------------------------------------------------------------


def handoff_rescisao(variables: dict[str, Any]) -> dict[str, Any]:
    """Neutral handoff: initiate/correlate CANCEL-001 for rescission.

    This is NOT an adverse effect — CANCEL-001 owns the sole rescission terminal.
    """
    decisao = variables.get("decisao_inadimplencia", "")
    if decisao != DECISAO_ENCAMINHAR_RESCISAO:
        logger.warning(
            "inadimplencia_handoff_rescisao_unexpected",
            decisao=decisao,
        )
        return {"handoff_executado": False}

    logger.info(
        "inadimplencia_handoff_rescisao",
        numero_contrato=variables.get("numero_contrato"),
    )

    return {
        "handoff_executado": True,
        "processo_destino": "SP-OP-CANCEL-001",
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class InadimplenciaError(Exception):
    """Worker guard error for inadimplencia adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   resolve_facts       -> operadora.inadimplencia.resolve_facts (exact spec match)
#   notify_beneficiario -> operadora.inadimplencia.check_prior_notice
#     (spec match: RN 593 prior-notice dispatch)
#   register_suspension (alias register_contract_suspension)
#     -> operadora.inadimplencia.register_contract_suspension (exact spec match, GUARDED)
#   handoff_rescisao -> operadora.inadimplencia.handoff_rescisao (exact spec match)
# assess_status/calculate_purge have no distinct spec topic (internal
# DMN-like classification feeding resolve_facts/check_prior_notice) —
# registered under function-derived topics for registry completeness.
# Spec topics with NO implementing function today (gap, not fabricated here):
# prepare_dossier, notify_sla_risk.
# ---------------------------------------------------------------


def register_inadimplencia_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-INADIMPLENCIA-001 function workers on `harness`."""
    del kafka, seams  # unused — no inadimplencia.py worker declares a Kafka/other seam dependency
    harness.register_worker(FunctionWorker("operadora.inadimplencia.resolve_facts", resolve_facts))
    harness.register_worker(FunctionWorker("operadora.inadimplencia.assess_status", assess_status))
    harness.register_worker(FunctionWorker("operadora.inadimplencia.calculate_purge", calculate_purge))
    harness.register_worker(FunctionWorker("operadora.inadimplencia.check_prior_notice", notify_beneficiario))
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.register_contract_suspension", register_contract_suspension)
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.handoff_rescisao", handoff_rescisao))
