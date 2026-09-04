"""Unit tests for scripts.ci.check_evidence_ledger — the T0.5 evidence-ledger CI gate.

Covers the acceptance criteria verbatim: green on a PR carrying a ledger entry
for its task ID, red on one that doesn't, fail-closed on any input error, and
no task ID detected is always a pass. TDD London School: the pure core
(detection + evaluation) is tested directly; the CLI wrapper is tested through
`main()` with monkeypatched env vars and real temp files (no I/O mocking of
the core logic itself).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from scripts.ci.check_evidence_ledger import (
    CheckResult,
    build_arg_parser,
    detect_task_ids,
    detect_task_ids_from_body,
    detect_task_ids_from_branch,
    evaluate,
    extract_ledger_task_ids,
    find_conflict_markers,
    main,
)

# Repo root, three levels up from tests/unit/ci/test_check_evidence_ledger.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]

LEDGER_HEADER = (
    "# Evidence Ledger\n\n"
    "| Task ID | Date | Author (agent, tier) | Verifier (agent, tier) | Commit SHA | "
    "Evidence (path:line) | Test hash | Status |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def _ledger_with_rows(*task_ids: str) -> str:
    rows = "\n".join(
        f"| {tid} | 2026-07-16 | gates-engineer (R2) | — | deadbeef | "
        f"scripts/ci/check_evidence_ledger.py:1 | sha256:abc123 | implemented — unverified |"
        for tid in task_ids
    )
    return LEDGER_HEADER + rows + "\n"


# ---------------------------------------------------------------------------
# detect_task_ids_from_branch
# ---------------------------------------------------------------------------


class TestDetectTaskIdsFromBranch:
    def test_matches_task_branch(self) -> None:
        assert detect_task_ids_from_branch("t0.5-evidence-ledger") == {"T0.5"}

    def test_matches_multi_digit_phase_and_n(self) -> None:
        assert detect_task_ids_from_branch("t1.9-remove-ceiling-bypass") == {"T1.9"}

    def test_case_insensitive(self) -> None:
        assert detect_task_ids_from_branch("T0.5-Evidence-Ledger") == {"T0.5"}

    def test_no_match_for_unrelated_branch(self) -> None:
        assert detect_task_ids_from_branch("chore/plan-of-record") == set()

    def test_no_match_for_worktree_branch(self) -> None:
        assert detect_task_ids_from_branch("worktree-agent-a9c1820012a2e2fe7") == set()

    def test_no_match_when_task_id_not_at_branch_start(self) -> None:
        # A task-ID-shaped token that is not a prefix of the branch name is not a task branch.
        assert detect_task_ids_from_branch("fix/mentions-t0.5-in-passing") == set()


# ---------------------------------------------------------------------------
# detect_task_ids_from_body
# ---------------------------------------------------------------------------


class TestDetectTaskIdsFromBody:
    def test_task_colon_form(self) -> None:
        assert detect_task_ids_from_body("Task: T0.5") == {"T0.5"}

    def test_task_no_colon_is_not_a_marker(self) -> None:
        # T0.5 gate-precision follow-up: a colon is required. Without one, "Task
        # T0.5 ..." is indistinguishable from a prose mention and must NOT bind
        # (this was previously a false-positive vector — see the module docstring's
        # PR #38 postmortem, where "task T3.1" appeared mid-sentence in prose).
        assert detect_task_ids_from_body("Task T0.5 closes the ledger gap.") == set()

    def test_task_id_infix_is_not_a_marker(self) -> None:
        # T0.5 gate-precision follow-up: "Task ID:" (word "ID" between "Task" and
        # the colon) and Markdown-bold-prefixed lines are no longer recognized —
        # only a bare "Task:"/"Tasks:" line or a "[T<phase>.<n>]" bracket binds.
        assert detect_task_ids_from_body("**Task ID:** T0.5") == set()

    def test_case_insensitive(self) -> None:
        assert detect_task_ids_from_body("task: t0.5") == {"T0.5"}

    def test_multiple_task_ids(self) -> None:
        body = "Task: T0.5\n\nTask: T0.6 closes the same sweep."
        assert detect_task_ids_from_body(body) == {"T0.5", "T0.6"}

    def test_no_task_word_is_not_detected(self) -> None:
        # A bare T0.5-shaped token with no preceding "task" is deliberately NOT a match —
        # avoids false positives from version strings or unrelated references.
        assert detect_task_ids_from_body("See spec T0.5 for details.") == set()

    def test_empty_body(self) -> None:
        assert detect_task_ids_from_body("") == set()


# ---------------------------------------------------------------------------
# detect_task_ids (combined)
# ---------------------------------------------------------------------------


class TestDetectTaskIds:
    def test_branch_only(self) -> None:
        assert detect_task_ids("t0.5-evidence-ledger", "no mention here") == {"T0.5"}

    def test_body_only(self) -> None:
        assert detect_task_ids("fix/unrelated", "Task: T0.5") == {"T0.5"}

    def test_neither(self) -> None:
        assert detect_task_ids("fix/unrelated", "no mention here") == set()

    def test_union_of_branch_and_body(self) -> None:
        assert detect_task_ids("t0.5-evidence-ledger", "Task: T0.6") == {"T0.5", "T0.6"}


# ---------------------------------------------------------------------------
# extract_ledger_task_ids
# ---------------------------------------------------------------------------


class TestExtractLedgerTaskIds:
    def test_finds_seeded_row(self) -> None:
        assert extract_ledger_task_ids(_ledger_with_rows("T0.5")) == {"T0.5"}

    def test_finds_multiple_rows(self) -> None:
        assert extract_ledger_task_ids(_ledger_with_rows("T0.1", "T0.5")) == {"T0.1", "T0.5"}

    def test_header_and_separator_rows_do_not_match(self) -> None:
        assert extract_ledger_task_ids(LEDGER_HEADER) == set()

    def test_empty_ledger(self) -> None:
        assert extract_ledger_task_ids("") == set()

    def test_case_insensitive(self) -> None:
        assert extract_ledger_task_ids("| t0.5 | ... |\n") == {"T0.5"}


# ---------------------------------------------------------------------------
# evaluate (pure core)
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_no_detected_ids_passes_without_ledger(self) -> None:
        result = evaluate(set(), None)
        assert result.ok is True
        assert result.missing_ids == frozenset()

    def test_detected_id_with_matching_row_passes(self) -> None:
        result = evaluate({"T0.5"}, _ledger_with_rows("T0.5"))
        assert result.ok is True
        assert result.detected_ids == frozenset({"T0.5"})
        assert result.missing_ids == frozenset()

    def test_detected_id_without_row_fails(self) -> None:
        result = evaluate({"T0.5"}, _ledger_with_rows("T0.1"))
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.5"})

    def test_detected_id_with_none_ledger_text_fails_closed(self) -> None:
        result = evaluate({"T0.5"}, None)
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.5"})

    def test_multiple_ids_all_present_passes(self) -> None:
        result = evaluate({"T0.5", "T0.6"}, _ledger_with_rows("T0.5", "T0.6"))
        assert result.ok is True

    def test_multiple_ids_one_missing_fails(self) -> None:
        result = evaluate({"T0.5", "T0.6"}, _ledger_with_rows("T0.5"))
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.6"})

    def test_multiple_ids_all_missing_fails(self) -> None:
        result = evaluate({"T0.5", "T0.6"}, _ledger_with_rows("T0.1"))
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.5", "T0.6"})

    def test_result_is_frozen_dataclass(self) -> None:
        result = evaluate(set(), None)
        assert isinstance(result, CheckResult)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI wrapper: main()
# ---------------------------------------------------------------------------


class TestMainGreenCases:
    """PRs that should make the gate exit 0."""

    def test_task_branch_with_ledger_row_present(self, tmp_path, monkeypatch) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")
        monkeypatch.delenv("PR_HEAD_REF", raising=False)
        monkeypatch.delenv("PR_BODY", raising=False)

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task ID: T0.5\n\nDemonstrates the green case.",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0

    def test_no_task_id_anywhere_passes_without_ledger_file(self, tmp_path) -> None:
        # Ledger path points at a file that does not exist at all — must not matter,
        # since no task ID was detected and the ledger is therefore never read.
        missing_ledger = tmp_path / "does-not-exist.md"
        exit_code = main(
            [
                "--branch",
                "dependabot/github_actions/actions-checkout-7",
                "--pr-body",
                "Bumps actions/checkout from 6 to 7.",
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 0

    def test_body_only_task_id_with_row_present(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")

        exit_code = main(
            [
                "--branch",
                "fix/some-unrelated-branch-name",
                "--pr-body",
                "Task: T0.5 — closes the gap.",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0

    def test_multiple_task_ids_all_present(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5", "T0.6"), encoding="utf-8")

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task: T0.6 closes the same sweep.",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0

    def test_env_vars_are_honored_when_no_cli_args_given(self, tmp_path, monkeypatch) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")
        monkeypatch.setenv("PR_HEAD_REF", "t0.5-evidence-ledger")
        monkeypatch.setenv("PR_BODY", "Task ID: T0.5")

        exit_code = main(["--ledger-path", str(ledger)])
        assert exit_code == 0


class TestMainRedCases:
    """PRs that should make the gate exit 1 (fail-closed)."""

    def test_task_branch_without_ledger_row_fails(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")  # some other task's row only

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task ID: T0.5\n\nDemonstrates the red case (no ledger row).",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 1

    def test_unreadable_ledger_fails(self, tmp_path) -> None:
        missing_ledger = tmp_path / "does-not-exist.md"

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task ID: T0.5",
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 1

    def test_ledger_path_is_a_directory_fails(self, tmp_path) -> None:
        directory_as_ledger = tmp_path / "a-directory"
        directory_as_ledger.mkdir()

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task ID: T0.5",
                "--ledger-path",
                str(directory_as_ledger),
            ]
        )
        assert exit_code == 1

    def test_multiple_task_ids_one_missing_fails(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")  # T0.6 row missing

        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task: T0.6 closes the same sweep.",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 1

    def test_missing_branch_input_fails_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("PR_HEAD_REF", raising=False)
        exit_code = main(["--pr-body", "Task ID: T0.5"])
        assert exit_code == 1

    def test_missing_pr_body_input_fails_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("PR_BODY", raising=False)
        exit_code = main(["--branch", "t0.5-evidence-ledger"])
        assert exit_code == 1

    def test_pr_body_file_unreadable_fails_closed(self, tmp_path) -> None:
        nonexistent = tmp_path / "no-such-body.txt"
        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body-file",
                str(nonexistent),
            ]
        )
        assert exit_code == 1


class TestMainPrBodyFile:
    def test_pr_body_file_is_read(self, tmp_path) -> None:
        body_file = tmp_path / "body.txt"
        body_file.write_text("Task: T0.5", encoding="utf-8")
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")

        exit_code = main(
            [
                "--branch",
                "fix/unrelated",
                "--pr-body-file",
                str(body_file),
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0

    def test_pr_body_file_overrides_pr_body_arg(self, tmp_path) -> None:
        body_file = tmp_path / "body.txt"
        body_file.write_text("no task id here", encoding="utf-8")
        exit_code = main(
            [
                "--branch",
                "fix/unrelated",
                "--pr-body",
                "Task ID: T0.5",  # should be ignored in favor of --pr-body-file
                "--pr-body-file",
                str(body_file),
            ]
        )
        assert exit_code == 0  # no task id detected at all -> pass


# ---------------------------------------------------------------------------
# Audit hardening: double-digit task IDs, exact-ID collision boundaries,
# empty-PR-body end-to-end, and proof that unexpected errors are never
# silently swallowed into a pass. These lock in the specific fail-open
# holes called out for review on this task (T0.5 predecessor audit).
# ---------------------------------------------------------------------------


class TestDoubleDigitTaskIds:
    """`\\d+` must not silently truncate multi-digit phase/n segments anywhere."""

    def test_branch_double_digit_phase_and_n(self) -> None:
        assert detect_task_ids_from_branch("t10.12-some-slug") == {"T10.12"}

    def test_body_double_digit_phase_and_n(self) -> None:
        assert detect_task_ids_from_body("Task: T10.12 closes the migration.") == {"T10.12"}

    def test_ledger_double_digit_phase_and_n(self) -> None:
        assert extract_ledger_task_ids(_ledger_with_rows("T10.12")) == {"T10.12"}

    def test_end_to_end_double_digit_green(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T10.12"), encoding="utf-8")
        exit_code = main(
            [
                "--branch",
                "t10.12-some-slug",
                "--pr-body",
                "no mention here",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0


class TestExactIdCollisionBoundaries:
    """T0.1 must never be satisfied by a T0.10 row (or vice versa) — substring
    collisions between a short task ID and a longer one sharing it as a
    prefix must be rejected as distinct IDs, not silently matched."""

    def test_branch_t0_1_is_not_confused_with_t0_10(self) -> None:
        assert detect_task_ids_from_branch("t0.1-migrate") == {"T0.1"}
        assert detect_task_ids_from_branch("t0.10-migrate") == {"T0.10"}

    def test_body_mentions_of_both_ids_stay_distinct(self) -> None:
        body = "Task: T0.1 and also Task: T0.10 in the same sweep."
        assert detect_task_ids_from_body(body) == {"T0.1", "T0.10"}

    def test_ledger_row_for_t0_10_does_not_satisfy_t0_1(self) -> None:
        ledger_text = _ledger_with_rows("T0.10")
        assert extract_ledger_task_ids(ledger_text) == {"T0.10"}
        result = evaluate({"T0.1"}, ledger_text)
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.1"})

    def test_ledger_row_for_t0_1_does_not_satisfy_t0_10(self) -> None:
        ledger_text = _ledger_with_rows("T0.1")
        assert extract_ledger_task_ids(ledger_text) == {"T0.1"}
        result = evaluate({"T0.10"}, ledger_text)
        assert result.ok is False
        assert result.missing_ids == frozenset({"T0.10"})

    def test_ledger_with_both_rows_satisfies_both_distinctly(self) -> None:
        ledger_text = _ledger_with_rows("T0.1", "T0.10")
        assert extract_ledger_task_ids(ledger_text) == {"T0.1", "T0.10"}
        result = evaluate({"T0.1", "T0.10"}, ledger_text)
        assert result.ok is True

    def test_end_to_end_collision_row_fails_closed(self, tmp_path) -> None:
        # Branch cites T0.1; the ledger only has a T0.10 row (a real predecessor
        # bug would be a substring match here). Must be a hard FAIL, not a pass.
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.10"), encoding="utf-8")
        exit_code = main(
            [
                "--branch",
                "t0.1-migrate",
                "--pr-body",
                "no mention here",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 1


class TestEmptyPrBodyEndToEnd:
    """An explicitly empty PR body (GitHub sends "" for a description-less PR,
    never a missing env var) must be treated as a legitimate, readable input —
    not conflated with the missing-input error path — while still requiring a
    ledger row when the branch name alone carries a task ID."""

    def test_empty_body_with_task_branch_and_row_present_passes(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")
        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 0

    def test_empty_body_with_task_branch_and_no_row_fails(self, tmp_path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")
        exit_code = main(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "",
                "--ledger-path",
                str(ledger),
            ]
        )
        assert exit_code == 1

    def test_empty_body_with_unrelated_branch_passes_without_ledger(self, tmp_path) -> None:
        missing_ledger = tmp_path / "does-not-exist.md"
        exit_code = main(
            [
                "--branch",
                "dependabot/pip/urllib3-2.5.0",
                "--pr-body",
                "",
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 0

    def test_empty_body_env_var_is_not_a_missing_input(self, monkeypatch, tmp_path) -> None:
        # $PR_BODY="" (set-but-empty) must NOT raise InputError the way an
        # *unset* $PR_BODY does — GitHub always sets the env var, even for a
        # description-less PR, so "" must be a valid, distinct case from unset.
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")
        monkeypatch.setenv("PR_HEAD_REF", "t0.5-evidence-ledger")
        monkeypatch.setenv("PR_BODY", "")
        exit_code = main(["--ledger-path", str(ledger)])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# T0.5 gate-precision follow-up: PR #38 false-positive regression tests.
#
# PR #38's body included "Status: modelado (suite de integração planejada —
# T3.1)" and "the porting is task T3.1, not yet done" — both bare prose
# mentions of T3.1, never a closure marker. The pre-fix _BODY_TASK_RE matched
# the word "task" followed by loosely-punctuated filler *anywhere* in the
# body, so "task T3.1" mid-sentence bound T3.1 and the gate wrongly demanded
# a T3.1 ledger row for a PR that never claimed to close T3.1 (it was on
# branch t0.2-g0-conditions, closing T0.1/T0.2 only). These tests lock in
# the narrowed, marker-only trigger against that exact regression.
# ---------------------------------------------------------------------------

PR_38_PROSE_EXCERPT = (
    "**Before:** \n"
    "```\n"
    "Status: modelado (suite integracao real-engine)\n"
    "```\n\n"
    "**After:**\n"
    "```\n"
    "Status: modelado (suite de integração planejada — T3.1)\n"
    "```\n\n"
    "**Rationale:** tests/integration/ contains zero integration tests; the "
    "porting is task T3.1, not yet done."
)


class TestPr38FalsePositiveRegression:
    def test_prose_mention_is_not_detected_from_body(self) -> None:
        assert detect_task_ids_from_body(PR_38_PROSE_EXCERPT) == set()

    def test_prose_mention_end_to_end_passes_on_neutral_branch(self, tmp_path) -> None:
        # Branch deliberately does NOT match `^t\d+\.\d+-` (PR #38's actual branch,
        # t0.2-g0-conditions, would itself trigger via the branch-name rule, which
        # is unrelated to and unchanged by this fix — see the branch-only tests).
        # Ledger path also points at a nonexistent file: if the body were (wrongly)
        # detected as citing T3.1, the ledger would have to be read and this would
        # fail closed. Exit 0 here proves nothing was detected at all.
        missing_ledger = tmp_path / "does-not-exist.md"
        exit_code = main(
            [
                "--branch",
                "docs/g0-gate-conditions",
                "--pr-body",
                PR_38_PROSE_EXCERPT,
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 0

    def test_task_colon_line_is_detected(self) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nTask: T3.1"
        assert detect_task_ids_from_body(body) == {"T3.1"}

    def test_task_colon_line_required_and_missing_fails(self, tmp_path) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nTask: T3.1"
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")  # no T3.1 row
        exit_code = main(["--branch", "fix/unrelated", "--pr-body", body, "--ledger-path", str(ledger)])
        assert exit_code == 1

    def test_task_colon_line_required_and_present_passes(self, tmp_path) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nTask: T3.1"
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T3.1"), encoding="utf-8")
        exit_code = main(["--branch", "fix/unrelated", "--pr-body", body, "--ledger-path", str(ledger)])
        assert exit_code == 0

    def test_bracket_marker_is_detected(self) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nCloses [T3.1]."
        assert detect_task_ids_from_body(body) == {"T3.1"}

    def test_bracket_marker_required_and_missing_fails(self, tmp_path) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nCloses [T3.1]."
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")  # no T3.1 row
        exit_code = main(["--branch", "fix/unrelated", "--pr-body", body, "--ledger-path", str(ledger)])
        assert exit_code == 1

    def test_bracket_marker_required_and_present_passes(self, tmp_path) -> None:
        body = PR_38_PROSE_EXCERPT + "\n\nCloses [T3.1]."
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T3.1"), encoding="utf-8")
        exit_code = main(["--branch", "fix/unrelated", "--pr-body", body, "--ledger-path", str(ledger)])
        assert exit_code == 0

    def test_mixed_prose_and_marker_binds_only_the_marker_id(self) -> None:
        # The prose mentions T3.1; the marker line closes T0.5. Only T0.5 binds.
        body = PR_38_PROSE_EXCERPT + "\n\nTask: T0.5"
        assert detect_task_ids_from_body(body) == {"T0.5"}

    def test_mixed_prose_and_marker_end_to_end_ignores_the_prose_id(self, tmp_path) -> None:
        # Ledger has a row for T0.5 (the real marker) but NOT T3.1 (the prose
        # mention) — must still pass, since T3.1 was never bound in the first place.
        body = PR_38_PROSE_EXCERPT + "\n\nTask: T0.5"
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")
        exit_code = main(["--branch", "fix/unrelated", "--pr-body", body, "--ledger-path", str(ledger)])
        assert exit_code == 0


class TestNoSilentExceptionSwallowing:
    """The CLI catches only the specific I/O/decoding exceptions it documents.
    An unexpected exception type must propagate (crashing the process, which
    is still a nonzero exit) rather than being swallowed into exit 0 — there
    must be no bare `except Exception` (or bare `except:`) anywhere on the
    fail path that could turn a real error into a false pass."""

    def test_unexpected_ledger_read_error_propagates_instead_of_passing(self, tmp_path, monkeypatch) -> None:
        class _BoomError(Exception):
            """Deliberately not OSError/UnicodeDecodeError — must NOT be caught."""

        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.5"), encoding="utf-8")

        original_read_text = Path.read_text

        def _boom_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if self == ledger:
                raise _BoomError("simulated unexpected failure reading the ledger")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", _boom_read_text)

        with pytest.raises(_BoomError):
            main(
                [
                    "--branch",
                    "t0.5-evidence-ledger",
                    "--pr-body",
                    "no mention here",
                    "--ledger-path",
                    str(ledger),
                ]
            )

    def test_source_has_no_bare_except(self) -> None:
        import inspect

        import scripts.ci.check_evidence_ledger as module

        source = inspect.getsource(module)
        assert "except:" not in source
        assert "except Exception" not in source
        assert "except BaseException" not in source


# ---------------------------------------------------------------------------
# CLI parser sanity
# ---------------------------------------------------------------------------


class TestArgParser:
    def test_defaults(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.branch is None
        assert args.pr_body is None
        assert args.pr_body_file is None
        assert args.ledger_path == "docs/evidence-ledger.md"

    def test_explicit_args(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--branch",
                "t0.5-evidence-ledger",
                "--pr-body",
                "Task: T0.5",
                "--ledger-path",
                "some/other/path.md",
            ]
        )
        assert args.branch == "t0.5-evidence-ledger"
        assert args.pr_body == "Task: T0.5"
        assert args.ledger_path == "some/other/path.md"


class TestConflictMarkerGuard:
    """Ledger integrity: committed git conflict markers must fail the gate.

    Incident precedent: PR #46 merged a stash-conflict block into the ledger;
    row-presence matching passed right over it.
    """

    _CORRUPT = (
        "| Task ID | ... |\n"
        "|---|---|\n"
        "| T9.9 | row |\n"
        "<<<<<<< Updated upstream\n"
        "| T9.8 | a |\n"
        "=======\n"
        "| T9.8 | b |\n"
        ">>>>>>> Stashed changes\n"
    )

    def test_markers_fail_even_with_matching_row(self) -> None:
        result = evaluate({"T9.9"}, self._CORRUPT)
        assert not result.ok
        assert "conflict markers" in result.message

    def test_markers_fail_with_no_detected_ids(self) -> None:
        result = evaluate(set(), self._CORRUPT)
        assert not result.ok
        assert "conflict markers" in result.message

    def test_clean_ledger_unaffected(self) -> None:
        clean = "| Task ID | ... |\n|---|---|\n| T9.9 | row |\n"
        assert evaluate({"T9.9"}, clean).ok
        assert evaluate(set(), clean).ok

    def test_marker_line_numbers_reported(self) -> None:
        assert find_conflict_markers(self._CORRUPT) == [4, 6, 8]

    def test_table_pipes_and_equals_in_cells_do_not_trigger(self) -> None:
        benign = "| T9.9 | uses ======= inside a cell | x=======y |\n"
        assert find_conflict_markers(benign) == []


# ---------------------------------------------------------------------------
# LEDGER-GATE-ID-PATTERN-INERT (round 3): the ledger's real first column uses
# id shapes the old `t\d+\.\d+`-only grammar could never match — `10.3`,
# `GAP-AF-02`, `mzo-040`, `flip-path-review-gate` — which meant a PR citing
# one of those via a `Tasks:`/`[...]` marker made the gate print "PASS: No
# task ID detected" and exit 0 without ever consulting the ledger. These
# tests lock in the widened grammar (`_TASK_ID_PATTERN`) at each binding
# position it now covers, prove the pre-existing false-positive protections
# (prose-never-binds, branch-rule-stays-narrow) survive the widening, prove
# the NEW bracket-purity gate this widening required (a bracket's entire
# payload must be pure id tokens or it binds nothing — see the module
# docstring's PR #274 example), and assert 100% row-coverage of the real
# ledger table.
# ---------------------------------------------------------------------------


class TestWidenedGrammarLedgerRows:
    """`_LEDGER_ROW_RE` / `extract_ledger_task_ids` accept every id shape the
    live ledger table actually uses, not just `T<phase>.<n>`."""

    def test_bare_numeric_phase_dot_n_row(self) -> None:
        # The real ledger carries rows like "10.3", "11.5", "9.5", "9.7" with
        # no leading letter at all.
        assert extract_ledger_task_ids(_ledger_with_rows("10.3")) == {"10.3"}

    def test_gap_hyphenated_row(self) -> None:
        assert extract_ledger_task_ids(_ledger_with_rows("GAP-AF-02")) == {"GAP-AF-02"}

    def test_gap_row_with_two_dotted_segments(self) -> None:
        # Real row: "GAP-9.3-9.4".
        assert extract_ledger_task_ids(_ledger_with_rows("GAP-9.3-9.4")) == {"GAP-9.3-9.4"}

    def test_kebab_slug_row_no_digits(self) -> None:
        # Real row: "flip-path-review-gate" — no digit anywhere in the id.
        ids = extract_ledger_task_ids(_ledger_with_rows("flip-path-review-gate"))
        assert ids == {"FLIP-PATH-REVIEW-GATE"}

    def test_lowercase_lettercode_row(self) -> None:
        # Real row: "mzo-040".
        assert extract_ledger_task_ids(_ledger_with_rows("mzo-040")) == {"MZO-040"}

    def test_original_t_dot_n_shape_still_a_subset(self) -> None:
        assert extract_ledger_task_ids(_ledger_with_rows("T0.5")) == {"T0.5"}

    def test_row_with_trailing_parenthetical_annotation_still_matches_leading_id(self) -> None:
        # Real rows: "T0.5 (gate precision fix)", "T1.3 (artifact fix: SP-OP-AUTH-001 gateway)".
        ledger_text = LEDGER_HEADER + (
            "| T0.5 (gate precision fix) | 2026-07-17 | gates-engineer (R2) | — | 80b34c8 | "
            "scripts/ci/check_evidence_ledger.py | sha256:abc | implemented — unverified |\n"
        )
        assert extract_ledger_task_ids(ledger_text) == {"T0.5"}

    def test_header_and_separator_still_excluded_under_widened_grammar(self) -> None:
        # "Task ID" is two plain words (no "-"/"." separator between them) so it
        # never qualifies as a single id-shaped token, widened grammar or not.
        assert extract_ledger_task_ids(LEDGER_HEADER) == set()

    def test_bare_single_word_cell_never_matches(self) -> None:
        # A hypothetical single-word, unseparated cell value must not bind —
        # the grammar requires at least one "-"/"." segment, structurally.
        ledger_text = LEDGER_HEADER + "| shadow | 2026-07-17 | x | x | x | x | x | x |\n"
        assert extract_ledger_task_ids(ledger_text) == set()


class TestWidenedGrammarTasksLine:
    """`Tasks:`/`Task:` line detection accepts every id shape, and multiple
    ids of different shapes on the same marker all bind."""

    def test_gap_id_on_task_line(self) -> None:
        assert detect_task_ids_from_body("Task: GAP-AF-02") == {"GAP-AF-02"}

    def test_kebab_id_on_task_line(self) -> None:
        assert detect_task_ids_from_body("Tasks: mzo-040") == {"MZO-040"}

    def test_mixed_kebab_and_t_id_on_same_tasks_line(self) -> None:
        # A "Tasks:" line naming both a legacy T-id and a round-3 kebab id
        # must bind both, not just one.
        assert detect_task_ids_from_body("Tasks: T0.5 mzo-040") == {"T0.5", "MZO-040"}

    def test_bare_numeric_id_on_task_line(self) -> None:
        assert detect_task_ids_from_body("Task: 10.3") == {"10.3"}


class TestWidenedGrammarProseStillNeverBinds:
    """The T0.5 anti-regression holds under the widened grammar: a hyphenated
    or dotted word that merely APPEARS in prose — never inside a `Tasks:`
    line or a pure-id bracket — must not bind, exactly like the narrow
    grammar's prose-never-binds guarantee."""

    def test_hyphenated_prose_word_outside_any_marker_does_not_bind(self) -> None:
        body = "This PR is a fail-closed, root-cause fix for the flip-path-review-gate follow-up."
        assert detect_task_ids_from_body(body) == set()

    def test_dotted_prose_token_outside_any_marker_does_not_bind(self) -> None:
        body = "Bumps the pinned image from cibseven:2.1.0 to cibseven:2.2.0."
        assert detect_task_ids_from_body(body) == set()

    def test_kebab_word_after_task_word_without_colon_does_not_bind(self) -> None:
        # Same T0.5 gate-precision rule as the colon-less "Task T0.5" case,
        # now checked against a widened-grammar id shape.
        assert detect_task_ids_from_body("Task mzo-040 closes the ledger gap.") == set()


class TestBracketPurityGate:
    """New for LEDGER-GATE-ID-PATTERN-INERT: a `[...]` bracket binds ONLY when
    its entire trimmed payload is nothing but id tokens. Proven necessary by
    replaying the widened grammar over this repo's last 40 merged PRs (see
    the round-3 report) — PR #274's real body contains
    "[owner-review: `glosa_triage.dmn` + shadow + `action-approvals.yaml`]",
    ordinary prose in brackets, which the naive widening would have bound as
    OWNER-REVIEW / ACTION-APPROVALS.YAML and demanded nonexistent ledger rows
    for.
    """

    def test_pure_single_id_bracket_still_binds(self) -> None:
        assert detect_task_ids_from_body("Closes [GAP-AF-02].") == {"GAP-AF-02"}

    def test_pure_multi_id_bracket_binds_all(self) -> None:
        # The existing "[T0.1 T0.2 T0.3]" multi-ID convention (commit 579e8f9)
        # must keep working, now alongside a non-t id in the same bracket.
        assert detect_task_ids_from_body("[T0.5 GAP-AF-02]") == {"T0.5", "GAP-AF-02"}

    def test_two_adjacent_pure_brackets_both_bind(self) -> None:
        assert detect_task_ids_from_body("[T1.8][mzo-040]") == {"T1.8", "MZO-040"}

    def test_pr_274_style_prose_bracket_binds_nothing(self) -> None:
        # Verbatim (trimmed) payload shape from PR #274's real body.
        body = "PR-4 CONTAS [owner-review: `glosa_triage.dmn` + shadow + `action-approvals.yaml`]"
        assert detect_task_ids_from_body(body) == set()

    def test_markdown_link_text_bracket_binds_nothing(self) -> None:
        # The "[Claude Code](https://claude.com/claude-code)" footer appears
        # in nearly every AI-authored PR body in this repo's history — must
        # never bind, even though this specific payload has no id-shaped
        # token anyway (belt-and-braces: two plain words, no separator).
        assert detect_task_ids_from_body("[Claude Code](https://claude.com/claude-code)") == set()

    def test_bracket_with_id_plus_trailing_prose_binds_nothing(self) -> None:
        # A bracket must be PURE ids, not "starts with an id" — this is the
        # asymmetry with `Tasks:` lines (which do scan past trailing prose),
        # documented in the module docstring.
        assert detect_task_ids_from_body("[T0.5 and also some prose]") == set()

    def test_empty_bracket_binds_nothing(self) -> None:
        assert detect_task_ids_from_body("[]") == set()

    def test_end_to_end_pr_274_style_body_passes_without_ledger(self, tmp_path: Path) -> None:
        # End-to-end proof: the prose bracket must not force a ledger read
        # demanding OWNER-REVIEW/ACTION-APPROVALS.YAML rows that don't exist.
        missing_ledger = tmp_path / "does-not-exist.md"
        body = (
            "PR-4 CONTAS [owner-review: `glosa_triage.dmn` + shadow + "
            "`action-approvals.yaml`]\n\n[Claude Code](https://claude.com/claude-code)"
        )
        exit_code = main(
            [
                "--branch",
                "docs/adr-0040-perspectiva-operadora",
                "--pr-body",
                body,
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 0


class TestWidenedGrammarBoundButMissingStillFails:
    """The core fail-closed contract must hold for every newly-reachable id
    shape, not just T<phase>.<n>: a bound id with no ledger row still FAILS."""

    def test_gap_id_bound_without_row_fails(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")  # no GAP-AF-02 row
        exit_code = main(
            ["--branch", "fix/unrelated", "--pr-body", "Task: GAP-AF-02", "--ledger-path", str(ledger)]
        )
        assert exit_code == 1

    def test_kebab_id_bound_without_row_fails(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("T0.1"), encoding="utf-8")
        exit_code = main(
            ["--branch", "fix/unrelated", "--pr-body", "[mzo-041]", "--ledger-path", str(ledger)]
        )
        assert exit_code == 1

    def test_gap_id_bound_with_row_present_passes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence-ledger.md"
        ledger.write_text(_ledger_with_rows("GAP-AF-02"), encoding="utf-8")
        exit_code = main(
            ["--branch", "fix/unrelated", "--pr-body", "Task: GAP-AF-02", "--ledger-path", str(ledger)]
        )
        assert exit_code == 0


class TestBranchRuleStaysNarrow:
    """Orchestrator decision (round 3): the branch-name rule is NOT widened —
    a kebab branch has no unambiguous id segment, so binding from it would be
    guessing. Only `^t<phase>.<n>-` binds via branch name; every other id
    shape binds ONLY via `Tasks:`/`[...]` in the body."""

    def test_kebab_branch_alone_detects_nothing(self) -> None:
        assert detect_task_ids_from_branch("fix/kafka-num-partitions-compose") == set()

    def test_gap_shaped_branch_prefix_does_not_bind_via_branch(self) -> None:
        # Even a branch that LOOKS like it starts with an id-shaped slug must
        # not bind through the branch-name path — only Tasks:/brackets do.
        assert detect_task_ids_from_branch("gap-af-02-fix-something") == set()

    def test_kebab_branch_end_to_end_passes_without_ledger_when_body_silent(self, tmp_path: Path) -> None:
        missing_ledger = tmp_path / "does-not-exist.md"
        exit_code = main(
            [
                "--branch",
                "fix/kafka-num-partitions-compose",
                "--pr-body",
                "Bumps KAFKA_NUM_PARTITIONS for the compose stack.",
                "--ledger-path",
                str(missing_ledger),
            ]
        )
        assert exit_code == 0


class TestRealLedgerRowCoverage:
    """LEDGER-GATE-ID-PATTERN-INERT's acceptance bar: every real data row in
    docs/evidence-ledger.md's first column must be matchable by
    `_LEDGER_ROW_RE`. Cross-checked independently of the module under test
    via a `grep -c '^|'`-equivalent line count so this test cannot be
    satisfied by a regex that happens to agree with itself."""

    def test_all_real_ledger_data_rows_match(self) -> None:
        ledger_path = _REPO_ROOT / "docs" / "evidence-ledger.md"
        text = ledger_path.read_text(encoding="utf-8")
        lines = text.split("\n")

        # Independent count: every line starting with "|" (grep -c '^|' equivalent).
        pipe_lines = [line for line in lines if line.startswith("|")]
        # The table has exactly one header text row ("| Task ID | ...") and one
        # separator row ("|---|...|"); every other "|"-started line is a data row.
        header_like = [
            line
            for line in pipe_lines
            if re.match(r"^\|\s*Task\s+ID\s*\|", line, re.IGNORECASE) or re.match(r"^\|[-:\s|]+\|$", line)
        ]
        assert len(header_like) == 2, (
            f"expected exactly 1 header row + 1 separator row, found {len(header_like)}: {header_like}"
        )
        data_row_count = len(pipe_lines) - len(header_like)

        matched_ids = extract_ledger_task_ids(text)
        matched_row_count = sum(1 for _ in _row_re_matches(text))

        # Report-friendly numbers (also asserted below): total pipe lines,
        # data rows, and how many of those data rows the widened regex binds.
        print(
            f"\n[LEDGER-GATE-ID-PATTERN-INERT] grep -c '^|' = {len(pipe_lines)}; "
            f"header/separator = {len(header_like)}; data rows = {data_row_count}; "
            f"_LEDGER_ROW_RE matches = {matched_row_count}; unique ids = {len(matched_ids)}"
        )

        assert matched_row_count == data_row_count, (
            f"_LEDGER_ROW_RE matched {matched_row_count} of {data_row_count} real ledger data rows "
            "— every data row must be matchable (LEDGER-GATE-ID-PATTERN-INERT acceptance bar)."
        )


def _row_re_matches(ledger_text: str) -> list[str]:
    """Re-derive the same match set `extract_ledger_task_ids` uses, for an
    independent-looking row count in the coverage test above (imports the
    module's own compiled regex rather than re-deriving the pattern, since
    the pattern string itself is the thing under test — the independence
    here is the external `grep`-equivalent line count, not a second regex)."""
    from scripts.ci.check_evidence_ledger import _LEDGER_ROW_RE

    return [m.group(1) for m in _LEDGER_ROW_RE.finditer(ledger_text)]


class TestMutationGrammarReverted:
    """Documents the mutation this task requires: reverting `_TASK_ID_PATTERN`
    to the old `t\\d+\\.\\d+`-only shape must turn specific tests RED. This
    test class itself doesn't perform the mutation (that's a manual gate-
    proof step, recorded in the PR/report) — it names, in one place, exactly
    which tests are the mutation witnesses, so a reviewer can run them
    against a reverted pattern and confirm the failure.

    Mutation witnesses (RED when `_TASK_ID_PATTERN` reverts to `t\\d+\\.\\d+`):
      - TestWidenedGrammarLedgerRows::test_bare_numeric_phase_dot_n_row
      - TestWidenedGrammarLedgerRows::test_gap_hyphenated_row
      - TestWidenedGrammarLedgerRows::test_kebab_slug_row_no_digits
      - TestWidenedGrammarTasksLine::test_gap_id_on_task_line
      - TestBracketPurityGate::test_pure_single_id_bracket_still_binds
      - TestRealLedgerRowCoverage::test_all_real_ledger_data_rows_match
        (269 -> 107 matched data rows; hard assertion failure)
    """

    def test_witness_list_is_non_empty_and_documented(self) -> None:
        # Trivial self-check that this documentation block exists and the
        # witness tests it names are real, collectible tests (guards against
        # the docstring silently drifting from the actual test names).
        assert TestWidenedGrammarLedgerRows.test_bare_numeric_phase_dot_n_row
        assert TestWidenedGrammarLedgerRows.test_gap_hyphenated_row
        assert TestWidenedGrammarLedgerRows.test_kebab_slug_row_no_digits
        assert TestWidenedGrammarTasksLine.test_gap_id_on_task_line
        assert TestBracketPurityGate.test_pure_single_id_bracket_still_binds
        assert TestRealLedgerRowCoverage.test_all_real_ledger_data_rows_match
