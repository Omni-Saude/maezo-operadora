#!/usr/bin/env python3
"""CI gate + generator: strict-xfail census over `tests/integration/processes/` (Hardening §0.8 Onda 0).

Purpose
-------
`git grep`-derived xfail counts have drifted three ways in the same week (PLANS.md said 24, the real
tree said 22, `handoff.yaml` and `docs/prompts/NEXT-ORCHESTRATOR-HANDOFF.md` each said something
else — PLANS.md §0.8 finding W5). A number a human recounts by hand and pastes into prose has no
mechanism to notice it went stale the next time a marker flips. This script makes the census a
GENERATED artifact instead: it re-derives every strict-xfail marker under `tests/integration/
processes/` straight from the AST, classifies each one by its `_*_REASON` constant name against an
explicit, committed mapping, and either regenerates the ledger (`--write`) or verifies the tree,
`docs/xfail-census.json` and the managed region of `PLANS.md` all agree (`--check`, the CI gate).

Fail-closed contract
---------------------
- A strict-xfail marker whose `reason=` constant has NO entry in `REASON_CLASSIFICATION` -> FAIL.
  New xfails must be classified at birth; the generator refuses to paper over an unknown one by
  silently omitting it or guessing a bucket.
- A `REASON_CLASSIFICATION` entry whose constant no longer exists anywhere in the scanned tree
  (renamed, deleted, retired) -> FAIL. A stale entry is exactly the kind of drift this script exists
  to catch, so it fails the census generator itself, not just the count.
- Any strict-ish xfail marker CONSTRUCTION that does not match the one sanctioned canonical shape
  (`@pytest.mark.xfail(reason=<NAME>, strict=True)` as a decorator directly on a `def`/`async def`
  test function, reached by the scan) -> FAIL loudly, never silently invisible. This includes
  module-level `pytestmark = pytest.mark.xfail(..., strict=True)`, a class-level decorator,
  `pytest.param(..., marks=pytest.mark.xfail(..., strict=True))`, an aliased/renamed import
  (`import pytest as pt`, `from pytest import mark`, `from pytest.mark import xfail`), or a
  non-literal `strict=` value (e.g. `strict=_SOME_FLAG`). The census only ever COUNTS the canonical
  shape; the point of this rule is that everything else BLOCKS the gate instead of being silently
  uncounted (`_scan_unrecognized_strict_shapes` below).
- `--check`: regenerates the census in memory and diffs it against the committed
  `docs/xfail-census.json` AND the generator-managed region of `PLANS.md`; either drift -> non-zero
  exit with a precise unified diff. Never silently passes on a stale ledger.
- `--write`: regenerates and overwrites both files. Refuses to write anything if classification or
  parsing produced violations (an invalid census must never be committed, generated or not).

Parsing
-------
AST-based (`ast.parse` + `ast.walk`), not regex over source text — this makes marker discovery
inherently "multiline-safe": a decorator call is one node in the tree no matter how its keyword
arguments wrap across lines. A `reason=` value is resolved as either a bare NAME (the common case —
`reason=_FOO_REASON`, whose constant name is what `REASON_CLASSIFICATION` keys on) or a string
literal (parsed without error, but unclassifiable by name — it fails the same "classify at birth"
gate as an unmapped constant, which is the correct fail-closed answer: name the reason).

PLANS.md region
----------------
Two small HTML-comment-bounded regions inside the "Censo de strict-xfail" sentence (§0.5.3) —
`xfail-census:total` (just the current total, appended to the hand-maintained history chain
`95 -> 36 -> 24 -> ...`) and `xfail-census:breakdown` (the per-classification `LABEL x=N` clause).
Everything else in that sentence — the history chain itself, the PR-attribution narrative, the
"censo passa a ser GERADO em CI" sentence — is regular prose, untouched by this script, editable by
hand like any other planning-doc text. Only the two number-carrying spans are generator-owned.

Usage
-----
    python scripts/ci/generate_xfail_census.py --check     # CI gate (also the default, no flags)
    python scripts/ci/generate_xfail_census.py --write      # regenerate docs/xfail-census.json + PLANS.md
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TESTS_DIR = "tests/integration/processes"
DEFAULT_CENSUS_PATH = "docs/xfail-census.json"
DEFAULT_PLANS_PATH = "PLANS.md"

TOTAL_BEGIN = "<!-- xfail-census:total:begin -->"
TOTAL_END = "<!-- xfail-census:total:end -->"
BREAKDOWN_BEGIN = "<!-- xfail-census:breakdown:begin -->"
BREAKDOWN_END = "<!-- xfail-census:breakdown:end -->"

# ---------------------------------------------------------------------------
# Classification — explicit, committed mapping (constant NAME -> classification).
#
# `HUMAN_CEILING_CLASSES` are today's six live buckets (2026-08-10 census, PLANS.md §0.5.3):
# TISS-XSD SME (14, ans_submit) / LGPD-DPO (3, lgpd_dsr) / cred guard-shape T-E (2, cred) /
# auth D-07 (1) / reembolso D-07 (1) / adequacao RN259 (1) — every one of them a governance or
# regulatory ceiling, not an engineering gap (PLANS.md §0.5.3/§0.8 preamble: "censo atual já é 100%
# teto-humano"). `AUTOMATABLE_CLASSES` are reserved for a FUTURE xfail that genuinely is an
# engineering gap the next agent could close — P0 (blocking), P1, P2, mirroring the priority scale
# already used in PLANS.md §0.8's weakness table. A marker classified P0-P2 is exactly the kind of
# thing item-9 used to flip; nothing here special-cases that beyond making the bucket nameable.
# ---------------------------------------------------------------------------

HUMAN_CEILING_CLASSES: frozenset[str] = frozenset(
    {
        "TISS_XSD_SME",
        "LGPD_DPO",
        "CRED_GUARD_SHAPE_TE",
        "AUTH_D07",
        "REEMBOLSO_D07",
        "ADEQUACAO_RN259",
    }
)
AUTOMATABLE_CLASSES: frozenset[str] = frozenset({"P0", "P1", "P2"})
VALID_CLASSES: frozenset[str] = HUMAN_CEILING_CLASSES | AUTOMATABLE_CLASSES

#: Display label used when rendering the PLANS.md breakdown clause — matches the prose already in
#: PLANS.md §0.5.3 ("TISS-XSD SME", "DPO" generalised to "LGPD/DPO", "cred T-E" spelled out, and the
#: single "D-07 x2" split into its two distinct suites since that's the real per-marker granularity).
DISPLAY_LABEL: dict[str, str] = {
    "TISS_XSD_SME": "TISS-XSD SME",
    "LGPD_DPO": "LGPD/DPO",
    "CRED_GUARD_SHAPE_TE": "cred guard-shape T-E",
    "AUTH_D07": "auth D-07",
    "REEMBOLSO_D07": "reembolso D-07",
    "ADEQUACAO_RN259": "adequacao RN259",
    "P0": "P0 (automatable)",
    "P1": "P1 (automatable)",
    "P2": "P2 (automatable)",
}

#: Deterministic rendering order for the PLANS.md breakdown clause. Matches the order the six live
#: classes are already listed in PLANS.md §0.8's own description of this gate.
CLASS_ORDER: tuple[str, ...] = (
    "TISS_XSD_SME",
    "LGPD_DPO",
    "CRED_GUARD_SHAPE_TE",
    "AUTH_D07",
    "REEMBOLSO_D07",
    "ADEQUACAO_RN259",
    "P0",
    "P1",
    "P2",
)

#: THE mapping. Every `_*_REASON` constant any strict-xfail marker in the tree cites must appear
#: here, and every constant named here must still exist in the tree (`check_stale_entries` below) —
#: both directions fail closed. Re-derived from the tree 2026-08-10, independently of PLANS.md's
#: prose (which this script now generates FROM this mapping, not the other way around).
REASON_CLASSIFICATION: dict[str, str] = {
    "_NOTIFY_REGULATORIO_GAP_REASON": "TISS_XSD_SME",
    "_LGPD_SEND_RESPONSE_DPO_MERIT_GATE_REASON": "LGPD_DPO",
    "_CRED_GUARD_SHAPE_MISMATCH_REASON": "CRED_GUARD_SHAPE_TE",
    "_AUTH_CEILING_D07_REASON": "AUTH_D07",
    "_REEMBOLSO_CEILING_D07_REASON": "REEMBOLSO_D07",
    "_ADEQUACAO_GAP_RULE_ORDER_INVERSION_REASON": "ADEQUACAO_RN259",
}

assert set(REASON_CLASSIFICATION.values()) <= VALID_CLASSES, (
    "REASON_CLASSIFICATION references a classification outside HUMAN_CEILING_CLASSES|AUTOMATABLE_CLASSES"
)

#: `test_sp_op_<suite>_<NNN>.py` -> `<suite>` (e.g. `test_sp_op_ans_submit_001.py` -> `ans_submit`).
#: Falls back to the bare stem (minus a leading `test_`) for any file that doesn't match the
#: convention — never raises, never drops a file from the per-suite breakdown.
_SUITE_RE = re.compile(r"^test_sp_op_(?P<suite>.+)_\d+$")


def suite_name(file_rel_path: str) -> str:
    stem = Path(file_rel_path).stem
    match = _SUITE_RE.match(stem)
    if match:
        return match.group("suite")
    return stem.removeprefix("test_")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str

    def render(self) -> str:
        return f"[{self.code}] {self.detail}"


@dataclass(frozen=True)
class RawMarker:
    """One `@pytest.mark.xfail(reason=..., strict=True)` decorator found in the tree."""

    file: str
    line: int
    test: str
    reason_constant: str | None  # None when `reason=` was a string literal, not a bare NAME
    reason_kind: str  # "constant" | "literal"


@dataclass(frozen=True)
class MarkerRecord:
    """A `RawMarker` that classified successfully — what actually lands in the census."""

    file: str
    line: int
    test: str
    reason_constant: str
    classification: str


# ---------------------------------------------------------------------------
# AST parsing (multiline-safe by construction)
# ---------------------------------------------------------------------------


def _dotted_name(node: ast.expr) -> str | None:
    """Render a `Name`/`Attribute` chain (e.g. `pytest.mark.xfail`) as a dotted string, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _is_literal_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_literal_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_suspect_xfail_call(node: ast.expr) -> bool:
    """True for any `Call` whose callee LOOKS like it could be an xfail-marker construction.

    Deliberately broad and name-based, not import-resolution-based: `func.attr == "xfail"` on ANY
    attribute chain (`pytest.mark.xfail`, `pt.mark.xfail` after `import pytest as pt`, `mark.xfail`
    after `from pytest import mark`, ...) or a bare `Name` `xfail` (`from pytest.mark import xfail`).
    Static import-alias resolution is out of scope on purpose — a name-shape heuristic that
    over-flags an unrelated `.xfail(...)` call fails closed (loud violation, human resolves it);
    under-flagging a real strict-xfail marker is the actual bug (W-2/MAJOR-2) this exists to close.
    """
    func = node.func if isinstance(node, ast.Call) else None
    if isinstance(func, ast.Attribute):
        return func.attr == "xfail"
    if isinstance(func, ast.Name):
        return func.id == "xfail"
    return False


def _scan_unrecognized_strict_shapes(tree: ast.Module, rel: str, canonical_ids: set[int]) -> list[Violation]:
    """Fail-closed sweep: ANY strict-ish xfail marker construction not already recognized as the one
    sanctioned canonical shape (a `pytest.mark.xfail(reason=<NAME>, strict=True)` decorator directly
    on a `def`/`async def`, tracked via `canonical_ids`) becomes a loud VIOLATION instead of being
    silently invisible to the census — module-level `pytestmark = pytest.mark.xfail(...)`, a
    class-level decorator, `pytest.param(..., marks=pytest.mark.xfail(...))`, an aliased/renamed
    import, and a non-literal `strict=` value on an otherwise-canonical decorator are all caught this
    way (MAJOR-2). A call is "strict-ish" here whenever it is `_is_suspect_xfail_call` AND carries a
    `strict=` keyword that is anything other than the literal `False`, OR carries a `**kwargs`-style
    keyword unpack (arg name unknown statically — ambiguous, so fail closed rather than guess) —
    matching the "unrecognized/ambiguous => FAIL, never invisible" contract. A suspect call with NO
    `strict=` keyword at all (and no `**kwargs` unpack) stays out of scope, unchanged from before:
    a bare `@pytest.mark.xfail(reason=...)` without `strict=` is a different, weaker marker this
    census has never tracked.
    """
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in canonical_ids:
            continue
        if not _is_suspect_xfail_call(node):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        has_star_kwargs = any(kw.arg is None for kw in node.keywords)
        strict_node = kwargs.get("strict")
        if strict_node is None and not has_star_kwargs:
            continue  # no `strict=` at all — out of scope, same as the pre-existing canonical path
        if strict_node is not None and _is_literal_false(strict_node):
            continue  # explicit `strict=False` — sanctioned non-strict marker, not this census's concern
        violations.append(
            Violation(
                "unrecognized-strict-xfail-shape",
                f"{rel}:{node.lineno}: a strict-ish xfail marker construction was found that does NOT "
                "match the sanctioned canonical shape (`@pytest.mark.xfail(reason=<NAME_CONST>, "
                "strict=True)` as a decorator directly on a `def`/`async def` test function). Either "
                "normalize this marker to the canonical shape, or — if this shape is intentional — "
                "extend scripts/ci/generate_xfail_census.py deliberately to recognize it. A "
                "strict-xfail marker must never be invisible to this census.",
            )
        )
    return violations


def parse_strict_xfail_markers(path: Path, repo_root: Path) -> tuple[list[RawMarker], list[Violation]]:
    """AST-walk one test module for strict-xfail markers on `def`/`async def` test functions.

    Only decorators that resolve to `pytest.mark.xfail(...)` with an explicit `strict=True` keyword
    count — an xfail without `strict=True` is a different (weaker) marker this census does not
    track, matching the ground truth confirmed 2026-08-10 (every strict-xfail marker in this tree is
    a single-line `@pytest.mark.xfail(reason=<CONST>, strict=True)`; the AST walk handles a
    multi-line decorator identically, since wrapping is a lexer/formatting detail the parser already
    normalises away).

    A second pass (`_scan_unrecognized_strict_shapes`, MAJOR-2) then walks EVERY `Call` node in the
    module — not just decorators on `def`/`async def` — and fails closed on anything that looks like
    a strict-ish xfail marker construction outside this one canonical shape, so a marker moved to a
    module-level `pytestmark`, a class decorator, a `pytest.param(marks=...)`, an aliased import, or
    a non-literal `strict=` value BLOCKS the gate instead of silently vanishing from the count.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [Violation("file-unreadable", f"{path}: {exc}")]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [], [Violation("file-unparseable", f"{path}: {exc}")]

    rel = path.relative_to(repo_root).as_posix()
    markers: list[RawMarker] = []
    violations: list[Violation] = []
    canonical_ids: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if _dotted_name(decorator.func) != "pytest.mark.xfail":
                continue
            kwargs = {kw.arg: kw.value for kw in decorator.keywords if kw.arg is not None}
            strict_node = kwargs.get("strict")
            if strict_node is None or not _is_literal_true(strict_node):
                continue  # not strict (or non-literal strict=) — out of this census's scope; a
                # non-literal `strict=` on this exact dotted name is still caught below as an
                # unrecognized shape, since it is NOT added to `canonical_ids` here.

            canonical_ids.add(id(decorator))

            reason_node = kwargs.get("reason")
            if reason_node is None:
                violations.append(
                    Violation(
                        "marker-no-reason",
                        f"{rel}:{decorator.lineno} ({node.name}): strict=True xfail has no `reason=` kwarg",
                    )
                )
                continue

            if isinstance(reason_node, ast.Name):
                markers.append(
                    RawMarker(
                        file=rel,
                        line=decorator.lineno,
                        test=node.name,
                        reason_constant=reason_node.id,
                        reason_kind="constant",
                    )
                )
            elif isinstance(reason_node, ast.Constant) and isinstance(reason_node.value, str):
                markers.append(
                    RawMarker(
                        file=rel,
                        line=decorator.lineno,
                        test=node.name,
                        reason_constant=None,
                        reason_kind="literal",
                    )
                )
            else:
                violations.append(
                    Violation(
                        "marker-reason-unresolvable",
                        f"{rel}:{decorator.lineno} ({node.name}): `reason=` is neither a bare NAME "
                        "constant nor a string literal — cannot classify",
                    )
                )

    violations.extend(_scan_unrecognized_strict_shapes(tree, rel, canonical_ids))

    return markers, violations


def module_level_names(tree: ast.Module) -> set[str]:
    """Every top-level `NAME = ...` / `NAME: T = ...` assignment target in a module."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@dataclass(frozen=True)
class ScanResult:
    markers: list[RawMarker]
    violations: list[Violation]
    assigned_names: set[str]
    files_scanned: list[str]


def scan_tree(tests_dir: Path, repo_root: Path) -> ScanResult:
    markers: list[RawMarker] = []
    violations: list[Violation] = []
    assigned_names: set[str] = set()
    files_scanned: list[str] = []

    if not tests_dir.is_dir():
        return ScanResult(
            [], [Violation("tests-dir-missing", f"directory not found: {tests_dir}")], set(), []
        )

    for path in sorted(tests_dir.rglob("test_*.py")):
        files_scanned.append(path.relative_to(repo_root).as_posix())
        file_markers, file_violations = parse_strict_xfail_markers(path, repo_root)
        markers.extend(file_markers)
        violations.extend(file_violations)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue  # already reported above
        assigned_names |= module_level_names(tree)

    return ScanResult(markers, violations, assigned_names, files_scanned)


# ---------------------------------------------------------------------------
# Classification (fail-closed both directions)
# ---------------------------------------------------------------------------


def classify_markers(
    markers: list[RawMarker], classification: dict[str, str] = REASON_CLASSIFICATION
) -> tuple[list[MarkerRecord], list[Violation]]:
    violations: list[Violation] = []
    records: list[MarkerRecord] = []
    for marker in markers:
        if marker.reason_constant is None:
            violations.append(
                Violation(
                    "unclassified-literal-reason",
                    f"{marker.file}:{marker.line} ({marker.test}): literal-string `reason=` has no "
                    "constant name to classify by — name the reason as a module constant and add a "
                    "REASON_CLASSIFICATION entry",
                )
            )
            continue
        cls = classification.get(marker.reason_constant)
        if cls is None:
            violations.append(
                Violation(
                    "unclassified-constant",
                    f"{marker.file}:{marker.line} ({marker.test}): reason constant "
                    f"{marker.reason_constant!r} has no REASON_CLASSIFICATION entry — new "
                    "strict-xfails must be classified at birth",
                )
            )
            continue
        if cls not in VALID_CLASSES:
            violations.append(
                Violation(
                    "invalid-classification",
                    f"{marker.file}:{marker.line} ({marker.test}): REASON_CLASSIFICATION maps "
                    f"{marker.reason_constant!r} to {cls!r}, which is not in HUMAN_CEILING_CLASSES "
                    "or AUTOMATABLE_CLASSES",
                )
            )
            continue
        records.append(
            MarkerRecord(
                file=marker.file,
                line=marker.line,
                test=marker.test,
                reason_constant=marker.reason_constant,
                classification=cls,
            )
        )
    return records, violations


def check_stale_classification_entries(
    assigned_names: set[str], classification: dict[str, str] = REASON_CLASSIFICATION
) -> list[Violation]:
    """A REASON_CLASSIFICATION entry naming a constant absent from the scanned tree is stale."""
    return [
        Violation(
            "stale-classification-entry",
            f"REASON_CLASSIFICATION entry {constant!r} -> {classification[constant]!r} does not "
            f"correspond to any module-level assignment under {DEFAULT_TESTS_DIR} — the constant "
            "was renamed or removed; update or delete this entry",
        )
        for constant in sorted(classification)
        if constant not in assigned_names
    ]


# ---------------------------------------------------------------------------
# Census construction + serialization
# ---------------------------------------------------------------------------


def build_census(records: list[MarkerRecord], tests_dir_rel: str) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: (r.file, r.line))

    per_suite: dict[str, int] = {}
    per_classification: dict[str, int] = {}
    for record in ordered:
        suite = suite_name(record.file)
        per_suite[suite] = per_suite.get(suite, 0) + 1
        per_classification[record.classification] = per_classification.get(record.classification, 0) + 1

    return {
        "generated_by": "scripts/ci/generate_xfail_census.py",
        "tests_dir": tests_dir_rel,
        "total": len(ordered),
        "per_suite": dict(sorted(per_suite.items())),
        "per_classification": dict(sorted(per_classification.items())),
        "markers": [
            {
                "file": r.file,
                "line": r.line,
                "test": r.test,
                "reason_constant": r.reason_constant,
                "classification": r.classification,
            }
            for r in ordered
        ],
    }


def render_census_json(census: dict[str, Any]) -> str:
    return json.dumps(census, indent=2, ensure_ascii=False) + "\n"


def render_total_fragment(total: int) -> str:
    return str(total)


def render_breakdown_fragment(per_classification: dict[str, int]) -> str:
    parts = [
        f"{DISPLAY_LABEL.get(cls, cls)} ×{per_classification[cls]}"
        for cls in CLASS_ORDER
        if per_classification.get(cls)
    ]
    # Safety net only — CLASS_ORDER is a superset of VALID_CLASSES, and classify_markers() already
    # refuses anything outside VALID_CLASSES, so this branch should be unreachable in practice.
    known = set(CLASS_ORDER)
    for cls in sorted(set(per_classification) - known):
        parts.append(f"{cls} ×{per_classification[cls]}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# PLANS.md managed-region rewrite
# ---------------------------------------------------------------------------


def _replace_region(text: str, begin: str, end: str, replacement: str) -> tuple[str, list[Violation]]:
    begin_count = text.count(begin)
    end_count = text.count(end)
    if begin_count == 0 or end_count == 0:
        return text, [Violation("plans-marker-missing", f"could not find {begin!r}/{end!r} in PLANS.md")]
    if begin_count > 1 or end_count > 1:
        return text, [
            Violation(
                "plans-marker-ambiguous",
                f"{begin!r}/{end!r} must each appear exactly once in PLANS.md "
                f"(found {begin_count}/{end_count})",
            )
        ]
    begin_idx = text.index(begin)
    end_idx = text.index(end)
    if end_idx < begin_idx:
        return text, [Violation("plans-marker-order", f"{end!r} appears before {begin!r} in PLANS.md")]
    content_start = begin_idx + len(begin)
    return text[:content_start] + replacement + text[end_idx:], []


def apply_plans_updates(
    text: str, total_fragment: str, breakdown_fragment: str
) -> tuple[str, list[Violation]]:
    violations: list[Violation] = []
    text, region_violations = _replace_region(text, TOTAL_BEGIN, TOTAL_END, total_fragment)
    violations.extend(region_violations)
    text, region_violations = _replace_region(text, BREAKDOWN_BEGIN, BREAKDOWN_END, breakdown_fragment)
    violations.extend(region_violations)
    return text, violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_xfail_census",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate/verify the committed strict-xfail census over tests/integration/processes/ "
            "(PLANS.md Hardening §0.8 Onda 0). Fail-closed: an unclassified or stale "
            "REASON_CLASSIFICATION entry fails BOTH modes, never just weakens the count."
        ),
        epilog=(
            "modes\n"
            "  --check (default)  regenerate in memory; diff against docs/xfail-census.json AND the\n"
            "                     generator-managed region of PLANS.md; non-zero exit + unified diff\n"
            "                     on any drift.\n"
            "  --write            regenerate and overwrite both files.\n"
        ),
    )
    parser.add_argument("--tests-dir", default=DEFAULT_TESTS_DIR, help=f"default: {DEFAULT_TESTS_DIR}")
    parser.add_argument("--census-path", default=DEFAULT_CENSUS_PATH, help=f"default: {DEFAULT_CENSUS_PATH}")
    parser.add_argument("--plans-path", default=DEFAULT_PLANS_PATH, help=f"default: {DEFAULT_PLANS_PATH}")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root for relative paths.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Verify only (default if neither flag given).")
    mode.add_argument("--write", action="store_true", help="Regenerate and overwrite both files.")
    return parser


def _resolve(repo_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    tests_dir = _resolve(repo_root, args.tests_dir)
    census_path = _resolve(repo_root, args.census_path)
    plans_path = _resolve(repo_root, args.plans_path)

    scan = scan_tree(tests_dir, repo_root)
    records, classify_violations = classify_markers(scan.markers)
    stale_violations = check_stale_classification_entries(scan.assigned_names)
    violations = [*scan.violations, *classify_violations, *stale_violations]

    if violations:
        print("[xfail-census] FAIL — cannot generate a trustworthy census:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1

    census = build_census(records, args.tests_dir)
    census_json = render_census_json(census)
    total_fragment = render_total_fragment(census["total"])
    breakdown_fragment = render_breakdown_fragment(census["per_classification"])

    if args.write:
        census_path.parent.mkdir(parents=True, exist_ok=True)
        census_path.write_text(census_json, encoding="utf-8")

        if not plans_path.is_file():
            print(f"[xfail-census] FAIL: {plans_path} not found", file=sys.stderr)
            return 1
        plans_text = plans_path.read_text(encoding="utf-8")
        new_plans_text, plans_violations = apply_plans_updates(plans_text, total_fragment, breakdown_fragment)
        if plans_violations:
            for violation in plans_violations:
                print(f"[xfail-census] FAIL: {violation.render()}", file=sys.stderr)
            return 1
        if new_plans_text != plans_text:
            plans_path.write_text(new_plans_text, encoding="utf-8")

        print(
            f"[xfail-census] WRITE: total={census['total']} per_suite={census['per_suite']} "
            f"-> {census_path}, {plans_path}"
        )
        return 0

    # Default (and --check): verify only, never mutate.
    exit_code = 0

    if not census_path.is_file():
        print(f"[xfail-census] FAIL: {census_path} does not exist — run with --write", file=sys.stderr)
        exit_code = 1
    else:
        committed_json = census_path.read_text(encoding="utf-8")
        if committed_json != census_json:
            print(f"[xfail-census] FAIL: {census_path} is stale vs the tree:", file=sys.stderr)
            sys.stderr.writelines(
                difflib.unified_diff(
                    committed_json.splitlines(keepends=True),
                    census_json.splitlines(keepends=True),
                    fromfile=f"{census_path} (committed)",
                    tofile=f"{census_path} (derived)",
                )
            )
            exit_code = 1

    if not plans_path.is_file():
        print(f"[xfail-census] FAIL: {plans_path} not found", file=sys.stderr)
        exit_code = 1
    else:
        plans_text = plans_path.read_text(encoding="utf-8")
        expected_plans_text, plans_violations = apply_plans_updates(
            plans_text, total_fragment, breakdown_fragment
        )
        if plans_violations:
            for violation in plans_violations:
                print(f"[xfail-census] FAIL: {violation.render()}", file=sys.stderr)
            exit_code = 1
        elif expected_plans_text != plans_text:
            print(f"[xfail-census] FAIL: {plans_path} xfail-census region is stale:", file=sys.stderr)
            sys.stderr.writelines(
                difflib.unified_diff(
                    plans_text.splitlines(keepends=True),
                    expected_plans_text.splitlines(keepends=True),
                    fromfile=f"{plans_path} (committed)",
                    tofile=f"{plans_path} (derived)",
                )
            )
            exit_code = 1

    if exit_code == 0:
        print(
            f"[xfail-census] PASS: total={census['total']} matches {census_path} and {plans_path} "
            f"(per_suite={census['per_suite']})"
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
