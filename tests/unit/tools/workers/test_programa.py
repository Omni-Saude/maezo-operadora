"""Unit tests for maezo.tools.workers.programa (SP-OP-PROGRAMA-001).

TDD London School: tests verify the consent chokepoint and clinical discharge guard.
"""

import pytest

from maezo.tools.workers.programa import (
    ERR_PROGRAM_DISCHARGE_NOT_HUMAN,
    ERR_PROGRAMA_NO_CONSENT,
    ProgramaError,
    check_consent,
    enroll_beneficiario,
    monitor_programa,
    register_discharge,
    register_program_discharge,
    stop_processing,
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
