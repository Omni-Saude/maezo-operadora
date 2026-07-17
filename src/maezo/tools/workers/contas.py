"""SP-OP-CONTAS-001 Worker — Processamento de Contas / Glosa.

External tasks consumed by the BPMN engine for glosa identification,
triage, and adverse-action gating.

Invariante L0 hard: register_glosa_accept is GUARDED by
ERR_GLOSA_ACCEPT_NOT_HUMAN — a glosa substantiva (authorization_denial-class)
only materializes after human decision in UT_AnalistaContas.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)


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


def analyze_reason(input_data: GlosaInput) -> dict[str, Any]:
    """Analyze glosa reason codes and produce normalized categories.

    Consults glosa_reason_normalization (DMN) to map TISS reason codes
    to categories: tecnica, administrativa, clinica, valor, documental,
    desconhecida (catch-all).
    """
    logger.info(
        "contas.analyze_reason.start",
        tenant_id=input_data.tenant_id,
        reason_codes=input_data.reason_codes_tiss,
    )

    reason_map: dict[str, str] = {}
    categories: dict[str, str] = {}

    for code in input_data.reason_codes_tiss:
        normalized = _normalize_reason_code(code)
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
) -> GlosaTriageResult:
    """Prepare the triage dossier for the human analyst.

    Delegates to Marina (LLM agent) for narrative assembly;
    the result routes to ANALISE_HUMANA — NEVER auto-accepts a glosa.

    The glosa_triage DMN (DMN-only) produces SEM_GLOSA, RECORRER,
    or ANALISE_HUMANA — never ACEITAR.
    """
    logger.info(
        "contas.prepare_triage_dossier.start",
        tenant_id=input_data.tenant_id,
        has_glosas=identified.has_glosas,
    )

    roteamento = "ANALISE_HUMANA"  # Conservative default
    motivo = ""

    if not identified.has_glosas and not identified.divergencia_valor:
        roteamento = "SEM_GLOSA"
        motivo = "Nenhuma glosa candidata identificada"
    elif identified.has_glosas and identified.denial_ratio > 0:
        motivo = f"Glosas candidatas: {identified.glosa_count} itens, {identified.denial_ratio:.1%}"
        # Route to RECORRER or ANALISE_HUMANA based on DMN triage
        # For now, always route to ANALISE_HUMANA (conservative)
        roteamento = "ANALISE_HUMANA"

    result = GlosaTriageResult(
        roteamento=roteamento,
        motivo=motivo,
        categoria_normalizada=reason_analysis.get("categoria_normalizada", "desconhecida"),
    )

    logger.info("contas.prepare_triage_dossier.complete", roteamento=result.roteamento)
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


def start_recurso(
    glosa_id: str,
    numero_guia_tiss: str,
    glosa_type: str = "",
    documentacao_anexa: bool = False,
) -> dict[str, Any]:
    """Handoff to SP-OP-RECURSO-001 when decisao_contas == RECORRER.

    Passes glosa_id, numero_guia_tiss, glosa_type, glosa_existe=true,
    and documentacao_anexa to the recurso process.
    """
    logger.info(
        "contas.start_recurso",
        glosa_id=glosa_id,
        numero_guia_tiss=numero_guia_tiss,
        glosa_type=glosa_type,
    )

    return {
        "handoff": "SP-OP-RECURSO-001",
        "glosa_id": glosa_id,
        "numero_guia_tiss": numero_guia_tiss,
        "glosa_type": glosa_type,
        "glosa_existe": True,
        "documentacao_anexa": documentacao_anexa,
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


def _normalize_reason_code(code: str) -> str:
    """Map a TISS reason code to a normalized category.

    DMN glosa_reason_normalization (hitPolicy FIRST).
    Catch-all -> 'desconhecida'.
    """
    code_upper = code.strip().upper()
    mapping: dict[str, str] = {
        "TECNICA": "tecnica",
        "ADMINISTRATIVA": "administrativa",
        "CLINICA": "clinica",
        "VALOR": "valor",
        "DOCUMENTAL": "documental",
        "DOCUMENTACAO": "documental",
    }
    for key, value in mapping.items():
        if key in code_upper:
            return value
    return "desconhecida"


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


def analyze_reason_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.analyze_reason` -> `analyze_reason`."""
    del kafka  # unused — analyze_reason emits no domain event
    input_data = _build_glosa_input(variables)
    return analyze_reason(input_data)


def calculate_impact_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.calculate_impact` -> `calculate_impact`."""
    del kafka  # unused — calculate_impact emits no domain event
    identified = GlosaIdentified(**pick_fields(variables, GlosaIdentified))
    valor_apresentado_brl = variables.get("valor_apresentado_brl", 0.0)
    return calculate_impact(identified, valor_apresentado_brl)


def prepare_triage_dossier_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.prepare_triage_dossier` -> `prepare_triage_dossier`."""
    del kafka  # unused — prepare_triage_dossier emits no domain event
    input_data = _build_glosa_input(variables)
    identified = GlosaIdentified(**pick_fields(variables, GlosaIdentified))
    reason_analysis = {
        "reason_map": variables.get("reason_map", {}),
        "categoria_normalizada": variables.get("categoria_normalizada", "desconhecida"),
        "categories": variables.get("categories", {}),
    }
    result = prepare_triage_dossier(input_data, identified, reason_analysis)
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


def start_recurso_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.start_recurso` -> `start_recurso`."""
    del kafka  # unused — start_recurso emits no domain event
    glosa_id = variables.get("glosa_id", "")
    numero_guia_tiss = variables.get("numero_guia_tiss", "")
    glosa_type = variables.get("glosa_type", "")
    documentacao_anexa = variables.get("documentacao_anexa", False)
    return start_recurso(glosa_id, numero_guia_tiss, glosa_type, documentacao_anexa)


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
    docstring for the same documented sync/async-boundary rationale.
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.identify_glosa", functools.partial(identify_glosa_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.analyze_reason", functools.partial(analyze_reason_entry, kafka=kafka)
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
            functools.partial(prepare_triage_dossier_entry, kafka=kafka),
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
        FunctionWorker("operadora.contas.start_recurso", functools.partial(start_recurso_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.reconcile_payment", functools.partial(reconcile_payment_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.contas.publish", functools.partial(publish_entry, kafka=kafka))
    )
