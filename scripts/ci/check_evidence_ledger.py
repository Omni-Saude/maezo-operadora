#!/usr/bin/env python3
"""CI gate: PRs closing a task ID must carry an evidence-ledger row for it (T0.5).

Purpose
-------
This repo's history includes a cautionary tale: prior agents self-certified
milestones against gates that returned success unconditionally (see
``src/maezo/platform/validation/cli.py``, the pre-M13 stub). This script exists
to make that anti-pattern structurally impossible for the evidence ledger: a
task ID detected in the PR's branch name or body REQUIRES a matching row in
``docs/evidence-ledger.md`` at the PR's head commit, or the check fails.

Design
------
The logic is a pure, dependency-free core (``detect_task_ids``,
``extract_ledger_task_ids``, ``evaluate``) plus a thin CLI wrapper
(``main``) that reads the branch name / PR body / ledger file and reports
results. The core takes no environment or filesystem input, so it is fully
unit-testable without mocking I/O.

Fail-closed contract
---------------------
- No task ID found in branch name or PR body  -> PASS (exit 0). Nothing to
  check; most PRs do not close a task.
- Task ID(s) found and a matching ledger row exists for every one of them
  -> PASS (exit 0).
- Task ID(s) found but the ledger is missing a row for one or more of them
  -> FAIL (exit 1).
- Any error obtaining the required inputs (branch name unset, PR body unset,
  ledger file unreadable/undecodable when a row lookup is required) -> FAIL
  (exit 1). There is no "warn and pass" path and no ``continue-on-error``:
  ambiguity is treated as failure, never as skip.

Usage (CI)
----------
Invoked by .github/workflows/evidence-ledger.yml with the untrusted PR
branch name and body passed through ``env:`` (never interpolated into the
shell command text, to avoid script-injection via a crafted PR body/branch):

    PR_HEAD_REF=<branch> PR_BODY=<body> python3 scripts/ci/check_evidence_ledger.py

Usage (local / tests)
----------------------
    python3 scripts/ci/check_evidence_ledger.py \\
        --branch t0.5-evidence-ledger \\
        --pr-body "Task ID: T0.5" \\
        --ledger-path docs/evidence-ledger.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Task-ID detection
# ---------------------------------------------------------------------------

# Task branches follow `t<phase>.<n>-slug`, e.g. `t0.1-truth-reset`, `t1.9-remove-ceiling-bypass`.
_BRANCH_TASK_RE = re.compile(r"^\s*(t\d+\.\d+)-", re.IGNORECASE)

# PR-body mentions look like "Task: T0.1", "Task T0.1", "Task ID: T0.1", or
# "**Task ID:** T0.1" (Markdown bold) — case-insensitive. Deliberately anchored on the
# word "task" so a stray "T0.1"-shaped token elsewhere in the body (e.g. a version string)
# is never mistaken for a task-ID citation. Any run of non-alphanumeric filler (colons,
# whitespace, Markdown emphasis characters) between "task[ id]" and the token is accepted.
_BODY_TASK_RE = re.compile(r"\btask\b(?:\s*id\b)?[^a-zA-Z0-9]*(t\d+\.\d+)\b", re.IGNORECASE)

# Ledger rows look like "| T0.5 | 2026-07-16 | ... |". Matched at the start of a table row
# (ignoring leading whitespace), case-insensitive, so header ("Task ID") and separator
# ("---") rows never match (they contain no `t<digits>.<digits>` token).
_LEDGER_ROW_RE = re.compile(r"^[ \t]*\|\s*(t\d+\.\d+)\s*\|", re.IGNORECASE | re.MULTILINE)


def _normalize_task_id(raw: str) -> str:
    """Canonicalize a task-ID token to its uppercase form, e.g. 't0.5' -> 'T0.5'."""
    return raw.upper()


def detect_task_ids_from_branch(branch: str) -> set[str]:
    """Detect a task ID from a branch name matching `^t<phase>.<n>-`."""
    match = _BRANCH_TASK_RE.match(branch)
    return {_normalize_task_id(match.group(1))} if match else set()


def detect_task_ids_from_body(pr_body: str) -> set[str]:
    """Detect all task IDs cited in a PR body via 'Task[:] T<phase>.<n>' style mentions."""
    return {_normalize_task_id(m.group(1)) for m in _BODY_TASK_RE.finditer(pr_body)}


def detect_task_ids(branch: str, pr_body: str) -> set[str]:
    """Detect the full set of task IDs a PR claims to close (branch name + PR body)."""
    return detect_task_ids_from_branch(branch) | detect_task_ids_from_body(pr_body)


def extract_ledger_task_ids(ledger_text: str) -> set[str]:
    """Extract the set of task IDs that already have a row in the evidence ledger."""
    return {_normalize_task_id(m.group(1)) for m in _LEDGER_ROW_RE.finditer(ledger_text)}


# ---------------------------------------------------------------------------
# Pure evaluation core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """Outcome of the evidence-ledger check, independent of how inputs were obtained."""

    ok: bool
    detected_ids: frozenset[str]
    missing_ids: frozenset[str]
    message: str


def evaluate(detected_ids: set[str], ledger_text: str | None) -> CheckResult:
    """Decide pass/fail given already-detected task IDs and (maybe) ledger content.

    Args:
        detected_ids: Task IDs detected from the branch name and/or PR body.
        ledger_text: Full text of docs/evidence-ledger.md at the PR's head, or
            None if it was never read (only valid when detected_ids is empty —
            the caller is not required to read the ledger when no task ID was
            detected at all).

    Returns:
        A CheckResult. `ok=False` whenever a detected task ID has no matching
        ledger row, or whenever ledger_text is None despite a non-empty
        detected_ids (treated as fail-closed, never as pass/skip).
    """
    if not detected_ids:
        return CheckResult(
            ok=True,
            detected_ids=frozenset(),
            missing_ids=frozenset(),
            message="No task ID detected in branch name or PR body — ledger entry not required.",
        )

    frozen_detected = frozenset(detected_ids)

    if ledger_text is None:
        return CheckResult(
            ok=False,
            detected_ids=frozen_detected,
            missing_ids=frozen_detected,
            message=(
                f"Task ID(s) detected ({', '.join(sorted(frozen_detected))}) but the evidence "
                "ledger could not be read — failing closed."
            ),
        )

    ledger_ids = extract_ledger_task_ids(ledger_text)
    missing = frozenset(frozen_detected - ledger_ids)

    if missing:
        return CheckResult(
            ok=False,
            detected_ids=frozen_detected,
            missing_ids=missing,
            message=(
                f"Missing docs/evidence-ledger.md row(s) for task ID(s): {', '.join(sorted(missing))}. "
                "Add a row before merging (see docs/evidence-ledger.md header for the format)."
            ),
        )

    return CheckResult(
        ok=True,
        detected_ids=frozen_detected,
        missing_ids=frozenset(),
        message=(f"Evidence-ledger row(s) found for task ID(s): {', '.join(sorted(frozen_detected))}."),
    )


# ---------------------------------------------------------------------------
# CLI wrapper (I/O boundary)
# ---------------------------------------------------------------------------


class InputError(RuntimeError):
    """Raised when a required input cannot be obtained — always fail-closed."""


def _required_input(env_name: str, arg_value: str | None) -> str:
    """Resolve a required string input from a CLI arg or environment variable.

    An explicitly empty string (arg or env) is a valid value (e.g. an empty PR
    body). Only the *total absence* of both the arg and the env var is treated
    as an input error, since that means the caller (workflow) never wired the
    value up at all rather than the value legitimately being empty.
    """
    if arg_value is not None:
        return arg_value
    if env_name in os.environ:
        return os.environ[env_name]
    flag = env_name.lower().replace("_", "-")
    raise InputError(f"missing required input: pass --{flag} or set ${env_name}")


def _read_pr_body(args: argparse.Namespace) -> str:
    if args.pr_body_file is not None:
        return Path(args.pr_body_file).read_text(encoding="utf-8")
    return _required_input("PR_BODY", args.pr_body)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="check_evidence_ledger",
        description=(
            "Fail-closed CI gate: a PR whose branch/body cites a task ID must carry a "
            "matching row in docs/evidence-ledger.md at HEAD."
        ),
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Head branch name. Defaults to $PR_HEAD_REF. Required (arg or env).",
    )
    parser.add_argument(
        "--pr-body",
        default=None,
        help="PR body text. Defaults to $PR_BODY. Ignored if --pr-body-file is given.",
    )
    parser.add_argument(
        "--pr-body-file",
        default=None,
        help="Path to a file containing the PR body (overrides --pr-body/$PR_BODY).",
    )
    parser.add_argument(
        "--ledger-path",
        default="docs/evidence-ledger.md",
        help="Path to the evidence ledger (default: docs/evidence-ledger.md).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail) — never anything else, never raises."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        branch = _required_input("PR_HEAD_REF", args.branch)
        pr_body = _read_pr_body(args)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"[evidence-ledger-check] ERROR: could not read inputs: {exc}", file=sys.stderr)
        return 1

    detected_ids = detect_task_ids(branch, pr_body)

    ledger_text: str | None = None
    if detected_ids:
        try:
            ledger_text = Path(args.ledger_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"[evidence-ledger-check] ERROR: could not read ledger '{args.ledger_path}': {exc}",
                file=sys.stderr,
            )
            return 1

    result = evaluate(detected_ids, ledger_text)
    prefix = "[evidence-ledger-check]"
    print(f"{prefix} {'PASS' if result.ok else 'FAIL'}: {result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
