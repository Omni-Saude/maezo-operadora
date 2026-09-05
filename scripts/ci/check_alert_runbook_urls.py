#!/usr/bin/env python3
"""CI gate: every shipped alert rule carries a `runbook_url` that resolves in-repo (D12-01-b / R-007).

Purpose
-------
`deploy/observability/alert-rules.yml` shipped 8 alert rules with ZERO `runbook_url` annotations
(`grep -c runbook deploy/observability/alert-rules.yml` -> 0). An alert that pages someone without
telling them what to do trains the on-call to ignore it — the register's own evidence cites a live
case of exactly that (a normalized alarm at `alert-rules.yml:159`, the lifecycle CronJobs that fail
by design). The owner ratified (OWNER-DECISIONS-REGISTER R-007, 2026-09-04): `runbook_url` on all
8 alerts now, pointing at the SPECIFIC `docs/runbooks/` file that covers each one — never a generic
index — leaving burn-rate/multi-window SLO rewrites for after `D12-01-a`.

This gate is the mechanism that keeps that true going forward: it fails on

  1. an `alert:` rule with no `annotations.runbook_url` at all (or an empty one),
  2. a `runbook_url` naming a GENERIC target — `README.md`/`index.md` (any case) or the bare
     `docs/runbooks` directory — rather than the specific file for THIS alert (finding 6,
     VERIFY-A1-OBS: a generic index resolves, so the dangling-pointer check alone never caught it,
     and it defeats R-007 exactly as thoroughly as no `runbook_url` at all),
  3. a `runbook_url` whose PATH does not exist in the repository tree (a dangling pointer is
     scarcely better than no pointer — the on-call still has nothing to read), including
  4. a `#anchor` fragment (when the URL carries one) that does not name a real Markdown heading in
     the target file — a link that resolves to the top of the wrong section is the softer version
     of the same defect.

It does NOT check that the runbook's CONTENT is any good, only that the pointer is real. It does
NOT check burn-rate/SLO thresholds — those are `D12-01-a`'s scope, not this gate's.

Usage
-----
    python scripts/ci/check_alert_runbook_urls.py                          # CI gate
    python scripts/ci/check_alert_runbook_urls.py --alert-rules PATH       # against another file
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_ALERT_RULES: Final[Path] = Path("deploy/observability/alert-rules.yml")

#: Markdown ATX heading line, e.g. `## Resposta do operador`. Used only to resolve `#anchor`
#: fragments against the target runbook's real headings — GitHub's own slugification rule
#: (lowercase, spaces -> `-`, strip everything that is not a word char/hyphen/space first).
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

#: Finding 6 (VERIFY-A1-OBS, INFO -> fixed): a `runbook_url` naming a generic index rather than the
#: SPECIFIC file for this alert defeats R-007 exactly as thoroughly as no `runbook_url` at all —
#: the on-call still has to go find the right document themselves. `README.md`/`index.md` resolve
#: (they exist), so the dangling-pointer check alone never caught them; probe D of VERIFY-A1-OBS
#: proved `docs/runbooks/README.md` passed before this fix.
_GENERIC_RUNBOOK_BASENAMES: Final[frozenset[str]] = frozenset({"readme.md", "index.md"})

#: A `runbook_url` naming the runbooks tree itself (with or without a trailing slash) rather than
#: one file in it — the directory form of the same generic-target defect.
_BARE_RUNBOOKS_DIR: Final[str] = "docs/runbooks"


def _slugify(heading: str) -> str:
    """GitHub-flavoured Markdown heading -> anchor slug (lowercase, spaces -> hyphens)."""
    stripped = re.sub(r"[^\w\s-]", "", heading, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s]+", "-", stripped)


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation, naming the alert and the precise defect."""

    alert: str
    reason: str

    def render(self) -> str:
        return f"  - {self.alert}: {self.reason}"


def _load_rules(alert_rules_path: Path) -> list[dict[str, Any]]:
    """Every `alert:` rule dict in the shipped file, in document order.

    Recording rules (`record:`, e.g. `maezo_dead_letter_derived` — ALERTS-WITHOUT-METRICS-b /
    R-056) are skipped: they page nobody, so a runbook pointer is not a meaningful concept for
    them.
    """
    document: Any = yaml.safe_load(alert_rules_path.read_text(encoding="utf-8"))
    rules: list[dict[str, Any]] = []
    for group in document.get("groups", []):
        for rule in group.get("rules", []):
            if "alert" in rule:
                rules.append(rule)
    return rules


def evaluate(alert_rules_path: Path, repo_root: Path) -> list[Finding]:
    """The whole gate, as a pure(ish) function over the two paths it reads. No process exit here.

    Args:
        alert_rules_path: the shipped rules file (or a synthetic one, for tests).
        repo_root: root the `runbook_url` path is resolved against.
    """
    findings: list[Finding] = []
    rules = _load_rules(alert_rules_path)
    if not rules:
        return [Finding(alert="(arquivo)", reason="nenhuma regra `alert:` encontrada — cerca vazia")]

    for rule in rules:
        name = str(rule.get("alert", "(sem nome)"))
        annotations = rule.get("annotations") or {}
        runbook_url = annotations.get("runbook_url")

        if not runbook_url or not str(runbook_url).strip():
            findings.append(
                Finding(
                    alert=name,
                    reason=(
                        "sem `annotations.runbook_url` — um alerta sem runbook treina o operador a "
                        "ignora-lo (R-007). Aponte para o arquivo especifico em docs/runbooks/ que "
                        "cobre este alerta, nunca para um indice generico."
                    ),
                )
            )
            continue

        url = str(runbook_url).strip()
        path_part, _sep, anchor = url.partition("#")

        basename = Path(path_part).name.lower()
        normalized_dir = path_part.rstrip("/")
        if basename in _GENERIC_RUNBOOK_BASENAMES or normalized_dir == _BARE_RUNBOOKS_DIR:
            findings.append(
                Finding(
                    alert=name,
                    reason=(
                        f"`runbook_url: {url!r}` aponta para um indice generico "
                        f"({path_part!r}) ou para o diretorio docs/runbooks/ inteiro, nunca para o "
                        "arquivo ESPECIFICO deste alerta — um indice generico ensina o operador a "
                        "ignorar o pointer e procurar por conta propria, o mesmo defeito que "
                        "R-007 fechou ao exigir runbook_url."
                    ),
                )
            )
            continue

        target = repo_root / path_part
        if not target.is_file():
            findings.append(
                Finding(
                    alert=name,
                    reason=(
                        f"`runbook_url: {url!r}` aponta para {path_part!r}, que NAO existe no "
                        "repositorio (ponteiro pendurado)."
                    ),
                )
            )
            continue

        if anchor:
            headings = _HEADING_RE.findall(target.read_text(encoding="utf-8"))
            slugs = {_slugify(h) for h in headings}
            if anchor not in slugs:
                findings.append(
                    Finding(
                        alert=name,
                        reason=(
                            f"`runbook_url: {url!r}` tem a ancora `#{anchor}`, que nao corresponde "
                            f"a nenhum titulo de {path_part!r} (titulos disponiveis: "
                            f"{sorted(slugs) or '(nenhum)'})."
                        ),
                    )
                )

    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "D12-01-b / R-007: toda regra `alert:` de alert-rules.yml precisa de "
            "annotations.runbook_url apontando para um arquivo real de docs/runbooks/."
        )
    )
    parser.add_argument(
        "--alert-rules",
        default=str(DEFAULT_ALERT_RULES),
        help=f"arquivo de regras a verificar (default: {DEFAULT_ALERT_RULES})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    alert_rules_path = Path(args.alert_rules)
    if not alert_rules_path.is_absolute():
        alert_rules_path = REPO_ROOT / alert_rules_path

    if not alert_rules_path.is_file():
        print(f"[alert-runbook-urls] FAIL: arquivo nao encontrado: {alert_rules_path}", file=sys.stderr)
        return 1

    findings = evaluate(alert_rules_path, REPO_ROOT)

    if findings:
        print(
            f"[alert-runbook-urls] FAIL: {len(findings)} regra(s) de {alert_rules_path} sem "
            "runbook_url valido:",
            file=sys.stderr,
        )
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(f"[alert-runbook-urls] PASS: {alert_rules_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
