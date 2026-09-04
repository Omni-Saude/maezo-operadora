"""Unit tests for scripts.ci.check_evidence_ledger_hashes (LEDGER-HASH-RECOMPUTE-CHECK).

Covers: row parsing (declared vs legacy), CONVENTION_START_DATE date-qualification (a row must be
BOTH shape-matching AND dated on/after the convention start to be declared — the real `mzo-040`
false-positive this gate must not repeat, VERIFY-LEDGER-RECOMPUTE.md finding D-2, reproduced below
against the real repo ledger, not just a synthetic fixture), diff-range selection (a real tmp git
repo — no mocked git), recipe-hash equality against a real fixture test file run for real (proving
BOTH that a hash computed WITH the pytest progress marker fails and that the STRIPPED one matches
— the 2026-09-03 VER-EVAL-REPLAY addendum's explicit requirement — through BOTH a pre-cleaned
synthetic string AND the real raw-output pipeline, finding E-2), mismatch -> non-zero, legacy rows
skipped+counted+reasoned, path-injection rejected (including the `.py`-suffixed traversal case
that is the ONLY thing the segment guard alone catches, finding E-1), and a non-vacuity proof (the
gate can actually FAIL, not just always print green).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.ci.check_evidence_ledger_hashes import (
    CONVENTION_START_DATE,
    REPO_ROOT,
    DeclaredRow,
    LegacyRow,
    RowSelection,
    compute_recipe_hash,
    extract_result_lines,
    extract_row_date,
    get_ledger_diff_added_lines,
    is_date_qualified,
    is_safe_test_path,
    is_table_row,
    main,
    parse_diff_added_lines,
    parse_row_line,
    resolve_effective_base,
    run_recipe,
    select_rows,
    strip_progress_marker,
    verify_row,
)

LEDGER_HEADER = (
    "# Evidence Ledger\n\n"
    "Test hash convention: ...\n\n"
    "| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | "
    "Evidence (path:line) | Test hash | Status |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def _declared_row_line(task_id: str, test_path: str, hash_hex: str, date: str = CONVENTION_START_DATE) -> str:
    return (
        f"| {task_id} | {date} | docs-spec-reconciler (R2) | pending (R2) | deadbeef | "
        f"evid:1 | sha256:{hash_hex} ({test_path}) | implemented — unverified |"
    )


def _legacy_row_line(task_id: str) -> str:
    return (
        f"| {task_id} | 2026-07-16 | gates-engineer (R2) | — | deadbeef | "
        "scripts/ci/check_evidence_ledger.py:1 | sha256:" + "a" * 64 + " | implemented |"
    )


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


class TestParseRowLine:
    def test_declared_row_parses(self) -> None:
        line = _declared_row_line("T-1", "tests/unit/x/test_y.py", "b" * 64)
        row = parse_row_line(line)
        assert row == DeclaredRow(task_id="T-1", test_path="tests/unit/x/test_y.py", declared_hash="b" * 64)

    def test_declared_hash_normalized_to_lowercase(self) -> None:
        line = _declared_row_line("T-1", "tests/unit/x/test_y.py", "B" * 64)
        row = parse_row_line(line)
        assert row is not None
        assert row.declared_hash == "b" * 64

    def test_legacy_row_no_path_returns_none_but_is_a_table_row(self) -> None:
        line = _legacy_row_line("T0.1")
        assert parse_row_line(line) is None
        assert is_table_row(line) is True

    def test_row_with_extra_text_inside_parens_is_legacy_not_declared(self) -> None:
        # Real ledger pattern (row ANS-CRON-DEAD-CODE): "(test_x.py, 74 passed ...)" — extra text
        # inside the parens, and no `tests/` prefix. Must NOT match the new convention.
        line = (
            "| ANS-CRON-DEAD-CODE | 2026-09-03 | x | y | b1de6f3 | ev | sha256:"
            + "c" * 64
            + " (test_ans_cron.py, 74 passed) | implemented |"
        )
        assert parse_row_line(line) is None
        assert is_table_row(line) is True

    def test_non_row_line_is_not_a_table_row(self) -> None:
        assert is_table_row("Some prose paragraph, not a table row.") is False
        assert parse_row_line("Some prose paragraph, not a table row.") is None

    def test_header_and_separator_rows_are_table_rows_but_never_declared(self) -> None:
        header = "| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA |"
        separator = "|---|---|---|---|---|"
        assert parse_row_line(header) is None
        assert parse_row_line(separator) is None

    def test_malformed_hash_short_hex_does_not_match(self) -> None:
        line = "| T-1 | 2026-09-03 | x | y | sha | ev | sha256:abc123 (tests/unit/x/test_y.py) | s |"
        assert parse_row_line(line) is None
        assert is_table_row(line) is True


class TestSelectRows:
    def test_partitions_declared_and_legacy(self) -> None:
        lines = [
            "Some convention paragraph text, not a row.",
            "",
            _declared_row_line("NEW-1", "tests/unit/a/test_b.py", "d" * 64),
            _legacy_row_line("T0.1"),
            _legacy_row_line("T0.2"),
        ]
        selection = select_rows(lines)
        expected_row = DeclaredRow(
            task_id="NEW-1", test_path="tests/unit/a/test_b.py", declared_hash="d" * 64
        )
        assert selection.declared == (expected_row,)
        assert [r.task_id for r in selection.legacy] == ["T0.1", "T0.2"]
        assert all("declared-path" in r.reason for r in selection.legacy)

    def test_empty_input_yields_empty_selection(self) -> None:
        assert select_rows([]) == RowSelection(declared=(), legacy=())

    def test_legacy_shape_row_reason_is_exact(self) -> None:
        selection = select_rows([_legacy_row_line("T0.1")])
        assert selection.legacy == (LegacyRow(task_id="T0.1", reason="no declared-path Test-hash form"),)

    def test_non_vacuity_a_row_with_no_hash_at_all_is_legacy_not_dropped(self) -> None:
        line = "| PERSP-X | 2026-09-03 | a | b | sha | ev | — (nao verificado ainda) | pendente |"
        selection = select_rows([line])
        assert selection.declared == ()
        assert [r.task_id for r in selection.legacy] == ["PERSP-X"]

    def test_shape_matching_row_before_convention_start_is_legacy_by_date(self) -> None:
        # The exact D-2 false-positive class this gate must not repeat: a row whose Test-hash
        # cell already writes the declared-path SHAPE, but whose Date cell precedes
        # CONVENTION_START_DATE — a coincidence, not an opt-in. Real example: `mzo-040`
        # (docs/evidence-ledger.md, 2026-08-09) cites
        # `sha256:070c3e2c... (tests/unit/gateway/test_action_execution_gateway.py)`.
        line = _declared_row_line(
            "mzo-040-style",
            "tests/unit/gateway/test_action_execution_gateway.py",
            "a" * 64,
            date="2026-08-09",
        )
        selection = select_rows([line])
        assert selection.declared == ()
        assert len(selection.legacy) == 1
        assert selection.legacy[0].task_id == "mzo-040-style"
        assert "precedes CONVENTION_START_DATE" in selection.legacy[0].reason
        assert CONVENTION_START_DATE in selection.legacy[0].reason

    def test_row_dated_exactly_convention_start_date_is_declared(self) -> None:
        # Boundary: the day itself qualifies (>=, not >).
        line = _declared_row_line(
            "T-BOUNDARY", "tests/unit/x/test_y.py", "d" * 64, date=CONVENTION_START_DATE
        )
        selection = select_rows([line])
        assert selection.declared == (
            DeclaredRow(task_id="T-BOUNDARY", test_path="tests/unit/x/test_y.py", declared_hash="d" * 64),
        )
        assert selection.legacy == ()

    def test_row_with_malformed_date_cell_is_legacy_not_declared(self) -> None:
        line = (
            "| T-BADDATE | not-a-date | x | y | sha | ev | sha256:" + "d" * 64 + " (tests/x/test_y.py) | s |"
        )
        selection = select_rows([line])
        assert selection.declared == ()
        assert selection.legacy[0].task_id == "T-BADDATE"
        assert "missing/unparseable" in selection.legacy[0].reason


class TestExtractRowDate:
    def test_extracts_iso_date_from_declared_row(self) -> None:
        line = _declared_row_line("T-1", "tests/unit/x/test_y.py", "b" * 64, date="2026-09-05")
        assert extract_row_date(line) == "2026-09-05"

    def test_extracts_iso_date_from_legacy_row(self) -> None:
        assert extract_row_date(_legacy_row_line("T0.1")) == "2026-07-16"

    def test_non_row_line_returns_none(self) -> None:
        assert extract_row_date("Some prose paragraph, not a table row.") is None

    def test_malformed_date_cell_returns_none(self) -> None:
        line = "| T-1 | not-a-date | x | y | sha | ev | sha256:" + "a" * 64 + " (tests/x/test_y.py) | s |"
        assert extract_row_date(line) is None


class TestIsDateQualified:
    def test_convention_start_date_itself_qualifies(self) -> None:
        assert is_date_qualified(CONVENTION_START_DATE) is True

    def test_date_after_convention_start_qualifies(self) -> None:
        assert is_date_qualified("2026-12-31") is True

    def test_date_before_convention_start_does_not_qualify(self) -> None:
        assert is_date_qualified("2026-08-09") is False  # the real mzo-040 date

    def test_none_date_does_not_qualify(self) -> None:
        assert is_date_qualified(None) is False


# ---------------------------------------------------------------------------
# Path-injection guard
# ---------------------------------------------------------------------------


class TestIsSafeTestPath:
    def test_accepts_a_normal_test_path(self) -> None:
        assert is_safe_test_path("tests/unit/ci/test_check_evidence_ledger_hashes.py") is True

    def test_rejects_parent_traversal(self) -> None:
        assert is_safe_test_path("tests/../etc/passwd") is False
        assert is_safe_test_path("tests/unit/../../etc/passwd") is False

    def test_rejects_parent_traversal_even_when_path_ends_in_py(self) -> None:
        # Non-vacuity for the segment guard itself (verifier mutation probe VERIFY-LEDGER-
        # RECOMPUTE.md §E.3): `_PATH_SAFETY_RE` (`^tests/[A-Za-z0-9_/.-]+\.py$`) ALREADY matches
        # a `..`-containing path that ends in `.py` — dot and dash are both in the character
        # class — so the explicit `".." not in path.split("/")` check is the ONLY thing rejecting
        # it. The two examples in `test_rejects_parent_traversal` above don't end in `.py`, so
        # they're already rejected by the `\.py$` suffix alone and never actually exercise the
        # segment guard; removing it left ALL other tests green. These do exercise it.
        assert is_safe_test_path("tests/../scripts/ci/check_evidence_ledger_hashes.py") is False
        assert is_safe_test_path("tests/unit/../../scripts/ci/x.py") is False

    def test_rejects_paths_outside_tests(self) -> None:
        assert is_safe_test_path("scripts/ci/check_evidence_ledger_hashes.py") is False
        assert is_safe_test_path("/etc/passwd") is False

    def test_rejects_shell_metacharacters(self) -> None:
        assert is_safe_test_path("tests/unit/x.py; rm -rf /") is False
        assert is_safe_test_path("tests/unit/$(whoami).py") is False
        assert is_safe_test_path("tests/unit/x.py | cat /etc/passwd") is False

    def test_rejects_non_py_suffix(self) -> None:
        assert is_safe_test_path("tests/unit/ci/not_a_test.txt") is False


# ---------------------------------------------------------------------------
# The recipe: progress-marker stripping (pure)
# ---------------------------------------------------------------------------


class TestStripProgressMarker:
    def test_strips_single_digit_percent(self) -> None:
        line = "tests/x.py::test_a PASSED [  4%]"
        assert strip_progress_marker(line) == "tests/x.py::test_a PASSED"

    def test_strips_three_digit_percent(self) -> None:
        line = "tests/x.py::test_a PASSED [100%]"
        assert strip_progress_marker(line) == "tests/x.py::test_a PASSED"

    def test_strips_extra_columns_padding_not_just_one_space(self) -> None:
        # COLUMNS-dependent right-padding (ledger row t1.1/35b8e3e) can insert MULTIPLE spaces
        # before the bracket — a literal-single-space strip would leave a dangling space.
        line = "tests/x.py::test_a PASSED     [ 50%]"
        assert strip_progress_marker(line) == "tests/x.py::test_a PASSED"

    def test_failed_line_also_strips(self) -> None:
        line = "tests/x.py::test_b FAILED [ 66%]"
        assert strip_progress_marker(line) == "tests/x.py::test_b FAILED"

    def test_line_without_marker_is_unchanged(self) -> None:
        line = "tests/x.py::test_a PASSED"
        assert strip_progress_marker(line) == line


class TestExtractResultLines:
    def test_extracts_and_strips_passed_and_failed(self) -> None:
        output = (
            "collecting ... collected 2 items\n\n"
            "tests/x.py::test_a PASSED [ 50%]\n"
            "tests/x.py::test_b FAILED [100%]\n\n"
            "=========== 1 failed, 1 passed in 0.01s ===========\n"
        )
        assert extract_result_lines(output) == [
            "tests/x.py::test_a PASSED",
            "tests/x.py::test_b FAILED",
        ]

    def test_ignores_a_warnings_summary_header_line(self) -> None:
        # The exact false-positive class ledger row t1.1 documents: a warnings-summary nodeid
        # header line must never be mistaken for a result line.
        output = (
            "tests/x.py::test_a PASSED [100%]\n"
            "=============================== warnings summary ================================\n"
            "tests/x.py::test_a\n"
            "  /some/path.py:1: DeprecationWarning: PASSED FAILED are not result lines here\n"
        )
        assert extract_result_lines(output) == ["tests/x.py::test_a PASSED"]

    def test_no_result_lines_yields_empty_list(self) -> None:
        assert extract_result_lines("no tests ran\n") == []


class TestComputeRecipeHash:
    def test_deterministic_and_sorted(self) -> None:
        h1 = compute_recipe_hash(["b PASSED", "a PASSED"])
        h2 = compute_recipe_hash(["a PASSED", "b PASSED"])
        assert h1 == h2
        assert h1.startswith("sha256:")
        assert len(h1) == len("sha256:") + 64

    def test_marker_kept_produces_a_different_hash_than_stripped(self) -> None:
        # The addendum's core claim, proven directly: hashing WITH the marker present is a
        # DIFFERENT value than hashing the stripped line.
        with_marker = compute_recipe_hash(["tests/x.py::test_a PASSED [ 50%]"])
        stripped = compute_recipe_hash(["tests/x.py::test_a PASSED"])
        assert with_marker != stripped

    def test_matches_manual_sort_join_trailing_newline_sha256(self) -> None:
        import hashlib

        lines = ["z PASSED", "a FAILED", "m PASSED"]
        expected = "sha256:" + hashlib.sha256(("\n".join(sorted(lines)) + "\n").encode("utf-8")).hexdigest()
        assert compute_recipe_hash(lines) == expected

    def test_matches_the_eval_replay_exhaustion_swallowed_ledger_row_hash(self) -> None:
        """The addendum's own target row, reconfirmed bit-for-bit WITHOUT executing anything on
        that sibling branch (forbidden by this task's brief): the 3 real `async def test_...`
        names were read via `git show 0dcde5c:tests/evals/test_replay_exhaustion_unswallowable.py`
        (read-only; the report cites "3 passed"), and the resulting synthetic `<nodeid> PASSED`
        lines below reproduce the declared ledger hash
        `sha256:6ce0f39605f55aede82194a2c1e9bf957a67600a18fab728ffb5ecb116314bfc` exactly — proof
        that `compute_recipe_hash`'s join/sort/trailing-newline choice is the one this ledger's
        real practice actually uses, not merely internally self-consistent."""
        node_ids = [
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_exhaustion_one_entry_short_fails_even_with_agent_fallback PASSED",
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_exhaustion_complete_golden_passes PASSED",
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_unconsumed_recorded_llm_entry_is_not_silently_ignored PASSED",
        ]
        assert (
            compute_recipe_hash(node_ids)
            == "sha256:6ce0f39605f55aede82194a2c1e9bf957a67600a18fab728ffb5ecb116314bfc"
        )

    def test_matches_the_eval_replay_exhaustion_swallowed_ledger_row_hash_via_full_pipeline(self) -> None:
        """Sibling of the test above (verifier advisory E-2, VERIFY-LEDGER-RECOMPUTE.md §E.1): the
        one above feeds ALREADY-STRIPPED node-id strings straight into `compute_recipe_hash`, so a
        mutation that breaks `strip_progress_marker` leaves it green (confirmed by mutation
        testing). This version exercises the REAL pipeline — raw pytest -v lines, WITH the
        progress marker and COLUMNS-style right-padding, through `extract_result_lines` (which
        calls `strip_progress_marker`) — against the exact same target hash, so regressing the
        strip step is caught here too, not just by the synthetic-string test above."""
        raw_output = (
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_exhaustion_one_entry_short_fails_even_with_agent_fallback PASSED"
            "                                                                        [ 33%]\n"
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_exhaustion_complete_golden_passes PASSED [ 66%]\n"
            "tests/evals/test_replay_exhaustion_unswallowable.py::"
            "test_replay_unconsumed_recorded_llm_entry_is_not_silently_ignored PASSED [100%]\n"
        )
        result_lines = extract_result_lines(raw_output)
        assert len(result_lines) == 3
        assert any("[" in line for line in raw_output.splitlines())  # non-vacuity: marker present in input
        assert all("[" not in line for line in result_lines)  # non-vacuity: marker actually stripped
        assert (
            compute_recipe_hash(result_lines)
            == "sha256:6ce0f39605f55aede82194a2c1e9bf957a67600a18fab728ffb5ecb116314bfc"
        )


# ---------------------------------------------------------------------------
# Recipe hash equality against a REAL fixture test file (Task 3 + addendum requirement)
# ---------------------------------------------------------------------------

_FIXTURE_TEST_FILE = '''"""Tiny real pytest file used only to prove the recipe end-to-end."""


def test_alpha_passes():
    assert True


def test_beta_passes():
    assert 1 + 1 == 2


def test_gamma_fails():
    assert False
'''


class TestRunRecipeAgainstRealFixture:
    def test_recipe_hash_is_deterministic_and_matches_manual_recomputation(self, tmp_path: Path) -> None:
        fixture = tmp_path / "test_fixture_recipe.py"
        fixture.write_text(_FIXTURE_TEST_FILE, encoding="utf-8")

        outcome = run_recipe(tmp_path, "test_fixture_recipe.py", sys.executable)
        assert outcome.ok is True
        assert outcome.result_line_count == 3
        assert outcome.computed_hash is not None
        assert outcome.computed_hash.startswith("sha256:")

        # Re-run for real: same content -> same hash (determinism), computed independently.
        outcome2 = run_recipe(tmp_path, "test_fixture_recipe.py", sys.executable)
        assert outcome2.computed_hash == outcome.computed_hash

    def test_marker_kept_hash_does_not_match_the_recipe_hash(self, tmp_path: Path) -> None:
        # Proves, against a REAL subprocess run (not a synthetic string), that a hash computed
        # from the RAW (unstripped) result lines fails to match what the recipe (stripped)
        # produces — the addendum's exact requirement.
        fixture = tmp_path / "test_fixture_marker.py"
        fixture.write_text(_FIXTURE_TEST_FILE, encoding="utf-8")

        pytest_argv = [sys.executable, "-m", "pytest", "test_fixture_marker.py", "-v", "--tb=no"]
        proc = subprocess.run(
            [*pytest_argv, "-p", "no:cacheprovider"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        combined = proc.stdout + proc.stderr
        raw_result_lines = [
            line
            for line in combined.splitlines()
            if line.startswith("test_fixture_marker.py::") and (" PASSED" in line or " FAILED" in line)
        ]
        assert len(raw_result_lines) == 3  # non-vacuity: the fixture really produced 3 result lines
        assert any("[" in line for line in raw_result_lines)  # non-vacuity: the marker really is present

        marker_kept_hash = compute_recipe_hash(raw_result_lines)
        stripped_hash = compute_recipe_hash([strip_progress_marker(line) for line in raw_result_lines])

        recipe_outcome = run_recipe(tmp_path, "test_fixture_marker.py", sys.executable)
        assert recipe_outcome.ok is True

        # (1) a hash computed WITH the marker fails to match the recipe's hash.
        assert marker_kept_hash != recipe_outcome.computed_hash
        # (2) the STRIPPED hash matches exactly.
        assert stripped_hash == recipe_outcome.computed_hash

    def test_hash_changes_when_the_file_content_changes(self, tmp_path: Path) -> None:
        fixture = tmp_path / "test_fixture_sensitivity.py"
        fixture.write_text(_FIXTURE_TEST_FILE, encoding="utf-8")
        before = run_recipe(tmp_path, "test_fixture_sensitivity.py", sys.executable)

        fixture.write_text(
            _FIXTURE_TEST_FILE + "\n\ndef test_delta_passes():\n    assert True\n", encoding="utf-8"
        )
        after = run_recipe(tmp_path, "test_fixture_sensitivity.py", sys.executable)

        assert before.computed_hash != after.computed_hash
        assert after.result_line_count == 4


# ---------------------------------------------------------------------------
# verify_row: mismatch -> non-zero, real-file success
# ---------------------------------------------------------------------------


class TestVerifyRow:
    def test_matching_hash_is_ok(self, tmp_path: Path) -> None:
        # verify_row enforces the tests/ prefix via is_safe_test_path — set up a real
        # "tests/"-shaped path relative to repo_root, matching how the gate is actually invoked.
        repo_root = tmp_path / "repo"
        tests_dir = repo_root / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_verify_ok.py").write_text(_FIXTURE_TEST_FILE, encoding="utf-8")

        outcome = run_recipe(repo_root, "tests/test_verify_ok.py", sys.executable)
        assert outcome.ok is True
        assert outcome.computed_hash is not None
        declared_hash = outcome.computed_hash.removeprefix("sha256:")

        row = DeclaredRow(task_id="T-OK", test_path="tests/test_verify_ok.py", declared_hash=declared_hash)
        result = verify_row(repo_root, row, sys.executable)
        assert result.ok is True
        assert "verified" in result.message

    def test_mismatched_hash_is_non_zero_and_explains_both_causes(self, tmp_path: Path) -> None:
        real_path = tmp_path / "tests"
        real_path.mkdir()
        (real_path / "test_mismatch.py").write_text(_FIXTURE_TEST_FILE, encoding="utf-8")

        wrong_row = DeclaredRow(task_id="T-BAD", test_path="tests/test_mismatch.py", declared_hash="0" * 64)
        result = verify_row(tmp_path, wrong_row, sys.executable)
        assert result.ok is False
        assert "hash mismatch" in result.message
        assert "computed wrong" in result.message
        assert "PR1-LEDGER-HASH-STALE-BY-DESIGN" in result.message

    def test_missing_file_is_non_zero(self, tmp_path: Path) -> None:
        row = DeclaredRow(task_id="T-MISSING", test_path="tests/does_not_exist.py", declared_hash="0" * 64)
        result = verify_row(tmp_path, row, sys.executable)
        assert result.ok is False
        assert "does not exist" in result.message

    def test_path_injection_attempt_is_rejected_before_execution(self, tmp_path: Path) -> None:
        row = DeclaredRow(task_id="T-EVIL", test_path="tests/../../../etc/passwd", declared_hash="0" * 64)
        result = verify_row(tmp_path, row, sys.executable)
        assert result.ok is False
        assert "path-injection guard" in result.message


# ---------------------------------------------------------------------------
# Diff-range selection: pure parser
# ---------------------------------------------------------------------------


class TestParseDiffAddedLines:
    def test_extracts_only_added_lines_not_context_or_removed(self) -> None:
        diff_text = (
            "diff --git a/docs/evidence-ledger.md b/docs/evidence-ledger.md\n"
            "index abc..def 100644\n"
            "--- a/docs/evidence-ledger.md\n"
            "+++ b/docs/evidence-ledger.md\n"
            "@@ -280,1 +280,3 @@\n"
            " context line unchanged\n"
            "-removed line\n"
            "+added paragraph line\n"
            "+| NEW-1 | 2026-09-03 | a | b | sha | ev | sha256:" + "e" * 64 + " (tests/x/test_y.py) | s |\n"
        )
        added = parse_diff_added_lines(diff_text)
        assert added == [
            "added paragraph line",
            "| NEW-1 | 2026-09-03 | a | b | sha | ev | sha256:" + "e" * 64 + " (tests/x/test_y.py) | s |",
        ]

    def test_empty_diff_yields_empty_list(self) -> None:
        assert parse_diff_added_lines("") == []

    def test_plus_plus_plus_header_never_counted_as_an_added_line(self) -> None:
        diff_text = "--- a/f\n+++ b/f\n+real added line\n"
        assert parse_diff_added_lines(diff_text) == ["real added line"]


# ---------------------------------------------------------------------------
# Diff-range selection: real tmp git repo (no mocked git — "fake git via a tmp repo")
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_ledger_repo(tmp_path: Path) -> tuple[Path, str]:
    """Real git repo: base commit carries a legacy row; returns (repo_root, base_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "docs").mkdir()
    ledger = repo / "docs" / "evidence-ledger.md"
    ledger.write_text(LEDGER_HEADER + _legacy_row_line("T0.1") + "\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    base_sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    return repo, base_sha


class TestGetLedgerDiffAddedLinesRealGitRepo:
    def test_new_declared_and_legacy_rows_are_both_returned_as_added_lines(self, tmp_path: Path) -> None:
        repo, base_sha = _init_ledger_repo(tmp_path)
        ledger = repo / "docs" / "evidence-ledger.md"
        new_content = (
            ledger.read_text(encoding="utf-8")
            + _declared_row_line("NEW-1", "tests/unit/x/test_y.py", "f" * 64)
            + "\n"
            + _legacy_row_line("T0.2")
            + "\n"
        )
        ledger.write_text(new_content, encoding="utf-8")
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add rows"], repo)

        added = get_ledger_diff_added_lines(repo, base_sha, "docs/evidence-ledger.md")
        selection = select_rows(added)
        assert selection.declared == (
            DeclaredRow(task_id="NEW-1", test_path="tests/unit/x/test_y.py", declared_hash="f" * 64),
        )
        assert [r.task_id for r in selection.legacy] == ["T0.2"]
        # The pre-existing base row must NEVER appear as "added" (design fact 1: scope discipline).
        assert not any("T0.1" in line for line in added)

    def test_no_new_rows_yields_empty_selection(self, tmp_path: Path) -> None:
        repo, base_sha = _init_ledger_repo(tmp_path)
        added = get_ledger_diff_added_lines(repo, base_sha, "docs/evidence-ledger.md")
        assert added == []

    def test_resolve_effective_base_with_explicit_sha_merge_bases_with_head(self, tmp_path: Path) -> None:
        repo, base_sha = _init_ledger_repo(tmp_path)
        effective = resolve_effective_base(repo, base_sha)
        assert effective == base_sha  # base_sha IS an ancestor of HEAD (== HEAD here) -> merge-base is itself


# ---------------------------------------------------------------------------
# main(): end-to-end, real git repo + real fixture test file
# ---------------------------------------------------------------------------


class TestMainEndToEnd:
    def test_zero_declared_rows_prints_explicit_counts_and_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo, base_sha = _init_ledger_repo(tmp_path)
        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(
            ledger.read_text(encoding="utf-8") + _legacy_row_line("T0.2") + "\n", encoding="utf-8"
        )
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add legacy row only"], repo)

        exit_code = main(["--base", base_sha, "--ledger-path", "docs/evidence-ledger.md"], repo_root=repo)
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "0 rows verified, 1 legacy rows skipped" in out

    def test_matching_declared_row_passes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo, base_sha = _init_ledger_repo(tmp_path)
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_e2e_ok.py").write_text(_FIXTURE_TEST_FILE, encoding="utf-8")
        outcome = run_recipe(repo, "tests/test_e2e_ok.py", sys.executable)
        assert outcome.ok is True
        assert outcome.computed_hash is not None
        declared_hash = outcome.computed_hash.removeprefix("sha256:")

        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(
            ledger.read_text(encoding="utf-8")
            + _declared_row_line("NEW-OK", "tests/test_e2e_ok.py", declared_hash)
            + "\n",
            encoding="utf-8",
        )
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add matching declared row"], repo)

        exit_code = main(
            ["--base", base_sha, "--ledger-path", "docs/evidence-ledger.md", "--python", sys.executable],
            repo_root=repo,
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "1/1 declared rows verified" in out

    def test_mismatched_declared_row_fails_non_vacuously(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # THE non-vacuity proof: this gate can genuinely FAIL, not just always print green.
        repo, base_sha = _init_ledger_repo(tmp_path)
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_e2e_bad.py").write_text(_FIXTURE_TEST_FILE, encoding="utf-8")

        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(
            ledger.read_text(encoding="utf-8")
            + _declared_row_line("NEW-BAD", "tests/test_e2e_bad.py", "0" * 64)
            + "\n",
            encoding="utf-8",
        )
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add wrong-hash declared row"], repo)

        exit_code = main(
            ["--base", base_sha, "--ledger-path", "docs/evidence-ledger.md", "--python", sys.executable],
            repo_root=repo,
        )
        out = capsys.readouterr().out
        assert exit_code == 1
        assert "MISMATCH" in out
        assert "0/1 declared rows verified" in out

    def test_all_flag_ignores_range_and_verifies_every_declared_row(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo, _base_sha = _init_ledger_repo(tmp_path)
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_e2e_all.py").write_text(_FIXTURE_TEST_FILE, encoding="utf-8")
        outcome = run_recipe(repo, "tests/test_e2e_all.py", sys.executable)
        assert outcome.computed_hash is not None
        declared_hash = outcome.computed_hash.removeprefix("sha256:")

        ledger = repo / "docs" / "evidence-ledger.md"
        ledger.write_text(
            ledger.read_text(encoding="utf-8")
            + _declared_row_line("NEW-ALL", "tests/test_e2e_all.py", declared_hash)
            + "\n",
            encoding="utf-8",
        )
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "add row, then verify with --all (no --base needed)"], repo)

        exit_code = main(
            ["--all", "--ledger-path", "docs/evidence-ledger.md", "--python", sys.executable], repo_root=repo
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "1/1 declared rows verified" in out
        assert "--all" in out


# ---------------------------------------------------------------------------
# D-2 regression, against the REAL repo ledger (not a synthetic fixture): `--all` must verify
# THIS task's own row and skip `mzo-040` by date, never flag it MISMATCH by shape coincidence.
# ---------------------------------------------------------------------------


class TestAllModeAgainstRealLedger:
    def test_all_mode_selection_verifies_recompute_check_row_and_skips_mzo_040_by_date(self) -> None:
        """VERIFY-LEDGER-RECOMPUTE.md finding D-2, reproduced against the REAL ledger this task
        ships (not a synthetic fixture): `mzo-040` (docs/evidence-ledger.md, dated 2026-08-09)
        already writes its Test-hash cell in the declared-path SHAPE by coincidence — without
        date-qualification, `--all` would execute it and report MISMATCH (the test file has
        drifted since), misleading a local spot-audit into treating a syntactic coincidence as a
        real hash-integrity problem.

        Deliberately exercises `select_rows` directly — the exact function `--all` mode calls with
        no range scoping, see `main`'s `if args.all: selection = select_rows(...)` — INSTEAD of
        `main(["--all"])`/`verify_row`. Running the real recipe here would shell out to
        `pytest tests/unit/ci/test_check_evidence_ledger_hashes.py`, i.e. THIS SAME FILE, which
        contains this very test — unbounded self-recursive re-invocation (a real fork-bomb hazard
        hit and killed during this task, confirmed live: dozens of nested pytest subprocesses,
        `run_recipe`'s 300s timeout was the only thing that eventually failed it closed). The
        recipe-execution path itself (`verify_row`/`run_recipe`) is already covered end-to-end,
        real subprocess included, by `TestVerifyRow`/`TestMainEndToEnd`/
        `TestRunRecipeAgainstRealFixture` — always against a disposable `tmp_path` fixture file,
        never against this file. What's real and unmocked here is the LEDGER TEXT and the
        SELECTION decision; that is exactly what D-2 is about."""
        real_ledger_text = (REPO_ROOT / "docs" / "evidence-ledger.md").read_text(encoding="utf-8")
        selection = select_rows(real_ledger_text.splitlines())

        declared_task_ids = [r.task_id for r in selection.declared]
        assert "LEDGER-HASH-RECOMPUTE-CHECK" in declared_task_ids

        mzo_040 = next((r for r in selection.legacy if r.task_id == "mzo-040"), None)
        assert mzo_040 is not None, "mzo-040 must be listed as legacy (skipped), never dropped"
        assert "precedes CONVENTION_START_DATE" in mzo_040.reason
