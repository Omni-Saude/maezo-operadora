r"""Unit tests for the ledger row cell-count gate (LEDGER-ROW-CELL-COUNT).

Three layers:

1. **Pure-core** tests drive `split_row_cells` / `find_expected_cell_count` / `check_lines`
   against synthetic strings — RED on an unescaped `\|` growing a row's cell count, GREEN on a
   properly-escaped `\|`, non-vacuity for "0 rows checked".
2. **Real tmp git repo** tests drive `main` end-to-end (no mocked git) — a malformed row ADDED in
   range fails; a malformed row that already existed at `<base>` (pre-existing on "main") is
   ignored by the scoped path, proving the same Scope discipline `check_evidence_ledger_hashes.py`
   established.
3. **Real-tree** tests run the gate against the SHIPPED `docs/evidence-ledger.md`: the scoped
   (default) invocation passes non-vacuously, and `--all` reproduces the exact 30 pre-existing
   malformed rows the reembolso gatekeeper counted live (regression proof for the review-queue
   inventory this task discloses).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.ci.check_ledger_row_cell_count import (
    RowCellCountFinding,
    check_lines,
    find_expected_cell_count,
    is_separator_row,
    main,
    split_row_cells,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_LEDGER = _REPO_ROOT / "docs" / "evidence-ledger.md"

_HEADER_LINE = (
    "| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | "
    "Evidence (path:line) | Test hash | Status |"
)
_SEPARATOR_LINE = "|---|---|---|---|---|---|---|---|"
_LEDGER_HEADER = (
    "# Evidence Ledger\n\nTest hash convention: ...\n\n" + _HEADER_LINE + "\n" + _SEPARATOR_LINE + "\n"
)


def _row(task_id: str, *, extra_cells: int = 0, unescaped_pipe: bool = False) -> str:
    """Build one well-formed 8-cell row, optionally with EXTRA cells (simulating an unescaped `|`
    inside the Evidence prose cell) or a genuinely unescaped `|` character."""
    evidence = "some evidence"
    if unescaped_pipe:
        evidence = "cmd1 | cmd2 (unescaped pipe)"
    cells = [
        task_id,
        "2026-09-05",
        "author (R2)",
        "verifier (R2)",
        "deadbeef",
        evidence,
        "sha256:" + "a" * 64,
        "status",
    ]
    if extra_cells:
        cells = cells + ["extra"] * extra_cells
    return "| " + " | ".join(cells) + " |"


# =================================================================================================
# split_row_cells / is_separator_row / find_expected_cell_count (pure)
# =================================================================================================


class TestSplitRowCells:
    def test_a_well_formed_8_cell_row_splits_into_8_cells(self) -> None:
        cells = split_row_cells(_row("T-1"))
        assert cells is not None
        assert len(cells) == 8
        assert cells[0].strip() == "T-1"

    def test_an_unescaped_pipe_inside_a_cell_grows_the_count(self) -> None:
        # THE defect this gate exists for: a literal `|` inside the Evidence cell, un-escaped,
        # silently produces an extra cell.
        cells = split_row_cells(_row("T-2", unescaped_pipe=True))
        assert cells is not None
        assert len(cells) == 9  # 8 real cells + the unescaped pipe splitting "cmd1 | cmd2" in two

    def test_an_escaped_pipe_does_not_grow_the_count(self) -> None:
        line = "| T-3 | 2026-09-05 | a | v | sha | cmd1 \\| cmd2 | sha256:" + "a" * 64 + " | status |"
        cells = split_row_cells(line)
        assert cells is not None
        assert len(cells) == 8

    def test_non_row_line_returns_none(self) -> None:
        assert split_row_cells("Some prose paragraph, not a table row.") is None
        assert split_row_cells("") is None

    def test_the_real_14_cell_repro_row_is_correctly_counted(self) -> None:
        # The exact live shape the reembolso gatekeeper found: several unescaped pipes.
        line = (
            "| T-REPRO | 2026-09-05 | a | v | sha | one | two | three | four | five | six | seven | sha256:"
            + ("a" * 64)
            + " | status |"
        )
        cells = split_row_cells(line)
        assert cells is not None
        assert len(cells) == 14


class TestIsSeparatorRow:
    def test_plain_separator_is_a_separator(self) -> None:
        assert is_separator_row(_SEPARATOR_LINE) is True

    def test_separator_with_alignment_markers_is_a_separator(self) -> None:
        assert is_separator_row("|:---|:---:|---:|") is True

    def test_header_row_is_not_a_separator(self) -> None:
        assert is_separator_row(_HEADER_LINE) is False

    def test_data_row_is_not_a_separator(self) -> None:
        assert is_separator_row(_row("T-1")) is False


class TestFindExpectedCellCount:
    def test_finds_8_for_the_standard_header(self) -> None:
        assert find_expected_cell_count(_LEDGER_HEADER) == 8

    def test_returns_none_when_no_header_present(self) -> None:
        assert find_expected_cell_count("no header here\njust prose\n") is None

    def test_finds_the_real_shipped_headers_width(self) -> None:
        assert find_expected_cell_count(_SHIPPED_LEDGER.read_text(encoding="utf-8")) == 8


# =================================================================================================
# check_lines (pure)
# =================================================================================================


class TestCheckLines:
    def test_a_well_formed_row_is_not_a_finding(self) -> None:
        findings, rows_checked = check_lines([_row("T-1")], expected_cells=8, with_line_numbers=False)
        assert findings == []
        assert rows_checked == 1

    def test_a_row_with_an_unescaped_pipe_is_a_finding(self) -> None:
        findings, rows_checked = check_lines(
            [_row("T-2", unescaped_pipe=True)], expected_cells=8, with_line_numbers=False
        )
        assert rows_checked == 1
        assert len(findings) == 1
        assert findings[0] == RowCellCountFinding(
            task_id="T-2", actual_cells=9, expected_cells=8, line_no=None
        )

    def test_header_and_separator_lines_are_excluded_from_rows_checked(self) -> None:
        findings, rows_checked = check_lines(
            [_HEADER_LINE, _SEPARATOR_LINE, _row("T-1")], expected_cells=8, with_line_numbers=False
        )
        assert findings == []
        assert rows_checked == 1  # only the data row, not header/separator

    def test_non_row_lines_are_silently_ignored_not_counted(self) -> None:
        findings, rows_checked = check_lines(
            ["", "prose paragraph", _row("T-1")], expected_cells=8, with_line_numbers=False
        )
        assert findings == []
        assert rows_checked == 1

    def test_zero_data_rows_is_not_a_finding_but_visible_in_rows_checked(self) -> None:
        findings, rows_checked = check_lines(
            [_HEADER_LINE, _SEPARATOR_LINE], expected_cells=8, with_line_numbers=False
        )
        assert findings == []
        assert rows_checked == 0  # non-vacuity: distinguishable from "checked and all passed"

    def test_with_line_numbers_true_reports_the_real_1_indexed_position(self) -> None:
        lines = [_HEADER_LINE, _SEPARATOR_LINE, _row("T-OK"), _row("T-BAD", extra_cells=2)]
        findings, rows_checked = check_lines(lines, expected_cells=8, with_line_numbers=True)
        assert rows_checked == 2
        assert len(findings) == 1
        assert findings[0].line_no == 4  # 1-indexed: T-BAD is the 4th line in the list
        assert findings[0].task_id == "T-BAD"

    def test_with_line_numbers_false_never_reports_a_line_number(self) -> None:
        findings, _rows_checked = check_lines(
            [_row("T-BAD", extra_cells=1)], expected_cells=8, with_line_numbers=False
        )
        assert findings[0].line_no is None

    def test_multiple_malformed_rows_are_all_reported_independently(self) -> None:
        lines = [_row("T-A", extra_cells=1), _row("T-B"), _row("T-C", extra_cells=3)]
        findings, rows_checked = check_lines(lines, expected_cells=8, with_line_numbers=False)
        assert rows_checked == 3
        assert {f.task_id for f in findings} == {"T-A", "T-C"}


class TestRowCellCountFindingRender:
    def test_render_without_line_number_says_linha_nova(self) -> None:
        finding = RowCellCountFinding(task_id="T-X", actual_cells=9, expected_cells=8, line_no=None)
        rendered = finding.render()
        assert "T-X" in rendered
        assert "linha nova" in rendered
        assert "9" in rendered and "8" in rendered

    def test_render_with_line_number_includes_the_file_position(self) -> None:
        finding = RowCellCountFinding(task_id="T-Y", actual_cells=10, expected_cells=8, line_no=42)
        rendered = finding.render()
        assert "evidence-ledger.md:42" in rendered


# =================================================================================================
# main: real tmp git repo (no mocked git) — Scope discipline
# =================================================================================================


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo_with_a_pre_existing_malformed_row(tmp_path: Path) -> tuple[Path, str]:
    """Real git repo whose BASE commit already carries one malformed (9-cell) row — the exact
    "pre-existing on main" shape this gate must never flag."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "docs").mkdir()
    ledger = repo / "docs" / "evidence-ledger.md"
    ledger.write_text(_LEDGER_HEADER + _row("PRE-EXISTING-BAD", extra_cells=1) + "\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "base with a pre-existing malformed row"], repo)
    base_sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    return repo, base_sha


class TestMainScopeDiscipline:
    def test_a_pre_existing_malformed_row_on_main_does_not_fail_the_scoped_gate(self, tmp_path: Path) -> None:
        repo, base_sha = _init_repo_with_a_pre_existing_malformed_row(tmp_path)
        # No new commit at all — the scoped range is empty, the pre-existing bad row must be
        # invisible to it (identical Scope discipline to check_evidence_ledger_hashes.py).
        exit_code = main(["--base", base_sha], repo_root=repo)
        assert exit_code == 0

    def test_a_newly_added_malformed_row_fails_the_scoped_gate(self, tmp_path: Path) -> None:
        repo, base_sha = _init_repo_with_a_pre_existing_malformed_row(tmp_path)
        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(
            ledger.read_text(encoding="utf-8") + _row("NEW-BAD", extra_cells=2) + "\n", encoding="utf-8"
        )
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add a new malformed row"], repo)

        exit_code = main(["--base", base_sha], repo_root=repo)
        assert exit_code == 1

    def test_a_newly_added_well_formed_row_passes_even_with_a_pre_existing_bad_one(
        self, tmp_path: Path
    ) -> None:
        repo, base_sha = _init_repo_with_a_pre_existing_malformed_row(tmp_path)
        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(ledger.read_text(encoding="utf-8") + _row("NEW-OK") + "\n", encoding="utf-8")
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add a well-formed row"], repo)

        exit_code = main(["--base", base_sha], repo_root=repo)
        assert exit_code == 0

    def test_all_flag_finds_the_pre_existing_malformed_row_with_its_line_number(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo, _base_sha = _init_repo_with_a_pre_existing_malformed_row(tmp_path)
        exit_code = main(["--all"], repo_root=repo)
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "PRE-EXISTING-BAD" in captured.err
        assert "evidence-ledger.md:" in captured.err

    def test_missing_header_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "docs" / "evidence-ledger.md").write_text("no header at all\n", encoding="utf-8")
        exit_code = main(["--all"], repo_root=repo)
        assert exit_code == 1


# =================================================================================================
# Real-tree regression proofs
# =================================================================================================


class TestAgainstTheRealShippedLedger:
    def test_scoped_default_invocation_passes_non_vacuously(self) -> None:
        # Scoped against origin/main's merge-base: the CI-shape invocation. Requires the real repo
        # (not a synthetic fixture) so this is a genuine regression proof, not just a unit test.
        exit_code = main([], repo_root=_REPO_ROOT)
        assert exit_code == 0

    def test_all_mode_reproduces_the_30_pre_existing_malformed_rows(self) -> None:
        from scripts.ci.check_ledger_row_cell_count import find_expected_cell_count as _fecc

        ledger_text = _SHIPPED_LEDGER.read_text(encoding="utf-8")
        expected = _fecc(ledger_text)
        assert expected == 8
        findings, rows_checked = check_lines(
            ledger_text.splitlines(), expected_cells=expected, with_line_numbers=True
        )
        assert rows_checked > 30  # non-vacuity: real ledger has hundreds of rows
        assert len(findings) == 30  # the exact count the reembolso gatekeeper counted live
        assert all(f.line_no is not None for f in findings)
