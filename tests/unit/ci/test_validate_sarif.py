"""Fence: the SARIF validator is fail-closed on every malformation the consumers can choke on.

WHY THIS FENCE EXISTS
---------------------
W7 7.1 ("prova e2e do caminho SARIF substituto-GHAS") established the SARIF path as three legs —
produce (security.yml lanes), ingest (code-scanning API), review (artifact + ``summarize_sarif``).
``scripts/ci/validate_sarif.py`` is the guard in front of legs 2 and 3: a SARIF that parses but
violates the format must NEVER reach the uploader (where its rejection would be misclassified
against the GHAS-gate message and fail the job via the confusing catch-all branch) nor the summary
renderer (where it would render a silently wrong table). These tests pin the fail-closed contract:
every malformation class below must exit 1 with a diagnostic, and only well-formed SARIF exits 0 —
including the real-world shapes of both producers (CodeQL's rules-under-extensions, Trivy's
zero-result run) so a legitimate artifact is never a false positive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.validate_sarif import main, validate_file


@pytest.fixture()
def sarif_file(tmp_path: Path) -> Path:
    """Factory: write a SARIF document to disk and return its path."""

    def _write(doc: object, name: str = "scan.sarif") -> Path:
        target = tmp_path / name
        target.write_text(json.dumps(doc), encoding="utf-8")
        return target

    return _write


def _run(*argv: str) -> int:
    return main(list(argv))


VALID_MINIMAL = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {"driver": {"name": "Trivy", "rules": []}},
            "results": [],
        }
    ],
}


# ── happy paths ──────────────────────────────────────────────────────────────────────────────


def test_minimal_valid_sarif_passes(sarif_file) -> None:
    assert validate_file(sarif_file(VALID_MINIMAL), None) == []


def test_cli_exit_0_on_valid_file(sarif_file) -> None:
    assert _run("--sarif", str(sarif_file(VALID_MINIMAL))) == 0


def test_trivy_zero_result_shape_is_valid(sarif_file) -> None:
    """A 0-result run (live trivy shape after the IaC backlog fix) must stay a false-positive-free pass."""
    doc = {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "Trivy", "rules": [], "version": "0.74.0"}},
                "results": [],
                "columnKind": "utf16CodeUnits",
            }
        ],
    }
    assert validate_file(sarif_file(doc), None) == []


def test_codeql_rules_under_extensions_shape_is_valid(sarif_file) -> None:
    """codeql-action v4 keeps rules under tool.extensions with driver.rules empty (live-observed)."""
    doc = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {"name": "CodeQL", "rules": []},
                    "extensions": [{"rules": [{"id": "py/path-injection", "defaultConfiguration": {"level": "error"}}]}],
                },
                "results": [
                    {
                        "ruleId": "py/path-injection",
                        "level": "error",
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "src/app/main.py"}, "region": {"startLine": 1}}}
                        ],
                    }
                ],
            }
        ],
    }
    assert validate_file(sarif_file(doc), None) == []


def test_multiple_files_all_valid_exit_0(sarif_file) -> None:
    a = sarif_file(VALID_MINIMAL, "a.sarif")
    b = sarif_file(VALID_MINIMAL, "b.sarif")
    assert _run("--sarif", str(a), "--sarif", str(b)) == 0


# ── negative paths: every malformation class must fail closed ────────────────────────────────


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "never-produced.sarif"
    errors = validate_file(missing, None)
    assert len(errors) == 1 and "file not found" in errors[0]


def test_unparseable_json_fails_closed(sarif_file) -> None:
    target = sarif_file({})
    target.write_text("{not json", encoding="utf-8")
    errors = validate_file(target, None)
    assert len(errors) == 1 and "not valid JSON" in errors[0]


def test_wrong_version_rejected(sarif_file) -> None:
    doc = {**VALID_MINIMAL, "version": "2.0.0"}
    assert any("version" in e for e in validate_file(sarif_file(doc), None))


def test_missing_version_rejected(sarif_file) -> None:
    doc = {"runs": VALID_MINIMAL["runs"]}
    assert any("version" in e for e in validate_file(sarif_file(doc), None))


def test_non_sarif_schema_ref_rejected(sarif_file) -> None:
    doc = {**VALID_MINIMAL, "$schema": "http://json-schema.org/draft-04/schema#"}
    assert any("$schema" in e for e in validate_file(sarif_file(doc), None))


@pytest.mark.parametrize("runs", [None, [], "one run", [{}]])
def test_bad_runs_container_rejected(sarif_file, runs) -> None:
    doc = {"version": "2.1.0", "runs": runs}
    assert validate_file(sarif_file(doc), None), f"runs={runs!r} must not pass"


def test_driver_without_name_rejected(sarif_file) -> None:
    doc = {"version": "2.1.0", "runs": [{"tool": {"driver": {}}, "results": []}]}
    assert any("driver.name" in e for e in validate_file(sarif_file(doc), None))


def test_result_without_ruleid_rejected(sarif_file) -> None:
    doc = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "X"}}, "results": [{"level": "warning"}]}]}
    assert any("ruleId" in e for e in validate_file(sarif_file(doc), None))


def test_result_with_unknown_level_rejected(sarif_file) -> None:
    doc = {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "X"}}, "results": [{"ruleId": "r", "level": "catastrophic"}]}],
    }
    assert any("level" in e for e in validate_file(sarif_file(doc), None))


def test_location_without_artifact_uri_rejected(sarif_file) -> None:
    doc = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "X"}},
                "results": [{"ruleId": "r", "locations": [{"physicalLocation": {"artifactLocation": {}}}]}],
            }
        ],
    }
    assert any("artifactLocation" in e for e in validate_file(sarif_file(doc), None))


def test_cli_exit_1_with_stderr_diagnostic(sarif_file, capsys) -> None:
    doc = {**VALID_MINIMAL, "version": "9.9.9"}
    assert _run("--sarif", str(sarif_file(doc))) == 1
    assert "INVALID SARIF" in capsys.readouterr().err


def test_one_bad_file_fails_the_whole_invocation(sarif_file) -> None:
    good = sarif_file(VALID_MINIMAL, "good.sarif")
    bad = sarif_file({**VALID_MINIMAL, "runs": []}, "bad.sarif")
    assert _run("--sarif", str(good), "--sarif", str(bad)) == 1


# ── --schema mode: full JSON-Schema on top of the structural checks ─────────────────────────


def _schema_file(tmp_path: Path) -> Path:
    """A tiny draft-04 schema requiring `columnKind`, which real producers emit but the structural
    checks don't demand — proving the schema leg adds checks without duplicating them."""
    schema = {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "required": ["version", "runs"],
        "properties": {
            "version": {"enum": ["2.1.0"]},
            "runs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["tool", "columnKind"],
                },
            },
        },
    }
    target = tmp_path / "schema.json"
    target.write_text(json.dumps(schema), encoding="utf-8")
    return target


def test_schema_mode_flags_what_structure_ignores(sarif_file, tmp_path) -> None:
    doc = {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "X"}}, "results": []}],  # no columnKind: structurally fine
    }
    errors = validate_file(sarif_file(doc), _schema_file(tmp_path))
    assert errors and "schema:" in errors[0] and "columnKind" in errors[0]


def test_schema_mode_passes_a_conformant_file(sarif_file, tmp_path) -> None:
    doc = {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "X"}}, "results": [], "columnKind": "utf16CodeUnits"}],
    }
    assert validate_file(sarif_file(doc), _schema_file(tmp_path)) == []


def test_broken_schema_file_is_a_caller_error(sarif_file, tmp_path) -> None:
    broken = tmp_path / "broken-schema.json"
    broken.write_text(json.dumps({"type": "not-a-real-type"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        validate_file(sarif_file(VALID_MINIMAL), broken)
