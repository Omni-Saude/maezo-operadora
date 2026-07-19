"""Unit tests for SP-OP-LGPD-DSR-001 workers — TDD London School.

Tests LGPD DSR workers: validate_identity, assess_request, execute_export,
execute_rectification, execute_erasure, publish_completed.

CRITICAL: Workers must NEVER make adverse decisions (accusation of fraud, denial).
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, ERR_FRAUD_ACCUSATION_NOT_HUMAN
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.lgpd import (
    AssessRequestWorker,
    ExecuteErasureWorker,
    ExecuteExportWorker,
    ExecuteRectificationWorker,
    PublishCompletedWorker,
    ValidateIdentityWorker,
)


def _lgpd_dsr_routing_fake(
    *, fluxo: str, grupo_revisor: str, sla_resposta: str = "P15D", sla_alerta: str = "P7D"
) -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register(
        "lgpd_dsr_routing",
        [
            {
                "fluxo": fluxo,
                "grupo_revisor": grupo_revisor,
                "sla_resposta": sla_resposta,
                "sla_alerta": sla_alerta,
            }
        ],
    )
    return fake


# ---------------------------------------------------------------------------
# validate_identity
# ---------------------------------------------------------------------------


def test_validate_identity_topic() -> None:
    """validate_identity worker must have topic 'operadora.lgpd.verify_identity'."""
    worker = ValidateIdentityWorker()
    assert worker.topic == "operadora.lgpd.verify_identity"


def test_validate_identity_fail_closed_requires_explicit_verified_signal() -> None:
    """FAIL-CLOSED (T2.8): identity is confirmed ONLY on an explicit `identidade_verificada is
    True`. Pseudo_id present but no verified signal -> NOT confirmed (routes to the challenge)."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "pseudo-abc123",
            "canal": "portal",
            "identidade_verificada": True,
        }
    )

    assert result["identidade_confirmada"] is True
    assert result["status"] == "verified"


@pytest.mark.parametrize(
    "vars_extra",
    [
        {},  # identidade_verificada absent
        {"identidade_verificada": False},  # explicit False
        {"identidade_verificada": "true"},  # garbage (truthy string, not the bool True)
        {"identidade_verificada": 1},  # garbage (truthy int, not the bool True)
    ],
)
def test_validate_identity_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    """The mere presence of the (obligatory) titular_pseudo_id NEVER confirms identity, and only
    the bool `True` confirms — absent/False/garbage -> NOT confirmed (fail-closed, anti eng.
    social). This is the fail-OPEN defect this change closes."""
    worker = ValidateIdentityWorker()

    result = worker.run(
        {"tenant_id": "amh", "titular_pseudo_id": "pseudo-abc123", "canal": "portal", **vars_extra}
    )

    assert result["identidade_confirmada"] is False
    assert result["status"] == "pending_proof"


def test_validate_identity_rejects_empty_pseudo_id() -> None:
    """validate_identity RAISES a modeled BPMN error when pseudo_id is empty/missing.

    Per GAP-LGPD-6: absent/empty titular_pseudo_id RAISES WorkerBpmnError(ERR_DSR_IDENTITY_
    UNVERIFIED) so the BE_IdentidadeInverificavel boundary can fire -> End_IdentidadeInverificavel.
    This is a TECHNICAL guard (impossibilidade mecanica), NEVER an accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": "",
                "canal": "whatsapp",
            }
        )

    assert exc_info.value.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"


def test_validate_identity_never_accuses_fraud() -> None:
    """validate_identity must NEVER automatically accuse fraud.

    L0 hard: fraud_accusation is intocavel. Even with missing data, the worker only reports
    inability to verify MECHANICALLY (a technical BPMN error), never a fraud accusation.
    """
    worker = ValidateIdentityWorker()

    with pytest.raises(WorkerBpmnError) as exc_info:
        worker.run(
            {
                "tenant_id": "amh",
                "titular_pseudo_id": None,  # missing
            }
        )

    exc = exc_info.value
    # Must be a technical, fail-safe outcome — never fraud-accusation language/code.
    assert exc.error_code == "ERR_DSR_IDENTITY_UNVERIFIED"
    assert exc.error_code != ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "fraud" not in str(exc).lower()
    assert "acusacao" not in str(exc).lower()


# ---------------------------------------------------------------------------
# assess_request
# ---------------------------------------------------------------------------


def test_assess_request_topic() -> None:
    """assess_request worker must have the correct topic."""
    worker = AssessRequestWorker()
    assert worker.topic == "operadora.lgpd.assess_request"


def test_assess_request_routes_by_type() -> None:
    """assess_request determines the fluxo based on tipo_requisicao, via the DMN seam
    (ADR-0028/T1.5)."""
    fake = _lgpd_dsr_routing_fake(fluxo="EXPORTACAO", grupo_revisor="dpo")
    worker = AssessRequestWorker(dmn=fake)

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "confirmacao_acesso",
            "envolve_dados_saude": False,
        }
    )

    assert result["status"] == "assessed"
    assert result["fluxo"] == "EXPORTACAO"


def test_assess_request_data_saude_routes_to_juridico() -> None:
    """Requests involving health data go to juridico-privacidade."""
    fake = _lgpd_dsr_routing_fake(fluxo="ELIMINACAO_AVALIACAO", grupo_revisor="juridico-privacidade")
    worker = AssessRequestWorker(dmn=fake)

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
    fake = _lgpd_dsr_routing_fake(fluxo="EXPORTACAO", grupo_revisor="dpo")
    worker = AssessRequestWorker(dmn=fake)

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "confirmacao_acesso",
            "envolve_dados_saude": False,
        }
    )

    assert result["grupo_revisor"] == "dpo"


def test_assess_request_eliminacao_always_juridico_regardless_of_saude() -> None:
    """golden-parity finding (T1.5, live-verified): the deployed lgpd_dsr_routing table's
    `eliminacao` rule (r5) ALWAYS routes to juridico-privacidade, REGARDLESS of
    envolve_dados_saude (conflict with legal retention — prontuario/ANS — is always a juridico
    matter). The old Python's grupo_revisor logic would have routed this exact combination
    (eliminacao + envolve_dados_saude=False) to 'dpo' instead. DMN wins (documented, not
    patched) — PHI/LGPD-adjacent, policy-guardian review recommended (ADR-0028 §7)."""
    fake = _lgpd_dsr_routing_fake(fluxo="ELIMINACAO_AVALIACAO", grupo_revisor="juridico-privacidade")
    worker = AssessRequestWorker(dmn=fake)

    result = worker.run(
        {
            "tenant_id": "amh",
            "tipo_requisicao": "eliminacao",
            "envolve_dados_saude": False,
        }
    )

    assert result["grupo_revisor"] == "juridico-privacidade"


def test_assess_request_dmn_unwired_raises_dmn_evaluation_error() -> None:
    """`require_dmn` fail-closed guard. Calls `execute()` directly (not `.run()`) to avoid
    exercising `WorkerBase.run()`'s in-process retry/backoff sleep for this deterministic,
    always-fails case — still ends up DmnEvaluationError, never a silent/degraded result."""
    worker = AssessRequestWorker(dmn=None)
    with pytest.raises(DmnEvaluationError):
        worker.execute({"tenant_id": "amh", "tipo_requisicao": "confirmacao_acesso"})


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
