#!/usr/bin/env python3
r"""CI gate: every ROW ADDED to `docs/evidence-ledger.md` has the same cell count as its table
header (LEDGER-ROW-CELL-COUNT).

Purpose
-------
`docs/evidence-ledger.md`'s table has 8 columns (`| Task ID | Date | Author (agent, tier) |
Verifier (agent, tier) | Commit SHA | Evidence (path:line) | Test hash | Status |`), and every row
below the header is supposed to match that shape. The convention for a literal `|` INSIDE a free
text cell (Evidence prose routinely quotes shell pipelines, e.g. `cmd1 | cmd2`) is to escape it as
`\|` — the module docstring of `check_evidence_ledger_hashes.py` already says so ("Escape `|`
inside cells"), but nothing ever ENFORCED it. A row that forgets the escape silently grows an extra
markdown table cell for every unescaped `|` it contains — the table renders WRONG (a later cell's
content bleeds into the wrong column) and the row's true field count drifts from the schema, with
no gate ever noticing: it still parses as SOME row, `check_evidence_ledger_hashes.py`'s own
`_TASK_ID_CELL_RE` still finds a first cell, its hash-verification gate is entirely blind to this —
a real 14-cell row (found live 2026-09-05 by the reembolso gatekeeper) passed that gate clean.

This gate closes that hole: it recomputes, for every row a PR ADDS to the ledger, whether its cell
count (honouring `\|` as an escaped, non-delimiting pipe) equals the header's cell count.

Scope (same range logic as `check_evidence_ledger_hashes.py`, deliberately reused, not
reimplemented)
-------------------------------------------------------------------------------------------------
The whole ledger ALREADY has 30 pre-existing malformed rows on `main` at the time this gate was
written (counted independently by the reembolso gatekeeper, and reproduced by this gate's own
`--all` mode). Retroactively enforcing the cell-count invariant against ALL of them would turn
every future PR red for a defect it did not introduce — exactly the failure mode
`check_evidence_ledger_hashes.py`'s own "Scope" section (design fact 1) already rejected for hash
staleness. This gate makes the identical choice, using the IDENTICAL mechanism: it checks ONLY rows
ADDED in the range `<base>..HEAD` of `docs/evidence-ledger.md` (`resolve_effective_base` +
`get_ledger_diff_added_lines`, imported unchanged from `check_evidence_ledger_hashes.py` — one
range-resolution implementation, not two that could drift apart). `main` stays green; a PR that
adds a malformed row goes red on THAT row, not on the 30 pre-existing ones. `--all` (local use
only, never wired into CI) checks every row in the CURRENT ledger regardless of when it was added —
used once to produce the `docs/review-queue.md` inventory of the 30 pre-existing rows (with their
line numbers) this gate's own introduction is required to disclose, never to merge-gate.

Fail-closed contract
---------------------
- A row ADDED in `<base>..HEAD` whose cell count (honouring `\|`) != the header's cell count ->
  FAIL, naming the row's Task-ID cell and its actual vs. expected cell count.
- The header row itself (`| Task ID | ...`) cannot be found in the ledger -> FAIL. A cell-count
  gate with no known schema to check against is not a gate, it is a guess.
- Zero data rows added in range (every added line is the header/separator, or nothing was added)
  -> PASS, but ALWAYS print the explicit count ("0 rows checked") — never silently green with no
  signal that nothing was actually checked.
- Any git/subprocess error resolving the range or reading the ledger -> FAIL (propagated from the
  shared `resolve_effective_base` / `get_ledger_diff_added_lines`).

Usage
-----
    python3 scripts/ci/check_ledger_row_cell_count.py               # CI: base = origin/main merge-base
    python3 scripts/ci/check_ledger_row_cell_count.py --base <sha>  # CI: pull_request.base.sha
    python3 scripts/ci/check_ledger_row_cell_count.py --all         # local spot-audit, every row, with
                                                                     # line numbers (review-queue inventory)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    # Same idempotent sys.path insert as generate_release_floor.py's own cross-script import, for
    # the same reason: this module must resolve `scripts.ci.check_evidence_ledger_hashes` the same
    # way whether run directly (sys.path[0] defaults to scripts/ci, NOT the repo root) or imported
    # by pytest (`pythonpath = ["."]` already puts the repo root on sys.path there).
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.check_evidence_ledger_hashes import (  # noqa: E402
    DEFAULT_LEDGER_PATH,
    GitError,
    get_ledger_diff_added_lines,
    resolve_effective_base,
)

# The literal, exact first line of the ledger table's header — the one hard-coded anchor this gate
# needs to know the schema. Every column-count claim below derives from THIS line, never from a
# guess or a second hard-coded integer that could drift out of sync with it.
_HEADER_ROW_PREFIX: Final[str] = "| Task ID |"

# A markdown table SEPARATOR row: one or more `|`-delimited cells each containing only optional
# `:` alignment markers and one-or-more `-`. Excluded from "data rows" the same way the header is —
# neither is a ledger entry, and both would trivially "pass" the cell-count check anyway (their
# cell count structurally equals the header's), but counting them as data rows would be a category
# error in any report this gate prints.
_SEPARATOR_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")

# Splits a table-row line on UNESCAPED `|` only — a `\|` (escaped, per the ledger's own stated
# convention: "Escape `|` inside cells", check_evidence_ledger_hashes.py module docstring) is never
# a delimiter. Negative lookbehind, not a positive "not preceded by a second backslash" attempt —
# the ledger's real free-text cells never need `\\|` (a literal backslash immediately before a
# literal pipe), so the simpler rule is the correct one for this corpus.
_UNESCAPED_PIPE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\\)\|")


def split_row_cells(line: str) -> list[str] | None:
    r"""Pure: given one line, return its markdown-table cell contents (honouring `\|` as a
    non-delimiting escaped pipe), or None if the line is not table-row-shaped (does not start with
    `|` once stripped). A well-formed `| a | b | c |` row splits into `['', ' a ', ' b ', ' c ',
    '']`; the empty leading/trailing artifacts of the boundary pipes are dropped. A row missing its
    trailing `|` (a DIFFERENT malformation than an unescaped inner pipe) keeps its trailing cell
    un-dropped, which correctly still drifts its count from a well-formed row of the same shape."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    parts = _UNESCAPED_PIPE_RE.split(stripped)
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def is_separator_row(line: str) -> bool:
    """Pure: True iff `line` is the markdown table separator row (`|---|---|...|`, optionally with
    `:` alignment markers) — never a data row, never the header."""
    return _SEPARATOR_ROW_RE.match(line.strip()) is not None


def find_expected_cell_count(ledger_text: str) -> int | None:
    """Pure: the ledger's declared schema width, read from the FIRST line starting with the exact
    literal `_HEADER_ROW_PREFIX`. Returns None if no such line exists — `main`'s never lacks a
    header, but a synthetic/corrupted fixture might, and this must fail closed, not guess a width."""
    for line in ledger_text.splitlines():
        if line.startswith(_HEADER_ROW_PREFIX):
            cells = split_row_cells(line)
            return len(cells) if cells is not None else None
    return None


@dataclass(frozen=True, slots=True)
class RowCellCountFinding:
    """One row whose cell count disagrees with the header. `line_no` is only meaningful in `--all`
    mode (a real position in the CURRENT file); the scoped (range) mode operates over diff-added
    line CONTENT, which carries no stable file position, so it is `None` there — matching how
    `check_evidence_ledger_hashes.py`'s own findings identify a row by Task ID, never a line
    number, in that same mode."""

    task_id: str
    actual_cells: int
    expected_cells: int
    line_no: int | None

    def render(self) -> str:
        where = f"docs/evidence-ledger.md:{self.line_no}" if self.line_no is not None else "(linha nova)"
        return (
            f"  - {where} [{self.task_id}]: {self.actual_cells} células, esperado "
            f"{self.expected_cells} — provável `|` não escapado dentro de uma célula (use `\\|`)."
        )


def _task_id_of(cells: list[str]) -> str:
    return cells[0].strip() if cells else "(sem primeira célula)"


def check_lines(
    lines: Sequence[str], expected_cells: int, *, with_line_numbers: bool
) -> tuple[list[RowCellCountFinding], int]:
    """Pure: partitions `lines` into (findings, rows_checked). A candidate line is any
    table-row-shaped line (`split_row_cells` returns non-None) that is NEITHER the header
    (`_HEADER_ROW_PREFIX`) NOR the separator (`is_separator_row`) — i.e. an actual ledger entry.
    `with_line_numbers=True` (the `--all` local-audit path) reports each finding's REAL 1-based
    position in `lines`; `False` (the scoped CI path, over diff-added content with no stable
    position) reports `line_no=None`."""
    findings: list[RowCellCountFinding] = []
    rows_checked = 0
    for idx, line in enumerate(lines, start=1):
        if line.startswith(_HEADER_ROW_PREFIX) or is_separator_row(line):
            continue
        cells = split_row_cells(line)
        if cells is None:
            continue  # not a table row at all — prose, blank line, diff noise
        rows_checked += 1
        if len(cells) != expected_cells:
            findings.append(
                RowCellCountFinding(
                    task_id=_task_id_of(cells),
                    actual_cells=len(cells),
                    expected_cells=expected_cells,
                    line_no=idx if with_line_numbers else None,
                )
            )
    return findings, rows_checked


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_ledger_row_cell_count",
        description=(
            "Fail-closed CI gate: every row added to docs/evidence-ledger.md in <base>..HEAD has "
            "the same cell count as the table header (LEDGER-ROW-CELL-COUNT)."
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
        help="Check every row in the CURRENT ledger with real line numbers, ignoring the "
        "base..HEAD range (local spot-audit / review-queue inventory only — never wire this into "
        "CI, see module docstring 'Scope').",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail) — never anything else, never raises.

    `repo_root` is a testability seam (defaults to this file's real repo root); production callers
    never pass it.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    root = repo_root if repo_root is not None else REPO_ROOT
    prefix = "[check-ledger-row-cell-count]"

    ledger_path = root / args.ledger_path
    try:
        ledger_text = ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{prefix} ERROR: could not read ledger {args.ledger_path}: {exc}", file=sys.stderr)
        return 1

    expected_cells = find_expected_cell_count(ledger_text)
    if expected_cells is None:
        print(
            f"{prefix} ERROR: could not find the header row ({_HEADER_ROW_PREFIX!r}) in "
            f"{args.ledger_path} — refusing to guess the schema width.",
            file=sys.stderr,
        )
        return 1

    if args.all:
        lines = ledger_text.splitlines()
        findings, rows_checked = check_lines(lines, expected_cells, with_line_numbers=True)
        scope_desc = f"every row in {args.ledger_path} (--all)"
    else:
        try:
            effective_base = resolve_effective_base(root, args.base)
            added_lines = get_ledger_diff_added_lines(root, effective_base, args.ledger_path)
        except GitError as exc:
            print(f"{prefix} ERROR: {exc}", file=sys.stderr)
            return 1
        findings, rows_checked = check_lines(added_lines, expected_cells, with_line_numbers=False)
        scope_desc = f"rows added in {effective_base}..HEAD of {args.ledger_path}"

    if findings:
        print(
            f"{prefix} FAIL: {len(findings)}/{rows_checked} row(s) with a cell-count mismatch "
            f"(expected {expected_cells}) — {scope_desc}:",
            file=sys.stderr,
        )
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(f"{prefix} PASS: {rows_checked} row(s) checked, all {expected_cells}-cell — {scope_desc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
