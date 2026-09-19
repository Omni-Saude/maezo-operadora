"""Unit tests for the D7-catalogue expected-fail expiry gate (owner override, 2026-09-12).

Two layers:

1. **Pure-core** tests drive `evaluate` against synthetic tracked entries and synthetic `.py`
   fixture files written to a tmp path — RED on a missing file, a missing/malformed xfail marker, a
   non-strict xfail, a `reason=` whose date drifted from the tracked deadline, and an expired
   deadline; GREEN when everything agrees and today is before the deadline.
2. **Real-tree** tests run the comparator against the SHIPPED `TRACKED_XFAILS` registry and the two
   real test files it names, and assert PASS as of a pinned "today" well before 2026-11-11 — the
   regression proof that both markers actually carry the shape this gate expects. A second real-tree
   assertion simulates `--today 2026-11-12` (one day past the deadline) and expects FAIL, proving the
   gate is not vacuously green.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from scripts.ci.check_d7_xfail_expiry import TRACKED_XFAILS, TrackedXfail, evaluate, main

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BEFORE_DEADLINE = date(2026, 9, 12)
_ON_DEADLINE = date(2026, 11, 11)
_AFTER_DEADLINE = date(2026, 11, 12)


def _write(tmp_path: Path, relative: str, body: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _entry(**overrides: object) -> TrackedXfail:
    base: dict[str, object] = {
        "path": "pkg/test_synthetic.py",
        "test_name": "test_thing",
        "deadline": _ON_DEADLINE,
        "what_expiry_means": "synthetic",
    }
    base.update(overrides)
    return TrackedXfail(**base)  # type: ignore[arg-type]


_VALID_BODY = (
    "import pytest\n\n"
    "@pytest.mark.xfail(strict=True, reason='owner decision, expected-fail-until=2026-11-11')\n"
    "def test_thing() -> None:\n"
    "    assert False\n"
)


def test_pass_when_marker_matches_and_today_is_before_deadline(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/test_synthetic.py", _VALID_BODY)
    assert evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE) == []


def test_pass_on_the_deadline_itself(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/test_synthetic.py", _VALID_BODY)
    assert evaluate(tmp_path, (_entry(),), _ON_DEADLINE) == []


def test_fail_when_file_is_missing(tmp_path: Path) -> None:
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "arquivo nao encontrado" in findings[0].reason


def test_fail_when_function_is_missing(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/test_synthetic.py", "def test_other() -> None:\n    pass\n")
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "nenhuma `def test_thing`" in findings[0].reason


def test_fail_when_xfail_marker_is_absent(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/test_synthetic.py", "def test_thing() -> None:\n    assert False\n")
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "nenhum decorador" in findings[0].reason


def test_fail_when_xfail_is_not_strict(tmp_path: Path) -> None:
    body = (
        "import pytest\n\n"
        "@pytest.mark.xfail(strict=False, reason='owner decision, expected-fail-until=2026-11-11')\n"
        "def test_thing() -> None:\n"
        "    assert False\n"
    )
    _write(tmp_path, "pkg/test_synthetic.py", body)
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "strict=True" in findings[0].reason


def test_fail_when_reason_date_drifted_from_tracked_deadline(tmp_path: Path) -> None:
    body = (
        "import pytest\n\n"
        "@pytest.mark.xfail(strict=True, reason='owner decision, expected-fail-until=2099-01-01')\n"
        "def test_thing() -> None:\n"
        "    assert False\n"
    )
    _write(tmp_path, "pkg/test_synthetic.py", body)
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "nao contem a data rastreada" in findings[0].reason


def test_fail_when_reason_is_not_a_literal_string(tmp_path: Path) -> None:
    body = (
        "import pytest\n\n"
        "REASON = 'computed at import time'\n\n"
        "@pytest.mark.xfail(strict=True, reason=REASON)\n"
        "def test_thing() -> None:\n"
        "    assert False\n"
    )
    _write(tmp_path, "pkg/test_synthetic.py", body)
    findings = evaluate(tmp_path, (_entry(),), _BEFORE_DEADLINE)
    assert len(findings) == 1
    assert "literal constante" in findings[0].reason


def test_fail_expired_deadline_names_renewal_procedure(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/test_synthetic.py", _VALID_BODY)
    findings = evaluate(tmp_path, (_entry(),), _AFTER_DEADLINE)
    assert len(findings) == 1
    assert "PRAZO VENCIDO" in findings[0].reason
    assert "COMO SAIR DO VERMELHO" in findings[0].reason


def test_two_independent_entries_report_two_independent_findings(tmp_path: Path) -> None:
    _write(tmp_path, "a/test_a.py", "def test_thing() -> None:\n    pass\n")
    _write(tmp_path, "b/test_b.py", "def test_thing() -> None:\n    pass\n")
    findings = evaluate(
        tmp_path,
        (_entry(path="a/test_a.py"), _entry(path="b/test_b.py")),
        _BEFORE_DEADLINE,
    )
    assert len(findings) == 2


# ---------------------------------------------------------------------------------------------
# Real-tree: the shipped registry and the two real files it names.
# ---------------------------------------------------------------------------------------------


def test_real_tracked_registry_is_exactly_two_entries_both_due_2026_11_11() -> None:
    assert len(TRACKED_XFAILS) == 2
    assert {entry.deadline for entry in TRACKED_XFAILS} == {date(2026, 11, 11)}
    assert {entry.test_name for entry in TRACKED_XFAILS} == {
        "test_explicit_cli_preflight_reads_only_closed_d7_source",
        "test_exact_test_bodies_and_ledger_history_unchanged",
    }


def test_real_registry_passes_today_well_before_the_deadline() -> None:
    assert evaluate(_REPO_ROOT, TRACKED_XFAILS, _BEFORE_DEADLINE) == []


def test_real_registry_fails_the_day_after_the_deadline() -> None:
    findings = evaluate(_REPO_ROOT, TRACKED_XFAILS, _AFTER_DEADLINE)
    assert len(findings) == len(TRACKED_XFAILS)
    assert all("PRAZO VENCIDO" in f.reason for f in findings)


@pytest.mark.parametrize("argv", [["--today", "2026-09-12"]])
def test_main_returns_zero_before_deadline(argv: list[str]) -> None:
    assert main(argv) == 0


def test_main_returns_nonzero_after_deadline() -> None:
    assert main(["--today", "2026-11-12"]) == 1


def test_main_rejects_unparseable_today() -> None:
    assert main(["--today", "not-a-date"]) == 1
