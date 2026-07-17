"""Worker: ans_cron (SP-OP-ANS-CRON-001).

Agendador per-report_type dos Envios Periodicos ANS.
Pure scheduler — NO adverse effects, all terminals NEUTRAL.
Dispatches via operadora.events.publish (reused generic publisher).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Report types and their periodicities (DRAFT/verify RN 124/209/388/424/DIOPS)
# ---------------------------------------------------------------

_REPORT_PERIODICIDADE: dict[str, str] = {
    "MAPEAMENTO_REDE": "P1M",  # monthly
    "DIOPS": "P3M",  # quarterly
    "SIP": "P1M",  # monthly
    "RPC": "P1M",  # monthly
    "ANS_TISS": "P1M",  # monthly
    "QUALIFICACAO": "P12M",  # annual
}


def _compute_competencia(reference_date_iso: str, periodicidade: str) -> str:
    """Compute the competencia (YYYY-MM) from the reference date.

    The competencia is the calendar period IMMEDIATELY BEFORE the reference date's month.
    For quarterly: the quarter ending before the reference month.

    DRAFT/verify regulatorio: mapeamento confirmado com ANS.
    """
    try:
        ref_date = datetime.fromisoformat(reference_date_iso)
    except (ValueError, TypeError):
        return "COMPETENCIA_PENDENTE"

    if periodicidade == "P3M":
        # Quarterly: compute the previous quarter
        month = ref_date.month
        quarter_month = ((month - 1) // 3) * 3 + 1
        if quarter_month == month:
            # If we're exactly at quarter start, go back one quarter
            quarter_month = ((month - 4) // 3) * 3 + 1
            if quarter_month < 1:
                quarter_month = 10
                year = ref_date.year - 1
            else:
                year = ref_date.year
        else:
            year = ref_date.year
        return f"{year:04d}-{quarter_month:02d}"
    else:
        # Monthly: previous month
        if ref_date.month == 1:
            year = ref_date.year - 1
            month = 12
        else:
            year = ref_date.year
            month = ref_date.month - 1
        return f"{year:04d}-{month:02d}"


# ---------------------------------------------------------------
# trigger_submissions — publish ans.cron_due facts
# ---------------------------------------------------------------


def trigger_submissions(variables: dict[str, Any]) -> dict[str, Any]:
    """Publish the ans.cron_due fact for the notification bridge.

    Each tick of the timer produces a typed fact on
    operadora.notifications.internal with type=ans.cron_due.
    The notifications_bridge then starts SP-OP-ANS-SUBMIT-001.

    NO adverse effects — pure scheduling dispatch.
    """
    report_type = variables.get("report_type", "")
    periodicidade = _REPORT_PERIODICIDADE.get(report_type, "P1M")
    reference_date = datetime.now(UTC).strftime("%Y-%m-%d")

    competencia = _compute_competencia(reference_date, periodicidade)

    logger.info(
        "ans_cron_trigger_submission",
        report_type=report_type,
        periodicidade=periodicidade,
        competencia=competencia,
        reference_date=reference_date,
    )

    return {
        "fato_publicado": True,
        "event_type": "ans.cron_due",
        "report_type": report_type,
        "periodicidade": periodicidade,
        "competencia": competencia,
        "ans_cron_reference_date_iso": reference_date,
    }


# ---------------------------------------------------------------
# check_calendar — verify calendar validity (DMN ans_calendar)
# ---------------------------------------------------------------


def check_calendar(variables: dict[str, Any]) -> dict[str, Any]:
    """Check if the current period requires a submission.

    DMN ans_calendar: validates periodicidade vs current date.
    Returns: deve_enviar (bool) + motivo.

    T1.5 (ADR-0028) golden-parity finding — NOT cut over, left as pure Python (BLOCKED, not
    silently shipped as done): the deployed `spec/processes/dmn/ans_calendar.dmn` keys
    `report_type` on RN-citation-style literals (`RN_124_SIP`, `RN_209_UTILIZACAO`,
    `RN_388_QUALIDADE`, `RN_424_TISS_MONITORAMENTO`, `DIOPS_TRIMESTRAL`) that have ZERO overlap
    with the `report_type` values this module (and every test/caller of it) actually uses
    (`MAPEAMENTO_REDE`, `DIOPS`, `SIP`, `RPC`, `ANS_TISS`, `QUALIFICACAO` — `_REPORT_PERIODICIDADE`
    above). Evaluating the real engine with any of the 6 known report types hits the DMN's
    catch-all (`fonte_regulatoria="REVISAO_HUMANA"`), which would flip `deve_enviar` to False
    for every currently-recognized report type — a functional regression (the ANS regulatory
    scheduler would stop recognizing anything), not a safe "DMN wins" relabeling. Per ADR-0028
    §7's explicit gate ("only after 100% parity in CI: delete the Python re-implementation"),
    parity does NOT hold here, so the Python is intentionally NOT replaced. Flagged for
    spec-side reconciliation of the `report_type` taxonomy (out of this task's `spec/` editing
    authority, constraint 5) — see the T1.5 PR body / evidence ledger for the parity evidence.
    """
    report_type = variables.get("report_type", "")
    periodicidade = _REPORT_PERIODICIDADE.get(report_type, "P1M")
    competencia = variables.get("competencia", "")

    # Always trigger for known report types
    deve_enviar = report_type in _REPORT_PERIODICIDADE

    logger.info(
        "ans_cron_check_calendar",
        report_type=report_type,
        periodicidade=periodicidade,
        deve_enviar=deve_enviar,
        competencia=competencia,
    )

    return {
        "deve_enviar": deve_enviar,
        "periodicidade": periodicidade,
        "competencia": competencia,
        "motivo": "calendario_ok" if deve_enviar else "report_type_desconhecido",
    }


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a). No custom
# error class in this module (pure scheduler, all terminals NEUTRAL).
#
# Topic mapping: spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn
# declares ONLY the shared/out-of-scope `operadora.events.publish` topic for
# every timer branch (this module's own docstring: "Dispatches via
# operadora.events.publish (reused generic publisher)") — there is no
# per-module BPMN topic to bind to (a single registry key can only serve one
# handler, and `operadora.events.publish` is shared across all 16 BPMNs, out
# of scope for T1.2 — see ADR-0026 §2b note). Both functions are registered
# under function-derived topics so ans_cron is not the one module of 16 left
# out of the registry; wiring the real dispatch of `operadora.events.publish`
# is a follow-up (a generic, cross-cutting event-publish worker), not
# fabricated here.
# ---------------------------------------------------------------


def register_ans_cron_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ANS-CRON-001 function workers on `harness`."""
    del kafka, seams  # unused — no ans_cron.py worker declares a Kafka/other seam dependency
    harness.register_worker(FunctionWorker("operadora.ans_cron.trigger_submissions", trigger_submissions))
    harness.register_worker(FunctionWorker("operadora.ans_cron.check_calendar", check_calendar))
