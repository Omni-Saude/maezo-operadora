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
looked at the ledger at all. `_TASK_ID_PATTERN` widens the id shape to
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

Where each id family binds (round-3 repair, after the 200-PR replay)
--------------------------------------------------------------------
A widened grammar is only safe where the position itself is unambiguous.
Replaying the first (round-3) implementation of this widening over the last
200 merged PRs (#85–#309) produced 19 RED against 0 for the pre-widening
script — every one of them a *false* positive, three of them on dependabot
PRs whose bodies nobody can edit. The repair narrows *positions*, not the
grammar:

    position               | legacy `t<phase>.<n>` | every other id shape
    -----------------------+-----------------------+----------------------
    branch-name prefix     | binds                 | NEVER binds
    `Task:`/`Tasks:` line  | binds                 | binds (leading run)
    `[...]` bracket        | binds                 | NEVER binds
    ledger first column    | matches               | matches

Branch-name detection (`_BRANCH_TASK_RE`) is DELIBERATELY NOT widened and
stays `^t<phase>.<n>-` only. A kebab branch name like
`fix/kafka-num-partitions-compose` has no unambiguous id segment — binding
from the branch name would mean guessing which hyphenated run of the slug is
"the id", which is a false-positive vector, not a widening.

`[...]` brackets are NOT widened either, for the same reason and with the
same evidence. Brackets in this repo's PR bodies are overwhelmingly not task
markers: markdown link text (`[Claude Code](...)`), dependabot's version and
package asides (`[langgraph-checkpoint]`, `[2.0.3]`), quoted regex character
classes (`[a-z]`), file names (`[contracts.lock.json]`) and prose asides
(`[owner-review: ... + action-approvals.yaml]`, PR #274). Six of the 200
replayed PRs — including three dependabot PRs — went RED purely on such a
bracket. A "the whole payload must be pure id tokens" gate cannot see this
class at all (a payload that IS one hyphenated token is "pure" by
construction), and the one that was tried also introduced catastrophic
backtracking on an attacker-controlled input (see "Regex safety" below). So
bracket payloads are scanned with the ORIGINAL narrow token regex
(`_LEGACY_TASK_ID_TOKEN_RE`, `\\b(t\\d+\\.\\d+)\\b`): byte-for-byte main's
behaviour, including `[T0.5 extra]` binding `T0.5` and `[T0.5](url)` link
text binding `T0.5`. Widened ids are NOT bracket-bindable — `[GAP-AF-02]`
and `[FAB-NOTIFIED-TRIO]` bind NOTHING; declare them on a `Tasks:` line.

`Task:`/`Tasks:` lines are therefore the ONLY position where a widened id
binds, and even there only as the LEADING RUN of id-shaped tokens after the
marker: tokens are taken left to right (separated by whitespace, `,` or `;`
— this repo's real history writes `Tasks: t2.6-eb3, t2.6-eb4, t3.3 — …`)
and the run STOPS at the first token that is not id-shaped. Scanning then
resumes only at the next `Task:`/`Tasks:` marker on the same line, which is
what keeps the deliberately pinned
`TestExactIdCollisionBoundaries::test_body_mentions_of_both_ids_stay_distinct`
("Task: T0.1 and also Task: T0.10 in the same sweep.") binding both ids
while `Tasks: FAB-NOTIFIED-TRIO — corrige o gate` binds only the gap id.
Under the pre-repair whole-line scan, 6 of the 200 replayed PRs bound
prose from such a line (`PR-OPEN`, `FAIL-CLOSED`, `6-WEEK-STALE`, `1.2.9`,
…); under the leading-run rule they bind exactly the ids they declare.

Two consequences of the leading-run rule are deliberate and documented:

- A token of the legacy slug shape `t<phase>.<n>-<slug>` binds its
  `t<phase>.<n>` PREFIX, not the whole token (`_LEGACY_SLUG_PREFIX_RE`) —
  exactly what `\\b(t\\d+\\.\\d+)\\b` did on main, and what keeps real PRs
  such as #106–#113 (`Tasks: t2.8-lgpd-identity-failclosed`, whose ledger
  row is the short `t2.8`) green. It is the one place where the bound id is
  not the literal token, and it is a LIVE FALSE-PASS hole, not a future
  false-RED one: the ledger already carries SEVEN rows of that shape —
  `t2.6-2`, `t2.6-7`, `t2.6-eb3`, `t2.6-eb4`, `t3.4-f2`, `t3.4-f3`,
  `t3.4-f4f5f1` — and NONE of them can ever be bound by a `Tasks:` line,
  because the prefix (`T2.6` / `T3.4`, both of which have their own rows)
  binds instead. The consequence: a BRAND-NEW id of that shape, e.g.
  `Tasks: t2.6-brand-new-gap`, binds `T2.6`, finds that row, and PASSES with
  no row of its own — the gate's whole purpose defeated for that one id
  family. This is preserved deliberately, on the orchestrator's round-3
  decision to keep main's behaviour and PRs #106–#113 green, and is
  disclosed rather than fixed: to close it, a future change would have to
  bind the whole token and backfill rows for the ids main never bound.
  Until then, do NOT mint new ledger ids of the shape `t<phase>.<n>-<slug>`.
- An ISO date IS id-shaped under this grammar (`2026-09-04` = digits,
  separator, digits). No exclusion list is introduced for it — the same
  "no hardcoded word list" reasoning as the `+` above — so a date placed
  INSIDE the leading run (`Tasks: GAP-1 2026-09-04`) would bind and demand
  a row. It is stopped by any prose in between (`Tasks: GAP-1 fechado em
  2026-09-04` binds only `GAP-1`) and did not occur in any of the 200
  replayed PRs. The convention paragraph in docs/evidence-ledger.md
  therefore states the rule positively: the `Tasks:` line carries ids and
  nothing else.

Regex safety (round-3 repair)
------------------------------
Every regex in this module must run in time linear in the input: `PR_BODY`
is attacker-controlled on a fork PR (see .github/workflows/evidence-ledger.yml)
and the job carries no `timeout-minutes`, so a catastrophically backtracking
pattern is a 6-hour runner pin and a permanently pending REQUIRED check. The
first round-3 implementation shipped exactly that: a bracket-purity regex
`^(?:\\s*[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+)+\\s*$` whose optional `\\s*`
separator inside an outer `+` made a run like `aa-aa-aa` splittable in
exponentially many ways — a 204-character bracket of 12 real gap ids plus
one stray word took >10 s, and a 437-byte PR body never finished. That regex
is deleted, not patched: brackets no longer use the widened grammar at all.
The patterns that remain are single-token or anchored-token scans with
disjoint character classes (`[A-Za-z0-9]` vs `[-.]`), and the `Tasks:`-line
scan consumes each character at most once, advancing past every token it has
already inspected. `TestDetectionRuntimeIsBounded` pins this with real
timings on the historical 12-ids-plus-stray bracket and on 100 KB bodies.

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
# docstring's "Where each id family binds" section (LEDGER-GATE-ID-PATTERN-INERT).
_BRANCH_TASK_RE = re.compile(r"^\s*(t\d+\.\d+)-", re.IGNORECASE)

# General id-token grammar (LEDGER-GATE-ID-PATTERN-INERT): one or more
# alphanumeric segments joined by "-" or ".", requiring AT LEAST ONE separator
# (the trailing group is `+`, not `*`). Matches every shape the live ledger's
# first column actually uses — `T0.5`, `10.3`, `GAP-AF-02`, `D2-02-EXEC`,
# `mzo-040`, `flip-path-review-gate` — while a bare single word never
# qualifies (no separator to match), which is what keeps this from matching
# the ledger's own header cell ("Task ID") or ordinary prose words. See the
# module docstring for the full rationale and the 200-PR false-positive
# replay that shaped where it may be applied.
_TASK_ID_PATTERN = r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+"

# PR-body detection binds ONLY on an explicit task-closure marker — never on a bare
# prose mention of a task-ID-shaped token. Two marker forms are recognized:
#
# (a) A "Task:"/"Tasks:" line: the marker must be the first token on its line
#     (leading whitespace aside) — a colon is required. This is deliberately
#     anchored to line-start so a sentence that merely *mentions* a task ID
#     mid-line — e.g. "the porting is task T3.1, not yet done" or "suite de
#     integração planejada — T3.1" (the exact PR #38 phrasing that previously
#     false-positived a T3.1 ledger demand) — never binds. The line's payload
#     is consumed by `_ids_from_marker_payload` below: the LEADING RUN of
#     id-shaped tokens binds, the run stops at the first non-id token, and
#     scanning resumes only at the next "Task:"/"Tasks:" marker on the same
#     line (so "Task: T0.1 T0.2" binds both, and so does a second "Task:"
#     mention later on the same line — see
#     TestExactIdCollisionBoundaries::test_body_mentions_of_both_ids_stay_distinct).
_BODY_TASK_LINE_RE = re.compile(r"^[ \t]*Tasks?\s*:\s*(?P<ids>.+)$", re.IGNORECASE | re.MULTILINE)

# (b) A "[...]" bracket marker anywhere in the body — the PR-title convention
#     (e.g. "[T0.5]", "[T1.8][T1.9]", or a multi-ID bracket like
#     "[T0.1 T0.2 T0.3]" as used in commit 579e8f9) carried into bodies.
#     Bracket payloads are scanned with the LEGACY narrow token regex only
#     (`_LEGACY_TASK_ID_TOKEN_RE`), never with the widened grammar: see the
#     module docstring's bracket paragraph and the 200-PR replay (6 PRs, 3 of
#     them dependabot, went RED on brackets that are not task markers at all).
_BODY_BRACKET_TASK_RE = re.compile(r"\[([^\[\]]*)\]")

# The pre-round-3 (main) token grammar, kept verbatim for bracket payloads so
# bracket binding is byte-for-byte main's behaviour: "[T0.5 extra]" binds
# T0.5, "[t2.6-eb3]" binds T2.6, "[langgraph-checkpoint]" binds nothing.
_LEGACY_TASK_ID_TOKEN_RE = re.compile(r"\b(t\d+\.\d+)\b", re.IGNORECASE)

# One whole token of the widened grammar, anchored — used with `.match()` on
# an already-split token (never scanned across a whole body), which is what
# makes the "leading run" rule cheap and linear.
_TASK_ID_FULL_RE = re.compile(rf"\A(?:{_TASK_ID_PATTERN})\Z", re.IGNORECASE)

# A legacy `t<phase>.<n>-<slug>` token binds its `t<phase>.<n>` PREFIX (what
# `\b(t\d+\.\d+)\b` did on main), so "Tasks: t2.8-lgpd-identity-failclosed"
# keeps binding the short `t2.8` row it always bound. This is a disclosed
# FALSE-PASS hole for the 7 `t<n>.<n>-<suffix>` rows already in the ledger and
# for any new id of that shape — see the module docstring's bullet.
_LEGACY_SLUG_PREFIX_RE = re.compile(r"\A(t\d+\.\d+)-", re.IGNORECASE)

# Tokens inside a marker payload are separated by whitespace, "," or ";" —
# this repo's real `Tasks:` lines write comma-separated lists (PRs #157/#159/
# #165/#166: "Tasks: t2.6-eb3, t2.6-eb4, t3.3 — verified ledger rows …").
_MARKER_TOKEN_RE = re.compile(r"[^\s,;]+")

# Where a stopped leading run may resume: the next "Task:"/"Tasks:" marker on
# the same line (the marker itself, not line-anchored — `_BODY_TASK_LINE_RE`
# already established that this text is a marker line).
_MARKER_RESUME_RE = re.compile(r"Tasks?[ \t]*:[ \t]*", re.IGNORECASE)

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
# that also carries a parenthetical annotation. The inner gap is `[ \t]*`, not
# `\s*`: `\s` matches newlines, so a lone "|" line could otherwise bind an id
# from the FOLLOWING line.
_LEDGER_ROW_RE = re.compile(rf"^[ \t]*\|[ \t]*({_TASK_ID_PATTERN})(?=[ \t]|\|)", re.IGNORECASE | re.MULTILINE)


def _normalize_task_id(raw: str) -> str:
    """Canonicalize a task-ID token to its uppercase form, e.g. 't0.5' -> 'T0.5'."""
    return raw.upper()


def detect_task_ids_from_branch(branch: str) -> set[str]:
    """Detect a task ID from a branch name matching `^t<phase>.<n>-`."""
    match = _BRANCH_TASK_RE.match(branch)
    return {_normalize_task_id(match.group(1))} if match else set()


def _marker_token_id(token: str) -> str | None:
    """Return the task id a single marker-payload token binds, or None.

    A token binds only if the WHOLE token is id-shaped (`_TASK_ID_FULL_RE`) —
    `GAP-AF-02` binds, `(co-requisite` and `https://github.com/x/y` do not.
    A legacy `t<phase>.<n>-<slug>` token binds its `t<phase>.<n>` prefix, the
    one case where the bound id is not the literal token (see the module
    docstring); every other token binds itself, uppercased.
    """
    if not _TASK_ID_FULL_RE.match(token):
        return None
    legacy_slug = _LEGACY_SLUG_PREFIX_RE.match(token)
    if legacy_slug:
        return _normalize_task_id(legacy_slug.group(1))
    return _normalize_task_id(token)


def _ids_from_marker_payload(payload: str) -> set[str]:
    """Bind the LEADING RUN of id tokens after a `Task:`/`Tasks:` marker.

    Tokens (whitespace/`,`/`;`-separated) are consumed left to right until the
    first token that is not id-shaped; scanning then resumes only at the next
    `Task:`/`Tasks:` marker in the remaining text. This is what makes
    "Tasks: FAB-NOTIFIED-TRIO — corrige o gate" bind only the gap id while
    "Task: T0.1 and also Task: T0.10" still binds both (the pinned
    TestExactIdCollisionBoundaries case).

    Linear by construction: every character is inspected at most once — the
    token scan stops at the first non-id token, and the resume search starts
    there and never rewinds (`payload` strictly shrinks each iteration).
    """
    ids: set[str] = set()
    rest = payload
    while rest:
        stop_at: int | None = None
        for token_match in _MARKER_TOKEN_RE.finditer(rest):
            task_id = _marker_token_id(token_match.group(0))
            if task_id is None:
                stop_at = token_match.start()
                break
            ids.add(task_id)
        if stop_at is None:
            return ids
        resume = _MARKER_RESUME_RE.search(rest, stop_at)
        if resume is None:
            return ids
        rest = rest[resume.end() :]
    return ids


def detect_task_ids_from_body(pr_body: str) -> set[str]:
    """Detect task IDs bound by an explicit closure marker in a PR body.

    Binds ONLY on (a) a "Task:"/"Tasks:" line — the leading run of id tokens
    after each marker, under the widened grammar — or (b) a "[...]" bracket
    marker, under the LEGACY narrow `t<phase>.<n>` grammar only. A bare prose
    mention of an id-shaped token never binds on its own, prose trailing a
    `Tasks:` line's ids never binds, and a widened id inside a bracket
    (`[GAP-AF-02]`) never binds — declare it on a `Tasks:` line instead. See
    the module docstring's "Where each id family binds" table.
    """
    ids: set[str] = set()
    for line_match in _BODY_TASK_LINE_RE.finditer(pr_body):
        ids |= _ids_from_marker_payload(line_match.group("ids"))
    for bracket_match in _BODY_BRACKET_TASK_RE.finditer(pr_body):
        tokens = _LEGACY_TASK_ID_TOKEN_RE.finditer(bracket_match.group(1))
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
