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

# T3.1 (mirrors lgpd.py's ADR-0031 `identidade_verificada` fail-closed matrix): the consent
# chokepoint (`consentimento_ativo`/`consent_checked`) is pinned to the explicit boolean `True` —
# NOT bare truthiness. Absent / False / None / any truthy junk (string incl. whitespace-only, int,
# list, dict) must NEVER be read as active/verified consent — this chokepoint "gates ALL PHI
# processing" (module docstring; contract SP-OP-PROGRAMA-001.md:15). Shared vectors reused across
# check_consent's and stratify_risk's own parametrized fail-closed tests below.
_CONSENT_NON_TRUE_VECTORS: list[dict[str, object]] = [
    {"consentimento_ativo": False},  # explicit False (consent_checked left True)
    {"consentimento_ativo": None},  # explicit None
    {"consentimento_ativo": "true"},  # garbage: truthy string, not the bool True
    {"consentimento_ativo": " "},  # garbage: whitespace-only truthy string
    {"consentimento_ativo": 1},  # garbage: truthy int, not the bool True
    {"consentimento_ativo": [1]},  # garbage: truthy list, not the bool True
    {"consentimento_ativo": {"ok": True}},  # garbage: truthy dict, not the bool True
    {"consent_checked": False},  # explicit False (consentimento_ativo left True)
    {"consent_checked": None},  # explicit None
    {"consent_checked": "true"},  # garbage: truthy string, not the bool True
    {"consent_checked": " "},  # garbage: whitespace-only truthy string
    {"consent_checked": 1},  # garbage: truthy int, not the bool True
    {"consent_checked": [1]},  # garbage: truthy list, not the bool True
    {"consent_checked": {"ok": True}},  # garbage: truthy dict, not the bool True
]

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


@pytest.mark.parametrize("vars_extra", _CONSENT_NON_TRUE_VECTORS)
def test_programa_consent_gate_fail_closed_rejects_non_true_signal(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): the chokepoint only accepts the literal `is True` for each consent flag.

    False/None/truthy-junk (string incl. whitespace-only, int, list, dict) on EITHER flag must
    NEVER be read as active/verified consent — closes the fail-OPEN class this change fixes.
    """
    variables: dict[str, object] = {
        "consentimento_ativo": True,
        "consent_checked": True,
        "consent_scope": "programa_cuidado",
        **vars_extra,
    }

    with pytest.raises(ProgramaError) as excinfo:
        check_consent(variables)
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


@pytest.mark.parametrize("vars_extra", _CONSENT_NON_TRUE_VECTORS)
def test_stratify_risk_fail_closed_rejects_non_true_signal(vars_extra: dict[str, object]) -> None:
    """FAIL-CLOSED (T3.1): this defense-in-depth guard only accepts the literal `is True` for each
    consent flag — same invariant/vectors as check_consent's own parametrized fail-closed test."""
    with pytest.raises(ProgramaError) as excinfo:
        stratify_risk(_consented_variables(**vars_extra))
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
