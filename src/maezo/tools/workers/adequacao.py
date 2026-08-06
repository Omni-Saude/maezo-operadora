"""Worker: adequacao (SP-OP-ADEQUACAO-001).

Adequacao Geografica de Rede — mostly L3 monitoring with one human-gated adverse effect.
Guard: ERR_FALLBACK_COMMITMENT_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, non_blank
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from collections.abc import Mapping

    from maezo.a2a import DelegationDispatcher
    from maezo.tools.workers.harness import (
        ExternalTask,
        KafkaPublisher,
        TaskHandler,
        WorkerHarness,
    )

logger = structlog.get_logger(__name__)

# Internal-notification channel (mirrors programa.py's/recurso.py's/lgpd.py's own
# `_NOTIFICATIONS_TOPIC` — a `type`-discriminated envelope on `operadora.notifications.internal`,
# NOT a BPMN-declared domain-event topic). Used by the `update_monitoring_plan` raw handler below
# (the item-9 notify-wiring gap: `register_adequacao_workers` used to `del kafka  # unused`)
# whose Kafka publish needs the async seam a `FunctionWorker`'s sync boundary cannot reach
# (`WorkerBase`'s own docstring: "Async I/O is handled by the engine/message layer, not by the
# worker logic").
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_FALLBACK_COMMITMENT_NOT_HUMAN = "ERR_FALLBACK_COMMITMENT_NOT_HUMAN"
ERR_ADEQUACAO_CELULA_INVALIDA = "ERR_ADEQUACAO_CELULA_INVALIDA"

# Decision values
DECISAO_COMPROMISSO_FALLBACK = "COMPROMISSO_FALLBACK"


# ---------------------------------------------------------------
# measure_gap — geo-analysis (FACT, no decision)
# ---------------------------------------------------------------


def measure_gap(variables: dict[str, Any]) -> dict[str, Any]:
    """Measure geographic coverage gap (FACT only — NEVER decides commitment).

    Computes: tempo_acesso_apurado_min, distancia_apurada_km,
    prestadores_disponiveis, cobertura_geo_suficiente, dados_geo_completos.

    FACT PRESERVATION (mirrors `credenciamento.validate_cred`'s FACT PRESERVATION fix — the
    IDENTICAL defect class): the pre-fix worker unconditionally overwrote
    `tempo_acesso_apurado_min`/`distancia_apurada_km` with hardcoded placeholders and
    RE-DERIVED `cobertura_geo_suficiente`/`dados_geo_completos` from OTHER variables — clobbering
    any already-resolved fact on the process BEFORE `BRT_AdequacaoGap`
    (`operadora.adequacao.calculate_gap`, the task that runs immediately after this one) could
    evaluate it. The BPMN's own task documentation says this worker "apura/ECOA" these facts
    (echo, not overwrite). An explicit, correctly-typed resolved value already on `variables` is
    now respected (echoed through unchanged); anything else (absent, wrong type) falls back to
    the SAME placeholder computation as before (real implementation: query geo-location / network
    DB) — `prestadores_disponiveis` was already preserved this way pre-fix and is unchanged here.

    Type-appropriate isinstance guards (engine variables arrive untyped): `tempo_acesso_apurado_min`
    is contract-typed `integer` (SP-OP-ADEQUACAO-001.md:108, ADR-0018 parte 2 "numeros nunca como
    number") — only a Python `int` counts, and `bool` is explicitly EXCLUDED even though `bool` is
    a subclass of `int` in Python (`isinstance(True, int) is True`) — a boolean must NEVER be
    accepted as a numeric measurement. `distancia_apurada_km` is contract-typed `double`
    (SP-OP-ADEQUACAO-001.md:109) — only a Python `float` counts (an `int` distance is a WRONG type
    here, not a resolved fact — falls back to the placeholder, same as absent).
    `cobertura_geo_suficiente`/`dados_geo_completos` are booleans — only `bool` counts, mirroring
    `credenciamento.validate_cred`'s `isinstance(seeded_licenca, bool)` exactly.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    seeded_tempo = variables.get("tempo_acesso_apurado_min")
    seeded_distancia = variables.get("distancia_apurada_km")
    seeded_cobertura = variables.get("cobertura_geo_suficiente")
    seeded_dados_completos = variables.get("dados_geo_completos")

    # Placeholder fallback — real implementation queries geo-location / network DB; only used
    # when no resolved fact of the CORRECT type exists on `variables` (FACT PRESERVATION above).
    tempo_placeholder = 45  # minutes
    distancia_placeholder = 15.5  # km
    prestadores = variables.get("prestadores_disponiveis", 3)

    tempo_acesso = (
        seeded_tempo
        if isinstance(seeded_tempo, int) and not isinstance(seeded_tempo, bool)
        else tempo_placeholder
    )
    distancia = seeded_distancia if isinstance(seeded_distancia, float) else distancia_placeholder
    cobertura_suficiente = seeded_cobertura if isinstance(seeded_cobertura, bool) else prestadores >= 2
    dados_completos = (
        seeded_dados_completos if isinstance(seeded_dados_completos, bool) else bool(regiao and especialidade)
    )

    logger.info(
        "adequacao_measure_gap",
        regiao_saude=regiao,
        especialidade=especialidade,
        tempo_acesso_min=tempo_acesso,
        distancia_km=distancia,
        prestadores=prestadores,
    )

    return {
        "tempo_acesso_apurado_min": tempo_acesso,
        "distancia_apurada_km": distancia,
        "prestadores_disponiveis": prestadores,
        "cobertura_geo_suficiente": cobertura_suficiente,
        "dados_geo_completos": dados_completos,
    }


# ---------------------------------------------------------------
# route_remediation — classify gap and route (no commitment decision)
# ---------------------------------------------------------------


def route_remediation(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify gap severity and route remediation — NEVER commits to fallback.

    Evaluates TWO chained deployed decision tables (ADR-0028/T1.5): `adequacao_gap` (tipo_carater,
    tempo_acesso_apurado_min, distancia_apurada_km, prestadores_disponiveis,
    cobertura_geo_suficiente) -> `gap_adequacao` in {CONFORME, GAP_LEVE, GAP_MODERADO,
    GAP_CRITICO}; then, UNCONDITIONALLY, `adequacao_remediation_routing` (gap_adequacao,
    dados_geo_completos) -> `roteamento_remediacao`.

    `tipo_carater` is a NEW input this function did not previously read at all (additive —
    sourced directly from `variables`, defaulting to `""` when absent).

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): the
    deployed `adequacao_gap` table's rule ORDER makes `GAP_LEVE` win over `CONFORME` for
    `tipo_carater="eletivo"` whenever `tempo_acesso_apurado_min<=60` and
    `distancia_apurada_km<=50.0` (its own row precedes `CONFORME`'s in FIRST-hit-policy order) —
    live-verified: `tempo=15, distancia=5.0, prestadores=5, cobertura=True` -> `GAP_LEVE`, not
    `CONFORME` (the old Python's own tighter `tempo<=30`/`distancia<=20.0` gate for CONFORME).
    `GAP_MODERADO` is also computed differently (DMN keys off `cobertura_geo_suficiente=false`;
    old Python keyed off `prestadores>=1 and tempo<=90`). Values stay within the same neutral
    closed set with no adverse output on either path — both `CONFORME` and `GAP_LEVE` route to
    the SAME `MONITORAR` remediation (live-verified), so this divergence has zero effect on the
    downstream remediation action.
    """
    tempo = variables.get("tempo_acesso_apurado_min", 0)
    distancia = variables.get("distancia_apurada_km", 0.0)
    prestadores = variables.get("prestadores_disponiveis", 0)
    cobertura = variables.get("cobertura_geo_suficiente", False)
    dados_completos = variables.get("dados_geo_completos", True)
    tipo_carater = variables.get("tipo_carater", "")

    dmn_transport = require_dmn(dmn, "operadora.adequacao.calculate_gap")

    gap_rows, gap_version = evaluate_sync(
        dmn_transport,
        "adequacao_gap",
        {
            "tipo_carater": tipo_carater,
            "tempo_acesso_apurado_min": int(tempo),
            "distancia_apurada_km": float(distancia),
            "prestadores_disponiveis": int(prestadores),
            "cobertura_geo_suficiente": bool(cobertura),
        },
    )
    gap_row = first_row(gap_rows, "adequacao_gap", variables)
    gap_adequacao = str(gap_row.get("gap_adequacao", "GAP_CRITICO"))
    motivo = str(gap_row.get("motivo", ""))

    route_rows, route_version = evaluate_sync(
        dmn_transport,
        "adequacao_remediation_routing",
        {"gap_adequacao": gap_adequacao, "dados_geo_completos": bool(dados_completos)},
    )
    route_row = first_row(route_rows, "adequacao_remediation_routing", variables)
    roteamento = str(route_row.get("roteamento_remediacao", "ANALISE_HUMANA"))
    route_motivo = str(route_row.get("motivo", motivo))

    logger.info(
        "adequacao_route_remediation",
        gap_adequacao=gap_adequacao,
        roteamento=roteamento,
        dmn_gap_version=gap_version.version,
        dmn_route_version=route_version.version,
    )

    return {
        "gap_adequacao": gap_adequacao,
        "roteamento_remediacao": roteamento,
        "motivo": route_motivo,
    }


# ---------------------------------------------------------------
# notify_coordenacao — neutral notification
# ---------------------------------------------------------------


def notify_coordenacao(variables: dict[str, Any]) -> dict[str, Any]:
    """Notify network coordination about gap (NEUTRAL, informational)."""
    regiao = variables.get("regiao_saude", "")
    gap = variables.get("gap_adequacao", "")

    logger.info(
        "adequacao_notify_coordenacao",
        regiao_saude=regiao,
        gap=gap,
    )

    return {"notificacao_enviada": True}


# ---------------------------------------------------------------
# execute_remediation — handoff to CRED-001 (NEUTRAL)
# ---------------------------------------------------------------


def execute_remediation(variables: dict[str, Any]) -> dict[str, Any]:
    """Initiate credentialing to close the gap (handoff to CRED-001 — NEUTRAL).

    This is L3 — buscar prestador nao e adverso.
    """
    logger.info(
        "adequacao_execute_remediation",
        regiao_saude=variables.get("regiao_saude"),
        especialidade=variables.get("especialidade"),
    )

    return {
        "handoff_credenciamento": True,
        "processo_destino": "SP-OP-CRED-001",
    }


# ---------------------------------------------------------------
# update_monitoring_plan — L3 monitoring, NEUTRAL (no adverse effect)
# ---------------------------------------------------------------


def update_monitoring_plan(variables: dict[str, Any]) -> dict[str, Any]:
    """Open/update the cell's (regiao x especialidade) monitoring plan.

    External task: `operadora.adequacao.update_monitoring_plan` (`ST_UpdateMonitoringPlanL3`,
    SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:197-203). First step of the GAP_LEVE/MONITORAR L3
    branch (reached from the initial routing gateway `GW_Monitorar` OR from
    `GWDec_MonitorarOk` — the human choosing `decisao_remediacao=MONITORAR_OK` at
    `UT_DecisaoFallback` instead of committing a fallback).

    NEUTRAL, per the BPMN's own task documentation (bpmn:documentation, line 199):
    "Monitoramento/alerta — NAO compromete caixa nem nega atendimento" (monitoring/alert only —
    does NOT commit cash-flow nor deny care) — mirrors the contract's own framing (GAP-ADEQ-6,
    docs/processes/contracts/SP-OP-ADEQUACAO-001.md:149). TASY write DROP (same doc line). No
    human gate: unlike `register_fallback_commitment`, this is a clerical/monitoring action, not
    an adverse effect.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")
    gap = variables.get("gap_adequacao", "")

    logger.info(
        "adequacao_update_monitoring_plan",
        regiao_saude=regiao,
        especialidade=especialidade,
        gap_adequacao=gap,
    )

    return {
        "plano_monitoramento_atualizado": True,
        "celula": f"{regiao}:{especialidade}",
        "gap_adequacao": gap,
    }


_UPDATE_MONITORING_PLAN_NOTIFICATION_TYPE = "adequacao.update_monitoring_plan"


def make_update_monitoring_plan_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `operadora.adequacao.update_monitoring_plan` (serves
    `ST_UpdateMonitoringPlanL3`).

    `register_adequacao_workers` previously `del kafka  # unused` — no adequacao.py worker ever
    declared a Kafka dependency, so `update_monitoring_plan` (the ONLY topic in this family whose
    BPMN documentation implies a downstream-observable side effect, GAP-ADEQ-6) silently never
    published anything. This closes that gap — mirrors `programa.make_notify_sla_risk_handler`/
    `recurso.make_notify_sla_risk_handler`'s exact idiom (a raw `harness.register()` handler is
    needed for the async Kafka seam a sync `FunctionWorker.execute` boundary cannot reach;
    `update_monitoring_plan` itself, the pure function above, is UNCHANGED).

    NEUTRAL, non-PHI payload — `tenant_id`/`regiao_saude`/`especialidade`/`gap_adequacao` (the
    cell identity + the SAME 3 input fields the pure function already reads off `variables`, plus
    the pure function's own `gap_adequacao` passthrough); this process carries no beneficiary
    identifier at all (geography is region/municipio granularity only — ADR-0006).
    `best_effort=False`: no BPMN error boundary is declared on `ST_UpdateMonitoringPlanL3` -> RAW
    propagate a broker failure to the harness retry/incident ladder (ADR-0030) instead of silently
    swallowing it, mirroring every sibling `_NOTIFICATIONS_TOPIC` publisher in this codebase.
    `kafka=None` (no producer wired) logs a warning and still completes the task — the L3
    monitoring-plan update itself is NEVER blocked by a missing producer.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = update_monitoring_plan(task.variables)
        if kafka is None:
            logger.warning("adequacao_update_monitoring_plan_no_producer", business_key=task.business_key)
            return result
        notification = {
            "type": _UPDATE_MONITORING_PLAN_NOTIFICATION_TYPE,
            "tenant_id": task.variables.get("tenant_id", ""),
            "regiao_saude": task.variables.get("regiao_saude", ""),
            "especialidade": task.variables.get("especialidade", ""),
            "gap_adequacao": result.get("gap_adequacao", ""),
        }
        # best_effort=False — see factory docstring (no boundary declared -> raw propagate).
        await kafka.publish(
            _NOTIFICATIONS_TOPIC, notification, key=task.business_key or None, best_effort=False
        )
        return result

    return handler


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Alert coordenacao-rede of SLA risk (non-interruptive timer BT_AlertaSlaAdequacao).

    External task: `operadora.adequacao.notify_sla_risk` (`ST_NotificarRiscoSla`,
    SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:287-291 — task name "Notificar risco de SLA
    (coordenacao-rede)"). Fires at `${sla.sla_alerta}` (60-70% of `adequacao_sla`'s
    `sla_alerta`, per the contract, DRAFT/verify — docs/processes/contracts/
    SP-OP-ADEQUACAO-001.md:153,225) on the non-interruptive boundary event
    (cancelActivity="false") attached to `UT_DecisaoFallback`.

    Informational only (mirrors `inadimplencia.notify_sla_risk`/`cancel.notify_sla_risk`/
    `fraude.notify_sla_risk`): UT_DecisaoFallback stays open, no decision is made or altered,
    NO adverse outcome (fallback commitment or otherwise) is ever produced by this alert.
    The fallback commitment NEVER arises from a timer -- only the human decision at
    UT_DecisaoFallback (`register_fallback_commitment`'s own guard, unchanged) does.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    logger.info(
        "adequacao_notify_sla_risk",
        regiao_saude=regiao,
        especialidade=especialidade,
        grupo_alertado="coordenacao-rede",
    )

    return {
        "sla_risk_notified": True,
        "grupo_alertado": "coordenacao-rede",
        "regiao_saude": regiao,
        "especialidade": especialidade,
    }


# ---------------------------------------------------------------
# register_fallback_commitment — GATED adverse effect
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str`/`pagto.register_payment_refusal`'s fix (t2.5-p2b-round2, then
    t3.1-guard-input-hardening for `release_high_value_payment`): the pre-fix bare
    `if not tipo_fallback` / `if not justificativa` / ... checks let WHITESPACE-ONLY decision +
    accountability fields pass the guard — defeating ADR-0007/RN 259 (a fallback commitment
    recorded with a non-identifying approver or a blank justification/regulatory reference).
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


def register_fallback_commitment(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a financial fallback commitment (livre escolha/reembolso).

    GUARDED: ERR_FALLBACK_COMMITMENT_NOT_HUMAN.
    This is the ONLY adverse effect in this L3-majority process.

    NORMALIZATION (t3.1-guard-input-hardening, closing the bare-truthiness gap noted in the
    #133 audit — adequacao.py:289-295 pre-fix): ALL decision + human-accountability fields
    (`decisao_remediacao`, `tipo_fallback`, `justificativa_fallback`, `referencia_regulatoria`,
    `responsavel_id`) are normalized via `_norm_str` (strip; non-string -> "") BEFORE any guard
    check, mirroring `pagto.register_payment_refusal`'s fix. Consequences, all fail-closed:
    - whitespace-only `tipo_fallback`/`justificativa_fallback`/`referencia_regulatoria`/
      `responsavel_id` REFUSES exactly like an absent field;
    - a whitespace-PADDED but otherwise exact `decisao_remediacao` literal
      ("COMPROMISSO_FALLBACK ") normalizes to the literal and still passes Guard 1 (still
      subject to the accountability-field checks) — case variants/substrings still refuse
      (exact `!=` match, no folding);
    - a non-string in ANY of these fields normalizes to "" (refusal), never a truthy
      pass-through and never an AttributeError incident.
    """
    decisao = _norm_str(variables.get("decisao_remediacao", ""))
    tipo_fallback = _norm_str(variables.get("tipo_fallback", ""))
    justificativa = _norm_str(variables.get("justificativa_fallback", ""))
    ref_regulatoria = _norm_str(variables.get("referencia_regulatoria", ""))
    responsavel_id = _norm_str(variables.get("responsavel_id", ""))

    errors: list[str] = []

    if decisao != DECISAO_COMPROMISSO_FALLBACK:
        errors.append(f"decisao_remediacao != {DECISAO_COMPROMISSO_FALLBACK} (got: {decisao!r})")
    if not tipo_fallback:
        errors.append("tipo_fallback ausente")
    if not justificativa:
        errors.append("justificativa_fallback ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 259)")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")

    if errors:
        logger.error(
            "adequacao_fallback_guard_rejected",
            errors=errors,
        )
        raise AdequacaoError(ERR_FALLBACK_COMMITMENT_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "adequacao_fallback_commitment_registered",
        tipo_fallback=tipo_fallback,
        responsavel_id=responsavel_id,
    )

    return {
        "compromisso_fallback_registrado": True,
        "estimativa_custo_cents": variables.get("estimativa_custo_cents", 0),
    }


# ---------------------------------------------------------------
# prepare_remediation_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed,
# DL-0037)
# ---------------------------------------------------------------


def make_prepare_remediation_dossier_handler(
    dispatcher: DelegationDispatcher | None,
) -> TaskHandler:
    """Create the handler for `operadora.adequacao.prepare_remediation_dossier` — the REAL Andre
    A2A delegation.

    Serves `ST_PrepareRemediationDossier` (ANALISE_HUMANA branch; also reached on the GAP-ADEQ-3
    `seguir_analise` re-entry). Replaces the DL-0033 local stub with
    `delegate_adequacao_dossier` -> `DelegationDispatcher.delegate` -> Andre's REAL graph
    (SHARED task_type `analytics.population`, disambiguated into his `adequacao_dossier` flow by
    the `adequacao-worker` origin — `agents/andre/delegation.py`).

    RAW ASYNC HANDLER (DL-0034 precedent): `dispatcher.delegate` is async; the raw
    `harness.register()` form runs on the harness's own loop. Populates `_handlers` but NOT the
    `WorkerRegistry` (see `test_bootstrap_registration.py`'s `raw_handler_topics`).

    FAIL-NEUTRAL-WITH-DISCLOSED-GAP (DL-0037): the dossier INSTRUCTS `UT_DecisaoFallback`
    ("instrui, nao decide" — SP-OP-ADEQUACAO-001). A missing dispatcher, missing cell identity,
    a structured rejection or ANY delegation failure returns
    `{"dossier_prepared": False, "dossier_gap": <bounded reason token>}` + a LOUD log and
    COMPLETES the task — the human UT MUST still open; this handler NEVER raises. The gap token
    is a bounded class token (engine-variable hygiene) — raw error text stays in the log.

    Idempotency note (disclosed): the delegation `task_id` is the cell key
    `ADEQ-{tenant}-{regiao}-{especialidade}[-{ciclo}]` — the `seguir_analise` re-entry for the
    SAME cell/ciclo receives the idempotent REPLAY of the same dossier; a genuinely new
    evaluation cycle carries a new `ciclo_avaliacao` (a new task_id, a fresh dossier).
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = str(v.get("tenant_id", "") or "")
        regiao_saude = str(v.get("regiao_saude", "") or "")
        especialidade = str(v.get("especialidade", "") or "")
        ciclo_avaliacao = str(v.get("ciclo_avaliacao", "") or "").strip() or None

        if dispatcher is None:
            # Degraded runtime (DL-0037): dispatcher absent at composition (no signing key /
            # no DATABASE_URL — worker_runtime readiness reports dossier_delegation_ready=false).
            logger.warning(
                "adequacao_prepare_remediation_dossier_dispatcher_unavailable",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "dispatcher_unavailable"}

        if not (non_blank(tenant_id) and non_blank(regiao_saude) and non_blank(especialidade)):
            # No well-formed ADEQ cell key can be derived (EB-4 R1 `non_blank` discipline) —
            # never delegate with a degenerate task_id; the UT still opens with the gap disclosed.
            logger.error(
                "adequacao_prepare_remediation_dossier_missing_cell_identity",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "missing_business_identifiers"}

        from maezo.agents.andre.delegation import delegate_adequacao_dossier

        try:
            result = await delegate_adequacao_dossier(
                dispatcher,
                tenant=tenant_id.strip(),
                regiao_saude=regiao_saude.strip(),
                especialidade=especialidade.strip(),
                case_meta=dict(v),
                ciclo_avaliacao=ciclo_avaliacao,
            )
        except Exception as exc:  # noqa: BLE001 — DL-0037: the UT must open; never raise here.
            logger.error(
                "adequacao_prepare_remediation_dossier_delegation_failed",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
                error=str(exc),
            )
            return {"dossier_prepared": False, "dossier_gap": "delegation_failed"}

        if not result.success:
            reason = str(result.rejection_reason or "unknown")
            logger.error(
                "adequacao_prepare_remediation_dossier_delegation_rejected",
                tenant_id=tenant_id,
                regiao_saude=regiao_saude,
                especialidade=especialidade,
                business_key=task.business_key,
                reason=reason,
                detail=result.detail,
            )
            return {"dossier_prepared": False, "dossier_gap": f"delegation_rejected:{reason}"}

        logger.info(
            "adequacao_prepare_remediation_dossier_delegated",
            tenant_id=tenant_id,
            regiao_saude=regiao_saude,
            especialidade=especialidade,
            business_key=task.business_key,
            dossier_ref=result.output_ref,
            idempotent_replay=result.idempotent_replay,
        )
        return {
            "dossier_prepared": True,
            "dossier_ref": result.output_ref or "",
            # UT-FORM SEAM (SME/PO sign-off PENDING): the dossier CONTENT field schema for
            # UT_DecisaoFallback is uncontracted — no dossier field appears in the contract's
            # variable table. `dossier_summary` carries Andre's agent-produced bounded summary
            # tokens AS-IS (route/desfecho/motivo/grupo — never the narrative, never a decision);
            # the full dossier is reachable via `dossier_ref`. Do NOT invent/extend this schema
            # here — it is the human-gated injection point.
            "dossier_summary": dict(result.meta),
        }

    return handler


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class AdequacaoError(Exception):
    """Worker guard error for adequacao adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a: dict-first
# functions wrap directly).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn
# (excl. the shared/out-of-scope `operadora.events.publish` — see ADR-0026 §2b
# note on cross-cutting event-publish topics):
#   measure_gap    -> operadora.adequacao.measure_coverage      (spec match: geo-analysis facts)
#   route_remediation -> operadora.adequacao.calculate_gap      (spec match: gap classification)
#   notify_coordenacao -> operadora.adequacao.notify_rede       (spec match: network coordination)
#   execute_remediation -> operadora.adequacao.start_credenciamento (spec match: CRED-001 handoff)
#   register_fallback_commitment -> operadora.adequacao.register_fallback_commitment
#     (exact spec match, GUARDED)
#   make_update_monitoring_plan_handler -> operadora.adequacao.update_monitoring_plan (spec
#     match, NEUTRAL — t2.5-p2b-round2 closed the registry-drift gap; RAW async handler now
#     publishes a `_NOTIFICATIONS_TOPIC` notification too — closes the item-9 `del kafka # unused`
#     notify-wiring gap, GAP-ADEQ-6)
#   notify_sla_risk -> operadora.adequacao.notify_sla_risk (spec match, informational —
#     t2.5-p2b-round2 closed this gap)
#   make_prepare_remediation_dossier_handler -> operadora.adequacao.prepare_remediation_dossier
#     (exact spec match; RAW async handler — the REAL Andre A2A delegation (analytics.population,
#     origin-disambiguated to his adequacao_dossier flow) DL-0033 deferred, now wired. NEUTRAL —
#     instructs UT_DecisaoFallback, never decides; dispatcher absent/failed -> disclosed-gap
#     marker, the UT still opens (DL-0037).)
# ---------------------------------------------------------------


def register_adequacao_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ADEQUACAO-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `route_remediation` (`adequacao_gap` +
    `adequacao_remediation_routing`, T1.5 cutover) via `functools.partial`. `kafka` is threaded
    into the `update_monitoring_plan` RAW handler (item-9 notify-wiring fix — this used to be
    `del kafka  # unused`; `update_monitoring_plan` is the ONLY adequacao.py worker with a Kafka
    dependency, `notify_sla_risk` stays plain `FunctionWorker`-wrapped, no publish).

    `dossier_dispatcher` (dossier-A2A seam, DL-0033 real wiring) is threaded into the
    `prepare_remediation_dossier` RAW async handler — a `DelegationDispatcher` assembled by the
    worker-runtime composition root (`build_dossier_delegation_dispatcher`). Absent (`None`, the
    topic-probe default and the degraded-runtime posture) the topic still registers and the
    handler fail-neutrals with a disclosed gap (DL-0037) — the human UT always still opens.
    """
    dmn = seams.get("dmn")
    dossier_dispatcher: DelegationDispatcher | None = seams.get("dossier_dispatcher")
    harness.register_worker(FunctionWorker("operadora.adequacao.measure_coverage", measure_gap))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.calculate_gap", functools.partial(route_remediation, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_rede", notify_coordenacao))
    harness.register_worker(FunctionWorker("operadora.adequacao.start_credenciamento", execute_remediation))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.register_fallback_commitment", register_fallback_commitment)
    )
    # RAW handler (NOT register_worker) — needs the async Kafka seam (module topic-map note).
    harness.register(
        "operadora.adequacao.update_monitoring_plan",
        make_update_monitoring_plan_handler(kafka),
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_sla_risk", notify_sla_risk))
    # RAW handler (NOT register_worker) — needs the async dispatcher seam (module topic-map note).
    harness.register(
        "operadora.adequacao.prepare_remediation_dossier",
        make_prepare_remediation_dossier_handler(dossier_dispatcher),
    )
