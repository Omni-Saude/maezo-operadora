"""Worker: programa (SP-OP-PROGRAMA-001).

Programas de Cuidado — Consent-Gated.
Chokepoint: check_consent (ERR_PROGRAMA_NO_CONSENT).
Guard: ERR_PROGRAM_DISCHARGE_NOT_HUMAN (L0-hard clinical decision).
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_PROGRAMA_NO_CONSENT = "ERR_PROGRAMA_NO_CONSENT"
ERR_PROGRAM_DISCHARGE_NOT_HUMAN = "ERR_PROGRAM_DISCHARGE_NOT_HUMAN"
ERR_PROGRAMA_INSTANCIA_INVALIDA = "ERR_PROGRAMA_INSTANCIA_INVALIDA"

# Decision values
DECISAO_DESLIGAR_CLINICO = "DESLIGAR_CLINICO"
DECISAO_ENROLL = "ENROLL"


# ---------------------------------------------------------------
# check_consent — CHOKEPOINT: gates ALL PHI processing
# ---------------------------------------------------------------


def check_consent(variables: dict[str, Any]) -> dict[str, Any]:
    """Verify active consent for programa_cuidado scope.

    CHOKEPOINT: if no active consent, raises ERR_PROGRAMA_NO_CONSENT.
    NO PHI processing happens before this gate passes (fail-closed).
    """
    consentimento_ativo = variables.get("consentimento_ativo", False)
    consent_checked = variables.get("consent_checked", False)
    consent_scope = variables.get("consent_scope", "programa_cuidado")

    if not consentimento_ativo or not consent_checked:
        logger.warning(
            "programa_no_consent",
            beneficiario=variables.get("beneficiario_pseudo_id"),
            consent_scope=consent_scope,
        )
        raise ProgramaError(
            ERR_PROGRAMA_NO_CONSENT,
            f"Consentimento ausente/revogado para escopo '{consent_scope}'",
        )

    logger.info(
        "programa_consent_verified",
        beneficiario=variables.get("beneficiario_pseudo_id"),
        consent_scope=consent_scope,
    )

    return {
        "consentimento_ativo": True,
        "consent_verified_at": "now",
    }


# ---------------------------------------------------------------
# enroll_beneficiario — L3: enroll after consent gate
# ---------------------------------------------------------------


def enroll_beneficiario(variables: dict[str, Any]) -> dict[str, Any]:
    """Enroll beneficiary in the care program (L3, consent-gated).

    This is neutral — enrollment is not an adverse effect.
    The clinical discharge is separate (human-gated).
    """
    programa_id = variables.get("programa_id", "")
    beneficiario = variables.get("beneficiario_pseudo_id", "")

    logger.info(
        "programa_enroll_beneficiario",
        programa_id=programa_id,
        beneficiario=beneficiario,
    )

    return {
        "enrollment_realizado": True,
        "data_enrollment": "now",
    }


# ---------------------------------------------------------------
# monitor_programa — L3 monitoring
# ---------------------------------------------------------------


def monitor_programa(variables: dict[str, Any]) -> dict[str, Any]:
    """Monitor care program progress (L3, no decision)."""
    programa_id = variables.get("programa_id", "")
    risco = variables.get("risco_estratificado", "")

    logger.info(
        "programa_monitor",
        programa_id=programa_id,
        risco=risco,
    )

    return {
        "monitoramento_atualizado": True,
    }


# ---------------------------------------------------------------
# register_discharge — GATED L0-hard clinical discharge
# ---------------------------------------------------------------


def register_discharge(variables: dict[str, Any]) -> dict[str, Any]:
    """Register clinical discharge from program.

    GUARDED: ERR_PROGRAM_DISCHARGE_NOT_HUMAN (L0-hard).
    Clinical decision MUST come from a human (coordenacao-clinica / equipe-cuidado).
    """
    return _register_program_discharge(variables)


def _register_program_discharge(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate clinical discharge guard."""
    decisao = variables.get("decisao_programa", "")
    motivo = variables.get("motivo_desligamento_clinico", "")
    referencia = variables.get("referencia_clinica", "")
    responsavel_id = variables.get("responsavel_clinico_id", "")

    errors: list[str] = []

    if decisao != DECISAO_DESLIGAR_CLINICO:
        errors.append(f"decisao_programa != {DECISAO_DESLIGAR_CLINICO} (got: {decisao!r})")
    if not motivo:
        errors.append("motivo_desligamento_clinico ausente")
    if not referencia:
        errors.append("referencia_clinica ausente")
    if not responsavel_id:
        errors.append("responsavel_clinico_id ausente (ADR-0007)")

    if errors:
        logger.error(
            "programa_discharge_guard_rejected",
            errors=errors,
            beneficiario=variables.get("beneficiario_pseudo_id"),
        )
        raise ProgramaError(ERR_PROGRAM_DISCHARGE_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "programa_clinical_discharge_registered",
        beneficiario=variables.get("beneficiario_pseudo_id"),
        responsavel_clinico_id=responsavel_id,
    )

    return {
        "desligamento_clinico_registrado": True,
    }


register_program_discharge = _register_program_discharge


# ---------------------------------------------------------------
# stop_processing — interruptive: stop PHI processing on consent revocation
# ---------------------------------------------------------------


def stop_processing(variables: dict[str, Any]) -> dict[str, Any]:
    """Stop PHI processing after consent revocation (LGPD fail-safe).

    This is NOT adverse — stopping PHI processing upon revocation is
    the safe/legal behavior. Terminal: End_ProcessamentoInterrompidoRevogacao.
    """
    logger.warning(
        "programa_stop_processing_revogacao",
        beneficiario=variables.get("beneficiario_pseudo_id"),
    )

    return {
        "processamento_parado": True,
        "motivo": "revogacao_consentimento",
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class ProgramaError(Exception):
    """Worker guard error for programa effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
