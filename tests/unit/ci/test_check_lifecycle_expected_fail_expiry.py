"""Unit tests for the R-040/SC-07 lifecycle expected-fail-marker expiry gate.

Three layers:

1. **Pure-core** tests drive `evaluate` against synthetic `values.yaml`/`alert-rules.yml` trees
   written to a tmp path plus a synthetic RENDER of the chart — RED on a vanished/malformed/expired
   date, RED on the alert expression dropping the marker reference, RED on a rendered CronJob that
   lost the annotation or carries a value that is not the tracked date, GREEN when all three agree.
2. **Real-tree** tests run the comparator against the SHIPPED
   `deploy/helm/maezo-tenant/values.yaml` + `deploy/observability/alert-rules.yml` and assert they
   pass as of a pinned "today" well before the ratified 2027-02-09 deadline (renovado pelo dono em
   2026-09-20 a partir de 2026-11-11), plus a
   template-SOURCE assertion that `templates/cronjob-lifecycle.yaml` still binds the annotation to
   the values key — the regression proof that R-040 landed for real.
3. The RENDER itself (`helm template`) is exercised by the gate's own `main()` in CI and locally;
   the reader of a render (`lifecycle_jobs_from_documents`) is tested here directly, so this file
   needs no helm binary and therefore no `skip` (the repo forbids new skips, and a helm-gated skip
   is precisely how the chart half would go unproven again).

Why the render exists at all: VERIFY-A1-OBS §Delta finding D4 — before it, deleting the annotation
block from `templates/cronjob-lifecycle.yaml` left every gate in this repo green while the R-040
exclusion silently ceased to exist.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.ci.check_lifecycle_expected_fail_expiry import (
    MARKER_ANNOTATION,
    RenderedLifecycleJob,
    evaluate,
    lifecycle_jobs_from_documents,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_SHIPPED_VALUES = _CHART_DIR / "values.yaml"
_SHIPPED_ALERT_RULES = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"
_SHIPPED_CRONJOB_TEMPLATE = _CHART_DIR / "templates" / "cronjob-lifecycle.yaml"

#: The three lifecycle CronJobs the chart renders, marked with the ratified date — the shape a
#: correct `helm template` produces. Pure-core tests perturb copies of this.
_LIFECYCLE_JOB_NAMES = ("lifecycle-audit-retention", "lifecycle-expurgo-working", "lifecycle-verify-erasure")


def _rendered(expected_fail_until: str | None = "2027-02-09") -> list[RenderedLifecycleJob]:
    """A synthetic render of the three lifecycle CronJobs, all carrying the same marker value.

    Default is the CURRENTLY tracked date (2027-02-09, renovado pelo dono em 2026-09-20 a partir de
    2026-11-11) so call sites that pair this with the shipped `values.yaml` stay consistent.
    """
    return [
        RenderedLifecycleJob(overlay="values-amh.yaml", name=name, expected_fail_until=expected_fail_until)
        for name in _LIFECYCLE_JOB_NAMES
    ]


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
    findings = evaluate(values, rules, _rendered(), today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "ausente" in findings[0].reason


def test_an_unparseable_date_fails(tmp_path: Path) -> None:
    values = _values(tmp_path, "not-a-date")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    # The render echoes whatever `values.yaml` says (Helm binds the annotation to that key), so an
    # unparseable date renders unparseable too — isolating the finding under test to the date.
    findings = evaluate(values, rules, _rendered("not-a-date"), today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "nao e uma data ISO" in findings[0].reason


def test_a_deadline_that_has_passed_fails() -> None:
    """The headline case: R-040 cannot silently outlive its own deadline.

    Uses the PRE-renewal date (2026-11-11, ratificado 2026-09-04) as the expired fixture — proof
    the fence fires on a past date as such and is not tuned to the currently tracked one.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        values = _values(tmp_path, "2026-11-11")
        rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
        findings = evaluate(values, rules, _rendered("2026-11-11"), today=date(2026, 11, 12))
        assert len(findings) == 1
        assert "DEADLINE VENCIDO" in findings[0].reason
        assert "2026-11-11" in findings[0].reason


def test_the_deadline_day_itself_still_passes(tmp_path: Path) -> None:
    """`today > deadline`, not `>=` — the deadline day itself is still in-date."""
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, _rendered(), today=date(2027, 2, 9)) == []


def test_a_date_comfortably_in_the_future_passes(tmp_path: Path) -> None:
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, _rendered(), today=date(2026, 9, 4)) == []


# =================================================================================================
# RED: the alert expression
# =================================================================================================


def test_the_alert_rule_missing_entirely_fails(tmp_path: Path) -> None:
    values = _values(tmp_path, "2027-02-09")
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
    findings = evaluate(values, rules, _rendered(), today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "nao encontrada" in findings[0].reason


def test_an_expression_that_dropped_the_marker_reference_fails(tmp_path: Path) -> None:
    """A future edit that removes the `unless` clause must go RED, not ship silently."""
    values = _values(tmp_path, "2027-02-09")
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
    findings = evaluate(values, rules, _rendered(), today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "parou de referenciar" in findings[0].reason


# =================================================================================================
# RED: the RENDERED chart (D4 — VERIFY-A1-OBS §Delta)
# =================================================================================================


def test_a_rendered_cronjob_without_the_annotation_fails(tmp_path: Path) -> None:
    """THE D4 CASE: deleting the annotation block from the template must go RED.

    Before this check, the date in `values.yaml` and the `unless` clause in `alert-rules.yml` both
    stayed intact, `helm lint --strict` stayed green, this gate exited 0 — and the exclusion had
    silently ceased to exist in the only artefact the cluster ever sees.
    """
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, _rendered(None), today=date(2026, 9, 4))
    assert len(findings) == len(_LIFECYCLE_JOB_NAMES)
    assert all("NAO carrega a anotacao" in f.reason for f in findings)


def test_one_rendered_cronjob_losing_the_annotation_is_enough_to_fail(tmp_path: Path) -> None:
    """Per-job, not all-or-nothing: a fourth lifecycle job added without the marker must go RED."""
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    jobs = _rendered()
    jobs.append(
        RenderedLifecycleJob(overlay="values-amh.yaml", name="lifecycle-novo-job", expected_fail_until=None)
    )
    findings = evaluate(values, rules, jobs, today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "lifecycle-novo-job" in findings[0].reason


def test_a_rendered_annotation_that_drifted_from_the_tracked_date_fails(tmp_path: Path) -> None:
    """Hardcoding a different date in the template detaches the marker from the gated value."""
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, _rendered("2027-01-01"), today=date(2026, 9, 4))
    assert len(findings) == len(_LIFECYCLE_JOB_NAMES)
    assert all("diferente da data" in f.reason for f in findings)


def test_an_annotation_rendered_empty_fails(tmp_path: Path) -> None:
    """Helm renders `""` when the values key it is bound to is gone — and `=~".+"` never matches it.

    The alert then pages on the by-design failures (honest fail-open), so an empty value is NOT the
    marker being present; it must be as loud as a missing one.
    """
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, _rendered(""), today=date(2026, 9, 4))
    assert len(findings) == len(_LIFECYCLE_JOB_NAMES)
    assert all("diferente da data" in f.reason for f in findings)


def test_a_render_with_no_lifecycle_cronjobs_at_all_fails(tmp_path: Path) -> None:
    """`lifecycle.enabled: false` (or the jobs vanishing) must fail closed, never pass vacuously."""
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    findings = evaluate(values, rules, [], today=date(2026, 9, 4))
    assert len(findings) == 1
    assert "NENHUM CronJob" in findings[0].reason


# =================================================================================================
# The render READER (no helm binary needed — helm produces these documents, this parses them)
# =================================================================================================


def test_the_render_reader_extracts_the_jobtemplate_annotation() -> None:
    documents = [
        {
            "kind": "CronJob",
            "metadata": {"name": "lifecycle-expurgo-working"},
            "spec": {"jobTemplate": {"metadata": {"annotations": {MARKER_ANNOTATION: "2026-11-11"}}}},
        },
        {"kind": "Deployment", "metadata": {"name": "agent-runtime"}},
        {"kind": "CronJob", "metadata": {"name": "outro-cronjob"}, "spec": {}},
        None,
    ]
    jobs = lifecycle_jobs_from_documents(documents, overlay="values-amh.yaml")
    assert [(j.overlay, j.name, j.expected_fail_until) for j in jobs] == [
        ("values-amh.yaml", "lifecycle-expurgo-working", "2026-11-11")
    ]


def test_the_render_reader_reports_a_missing_annotation_as_none() -> None:
    """The distinction the comparator relies on: absent key -> None, present-but-empty -> ""."""
    documents = [
        {
            "kind": "CronJob",
            "metadata": {"name": "lifecycle-verify-erasure"},
            "spec": {"jobTemplate": {"metadata": {"annotations": {"outra": "x"}}}},
        },
        {
            "kind": "CronJob",
            "metadata": {"name": "lifecycle-audit-retention"},
            "spec": {"jobTemplate": {"metadata": {"annotations": {MARKER_ANNOTATION: ""}}}},
        },
    ]
    jobs = lifecycle_jobs_from_documents(documents, overlay="values-staging.yaml")
    assert [(j.name, j.expected_fail_until) for j in jobs] == [
        ("lifecycle-audit-retention", ""),
        ("lifecycle-verify-erasure", None),
    ]


# =================================================================================================
# GREEN: both correct
# =================================================================================================


def test_an_in_date_marker_referenced_by_the_expression_passes(tmp_path: Path) -> None:
    values = _values(tmp_path, "2027-02-09")
    rules = _write(tmp_path, "alert-rules.yml", _VALID_EXPR)
    assert evaluate(values, rules, _rendered(), today=date(2026, 9, 4)) == []


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
    findings = evaluate(values, rules, _rendered("2026-01-01"), today=date(2026, 9, 4))
    assert len(findings) == 2


# =================================================================================================
# Real-tree regression: the shipped files themselves
# =================================================================================================


def test_the_shipped_files_pass_non_vacuously_well_before_the_deadline() -> None:
    """The real fix: R-040's marker is in-date and the shipped expression still references it."""
    assert _SHIPPED_VALUES.is_file(), _SHIPPED_VALUES
    assert _SHIPPED_ALERT_RULES.is_file(), _SHIPPED_ALERT_RULES
    findings = evaluate(_SHIPPED_VALUES, _SHIPPED_ALERT_RULES, _rendered(), today=date(2026, 9, 4))
    assert findings == [], [f.render() for f in findings]


def test_the_shipped_deadline_is_exactly_the_renewed_2027_02_09() -> None:
    """Pins the ratified date itself — a quiet edit to a different date must be a visible diff.

    2027-02-09 = renewal ratified by the owner on 2026-09-20 (original R-040 date: 2026-11-11).
    """
    from scripts.ci.check_lifecycle_expected_fail_expiry import _load_expected_fail_until

    assert _load_expected_fail_until(_SHIPPED_VALUES) == "2027-02-09"


def test_the_shipped_template_still_binds_the_annotation_to_the_values_key() -> None:
    """Real-tree, helm-free half of D4: the template must carry the marker, bound to values.yaml.

    The RENDER is what the gate's `main()` checks in CI (and it is strictly stronger — it sees
    conditionals, indentation and the resolved value). This assertion exists so the same mutation
    (deleting the annotation block) also goes RED in the unit lane, which runs without a helm
    binary and must not gain a `skip` to get one.
    """
    source = _SHIPPED_CRONJOB_TEMPLATE.read_text(encoding="utf-8")
    assert MARKER_ANNOTATION in source, _SHIPPED_CRONJOB_TEMPLATE
    marker_line = next(
        line for line in source.splitlines() if line.lstrip().startswith(f"{MARKER_ANNOTATION}:")
    )
    assert ".Values.lifecycle.expectedFailUntil" in marker_line, (
        "a anotacao deixou de ser derivada de `lifecycle.expectedFailUntil` (values.yaml) — a data "
        f"passou a ser outra coisa nesta linha: {marker_line!r}"
    )
