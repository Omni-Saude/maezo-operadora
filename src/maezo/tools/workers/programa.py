"""Worker: programa (SP-OP-PROGRAMA-001).

Programas de Cuidado — Consent-Gated.
Chokepoint: check_consent (ERR_PROGRAMA_NO_CONSENT).
Guard: ERR_PROGRAM_DISCHARGE_NOT_HUMAN (L0-hard clinical decision).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)

# Internal-notification channel (mirrors recurso.py's/lgpd.py's own `_NOTIFICATIONS_TOPIC` — a
# `type`-discriminated envelope on `operadora.notifications.internal`, NOT a BPMN-declared
# domain-event topic). Used by the 4 raw-handler workers below (item A/B/C, event-wiring wave)
# whose Kafka publish needs the async seam a `FunctionWorker`'s sync boundary cannot reach
# (`WorkerBase`'s own docstring: "Async I/O is handled by the engine/message layer, not by the
# worker logic").
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_PROGRAMA_NO_CONSENT = "ERR_PROGRAMA_NO_CONSENT"
ERR_PROGRAM_DISCHARGE_NOT_HUMAN = "ERR_PROGRAM_DISCHARGE_NOT_HUMAN"
ERR_PROGRAMA_INSTANCIA_INVALIDA = "ERR_PROGRAMA_INSTANCIA_INVALIDA"

# ADR-0030 §2 modeled BPMN error. `check_consent` (the CHOKEPOINT worker on
# `operadora.programa.check_consent`) raises `WorkerBpmnError(ERR_PROGRAMA_NO_CONSENT)` so its
# modeled boundary `BE_SemConsentimento` -> `End_SemConsentimento` (the documented LGPD fail-safe
# terminal, Invariante A) can fire. Consumption-covered ("simple rule"): the topic is consumed
# ONLY by SP-OP-PROGRAMA-001, which declares this errorCode on that task's boundary. It is a
# consent origin/consistency guard (G2-val) — never an adverse action — so NOT a `*_NOT_HUMAN`
# guard and NOT T-E-gated. Unioned into `worker_runtime/service.py`'s
# `_GATE_PROVEN_BPMN_ERROR_CODES`. NOTE: `stratify_risk`'s defense-in-depth guard raises the SAME
# code as a `ProgramaError` (-> ValueError -> incident) DELIBERATELY — `ST_StratifyRisk` carries NO
# error boundary, so a `WorkerBpmnError` there would silently end the process scope on CIB Seven
# 2.1.0 (the live-verified hazard); it MUST stay a human-visible incident.
PROGRAMA_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_PROGRAMA_NO_CONSENT})

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
    # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): consentimento confirmado
    # SO com sinal explicito `is True`. Ausente/False/lixo (string truthy como "true"/" ", int 1,
    # list/dict) -> False -> o chokepoint bloqueia TODO processamento de PHI do programa.
    consentimento_ativo = variables.get("consentimento_ativo") is True
    consent_checked = variables.get("consent_checked") is True
    consent_scope = variables.get("consent_scope", "programa_cuidado")

    if not consentimento_ativo or not consent_checked:
        logger.warning(
            "programa_no_consent",
            beneficiario=variables.get("beneficiario_pseudo_id"),
            consent_scope=consent_scope,
        )
        # MODELED boundary error (BE_SemConsentimento -> End_SemConsentimento) — WorkerBpmnError,
        # NOT a ProgramaError (which FunctionWorker.execute reclassifies to a bare ValueError ->
        # incident, so the boundary could never fire). ADR-0030 §2; see PROGRAMA_BPMN_ERROR_ALLOWLIST.
        raise WorkerBpmnError(
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
    # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): consentimento confirmado
    # SO com sinal explicito `is True`. Ausente/False/lixo (string truthy como "true"/" ", int 1,
    # list/dict) -> False -> este guard (mesma invariante de check_consent) bloqueia o PHI.
    consentimento_ativo = variables.get("consentimento_ativo") is True
    consent_checked = variables.get("consent_checked") is True
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


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str` (t2.5-p2b-round2 / t3.1-guard-input-hardening): the pre-fix bare
    `if not motivo` / `if not referencia` / `if not responsavel_id` checks in
    `_register_program_discharge` let WHITESPACE-ONLY decision + accountability fields pass —
    on an L0-HARD CLINICAL decision (ADR-0005/0008: a clinical discharge NEVER without a
    genuine human clinician behind it), a whitespace-only `responsavel_clinico_id` would defeat
    ADR-0007's audit-chain identification and a whitespace-only `motivo_desligamento_clinico`/
    `referencia_clinica` would record a discharge with no real clinical justification/protocol.
    Closes that class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through and never an AttributeError
      incident from calling `.strip()` on a non-string.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def register_discharge(variables: dict[str, Any]) -> dict[str, Any]:
    """Register clinical discharge from program.

    GUARDED: ERR_PROGRAM_DISCHARGE_NOT_HUMAN (L0-hard).
    Clinical decision MUST come from a human (coordenacao-clinica / equipe-cuidado).
    """
    return _register_program_discharge(variables)


def _register_program_discharge(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate clinical discharge guard.

    NORMALIZATION (t3.1-guard-input-hardening, L0-hard clinical decision): ALL decision +
    human-accountability fields (`decisao_programa`, `motivo_desligamento_clinico`,
    `referencia_clinica`, `responsavel_clinico_id` — BPMN ST_RegisterDischarge doc,
    SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn:317; contract SP-OP-PROGRAMA-001.md:102-105,181)
    are normalized via `_norm_str` (strip; non-string -> "") BEFORE any guard check, mirroring
    `pagto.register_payment_refusal`'s fix. Whitespace-only/non-string refuses exactly like
    absent; a whitespace-PADDED exact `decisao_programa` literal ("DESLIGAR_CLINICO ") still
    passes (case variants/substrings still refuse — exact `!=` match, no folding). Input
    normalization ONLY — the guard structure, error class and refusal semantics are unchanged.
    """
    decisao = _norm_str(variables.get("decisao_programa", ""))
    motivo = _norm_str(variables.get("motivo_desligamento_clinico", ""))
    referencia = _norm_str(variables.get("referencia_clinica", ""))
    responsavel_id = _norm_str(variables.get("responsavel_clinico_id", ""))

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
# proactive_contact — item-9 wave-5 item B: ST_ProactiveContact (ELEGIVEL branch, in-zone)
# ---------------------------------------------------------------


def proactive_contact(variables: dict[str, Any]) -> dict[str, Any]:
    """Contato proativo com o beneficiario (`ST_ProactiveContact`, BPMN:153-162, in-zone).

    Defense-in-depth guard mirrors `stratify_risk`'s EXACT pattern — same invariant (consent for
    `consent_scope`), same code (`ERR_PROGRAMA_NO_CONSENT`), same `ProgramaError`-not-
    `WorkerBpmnError` posture: `ST_ProactiveContact` carries no error boundary in the BPMN (re-
    verified: no `errorEventDefinition` attached to it), so a `WorkerBpmnError` here would
    silently end the process scope on CIB Seven 2.1.0 (the same live-verified hazard documented
    on `stratify_risk`/module docstring lines ~37-48) — it MUST stay a human-visible incident.
    `check_consent` (the CHOKEPOINT) already gates entry to `SUB_Cuidado`; this re-checks the SAME
    invariant in case the task is ever reached without the gate having passed (D9: "consent_checked
    exigido antes de qualquer contato").

    Does NOT set `desfecho` — BPMN's own `ST_ProactiveContact` `outputParameter` literal (~157)
    stamps `desfecho=enrollment_realizado`; this worker only performs the contact.
    """
    consentimento_ativo = variables.get("consentimento_ativo") is True
    consent_checked = variables.get("consent_checked") is True
    consent_scope = variables.get("consent_scope", "programa_cuidado")
    beneficiario = variables.get("beneficiario_pseudo_id")
    programa_id = variables.get("programa_id")

    if not consentimento_ativo or not consent_checked:
        logger.warning(
            "programa_proactive_contact_no_consent",
            beneficiario=beneficiario,
            consent_scope=consent_scope,
        )
        raise ProgramaError(
            ERR_PROGRAMA_NO_CONSENT,
            f"proactive_contact recusado: consentimento ausente/revogado para escopo '{consent_scope}'",
        )

    logger.info(
        "programa_proactive_contact",
        beneficiario=beneficiario,
        programa_id=programa_id,
    )

    return {
        "contato_realizado": True,
    }


# ---------------------------------------------------------------
# notify_sla_risk — item-9 wave-5 item C: ST_NotifySlaRisk (non-interruptive timer alert)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Notify coordenacao-clinica/equipe-cuidado of SLA risk (`ST_NotifySlaRisk`, BPMN:202-205).

    Fed ONLY by the NON-interruptive boundary timer `BT_AlertaSlaPrograma` (`cancelActivity=
    "false"` on the attached boundary — re-verified against the BPMN) attached to
    `UT_DecisaoClinica`: informational only. `UT_DecisaoClinica` stays open, no decision is made
    or altered here — mirrors `recurso.notify_sla_risk`'s (recurso.py:700-713) exact rationale.
    """
    logger.info(
        "programa_notify_sla_risk",
        beneficiario=variables.get("beneficiario_pseudo_id"),
        programa_id=variables.get("programa_id"),
    )

    return {
        "sla_risk_notified": True,
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
# item-9 wave-5 — raw-handler Kafka seam (items A/B/C).
#
# `stratify_risk`/`stop_processing` (item A, root fix) and the 2 NEW workers `proactive_contact`/
# `notify_sla_risk` (items B/C) are raw `harness.register()` handlers — mirrors
# `recurso.make_notify_sla_risk_handler` (recurso.py:716-752) / `lgpd.make_request_additional_
# proof_handler` (lgpd.py:449-527) — NOT `FunctionWorker`-wrapped: each needs the async Kafka seam
# a sync `FunctionWorker.execute`/`WorkerBase.execute` boundary cannot reach (`WorkerBase`'s own
# docstring: "Async I/O is handled by the engine/message layer, not by the worker logic") to
# publish a `type`-discriminated internal notification (`_NOTIFICATIONS_TOPIC`) observable by
# `notifications_of_type(...)` (the port's own `ProgramaEngineProbe`, T3.1 R2 finding 4).
# ---------------------------------------------------------------

_STRATIFY_RISK_TOPIC = "operadora.programa.stratify_risk"
_STOP_PROCESSING_TOPIC = "operadora.programa.stop_processing"
_PROACTIVE_CONTACT_TOPIC = "operadora.programa.proactive_contact"
_NOTIFY_SLA_RISK_TOPIC = "operadora.programa.notify_sla_risk"

_STRATIFY_RISK_NOTIFICATION_TYPE = "programa.stratify_risk"
_STOP_PROCESSING_NOTIFICATION_TYPE = "programa.stop_processing"
_PROACTIVE_CONTACT_NOTIFICATION_TYPE = "programa.proactive_contact"
_NOTIFY_SLA_RISK_NOTIFICATION_TYPE = "programa.notify_sla_risk"


def _call_guarded(
    fn: Callable[[dict[str, Any]], dict[str, Any]], variables: dict[str, Any]
) -> dict[str, Any]:
    """Call a programa.py guard function with the reclassification `FunctionWorker.execute` applies.

    Needed because `stratify_risk`/`proactive_contact` (defense-in-depth `ERR_PROGRAMA_NO_CONSENT`
    guards) are now raw-handler-wrapped (item A/B, Kafka seam) instead of `FunctionWorker`-wrapped
    — without this, their `ProgramaError` would fall through the harness's generic `except
    Exception` branch (`harness.py` `_handle`) as an *unclassified* error (engine-computed retry),
    NOT the intended never-retried incident. This mirrors `base.FunctionWorker.execute` byte-for-
    byte (same exception family, same duck-typed `.code`/`.message` check, same
    `ValueError(f"{code}: {message}")` re-raise) so the harness's existing `except ValueError`
    branch still routes to `failure(retries=0)` — a guaranteed, never-retried, human-visible
    incident (ADR-0008) — EXACTLY as it did when these functions were `FunctionWorker`-wrapped.
    Already-classified exception types (the harness's own `PermissionError`/`ValueError`/
    `RuntimeError`/`OSError`/`TimeoutError`/`ConnectionError` family) and `WorkerBpmnError` pass
    through unchanged (`check_consent`'s modeled boundary raise never reaches this helper — it
    stays `FunctionWorker`-wrapped).
    """
    try:
        return fn(variables)
    except (
        WorkerBpmnError,
        PermissionError,
        ValueError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        raise
    except Exception as exc:  # noqa: BLE001 — reclassified below, mirrors base.FunctionWorker.execute.
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None)
        if isinstance(code, str) and isinstance(message, str):
            raise ValueError(f"{code}: {message}") from exc
        raise


def make_stratify_risk_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.programa.stratify_risk` (serves `ST_StratifyRisk`).

    Item A (root fix): `stratify_risk` itself (pure function + guard) is UNCHANGED; only the
    registration wrapping changes so the worker can publish `{"type": "programa.stratify_risk",
    ...}` to `_NOTIFICATIONS_TOPIC` — the `_PHI_NOTIFICATION_TYPES` invariant the port's test suite
    checks (a PHI worker's notification must NEVER be observed when consent was refused) depends on
    this channel existing. `kafka=None` (no producer wired) logs a warning and still returns the
    result — the task MUST complete either way (never blocks the flow on a missing producer).
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = _call_guarded(stratify_risk, task.variables)
        if kafka is None:
            logger.warning("programa_stratify_risk_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _STRATIFY_RISK_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "programa_id": task.variables.get("programa_id", ""),
            "beneficiario_pseudo_id": task.variables.get("beneficiario_pseudo_id", ""),
            "risco_estratificado": result.get("risco_estratificado"),
        }
        # best_effort=False — no BPMN error boundary declared on ST_StratifyRisk -> RAW propagate
        # to the harness retry/incident ladder (ADR-0030), mirrors recurso/lgpd's own posture.
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


def make_stop_processing_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.programa.stop_processing` (serves `ST_StopProcessing`).

    Item A (root fix): `stop_processing` itself is UNCHANGED; only the registration wrapping
    changes so the worker can publish `{"type": "programa.stop_processing", ...}` to
    `_NOTIFICATIONS_TOPIC` — the revogacao-interrompe-processamento invariant the port's test suite
    checks depends on this channel existing (`notifications_of_type("programa.stop_processing")`).
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = _call_guarded(stop_processing, task.variables)
        if kafka is None:
            logger.warning("programa_stop_processing_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _STOP_PROCESSING_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "programa_id": task.variables.get("programa_id", ""),
            "beneficiario_pseudo_id": task.variables.get("beneficiario_pseudo_id", ""),
        }
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


def make_proactive_contact_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.programa.proactive_contact` (serves `ST_ProactiveContact`).

    Item B (new worker): needs the async Kafka seam for the same
    `notifications_of_type("programa.proactive_contact")` observability the `_PHI_NOTIFICATION_
    TYPES` invariant checks (this IS a PHI-touching worker, D9 — contact only proceeds with
    `consent_checked==true`).
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = _call_guarded(proactive_contact, task.variables)
        if kafka is None:
            logger.warning("programa_proactive_contact_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _PROACTIVE_CONTACT_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "programa_id": task.variables.get("programa_id", ""),
            "beneficiario_pseudo_id": task.variables.get("beneficiario_pseudo_id", ""),
        }
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


def make_notify_sla_risk_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.programa.notify_sla_risk` (serves `ST_NotifySlaRisk`).

    Item C (new worker): informational-only alert (non-interruptive timer) — no guard, so
    `_call_guarded` is unnecessary here, but the Kafka seam is still needed for
    `notifications_of_type("programa.notify_sla_risk")` observability. Mirrors
    `recurso.make_notify_sla_risk_handler`'s rationale (recurso.py:716-752): `UT_DecisaoClinica`
    stays open, no decision is made or altered.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = notify_sla_risk(task.variables)
        if kafka is None:
            logger.warning("programa_notify_sla_risk_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _NOTIFY_SLA_RISK_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "programa_id": task.variables.get("programa_id", ""),
            "beneficiario_pseudo_id": task.variables.get("beneficiario_pseudo_id", ""),
        }
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   check_consent    -> operadora.programa.check_consent (exact spec match, CHOKEPOINT guard,
#     FunctionWorker-wrapped — raises the MODELED WorkerBpmnError, no kafka publish needed)
#   stratify_risk    -> operadora.programa.stratify_risk (exact spec match; raw handler, item A —
#     the FIRST external task after Start_Cuidado, immediately after the consent gate)
#   enroll_beneficiario -> operadora.programa.build_care_plan
#     (spec match: task name says "care.enroll"; FunctionWorker-wrapped, no kafka publish)
#   register_discharge (alias register_program_discharge)
#     -> operadora.programa.register_program_discharge (exact spec match, GUARDED,
#     FunctionWorker-wrapped — ProgramaError -> ValueError -> incident, no kafka publish)
#   stop_processing  -> operadora.programa.stop_processing (exact spec match; raw handler, item A)
#   proactive_contact -> operadora.programa.proactive_contact (exact spec match; raw handler,
#     item B — NEW worker, closes the T2.5-documented registry gap)
#   notify_sla_risk  -> operadora.programa.notify_sla_risk (exact spec match; raw handler,
#     item C — NEW worker, closes the T2.5-documented registry gap)
# monitor_programa has no distinct spec topic — registered under a
# function-derived topic for registry completeness.
# ---------------------------------------------------------------


def register_programa_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-PROGRAMA-001 workers on `harness` — 8 `operadora.programa.*` topics.

    `kafka` is threaded into the 4 raw-handler factories (item A/B/C) that publish an internal
    notification; the other 4 stay plain `FunctionWorker` entries with no kafka dependency at all
    (mirrors `recurso.register_recurso_workers`'s split between `FunctionWorker` entries and raw
    handlers).
    """
    del seams  # unused — no other seam dependency (no dmn/etc. threaded into programa.py workers)
    harness.register_worker(FunctionWorker("operadora.programa.check_consent", check_consent))
    harness.register_worker(FunctionWorker("operadora.programa.build_care_plan", enroll_beneficiario))
    harness.register_worker(FunctionWorker("operadora.programa.monitor_programa", monitor_programa))
    harness.register_worker(
        FunctionWorker("operadora.programa.register_program_discharge", register_program_discharge)
    )
    # Items A/B/C — raw handlers, need the async Kafka seam (module-level rationale above).
    harness.register(_STRATIFY_RISK_TOPIC, make_stratify_risk_handler(kafka))
    harness.register(_STOP_PROCESSING_TOPIC, make_stop_processing_handler(kafka))
    harness.register(_PROACTIVE_CONTACT_TOPIC, make_proactive_contact_handler(kafka))
    harness.register(_NOTIFY_SLA_RISK_TOPIC, make_notify_sla_risk_handler(kafka))
