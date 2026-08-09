"""Worker: ans_cron (SP-OP-ANS-CRON-001).

Agendador per-report_type dos Envios Periodicos ANS.
Pure scheduler — NO adverse effects, all terminals NEUTRAL.
Dispatches via operadora.events.publish (reused generic publisher).
"""

from __future__ import annotations

import functools
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
    """Compute the competencia from the reference date, per `periodicidade` (`_REPORT_PERIODICIDADE`).

    The competencia is the calendar period IMMEDIATELY BEFORE — i.e. already CLOSED as of — the
    reference date. The still-OPEN period containing the reference date is never itself a valid
    answer (it has not finished yet):
      - P1M (monthly):   the previous month, "YYYY-MM".
      - P3M (quarterly): the most recently CLOSED quarter's start month, "YYYY-MM" — e.g. a
        reference date anywhere in Apr/May/Jun (all of Q2, still open) resolves to the SAME
        "YYYY-01" (Q1, the quarter that already ended), never "YYYY-04" (Q2 itself).
      - P12M (annual):   the previous calendar year in full, "YYYY" — never a month within it.
      - anything else:   treated as P1M (pre-existing default for an unmapped periodicidade).

    DRAFT/verify regulatorio: mapeamento confirmado com ANS.
    """
    try:
        ref_date = datetime.fromisoformat(reference_date_iso)
    except (ValueError, TypeError):
        return "COMPETENCIA_PENDENTE"

    if periodicidade == "P12M":
        # Annual: the previous calendar year, in full. Pre-fix this fell through to the monthly
        # branch below and returned the previous MONTH for an annual report — wrong period unit
        # entirely, not merely off by a quarter.
        return f"{ref_date.year - 1:04d}"

    if periodicidade == "P3M":
        # Quarterly: the most recently CLOSED quarter — computed the SAME way regardless of
        # which of the 3 months of the current quarter the reference date falls in. Pre-fix, only
        # the FIRST month of a quarter (Jan/Apr/Jul/Oct) took this "go back one quarter" step;
        # the other 8 months returned the CURRENT, still-open quarter's own start month instead
        # (a `quarter_month == month` special-case that only ever matched the first month).
        quarter_start = ((ref_date.month - 1) // 3) * 3 + 1  # start month of the CURRENT quarter
        prev_quarter_start = quarter_start - 3
        if prev_quarter_start < 1:
            prev_quarter_start += 12
            year = ref_date.year - 1
        else:
            year = ref_date.year
        return f"{year:04d}-{prev_quarter_start:02d}"

    # P1M (monthly) and anything else (pre-existing default): the previous month.
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


def trigger_submissions(variables: dict[str, Any], *, tenant_id: str = "") -> dict[str, Any]:
    """Publish the ans.cron_due fact for the notification bridge.

    Each tick of the timer produces a typed fact on
    operadora.notifications.internal with type=ans.cron_due.
    The notifications_bridge then starts SP-OP-ANS-SUBMIT-001.

    NO adverse effects — pure scheduling dispatch.

    `tenant_id` (T2.6-EB3 part 3 — worker-signature change, keyword-only, threaded via
    `register_ans_cron_workers(harness, tenant_id=...)`): SP-OP-ANS-CRON-001 is a
    TimerStartEvent-triggered scheduler with NO per-instance case context (unlike a
    case-driven process such as SP-OP-NIP-001), so there is no process variable to read a real
    tenant from — the source here is the DEPLOYMENT's own identity
    (`WorkerRuntimeSettings.tenant_id`/`TENANT_ID`), passed in at worker registration. HONEST
    RESIDUAL BOUNDARY (not fixed here — see `notification_bridge._ans_submit_variables_from_
    cron_due`'s docstring): this function itself is UNREACHABLE in the real deployed BPMN today
    (FINDING #1c — the engine binds `ST_PublishCronDue*` directly to the generic
    `operadora.events.publish` topic with literal `camunda:inputParameter`s, never to
    `operadora.ans_cron.trigger_submissions`), and that generic worker's own `event_payload_vars`
    literal does not list `tenant_id` — so the REAL live Kafka fact still omits it until a
    `spec/` edit extends that BPMN literal (outside this task's edit authority).
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
        tenant_id=tenant_id,
    )

    return {
        "fato_publicado": True,
        "event_type": "ans.cron_due",
        "report_type": report_type,
        "periodicidade": periodicidade,
        "competencia": competencia,
        "ans_cron_reference_date_iso": reference_date,
        "tenant_id": tenant_id,
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
    """Register the SP-OP-ANS-CRON-001 function workers on `harness`.

    `tenant_id` (T2.6-EB3 part 3 seam, `**seams` catch-all — mirrors `ans_gateway`/
    `tiss_validator` in `ans_submit.py`): threaded ONLY into `trigger_submissions` (see its
    docstring for why this scheduler needs a deployment-scoped seam rather than a per-instance
    process variable). Defaults to `""` fail-closed when the composition root does not pass one
    (unchanged behavior for every existing caller that doesn't know about this seam yet).
    """
    del kafka  # unused — no ans_cron.py worker declares a Kafka dependency
    tenant_id = str(seams.get("tenant_id", ""))
    harness.register_worker(
        FunctionWorker(
            "operadora.ans_cron.trigger_submissions",
            functools.partial(trigger_submissions, tenant_id=tenant_id),
        )
    )
    harness.register_worker(FunctionWorker("operadora.ans_cron.check_calendar", check_calendar))
