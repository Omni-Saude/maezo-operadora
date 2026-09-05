#!/usr/bin/env python3
"""CI gate: PLANS.md's ADR-count claims must reconcile against the real `docs/adr/` tree (AF-06).

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

Scope (deliberately narrow, not "every number in a 1000-line document")
-------------------------------------------------------------------------
`PLANS.md` also contains many numbers inside its preserved historical sections (§3's milestone
envelopes, explicitly marked SUPERSEDED at the top of the file, kept as "reference of what each
milestone was meant to deliver" — never live status). Trying to reconcile every digit in that prose
against the tree would be both fragile (constant false positives on deliberately-frozen historical
text) and wrong (those numbers are NOT claims about today's tree). This gate therefore checks only
two narrowly-anchored, unambiguous CURRENT-count claim shapes — the two spots the AF-06 finding
named, and the only places in the file that use these exact phrasings:

    1. `<N> ADRs numerados`               -> N must equal the number of numbered ADR files
                                              (`docs/adr/NNNN-*.md`).
    2. `<N> ADRs (não <anything>)`         -> N must equal the same numbered-ADR-file count.
    3. `<N> arquivos incl. README+template` -> N must equal the TOTAL `.md` file count in
                                              `docs/adr/` (numbered files + README.md + template.md).

Every match of any of these three patterns anywhere in `PLANS.md` is checked — not just the two
spots known today — so a THIRD occurrence added later (a new paragraph restating the count) is
caught automatically without touching this gate. A line matching none of these three patterns is
never touched, never flagged: this is a reconciliation fence for a SPECIFIC named claim shape, not
a general-purpose number-guesser.

Fail-closed contract
---------------------
- Any match of pattern 1 or 2 whose captured integer != the real numbered-ADR-file count -> FAIL,
  naming the line number, the claimed count, and the real count.
- Any match of pattern 3 whose captured integer != the real total `docs/adr/*.md` file count ->
  FAIL, same shape.
- Zero matches of ALL three patterns (e.g. a future rewrite removes this phrasing entirely) ->
  PASS, but ALWAYS print the explicit zero-matches signal — never silently green with no evidence
  anything was actually checked (same convention as `check_evidence_ledger_hashes.py`).
- `docs/adr/` missing, unreadable, or `PLANS.md` missing -> FAIL. Never silently skip.

Usage
-----
    python3 scripts/ci/check_plans_counts.py                 # CI gate, default paths
    python3 scripts/ci/check_plans_counts.py --plans PATH --adr-dir PATH   # against other paths
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


def evaluate(plans_text: str, adr_dir: Path) -> tuple[list[Finding], int]:
    """Pure(ish) — given the already-read PLANS.md text and the real `docs/adr/` path, returns
    (findings, total_matches_checked). `total_matches_checked` is always printed by `main`, even
    when it is zero, so a future rewrite that removes this phrasing entirely is a visible PASS with
    an explicit "0 claims found" signal — never a silent green with no evidence anything ran."""
    numbered_count = count_numbered_adr_files(adr_dir)
    total_count = count_all_adr_dir_files(adr_dir)

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

    if not adr_dir.is_dir():
        print(f"{prefix} ERROR: diretório de ADRs não encontrado: {adr_dir}", file=sys.stderr)
        return 1
    try:
        plans_text = plans_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{prefix} ERROR: não foi possível ler {plans_path}: {exc}", file=sys.stderr)
        return 1

    findings, matches_checked = evaluate(plans_text, adr_dir)

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
