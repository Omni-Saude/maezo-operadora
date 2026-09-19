#!/usr/bin/env python3
"""Render a compact, human-readable summary of a SARIF file into the run's Summary tab.

WHY THIS SCRIPT EXISTS
----------------------
GitHub Advanced Security is NOT provisioned on this repository (admin-scoped check 2026-07-17:
``security_and_analysis.code_security.status == "disabled"``), so the SARIF that CodeQL — and now
Trivy — produce cannot be rendered in the Security tab. ``.github/workflows/security.yml`` therefore
runs a "GHAS-substitute" review path: publish the SARIF as a workflow artifact AND print a summary of
it in the job's step summary, so findings are reviewable without GHAS at all.

That summary used to be an 80-line heredoc inlined in the ``codeql`` job. With a SECOND CodeQL lane
(``java-kotlin``) and a Trivy IaC lane needing the identical rendering, inlining it again would mean
three copies of the same parser drifting apart. It lives here instead: one implementation, importable
and unit-testable without a runner (``tests/unit/ci/test_summarize_sarif.py``).

CONTRACT (fail-closed). A missing or malformed SARIF is a real defect — the file was just produced by
the scanner in the same job — so this script raises rather than silently skipping the summary. It
NEVER inspects severities to decide an exit code: gating is the caller's job, this only renders.

Severity resolution follows the SARIF spec: a result's own ``level`` wins; when absent, the rule's
``defaultConfiguration.level`` is used, defaulting to ``warning``. Rules are read from BOTH
``tool.driver.rules`` AND ``tool.extensions[].rules`` — codeql-action v4 keeps the real query pack's
rules under ``extensions`` with ``driver.rules`` EMPTY (live-observed on this repo's own PR
artifact), while other producers (Trivy) populate ``driver.rules``.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import Any

#: SARIF `level` values in the order the summary table lists them; anything else is appended after.
KNOWN_LEVEL_ORDER = ("error", "warning", "note", "none")

#: How many distinct rules the "top rules" table shows.
TOP_RULES = 10


def _rule_index(run: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``({rule_id: default level}, {rule_id: display name})`` for one SARIF run."""
    tool = run.get("tool", {}) or {}
    rule_sources: list[Any] = [(tool.get("driver", {}) or {}).get("rules", []) or []]
    for extension in tool.get("extensions", []) or []:
        rule_sources.append((extension or {}).get("rules", []) or [])

    default_level_by_id: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    for rules in rule_sources:
        for rule in rules:
            rule_id = (rule or {}).get("id", "")
            if not rule_id:
                continue
            default_level_by_id[rule_id] = (rule.get("defaultConfiguration", {}) or {}).get(
                "level", "warning"
            )
            short_desc = (rule.get("shortDescription", {}) or {}).get("text", "")
            name_by_id[rule_id] = rule.get("name") or short_desc
    return default_level_by_id, name_by_id


def summarize(sarif: dict[str, Any]) -> tuple[collections.Counter, collections.Counter, dict[str, str]]:
    """Count results by SARIF ``level`` and by rule id, and collect rule display names."""
    level_counts: collections.Counter = collections.Counter()
    rule_counts: collections.Counter = collections.Counter()
    rule_names: dict[str, str] = {}

    for run in sarif.get("runs", []) or []:
        default_level_by_id, name_by_id = _rule_index(run)
        rule_names.update(name_by_id)
        for result in run.get("results", []) or []:
            rule_id = result.get("ruleId", "unknown")
            level = result.get("level") or default_level_by_id.get(rule_id, "warning")
            level_counts[level] += 1
            rule_counts[rule_id] += 1

    return level_counts, rule_counts, rule_names


def render(
    *,
    label: str,
    artifact_name: str,
    level_counts: collections.Counter,
    rule_counts: collections.Counter,
    rule_names: dict[str, str],
) -> str:
    """Render the Markdown block appended to ``$GITHUB_STEP_SUMMARY``."""
    total = sum(level_counts.values())
    lines = [
        f"## {label} — SARIF summary (GHAS-substitute review path)",
        "",
        f"Total results in this scan: **{total}**",
        "",
        f"Full SARIF is attached as the `{artifact_name}` workflow artifact",
        "(30-day retention) for offline review without GitHub Advanced Security.",
        "",
        "| Severity (SARIF `level`) | Count |",
        "|---|---|",
    ]
    for level in KNOWN_LEVEL_ORDER:
        if level_counts.get(level):
            lines.append(f"| {level} | {level_counts[level]} |")
    for level, count in level_counts.items():
        if level not in KNOWN_LEVEL_ORDER:
            lines.append(f"| {level} | {count} |")
    if total == 0:
        lines.append("| (none) | 0 |")

    lines += ["", "### Top rules by finding count", "", "| Rule ID | Name | Count |", "|---|---|---|"]
    if rule_counts:
        for rule_id, count in rule_counts.most_common(TOP_RULES):
            name = rule_names.get(rule_id, "").replace("|", "\\|")
            lines.append(f"| `{rule_id}` | {name} | {count} |")
    else:
        lines.append("| (none) | — | 0 |")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarif", required=True, help="path to the SARIF file produced by the scan")
    parser.add_argument("--label", required=True, help="lane label, e.g. 'codeql / java-kotlin'")
    parser.add_argument(
        "--artifact-name", required=True, help="workflow artifact the full SARIF is published as"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="file to append to (default: $GITHUB_STEP_SUMMARY; required on a runner)",
    )
    args = parser.parse_args(argv)

    sarif_path = Path(args.sarif)
    # Fail-closed: the scan step in the same job just produced this file. Its absence or a parse
    # error is a defect to surface loudly, never a summary to skip quietly.
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))

    level_counts, rule_counts, rule_names = summarize(sarif)
    block = render(
        label=args.label,
        artifact_name=args.artifact_name,
        level_counts=level_counts,
        rule_counts=rule_counts,
        rule_names=rule_names,
    )

    output = args.output or os.environ.get("GITHUB_STEP_SUMMARY")
    if not output:
        raise SystemExit("no --output and no $GITHUB_STEP_SUMMARY — refusing to drop the summary silently")
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(block)

    total = sum(level_counts.values())
    print(
        f"SARIF summary written for {args.label}: {total} result(s) across "
        f"{len(level_counts)} level(s), {len(rule_counts)} distinct rule(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
