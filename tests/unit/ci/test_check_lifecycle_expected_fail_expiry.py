"""Unit tests for the R-040/SC-07 lifecycle expected-fail-marker expiry gate.

Two layers:

1. **Pure-core** tests drive `evaluate` against synthetic `values.yaml`/`alert-rules.yml` trees
   written to a tmp path — RED on a vanished/malformed/expired date, RED on the alert expression
   dropping the marker reference, GREEN on an in-date marker that the expression still references.
2. **Real-tree** test runs the gate against the SHIPPED `deploy/helm/maezo-tenant/values.yaml` +
   `deploy/observability/alert-rules.yml` and asserts it passes as of a pinned "today" well before
   the ratified 2026-11-11 deadline — the regression proof that R-040 landed for real.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.ci.check_lifecycle_expected_fail_expiry import evaluate

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_VALUES = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant" / "values.yaml"
_SHIPPED_ALERT_RULES = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"

_VALID_EXPR = """
groups:
  - name: maezo_lifecycle
    rules:
      - alert: MaezoLifecycleJobFailed
        expr: |
          kube_job_status_failed{job_name=~"lifecycle-.*"} > 0
          unless on (job_name) (
            kube_job_annotations{annotation_maezo_io_expected_fail_until=~".+"}
          )
        annotations:
          runbook_url: "docs/runbooks/alerts/MaezoLifecycleJobFailed.md"
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _values(tmp_path: Path, expected_fail_until: str | None) -> Path:
    if expected_fail_until is None:
        body = "lifecycle:\n  enabled: true\n"
    else:
        body = f'lifecycle:\n  enabled: true\n  expectedFailUntil: "{expected_fail_until}"\n'
    return _write(tmp_path, "values.yaml", body)


# =================================================================================================
# RED: the date
# =================================================================================================


def test_a_missing_expected_fail_until_key_fails(tmp_path: Path) -> None:
    values = _values(tmp_path, None)
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "ausente" in findings[0].reason


def test_an_unparseable_date_fails(tmp_path: Path) -> None:
    values = _values(tmp_path, "not-a-date")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "nao e uma data ISO" in findings[0].reason


def test_a_deadline_that_has_passed_fails() -> None:
    """The headline case: R-040 cannot silently outlive its own deadline."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        values = _values(tmp_path, "2026-11-11")
        rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
        findings = evaluate(values, rules, today=date(2026, 11, 12))
        assert len(findings) == 1
        assert "DEADLINE VENCIDO" in findings[0].reason
        assert "2026-11-11" in findings[0].reason


def test_the_deadline_day_itself_still_passes(tmp_path: Path) -> None:
    """`today > deadline`, not `>=` — the deadline day itself is still in-date."""
    values = _values(tmp_path, "2026-11-11")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, today=date(2026, 11, 11)) == []


def test_a_date_comfortably_in_the_future_passes(tmp_path: Path) -> None:
    values = _values(tmp_path, "2026-11-11")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, today=date(2026, 9, 4)) == []


# =================================================================================================
# RED: the alert expression
# =================================================================================================


def test_the_alert_rule_missing_entirely_fails(tmp_path: Path) -> None:
    values = _values(tmp_path, "2026-11-11")
    rules = _write(
        tmp_path,
        "alert-rules.yml",
        """
        groups:
          - name: g
            rules:
              - alert: SomeOtherAlert
                expr: up == 0
        """,
    )
    findings = evaluate(values, rules, today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "nao encontrada" in findings[0].reason


def test_an_expression_that_dropped_the_marker_reference_fails(tmp_path: Path) -> None:
    """A future edit that removes the `unless` clause must go RED, not ship silently."""
    values = _values(tmp_path, "2026-11-11")
    rules = _write(
        tmp_path,
        "alert-rules.yml",
        """
        groups:
          - name: maezo_lifecycle
            rules:
              - alert: MaezoLifecycleJobFailed
                expr: |
                  kube_job_status_failed{job_name=~"lifecycle-.*"} > 0
        """,
    )
    findings = evaluate(values, rules, today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "parou de referenciar" in findings[0].reason


# =================================================================================================
# GREEN: both correct
# =================================================================================================


def test_an_in_date_marker_referenced_by_the_expression_passes(tmp_path: Path) -> None:
    values = _values(tmp_path, "2026-11-11")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, today=date(2026, 9, 4)) == []


def test_both_findings_fire_together_when_both_are_wrong(tmp_path: Path) -> None:
    values = _values(tmp_path, "2026-01-01")
    rules = _write(
        tmp_path,
        "alert-rules.yml",
        """
        groups:
          - name: maezo_lifecycle
            rules:
              - alert: MaezoLifecycleJobFailed
                expr: |
                  kube_job_status_failed{job_name=~"lifecycle-.*"} > 0
        """,
    )
    findings = evaluate(values, rules, today=date(2026, 9, 4))
    assert len(findings) == 2


# =================================================================================================
# Real-tree regression: the shipped files themselves
# =================================================================================================


def test_the_shipped_files_pass_non_vacuously_well_before_the_deadline() -> None:
    """The real fix: R-040's marker is in-date and the shipped expression still references it."""
    assert _SHIPPED_VALUES.is_file(), _SHIPPED_VALUES
    assert _SHIPPED_ALERT_RULES.is_file(), _SHIPPED_ALERT_RULES
    findings = evaluate(_SHIPPED_VALUES, _SHIPPED_ALERT_RULES, today=date(2026, 9, 4))
    assert findings == [], [f.render() for f in findings]


def test_the_shipped_deadline_is_exactly_the_ratified_2026_11_11() -> None:
    """Pins the ratified date itself — a quiet edit to a different date must be a visible diff."""
    from scripts.ci.check_lifecycle_expected_fail_expiry import _load_expected_fail_until

    assert _load_expected_fail_until(_SHIPPED_VALUES) == "2026-11-11"
