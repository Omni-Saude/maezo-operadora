"""RUNBOOK-PHANTOM-ALERTS-5-MORE — the runbook<->alert-rules fence.

`docs/runbooks/devops-stack.md` documents Prometheus alert runbooks (`## 6. Alert runbooks`).
Before this change, five of those alert names had no corresponding rule anywhere in
`deploy/observability/alert-rules.yml` and nothing in the doc said so: `MaezoDLQRateHigh`,
`MaezoKafkaConsumerLagHigh`, `MaezoAgentRuntimeDown`, `MaezoFhirSyncDown`, `MaezoPodCrashLooping`
(the pattern is the same class of defect as `RUNBOOK-PHANTOM-METRICS`, commit `2353d25`, which
reconciled three others: `MaezoPEPDenySpike`, `MaezoAuditLagHigh`, `MaezoEscalationRateHigh`). A
reader has no way to tell "this alert exists and can fire" from "this alert is aspirational prose"
without independently grepping `alert-rules.yml` — exactly the defect class ALERTS-WITHOUT-METRICS-a
names for metrics, applied here to alert RULES.

WHAT THIS FENCE ASSERTS: every `Maezo<Name>` token that names an alert on an `**Alert:**` line in
the runbook is either (a) a real, shipped rule in `alert-rules.yml`, or (b) named on an `**Alert:**`
line whose `###` section carries an explicit disclosure marker — `PLANNED — NOT YET IMPLEMENTED`
(the convention `2353d25` established, reused by this fix) or `**REMOVED**` (the convention the
pre-existing "HITL approval drop" section uses for an alert that shipped and was later pulled). The
check is scoped to the `**Alert:**` line specifically (not "anywhere the name is mentioned in the
section"), because a section's prose routinely cross-references an alert covered by a DIFFERENT
section (e.g. "HITL approval drop" names `MaezoHITLPendingTooLong` only to point the reader at it —
that is not a disclosure that the pending-alert name is itself phantom).

It does NOT assert the runbook covers every SHIPPED alert (a documentation-coverage gap in the
other direction — most of the 8 real alerts are not mentioned in the runbook at all — is a
separate, undisclosed gap, out of scope here) and it does NOT assert every remaining phantom name
in the file is disclosed: the full census this fence's construction required turned up 14 MORE
undisclosed phantom alert names beyond the 5 this change fixes (see
`_KNOWN_UNRECONCILED_PHANTOM_ALERTS` below) — reconciling those is out of scope for
RUNBOOK-PHANTOM-ALERTS-5-MORE and is disclosed here, not silently swallowed, so this fence does not
regress further (a FRESH undisclosed name still fails it) without claiming a completeness this
change does not deliver.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_RUNBOOK: Final[Path] = _REPO_ROOT / "docs" / "runbooks" / "devops-stack.md"
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"

#: The disclosure markers this repo uses, in this file, to say "this named alert cannot fire
#: today" — either it was never built (PLANNED) or it was built and later pulled (REMOVED). A
#: section carrying either, anywhere in it, discloses every alert name named on that section's own
#: `**Alert:**` line(s) — matching the existing house style (RUNBOOK-PHANTOM-METRICS, `2353d25`)
#: of one **Status:** paragraph per `###` section.
_DISCLOSURE_MARKERS: Final[tuple[str, ...]] = (
    "PLANNED — NOT YET IMPLEMENTED",
    "**REMOVED**",
)

#: Alert names the runbook mentions on an `**Alert:**` line with NEITHER a shipped rule NOR a
#: disclosure marker in that line's own section, found by this fence's own construction
#: (2026-09-03) and confirmed OUT OF SCOPE for RUNBOOK-PHANTOM-ALERTS-5-MORE (which reconciles
#: exactly 5 named gaps: `MaezoDLQRateHigh`, `MaezoKafkaConsumerLagHigh`, `MaezoAgentRuntimeDown`,
#: `MaezoFhirSyncDown`, `MaezoPodCrashLooping` — all NOT in this set, all disclosed by this same
#: change). Each entry is disclosed debt, not a silent pass: it is checked against the LIVE
#: runbook content below, not a frozen copy, so removing an entry's disclosure without adding a
#: marker makes this fence catch it immediately. A brand-new undisclosed name is NOT covered by
#: this allowlist and fails the fence — this list is a closed, dated inventory, not an open escape
#: hatch (widening it is a reviewed edit, exactly like `EXTERNAL_ALERT_METRICS` in
#: `test_alert_metrics_fence.py`). Suggested follow-up gap id for reconciling these:
#: `RUNBOOK-PHANTOM-ALERTS-REMAINING-14`.
_KNOWN_UNRECONCILED_PHANTOM_ALERTS: Final[frozenset[str]] = frozenset(
    {
        "MaezoA2ATaskBudgetExceeded",
        "MaezoAnsSubmissionFailed",
        "MaezoBpmnUserTaskDecisionStalled",
        "MaezoCollectorDropRateHigh",
        "MaezoEvalScoreDrop",
        "MaezoHITLPendingTooLong",
        "MaezoLLMCostSpike",
        "MaezoLLMTokenRateHigh",
        "MaezoMemoryRowcountApproachingCeiling",
        "MaezoMemoryRowcountCritical",
        "MaezoTraceSamplerStalled",
        "MaezoUserTaskSLABreach",
        "MaezoWorkerTaskBpmnError",
        "MaezoWorkerTaskFailureRateHigh",
    }
)

#: Names this fence must find in the live runbook for its own non-vacuity check — proof the parse
#: still works, not the closed set of names the file may contain.
_MUST_BE_IMPLEMENTED_FLOOR: Final[str] = "MaezoAgentCrashLoop"
_MUST_BE_PLANNED_FLOOR: Final[str] = "MaezoDLQRateHigh"

#: `[A-Za-z0-9]`, not just letters — several real alert names carry digits (`MaezoA2ATaskBudget
#: Exceeded`). A letters-only class silently truncates those to a wrong, shorter token.
_ALERT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\bMaezo[A-Za-z][A-Za-z0-9]*\b")
_ALERT_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\*\*Alert:\*\*.*$", re.MULTILINE)


def _shipped_alert_names() -> set[str]:
    """Every `alert:` name actually defined in `deploy/observability/alert-rules.yml`."""
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    return {rule["alert"] for group in document["groups"] for rule in group["rules"]}


def _runbook_sections() -> list[str]:
    """The runbook split into `###`-header-delimited chunks (the doc's own alert-runbook unit).

    The preamble before the first `###` is its own chunk; irrelevant here since no `**Alert:**`
    line lives there, but included so the split is total and never drops text.
    """
    text = _RUNBOOK.read_text(encoding="utf-8")
    return re.split(r"(?=^### )", text, flags=re.MULTILINE)


def _names_in(text: str) -> set[str]:
    return set(_ALERT_NAME_RE.findall(text))


def _alert_line_names(section: str) -> set[str]:
    """Names named on this section's own `**Alert:**` line(s) — the section's SUBJECT alerts."""
    return {name for line in _ALERT_LINE_RE.findall(section) for name in _names_in(line)}


def _all_runbook_subject_alert_names() -> set[str]:
    """Every name that appears on an `**Alert:**` line anywhere in the runbook.

    This is the set the fence holds accountable — a name mentioned only in passing prose (a
    cross-reference, a metric name that happens to share the `Maezo` prefix pattern) is not itself
    a claim that the alert exists, so it is not required to be shipped or disclosed.
    """
    return {name for section in _runbook_sections() for name in _alert_line_names(section)}


def _disclosed_subject_alert_names() -> set[str]:
    """Subject alert names whose OWN section carries a disclosure marker."""
    disclosed: set[str] = set()
    for section in _runbook_sections():
        if any(marker in section for marker in _DISCLOSURE_MARKERS):
            disclosed |= _alert_line_names(section)
    return disclosed


def test_the_parse_is_non_vacuous() -> None:
    """A fence that found nothing would pass everything — anchor it to known-good and known-bad."""
    names = _all_runbook_subject_alert_names()
    assert names, "no **Alert:** line with a Maezo* name found — the regex or file path broke"
    shipped = _shipped_alert_names()
    assert shipped, "no alert parsed from alert-rules.yml — the yaml parse broke"
    assert _MUST_BE_IMPLEMENTED_FLOOR in shipped, sorted(shipped)
    assert _MUST_BE_PLANNED_FLOOR in names, sorted(names)
    assert _MUST_BE_PLANNED_FLOOR not in shipped, (
        f"{_MUST_BE_PLANNED_FLOOR} is a PLANNED example for this test and must stay phantom for "
        "the non-vacuity check to mean anything; it now has a shipped rule — pick a different "
        "floor constant."
    )


def test_every_runbook_alert_name_is_shipped_disclosed_or_known_debt() -> None:
    """The fence: every `**Alert:**`-line name in the runbook is accounted for by one of three
    buckets — shipped, disclosed in its own section, or dated known debt.

    A name that is none of the three is a FRESH undisclosed phantom alert and fails here.
    """
    names = _all_runbook_subject_alert_names()
    shipped = _shipped_alert_names()
    disclosed = _disclosed_subject_alert_names()

    unaccounted = names - shipped - disclosed - _KNOWN_UNRECONCILED_PHANTOM_ALERTS
    assert not unaccounted, (
        f"alert name(s) {sorted(unaccounted)} on an **Alert:** line in {_RUNBOOK} are neither a "
        f"shipped rule in {_ALERT_RULES}, nor disclosed with a PLANNED/REMOVED marker in their own "
        "section, nor in the dated known-debt allowlist. Either the rule needs to ship, or the "
        "section needs a disclosure marker (see RUNBOOK-PHANTOM-ALERTS-5-MORE for the wording)."
    )


def test_the_five_gaps_this_change_fixes_are_disclosed_not_shipped() -> None:
    """RUNBOOK-PHANTOM-ALERTS-5-MORE's own five names: still phantom, now disclosed as such."""
    shipped = _shipped_alert_names()
    disclosed = _disclosed_subject_alert_names()
    five = {
        "MaezoDLQRateHigh",
        "MaezoKafkaConsumerLagHigh",
        "MaezoAgentRuntimeDown",
        "MaezoFhirSyncDown",
        "MaezoPodCrashLooping",
    }
    assert five <= disclosed, sorted(five - disclosed)
    assert not (five & shipped), sorted(five & shipped)


def test_known_debt_allowlist_names_are_genuinely_absent_from_both_shipped_and_disclosed() -> None:
    """The allowlist is a closed, honest inventory — not a mislabel hiding a name that IS fine.

    If a name here were actually shipped or disclosed, it should not be on this list (a stale
    allowlist entry hides nothing dangerous, but it is dead weight this test should catch). If a
    name here no longer appears in the runbook at all, it should also be removed.
    """
    shipped = _shipped_alert_names()
    disclosed = _disclosed_subject_alert_names()
    names = _all_runbook_subject_alert_names()

    stale = _KNOWN_UNRECONCILED_PHANTOM_ALERTS & (shipped | disclosed)
    assert not stale, f"allowlist entry now shipped or disclosed — remove from the list: {stale}"

    vanished = _KNOWN_UNRECONCILED_PHANTOM_ALERTS - names
    assert not vanished, (
        f"allowlist entry no longer names an **Alert:** line in the runbook — remove: {vanished}"
    )
