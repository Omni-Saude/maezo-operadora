"""SP-OP-ANS-SUBMIT-001 Worker — Envios Periodicos ANS.

External tasks for ANS regulatory submissions (calendar-driven).
HITL pre-filing (nao-repudio ADR-0007): submit_to_ans is GUARDED by
ERR_ANS_SUBMIT_NOT_HUMAN — the official filing only occurs after
UT_RevisarEnvio with decisao_envio == APROVAR_ENVIO set by a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

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
) -> dict[str, Any]:
    """Transmit the official filing to ANS — GUARDED adverse effect.

    ERR_ANS_SUBMIT_NOT_HUMAN: MUST refuse if:
    - decisao_envio != APROVAR_ENVIO
    - Missing revisor_id

    The official filing is legally binding — only after UT_RevisarEnvio
    completed by a human (nao-repudio ADR-0007).
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

    import hashlib
    import time

    protocolo_ans = f"ANSPROTO-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16].upper()}"

    result = {
        "submitted": True,
        "protocolo_ans": protocolo_ans,
        "status_envio": "enviado",
        "data_envio": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "revisor_id": decision.revisor_id,
    }

    logger.info("ans_submit.transmit_to_ans.complete", protocolo_ans=protocolo_ans)
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
) -> AnsRetryPolicy:
    """Apply DMN ans_retry_policy for backoff and retry decision.

    retry_attempt 1 -> PT5M / continue_retry=true
    2 -> PT30M / true
    3 -> PT2H / true
    catch-all (>3) -> "" / continue_retry=false (esgotado)
    """
    logger.info(
        "ans_submit.retry_submission.start",
        protocolo_ans=protocolo_ans,
        retry_attempt=retry_attempt,
    )

    # DMN ans_retry_policy (hitPolicy FIRST)
    backoff_map: dict[int, str] = {
        1: "PT5M",
        2: "PT30M",
        3: "PT2H",
    }

    if retry_attempt <= 3:
        backoff = backoff_map.get(retry_attempt, "PT5M")
        continue_retry = True
    else:
        backoff = ""
        continue_retry = False

    result = AnsRetryPolicy(backoff=backoff, continue_retry=continue_retry)

    logger.info(
        "ans_submit.retry_submission.complete",
        backoff=result.backoff,
        continue_retry=result.continue_retry,
    )

    if not continue_retry:
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
