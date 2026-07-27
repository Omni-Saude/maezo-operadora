"""SP-OP-ANS-SUBMIT-001 Worker — Envios Periodicos ANS.

External tasks for ANS regulatory submissions (calendar-driven).
HITL pre-filing (nao-repudio ADR-0007): submit_to_ans is GUARDED by
ERR_ANS_SUBMIT_NOT_HUMAN — the official filing only occurs after
UT_RevisarEnvio with decisao_envio == APROVAR_ENVIO set by a human.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.ans_gateway import AnsGatewayTransport, resolve_ans_gateway
from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.tiss_schema import TissSchemaValidator

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)

# Internal notification channel — the SAME typed-envelope channel recurso.py/lgpd.py publish their
# per-worker notifications to (`operadora.notifications.internal`; observed in tests via
# `notifications_of_type(...)`). notify_regulatorio is the one ans_submit worker that emits one.
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class AnsSubmitNotHumanError(PermissionError):
    """Raised when submit_to_ans is called without human authorization.

    Guard ERR_ANS_SUBMIT_NOT_HUMAN — the worker MUST refuse to transmit
    the official filing unless decisao_envio == APROVAR_ENVIO was set
    by a human with revisor_id present.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_ANS_SUBMIT_NOT_HUMAN: ANS submission requires human approval"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class AnsDatasetIncompletoError(ValueError):
    """Raised when dataset assembly fails (ERR_ANS_DATASET_INCOMPLETO)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"ERR_ANS_DATASET_INCOMPLETO: {detail}" if detail else "ERR_ANS_DATASET_INCOMPLETO")


class AnsRetryEsgotadoError(RuntimeError):
    """Raised when retry policy is exhausted (ERR_ANS_RETRY_ESGOTADO)."""

    def __init__(self, attempt: int = 0) -> None:
        super().__init__(f"ERR_ANS_RETRY_ESGOTADO: exhausted after {attempt} attempts")


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class AnsSubmitInput:
    """Input for ANS submission processing."""

    tenant_id: str = ""
    report_type: str = ""
    competencia: str = ""
    periodicidade: str = ""
    origem_envio: str = "calendario"
    nip_protocolo_origem: str | None = None
    dataset_complete: bool = False
    schema_valid: bool = False
    lgpd_anonimizado: bool = False
    dataset_ref: str = ""


@dataclass
class AnsSubmissionData:
    """Data prepared for ANS submission."""

    dataset_ref: str = ""
    report_type: str = ""
    competencia: str = ""
    dataset_complete: bool = False
    schema_valid: bool = False
    lgpd_anonimizado: bool = False


@dataclass
class AnsSubmitDecision:
    """Human decision for ANS submission."""

    decisao_envio: str = ""
    revisor_id: str = ""
    justificativa_adiamento: str = ""


@dataclass
class AnsRetryPolicy:
    """DMN ans_retry_policy result."""

    backoff: str = ""
    continue_retry: bool = False


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def prepare_submission(input_data: AnsSubmitInput) -> AnsSubmissionData:
    """Prepare the ANS regulatory submission dataset.

    Assembles an anonymized dataset (Zona Geral, ADR-0006).
    Delegates to mcp-regdata.assemble.
    """
    logger.info(
        "ans_submit.prepare_submission.start",
        tenant_id=input_data.tenant_id,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
    )

    # In production, this delegates to mcp-regdata.assemble
    # For now, produce stub dataset
    dataset_ref = input_data.dataset_ref or f"dataset-{input_data.report_type}-{input_data.competencia}"
    dataset_complete = input_data.dataset_complete or bool(input_data.dataset_ref)

    result = AnsSubmissionData(
        dataset_ref=dataset_ref,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
        dataset_complete=dataset_complete,
        schema_valid=input_data.schema_valid,
        lgpd_anonimizado=input_data.lgpd_anonimizado,
    )

    logger.info(
        "ans_submit.prepare_submission.complete",
        dataset_ref=result.dataset_ref,
        dataset_complete=result.dataset_complete,
    )
    return result


def validate_data(
    submission: AnsSubmissionData,
    *,
    tiss_validator: TissSchemaValidator | None = None,
) -> dict[str, Any]:
    """Validate the ANS submission dataset (XSD/TISS schema) — T2.6-2, design §2.B.

    Real `lxml.etree.XMLSchema` validation against the pinned padrão-TISS version
    (`tools/workers/tiss_schema.py`), NOT an echo of the inbound `schema_valid` flag (the old
    stub this replaces). Fail-closed on every axis: missing dataset_ref/incomplete dataset
    (unchanged structural checks), unpinned/unvendored TISS schema, unreadable/malformed XML, or
    a genuine schema violation all resolve `schema_valid=False` — NEVER an exception, NEVER an
    auto-pass. Returns `schema_valid` + structured `errors`; the BPMN routes False to
    `UT_CorrigirPendenciaEnvio` (human), never a reject.

    `tiss_validator` is the T2.6-2 seam (mirrors the `dmn`/`ans_gateway` seams already threaded
    through this module): `None` (production default, no seam injected) resolves to a real
    `TissSchemaValidator()`, which itself fails closed to `schema_valid=False` while
    `MAEZO_TISS_SCHEMA_VERSION` stays unset (today, everywhere — the version is SME-gated,
    design §2.B/§7, and the real vendored XSD set is a documented external dependency, not
    fabricated here). Dev/test inject a `TissSchemaValidator` pinned at a fixture schema root.
    """
    logger.info(
        "ans_submit.validate_data.start",
        dataset_ref=submission.dataset_ref,
        report_type=submission.report_type,
    )

    validator = tiss_validator if tiss_validator is not None else TissSchemaValidator()
    tiss_result = validator.validate(report_type=submission.report_type, dataset_ref=submission.dataset_ref)

    schema_valid = tiss_result.schema_valid
    errors: list[str] = list(tiss_result.errors)

    if not submission.dataset_ref.strip():
        errors.append("dataset_ref ausente")
        schema_valid = False
    if not submission.dataset_complete:
        errors.append("dataset incompleto")
        schema_valid = False

    result = {
        "schema_valid": schema_valid,
        "dataset_ref": submission.dataset_ref,
        "errors": errors,
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "tiss_schema_version": tiss_result.tiss_schema_version,
    }

    logger.info(
        "ans_submit.validate_data.complete",
        schema_valid=schema_valid,
        errors=errors,
        tiss_schema_version=tiss_result.tiss_schema_version,
    )
    return result


def transmit_to_ans(
    submission: AnsSubmissionData,
    decision: AnsSubmitDecision,
    *,
    gateway: AnsGatewayTransport | None = None,
    business_key: str = "",
) -> dict[str, Any]:
    """Transmit the official filing to ANS — GUARDED adverse effect.

    ERR_ANS_SUBMIT_NOT_HUMAN: MUST refuse if:
    - decisao_envio != APROVAR_ENVIO
    - Missing revisor_id

    The official filing is legally binding — only after UT_RevisarEnvio
    completed by a human (nao-repudio ADR-0007).

    Protocol issuance goes through the explicit `AnsGatewayTransport` seam (T2.6-1, design §2.A),
    NEVER a fabricated `sha256(time_ns)` number. `gateway=None` resolves fail-closed to
    `RefusingAnsGatewayTransport` (the production default: refuses, raises
    `AnsGatewayUnavailableError`, issues no protocol until real ANS credentials are wired). Dev/test
    inject `LabeledMockAnsGatewayTransport` (deterministic `MOCK-ANS-NAO-VINCULATIVO-{business_key}`).
    The `ERR_ANS_SUBMIT_NOT_HUMAN` guard fires FIRST, before any gateway touch — orthogonal to the
    gateway and unchanged.
    """
    logger.info(
        "ans_submit.transmit_to_ans.start",
        report_type=submission.report_type,
        decisao_envio=decision.decisao_envio,
        revisor_id=decision.revisor_id,
    )

    missing: list[str] = []

    if decision.decisao_envio != "APROVAR_ENVIO":
        missing.append("decisao_envio != APROVAR_ENVIO")
    if not decision.revisor_id.strip():
        missing.append("revisor_id")

    if missing:
        raise AnsSubmitNotHumanError(missing_fields=missing)

    import time

    # Route protocol issuance through the explicit gateway. Refusing (prod default) RAISES here —
    # no result dict with a fabricated protocol is ever built. Mock returns a deterministic,
    # unmistakably-synthetic protocol keyed on the business key.
    protocol = resolve_ans_gateway(gateway).submit(
        business_key=business_key,
        report_type=submission.report_type,
        competencia=submission.competencia,
        dataset_ref=submission.dataset_ref,
        revisor_id=decision.revisor_id,
    )

    result = {
        "submitted": True,
        "protocolo_ans": protocol.protocolo_ans,
        "status_envio": protocol.status_envio,
        "synthetic": protocol.synthetic,
        "vinculativo": protocol.vinculativo,
        "data_envio": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "revisor_id": decision.revisor_id,
    }

    logger.info(
        "ans_submit.transmit_to_ans.complete",
        protocolo_ans=protocol.protocolo_ans,
        synthetic=protocol.synthetic,
        vinculativo=protocol.vinculativo,
    )
    return result


def handle_nack(
    protocolo_ans: str,
    nack_motivo: str = "",
    retry_attempt: int = 0,
) -> dict[str, Any]:
    """Handle a NACK from ANS — determine if retryable.

    ERR_ANS_PROTOCOLO_NACK: enters SUB_RetryEnvio subprocess.
    Transient NACK -> retry; permanent NACK -> UT_TratarNack (human).
    """
    logger.info(
        "ans_submit.handle_nack",
        protocolo_ans=protocolo_ans,
        nack_motivo=nack_motivo,
        retry_attempt=retry_attempt,
    )

    # Determine if NACK is retryable
    nack_lower = nack_motivo.lower()
    retryable = any(
        kw in nack_lower
        for kw in ("timeout", "transient", "temporary", "servico_indisponivel", "tente_novamente")
    )

    return {
        "protocolo_ans": protocolo_ans,
        "nack_motivo": nack_motivo,
        "retryable": retryable,
        "retry_attempt": retry_attempt,
        "status": "nack",
    }


def retry_submission(
    protocolo_ans: str,
    retry_attempt: int,
    report_type: str = "",
    competencia: str = "",
    *,
    dmn: DmnTransport,
) -> AnsRetryPolicy:
    """Evaluate DMN `ans_retry_policy` for backoff and retry decision (ADR-0028/T1.5).

    The attempt-count -> backoff/continue_retry ladder (1 -> PT5M, 2 -> PT30M, 3 -> PT2H,
    catch-all >3 -> ""/continue_retry=false) used to be hand-forked here; it now lives ONLY in
    the deployed `ans_retry_policy` decision table (ADR-0012) — golden-parity proven 1:1
    against attempts 1-3 and the catch-all before this cutover (T1.5 PR body / evidence
    ledger). `report_type`/`competencia` are accepted for call-site compatibility (unused by
    this decision — the table keys only on `retry_attempt`).
    """
    del report_type, competencia  # unused by ans_retry_policy — kept for call-site compatibility
    logger.info(
        "ans_submit.retry_submission.start",
        protocolo_ans=protocolo_ans,
        retry_attempt=retry_attempt,
    )

    rows, version = evaluate_sync(dmn, "ans_retry_policy", {"retry_attempt": retry_attempt})
    row = first_row(rows, "ans_retry_policy", {"retry_attempt": retry_attempt})
    result = AnsRetryPolicy(
        backoff=str(row.get("backoff", "")),
        continue_retry=bool(row.get("continue_retry", False)),
    )

    logger.info(
        "ans_submit.retry_submission.complete",
        backoff=result.backoff,
        continue_retry=result.continue_retry,
        dmn_decision_version=version.version,
    )

    if not result.continue_retry:
        raise AnsRetryEsgotadoError(attempt=retry_attempt)

    return result


def publish_completed(
    event_type: str = "anssubmit.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish ANS submission completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "ans_submit.publish_completed",
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
# notify_regulatorio — the ONE ans_submit worker that emits an internal
# notification (event-gap design, mirrors recurso.py's raw-async handlers).
# Serves BOTH BPMN tasks on `regulatorio.anssubmit.notify_regulatorio`:
#   ST_PrepararDossie      — dossie de envio do agente Gustavo (resumo do
#                            dataset + regras DMN + due_date). INSTRUI a decisao
#                            humana de aprovar o envio; NUNCA transmite (Gustavo
#                            L1 require_human) — sem efeito adverso.
#   ST_NotificarDeadlineRisk — alerta nao-interruptivo de risco de prazo
#                            (reusado por BT_DeadlineRisk/Pendencia/Juridico).
# Raw async handler (not a `FunctionWorker` dict boundary) for the SAME reason
# recurso's notify_sla_risk/track_status are: it needs the async Kafka seam to
# publish the notification the donor emitted (`anssubmit.notify_regulatorio`);
# the sync `FunctionWorker.execute` boundary cannot reach `await kafka.publish`.
# ---------------------------------------------------------------------------

_NOTIFY_REGULATORIO_TOPIC = "regulatorio.anssubmit.notify_regulatorio"
_NOTIFY_REGULATORIO_NOTIFICATION_TYPE = "anssubmit.notify_regulatorio"


@dataclass
class AnsNotifyRegulatorioInput:
    """Input for `notify_regulatorio` (ST_PrepararDossie + ST_NotificarDeadlineRisk)."""

    tenant_id: str = ""
    report_type: str = ""
    competencia: str = ""
    periodicidade: str = ""


def notify_regulatorio(
    input_data: AnsNotifyRegulatorioInput,
    *,
    event_topic_deadline_risk: str = "",
) -> dict[str, Any]:
    """Convoca o agente Gustavo (dossie de envio) / notifica risco de prazo — NAO decide.

    Informational only, no adverse effect: the dossie INSTRUI a decisao humana de aprovar o
    envio (UT_RevisarEnvio) e o alerta de deadline-risk e nao-interruptivo (a UT segue aberta).
    Gustavo NUNCA transmite autonomamente (PEP require_human, ans_official_submission L1) — this
    worker only assembles/announces, it never touches the ANS gateway or sets `decisao_envio`.

    `event_topic_deadline_risk` is `ST_NotificarDeadlineRisk`'s own BPMN `inputParameter`
    (`agents.events.anssubmit.deadline_risk`); when present it marks this invocation as the
    deadline-risk variant (vs the dossie-prep variant on `ST_PrepararDossie`, which carries none).
    """
    is_deadline_risk = bool(event_topic_deadline_risk)
    logger.info(
        "ans_submit.notify_regulatorio",
        tenant_id=input_data.tenant_id,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
        variant="deadline_risk" if is_deadline_risk else "dossie",
    )
    return {
        "notify_regulatorio_sent": True,
        "report_type": input_data.report_type,
        "competencia": input_data.competencia,
        "deadline_risk": is_deadline_risk,
    }


def make_notify_regulatorio_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `regulatorio.anssubmit.notify_regulatorio`.

    Mirrors recurso.py's `make_notify_sla_risk_handler` exactly (raw `harness.register()` handler,
    NOT a `FunctionWorker`): publishes the `anssubmit.notify_regulatorio` typed notification to
    `operadora.notifications.internal` (the donor's own per-worker notification — its
    `test_happy_path_envio_aprovado_e_acked` asserts
    `notifications_of_type("anssubmit.notify_regulatorio")` is non-empty).

    kafka=None (fail-closed, evidenced — same decision as events.py/recurso.py): completes the
    external task ANYWAY (informational-only; blocking here would starve the default
    `Flow_GW_Revisao` path to UT_RevisarEnvio and every shared deadline-risk timer), logging
    LOUDLY instead of ever fabricating a publish. No BPMN gateway routes on this worker's output.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = AnsNotifyRegulatorioInput(**pick_fields(task.variables, AnsNotifyRegulatorioInput))
        event_topic_deadline_risk = str(task.variables.get("event_topic_deadline_risk") or "")
        result = notify_regulatorio(input_data, event_topic_deadline_risk=event_topic_deadline_risk)
        if kafka is None:
            logger.warning(
                "ans_submit_notify_regulatorio_no_producer",
                business_key=task.business_key,
                report_type=input_data.report_type,
            )
            return result
        notification = {
            "type": _NOTIFY_REGULATORIO_NOTIFICATION_TYPE,
            "tenant_id": input_data.tenant_id,
            "report_type": input_data.report_type,
            "competencia": input_data.competencia,
            "periodicidade": input_data.periodicidade,
            "deadline_risk": result["deadline_risk"],
        }
        if event_topic_deadline_risk:
            notification["event_topic_deadline_risk"] = event_topic_deadline_risk
        # Posture: topic-default best-effort BY DESIGN (DL-0038, t2-notify-integrity keep) — this
        # notification is ADVISORY on both variants it serves: (a) dossie (ST_PrepararDossie, the
        # MAIN ANS-filing path — an incident here would stall the regulatory pipeline for a ping;
        # UT_RevisarEnvio is engine-created regardless and BT_DueDate*/BT_DeadlineRisk* guard the
        # deadline engine-side), and (b) deadline-risk (ST_NotificarDeadlineRisk — the
        # fail-closed `agents.events.anssubmit.deadline_risk` publish one step downstream
        # incidents the branch on a real broker outage anyway). Deliberately NOT
        # best_effort=False — pinned by test_ans_submit.py.
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=task.business_key or None)
        return result

    return handler


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a
# flat dict). The typed functions are byte-identical; only these NEW
# functions marshal the BPMN dict boundary.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   prepare_submission -> regulatorio.anssubmit.assemble        (exact spec match: "Montar dataset")
#   validate_data       -> regulatorio.anssubmit.validate        (exact spec match)
#   transmit_to_ans      -> regulatorio.anssubmit.submit          (exact spec match, GUARDED)
#   handle_nack          -> regulatorio.anssubmit.track_protocol  (spec match: "Correlacionar
#                            status do protocolo ANS")
#   retry_submission      -> regulatorio.anssubmit.retransmit      (exact spec match)
# publish_completed has no distinct spec topic (folds into the generic
# events.publish task per BPMN) — function-derived topic.
#   notify_regulatorio -> regulatorio.anssubmit.notify_regulatorio (shared by 2
#     distinct BPMN tasks: ST_PrepararDossie dossie prep + ST_NotificarDeadlineRisk
#     deadline-risk notice). Implemented ABOVE as a raw async handler
#     (`make_notify_regulatorio_handler`), NOT a dict-boundary entry — it emits the
#     `anssubmit.notify_regulatorio` notification the donor's worker emitted, which
#     needs the async Kafka seam a sync FunctionWorker.execute boundary cannot reach.
# ---------------------------------------------------------------------------


def assemble_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.assemble` -> `prepare_submission`."""
    del kafka  # unused — prepare_submission emits no domain event
    input_data = AnsSubmitInput(**pick_fields(variables, AnsSubmitInput))
    result = prepare_submission(input_data)
    return dataclasses.asdict(result)


def validate_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    tiss_validator: TissSchemaValidator | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.validate` -> `validate_data`.

    `tiss_validator` (T2.6-2 seam, design §2.B — threaded the same way T2.6-1's `ans_gateway` is)
    is threaded via `register_ans_submit_workers`'s `**seams` — production injects nothing, so
    `validate_data` resolves a real `TissSchemaValidator()` (fail-closed while the version is
    SME-gated); dev/test inject one pinned at a fixture schema root.
    """
    del kafka  # unused — validate_data emits no domain event
    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    return validate_data(submission, tiss_validator=tiss_validator)


def _ans_business_key(variables: dict[str, Any]) -> str:
    """Derive the SP-OP-ANS-SUBMIT-001 business key `ANSSUB-{tenant}-{report_type}-{competencia}`
    (contract "Business key") from the flat process-variable dict.

    Prefers an explicit `business_key` variable (the engine's own instance business key) when
    present; otherwise reconstructs it from `tenant_id`/`report_type`/`competencia`. This is the key
    the `LabeledMockAnsGatewayTransport` derives its deterministic protocol from — so a retransmit
    for the same competência yields the same synthetic protocol (BPMN `:461` idempotency).
    """
    bk = variables.get("business_key")
    if isinstance(bk, str) and bk.strip():
        return bk
    tenant_id = str(variables.get("tenant_id", ""))
    report_type = str(variables.get("report_type", ""))
    competencia = str(variables.get("competencia", ""))
    return f"ANSSUB-{tenant_id}-{report_type}-{competencia}"


def submit_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    ans_gateway: AnsGatewayTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.submit` -> `transmit_to_ans` (GUARDED).

    Raises `AnsSubmitNotHumanError` (fail-closed, ERR_ANS_SUBMIT_NOT_HUMAN) when the human
    review (`decisao_envio == APROVAR_ENVIO` + `revisor_id`) is missing — unchanged guard.

    `ans_gateway` (T2.6-1 seam, design §2.A) is the protocol-issuance transport threaded via
    `register_ans_submit_workers`'s `**seams`. Production injects nothing → `resolve_ans_gateway`
    picks `RefusingAnsGatewayTransport` (refuses, fail-closed); dev/test inject
    `LabeledMockAnsGatewayTransport`.
    """
    del kafka  # unused — transmit_to_ans emits no domain event itself (publish_completed does)
    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    decision = AnsSubmitDecision(**pick_fields(variables, AnsSubmitDecision))
    return transmit_to_ans(
        submission, decision, gateway=ans_gateway, business_key=_ans_business_key(variables)
    )


def track_protocol_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.track_protocol` -> `handle_nack`.

    Fail-closed (ADR-0026 §2b): `protocolo_ans` is required to correlate a NACK — missing/blank
    raises `AnsDatasetIncompletoError` (this module's own `*Invalido*`-class ValueError) rather
    than silently tracking an empty protocol.
    """
    del kafka  # unused — handle_nack emits no domain event
    protocolo_ans = variables.get("protocolo_ans", "")
    if not isinstance(protocolo_ans, str) or not protocolo_ans.strip():
        raise AnsDatasetIncompletoError("protocolo_ans ausente/invalido para track_protocol")
    nack_motivo = variables.get("nack_motivo", "")
    retry_attempt = variables.get("retry_attempt", 0)
    return handle_nack(protocolo_ans, nack_motivo, retry_attempt)


def retransmit_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    dmn: DmnTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.retransmit` -> `retry_submission`.

    Fail-closed: missing/blank `protocolo_ans` raises `AnsDatasetIncompletoError`. Exhaustion
    raises `AnsRetryEsgotadoError` (`RuntimeError` family) unchanged — the harness's existing
    transient classification applies (engine-side retry, incident at 0). `dmn` unwired raises
    `DmnEvaluationError` (`require_dmn`, ADR-0028) — also transient/engine-retried.
    """
    del kafka  # unused — retry_submission emits no domain event
    protocolo_ans = variables.get("protocolo_ans", "")
    if not isinstance(protocolo_ans, str) or not protocolo_ans.strip():
        raise AnsDatasetIncompletoError("protocolo_ans ausente/invalido para retransmit")
    retry_attempt = variables.get("retry_attempt", 0)
    report_type = variables.get("report_type", "")
    competencia = variables.get("competencia", "")
    result = retry_submission(
        protocolo_ans,
        retry_attempt,
        report_type,
        competencia,
        dmn=require_dmn(dmn, "regulatorio.anssubmit.retransmit"),
    )
    return dataclasses.asdict(result)


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "anssubmit.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_ans_submit_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ANS-SUBMIT-001 workers on `harness`.

    Six dict-boundary `FunctionWorker` entries (assemble/validate/submit/track_protocol/
    retransmit/publish_completed) PLUS one raw async handler
    (`make_notify_regulatorio_handler` on `regulatorio.anssubmit.notify_regulatorio`, via
    `harness.register` not `register_worker` — it emits the `anssubmit.notify_regulatorio`
    notification the sync `FunctionWorker` boundary cannot; mirrors recurso's raw handlers).

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded to every entry function via
    `functools.partial` for signature parity with modules that DO emit domain events; none of
    these entry functions calls `kafka.publish` today (the typed functions only DESCRIBE the
    event to publish — see `publish_completed_entry` — a genuine `kafka.publish` fan-out would
    need an async seam distinct from these sync entry points; not fabricated here).

    `dmn` (ADR-0028 §1 seam) is threaded ONLY into `retransmit_entry` — the sole function here
    that evaluates a DMN table (`ans_retry_policy`, T1.5 cutover); every other entry function
    ignores it (dict `**seams` passthrough, not a hand-maintained per-module signature).

    `ans_gateway` (T2.6-1 seam, design §2.A) is the ANS protocol-issuance transport threaded ONLY
    into `submit_entry`. PRODUCTION injects nothing here (`register_all_workers` passes no
    `ans_gateway`), so `submit_entry` receives `None` and `resolve_ans_gateway` fails closed to
    `RefusingAnsGatewayTransport` — production refuses to issue a protocol (never fabricates) until
    real ANS credentials are wired. Dev/test/integration explicitly inject
    `LabeledMockAnsGatewayTransport`.

    `tiss_validator` (T2.6-2 seam, design §2.B) is the TISS/XSD validation resolver threaded
    ONLY into `validate_entry`. PRODUCTION injects nothing here, so `validate_data` resolves a
    real `TissSchemaValidator()` — fail-closed (`schema_valid=False`) until a real vendored XSD
    set + pinned version exist (SME-gated, `MAEZO_TISS_SCHEMA_VERSION` unset everywhere today).
    Dev/test inject a `TissSchemaValidator` pinned at a fixture schema root.
    """
    dmn = seams.get("dmn")
    ans_gateway = seams.get("ans_gateway")
    tiss_validator = seams.get("tiss_validator")
    harness.register_worker(
        FunctionWorker("regulatorio.anssubmit.assemble", functools.partial(assemble_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.validate",
            functools.partial(validate_entry, kafka=kafka, tiss_validator=tiss_validator),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.submit",
            functools.partial(submit_entry, kafka=kafka, ans_gateway=ans_gateway),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.track_protocol", functools.partial(track_protocol_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.retransmit",
            functools.partial(retransmit_entry, kafka=kafka, dmn=dmn),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.publish_completed",
            functools.partial(publish_completed_entry, kafka=kafka),
        )
    )
    # notify_regulatorio: raw async handler (NOT register_worker) — mirrors recurso's
    # notify_sla_risk / events.publish. Serves the BPMN topic shared by ST_PrepararDossie
    # (dossie prep, DEFAULT Flow_GW_Revisao happy path) and ST_NotificarDeadlineRisk (all 3
    # shared deadline-risk boundary timers). Was MISSING before — the process stalled at
    # ST_PrepararDossie with "no handler registered for topic" (T3.1 phase-2 FINDING A).
    harness.register(
        _NOTIFY_REGULATORIO_TOPIC,
        make_notify_regulatorio_handler(kafka),
    )
