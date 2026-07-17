"""SP-OP-CANCEL-001 Worker — Cancelamento / Rescisao Contratual.

External tasks for contract cancellation and termination.
contract_termination is L0 hard: send_cancellation_notice is GUARDED by
ERR_CANCELLATION_NOT_HUMAN, and confirm_maintained_decision is GUARDED by
ERR_CANCEL_MANTER_NOT_HUMAN — adverse effects on the contract only
materialize after human decision in UT_AnaliseRescisao.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class CancellationNotHumanError(PermissionError):
    """Raised when send_cancellation_notice is called without human authorization.

    Guard ERR_CANCELLATION_NOT_HUMAN — the worker MUST refuse to issue
    formal rescission/suspension notice unless decisao_cancelamento
    in {RESCINDIR, SUSPENDER} was set by a human with responsavel_id
    and required justifications.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_CANCELLATION_NOT_HUMAN: rescission/suspension requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class CancelManterNotHumanError(PermissionError):
    """Raised when confirm_maintained_decision is called without human authorization.

    Guard ERR_CANCEL_MANTER_NOT_HUMAN — the worker MUST refuse to confirm
    MANTER decision unless decisao_cancelamento == MANTER was set by a human
    with fundamentacao_contratual present.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_CANCEL_MANTER_NOT_HUMAN: MANTER decision requires human justification"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class CancelContratoInvalidoError(ValueError):
    """Raised when contract is inconsistent (ERR_CANCEL_INVALID_CONTRATO)."""

    def __init__(self, detail: str = "") -> None:
        msg = f"ERR_CANCEL_INVALID_CONTRATO: {detail}" if detail else "ERR_CANCEL_INVALID_CONTRATO"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class CancelInput:
    """Input for cancel processing."""

    tenant_id: str = ""
    numero_contrato: str = ""
    matricula_beneficiario: str = ""
    tipo_solicitacao: str = ""
    origem_solicitacao: str = ""
    tipo_plano: str = ""
    motivo_informado: str | None = None
    dentro_prazo: bool = False
    notificacao_previa_feita: bool = False
    titularidade_confirmada: bool = False
    vinculo_ativo: bool = False
    meses_inadimplencia: int | None = None
    data_solicitacao_iso: str = ""
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CancelValidationResult:
    """Output of validate_cancel — pre-resolved facts."""

    valid: bool = False
    dentro_prazo: bool = False
    notificacao_previa_feita: bool = False
    titularidade_confirmada: bool = False
    vinculo_ativo: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class CancelAdmissibilityResult:
    """Output of assess_admissibility — DMN cancel_admissibility."""

    roteamento: str = "ANALISE_HUMANA"
    motivo: str = ""
    natureza_caso: str = "indeterminado"
    grupo_sugerido: str = "juridico-contratos"


@dataclass
class CancelDecisionInput:
    """Human decision for cancel process."""

    decisao_cancelamento: str = ""
    fundamentacao_contratual: str = ""
    referencia_regulatoria: str = ""
    comprovacao_notificacao_previa: str = ""
    responsavel_id: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def validate_cancel(input_data: CancelInput) -> CancelValidationResult:
    """Validate and pre-resolve cancel/termination facts.

    Computes: dentro_prazo, notificacao_previa_feita, titularidade_confirmada,
    vinculo_ativo. Facts only — never decides RESCINDIR/MANTER/SUSPENDER.
    """
    logger.info(
        "cancel.validate_cancel.start",
        tenant_id=input_data.tenant_id,
        numero_contrato=input_data.numero_contrato,
        tipo_solicitacao=input_data.tipo_solicitacao,
    )

    errors: list[str] = []

    # Validate contract key
    if not input_data.numero_contrato.strip() and not input_data.matricula_beneficiario.strip():
        errors.append("numero_contrato e matricula_beneficiario ausentes")

    # Validate tipo_solicitacao
    valid_solicitacoes = {"pedido_beneficiario", "inadimplencia", "for_cause_operadora", "fraude_referida"}
    if input_data.tipo_solicitacao not in valid_solicitacoes:
        errors.append(f"tipo_solicitacao invalido: {input_data.tipo_solicitacao}")

    # vinculo_ativo check
    vinculo_ativo = input_data.vinculo_ativo
    if not vinculo_ativo:
        # Could still be valid (e.g., pedido_beneficiario with inactive contract)
        pass

    result = CancelValidationResult(
        valid=len(errors) == 0,
        dentro_prazo=input_data.dentro_prazo,
        notificacao_previa_feita=input_data.notificacao_previa_feita,
        titularidade_confirmada=input_data.titularidade_confirmada,
        vinculo_ativo=vinculo_ativo,
        errors=errors,
    )

    logger.info("cancel.validate_cancel.complete", valid=result.valid, errors=errors)
    return result


def assess_admissibility(
    input_data: CancelInput,
    validation: CancelValidationResult,
) -> CancelAdmissibilityResult:
    """Assess cancel admissibility — DMN cancel_admissibility.

    Routes: EFETIVAR_PEDIDO | SEGUE_ANALISE | PENDENTE_NOTIFICACAO | ANALISE_HUMANA.
    NO saida RESCINDIR/NEGAR/RETER — those are always human decisions.

    cancel_routing DMN enriches with natureza_caso and grupo_sugerido
    (informational, never gates a decision).
    """
    logger.info(
        "cancel.assess_admissibility.start",
        tipo_solicitacao=input_data.tipo_solicitacao,
        tipo_plano=input_data.tipo_plano,
    )

    tipo_sol = input_data.tipo_solicitacao
    tipo_plano = input_data.tipo_plano
    roteamento = "ANALISE_HUMANA"
    motivo = ""
    natureza_caso = "indeterminado"
    grupo_sugerido = "juridico-contratos"

    if tipo_sol == "fraude_referida":
        roteamento = "ANALISE_HUMANA"
        motivo = "Indicio de fraude requer analise humana (L0 hard)"
        natureza_caso = "fraude_referida"
        grupo_sugerido = "juridico-contratos"
    elif tipo_sol == "pedido_beneficiario":
        natureza_caso = "pedido_titular"
        if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
            roteamento = "ANALISE_HUMANA"
            motivo = "Contrato coletivo — rescisao cabe ao estipulante"
            grupo_sugerido = "juridico-contratos"
        elif validation.dentro_prazo and validation.titularidade_confirmada and validation.vinculo_ativo:
            roteamento = "EFETIVAR_PEDIDO"
            motivo = "Direito do titular — cancelamento a pedido (L2 clerical)"
            grupo_sugerido = "gestao-contratos"
        else:
            roteamento = "ANALISE_HUMANA"
            motivo = "Titularidade/prazo/vinculo nao confirmados"
    elif tipo_sol in ("inadimplencia", "for_cause_operadora"):
        natureza_caso = "inadimplencia" if tipo_sol == "inadimplencia" else "for_cause"
        if not validation.notificacao_previa_feita:
            roteamento = "PENDENTE_NOTIFICACAO"
            motivo = "Notificacao previa pendente (RN 593)"
        else:
            roteamento = "SEGUE_ANALISE"
            motivo = "Notificacao previa confirmada — segue analise humana"

    result = CancelAdmissibilityResult(
        roteamento=roteamento,
        motivo=motivo,
        natureza_caso=natureza_caso,
        grupo_sugerido=grupo_sugerido,
    )

    logger.info(
        "cancel.assess_admissibility.complete",
        roteamento=result.roteamento,
        natureza_caso=result.natureza_caso,
    )
    return result


def notify_beneficiario(
    matricula_beneficiario: str,
    numero_contrato: str,
    message_type: str = "",
) -> dict[str, Any]:
    """Notify beneficiario about cancel/termination status."""
    logger.info(
        "cancel.notify_beneficiario",
        matricula_beneficiario=matricula_beneficiario,
        numero_contrato=numero_contrato,
        message_type=message_type,
    )

    return {
        "notified": True,
        "matricula_beneficiario": matricula_beneficiario,
        "numero_contrato": numero_contrato,
        "message_type": message_type,
    }


def process_cancel(
    input_data: CancelInput,
    decision: CancelDecisionInput,
) -> dict[str, Any]:
    """Process the cancel/termination based on human decision.

    Routes to appropriate effector based on decisao_cancelamento:
    - RESCINDIR -> send_cancellation_notice (guarded)
    - SUSPENDER -> send_cancellation_notice (guarded)
    - MANTER -> confirm_maintained_decision (guarded)
    - EFETIVAR_PEDIDO -> effectuate_member_request (L2 clerical)
    """
    logger.info(
        "cancel.process_cancel.start",
        decisao_cancelamento=decision.decisao_cancelamento,
        responsavel_id=decision.responsavel_id,
    )

    if decision.decisao_cancelamento in ("RESCINDIR", "SUSPENDER"):
        _guard_cancellation(decision)
        return {
            "action": "send_cancellation_notice",
            "decisao": decision.decisao_cancelamento,
            "responsavel_id": decision.responsavel_id,
            "numero_contrato": input_data.numero_contrato,
        }
    elif decision.decisao_cancelamento == "MANTER":
        _guard_manter(decision)
        return {
            "action": "confirm_maintained_decision",
            "decisao": decision.decisao_cancelamento,
            "responsavel_id": decision.responsavel_id,
            "numero_contrato": input_data.numero_contrato,
        }
    elif decision.decisao_cancelamento == "EFETIVAR_PEDIDO":
        return {
            "action": "effectuate_member_request",
            "decisao": decision.decisao_cancelamento,
            "numero_contrato": input_data.numero_contrato,
        }
    else:
        return {
            "action": "unknown",
            "decisao": decision.decisao_cancelamento,
            "error": "Decisao nao reconhecida — roteando a humano",
        }


def register_contract_termination(decision: CancelDecisionInput) -> dict[str, Any]:
    """Register contract termination — GUARDED adverse effect.

    ERR_CANCELLATION_NOT_HUMAN: MUST refuse if:
    - decisao_cancelamento not in {RESCINDIR, SUSPENDER}
    - Missing fundamentacao_contratual, referencia_regulatoria,
      comprovacao_notificacao_previa, or responsavel_id
    """
    logger.info(
        "cancel.register_contract_termination.start",
        decisao_cancelamento=decision.decisao_cancelamento,
        responsavel_id=decision.responsavel_id,
    )

    _guard_cancellation(decision)

    return {
        "registered": True,
        "decisao": decision.decisao_cancelamento,
        "responsavel_id": decision.responsavel_id,
        "data_efeito_iso": _current_date_iso(),
    }


def publish_completed(
    event_type: str = "cancel.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish cancel completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "cancel.publish_completed",
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
# Guard helpers (reused by process_cancel and register_contract_termination)
# ---------------------------------------------------------------------------


def _guard_cancellation(decision: CancelDecisionInput) -> None:
    """Enforce ERR_CANCELLATION_NOT_HUMAN guard."""
    missing: list[str] = []

    if decision.decisao_cancelamento not in ("RESCINDIR", "SUSPENDER"):
        missing.append("decisao_cancelamento not in {RESCINDIR, SUSPENDER}")
    if not decision.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")
    if not decision.referencia_regulatoria.strip():
        missing.append("referencia_regulatoria")
    if not decision.comprovacao_notificacao_previa.strip():
        missing.append("comprovacao_notificacao_previa")
    if not decision.responsavel_id.strip():
        missing.append("responsavel_id")

    if missing:
        raise CancellationNotHumanError(missing_fields=missing)


def _guard_manter(decision: CancelDecisionInput) -> None:
    """Enforce ERR_CANCEL_MANTER_NOT_HUMAN guard."""
    missing: list[str] = []

    if decision.decisao_cancelamento != "MANTER":
        missing.append("decisao_cancelamento != MANTER")
    if not decision.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")

    if missing:
        raise CancelManterNotHumanError(missing_fields=missing)


def _current_date_iso() -> str:
    """Return current date in ISO format."""
    import time

    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   validate_cancel      -> operadora.cancel.resolve_facts        (spec match: "Pre-resolve
#                           cancel/termination facts")
#   assess_admissibility -> operadora.cancel.prepare_dossier      (spec match: routing/motivo
#                           dossier for UT_AnaliseRescisao)
#   notify_beneficiario  -> operadora.cancel.request_notification (spec match: prior-notice dispatch)
#   register_contract_termination -> operadora.cancel.send_cancellation_notice (spec match, GUARDED)
# process_cancel is an internal decision router with no single spec topic
# (its RESCINDIR/SUSPENDER branch re-validates the SAME guard as
# register_contract_termination; its MANTER/EFETIVAR_PEDIDO branches have no
# dedicated gated effector in this module) — registered under a
# function-derived topic for registry completeness, NOT as
# confirm_maintained_decision/effectuate_member_request (that would imply
# guard coverage this module does not independently provide for those two
# spec topics — left unmapped rather than mis-wired).
# Spec topics with NO implementing function today (gap, not fabricated here):
# confirm_maintained_decision, effectuate_member_request, notify_sla_risk.
# ---------------------------------------------------------------------------


def resolve_facts_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.resolve_facts` -> `validate_cancel`."""
    del kafka  # unused — validate_cancel emits no domain event
    input_data = CancelInput(**pick_fields(variables, CancelInput))
    result = validate_cancel(input_data)
    return dataclasses.asdict(result)


def prepare_dossier_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.prepare_dossier` -> `assess_admissibility`."""
    del kafka  # unused — assess_admissibility emits no domain event
    input_data = CancelInput(**pick_fields(variables, CancelInput))
    validation = CancelValidationResult(**pick_fields(variables, CancelValidationResult))
    result = assess_admissibility(input_data, validation)
    return dataclasses.asdict(result)


def request_notification_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.request_notification` -> `notify_beneficiario`."""
    del kafka  # unused — notify_beneficiario emits no domain event
    matricula = variables.get("matricula_beneficiario", "")
    numero_contrato = variables.get("numero_contrato", "")
    message_type = variables.get("message_type", "")
    return notify_beneficiario(matricula, numero_contrato, message_type)


def send_cancellation_notice_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.send_cancellation_notice` ->
    `register_contract_termination` (GUARDED).

    Raises `CancellationNotHumanError` (fail-closed, ERR_CANCELLATION_NOT_HUMAN) when the human
    decision (`decisao_cancelamento` in {RESCINDIR, SUSPENDER} + required justification fields)
    is missing — unchanged guard, only the dict<->dataclass marshalling is new.
    """
    del kafka  # unused — register_contract_termination emits no domain event itself
    decision = CancelDecisionInput(**pick_fields(variables, CancelDecisionInput))
    return register_contract_termination(decision)


def process_cancel_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.process_cancel` -> `process_cancel`.

    `process_cancel` re-applies `_guard_cancellation`/`_guard_manter` internally for the
    RESCINDIR/SUSPENDER/MANTER branches — unchanged guard behavior.
    """
    del kafka  # unused — process_cancel emits no domain event
    input_data = CancelInput(**pick_fields(variables, CancelInput))
    decision = CancelDecisionInput(**pick_fields(variables, CancelDecisionInput))
    return process_cancel(input_data, decision)


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "cancel.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_cancel_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-CANCEL-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale.
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    harness.register_worker(
        FunctionWorker("operadora.cancel.resolve_facts", functools.partial(resolve_facts_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.prepare_dossier", functools.partial(prepare_dossier_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.request_notification",
            functools.partial(request_notification_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.send_cancellation_notice",
            functools.partial(send_cancellation_notice_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.process_cancel", functools.partial(process_cancel_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.publish_completed", functools.partial(publish_completed_entry, kafka=kafka)
        )
    )
