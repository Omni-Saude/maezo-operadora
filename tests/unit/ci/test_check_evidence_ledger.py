"""Unit tests for scripts.ci.check_evidence_ledger — the T0.5 evidence-ledger CI gate.

Covers the acceptance criteria verbatim: green on a PR carrying a ledger entry
for its task ID, red on one that doesn't, fail-closed on any input error, and
no task ID detected is always a pass. TDD London School: the pure core
(detection + evaluation) is tested directly; the CLI wrapper is tested through
`main()` with monkeypatched env vars and real temp files (no I/O mocking of
the core logic itself).
"""

from __future__ import annotations

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
    main,
)

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

    def test_task_no_colon_form(self) -> None:
        assert detect_task_ids_from_body("Task T0.5 closes the ledger gap.") == {"T0.5"}

    def test_task_id_form(self) -> None:
        assert detect_task_ids_from_body("**Task ID:** T0.5") == {"T0.5"}

    def test_case_insensitive(self) -> None:
        assert detect_task_ids_from_body("task: t0.5") == {"T0.5"}

    def test_multiple_task_ids(self) -> None:
        body = "Task: T0.5\n\nAlso closes Task: T0.6 in the same sweep."
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
        assert detect_task_ids("t0.5-evidence-ledger", "Also closes Task: T0.6") == {"T0.5", "T0.6"}


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
                "Also closes Task: T0.6 in the same sweep.",
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
                "Also closes Task: T0.6 in the same sweep.",
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
        body_file.write_text("Task ID: T0.5", encoding="utf-8")
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
