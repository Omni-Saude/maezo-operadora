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
from maezo.tools.workers.harness import WorkerBpmnError

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


# BPMN-declared error code for GAP-CANCEL-3 (`Error_CancelManterNotHuman` in the BPMN's own
# <bpmn:error> declaration; docs/processes/contracts/SP-OP-CANCEL-001.md SS Codigos de erro).
# `confirm_maintained_decision` raises this as a `WorkerBpmnError` — NOT `CancelManterNotHumanError`
# — so the harness can report it as a MODELED `bpmnError` that the BPMN's own
# `BE_ManterNaoConfirmado` boundary event captures (-> `End_ManterNaoConfirmado`, terminal NEUTRO).
# Distinct from `ERR_CANCELLATION_NOT_HUMAN` (`send_cancellation_notice_entry`): that guard is
# declared UNCAUGHT by BPMN design (an engine incident on the RESCINDIR/SUSPENDER path is the
# correct, intended outcome). See `confirm_maintained_decision`'s docstring for the full rationale.
_ERR_CANCEL_MANTER_NOT_HUMAN = "ERR_CANCEL_MANTER_NOT_HUMAN"
_DECISAO_MANTER = "MANTER"


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


@dataclass
class CancelEffectuationResult:
    """Output of effectuate_member_request — L2 clerical (RN 412), NOT an adverse effect."""

    member_request_effectuated: bool = False
    numero_contrato: str = ""
    matricula_beneficiario: str = ""


@dataclass
class CancelMaintainedConfirmation:
    """Output of confirm_maintained_decision — GATED confirmation (GAP-CANCEL-3)."""

    maintained_decision_confirmed: bool = False
    fundamentacao_provided: bool = False
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


def effectuate_member_request(input_data: CancelInput) -> CancelEffectuationResult:
    """Effectuate the beneficiario-requested cancellation (RN 412, L2 clerical).

    NOT the adverse path: this worker EXECUTES a cancellation the MEMBER requested (a right of
    the titular), never one the operadora originates. No guard — reached only via
    cancel_admissibility=EFETIVAR_PEDIDO (DMN, engine-routed: titularidade/vinculo/prazo already
    confirmed) or a human's EFETIVAR_PEDIDO decision, itself restricted by the BPMN's own
    conditionExpression to tipo_solicitacao=pedido_beneficiario (GAP-CANCEL-7 —
    inadimplencia/for_cause_operadora/fraude_referida can never reach this worker via
    decisao_cancelamento, mirroring GW_Manter's tipo_solicitacao gate). NUNCA rescinde for-cause;
    NUNCA produz negativa do pedido. TASY write DROP (ADR-0013) — consumes CDC, never writes back.
    """
    logger.info(
        "cancel.effectuate_member_request.start",
        numero_contrato=input_data.numero_contrato,
        tipo_plano=input_data.tipo_plano,
    )
    result = CancelEffectuationResult(
        member_request_effectuated=True,
        numero_contrato=input_data.numero_contrato,
        matricula_beneficiario=input_data.matricula_beneficiario,
    )
    logger.info(
        "cancel.effectuate_member_request.complete",
        numero_contrato=input_data.numero_contrato,
    )
    return result


def confirm_maintained_decision(decision: CancelDecisionInput) -> CancelMaintainedConfirmation:
    """Confirm the MANTER decision — GATED effect (GAP-CANCEL-3).

    HARD BOUNDARY (L0 hard, contract_termination, ADR-0005/0008). GW_DecisaoCancelamento's flow
    default (`Flow_GWDec_Mantido`) fires for ANY unrecognized decisao_cancelamento — including one
    that is absent/empty/corrupted. Without a worker guard on this branch, a modeling bug or a
    malformed User Task could reach End_PedidoCancelamentoNegado (ADVERSO) or End_ContratoMantido
    without a human having actually recorded MANTER with justification. This worker closes that
    gap: mirrors send_cancellation_notice's defense-in-depth, but for decisao_cancelamento ==
    "MANTER" (strict equality — not membership: any other value, including empty, means the
    instance fell through the gateway's default WITHOUT a valid human decision) and
    fundamentacao_contratual (contract SP-OP-CANCEL-001: "fundamentacao_contratual obrigatoria se
    RESCINDIR, MANTER ou SUSPENDER").

    Raises `WorkerBpmnError(ERR_CANCEL_MANTER_NOT_HUMAN)` — a MODELED BPMN error, deliberately NOT
    `CancelManterNotHumanError` (the PermissionError `process_cancel` raises internally for its
    own, separate decision-routing use). The BPMN's own `BE_ManterNaoConfirmado` boundary event is
    a CAPTURED catch (unlike the send_cancellation_notice siblings, declared-uncaught by design —
    an incident on THAT path is the correct outcome) because an uncaught error on the MANTER
    branch would silently end a live case. Routing this guard failure to a `bpmnError` lets the
    boundary catch fire as the BPMN designs it (`End_ManterNaoConfirmado`, a NEUTRO terminal —
    nothing registered, the human recomposes the decision) instead of an opaque engine incident
    (docs/processes/contracts/SP-OP-CANCEL-001.md SS Codigos de erro; SS test-specs
    test_manter_sem_fundamentacao_bloqueado_pelo_guard: "lista de incidentes vazia").

    This worker NEVER decides whether the bond is maintained or the request denied (GW_Manter
    routes on tipo_solicitacao, an input fact) — it only confirms the MANTER decision already
    taken in the human User Task.
    """
    if decision.decisao_cancelamento != _DECISAO_MANTER:
        raise WorkerBpmnError(
            _ERR_CANCEL_MANTER_NOT_HUMAN,
            f"confirm_maintained_decision: decisao_cancelamento={decision.decisao_cancelamento!r} — "
            "manutencao do vinculo / negativa do pedido exige decisao humana explicita (MANTER) na "
            "User Task juridico-contratos (L0 hard, contract_termination). Este worker NUNCA "
            "decide; so confirma a decisao ja tomada na UT humana — inclui o flow default da "
            "gateway GW_DecisaoCancelamento (nenhuma decisao_cancelamento reconhecida cai aqui SEM "
            "confirmacao humana).",
        )
    if not decision.fundamentacao_contratual.strip():
        raise WorkerBpmnError(
            _ERR_CANCEL_MANTER_NOT_HUMAN,
            "confirm_maintained_decision: fundamentacao_contratual ausente — MANTER exige "
            "fundamentacao da decisao (contrato SP-OP-CANCEL-001 SS Variaveis de saida; L0 hard, "
            "contract_termination).",
        )

    logger.info(
        "cancel.confirm_maintained_decision.confirmed",
        responsavel_id=decision.responsavel_id,
    )
    # RETORNA APENAS confirmacao (ADR-0018 no-denial): fundamentacao_provided=True proves the
    # justification was present WITHOUT echoing its text (same caution as send_cancellation_notice
    # — free-text justification never rides the confirmation channel). responsavel_id is always
    # returned (default "" when the UT did not require it) — GAP-CANCEL-6 provenance parity with
    # ST_PublishMantido/ST_PublishPedidoNegado's event_payload_vars.
    return CancelMaintainedConfirmation(
        maintained_decision_confirmed=True,
        fundamentacao_provided=True,
        responsavel_id=decision.responsavel_id,
    )


def notify_sla_risk(input_data: CancelInput) -> dict[str, Any]:
    """Notify coordenacao-contratos of SLA risk (non-interruptive timer BT_AlertaSla).

    Informational only: UT_AnaliseRescisao stays open, no decision is made or altered. Fires at
    ~60-70% of sla.sla_analise (internal policy; DMN cancel_sla resolves the actual duration).
    """
    logger.info(
        "cancel.notify_sla_risk",
        numero_contrato=input_data.numero_contrato,
        tipo_solicitacao=input_data.tipo_solicitacao,
    )
    return {"sla_risk_notified": True, "numero_contrato": input_data.numero_contrato}


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
# (excl. shared `operadora.events.publish`, registered separately by
# `events.register_events_workers` — T3.1 R2, PR #61):
#   validate_cancel               -> operadora.cancel.resolve_facts        (spec match:
#                                     "Pre-resolve cancel/termination facts")
#   assess_admissibility          -> operadora.cancel.prepare_dossier      (spec match:
#                                     routing/motivo dossier for UT_AnaliseRescisao)
#   notify_beneficiario           -> operadora.cancel.request_notification (spec match:
#                                     prior-notice dispatch)
#   effectuate_member_request     -> operadora.cancel.effectuate_member_request (spec match,
#                                     L2 clerical, NOT adverse)
#   register_contract_termination -> operadora.cancel.send_cancellation_notice (spec match, GUARDED)
#   confirm_maintained_decision   -> operadora.cancel.confirm_maintained_decision (spec match,
#                                     GATED, GAP-CANCEL-3 — raises WorkerBpmnError so
#                                     BE_ManterNaoConfirmado's boundary catch fires; distinct from
#                                     the PermissionError-based guards elsewhere in this module)
#   notify_sla_risk                -> operadora.cancel.notify_sla_risk (spec match, informational,
#                                     non-interruptive timer)
#
# T3.1 R2 (topic reconciliation — this PR): the BPMN declares exactly 7 `operadora.cancel.*`
# topics (+ the shared `operadora.events.publish`); the registry above now maps 1:1 onto them —
# no gaps, no orphans. `process_cancel`/`publish_completed` (below) are kept as PURE, UNREGISTERED
# functions — no BPMN camunda:topic anywhere in this process (or any other, `grep -r` verified)
# references `operadora.cancel.process_cancel`/`operadora.cancel.publish_completed`; the donor's
# own `register_cancel_workers` never registered either. `process_cancel` re-validates the SAME
# guards as `register_contract_termination`/`confirm_maintained_decision` internally (dead code
# from a registry-dispatch perspective, but exercised directly by pre-existing unit tests as
# reusable decision-routing logic); `publish_completed` describes an event the generic
# `operadora.events.publish` worker (`events.py`, registered by `register_events_workers`) already
# emits for every `ST_Publish*` service task in this BPMN. Removed from `register_cancel_workers`
# (were the PROXIMATE cause of all 24 `cancel_probe`-based integration xfails — the donor's own
# ported drift-guard, `assert not missing_from_drain`, correctly refused every test at fixture
# SETUP while these 2 orphan registrations existed). Functions/entry-functions/tests kept for the
# reusable logic they still exercise; only the dead `harness.register_worker(...)` calls are gone.
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


def effectuate_member_request_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.effectuate_member_request` ->
    `effectuate_member_request`. NOT the adverse path — no guard (module docstring)."""
    del kafka  # unused — effectuate_member_request emits no domain event itself
    input_data = CancelInput(**pick_fields(variables, CancelInput))
    result = effectuate_member_request(input_data)
    return dataclasses.asdict(result)


def confirm_maintained_decision_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.confirm_maintained_decision` ->
    `confirm_maintained_decision` (GATED, GAP-CANCEL-3).

    Raises `WorkerBpmnError(ERR_CANCEL_MANTER_NOT_HUMAN)` — a MODELED BPMN error, not one of the
    `_HARNESS_CLASSIFIED` types `FunctionWorker.execute` special-cases (`base.py`), so it
    propagates unchanged to `WorkerHarness._handle`'s `except WorkerBpmnError` branch, which
    reports it as `bpmnError` when the harness's `bpmn_error_allowlist` includes
    `ERR_CANCEL_MANTER_NOT_HUMAN` (opt-in, ADR-0026 Decisao §5 — gate-proven: SP-OP-CANCEL-001 is
    the only BPMN declaring `Error_CancelManterNotHuman` with a matching boundary catch,
    `BE_ManterNaoConfirmado`). See `confirm_maintained_decision`'s docstring for why this must be a
    modeled bpmnError rather than the `PermissionError`-based guard used elsewhere in this module.
    """
    del kafka  # unused — confirm_maintained_decision emits no domain event itself
    decision = CancelDecisionInput(**pick_fields(variables, CancelDecisionInput))
    result = confirm_maintained_decision(decision)
    return dataclasses.asdict(result)


def notify_sla_risk_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.cancel.notify_sla_risk` -> `notify_sla_risk`."""
    del kafka  # unused — notify_sla_risk emits no domain event itself
    input_data = CancelInput(**pick_fields(variables, CancelInput))
    return notify_sla_risk(input_data)


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

    T3.1 R2 (topic reconciliation): registers exactly the 7 `operadora.cancel.*` topics
    `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn` declares — 1:1, no gaps, no
    orphans (see the "Topic mapping" comment above `resolve_facts_entry`). `process_cancel`/
    `publish_completed` are deliberately NOT registered here (evidence + rationale in that same
    comment) — the shared `operadora.events.publish` topic is registered separately by
    `events.register_events_workers` (PR #61), not by this module.
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
            "operadora.cancel.effectuate_member_request",
            functools.partial(effectuate_member_request_entry, kafka=kafka),
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
            "operadora.cancel.confirm_maintained_decision",
            functools.partial(confirm_maintained_decision_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.cancel.notify_sla_risk",
            functools.partial(notify_sla_risk_entry, kafka=kafka),
        )
    )
