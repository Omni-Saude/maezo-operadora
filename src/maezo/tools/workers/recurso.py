"""SP-OP-RECURSO-001 Worker — Recurso de Glosa.

External tasks for glosa appeal processing.
Negativa-like L0 hard: register_desistencia is GUARDED by
ERR_DESISTENCIA_NOT_HUMAN — maintaining a glosa (denying the appeal)
only materializes after human decision.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class DesistenciaNotHumanError(PermissionError):
    """Raised when register_desistencia is called without human authorization.

    Guard ERR_DESISTENCIA_NOT_HUMAN — the worker MUST refuse to register
    a desistencia (maintain glosa) unless decisao_recurso == NAO_RECORRER
    was set by a human with all required fields.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_DESISTENCIA_NOT_HUMAN: desistencia requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class RecursoGlosaInvalidaError(ValueError):
    """Raised when glosa_id does not reference a confirmed/active glosa (ERR_RECURSO_INVALID_GLOSA)."""

    def __init__(self, glosa_id: str = "") -> None:
        msg = f"ERR_RECURSO_INVALID_GLOSA: glosa_id '{glosa_id}' not found or not active"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class RecursoInput:
    """Input for recurso validation and processing."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    numero_lote_tiss: str = ""
    numero_conta: str | None = None
    prestador_id: str = ""
    beneficiario_pseudo_id: str = ""
    glosa_type: str = ""
    glosa_reason_code: str = ""
    valor_glosado_brl: float = 0.0
    codigo_procedimento_tuss: str = ""
    cid10: str | None = None
    documentos_recurso_refs: list[dict[str, Any]] = field(default_factory=list)
    data_ciencia_glosa: str = ""
    data_recebimento_recurso_iso: str = ""
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False


@dataclass
class RecursoValidationResult:
    """Output of validate_recurso."""

    valid: bool = False
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class RecursoAdmissibilityResult:
    """Output of assess_eligibility — DMN recurso_admissibility + recurso_eligibility."""

    roteamento: str = "ANALISE_HUMANA"
    motivo: str = ""
    grupo_revisor: str = "analista-recurso-glosa"
    recorivel: bool = False


@dataclass
class RecursoDesistenciaInput:
    """Input for register_desistencia — the gated adverse effect."""

    decisao_recurso: str = ""
    justificativa_desistencia: str = ""
    valor_glosa_aceito: float = 0.0
    referencia_contratual: str = ""
    analista_id: str = ""


@dataclass
class RecursoDesistenciaResult:
    """Output of register_desistencia."""

    registered: bool = False
    protocolo: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def validate_recurso(input_data: RecursoInput) -> RecursoValidationResult:
    """Validate recurso inputs — compute glosa_existe, prazo, and documentacao flags.

    Pre-resolves facts before BRT_Admissibilidade. The arithmetic of
    prazo lives in the worker; the decision of NAO_RECORRER is always human.
    """
    logger.info(
        "recurso.validate_recurso.start",
        tenant_id=input_data.tenant_id,
        glosa_id=input_data.glosa_id,
    )

    errors: list[str] = []

    glosa_existe = input_data.glosa_existe
    if not input_data.glosa_id.strip():
        errors.append("glosa_id ausente")
        glosa_existe = False

    dentro_prazo = input_data.dentro_prazo_recurso
    if input_data.data_ciencia_glosa and not dentro_prazo:
        errors.append("fora do prazo recursal")

    docs_completa = input_data.documentacao_recurso_completa
    if not docs_completa and input_data.documentos_recurso_refs is not None:
        # Document list provided but flagged incomplete
        pass

    result = RecursoValidationResult(
        valid=glosa_existe and len(errors) == 0,
        glosa_existe=glosa_existe,
        dentro_prazo_recurso=dentro_prazo,
        documentacao_recurso_completa=docs_completa,
        errors=errors,
    )

    logger.info("recurso.validate_recurso.complete", valid=result.valid, errors=errors)
    return result


def assess_eligibility(
    input_data: RecursoInput,
    validation: RecursoValidationResult,
    *,
    dmn: DmnTransport | None = None,
) -> RecursoAdmissibilityResult:
    """Assess recurso admissibility and eligibility.

    Evaluates TWO chained deployed decision tables (ADR-0028/T1.5) — replaces the hand-forked
    if/elif ladder + a hand-computed `grupo_revisor`/`recorivel` that did NOT correspond to
    either table's real inputs/outputs:

    1. `recurso_admissibility` (glosa_existe, dentro_prazo_recurso,
       documentacao_recurso_completa) -> `roteamento` in {ANALISE_HUMANA,
       PENDENTE_DOCUMENTACAO, SEGUE_ANALISE} + `motivo`.
    2. ONLY when (1) returns `SEGUE_ANALISE` (non-terminal — admissible, proceed), chain into
       `recurso_eligibility` (glosa_type, glosa_reason_code, valor_glosado_brl) ->
       `grupo_revisor` + a `RECORRIVEL`/`ANALISE_HUMANA` signal folded into `recorivel`. The old
       Python instead derived `grupo_revisor` from a hand-coded tecnica/clinica check and set
       `recorivel = (roteamento == "SEGUE_ANALISE")` — a tautology, never actually consulting
       the real eligibility table's own output domain or its `glosa_reason_code`/
       `valor_glosado_brl` inputs.

    NO automatic desistencia — inadmissibility routes to ANALISE_HUMANA either way. Both
    decisions live-verified against the compose engine before cutover (5/5 existing test
    scenarios reproduced exactly). Flagged: glosa appeal is money-adjacent — per ADR-0028 §7
    this is in the money/adverse cutover bucket requiring policy-guardian review.
    """
    logger.info(
        "recurso.assess_eligibility.start",
        glosa_type=input_data.glosa_type,
        glosa_existe=validation.glosa_existe,
    )

    dmn_transport = require_dmn(dmn, "operadora.recurso.assess_eligibility")

    adm_rows, adm_version = evaluate_sync(
        dmn_transport,
        "recurso_admissibility",
        {
            "glosa_existe": validation.glosa_existe,
            "dentro_prazo_recurso": validation.dentro_prazo_recurso,
            "documentacao_recurso_completa": validation.documentacao_recurso_completa,
        },
    )
    adm_row = first_row(adm_rows, "recurso_admissibility", {"glosa_id": input_data.glosa_id})
    roteamento = str(adm_row.get("roteamento", "ANALISE_HUMANA"))
    motivo = str(adm_row.get("motivo", ""))

    grupo_revisor = "analista-recurso-glosa"
    recorivel = False
    elig_version = None
    if roteamento == "SEGUE_ANALISE":
        elig_rows, elig_version = evaluate_sync(
            dmn_transport,
            "recurso_eligibility",
            {
                "glosa_type": input_data.glosa_type,
                "glosa_reason_code": input_data.glosa_reason_code,
                "valor_glosado_brl": input_data.valor_glosado_brl,
            },
        )
        elig_row = first_row(elig_rows, "recurso_eligibility", {"glosa_id": input_data.glosa_id})
        grupo_revisor = str(elig_row.get("grupo_revisor", grupo_revisor))
        recorivel = str(elig_row.get("roteamento", "")) == "RECORRIVEL"

    result = RecursoAdmissibilityResult(
        roteamento=roteamento,
        motivo=motivo,
        grupo_revisor=grupo_revisor,
        recorivel=recorivel,
    )

    logger.info(
        "recurso.assess_eligibility.complete",
        roteamento=result.roteamento,
        grupo_revisor=result.grupo_revisor,
        dmn_admissibility_version=adm_version.version,
        dmn_eligibility_version=elig_version.version if elig_version else None,
    )
    return result


def analyze_merits(input_data: RecursoInput) -> dict[str, Any]:
    """Analyze the merits of a recurso — delegates to Marina (LLM agent).

    The agent instructs (monta dossie), never decides.
    """
    logger.info(
        "recurso.analyze_merits.start",
        glosa_id=input_data.glosa_id,
        glosa_type=input_data.glosa_type,
    )

    result = {
        "glosa_id": input_data.glosa_id,
        "glosa_type": input_data.glosa_type,
        "valor_glosado_brl": input_data.valor_glosado_brl,
        "codigo_procedimento_tuss": input_data.codigo_procedimento_tuss,
        "analise": "dossie_instruido",
        "merito_sugerido": "ANALISE_HUMANA",  # Agent never decides
    }

    logger.info("recurso.analyze_merits.complete")
    return result


def prepare_dossier(validation: RecursoValidationResult, merits: dict[str, Any]) -> dict[str, Any]:
    """Prepare the recurso dossier for human analysis.

    Combines validation facts and merit analysis into a structured dossier.
    """
    logger.info("recurso.prepare_dossier.start")

    result = {
        "dossier": {
            "validacao": {
                "glosa_existe": validation.glosa_existe,
                "dentro_prazo": validation.dentro_prazo_recurso,
                "documentacao_completa": validation.documentacao_recurso_completa,
            },
            "meritos": merits,
        },
        "roteamento": "ANALISE_HUMANA",
    }

    logger.info("recurso.prepare_dossier.complete")
    return result


def notify_prestador(
    prestador_id: str,
    glosa_id: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Notify the prestador about recurso status or document requests."""
    logger.info(
        "recurso.notify_prestador",
        prestador_id=prestador_id,
        glosa_id=glosa_id,
        message_type=message_type,
    )

    return {
        "notified": True,
        "prestador_id": prestador_id,
        "glosa_id": glosa_id,
        "message_type": message_type,
    }


def escalate_to_junta(
    glosa_id: str,
    motivo: str = "",
    glosa_type: str = "",
) -> dict[str, Any]:
    """Escalate recurso to junta medica / auditor for clinical/technical review.

    Glosa tecnica/clinica -> medico-auditor decides the merit.
    """
    logger.info(
        "recurso.escalate_to_junta",
        glosa_id=glosa_id,
        glosa_type=glosa_type,
        motivo=motivo,
    )

    return {
        "escalated": True,
        "glosa_id": glosa_id,
        "grupo": "medico-auditor",
        "glosa_type": glosa_type,
        "motivo": motivo,
    }


def _parse_valor_glosa_aceito(value: Any) -> float | None:
    """Coerce a monetary ``valor_glosa_aceito`` to a float for the desistencia guard.

    Root cause: the engine seeds this field as a Camunda ``String`` (e.g. ``"150.00"`` —
    EngineRest._to_camunda_vars has no float branch, so it falls through to the String
    catch-all); harness ``_from_camunda_var`` decodes it back to a Python ``str`` and
    ``pick_fields`` performs NO coercion. A raw ``<= 0`` comparison then raises
    ``TypeError: '<=' not supported between instances of 'str' and 'int'``.

    Returns the parsed amount as a ``float`` — matching the dataclass's declared ``float``
    typing and the canonical ``contas.py`` money idiom (``float(brl)``, dot-decimal, the
    donor/engine wire convention). Returns ``None`` for a missing/blank/non-numeric value so
    the caller FAILS CLOSED (treats it as a missing required field ->
    ``DesistenciaNotHumanError``). NEVER silently defaults to ``0`` — a wrong monetary
    decision on a glosa waiver is a financial defect.
    """
    if isinstance(value, bool):  # bool is an int subclass — never a monetary amount
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def register_desistencia(input_data: RecursoDesistenciaInput) -> RecursoDesistenciaResult:
    """Register desistencia (maintain glosa) — GUARDED adverse effect.

    ERR_DESISTENCIA_NOT_HUMAN: MUST refuse if:
    - decisao_recurso != NAO_RECORRER
    - Missing justificativa_desistencia, valor_glosa_aceito,
      referencia_contratual, or analista_id
    """
    logger.info(
        "recurso.register_desistencia.start",
        decisao_recurso=input_data.decisao_recurso,
        analista_id=input_data.analista_id,
    )

    missing: list[str] = []

    if input_data.decisao_recurso != "NAO_RECORRER":
        missing.append("decisao_recurso != NAO_RECORRER")
    if not input_data.justificativa_desistencia.strip():
        missing.append("justificativa_desistencia")
    # valor_glosa_aceito arrives from Camunda as a String ("150.00"); parse fail-closed
    # before comparing. None (missing/blank/non-numeric) or <= 0 => required field absent.
    valor_glosa_aceito = _parse_valor_glosa_aceito(input_data.valor_glosa_aceito)
    if valor_glosa_aceito is None or valor_glosa_aceito <= 0:
        missing.append("valor_glosa_aceito")
    if not input_data.referencia_contratual.strip():
        missing.append("referencia_contratual")
    if not input_data.analista_id.strip():
        missing.append("analista_id")

    if missing:
        raise DesistenciaNotHumanError(missing_fields=missing)

    import hashlib
    import time

    protocolo = f"RECDESIST-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12].upper()}"

    logger.info(
        "recurso.register_desistencia.complete",
        protocolo=protocolo,
        analista_id=input_data.analista_id,
    )
    return RecursoDesistenciaResult(registered=True, protocolo=protocolo)


def publish_completed(
    event_type: str = "recurso.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish recurso completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "recurso.publish_completed",
        event_type=event_type,
        desfecho=desfecho,
    )

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   notify_prestador  -> operadora.recurso.request_documents  (spec match: default message_type
#                        IS "pendencia_documentacao" == "Abrir pendencia de documentacao ao prestador")
#   analyze_merits     -> operadora.recurso.analyze_request     (spec match: "(agente Marina)" == this
#                        function's own docstring "delegates to Marina (LLM agent)")
#   register_desistencia -> operadora.recurso.register_desistencia (exact spec match, GUARDED)
# validate_recurso/assess_eligibility/prepare_dossier/escalate_to_junta have
# no distinct spec topic today (escalate_to_junta targets medico-auditor —
# a DIFFERENT audience than spec's escalate_ans_timeout, not force-mapped) —
# registered under function-derived topics for registry completeness.
# publish_completed folds into the generic events.publish task per BPMN —
# function-derived topic.
# Spec topics with NO implementing function today (gap, not fabricated here):
# notify_sla_risk, escalate_ans_timeout, submit_appeal, track_status, reconcile_payment.
# ---------------------------------------------------------------------------


def validate_recurso_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.validate_recurso` -> `validate_recurso`."""
    del kafka  # unused — validate_recurso emits no domain event
    input_data = RecursoInput(**pick_fields(variables, RecursoInput))
    result = validate_recurso(input_data)
    return dataclasses.asdict(result)


def assess_eligibility_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    dmn: DmnTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.assess_eligibility` -> `assess_eligibility`."""
    del kafka  # unused — assess_eligibility emits no domain event
    input_data = RecursoInput(**pick_fields(variables, RecursoInput))
    validation = RecursoValidationResult(**pick_fields(variables, RecursoValidationResult))
    result = assess_eligibility(input_data, validation, dmn=dmn)
    return dataclasses.asdict(result)


def request_documents_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.request_documents` -> `notify_prestador`."""
    del kafka  # unused — notify_prestador emits no domain event
    prestador_id = variables.get("prestador_id", "")
    glosa_id = variables.get("glosa_id", "")
    message_type = variables.get("message_type", "pendencia_documentacao")
    return notify_prestador(prestador_id, glosa_id, message_type)


def analyze_request_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.analyze_request` -> `analyze_merits`."""
    del kafka  # unused — analyze_merits emits no domain event
    input_data = RecursoInput(**pick_fields(variables, RecursoInput))
    return analyze_merits(input_data)


def prepare_dossier_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.prepare_dossier` -> `prepare_dossier`."""
    del kafka  # unused — prepare_dossier emits no domain event
    validation = RecursoValidationResult(**pick_fields(variables, RecursoValidationResult))
    merits = {
        "glosa_id": variables.get("glosa_id", ""),
        "glosa_type": variables.get("glosa_type", ""),
        "valor_glosado_brl": variables.get("valor_glosado_brl", 0.0),
        "codigo_procedimento_tuss": variables.get("codigo_procedimento_tuss", ""),
        "analise": variables.get("analise", ""),
        "merito_sugerido": variables.get("merito_sugerido", "ANALISE_HUMANA"),
    }
    return prepare_dossier(validation, merits)


def escalate_to_junta_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.escalate_to_junta` -> `escalate_to_junta`."""
    del kafka  # unused — escalate_to_junta emits no domain event
    glosa_id = variables.get("glosa_id", "")
    motivo = variables.get("motivo", "")
    glosa_type = variables.get("glosa_type", "")
    return escalate_to_junta(glosa_id, motivo, glosa_type)


def register_desistencia_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.register_desistencia` -> `register_desistencia`
    (GUARDED).

    Raises `DesistenciaNotHumanError` (fail-closed, ERR_DESISTENCIA_NOT_HUMAN) when
    `decisao_recurso != NAO_RECORRER` or required fields are missing — unchanged guard, only the
    dict<->dataclass marshalling is new.
    """
    del kafka  # unused — register_desistencia emits no domain event itself
    input_data = RecursoDesistenciaInput(**pick_fields(variables, RecursoDesistenciaInput))
    result = register_desistencia(input_data)
    return dataclasses.asdict(result)


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "recurso.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_recurso_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-RECURSO-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale. `dmn` (ADR-0028 §1 seam) is
    threaded into `assess_eligibility_entry` (`recurso_admissibility` + `recurso_eligibility`,
    T1.5 cutover).
    """
    dmn = seams.get("dmn")
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.validate_recurso", functools.partial(validate_recurso_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.assess_eligibility",
            functools.partial(assess_eligibility_entry, kafka=kafka, dmn=dmn),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.request_documents", functools.partial(request_documents_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.analyze_request", functools.partial(analyze_request_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.prepare_dossier", functools.partial(prepare_dossier_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.escalate_to_junta", functools.partial(escalate_to_junta_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.register_desistencia",
            functools.partial(register_desistencia_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.publish_completed", functools.partial(publish_completed_entry, kafka=kafka)
        )
    )
