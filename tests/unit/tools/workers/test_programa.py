"""Unit tests for maezo.tools.workers.programa (SP-OP-PROGRAMA-001).

TDD London School: tests verify the consent chokepoint and clinical discharge guard.
"""

import pytest

from maezo.tools.workers.programa import (
    ERR_PROGRAM_DISCHARGE_NOT_HUMAN,
    ERR_PROGRAMA_NO_CONSENT,
    RISCO_FAIL_CLOSED_DEFAULT,
    ProgramaError,
    check_consent,
    enroll_beneficiario,
    monitor_programa,
    register_discharge,
    register_program_discharge,
    stop_processing,
    stratify_risk,
)

# ---------------------------------------------------------------
# check_consent — CHOKEPOINT
# ---------------------------------------------------------------


def test_programa_consent_gate_happy_path() -> None:
    result = check_consent(
        {
            "consentimento_ativo": True,
            "consent_checked": True,
            "consent_scope": "programa_cuidado",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["consentimento_ativo"] is True


def test_programa_consent_gate_blocks_no_consent() -> None:
    """Without consent, the chokepoint blocks ALL PHI processing."""
    with pytest.raises(ProgramaError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": False,
                "consent_checked": False,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_programa_consent_gate_blocks_revoked() -> None:
    """Revoked consent (ativo=False) blocks processing."""
    with pytest.raises(ProgramaError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": False,
                "consent_checked": True,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_programa_consent_gate_blocks_not_checked() -> None:
    """consent_checked=False means consent was never verified."""
    with pytest.raises(ProgramaError) as excinfo:
        check_consent(
            {
                "consentimento_ativo": True,
                "consent_checked": False,
                "consent_scope": "programa_cuidado",
            }
        )
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


# ---------------------------------------------------------------
# stratify_risk — in-zone risk-band delegation stub (care.stratify — Valentina A2A)
# ---------------------------------------------------------------


def _consented_variables(**overrides: object) -> dict[str, object]:
    variables: dict[str, object] = {
        "consentimento_ativo": True,
        "consent_checked": True,
        "consent_scope": "programa_cuidado",
        "beneficiario_pseudo_id": "b-001",
        "programa_id": "cronicos",
    }
    variables.update(overrides)
    return variables


def test_stratify_risk_happy_path_echoes_pre_resolved_band() -> None:
    """A pre-resolved, valid risk band is echoed through unchanged (instrui, nao decide)."""
    result = stratify_risk(_consented_variables(risco_estratificado="moderado"))
    assert result["risco_estratificado"] == "moderado"
    assert result["risco_estratificado_origem"] == "pre_resolvido"


def test_stratify_risk_normalizes_case_and_whitespace() -> None:
    """Only mechanical coercion (case/whitespace) — never business derivation."""
    result = stratify_risk(_consented_variables(risco_estratificado="  ALTO  "))
    assert result["risco_estratificado"] == "alto"
    assert result["risco_estratificado_origem"] == "pre_resolvido"


def test_stratify_risk_refuses_without_active_consent() -> None:
    """Defense-in-depth: estratificacao de risco is PHI processing gated by consent (Invariante A).

    Same ERR_PROGRAMA_NO_CONSENT code as check_consent's chokepoint — this is the SAME invariant,
    checked a second time in case this task is ever reached without the gate having passed.
    """
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(consentimento_ativo=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_stratify_risk_refuses_without_consent_checked() -> None:
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(consent_checked=False))
    assert excinfo.value.code == ERR_PROGRAMA_NO_CONSENT


def test_stratify_risk_fail_closed_default_when_missing() -> None:
    """No pre-resolved band available => fail-closed to the DMN's lowest-autonomy path ("alto"),
    which programa_routing ALWAYS routes to ANALISE_HUMANA (never auto-elegivel/auto-alta)."""
    result = stratify_risk(_consented_variables())
    assert result["risco_estratificado"] == RISCO_FAIL_CLOSED_DEFAULT
    assert result["risco_estratificado_origem"] == "fail_closed_default"


def test_stratify_risk_fail_closed_default_when_invalid_value() -> None:
    """A band outside the DMN's known vocabulary is treated as unresolved, not guessed at."""
    result = stratify_risk(_consented_variables(risco_estratificado="urgentissimo"))
    assert result["risco_estratificado"] == RISCO_FAIL_CLOSED_DEFAULT
    assert result["risco_estratificado_origem"] == "fail_closed_default"


def test_stratify_risk_never_sets_decisao_programa() -> None:
    """Invariant C: no worker in this module (esp. stratify_risk) may set decisao_programa."""
    result = stratify_risk(_consented_variables(risco_estratificado="alto"))
    assert "decisao_programa" not in result


# ---------------------------------------------------------------
# enroll_beneficiario
# ---------------------------------------------------------------


def test_enroll_beneficiario() -> None:
    result = enroll_beneficiario(
        {
            "programa_id": "cronicos",
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["enrollment_realizado"] is True


# ---------------------------------------------------------------
# monitor_programa
# ---------------------------------------------------------------


def test_monitor_programa() -> None:
    result = monitor_programa(
        {
            "programa_id": "cronicos",
            "risco_estratificado": "alto",
        }
    )
    assert result["monitoramento_atualizado"] is True


# ---------------------------------------------------------------
# register_discharge / register_program_discharge — L0-hard GUARD
# ---------------------------------------------------------------


def test_programa_discharge_happy_path() -> None:
    result = register_program_discharge(
        {
            "decisao_programa": "DESLIGAR_CLINICO",
            "motivo_desligamento_clinico": "Alta apos conclusao do ciclo terapeutico",
            "referencia_clinica": "Protocolo HCPA 2023",
            "responsavel_clinico_id": "med-001",
        }
    )
    assert result["desligamento_clinico_registrado"] is True


def test_programa_discharge_rejects_enroll() -> None:
    """ENROLL is not DESLIGAR — guard must reject."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(
            {
                "decisao_programa": "ENROLL",
                "motivo_desligamento_clinico": "x",
                "referencia_clinica": "x",
                "responsavel_clinico_id": "med-001",
            }
        )
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN


def test_programa_discharge_rejects_missing_clinico() -> None:
    """Clinical decision without responsible clinician."""
    with pytest.raises(ProgramaError) as excinfo:
        register_program_discharge(
            {
                "decisao_programa": "DESLIGAR_CLINICO",
                "motivo_desligamento_clinico": "x",
                "referencia_clinica": "x",
                "responsavel_clinico_id": "",
            }
        )
    assert excinfo.value.code == ERR_PROGRAM_DISCHARGE_NOT_HUMAN


def test_programa_register_discharge_alias() -> None:
    result = register_discharge(
        {
            "decisao_programa": "DESLIGAR_CLINICO",
            "motivo_desligamento_clinico": "Alta clinica",
            "referencia_clinica": "Protocolo X",
            "responsavel_clinico_id": "med-001",
        }
    )
    assert result["desligamento_clinico_registrado"] is True


# ---------------------------------------------------------------
# stop_processing
# ---------------------------------------------------------------


def test_stop_processing() -> None:
    result = stop_processing(
        {
            "beneficiario_pseudo_id": "b-001",
        }
    )
    assert result["processamento_parado"] is True
    assert result["motivo"] == "revogacao_consentimento"
