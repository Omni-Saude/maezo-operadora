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
This gate verifies rows ADDED in the range `<base>..HEAD` of `docs/evidence-ledger.md`. A plain row
is checked at candidate HEAD. A v1 append-only successor additionally pins an older exact row,
source commit, test blob, and lock blob: the old claim runs from that ancestral Git archive and the
successor runs at HEAD. Thus a pre-base target is re-verified only when a new successor explicitly
and cryptographically links it; it is never run against moving HEAD. `--all` lifts range selection
but preserves the same historical/current routing.

Current-HEAD recipes retain the gate's established pytest invocation and caller environment,
including explicitly authorized live-stack coordinates and installed evidence plugins. Historical
execution alone receives a minimal environment with no inherited credentials, `PYTEST_ADDOPTS`,
or plugin autoload. It fences every executed Python source file (tests/helpers as well as `maezo`)
inside its owned archive or the locked interpreter and refuses a source lock different from the
candidate lock.

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
- A declared row whose path is under `tests/integration/**` -> FAIL (refused, never silently
  skipped or executed) unless the caller asserts `--allow-live` or `LEDGER_HASH_ALLOW_LIVE`
  (HARNESS-LEDGER-HASH-AMBIENT-STACK, deviation D-24): this gate has no mutex over the
  engine/PG/Kafka stack an integration test needs, and no way to tell a stack it brought up itself
  from another agent's ambient one on the same standard ports.
- A declared row's recomputed hash mismatches under the FIXED node-id recipe -> before failing,
  IF the row's Date cell is strictly before `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE`, also try the
  LEGACY (pre-fix) node-id recipe against the SAME captured pytest run — a row declared before
  LEDGER-HASH-PARAM-IDS-WITH-SPACES was fixed may have been hashed under the old, buggy
  extraction. A match there is reported OK but explicitly labelled `recipe_version=legacy`, never
  silently indistinguishable from a normal pass. Neither recipe matching is a genuine FAIL. A row
  dated on/after the cutoff gets no such fallback — it must reproduce under the fixed recipe alone.

Usage
-----
    python3 scripts/ci/check_evidence_ledger_hashes.py               # CI: base = origin/main merge-base
    python3 scripts/ci/check_evidence_ledger_hashes.py --base <sha>  # CI: pull_request.base.sha
    python3 scripts/ci/check_evidence_ledger_hashes.py --all         # local spot-audit, every declared row
    python3 scripts/ci/check_evidence_ledger_hashes.py --allow-live  # allow tests/integration/** rows
                                                                      # (assert you hold the engine mutex)
    LEDGER_HASH_ALLOW_LIVE=1 make check-ledger-hashes                # same assertion, zero Makefile diff
"""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import ParseResult, parse_qsl, urlparse

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

# The date (ISO `YYYY-MM-DD`) on/after which a declared row's hash must reproduce under the FIXED
# node-id recipe (`_RESULT_LINE_RE`) ALONE. A row dated STRICTLY BEFORE this constant is also
# allowed to reproduce under the LEGACY (pre-fix) recipe (`_RESULT_LINE_RE_LEGACY`) — see
# `verify_row`'s recipe-version dual-check.
#
# Why this exists (LEDGER-HASH-PARAM-IDS-WITH-SPACES, fixed 2026-09-05): the ORIGINAL node-id
# regex required the WHOLE node id (both sides of `::`) to be whitespace-free, so a parametrized
# id containing a literal space (e.g. a prose-built case id like `[RATIFICADO pelo DPO]`) made the
# whole PASSED/FAILED line fail to match at all — not hashed, not reported as unparsed, simply
# absent from the sorted set the hash was computed over. Fixing that regex is CORRECT going
# forward, but it is not cost-free: recomputing the recipe against a test file that has ANY
# spaced parametrize id now includes MORE result lines than the row's author saw when they
# declared the hash, so the recomputed hash legitimately CHANGES for every such row — confirmed by
# running this gate's own `--all` mode before and after the fix, over the CURRENT ledger: 11
# declared rows (9 distinct test files: `test_fraude.py`, `test_nip.py`,
# `test_validation_phi_completeness.py`, `test_validation_perspective.py`,
# `test_check_flip_path_review.py`, `test_check_production_approval.py`, `test_programa.py`,
# `test_escalation_notify_team.py`, `test_inadimplencia.py`) flip from OK to MISMATCH. None of
# them are a real integrity problem — the code being hashed did not change, only the tool's own
# extraction algorithm did — but a ledger row is NEVER retroactively edited (append-only,
# `PR1-LEDGER-HASH-STALE-BY-DESIGN`), so those already-declared hashes must stay verifiable under
# the recipe that was actually in effect when they were computed. Full before/after listing (task
# ID, file, old hash -> new hash): the disclosure row `LEDGER-HASH-RECIPE-CHANGE-2026-09-05` in
# `docs/evidence-ledger.md`.
#
# Set to the day AFTER the fix lands (not the fix's own date, 2026-09-05) so every row already in
# the ledger as of the fix's commit — including same-day rows created earlier on 2026-09-05, before
# this fix, under the old script — is grandfathered: date-only granularity cannot distinguish
# "before this commit, same day" from "after this commit, same day", so the cutoff is deliberately
# coarse in the SAFE direction (never rejects a genuinely pre-fix row) while still forcing every
# row dated 2026-09-06 onward onto the fixed recipe alone — a future author cannot re-declare a
# spaced-id row under the legacy recipe to dodge the fix.
LEGACY_NODE_ID_RECIPE_CUTOFF_DATE = "2026-09-06"

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

# Append-only supersession convention.  A successor row remains an ordinary declared row (and is
# therefore re-run at HEAD), while this marker binds the older row to the exact historical Git
# tree where its own hash was true.  Fixed field order and a closed grammar are deliberate: an
# unknown/missing field must fail instead of being silently ignored.
_SUPERSESSION_HINT = "[ledger-supersedes:"
_SUPERSESSION_RE = re.compile(
    r"\[ledger-supersedes:v1;target=([^;\]\n]+);"
    r"source_commit=([0-9a-f]{40});"
    r"source_row_sha256=([0-9a-f]{64});"
    r"source_test_sha256=([0-9a-f]{64});"
    r"source_lock_sha256=([0-9a-f]{64})\]"
)


@dataclass(frozen=True)
class SupersessionClaim:
    target_task_id: str
    source_commit: str
    source_row_sha256: str
    source_test_sha256: str
    source_lock_sha256: str


class SupersessionError(RuntimeError):
    """A malformed, ambiguous, or unverifiable append-only supersession claim."""


def parse_supersession_claim(line: str) -> SupersessionClaim | None:
    """Parse the closed v1 marker, rejecting partial/duplicate/unknown metadata fail-closed."""
    if _SUPERSESSION_HINT not in line:
        return None
    matches = list(_SUPERSESSION_RE.finditer(line))
    if len(matches) != 1 or line.count(_SUPERSESSION_HINT) != 1:
        raise SupersessionError(
            "malformed ledger-supersedes marker (expected exactly one closed v1 marker with "
            "target, source_commit, source_row_sha256, source_test_sha256, source_lock_sha256)"
        )
    match = matches[0]
    target = match.group(1).strip()
    if not target or target != match.group(1):
        raise SupersessionError("ledger-supersedes target must be non-empty and have no edge whitespace")
    return SupersessionClaim(target, *match.groups()[1:])


@dataclass(frozen=True)
class DeclaredRow:
    """One ledger row that opted into the machine-readable declared-path convention."""

    task_id: str
    test_path: str
    declared_hash: str  # lowercase hex, no "sha256:" prefix
    # The row's Date cell (ISO YYYY-MM-DD), or None when unavailable (e.g. a row built by hand in
    # a test without one). Used ONLY by `verify_row` to decide recipe-version eligibility (see
    # `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE`) — never to decide declared/legacy status, which stays
    # `select_rows`'s job via `is_date_qualified`. Defaulted so existing call sites that construct
    # a `DeclaredRow` without a date (every test in this file predating the recipe-version dual
    # check) keep compiling unchanged.
    row_date: str | None = None
    # Exact row bytes are needed only for append-only provenance. compare=False preserves the
    # public value semantics used by the pre-existing parser tests and hand-built rows.
    raw_line: str = field(default="", compare=False, repr=False)
    supersession: SupersessionClaim | None = field(default=None, compare=False)


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
    supersession = parse_supersession_claim(line)
    return DeclaredRow(
        task_id=task_match.group(1).strip(),
        declared_hash=hash_match.group(1).lower(),
        test_path=hash_match.group(2),
        # Still a pure, per-line, no-notion-of-"today" extraction (see this function's own
        # docstring) — `extract_row_date` never consults CONVENTION_START_DATE or any other
        # "today" state, it just reads the second cell.
        row_date=extract_row_date(line),
        raw_line=line,
        supersession=supersession,
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


def run_git_bytes(args: Sequence[str], repo_root: Path) -> bytes:
    """Binary sibling of ``run_git`` for blobs/archive payloads whose bytes are evidence."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)} failed (exit {proc.returncode}): {detail}")
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
# Append-only supersession provenance
# ---------------------------------------------------------------------------


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def row_sha256(row: DeclaredRow) -> str:
    """Digest the exact UTF-8 Markdown row, excluding its line terminator."""
    return _sha256_bytes(row.raw_line.encode("utf-8"))


@dataclass(frozen=True)
class SupersessionEdge:
    successor: DeclaredRow
    target: DeclaredRow
    claim: SupersessionClaim


@dataclass(frozen=True)
class SupersessionPlan:
    by_target_row_sha256: Mapping[str, SupersessionEdge]
    by_successor_row_sha256: Mapping[str, SupersessionEdge]


def assert_acyclic_supersession_links(links: Mapping[str, str]) -> None:
    """Reject a successor->target digest graph containing a cycle (pure mutation fence seam)."""
    for start in links:
        seen: set[str] = set()
        cursor = start
        while cursor in links:
            if cursor in seen:
                raise SupersessionError("cycle in ledger supersession graph")
            seen.add(cursor)
            cursor = links[cursor]


def _validate_ledger_rel_path(ledger_rel_path: str) -> None:
    path = Path(ledger_rel_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SupersessionError(f"unsafe ledger path {ledger_rel_path!r}")


def _source_blob(repo_root: Path, commit: str, path: str) -> bytes:
    try:
        return run_git_bytes(["show", f"{commit}:{path}"], repo_root)
    except GitError as exc:
        raise SupersessionError(str(exc)) from exc


def _assert_ancestor(repo_root: Path, ancestor: str) -> None:
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SupersessionError(f"git ancestor check timed out for {ancestor}") from exc
    if proc.returncode == 1:
        raise SupersessionError(f"source_commit {ancestor} is not an ancestor of HEAD")
    if proc.returncode != 0:
        raise SupersessionError(
            f"git ancestor check failed for {ancestor} (exit {proc.returncode}): {proc.stderr.strip()}"
        )


def build_supersession_plan(
    repo_root: Path, ledger_text: str, ledger_rel_path: str = DEFAULT_LEDGER_PATH
) -> SupersessionPlan:
    """Validate every v1 edge against Git and return exact-row keyed execution routing.

    Existing unrelated duplicate task IDs are tolerated.  A linked target resolves by BOTH its
    exact task ID and its exact row digest, so ambiguity cannot be hidden behind a historical ID
    collision.
    """
    _validate_ledger_rel_path(ledger_rel_path)
    ledger_lines = ledger_text.splitlines()
    try:
        selection = select_rows(ledger_lines)
    except SupersessionError:
        raise
    rows = selection.declared
    by_target: dict[str, SupersessionEdge] = {}
    by_successor: dict[str, SupersessionEdge] = {}

    # A malformed marker on a non-declared row must not fall through the legacy path.
    for line in ledger_lines:
        if is_table_row(line) and _SUPERSESSION_HINT in line:
            parse_supersession_claim(line)
            successor = parse_row_line(line)
            if successor is None:
                raise SupersessionError(
                    "ledger-supersedes marker requires a declared successor with the exact "
                    "sha256:<digest> (tests/...py) Test-hash form"
                )
            if not is_date_qualified(successor.row_date):
                date_desc = successor.row_date if successor.row_date is not None else "missing/unparseable"
                raise SupersessionError(
                    "ledger-supersedes marker requires a convention-qualified successor Date; "
                    f"got {date_desc}, expected {CONVENTION_START_DATE} or later"
                )

    claim_rows = [row for row in rows if row.supersession is not None]
    if not claim_rows:
        return SupersessionPlan({}, {})

    for successor in claim_rows:
        claim = successor.supersession
        if claim is None:
            continue
        successor_digest = row_sha256(successor)
        if successor_digest in by_successor:
            raise SupersessionError(f"duplicate successor row for {successor.task_id} ({successor_digest})")
        targets = [
            row
            for row in rows
            if row.task_id == claim.target_task_id and row_sha256(row) == claim.source_row_sha256
        ]
        if len(targets) != 1:
            raise SupersessionError(
                f"{successor.task_id}: target {claim.target_task_id!r} with row digest "
                f"{claim.source_row_sha256} resolves to {len(targets)} rows (expected exactly 1)"
            )
        target = targets[0]
        target_digest = row_sha256(target)
        if target_digest == successor_digest:
            raise SupersessionError(f"{successor.task_id}: supersession cannot target itself")
        if target_digest in by_target:
            prior = by_target[target_digest]
            raise SupersessionError(
                f"target {target.task_id} already has successor {prior.successor.task_id}; "
                f"duplicate successor {successor.task_id} is forbidden"
            )

        try:
            resolved = run_git(
                ["rev-parse", "--verify", f"{claim.source_commit}^{{commit}}"], repo_root
            ).strip()
        except GitError as exc:
            raise SupersessionError(str(exc)) from exc
        if resolved != claim.source_commit:
            raise SupersessionError(
                f"{successor.task_id}: source_commit must be the exact full commit id; "
                f"declared {claim.source_commit}, resolved {resolved}"
            )
        _assert_ancestor(repo_root, claim.source_commit)

        source_ledger_bytes = _source_blob(repo_root, claim.source_commit, ledger_rel_path)
        try:
            source_ledger = source_ledger_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SupersessionError(
                f"{successor.task_id}: historical ledger is not UTF-8 at {claim.source_commit}"
            ) from exc
        source_matches = [
            line
            for line in source_ledger.splitlines()
            if line == target.raw_line and _sha256_bytes(line.encode("utf-8")) == claim.source_row_sha256
        ]
        if len(source_matches) != 1:
            raise SupersessionError(
                f"{successor.task_id}: source_commit does not contain exactly one byte-identical "
                f"target row {claim.target_task_id}"
            )

        if not is_safe_test_path(target.test_path):
            raise SupersessionError(
                f"{successor.task_id}: historical target path {target.test_path!r} is unsafe"
            )
        source_test = _source_blob(repo_root, claim.source_commit, target.test_path)
        if _sha256_bytes(source_test) != claim.source_test_sha256:
            raise SupersessionError(
                f"{successor.task_id}: historical test blob digest mismatch for {target.test_path}"
            )
        source_lock = _source_blob(repo_root, claim.source_commit, "uv.lock")
        if _sha256_bytes(source_lock) != claim.source_lock_sha256:
            raise SupersessionError(f"{successor.task_id}: historical uv.lock digest mismatch")
        edge = SupersessionEdge(successor, target, claim)
        by_target[target_digest] = edge
        by_successor[successor_digest] = edge

    # Edges point successor -> target.  Chaining is supported; cycles are not.
    assert_acyclic_supersession_links(
        {successor_digest: row_sha256(edge.target) for successor_digest, edge in by_successor.items()}
    )

    return SupersessionPlan(by_target, by_successor)


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
#
# The node id's PATH component (before the FIRST `::`) is required whitespace-free (`\S+`) — a
# test file path never contains a space — but everything AFTER that first `::` (the rest of the
# node id: class name(s), test name, and a parametrize id) is matched with `.+`, deliberately NOT
# `\S+`. A parametrize id built from prose legitimately contains a literal space (e.g.
# `test_x[RATIFICADO pelo DPO]`) and pytest -v renders it as part of the SAME PASSED/FAILED
# line — the previous `\S+` requirement on that half meant the whole line silently failed to
# match at all (LEDGER-HASH-PARAM-IDS-WITH-SPACES): not hashed, not reported as unparsed, simply
# absent from the sorted set the hash is computed over. Greedy `.+` (not lazy) is required so that,
# when a node id itself contains more than one `::` (a class-scoped test:
# `path::TestClass::test_x[a b]`), the match anchors on the LAST ` PASSED`/` FAILED` token in the
# line rather than an accidental earlier substring. The leading `\S+` (not `.*`) before the first
# `::` is equally deliberate: it keeps a "short test summary info" line — e.g.
# `FAILED tests/x.py::test_y - AssertionError` — excluded, because that line's first whitespace-free
# token is `FAILED` itself with no `::` immediately following it, so `\S+::` never matches at
# position 0; a looser `.*?::` would let such a line's leading token float across the space and
# wrongly re-admit it whenever the failure reason happens to end in the literal word "PASSED" or
# "FAILED".
_RESULT_LINE_RE = re.compile(r"^(\S+::.+ (?:PASSED|FAILED))(?:\s.*)?$", re.MULTILINE)

# The LEGACY (pre-fix) node-id extraction, kept verbatim — required the ENTIRE node id (both
# sides of `::`) to be whitespace-free, so a parametrized id containing a literal space made the
# whole line fail to match (LEDGER-HASH-PARAM-IDS-WITH-SPACES). Never used to extract NEW rows —
# only `verify_row`'s recipe-version dual-check reaches for it, and only for a row dated strictly
# before `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE`, to keep an already-declared pre-fix hash verifiable
# without ever retroactively editing the (append-only) ledger row.
_RESULT_LINE_RE_LEGACY = re.compile(r"^(\S+::\S+ (?:PASSED|FAILED)(?:\s.*)?)$", re.MULTILINE)

# pytest's right-aligned progress column, e.g. " [ 4%]" / "  [100%]". `\s+` (one or more), not a
# single literal space — see module docstring for why (COLUMNS-dependent padding, ledger row
# `t1.1 (defect fix)`).
_PROGRESS_MARKER_RE = re.compile(r"\s+\[\s*\d+%\]\s*$")


def strip_progress_marker(line: str) -> str:
    return _PROGRESS_MARKER_RE.sub("", line)


def extract_result_lines(
    pytest_output: str, *, node_id_regex: re.Pattern[str] = _RESULT_LINE_RE
) -> list[str]:
    """Pure: pytest's captured stdout+stderr -> the stripped PASSED/FAILED result lines, in
    pytest's own (unsorted) emission order. Sorting is a separate step (`compute_recipe_hash`) so
    this function's output is itself useful for the "strip changes the result" proof in tests.
    `node_id_regex` defaults to the current (fixed) recipe; `verify_row`'s recipe-version dual-
    check passes `_RESULT_LINE_RE_LEGACY` explicitly to reproduce a pre-fix declared hash."""
    return [strip_progress_marker(m.group(1)) for m in node_id_regex.finditer(pytest_output)]


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

_SAFE_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TZ", "SYSTEMROOT")
_LIVE_COORDINATE_ENV_KEYS = (
    "CIBSEVEN_BASE_URL",
    "ENGINE_REST_URL",
    "KAFKA_BOOTSTRAP_SERVERS",
    "MAEZO_PG_HOST_PORT",
    "MAEZO_TEST_DATABASE_URL",
)


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _has_valid_url_port(parsed: ParseResult) -> bool:
    """Reject malformed/out-of-range ports without letting ``ParseResult.port`` raise."""
    try:
        port = parsed.port
    except ValueError:
        return False
    return port is None or 0 < port <= 65535


def _is_safe_loopback_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and _is_loopback_host(parsed.hostname)
        and _has_valid_url_port(parsed)
        and not parsed.fragment
    )


_LIBPQ_DESTINATION_QUERY_KEYS = {"host", "hostaddr", "port", "service", "servicefile"}


def _is_safe_loopback_postgres_url(value: str) -> bool:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.netloc
        or not _is_loopback_host(parsed.hostname)
        or not _has_valid_url_port(parsed)
        or parsed.params
        or parsed.fragment
    ):
        return False
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    # libpq gives these query options precedence over the URI authority.  Reject every
    # destination selector, including percent-encoded spellings and Unix-socket hosts, so the
    # textual loopback authority is the destination the archived consumer actually receives.
    return not any(key.lower() in _LIBPQ_DESTINATION_QUERY_KEYS for key, _value in query)


def _is_safe_kafka_endpoint(value: str) -> bool:
    parsed = urlparse("//" + value)
    return (
        bool(parsed.netloc)
        and _is_loopback_host(parsed.hostname)
        and _has_valid_url_port(parsed)
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _is_safe_historical_live_coordinate(key: str, value: str) -> bool:
    """Allow archived code to reach only an explicitly selected local/isolated lane."""
    if key == "MAEZO_PG_HOST_PORT":
        return value.isascii() and value.isdigit() and 0 < int(value) <= 65535
    if key == "KAFKA_BOOTSTRAP_SERVERS":
        endpoints = [item.strip() for item in value.split(",")]
        return bool(endpoints) and all(
            endpoint and _is_safe_kafka_endpoint(endpoint) for endpoint in endpoints
        )
    if key in {"CIBSEVEN_BASE_URL", "ENGINE_REST_URL"}:
        return _is_safe_loopback_http_url(value)
    if key == "MAEZO_TEST_DATABASE_URL":
        return _is_safe_loopback_postgres_url(value)
    return False


def recipe_environment(
    *, python_path: str | None = None, live_coordinates: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Minimal historical environment: no credentials or ambient pytest selection/plugins.

    `live_coordinates` is populated only after the caller explicitly asserted `--allow-live`.
    Its closed keys preserve the integration lane's chosen endpoints without passing unrelated
    process credentials or selectors through to archived code.
    """
    env = {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    if python_path is not None:
        env["PYTHONPATH"] = python_path
    if live_coordinates is not None:
        for key in _LIVE_COORDINATE_ENV_KEYS:
            if key not in live_coordinates:
                continue
            value = live_coordinates[key]
            if not _is_safe_historical_live_coordinate(key, value):
                raise ValueError(
                    f"refusing non-loopback historical live coordinate {key}; "
                    "archived recipes may target only an explicitly isolated local lane"
                )
            env[key] = value
    return env


@dataclass(frozen=True)
class PytestCapture:
    """I/O outcome of running the frozen recipe's pytest invocation exactly ONCE — deliberately
    decoupled from which node-id regex later interprets its output. `verify_row`'s recipe-version
    dual-check (LEDGER-HASH-PARAM-IDS-WITH-SPACES) needs to try TWO regexes against the SAME real
    pytest run; without this split it would have to re-run pytest a second time to do that —
    doubling every row's cost and, worse, risking a flaky/non-deterministic test producing a
    genuinely different second run instead of re-reading the same captured bytes."""

    ok: bool
    combined_output: str | None
    returncode: int | None
    detail: str


def capture_pytest_recipe(repo_root: Path, test_path: str, python_exe: str) -> PytestCapture:
    """I/O wrapper: runs the frozen recipe for real against `test_path` (relative to `repo_root`)
    exactly once and returns the raw captured stdout+stderr, or a non-ok capture with a
    diagnosable `detail` — never raises. Extraction/hashing is `recipe_outcome_from_capture`'s job,
    kept separate so the SAME capture can be interpreted under more than one node-id regex."""
    try:
        proc = subprocess.run(
            [
                python_exe,
                "-m",
                "pytest",
                test_path,
                "-v",
                "--tb=no",
                "-p",
                "no:cacheprovider",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_RECIPE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return PytestCapture(
            False, None, None, f"TIMEOUT after {_RECIPE_TIMEOUT_SECONDS}s running pytest {test_path}"
        )
    combined = proc.stdout + proc.stderr
    # exit 0 = all passed, exit 1 = some failed — both are legitimate recipe outcomes (a FAILED
    # line is still a valid, hashable result). Anything else (2/3/4/5 = usage/internal/collection
    # error) means the run itself is not trustworthy.
    if proc.returncode not in (0, 1):
        tail = "\n".join(combined.splitlines()[-10:])
        return PytestCapture(
            False,
            combined,
            proc.returncode,
            f"pytest exited {proc.returncode} (neither 0=all-passed nor 1=some-failed) for "
            f"{test_path}; tail:\n{tail}",
        )
    return PytestCapture(True, combined, proc.returncode, "ok")


def recipe_outcome_from_capture(
    capture: PytestCapture, test_path: str, *, node_id_regex: re.Pattern[str] = _RESULT_LINE_RE
) -> RecipeOutcome:
    """Pure (given `capture`): extracts+hashes result lines from an already-captured pytest run
    using `node_id_regex`. A capture that failed at the I/O layer (timeout, bad exit code)
    propagates its `detail` unchanged, never re-interpreted as "0 result lines"."""
    if not capture.ok:
        return RecipeOutcome(False, None, 0, capture.detail)
    assert capture.combined_output is not None  # ok=True guarantees this
    result_lines = extract_result_lines(capture.combined_output, node_id_regex=node_id_regex)
    if not result_lines:
        tail = "\n".join(capture.combined_output.splitlines()[-10:])
        return RecipeOutcome(
            False,
            None,
            0,
            f"pytest produced 0 PASSED/FAILED result lines for {test_path} (exit={capture.returncode}); "
            f"tail:\n{tail}",
        )
    return RecipeOutcome(True, compute_recipe_hash(result_lines), len(result_lines), "ok")


def run_recipe(
    repo_root: Path, test_path: str, python_exe: str, *, node_id_regex: re.Pattern[str] = _RESULT_LINE_RE
) -> RecipeOutcome:
    """I/O wrapper: runs the frozen recipe for real against `test_path` (relative to `repo_root`)
    and returns the computed hash, or a non-ok outcome with a diagnosable `detail` — never raises.
    Back-compat single-shot convenience: `capture_pytest_recipe` + `recipe_outcome_from_capture`
    in one call, for callers (and every pre-existing test in this suite) that don't need the
    recipe-version dual-check and are fine with the default (fixed) node-id regex."""
    capture = capture_pytest_recipe(repo_root, test_path, python_exe)
    return recipe_outcome_from_capture(capture, test_path, node_id_regex=node_id_regex)


_SOURCE_GUARD_PLUGIN = r'''"""Ephemeral guard for historical ledger recipe execution."""
from __future__ import annotations

import importlib
import json
import os
import sys
import sysconfig
from pathlib import Path

_ROOT = Path(os.environ["LEDGER_ARCHIVE_ROOT"]).resolve()
_GUARD_ROOT = Path(os.environ["LEDGER_SOURCE_GUARD_ROOT"]).resolve()
_MARKER = Path(os.environ["LEDGER_SOURCE_GUARD_MARKER"])
_TRUSTED = {
    Path(value).resolve()
    for value in sysconfig.get_paths().values()
    if value
}
_SEEN = set()


def _is_relative_to_any(path, roots):
    return any(path.is_relative_to(root) for root in roots)


def _record(raw):
    if not raw or raw.startswith("<"):
        return
    path = Path(raw).resolve()
    _SEEN.add(path)


def _audit(event, args):
    if event == "exec" and args:
        _record(getattr(args[0], "co_filename", None))


sys.addaudithook(_audit)


def _module_paths():
    paths = set(_SEEN)
    for _name, module in sorted(sys.modules.items()):
        raw = getattr(module, "__file__", None)
        if raw:
            _record(raw)
    paths.update(_SEEN)
    return sorted(paths)


def _escaped(paths):
    allowed = {*_TRUSTED, _ROOT, _GUARD_ROOT}
    return [path for path in paths if not _is_relative_to_any(path, allowed)]


def _archived(paths):
    return [path for path in paths if path.is_relative_to(_ROOT)]


def _write_marker(paths):
    _MARKER.write_text(
        json.dumps(
            {
                "archived": [str(path) for path in _archived(paths)],
                "escaped": [str(path) for path in _escaped(paths)],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def pytest_sessionstart(session):
    importlib.import_module("maezo")
    paths = _module_paths()
    escaped = _escaped(paths)
    if escaped:
        raise RuntimeError("historical source import escaped archive: " + ", ".join(map(str, escaped)))


def pytest_sessionfinish(session, exitstatus):
    paths = _module_paths()
    escaped = _escaped(paths)
    _write_marker(paths)
    if escaped:
        raise RuntimeError("historical source import escaped archive: " + ", ".join(map(str, escaped)))
'''


def _capture_historical_recipe(
    repo_root: Path, edge: SupersessionEdge, python_exe: str, *, allow_live: bool
) -> PytestCapture:
    """Execute pytest from an archived source commit, with source-import provenance fenced."""
    try:
        archive_bytes = run_git_bytes(["archive", "--format=tar", edge.claim.source_commit], repo_root)
    except GitError as exc:
        return PytestCapture(False, None, None, str(exc))
    with tempfile.TemporaryDirectory(prefix="maezo-ledger-history-") as tmp:
        temp_root = Path(tmp)
        archive_root = temp_root / "tree"
        guard_root = temp_root / "guard"
        archive_root.mkdir()
        guard_root.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
                archive.extractall(archive_root, filter="data")
        except (tarfile.TarError, OSError) as exc:
            return PytestCapture(False, None, None, f"could not extract historical Git archive: {exc}")

        extracted_test = archive_root / edge.target.test_path
        extracted_lock = archive_root / "uv.lock"
        if (
            not extracted_test.is_file()
            or _sha256_bytes(extracted_test.read_bytes()) != edge.claim.source_test_sha256
        ):
            return PytestCapture(False, None, None, "extracted historical test does not match its bound blob")
        if (
            not extracted_lock.is_file()
            or _sha256_bytes(extracted_lock.read_bytes()) != edge.claim.source_lock_sha256
        ):
            return PytestCapture(
                False, None, None, "extracted historical uv.lock does not match its bound blob"
            )

        (guard_root / "ledger_source_guard.py").write_text(_SOURCE_GUARD_PLUGIN, encoding="utf-8")
        marker = temp_root / "source-paths.txt"
        try:
            env = recipe_environment(
                python_path=os.pathsep.join((str(guard_root), str(archive_root / "src"), str(archive_root))),
                live_coordinates=os.environ if allow_live else None,
            )
        except ValueError as exc:
            return PytestCapture(False, None, None, str(exc))
        env["LEDGER_ARCHIVE_ROOT"] = str(archive_root)
        env["LEDGER_SOURCE_GUARD_ROOT"] = str(guard_root)
        env["LEDGER_SOURCE_GUARD_MARKER"] = str(marker)
        try:
            proc = subprocess.run(
                [
                    python_exe,
                    "-P",
                    "-m",
                    "pytest",
                    edge.target.test_path,
                    "-v",
                    "--tb=no",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "pytest_asyncio.plugin",
                    "-p",
                    "ledger_source_guard",
                ],
                cwd=archive_root,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=_RECIPE_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return PytestCapture(
                False,
                None,
                None,
                f"TIMEOUT after {_RECIPE_TIMEOUT_SECONDS}s running historical pytest "
                f"{edge.target.test_path} at {edge.claim.source_commit}",
            )
        combined = proc.stdout + proc.stderr
        if proc.returncode not in (0, 1):
            return PytestCapture(
                False,
                combined,
                proc.returncode,
                f"historical pytest exited {proc.returncode} for {edge.target.test_path}; tail:\n"
                + "\n".join(combined.splitlines()[-10:]),
            )
        if not marker.is_file():
            return PytestCapture(
                False, combined, proc.returncode, "historical source guard did not attest imports"
            )
        try:
            attestation = json.loads(marker.read_text(encoding="utf-8"))
            imported_paths = [Path(raw) for raw in attestation["archived"]]
            escaped_paths = [Path(raw) for raw in attestation["escaped"]]
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
            return PytestCapture(
                False, combined, proc.returncode, f"historical source guard marker is malformed: {exc}"
            )
        expected_test = extracted_test.resolve()
        if expected_test not in imported_paths:
            return PytestCapture(
                False,
                combined,
                proc.returncode,
                "historical source guard did not attest execution of the bound archived test",
            )
        if escaped_paths:
            return PytestCapture(
                False,
                combined,
                proc.returncode,
                "historical source import escaped archived tree: " + ", ".join(map(str, escaped_paths)),
            )
        return PytestCapture(
            True,
            combined,
            proc.returncode,
            f"{len(imported_paths)} archived Python source files attested",
        )


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


# HARNESS-LEDGER-HASH-AMBIENT-STACK: a `tests/integration/**` file's recipe run needs a real
# engine/PG/Kafka stack up on well-known ports — this tool has no mutex over that stack and no way
# to know whether it brought it up itself or is reaching another agent's ambient one (confirmed
# once via `docker ps`: a queued agent's OWN `maezo-operadora-*` containers were up on the standard
# ports at the moment this gate ran; the hash happened to still be correct that time — topics are
# uniquely named per run — but the tool had no way to KNOW that). `is_live_test_path` names the
# scope this refusal applies to; `verify_row` refuses to execute such a row unless the caller
# explicitly asserts (`--allow-live` / `LEDGER_HASH_ALLOW_LIVE`) that they hold the engine mutex.
_LIVE_TEST_PATH_PREFIX = "tests/integration/"


def is_live_test_path(path: str) -> bool:
    """Pure: True iff `path` falls under `tests/integration/**` — the scope this gate refuses to
    execute without an explicit `--allow-live` assertion (see module docstring, deviation D-24)."""
    return path.startswith(_LIVE_TEST_PATH_PREFIX)


# Truthy values accepted for the `LEDGER_HASH_ALLOW_LIVE` env-var fallback to `--allow-live` (see
# `resolve_allow_live`) — deliberately small and explicit, not "any non-empty string", so a stray
# exported-but-empty or accidentally-"0"/"false" var never silently grants the assertion.
_ALLOW_LIVE_ENV_TRUE_VALUES = {"1", "true", "yes"}


def resolve_allow_live(cli_flag: bool, env: Mapping[str, str]) -> bool:
    """Pure: True iff the caller asserted engine-mutex ownership via the CLI flag OR the
    `LEDGER_HASH_ALLOW_LIVE` env var (case-insensitive, one of `_ALLOW_LIVE_ENV_TRUE_VALUES`). The
    env-var path exists so `make check-ledger-hashes` — CODEOWNED, deliberately left unchanged by
    this fix — can be invoked as `LEDGER_HASH_ALLOW_LIVE=1 make check-ledger-hashes` by a caller
    that already holds the mutex, with ZERO Makefile diff: the exported var reaches the recipe's
    subprocess through the normal shell-environment inheritance a Make recipe always has, without
    the Makefile needing to name or forward it."""
    if cli_flag:
        return True
    return env.get("LEDGER_HASH_ALLOW_LIVE", "").strip().lower() in _ALLOW_LIVE_ENV_TRUE_VALUES


@dataclass(frozen=True)
class RowVerification:
    row: DeclaredRow
    ok: bool
    message: str


def verify_row(
    repo_root: Path,
    row: DeclaredRow,
    python_exe: str,
    *,
    allow_live: bool = False,
    allow_legacy_recipe: bool = True,
) -> RowVerification:
    if not is_safe_test_path(row.test_path):
        return RowVerification(
            row,
            False,
            f"{row.task_id}: declared path {row.test_path!r} fails the safety pattern "
            f"^tests/[A-Za-z0-9_/.-]+\\.py$ or contains a '..' segment — refusing to execute it "
            "(path-injection guard).",
        )
    if is_live_test_path(row.test_path) and not allow_live:
        return RowVerification(
            row,
            False,
            f"{row.task_id}: declared path {row.test_path} is under {_LIVE_TEST_PATH_PREFIX} — "
            "refusing to recompute its hash (HARNESS-LEDGER-HASH-AMBIENT-STACK): this gate has no "
            "mutex over the engine/PG/Kafka stack an integration test needs and no way to tell a "
            "stack it brought up itself from another agent's ambient one on the same standard "
            "ports. Re-run with --allow-live (or LEDGER_HASH_ALLOW_LIVE=1) ONLY while you "
            "personally hold the engine mutex and control the stack.",
        )
    file_path = repo_root / row.test_path
    if not file_path.is_file():
        return RowVerification(
            row, False, f"{row.task_id}: declared test file {row.test_path} does not exist at HEAD."
        )
    capture = capture_pytest_recipe(repo_root, row.test_path, python_exe)
    outcome = recipe_outcome_from_capture(capture, row.test_path, node_id_regex=_RESULT_LINE_RE)
    if not outcome.ok:
        return RowVerification(
            row, False, f"{row.task_id}: recipe execution failed for {row.test_path}: {outcome.detail}"
        )
    declared_full = f"sha256:{row.declared_hash}"
    assert outcome.computed_hash is not None  # ok=True guarantees this
    if outcome.computed_hash.lower() == declared_full.lower():
        return RowVerification(
            row,
            True,
            f"{row.task_id}: verified {declared_full} ({row.test_path}, {outcome.result_line_count} "
            "result lines).",
        )
    # The FIXED recipe didn't match. A row dated strictly before LEGACY_NODE_ID_RECIPE_CUTOFF_DATE
    # may have been declared under the LEGACY (pre-fix) node-id extraction, which silently dropped
    # a spaced parametrize id (LEDGER-HASH-PARAM-IDS-WITH-SPACES) — try that recipe too, against
    # the SAME already-captured pytest run (no second subprocess), before concluding a real
    # mismatch. This is never offered to a row dated on/after the cutoff: a future row must
    # reproduce under the fixed recipe alone, or fixing the bug accomplishes nothing.
    if allow_legacy_recipe and row.row_date is not None and row.row_date < LEGACY_NODE_ID_RECIPE_CUTOFF_DATE:
        legacy_outcome = recipe_outcome_from_capture(
            capture, row.test_path, node_id_regex=_RESULT_LINE_RE_LEGACY
        )
        if (
            legacy_outcome.ok
            and legacy_outcome.computed_hash is not None
            and legacy_outcome.computed_hash.lower() == declared_full.lower()
        ):
            return RowVerification(
                row,
                True,
                f"{row.task_id}: verified {declared_full} ({row.test_path}, "
                f"{legacy_outcome.result_line_count} result lines) via the LEGACY pre-fix node-id "
                "recipe (recipe_version=legacy — this row predates LEDGER-HASH-PARAM-IDS-WITH-SPACES, "
                "dated before LEGACY_NODE_ID_RECIPE_CUTOFF_DATE; see the disclosure row "
                "LEDGER-HASH-RECIPE-CHANGE-2026-09-05 in docs/evidence-ledger.md). The FIXED recipe "
                f"recomputes {outcome.computed_hash} ({outcome.result_line_count} result lines) for "
                "the same file at HEAD — expected, not a new integrity problem: the file did not "
                "change, only this tool's own node-id extraction did.",
            )
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


def verify_historical_row(
    repo_root: Path,
    edge: SupersessionEdge,
    python_exe: str,
    *,
    allow_live: bool = False,
) -> RowVerification:
    """Re-run a superseded target in its bound archive; never fall back to candidate HEAD."""
    row = edge.target
    if is_live_test_path(row.test_path) and not allow_live:
        return RowVerification(
            row,
            False,
            f"{row.task_id}: historical declared path {row.test_path} is under "
            f"{_LIVE_TEST_PATH_PREFIX}; HARNESS-LEDGER-HASH-AMBIENT-STACK applies equally to "
            "archived tests. Re-run with --allow-live only while holding the engine mutex.",
        )
    current_lock_path = repo_root / "uv.lock"
    if not current_lock_path.is_file():
        return RowVerification(
            row,
            False,
            f"{edge.successor.task_id}: current uv.lock is missing; historical dependency "
            "provenance is unbound",
        )
    current_lock = current_lock_path.read_bytes()
    try:
        head_lock = _source_blob(repo_root, "HEAD", "uv.lock")
        source_lock = _source_blob(repo_root, edge.claim.source_commit, "uv.lock")
    except SupersessionError as exc:
        return RowVerification(row, False, f"{edge.successor.task_id}: {exc}")
    if current_lock != head_lock:
        return RowVerification(
            row,
            False,
            f"{edge.successor.task_id}: working-tree uv.lock differs from HEAD; refusing "
            "historical execution",
        )
    if source_lock != current_lock:
        return RowVerification(
            row,
            False,
            f"{edge.successor.task_id}: historical uv.lock differs from HEAD; the current locked "
            "environment cannot truthfully execute that historical tree",
        )
    capture = _capture_historical_recipe(repo_root, edge, python_exe, allow_live=allow_live)
    outcome = recipe_outcome_from_capture(capture, row.test_path, node_id_regex=_RESULT_LINE_RE)
    if not outcome.ok:
        return RowVerification(
            row,
            False,
            f"{row.task_id}: historical recipe execution failed at {edge.claim.source_commit}: "
            f"{outcome.detail}",
        )
    declared_full = f"sha256:{row.declared_hash}"
    assert outcome.computed_hash is not None
    if outcome.computed_hash.lower() == declared_full.lower():
        return RowVerification(
            row,
            True,
            f"{row.task_id}: historical claim verified at exact source_commit "
            f"{edge.claim.source_commit} ({row.test_path}, {outcome.result_line_count} result lines; "
            f"{capture.detail}).",
        )
    if row.row_date is not None and row.row_date < LEGACY_NODE_ID_RECIPE_CUTOFF_DATE:
        legacy = recipe_outcome_from_capture(capture, row.test_path, node_id_regex=_RESULT_LINE_RE_LEGACY)
        if (
            legacy.ok
            and legacy.computed_hash is not None
            and legacy.computed_hash.lower() == declared_full.lower()
        ):
            return RowVerification(
                row,
                True,
                f"{row.task_id}: historical claim verified at exact source_commit "
                f"{edge.claim.source_commit} via recipe_version=legacy "
                f"({row.test_path}, {legacy.result_line_count} result lines; {capture.detail}).",
            )
    return RowVerification(
        row,
        False,
        f"{row.task_id}: historical hash mismatch at exact source_commit "
        f"{edge.claim.source_commit} for {row.test_path} — declared {declared_full}, recomputed "
        f"{outcome.computed_hash}; refusing any HEAD fallback.",
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
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Assert that the caller holds the engine mutex and controls the stack, so this gate "
        "may recompute a declared row whose path is under tests/integration/** "
        "(HARNESS-LEDGER-HASH-AMBIENT-STACK, deviation D-24). Without this flag (or the "
        "LEDGER_HASH_ALLOW_LIVE env var), such a row is refused, never silently skipped or run "
        "against a stack this tool did not bring up itself.",
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

    try:
        supersession_plan = build_supersession_plan(root, ledger_text, args.ledger_path)
    except (OSError, SupersessionError) as exc:
        print(f"{prefix} ERROR: invalid ledger supersession provenance: {exc}", file=sys.stderr)
        return 1

    if args.all:
        try:
            selection = select_rows(ledger_text.splitlines())
        except SupersessionError as exc:
            print(f"{prefix} ERROR: {exc}", file=sys.stderr)
            return 1
        scope_desc = f"every declared row in {args.ledger_path} (--all)"
    else:
        try:
            effective_base = resolve_effective_base(root, args.base)
            added_lines = get_ledger_diff_added_lines(root, effective_base, args.ledger_path)
        except GitError as exc:
            print(f"{prefix} ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            selection = select_rows(added_lines)
        except SupersessionError as exc:
            print(f"{prefix} ERROR: {exc}", file=sys.stderr)
            return 1
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

    effective_allow_live = resolve_allow_live(args.allow_live, os.environ)
    results: list[RowVerification] = []
    historical_scheduled: set[str] = set()
    for row in selection.declared:
        digest = row_sha256(row)
        successor_edge = supersession_plan.by_successor_row_sha256.get(digest)
        target_edge = supersession_plan.by_target_row_sha256.get(digest)

        # A newly appended successor must prove the older row even when that older row was already
        # present at the PR base and would not otherwise be selected by the diff.
        if successor_edge is not None:
            target_digest = row_sha256(successor_edge.target)
            if target_digest not in historical_scheduled:
                results.append(
                    verify_historical_row(root, successor_edge, args.python, allow_live=effective_allow_live)
                )
                historical_scheduled.add(target_digest)

        # A stale imported target is verified only in its pinned source tree.  Its successor is a
        # separate current-HEAD assertion, never an exemption or a silent replacement.
        if target_edge is not None:
            if digest not in historical_scheduled:
                results.append(
                    verify_historical_row(root, target_edge, args.python, allow_live=effective_allow_live)
                )
                historical_scheduled.add(digest)
            continue

        results.append(
            verify_row(
                root,
                row,
                args.python,
                allow_live=effective_allow_live,
                # Successor rows are go-forward claims and never receive the pre-fix recipe.
                allow_legacy_recipe=successor_edge is None,
            )
        )
    for result in results:
        status = "OK" if result.ok else "MISMATCH"
        print(f"{prefix} {status}: {result.message}")

    failures = [r for r in results if not r.ok]
    verified_ok = len(results) - len(failures)
    declared_count = len(selection.declared)
    proof_note = f"; {len(results)} total current/historical proofs" if len(results) != declared_count else ""
    proof_label = (
        "declared rows verified" if len(results) == declared_count else "declared-row proofs verified"
    )
    print(
        f"{prefix} SUMMARY: {verified_ok}/{len(results)} {proof_label}"
        f" ({declared_count} selected rows{proof_note}), "
        f"{len(selection.legacy)} legacy rows skipped — {scope_desc}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
