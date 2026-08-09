"""Unit tests for maezo.tools.workers.ans_cron (SP-OP-ANS-CRON-001).

TDD London School: tests verify scheduler dispatch and calendar checks.
"""

from typing import Any

import pytest

from maezo.tools.workers.ans_cron import (
    _compute_competencia,
    check_calendar,
    register_ans_cron_workers,
    trigger_submissions,
)
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

# ---------------------------------------------------------------
# trigger_submissions
# ---------------------------------------------------------------


def test_ans_cron_trigger_submission() -> None:
    result = trigger_submissions(
        {
            "report_type": "DIOPS",
        }
    )
    assert result["fato_publicado"] is True
    assert result["event_type"] == "ans.cron_due"
    assert result["report_type"] == "DIOPS"
    assert result["periodicidade"] == "P3M"
    assert result["competencia"] != "COMPETENCIA_PENDENTE"
    assert result["ans_cron_reference_date_iso"] is not None


def test_ans_cron_trigger_different_reports() -> None:
    for report_type, periodicidade in [
        ("MAPEAMENTO_REDE", "P1M"),
        ("SIP", "P1M"),
        ("RPC", "P1M"),
        ("QUALIFICACAO", "P12M"),
    ]:
        result = trigger_submissions({"report_type": report_type})
        assert result["periodicidade"] == periodicidade
        assert result["report_type"] == report_type


def test_ans_cron_trigger_unknown_report() -> None:
    result = trigger_submissions({"report_type": "UNKNOWN_REPORT"})
    assert result["periodicidade"] == "P1M"  # default


def test_ans_cron_trigger_submission_defaults_tenant_id_to_blank() -> None:
    """T2.6-EB3 part 3: tenant_id defaults to "" fail-closed for any caller that predates the
    new keyword-only parameter — no behavior change for existing positional-only callers."""
    result = trigger_submissions({"report_type": "DIOPS"})
    assert result["tenant_id"] == ""


def test_ans_cron_trigger_submission_carries_tenant_id_when_passed() -> None:
    """T2.6-EB3 part 3 — worker-signature fix: trigger_submissions now accepts and echoes a
    tenant_id (source: the deployment's own identity, threaded via register_ans_cron_workers)."""
    result = trigger_submissions({"report_type": "DIOPS"}, tenant_id="amh")
    assert result["tenant_id"] == "amh"


def test_register_ans_cron_workers_threads_tenant_id_seam() -> None:
    """T2.6-EB3 part 3: register_ans_cron_workers(harness, tenant_id=...) binds it into
    trigger_submissions via functools.partial — proven end-to-end through the registry, not just
    by calling trigger_submissions directly."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="ans-cron-tenant-probe")
    register_ans_cron_workers(harness, tenant_id="amh")

    worker = harness.registry.get("operadora.ans_cron.trigger_submissions")
    assert worker is not None
    result: dict[str, Any] = worker.run({"report_type": "DIOPS"})
    assert result["tenant_id"] == "amh"


def test_register_ans_cron_workers_defaults_tenant_id_to_blank_when_not_passed() -> None:
    """No seam passed -> "" fail-closed, never fabricated (unchanged behavior for any existing
    caller of register_ans_cron_workers that doesn't know about this seam yet)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="ans-cron-tenant-probe-2")
    register_ans_cron_workers(harness)

    worker = harness.registry.get("operadora.ans_cron.trigger_submissions")
    assert worker is not None
    result: dict[str, Any] = worker.run({"report_type": "DIOPS"})
    assert result["tenant_id"] == ""


# ---------------------------------------------------------------
# _compute_competencia — table-driven, all 12 months x {P1M, P3M, P12M} (item 3)
#
# The competencia is the calendar period IMMEDIATELY BEFORE — already CLOSED as of — the
# reference date; the period CONTAINING the reference date is never itself a valid answer.
# Reference year 2026 throughout (year-wrap vectors are the Jan/P1M and Jan-Mar/P3M rows, which
# roll back into 2025).
# ---------------------------------------------------------------

#: (reference month in 2026, expected P1M competencia, expected P3M competencia)
#: P3M expectation encodes the FIX: all 3 months of a quarter resolve to the SAME prior, CLOSED
#: quarter — pre-fix, only the quarter's first month (Jan/Apr/Jul/Oct) did; the other 8 months
#: wrongly returned the CURRENT, still-open quarter's own start month instead.
_COMPETENCIA_TABLE: list[tuple[int, str, str]] = [
    (1, "2025-12", "2025-10"),  # Q1 month 1 -> still-open Q1 -> prior CLOSED quarter is Q4/2025
    (2, "2026-01", "2025-10"),  # Q1 month 2 (mid-quarter) -> same prior quarter, Q4/2025
    (3, "2026-02", "2025-10"),  # Q1 month 3 (quarter-end) -> same prior quarter, Q4/2025
    (4, "2026-03", "2026-01"),  # Q2 month 1 -> prior CLOSED quarter is Q1/2026
    (5, "2026-04", "2026-01"),  # Q2 month 2 (mid-quarter) -> same prior quarter, Q1/2026
    (6, "2026-05", "2026-01"),  # Q2 month 3 (quarter-end) -> same prior quarter, Q1/2026
    (7, "2026-06", "2026-04"),  # Q3 month 1 -> prior CLOSED quarter is Q2/2026
    (8, "2026-07", "2026-04"),  # Q3 month 2 (mid-quarter) -> same prior quarter, Q2/2026
    (9, "2026-08", "2026-04"),  # Q3 month 3 (quarter-end) -> same prior quarter, Q2/2026
    (10, "2026-09", "2026-07"),  # Q4 month 1 -> prior CLOSED quarter is Q3/2026
    (11, "2026-10", "2026-07"),  # Q4 month 2 (mid-quarter) -> same prior quarter, Q3/2026
    (12, "2026-11", "2026-07"),  # Q4 month 3 (quarter-end) -> same prior quarter, Q3/2026
]


@pytest.mark.parametrize(("month", "expected_p1m", "expected_p3m"), _COMPETENCIA_TABLE)
def test_compute_competencia_p1m_table(month: int, expected_p1m: str, expected_p3m: str) -> None:
    """P1M (monthly): always the previous month, every month of the year, incl. the Jan->Dec/y-1 wrap."""
    del expected_p3m
    assert _compute_competencia(f"2026-{month:02d}-15", "P1M") == expected_p1m


@pytest.mark.parametrize(("month", "expected_p1m", "expected_p3m"), _COMPETENCIA_TABLE)
def test_compute_competencia_p3m_table(month: int, expected_p1m: str, expected_p3m: str) -> None:
    """P3M (quarterly) REGRESSION PIN: every month of a quarter — first, mid, and last — resolves
    to the SAME most-recently-CLOSED quarter. Pre-fix, only Jan/Apr/Jul/Oct did; Feb/Mar,
    May/Jun, Aug/Sep and Nov/Dec each wrongly returned their own CURRENT, still-open quarter."""
    del expected_p1m
    assert _compute_competencia(f"2026-{month:02d}-15", "P3M") == expected_p3m


@pytest.mark.parametrize("month", [m for m, _, _ in _COMPETENCIA_TABLE])
def test_compute_competencia_p12m_table_is_always_the_prior_calendar_year(month: int) -> None:
    """P12M (annual) REGRESSION PIN: every month of the year resolves to the previous calendar
    YEAR, "YYYY" — never a month within it. Pre-fix, P12M had no dedicated branch and fell
    through to the P1M (monthly) logic, so QUALIFICACAO got the previous MONTH instead of the
    previous YEAR — wrong period unit entirely, every month of the year."""
    assert _compute_competencia(f"2026-{month:02d}-15", "P12M") == "2025"


def test_compute_competencia_p12m_year_boundary() -> None:
    """The P12M year rollback is unconditional on month — pinned explicitly at both ends of the
    year (January and December of the same reference year still both resolve to year-1)."""
    assert _compute_competencia("2026-01-01", "P12M") == "2025"
    assert _compute_competencia("2026-12-31", "P12M") == "2025"


def test_compute_competencia_unmapped_periodicidade_defaults_to_monthly() -> None:
    """An unrecognized `periodicidade` keeps the pre-existing fallback: treated as P1M (mirrors
    `_REPORT_PERIODICIDADE.get(report_type, "P1M")`'s own default one layer up)."""
    assert _compute_competencia("2026-08-15", "") == _compute_competencia("2026-08-15", "P1M")
    assert _compute_competencia("2026-08-15", "P6M") == _compute_competencia("2026-08-15", "P1M")


def test_compute_competencia_invalid_reference_date_is_pending_not_a_crash() -> None:
    """An unparseable reference_date_iso fails closed to the sentinel, for every periodicidade —
    never raises, never silently defaults to a real-looking competencia."""
    for periodicidade in ("P1M", "P3M", "P12M"):
        assert _compute_competencia("not-a-date", periodicidade) == "COMPETENCIA_PENDENTE"


# ---------------------------------------------------------------
# check_calendar
# ---------------------------------------------------------------


def test_check_calendar_known_report() -> None:
    result = check_calendar(
        {
            "report_type": "DIOPS",
            "competencia": "2024-Q1",
        }
    )
    assert result["deve_enviar"] is True
    assert result["periodicidade"] == "P3M"


def test_check_calendar_unknown_report() -> None:
    result = check_calendar(
        {
            "report_type": "UNKNOWN",
            "competencia": "",
        }
    )
    assert result["deve_enviar"] is False
    assert result["motivo"] == "report_type_desconhecido"


def test_check_calendar_all_known_reports() -> None:
    """All registered report types should return deve_enviar=True."""
    for report_type in ["MAPEAMENTO_REDE", "DIOPS", "SIP", "RPC", "ANS_TISS", "QUALIFICACAO"]:
        result = check_calendar({"report_type": report_type})
        assert result["deve_enviar"] is True, f"{report_type} should be known"
