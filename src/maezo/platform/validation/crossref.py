"""BPMN -> DMN cross-reference and orphan-DMN checks.

A `businessRuleTask camunda:decisionRef="X"` in a BPMN process must resolve
to a `dmn:decision id="X"` declared in *some* `.dmn` file under
`spec/processes/dmn/` — decision ids are the join key, not filenames (a
decision id need not match the name of the `.dmn` file that declares it;
e.g. `reembolso_coverage.dmn` declares `reembolso_admissibility`).

Two failure modes, both fail-closed:

1. A BPMN `decisionRef` that resolves to no DMN decision anywhere — a
   broken/corrupted cross-reference. Always an ERROR.
2. A DMN decision id that no BPMN `businessRuleTask` references — an orphan.
   Orphans are only tolerated if explicitly listed (with a one-line
   justification) in `orphans-allowlist.yaml`; an unlisted orphan is an
   ERROR — new orphans must be deliberately reviewed and allowlisted, never
   silently introduced. A stale allowlist entry (no longer orphaned, or for
   a decision id that no longer exists) is reported as a non-blocking
   notice, not an error — it is debt-visibility, not a compliance risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._loaders import ParseError, load_yaml
from .result import Report

ALLOWLIST_FILENAME = "orphans-allowlist.yaml"


@dataclass(frozen=True, slots=True)
class DecisionRef:
    """One `camunda:decisionRef` usage, with the BPMN file it came from."""

    decision_id: str
    bpmn_path: Path


def load_orphan_allowlist(path: Path, report: Report) -> dict[str, str] | None:
    """Load `{decision_id: justification}` from `orphans-allowlist.yaml`.

    A missing allowlist file is not itself an error — it just means zero
    orphans are tolerated (returns `{}`). A malformed allowlist file *is* an
    error (fail-closed: we cannot tell what's allowlisted, so nothing is).
    """
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
    except ParseError as exc:
        report.error(path, str(exc))
        return None
    if not isinstance(data, dict):
        report.error(path, "root is not a mapping")
        return None
    orphans = data.get("orphans")
    if orphans is None:
        return {}
    if not isinstance(orphans, dict):
        report.error(path, "'orphans' must be a mapping of decision id -> justification")
        return None
    allow: dict[str, str] = {}
    for key, value in orphans.items():
        if not isinstance(value, str) or not value.strip():
            report.error(path, f"orphan allowlist entry '{key}' needs a non-empty string justification")
            continue
        allow[str(key)] = value
    return allow


def check(
    decision_refs: list[DecisionRef],
    defined_ids: dict[str, Path],
    allowlist_path: Path,
    report: Report,
) -> None:
    """Cross-check BPMN decisionRefs against DMN decision ids; enforce the orphan allowlist.

    `defined_ids` maps decision id -> the `.dmn` file that declares it.
    """
    referenced: set[str] = set()
    for ref in decision_refs:
        referenced.add(ref.decision_id)
        if ref.decision_id not in defined_ids:
            report.error(
                ref.bpmn_path,
                f"camunda:decisionRef '{ref.decision_id}' does not match any dmn:decision id "
                "under spec/processes/dmn/ (broken or corrupted cross-reference)",
            )

    allowlist = load_orphan_allowlist(allowlist_path, report)
    if allowlist is None:
        return  # allowlist itself is malformed; already reported — don't pile on

    orphan_ids = sorted(set(defined_ids) - referenced)
    for did in orphan_ids:
        if did not in allowlist:
            report.error(
                defined_ids[did],
                f"dmn:decision '{did}' is not referenced by any BPMN businessRuleTask and is "
                f"not listed in {allowlist_path.name} — new orphans must be explicitly reviewed "
                "and allowlisted with a justification, not silently introduced",
            )

    stale = sorted(set(allowlist) - set(orphan_ids))
    for did in stale:
        report.notice(
            f"{allowlist_path.name}: '{did}' is allowlisted as an orphan but is no longer "
            "orphaned (or the decision id no longer exists) — consider removing the entry"
        )
