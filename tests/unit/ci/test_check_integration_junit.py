from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ci.check_live_junit import inspect_junit, main, validate_junit


def _write(tmp_path: Path, cases: str) -> Path:
    report = tmp_path / "report.xml"
    report.write_text(f'<testsuites><testsuite name="live">{cases}</testsuite></testsuites>')
    return report


def _collection(tmp_path: Path, *nodeids: str) -> Path:
    collection = tmp_path / "collection.txt"
    collection.write_text("\n".join((*nodeids, "3/3 tests collected")))
    return collection


def test_accepts_execution_and_keeps_mutation_skip_and_xfail_visible(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        """
        <testcase classname="suite" name="passed" />
        <testcase classname="suite" name="mutation">
          <skipped message="only runs when MAEZO_CHAOS_MUTATE=x" />
        </testcase>
        <testcase classname="suite" name="expected">
          <skipped type="pytest.xfail" message="known strict xfail" />
        </testcase>
        """,
    )
    collection = _collection(tmp_path, "suite.py::passed", "suite.py::mutation", "suite.py::expected")
    summary = validate_junit(report, collection)
    assert (summary.total, summary.executed) == (3, 1)
    assert summary.collected == 3
    assert len(summary.mutation_skipped) == 1
    assert len(summary.xfailed) == 1
    assert summary.unexpected_skips == ()


@pytest.mark.parametrize(
    "reason",
    [
        "COULD NOT VERIFY: Postgres unreachable",
        "no reachable Kafka broker",
        "CIB Seven indisponível",
    ],
)
def test_rejects_every_existing_infrastructure_skip_vocabulary(tmp_path: Path, reason: str) -> None:
    report = _write(
        tmp_path,
        f'<testcase classname="suite" name="live"><skipped message="{reason}" /></testcase>',
    )
    with pytest.raises(ValueError, match="skip ordinário em lane live"):
        validate_junit(report)
    assert main([str(report)]) == 1


def test_rejects_zero_cases_and_zero_executed(tmp_path: Path) -> None:
    empty = _write(tmp_path, "")
    with pytest.raises(ValueError, match="nenhum caso coletado"):
        validate_junit(empty)

    only_mutation = _write(
        tmp_path,
        '<testcase classname="suite" name="mutation">'
        '<skipped message="only runs when MAEZO_CHAOS_MUTATE=x" /></testcase>',
    )
    with pytest.raises(ValueError, match="nenhum caso efetivamente executado"):
        validate_junit(only_mutation)


def test_inspection_reports_test_failures_even_if_the_caller_ignored_pytest_exit(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        '<testcase classname="suite" name="broken"><failure message="assertion failed" /></testcase>',
    )
    summary = inspect_junit(report)
    assert summary.failures == 1
    with pytest.raises(ValueError, match="failures=1"):
        validate_junit(report)


def test_rejects_incomplete_junit_against_the_saved_collection(tmp_path: Path) -> None:
    report = _write(tmp_path, '<testcase classname="suite" name="only_one" />')
    collection = _collection(tmp_path, "suite.py::only_one", "suite.py::never_reported")
    with pytest.raises(ValueError, match="resultado incompleto: collected=2, junit_total=1"):
        validate_junit(report, collection)


def test_rejects_an_ordinary_non_infrastructure_skip_too(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        '<testcase classname="suite" name="hidden"><skipped message="optional today" /></testcase>',
    )
    with pytest.raises(ValueError, match="skip ordinário em lane live"):
        validate_junit(report)
