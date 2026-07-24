"""Worker: programa (SP-OP-PROGRAMA-001).

Programas de Cuidado — Consent-Gated.
Chokepoint: check_consent (ERR_PROGRAMA_NO_CONSENT).
Guard: ERR_PROGRAM_DISCHARGE_NOT_HUMAN (L0-hard clinical decision).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

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
# stratify_risk — in-zone risk-band delegation stub (care.stratify — Valentina A2A)
# ---------------------------------------------------------------

# Domain the `programa_routing` DMN (spec/processes/dmn/programa_routing.dmn) actually reads for
# `risco_estratificado` (typeRef string, exact FEEL literal match — "alto"/"moderado"/"baixo").
# Anything outside this set never matches a specific row and falls through to the DMN's own
# wildcard catch-all (-> ANALISE_HUMANA) anyway; validating here just makes that fact observable
# in worker logs/tests instead of silently relying on FEEL fallthrough.
_RISCO_BANDAS_VALIDAS = frozenset({"baixo", "moderado", "alto"})

# FAIL-CLOSED default (T2.5): no deterministic clinical stratification algorithm is prescribed —
# neither the contract (docs/processes/contracts/SP-OP-PROGRAMA-001.md §DMN referenciadas:
# "criterios clinicos DRAFT/verify medico") nor the DMN itself (programa_routing.dmn description:
# "criterios de estratificacao/elegibilidade requerem SME medico-auditor") define one. POLICY GAP
# (flagged for medico-auditor/SME — not resolved here, and out of scope for this worker to invent):
# until Valentina's real `care.stratify` A2A delegation lands, this worker cannot compute an actual
# risk band, so it defaults to the DMN's OWN lowest-autonomy path. "alto" is not a guess at the
# beneficiary's real risk — it is the row the DMN itself documents as the conservative, always-
# human destination (`r_risco_alto`: "Risco alto -> sempre analise clinica humana (conservador;
# nunca auto-elegivel/auto-alta)"), regardless of `elegibilidade_criterios_atendidos`. This mirrors
# the DMN's wildcard catch-all row destination (ANALISE_HUMANA) but as an explicit, auditable value
# rather than an implicit FEEL fallthrough.
RISCO_FAIL_CLOSED_DEFAULT = "alto"


def stratify_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Stratify beneficiary risk band (in-zone, care.stratify — Valentina A2A delegation stub).

    CONTRACT (SP-OP-PROGRAMA-001.md, Invariante A): estratificacao de risco is explicitly named as
    PHI processing gated by consent ("Antes de qualquer tratamento de dados de saude do
    beneficiario no ambito do programa (estratificacao de risco, ...) o consentimento DEVE estar
    ativo e verificado"). This is the FIRST task after `Start_Cuidado` (BPMN:147-157) — it runs
    in-zone, immediately after the consent gate. Defense-in-depth guard mirrors check_consent's
    chokepoint exactly (same ERR_PROGRAMA_NO_CONSENT code — this is the SAME invariant, checked a
    second time) in case this task is ever reached without the gate having passed.

    Delegation stub (mirrors `enroll_beneficiario`/`fraude.gather_evidence`): does NOT invent
    clinical risk logic. INSTRUI, NAO DECIDE (BPMN:154). If a pre-resolved risk band is already on
    the process variables (Valentina/agent output, or test-seeded — normalized case/whitespace
    only, never re-derived), it is echoed through UNCHANGED. Otherwise fails closed to
    `RISCO_FAIL_CLOSED_DEFAULT` ("alto"), which `programa_routing` ALWAYS routes to
    ANALISE_HUMANA (clinico humano) — NEVER to auto-elegivel/auto-nao-elegivel. NEVER sets
    `decisao_programa` (BPMN:154 documentation) — no clinical decision is made here.
    """
    consentimento_ativo = variables.get("consentimento_ativo", False)
    consent_checked = variables.get("consent_checked", False)
    consent_scope = variables.get("consent_scope", "programa_cuidado")
    beneficiario = variables.get("beneficiario_pseudo_id")
    programa_id = variables.get("programa_id")

    if not consentimento_ativo or not consent_checked:
        logger.warning(
            "programa_stratify_risk_no_consent",
            beneficiario=beneficiario,
            consent_scope=consent_scope,
        )
        raise ProgramaError(
            ERR_PROGRAMA_NO_CONSENT,
            f"stratify_risk recusado: consentimento ausente/revogado para escopo '{consent_scope}'",
        )

    raw = variables.get("risco_estratificado")
    normalized = raw.strip().lower() if isinstance(raw, str) else None

    if normalized in _RISCO_BANDAS_VALIDAS:
        risco = normalized
        origem = "pre_resolvido"
    else:
        risco = RISCO_FAIL_CLOSED_DEFAULT
        origem = "fail_closed_default"
        logger.warning(
            "programa_stratify_risk_fail_closed_default",
            beneficiario=beneficiario,
            programa_id=programa_id,
            risco_recebido=raw,
        )

    logger.info(
        "programa_stratify_risk",
        beneficiario=beneficiario,
        programa_id=programa_id,
        risco_estratificado=risco,
        origem=origem,
    )

    return {
        "risco_estratificado": risco,
        "risco_estratificado_origem": origem,
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


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   check_consent    -> operadora.programa.check_consent (exact spec match, CHOKEPOINT guard)
#   stratify_risk    -> operadora.programa.stratify_risk (exact spec match; T2.5 — the FIRST
#     external task after Start_Cuidado, immediately after the consent gate; was a registry gap,
#     now implemented as a fail-closed delegation stub — see stratify_risk's own docstring)
#   enroll_beneficiario -> operadora.programa.build_care_plan
#     (spec match: task name says "care.enroll")
#   register_discharge (alias register_program_discharge)
#     -> operadora.programa.register_program_discharge (exact spec match, GUARDED)
#   stop_processing  -> operadora.programa.stop_processing (exact spec match)
# monitor_programa has no distinct spec topic — registered under a
# function-derived topic for registry completeness.
# Spec topics with NO implementing function today (gap, not fabricated here; T2.5 scope is
# stratify_risk ONLY — proactive_contact/notify_sla_risk are tracked separately):
# proactive_contact, notify_sla_risk.
# ---------------------------------------------------------------


def register_programa_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-PROGRAMA-001 function workers on `harness`."""
    del kafka, seams  # unused — no programa.py worker declares a Kafka/other seam dependency
    harness.register_worker(FunctionWorker("operadora.programa.check_consent", check_consent))
    harness.register_worker(FunctionWorker("operadora.programa.stratify_risk", stratify_risk))
    harness.register_worker(FunctionWorker("operadora.programa.build_care_plan", enroll_beneficiario))
    harness.register_worker(FunctionWorker("operadora.programa.monitor_programa", monitor_programa))
    harness.register_worker(
        FunctionWorker("operadora.programa.register_program_discharge", register_program_discharge)
    )
    harness.register_worker(FunctionWorker("operadora.programa.stop_processing", stop_processing))
