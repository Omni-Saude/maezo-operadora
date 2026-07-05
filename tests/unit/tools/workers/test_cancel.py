"""Unit tests for maezo.tools.workers.cancel — SP-OP-CANCEL-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.cancel import (
    CancelContratoInvalidoError,
    CancelDecisionInput,
    CancelInput,
    CancellationNotHumanError,
    CancelManterNotHumanError,
    assess_admissibility,
    notify_beneficiario,
    process_cancel,
    publish_completed,
    register_contract_termination,
    validate_cancel,
)

# ---------------------------------------------------------------------------
# validate_cancel
# ---------------------------------------------------------------------------


def test_validate_cancel_valid() -> None:
    """validate_cancel accepts a valid cancel input."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="CONT-001",
        matricula_beneficiario="BEN-001",
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = validate_cancel(inp)
    assert result.valid is True
    assert result.errors == []


def test_validate_cancel_invalid_tipo_solicitacao() -> None:
    """validate_cancel rejects unknown tipo_solicitacao."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="CONT-001",
        tipo_solicitacao="tipo_invalido",
    )
    result = validate_cancel(inp)
    assert result.valid is False
    assert any("tipo_solicitacao" in e for e in result.errors)


def test_validate_cancel_missing_contract_key() -> None:
    """validate_cancel flags missing contract and beneficiario keys."""
    inp = CancelInput(
        tenant_id="amh",
        numero_contrato="",
        matricula_beneficiario="",
        tipo_solicitacao="pedido_beneficiario",
    )
    result = validate_cancel(inp)
    assert any("contrato" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# assess_admissibility
# ---------------------------------------------------------------------------


def test_assess_admissibility_pedido_individual() -> None:
    """Pedido beneficiario individual -> EFETIVAR_PEDIDO (L2 clerical)."""
    inp = CancelInput(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "EFETIVAR_PEDIDO"
    assert result.grupo_sugerido == "gestao-contratos"


def test_assess_admissibility_pedido_coletivo() -> None:
    """Pedido beneficiario coletivo -> ANALISE_HUMANA (cabe ao estipulante)."""
    inp = CancelInput(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="coletivo_empresarial",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        dentro_prazo=True,
        titularidade_confirmada=True,
        vinculo_ativo=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "coletivo" in result.motivo.lower() or "estipulante" in result.motivo.lower()


def test_assess_admissibility_fraude_referida() -> None:
    """fraude_referida -> ANALISE_HUMANA always (L0 hard)."""
    inp = CancelInput(
        tipo_solicitacao="fraude_referida",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(valid=True)
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "fraude" in result.motivo.lower()


def test_assess_admissibility_pendente_notificacao() -> None:
    """inadimplencia without notificacao -> PENDENTE_NOTIFICACAO."""
    inp = CancelInput(
        tipo_solicitacao="inadimplencia",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        notificacao_previa_feita=False,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "PENDENTE_NOTIFICACAO"


def test_assess_admissibility_segue_analise() -> None:
    """for_cause_operadora with notificacao -> SEGUE_ANALISE."""
    inp = CancelInput(
        tipo_solicitacao="for_cause_operadora",
        tipo_plano="individual",
    )
    from maezo.tools.workers.cancel import CancelValidationResult

    validation = CancelValidationResult(
        valid=True,
        notificacao_previa_feita=True,
    )
    result = assess_admissibility(inp, validation)
    assert result.roteamento == "SEGUE_ANALISE"


# ---------------------------------------------------------------------------
# notify_beneficiario
# ---------------------------------------------------------------------------


def test_notify_beneficiario_cancel() -> None:
    """notify_beneficiario sends status notification."""
    result = notify_beneficiario("BEN-001", "CONT-001", "rescindido")
    assert result["notified"] is True


# ---------------------------------------------------------------------------
# process_cancel
# ---------------------------------------------------------------------------


def test_process_cancel_rescindir() -> None:
    """process_cancel routes RESCINDIR to send_cancellation_notice."""
    inp = CancelInput(numero_contrato="CONT-001")
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="Clausula X",
        referencia_regulatoria="RN 593",
        comprovacao_notificacao_previa="NOTIF-001",
        responsavel_id="resp-001",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "send_cancellation_notice"


def test_process_cancel_suspender() -> None:
    """process_cancel routes SUSPENDER to send_cancellation_notice."""
    inp = CancelInput(numero_contrato="CONT-002")
    decision = CancelDecisionInput(
        decisao_cancelamento="SUSPENDER",
        fundamentacao_contratual="Clausula Y",
        referencia_regulatoria="RN 593",
        comprovacao_notificacao_previa="NOTIF-002",
        responsavel_id="resp-002",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "send_cancellation_notice"


def test_process_cancel_manter() -> None:
    """process_cancel routes MANTER to confirm_maintained_decision."""
    inp = CancelInput(numero_contrato="CONT-003")
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="Clausula Z",
        responsavel_id="resp-003",
    )
    result = process_cancel(inp, decision)
    assert result["action"] == "confirm_maintained_decision"


def test_process_cancel_efetivar_pedido() -> None:
    """process_cancel routes EFETIVAR_PEDIDO to effectuate_member_request."""
    inp = CancelInput(numero_contrato="CONT-004")
    decision = CancelDecisionInput(decisao_cancelamento="EFETIVAR_PEDIDO")
    result = process_cancel(inp, decision)
    assert result["action"] == "effectuate_member_request"


# ---------------------------------------------------------------------------
# register_contract_termination — GUARD tests
# ---------------------------------------------------------------------------


def test_register_termination_guard_not_adverse() -> None:
    """register_contract_termination raises if decisao not in {RESCINDIR, SUSPENDER}."""
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="justificativa",
        referencia_regulatoria="RN-593",
        comprovacao_notificacao_previa="NOTIF-1",
        responsavel_id="resp-1",
    )
    with pytest.raises(CancellationNotHumanError):
        register_contract_termination(decision)


def test_register_termination_guard_missing_fields() -> None:
    """register_contract_termination raises if required fields are missing."""
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="",
        referencia_regulatoria="",
        comprovacao_notificacao_previa="",
        responsavel_id="",
    )
    with pytest.raises(CancellationNotHumanError) as exc:
        register_contract_termination(decision)
    msg = str(exc.value)
    assert "fundamentacao_contratual" in msg
    assert "referencia_regulatoria" in msg


def test_register_termination_success() -> None:
    """register_contract_termination succeeds with all required fields."""
    decision = CancelDecisionInput(
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="Violacao contratual art 13",
        referencia_regulatoria="RN 593/2023",
        comprovacao_notificacao_previa="NOTIF-2026-001",
        responsavel_id="resp-999",
    )
    result = register_contract_termination(decision)
    assert result["registered"] is True
    assert result["decisao"] == "RESCINDIR"


# ---------------------------------------------------------------------------
# GUARD tests: process_cancel raises on invalid MANTER
# ---------------------------------------------------------------------------


def test_process_cancel_manter_guard_missing_fundamentacao() -> None:
    """MANTER without fundamentacao_contratual -> raises CancelManterNotHumanError."""
    inp = CancelInput(numero_contrato="CONT-005")
    decision = CancelDecisionInput(
        decisao_cancelamento="MANTER",
        fundamentacao_contratual="",
        responsavel_id="resp-005",
    )
    with pytest.raises(CancelManterNotHumanError):
        process_cancel(inp, decision)


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_cancel() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="cancelado_beneficiario")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "cancelado_beneficiario"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_cancellation_not_human_is_permission_error() -> None:
    """CancellationNotHumanError must be a subclass of PermissionError."""
    assert issubclass(CancellationNotHumanError, PermissionError)


def test_cancel_manter_not_human_is_permission_error() -> None:
    """CancelManterNotHumanError must be a subclass of PermissionError."""
    assert issubclass(CancelManterNotHumanError, PermissionError)


def test_cancel_contrato_invalido_is_value_error() -> None:
    """CancelContratoInvalidoError must be a subclass of ValueError."""
    assert issubclass(CancelContratoInvalidoError, ValueError)
