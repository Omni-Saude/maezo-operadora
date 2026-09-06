#!/usr/bin/env python3
"""CI gate: PLANS.md's count claims must reconcile against the real tree (AF-06).

Purpose
-------
`PLANS.md` states, in prose, how many ADRs the repository has — twice, in two different shapes
(`docs/adr/` (**N** ADRs numerados, **M** arquivos incl. README+template ...)` in the "fonte de
verdade" header paragraph, and `**N** ADRs (não 24); ...` in the §0.3 milestone-reconciliation
table). Both numbers are HAND-WRITTEN prose, and both went stale: the audit that opened this gap
(AF-06, `reports/domain-01-architecture-fidelity.md:43` + `gateways/gvr-d01.md:53,59`) found
`PLANS.md:39` still claiming "32 ADRs (não 24)" while the tree already held 39 numbered files —
and by the time this gate was written the tree had grown again, to 48 (`docs/adr/0001-...md`
through `docs/adr/0048-...md`). Nothing ever recomputed these numbers against the filesystem; they
were trust-on-claim, exactly the anti-pattern `docs/evidence-ledger.md` exists to make impossible
for task claims — this gate closes the same hole for `PLANS.md`'s own count claims.

A fourth claim shape was found OPEN after this gate first landed (`docs/review-queue.md`'s
"AF-06 -- PLANS.md:43 ..." item, 2026-09-05): `PLANS.md:43`'s `≈540 linhas` claim about
`src/maezo/gateway/tool_registry.py`, left unfenced on purpose because it is written as an
EXPLICIT approximation (the `≈` glyph), unlike patterns 1-3's exact-equality claims. It is closed
here as pattern 4, tolerance-based rather than exact (see `TOOL_REGISTRY_LINES_TOLERANCE`).

Scope (deliberately narrow, not "every number in a 1000-line document")
-------------------------------------------------------------------------
`PLANS.md` also contains many numbers inside its preserved historical sections (§3's milestone
envelopes, explicitly marked SUPERSEDED at the top of the file, kept as "reference of what each
milestone was meant to deliver" — never live status). Trying to reconcile every digit in that prose
against the tree would be both fragile (constant false positives on deliberately-frozen historical
text) and wrong (those numbers are NOT claims about today's tree). This gate therefore checks only
four narrowly-anchored, unambiguous CURRENT-count claim shapes — the spots the AF-06 finding named
(plus its own documented residual, pattern 4), and the only places in the file that use these exact
phrasings:

    1. `<N> ADRs numerados`               -> N must equal the number of numbered ADR files
                                              (`docs/adr/NNNN-*.md`).
    2. `<N> ADRs (não <anything>)`         -> N must equal the same numbered-ADR-file count.
    3. `<N> arquivos incl. README+template` -> N must equal the TOTAL `.md` file count in
                                              `docs/adr/` (numbered files + README.md + template.md).
    4. `gateway/tool_registry.py`, classe `ToolRegistry`, ≈<N> linhas` -> N must be within
                                              `TOOL_REGISTRY_LINES_TOLERANCE` (relative) of
                                              `wc -l src/maezo/gateway/tool_registry.py`.

Every match of any of these four patterns anywhere in `PLANS.md` is checked — not just the spots
known today — so another occurrence added later (a new paragraph restating a count) is caught
automatically without touching this gate. A line matching none of these four patterns is never
touched, never flagged: this is a reconciliation fence for SPECIFIC named claim shapes, not a
general-purpose number-guesser.

Fail-closed contract
---------------------
- Any match of pattern 1 or 2 whose captured integer != the real numbered-ADR-file count -> FAIL,
  naming the line number, the claimed count, and the real count.
- Any match of pattern 3 whose captured integer != the real total `docs/adr/*.md` file count ->
  FAIL, same shape.
- Any match of pattern 4 whose captured integer is outside `TOOL_REGISTRY_LINES_TOLERANCE`
  (relative) of the real `tool_registry.py` line count -> FAIL, same shape (tolerant, not exact,
  because the claim is written with `≈`).
- Zero matches of ALL four patterns (e.g. a future rewrite removes this phrasing entirely) ->
  PASS, but ALWAYS print the explicit zero-matches signal — never silently green with no evidence
  anything was actually checked (same convention as `check_evidence_ledger_hashes.py`).
- `docs/adr/` missing, `tool_registry.py` missing, unreadable, or `PLANS.md` missing -> FAIL. Never
  silently skip.

Usage
-----
    python3 scripts/ci/check_plans_counts.py                 # CI gate, default paths
    python3 scripts/ci/check_plans_counts.py --plans PATH --adr-dir PATH --tool-registry PATH
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
DEFAULT_PLANS_PATH: Final[Path] = Path("PLANS.md")
DEFAULT_ADR_DIR: Final[Path] = Path("docs/adr")
DEFAULT_TOOL_REGISTRY_PATH: Final[Path] = Path("src/maezo/gateway/tool_registry.py")

# A numbered ADR file: exactly 4 digits, a hyphen, then a slug, `.md`. Deliberately excludes
# `README.md` and `template.md` (neither is numbered) — the same distinction PLANS.md itself draws
# ("N ADRs numerados, M arquivos incl. README+template").
_NUMBERED_ADR_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-.+\.md$")

# Pattern 1: "<N> ADRs numerados" (the header paragraph's phrasing).
_ADRS_NUMERADOS_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+ADRs numerados")

# Pattern 2: "<N> ADRs (não ...)" (the §0.3 milestone-reconciliation table's phrasing) — the
# parenthesised remainder is free text (historically a contrast against an even older count, e.g.
# "não 24"); only the FIRST number is a claim this gate checks.
_ADRS_NAO_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+ADRs\s+\(não\b")

# Pattern 3: "<N> arquivos incl. README+template" (the header paragraph's companion claim,
# the TOTAL file count including the two non-numbered files).
_ARQUIVOS_TEMPLATE_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+arquivos incl\. README\+template")

# Pattern 4 (AF-06 residual, PLANS.md:43, `docs/review-queue.md`'s "AF-06 -- PLANS.md:43 ..."
# item): "gateway/tool_registry.py`, classe `ToolRegistry`, ≈<N> linhas" — unlike patterns 1-3,
# this claim is written with the `≈` glyph: the author's own signal that it is an ESTIMATE, not a
# pin, and the surrounding prose already tells the reader to "medir com `wc -l` antes de citar".
# Anchored on BOTH the file path and the `≈<N> linhas` shape appearing on the same line (`.{0,80}`
# bridges "ToolRegistry`, " between them) so this pattern cannot accidentally match an unrelated
# `≈N linhas` claim about some other file elsewhere in the document.
_TOOL_REGISTRY_LINES_RE: Final[re.Pattern[str]] = re.compile(
    r"gateway/tool_registry\.py.{0,80}?≈\s*(\d+)\s+linhas"
)

#: Relative tolerance for the `≈N linhas` claim: the `≈` glyph is an explicit "this is an
#: estimate" marker, so exact equality (patterns 1-3's contract) would be the wrong bar — it would
#: force a PLANS.md edit on every single-line change to `tool_registry.py`, exactly the churn the
#: file's OWN prose (`docs/evidence-ledger.md`'s line count, cited two paragraphs above this one in
#: PLANS.md) already gives as the reason NOT to pin an append-only-file's size exactly. 10% is
#: generous enough to absorb ordinary drift but still catches genuine staleness: the actual
#: residual this pattern exists to close (540 claimed vs 794 real, found 2026-09-05) is 47% off,
#: comfortably outside this band either way the drift runs.
TOOL_REGISTRY_LINES_TOLERANCE: Final[float] = 0.10


def count_tool_registry_lines(tool_registry_path: Path) -> int:
    """The real, current line count of `tool_registry_path` — the same measure `wc -l` reports
    (one count per newline-terminated line), which is the exact command PLANS.md's own prose
    already tells the reader to run before citing this number."""
    return len(tool_registry_path.read_text(encoding="utf-8").splitlines())


def count_numbered_adr_files(adr_dir: Path) -> int:
    """The real, current number of numbered ADR files under `adr_dir` — the ground truth every
    `ADRs numerados` / `ADRs (não ...)` claim in PLANS.md is checked against."""
    return sum(1 for p in adr_dir.iterdir() if p.is_file() and _NUMBERED_ADR_RE.match(p.name))


def count_all_adr_dir_files(adr_dir: Path) -> int:
    """The real, current TOTAL `.md` file count under `adr_dir` (numbered ADRs + README.md +
    template.md) — the ground truth every `arquivos incl. README+template` claim is checked
    against."""
    return sum(1 for p in adr_dir.iterdir() if p.is_file() and p.suffix == ".md")


@dataclass(frozen=True, slots=True)
class Finding:
    """One reconciliation mismatch: a claimed count in PLANS.md that disagrees with the real
    tree, named by 1-based line number so a human can jump straight to it."""

    line_no: int
    claim_shape: str
    claimed: int
    real: int

    def render(self) -> str:
        return (
            f"  - PLANS.md:{self.line_no}: alega \"{self.claimed}\" para '{self.claim_shape}', "
            f"mas a árvore tem {self.real} — reconcilie o número ou corrija o gate se a árvore "
            "mudou de forma legítima."
        )


def _within_tolerance(claimed: int, real: int, tolerance: float) -> bool:
    """Pure: True iff `claimed` is within `tolerance` (relative — 0.10 == 10%) of `real`. `real ==
    0` is treated as an exact-match edge case (a relative tolerance is undefined against zero)."""
    if real == 0:
        return claimed == 0
    return abs(claimed - real) / real <= tolerance


def evaluate(
    plans_text: str, adr_dir: Path, tool_registry_path: Path | None = None
) -> tuple[list[Finding], int]:
    """Pure(ish) — given the already-read PLANS.md text, the real `docs/adr/` path, and
    (optionally) the real `tool_registry.py` path, returns (findings, total_matches_checked).
    `tool_registry_path=None` skips pattern 4 entirely (matches_checked stays accurate — it simply
    never increments for that pattern) rather than failing: callers that only care about the ADR
    patterns (most of this file's own test fixtures, which build a synthetic `adr_dir` with no
    accompanying `tool_registry.py`) keep working unchanged. `main` always passes the real path.
    `total_matches_checked` is always printed by `main`, even when it is zero, so a future rewrite
    that removes this phrasing entirely is a visible PASS with an explicit "0 claims found" signal
    — never a silent green with no evidence anything ran."""
    numbered_count = count_numbered_adr_files(adr_dir)
    total_count = count_all_adr_dir_files(adr_dir)
    tool_registry_lines = (
        count_tool_registry_lines(tool_registry_path) if tool_registry_path is not None else None
    )

    findings: list[Finding] = []
    matches_checked = 0
    lines = plans_text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        for pattern, shape, real in (
            (_ADRS_NUMERADOS_RE, "ADRs numerados", numbered_count),
            (_ADRS_NAO_RE, "ADRs (não ...)", numbered_count),
            (_ARQUIVOS_TEMPLATE_RE, "arquivos incl. README+template", total_count),
        ):
            for match in pattern.finditer(line):
                matches_checked += 1
                claimed = int(match.group(1))
                if claimed != real:
                    findings.append(Finding(line_no=line_no, claim_shape=shape, claimed=claimed, real=real))

        if tool_registry_lines is not None:
            for match in _TOOL_REGISTRY_LINES_RE.finditer(line):
                matches_checked += 1
                claimed = int(match.group(1))
                if not _within_tolerance(claimed, tool_registry_lines, TOOL_REGISTRY_LINES_TOLERANCE):
                    findings.append(
                        Finding(
                            line_no=line_no,
                            claim_shape="≈N linhas em tool_registry.py",
                            claimed=claimed,
                            real=tool_registry_lines,
                        )
                    )
    return findings, matches_checked


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "AF-06: reconcile PLANS.md's ADR-count claims (ADRs numerados / ADRs (não ...) / "
            "arquivos incl. README+template) against the real docs/adr/ tree."
        )
    )
    parser.add_argument(
        "--plans",
        default=str(DEFAULT_PLANS_PATH),
        help=f"path to PLANS.md, relative to repo root unless absolute (default: {DEFAULT_PLANS_PATH}).",
    )
    parser.add_argument(
        "--adr-dir",
        default=str(DEFAULT_ADR_DIR),
        help=f"path to the ADR directory, relative to repo root unless absolute "
        f"(default: {DEFAULT_ADR_DIR}).",
    )
    parser.add_argument(
        "--tool-registry",
        default=str(DEFAULT_TOOL_REGISTRY_PATH),
        help="path to tool_registry.py (AF-06 residual, PLANS.md:43's `≈N linhas` claim), "
        f"relative to repo root unless absolute (default: {DEFAULT_TOOL_REGISTRY_PATH}).",
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
    prefix = "[check-plans-counts]"

    plans_path = Path(args.plans)
    if not plans_path.is_absolute():
        plans_path = root / plans_path
    adr_dir = Path(args.adr_dir)
    if not adr_dir.is_absolute():
        adr_dir = root / adr_dir
    tool_registry_path = Path(args.tool_registry)
    if not tool_registry_path.is_absolute():
        tool_registry_path = root / tool_registry_path

    if not adr_dir.is_dir():
        print(f"{prefix} ERROR: diretório de ADRs não encontrado: {adr_dir}", file=sys.stderr)
        return 1
    if not tool_registry_path.is_file():
        print(
            f"{prefix} ERROR: tool_registry.py não encontrado: {tool_registry_path}",
            file=sys.stderr,
        )
        return 1
    try:
        plans_text = plans_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{prefix} ERROR: não foi possível ler {plans_path}: {exc}", file=sys.stderr)
        return 1

    findings, matches_checked = evaluate(plans_text, adr_dir, tool_registry_path)

    if findings:
        print(
            f"{prefix} FAIL: {len(findings)} de {matches_checked} alegação(ões) de contagem de "
            f"ADR em {plans_path} não reconcilia(m) com a árvore real:",
            file=sys.stderr,
        )
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(
        f"{prefix} PASS: {matches_checked} alegação(ões) de contagem de ADR em {plans_path} "
        f"reconciliam com a árvore real ({adr_dir})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
