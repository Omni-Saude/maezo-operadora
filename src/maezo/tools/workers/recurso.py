"""SP-OP-RECURSO-001 Worker — Analise de Recurso de Glosa (resposta ao recurso).

External tasks for the PAYER's judgement of a glosa appeal filed by the prestador.
Negativa-like L0 hard: registrar_indeferimento is GUARDED by
ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN — indeferring the appeal (maintaining the
glosa against the prestador) only materializes after human decision.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from maezo.agents.andre.keys import key_segment
from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from maezo.tools.workers.base import FunctionWorker, non_blank, pick_fields
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, WorkerBpmnError, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)

# Internal-notification channel (mirrors lgpd.py's own `_NOTIFICATIONS_TOPIC` — a
# `type`-discriminated envelope on `operadora.notifications.internal`, NOT a BPMN-declared
# domain-event topic). Used by the raw-handler workers below whose test-spec-demanded
# observability (`notifications_of_type(...)`) needs the async Kafka seam a `FunctionWorker`'s
# sync boundary cannot reach (`WorkerBase`'s own docstring: "Async I/O is handled by the
# engine/message layer, not by the worker logic").
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

#: Process key of the SP-OP-PAGTO-001 handoff target — the payment order for a glosa the payer
#: REVERSED after a human deferimento. RECURSO never releases money; it emits the order and
#: PAGTO's own alcada ladder (plus the mandatory `UT_AnaliseAdmissibilidade`, invariant I-PAGTO-1)
#: governs the release.
PAGTO_PROCESS_KEY = "SP-OP-PAGTO-001"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class RecursoIndeferimentoNotHumanError(PermissionError):
    """Raised when registrar_indeferimento is called without human authorization.

    Guard ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN — the worker MUST refuse to register an
    indeferimento (maintain the glosa against the prestador), a deferimento parcial (maintain
    PART of it) or an inadmissibilidade unless ONE of two human channels was satisfied with all
    its required fields:
    - analista channel (`ST_RegistrarIndeferimento` / `ST_RegistrarIndeferimentoParcial` /
      `ST_RegistrarInadmissivel`): `decisao_recurso in {INDEFERIR, DEFERIR_PARCIAL}` + `analista_id`
    - auditor channel (`ST_RegistrarIndeferimentoAuditor` / `ST_RegistrarIndeferimentoParcial`,
      auditor merito): `decisao_auditor_recurso in {INDEFERIR, DEFERIR_PARCIAL}` + `auditor_id`
    Both channels ALSO require `fundamentacao_indeferimento`, `valor_glosa_mantido_brl` and
    `referencia_contratual`; `DEFERIR_PARCIAL` additionally requires `valor_deferido_brl` and a
    sum that closes against `valor_glosado_brl` (integer cents, exact — OQ-R2).

    Subclass of `PermissionError` so `harness._handle` classifies it as an AUDITED REFUSAL and
    opens an incident (ADR-0030 §4; `harness.is_guard_refusal_code` recognises the code by its
    `_NOT_HUMAN` suffix).
    """

    def __init__(self, missing_fields: list[str] | None = None, channel: str = "") -> None:
        self.missing_fields = missing_fields or []
        self.channel = channel
        msg = "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN: indeferimento requires human decision"
        if self.channel:
            msg += f" (canal tentado: {self.channel})"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


ERR_RECURSO_HANDOFF_PAGAMENTO_INVALIDO = "ERR_RECURSO_HANDOFF_PAGAMENTO_INVALIDO"


class RecursoHandoffPagamentoInvalidoError(ValueError):
    """Raised when the RECURSO->PAGTO handoff has no usable identity/value to emit an order.

    Fail-closed: a handoff without the business-key anchors (`tenant_id`/`numero_guia_tiss`/
    `glosa_id`), without a positive `valor_deferido_brl`, or with a blank `data_vencimento`
    cannot mint a deterministic PAGTO-001 order — refuse rather than start a payment order under
    a degenerate key or a defaulted amount (`_parse_valor_monetario` NEVER defaults to 0).
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_RECURSO_HANDOFF_PAGAMENTO_INVALIDO}: {detail}"
            if detail
            else ERR_RECURSO_HANDOFF_PAGAMENTO_INVALIDO
        )


_ERR_RECURSO_INVALID_GLOSA = "ERR_RECURSO_INVALID_GLOSA"

# ADR-0030 Tier-0/Tier-2 (G2-val, origin/consistency validation — fail-safe, NON-adverse: the
# worker never decides merito, it only signals that `glosa_id` arrived empty/absent at the
# origin). NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated (ADR-0030 census + §4's `is_te_gated`
# predicate: matched only by the `_NOT_HUMAN` suffix or `ERR_AUTH_DENIAL_INCOMPLETE`) — enabled
# directly in the runtime allowlist without an audited-refusal co-requisite. Consumption-covered
# (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"): `operadora.recurso.validate_recurso`,
# `operadora.recurso.request_documents` and `operadora.recurso.analyze_request` are consumed
# ONLY by SP-OP-RECURSO-001, which declares this errorCode on ALL THREE topics' boundary catches
# (`BE_GlosaInvalidaValidacao` / `BE_GlosaInvalidaDocs` / `BE_GlosaInvalidaDossie` ->
# `End_RecursoGlosaInvalidaOrigem`). Mirrors `auth.AUTH_BPMN_ERROR_ALLOWLIST` /
# `lgpd.LGPD_BPMN_ERROR_ALLOWLIST` — unioned into `worker_runtime/service.py`'s
# `_GATE_PROVEN_BPMN_ERROR_CODES` (SHARED FILE — see PR/report).
RECURSO_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({_ERR_RECURSO_INVALID_GLOSA})


def _require_glosa_id(glosa_id: str) -> None:
    """GAP-RECURSO-3 guard: glosa_id ausente/vazio -> `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)`.

    Fires BEFORE any downstream call (`validate_recurso`/`notify_prestador`/`analyze_merits`) —
    mirrors cancel.py's `WorkerBpmnError` raising pattern (`confirm_maintained_decision`). The
    BPMN's boundary catches (`BE_GlosaInvalidaValidacao` on `ST_ValidarRecurso`,
    `BE_GlosaInvalidaDocs` on `ST_SolicitarDocumentos`, `BE_GlosaInvalidaDossie` on
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
            "boundary catch (BE_GlosaInvalidaValidacao/BE_GlosaInvalidaDocs/BE_GlosaInvalidaDossie) "
            "roteia a End_RecursoGlosaInvalidaOrigem (GAP-RECURSO-3, terminal NEUTRO nao-adverso).",
        )


def _recurso_business_key(tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """`RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` (contract SP-OP-RECURSO-001.md
    "Business key (idempotencia)") — one recurso per glosa per guia TISS. BYTE-IDENTICAL scheme to
    `platform/notification_bridge._recurso_business_key` and `agents/marina/graph._business_key`,
    so every entry path (TISS intake bridge, Marina, manual operation) converges on the SAME
    instance. Used here only to mint DETERMINISTIC protocolos from the instance's own identity."""
    return f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"


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
    #: Data que o prestador ALEGA no pleito como a de sua ciencia — registro do que ele
    #: declarou, JAMAIS base do prazo da operadora (essa e `data_recebimento_recurso_iso`).
    #: Renomeada no reparo do gate do PR-3 (M1): o nome antigo era o relogio do RECORRENTE e a
    #: cerca de perspectiva (familia `ancora-kpi`) o acusa. Opcional.
    data_ciencia_alegada_prestador: str = ""
    #: Data em que a OPERADORA recebeu o recurso — ancora UNICA do SLA e do teto absoluto.
    #: Normalizada/defaultada fail-safe por `validate_recurso`.
    data_recebimento_recurso_iso: str = ""
    #: Vencimento da conta de origem — herdada do envelope de intake; exigida pelo handoff de
    #: pagamento da glosa revertida (DRAFT/verify OQ-3: de onde ela vem numa reversao de glosa).
    data_vencimento: str = ""
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False


@dataclass
class RecursoValidationResult:
    """Output of validate_recurso.

    `data_recebimento_recurso_iso` is ECHOED BACK as a process variable on purpose: it is the
    single anchor `recurso_sla`'s FEEL reads for `prazo_max_absoluto_iso`, and the DMN no longer
    carries a fail-safe branch to another party's date (GAP-RECURSO-1). The normalisation
    therefore has to be durable in process scope, not local to this call.
    """

    valid: bool = False
    glosa_existe: bool = False
    dentro_prazo_recurso: bool = False
    documentacao_recurso_completa: bool = False
    data_recebimento_recurso_iso: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class RecursoAdmissibilityResult:
    """Output of assess_eligibility — DMN recurso_admissibility + recurso_eligibility."""

    roteamento: str = "ANALISE_HUMANA"
    motivo: str = ""
    grupo_revisor: str = "analista-recurso-glosa"
    segue_merito: bool = False


@dataclass
class RecursoIndeferimentoInput:
    """Input for registrar_indeferimento — the gated adverse effect.

    Two human channels route to the SAME `operadora.recurso.registrar_indeferimento` topic
    (BPMN `ST_RegistrarIndeferimento` / `ST_RegistrarIndeferimentoParcial` /
    `ST_RegistrarIndeferimentoAuditor` / `ST_RegistrarInadmissivel`): the analista's
    `decisao_recurso in {INDEFERIR, DEFERIR_PARCIAL}` + `analista_id`, or the auditor's merito
    `decisao_auditor_recurso in {INDEFERIR, DEFERIR_PARCIAL}` + `auditor_id`.

    The three business-key anchors are carried so the protocolo can be minted DETERMINISTICALLY
    from the instance's own identity (a re-delivered task mints the IDENTICAL protocolo).
    """

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    decisao_recurso: str = ""
    fundamentacao_indeferimento: str = ""
    valor_glosa_mantido_brl: float = 0.0
    valor_deferido_brl: float = 0.0
    valor_glosado_brl: float = 0.0
    referencia_contratual: str = ""
    analista_id: str = ""
    decisao_auditor_recurso: str = ""
    auditor_id: str = ""


@dataclass
class RecursoIndeferimentoResult:
    """Output of registrar_indeferimento."""

    registered: bool = False
    protocolo: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def _normalize_data_recebimento(raw: Any) -> tuple[str, bool]:
    """Normalise the payer's own receipt date to ISO `YYYY-MM-DD`; report whether it defaulted.

    Mirrors `contas.identify_glosa`'s treatment of `data_recebimento_lote` (GAP-CONTAS-4 /
    ENGINE-16004): the DMN's FEEL expression must never see `null`/`""`, because `recurso_sla`
    computes the ABSOLUTE ceiling from this single anchor and no longer falls back to the
    appellant's own date. When the field arrives absent/blank/malformed the worker defaults
    FAIL-SAFE to TODAY/UTC and the caller LOGS A WARNING — it never invents a plausible past
    date, and timeliness stays a separate fact (`dentro_prazo_recurso`), which routes a doubtful
    appeal to ANALISE_HUMANA through `recurso_admissibility` rather than through the clock.
    """
    text = str(raw or "").strip()
    if text:
        # Accept a full ISO datetime ("2026-05-20T00:00:00", "2026-05-20T00:00:00Z") by keeping
        # only the calendar date the DMN concatenates "T00:00:00" onto.
        candidate = text.split("T", 1)[0]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")  # noqa: DTZ007 — calendar date, not an instant
        except ValueError:
            pass
        else:
            return candidate, False
    return datetime.now(UTC).strftime("%Y-%m-%d"), True


def validate_recurso(input_data: RecursoInput) -> RecursoValidationResult:
    """Validate the received recurso — normalise the SLA anchor and pre-resolve the facts.

    INTAKE task (`ST_ValidarRecurso`): the FIRST RECURSO touchpoint on EVERY path, before
    `BRT_Admissibilidade`. It (a) normalises/defaults `data_recebimento_recurso_iso` — the date
    the OPERADORA received the appeal, and the single anchor of both the analysis SLA and the
    absolute ceiling — and (b) pre-resolves `glosa_existe`, `dentro_prazo_recurso` and
    `documentacao_recurso_completa` for the DMN. The arithmetic of prazo lives in the worker; the
    decision to INDEFERIR is always human.
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

    data_recebimento, defaulted = _normalize_data_recebimento(input_data.data_recebimento_recurso_iso)
    if defaulted:
        logger.warning(
            "recurso.validate_recurso.data_recebimento_defaulted",
            tenant_id=input_data.tenant_id,
            glosa_id=input_data.glosa_id,
            recebido=str(input_data.data_recebimento_recurso_iso or ""),
            defaulted_to=data_recebimento,
            motivo=(
                "ancora do SLA/teto ausente ou invalida — default fail-safe HOJE/UTC; a "
                "tempestividade continua aferida por dentro_prazo_recurso (ANALISE_HUMANA quando "
                "falsa), nunca pelo relogio"
            ),
        )

    dentro_prazo = input_data.dentro_prazo_recurso
    if not dentro_prazo:
        errors.append("fora do prazo recursal")

    docs_completa = input_data.documentacao_recurso_completa

    result = RecursoValidationResult(
        valid=glosa_existe and len(errors) == 0,
        glosa_existe=glosa_existe,
        dentro_prazo_recurso=dentro_prazo,
        documentacao_recurso_completa=docs_completa,
        data_recebimento_recurso_iso=data_recebimento,
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
    if/elif ladder + a hand-computed `grupo_revisor`/`segue_merito` that did NOT correspond to
    either table's real inputs/outputs:

    1. `recurso_admissibility` (glosa_existe, dentro_prazo_recurso,
       documentacao_recurso_completa) -> `roteamento` in {ANALISE_HUMANA,
       PENDENTE_DOCUMENTACAO, SEGUE_ANALISE} + `motivo`.
    2. ONLY when (1) returns `SEGUE_ANALISE` (non-terminal — admissible, proceed), chain into
       `recurso_eligibility` (glosa_type, glosa_reason_code, valor_glosado_brl) ->
       `grupo_revisor` + a `SEGUE_MERITO`/`ANALISE_HUMANA` signal folded into `segue_merito`. The
       old Python instead derived `grupo_revisor` from a hand-coded tecnica/clinica check and set
       the flag to `roteamento == "SEGUE_ANALISE"` — a tautology, never actually consulting the
       real eligibility table's own output domain or its `glosa_reason_code`/`valor_glosado_brl`
       inputs.

    NO automatic indeferimento — inadmissibility routes to ANALISE_HUMANA either way. Both
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
    segue_merito = False
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
        segue_merito = str(elig_row.get("roteamento", "")) == "SEGUE_MERITO"

    result = RecursoAdmissibilityResult(
        roteamento=roteamento,
        motivo=motivo,
        grupo_revisor=grupo_revisor,
        segue_merito=segue_merito,
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


def _parse_valor_monetario(value: Any) -> float | None:
    """Coerce a monetary process variable to a float for the indeferimento/handoff guards.

    Root cause: the engine seeds these fields as a Camunda ``String`` (e.g. ``"150.00"`` —
    EngineRest._to_camunda_vars has no float branch, so it falls through to the String
    catch-all); harness ``_from_camunda_var`` decodes it back to a Python ``str`` and
    ``pick_fields`` performs NO coercion. A raw ``<= 0`` comparison then raises
    ``TypeError: '<=' not supported between instances of 'str' and 'int'``.

    Returns the parsed amount as a ``float`` — matching the dataclass's declared ``float``
    typing and the canonical ``contas.py`` money idiom (``float(brl)``, dot-decimal, the
    donor/engine wire convention). Returns ``None`` for a missing/blank/non-numeric value so
    the caller FAILS CLOSED (treats it as a missing required field ->
    ``RecursoIndeferimentoNotHumanError`` / ``RecursoHandoffPagamentoInvalidoError``). NEVER
    silently defaults to ``0`` — a wrong monetary decision on a glosa is a financial defect.
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


def _to_cents(brl: float) -> int:
    """BRL (double) -> integer cents. Pure conversion — no table, no threshold, no rounding rule.

    ``round`` (not truncation) so ``1.15`` becomes ``115`` and not ``114``: the binary
    representation of a decimal BRL amount is systematically a hair below the exact value, and
    truncating it would shave a cent off a real payment. The DMN keeps every threshold
    (``recurso_sla`` alto-valor ``> 50000.0``) — this function never compares, only converts.
    """
    return int(round(brl * 100))


def registrar_indeferimento(input_data: RecursoIndeferimentoInput) -> RecursoIndeferimentoResult:
    """Register the payer's indeferimento / deferimento parcial / inadmissibilidade — GUARDED.

    ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN: MUST refuse unless ONE of two human channels is
    attempted —
    - analista channel: `decisao_recurso in {INDEFERIR, DEFERIR_PARCIAL}` (requires `analista_id`)
    - auditor channel: `decisao_auditor_recurso in {INDEFERIR, DEFERIR_PARCIAL}` (requires
      `auditor_id`)
    Both channels ALSO require `fundamentacao_indeferimento`, `valor_glosa_mantido_brl` (> 0) and
    `referencia_contratual`. Refuses with `RecursoIndeferimentoNotHumanError` ONLY if NEITHER
    channel's decision field matches; otherwise validates the attempted channel's own required
    fields (channel-aware missing-fields message).

    DEFERIR_PARCIAL additionally requires `valor_deferido_brl` (>= 0, the reverted share that
    feeds the SP-OP-PAGTO-001 order) and a SUM that closes exactly in integer cents:
    ``valor_deferido + valor_glosa_mantido == valor_glosado``. That is pure arithmetic, not a
    business rule — comparing `float`s for equality would fail spuriously on exact amounts
    (``0.1 + 0.2 != 0.3``), so the comparison is in cents. If the operation uses rounding or a
    tolerance, that is an SME decision (OQ-R2, `docs/review-queue.md`) and this guard changes;
    until then it REFUSES a sum that does not close rather than rounding on its own authority.
    """
    logger.info(
        "recurso.registrar_indeferimento.start",
        decisao_recurso=input_data.decisao_recurso,
        decisao_auditor_recurso=input_data.decisao_auditor_recurso,
        analista_id=input_data.analista_id,
        auditor_id=input_data.auditor_id,
    )

    adversas = {"INDEFERIR", "DEFERIR_PARCIAL"}
    is_analista = input_data.decisao_recurso in adversas
    is_auditor = input_data.decisao_auditor_recurso in adversas

    if not is_analista and not is_auditor:
        raise RecursoIndeferimentoNotHumanError(
            missing_fields=[
                "decisao_recurso not in {INDEFERIR, DEFERIR_PARCIAL} (canal analista)",
                "decisao_auditor_recurso not in {INDEFERIR, DEFERIR_PARCIAL} (canal auditor)",
            ],
            channel="nenhum",
        )

    channel = "analista" if is_analista else "auditor"
    decisao = input_data.decisao_recurso if is_analista else input_data.decisao_auditor_recurso
    missing: list[str] = []

    if is_analista and not input_data.analista_id.strip():
        missing.append("analista_id")
    if is_auditor and not input_data.auditor_id.strip():
        missing.append("auditor_id")
    if not input_data.fundamentacao_indeferimento.strip():
        missing.append("fundamentacao_indeferimento")
    # Monetary fields arrive from Camunda as Strings ("150.00"); parse fail-closed before
    # comparing. None (missing/blank/non-numeric) or <= 0 => required field absent.
    valor_mantido = _parse_valor_monetario(input_data.valor_glosa_mantido_brl)
    if valor_mantido is None or valor_mantido <= 0:
        missing.append("valor_glosa_mantido_brl")
    if not input_data.referencia_contratual.strip():
        missing.append("referencia_contratual")

    valor_deferido = _parse_valor_monetario(input_data.valor_deferido_brl)
    if decisao == "DEFERIR_PARCIAL" and (valor_deferido is None or valor_deferido < 0):
        missing.append("valor_deferido_brl")

    if missing:
        raise RecursoIndeferimentoNotHumanError(missing_fields=missing, channel=channel)

    if decisao == "DEFERIR_PARCIAL":
        valor_glosado = _parse_valor_monetario(input_data.valor_glosado_brl)
        if valor_glosado is None:
            raise RecursoIndeferimentoNotHumanError(missing_fields=["valor_glosado_brl"], channel=channel)
        # mypy: both are non-None here (guarded above).
        assert valor_deferido is not None and valor_mantido is not None  # noqa: S101
        if _to_cents(valor_deferido) + _to_cents(valor_mantido) != _to_cents(valor_glosado):
            raise RecursoIndeferimentoNotHumanError(
                missing_fields=[
                    "soma nao fecha: valor_deferido_brl + valor_glosa_mantido_brl != "
                    "valor_glosado_brl (centavos-inteiros, igualdade exata — OQ-R2)"
                ],
                channel=channel,
            )

    protocolo = "RECIND-" + _recurso_business_key(
        input_data.tenant_id, input_data.numero_guia_tiss, input_data.glosa_id
    )

    logger.info(
        "recurso.registrar_indeferimento.complete",
        protocolo=protocolo,
        decisao=decisao,
        channel=channel,
        analista_id=input_data.analista_id,
        auditor_id=input_data.auditor_id,
    )
    return RecursoIndeferimentoResult(registered=True, protocolo=protocolo)


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
#   validate_recurso        -> operadora.recurso.validate_recurso   (ST_ValidarRecurso — intake)
#   notify_prestador        -> operadora.recurso.request_documents  (spec match: default message_type
#                              IS "pendencia_documentacao" == "Abrir pendencia de documentacao ao prestador")
#   analyze_merits          -> operadora.recurso.analyze_request    (spec match: "(agente Marina)" == this
#                              function's own docstring "delegates to Marina (LLM agent)")
#   registrar_indeferimento -> operadora.recurso.registrar_indeferimento (exact spec match, GUARDED)
#   comunicar_resposta      -> operadora.recurso.comunicar_resposta (the 5 ST_Comunicar* tasks)
#   handoff_pagamento       -> operadora.recurso.handoff_pagamento  (ST_HandoffPagamento*)
#   notify_sla_risk         -> operadora.recurso.notify_sla_risk
#   escalate_ans_timeout    -> operadora.recurso.escalate_ans_timeout
# assess_eligibility/prepare_dossier/escalate_to_junta have no distinct spec topic today (the two
# DMNs are evaluated engine-side by the BRTs; escalate_to_junta targets medico-auditor — a
# DIFFERENT audience than spec's escalate_ans_timeout, not force-mapped) — registered under
# function-derived topics for registry completeness. publish_completed folds into the generic
# events.publish task per BPMN — function-derived topic.
# ---------------------------------------------------------------------------


def validate_recurso_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.validate_recurso` -> `validate_recurso`.

    GAP-RECURSO-3: guards `glosa_id` BEFORE validating — raises
    `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` (`_require_glosa_id`) when absent/empty, so an
    origin-invalid appeal never reaches `BRT_Admissibilidade` (`BE_GlosaInvalidaValidacao`
    boundary catch). This is the THIRD site of the same code and the FIRST on every path.
    """
    del kafka  # unused — validate_recurso emits no domain event
    _require_glosa_id(str(variables.get("glosa_id", "")))
    input_data = RecursoInput(**pick_fields(variables, RecursoInput))
    result = validate_recurso(input_data)
    out = dataclasses.asdict(result)
    # m8: `ST_ValidarRecurso` is the FIRST service task on EVERY path, so this is the first
    # completion payload the engine sees for this process. `errors` is a `list[str]`; a list
    # becomes a JSON/object process variable in CIB Seven and nothing in the BPMN reads it.
    # Flatten it to a scalar (empty string when clean) — observability without an untyped
    # object variable, and without silently dropping the diagnosis.
    out["errors_validacao"] = "; ".join(out.pop("errors"))
    return out


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


def registrar_indeferimento_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.registrar_indeferimento` ->
    `registrar_indeferimento` (GUARDED).

    Raises `RecursoIndeferimentoNotHumanError` (fail-closed,
    ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN) unless the analista channel (`decisao_recurso in
    {INDEFERIR, DEFERIR_PARCIAL}` + `analista_id`) or the auditor channel
    (`decisao_auditor_recurso in {INDEFERIR, DEFERIR_PARCIAL}` + `auditor_id`) is satisfied with
    its required fields — only the dict<->dataclass marshalling is a boundary concern here.
    """
    del kafka  # unused — registrar_indeferimento emits no domain event itself
    input_data = RecursoIndeferimentoInput(**pick_fields(variables, RecursoIndeferimentoInput))
    result = registrar_indeferimento(input_data)
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
# Raw `harness.register()` handlers (like `events.make_publish_event_handler` /
# `lgpd.make_request_additional_proof_handler`), NOT `FunctionWorker`-wrapped: each needs the
# async Kafka seam a sync `FunctionWorker.execute` boundary cannot reach (`WorkerBase`'s own
# docstring: "Async I/O is handled by the engine/message layer, not by the worker logic") to
# satisfy the test-spec's `notifications_of_type(...)`/`has_event(...)` observability — and
# `comunicar_resposta` ALSO needs `task.business_key` for deterministic protocolo minting.
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
    observability channel the test-spec demands (`test_timer_alerta_sla_nao_interruptivo`) — see
    the module-level rationale above.

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
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
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
    """Escalate the P30D response-ceiling breach to human coordenacao-recurso.

    "ANS" in the topic name is HISTORICAL — this is NOT an ANS-gateway integration: the worker
    NEVER imports/touches `ans_gateway.py`'s `AnsGatewayTransport` triple (the real
    external-protocol seam belongs to SP-OP-ANS-SUBMIT-001, a different process entirely). The
    ceiling it escalates is the PAYER's own response deadline (prazo contratual — DRAFT/verify),
    not an ANS filing deadline. Common target of ALL THREE P30D boundary timers
    (`BT_PrazoMaxRecurso`/`BT_PrazoMaxCoord`/`BT_PrazoMaxAuditor`, GAP-RECURSO-1 — same absolute
    instant regardless of who held the recurso). NUNCA auto-desfecho adverso — routes
    unconditionally to `UT_EscalonamentoPrazo` (human decides the destino).
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
    async Kafka seam -> raw handler (same rationale as `make_notify_sla_risk_handler`). Reachable
    assertions: `test_prazo_max_recurso_escala_humano`,
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
        await kafka.publish(event_topic, payload, key=task.business_key or None)
        return result

    return handler


_COMUNICAR_RESPOSTA_TOPIC = "operadora.recurso.comunicar_resposta"
_COMUNICAR_RESPOSTA_NOTIFICATION_TYPE = "recurso.comunicar_resposta"
_COMUNICAR_RESPOSTA_PROTOCOLO_PREFIX = "RECRESP-"

#: The four communication kinds the five terminal `ST_Comunicar*` tasks declare via
#: `camunda:inputParameter tipo_comunicacao` (both indeferimento tasks — analista and auditor —
#: emit the same kind of answer to the prestador, so there are four kinds and five tasks).
COMUNICACAO_TIPOS: frozenset[str] = frozenset(
    {"deferimento", "deferimento_parcial", "indeferimento", "inadmissibilidade"}
)


@dataclass
class ComunicarRespostaInput:
    """Input for `comunicar_resposta` — the payer's answer to the prestador's appeal."""

    tenant_id: str = ""
    numero_guia_tiss: str = ""
    glosa_id: str = ""
    prestador_id: str = ""
    tipo_comunicacao: str = ""
    valor_deferido_brl: float = 0.0
    valor_glosa_mantido_brl: float = 0.0


def _mint_protocolo_resposta(business_key: str, tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """Deterministically derive `protocolo_resposta_recurso` from business identity.

    ADR-0030/T-H determinism: NO `time.time_ns`/`uuid`/`random` —
    `tests/unit/tools/workers/test_worker_handler_purity.py`'s non-determinism baseline fence
    tracks this module, and the ONE wall-clock mint it used to carry
    (the removed appellant-side worker's `RECDESIST-{sha256(time.time_ns())}`) is gone with the
    rewrite.
    Mirrors `LabeledMockAnsGatewayTransport.submit`'s `MOCK-ANS-NAO-VINCULATIVO-{business_key}`
    idiom (`ans_gateway.py`) — the SAME appeal (same business key) always mints the IDENTICAL
    protocol, which is also what makes a re-delivered task republish the IDENTICAL fact instead
    of minting a second protocol for one answer.
    """
    key = business_key.strip() or _recurso_business_key(tenant_id, numero_guia_tiss, glosa_id)
    return f"{_COMUNICAR_RESPOSTA_PROTOCOLO_PREFIX}{key}"


def comunicar_resposta(input_data: ComunicarRespostaInput, *, business_key: str = "") -> dict[str, Any]:
    """Emit the payer's answer to the prestador's appeal — `ST_Comunicar*` (5 terminals).

    Serves all four outcome kinds (`COMUNICACAO_TIPOS`). It carries NO decision of its own: the
    decision was made by a human in a User Task and, for the three adverse kinds, already
    materialised by the gated `registrar_indeferimento`. TISS component "Resposta ao Recurso de
    Glosa" is a PROPOSED name, not confirmed against the current TISS standard — DRAFT/verify
    (OQ-1 of ADR-0040); no real TISS call is made here (TASY write DROP, ADR-0013), the protocolo
    is synthetic and deterministic.
    """
    protocolo = _mint_protocolo_resposta(
        business_key, input_data.tenant_id, input_data.numero_guia_tiss, input_data.glosa_id
    )
    logger.info(
        "recurso.comunicar_resposta",
        glosa_id=input_data.glosa_id,
        prestador_id=input_data.prestador_id,
        tipo_comunicacao=input_data.tipo_comunicacao,
        protocolo_resposta_recurso=protocolo,
    )
    return {
        "comunicado": True,
        "protocolo_resposta_recurso": protocolo,
        "tipo_comunicacao": input_data.tipo_comunicacao,
        "glosa_id": input_data.glosa_id,
    }


def make_comunicar_resposta_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.recurso.comunicar_resposta` (serves the 5 terminals).

    Needs `task.business_key` for deterministic protocolo minting AND the async Kafka seam for
    the `notifications_of_type("recurso.comunicar_resposta")` observability channel
    (`test_todos_os_fins_comunicam_o_prestador`) — the contract has no distinct Kafka
    domain-event topic for this worker beyond the internal notification (only
    `protocolo_resposta_recurso` as an output VARIABLE; the terminal domain event is the
    downstream `ST_Publish*`).

    Fail-closed publish posture (t2-notify-integrity item 1): this is a MAIN-path fact recording
    an act the prestador is entitled to receive (the payer's answer to his appeal, carrying the
    minted `protocolo_resposta_recurso`) — losing it silently is unacceptable, so
    `best_effort=False` forces the producer to PROPAGATE a broker failure. No BPMN error boundary
    is declared on any `ST_Comunicar*` -> RAW propagate to the harness retry/incident ladder
    (ADR-0030); the token is held at the task until the fact is actually published. Retry-safe:
    `_mint_protocolo_resposta` is deterministic by business key.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = ComunicarRespostaInput(**pick_fields(task.variables, ComunicarRespostaInput))
        result = comunicar_resposta(input_data, business_key=task.business_key)
        if kafka is None:
            logger.warning("recurso_comunicar_resposta_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _COMUNICAR_RESPOSTA_NOTIFICATION_TYPE,
            "glosa_id": input_data.glosa_id,
            "prestador_id": input_data.prestador_id,
            "tipo_comunicacao": input_data.tipo_comunicacao,
            "protocolo_resposta_recurso": result["protocolo_resposta_recurso"],
        }
        # best_effort=False — see factory docstring (no boundary declared -> raw propagate).
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


# ---------------------------------------------------------------------------
# RECURSO -> SP-OP-PAGTO-001 handoff (I-PAGTO-1)
# ---------------------------------------------------------------------------

#: The EXACT set of process variables the handoff seeds into SP-OP-PAGTO-001. Pinned as a
#: frozenset (and asserted by EQUALITY, never `issubset`, in
#: `test_handoff_pagamento_semeia_exatamente_o_conjunto_declarado`) because the invariant this
#: worker has to keep is a NEGATIVE one: the four admissibility booleans PAGTO resolves for
#: itself must NEVER appear here.
HANDOFF_PAGTO_SEEDED_KEYS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "ordem_pagamento_id",
        "numero_lote_tiss",
        "numero_guia_tiss",
        "glosa_id",
        "prestador_id",
        "tipo_pagamento",
        "valor_pagamento_cents",
        "moeda",
        "competencia",
        "data_vencimento",
        "conta_origem_ref",
        "instrumento_pagamento",
        "fonte_valor",
        "lastro_origem",
        "lastro_decisor_id",
    }
)

#: I-PAGTO-1 (ADR-0040): the four facts SP-OP-PAGTO-001 resolves INSIDE itself. A source process
#: supplies EVIDENCE of the lastro (`lastro_origem`/`lastro_decisor_id`), never the FACT. Seeding
#: `lastro_confirmado=true` here would put the order straight into the `DENTRO_TETO_L2` clerical
#: lane (`ST_ReleaseLowValue`, no User Task) and skip the segregation of duties: whoever judged
#: the appeal would also have confirmed the lastro of the payment order it produced.
HANDOFF_PAGTO_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"lastro_confirmado", "dados_pagamento_validos", "duplicidade_suspeita", "dentro_teto_l2"}
)

#: Provenance value seeded into `lastro_origem` (I-PAGTO-1's enum) for every order this worker
#: emits — every one of them is downstream of a human DEFERIR/DEFERIR_PARCIAL.
LASTRO_ORIGEM_RECURSO = "recurso_deferimento_humano"

TIPO_PAGAMENTO_GLOSA_REVERTIDA = "glosa_revertida"

#: The ONLY declared source of the amount this handoff can carry (m1). The BPMN pins
#: `fonte_valor=deferido` by `camunda:inputParameter` on BOTH handoff tasks
#: (`ST_HandoffPagamentoRecurso`, `ST_HandoffPagamentoParcial`) and the worker ALWAYS derives
#: `valor_pagamento_cents` from `valor_deferido_brl` — so any other value would put a provenance
#: claim (`AgentDecisionProvenance.decision_basis["fonte_valor"]`) in CONTRADICTION with the real
#: source. Refused rather than echoed: an unknown source is a disclosed gap, never a plausible label.
FONTES_VALOR_PERMITIDAS: frozenset[str] = frozenset({"deferido"})


def _pagto_business_key(tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """`PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` — ONE order per reverted glosa.

    NORMALISED THROUGH `agents.andre.keys.key_segment` (gate finding M2 — the SAME defect that
    module's header documents for the SAME family: "a whitespace-padded `ordem_pagamento_id`
    produced `PAGTO-{t}- 123 ` against the other site's `PAGTO-{t}-123`"). Without it a
    re-delivered handoff whose `numero_guia_tiss` arrives with one extra space mints a DIFFERENT
    key, the STRICT dedup claim does not see the previous instance, and the same reverted glosa is
    paid TWICE. `non_blank` above rejects absent/empty/whitespace-only/`None` but ACCEPTS
    whitespace-PADDED, so refusing is not enough on its own — the segments must also be normalised.
    Position-preserving: an empty segment is impossible here because the caller refuses first.

    A THIRD documented shape of the `PAGTO-{tenant}-...` family, alongside the contract's
    `PAGTO-{tenant}-{ordem_pagamento_id}` and `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`
    (`docs/processes/contracts/SP-OP-PAGTO-001.md`): a reverted glosa has no pre-existing ordem
    id and is not lote-scoped — its identity IS the glosa. Deriving the key from the glosa
    identity is what makes a re-delivered handoff (or a second human deferimento on the same
    appeal) converge on the SAME order instead of paying twice; SP-OP-PAGTO-001 is the ONE STRICT
    start-dedup family (`mcp_cibseven.transport._START_DEDUP_POLICY`), so that convergence is
    enforced by a durable claim, not by hope.
    """
    return f"PAGTO-{key_segment(tenant_id)}-{key_segment(numero_guia_tiss)}-{key_segment(glosa_id)}"


def _ordem_pagamento_id(tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """Deterministic id of the payment order this handoff MINTS (there is no pre-existing one).

    Same determinism discipline as `contas._glosa_id`: a re-delivered external task must mint the
    IDENTICAL id, never a fresh identity for one decision. It is an internal identifier of the
    order RECURSO creates — not a claim about any external system's numbering.

    Composed through the SAME `key_segment` normalisation as `_pagto_business_key` (M2): the order
    id and the key it travels with must never disagree about what the identity of the glosa is.
    """
    return f"ORDEM-GLOSAREV-{key_segment(tenant_id)}-{key_segment(numero_guia_tiss)}-{key_segment(glosa_id)}"


def handoff_pagamento(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff: idempotently START SP-OP-PAGTO-001 for the glosa the payer REVERSED.

    NOT an adverse effect and NOT a release of money — SP-OP-PAGTO-001 owns the release, behind
    its own alcada ladder AND (invariant **I-PAGTO-1**) behind the mandatory human
    `UT_AnaliseAdmissibilidade`. Reached ONLY from `ST_ComunicarDeferimento` /
    `ST_ComunicarDeferimentoParcial`, i.e. always downstream of a human `DEFERIR` /
    `DEFERIR_PARCIAL`.

    **I-PAGTO-1, mechanically.** The seeded variable set is PINNED
    (`HANDOFF_PAGTO_SEEDED_KEYS`) and the four admissibility booleans
    (`HANDOFF_PAGTO_FORBIDDEN_KEYS`) are NEVER among them. Consequence, by construction:
    `pagto_admissibility` reads `lastro_confirmado` absent => `False` => row `r_sem_lastro` =>
    `ANALISE_HUMANA` => `UT_AnaliseAdmissibilidade` (`coordenacao-financeira`). What this buys is
    SEGREGATION OF DUTIES on top of HITL: the analista/auditor who granted the appeal did in fact
    confirm the lastro, and letting that fact ride into PAGTO would drop the order straight into
    the clerical `DENTRO_TETO_L2` lane (`ST_ReleaseLowValue`, no User Task at all). Instead the
    worker seeds EVIDENCE — `lastro_origem="recurso_deferimento_humano"` and `lastro_decisor_id`
    (the `analista_id` or `auditor_id`) — which feeds the human admissibility form.

    T-C2 FENCE (mirrors `contas.handoff_pagamento` / `inadimplencia.handoff_rescisao`): the chokepoint
    REQUIRES a durable `audit_sink` + `AgentDecisionProvenance` — the ADR-0007 start record is
    emitted exactly-once BEFORE any engine effect. SP-OP-PAGTO-001 is additionally the ONE STRICT
    dedup family, so `start_process_idempotent` refuses outright
    (`StartDedupGateUnavailableError`) unless the injected sink implements `emit_once_status`
    (`DedupReportingAuditSink`) and the transport implements `find_any_instance`
    (`HistoryQueryingTransport`). Fail-closed by design: a deferimento never becomes a duplicate
    payment.

    FAIL-CLOSED (never a silent no-op, never a defaulted amount):
      - missing engine/audit seam raises (transient) — an un-audited PAGTO-001 start is
        structurally impossible (ADR-0007 L0);
      - missing/blank/None business-key anchor (`tenant_id`/`numero_guia_tiss`/`glosa_id`) raises
        `RecursoHandoffPagamentoInvalidoError` (deterministic -> incident);
      - `valor_deferido_brl` absent/blank/non-numeric/`<= 0` raises — `_parse_valor_monetario`
        NEVER defaults to `0`, even though the User Task declares the field mandatory;
      - `fonte_valor` outside `FONTES_VALOR_PERMITIDAS` raises (m1) — the amount always comes
        from `valor_deferido_brl`, so an unknown source label would make the ADR-0007 provenance
        contradict the value it describes;
      - a blank `lastro_decisor_id` (neither `analista_id` nor `auditor_id`) raises (m2) — the
        order is downstream of a HUMAN decision, so the decisor is a required piece of evidence;
      - `data_vencimento` blank raises. It is `sim` in the PAGTO contract and RECURSO inherits it
        from the intake envelope; where it comes from for a reverted glosa (the original bill's
        due date, or a new term counted from the deferimento) is OQ-3 — DRAFT/verify with
        financas + juridico. The worker refuses rather than inventing one.

    `competencia`, `conta_origem_ref` and `instrumento_pagamento` are PAGTO-required inputs that
    RECURSO does not itself produce: they are ECHOED VERBATIM from process scope and left BLANK
    when the intake did not carry them — never filled with a plausible-looking value.
    `UT_AnaliseAdmissibilidade` is where a human resolves them, which is exactly the lane
    I-PAGTO-1 guarantees the order lands in.
    """
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    # NORMALISED (M2) with the same `key_segment` the composers use, so the identity the PAYLOAD
    # and the LOGS carry is byte-identical to the identity the STRICT dedup key is minted from —
    # a padded `numero_guia_tiss` must not travel to PAGTO alongside a key that stripped it.
    # `non_blank` alone is not enough on either side: it ACCEPTS whitespace-padding, and it
    # ACCEPTS `0` (whose `key_segment` is `""`), so the emptiness is re-checked after normalising.
    tenant_id = key_segment(variables.get("tenant_id"))
    numero_guia_tiss = key_segment(variables.get("numero_guia_tiss"))
    glosa_id = key_segment(variables.get("glosa_id"))
    if not (
        non_blank(variables.get("tenant_id"))
        and non_blank(variables.get("numero_guia_tiss"))
        and non_blank(variables.get("glosa_id"))
        and tenant_id
        and numero_guia_tiss
        and glosa_id
    ):
        logger.error(
            "recurso_handoff_pagamento_no_glosa_identity",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise RecursoHandoffPagamentoInvalidoError(
            "handoff_pagamento: tenant_id/numero_guia_tiss/glosa_id ausente, em branco ou None — "
            "nao ha ancora de business key para iniciar PAGTO-001 (recusado, nunca inicia com "
            "business key vazia/degenerada)"
        )

    valor_deferido = _parse_valor_monetario(variables.get("valor_deferido_brl"))
    if valor_deferido is None or valor_deferido <= 0:
        logger.error("recurso_handoff_pagamento_valor_invalido", glosa_id=glosa_id)
        raise RecursoHandoffPagamentoInvalidoError(
            "handoff_pagamento: valor_deferido_brl ausente, em branco, nao-numerico ou <= 0 "
            "(fonte_valor=deferido) — recusado; o valor NUNCA e defaultado para 0"
        )

    data_vencimento = str(variables.get("data_vencimento") or "").strip()
    if not data_vencimento:
        logger.error("recurso_handoff_pagamento_sem_vencimento", glosa_id=glosa_id)
        raise RecursoHandoffPagamentoInvalidoError(
            "handoff_pagamento: data_vencimento em branco — obrigatoria em SP-OP-PAGTO-001 e "
            "herdada do envelope de intake (OQ-3, DRAFT/verify); recusado, nunca defaultada"
        )

    if engine is None:
        logger.error("recurso_handoff_pagamento_engine_seam_not_wired", glosa_id=glosa_id)
        raise RuntimeError(
            "handoff_pagamento: engine seam (CibSevenTransport) not wired — cannot start "
            "PAGTO-001; failing closed to a retry/incident (never a silent no-op)"
        )
    if audit_sink is None:
        logger.error("recurso_handoff_pagamento_audit_sink_not_wired", glosa_id=glosa_id)
        raise RuntimeError(
            "handoff_pagamento: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 "
            "start record, so PAGTO-001 is NOT started (fail-closed, emit-before-effect); failing "
            "to a retry/incident (never a silent no-op, never an un-audited start)"
        )

    fonte_valor = str(variables.get("fonte_valor") or "deferido").strip()
    if fonte_valor not in FONTES_VALOR_PERMITIDAS:
        logger.error("recurso_handoff_pagamento_fonte_valor_desconhecida", glosa_id=glosa_id)
        raise RecursoHandoffPagamentoInvalidoError(
            f"handoff_pagamento: fonte_valor={fonte_valor!r} fora do dominio declarado "
            f"{sorted(FONTES_VALOR_PERMITIDAS)} — o valor vem SEMPRE de valor_deferido_brl, e "
            "declarar outra fonte poria a proveniencia ADR-0007 em contradicao com o valor "
            "(recusado, nunca ecoado)"
        )

    lastro_decisor_id = key_segment(variables.get("analista_id") or variables.get("auditor_id"))
    if not lastro_decisor_id:
        logger.error("recurso_handoff_pagamento_sem_decisor", glosa_id=glosa_id)
        raise RecursoHandoffPagamentoInvalidoError(
            "handoff_pagamento: nem analista_id nem auditor_id presentes — uma ordem de pagamento "
            "originada de decisao humana NAO pode sair sem o decisor identificado (ADR-0007; "
            "`lastro_decisor_id` e a EVIDENCIA que UT_AnaliseAdmissibilidade le). Recusado."
        )
    business_key = _pagto_business_key(tenant_id, numero_guia_tiss, glosa_id)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "ordem_pagamento_id": _ordem_pagamento_id(tenant_id, numero_guia_tiss, glosa_id),
        "numero_lote_tiss": str(variables.get("numero_lote_tiss", "")),
        "numero_guia_tiss": numero_guia_tiss,
        "glosa_id": glosa_id,
        "prestador_id": str(variables.get("prestador_id", "")),
        "tipo_pagamento": TIPO_PAGAMENTO_GLOSA_REVERTIDA,
        "valor_pagamento_cents": _to_cents(valor_deferido),
        "moeda": "BRL",
        "competencia": str(variables.get("competencia", "")),
        "data_vencimento": data_vencimento,
        "conta_origem_ref": str(variables.get("conta_origem_ref", "")),
        "instrumento_pagamento": str(variables.get("instrumento_pagamento", "")),
        # Declared source of the amount (M7) — validated above (m1), never inferred downstream.
        "fonte_valor": fonte_valor,
        # I-PAGTO-1 evidence, NOT the fact.
        "lastro_origem": LASTRO_ORIGEM_RECURSO,
        "lastro_decisor_id": lastro_decisor_id,
    }
    # Defensive, and the reason the set is pinned: an editor who adds a key must see this fail
    # rather than discover it in production as a skipped human admissibility review.
    if set(payload) != HANDOFF_PAGTO_SEEDED_KEYS:
        raise RecursoHandoffPagamentoInvalidoError(
            "handoff_pagamento: o conjunto semeado divergiu de HANDOFF_PAGTO_SEEDED_KEYS "
            f"(a mais: {sorted(set(payload) - HANDOFF_PAGTO_SEEDED_KEYS)}; "
            f"a menos: {sorted(HANDOFF_PAGTO_SEEDED_KEYS - set(payload))}) — I-PAGTO-1"
        )

    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "lastro_origem": LASTRO_ORIGEM_RECURSO,
            "tipo_pagamento": TIPO_PAGAMENTO_GLOSA_REVERTIDA,
            "fonte_valor": payload["fonte_valor"],
        },
        model_id=None,
        prompt_version=None,
    )
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=PAGTO_PROCESS_KEY,
            business_key=business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )
    logger.info(
        "recurso.handoff_pagamento",
        glosa_id=glosa_id,
        numero_guia_tiss=numero_guia_tiss,
        pagto_business_key=business_key,
        pagto_instance_id=instance.instance_id,
        pagto_already_existed=instance.already_existed,
    )
    return {
        "handoff": PAGTO_PROCESS_KEY,
        "handoff_executado": True,
        "processo_destino": PAGTO_PROCESS_KEY,
        "glosa_id": glosa_id,
        "numero_guia_tiss": numero_guia_tiss,
        "tipo_pagamento": TIPO_PAGAMENTO_GLOSA_REVERTIDA,
        "valor_pagamento_cents": payload["valor_pagamento_cents"],
        "lastro_origem": LASTRO_ORIGEM_RECURSO,
        "lastro_decisor_id": lastro_decisor_id,
        "pagto_business_key": business_key,
        "pagto_instance_id": instance.instance_id,
        "pagto_already_existed": instance.already_existed,
    }


def handoff_pagamento_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.recurso.handoff_pagamento` -> `handoff_pagamento`.

    Threads the fenced-start seams (`engine`/`audit_sink`) — the handoff REALLY starts
    SP-OP-PAGTO-001 through the ADR-0007 chokepoint.
    """
    del kafka  # unused — handoff_pagamento starts a process, it does not publish a Kafka event
    return handoff_pagamento(variables, engine=engine, audit_sink=audit_sink)


def register_recurso_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-RECURSO-001 workers on `harness` — 12 `operadora.recurso.*` topics.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial` to
    every `FunctionWorker` entry function below; none of THOSE calls `kafka.publish` — see
    `ans_submit.register_ans_submit_workers`'s docstring for the same documented sync/async-
    boundary rationale. `dmn` (ADR-0028 §1 seam) is threaded into `assess_eligibility_entry`
    (`recurso_admissibility` + `recurso_eligibility`, T1.5 cutover).

    Fenced-start seams (T-C2, mirrors `register_contas_workers`) — `engine`/`audit_sink` are
    threaded into `handoff_pagamento` ONLY (the one RECURSO worker that runs
    `start_process_idempotent`). ABSENT (`None`) -> the handoff RAISES before any engine effect
    (an un-audited PAGTO-001 start is structurally impossible, ADR-0007 L0). SP-OP-PAGTO-001 is
    also the one STRICT start-dedup family, so the chokepoint additionally demands a
    `DedupReportingAuditSink` + `HistoryQueryingTransport` and refuses without them.

    The 3 raw-handler registrations at the bottom (`notify_sla_risk`/`escalate_ans_timeout`/
    `comunicar_resposta`) DO call `kafka.publish` (module-level rationale above their factories)
    — `harness.register()`, not `register_worker()` (mirrors `lgpd`'s
    `request_additional_proof` / `events.publish`).
    """
    dmn = seams.get("dmn")
    engine: CibSevenTransport | None = seams.get("engine")
    audit_sink: AuditStartSink | None = seams.get("audit_sink")
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
            "operadora.recurso.registrar_indeferimento",
            functools.partial(registrar_indeferimento_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.publish_completed", functools.partial(publish_completed_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.recurso.handoff_pagamento",
            functools.partial(handoff_pagamento_entry, kafka=kafka, engine=engine, audit_sink=audit_sink),
        )
    )
    # Raw handlers — need the async Kafka seam (module-level rationale above their factories).
    harness.register(_NOTIFY_SLA_RISK_TOPIC, make_notify_sla_risk_handler(kafka))
    harness.register(_ESCALATE_ANS_TIMEOUT_TOPIC, make_escalate_ans_timeout_handler(kafka))
    harness.register(_COMUNICAR_RESPOSTA_TOPIC, make_comunicar_resposta_handler(kafka))
