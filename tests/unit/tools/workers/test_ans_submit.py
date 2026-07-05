"""Unit tests for maezo.tools.workers.ans_submit — SP-OP-ANS-SUBMIT-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.ans_submit import (
    AnsDatasetIncompletoError,
    AnsRetryEsgotadoError,
    AnsSubmissionData,
    AnsSubmitDecision,
    AnsSubmitInput,
    AnsSubmitNotHumanError,
    handle_nack,
    prepare_submission,
    publish_completed,
    retry_submission,
    transmit_to_ans,
    validate_data,
)

# ---------------------------------------------------------------------------
# prepare_submission
# ---------------------------------------------------------------------------


def test_prepare_submission() -> None:
    """prepare_submission builds a dataset stub."""
    inp = AnsSubmitInput(
        tenant_id="amh",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
        schema_valid=True,
        lgpd_anonimizado=False,
    )
    result = prepare_submission(inp)
    assert result.dataset_ref is not None
    assert result.report_type == "RN_124_SIP"
    assert result.dataset_complete is True


def test_prepare_submission_incomplete() -> None:
    """prepare_submission with dataset_ref absent -> dataset_complete=False."""
    inp = AnsSubmitInput(
        tenant_id="amh",
        report_type="DIOPS_TRIMESTRAL",
        competencia="2026-Q2",
        dataset_ref="",
    )
    result = prepare_submission(inp)
    assert result.dataset_complete is False


# ---------------------------------------------------------------------------
# validate_data
# ---------------------------------------------------------------------------


def test_validate_data_valid() -> None:
    """validate_data returns schema_valid=True for complete data."""
    submission = AnsSubmissionData(
        dataset_ref="dataset-ok",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
        schema_valid=True,
    )
    result = validate_data(submission)
    assert result["schema_valid"] is True
    assert len(result["errors"]) == 0


def test_validate_data_missing_ref() -> None:
    """validate_data flags missing dataset_ref."""
    submission = AnsSubmissionData(
        dataset_ref="",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission)
    assert result["schema_valid"] is False
    assert any("dataset_ref" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------------
# transmit_to_ans — GUARD tests
# ---------------------------------------------------------------------------


def test_transmit_to_ans_guard_not_approved() -> None:
    """transmit_to_ans raises if decisao_envio != APROVAR_ENVIO."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="ADIAR_ENVIO",
        revisor_id="reg-001",
    )
    with pytest.raises(AnsSubmitNotHumanError) as exc:
        transmit_to_ans(submission, decision)
    assert "decisao_envio" in str(exc.value)


def test_transmit_to_ans_guard_missing_revisor() -> None:
    """transmit_to_ans raises if revisor_id is missing."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="APROVAR_ENVIO",
        revisor_id="",
    )
    with pytest.raises(AnsSubmitNotHumanError) as exc:
        transmit_to_ans(submission, decision)
    assert "revisor_id" in str(exc.value)


def test_transmit_to_ans_success() -> None:
    """transmit_to_ans succeeds with approved decision."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="APROVAR_ENVIO",
        revisor_id="reg-001",
    )
    result = transmit_to_ans(submission, decision)
    assert result["submitted"] is True
    assert result["protocolo_ans"].startswith("ANSPROTO-")
    assert result["status_envio"] == "enviado"


# ---------------------------------------------------------------------------
# handle_nack
# ---------------------------------------------------------------------------


def test_handle_nack_retryable() -> None:
    """handle_nack marks transient errors as retryable."""
    result = handle_nack("ANSPROTO-1", nack_motivo="Erro transiente", retry_attempt=1)
    assert result["retryable"] is True
    assert result["status"] == "nack"


def test_handle_nack_non_retryable() -> None:
    """handle_nack marks permanent errors as non-retryable."""
    result = handle_nack("ANSPROTO-1", nack_motivo="Schema invalido permanente", retry_attempt=1)
    assert result["retryable"] is False


# ---------------------------------------------------------------------------
# retry_submission — DMN ans_retry_policy
# ---------------------------------------------------------------------------


def test_retry_submission_attempt_1() -> None:
    """Retry attempt 1 -> PT5M, continue=True."""
    result = retry_submission("ANSPROTO-1", retry_attempt=1)
    assert result.backoff == "PT5M"
    assert result.continue_retry is True


def test_retry_submission_attempt_2() -> None:
    """Retry attempt 2 -> PT30M."""
    result = retry_submission("ANSPROTO-1", retry_attempt=2)
    assert result.backoff == "PT30M"
    assert result.continue_retry is True


def test_retry_submission_attempt_3() -> None:
    """Retry attempt 3 -> PT2H."""
    result = retry_submission("ANSPROTO-1", retry_attempt=3)
    assert result.backoff == "PT2H"
    assert result.continue_retry is True


def test_retry_submission_exhausted() -> None:
    """Retry attempt > 3 -> raises ERR_ANS_RETRY_ESGOTADO."""
    with pytest.raises(AnsRetryEsgotadoError) as exc:
        retry_submission("ANSPROTO-1", retry_attempt=4)
    assert "exhausted" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_anssubmit() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="enviado_ack")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "enviado_ack"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_ans_submit_not_human_is_permission_error() -> None:
    """AnsSubmitNotHumanError must be a subclass of PermissionError."""
    assert issubclass(AnsSubmitNotHumanError, PermissionError)


def test_ans_dataset_incompleto_is_value_error() -> None:
    """AnsDatasetIncompletoError must be a subclass of ValueError."""
    assert issubclass(AnsDatasetIncompletoError, ValueError)


def test_ans_retry_esgotado_is_runtime_error() -> None:
    """AnsRetryEsgotadoError must be a subclass of RuntimeError."""
    assert issubclass(AnsRetryEsgotadoError, RuntimeError)
