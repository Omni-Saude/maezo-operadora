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

from maezo.platform.integrations.partition_key import partition_key_for_task
from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)

# Internal-notification channel (mirrors lgpd.py's own `_NOTIFICATIONS_TOPIC` — a
# `type`-discriminated envelope on `operadora.notifications.internal`, NOT a BPMN-declared
# domain-event topic). Used by the 4 new raw-handler workers below whose test-spec-demanded
# observability (`notifications_of_type(...)`) needs the async Kafka seam a `FunctionWorker`'s
# sync boundary cannot reach (`WorkerBase`'s own docstring: "Async I/O is handled by the
# engine/message layer, not by the worker logic").
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class DesistenciaNotHumanError(PermissionError):
    """Raised when register_desistencia is called without human authorization.

    Guard ERR_DESISTENCIA_NOT_HUMAN — the worker MUST refuse to register a desistencia
    (maintain glosa) unless ONE of two human channels was satisfied with all its required
    fields:
    - analista channel (ST_RegisterDesistencia): decisao_recurso == NAO_RECORRER + analista_id
    - auditor channel (ST_RegisterGlosaMantida, auditor merito ACEITAR_GLOSA):
      decisao_auditor_recurso == ACEITAR_GLOSA + auditor_id
    Both channels ALSO require justificativa_desistencia, valor_glosa_aceito, and
    referencia_contratual.
    """

    def __init__(self, missing_fields: list[str] | None = None, channel: str = "") -> None:
        self.missing_fields = missing_fields or []
        self.channel = channel
        msg = "ERR_DESISTENCIA_NOT_HUMAN: desistencia requires human decision"
        if self.channel:
            msg += f" (canal tentado: {self.channel})"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


_ERR_RECURSO_INVALID_GLOSA = "ERR_RECURSO_INVALID_GLOSA"

# ADR-0030 Tier-0/Tier-2 (G2-val, origin/consistency validation — fail-safe, NON-adverse: the
# worker never decides merito, it only signals that `glosa_id` arrived empty/absent at the
# origin). NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated (ADR-0030 census + §4's `is_te_gated`
# predicate: matched only by the `_NOT_HUMAN` suffix or `ERR_AUTH_DENIAL_INCOMPLETE`) — enabled
# directly in the runtime allowlist without an audited-refusal co-requisite. Consumption-covered
# (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"): both
# `operadora.recurso.request_documents` and `operadora.recurso.analyze_request` are consumed
# ONLY by SP-OP-RECURSO-001, which declares this errorCode on BOTH topics' boundary catches
# (`BE_GlosaInvalidaDocs` / `BE_GlosaInvalidaDossie` -> `End_RecursoGlosaInvalidaOrigem`). Mirrors
# `auth.AUTH_BPMN_ERROR_ALLOWLIST` / `lgpd.LGPD_BPMN_ERROR_ALLOWLIST` — unioned into
# `worker_runtime/service.py`'s `_GATE_PROVEN_BPMN_ERROR_CODES` (SHARED FILE — see PR/report).
RECURSO_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({_ERR_RECURSO_INVALID_GLOSA})


def _require_glosa_id(glosa_id: str) -> None:
    """GAP-RECURSO-3 guard: glosa_id ausente/vazio -> `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)`.

    Fires BEFORE any downstream call (`notify_prestador`/`analyze_merits`) — mirrors cancel.py's
    `WorkerBpmnError` raising pattern (`confirm_maintained_decision`). The BPMN's boundary catches
    (`BE_GlosaInvalidaDocs` on `ST_SolicitarDocumentos`, `BE_GlosaInvalidaDossie` on
    `ST_PrepararDossie`) route to the shared NEUTRO terminal `End_RecursoGlosaInvalidaOrigem`
    (GAP-RECURSO-3) — never an adverse outcome. This is a TECHNICAL origin/consistency guard,
    distinct from the business fact `glosa_existe` (which routes to `ANALISE_HUMANA` via the
    `recurso_admissibility` DMN, never an error — see module docstring GAP-RECURSO-3 note at the
    top of the BPMN). The worker RECUSA prosseguir; it never decides merito.
    """
    if not glosa_id.strip():
        raise WorkerBpmnError(
            _ERR_RECURSO_INVALID_GLOSA,
            "glosa_id ausente/vazio nas variaveis de processo — defeito TECNICO de origem "
            "(distinto do fato de negocio glosa_existe, que roteia a ANALISE_HUMANA via DMN "
            "recurso_admissibility, nunca erro). O worker RECUSA prosseguir (nao decide merito) — "
            "boundary catch (BE_GlosaInvalidaDocs/BE_GlosaInvalidaDossie) roteia a "
            "End_RecursoGlosaInvalidaOrigem (GAP-RECURSO-3, terminal NEUTRO nao-adverso).",
        )


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
    """Input for register_desistencia — the gated adverse effect.

    Two human channels route to the SAME `operadora.recurso.register_desistencia` topic
    (BPMN `ST_RegisterDesistencia` / `ST_RegisterGlosaMantida`): the analista's
    `decisao_recurso == NAO_RECORRER` + `analista_id`, or the auditor's merito
    `decisao_auditor_recurso == ACEITAR_GLOSA` + `auditor_id` (`GW_MeritoAuditor` ->
    `Flow_GWMerito_AceitarGlosa` -> `End_GlosaMantida`).
    """

    decisao_recurso: str = ""
    justificativa_desistencia: str = ""
    valor_glosa_aceito: float = 0.0
    referencia_contratual: str = ""
    analista_id: str = ""
    decisao_auditor_recurso: str = ""
    auditor_id: str = ""


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

    ERR_DESISTENCIA_NOT_HUMAN: MUST refuse unless ONE of two human channels is attempted —
    - analista channel: decisao_recurso == NAO_RECORRER (requires analista_id)
    - auditor channel: decisao_auditor_recurso == ACEITAR_GLOSA (requires auditor_id)
    Both channels ALSO require justificativa_desistencia, valor_glosa_aceito (> 0), and
    referencia_contratual. Refuses with `DesistenciaNotHumanError` ONLY if NEITHER channel's
    decision field matches; otherwise validates the attempted channel's own required fields
    (channel-aware missing-fields message).
    """
    logger.info(
        "recurso.register_desistencia.start",
        decisao_recurso=input_data.decisao_recurso,
        decisao_auditor_recurso=input_data.decisao_auditor_recurso,
        analista_id=input_data.analista_id,
        auditor_id=input_data.auditor_id,
    )

    is_analista = input_data.decisao_recurso == "NAO_RECORRER"
    is_auditor = input_data.decisao_auditor_recurso == "ACEITAR_GLOSA"

    if not is_analista and not is_auditor:
        raise DesistenciaNotHumanError(
            missing_fields=[
                "decisao_recurso != NAO_RECORRER (canal analista)",
                "decisao_auditor_recurso != ACEITAR_GLOSA (canal auditor)",
            ],
            channel="nenhum",
        )

    channel = "analista" if is_analista else "auditor"
    missing: list[str] = []

    if is_analista and not input_data.analista_id.strip():
        missing.append("analista_id")
    if is_auditor and not input_data.auditor_id.strip():
        missing.append("auditor_id")
    if not input_data.justificativa_desistencia.strip():
        missing.append("justificativa_desistencia")
    # valor_glosa_aceito arrives from Camunda as a String ("150.00"); parse fail-closed
    # before comparing. None (missing/blank/non-numeric) or <= 0 => required field absent.
    valor_glosa_aceito = _parse_valor_glosa_aceito(input_data.valor_glosa_aceito)
    if valor_glosa_aceito is None or valor_glosa_aceito <= 0:
        missing.append("valor_glosa_aceito")
    if not input_data.referencia_contratual.strip():
        missing.append("referencia_contratual")

    if missing:
        raise DesistenciaNotHumanError(missing_fields=missing, channel=channel)

    import hashlib
    import time

    protocolo = f"RECDESIST-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12].upper()}"

    logger.info(
        "recurso.register_desistencia.complete",
        protocolo=protocolo,
        channel=channel,
        analista_id=input_data.analista_id,
        auditor_id=input_data.auditor_id,
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
# T3.1 P2b (Finding 2, RESOLVED — built, pending live-proof flip): the 5 previously-zero-worker
# spec topics now have implementing functions + registrations below: notify_sla_risk,
# escalate_ans_timeout, submit_appeal, track_status (raw handlers — need the async Kafka seam for
# their test-spec-demanded `notifications_of_type`/domain-event observability) and
# reconcile_payment (plain FunctionWorker, mirrors contas.py:441-463 exactly — no kafka).
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
    """Dict-boundary entry for `operadora.recurso.request_documents` -> `notify_prestador`.

    GAP-RECURSO-3 (Finding 5): guards `glosa_id` BEFORE calling `notify_prestador` — raises
    `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` (`_require_glosa_id`) when absent/empty, so no
    pendencia is opened for an origin-invalid glosa (`BE_GlosaInvalidaDocs` boundary catch).
    """
    del kafka  # unused — notify_prestador emits no domain event
    glosa_id = variables.get("glosa_id", "")
    _require_glosa_id(glosa_id)
    prestador_id = variables.get("prestador_id", "")
    message_type = variables.get("message_type", "pendencia_documentacao")
    return notify_prestador(prestador_id, glosa_id, message_type)


def analyze_request_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.analyze_request` -> `analyze_merits`.

    GAP-RECURSO-3 (Finding 5): guards `glosa_id` BEFORE calling `analyze_merits` — raises
    `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` (`_require_glosa_id`) when absent/empty, so
    Marina's dossier is never prepared for an origin-invalid glosa (`BE_GlosaInvalidaDossie`
    boundary catch).
    """
    del kafka  # unused — analyze_merits emits no domain event
    glosa_id = variables.get("glosa_id", "")
    _require_glosa_id(glosa_id)
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

    Raises `DesistenciaNotHumanError` (fail-closed, ERR_DESISTENCIA_NOT_HUMAN) unless the
    analista channel (`decisao_recurso == NAO_RECORRER` + `analista_id`) or the auditor channel
    (`decisao_auditor_recurso == ACEITAR_GLOSA` + `auditor_id`) is satisfied with its required
    fields — only the dict<->dataclass marshalling is a boundary concern here.
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


# ---------------------------------------------------------------------------
# T3.1 P2b (Finding 2) — the 5 previously-zero-worker topics.
#
# notify_sla_risk / escalate_ans_timeout / submit_appeal / track_status are raw
# `harness.register()` handlers (like `events.make_publish_event_handler` / `lgpd
# .make_request_additional_proof_handler`), NOT `FunctionWorker`-wrapped: each needs the async
# Kafka seam a sync `FunctionWorker.execute` boundary cannot reach (`WorkerBase`'s own docstring:
# "Async I/O is handled by the engine/message layer, not by the worker logic") to satisfy the
# ported test-spec's `notifications_of_type(...)`/`has_event(...)` observability —
# test_sp_op_recurso_001.py's `test_happy_path_escalar_auditor_mantem_recurso` (submit_appeal),
# `test_timer_alerta_sla_nao_interruptivo` (notify_sla_risk),
# `test_loop_acompanhamento_limitado` (track_status), and the four `test_prazo_max_*` tests
# (escalate_ans_timeout's `agents.events.recurso.sla_breached` fase=prazo_max domain event) each
# assert on a kafka-observed channel that is unreachable without it — and each assertion is
# reachable independent of the OTHER, already-tracked, out-of-scope Kafka-producer gap (finding
# 1: `analyze_request_entry`/`request_documents_entry` never call `kafka.publish`).
# reconcile_payment stays a plain typed function + `FunctionWorker`-wrapped dict-boundary entry
# (mirrors `contas.py:441-463`'s `reconcile_payment` exactly — sync, `del kafka`, no assert in the
# reachable test-spec ever demands a kafka-observed channel from it).
# ---------------------------------------------------------------------------

_NOTIFY_SLA_RISK_TOPIC = "operadora.recurso.notify_sla_risk"
_NOTIFY_SLA_RISK_NOTIFICATION_TYPE = "recurso.notify_sla_risk"


@dataclass
class NotifySlaRiskInput:
    """Input for `notify_sla_risk` — `BT_AlertaSlaRecurso` (non-interruptive SLA-risk alert)."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    glosa_type: str = ""


def notify_sla_risk(input_data: NotifySlaRiskInput) -> dict[str, Any]:
    """Notify coordenacao-recurso of SLA risk (non-interruptive timer `BT_AlertaSlaRecurso`).

    Informational only: `UT_AnaliseRecursoAnalista` stays open (`cancelActivity="false"`), no
    decision is made or altered — mirrors `cancel.py`'s own `notify_sla_risk`. Contract
    SP-OP-RECURSO-001.md SS Topicos: "alerta coordenacao-recurso (timer nao-interruptivo)".
    """
    logger.info(
        "recurso.notify_sla_risk",
        tenant_id=input_data.tenant_id,
        numero_guia_tiss=input_data.numero_guia_tiss,
        glosa_id=input_data.glosa_id,
    )
    return {"sla_risk_notified": True, "glosa_id": input_data.glosa_id}


def make_notify_sla_risk_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.recurso.notify_sla_risk` (serves `ST_NotificarRiscoSla`).

    Needs the async Kafka seam for the `notifications_of_type("recurso.notify_sla_risk")`
    observability channel the ported test-spec demands
    (`test_timer_alerta_sla_nao_interruptivo`) — see the module-level rationale above.

    Fail-closed publish posture (t2-notify-integrity item 1): the publish is this task's ONLY
    effect, so `best_effort=False` forces the producer to PROPAGATE a broker failure instead of
    silently swallowing it while the handler reports success. No BPMN error boundary is declared
    on `ST_NotificarRiscoSla` -> RAW propagate to the harness retry/incident ladder (ADR-0030) —
    byte-for-byte the lgpd `make_notify_sla_risk_handler` posture. Containment: the task is fed
    ONLY by the NON-interrupting `BT_AlertaSlaRecurso` boundary timer, so the propagation stays on
    the alert side branch — `UT_AnaliseRecursoAnalista` and the interrupting SLA/P30D ceilings are
    engine-side and unaffected.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = NotifySlaRiskInput(**pick_fields(task.variables, NotifySlaRiskInput))
        result = notify_sla_risk(input_data)
        if kafka is None:
            logger.warning("recurso_notify_sla_risk_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _NOTIFY_SLA_RISK_NOTIFICATION_TYPE,
            "tenant_id": input_data.tenant_id,
            "numero_guia_tiss": input_data.numero_guia_tiss,
            "glosa_id": input_data.glosa_id,
            "glosa_type": input_data.glosa_type,
        }
        # best_effort=False — see factory docstring (no boundary declared -> raw propagate).
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        return result

    return handler


_ESCALATE_ANS_TIMEOUT_TOPIC = "operadora.recurso.escalate_ans_timeout"
_ESCALATE_ANS_TIMEOUT_DEFAULT_EVENT_TOPIC = "agents.events.recurso.sla_breached"
_ESCALATE_ANS_TIMEOUT_FASE = "prazo_max"


@dataclass
class EscalateAnsTimeoutInput:
    """Input for `escalate_ans_timeout` — the common target of the 3 P30D ceiling boundaries."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    glosa_type: str = ""


def escalate_ans_timeout(input_data: EscalateAnsTimeoutInput) -> dict[str, Any]:
    """Escalate the P30D regulatory-ceiling (RN 424) breach to human coordenacao-recurso.

    "ANS" in the BPMN task name (`ST_EscalateAnsTimeout`) names the RN 424 REGULATORY deadline
    (Agencia Nacional de Saude Suplementar rulemaking) — this is NOT an ANS-gateway integration:
    the worker NEVER imports/touches `ans_gateway.py`'s `AnsGatewayTransport` triple (the real
    external-protocol seam belongs to SP-OP-ANS-SUBMIT-001, a different process entirely).
    Common target of ALL THREE P30D boundary timers (`BT_PrazoMaxRecurso`/`BT_PrazoMaxCoord`/
    `BT_PrazoMaxAuditor`, GAP-RECURSO-1 — same absolute instant regardless of who held the
    recurso). NUNCA auto-desfecho adverso — routes unconditionally to `UT_EscalonamentoPrazo`
    (human decides the destino).
    """
    logger.info(
        "recurso.escalate_ans_timeout",
        tenant_id=input_data.tenant_id,
        numero_guia_tiss=input_data.numero_guia_tiss,
        glosa_id=input_data.glosa_id,
    )
    return {"escalated": True, "fase": _ESCALATE_ANS_TIMEOUT_FASE, "glosa_id": input_data.glosa_id}


def make_escalate_ans_timeout_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.recurso.escalate_ans_timeout` (serves
    `ST_EscalateAnsTimeout`).

    Publishes the EMBEDDED `agents.events.recurso.sla_breached` (fase=`prazo_max`) domain event
    the BPMN's own `event_topic_breach` inputParameter documents — the SAME "embedded publish"
    idiom as `ST_SolicitarDocumentos`'s `event_topic_pended` (module docstring finding 1), but
    THIS task has no downstream `ST_Publish*` service task to route through (unlike
    `ST_PublishSlaBreach` for fase=`analise`) — the worker must publish it directly. Needs the
    async Kafka seam -> raw handler (same rationale as `make_notify_sla_risk_handler`). Reachable,
    Tier-2-only-blocked assertions: `test_prazo_max_recurso_escala_humano`,
    `test_prazo_max_ancora_absoluta_nao_no_attach_da_ut`,
    `test_prazo_max_coord_mesmo_instante_absoluto`, `test_prazo_max_auditor_mesmo_instante_absoluto`,
    `test_prazo_max_escalonamento_sem_cascata` — all assert
    `has_event(_RECURSO_SLA_BREACHED, fase="prazo_max")`.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        variables = task.variables
        input_data = EscalateAnsTimeoutInput(**pick_fields(variables, EscalateAnsTimeoutInput))
        result = escalate_ans_timeout(input_data)
        if kafka is None:
            logger.warning("recurso_escalate_ans_timeout_no_producer", business_key=task.business_key)
            return result
        event_topic = str(variables.get("event_topic_breach") or _ESCALATE_ANS_TIMEOUT_DEFAULT_EVENT_TOPIC)
        payload = {
            "fase": _ESCALATE_ANS_TIMEOUT_FASE,
            "tenant_id": input_data.tenant_id,
            "numero_guia_tiss": input_data.numero_guia_tiss,
            "glosa_id": input_data.glosa_id,
            "glosa_type": input_data.glosa_type,
        }
        # GAP-SC-04-a partition key — see the shared chain in `partition_key.py`.
        message_key = partition_key_for_task(task, event_topic, payload)
        await kafka.publish(event_topic, payload, key=message_key)
        return result

    return handler


_SUBMIT_APPEAL_TOPIC = "operadora.recurso.submit_appeal"
_SUBMIT_APPEAL_NOTIFICATION_TYPE = "recurso.submit_appeal"
_SUBMIT_APPEAL_PROTOCOLO_PREFIX = "RECAPPEAL-"


@dataclass
class SubmitAppealInput:
    """Input for `submit_appeal` — `ST_SubmitAppeal` (interpoe o recurso, TASY write DROP)."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""


def _mint_protocolo_recurso(business_key: str, tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """Deterministically derive `protocolo_recurso` from business identity.

    ADR-0030/T-H determinism: NO `time.time_ns`/`uuid`/`random` for this (NEW) minting site —
    `tests/unit/tools/workers/test_worker_handler_purity.py`'s non-determinism baseline fence
    tracks `recurso`'s PRE-EXISTING `register_desistencia` `time.time_ns` usage only; this
    function must not add a second, undocumented nondeterminism source. Mirrors
    `LabeledMockAnsGatewayTransport.submit`'s `MOCK-ANS-NAO-VINCULATIVO-{business_key}` idiom
    (`ans_gateway.py`) — the SAME appeal (same business key) always mints the IDENTICAL protocol
    (also fixes the latent retry-idempotency hazard that idiom's own docstring calls out).
    """
    key = business_key.strip() or f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"
    return f"{_SUBMIT_APPEAL_PROTOCOLO_PREFIX}{key}"


def submit_appeal(input_data: SubmitAppealInput, *, business_key: str = "") -> dict[str, Any]:
    """Interpoe o recurso a operadora (TISS) — `ST_SubmitAppeal`.

    So apos `UT_AnaliseRecursoAnalista` com `RECORRER` (ou auditor `MANTER_RECURSO`/
    `RECURSO_PARCIAL`). TASY write DROP (ADR-0013): consome, nunca escreve no Tasy — nenhuma
    chamada TISS real e feita aqui (protocolo sintetico, deterministico,
    `_mint_protocolo_recurso`). Sem efeito adverso. Sets `loop_counter=0` (unica entrada do loop
    de acompanhamento) — the BPMN's own literal `camunda:outputParameter` (`${0}`) ALSO sets this
    engine-side; echoed here so the dict-boundary output is self-consistent for
    unit/harness-level testing without a live engine evaluating that expression.
    """
    protocolo_recurso = _mint_protocolo_recurso(
        business_key, input_data.tenant_id, input_data.numero_guia_tiss, input_data.glosa_id
    )
    logger.info(
        "recurso.submit_appeal",
        glosa_id=input_data.glosa_id,
        protocolo_recurso=protocolo_recurso,
    )
    return {"protocolo_recurso": protocolo_recurso, "loop_counter": 0}


def make_submit_appeal_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.recurso.submit_appeal` (serves `ST_SubmitAppeal`).

    Needs `task.business_key` for deterministic protocolo minting AND the async Kafka seam for
    the `notifications_of_type("recurso.submit_appeal")` observability channel the ported
    test-spec demands (`test_happy_path_escalar_auditor_mantem_recurso`) — contract
    SP-OP-RECURSO-001.md has no distinct Kafka domain-event topic for this worker beyond the
    internal notification (only `protocolo_recurso` as an output VARIABLE).

    Fail-closed publish posture (t2-notify-integrity item 1): this is a MAIN-path fact recording
    a post-human-decision act (the appeal was interposed after RECORRER/MANTER_RECURSO, carrying
    the minted `protocolo_recurso`) — losing it silently is unacceptable, so `best_effort=False`
    forces the producer to PROPAGATE a broker failure. No BPMN error boundary is declared on
    `ST_SubmitAppeal` -> RAW propagate to the harness retry/incident ladder (ADR-0030); the token
    is held at this task (before the P5D tracking loop) until the fact is actually published.
    Retry-safe: `_mint_protocolo_recurso` is deterministic by business key, so a re-dispatch
    republishes the IDENTICAL fact.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = SubmitAppealInput(**pick_fields(task.variables, SubmitAppealInput))
        result = submit_appeal(input_data, business_key=task.business_key)
        if kafka is None:
            logger.warning("recurso_submit_appeal_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _SUBMIT_APPEAL_NOTIFICATION_TYPE,
            "glosa_id": input_data.glosa_id,
            "protocolo_recurso": result["protocolo_recurso"],
        }
        # best_effort=False — see factory docstring (no boundary declared -> raw propagate).
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        return result

    return handler


_TRACK_STATUS_TOPIC = "operadora.recurso.track_status"
_TRACK_STATUS_NOTIFICATION_TYPE = "recurso.track_status"


@dataclass
class TrackStatusInput:
    """Input for `track_status` — `ST_TrackStatus` (loop `ICE_AguardarResposta`, P5D)."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    protocolo_recurso: str = ""


def track_status(input_data: TrackStatusInput) -> dict[str, Any]:
    """Acompanha o status do recurso interposto (loop `ICE_AguardarResposta`, P5D).

    Sem efeito adverso; NUNCA auto-desfecho — a transicao real do recurso chega via
    `msg.recurso.resposta_recebida` (`GW_RecursoResolvido`), nunca por este worker. Does not
    touch `loop_counter` — the BPMN's own `camunda:outputParameter` (`${loop_counter + 1}`)
    increments it engine-side; a second, worker-side increment risks double-counting against
    that expression's pre-complete read of the variable.
    """
    logger.info(
        "recurso.track_status",
        glosa_id=input_data.glosa_id,
        protocolo_recurso=input_data.protocolo_recurso,
    )
    return {"tracked": True, "glosa_id": input_data.glosa_id}


def make_track_status_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.recurso.track_status` (serves `ST_TrackStatus`).

    Needs the async Kafka seam for the `notifications_of_type("recurso.track_status")`
    observability channel the ported test-spec demands (`test_loop_acompanhamento_limitado`).
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = TrackStatusInput(**pick_fields(task.variables, TrackStatusInput))
        result = track_status(input_data)
        if kafka is None:
            logger.warning("recurso_track_status_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _TRACK_STATUS_NOTIFICATION_TYPE,
            "glosa_id": input_data.glosa_id,
            "protocolo_recurso": input_data.protocolo_recurso,
        }
        # Posture: topic-default best-effort BY DESIGN (DL-0038, t2-notify-integrity keep) — this
        # publish re-fires EVERY P5D `ICE_AguardarResposta` loop iteration, so a swallowed broker
        # failure self-heals on the next tick; forcing fail-closed would incident an advisory
        # re-tick. Deliberately NOT best_effort=False — pinned by test_recurso.py.
        # GAP-SC-04-a partition key — see the shared chain in `partition_key.py`.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key)
        return result

    return handler


def reconcile_payment(
    numero_lote_tiss: str,
    numero_guia_tiss: str,
    status: str = "deferido",
) -> dict[str, Any]:
    """Reconcile re-payment to the prestador on deferimento/parcial deferimento.

    No adverse effect — purely clerical reconciliation. TASY write DROP: we consume, never write
    to Tasy (ADR-0013). Mirrors `contas.py:441-463`'s `reconcile_payment` EXACTLY (same shape,
    same log keys, same return dict) — only the default `status` vocabulary swapped for
    recurso's own `deferido`/`parcialmente_deferido` (contas's is `REENVIAR`). Serves BOTH
    `ST_ReconcilePaymentDeferido` and `ST_ReconcilePaymentParcial` (same topic, same worker,
    neither BPMN task carries a distinguishing input variable — `status` is read from the
    already-resolved `resposta_operadora` process variable by the entry function below).
    """
    logger.info(
        "recurso.reconcile_payment",
        numero_lote_tiss=numero_lote_tiss,
        numero_guia_tiss=numero_guia_tiss,
        status=status,
    )

    return {
        "reconciled": True,
        "status": status,
        "numero_lote_tiss": numero_lote_tiss,
        "numero_guia_tiss": numero_guia_tiss,
    }


def reconcile_payment_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.reconcile_payment` -> `reconcile_payment`."""
    del kafka  # unused — mirrors contas.py's reconcile_payment_entry; no domain event from this worker
    numero_lote_tiss = variables.get("numero_lote_tiss", "")
    numero_guia_tiss = variables.get("numero_guia_tiss", "")
    status = variables.get("resposta_operadora", "deferido")
    return reconcile_payment(numero_lote_tiss, numero_guia_tiss, status)


def register_recurso_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-RECURSO-001 workers on `harness` — 13 `operadora.recurso.*` topics.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial` to
    every `FunctionWorker` entry function below; none of THOSE calls `kafka.publish` — see
    `ans_submit.register_ans_submit_workers`'s docstring for the same documented sync/async-
    boundary rationale. `dmn` (ADR-0028 §1 seam) is threaded into `assess_eligibility_entry`
    (`recurso_admissibility` + `recurso_eligibility`, T1.5 cutover).

    T3.1 P2b (Finding 2, RESOLVED — built, pending live-proof flip): the 4 raw-handler
    registrations at the bottom (`notify_sla_risk`/`escalate_ans_timeout`/`submit_appeal`/
    `track_status`) DO call `kafka.publish` (module-level rationale above their factories) —
    `harness.register()`, not `register_worker()` (mirrors `lgpd`'s `request_additional_proof` /
    `events.publish`). `reconcile_payment` closes the 5th zero-worker gap as a plain
    `FunctionWorker` (no kafka, mirrors `contas.py` exactly).
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
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.reconcile_payment", functools.partial(reconcile_payment_entry, kafka=kafka)
        )
    )
    # Finding 2 (P2b): raw handlers — need the async Kafka seam (module-level rationale above).
    harness.register(_NOTIFY_SLA_RISK_TOPIC, make_notify_sla_risk_handler(kafka))
    harness.register(_ESCALATE_ANS_TIMEOUT_TOPIC, make_escalate_ans_timeout_handler(kafka))
    harness.register(_SUBMIT_APPEAL_TOPIC, make_submit_appeal_handler(kafka))
    harness.register(_TRACK_STATUS_TOPIC, make_track_status_handler(kafka))
