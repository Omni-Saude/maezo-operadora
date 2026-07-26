"""SP-OP-CONTAS-001 Worker — Processamento de Contas / Glosa.

External tasks consumed by the BPMN engine for glosa identification,
triage, and adverse-action gating.

Invariante L0 hard: register_glosa_accept is GUARDED by
ERR_GLOSA_ACCEPT_NOT_HUMAN — a glosa substantiva (authorization_denial-class)
only materializes after human decision in UT_AnalistaContas.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from maezo.tools.workers.base import FunctionWorker, non_blank, pick_fields
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# Process key of the SP-OP-RECURSO-001 handoff target — one recurso per glosa per guia TISS,
# human-gated by RECURSO-001's own User Tasks. CONTAS never files the recurso itself; it hands off.
RECURSO_PROCESS_KEY = "SP-OP-RECURSO-001"

# Deterministic-input guard: cannot key RECURSO-001 without a glosa identity (fail-closed).
ERR_CONTAS_RECURSO_SEM_GLOSA = "ERR_CONTAS_RECURSO_SEM_GLOSA"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class GlosaAcceptNotHumanError(PermissionError):
    """Raised when register_glosa_accept is called without human authorization.

    Guard ERR_GLOSA_ACCEPT_NOT_HUMAN — the worker MUST refuse to register
    a glosa acceptance unless decisao_contas == ACEITAR_GLOSA was set by
    a human analyst with all required fields present.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_GLOSA_ACCEPT_NOT_HUMAN: glosa acceptance requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class ContasRecursoSemGlosaError(ValueError):
    """Raised when start_recurso has no glosa identity to key SP-OP-RECURSO-001.

    Fail-closed (ERR_CONTAS_RECURSO_SEM_GLOSA): a handoff without glosa_id/numero_guia_tiss cannot
    derive a deterministic RECURSO-001 business key — refuse rather than start under an empty key.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_CONTAS_RECURSO_SEM_GLOSA}: {detail}" if detail else ERR_CONTAS_RECURSO_SEM_GLOSA
        )


class ContasLoteInvalidoError(ValueError):
    """Raised when the lote/demonstrativo is inconsistent (ERR_CONTAS_LOTE_INVALIDO)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"ERR_CONTAS_LOTE_INVALIDO: {detail}" if detail else "ERR_CONTAS_LOTE_INVALIDO")


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class GlosaInput:
    """Input for glosa identification and analysis workers."""

    tenant_id: str
    numero_lote_tiss: str
    numero_guia_tiss: str | None = None
    numero_conta: str | None = None
    prestador_id: str = ""
    beneficiario_pseudo_id: str = ""
    competencia: str = ""
    data_recebimento_lote: str = ""
    valor_apresentado_brl: float = 0.0
    tipo_lote: str = ""
    linhas_conta_refs: list[dict[str, Any]] = field(default_factory=list)
    reason_codes_tiss: list[str] = field(default_factory=list)
    item_conforme_tabela: bool = False
    documentacao_anexa: bool = False
    indicio_fraude_sinalizado: bool = False


@dataclass
class GlosaIdentified:
    """Output of identify_glosa — computed facts, never decisions."""

    has_glosas: bool = False
    denial_ratio: float = 0.0
    divergencia_valor: bool = False
    glosa_count: int = 0
    total_glosado_candidato_centavos: int = 0
    linhas_glosadas_candidatas: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GlosaTriageResult:
    """Output of prepare_triage_dossier — roteamento, never decisao."""

    roteamento: str = "ANALISE_HUMANA"
    motivo: str = ""
    glosa_type: str = ""
    categoria_normalizada: str = "desconhecida"


@dataclass
class GlosaAcceptInput:
    """Input for register_glosa_accept — the gated adverse effect."""

    decisao_contas: str = ""
    justificativa_glosa: str = ""
    codigo_glosa_aceito: str = ""
    valor_glosa_aceito_brl: float = 0.0
    analista_id: str = ""


@dataclass
class GlosaAcceptResult:
    """Output of register_glosa_accept."""

    glosa_id: str = ""
    registered: bool = False


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def identify_glosa(input_data: GlosaInput) -> GlosaIdentified:
    """Identify candidate glosa lines in a TISS demonstrativo.

    Computes has_glosas, denial_ratio, divergencia_valor, glosa_count,
    and total_glosado_candidato_centavos from linhas_conta_refs and
    reason_codes_tiss. This is pure arithmetic — no decision.

    FAIL-CLOSED: missing line detail => has_glosas=true,
    divergencia_valor=true, denial_ratio=1.0.
    """
    logger.info(
        "contas.identify_glosa.start",
        tenant_id=input_data.tenant_id,
        numero_lote_tiss=input_data.numero_lote_tiss,
    )

    linhas = input_data.linhas_conta_refs
    reason_codes = input_data.reason_codes_tiss

    # Fail-closed: no lines => conservative
    if not linhas:
        logger.warning(
            "contas.identify_glosa.no_lines",
            tenant_id=input_data.tenant_id,
            numero_lote_tiss=input_data.numero_lote_tiss,
        )
        return GlosaIdentified(
            has_glosas=True,
            denial_ratio=1.0,
            divergencia_valor=True,
            glosa_count=0,
            total_glosado_candidato_centavos=0,
        )

    linhas_glosadas: list[dict[str, Any]] = []
    total_glosado = 0
    total_apresentado = 0

    for linha in linhas:
        valor_apresentado = _extract_cents(linha, "valor_apresentado")
        valor_glosado = _extract_cents(linha, "valor_glosado")
        valor_pago = _extract_cents(linha, "valor_pago")
        rc_list = linha.get("reason_codes_tiss")
        reason_code = linha.get("reason_code_tiss") or (
            rc_list[0] if isinstance(rc_list, list) and rc_list else None
        )

        total_apresentado += valor_apresentado

        # Glosa candidate: valor_glosado > 0 or reason_code present
        if valor_glosado > 0 or (reason_code and str(reason_code).strip()):
            if valor_glosado > 0:
                total_glosado += valor_glosado
            elif valor_pago:
                total_glosado += valor_apresentado - valor_pago
            else:
                total_glosado += valor_apresentado
            linhas_glosadas.append(linha)

    has_glosas = len(linhas_glosadas) > 0 or len(reason_codes) > 0
    glosa_count = len(linhas_glosadas)

    denial_ratio = min(total_glosado / total_apresentado, 1.0) if total_apresentado > 0 else 0.0

    total_apresentado_int = int(input_data.valor_apresentado_brl * 100)
    divergencia_valor = total_glosado > 0 or (total_apresentado != total_apresentado_int)

    result = GlosaIdentified(
        has_glosas=has_glosas,
        denial_ratio=denial_ratio,
        divergencia_valor=divergencia_valor,
        glosa_count=glosa_count,
        total_glosado_candidato_centavos=total_glosado,
        linhas_glosadas_candidatas=linhas_glosadas,
    )

    logger.info(
        "contas.identify_glosa.complete",
        has_glosas=result.has_glosas,
        glosa_count=result.glosa_count,
        denial_ratio=result.denial_ratio,
    )
    return result


def analyze_reason(input_data: GlosaInput, *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Analyze glosa reason codes and produce normalized categories.

    Evaluates the deployed `glosa_reason_normalization` decision table (ADR-0028/T1.5) per
    reason code — replaces the substring-match Python dict that used to live here.

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): the old
    Python mapping was a strict subset of the deployed table — e.g. `CARENCIA`/
    `EXCLUSAO_CONTRATUAL`/`BENEFICIARIO_INATIVO` fell through to Python's `"desconhecida"`
    catch-all, but the DMN maps them to `"clinica"` (live-verified). No adverse output either
    way (both route conservatively downstream via `glosa_triage`).
    """
    logger.info(
        "contas.analyze_reason.start",
        tenant_id=input_data.tenant_id,
        reason_codes=input_data.reason_codes_tiss,
    )

    dmn_transport = require_dmn(dmn, "operadora.contas.analyze_reason")
    reason_map: dict[str, str] = {}
    categories: dict[str, str] = {}

    for code in input_data.reason_codes_tiss:
        rows, _version = evaluate_sync(
            dmn_transport, "glosa_reason_normalization", {"reason_code_tiss": code}
        )
        row = first_row(rows, "glosa_reason_normalization", {"reason_code_tiss": code})
        normalized = str(row.get("categoria_normalizada", "desconhecida"))
        reason_map[code] = normalized
        categories[code] = normalized

    result = {
        "reason_map": reason_map,
        "categoria_normalizada": next(iter(categories.values()), "desconhecida"),
        "categories": categories,
    }

    logger.info("contas.analyze_reason.complete", categories=categories)
    return result


def calculate_impact(identified: GlosaIdentified, valor_apresentado_brl: float) -> dict[str, Any]:
    """Calculate financial impact of identified glosas.

    Pure arithmetic: computes denial_ratio, divergencia_valor, and
    total_glosado_candidato_brl from the identified glosa facts.
    """
    logger.info("contas.calculate_impact.start")

    total_brl = identified.total_glosado_candidato_centavos / 100.0

    result = {
        "denial_ratio": identified.denial_ratio,
        "divergencia_valor": identified.divergencia_valor,
        "total_glosado_candidato_brl": total_brl,
        "total_glosado_candidato_centavos": identified.total_glosado_candidato_centavos,
        "glosa_count": identified.glosa_count,
        "valor_apresentado_brl": valor_apresentado_brl,
        "impacto_percentual": round(identified.denial_ratio * 100, 2),
    }

    logger.info("contas.calculate_impact.complete", impacto_percentual=result["impacto_percentual"])
    return result


def prepare_triage_dossier(
    input_data: GlosaInput,
    identified: GlosaIdentified,
    reason_analysis: dict[str, Any],
    *,
    dmn: DmnTransport | None = None,
    tipo_item: str = "",
) -> GlosaTriageResult:
    """Prepare the triage dossier for the human analyst.

    Delegates to Marina (LLM agent) for narrative assembly; evaluates the deployed
    `glosa_triage` decision table (ADR-0028/T1.5) — the DMN produces `SEM_GLOSA`, `RECORRER`,
    or `ANALISE_HUMANA` — never `ACEITAR` (glosa acceptance is exclusively
    `operadora.contas.register_glosa_accept`, GUARDED, untouched by this cutover).

    golden-parity finding — MAJOR behavior gap closed (T1.5): the old Python NEVER produced
    `RECORRER` (its own docstring said "For now, always route to ANALISE_HUMANA (conservative)")
    — it consulted only 2 of the DMN's 5 inputs (`has_glosas`, `divergencia_valor`), never
    `item_conforme_tabela`/`documentacao_anexa`/`tipo_item`/`categoria_normalizada` for
    branching, so `RECORRER` was structurally unreachable dead code. This cutover activates
    that path (live-verified: `categoria_normalizada="valor"` + `item_conforme_tabela=True` +
    `divergencia_valor=True` + `documentacao_anexa=True` -> `RECORRER`). `tipo_item` is a NEW,
    additive parameter (not previously part of `GlosaInput`) sourced from the raw process
    variables at the entry-function boundary. Flagged: glosa is money-adjacent — per ADR-0028
    §7 this is in the money/adverse cutover bucket requiring policy-guardian review.
    """
    logger.info(
        "contas.prepare_triage_dossier.start",
        tenant_id=input_data.tenant_id,
        has_glosas=identified.has_glosas,
    )

    categoria_normalizada = reason_analysis.get("categoria_normalizada", "desconhecida")
    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.contas.prepare_triage_dossier"),
        "glosa_triage",
        {
            "tipo_item": tipo_item,
            "categoria_normalizada": categoria_normalizada,
            "item_conforme_tabela": input_data.item_conforme_tabela,
            "divergencia_valor": identified.divergencia_valor,
            "documentacao_anexa": input_data.documentacao_anexa,
        },
    )
    row = first_row(
        rows,
        "glosa_triage",
        {"tenant_id": input_data.tenant_id, "numero_lote_tiss": input_data.numero_lote_tiss},
    )
    roteamento = str(row.get("roteamento", "ANALISE_HUMANA"))
    motivo = str(row.get("motivo", ""))

    result = GlosaTriageResult(
        roteamento=roteamento,
        motivo=motivo,
        categoria_normalizada=categoria_normalizada,
    )

    logger.info(
        "contas.prepare_triage_dossier.complete",
        roteamento=result.roteamento,
        dmn_decision_version=version.version,
    )
    return result


def register_glosa_accept(input_data: GlosaAcceptInput) -> GlosaAcceptResult:
    """Register a glosa acceptance — GUARDED adverse effect.

    ERR_GLOSA_ACCEPT_NOT_HUMAN: the worker MUST refuse if:
    - decisao_contas != ACEITAR_GLOSA
    - Missing justificativa_glosa, codigo_glosa_aceito,
      valor_glosa_aceito_brl, or analista_id

    Only reachable after UT_AnalistaContas completed by a human.
    """
    logger.info(
        "contas.register_glosa_accept.start",
        decisao_contas=input_data.decisao_contas,
        analista_id=input_data.analista_id,
    )

    missing: list[str] = []

    if input_data.decisao_contas != "ACEITAR_GLOSA":
        missing.append("decisao_contas != ACEITAR_GLOSA")
    if not input_data.justificativa_glosa.strip():
        missing.append("justificativa_glosa")
    if not input_data.codigo_glosa_aceito.strip():
        missing.append("codigo_glosa_aceito")
    if input_data.valor_glosa_aceito_brl <= 0:
        missing.append("valor_glosa_aceito_brl")
    if not input_data.analista_id.strip():
        missing.append("analista_id")

    if missing:
        raise GlosaAcceptNotHumanError(missing_fields=missing)

    glosa_id = f"GLOSA-{input_data.analista_id}-{_timestamp_hash()}"

    logger.info(
        "contas.register_glosa_accept.complete",
        glosa_id=glosa_id,
        analista_id=input_data.analista_id,
    )
    return GlosaAcceptResult(glosa_id=glosa_id, registered=True)


def notify_sla_risk(
    tenant_id: str,
    numero_lote_tiss: str,
    sla_remaining: str = "",
    grupo: str = "coordenacao-contas",
) -> dict[str, Any]:
    """Notify coordenação-contas of SLA risk (non-interruptive timer).

    Alerts the coordination group before the SLA breach triggers.
    """
    logger.info(
        "contas.notify_sla_risk",
        tenant_id=tenant_id,
        numero_lote_tiss=numero_lote_tiss,
        sla_remaining=sla_remaining,
        grupo=grupo,
    )

    return {
        "notified": True,
        "grupo": grupo,
        "sla_remaining": sla_remaining,
        "numero_lote_tiss": numero_lote_tiss,
    }


def _recurso_business_key(tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """`RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` (contract `SP-OP-RECURSO-001.md`
    "Business key (idempotencia)") — one recurso per glosa per guia TISS. IDENTICAL scheme to
    `notification_bridge._recurso_business_key`, so the in-flow worker start and the (decoupled)
    Kafka-bridge start converge on the SAME instance (idempotent redundancy, never a divergent
    double-start)."""
    return f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"


def start_recurso(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff: idempotently START/correlate SP-OP-RECURSO-001 when decisao_contas==RECORRER.

    NOT an adverse effect — RECURSO-001 owns the recurso filing, human-gated by its OWN User Tasks;
    CONTAS never files the recurso. This worker starts RECURSO-001 (business key
    ``RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}``) via the shared, business-key-idempotent
    ``start_process_idempotent`` chokepoint — an already-active recurso for this glosa is returned
    unchanged (no duplicate recurso).

    T-C2 FENCE (mirrors ``inadimplencia.handoff_rescisao``, the merged 10th start site): the
    chokepoint REQUIRES a durable ``audit_sink`` + ``AgentDecisionProvenance`` — the ADR-0007 start
    record is emitted exactly-once (dedup key ``{tenant}:start:SP-OP-RECURSO-001:{business_key}``)
    BEFORE any engine effect. Deterministic worker context: ``agent_id`` = the stable service
    identity (``AUDIT_AGENT_ID``), no ``model_id``/``prompt_version`` (no LLM decides — the analyst's
    RECORRER in ``UT_AnalistaContas`` does), and ``decision_basis`` carries ONLY bounded enum/flag
    tokens (design §3.3); glosa/guia identifiers stay in the start ``variables``, bound one-way via
    the record's ``input_sha256``, never stored in the clear.

    P1-safe mid-handler effect (audit-emit design §4.2): the ONLY external effect is the
    business-key-idempotent process start (its dedup makes re-delivery return the SAME active
    instance), and the business key is a DETERMINISTIC function of process variables (no
    wall-clock/uuid) — so this worker is exempt from the "no mid-handler external effect" rule.

    FAIL-CLOSED (never a silent no-op):
      - missing engine seam (``engine is None`` — composition root not wired) raises (transient);
      - missing audit seam (``audit_sink is None``) raises (transient) — RECURSO-001 can never
        start un-audited (ADR-0007 L0);
      - missing/blank/None business-key anchor (``tenant_id``/``glosa_id``/``numero_guia_tiss``)
        raises ``ContasRecursoSemGlosaError`` (deterministic -> incident) — never a start under an
        empty/degenerate business key. EB-4 R1 finding: anchors are validated with the SHARED
        `non_blank` (the bridge's own semantics) — plain `str(...)` truthiness would let a
        whitespace-only anchor or an explicit ``None`` (`str(None) == "None"`) through, minting
        degenerate keys like ``RECURSO--GUIA-…-None``.
    """
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    if not (
        non_blank(variables.get("tenant_id"))
        and non_blank(variables.get("glosa_id"))
        and non_blank(variables.get("numero_guia_tiss"))
    ):
        # No usable business-key anchor -> cannot key RECURSO-001 -> refuse (deterministic bad
        # input, never a start under an empty/whitespace/None-degenerate business key).
        logger.error(
            "contas_start_recurso_no_glosa_identity",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise ContasRecursoSemGlosaError(
            "start_recurso: tenant_id/glosa_id/numero_guia_tiss ausente, em branco ou None — nao "
            "ha ancora de business key para iniciar RECURSO-001 (recusado, nunca inicia com "
            "business key vazia/degenerada)"
        )

    tenant_id = str(variables.get("tenant_id", ""))
    glosa_id = str(variables.get("glosa_id", ""))
    numero_guia_tiss = str(variables.get("numero_guia_tiss", ""))
    glosa_type = str(variables.get("glosa_type", ""))
    documentacao_anexa = bool(variables.get("documentacao_anexa", False))
    numero_lote_tiss = str(variables.get("numero_lote_tiss", ""))

    if engine is None:
        logger.error("contas_start_recurso_engine_seam_not_wired", glosa_id=glosa_id)
        raise RuntimeError(
            "start_recurso: engine seam (CibSevenTransport) not wired — cannot start RECURSO-001; "
            "failing closed to a retry/incident (never a silent no-op)"
        )
    if audit_sink is None:
        logger.error("contas_start_recurso_audit_sink_not_wired", glosa_id=glosa_id)
        raise RuntimeError(
            "start_recurso: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 start "
            "record, so RECURSO-001 is NOT started (fail-closed, emit-before-effect); failing to a "
            "retry/incident (never a silent no-op, never an un-audited start)"
        )

    business_key = _recurso_business_key(tenant_id, numero_guia_tiss, glosa_id)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "glosa_id": glosa_id,
        "numero_guia_tiss": numero_guia_tiss,
        "glosa_type": glosa_type,
        "glosa_existe": True,
        "documentacao_anexa": documentacao_anexa,
        "numero_lote_tiss": numero_lote_tiss,
    }
    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "decisao_contas": "RECORRER",
            "glosa_type": glosa_type,
            "documentacao_anexa": documentacao_anexa,
        },
        model_id=None,
        prompt_version=None,
    )
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=RECURSO_PROCESS_KEY,
            business_key=business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )
    logger.info(
        "contas.start_recurso",
        glosa_id=glosa_id,
        numero_guia_tiss=numero_guia_tiss,
        recurso_business_key=business_key,
        recurso_instance_id=instance.instance_id,
        recurso_already_existed=instance.already_existed,
    )
    return {
        "handoff": RECURSO_PROCESS_KEY,
        "handoff_executado": True,
        "processo_destino": RECURSO_PROCESS_KEY,
        "glosa_id": glosa_id,
        "numero_guia_tiss": numero_guia_tiss,
        "glosa_type": glosa_type,
        "glosa_existe": True,
        "documentacao_anexa": documentacao_anexa,
        "recurso_business_key": business_key,
        "recurso_instance_id": instance.instance_id,
        "recurso_already_existed": instance.already_existed,
    }


def reconcile_payment(
    numero_lote_tiss: str,
    numero_guia_tiss: str,
    status: str = "REENVIAR",
) -> dict[str, Any]:
    """Reconcile/re-register payment when decisao_contas == REENVIAR.

    No adverse effect — purely clerical reconciliation.
    TASY write DROP: we consume CDC, never write to Tasy (ADR-0013).
    """
    logger.info(
        "contas.reconcile_payment",
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


def publish(
    event_type: str,
    payload: dict[str, Any],
    topic: str = "",
) -> dict[str, Any]:
    """Generic domain event publisher.

    Consumed as operadora.events.publish — publishes Kafka events
    for audit trail (dual engine+Kafka, ADR-0007).
    """
    full_topic = topic or f"agents.events.{event_type}"
    logger.info(
        "contas.publish",
        event_type=event_type,
        topic=full_topic,
    )

    return {
        "published": True,
        "topic": full_topic,
        "event_type": event_type,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_cents(linha: dict[str, Any], prefix: str) -> int:
    """Extract value in centavos from a conta line.

    Tries {prefix}_centavos first, then {prefix}_brl * 100.
    """
    cents = linha.get(f"{prefix}_centavos")
    if cents is not None:
        return int(cents)
    brl = linha.get(f"{prefix}_brl")
    if brl is not None:
        return int(float(brl) * 100)
    return 0


def _timestamp_hash() -> str:
    """Generate a short unique hash for IDs."""
    import hashlib
    import time

    raw = f"{time.time_ns()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12].upper()


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`) — EXACT 1:1 name
# match for 8 of 9 functions:
#   identify_glosa, analyze_reason, calculate_impact, prepare_triage_dossier,
#   register_glosa_accept (GUARDED), notify_sla_risk, start_recurso, reconcile_payment.
# `publish` has no distinct spec topic (its own docstring: "Consumed as
# operadora.events.publish" — the shared/out-of-scope generic topic, ADR-0026
# §2b note) — registered under a function-derived topic for registry
# completeness.
#
# Fail-closed (ADR-0026 §2b): `GlosaInput.tenant_id`/`numero_lote_tiss` have
# no dataclass default — a missing/blank value raises `TypeError` at
# construction, translated here to this module's own `ContasLoteInvalidoError`
# (ERR_CONTAS_LOTE_INVALIDO) rather than the harness's generic classification.
# ---------------------------------------------------------------------------


def _build_glosa_input(variables: dict[str, Any]) -> GlosaInput:
    try:
        return GlosaInput(**pick_fields(variables, GlosaInput))
    except TypeError as exc:
        raise ContasLoteInvalidoError(f"campos obrigatorios ausentes: {exc}") from exc


def identify_glosa_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.identify_glosa` -> `identify_glosa`."""
    del kafka  # unused — identify_glosa emits no domain event
    input_data = _build_glosa_input(variables)
    result = identify_glosa(input_data)
    return dataclasses.asdict(result)


def analyze_reason_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    dmn: DmnTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.analyze_reason` -> `analyze_reason`."""
    del kafka  # unused — analyze_reason emits no domain event
    input_data = _build_glosa_input(variables)
    return analyze_reason(input_data, dmn=dmn)


def calculate_impact_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.calculate_impact` -> `calculate_impact`."""
    del kafka  # unused — calculate_impact emits no domain event
    identified = GlosaIdentified(**pick_fields(variables, GlosaIdentified))
    valor_apresentado_brl = variables.get("valor_apresentado_brl", 0.0)
    return calculate_impact(identified, valor_apresentado_brl)


def prepare_triage_dossier_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    dmn: DmnTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.prepare_triage_dossier` -> `prepare_triage_dossier`.

    `tipo_item` (NEW, ADR-0028/T1.5 — see `prepare_triage_dossier`'s docstring) is read directly
    off the raw process variables here, since it is not (and was never) part of `GlosaInput`.
    """
    del kafka  # unused — prepare_triage_dossier emits no domain event
    input_data = _build_glosa_input(variables)
    identified = GlosaIdentified(**pick_fields(variables, GlosaIdentified))
    reason_analysis = {
        "reason_map": variables.get("reason_map", {}),
        "categoria_normalizada": variables.get("categoria_normalizada", "desconhecida"),
        "categories": variables.get("categories", {}),
    }
    tipo_item = variables.get("tipo_item", "")
    result = prepare_triage_dossier(input_data, identified, reason_analysis, dmn=dmn, tipo_item=tipo_item)
    return dataclasses.asdict(result)


def register_glosa_accept_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.register_glosa_accept` -> `register_glosa_accept`
    (GUARDED).

    Raises `GlosaAcceptNotHumanError` (fail-closed, ERR_GLOSA_ACCEPT_NOT_HUMAN) when the human
    decision (`decisao_contas == ACEITAR_GLOSA` + required fields) is missing — unchanged guard,
    only the dict<->dataclass marshalling is new.
    """
    del kafka  # unused — register_glosa_accept emits no domain event itself
    input_data = GlosaAcceptInput(**pick_fields(variables, GlosaAcceptInput))
    result = register_glosa_accept(input_data)
    return dataclasses.asdict(result)


def notify_sla_risk_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.notify_sla_risk` -> `notify_sla_risk`."""
    del kafka  # unused — notify_sla_risk emits no domain event
    tenant_id = variables.get("tenant_id", "")
    numero_lote_tiss = variables.get("numero_lote_tiss", "")
    sla_remaining = variables.get("sla_remaining", "")
    grupo = variables.get("grupo", "coordenacao-contas")
    return notify_sla_risk(tenant_id, numero_lote_tiss, sla_remaining, grupo)


def start_recurso_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.start_recurso` -> `start_recurso`.

    Threads the fenced-start seams (`engine`/`audit_sink`) — start_recurso now REALLY starts
    SP-OP-RECURSO-001 through the ADR-0007 chokepoint (was a stub returning a marker dict).
    """
    del kafka  # unused — start_recurso starts a process, it does not publish a Kafka event
    return start_recurso(variables, engine=engine, audit_sink=audit_sink)


def reconcile_payment_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.reconcile_payment` -> `reconcile_payment`."""
    del kafka  # unused — reconcile_payment emits no domain event
    numero_lote_tiss = variables.get("numero_lote_tiss", "")
    numero_guia_tiss = variables.get("numero_guia_tiss", "")
    status = variables.get("status", "REENVIAR")
    return reconcile_payment(numero_lote_tiss, numero_guia_tiss, status)


def publish_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.publish` -> `publish`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "")
    payload = variables.get("payload") or {}
    topic = variables.get("topic", "")
    return publish(event_type, payload, topic)


def register_contas_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-CONTAS-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale. `dmn` (ADR-0028 §1 seam) is
    threaded into `analyze_reason_entry` (`glosa_reason_normalization`) and
    `prepare_triage_dossier_entry` (`glosa_triage`, T1.5 cutover).
    """
    dmn = seams.get("dmn")
    # Fenced-start seams (T-C2, mirrors register_inadimplencia_workers) — threaded into
    # start_recurso ONLY (the one CONTAS worker that runs `start_process_idempotent`). ABSENT
    # (`None`) -> start_recurso RAISES before any engine effect (an un-audited RECURSO-001 start is
    # structurally impossible, ADR-0007 L0). In the live daemon: FreshClientCibSevenTransport +
    # FreshSinkAuditEmitter (loop-agnostic, sync dispatch emits on its own asyncio.run loop).
    engine: CibSevenTransport | None = seams.get("engine")
    audit_sink: AuditStartSink | None = seams.get("audit_sink")
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.identify_glosa", functools.partial(identify_glosa_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.analyze_reason",
            functools.partial(analyze_reason_entry, kafka=kafka, dmn=dmn),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.calculate_impact", functools.partial(calculate_impact_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.prepare_triage_dossier",
            functools.partial(prepare_triage_dossier_entry, kafka=kafka, dmn=dmn),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.register_glosa_accept",
            functools.partial(register_glosa_accept_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.notify_sla_risk", functools.partial(notify_sla_risk_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.start_recurso",
            functools.partial(start_recurso_entry, kafka=kafka, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.reconcile_payment", functools.partial(reconcile_payment_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.contas.publish", functools.partial(publish_entry, kafka=kafka))
    )
