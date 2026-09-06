"""TRAIN3-INTEGRATION-ERRATA-DEDUP: the two erratas appended to
`docs/reports/autonomous-completion-report.md` describe the SAME defect.

Two branches wrote an errata for the same two lines of the same report, in parallel, and both
landed in the r5/train-3 integration train:

  * `AUTONOMOUS-REPORT-FALSE-81` — authored on `r5/r4-docs-w4`, already on `origin/main` via #337;
  * `AF-18` — authored on `r5/adr-batch`, merged by this train.

Neither was dropped: the report is append-only *by the explicit decision of both erratas* (each one
says so in its own opening paragraph, because rewriting `:58`/`:89` would erase the evidence that the
false claim was ever made and would shift the line anchors other documents cite into this file). The
train appended a third section — the integration note — recording that the two are ONE defect, so a
later auditor does not count two.

This fence exists because that note is a factual claim about the file it lives in, and a claim
nothing checks is a claim that rots. It pins, in this order:

  1. the append-only property both erratas assert — `:58` and `:89` still carry the ORIGINAL false
     claim, byte-for-byte. If a future edit "cleans up" those lines, every anchor in both erratas,
     in `docs/adr/0041-*.md` §7 and in the audit tree silently stops resolving, and this goes RED;
  2. that BOTH erratas are still present and each still cites BOTH lines — i.e. nobody resolved the
     duplication by deleting a side, which is the failure mode the integration note guards against;
  3. that the integration note names both gap ids, so the cross-reference cannot survive as prose
     while losing the ids it is supposed to join;
  4. that the third instance of the same attribution — `:139` — is still UNCORRECTED and still
     inside the follow-up the AF-18 errata opened. This is the honest half: the train did not fix it,
     and this fence refuses to let the report look finished while it stands.

Deliberately NOT asserted: any wording of either errata. They are gatekept component content; this
module fences the integration claim only.
"""

from __future__ import annotations

from pathlib import Path

REPORT = Path(__file__).resolve().parents[3] / "docs" / "reports" / "autonomous-completion-report.md"

#: The two original false claims, quoted verbatim from the pre-errata report. Both erratas cite them
#: by line number (`:58`, `:89`); the line numbers only stay true while the file stays append-only.
ORIGINAL_CLAIM_58 = "| #81 | contract_extraction DMN-driven scaffold (#21) | Sonnet | — |"
ORIGINAL_CLAIM_89 = (
    "| 21 | contract_extraction pipeline (0012) | **scaffolded** #81; source/LLM/SME = manual |"
)

ERRATA_HEADINGS = {
    "AUTONOMOUS-REPORT-FALSE-81": "## ERRATA 2026-09-04",
    "AF-18": "## ERRATA 2026-09-05",
}
INTEGRATION_NOTE_HEADING = "## NOTA DE INTEGRACAO 2026-09-05 (trem `r5/train-3`)"


def _lines() -> list[str]:
    return REPORT.read_text(encoding="utf-8").splitlines()


def test_the_two_cited_lines_still_carry_the_original_false_claim() -> None:
    """Both erratas cite `:58`/`:89` and both promise the lines were NOT rewritten."""
    lines = _lines()
    assert lines[57] == ORIGINAL_CLAIM_58, (
        "docs/reports/autonomous-completion-report.md:58 no longer carries the original claim — "
        "the report is append-only by both erratas' own decision, and every `:58` anchor "
        "(both erratas, docs/adr/0041-*.md §7, the audit tree) has just gone stale"
    )
    assert lines[88] == ORIGINAL_CLAIM_89, (
        "docs/reports/autonomous-completion-report.md:89 no longer carries the original claim — "
        "same species as `:58` above"
    )


def test_both_erratas_are_present_and_each_cites_both_lines() -> None:
    """Neither side was dropped when the two parallel erratas met in the train."""
    text = REPORT.read_text(encoding="utf-8")
    for gap, heading in ERRATA_HEADINGS.items():
        start = text.find(heading)
        assert start != -1, (
            f"the errata for gap {gap} ({heading!r}) is gone from the report — the integration "
            "note says BOTH are kept; resolving the duplication by deletion is exactly what it "
            "exists to prevent"
        )
        # Bound the section at the next top-level heading so a citation in a LATER section cannot
        # satisfy an EARLIER one.
        nxt = text.find("\n## ", start + 1)
        section = text[start : nxt if nxt != -1 else len(text)]
        for anchor in (":58", ":89"):
            assert anchor in section, (
                f"the errata for gap {gap} no longer cites `{anchor}` — an errata that does not "
                "name the line it corrects is not an errata"
            )


def test_the_integration_note_joins_the_two_gap_ids() -> None:
    """The note's whole job is to say these two ids are one defect."""
    text = REPORT.read_text(encoding="utf-8")
    start = text.find(INTEGRATION_NOTE_HEADING)
    assert start != -1, f"the r5/train-3 integration note ({INTEGRATION_NOTE_HEADING!r}) is gone"
    # Bound the section at the next top-level heading so a citation in a LATER section cannot
    # satisfy an EARLIER one (same guard as test_both_erratas_are_present_and_each_cites_both_lines).
    nxt = text.find("\n## ", start + 1)
    note = text[start : nxt if nxt != -1 else len(text)]
    for gap in ERRATA_HEADINGS:
        assert gap in note, (
            f"the integration note no longer names gap {gap}; it would then be prose about "
            "'the two erratas' with no way for a reader to tell WHICH two"
        )


def test_the_third_instance_at_139_is_still_uncorrected_and_still_disclosed() -> None:
    """`:139` repeats the same `#21`/`#81` attribution. The train did NOT fix it.

    This asserts the disclosure and the defect together, on purpose: whoever repairs `:139` must
    come here and retire this test along with the follow-up row, rather than leaving a fence that
    describes a gap that no longer exists.
    """
    lines = _lines()
    assert "#81" in lines[138] and "#21" in lines[138], (
        "docs/reports/autonomous-completion-report.md:139 no longer repeats the "
        "`contract_extraction` attribution — if it was corrected, retire this test and close the "
        "review-queue follow-up the AF-18 errata opened; if the line merely MOVED, the report "
        "stopped being append-only (see the `:58`/`:89` fence above)"
    )
    text = REPORT.read_text(encoding="utf-8")
    note_start = text.find(INTEGRATION_NOTE_HEADING)
    assert note_start != -1
    # Bound the section at the next top-level heading so a citation in a LATER section cannot
    # satisfy an EARLIER one (same guard as test_both_erratas_are_present_and_each_cites_both_lines).
    nxt = text.find("\n## ", note_start + 1)
    note = text[note_start : nxt if nxt != -1 else len(text)]
    assert "`:139`" in note, (
        "the integration note stopped disclosing that `:139` is still uncorrected — the report "
        "would then read as fully repaired while a third instance of the same false attribution "
        "stands"
    )
