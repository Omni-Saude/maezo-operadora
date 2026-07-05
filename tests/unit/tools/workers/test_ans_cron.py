"""Unit tests for maezo.tools.workers.ans_cron (SP-OP-ANS-CRON-001).

TDD London School: tests verify scheduler dispatch and calendar checks.
"""

from maezo.tools.workers.ans_cron import check_calendar, trigger_submissions

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
