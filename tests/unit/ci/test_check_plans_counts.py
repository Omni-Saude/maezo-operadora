"""Unit tests for the PLANS.md count-reconciliation gate (AF-06).

Two layers:

1. **Pure-core** tests drive `count_numbered_adr_files` / `count_all_adr_dir_files` /
   `count_tool_registry_lines` / `evaluate` against synthetic `docs/adr/`-shaped trees and
   synthetic PLANS.md text written to a tmp path — RED on a stale claim (any of the 4 patterns),
   GREEN on a reconciled one, and non-vacuity for "zero claims found" (still PASS, but the
   explicit zero-matches signal is asserted).
2. **Real-tree** test runs the gate against the SHIPPED `PLANS.md` + `docs/adr/` +
   `tool_registry.py` and asserts it passes non-vacuously (>=1 claim actually checked) — the
   regression proof that the fix landed for real, not just in a fixture.
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_plans_counts import (
    Finding,
    TOOL_REGISTRY_LINES_TOLERANCE,
    count_all_adr_dir_files,
    count_numbered_adr_files,
    count_tool_registry_lines,
    evaluate,
    main,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_PLANS = _REPO_ROOT / "PLANS.md"
_SHIPPED_ADR_DIR = _REPO_ROOT / "docs" / "adr"
_SHIPPED_TOOL_REGISTRY = _REPO_ROOT / "src" / "maezo" / "gateway" / "tool_registry.py"


def _write_adr_tree(
    tmp_path: Path, numbered_count: int, *, with_readme: bool = True, with_template: bool = True
) -> Path:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for i in range(1, numbered_count + 1):
        (adr_dir / f"{i:04d}-decisao.md").write_text("# ADR\n", encoding="utf-8")
    if with_readme:
        (adr_dir / "README.md").write_text("# index\n", encoding="utf-8")
    if with_template:
        (adr_dir / "template.md").write_text("# template\n", encoding="utf-8")
    return adr_dir


def _write_plans(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "PLANS.md"
    path.write_text(body, encoding="utf-8")
    return path


def _write_tool_registry(tmp_path: Path, line_count: int, *, rel: str = "src/maezo/gateway/tool_registry.py") -> Path:
    """A synthetic `tool_registry.py` with EXACTLY `line_count` newline-terminated lines (`wc -l`
    semantics) — one line per `pass`, so the line count is unambiguous regardless of content."""
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pass\n" * line_count, encoding="utf-8")
    return path


# =================================================================================================
# count_numbered_adr_files / count_all_adr_dir_files (pure)
# =================================================================================================


class TestCountAdrFiles:
    def test_counts_only_numbered_files(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        assert count_numbered_adr_files(adr_dir) == 5

    def test_readme_and_template_are_excluded_from_the_numbered_count(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 3, with_readme=True, with_template=True)
        assert count_numbered_adr_files(adr_dir) == 3

    def test_total_count_includes_readme_and_template(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 3, with_readme=True, with_template=True)
        assert count_all_adr_dir_files(adr_dir) == 5  # 3 numbered + README + template

    def test_total_count_without_readme_or_template(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 4, with_readme=False, with_template=False)
        assert count_all_adr_dir_files(adr_dir) == 4

    def test_a_non_markdown_file_is_never_counted(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 2)
        (adr_dir / "notes.txt").write_text("not markdown\n", encoding="utf-8")
        assert count_numbered_adr_files(adr_dir) == 2
        assert count_all_adr_dir_files(adr_dir) == 4  # 2 numbered + README + template, not notes.txt

    def test_a_non_4_digit_prefixed_file_is_not_numbered(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 2)
        (adr_dir / "42-shortprefix.md").write_text("# not 4 digits\n", encoding="utf-8")
        assert count_numbered_adr_files(adr_dir) == 2


# =================================================================================================
# evaluate (pure, given text + a real adr_dir path)
# =================================================================================================


class TestEvaluate:
    def test_a_matching_adrs_numerados_claim_is_not_a_finding(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        plans = _write_plans(tmp_path, "docs/adr/ (5 ADRs numerados, 7 arquivos incl. README+template)\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert findings == []
        assert matches_checked == 2  # "5 ADRs numerados" + "7 arquivos incl. README+template"

    def test_a_stale_adrs_numerados_claim_is_a_finding(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        plans = _write_plans(tmp_path, "docs/adr/ (3 ADRs numerados)\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert matches_checked == 1
        assert findings == [Finding(line_no=1, claim_shape="ADRs numerados", claimed=3, real=5)]

    def test_a_stale_adrs_nao_claim_is_a_finding(self, tmp_path: Path) -> None:
        # The exact AF-06 repro shape: "32 ADRs (não 24)".
        adr_dir = _write_adr_tree(tmp_path, 48)
        plans = _write_plans(tmp_path, "| M1 | 32 ADRs (não 24); ADR-0030 adicionado. |\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert matches_checked == 1
        assert findings == [Finding(line_no=1, claim_shape="ADRs (não ...)", claimed=32, real=48)]

    def test_a_matching_adrs_nao_claim_is_not_a_finding(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 48)
        plans = _write_plans(tmp_path, "| M1 | 48 ADRs (não 24); ainda Proposed. |\n")
        findings, _matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert findings == []

    def test_a_stale_arquivos_template_claim_is_a_finding(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 48)  # 48 + README + template = 50
        plans = _write_plans(tmp_path, "docs/adr/ (48 ADRs numerados, 41 arquivos incl. README+template)\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert matches_checked == 2
        assert findings == [
            Finding(line_no=1, claim_shape="arquivos incl. README+template", claimed=41, real=50)
        ]

    def test_line_number_is_1_indexed_and_points_at_the_right_line(self, tmp_path: Path) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        plans = _write_plans(tmp_path, "line one\nline two\n3 ADRs numerados on line three\n")
        findings, _matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert findings[0].line_no == 3

    def test_zero_claims_found_is_not_a_finding_but_is_visible_in_matches_checked(
        self, tmp_path: Path
    ) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        plans = _write_plans(tmp_path, "This document mentions no ADR counts at all.\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert findings == []
        assert matches_checked == 0  # non-vacuity: distinguishable from "checked and all passed"

    def test_a_historical_number_not_matching_any_of_the_three_patterns_is_ignored(
        self, tmp_path: Path
    ) -> None:
        # Regression guard for over-reach: a plain historical count ("14 ADRs revisados") must
        # NEVER be treated as a live claim — this gate's scope is the three named phrasings only.
        adr_dir = _write_adr_tree(tmp_path, 48)
        plans = _write_plans(tmp_path, "14 ADRs (0001-0012, 0019-0020) revisados e promovidos\n")
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert findings == []
        assert matches_checked == 0

    def test_multiple_occurrences_of_the_same_pattern_are_each_checked_independently(
        self, tmp_path: Path
    ) -> None:
        adr_dir = _write_adr_tree(tmp_path, 5)
        plans = _write_plans(
            tmp_path, "First mention: 5 ADRs numerados.\nSecond mention: 3 ADRs numerados.\n"
        )
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), adr_dir)
        assert matches_checked == 2
        assert len(findings) == 1
        assert findings[0].line_no == 2
        assert findings[0].claimed == 3


# =================================================================================================
# main: end-to-end CLI wrapper
# =================================================================================================


class TestMainEndToEnd:
    def test_reconciled_plans_passes(self, tmp_path: Path) -> None:
        _write_adr_tree(tmp_path, 5)
        _write_tool_registry(tmp_path, 100)
        _write_plans(tmp_path, "docs/adr/ (5 ADRs numerados, 7 arquivos incl. README+template)\n")
        exit_code = main([], repo_root=tmp_path)
        assert exit_code == 0

    def test_stale_plans_fails(self, tmp_path: Path) -> None:
        _write_adr_tree(tmp_path, 5)
        _write_tool_registry(tmp_path, 100)
        _write_plans(tmp_path, "docs/adr/ (3 ADRs numerados)\n")
        exit_code = main([], repo_root=tmp_path)
        assert exit_code == 1

    def test_missing_adr_dir_fails(self, tmp_path: Path) -> None:
        _write_plans(tmp_path, "no adr dir here\n")
        exit_code = main([], repo_root=tmp_path)
        assert exit_code == 1

    def test_missing_tool_registry_fails(self, tmp_path: Path) -> None:
        _write_adr_tree(tmp_path, 5)
        _write_plans(tmp_path, "docs/adr/ (5 ADRs numerados)\n")
        # No `tool_registry.py` written at the default path this time.
        exit_code = main([], repo_root=tmp_path)
        assert exit_code == 1

    def test_missing_plans_file_fails(self, tmp_path: Path) -> None:
        _write_adr_tree(tmp_path, 5)
        _write_tool_registry(tmp_path, 100)
        exit_code = main([], repo_root=tmp_path)
        assert exit_code == 1

    def test_custom_paths_via_flags(self, tmp_path: Path) -> None:
        adr_dir = tmp_path / "somewhere" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "0001-x.md").write_text("# x\n", encoding="utf-8")
        tool_registry_path = _write_tool_registry(tmp_path, 100, rel="somewhere/registry.py")
        plans_path = tmp_path / "elsewhere" / "STATUS.md"
        plans_path.parent.mkdir(parents=True)
        plans_path.write_text("1 ADRs numerados\n", encoding="utf-8")

        exit_code = main(
            [
                "--plans",
                str(plans_path),
                "--adr-dir",
                str(adr_dir),
                "--tool-registry",
                str(tool_registry_path),
            ],
            repo_root=tmp_path,
        )
        assert exit_code == 0


# =================================================================================================
# Pattern 4 (AF-06 residual): `gateway/tool_registry.py`, classe `ToolRegistry`, ≈<N> linhas`
# =================================================================================================


class TestToolRegistryLinesPattern:
    def test_count_tool_registry_lines_matches_wc_l_semantics(self, tmp_path: Path) -> None:
        path = _write_tool_registry(tmp_path, 42, rel="registry.py")
        assert count_tool_registry_lines(path) == 42

    def test_claim_within_tolerance_is_not_a_finding(self, tmp_path: Path) -> None:
        tool_registry_path = _write_tool_registry(tmp_path, 100, rel="registry.py")
        # 100 real, tolerance 10% -> up to 110 claimed still passes.
        plans = _write_plans(
            tmp_path, "`gateway/tool_registry.py`, classe `ToolRegistry`, ≈108 linhas em hoje.\n"
        )
        findings, matches_checked = evaluate(
            plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1), tool_registry_path
        )
        assert findings == []
        assert matches_checked == 1

    def test_claim_outside_tolerance_is_a_finding(self, tmp_path: Path) -> None:
        tool_registry_path = _write_tool_registry(tmp_path, 794, rel="registry.py")
        plans = _write_plans(
            tmp_path, "`gateway/tool_registry.py`, classe `ToolRegistry`, ≈540 linhas em hoje.\n"
        )
        findings, matches_checked = evaluate(
            plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1), tool_registry_path
        )
        assert matches_checked == 1
        assert len(findings) == 1
        assert findings[0] == Finding(
            line_no=1, claim_shape="≈N linhas em tool_registry.py", claimed=540, real=794
        )

    def test_tolerance_boundary_is_inclusive(self, tmp_path: Path) -> None:
        # 100 real; 10% tolerance -> exactly 90 is the lower inclusive boundary.
        tool_registry_path = _write_tool_registry(tmp_path, 100, rel="registry.py")
        plans = _write_plans(
            tmp_path, "`gateway/tool_registry.py`, classe `ToolRegistry`, ≈90 linhas em hoje.\n"
        )
        findings, _matches_checked = evaluate(
            plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1), tool_registry_path
        )
        assert findings == []

    def test_just_outside_tolerance_boundary_is_a_finding(self, tmp_path: Path) -> None:
        tool_registry_path = _write_tool_registry(tmp_path, 100, rel="registry.py")
        plans = _write_plans(
            tmp_path, "`gateway/tool_registry.py`, classe `ToolRegistry`, ≈89 linhas em hoje.\n"
        )
        findings, _matches_checked = evaluate(
            plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1), tool_registry_path
        )
        assert len(findings) == 1

    def test_tool_registry_path_none_skips_pattern_4_entirely(self, tmp_path: Path) -> None:
        """Callers that never pass `tool_registry_path` (e.g. `evaluate`'s own ADR-only fixtures
        elsewhere in this file) must not have pattern 4 sprung on them by surprise."""
        plans = _write_plans(
            tmp_path, "`gateway/tool_registry.py`, classe `ToolRegistry`, ≈1 linhas em hoje.\n"
        )
        findings, matches_checked = evaluate(plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1))
        assert findings == []
        assert matches_checked == 0

    def test_unrelated_approx_line_claim_about_another_file_does_not_match(self, tmp_path: Path) -> None:
        """The pattern is anchored on `gateway/tool_registry.py` appearing on the SAME line — an
        unrelated `≈N linhas` claim about some other file must never be swept in."""
        tool_registry_path = _write_tool_registry(tmp_path, 100, rel="registry.py")
        plans = _write_plans(tmp_path, "algum outro arquivo tem ≈9999 linhas, sem relação nenhuma.\n")
        findings, matches_checked = evaluate(
            plans.read_text(encoding="utf-8"), _write_adr_tree(tmp_path, 1), tool_registry_path
        )
        assert findings == []
        assert matches_checked == 0

    def test_tolerance_constant_is_ten_percent(self) -> None:
        # Pinned so a silent widen/narrow of the tolerance is a deliberate, reviewed diff.
        assert TOOL_REGISTRY_LINES_TOLERANCE == 0.10


class TestAgainstTheRealShippedTree:
    """Non-vacuity: the SHIPPED PLANS.md, checked against the SHIPPED docs/adr/ +
    tool_registry.py, passes — proving the fix landed for real (not just in a synthetic fixture)
    and that at least one claim of EACH pattern is actually being checked (never a vacuous
    0-claims PASS on the real file)."""

    def test_shipped_plans_reconciles_with_the_shipped_tree(self) -> None:
        findings, matches_checked = evaluate(
            _SHIPPED_PLANS.read_text(encoding="utf-8"), _SHIPPED_ADR_DIR, _SHIPPED_TOOL_REGISTRY
        )
        assert findings == []
        # Non-vacuity: the two ADR spots (patterns 1-3, at least one match each on the header
        # paragraph) plus the tool_registry.py spot (pattern 4) are all still there.
        assert matches_checked >= 4

    def test_main_against_the_real_repo_root_passes(self) -> None:
        exit_code = main([], repo_root=_REPO_ROOT)
        assert exit_code == 0
