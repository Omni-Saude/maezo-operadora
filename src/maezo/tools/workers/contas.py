"""SP-OP-CONTAS-001 Worker — Analise e Adjudicacao de Conta Medica (Glosa).

External tasks consumed by the BPMN engine for the PAYER's adjudication of a conta the
prestador presented: apuracao of divergences, triage, the gated adverse action, the
communication back to the prestador, and the neutral handoffs.

PERSPECTIVE (ADR-0040, Proposed). The operadora RECEIVES the lote de guias and EMITS the
demonstrativo de analise. It does not contest, accept or re-file its own glosa. What this module
gates is therefore the APPLICATION of a glosa against the prestador — not the acceptance of
someone else's.

Invariante L0 hard: `registrar_glosa` is GUARDED by ERR_CONTAS_GLOSA_NOT_HUMAN — a glosa
substantiva (authorization_denial-adjacent) only materializes after a human decision
`decisao_contas in {GLOSAR, PAGAR_PARCIAL}` in `UT_AnalistaContas` /
`UT_CoordenacaoContasAssume`.

Invariante I-PAGTO-1 (ADR-0040): `handoff_pagamento` NEVER seeds `lastro_confirmado`,
`dados_pagamento_validos`, `duplicidade_suspeita` or `dentro_teto_l2` into SP-OP-PAGTO-001 — it
seeds the EVIDENCE (`lastro_origem`/`lastro_decisor_id`). Consequence, by construction: every
order originated here enters PAGTO through the human `UT_AnaliseAdmissibilidade`.
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
from maezo.tools.workers.base import (
    FunctionWorker,
    non_blank,
    pick_fields,
    resolve_fraude_numero_caso,
)
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# Process key of the SP-OP-PAGTO-001 handoff target — one payment order per adjudicated lote per
# prestador. CONTAS never releases money: PAGTO owns the release, behind its own alcada ladder AND
# (invariant I-PAGTO-1) behind the mandatory human `UT_AnaliseAdmissibilidade`.
PAGTO_PROCESS_KEY = "SP-OP-PAGTO-001"

# Deterministic-input guard: cannot key PAGTO-001 without the lote/prestador anchors, a positive
# amount and a due date (fail-closed — never a defaulted amount, never an invented deadline).
ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO = "ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO"

# The communication worker refuses an undeclared `tipo_comunicacao` rather than defaulting to the
# most common one — a silently mistyped communication to a prestador is a regulatory artifact.
ERR_CONTAS_COMUNICACAO_INVALIDA = "ERR_CONTAS_COMUNICACAO_INVALIDA"

# Returning a conta to the prestador requires a stated reason and an author (fail-closed).
ERR_CONTAS_DEVOLUCAO_INVALIDA = "ERR_CONTAS_DEVOLUCAO_INVALIDA"

# Process key of the SP-OP-FRAUDE-001 handoff target (Phase-3 CONTAS→FRAUDE leg) — one active
# investigation per case, human-gated by FRAUDE-001's own User Tasks. CONTAS never investigates
# nor accuses: the analyst's ENCAMINHAR_FRAUDE decision (UT_AnalistaContas, L0 hard) only starts
# the neutral FRAUDE-001 process; the accusation is FRAUDE-001's own human decision.
FRAUDE_PROCESS_KEY = "SP-OP-FRAUDE-001"

# Deterministic-input guard: cannot key FRAUDE-001 without a target identity (fail-closed).
ERR_CONTAS_FRAUDE_SEM_ALVO = "ERR_CONTAS_FRAUDE_SEM_ALVO"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ContasGlosaNotHumanError(PermissionError):
    """Raised when `registrar_glosa` is called without human authorization.

    Guard ERR_CONTAS_GLOSA_NOT_HUMAN — the worker MUST refuse to APPLY a glosa against the
    prestador unless `decisao_contas in {GLOSAR, PAGAR_PARCIAL}` was set by a human (analista de
    contas in `UT_AnalistaContas`, or coordenacao in `UT_CoordenacaoContasAssume`) with every
    required field present.

    SAME BASE CLASS as the guard it replaces (`PermissionError`), so the refusal keeps travelling
    the ADR-0030 §5 audited-incident path and is NEVER reported as a `bpmnError`. The harness
    recognises the code by its `_NOT_HUMAN` suffix (`harness.is_guard_refusal_code`) — no harness
    change is needed for the rename.

    WHAT CHANGED IS THE PROTECTED OBJECT, not the mechanism: the adverse act of a payer is
    applying the glosa, not accepting one. Precedent for guarding two outcomes with one worker:
    `reembolso.registrar_decisao` (`decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}`).
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_CONTAS_GLOSA_NOT_HUMAN: applying a glosa requires a human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class ContasHandoffPagamentoInvalidoError(ValueError):
    """Raised when `handoff_pagamento` cannot key SP-OP-PAGTO-001, or has no honest amount.

    Fail-closed (ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO): a handoff without
    tenant_id/numero_lote_tiss/prestador_id cannot derive a deterministic PAGTO-001 business key;
    an absent/blank/non-numeric/`<= 0` amount would create an order for a number nobody decided;
    a blank `data_vencimento` would create an order with a fabricated deadline. Refuse, never
    default.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO}: {detail}"
            if detail
            else ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO
        )


class ContasComunicacaoInvalidaError(ValueError):
    """Raised when `emitir_demonstrativo` is asked for a `tipo_comunicacao` it does not declare.

    Fail-closed (ERR_CONTAS_COMUNICACAO_INVALIDA): a communication to the prestador is a
    regulatory artifact. Defaulting an unknown discriminator to the most common type would send
    an adjudication where a devolucao was meant (or the reverse), silently.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_CONTAS_COMUNICACAO_INVALIDA}: {detail}" if detail else ERR_CONTAS_COMUNICACAO_INVALIDA
        )


class ContasDevolucaoInvalidaError(ValueError):
    """Raised when `devolver_conta` has no stated reason or no author.

    Fail-closed (ERR_CONTAS_DEVOLUCAO_INVALIDA). Returning a conta delays the prestador's payment;
    it is not L0-adverse (whether it should be is ADR-0040 OQ-7), but it is never anonymous and
    never unmotivated.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_CONTAS_DEVOLUCAO_INVALIDA}: {detail}" if detail else ERR_CONTAS_DEVOLUCAO_INVALIDA
        )


class ContasFraudeSemAlvoError(ValueError):
    """Raised when start_fraude has no target identity to key SP-OP-FRAUDE-001.

    Fail-closed (ERR_CONTAS_FRAUDE_SEM_ALVO): a fraud referral without tenant_id/prestador_id cannot
    derive a deterministic FRAUDE-001 business key — refuse rather than start under an empty key.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"{ERR_CONTAS_FRAUDE_SEM_ALVO}: {detail}" if detail else ERR_CONTAS_FRAUDE_SEM_ALVO)


class ContasLoteInvalidoError(ValueError):
    """Raised when the lote the prestador transmitted is inconsistent (ERR_CONTAS_LOTE_INVALIDO)."""

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
    # PAGTO-001 contract inputs CONTAS echoes verbatim into the handoff (never invented here).
    # `data_vencimento` is `sim` in `SP-OP-PAGTO-001.md`; its contractual origin is DRAFT/verify
    # (ADR-0040 OQ-2) and `handoff_pagamento` REFUSES it blank rather than defaulting a deadline.
    data_vencimento: str = ""
    conta_origem_ref: str = ""
    instrumento_pagamento: str = ""


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
class GlosaRegistroInput:
    """Input for `registrar_glosa` — the gated adverse effect (the payer APPLYING a glosa).

    The first six fields are the HUMAN decision from `UT_AnalistaContas` /
    `UT_CoordenacaoContasAssume` and are what the L0 guard validates. `valor_liberado_brl` is
    required only on the `PAGAR_PARCIAL` outcome (a reduction states BOTH halves, so the
    demonstrativo can close `apresentado = liberado + glosa`). The trailing three are the
    PROCESS-INSTANCE ANCHORS declared as contract inputs by the BPMN; they are read-only context,
    NEVER validated by the guard, and exist solely so `glosa_id` can be derived DETERMINISTICALLY
    instead of from the wall clock (M-9). `registrar_glosa_entry` selects them from the process
    variables via `pick_fields`, so adding them here is all the wiring they need.
    """

    decisao_contas: str = ""
    justificativa_glosa: str = ""
    codigo_glosa_tiss: str = ""
    valor_glosado_brl: float = 0.0
    valor_liberado_brl: Any = None
    analista_id: str = ""
    # --- process-instance anchors (M-9 determinism scope; not guarded, not decided on) ---
    tenant_id: str = ""
    numero_lote_tiss: str = ""
    numero_guia_tiss: str = ""


@dataclass
class GlosaRegistroResult:
    """Output of `registrar_glosa`."""

    glosa_id: str = ""
    registered: bool = False
    decisao_contas: str = ""
    valor_glosado_brl: float = 0.0
    valor_liberado_brl: float = 0.0


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def identify_glosa(input_data: GlosaInput) -> GlosaIdentified:
    """Apura divergences and candidate glosa lines in the conta the PRESTADOR presented.

    Computes has_glosas, denial_ratio, divergencia_valor, glosa_count,
    and total_glosado_candidato_centavos from linhas_conta_refs and
    reason_codes_tiss. This is pure arithmetic — no decision: it flags a CANDIDATE, and the
    decision to glosar is human (`UT_AnalistaContas`, guarded by `registrar_glosa`).

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
    `glosa_triage` decision table (ADR-0028/T1.5) — the DMN produces `PAGAR` or `ANALISE_HUMANA`
    and, by design, NOTHING that gloses (the application of a glosa is exclusively
    `operadora.contas.registrar_glosa`, GUARDED, ADR-0040).

    golden-parity finding — MAJOR behavior gap closed (T1.5): the old Python never produced the
    value-divergence route (its own docstring said "For now, always route to ANALISE_HUMANA
    (conservative)") — it consulted only 2 of the DMN's 5 inputs (`has_glosas`,
    `divergencia_valor`), never `item_conforme_tabela`/`documentacao_anexa`/`tipo_item`/
    `categoria_normalizada` for branching, so that row was structurally unreachable dead code.
    This cutover activated it. Under ADR-0040 the row's OUTPUT changed — the value-divergence row
    now emits `ANALISE_HUMANA` like every other substantive row, because the divergence is a glosa
    CANDIDATE and the decision to glosar belongs to the human. The cutover finding stands; the
    routed-to value is now the conservative one. `tipo_item` is an additive parameter sourced from
    the raw process variables at the entry-function boundary. Flagged: glosa is money-adjacent —
    per ADR-0028 §7 this is in the money/adverse cutover bucket requiring policy-guardian review.
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


def registrar_glosa(input_data: GlosaRegistroInput) -> GlosaRegistroResult:
    """APPLY the glosa the human decided — GUARDED adverse effect (L0 hard, ADR-0005/0018).

    ERR_CONTAS_GLOSA_NOT_HUMAN: the worker MUST refuse if
      - `decisao_contas not in {"GLOSAR", "PAGAR_PARCIAL"}`;
      - `justificativa_glosa` / `codigo_glosa_tiss` / `analista_id` is missing or blank;
      - `valor_glosado_brl <= 0`;
      - `decisao_contas == "PAGAR_PARCIAL"` and `valor_liberado_brl` is absent/blank/non-numeric
        or negative — a reduction must state what it DOES release, not only what it withholds.

    Only reachable after `UT_AnalistaContas` / `UT_CoordenacaoContasAssume` completed by a human.
    NEVER decides; only registers a decision already taken. Mints a DETERMINISTIC `glosa_id`.

    TWO HUMAN CHANNELS, ONE GUARD (mirrors `recurso.registrar_indeferimento`): the analista's User
    Task and the coordenacao's SLA-escalation User Task both write `decisao_contas`, so the guard
    reads the decision, not the element that produced it. Every string field is `.strip()`ed before
    the emptiness test — a form that submits `"   "` must refuse exactly like one that submits `""`.
    """
    logger.info(
        "contas.registrar_glosa.start",
        decisao_contas=input_data.decisao_contas,
        analista_id=input_data.analista_id,
    )

    missing: list[str] = []

    decisao = (input_data.decisao_contas or "").strip()
    if decisao not in _DECISOES_QUE_GLOSAM:
        missing.append("decisao_contas not in {GLOSAR, PAGAR_PARCIAL}")
    if not (input_data.justificativa_glosa or "").strip():
        missing.append("justificativa_glosa")
    if not (input_data.codigo_glosa_tiss or "").strip():
        missing.append("codigo_glosa_tiss")

    valor_glosado = _parse_valor_monetario(input_data.valor_glosado_brl)
    if valor_glosado is None or valor_glosado <= 0:
        missing.append("valor_glosado_brl")
    if not (input_data.analista_id or "").strip():
        missing.append("analista_id")

    valor_liberado = _parse_valor_monetario(input_data.valor_liberado_brl)
    # `> 0`, not `>= 0` (DESVIO declarado do desenho de registro, REDESIGN-SP-OP-CONTAS-001.md:471,
    # que escreveu `>= 0`): um "pagamento parcial" que libera R$ 0,00 e, materialmente, um GLOSAR
    # integral — e o desenho ja diz que o unico caminho para glosar tudo e `decisao_contas=GLOSAR`.
    # Aceita-lo aqui registrava a glosa, emitia ao prestador um demonstrativo de "pagamento
    # parcial" que nao paga nada, e depois TRAVAVA a instancia em ST_HandoffPagamentoParcial, que
    # recusa `valor <= 0` (`:1069`) — o efeito adverso materializado com o desfecho inalcancavel.
    # Recusar aqui e fail-closed e mantem as duas metades do mesmo ato consistentes.
    if decisao == "PAGAR_PARCIAL" and (valor_liberado is None or valor_liberado <= 0):
        missing.append("valor_liberado_brl")

    if missing:
        raise ContasGlosaNotHumanError(missing_fields=missing)

    glosa_id = _glosa_id(input_data)

    logger.info(
        "contas.registrar_glosa.complete",
        glosa_id=glosa_id,
        decisao_contas=decisao,
        analista_id=input_data.analista_id,
    )
    return GlosaRegistroResult(
        glosa_id=glosa_id,
        registered=True,
        decisao_contas=decisao,
        valor_glosado_brl=float(valor_glosado or 0.0),
        valor_liberado_brl=float(valor_liberado or 0.0),
    )


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


def _fraude_business_key(tenant_id: str, numero_caso: str) -> str:
    """`FRAUDE-{tenant_id}-{numero_caso}` (contract `SP-OP-FRAUDE-001.md` "Business key
    (idempotencia)") — one active investigation instance per case. IDENTICAL scheme to
    `notification_bridge._fraude_business_key`, so the in-flow worker start and the (decoupled)
    Kafka-bridge start (CONTAS→FRAUDE rule) CONVERGE on the SAME instance (idempotent redundancy,
    never a divergent double-start)."""
    return f"FRAUDE-{tenant_id}-{numero_caso}"


def _fraude_numero_caso_for_handoff(variables: dict[str, Any]) -> str:
    """Resolve the FRAUDE-001 `numero_caso` business-key anchor for the CONTAS→FRAUDE handoff.

    Thin wrapper over the SHARED `base.resolve_fraude_numero_caso` — see that function's
    docstring for the full derivation contract. Delegating (rather than re-implementing) is what
    guarantees a BYTE-IDENTICAL business key with `notification_bridge._fraude_numero_caso_for_
    contas_handoff` for ALL input types (BK convergence is the L0 requirement), not just
    coincidentally-matching logic that can silently drift.
    """
    return resolve_fraude_numero_caso(variables)


def start_fraude(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff (Phase-3 CONTAS→FRAUDE leg): idempotently START SP-OP-FRAUDE-001 when the
    HUMAN analyst decides ``decisao_contas == ENCAMINHAR_FRAUDE`` (BPMN ``GW_DecisaoContas`` ->
    ``ST_StartFraude``, fed only by ``UT_AnalistaContas``/``UT_CoordenacaoContasAssume``).

    L0 HARD (ADR-0005/0008): a fraud referral is NEVER auto-flagged — ``indicio_fraude_sinalizado``
    is informative only; this worker is reached SOLELY downstream of the human ENCAMINHAR_FRAUDE
    decision (exactly like GLOSAR/PAGAR_PARCIAL). CONTAS never investigates nor accuses:
    FRAUDE-001 owns the accusation via its OWN human User Tasks. This worker starts FRAUDE-001
    (business key ``FRAUDE-{tenant}-{numero_caso|prestador_id}``) via the shared,
    business-key-idempotent ``start_process_idempotent`` chokepoint — an already-active
    investigation for this case is returned unchanged (no duplicate case), and the SAME business
    key is derived by the ``notification_bridge`` CONTAS→FRAUDE rule (the completed-event
    re-delivery path) so the two paths CONVERGE, never a divergent double-start.

    T-C2 FENCE (mirrors ``handoff_pagamento``): the chokepoint REQUIRES a durable ``audit_sink`` +
    ``AgentDecisionProvenance`` — the ADR-0007 start record is emitted exactly-once
    (dedup key ``{tenant}:start:SP-OP-FRAUDE-001:{business_key}``) BEFORE any engine effect.
    ``agent_id`` = the stable service identity (``AUDIT_AGENT_ID``), no ``model_id``/
    ``prompt_version`` (no LLM decides — the analyst's ENCAMINHAR_FRAUDE in ``UT_AnalistaContas``
    does), and ``decision_basis`` carries ONLY bounded enum/flag tokens; operational identifiers
    stay in the start ``variables``, bound one-way via the record's ``input_sha256``.

    FAIL-CLOSED (never a silent no-op):
      - missing engine seam (``engine is None``) raises (transient);
      - missing audit seam (``audit_sink is None``) raises (transient) — FRAUDE-001 can never
        start un-audited (ADR-0007 L0);
      - missing/blank/None business-key anchor (``tenant_id``/``prestador_id``) raises
        ``ContasFraudeSemAlvoError`` (deterministic -> incident) — never a start under an
        empty/degenerate business key. Anchors validated with the SHARED ``non_blank`` (the
        bridge's own semantics) — a whitespace-only anchor or an explicit ``None``
        (``str(None) == "None"``) is refused, never minting a degenerate ``FRAUDE-{t}-None`` key.
    """
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    if not (non_blank(variables.get("tenant_id")) and non_blank(variables.get("prestador_id"))):
        logger.error(
            "contas_start_fraude_no_target",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise ContasFraudeSemAlvoError(
            "start_fraude: tenant_id/prestador_id ausente, em branco ou None — nao ha ancora de "
            "business key para iniciar FRAUDE-001 (recusado, nunca inicia com business key "
            "vazia/degenerada)"
        )

    tenant_id = str(variables.get("tenant_id", ""))
    prestador_id = str(variables.get("prestador_id", ""))
    numero_caso = _fraude_numero_caso_for_handoff(variables)
    numero_lote_tiss = str(variables.get("numero_lote_tiss", ""))
    analista_id = str(variables.get("analista_id", ""))
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []
    indicadores_presentes = variables.get("indicadores_presentes", [])
    if not isinstance(indicadores_presentes, list):
        indicadores_presentes = []

    if engine is None:
        logger.error("contas_start_fraude_engine_seam_not_wired", prestador_id=prestador_id)
        raise RuntimeError(
            "start_fraude: engine seam (CibSevenTransport) not wired — cannot start FRAUDE-001; "
            "failing closed to a retry/incident (never a silent no-op)"
        )
    if audit_sink is None:
        logger.error("contas_start_fraude_audit_sink_not_wired", prestador_id=prestador_id)
        raise RuntimeError(
            "start_fraude: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 start "
            "record, so FRAUDE-001 is NOT started (fail-closed, emit-before-effect); failing to a "
            "retry/incident (never a silent no-op, never an un-audited start)"
        )

    business_key = _fraude_business_key(tenant_id, numero_caso)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "origem_encaminhamento": "contas",
        "encaminhado_por_id": analista_id,
        "numero_lote_tiss": numero_lote_tiss,
        "prestador_id": prestador_id,
        "numero_caso": numero_caso,
        "entidade_tipo": "prestador",
        "evidencia_refs": evidencia_refs,
        "indicadores_presentes": indicadores_presentes,
    }
    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "decisao_contas": "ENCAMINHAR_FRAUDE",
            "origem_encaminhamento": "contas",
            "entidade_tipo": "prestador",
        },
        model_id=None,
        prompt_version=None,
    )
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=FRAUDE_PROCESS_KEY,
            business_key=business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )
    logger.info(
        "contas.start_fraude",
        prestador_id=prestador_id,
        numero_caso=numero_caso,
        fraude_business_key=business_key,
        fraude_instance_id=instance.instance_id,
        fraude_already_existed=instance.already_existed,
    )
    return {
        "handoff": FRAUDE_PROCESS_KEY,
        "handoff_executado": True,
        "processo_destino": FRAUDE_PROCESS_KEY,
        "prestador_id": prestador_id,
        "numero_caso": numero_caso,
        "fraude_business_key": business_key,
        "fraude_instance_id": instance.instance_id,
        "fraude_already_existed": instance.already_existed,
    }


def devolver_conta(
    numero_lote_tiss: str,
    numero_guia_tiss: str,
    justificativa_devolucao: str,
    analista_id: str,
    motivo_devolucao: str = "",
) -> dict[str, Any]:
    """Register the return of the conta to the prestador for correction (decisao_contas=DEVOLVER).

    NOT a glosa and NOT an adjudication: no value is denied — the conta goes back so the prestador
    can re-present it, and `Msg_ContasLinhasAtualizadas` re-enters the SAME instance. Mirrors
    `End_PagamentoRecusadoHumano` of SP-OP-PAGTO-001, classified there as
    "neutro->prestador-adjacente ... (NAO e glosa)". Whether a devolucao should nonetheless be
    treated as adverse is ADR-0040 OQ-7 — recorded, not decided here.

    FAIL-CLOSED (ERR_CONTAS_DEVOLUCAO_INVALIDA): refuses a blank `justificativa_devolucao` or a
    blank `analista_id`. A conta that comes back without a stated reason cannot be corrected, and
    an anonymous return has no ADR-0007 attribution.

    TASY write DROP: we consume CDC, never write to Tasy (ADR-0013).
    """
    if not (justificativa_devolucao or "").strip():
        raise ContasDevolucaoInvalidaError(
            "devolver_conta: justificativa_devolucao em branco — o prestador nao tem como corrigir "
            "uma conta devolvida sem motivo declarado (recusado, nunca devolve em silencio)"
        )
    if not (analista_id or "").strip():
        raise ContasDevolucaoInvalidaError(
            "devolver_conta: analista_id em branco — a devolucao sai em nome de quem a decidiu "
            "(ADR-0007); recusado, nunca anonimo"
        )

    logger.info(
        "contas.devolver_conta",
        numero_lote_tiss=numero_lote_tiss,
        numero_guia_tiss=numero_guia_tiss,
        analista_id=analista_id,
    )

    return {
        "devolvida": True,
        "numero_lote_tiss": numero_lote_tiss,
        "numero_guia_tiss": numero_guia_tiss,
        "justificativa_devolucao": justificativa_devolucao.strip(),
        "motivo_devolucao": (motivo_devolucao or "").strip(),
        "analista_id": analista_id.strip(),
    }


#: The two artifacts this process sends the prestador. `tipo_comunicacao` is a
#: `camunda:inputParameter` OF THE CALLING ELEMENT — never inferred from process state — exactly
#: as `recurso.comunicar_resposta` discriminates its four outcomes on one topic.
COMUNICACAO_TIPOS: frozenset[str] = frozenset({"demonstrativo_analise", "devolucao_para_correcao"})

_PROTOCOLO_DEMONSTRATIVO_PREFIX = "DEMONS-"


@dataclass
class DemonstrativoInput:
    """Input for `emitir_demonstrativo` — what the prestador is told, and about which conta."""

    tenant_id: str = ""
    numero_lote_tiss: str = ""
    numero_guia_tiss: str = ""
    prestador_id: str = ""
    competencia: str = ""
    desfecho: str = ""
    valor_apresentado_brl: Any = None
    valor_liberado_brl: Any = None
    valor_glosado_brl: Any = None
    codigo_glosa_tiss: str = ""
    justificativa_glosa: str = ""
    motivo_devolucao: str = ""
    justificativa_devolucao: str = ""
    analista_id: str = ""


def _mint_protocolo_demonstrativo(business_key: str, tipo_comunicacao: str) -> str:
    """`DEMONS-{sha256(business_key|tipo_comunicacao)[:16]}` — DETERMINISTIC, never wall-clock.

    Same determinism discipline as `_glosa_id` and `recurso._mint_protocolo_resposta`: the engine
    re-delivers an external task on lock expiry, and a re-delivery of ONE communication must carry
    the SAME protocol instead of minting a second identity for one act. The purity fence
    (`tests/unit/tools/workers/test_worker_handler_purity.py`) keeps `time`/`uuid`/`random` out of
    this module, and this function is why the constraint is satisfiable here.

    SYNTHETIC, and labelled as such: no real TISS call is made in this phase (TASY write DROP,
    ADR-0013). It is an internal identifier of the communication CONTAS emitted — never a claim
    about any external system's numbering (OQ-1).
    """
    import hashlib

    digest = hashlib.sha256(f"{business_key}|{tipo_comunicacao}".encode()).hexdigest()[:16].upper()
    return f"{_PROTOCOLO_DEMONSTRATIVO_PREFIX}{digest}"


def emitir_demonstrativo(
    input_data: DemonstrativoInput, *, tipo_comunicacao: str, business_key: str = ""
) -> dict[str, Any]:
    """Communicate the result of the conta analysis to the prestador (TISS — DRAFT/verify OQ-1).

    Serves the FIVE tasks that affect the prestador, discriminated by `tipo_comunicacao`:
      `demonstrativo_analise`   -> ST_EmitirDemonstrativoIntegral, ST_EmitirDemonstrativoAprovado,
                                   ST_EmitirDemonstrativo, ST_EmitirDemonstrativoParcial
      `devolucao_para_correcao` -> ST_ComunicarDevolucao
    A `tipo_comunicacao` outside `COMUNICACAO_TIPOS` raises `ContasComunicacaoInvalidaError`
    (audited incident) — NEVER a silent default to the most common type.

    `demonstrativo_analise` carries, per line: valor_apresentado, valor_processado, valor_liberado,
    valor_glosa, codigo_glosa (TISS motive table — DRAFT/verify, OQ-1/OQ-5).
    `devolucao_para_correcao` carries motivo_devolucao, the lines/fields to correct and the
    re-presentation deadline (OQ-1: is it its own TISS artifact, or a demonstrativo? not
    confirmed).

    DOES NOT DECIDE ANYTHING: the worker only transmits a decision already taken — the same stance
    as `auth.send_denial_notice`, which "transmits the auditor's denial, never decides it"
    (ADR-0018). On the TWO AUTOMATIC legs (`has_glosas == false`, `glosa_triage == PAGAR`) there is
    no human decision to transmit: the document says the conta was processed with no divergence, it
    does NOT cite an `analista_id`, and the signature field stays empty. That is deliberate —
    inventing a human author for an automatic outcome would falsify the trail (ADR-0007).
    """
    tipo = (tipo_comunicacao or "").strip()
    if tipo not in COMUNICACAO_TIPOS:
        raise ContasComunicacaoInvalidaError(
            f"emitir_demonstrativo: tipo_comunicacao {tipo_comunicacao!r} nao declarado "
            f"(esperado um de {sorted(COMUNICACAO_TIPOS)}) — recusado; nunca defaulta para o "
            "tipo mais comum"
        )

    key = business_key or f"CONTAS-{input_data.tenant_id}-{input_data.numero_lote_tiss}"
    protocolo = _mint_protocolo_demonstrativo(key, tipo)

    # The automatic legs have no human author. `analista_id` is echoed ONLY when the process
    # variable actually carries one; it is never synthesised.
    analista_id = (input_data.analista_id or "").strip()

    logger.info(
        "contas.emitir_demonstrativo",
        tipo_comunicacao=tipo,
        numero_lote_tiss=input_data.numero_lote_tiss,
        prestador_id=input_data.prestador_id,
        protocolo_demonstrativo=protocolo,
        automatica=not analista_id,
    )

    return {
        "comunicado": True,
        "tipo_comunicacao": tipo,
        "protocolo_demonstrativo": protocolo,
        "numero_lote_tiss": input_data.numero_lote_tiss,
        "numero_guia_tiss": input_data.numero_guia_tiss,
        "prestador_id": input_data.prestador_id,
        "competencia": input_data.competencia,
        "desfecho": input_data.desfecho,
        "analista_id": analista_id,
        "assinatura_analista": analista_id,
        "tiss_transmitido": False,
    }


#: The EXACT set of process variables the handoff seeds into SP-OP-PAGTO-001. Pinned as a
#: frozenset (and asserted by EQUALITY, never `issubset`, in
#: `test_handoff_pagamento_semeia_exatamente_o_conjunto_declarado`) because the invariant this
#: worker has to keep is a NEGATIVE one: the four admissibility booleans PAGTO resolves for itself
#: must NEVER appear here. Same shape as `recurso.HANDOFF_PAGTO_SEEDED_KEYS` — one handoff shape
#: for both chains.
HANDOFF_PAGTO_SEEDED_KEYS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "ordem_pagamento_id",
        "numero_lote_tiss",
        "numero_guia_tiss",
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
#: `lastro_confirmado=true` here would send the order straight into PAGTO's clerical
#: `DENTRO_TETO_L2` lane (`ST_ReleaseLowValue` — no User Task, and NOT guarded by
#: `ERR_PAYMENT_RELEASE_NOT_HUMAN`, which covers only `ST_ReleaseHighValue`).
HANDOFF_PAGTO_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"lastro_confirmado", "dados_pagamento_validos", "duplicidade_suspeita", "dentro_teto_l2"}
)

#: `lastro_origem` values this worker may emit (I-PAGTO-1's enum, CONTAS half). The automatic
#: value is not a euphemism: it says plainly that NO human adjudicated, which is precisely what
#: `UT_AnaliseAdmissibilidade` needs to see.
LASTRO_ORIGEM_AUTOMATICA = "contas_adjudicacao_automatica"
LASTRO_ORIGEM_HUMANA = "contas_adjudicacao_humana"

#: The CLOSED enum this worker may emit as `lastro_origem`. `SP-OP-PAGTO-001.md` declares
#: `lastro_origem` a three-value closed enum that FEEDS THE FORM of `UT_AnaliseAdmissibilidade` —
#: it is what the human reviewer reads. The calling element declares it as a
#: `camunda:inputParameter` (which is why it is read from `variables` at all), but "the caller
#: declares it" is not "the caller may write anything into the reviewer's form": an unknown label,
#: the OTHER chain's value (`recurso_deferimento_humano`) or free text would all reach the human
#: as if it were provenance. `recurso.py` pins its single constant and refuses a blank decisor;
#: this is the CONTAS half of the same rule — one handoff shape for both chains.
LASTROS_ORIGEM_PERMITIDOS: frozenset[str] = frozenset({LASTRO_ORIGEM_AUTOMATICA, LASTRO_ORIGEM_HUMANA})

TIPO_PAGAMENTO_PRESTADOR_REDE = "prestador_rede"

#: Declared source of the amount (M7) — the CALLING ELEMENT states it as a `camunda:inputParameter`;
#: the worker never guesses. `apresentado` is only valid on the legs where nothing was glosado.
FONTE_VALOR: dict[str, str] = {
    "apresentado": "valor_apresentado_brl",
    "liberado": "valor_liberado_brl",
}


def _pagto_business_key(tenant_id: str, numero_lote_tiss: str, prestador_id: str) -> str:
    """`PAGTO-{tenant_id}-{numero_lote_tiss}-{prestador_id}` — ONE order per adjudicated lote.

    NORMALISED THROUGH `agents.andre.keys.key_segment`, the SAME normalisation
    `recurso._pagto_business_key` uses (gate finding M2 on the RECURSO half) — one key shape for
    both chains. That module's own header documents the identical defect for the identical family:
    "a whitespace-padded `ordem_pagamento_id` produced `PAGTO-{t}- 123 ` against the other site's
    `PAGTO-{t}-123`". Without it a re-delivered handoff whose `numero_lote_tiss` arrives with one
    extra space mints a DIFFERENT key, the STRICT dedup claim does not see the previous instance,
    and the same adjudicated conta is paid TWICE. `non_blank` rejects absent/empty/whitespace-only/
    `None` but ACCEPTS whitespace-PADDED, so refusing is not enough on its own.
    Position-preserving: an empty segment is impossible here because the caller refuses first.

    The alternative shape SP-OP-PAGTO-001's own contract already foresees for an adjudicated
    conta, alongside `PAGTO-{tenant}-{ordem_pagamento_id}` and the `PAGTO-{tenant}-{guia}-{glosa}`
    form `recurso.handoff_pagamento` uses for a reverted glosa. Deriving the key from the lote
    identity is what makes a re-delivered handoff (or a second decision on the same lote after a
    `Msg_ContasLinhasAtualizadas` round-trip) converge on the SAME order instead of paying twice;
    SP-OP-PAGTO-001 is the ONE STRICT start-dedup family
    (`mcp_cibseven.transport._START_DEDUP_POLICY`), so that convergence is enforced by a durable
    claim, not by hope.
    """
    return f"PAGTO-{key_segment(tenant_id)}-{key_segment(numero_lote_tiss)}-{key_segment(prestador_id)}"


def _ordem_pagamento_id(tenant_id: str, numero_lote_tiss: str, prestador_id: str) -> str:
    """Deterministic id of the payment order this handoff MINTS (there is no pre-existing one).

    Same determinism discipline as `_glosa_id`: a re-delivered external task must mint the
    IDENTICAL id, never a fresh identity for one adjudication. It is an internal identifier of the
    order CONTAS creates — not a claim about any external system's numbering.

    Composed through the SAME `key_segment` normalisation as `_pagto_business_key` (M2): the order
    id and the key it travels with must never disagree about what the identity of the conta is.
    """
    return f"ORDEM-CONTA-{key_segment(tenant_id)}-{key_segment(numero_lote_tiss)}-{key_segment(prestador_id)}"


def handoff_pagamento(
    variables: dict[str, Any],
    *,
    fonte_valor: str = "",
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff: idempotently START SP-OP-PAGTO-001 for the conta the payer adjudicated.

    NOT an adverse effect and NOT a release of money — SP-OP-PAGTO-001 owns the release, behind
    its own alcada ladder AND (invariant **I-PAGTO-1**) behind the mandatory human
    `UT_AnaliseAdmissibilidade`. Reached from three elements: `ST_HandoffPagamentoAuto` (the two
    automatic legs), `ST_HandoffPagamentoHumano` (`decisao_contas == PAGAR`) and
    `ST_HandoffPagamentoParcial` (`decisao_contas == PAGAR_PARCIAL`).

    **I-PAGTO-1, mechanically.** The seeded variable set is PINNED (`HANDOFF_PAGTO_SEEDED_KEYS`)
    and the four admissibility booleans (`HANDOFF_PAGTO_FORBIDDEN_KEYS`) are NEVER among them.
    Consequence, by construction: `pagto_admissibility` reads `lastro_confirmado` absent =>
    `False` => row `r_sem_lastro` => `ANALISE_HUMANA` => `UT_AnaliseAdmissibilidade`
    (`coordenacao-financeira`). This is the REAL gate of this path — not
    `ERR_PAYMENT_RELEASE_NOT_HUMAN`, which guards only `ST_ReleaseHighValue` and which this path
    never reaches. In place of the facts the worker seeds EVIDENCE: `lastro_origem` and
    `lastro_decisor_id` (the `analista_id`; the EMPTY STRING on the automatic leg — never invented).

    **Source of the amount (M7).** `fonte_valor` is a `camunda:inputParameter` OF THE CALLING
    ELEMENT, not a guess:
      `apresentado` -> `valor_apresentado_brl` (automatic legs: nothing was glosado, so the amount
                       owed IS the presented one)
      `liberado`    -> `valor_liberado_brl`    (human legs: the User Task's own field)
    REFUSES (`ContasHandoffPagamentoInvalidoError` -> audited incident) when `fonte_valor` is not
    declared, when the source variable is absent/blank/non-numeric, or when the resolved amount is
    `<= 0`. The last case is REAL, not theoretical (see `_parse_valor_monetario`).

    **`data_vencimento`** is `sim` in `SP-OP-PAGTO-001.md`. It is NOT invented and NOT defaulted
    here: it is a contract input of CONTAS (from the lote / contractual term — DRAFT/verify,
    ADR-0040 OQ-2) and the handoff REFUSES it blank. An order with no due date is malformed;
    refusing produces a visible incident, defaulting would produce a false deadline.

    **Mechanics.** Mirrors `start_fraude`: passes through the `start_process_idempotent`
    chokepoint (T-C2 fence), REQUIRES `engine` + `audit_sink` (an un-audited PAGTO-001 start is
    structurally impossible, ADR-0007 L0), and REFUSES fail-closed when any business-key anchor is
    blank — via `non_blank`, never `str(...)` truthiness (EB-4 R1: explicit `None` must refuse,
    not stringify to a truthy `"None"`).

    ATTENTION — SP-OP-PAGTO-001 is the STRICT dedup family
    (`mcp_cibseven/transport._START_DEDUP_POLICY`): the chokepoint additionally demands a
    `DedupReportingAuditSink` + `HistoryQueryingTransport` and raises
    `StartDedupGateUnavailableError` if either is missing. Fail-closed by construction: a
    mis-wired handoff fails, it never pays twice.

    `competencia`, `conta_origem_ref` and `instrumento_pagamento` are PAGTO-required inputs CONTAS
    ECHOES VERBATIM from process scope and leaves BLANK when the lote did not carry them — never
    filled with a plausible-looking value. `UT_AnaliseAdmissibilidade` is where a human resolves
    them, which is exactly the lane I-PAGTO-1 guarantees the order lands in.
    """
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    # NORMALISED (M2, mirroring the RECURSO half) with the same `key_segment` the composers use,
    # so the identity the PAYLOAD and the LOGS carry is byte-identical to the identity the STRICT
    # dedup key is minted from — a padded `numero_lote_tiss` must not travel to PAGTO alongside a
    # key that stripped it. `non_blank` alone is not enough on either side: it ACCEPTS
    # whitespace-padding, and it ACCEPTS `0` (whose `key_segment` is `""`), so the emptiness is
    # re-checked after normalising.
    tenant_id = key_segment(variables.get("tenant_id"))
    numero_lote_tiss = key_segment(variables.get("numero_lote_tiss"))
    prestador_id = key_segment(variables.get("prestador_id"))
    if not (
        non_blank(variables.get("tenant_id"))
        and non_blank(variables.get("numero_lote_tiss"))
        and non_blank(variables.get("prestador_id"))
        and tenant_id
        and numero_lote_tiss
        and prestador_id
    ):
        logger.error(
            "contas_handoff_pagamento_sem_ancora",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise ContasHandoffPagamentoInvalidoError(
            "handoff_pagamento: tenant_id/numero_lote_tiss/prestador_id ausente, em branco, None "
            "ou vazio apos normalizacao — nao ha ancora de business key para iniciar PAGTO-001 "
            "(recusado, nunca inicia com business key vazia/degenerada)"
        )

    fonte = (fonte_valor or str(variables.get("fonte_valor") or "")).strip()
    if fonte not in FONTE_VALOR:
        logger.error("contas_handoff_pagamento_fonte_valor_desconhecida", fonte_valor=fonte)
        raise ContasHandoffPagamentoInvalidoError(
            f"handoff_pagamento: fonte_valor {fonte_valor!r} nao declarada "
            f"(esperado um de {sorted(FONTE_VALOR)}) — o elemento chamador declara a fonte; "
            "o worker NUNCA adivinha de qual variavel o valor vem"
        )

    variavel_valor = FONTE_VALOR[fonte]
    valor = _parse_valor_monetario(variables.get(variavel_valor))
    if valor is None or valor <= 0:
        logger.error(
            "contas_handoff_pagamento_valor_invalido",
            numero_lote_tiss=numero_lote_tiss,
            fonte_valor=fonte,
        )
        raise ContasHandoffPagamentoInvalidoError(
            f"handoff_pagamento: {variavel_valor} ausente, em branco, nao-numerico ou <= 0 "
            f"(fonte_valor={fonte}) — recusado; o valor NUNCA e defaultado para 0"
        )

    data_vencimento = str(variables.get("data_vencimento") or "").strip()
    if not data_vencimento:
        logger.error("contas_handoff_pagamento_sem_vencimento", numero_lote_tiss=numero_lote_tiss)
        raise ContasHandoffPagamentoInvalidoError(
            "handoff_pagamento: data_vencimento em branco — obrigatoria em SP-OP-PAGTO-001 e vinda "
            "do lote/termo contratual (OQ-2, DRAFT/verify); recusado, nunca defaultada"
        )

    if engine is None:
        logger.error("contas_handoff_pagamento_engine_seam_not_wired", numero_lote_tiss=numero_lote_tiss)
        raise RuntimeError(
            "handoff_pagamento: engine seam (CibSevenTransport) not wired — cannot start "
            "PAGTO-001; failing closed to a retry/incident (never a silent no-op)"
        )
    if audit_sink is None:
        logger.error("contas_handoff_pagamento_audit_sink_not_wired", numero_lote_tiss=numero_lote_tiss)
        raise RuntimeError(
            "handoff_pagamento: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 "
            "start record, so PAGTO-001 is NOT started (fail-closed, emit-before-effect); failing "
            "to a retry/incident (never a silent no-op, never an un-audited start)"
        )

    lastro_decisor_id = key_segment(variables.get("analista_id"))
    lastro_origem = str(variables.get("lastro_origem") or "").strip() or (
        LASTRO_ORIGEM_HUMANA if lastro_decisor_id else LASTRO_ORIGEM_AUTOMATICA
    )
    if lastro_origem not in LASTROS_ORIGEM_PERMITIDOS:
        logger.error(
            "contas_handoff_pagamento_lastro_origem_desconhecida",
            numero_lote_tiss=numero_lote_tiss,
            lastro_origem=lastro_origem,
        )
        raise ContasHandoffPagamentoInvalidoError(
            f"handoff_pagamento: lastro_origem {lastro_origem!r} fora do dominio declarado "
            f"{sorted(LASTROS_ORIGEM_PERMITIDOS)} — `lastro_origem` alimenta o formulario de "
            "UT_AnaliseAdmissibilidade em SP-OP-PAGTO-001 (e o que o humano LE como proveniencia); "
            "um rotulo desconhecido, o da outra cadeia ou texto livre seria evidencia forjada. "
            "Recusado, nunca ecoado"
        )
    # The pair has to be internally consistent, in BOTH directions, or the reviewer reads a
    # contradiction as provenance: "adjudicated by a human" with no human named, or "adjudicated
    # automatically" carrying a human's id. Neither is repaired by guessing — both are refused.
    if lastro_origem == LASTRO_ORIGEM_HUMANA and not lastro_decisor_id:
        logger.error("contas_handoff_pagamento_humano_sem_decisor", numero_lote_tiss=numero_lote_tiss)
        raise ContasHandoffPagamentoInvalidoError(
            f"handoff_pagamento: lastro_origem={LASTRO_ORIGEM_HUMANA} sem analista_id — uma ordem "
            "declarada como adjudicada por humano NAO pode sair sem o decisor identificado "
            "(ADR-0007; `lastro_decisor_id` e a EVIDENCIA que UT_AnaliseAdmissibilidade le). "
            "Recusado, nunca preenchido com um valor plausivel"
        )
    if lastro_origem == LASTRO_ORIGEM_AUTOMATICA and lastro_decisor_id:
        logger.error("contas_handoff_pagamento_automatica_com_decisor", numero_lote_tiss=numero_lote_tiss)
        raise ContasHandoffPagamentoInvalidoError(
            f"handoff_pagamento: lastro_origem={LASTRO_ORIGEM_AUTOMATICA} com analista_id "
            f"{lastro_decisor_id!r} — a perna automatica NAO tem decisor humano (documentacao de "
            "ST_HandoffPagamentoAuto: `lastro_decisor_id` VAZIO, nunca inventado). Carregar um id "
            "aqui diria ao revisor de PAGTO que um humano adjudicou quando nenhum adjudicou. "
            "Recusado"
        )
    business_key = _pagto_business_key(tenant_id, numero_lote_tiss, prestador_id)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "ordem_pagamento_id": _ordem_pagamento_id(tenant_id, numero_lote_tiss, prestador_id),
        "numero_lote_tiss": numero_lote_tiss,
        "numero_guia_tiss": str(variables.get("numero_guia_tiss", "")),
        "prestador_id": prestador_id,
        "tipo_pagamento": TIPO_PAGAMENTO_PRESTADOR_REDE,
        "valor_pagamento_cents": _to_cents(valor),
        "moeda": "BRL",
        "competencia": str(variables.get("competencia", "")),
        "data_vencimento": data_vencimento,
        "conta_origem_ref": str(variables.get("conta_origem_ref", "")),
        "instrumento_pagamento": str(variables.get("instrumento_pagamento", "")),
        # Declared source of the amount (M7) — never inferred downstream.
        "fonte_valor": fonte,
        # I-PAGTO-1 evidence, NOT the fact.
        "lastro_origem": lastro_origem,
        "lastro_decisor_id": lastro_decisor_id,
    }
    # Defensive, and the reason the set is pinned: an editor who adds a key must see this fail
    # rather than discover it in production as a skipped human admissibility review.
    if set(payload) != HANDOFF_PAGTO_SEEDED_KEYS:
        raise ContasHandoffPagamentoInvalidoError(
            "handoff_pagamento: o conjunto semeado divergiu de HANDOFF_PAGTO_SEEDED_KEYS "
            f"(a mais: {sorted(set(payload) - HANDOFF_PAGTO_SEEDED_KEYS)}; "
            f"a menos: {sorted(HANDOFF_PAGTO_SEEDED_KEYS - set(payload))}) — I-PAGTO-1"
        )

    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "lastro_origem": lastro_origem,
            "tipo_pagamento": TIPO_PAGAMENTO_PRESTADOR_REDE,
            "fonte_valor": fonte,
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
        "contas.handoff_pagamento",
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        pagto_business_key=business_key,
        pagto_instance_id=instance.instance_id,
        pagto_already_existed=instance.already_existed,
    )
    return {
        "handoff": PAGTO_PROCESS_KEY,
        "handoff_executado": True,
        "processo_destino": PAGTO_PROCESS_KEY,
        "ordem_pagamento_id": payload["ordem_pagamento_id"],
        "numero_lote_tiss": numero_lote_tiss,
        "prestador_id": prestador_id,
        "tipo_pagamento": TIPO_PAGAMENTO_PRESTADOR_REDE,
        "valor_pagamento_cents": payload["valor_pagamento_cents"],
        "fonte_valor": fonte,
        "lastro_origem": lastro_origem,
        "lastro_decisor_id": lastro_decisor_id,
        "pagto_business_key": business_key,
        "pagto_instance_id": instance.instance_id,
        "pagto_already_existed": instance.already_existed,
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

#: The two human outcomes that APPLY a glosa against the prestador. Exactly the set
#: `registrar_glosa` guards; a payer decision outside it must never reach the adverse worker.
_DECISOES_QUE_GLOSAM: frozenset[str] = frozenset({"GLOSAR", "PAGAR_PARCIAL"})


def _parse_valor_monetario(value: Any) -> float | None:
    """Coerce a monetary process variable to a float for the glosa/handoff guards.

    THE SAME FAIL-CLOSED IDIOM AS `recurso._parse_valor_monetario`, kept verbatim in shape so the
    two chains behave identically at the same boundary. Root cause: the engine seeds these fields
    as a Camunda ``String`` (e.g. ``"150.00"`` — `EngineRest._to_camunda_vars` has no float branch,
    so it falls through to the String catch-all); harness ``_from_camunda_var`` decodes it back to
    a Python ``str`` and ``pick_fields`` performs NO coercion. A raw ``<= 0`` comparison then
    raises ``TypeError: '<=' not supported between instances of 'str' and 'int'``.

    Returns ``None`` for a missing/blank/non-numeric value so the caller FAILS CLOSED (a missing
    required field -> `ContasGlosaNotHumanError` / `ContasHandoffPagamentoInvalidoError`). NEVER
    silently defaults to ``0`` — a wrong monetary decision on a conta is a financial defect, and
    the ``0.0`` case is REAL, not theoretical: `marina/graph.py` seeds
    ``float(state.get("valor_apresentado_brl", 0.0))``, so a state missing the field produces
    ``0.0``, and ``0`` cents would sail through PAGTO's `r_dentro_teto_l2` (``0 <= 10000000``).

    DUPLICATED, NOT SHARED, deliberately: hoisting it into `workers/base.py` would couple two
    worker modules through a third at the exact moment both are being rebuilt. Consolidation is a
    named follow-up, not a silent refactor inside a perspective rebuild.
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
    truncating it would shave a cent off a real payment. Every threshold stays in DMN
    (`contas_sla`'s alto-valor ``> 50000.0``, `pagto_alcada`'s teto) — this function never
    compares, only converts.
    """
    return int(round(brl * 100))


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


#: Field order of the `glosa_id` digest. FROZEN — reordering or adding a field changes every id
#: this worker has ever minted, which would orphan any `RECURSO-{tenant}-{guia}-{glosa_id}` anchor
#: already in flight. Append-only, and only with a migration story.
_GLOSA_ID_DIGEST_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "numero_lote_tiss",
    "numero_guia_tiss",
    "decisao_contas",
    "codigo_glosa_tiss",
    "valor_glosado_brl",
    "analista_id",
)


def _glosa_id(input_data: GlosaRegistroInput) -> str:
    """`GLOSA-{analista_id}-{sha256(contract facts)[:12]}` — DETERMINISTIC (M-9).

    WAS `GLOSA-{analista}-{sha256(time.time_ns())[:12]}`. That made this worker a P1 (handler
    purity) violation: the engine re-delivers an external task on lock-expiry / failure-with-
    retries, and every re-delivery of the SAME human decision minted a DIFFERENT `glosa_id` — a
    fresh identity for one decision. `tests/unit/tools/workers/test_worker_handler_purity.py`
    (`test_contas_glosa_id_m9_landed_deterministic`) pins the fixed state.

    THE SCOPE OF THE HAZARD CHANGED WITH ADR-0040, AND IT CHANGED FOR THE BETTER. Before the
    perspective rebuild, `glosa_id` was minted here on a TERMINAL branch and the identity that
    SP-OP-RECURSO-001 keyed on came from OUTSIDE (the analyst's form / the origin system) — which
    is why the earlier revision of this docstring called the drift "latent". Now the identifier is
    ALWAYS minted HERE, inside the operadora, on the branch that actually applies the glosa, and it
    is published in `agents.events.contas.completed` and printed on the demonstrativo: it is the
    identity the prestador cites when it files a recurso. So determinism is no longer a hygiene
    property — it is what makes a re-delivered task refer to the SAME glosa.

    *A consequence is now AVAILABLE and deliberately NOT taken here:* SP-OP-RECURSO-001 is
    classified NON-strict in `mcp_cibseven.transport._START_DEDUP_POLICY` precisely because its
    anchoring `glosa_id` used to be externally supplied. Reclassifying it to STRICT is a separate
    decision with its own gate consequences and is NOT proposed by this rebuild — it is recorded
    as available.

    HOW THE IDENTITY IS SCOPED, AND WHY THAT IS SOUND RATHER THAN INVENTED. There is NO per-glosa
    instance identifier anywhere in the contract facts: `codigo_glosa_tiss` is a TISS MOTIVE CODE
    (a class token, reusable across lines), and `UT_AnalistaContas` records a single aggregate
    decision — one codigo, one valor, one justificativa — with no reference to which glosa LINE it
    applies to. So this id does NOT claim to identify "a glosa line"; it identifies THIS
    ADJUDICATION, and it is scoped by the process instance that owns it. That scope is sufficient
    because both adverse branches are TERMINAL (`Flow_GWDec_Glosar` and `Flow_GWDec_PagarParcial`
    each run registrar -> emitir -> publish -> end, and neither loops back), so at most ONE
    adjudication exists per CONTAS-001 instance, and the instance anchors (tenant + lote + guia)
    separate any two adjudications that are genuinely distinct.

    `decisao_contas` IS in the digest (it was not before, because there was only one guarded
    outcome): `GLOSAR` and `PAGAR_PARCIAL` on otherwise identical facts are two DIFFERENT
    adjudications and must not alias to one id.

    Determinism holds because every digest input is either a process variable or a completed User
    Task output — the engine replays both unchanged on re-delivery. Blank anchors degrade the
    SCOPE, never the determinism (a degenerate instance with no lote and no guia could not have had
    a business key either); they are deliberately NOT guarded here, because widening this L0
    worker's refusal set is a decision for the humans who own the guard, not a side effect of an
    idempotency fix.

    `analista_id` stays in the CLEAR prefix (ADR-0007 attribution: the decision is in the analyst's
    name) and is ALSO inside the digest, so two analysts deciding identical facts never collide.
    """
    import hashlib

    raw = "|".join(
        f"{field}={_digest_token(getattr(input_data, field, ''))}" for field in _GLOSA_ID_DIGEST_FIELDS
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"GLOSA-{input_data.analista_id}-{digest}"


def _digest_token(value: Any) -> str:
    """Canonical string form of a digest input — numerically STABLE across engine round-trips.

    A monetary amount can reach a handler as `150` on one delivery and `150.0` on the next
    (engine `Double`/`Long` decoding, the same int/float boundary `_to_camunda_vars` handles in
    the other direction). Naive `str()` would then digest to two different ids for one decision —
    reintroducing exactly the drift this derivation exists to remove. Every real number is
    therefore rendered through `float`, and `bool` is excluded (it is an `int` subclass, and
    `True`/`1.0` must not alias).
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return repr(float(value))
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`) — EXACT 1:1 name
# match for the fenced/handoff functions:
#   identify_glosa, analyze_reason, calculate_impact, prepare_triage_dossier,
#   registrar_glosa (GUARDED), emitir_demonstrativo, devolver_conta, notify_sla_risk,
#   handoff_pagamento (CONTAS→PAGTO leg), start_fraude (Phase-3 CONTAS→FRAUDE leg).
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


def _normalize_data_recebimento_lote(raw: Any) -> tuple[str, bool]:
    """Normalise the payer's own receipt date to ISO `YYYY-MM-DD`; report whether it defaulted.

    GAP-CONTAS-4 / ENGINE-16004. `contas_sla` computes the ABSOLUTE `timeDate` deadlines of
    `BT_SlaAnaliseContas`/`BT_AlertaSlaContas` from this single anchor, and a FEEL expression that
    sees `null`/`""` does not produce a late timer — it produces an unresolvable one. When the
    field arrives absent/blank/malformed the intake defaults FAIL-SAFE to TODAY/UTC and the caller
    LOGS A WARNING; it never invents a plausible PAST date, which would silently shorten a
    contractual deadline the operadora owes.

    `recurso._normalize_data_recebimento` is the twin of this function and its docstring already
    named this one as the original — the behaviour just did not exist here yet.
    """
    text = str(raw or "").strip()
    if text:
        # Accept a full ISO datetime by keeping only the calendar date the DMN concatenates
        # "T00:00:00" onto.
        candidate = text.split("T", 1)[0]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")  # noqa: DTZ007 — calendar date, not an instant
        except ValueError:
            pass
        else:
            return candidate, False
    return datetime.now(UTC).strftime("%Y-%m-%d"), True


def identify_glosa_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.identify_glosa` -> `identify_glosa`.

    This is the INTAKE: `ST_ApurarDivergencias` is the first worker on EVERY path (including the
    `BME_LinhasAtualizadas` re-entry), which is why the two DATE fields of the lote are resolved
    HERE and written back as process variables — exactly as the BPMN documentation of that element
    already declared, and as `recurso.validate_recurso` does on its own chain:

    * `data_recebimento_lote` — the SLA anchor. Normalised, and defaulted FAIL-SAFE to today/UTC
      with a warning when the lote does not carry it (never a fabricated past date).
    * `data_vencimento` — the payment obligation's due date, read from the lote/termo contratual
      (origin DRAFT/verify, ADR-0040 OQ-2). ECHOED verbatim (stripped), **never defaulted**: an
      invented due date is a false deadline, and `handoff_pagamento` refuses it blank. What the
      intake adds is VISIBILITY: the absence is logged at the first task of the process, in the
      audit trail, instead of only surfacing four tasks later as a stalled handoff nobody expected.
      MAJOR-1 of VERIFY-PR4-CONTAS is precisely that nothing in the process produced this field.
    """
    del kafka  # unused — identify_glosa emits no domain event
    input_data = _build_glosa_input(variables)
    result = identify_glosa(input_data)
    out = dataclasses.asdict(result)

    data_recebimento, defaulted = _normalize_data_recebimento_lote(input_data.data_recebimento_lote)
    if defaulted:
        logger.warning(
            "contas.identify_glosa.data_recebimento_lote_defaultada",
            tenant_id=input_data.tenant_id,
            numero_lote_tiss=input_data.numero_lote_tiss,
            recebido=str(input_data.data_recebimento_lote or ""),
            usada=data_recebimento,
        )
    out["data_recebimento_lote"] = data_recebimento

    data_vencimento = str(input_data.data_vencimento or "").strip()
    if not data_vencimento:
        logger.warning(
            "contas.identify_glosa.sem_data_vencimento",
            tenant_id=input_data.tenant_id,
            numero_lote_tiss=input_data.numero_lote_tiss,
        )
    out["data_vencimento"] = data_vencimento
    return out


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


def registrar_glosa_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.registrar_glosa` -> `registrar_glosa` (GUARDED).

    Raises `ContasGlosaNotHumanError` (fail-closed, ERR_CONTAS_GLOSA_NOT_HUMAN) when the human
    decision (`decisao_contas in {GLOSAR, PAGAR_PARCIAL}` + required fields) is missing. Serves
    BOTH adverse elements — `ST_RegistrarGlosa` and `ST_RegistrarGlosaParcial` — on one topic; the
    outcome is read from `decisao_contas`, never from which element called.
    """
    del kafka  # unused — registrar_glosa emits no domain event itself
    input_data = GlosaRegistroInput(**pick_fields(variables, GlosaRegistroInput))
    result = registrar_glosa(input_data)
    return dataclasses.asdict(result)


def emitir_demonstrativo_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.emitir_demonstrativo` -> `emitir_demonstrativo`.

    `tipo_comunicacao` comes from the CALLING ELEMENT's `camunda:inputParameter`, which the engine
    merges into the task's variables — it is read here, never inferred from the outcome. A missing
    or undeclared value raises `ContasComunicacaoInvalidaError` rather than defaulting.
    """
    del kafka  # unused — emitir_demonstrativo emits no Kafka event; the ST_Publish* task does
    input_data = DemonstrativoInput(**pick_fields(variables, DemonstrativoInput))
    business_key = str(variables.get("business_key", ""))
    return emitir_demonstrativo(
        input_data,
        tipo_comunicacao=str(variables.get("tipo_comunicacao", "")),
        business_key=business_key,
    )


def devolver_conta_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.devolver_conta` -> `devolver_conta`."""
    del kafka  # unused — devolver_conta emits no domain event
    return devolver_conta(
        str(variables.get("numero_lote_tiss", "")),
        str(variables.get("numero_guia_tiss", "")),
        str(variables.get("justificativa_devolucao") or ""),
        str(variables.get("analista_id") or ""),
        str(variables.get("motivo_devolucao") or ""),
    )


def handoff_pagamento_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.handoff_pagamento` -> `handoff_pagamento`.

    Threads the fenced-start seams (`engine`/`audit_sink`) — the handoff REALLY starts
    SP-OP-PAGTO-001 through the ADR-0007 chokepoint, and PAGTO is the STRICT dedup family, so the
    chokepoint additionally demands a dedup-reporting sink + history-querying transport.
    """
    del kafka  # unused — handoff_pagamento starts a process, it does not publish a Kafka event
    return handoff_pagamento(
        variables,
        fonte_valor=str(variables.get("fonte_valor", "")),
        engine=engine,
        audit_sink=audit_sink,
    )


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


def start_fraude_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.contas.start_fraude` -> `start_fraude` (Phase-3 leg).

    Threads the fenced-start seams (`engine`/`audit_sink`) — start_fraude REALLY starts
    SP-OP-FRAUDE-001 through the ADR-0007 chokepoint, converging on the SAME business key the
    notification_bridge CONTAS→FRAUDE rule derives (never a divergent double-start).
    """
    del kafka  # unused — start_fraude starts a process, it does not publish a Kafka event
    return start_fraude(variables, engine=engine, audit_sink=audit_sink)


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
    """Register the SP-OP-CONTAS-001 dict-boundary entry functions on `harness` — 11 topics.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale. `dmn` (ADR-0028 §1 seam) is
    threaded into `analyze_reason_entry` (`glosa_reason_normalization`) and
    `prepare_triage_dossier_entry` (`glosa_triage`, T1.5 cutover).
    """
    dmn = seams.get("dmn")
    # Fenced-start seams (T-C2, mirrors register_inadimplencia_workers) — threaded into
    # `handoff_pagamento` and `start_fraude`, the two CONTAS workers that run
    # `start_process_idempotent`. ABSENT (`None`) -> they RAISE before any engine effect (an
    # un-audited start is structurally impossible, ADR-0007 L0). SP-OP-PAGTO-001 is additionally
    # the STRICT dedup family, so the chokepoint demands a DedupReportingAuditSink +
    # HistoryQueryingTransport for `handoff_pagamento` and refuses without them. In the live
    # daemon: FreshClientCibSevenTransport + FreshSinkAuditEmitter (loop-agnostic, sync dispatch
    # emits on its own asyncio.run loop).
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
            "operadora.contas.registrar_glosa",
            functools.partial(registrar_glosa_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.emitir_demonstrativo",
            functools.partial(emitir_demonstrativo_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.devolver_conta", functools.partial(devolver_conta_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.notify_sla_risk", functools.partial(notify_sla_risk_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.handoff_pagamento",
            functools.partial(handoff_pagamento_entry, kafka=kafka, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.contas.start_fraude",
            functools.partial(start_fraude_entry, kafka=kafka, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.contas.publish", functools.partial(publish_entry, kafka=kafka))
    )
