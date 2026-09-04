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

PR-body detection binds ONLY on an explicit closure marker — a `Task:`/`Tasks:`
line or a `[...]` bracket — never on a bare prose mention of an id-shaped token
(T0.5 gate-precision follow-up, after PR #38's "suite de integração planejada
— T3.1" false-positived a T3.1 ledger demand).

Id grammar (LEDGER-GATE-ID-PATTERN-INERT, round 3)
----------------------------------------------------
The ledger's real first column is NOT limited to the `T<phase>.<n>` shape:
across the live table it also carries bare `<phase>.<n>` rows (`10.3`,
`11.5`), `GAP-`/`D-`/other hyphenated prefixes (`GAP-AF-02`, `D2-02-EXEC`),
and free-form kebab slugs (`mzo-040`, `flip-path-review-gate`,
`onda3-a2a-distribution`). The original `t\\d+\\.\\d+`-only grammar left
every one of those rows structurally unreachable: a PR that cited one via a
`Tasks:` line or bracket marker made this gate print "PASS: No task ID
detected" and exit 0 — a REQUIRED check passing vacuously, never having
looked at the ledger at all. `_TASK_ID_PATTERN` widens the id shape (used by
`_TASK_ID_TOKEN_RE`, for `Tasks:`-line and bracket payloads, and by
`_LEDGER_ROW_RE`, for the ledger's own first column) to
``[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+`` — one or more alphanumeric segments
joined by `-` or `.`, case-insensitive, with the original `t\\d+\\.\\d+`
shape still a strict subset. The `+` (not `*`) on the trailing group is
deliberate: it requires AT LEAST ONE separator, so a bare single English/PT
word (including the ledger's own header cell, "Task ID") never qualifies —
this is what keeps the widened grammar from re-opening the exact prose-false-
positive hole T0.5 closed, without hardcoding an exclusion list of English
words. `_LEDGER_ROW_RE` additionally no longer requires the id to be the
row's ENTIRE first cell — several real rows carry an id followed by a
parenthetical annotation in the same cell (`T0.5 (gate precision fix)`,
`T1.3 (artifact fix: SP-OP-AUTH-001 gateway)`); the regex now matches the
LEADING id token and stops (lookahead on whitespace-or-pipe) rather than
demanding an immediate closing pipe.

Branch-name detection (`_BRANCH_TASK_RE`) is DELIBERATELY NOT widened and
stays `^t<phase>.<n>-` only. A kebab branch name like
`fix/kafka-num-partitions-compose` has no unambiguous id segment — binding
from the branch name would mean guessing which hyphenated run of the slug is
"the id", which is a false-positive vector, not a widening. Every non-`t.n`
id form binds ONLY via an explicit `Tasks:` line or `[...]` bracket in the PR
body (see docs/evidence-ledger.md's header for the convention: a PR closing
a ledger row declares `Tasks: <ID>` or `[<ID>]` in its body).

Bracket payloads are held to a STRICTER rule than `Tasks:` lines: a bracket's
entire trimmed content must be nothing but whitespace-separated id tokens
(`_BRACKET_PURE_IDS_RE`) or it binds NOTHING at all — not even the tokens
inside it that happen to look id-shaped. This was proven necessary, not
theoretical: replaying the widened grammar over this repo's last 40 merged
PR bodies (round-3 false-positive sweep) found a real `[owner-review:
`glosa_triage.dmn` + shadow + `action-approvals.yaml`]` bracket (PR #274) —
ordinary prose-in-brackets, not a task marker — that would otherwise have
spuriously bound `OWNER-REVIEW` and `ACTION-APPROVALS.YAML` and demanded
ledger rows that were never meant to exist. `Tasks:`/`Task:` lines keep the
pre-existing (T0.5) whole-remainder token scan instead of the same
whole-payload purity gate: an existing, deliberately pinned test
(`TestExactIdCollisionBoundaries::test_body_mentions_of_both_ids_stay_
distinct`) requires a second `Task:` mention later on the SAME line to also
bind, which a purity gate on the line's tail would break. This is an accepted
residual risk, not an oversight: across the 40 replayed PRs every real
`Task:`/`Tasks:` line was a bare `Task: <ID>` with nothing trailing (see the
round-3 report), so the risk of a hyphenated prose word (e.g. "closes the
follow-up work") on the SAME line as a real marker spuriously binding is
real but currently unobserved in this repo's history — the convention
paragraph in docs/evidence-ledger.md now tells authors to keep the `Tasks:`
line to ids only, precisely to keep it that way.

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
        --pr-body "Task: T0.5" \\
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
# Deliberately NOT widened to the general id grammar below — see the module
# docstring's "Branch-name detection" section (LEDGER-GATE-ID-PATTERN-INERT).
_BRANCH_TASK_RE = re.compile(r"^\s*(t\d+\.\d+)-", re.IGNORECASE)

# General id-token grammar (LEDGER-GATE-ID-PATTERN-INERT): one or more
# alphanumeric segments joined by "-" or ".", requiring AT LEAST ONE separator
# (the trailing group is `+`, not `*`). Matches every shape the live ledger's
# first column actually uses — `T0.5`, `10.3`, `GAP-AF-02`, `D2-02-EXEC`,
# `mzo-040`, `flip-path-review-gate` — while a bare single word never
# qualifies (no separator to match), which is what keeps this from matching
# the ledger's own header cell ("Task ID") or ordinary prose words. See the
# module docstring for the full rationale and the 40-PR false-positive replay
# that shaped it.
_TASK_ID_PATTERN = r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+"

# PR-body detection binds ONLY on an explicit task-closure marker — never on a bare
# prose mention of a task-ID-shaped token. Two marker forms are recognized:
#
# (a) A "Task:"/"Tasks:" line: the marker must be the first token on its line
#     (leading whitespace aside) — a colon is required. This is deliberately
#     anchored to line-start so a sentence that merely *mentions* a task ID
#     mid-line — e.g. "the porting is task T3.1, not yet done" or "suite de
#     integração planejada — T3.1" (the exact PR #38 phrasing that previously
#     false-positived a T3.1 ledger demand) — never binds. Everything after the
#     colon, to end of line, is scanned for IDs, so "Task: T0.1 T0.2" binds both
#     (and so does a second "Task:" mention later on the same line — see
#     TestExactIdCollisionBoundaries::test_body_mentions_of_both_ids_stay_distinct).
_BODY_TASK_LINE_RE = re.compile(r"^[ \t]*Tasks?\s*:\s*(?P<ids>.+)$", re.IGNORECASE | re.MULTILINE)

# (b) A "[...]" bracket marker anywhere in the body — the PR-title convention
#     (e.g. "[T0.5]", "[T1.8][T1.9]", or a multi-ID bracket like
#     "[T0.1 T0.2 T0.3]" as used in commit 579e8f9) carried into bodies. Every
#     ID-shaped token found inside any bracket pair binds — a stricter
#     single-ID-only reading would under-detect a convention already live in
#     this repo's history, which is the wrong direction for a fail-closed gate.
#     A bracket's payload must ALSO pass `_BRACKET_PURE_IDS_RE` (below) before
#     any of its tokens bind — see the module docstring's PR #274 example.
_BODY_BRACKET_TASK_RE = re.compile(r"\[([^\[\]]*)\]")

# Pulls every id-shaped token out of a marker's payload (the tail of a "Task:"
# line, or a purity-gated "[...]" bracket) so a marker carrying more than one
# ID (e.g. "Task: T0.1 T0.2") binds all of them, not just the first —
# resolving that ambiguity by binding, never by silently dropping IDs.
_TASK_ID_TOKEN_RE = re.compile(rf"\b({_TASK_ID_PATTERN})\b", re.IGNORECASE)

# Gate for bracket payloads only (LEDGER-GATE-ID-PATTERN-INERT): the ENTIRE
# trimmed bracket content must be nothing but whitespace-separated id tokens,
# or the bracket binds nothing. Brackets are heavily reused in this repo's PR
# bodies for things that are not task markers at all — the "[Claude Code]"
# link text in every AI-authored PR footer, "[skip ci]"-style asides, prose
# parentheticals like "[owner-review: ... + action-approvals.yaml]" — and the
# general id grammar above is permissive enough that some of that prose is
# itself id-shaped (a hyphenated or dotted compound word). `Tasks:` lines
# don't need this same gate: see the comment on `_BODY_TASK_LINE_RE`.
_BRACKET_PURE_IDS_RE = re.compile(rf"^(?:\s*{_TASK_ID_PATTERN})+\s*$", re.IGNORECASE)

# Ledger rows look like "| T0.5 | 2026-07-16 | ... |", but also
# "| T0.5 (gate precision fix) | ..." and "| GAP-AF-02 | ... |" — the id is
# always the LEADING token of the first cell, not necessarily its entire
# content. Matched at the start of a table row (ignoring leading whitespace),
# case-insensitive, so the header ("Task ID") and separator ("---") rows never
# match (neither contains an id-shaped token per `_TASK_ID_PATTERN`, which
# requires at least one "-"/"." separator — "Task" and "ID" are separate
# words, not a single hyphenated/dotted token, and "---" has no alphanumerics
# at all). The lookahead (whitespace or the next pipe), rather than requiring
# an immediate closing pipe, is what lets this match the leading id in a cell
# that also carries a parenthetical annotation.
_LEDGER_ROW_RE = re.compile(rf"^[ \t]*\|\s*({_TASK_ID_PATTERN})(?=[ \t]|\|)", re.IGNORECASE | re.MULTILINE)


def _normalize_task_id(raw: str) -> str:
    """Canonicalize a task-ID token to its uppercase form, e.g. 't0.5' -> 'T0.5'."""
    return raw.upper()


def detect_task_ids_from_branch(branch: str) -> set[str]:
    """Detect a task ID from a branch name matching `^t<phase>.<n>-`."""
    match = _BRANCH_TASK_RE.match(branch)
    return {_normalize_task_id(match.group(1))} if match else set()


def detect_task_ids_from_body(pr_body: str) -> set[str]:
    """Detect task IDs bound by an explicit closure marker in a PR body.

    Binds ONLY on (a) a "Task:"/"Tasks:" line or (b) a "[...]" bracket marker
    whose ENTIRE payload is id tokens (see the module docstring and the
    comments above these regexes). A bare prose mention of an id-shaped token
    never binds on its own, and a bracket that mixes id-shaped tokens with
    any other content (prose, markdown-link text, punctuation) binds nothing
    at all — see `_BRACKET_PURE_IDS_RE`.
    """
    ids: set[str] = set()
    for line_match in _BODY_TASK_LINE_RE.finditer(pr_body):
        tokens = _TASK_ID_TOKEN_RE.finditer(line_match.group("ids"))
        ids.update(_normalize_task_id(m.group(1)) for m in tokens)
    for bracket_match in _BODY_BRACKET_TASK_RE.finditer(pr_body):
        payload = bracket_match.group(1)
        if not _BRACKET_PURE_IDS_RE.match(payload):
            continue
        tokens = _TASK_ID_TOKEN_RE.finditer(payload)
        ids.update(_normalize_task_id(m.group(1)) for m in tokens)
    return ids


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


# Git conflict markers at line start. `=======` alone is ambiguous with setext
# underlines in general markdown, but the ledger is a single table document —
# a bare 7-equals line only ever means an unresolved (or committed) conflict.
# Incident precedent: PR #46 merged a stash-conflict block into the ledger and
# the row-presence check happily passed over it.
_CONFLICT_MARKER_RE = re.compile(r"^(?:<{7}(?: |$)|={7}$|>{7}(?: |$))", re.MULTILINE)


def find_conflict_markers(ledger_text: str) -> list[int]:
    """Return 1-indexed line numbers of git conflict markers in the ledger."""
    return [ledger_text.count("\n", 0, m.start()) + 1 for m in _CONFLICT_MARKER_RE.finditer(ledger_text)]


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
    if ledger_text is not None:
        marker_lines = find_conflict_markers(ledger_text)
        if marker_lines:
            return CheckResult(
                ok=False,
                detected_ids=frozenset(detected_ids),
                missing_ids=frozenset(),
                message=(
                    "Ledger integrity failure: git conflict markers at line(s) "
                    f"{marker_lines} of the evidence ledger — resolve the conflict "
                    "properly before merging (fail-closed regardless of task rows)."
                ),
            )

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

    # Read the ledger whenever it exists (not only when IDs were detected) so
    # the conflict-marker integrity guard covers every PR, task-scoped or not.
    ledger_text: str | None = None
    ledger_path = Path(args.ledger_path)
    if detected_ids or ledger_path.exists():
        try:
            ledger_text = ledger_path.read_text(encoding="utf-8")
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
