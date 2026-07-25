"""Worker: adequacao (SP-OP-ADEQUACAO-001).

Adequacao Geografica de Rede — mostly L3 monitoring with one human-gated adverse effect.
Guard: ERR_FALLBACK_COMMITMENT_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

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
    prestadores_disponiveis, cobertura_geo_suficiente.
    """
    regiao = variables.get("regiao_saude", "")
    especialidade = variables.get("especialidade", "")

    # Placeholder: real implementation queries geo-location / network DB
    tempo_acesso = 45  # minutes
    distancia = 15.5  # km
    prestadores = variables.get("prestadores_disponiveis", 3)
    cobertura_suficiente = prestadores >= 2
    dados_completos = bool(regiao and especialidade)

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


def register_fallback_commitment(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a financial fallback commitment (livre escolha/reembolso).

    GUARDED: ERR_FALLBACK_COMMITMENT_NOT_HUMAN.
    This is the ONLY adverse effect in this L3-majority process.
    """
    decisao = variables.get("decisao_remediacao", "")
    tipo_fallback = variables.get("tipo_fallback", "")
    justificativa = variables.get("justificativa_fallback", "")
    ref_regulatoria = variables.get("referencia_regulatoria", "")
    responsavel_id = variables.get("responsavel_id", "")

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
#   update_monitoring_plan -> operadora.adequacao.update_monitoring_plan (spec match, NEUTRAL —
#     t2.5-p2b-round2 closed this gap)
#   notify_sla_risk -> operadora.adequacao.notify_sla_risk (spec match, informational —
#     t2.5-p2b-round2 closed this gap)
# Spec topic with NO implementing function today (gap, not fabricated here, Andre A2A-gated —
# out of scope for t2.5-p2b-round2): prepare_remediation_dossier.
# ---------------------------------------------------------------


def register_adequacao_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ADEQUACAO-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `route_remediation` (`adequacao_gap` +
    `adequacao_remediation_routing`, T1.5 cutover) via `functools.partial`. `kafka` is accepted
    but unused — no adequacao.py worker declares a Kafka dependency, `update_monitoring_plan`/
    `notify_sla_risk` included (dict-first, mirror the family's existing idiom).
    """
    del kafka  # unused — no adequacao.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    harness.register_worker(FunctionWorker("operadora.adequacao.measure_coverage", measure_gap))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.calculate_gap", functools.partial(route_remediation, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_rede", notify_coordenacao))
    harness.register_worker(FunctionWorker("operadora.adequacao.start_credenciamento", execute_remediation))
    harness.register_worker(
        FunctionWorker("operadora.adequacao.register_fallback_commitment", register_fallback_commitment)
    )
    harness.register_worker(
        FunctionWorker("operadora.adequacao.update_monitoring_plan", update_monitoring_plan)
    )
    harness.register_worker(FunctionWorker("operadora.adequacao.notify_sla_risk", notify_sla_risk))
