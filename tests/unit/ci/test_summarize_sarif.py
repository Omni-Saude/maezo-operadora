"""Fence: the GHAS-substitute SARIF summary renders the REAL counts, for every lane that uses it.

WHY THIS FENCE EXISTS
---------------------
GitHub Advanced Security is disabled on this repository, so `.github/workflows/security.yml` cannot
show findings in the Security tab. The step summary written by `scripts/ci/summarize_sarif.py` IS the
review surface — if it under-reports, a finding is invisible and the job is still green, which is the
exact "continues green, only blind" failure mode `.github/CODEOWNERS` warns about.

The counting is not trivial in one specific way: codeql-action v4 publishes the query pack's rules
under `tool.extensions[].rules` and leaves `tool.driver.rules` EMPTY (live-observed on this repo's own
PR artifact), while other producers populate `driver.rules`. A summarizer that reads only one of them
silently loses every severity that came from the other — so both are asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.ci.summarize_sarif import main, render, summarize


def _sarif(runs: list[dict]) -> dict:
    return {"version": "2.1.0", "runs": runs}


def test_a_results_own_level_wins_over_the_rules_default() -> None:
    levels, rules, _ = summarize(
        _sarif(
            [
                {
                    "tool": {"driver": {"rules": [{"id": "r1", "defaultConfiguration": {"level": "note"}}]}},
                    "results": [{"ruleId": "r1", "level": "error"}, {"ruleId": "r1"}],
                }
            ]
        )
    )
    assert levels == {"error": 1, "note": 1}
    assert rules == {"r1": 2}


def test_rules_published_under_extensions_are_not_lost() -> None:
    """codeql-action v4 keeps the python-queries rules here with driver.rules empty."""
    levels, _, names = summarize(
        _sarif(
            [
                {
                    "tool": {
                        "driver": {"rules": []},
                        "extensions": [
                            {
                                "rules": [
                                    {
                                        "id": "java/sql-injection",
                                        "name": "Query built from user-controlled sources",
                                        "defaultConfiguration": {"level": "error"},
                                    }
                                ]
                            }
                        ],
                    },
                    "results": [{"ruleId": "java/sql-injection"}],
                }
            ]
        )
    )
    assert levels == {"error": 1}, "a finding whose severity lives under extensions was dropped"
    assert names["java/sql-injection"] == "Query built from user-controlled sources"


def test_a_result_with_no_rule_entry_at_all_still_counts() -> None:
    levels, rules, _ = summarize(_sarif([{"tool": {"driver": {}}, "results": [{"ruleId": "x"}]}]))
    assert levels == {"warning": 1} and rules == {"x": 1}, "an unknown rule must never vanish"


def test_results_from_every_run_are_summed() -> None:
    levels, _, _ = summarize(
        _sarif(
            [
                {"tool": {"driver": {}}, "results": [{"ruleId": "a", "level": "error"}]},
                {"tool": {"driver": {}}, "results": [{"ruleId": "b", "level": "error"}]},
            ]
        )
    )
    assert levels == {"error": 2}


def test_the_rendered_block_names_the_lane_and_the_artifact() -> None:
    levels, rules, names = summarize(
        _sarif([{"tool": {"driver": {}}, "results": [{"ruleId": "a|b", "level": "error"}]}])
    )
    block = render(
        label="codeql / java-kotlin",
        artifact_name="codeql-java-sarif",
        level_counts=levels,
        rule_counts=rules,
        rule_names=names,
    )
    assert "codeql / java-kotlin" in block
    assert "codeql-java-sarif" in block
    assert "| error | 1 |" in block
    assert "Total results in this scan: **1**" in block


def test_an_empty_scan_renders_an_honest_zero() -> None:
    block = render(
        label="l",
        artifact_name="a",
        level_counts=summarize(_sarif([]))[0],
        rule_counts=summarize(_sarif([]))[1],
        rule_names={},
    )
    assert "Total results in this scan: **0**" in block
    assert "| (none) | 0 |" in block


def test_the_cli_appends_to_the_output_file(tmp_path: Path) -> None:
    sarif_path = tmp_path / "java.sarif"
    sarif_path.write_text(
        json.dumps(_sarif([{"tool": {"driver": {}}, "results": [{"ruleId": "a", "level": "error"}]}])),
        encoding="utf-8",
    )
    out = tmp_path / "summary.md"
    out.write_text("previous content\n", encoding="utf-8")

    assert (
        main(
            [
                "--sarif",
                str(sarif_path),
                "--label",
                "codeql / java-kotlin",
                "--artifact-name",
                "codeql-java-sarif",
                "--output",
                str(out),
            ]
        )
        == 0
    )
    written = out.read_text(encoding="utf-8")
    assert written.startswith("previous content\n"), "the step summary must be appended, not replaced"
    assert "codeql / java-kotlin" in written


def test_a_missing_sarif_fails_closed(tmp_path: Path) -> None:
    """A scan that produced no SARIF must never read as a clean scan."""
    with pytest.raises(OSError):
        main(
            [
                "--sarif",
                str(tmp_path / "absent.sarif"),
                "--label",
                "l",
                "--artifact-name",
                "a",
                "--output",
                str(tmp_path / "summary.md"),
            ]
        )
