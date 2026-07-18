"""Worker: inadimplencia (SP-OP-INADIMPLENCIA-001).

Suspensao/Rescisao por Inadimplencia.
Guard: ERR_CONTRACT_SUSPENSION_NOT_HUMAN (L0-hard, ADR-0018).
NAO ha worker de rescisao gated AQUI — rescisao e propriedade de CANCEL-001.
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_CONTRACT_SUSPENSION_NOT_HUMAN = "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"
ERR_INAD_INVALID_CONTRATO = "ERR_INAD_INVALID_CONTRATO"

# Human decision value that allows suspension
DECISAO_SUSPENDER = "SUSPENDER"
DECISAO_ENCAMINHAR_RESCISAO = "ENCAMINHAR_RESCISAO"
DECISAO_MANTER = "MANTER"

# Process key of the sole owner of the contract-termination (rescisao) terminal — SP-OP-CANCEL-001
# holds End_ContratoRescindido, human-gated by UT_AnaliseRescisao (docs/processes/
# harmonization-inadimplencia-cancel.md §1). INADIMPLENCIA-001 NEVER rescinds; it hands off.
CANCEL_PROCESS_KEY = "SP-OP-CANCEL-001"


# ---------------------------------------------------------------
# Anti-dupla-terminacao — cross-process CANCEL-001 correlation query (GAP-INAD-1)
# ---------------------------------------------------------------


def _cancel_business_key(tenant_id: str, numero_contrato: str, matricula_beneficiario: str) -> str:
    """Business key of the SP-OP-CANCEL-001 instance that would be terminating THIS contract.

    Same identity scheme both processes derive (docs/processes/harmonization-inadimplencia-cancel.md
    §1): a DISTINCT prefix (``CANCEL-`` vs INADIMPLENCIA's ``INAD-``) over the SAME contract identity,
    falling back to ``matricula_beneficiario`` when there is no contract number (individual/familiar
    plans) — mirrors ``fernando.graph._business_key`` exactly. An ACTIVE instance under this key means
    a rescisao/suspensao is already in flight in CANCEL-001 for this same ``contract_termination``.
    """
    contrato = numero_contrato or matricula_beneficiario
    return f"CANCEL-{tenant_id}-{contrato}"


def _query_ja_em_rescisao_cancel(
    engine: CibSevenTransport | None,
    *,
    tenant_id: str,
    numero_contrato: str,
    matricula_beneficiario: str,
) -> bool:
    """REAL cross-process query: is a live SP-OP-CANCEL-001 instance already terminating this contract?

    Anti-dupla-terminacao (GAP-INAD-1, docs/processes/harmonization-inadimplencia-cancel.md §1): keys
    the engine by ``CANCEL-{tenant}-{contrato}`` (the CANCEL-001 business key — the process that OWNS
    the rescisao terminal) via the same ``CibSevenTransport.find_active_instance`` seam the agent graphs
    use for start-time idempotency. Read-only (TASY write DROP, ADR-0013); a FACT, never a decision.

    FAIL CLOSED (ADR-0018, defense in depth): returns ``True`` (BLOCK the suspension) whenever the
    correlation CANNOT be confirmed — no engine seam wired, no contract identity to key on, or any
    transport/engine error. "Inability to decide = do not suspend": the guard NEVER presumes the
    absence of an in-flight rescisao it could not positively rule out. Returns ``False`` (suspension
    may proceed) ONLY when the engine positively confirms no active CANCEL-001 instance exists.
    """
    contrato = numero_contrato or matricula_beneficiario
    cancel_business_key = _cancel_business_key(tenant_id, numero_contrato, matricula_beneficiario)

    if not contrato:
        # No contract identity -> cannot key the correlation query -> cannot rule out a rescisao.
        logger.warning(
            "inadimplencia_cancel_correlation_unavailable",
            reason="contract_identity_missing",
            tenant_id=tenant_id,
        )
        return True

    if engine is None:
        # No engine seam injected -> the cross-process correlation cannot be confirmed: fail closed.
        logger.warning(
            "inadimplencia_cancel_correlation_unavailable",
            reason="engine_seam_not_wired",
            cancel_business_key=cancel_business_key,
        )
        return True

    try:
        instance = asyncio.run(engine.find_active_instance(cancel_business_key))
    except Exception as exc:  # noqa: BLE001 — engine/transport error -> fail closed (block, route human).
        logger.warning(
            "inadimplencia_cancel_correlation_query_failed",
            error=str(exc),
            cancel_business_key=cancel_business_key,
        )
        return True

    return instance is not None


# ---------------------------------------------------------------
# resolve_facts — pre-resolve facts (arithmetic + cross-process query, no decision)
# ---------------------------------------------------------------


def resolve_facts(variables: dict[str, Any], *, engine: CibSevenTransport | None = None) -> dict[str, Any]:
    """Pre-resolve factual variables: mes count, value, period, purge window, rescisao correlation.

    This worker computes factual data — it NEVER makes an adverse decision.
    All monetary values in integer cents.

    ``ja_em_rescisao_cancel`` is the anti-dupla-terminacao correlation RESOLVED HERE (GAP-INAD-1) via
    a REAL read-only query to the engine (``engine`` seam, ``CibSevenTransport.find_active_instance``):
    does a live SP-OP-CANCEL-001 instance already terminate this ``{tenant}-{contrato}``? Before this
    fix nothing populated the variable — the ``register_contract_suspension`` guard read a value no
    worker ever set (a dead guard). The FACT is returned to the engine as an output variable the
    harness carries on ``complete`` so the downstream suspension guard reads a real value.

    FAIL CLOSED (ADR-0018): when the query cannot be answered (no ``engine`` seam wired, missing
    contract identity, or a transport/engine error) ``ja_em_rescisao_cancel := True`` — the guard
    downstream then REFUSES the suspension and routes to a human. See ``_query_ja_em_rescisao_cancel``.
    """
    competencias = variables.get("competencias_em_aberto", [])
    meses_inadimplencia = len(competencias) if isinstance(competencias, list) else 0

    valor_total = variables.get("valor_total_devido_cents", 0)
    if not isinstance(valor_total, int):
        valor_total = int(valor_total) if valor_total else 0

    # Periodo minimo: DRAFT 60 dias (2 meses)
    dentro_periodo_minimo = meses_inadimplencia >= 2

    # Janela de purga: 10 dias apos notificacao (DRAFT/verify RN 593)
    dentro_janela_purga = variables.get("dentro_janela_purga", True)

    notificacao_previa_feita = variables.get("notificacao_previa_feita", False)

    # REAL cross-process query (anti-dupla-terminacao) — replaces the pure pass-through. Fail closed.
    ja_em_rescisao_cancel = _query_ja_em_rescisao_cancel(
        engine,
        tenant_id=str(variables.get("tenant_id", "")),
        numero_contrato=str(variables.get("numero_contrato", "")),
        matricula_beneficiario=str(variables.get("matricula_beneficiario", "")),
    )

    logger.info(
        "inadimplencia_resolve_facts",
        meses_inadimplencia=meses_inadimplencia,
        valor_total_devido_cents=valor_total,
        dentro_periodo_minimo=dentro_periodo_minimo,
        notificacao_previa_feita=notificacao_previa_feita,
        ja_em_rescisao_cancel=ja_em_rescisao_cancel,
    )

    return {
        "meses_inadimplencia": meses_inadimplencia,
        "valor_total_devido_cents": valor_total,
        "dentro_periodo_minimo": dentro_periodo_minimo,
        "notificacao_previa_feita": notificacao_previa_feita,
        "dentro_janela_purga": dentro_janela_purga,
        # RESOLVED by engine query (not echoed): the real anti-dupla-terminacao correlation fact.
        "ja_em_rescisao_cancel": ja_em_rescisao_cancel,
    }


# ---------------------------------------------------------------
# assess_status — classify default status (no adverse decision)
# ---------------------------------------------------------------


def assess_status(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify the default status for routing — NEVER produces SUSPENDER/RESCINDIR.

    Evaluates the deployed `inadimplencia_status` decision table (ADR-0028/T1.5) — replaces
    the hand-forked if/elif ladder that used to live here.

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): rule
    ORDER differs. The old Python checked `notificacao_previa_feita` BEFORE
    `dentro_janela_purga`; the deployed table's FIRST-hit-policy checks `dentro_janela_purga`
    (-> `AGUARDA_PURGA`) BEFORE `notificacao_previa_feita` (-> `PENDENTE_NOTIFICACAO`). E.g.
    `notificacao_previa_feita=False` AND `dentro_janela_purga=True` now yields `AGUARDA_PURGA`
    (was `PENDENTE_NOTIFICACAO`). Neither is adverse — no `SUSPENDER`/`RESCINDIR` path exists in
    either version; live-verified against the compose engine before cutover (T1.5 PR body /
    evidence ledger).
    """
    dentro_min = variables.get("dentro_periodo_minimo", False)
    notificacao = variables.get("notificacao_previa_feita", False)
    dentro_purga = variables.get("dentro_janela_purga", True)
    tipo_plano = variables.get("tipo_plano", "individual")
    meses_inadimplencia = variables.get("meses_inadimplencia", 0)

    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.inadimplencia.assess_status"),
        "inadimplencia_status",
        {
            "meses_inadimplencia": int(meses_inadimplencia),
            "dentro_periodo_minimo": bool(dentro_min),
            "notificacao_previa_feita": bool(notificacao),
            "dentro_janela_purga": bool(dentro_purga),
            "tipo_plano": tipo_plano,
        },
    )
    row = first_row(rows, "inadimplencia_status", variables)
    roteamento = str(row.get("roteamento", ""))
    motivo = str(row.get("motivo", ""))

    logger.info(
        "inadimplencia_assess_status",
        tipo_plano=tipo_plano,
        roteamento=roteamento,
        motivo=motivo,
        dmn_decision_version=version.version,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# calculate_purge — determine purge window (RN 593 DRAFT)
# ---------------------------------------------------------------


def calculate_purge(variables: dict[str, Any]) -> dict[str, Any]:
    """Calculate the purge/cure window and prior notice deadlines.

    Only determines regulatory deadlines — NEVER decides to suspend/rescind.
    All deadlines DRAFT/verify against RN 593.
    """
    tipo_plano = variables.get("tipo_plano", "individual")

    # DRAFT/verify: prazos RN 593
    if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
        prazo_purga = "P30D"  # conservative for collective
        prazo_notificacao_previa = "P60D"
        periodo_minimo = "P90D"
    else:
        prazo_purga = "P10D"
        prazo_notificacao_previa = "P50D"
        periodo_minimo = "P60D"

    logger.info(
        "inadimplencia_calculate_purge",
        tipo_plano=tipo_plano,
        prazo_purga=prazo_purga,
    )

    return {
        "prazo_purga": prazo_purga,
        "prazo_notificacao_previa": prazo_notificacao_previa,
        "periodo_minimo": periodo_minimo,
        "fonte_regulatoria": "RN 593 (DRAFT/verify)",
    }


# ---------------------------------------------------------------
# notify_beneficiario — register prior notice
# ---------------------------------------------------------------


def notify_beneficiario(variables: dict[str, Any]) -> dict[str, Any]:
    """Register/dispatch prior notice to the beneficiary (RN 593).

    This is a NEUTRAL action — it informs, it does NOT suspend/rescind.
    """
    numero_contrato = variables.get("numero_contrato", "")
    matricula = variables.get("matricula_beneficiario", "")

    logger.info(
        "inadimplencia_notify_beneficiario",
        numero_contrato=numero_contrato,
        matricula=matricula,
    )

    return {
        "notificacao_previa_feita": True,
        "notificacao_previa_registrada_em": "now",  # placeholder
    }


# ---------------------------------------------------------------
# prepare_dossier — assemble human-review dossier (INSTRUCTS, never decides)
# ---------------------------------------------------------------

#: Keys copied verbatim from the pre-resolved process facts into the dossier summary the human
#: analyst reviews. Deliberately EXCLUDES every decision variable (`decisao_inadimplencia`) — the
#: dossier instructs UT_AnaliseInadimplencia, it never originates the adverse decision.
_DOSSIER_SUMMARY_KEYS = (
    "numero_contrato",
    "matricula_beneficiario",
    "tipo_plano",
    "origem_solicitacao",
    "meses_inadimplencia",
    "valor_total_devido_cents",
    "dentro_periodo_minimo",
    "notificacao_previa_feita",
    "dentro_janela_purga",
    "ja_em_rescisao_cancel",
)


def prepare_dossier(variables: dict[str, Any]) -> dict[str, Any]:
    """Assemble the human-review dossier for UT_AnaliseInadimplencia — INSTRUCTS, never decides.

    Mirrors the cancel/auth dossier pattern (`auth.AnalyzeRequestWorker`, `cancel.assess_admissibility`):
    it gathers the pre-resolved facts + regulatory context into a dossier the human analyst
    (juridico-contratos / gestao-cobranca) reviews. It NEVER originates an adverse decision — the
    suspension/rescisao is born ONLY in the human User Task (L0 hard, contract_termination,
    ADR-0005/0008). This worker therefore produces NO `decisao_inadimplencia` and NO
    SUSPENDER/RESCINDIR output; the returned dict is a non-routing record no BPMN gateway reads for a
    decision (it only feeds the human task). `ja_em_rescisao_cancel` is carried through as an
    informational fact (surfaced to the analyst), never re-derived or overridden here.
    """
    dossier_ref = f"dossier-inad-{uuid.uuid4().hex[:12]}"
    dossier_summary = {k: variables[k] for k in _DOSSIER_SUMMARY_KEYS if k in variables}

    logger.info(
        "inadimplencia_dossier_prepared",
        numero_contrato=variables.get("numero_contrato"),
        dossier_ref=dossier_ref,
    )

    return {
        "dossier_prepared": True,
        "dossier_ref": dossier_ref,
        "dossier_summary": dossier_summary,
        "numero_contrato": variables.get("numero_contrato", ""),
    }


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Alert coordenacao-cobranca of SLA risk (non-interruptive timer BT_AlertaSla).

    Informational only (mirrors `cancel.notify_sla_risk`): UT_AnaliseInadimplencia stays open, no
    decision is made or altered, NO adverse outcome is produced by timeout. Fires at ~60-70% of the
    analysis SLA (internal policy, DRAFT; `inadimplencia_sla` DMN resolves the actual duration).
    """
    numero_contrato = variables.get("numero_contrato", "")

    logger.info(
        "inadimplencia_notify_sla_risk",
        numero_contrato=numero_contrato,
        grupo_alertado="coordenacao-cobranca",
    )

    return {
        "sla_risk_notified": True,
        "grupo_alertado": "coordenacao-cobranca",
        "numero_contrato": numero_contrato,
    }


# ---------------------------------------------------------------
# register_suspension — GATED adverse effect (L0-hard)
# ---------------------------------------------------------------


def register_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Register contract suspension for default.

    GUARDED: ERR_CONTRACT_SUSPENSION_NOT_HUMAN.
    Requires:
      - decisao_inadimplencia == SUSPENDER from human user task
      - responsavel_id present (ADR-0007)
      - fundamentacao_contratual, referencia_regulatoria
      - comprovacao_notificacao_previa, comprovacao_periodo_minimo
      - NOT ja_em_rescisao_cancel (anti-double-termination)
    """
    return _register_contract_suspension(variables)


def _register_contract_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate guard conditions and register suspension.

    Raises ERR_CONTRACT_SUSPENSION_NOT_HUMAN if any guard fails.
    """
    decisao = variables.get("decisao_inadimplencia", "")
    responsavel_id = variables.get("responsavel_id", "")
    fundamentacao = variables.get("fundamentacao_contratual", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")
    comprovacao_notif = variables.get("comprovacao_notificacao_previa", "")
    comprovacao_periodo = variables.get("comprovacao_periodo_minimo", "")
    # Anti-dupla-terminacao (GAP-INAD-1): the FACT resolved by resolve_facts's cross-process CANCEL-001
    # query. FAIL CLOSED default `True` — an ABSENT fact means the correlation was never confirmed
    # (resolve_facts did not run / its output did not propagate), so the suspension is REFUSED. Only an
    # explicit `False` (engine positively confirmed no active CANCEL-001 instance) permits the effect.
    ja_em_rescisao = variables.get("ja_em_rescisao_cancel", True)

    errors: list[str] = []

    if decisao != DECISAO_SUSPENDER:
        errors.append(f"decisao_inadimplencia != {DECISAO_SUSPENDER} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao_contratual ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente")
    if not comprovacao_notif:
        errors.append("comprovacao_notificacao_previa ausente (RN 593)")
    if not comprovacao_periodo:
        errors.append("comprovacao_periodo_minimo ausente")
    if ja_em_rescisao is not False:
        errors.append(
            "ja_em_rescisao_cancel — contrato ja em rescisao/suspensao ativa em CANCEL-001 (ou "
            "correlacao nao confirmada): suspensao recusada para evitar dupla-terminacao do "
            "contract_termination (anti-dupla-rescisao, harmonization-inadimplencia-cancel.md §1)"
        )

    if errors:
        logger.error(
            "inadimplencia_suspension_guard_rejected",
            errors=errors,
            numero_contrato=variables.get("numero_contrato"),
        )
        raise InadimplenciaError(
            ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
            "; ".join(errors),
        )

    logger.info(
        "inadimplencia_contract_suspension_registered",
        numero_contrato=variables.get("numero_contrato"),
        responsavel_id=responsavel_id,
    )

    return {
        "suspensao_registrada": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
    }


# ---------------------------------------------------------------
# register_contract_suspension — external task handler alias
# ---------------------------------------------------------------

register_contract_suspension = _register_contract_suspension


# ---------------------------------------------------------------
# handoff_rescisao — NEUTRO handoff to CANCEL-001 (DRAFT-A shipped)
# ---------------------------------------------------------------


#: PHI-safe allowlist of process variables carried into the CANCEL-001 start payload (the handoff
#: hands CANCEL the RN-593 evidence so its `notificacao_previa_feita=true` branch routes to
#: SEGUE_ANALISE without re-opening the cure-window — INADIMPLENCIA owns the cure-window, CANCEL
#: must not re-run it (harmonization-inadimplencia-cancel.md §2). Explicit allowlist (never a
#: passthrough of the full variable dict): only bounded identifiers/enums/refs, no raw PHI.
_HANDOFF_CARRY_KEYS = (
    "numero_contrato",
    "matricula_beneficiario",
    "tenant_id",
    "tipo_plano",
    "meses_inadimplencia",
    "notificacao_previa_feita",
    "comprovacao_notificacao_previa",
    "comprovacao_periodo_minimo",
    "referencia_regulatoria",
    "fundamentacao_contratual",
    "documentos_refs",
    # The inadimplencia human who decided ENCAMINHAR_RESCISAO — carried for the ADR-0007 audit
    # trail; CANCEL's OWN human gate (UT_AnaliseRescisao) re-confirms the rescisao decision.
    "responsavel_id",
)


def handoff_rescisao(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff: idempotently START/correlate SP-OP-CANCEL-001 for the rescission.

    NOT an adverse effect — CANCEL-001 owns the sole rescission terminal, human-gated by its OWN
    `UT_AnaliseRescisao` (harmonization-inadimplencia-cancel.md §2). When the INADIMPLENCIA human
    decides `ENCAMINHAR_RESCISAO`, this worker starts CANCEL-001 (business key
    ``CANCEL-{tenant}-{numero_contrato}``, matricula fallback for individual/familiar) via the
    shared, business-key-idempotent ``start_process_idempotent`` chokepoint — an already-active
    CANCEL instance for this contract is returned unchanged (no duplicate rescisao).

    T-C2 FENCE (10th start site, T1.10 wave integration): the chokepoint REQUIRES a durable
    ``audit_sink`` + ``AgentDecisionProvenance`` — the ADR-0007 start record is emitted
    exactly-once (dedup key ``{tenant}:start:SP-OP-CANCEL-001:{business_key}``) BEFORE any engine
    effect. Provenance for this deterministic worker context: ``agent_id`` is the stable service
    identity (`AUDIT_AGENT_ID`, T-C convention — not the per-replica worker id), no
    ``model_id``/``prompt_version`` (no LLM decides here; the human's ENCAMINHAR_RESCISAO does),
    and ``decision_basis`` carries ONLY bounded enum/flag tokens (design §3.3). ``responsavel_id``
    and the contract identity stay in the start ``variables``, bound one-way via the record's
    ``input_sha256`` — never stored in the clear.

    P1-safe mid-handler effect (design §4.2, "MUST-FIX 1"): the ONLY external effect here is the
    business-key-idempotent process start, whose dedup makes a re-delivery return the SAME active
    instance — no double-effect — so it is exempt from the "no mid-handler external effect" rule.
    The business key is a DETERMINISTIC function of process variables (no wall-clock / uuid), so a
    re-run keys the same instance (and its audit emit dedups to the same chain link).

    FAIL-CLOSED (never a silent no-op — mirrors fraude's ``start_contratual`` handoff intent):
      - a transport/engine error propagates from ``start_process_idempotent`` (``CibSevenError`` ->
        transient -> engine retry -> incident);
      - an audit-persistence failure propagates (``AuditPersistenceError``, a ``RuntimeError`` ->
        transient -> retry) and the engine start NEVER happens (emit-before-effect);
      - a missing engine seam (``engine is None`` — composition root not wired) raises (transient);
      - a missing audit seam (``audit_sink is None``) raises (transient) — the handoff can never
        start CANCEL-001 un-audited (ADR-0007 L0);
      - a missing contract identity raises ``InadimplenciaError`` (deterministic -> immediate
        incident) — the handoff can never target an empty CANCEL business key.
    """
    decisao = variables.get("decisao_inadimplencia", "")
    if decisao != DECISAO_ENCAMINHAR_RESCISAO:
        logger.warning(
            "inadimplencia_handoff_rescisao_unexpected",
            decisao=decisao,
        )
        return {"handoff_executado": False}

    tenant_id = str(variables.get("tenant_id", ""))
    numero_contrato = str(variables.get("numero_contrato", ""))
    matricula_beneficiario = str(variables.get("matricula_beneficiario", ""))

    if not (numero_contrato or matricula_beneficiario):
        # No contract identity -> cannot key CANCEL-001 -> refuse (deterministic bad input).
        logger.error(
            "inadimplencia_handoff_rescisao_no_contract_identity",
            tenant_id=tenant_id,
        )
        raise InadimplenciaError(
            ERR_INAD_INVALID_CONTRATO,
            "handoff_rescisao: sem numero_contrato/matricula_beneficiario — nao ha identidade de "
            "contrato para iniciar CANCEL-001 (recusado, nunca inicia com business key vazia)",
        )

    if engine is None:
        # Composition root has not wired the agent->engine seam: fail closed (never a silent
        # no-op that would drop the human's ENCAMINHAR_RESCISAO decision). Transient RuntimeError
        # -> engine-computed retry -> incident (mirrors require_dmn's unwired-seam posture).
        logger.error(
            "inadimplencia_handoff_rescisao_engine_seam_not_wired",
            numero_contrato=numero_contrato or matricula_beneficiario,
        )
        raise RuntimeError(
            "handoff_rescisao: engine seam (CibSevenTransport) not wired — cannot start CANCEL-001; "
            "failing closed to a retry/incident (never a silent no-op)"
        )

    if audit_sink is None:
        # T-C2 fence co-requisite: no durable audit sink -> the fenced chokepoint cannot emit the
        # ADR-0007 start record -> the handoff must NOT start CANCEL-001 (emit-before-effect, L0).
        # Same transient fail-closed posture as the engine seam above: RuntimeError -> retry ->
        # incident, never a silent no-op and never an un-audited start.
        logger.error(
            "inadimplencia_handoff_rescisao_audit_sink_not_wired",
            numero_contrato=numero_contrato or matricula_beneficiario,
        )
        raise RuntimeError(
            "handoff_rescisao: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 "
            "start record, so CANCEL-001 is NOT started (fail-closed, emit-before-effect); "
            "failing to a retry/incident (never a silent no-op, never an un-audited start)"
        )

    cancel_business_key = _cancel_business_key(tenant_id, numero_contrato, matricula_beneficiario)
    payload: dict[str, Any] = {
        "tipo_solicitacao": "inadimplencia",
        "origem_solicitacao": "operadora",  # handoff origin (harmonization §2)
        **{k: variables[k] for k in _HANDOFF_CARRY_KEYS if k in variables},
    }

    # ADR-0007 provenance for the fenced start (T-C2, 10th site). Deterministic worker context:
    # stable service identity, no LLM legs; `decision_basis` is a curated allowlist of bounded
    # enum/flag tokens ONLY (design §3.3) — `responsavel_id`/contract identity live in `payload`,
    # bound one-way via the record's `input_sha256`, never in the clear.
    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "decisao_inadimplencia": decisao,
            "origem_solicitacao": "operadora",
            "notificacao_previa_feita": bool(variables.get("notificacao_previa_feita", False)),
        },
        model_id=None,
        prompt_version=None,
    )

    # Fenced chokepoint call: emits the durable start record exactly-once (dedup key
    # `{tenant}:start:SP-OP-CANCEL-001:{cancel_business_key}`) BEFORE `find_active_instance`/start.
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=CANCEL_PROCESS_KEY,
            business_key=cancel_business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )

    logger.info(
        "inadimplencia_handoff_rescisao",
        numero_contrato=numero_contrato or matricula_beneficiario,
        cancel_business_key=cancel_business_key,
        cancel_instance_id=instance.instance_id,
        cancel_already_existed=instance.already_existed,
    )

    return {
        "handoff_executado": True,
        "processo_destino": "SP-OP-CANCEL-001",
        "cancel_business_key": cancel_business_key,
        "cancel_instance_id": instance.instance_id,
        "cancel_already_existed": instance.already_existed,
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class InadimplenciaError(Exception):
    """Worker guard error for inadimplencia adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   resolve_facts       -> operadora.inadimplencia.resolve_facts (exact spec match; threads the
#     `engine=` seam for the anti-dupla-terminacao CANCEL-001 correlation query, GAP-INAD-1)
#   notify_beneficiario -> operadora.inadimplencia.check_prior_notice
#     (spec match: RN 593 prior-notice dispatch)
#   prepare_dossier -> operadora.inadimplencia.prepare_dossier (exact spec match; INSTRUCTS the
#     human User Task, never originates an adverse decision — cancel/auth dossier pattern)
#   register_suspension (alias register_contract_suspension)
#     -> operadora.inadimplencia.register_contract_suspension (exact spec match, GUARDED)
#   handoff_rescisao -> operadora.inadimplencia.handoff_rescisao (exact spec match)
#   notify_sla_risk -> operadora.inadimplencia.notify_sla_risk (exact spec match; informational)
# assess_status/calculate_purge have no distinct spec topic (assess_status
# evaluates the deployed `inadimplencia_status` decision table via the dmn=
# seam since T1.5/ADR-0028; calculate_purge resolves regulatory deadlines) —
# registered under function-derived topics for registry completeness.
# ---------------------------------------------------------------


def register_inadimplencia_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-INADIMPLENCIA-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `assess_status` (`inadimplencia_status`, T1.5
    cutover) via `functools.partial`; no other function here evaluates a DMN table.

    `engine` (a `CibSevenTransport`, the SAME `find_active_instance`/`start_process` seam the agent
    graphs use, ADR-0001/T1.11) is threaded into BOTH engine-touching workers:
      - `resolve_facts` — the anti-dupla-terminacao cross-process `find_active_instance` query
        (GAP-INAD-1). ABSENT (`None`) -> FAILS CLOSED (`ja_em_rescisao_cancel := True`), so the
        `register_contract_suspension` guard REFUSES the suspension (ADR-0018).
      - `handoff_rescisao` — the CANCEL-001 `start_process_idempotent` start (T1.10 T-D). ABSENT
        (`None`) -> RAISES (never a silent no-op that would drop the human's ENCAMINHAR_RESCISAO).
    The seam flows in through the standard `**seams` channel (`register_all_workers(harness, ...,
    engine=...)`), identical to how `dmn` is threaded — no other bootstrap needs to change. In the
    live daemon it is a `FreshClientCibSevenTransport` (fresh-client-per-call, GAP-INAD-1).

    `audit_sink` (an `AuditStartSink`, T1.10 T-C2 fence co-requisite) is threaded into
    `handoff_rescisao` only — the one worker here that runs the fenced `start_process_idempotent`
    chokepoint. ABSENT (`None`) -> `handoff_rescisao` RAISES before any engine effect (an
    un-audited CANCEL-001 start is structurally impossible, ADR-0007 L0). In the live daemon it is
    a `FreshSinkAuditEmitter` (gateway/audit_postgres.py) — the sink-side mirror of the
    fresh-client-per-call pattern, because each sync dispatch emits on its own `asyncio.run` loop.
    """
    del kafka  # unused — no inadimplencia.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    engine: CibSevenTransport | None = seams.get("engine")
    audit_sink: AuditStartSink | None = seams.get("audit_sink")
    harness.register_worker(
        FunctionWorker(
            "operadora.inadimplencia.resolve_facts", functools.partial(resolve_facts, engine=engine)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.assess_status", functools.partial(assess_status, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.calculate_purge", calculate_purge))
    harness.register_worker(FunctionWorker("operadora.inadimplencia.check_prior_notice", notify_beneficiario))
    harness.register_worker(FunctionWorker("operadora.inadimplencia.prepare_dossier", prepare_dossier))
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.register_contract_suspension", register_contract_suspension)
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.inadimplencia.handoff_rescisao",
            functools.partial(handoff_rescisao, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.notify_sla_risk", notify_sla_risk))
