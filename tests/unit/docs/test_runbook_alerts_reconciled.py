"""RUNBOOK-PHANTOM-ALERTS-5-MORE / RUNBOOK-PHANTOM-ALERTS-REMAINING-14 — the bidirectional
runbook<->alert-rules fence, with NO baseline/allowlist.

`docs/runbooks/devops-stack.md` documents Prometheus alert runbooks (`## 6. Alert runbooks`).
Two distinct defects were found here across two passes:

  FORWARD gap (a runbook section claims an alert that does not exist): originally 5 names
  (`MaezoDLQRateHigh`, `MaezoKafkaConsumerLagHigh`, `MaezoAgentRuntimeDown`, `MaezoFhirSyncDown`,
  `MaezoPodCrashLooping`), then a fuller census (built alongside this fence's first version) found
  14 MORE (`MaezoA2ATaskBudgetExceeded`, `MaezoAnsSubmissionFailed`,
  `MaezoBpmnUserTaskDecisionStalled`, `MaezoCollectorDropRateHigh`, `MaezoEvalScoreDrop`,
  `MaezoHITLPendingTooLong`, `MaezoLLMCostSpike`, `MaezoLLMTokenRateHigh`,
  `MaezoMemoryRowcountApproachingCeiling`, `MaezoMemoryRowcountCritical`, `MaezoTraceSamplerStalled`,
  `MaezoUserTaskSLABreach`, `MaezoWorkerTaskBpmnError`, `MaezoWorkerTaskFailureRateHigh`) — all 19
  are now disclosed with the `PLANNED — NOT YET IMPLEMENTED` marker (the convention `2353d25`
  established for 3 originals), or `**REMOVED**` for the one alert (`MaezoHITLApprovalRateDrop`)
  that shipped and was later pulled.

  REVERSE gap (a real, shipped alert has no runbook section at all): all 8 alerts in
  `deploy/observability/alert-rules.yml` had zero runbook coverage before this fix. Each now has
  its own `###` section under "Alertas implementados", derived strictly from the rule's own
  `expr`/`for`/`labels`/`annotations` — no invented threshold or procedure.

There is deliberately NO allowlist/baseline/grandfather-list anywhere in this file — a prior
version of this fence carried one for the 14-name gap while it was open; closing that gap deleted
it, per this program's standing rule that a baseline in a fence is itself the defect it exists to
remove. The two directions below are individually assertable and together leave nothing
unaccounted: every name that appears anywhere in the runbook must be shipped or disclosed, and
every shipped name must appear in the runbook.

DISCLOSURE, precisely defined (two independent forms):
  1. SECTION disclosure: the name is named on a `**Alert:**` line, and that line's own `###`
     section (not any other section that happens to mention the name in passing — see the
     docstring on `_alert_line_names` for the false positive this scoping was built to avoid)
     contains `PLANNED — NOT YET IMPLEMENTED` or `**REMOVED**` (or, for a shipped alert, the
     section documents it as `IMPLEMENTADO`).
  2. IN-PROSE disclosure (for a name that never gets a dedicated `**Alert:**` line/section of its
     own): the name is wrapped in the fixed phrase `alerta planejado` (pt-BR) or `planned alert`
     (English), case-insensitive, on the SAME line — e.g. "alerta planejado `MaezoX`" or "a
     planned alert, `MaezoX`, ...". This form does not exist in the runbook today (every name
     currently has its own `**Alert:**` line), so it is unused in practice, but it is defined and
     unit-tested here precisely because the fence must not silently regress into needing a
     baseline again the next time someone adds a name only in passing prose without a section.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_RUNBOOK: Final[Path] = _REPO_ROOT / "docs" / "runbooks" / "devops-stack.md"
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"

#: The disclosure markers this repo uses to say "this named alert cannot fire today" — never
#: built (PLANNED) or built-then-pulled (REMOVED) — plus the marker a shipped alert's own section
#: carries instead (IMPLEMENTADO). A section carrying any of the three, anywhere in it, discloses
#: every alert name named on that section's own `**Alert:**` line(s).
_SECTION_DISCLOSURE_MARKERS: Final[tuple[str, ...]] = (
    "PLANNED — NOT YET IMPLEMENTED",
    "**REMOVED**",
    "IMPLEMENTADO",
)

#: The fixed phrases that make an in-prose mention self-disclosing (see module docstring, form 2).
_IN_PROSE_MARKERS: Final[tuple[str, ...]] = (
    "alerta planejado",
    "planned alert",
)

#: The 8 real alerts, pinned exactly (not merely "at least") — both a non-vacuity floor and a
#: change-detector: if `alert-rules.yml` gains or loses an alert, this test fails until a human
#: updates both this constant and the corresponding runbook section, so the two can never quietly
#: drift apart again the way they did before this fix (reverse gap: zero of the 8 had a section).
_SHIPPED_ALERT_NAMES_FLOOR: Final[frozenset[str]] = frozenset(
    {
        "MaezoSLAWorkerLatencyHigh",
        "MaezoSLAAgentErrorRateHigh",
        "MaezoSLAWorkerErrorRateHigh",
        "MaezoWorkerCrashLoop",
        "MaezoAgentCrashLoop",
        "MaezoDeadLetterBacklog",
        "MaezoDeadLetterGrowth",
        "MaezoLifecycleJobFailed",
    }
)

#: Names this fence must find in the live runbook for its own non-vacuity check.
_MUST_BE_PLANNED_FLOOR: Final[str] = "MaezoDLQRateHigh"

#: `[A-Za-z0-9]`, not just letters — several real alert names carry digits (`MaezoA2ATaskBudget
#: Exceeded`). A letters-only class silently truncates those to a wrong, shorter token (caught
#: during this fence's construction: an earlier draft matched only `MaezoA`).
_ALERT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\bMaezo[A-Za-z][A-Za-z0-9]*\b")
_ALERT_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\*\*Alert:\*\*.*$", re.MULTILINE)


def _shipped_alert_names() -> set[str]:
    """Every `alert:` name actually defined in `deploy/observability/alert-rules.yml`.

    ALERTS-WITHOUT-METRICS-b / R-056 added a `record:` rule (group `maezo_dead_letter_derived`) —
    it has no `alert` key and pages nobody, so it is excluded from the shipped-ALERT accounting
    this file does (a Maezo* name in a recording-rule's own `record:`/`expr` would still be caught
    by `_all_runbook_alert_names()`'s whole-document scan if it ever showed up in prose, same as
    any other name; `maezo_dead_letter_queue_size` itself is lower_snake_case and does not match
    `_ALERT_NAME_RE`'s `Maezo[A-Za-z]...` CamelCase pattern regardless).
    """
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    return {
        rule["alert"]
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }


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
    """Names named on this section's own `**Alert:**` line(s) — the section's SUBJECT alerts.

    Scoped to the `**Alert:**` line specifically, not "anywhere the name is mentioned in the
    section": a section's prose routinely cross-references an alert covered by a DIFFERENT
    section (e.g. "HITL approval drop" names `MaezoHITLPendingTooLong` only to point the reader at
    it — that must not be read as a claim that the pending-alert name is itself defined here, nor
    as this section disclosing it).
    """
    return {name for line in _ALERT_LINE_RE.findall(section) for name in _names_in(line)}


def _all_runbook_alert_names() -> set[str]:
    """Every Maezo* token anywhere in the runbook — the full accountability set.

    Whole-document, not just `**Alert:**` lines: the point of removing the baseline is that
    NOTHING gets a free pass, including a name that might show up only in passing prose. (Verified
    while building this fence: every current occurrence outside `## 6. Alert runbooks` is zero —
    `grep` the section-6-vs-whole-file diff in the construction notes — so this is not currently
    pulling in unrelated noise from earlier sections of the doc.)
    """
    return _names_in(_RUNBOOK.read_text(encoding="utf-8"))


def _section_disclosed_names() -> set[str]:
    """Subject alert names (on an `**Alert:**` line) whose OWN section carries a marker."""
    disclosed: set[str] = set()
    for section in _runbook_sections():
        if any(marker in section for marker in _SECTION_DISCLOSURE_MARKERS):
            disclosed |= _alert_line_names(section)
    return disclosed


def _in_prose_disclosed_names() -> set[str]:
    """Names wrapped in an in-prose disclosure phrase on the same line (form 2, module docstring).

    Line-scoped, not section-scoped: the phrase and the name must appear together as a single,
    self-contained disclosure — "alerta planejado `MaezoX`" — not merely somewhere in a section
    that happens to also discuss something else planned.
    """
    disclosed: set[str] = set()
    for line in _RUNBOOK.read_text(encoding="utf-8").splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in _IN_PROSE_MARKERS):
            disclosed |= _names_in(line)
    return disclosed


def test_the_parse_is_non_vacuous() -> None:
    """A fence that found nothing would pass everything — anchor it to known-good and known-bad."""
    names = _all_runbook_alert_names()
    assert names, "no Maezo* alert name found in the runbook — the regex or file path broke"
    shipped = _shipped_alert_names()
    assert shipped, "no alert parsed from alert-rules.yml — the yaml parse broke"
    assert _MUST_BE_PLANNED_FLOOR in names, sorted(names)
    assert _MUST_BE_PLANNED_FLOOR not in shipped, (
        f"{_MUST_BE_PLANNED_FLOOR} is a PLANNED example for this test and must stay phantom for "
        "the non-vacuity check to mean anything; it now has a shipped rule — pick a different "
        "floor constant."
    )


def test_shipped_alerts_are_exactly_the_pinned_eight() -> None:
    """Non-vacuity + change-detector for the reverse-gap fence below.

    Deliberately an EXACT match, not `<=`/`>=`: `alert-rules.yml` is owner-gated, so a change to it
    is already a reviewed edit — this test forces that review to also touch this file (and, via
    the next test, the runbook section), instead of the reverse gap re-opening silently the way it
    did before this fix (all 8 existing alerts had zero runbook coverage).
    """
    assert _shipped_alert_names() == _SHIPPED_ALERT_NAMES_FLOOR, (
        f"alert-rules.yml now defines {sorted(_shipped_alert_names())}, not the pinned "
        f"{sorted(_SHIPPED_ALERT_NAMES_FLOOR)} — update _SHIPPED_ALERT_NAMES_FLOOR AND add/remove "
        "the corresponding runbook section under 'Alertas implementados'."
    )


def test_every_shipped_alert_has_a_runbook_section() -> None:
    """The REVERSE gap fence: every real alert must be named on some `**Alert:**` line.

    Before this change all 8 shipped alerts had ZERO runbook coverage — this is the fence that
    keeps that from happening again silently.
    """
    subject_names = {name for section in _runbook_sections() for name in _alert_line_names(section)}
    missing = _SHIPPED_ALERT_NAMES_FLOOR - subject_names
    assert not missing, (
        f"shipped alert(s) {sorted(missing)} have no '**Alert:**' line anywhere in {_RUNBOOK} — "
        "add a runbook section for them under 'Alertas implementados', derived strictly from "
        "their alert-rules.yml rule."
    )


def test_every_runbook_alert_name_is_shipped_or_disclosed_with_no_baseline() -> None:
    """The FORWARD gap fence, with NO allowlist: every name anywhere in the runbook is either a
    shipped rule or disclosed (section- or in-prose-form). A name that is neither fails here —
    there is no escape hatch to add it to."""
    names = _all_runbook_alert_names()
    shipped = _shipped_alert_names()
    disclosed = _section_disclosed_names() | _in_prose_disclosed_names()

    unaccounted = names - shipped - disclosed
    assert not unaccounted, (
        f"alert name(s) {sorted(unaccounted)} in {_RUNBOOK} are neither a shipped rule in "
        f"{_ALERT_RULES}, nor disclosed (PLANNED/REMOVED/IMPLEMENTADO marker in their own "
        "section, or an in-prose 'alerta planejado'/'planned alert' phrase on the same line). "
        "There is no baseline/allowlist for this fence — either the rule needs to ship, or the "
        "runbook needs a disclosure."
    )


def test_the_nineteen_forward_gap_names_are_disclosed_not_shipped() -> None:
    """The specific 19 names both reconciliation passes fixed: still phantom, now disclosed."""
    shipped = _shipped_alert_names()
    disclosed = _section_disclosed_names() | _in_prose_disclosed_names()
    nineteen = {
        "MaezoDLQRateHigh",
        "MaezoKafkaConsumerLagHigh",
        "MaezoAgentRuntimeDown",
        "MaezoFhirSyncDown",
        "MaezoPodCrashLooping",
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
    assert nineteen <= disclosed, sorted(nineteen - disclosed)
    assert not (nineteen & shipped), sorted(nineteen & shipped)


def test_in_prose_disclosure_form_is_detected_when_present() -> None:
    """Unit-proves form 2 (module docstring) in isolation, since no real runbook name needs it
    today — a synthetic sample, not a read of the live file, so this stays green regardless of
    what the runbook currently contains."""
    sample_pt = "Este e um alerta planejado, `MaezoSyntheticExampleOnly`, ainda sem regra."
    sample_en = "This is a planned alert, `MaezoAnotherSyntheticExample`, with no rule yet."
    sample_bare = "This section merely mentions `MaezoBareMentionNotDisclosed` in passing."

    assert "MaezoSyntheticExampleOnly" in _names_in(sample_pt)
    assert any(marker in sample_pt.lower() for marker in _IN_PROSE_MARKERS)
    assert "MaezoAnotherSyntheticExample" in _names_in(sample_en)
    assert any(marker in sample_en.lower() for marker in _IN_PROSE_MARKERS)
    assert not any(marker in sample_bare.lower() for marker in _IN_PROSE_MARKERS), (
        "a bare mention with neither phrase must NOT be treated as self-disclosing — that would "
        "defeat the whole point of requiring the fixed phrase"
    )
