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
answer is explicit: the annotation carries the CALENDAR DATE `2026-11-11` (never the literal
`AF-07`), and lands in the SAME PR as a CI fence that turns red the day that date passes — the same
discipline `scripts/ci/check_deviation_expiry.py` already applies to the two ratified `shadow`
deviations (PLANS.md Sec.0.8). This is that fence, scoped to the single R-040 marker.

This gate fails on exactly two things:

  1. `deploy/helm/maezo-tenant/values.yaml`'s `lifecycle.expectedFailUntil` date has passed (today
     > that date), is missing, or does not parse. There is no override and no silent renewal — the
     way out is a reviewed PR that either removes the annotation (AF-07 closed, the jobs are now
     real and SHOULD page) or renews the date, and that renewal PR is itself green because the
     gate reads the tree under test.
  2. `MaezoLifecycleJobFailed`'s `expr` in `deploy/observability/alert-rules.yml` stopped
     referencing the marker series (`annotation_maezo_io_expected_fail_until`) — silently dropping
     the negative selector would re-normalise the alarm without anyone deciding to.

It does NOT know whether `AF-07` itself closed — that is a DPO governance fact this script has no
access to (the audit register is gitignored and lives outside the tree this gate reads). Reaching
the deadline is the mechanical trigger regardless of that unknown; a human reads the red and
decides which of the two exits applies. It also does NOT verify that kube-state-metrics is
actually configured with `--metric-annotations-allowlist` (that is an operator-owned, out-of-repo
deployment — see the comment on the `kube-state-metrics` job in `deploy/observability/
prometheus.yml`) — only that the *repo's own* two artefacts (the date and the PromQL reference to
it) stay consistent with each other and with the calendar.

Usage
-----
    python scripts/ci/check_lifecycle_expected_fail_expiry.py                    # CI gate
    python scripts/ci/check_lifecycle_expected_fail_expiry.py --today 2026-11-12 # what CI does then
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_VALUES: Final[Path] = Path("deploy/helm/maezo-tenant/values.yaml")
DEFAULT_ALERT_RULES: Final[Path] = Path("deploy/observability/alert-rules.yml")

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


def evaluate(values_path: Path, alert_rules_path: Path, today: date) -> list[Finding]:
    """The whole comparator, pure over its three inputs. No process exit, no I/O beyond the reads.

    Args:
        values_path: the Helm values file carrying `lifecycle.expectedFailUntil` (or a synthetic
            one, for tests).
        alert_rules_path: the Prometheus rules file carrying `MaezoLifecycleJobFailed` (or a
            synthetic one, for tests).
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

    findings = evaluate(values_path, alert_rules_path, today)

    if findings:
        print(f"[lifecycle-expected-fail-expiry] FAIL @ {today.isoformat()}:", file=sys.stderr)
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(f"[lifecycle-expected-fail-expiry] PASS @ {today.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
