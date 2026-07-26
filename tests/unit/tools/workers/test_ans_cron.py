"""Unit tests for maezo.tools.workers.ans_cron (SP-OP-ANS-CRON-001).

TDD London School: tests verify scheduler dispatch and calendar checks.
"""

from typing import Any

from maezo.tools.workers.ans_cron import check_calendar, register_ans_cron_workers, trigger_submissions
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
