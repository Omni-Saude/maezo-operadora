"""Unit tests for the alert-runbook-url gate (D12-01-b / R-007).

Two layers:

1. **Pure-core** tests drive `evaluate` against synthetic alert-rules trees written to a tmp
   path — RED on a missing `runbook_url`, RED on a dangling path, RED on a bad anchor, GREEN on a
   real file (with and without a valid anchor).
2. **Real-tree** test runs the gate against the SHIPPED `deploy/observability/alert-rules.yml` and
   asserts it passes non-vacuously (8 alerts, all resolved) — the regression proof that the 8
   annotations landed for real, not just in a fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ci.check_alert_runbook_urls import evaluate

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_ALERT_RULES = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"


def _write_rules(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "alert-rules.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_runbook(tmp_path: Path, relative: str, body: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


# =================================================================================================
# RED: a rule with no runbook_url at all
# =================================================================================================


def test_a_rule_with_no_runbook_url_annotation_fails(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: NoRunbookAtAll
                expr: up == 0
                annotations:
                  summary: "no runbook here"
        """,
    )
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert findings[0].alert == "NoRunbookAtAll"
    assert "runbook_url" in findings[0].reason


def test_a_rule_with_no_annotations_block_at_all_fails(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: NoAnnotationsBlock
                expr: up == 0
        """,
    )
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert findings[0].alert == "NoAnnotationsBlock"


def test_a_rule_with_an_empty_runbook_url_fails(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: EmptyRunbookUrl
                expr: up == 0
                annotations:
                  runbook_url: "   "
        """,
    )
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert findings[0].alert == "EmptyRunbookUrl"


# =================================================================================================
# RED: a runbook_url that points nowhere real
# =================================================================================================


def test_a_dangling_runbook_path_fails(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: DanglingPath
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/alerts/DoesNotExist.md"
        """,
    )
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert findings[0].alert == "DanglingPath"
    assert "NAO existe" in findings[0].reason


def test_a_runbook_url_pointing_at_a_generic_index_is_a_dangling_target_if_it_does_not_exist(
    tmp_path: Path,
) -> None:
    """The gate cannot judge "generic vs specific" semantically, but a nonexistent index still fails."""
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: PointsAtMissingIndex
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/README.md#nonexistent-section"
        """,
    )
    _write_runbook(tmp_path, "docs/runbooks/README.md", "# Index\n\nNo matching section here.\n")
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert "ancora" in findings[0].reason


# =================================================================================================
# RED: a runbook_url with a bad anchor
# =================================================================================================


def test_a_bad_anchor_on_an_existing_file_fails(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: BadAnchor
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/alerts/Real.md#does-not-exist"
        """,
    )
    _write_runbook(
        tmp_path,
        "docs/runbooks/alerts/Real.md",
        "# Real\n\n## Resposta do operador\n\nSteps here.\n",
    )
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert findings[0].alert == "BadAnchor"
    assert "ancora" in findings[0].reason


# =================================================================================================
# GREEN: a real file, with and without an anchor
# =================================================================================================


def test_a_runbook_url_to_a_real_file_with_no_anchor_passes(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: RealFileNoAnchor
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/alerts/Real.md"
        """,
    )
    _write_runbook(tmp_path, "docs/runbooks/alerts/Real.md", "# Real\n\nBody.\n")
    assert evaluate(rules, tmp_path) == []


def test_a_runbook_url_to_a_real_file_with_a_matching_anchor_passes(tmp_path: Path) -> None:
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - alert: RealFileGoodAnchor
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/alerts/Real.md#resposta-do-operador"
        """,
    )
    _write_runbook(
        tmp_path,
        "docs/runbooks/alerts/Real.md",
        "# Real\n\n## Resposta do operador\n\nSteps here.\n",
    )
    assert evaluate(rules, tmp_path) == []


def test_a_record_rule_is_not_required_to_carry_a_runbook_url(tmp_path: Path) -> None:
    """ALERTS-WITHOUT-METRICS-b / R-056: a `record:` rule pages nobody — skip it entirely."""
    rules = _write_rules(
        tmp_path,
        """
        groups:
          - name: g
            rules:
              - record: some_derived_series
                expr: sum(some_other_series)
              - alert: HasRunbook
                expr: up == 0
                annotations:
                  runbook_url: "docs/runbooks/alerts/Real.md"
        """,
    )
    _write_runbook(tmp_path, "docs/runbooks/alerts/Real.md", "# Real\n\nBody.\n")
    assert evaluate(rules, tmp_path) == []


def test_an_empty_rules_file_is_a_vacuity_failure_not_a_silent_pass(tmp_path: Path) -> None:
    """A gate that finds zero `alert:` rules must say so loudly, not report an empty (green) diff."""
    rules = _write_rules(tmp_path, "groups: []\n")
    findings = evaluate(rules, tmp_path)
    assert len(findings) == 1
    assert "nenhuma regra" in findings[0].reason


# =================================================================================================
# Real-tree regression: the shipped file itself
# =================================================================================================


def test_the_shipped_alert_rules_file_passes_non_vacuously() -> None:
    """The real fix: all 8 shipped alerts carry a `runbook_url` that resolves in this repo tree."""
    assert _SHIPPED_ALERT_RULES.is_file(), _SHIPPED_ALERT_RULES
    findings = evaluate(_SHIPPED_ALERT_RULES, _REPO_ROOT)
    assert findings == [], [f.render() for f in findings]


@pytest.mark.parametrize(
    "alert_name",
    [
        "MaezoSLAWorkerLatencyHigh",
        "MaezoSLAAgentErrorRateHigh",
        "MaezoSLAWorkerErrorRateHigh",
        "MaezoWorkerCrashLoop",
        "MaezoAgentCrashLoop",
        "MaezoDeadLetterBacklog",
        "MaezoDeadLetterGrowth",
        "MaezoLifecycleJobFailed",
    ],
)
def test_every_shipped_alert_is_covered_by_the_fixture_list(alert_name: str) -> None:
    """Non-vacuity floor: the 8 known alert names must still be exactly what the file ships."""
    import yaml

    document = yaml.safe_load(_SHIPPED_ALERT_RULES.read_text(encoding="utf-8"))
    names = {rule["alert"] for group in document["groups"] for rule in group["rules"] if "alert" in rule}
    assert alert_name in names, sorted(names)
