"""Unit tests for SP-OP-LGPD-DSR-001 workers — TDD London School.

Tests LGPD DSR workers: validate_identity, assess_request, execute_export,
execute_rectification, execute_erasure, publish_completed.

CRITICAL: Workers must NEVER make adverse decisions (accusation of fraud, denial).
"""

from __future__ import annotations

from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, ERR_FRAUD_ACCUSATION_NOT_HUMAN
from maezo.tools.workers.lgpd import (
    AssessRequestWorker,
    ExecuteErasureWorker,
    ExecuteExportWorker,
    ExecuteRectificationWorker,
    PublishCompletedWorker,
    ValidateIdentityWorker,
)

# ---------------------------------------------------------------------------
# validate_identity
# ---------------------------------------------------------------------------


def test_validate_identity_topic() -> None:
    """validate_identity worker must have topic 'operadora.lgpd.verify_identity'."""
    worker = ValidateIdentityWorker()
    assert worker.topic == "operadora.lgpd.verify_identity"


def test_validate_identity_confirms_valid_id() -> None:
    """validate_identity confirms identity when pseudo_id is present."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc123",
            "canal": "portal",
        }
    )

    assert result["identidade_confirmada"] is True
    assert result["status"] == "verified"


def test_validate_identity_rejects_empty_pseudo_id() -> None:
    """validate_identity raises identity error when pseudo_id is empty/missing.

    Per GAP-LGPD-6: absent/empty titular_pseudo_id raises ERR_DSR_IDENTITY_UNVERIFIED.
    This is a TECHNICAL guard (impossibilidade mecanica), NEVER an accusation.
    """
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "",
            "canal": "whatsapp",
        }
    )

    assert result["identidade_confirmada"] is False
    assert result["status"] == "identity_unverified"
    assert result["error_code"] == "ERR_DSR_IDENTITY_UNVERIFIED"


def test_validate_identity_never_accuses_fraud() -> None:
    """validate_identity must NEVER automatically accuse fraud.

    L0 hard: fraud_accusation is intocavel. Even with obviously fake data,
    the worker only reports inability to verify mechanically.
    """
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": None,  # missing
        }
    )

    # Must not contain fraud accusation language
    assert "fraud" not in str(result).lower()
    assert "acusacao" not in str(result).lower()
    assert result["error_code"] != ERR_FRAUD_ACCUSATION_NOT_HUMAN
    # Must be a technical, fail-safe outcome
    assert result["status"] == "identity_unverified"


# ---------------------------------------------------------------------------
# assess_request
# ---------------------------------------------------------------------------


def test_assess_request_topic() -> None:
    """assess_request worker must have the correct topic."""
    worker = AssessRequestWorker()
    assert worker.topic == "operadora.lgpd.assess_request"


def test_assess_request_routes_by_type() -> None:
    """assess_request determines the fluxo based on tipo_requisicao."""
    worker = AssessRequestWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "confirmacao_acesso",
            "envolve_dados_saude": False,
        }
    )

    assert result["status"] == "assessed"
    assert "fluxo" in result


def test_assess_request_data_saude_routes_to_juridico() -> None:
    """Requests involving health data go to juridico-privacidade."""
    worker = AssessRequestWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "eliminacao",
            "envolve_dados_saude": True,
        }
    )

    assert result["grupo_revisor"] == "juridico-privacidade"


def test_assess_request_non_saude_routes_to_dpo() -> None:
    """Requests without health data go to dpo."""
    worker = AssessRequestWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "confirmacao_acesso",
            "envolve_dados_saude": False,
        }
    )

    assert result["grupo_revisor"] == "dpo"


# ---------------------------------------------------------------------------
# execute_erasure (LGPD art. 18)
# ---------------------------------------------------------------------------


def test_execute_erasure_topic() -> None:
    """execute_erasure worker must have topic 'operadora.lgpd.execute_erasure'."""
    worker = ExecuteErasureWorker()
    assert worker.topic == "operadora.lgpd.execute_erasure"


def test_execute_erasure_blocks_without_human_approval() -> None:
    """execute_erasure MUST block without human approval (DPO/juridico).

    L0 hard: data erasure is an adverse action — requires human authorization.
    """
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            # no human_approved
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_execute_erasure_with_human_approval() -> None:
    """execute_erasure proceeds when human_approved flag is present."""
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            "human_approved": True,
            "fundamentacao_legal": "Art. 18, LGPD",
        }
    )

    assert result["status"] == "erasure_completed"
    assert result["data_type"] == "erasure"


def test_execute_erasure_never_erases_without_decision() -> None:
    """execute_erasure must NEVER proceed when decisao_dsr is missing/invalid."""
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            # no decisao_dsr
            "human_approved": True,
        }
    )

    assert result["status"] != "erasure_completed"


def test_execute_erasure_respects_negativa_fundamentada() -> None:
    """execute_erasure must NOT erase when decisao_dsr == NEGAR_FUNDAMENTADO."""
    worker = ExecuteErasureWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "NEGAR_FUNDAMENTADO",
            "fundamentacao_legal": "Retencao legal obrigatoria — Lei 13.787/2018",
            "human_approved": True,
        }
    )

    # Should NOT erase — this is a human decision to deny
    assert result["status"] == "erasure_blocked"
    assert result["reason"] == "negada_fundamentada"


# ---------------------------------------------------------------------------
# execute_export
# ---------------------------------------------------------------------------


def test_execute_export_topic() -> None:
    """execute_export worker topic."""
    worker = ExecuteExportWorker()
    assert worker.topic == "operadora.lgpd.execute_export"


def test_execute_export_compiles_data() -> None:
    """execute_export compiles a data package for the titular."""
    worker = ExecuteExportWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "APROVAR_ENVIO",
            "human_approved": True,
        }
    )

    assert result["status"] == "export_compiled"
    assert "package_ref" in result


def test_execute_export_guard_blocks_without_human() -> None:
    """execute_export must block without human approval."""
    worker = ExecuteExportWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "APROVAR_ENVIO",
        }
    )

    assert result["status"] == "blocked_by_guard"


# ---------------------------------------------------------------------------
# execute_rectification
# ---------------------------------------------------------------------------


def test_execute_rectification_topic() -> None:
    """execute_rectification worker topic."""
    worker = ExecuteRectificationWorker()
    assert worker.topic == "operadora.lgpd.execute_rectification"


def test_execute_rectification_applies_correction() -> None:
    """execute_rectification applies the requested data correction."""
    worker = ExecuteRectificationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "decisao_dsr": "EXECUTAR_E_ENVIAR",
            "human_approved": True,
        }
    )

    assert result["status"] == "rectification_completed"
    assert result["data_type"] == "rectification"


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_topic() -> None:
    """publish_completed worker topic."""
    worker = PublishCompletedWorker()
    assert worker.topic == "operadora.lgpd.publish_completed"


def test_publish_completed_publishes_event() -> None:
    """publish_completed publishes the final completion event."""
    worker = PublishCompletedWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc",
            "desfecho": "atendida",
        }
    )

    assert result["status"] == "published"
    assert result["event"] == "agents.events.lgpd_dsr.completed"
    assert result["desfecho"] == "atendida"


def test_publish_completed_includes_desfecho() -> None:
    """publish_completed must include the final desfecho."""
    worker = PublishCompletedWorker()

    for desfecho in ["atendida", "negada_fundamentada", "identidade_inverificavel"]:
        result = worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "pseudo-abc",
                "desfecho": desfecho,
            }
        )
        assert result["desfecho"] == desfecho
