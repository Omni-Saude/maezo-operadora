#!/usr/bin/env python3
r"""CI gate: recompute the Test-hash claims that NEW `docs/evidence-ledger.md` rows declare
(LEDGER-HASH-RECOMPUTE-CHECK).

Purpose
-------
The ledger's header defines a test-hash recipe (sha256 of the sorted `PASSED`/`FAILED` result
lines of `pytest <test file> -v --tb=no -p no:cacheprovider`), but a live audit of the table found
several rows that deviated from it: some hashed file BYTES, some hashed several files in one
invocation, some are not reproducible at all. Nothing ever recomputed a hash — the whole table was
trust-on-claim. This gate closes that hole, but ONLY for rows that opt into a machine-readable
form; see "Convention" below for why the whole table cannot be swept at once.

Two design facts constrain this gate (do not try to "fix" around them):

1. A row's hash is a claim about its OWN cited commit. A test file legitimately drifts after a
   LATER, unrelated PR edits it — the row does not silently become wrong, it goes STALE BY DESIGN
   (`PR1-LEDGER-HASH-STALE-BY-DESIGN`). This gate therefore never re-verifies old rows against a
   moving HEAD; see "Scope" below.
2. Rows do not declare, in a structured way, WHICH test file was hashed — the Evidence column is
   free text. Guessing a path out of prose is exactly the kind of fragile heuristic this gate must
   not rely on to stay fail-closed.

Convention (new rows only — see docs/evidence-ledger.md, the paragraph immediately above the
table header)
----------------------------------------------------------------------------------------------
A row that wants to be machine-verified declares the single test file its hash was computed from
INSIDE the Test-hash cell, immediately after the hash, in parentheses:

    sha256:<64 lowercase hex> (tests/unit/x/test_y.py)

A row without that parenthesised path is LEGACY: this gate counts and lists it, but never attempts
to verify it (see design fact 2). This is backwards compatible by construction — every row in the
table today is legacy, and stays legacy (and green) until a future edit opts it in.

Shape alone is NOT enough, though: a row is only DECLARED when it ALSO carries a Date column
(the ledger's second cell) on or after `CONVENTION_START_DATE` (below). Several rows that predate
this gate by weeks already happen to write their Test-hash cell in exactly the declared-path
SHAPE — a coincidence, not an opt-in. The concrete case that proves this is not hypothetical:
`mzo-040` (`docs/evidence-ledger.md`, dated 2026-08-09) already cites
`sha256:070c3e2c... (tests/unit/gateway/test_action_execution_gateway.py)`, and `test_action_...`
has drifted since — running `--all` without the date gate flags it MISMATCH, a false positive
that would mislead a local spot-audit into treating a syntactic coincidence as a real
hash-integrity problem. `CONVENTION_START_DATE` closes that hole structurally: a row dated before
it is always LEGACY regardless of shape.

Recipe (frozen — must byte-for-byte match the declared hash)
--------------------------------------------------------------
    python -m pytest <test file> -v --tb=no -p no:cacheprovider

Run with cwd = repo root. Keep only pytest's per-test verbose RESULT lines — a line starting with
a `tests/...` node id followed by ` PASSED` or ` FAILED` (never a warnings-summary header line,
which happens to often *contain* those words too — the exact false-positive an R2 verifier found
and fixed in the ledger's own history, docs/evidence-ledger.md, row `t1.1 (defect fix)` /
commit `35b8e3e`).

Then STRIP pytest's right-aligned progress column before sorting: `\s+\[\s*\d+%\]\s*$`. That same
ledger row is why this is mandatory, not optional — it documents an R2 verifier reproducing a
DIFFERENT hash than the author for byte-identical test content, root-caused to the progress column
right-padding to the terminal width (`COLUMNS`) at run time, so a naive recipe that keeps the
marker is not reproducible across environments. This gate deliberately strips with `\s+` (one or
more whitespace characters), not a single literal space, for the same reason — a literal-single-
space strip is exactly as environment-sensitive as no strip at all whenever `COLUMNS` pads with
more than one space. (2026-09-03, VER-EVAL-REPLAY reproduced this independently against
`EVAL-REPLAY-EXHAUSTION-SWALLOWED`'s declared hash on a sibling branch; this task's author
independently RECONFIRMED it bit-for-bit — without executing anything on that branch, per this
task's own constraint — by reading the 3 real `async def test_...` names out of that branch's test
file with `git show <sha>:<path>` and feeding the resulting 3 synthetic `<nodeid> PASSED` lines
through this exact `compute_recipe_hash`: the output is byte-identical to the declared
`sha256:6ce0f396...`.)

Sort the stripped lines (a plain Python string sort over ASCII test node-ids is equivalent to
`LC_ALL=C sort`), join with `\n`, add one trailing `\n` (matching what `sort | sha256sum` produces
on stdin), sha256, lowercase hex, `sha256:`-prefixed.

DISCLOSED DIVERGENT PRACTICE (found while validating this recipe, not silently reconciled): the
ledger's own history is NOT uniform on the strip. Two currently-unchanged, currently-reproducible
rows — the `GAP-AF-*` rows over `tests/unit/docs/test_adr_amendments.py`
(`sha256:293ec8b6ce10b0ad063146f652d325900ba9e3e03960639d5cba0f487419a35f`, commit `135eb27`, an
ancestor of this file's HEAD with an EMPTY diff since) and `PERSP-NETBRIDGE`'s citation of
`tests/unit/docs/test_contract_bpmn_dmn_citations.py`
(`sha256:facd44bae253272d7317afb0594d11874641bee7118b09cf860cb9fefadd72d0`) — reproduce ONLY when
the progress marker is KEPT (raw, unstripped); row `t1.10` (line ~91) explicitly documents a THIRD
row that pins "incl [NN%]" (i.e. also unstripped) as its own recipe. This gate implements the strip
as the GO-FORWARD convention per the explicit VER-EVAL-REPLAY instruction and the `t1.1` reconcile
above, not as a claim that it was the ledger's uniform past practice — which is precisely why
verification is opt-in per row (design fact 2) instead of retroactive.

Scope (design fact 1, enforced structurally, not just by convention)
------------------------------------------------------------------------
This gate verifies ONLY rows ADDED in the range `<base>..HEAD` of `docs/evidence-ledger.md` — i.e.
rows a PR is introducing right now, where "current HEAD" and "the commit the row cites" are (or
should be) the same tree. It NEVER re-verifies a row that already existed at `<base>`, because that
row's cited commit is (by definition) further back than `<base>` and its file may have legitimately
drifted since (design fact 1) — re-running the recipe against today's HEAD would produce exactly
the "stale by design" false failure the ledger header already warns about. `--all` (local use only,
never wired into CI) lifts this scope restriction and verifies every declared-path row in the
CURRENT ledger regardless of when it was added — useful for a spot audit, never for merge-gating.

Fail-closed contract
---------------------
- A declared row's recomputed hash mismatches -> FAIL, with a message naming BOTH possible causes
  (the hash was computed wrong; or the file was edited by a later commit on the SAME branch after
  the hash was taken) so a human knows which repair applies.
- A declared row's path fails the safety pattern (`^tests/[A-Za-z0-9_/.-]+\.py$`, no `..` segment)
  or does not exist at HEAD -> FAIL. Never execute an unvalidated path.
- A row's Date cell is missing, unparseable, or before `CONVENTION_START_DATE` -> that row is
  LEGACY (skipped, listed with its reason), never executed, even when its Test-hash cell already
  matches the declared-path shape (see "Convention" above — a coincidence is not an opt-in).
- Zero declared rows to check (every added row is legacy, or nothing was added) -> PASS, but ALWAYS
  print the explicit counts ("0 rows verified, N legacy rows skipped") — never silently green with
  no signal that nothing was actually checked.
- Any git/subprocess error resolving the range or reading the ledger -> FAIL.

Usage
-----
    python3 scripts/ci/check_evidence_ledger_hashes.py               # CI: base = origin/main merge-base
    python3 scripts/ci/check_evidence_ledger_hashes.py --base <sha>  # CI: pull_request.base.sha
    python3 scripts/ci/check_evidence_ledger_hashes.py --all         # local spot-audit, every declared row
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LEDGER_PATH = "docs/evidence-ledger.md"

# The date (ISO `YYYY-MM-DD`, the ledger's own Date-column format — plain string `>=` sorts it
# correctly) on/after which a row's Date cell must fall for that row to be eligible for the
# declared-path convention, EVEN WHEN its Test-hash cell already matches the shape below. Without
# this, row selection is shape-only and a pre-existing row can coincidentally match the shape and
# get executed as if it had opted in (the real, non-hypothetical case: `mzo-040`,
# docs/evidence-ledger.md, dated 2026-08-09 — see module docstring "Convention"). Documented here
# AND in docs/evidence-ledger.md's convention paragraph — keep both in sync if this ever changes.
CONVENTION_START_DATE = "2026-09-04"

# ---------------------------------------------------------------------------
# Row parsing (pure)
# ---------------------------------------------------------------------------

# The first cell of a ledger table row: "| <task id> | ...". Deliberately narrow (no embedded
# pipes expected in a task ID) so it works even though the Evidence prose columns elsewhere in the
# same row sometimes DO contain literal/escaped pipe characters.
_TASK_ID_CELL_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")

# The second cell of a ledger table row: the Date column, immediately after the Task ID cell,
# required to be a plain ISO `YYYY-MM-DD` (the ledger's own convention — every existing row uses
# this form). A row whose Date cell is anything else (malformed, missing) yields no match here,
# and `is_date_qualified(None)` is fail-closed False — never treated as convention-qualified.
_DATE_CELL_RE = re.compile(r"^\|\s*[^|]+?\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|")

# The new convention, searched over the WHOLE row line (not column-isolated — see module
# docstring): a 64-hex sha256, one-or-more spaces, then the declared path in parentheses, and
# nothing else inside those parentheses. A row with extra text inside the parens (several existing
# rows do this, e.g. "(test_x.py, 74 passed)") deliberately does NOT match — it is legacy free
# text, not this convention, and must not be silently swept in.
_DECLARED_HASH_RE = re.compile(r"sha256:([0-9a-fA-F]{64})\s+\((tests/[A-Za-z0-9_./-]+\.py)\)")


@dataclass(frozen=True)
class DeclaredRow:
    """One ledger row that opted into the machine-readable declared-path convention."""

    task_id: str
    test_path: str
    declared_hash: str  # lowercase hex, no "sha256:" prefix


def is_table_row(line: str) -> bool:
    """True if `line` opens a ledger table row (a Task-ID-shaped first cell) — true for BOTH
    legacy and declared-path rows, used so legacy rows are counted, never silently dropped."""
    return _TASK_ID_CELL_RE.match(line.lstrip()) is not None


def parse_row_line(line: str) -> DeclaredRow | None:
    """Parse one ledger row line. Returns None for a non-row line OR a row whose Test-hash cell
    does not carry the declared-path SHAPE — callers distinguish the two with `is_table_row`. Pure
    shape check only: whether a shape-matching row is actually eligible (Date-qualified) is a
    separate concern, decided by `select_rows` via `extract_row_date`/`is_date_qualified` — kept
    apart so this function stays a one-line-in-one-line-out parser with no notion of "today"."""
    stripped = line.lstrip()
    task_match = _TASK_ID_CELL_RE.match(stripped)
    if task_match is None:
        return None
    hash_match = _DECLARED_HASH_RE.search(line)
    if hash_match is None:
        return None
    return DeclaredRow(
        task_id=task_match.group(1).strip(),
        declared_hash=hash_match.group(1).lower(),
        test_path=hash_match.group(2),
    )


def extract_row_date(line: str) -> str | None:
    """Pure: the Date cell (second column) of a table row line, as the raw ISO `YYYY-MM-DD`
    string, or None when the line is not a table row or its Date cell isn't in that exact form
    (fail-closed — an unparseable date is never treated as convention-qualified)."""
    match = _DATE_CELL_RE.match(line.lstrip())
    return match.group(1) if match else None


def is_date_qualified(row_date: str | None) -> bool:
    """Pure: True iff `row_date` is a non-None ISO `YYYY-MM-DD` on/after `CONVENTION_START_DATE`.
    Plain Python string `>=` is correct here because ISO `YYYY-MM-DD` sorts lexicographically the
    same as chronologically. `None` (missing/malformed Date cell) is never qualified."""
    return row_date is not None and row_date >= CONVENTION_START_DATE


@dataclass(frozen=True)
class LegacyRow:
    """A table row this gate will never execute, with a human-readable reason why: either its
    Test-hash cell never carried the declared-path shape, or it did but the row predates
    `CONVENTION_START_DATE` (a shape coincidence, not an opt-in — see module docstring)."""

    task_id: str
    reason: str


@dataclass(frozen=True)
class RowSelection:
    declared: tuple[DeclaredRow, ...]
    legacy: tuple[LegacyRow, ...]


def select_rows(lines: Sequence[str]) -> RowSelection:
    """Pure: partition candidate ledger lines into (declared rows, legacy rows-with-reason). A
    row is declared only when BOTH hold: (1) `parse_row_line` succeeds (Test-hash cell carries the
    `sha256:<hex> (tests/...py)` shape), AND (2) `is_date_qualified(extract_row_date(line))` is
    True (Date cell on/after `CONVENTION_START_DATE`) — shape alone is not an opt-in (the real
    `mzo-040` false-positive this gate must not repeat; see module docstring). Lines that are not
    table rows at all (blank lines, the convention paragraph, diff noise) are silently ignored —
    they are neither declared nor legacy, they are not rows."""
    declared: list[DeclaredRow] = []
    legacy: list[LegacyRow] = []
    for line in lines:
        stripped = line.lstrip()
        task_match = _TASK_ID_CELL_RE.match(stripped)
        if task_match is None:
            continue
        task_id = task_match.group(1).strip()
        row = parse_row_line(line)
        if row is None:
            legacy.append(LegacyRow(task_id, "no declared-path Test-hash form"))
            continue
        row_date = extract_row_date(line)
        if not is_date_qualified(row_date):
            date_desc = row_date if row_date is not None else "missing/unparseable"
            legacy.append(
                LegacyRow(
                    task_id,
                    f"Date cell {date_desc} precedes CONVENTION_START_DATE "
                    f"{CONVENTION_START_DATE} (Test-hash cell matches the declared-path shape by "
                    "coincidence, not by opt-in)",
                )
            )
            continue
        declared.append(row)
    return RowSelection(declared=tuple(declared), legacy=tuple(legacy))


# ---------------------------------------------------------------------------
# Diff-range selection (pure parser + thin git I/O)
# ---------------------------------------------------------------------------


def parse_diff_added_lines(diff_text: str) -> list[str]:
    """Pure: given `git diff <base> HEAD -- <path>` unified-diff text, return the content of
    every ADDED line (a `+` line, excluding the `+++` file-header line), in order. A row that was
    genuinely modified (not just appended) would show as a `-old`/`+new` pair; the `+new` side is
    still returned here — append-only-table discipline is a ledger convention this parser does not
    itself enforce."""
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
    return added


class GitError(RuntimeError):
    """Raised when a required git command fails — always fail-closed, never a silent skip."""


_GIT_TIMEOUT_SECONDS = 30


def run_git(args: Sequence[str], repo_root: Path) -> str:
    """Thin I/O wrapper around one git invocation. `cwd=repo_root`, `stdin=DEVNULL` (a subprocess
    must never be able to block waiting on a stdin nobody wired up)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def resolve_effective_base(repo_root: Path, base_arg: str | None) -> str:
    """The range's lower bound is ALWAYS a merge-base — of `base_arg` (or `origin/main` when not
    given) with HEAD — so `--base <pr-base-sha>` (what CI passes) and the bare default behave
    identically in shape: both collapse to "what this branch actually added", matching GitHub's
    own three-dot PR-diff semantics, regardless of whether the literal ref given is itself an
    ancestor of HEAD."""
    base_ref = base_arg if base_arg is not None else "origin/main"
    return run_git(["merge-base", base_ref, "HEAD"], repo_root).strip()


def get_ledger_diff_added_lines(repo_root: Path, effective_base: str, ledger_rel_path: str) -> list[str]:
    diff_text = run_git(["diff", effective_base, "HEAD", "--", ledger_rel_path], repo_root)
    return parse_diff_added_lines(diff_text)


# ---------------------------------------------------------------------------
# The recipe itself (pure line-processing + thin subprocess I/O)
# ---------------------------------------------------------------------------

# A pytest -v RESULT line: a `<path>::<test>` node id (the `::` is the discriminator — pytest's
# collection/warnings-summary lines never carry one at line start), a space, PASSED or FAILED, and
# (usually) the right-aligned progress column to end of line. NOT anchored to a literal `tests/`
# prefix — the recipe runs against whatever relative path the caller passes (production always
# passes a `tests/`-rooted path, enforced upstream by `is_safe_test_path`; the fixture-file unit
# tests below deliberately do not, to stay decoupled from that policy layer). Requiring `::` right
# after the node id, and PASSED/FAILED immediately after that, is what keeps this from matching
# the exact false-positive ledger row t1.1 documents: a warnings-summary line carries a BARE node
# id (no `::`+PASSED/FAILED token) or is indented — neither shape satisfies this pattern.
_RESULT_LINE_RE = re.compile(r"^(\S+::\S+ (?:PASSED|FAILED)(?:\s.*)?)$", re.MULTILINE)

# pytest's right-aligned progress column, e.g. " [ 4%]" / "  [100%]". `\s+` (one or more), not a
# single literal space — see module docstring for why (COLUMNS-dependent padding, ledger row
# `t1.1 (defect fix)`).
_PROGRESS_MARKER_RE = re.compile(r"\s+\[\s*\d+%\]\s*$")


def strip_progress_marker(line: str) -> str:
    return _PROGRESS_MARKER_RE.sub("", line)


def extract_result_lines(pytest_output: str) -> list[str]:
    """Pure: pytest's captured stdout+stderr -> the stripped PASSED/FAILED result lines, in
    pytest's own (unsorted) emission order. Sorting is a separate step (`compute_recipe_hash`) so
    this function's output is itself useful for the "strip changes the result" proof in tests."""
    return [strip_progress_marker(m.group(1)) for m in _RESULT_LINE_RE.finditer(pytest_output)]


def compute_recipe_hash(result_lines: Sequence[str]) -> str:
    """Pure: sorted, `\\n`-joined (+ trailing `\\n`, matching `sort | sha256sum` on stdin) sha256
    of the given (already-stripped) result lines."""
    sorted_lines = sorted(result_lines)
    payload = ("\n".join(sorted_lines) + "\n") if sorted_lines else ""
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecipeOutcome:
    ok: bool
    computed_hash: str | None
    result_line_count: int
    detail: str


_RECIPE_TIMEOUT_SECONDS = 300


def run_recipe(repo_root: Path, test_path: str, python_exe: str) -> RecipeOutcome:
    """I/O wrapper: runs the frozen recipe for real against `test_path` (relative to `repo_root`)
    and returns the computed hash, or a non-ok outcome with a diagnosable `detail` — never raises."""
    try:
        proc = subprocess.run(
            [python_exe, "-m", "pytest", test_path, "-v", "--tb=no", "-p", "no:cacheprovider"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_RECIPE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return RecipeOutcome(
            False, None, 0, f"TIMEOUT after {_RECIPE_TIMEOUT_SECONDS}s running pytest {test_path}"
        )
    combined = proc.stdout + proc.stderr
    result_lines = extract_result_lines(combined)
    if not result_lines:
        tail = "\n".join(combined.splitlines()[-10:])
        return RecipeOutcome(
            False,
            None,
            0,
            f"pytest produced 0 PASSED/FAILED result lines for {test_path} (exit={proc.returncode}); "
            f"tail:\n{tail}",
        )
    # exit 0 = all passed, exit 1 = some failed — both are legitimate recipe outcomes (a FAILED
    # line is still a valid, hashable result). Anything else (2/3/4/5 = usage/internal/collection
    # error) means the run itself is not trustworthy.
    if proc.returncode not in (0, 1):
        tail = "\n".join(combined.splitlines()[-10:])
        return RecipeOutcome(
            False,
            None,
            len(result_lines),
            f"pytest exited {proc.returncode} (neither 0=all-passed nor 1=some-failed) for "
            f"{test_path}; tail:\n{tail}",
        )
    return RecipeOutcome(True, compute_recipe_hash(result_lines), len(result_lines), "ok")


# ---------------------------------------------------------------------------
# Row-level verification
# ---------------------------------------------------------------------------

# Guard against path injection: only a plain relative path under tests/, alnum + a small
# punctuation set, ending in .py. `..` is separately rejected by segment (the character class
# alone would allow it).
_PATH_SAFETY_RE = re.compile(r"^tests/[A-Za-z0-9_/.-]+\.py$")


def is_safe_test_path(path: str) -> bool:
    if not _PATH_SAFETY_RE.match(path):
        return False
    return ".." not in path.split("/")


@dataclass(frozen=True)
class RowVerification:
    row: DeclaredRow
    ok: bool
    message: str


def verify_row(repo_root: Path, row: DeclaredRow, python_exe: str) -> RowVerification:
    if not is_safe_test_path(row.test_path):
        return RowVerification(
            row,
            False,
            f"{row.task_id}: declared path {row.test_path!r} fails the safety pattern "
            f"^tests/[A-Za-z0-9_/.-]+\\.py$ or contains a '..' segment — refusing to execute it "
            "(path-injection guard).",
        )
    file_path = repo_root / row.test_path
    if not file_path.is_file():
        return RowVerification(
            row, False, f"{row.task_id}: declared test file {row.test_path} does not exist at HEAD."
        )
    outcome = run_recipe(repo_root, row.test_path, python_exe)
    if not outcome.ok:
        return RowVerification(
            row, False, f"{row.task_id}: recipe execution failed for {row.test_path}: {outcome.detail}"
        )
    declared_full = f"sha256:{row.declared_hash}"
    assert outcome.computed_hash is not None  # ok=True guarantees this
    if outcome.computed_hash.lower() != declared_full.lower():
        return RowVerification(
            row,
            False,
            f"{row.task_id}: hash mismatch for {row.test_path} — declared {declared_full}, "
            f"recomputed {outcome.computed_hash} ({outcome.result_line_count} result lines at "
            "HEAD). Two possible causes: (1) the declared hash was computed wrong — fix it in "
            "this same still-open PR before merge; (2) a LATER commit on this same branch edited "
            f"{row.test_path} after the hash was taken — append a disclosure row noting the "
            "recompute (PR1-LEDGER-HASH-STALE-BY-DESIGN), never silently edit an already-merged "
            "row.",
        )
    return RowVerification(
        row,
        True,
        f"{row.task_id}: verified {declared_full} ({row.test_path}, {outcome.result_line_count} "
        "result lines).",
    )


# ---------------------------------------------------------------------------
# CLI wrapper (I/O boundary)
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_evidence_ledger_hashes",
        description=(
            "Fail-closed CI gate: recompute the declared-path Test-hash claims of NEW rows added "
            "to docs/evidence-ledger.md in <base>..HEAD."
        ),
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Base ref/sha for range selection (merge-based with HEAD). Default: origin/main. "
        "CI passes github.event.pull_request.base.sha.",
    )
    parser.add_argument(
        "--ledger-path",
        default=DEFAULT_LEDGER_PATH,
        help=f"Path to the evidence ledger, relative to repo root (default: {DEFAULT_LEDGER_PATH}).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Verify every declared-path row in the CURRENT ledger, ignoring the base..HEAD range "
        "(local spot-audit only — never wire this into CI, see module docstring 'Scope').",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter used for the pytest subprocess (default: sys.executable).",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail) — never anything else, never raises.

    `repo_root` is a testability seam (defaults to this file's real repo root); production
    callers never pass it.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    root = repo_root if repo_root is not None else REPO_ROOT
    prefix = "[check-evidence-ledger-hashes]"

    ledger_path = root / args.ledger_path
    try:
        ledger_text = ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{prefix} ERROR: could not read ledger {args.ledger_path}: {exc}", file=sys.stderr)
        return 1

    if args.all:
        selection = select_rows(ledger_text.splitlines())
        scope_desc = f"every declared row in {args.ledger_path} (--all)"
    else:
        try:
            effective_base = resolve_effective_base(root, args.base)
            added_lines = get_ledger_diff_added_lines(root, effective_base, args.ledger_path)
        except GitError as exc:
            print(f"{prefix} ERROR: {exc}", file=sys.stderr)
            return 1
        selection = select_rows(added_lines)
        scope_desc = f"rows added in {effective_base}..HEAD of {args.ledger_path}"

    # Legacy rows are listed with their reason (shape or date) EVERY time, not just when nothing
    # is declared — a row skipped for the mzo-040-style date-coincidence reason must be visible
    # even on a run where other rows ARE verified.
    for legacy_row in selection.legacy:
        print(f"{prefix} SKIP (legacy): {legacy_row.task_id} — {legacy_row.reason}")

    if not selection.declared:
        legacy_note = (
            f" (legacy task IDs: {', '.join(r.task_id for r in selection.legacy)})"
            if selection.legacy
            else ""
        )
        print(
            f"{prefix} PASS: 0 rows verified, {len(selection.legacy)} legacy rows skipped "
            f"— {scope_desc}{legacy_note}."
        )
        return 0

    results = [verify_row(root, row, args.python) for row in selection.declared]
    for result in results:
        status = "OK" if result.ok else "MISMATCH"
        print(f"{prefix} {status}: {result.message}")

    failures = [r for r in results if not r.ok]
    verified_ok = len(results) - len(failures)
    print(
        f"{prefix} SUMMARY: {verified_ok}/{len(results)} declared rows verified, "
        f"{len(selection.legacy)} legacy rows skipped — {scope_desc}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
