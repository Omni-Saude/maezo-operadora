"""ADR-PHANTOM-PATH-RESIDUE-NON-ADR — `src/maezo/policies/` has never existed on disk. The 2026-
08-13 CODEOWNERS audit confirmed it (`git log --all --diff-filter=A` empty for the path) and kept
the CODEOWNERS line as a deliberate PRE-POSITIONED gate, never as coverage of a real path today
(`.github/CODEOWNERS`, the two comment blocks around `/src/maezo/policies/` and
`process_allowlist.yaml`). The real autonomy matrix lives at `spec/policies/autonomy/`; the real
process-key allowlist lives entirely in `src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS`
(no YAML overlay for it exists).

Eleven tracked non-ADR docs cited the phantom path (`docs/review-queue.md:1806`'s recount).  This
fence is tree-derived (`git ls-files`, never a hardcoded count) and is a BASELINE fence, same shape
as `test_contract_bpmn_dmn_citations.py::_HIT_POLICY_DRIFT_BASELINE`: every tracked non-ADR/non-
self-referential file that still cites `src/maezo/policies` must be a documented, justified entry
below — a NEW file adopting the phantom path (one nobody has reviewed) trips this fence. Docs
already fixed to the real path (`docs/runbooks/gateway.md`,
`docs/processes/harmonization-inadimplencia-cancel.md`) are intentionally NOT in the baseline —
if either regresses back to citing the phantom path, this fence must catch it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_PHANTOM = "src/maezo/policies"

# file -> one-line reason it is allowed to still contain the phantom-path string.
_BASELINE: dict[str, str] = {
    # Deliberate CODEOWNERS pre-positioned gate + its own audit comment (declares the path
    # nonexistent explicitly) — not itself a non-ADR "doc", but globbed by *.md/*.yaml/*.yml? No:
    # CODEOWNERS has no extension, so `git ls-files '*.md' '*.yaml' '*.yml'` never matches it. Kept
    # here as documentation only (this fence's own glob does not need to exclude it).
    "docs/Tarefas_Pendentes.md": (
        "cites `src/maezo/policies/` only as the NAME of a CODEOWNERS-surface pattern in a PR-"
        "classification method, never as a file with content to read (line ~577)."
    ),
    "docs/reports/review-before-launch-extension.md": (
        "same CODEOWNERS-surface-pattern-name citation as Tarefas_Pendentes.md (mirrors its "
        "review method, line ~22) — not a claim the path holds content."
    ),
    "docs/design/T1.9-ceiling-enforcement.md": (
        "deliberate v1-vs-v2 historical contrast: explicitly states v1's "
        '`_DEFAULT_CORE_PATH = "src/maezo/policies/autonomy/L0-core.yaml"` \'does NOT carry '
        "over' to v2, and that v2 resolves via `spec/policies/autonomy/` (line ~113)."
    ),
    "docs/reports/G0-gate-review.md": (
        "already declares the path 'confirmed nonexistent' (line ~71) and lists it as a fixed-"
        "elsewhere residual defect (C3, PROJECT.md/CONTRIBUTING.md, both since corrected) — not "
        "residue itself."
    ),
    "docs/reports/business-logic-audit-improvement-plan.md": (
        "historical audit-improvement-plan dated 2026-07-02, predates the T0.3/T0.4 spec/ "
        "migration; a dated 2026-09-06 addendum was added correcting the real path without "
        "rewriting the historical brief bodies."
    ),
    "docs/reports/phase3-report.md": (
        "historical Phase-3 DoD report dated 2026-06-14; a dated 2026-09-06 addendum corrects "
        "the phantom-path claim in §D7 without rewriting history."
    ),
    "docs/reports/predeploy-audit-report.md": (
        "historical pre-deployment audit dated 2026-07-04; its own 'real path' fix suggestion "
        "for gateway.md was ITSELF phantom — a dated 2026-09-06 addendum corrects this without "
        "rewriting the historical findings table."
    ),
    "docs/processes/ALLOWLIST-PR-READY.md": (
        "historical human-gated-PR staging doc; a dated 2026-09-06 addendum notes the phantom "
        "YAML overlay never existed AND that the six Phase-3 keys it stages are already live in "
        "KNOWN_PROCESS_KEYS today, making this checklist historical reference, not pending work."
    ),
    "deploy/helm/README.md": (
        "CODEOWNED (`/deploy/`, BRIEF-COMMON) — an executable `--overlay "
        "src/maezo/policies/autonomy/tenants-amh.yaml` command (line ~74) needs fixing to "
        "`spec/policies/autonomy/tenants-amh.yaml` but requires an owner-review PR, not an "
        "autonomous docs-sweep edit; reported, not fixed, by gap ADR-PHANTOM-PATH-RESIDUE-NON-ADR."
    ),
    # Self-referential tracking docs: excluded from the "11 files" count by the gap's own recount
    # convention (docs/review-queue.md:1806) — they legitimately quote the phantom path while
    # describing/tracking THIS very gap and its history (evidence-ledger rows, review-queue rows).
    "docs/evidence-ledger.md": "self-referential ledger rows describing/tracking this gap's history.",
    "docs/review-queue.md": "self-referential tracking rows (:1804, :1806) for this exact gap.",
}


def _tracked_hits() -> set[str]:
    """Tree-derived: every tracked `*.md`/`*.yaml`/`*.yml` file citing the phantom path, via a
    real `git grep` subprocess (never a hardcoded list) — mirrors the gap's own reproduction
    command exactly (`git grep -l ... -- '*.md' '*.yaml' '*.yml'`)."""
    result = subprocess.run(
        ["git", "grep", "-l", _PHANTOM, "--", "*.md", "*.yaml", "*.yml"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # exit code 1 = no matches (not an error); anything else is unexpected.
    assert result.returncode in (0, 1), f"git grep failed: {result.stderr}"
    return {line for line in result.stdout.splitlines() if line}


def test_src_maezo_policies_never_exists_on_disk() -> None:
    """Ground truth precondition for this whole fence: if this ever starts existing, every
    assumption below (and the CODEOWNERS pre-positioned-gate framing) must be revisited."""
    result = subprocess.run(
        ["git", "ls-files", "src/maezo/policies"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "", "src/maezo/policies now has tracked files — re-derive this fence"


def test_no_new_non_adr_doc_cites_the_phantom_policies_path() -> None:
    """The load-bearing fence: every tracked non-ADR file citing `src/maezo/policies` must be a
    documented, justified `_BASELINE` entry. A file NOT in `docs/adr/` and NOT in `_BASELINE`
    citing the phantom path is a NEW, unreviewed instance of this gap's defect class."""
    hits = _tracked_hits()
    non_adr_hits = {f for f in hits if not f.startswith("docs/adr/")}
    unexplained = non_adr_hits - set(_BASELINE)
    assert not unexplained, (
        f"NEW file(s) cite the phantom path 'src/maezo/policies' with no baseline justification: "
        f"{sorted(unexplained)} -- either fix the citation to the real path (spec/policies/autonomy/ "
        f"or src/maezo/tools/process_allowlist.py) or add a justified _BASELINE entry."
    )


def test_baseline_entries_are_still_actually_present() -> None:
    """The mirror check: a baseline entry whose file no longer cites the phantom path (fixed since)
    must be REMOVED from `_BASELINE`, or a future regression back to the phantom path would be
    silently permitted by a stale allowlist entry."""
    hits = _tracked_hits()
    stale = set(_BASELINE) - hits
    assert not stale, (
        f"_BASELINE entries no longer cite the phantom path (fixed!) -- remove them: {sorted(stale)}"
    )


def test_gateway_and_harmonization_docs_are_fixed_and_absent_from_baseline() -> None:
    """Pins the two REAL fixes this gap made (not just the baseline/addendum treatment of the
    historical docs): `docs/runbooks/gateway.md` (9 sites) and
    `docs/processes/harmonization-inadimplencia-cancel.md` (1 site) must cite the REAL
    `spec/policies/autonomy/` path and must NOT be in `_BASELINE` — if either regresses to the
    phantom path, this fence must catch it as an unexplained new hit, not silently pass."""
    for rel_path in (
        "docs/runbooks/gateway.md",
        "docs/processes/harmonization-inadimplencia-cancel.md",
    ):
        assert rel_path not in _BASELINE, f"{rel_path} should be FIXED, not baselined"
        text = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert _PHANTOM not in text, f"{rel_path} regressed to citing the phantom path"
        assert "spec/policies/autonomy" in text, f"{rel_path} should cite the real spec/policies path"
