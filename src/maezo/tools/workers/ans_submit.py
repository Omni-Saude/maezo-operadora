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

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)


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


def validate_data(submission: AnsSubmissionData) -> dict[str, Any]:
    """Validate the ANS submission dataset (XSD/TISS schema).

    Returns schema_valid flag and any validation errors.
    Never rejects — routes to human for correction.
    """
    logger.info(
        "ans_submit.validate_data.start",
        dataset_ref=submission.dataset_ref,
        report_type=submission.report_type,
    )

    # In production, validates against XSD/TISS schemas
    schema_valid = submission.schema_valid
    errors: list[str] = []

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
    }

    logger.info("ans_submit.validate_data.complete", schema_valid=schema_valid, errors=errors)
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
# Spec topic with NO implementing function today (gap, not fabricated here):
# notify_regulatorio (shared by 2 distinct BPMN tasks: dossie prep + deadline-risk notice).
# ---------------------------------------------------------------------------


def assemble_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.assemble` -> `prepare_submission`."""
    del kafka  # unused — prepare_submission emits no domain event
    input_data = AnsSubmitInput(**pick_fields(variables, AnsSubmitInput))
    result = prepare_submission(input_data)
    return dataclasses.asdict(result)


def validate_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.validate` -> `validate_data`."""
    del kafka  # unused — validate_data emits no domain event
    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    return validate_data(submission)


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
    """Register the SP-OP-ANS-SUBMIT-001 dict-boundary entry functions on `harness`.

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
    """
    dmn = seams.get("dmn")
    ans_gateway = seams.get("ans_gateway")
    harness.register_worker(
        FunctionWorker("regulatorio.anssubmit.assemble", functools.partial(assemble_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker("regulatorio.anssubmit.validate", functools.partial(validate_entry, kafka=kafka))
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
