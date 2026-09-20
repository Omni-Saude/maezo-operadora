#!/usr/bin/env python3
"""CI gate: the R-040/SC-07 lifecycle expected-fail marker cannot silently outlive its deadline.

Purpose
-------
R-040 (UNLOCK-LEDGER, owner-decision, 2026-09-04) ratified annotating the 3 by-design-failing
lifecycle CronJobs (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml` — none of
`expurgo-working` / `verify-erasure` / `audit-retention` is implemented, see
`src/maezo/platform/lifecycle/__init__.py`) with `maezo.io/expected-fail-until`, and excluding
them from `MaezoLifecycleJobFailed` (`deploy/observability/alert-rules.yml`) while that marker is
present — otherwise the kube-state-metrics scrape ALERTS-WITHOUT-METRICS-b / R-056 wired in the
same window turns 3 by-design failures into a daily page, which is exactly the alarm-normalisation
defect D12-01-b's own justification names (`alert-rules.yml:159` in the pre-R-056 tree).

An exclusion that never expires is that same normalisation by another name. The owner's ratified
answer is explicit: the annotation carries the CALENDAR DATE (never the literal `AF-07`) —
`2026-11-11` as ratified on 2026-09-04, RENEWED to `2027-02-09` by the owner on 2026-09-20 (one
owner data-PR aligning the three same-date fences: deviation Q-2/Q-10, this marker, and the D7
catalogue xfails; suggested review checkpoint 2026-12-15) — and lands in the SAME PR as a CI fence
that turns red the day that date passes — the same
discipline `scripts/ci/check_deviation_expiry.py` already applies to the two ratified `shadow`
deviations (PLANS.md Sec.0.8). This is that fence, scoped to the single R-040 marker.

This gate fails on exactly three things:

  1. `deploy/helm/maezo-tenant/values.yaml`'s `lifecycle.expectedFailUntil` date has passed (today
     > that date), is missing, or does not parse. There is no override and no silent renewal — the
     way out is a reviewed PR that either removes the annotation (AF-07 closed, the jobs are now
     real and SHOULD page) or renews the date, and that renewal PR is itself green because the
     gate reads the tree under test.
  2. `MaezoLifecycleJobFailed`'s `expr` in `deploy/observability/alert-rules.yml` stopped
     referencing the marker series (`annotation_maezo_io_expected_fail_until`) — silently dropping
     the negative selector would re-normalise the alarm without anyone deciding to.
  3. The RENDERED chart (`helm template`) no longer puts `maezo.io/expected-fail-until` on every
     lifecycle CronJob's `jobTemplate.metadata.annotations`, or renders it with a value that is
     not the `values.yaml` date. This third check exists because (1) and (2) alone are ONE-SIDED
     (VERIFY-A1-OBS §Delta finding D4): deleting the annotation block from
     `templates/cronjob-lifecycle.yaml` left the date in `values.yaml` and the `unless` clause in
     the alert both intact and every gate green, while the exclusion had silently ceased to exist.
     Rendering is the only way to see what the cluster will actually receive — a substring grep on
     the template would pass on an annotation that is inside a `{{- if false }}` block, or bound
     to a values key that does not exist (Helm renders that as an EMPTY value, which fails the
     alert's `=~".+"` matcher).

It does NOT know whether `AF-07` itself closed — that is a DPO governance fact this script has no
access to (the audit register is gitignored and lives outside the tree this gate reads). Reaching
the deadline is the mechanical trigger regardless of that unknown; a human reads the red and
decides which of the two exits applies. It also does NOT verify that kube-state-metrics is
actually configured with `--metric-annotations-allowlist` (that is an operator-owned, out-of-repo
deployment — see the comment on the `kube-state-metrics` job in `deploy/observability/
prometheus.yml`) — only that the *repo's own* three artefacts (the date, the rendered annotation
that carries it, and the PromQL reference to the resulting series) stay consistent with each other
and with the calendar. Without that operator flag the exclusion is INERT and the alert pages on the
by-design failures anyway: honest fail-open, and this gate cannot see it.

`helm` is REQUIRED (same binary the `validate-helm` CI job and `make helm-lint` already use). Its
absence is a hard FAIL with an explicit message, never a skip: a gate that quietly degrades to two
checks when a tool is missing is the same "absence of a diagnostic read as resolution" this repo
keeps finding.

Usage
-----
    python scripts/ci/check_lifecycle_expected_fail_expiry.py                    # CI gate
    python scripts/ci/check_lifecycle_expected_fail_expiry.py --today 2027-02-10 # what CI does then
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_VALUES: Final[Path] = Path("deploy/helm/maezo-tenant/values.yaml")
DEFAULT_ALERT_RULES: Final[Path] = Path("deploy/observability/alert-rules.yml")
DEFAULT_CHART: Final[Path] = Path("deploy/helm/maezo-tenant")
#: The chart CANNOT be rendered from its bare `values.yaml`: ADR-0017 makes `networkpolicy.yaml`
#: `fail` when a general-zone endpoint has no concrete `cidr`, which is exactly the default. Every
#: render therefore carries one of the shipped overlays, and this gate checks ALL of them — an
#: overlay that turned `lifecycle.enabled` off, or overrode the date, would otherwise ship an
#: unmarked (and therefore paging) set of by-design-failing jobs while the gate stayed green.
CHART_VALUES_OVERLAY_GLOB: Final[str] = "values-*.yaml"

#: The annotation key as written in the chart (kube-state-metrics sanitises it into
#: MARKER_SERIES_LABEL below when it exports the series).
MARKER_ANNOTATION: Final[str] = "maezo.io/expected-fail-until"
#: Rendered CronJobs whose name starts with this are the lifecycle jobs R-040 covers
#: (`templates/cronjob-lifecycle.yaml` names them `lifecycle-<job.name>`).
LIFECYCLE_CRONJOB_PREFIX: Final[str] = "lifecycle-"

ALERT_NAME: Final[str] = "MaezoLifecycleJobFailed"
#: kube-state-metrics v2 sanitises an annotation key by replacing every character outside
#: [A-Za-z0-9_] with `_` and prefixing `annotation_` — `maezo.io/expected-fail-until` becomes this.
MARKER_SERIES_LABEL: Final[str] = "annotation_maezo_io_expected_fail_until"

RENEWAL_PROCEDURE: Final[str] = (
    "COMO SAIR DO VERMELHO (ato humano, uma das duas — nenhuma e' editavel por agente sem PR "
    "revisado):\n"
    "      (a) REMOVER a anotacao `maezo.io/expected-fail-until` do jobTemplate — AF-07 fechou, "
    "os jobs\n"
    "          agora sao reais e DEVEM paginar quando falharem; ou\n"
    "      (b) RENOVAR a data: atualizar `lifecycle.expectedFailUntil` em "
    "deploy/helm/maezo-tenant/values.yaml,\n"
    "          em PR de dados sob revisao CODEOWNERS (deploy/** e' owner-review por politica, "
    "Sec.8).\n"
    "      O PR de renovacao e' VERDE por construcao — este gate le a data da arvore em teste, e "
    "a arvore\n"
    "      dele ja carrega a data nova. Prorrogar e' possivel; prorrogar EM SILENCIO nao e'."
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation."""

    reason: str

    def render(self) -> str:
        return f"  - {self.reason}"


def _load_expected_fail_until(values_path: Path) -> str | None:
    document: Any = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    lifecycle = (document or {}).get("lifecycle") or {}
    value = lifecycle.get("expectedFailUntil")
    return str(value) if value not in (None, "") else None


def _load_alert_expr(alert_rules_path: Path, alert_name: str) -> str | None:
    """The `expr` of the named `alert:` rule, or None if no rule by that name exists."""
    document: Any = yaml.safe_load(alert_rules_path.read_text(encoding="utf-8"))
    for group in (document or {}).get("groups", []):
        for rule in group.get("rules", []):
            if rule.get("alert") == alert_name:
                return str(rule.get("expr", ""))
    return None


class ChartRenderError(RuntimeError):
    """`helm template` could not be run, or failed. Never swallowed — the gate exits 1 on it."""


@dataclass(frozen=True, slots=True)
class RenderedLifecycleJob:
    """One rendered lifecycle CronJob and the marker annotation it actually carries."""

    #: The overlay values file this render used (`values-amh.yaml`, `values-staging.yaml`, ...).
    overlay: str
    name: str
    #: The value of `jobTemplate.metadata.annotations["maezo.io/expected-fail-until"]`.
    #: `None` = the key is absent altogether; `""` = present but empty (what Helm renders when the
    #: values key it is bound to has been deleted — which fails the alert's `=~".+"` matcher and so
    #: fails OPEN, i.e. pages on the by-design failures).
    expected_fail_until: str | None


def render_lifecycle_jobs(chart_dir: Path) -> list[RenderedLifecycleJob]:
    """Render the chart once per shipped overlay with `helm template`; return its lifecycle CronJobs.

    Raises:
        ChartRenderError: `helm` is not on PATH, no overlay exists, a render failed, or its output
            is not parseable YAML. Always an error, never a silent skip.
    """
    helm = shutil.which("helm")
    if helm is None:
        raise ChartRenderError(
            "`helm` nao esta no PATH. Este gate RENDERIZA o chart para provar que a anotacao "
            f"`{MARKER_ANNOTATION}` chega de fato em cada CronJob de ciclo de vida — sem o binario "
            "ele nao tem como saber, e degradar em silencio para os outros dois checks e' "
            "exatamente o defeito D4 (VERIFY-A1-OBS §Delta). Instale helm (>=3.15, o mesmo do job "
            "`validate-helm` do CI)."
        )
    overlays = sorted(chart_dir.glob(CHART_VALUES_OVERLAY_GLOB))
    if not overlays:
        raise ChartRenderError(
            f"nenhum overlay `{CHART_VALUES_OVERLAY_GLOB}` em {chart_dir} — o chart nao renderiza "
            "a partir do values.yaml puro (ADR-0017 falha fechado sem CIDR concreto), entao sem "
            "overlay este gate nao tem o que renderizar."
        )
    jobs: list[RenderedLifecycleJob] = []
    for overlay in overlays:
        try:
            completed = subprocess.run(
                [helm, "template", "lifecycle-expected-fail-gate", str(chart_dir), "-f", str(overlay)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except OSError as exc:  # pragma: no cover - defensivo; `which` ja provou que existe
            raise ChartRenderError(f"falha ao executar `helm template`: {exc}") from exc
        if completed.returncode != 0:
            raise ChartRenderError(
                f"`helm template {chart_dir} -f {overlay.name}` falhou (exit "
                f"{completed.returncode}):\n{completed.stderr.strip()}"
            )
        try:
            documents: Iterable[Any] = list(yaml.safe_load_all(completed.stdout))
        except yaml.YAMLError as exc:
            raise ChartRenderError(
                f"saida de `helm template ... -f {overlay.name}` nao e YAML valido: {exc}"
            ) from exc
        jobs.extend(lifecycle_jobs_from_documents(documents, overlay=overlay.name))
    return jobs


def lifecycle_jobs_from_documents(documents: Iterable[Any], *, overlay: str) -> list[RenderedLifecycleJob]:
    """Extract the lifecycle CronJobs from already-parsed rendered documents.

    Split out from `render_lifecycle_jobs` so the comparator can be driven from synthetic rendered
    documents in tests without a helm binary — the RENDER needs helm, the READING of a render does
    not.
    """
    jobs: list[RenderedLifecycleJob] = []
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "CronJob":
            continue
        name = str((document.get("metadata") or {}).get("name", ""))
        if not name.startswith(LIFECYCLE_CRONJOB_PREFIX):
            continue
        job_template = (document.get("spec") or {}).get("jobTemplate") or {}
        annotations = (job_template.get("metadata") or {}).get("annotations") or {}
        raw = annotations.get(MARKER_ANNOTATION, None)
        jobs.append(
            RenderedLifecycleJob(
                overlay=overlay,
                name=name,
                expected_fail_until=None if raw is None else str(raw),
            )
        )
    return sorted(jobs, key=lambda job: job.name)


def _chart_findings(jobs: Sequence[RenderedLifecycleJob], raw_date: str | None) -> list[Finding]:
    """Third check: the RENDERED chart really carries the marker, with the ratified value."""
    findings: list[Finding] = []
    if not jobs:
        findings.append(
            Finding(
                "o chart renderizado nao produziu NENHUM CronJob "
                f"`{LIFECYCLE_CRONJOB_PREFIX}*` — ou `lifecycle.enabled` virou false, ou os jobs "
                "sumiram. Sem job renderizado a exclusao R-040 nao tem sobre o que incidir, e este "
                "gate nao pode provar nada: falha fechado em vez de passar vacuamente."
            )
        )
        return findings
    for job in jobs:
        if job.expected_fail_until is None:
            findings.append(
                Finding(
                    f"o CronJob renderizado `{job.name}` (overlay {job.overlay}) NAO carrega a anotacao "
                    f"`{MARKER_ANNOTATION}` em jobTemplate.metadata.annotations — a exclusao "
                    "R-040 deixou de existir no artefato que vai para o cluster, mesmo com a data "
                    "em values.yaml e o `unless` no alerta intactos (defeito D4, VERIFY-A1-OBS "
                    "§Delta)."
                )
            )
        elif raw_date is not None and job.expected_fail_until != raw_date:
            findings.append(
                Finding(
                    f"o CronJob renderizado `{job.name}` (overlay {job.overlay}) carrega "
                    f"`{MARKER_ANNOTATION}: {job.expected_fail_until!r}`, diferente da data "
                    f"rastreada em values.yaml (`lifecycle.expectedFailUntil: {raw_date!r}`) — a "
                    "anotacao deixou de estar amarrada ao valor versionado que este gate vigia, "
                    "entao o prazo poderia vencer sem que a build ficasse vermelha."
                )
            )
    return findings


def evaluate(
    values_path: Path,
    alert_rules_path: Path,
    rendered_jobs: Sequence[RenderedLifecycleJob],
    today: date,
) -> list[Finding]:
    """The whole comparator, pure over its four inputs. No process exit, no I/O beyond the reads.

    Args:
        values_path: the Helm values file carrying `lifecycle.expectedFailUntil` (or a synthetic
            one, for tests).
        alert_rules_path: the Prometheus rules file carrying `MaezoLifecycleJobFailed` (or a
            synthetic one, for tests).
        rendered_jobs: the lifecycle CronJobs as `helm template` actually renders them (see
            `render_lifecycle_jobs`). Injected rather than rendered in here so the comparator stays
            pure and testable without a helm binary — and REQUIRED, with no default, so a caller
            cannot silently drop the chart half of the check the way the pre-D4 gate did.
        today: the reference date. Injected so expiry is testable at any point in its life instead
            of only on the day the suite happens to run.
    """
    findings: list[Finding] = []

    raw_date = _load_expected_fail_until(values_path)
    if raw_date is None:
        findings.append(
            Finding(
                f"`lifecycle.expectedFailUntil` ausente/vazio em {values_path} — o marcador R-040 "
                "nao tem data rastreada (apagar o prazo nao pode ser mais barato que honra-lo)."
            )
        )
    else:
        try:
            deadline = date.fromisoformat(raw_date)
        except ValueError:
            findings.append(
                Finding(f"`lifecycle.expectedFailUntil: {raw_date!r}` nao e uma data ISO YYYY-MM-DD valida.")
            )
        else:
            if today > deadline:
                findings.append(
                    Finding(
                        "DEADLINE VENCIDO — `lifecycle.expectedFailUntil: "
                        f"{deadline.isoformat()}` passou (hoje = {today.isoformat()}). "
                        f"R-040/SC-07 nunca previu renovacao silenciosa.\n{RENEWAL_PROCEDURE}"
                    )
                )

    findings.extend(_chart_findings(rendered_jobs, raw_date))

    expr = _load_alert_expr(alert_rules_path, ALERT_NAME)
    if expr is None:
        findings.append(Finding(f"regra `alert: {ALERT_NAME}` nao encontrada em {alert_rules_path}."))
    elif MARKER_SERIES_LABEL not in expr:
        findings.append(
            Finding(
                f"`{ALERT_NAME}.expr` parou de referenciar `{MARKER_SERIES_LABEL}` — a exclusao "
                "dos jobs com o marcador R-040 nao-expirado sumiu, o que reintroduz a "
                "normalizacao de alarme (D12-01-b) para as 3 falhas por desenho do ciclo de vida."
            )
        )

    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--values",
        default=str(DEFAULT_VALUES),
        help=f"values.yaml a verificar (default: {DEFAULT_VALUES})",
    )
    parser.add_argument(
        "--alert-rules",
        default=str(DEFAULT_ALERT_RULES),
        help=f"arquivo de regras a verificar (default: {DEFAULT_ALERT_RULES})",
    )
    parser.add_argument(
        "--chart",
        default=str(DEFAULT_CHART),
        help=f"diretorio do chart Helm a renderizar (default: {DEFAULT_CHART})",
    )
    parser.add_argument(
        "--today",
        default=None,
        metavar="YYYY-MM-DD",
        help="data de referencia (default: data do sistema). Existe para tornar o vencimento testavel.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    values_path = Path(args.values)
    if not values_path.is_absolute():
        values_path = REPO_ROOT / values_path
    alert_rules_path = Path(args.alert_rules)
    if not alert_rules_path.is_absolute():
        alert_rules_path = REPO_ROOT / alert_rules_path
    chart_dir = Path(args.chart)
    if not chart_dir.is_absolute():
        chart_dir = REPO_ROOT / chart_dir

    if args.today is None:
        today = date.today()
    else:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(
                f"[lifecycle-expected-fail-expiry] FAIL: --today {args.today!r} nao e uma data "
                "ISO YYYY-MM-DD",
                file=sys.stderr,
            )
            return 1

    for path in (values_path, alert_rules_path):
        if not path.is_file():
            print(f"[lifecycle-expected-fail-expiry] FAIL: arquivo nao encontrado: {path}", file=sys.stderr)
            return 1
    if not (chart_dir / "Chart.yaml").is_file():
        print(
            f"[lifecycle-expected-fail-expiry] FAIL: chart nao encontrado: {chart_dir}/Chart.yaml",
            file=sys.stderr,
        )
        return 1

    try:
        rendered_jobs = render_lifecycle_jobs(chart_dir)
    except ChartRenderError as exc:
        print(f"[lifecycle-expected-fail-expiry] FAIL: {exc}", file=sys.stderr)
        return 1

    findings = evaluate(values_path, alert_rules_path, rendered_jobs, today)

    if findings:
        print(f"[lifecycle-expected-fail-expiry] FAIL @ {today.isoformat()}:", file=sys.stderr)
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    rendered_summary = ", ".join(
        f"{job.overlay}:{job.name}={job.expected_fail_until}" for job in rendered_jobs
    )
    print(
        f"[lifecycle-expected-fail-expiry] PASS @ {today.isoformat()} (chart renderizado: {rendered_summary})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
