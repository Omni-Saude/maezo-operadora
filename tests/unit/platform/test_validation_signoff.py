"""Unit tests for maezo.platform.validation.signoff — human sign-off gate (T2.2).

`validate_signoffs()` is a real, fail-closed gate: every test in
`TestMutation*` below asserts `not report.ok` for a genuine problem — there
is no test left here that blesses the old always-pass stub (defect B2/B8,
signoff side). `TestHappyPath` proves both an unsigned DRAFT and a properly
signed-off FINAL contract validate clean, and `TestGrandfatherException`
proves the one narrow, visible exception behaves exactly as scoped (never a
silent pass, never a general allowlist).
"""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.result import Report
from maezo.platform.validation.signoff import _flag_case_duplicates, validate_signoffs

APPROVED_V1 = """\
- reviewer_name: "Dra. Ana Souza"
  role: "medico-auditor"
  date: "2026-07-10"
  contract_version_reviewed: "v1.0.0"
  verdict: "approved"
  notes: "Confirmed SLA values against Lei 9.656/98 art. 35-C."
"""

NEEDS_CHANGES_V1 = """\
- reviewer_name: "Dra. Ana Souza"
  role: "medico-auditor"
  date: "2026-07-10"
  contract_version_reviewed: "v1.0.0"
  verdict: "needs-changes"
  notes: "SLA timer for grave severity needs to cite the current RN."
"""

DEFAULT_TRACKER = (
    "# SME review tracker\n\n"
    "| # | Contract ID | Status (version) | Roles assigned |\n"
    "|---|---|---|---|\n"
    "| 1 | SP-OP-ESCALATION-001 | **FINAL (v1.0.0)** | MA (retro-verification) |\n"
    "| 2 | SP-OP-AUTH-001 | DRAFT (v0.1.0) | MA |\n"
    "| 3 | SP-OP-AMBIG-001 | DRAFT (v0.1.0) | MA |\n"
)


def _write_contract(
    contracts_dir: Path, contract_id: str, status: str = "DRAFT", version: str = "0.1.0"
) -> Path:
    contracts_dir.mkdir(parents=True, exist_ok=True)
    text = f"# Contrato — {contract_id}\n\n**Status:** {status} (v{version}) — test fixture\n"
    path = contracts_dir / f"{contract_id}.md"
    path.write_text(text)
    return path


def _write_signoff(contracts_dir: Path, contract_id: str, body: str) -> Path:
    signoffs_dir = contracts_dir / "signoffs"
    signoffs_dir.mkdir(parents=True, exist_ok=True)
    path = signoffs_dir / f"{contract_id}.signoff.yaml"
    path.write_text(body)
    return path


def _write_tracker(tmp_path: Path, text: str = DEFAULT_TRACKER) -> Path:
    path = tmp_path / "tracker.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_exceptions(
    contracts_dir: Path,
    contract_id: str,
    justification: str = "Pre-existing debt, see tracker.md.",
    tracker_ref: str = "docs/sme-dispatch/tracker.md",
) -> Path:
    signoffs_dir = contracts_dir / "signoffs"
    signoffs_dir.mkdir(parents=True, exist_ok=True)
    path = signoffs_dir / "retro-verification-pending.yaml"
    path.write_text(
        "entries:\n"
        f"  - contract_id: {contract_id}\n"
        f'    justification: "{justification}"\n'
        f"    tracker_ref: {tracker_ref}\n"
    )
    return path


# ---------------------------------------------------------------------------
# Status-line parsing
# ---------------------------------------------------------------------------


class TestStatusParsing:
    def test_missing_status_line_is_an_error(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "SP-OP-X-001.md").write_text("# Contrato sem status\n")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("Status" in f.message for f in report.findings)


class TestMutationAmbiguousStatus:
    """Regression for T2.2 verification cycle 1 (CRITICAL, fail-open): the first
    `**Status:**` token used to win, so a contract whose operative status is FINAL
    but which contained an earlier DRAFT-shaped token was classified DRAFT and its
    signoff requirement silently skipped (exit 0). All three verifier phrasings
    must now be ERROR findings — ambiguity resolves to FAIL, never to the more
    permissive class."""

    def _run(self, tmp_path: Path, contract_text: str) -> Report:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "SP-OP-AMBIG-001.md").write_text(contract_text)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        return report

    def test_inline_promotion_note_fails(self, tmp_path: Path) -> None:
        """Verifier phrasing (a): a single Status line whose promotion note carries
        the operative FINAL — one marker, two conflicting status/version pairs."""
        report = self._run(
            tmp_path,
            "# Contrato — SP-OP-AMBIG-001\n\n**Status:** DRAFT (v0.9.0) -> now promoted to FINAL (v1.0.0)\n",
        )
        assert not report.ok
        assert any("ambiguous status declaration" in f.message for f in report.findings)

    def test_history_draft_line_above_real_final_line_fails(self, tmp_path: Path) -> None:
        """Verifier phrasing (b): a history/changelog DRAFT line above the real FINAL line."""
        report = self._run(
            tmp_path,
            "# Contrato — SP-OP-AMBIG-001\n\n"
            "## History\n\n"
            "**Status:** DRAFT (v0.9.0) — superseded 2026-06-01\n\n"
            "**Status:** FINAL (v1.0.0)\n",
        )
        assert not report.ok
        assert any("ambiguous status declaration" in f.message for f in report.findings)
        # The finding must pinpoint each conflicting token's line for the human fixing it.
        assert any("at line 5" in f.message and "at line 7" in f.message for f in report.findings)

    def test_superseded_paragraph_quoting_old_status_fails(self, tmp_path: Path) -> None:
        """Verifier phrasing (c): prose quoting the superseded line next to the current one."""
        report = self._run(
            tmp_path,
            "# Contrato — SP-OP-AMBIG-001\n\n"
            "This **Status:** DRAFT (v0.9.0) declaration is superseded. "
            "Current: **Status:** FINAL (v1.0.0)\n",
        )
        assert not report.ok
        assert any("ambiguous status declaration" in f.message for f in report.findings)

    def test_conflicting_versions_same_status_fails(self, tmp_path: Path) -> None:
        """Two FINAL tokens that disagree only on version are still ambiguous — the gate
        cannot know which version a signoff must match."""
        report = self._run(
            tmp_path,
            "# Contrato — SP-OP-AMBIG-001\n\n**Status:** FINAL (v1.0.0)\n\n**Status:** FINAL (v2.0.0)\n",
        )
        assert not report.ok
        assert any("ambiguous status declaration" in f.message for f in report.findings)

    def test_multiple_agreeing_tokens_pass(self, tmp_path: Path) -> None:
        """Repeated but IDENTICAL declarations are unambiguous: classified FINAL (v1.0.0),
        so with an approved current-version signoff on file the gate passes."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "SP-OP-AMBIG-001.md").write_text(
            "# Contrato — SP-OP-AMBIG-001\n\n"
            "**Status:** FINAL (v1.0.0)\n\n"
            "## Recap\n\n"
            "**Status:** FINAL (v1.0.0)\n"
        )
        _write_signoff(contracts_dir, "SP-OP-AMBIG-001", APPROVED_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert report.ok, [f.message for f in report.findings]

    def test_multiple_agreeing_final_tokens_still_require_signoff(self, tmp_path: Path) -> None:
        """The agreeing-tokens path must not weaken the gate: without a signoff it fails."""
        report = self._run(
            tmp_path,
            "# Contrato — SP-OP-AMBIG-001\n\n**Status:** FINAL (v1.0.0)\n\n**Status:** FINAL (v1.0.0)\n",
        )
        assert not report.ok
        assert any("no signoff file" in f.message for f in report.findings)


class TestMissingContractsDir:
    def test_missing_contracts_dir_is_an_error(self, tmp_path: Path) -> None:
        report = Report()
        validate_signoffs(tmp_path / "does-not-exist", tmp_path / "tracker.md", report)
        assert not report.ok

    def test_no_contract_files_is_an_error(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        report = Report()
        validate_signoffs(contracts_dir, tmp_path / "tracker.md", report)
        assert not report.ok


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_draft_without_signoff_passes(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="DRAFT", version="0.1.0")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert report.ok, [f.message for f in report.findings]

    def test_final_with_approved_current_version_signoff_passes(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-ESCALATION-001", APPROVED_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert report.ok, [f.message for f in report.findings]


# ---------------------------------------------------------------------------
# Mutation: FINAL contract, no signoff file at all
# ---------------------------------------------------------------------------


class TestMutationFinalWithoutSignoff:
    def test_final_without_any_signoff_file_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("no signoff file" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Mutation: verdict needs-changes at the current version
# ---------------------------------------------------------------------------


class TestMutationNeedsChangesVerdict:
    def test_final_contract_needs_changes_at_current_version_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", NEEDS_CHANGES_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("non-approved verdict" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Mutation: version mismatch
# ---------------------------------------------------------------------------


class TestMutationVersionMismatch:
    def test_signoff_at_stale_version_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        # Contract is now v2.0.0 but the only signoff on file reviewed v1.0.0.
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="2.0.0")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", APPROVED_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("no signoff record for" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Mutation: missing/empty required field
# ---------------------------------------------------------------------------


class TestMutationMissingField:
    def test_record_missing_reviewer_name_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        body = (
            '- role: "medico-auditor"\n'
            '  date: "2026-07-10"\n'
            '  contract_version_reviewed: "v1.0.0"\n'
            '  verdict: "approved"\n'
            '  notes: "ok"\n'
        )
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", body)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("reviewer_name" in f.message for f in report.findings)

    def test_record_with_empty_notes_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        body = (
            '- reviewer_name: "Dra. Ana Souza"\n'
            '  role: "medico-auditor"\n'
            '  date: "2026-07-10"\n'
            '  contract_version_reviewed: "v1.0.0"\n'
            '  verdict: "approved"\n'
            '  notes: ""\n'
        )
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", body)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("notes" in f.message for f in report.findings)


class TestInvalidFieldValues:
    def test_invalid_verdict_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        body = APPROVED_V1.replace("approved", "rubber-stamped")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", body)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("invalid verdict" in f.message for f in report.findings)

    def test_invalid_role_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        body = APPROVED_V1.replace("medico-auditor", "intern-friend-of-the-family")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", body)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("invalid role" in f.message for f in report.findings)

    def test_invalid_version_format_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        body = APPROVED_V1.replace("v1.0.0", "1.0.0 final")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", body)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("is not a version string" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Mutation: malformed YAML / bad root shape
# ---------------------------------------------------------------------------


class TestMutationMalformedYaml:
    def test_malformed_signoff_yaml_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", "- reviewer_name: [not\n  valid: :: yaml")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("malformed signoff file" in f.message for f in report.findings)

    def test_root_not_a_list_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", "reviewer_name: solo record, not a list\n")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("must be a YAML list" in f.message for f in report.findings)

    def test_empty_list_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-AUTH-001", "[]\n")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("zero records" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Signoff file for a nonexistent contract / case mismatches
# ---------------------------------------------------------------------------


class TestNonexistentContractAndCaseHandling:
    def test_signoff_for_nonexistent_contract_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="DRAFT", version="0.1.0")
        _write_signoff(contracts_dir, "SP-OP-GHOST-999", APPROVED_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("nonexistent contract" in f.message for f in report.findings)

    def test_signoff_filename_case_mismatch_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="DRAFT", version="0.1.0")
        _write_signoff(contracts_dir, "sp-op-auth-001", APPROVED_V1)
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("case must exactly match" in f.message for f in report.findings)

    def test_duplicate_signoff_files_differing_in_case_fail(self, tmp_path: Path) -> None:
        # Exercises `_flag_case_duplicates()` directly with synthetic paths rather than
        # writing two case-colliding files to disk: on a case-insensitive filesystem
        # (e.g. default macOS APFS) two such writes collapse into a single file, which
        # would make this test pass or fail depending on the dev machine rather than on
        # the gate's actual logic. CI (Linux/ext4) is case-sensitive, so the real-world
        # collision this guards against is a genuine possibility there.
        report = Report()
        signoffs_dir = tmp_path / "signoffs"
        a = signoffs_dir / "SP-OP-AUTH-001.signoff.yaml"
        b = signoffs_dir / "sp-op-auth-001.signoff.yaml"
        ambiguous = _flag_case_duplicates([a, b], report)
        assert not report.ok
        assert any("differing only in case" in f.message for f in report.findings)
        assert ambiguous == {"sp-op-auth-001.signoff.yaml"}


# ---------------------------------------------------------------------------
# Grandfather exception list
# ---------------------------------------------------------------------------


class TestGrandfatherException:
    def test_valid_exception_grants_pass_with_loud_notice(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        _write_exceptions(contracts_dir, "SP-OP-ESCALATION-001")
        tracker = _write_tracker(tmp_path)  # flags SP-OP-ESCALATION-001 for retro-verification
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert report.ok, [f.message for f in report.findings]
        assert report.notices, "grandfather grant must be visible, not silent"
        assert any("GRANDFATHERED" in n and "SP-OP-ESCALATION-001" in n for n in report.notices)
        assert any("::warning::" in n for n in report.notices)

    def test_exception_listing_a_draft_contract_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="DRAFT", version="0.1.0")
        _write_exceptions(contracts_dir, "SP-OP-AUTH-001")
        tracker = _write_tracker(tmp_path, DEFAULT_TRACKER + "SP-OP-AUTH-001 (retro-verification)\n")
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("is not FINAL" in f.message for f in report.findings)

    def test_exception_listing_nonexistent_contract_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-AUTH-001", status="DRAFT", version="0.1.0")
        _write_exceptions(contracts_dir, "SP-OP-GHOST-999")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("nonexistent contract" in f.message for f in report.findings)

    def test_exception_for_contract_with_existing_signoff_is_obsolete(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-ESCALATION-001", APPROVED_V1)
        _write_exceptions(contracts_dir, "SP-OP-ESCALATION-001")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("obsolete" in f.message for f in report.findings)

    def test_exception_not_flagged_in_tracker_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        _write_exceptions(contracts_dir, "SP-OP-ESCALATION-001")
        # Tracker mentions the contract but never says retro-verification.
        tracker = _write_tracker(tmp_path, "| 1 | SP-OP-ESCALATION-001 | FINAL | none |\n")
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("cross-check failed" in f.message for f in report.findings)

    def test_exception_with_missing_tracker_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        _write_exceptions(contracts_dir, "SP-OP-ESCALATION-001")
        report = Report()
        validate_signoffs(contracts_dir, tmp_path / "does-not-exist-tracker.md", report)
        assert not report.ok
        assert any("tracker.md not found" in f.message for f in report.findings)

    def test_malformed_exceptions_file_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        signoffs_dir = contracts_dir / "signoffs"
        signoffs_dir.mkdir(parents=True)
        (signoffs_dir / "retro-verification-pending.yaml").write_text("entries: [not\n  valid: :: yaml")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("malformed exceptions file" in f.message for f in report.findings)

    def test_exceptions_entry_missing_justification_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        signoffs_dir = contracts_dir / "signoffs"
        signoffs_dir.mkdir(parents=True)
        (signoffs_dir / "retro-verification-pending.yaml").write_text(
            "entries:\n  - contract_id: SP-OP-ESCALATION-001\n    tracker_ref: docs/sme-dispatch/tracker.md\n"
        )
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("justification" in f.message for f in report.findings)

    def test_duplicate_exception_entries_fail(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        signoffs_dir = contracts_dir / "signoffs"
        signoffs_dir.mkdir(parents=True)
        (signoffs_dir / "retro-verification-pending.yaml").write_text(
            "entries:\n"
            "  - contract_id: SP-OP-ESCALATION-001\n"
            '    justification: "first"\n'
            "    tracker_ref: docs/sme-dispatch/tracker.md\n"
            "  - contract_id: SP-OP-ESCALATION-001\n"
            '    justification: "second"\n'
            "    tracker_ref: docs/sme-dispatch/tracker.md\n"
        )
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("listed more than once" in f.message for f in report.findings)

    def test_missing_exceptions_file_means_no_exceptions_granted(self, tmp_path: Path) -> None:
        """No retro-verification-pending.yaml at all -> FINAL contract just needs a real signoff."""
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-ESCALATION-001", status="FINAL", version="1.0.0")
        tracker = _write_tracker(tmp_path)
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        assert not report.ok
        assert any("no signoff file" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Exact role set from tracker.md (onda 0 da chancela de regras; DL-0194/ADR-0080 PROPOSTO)
# ---------------------------------------------------------------------------


def _record(role: str, verdict: str = "approved", version: str = "v1.0.0") -> str:
    return (
        f'- reviewer_name: "Pessoa {role}"\n'
        f'  role: "{role}"\n'
        '  date: "2026-07-10"\n'
        f'  contract_version_reviewed: "{version}"\n'
        f'  verdict: "{verdict}"\n'
        '  notes: "ok"\n'
    )


def _tracker_with(roles_cell: str, contract_id: str = "SP-OP-PAGTO-001") -> str:
    return (
        "| # | Contract ID | Status (version) | Roles assigned | Dispatch status |\n"
        "|---|---|---|---|---|\n"
        f"| 1 | {contract_id} | **FINAL (v1.0.0)** | {roles_cell} | owner action |\n"
    )


class TestExactRoleSet:
    def _run(
        self, tmp_path: Path, roles_cell: str, body: str, contract_id: str = "SP-OP-PAGTO-001"
    ) -> Report:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-PAGTO-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-PAGTO-001", body)
        tracker = _write_tracker(tmp_path, _tracker_with(roles_cell, contract_id))
        report = Report()
        validate_signoffs(contracts_dir, tracker, report)
        return report

    def test_every_tracker_role_approved_passes(self, tmp_path: Path) -> None:
        body = _record("financas") + _record("juridico") + _record("po")
        report = self._run(tmp_path, "FIN, JU (light), PO", body)
        assert report.ok, [f.message for f in report.findings]

    def test_partial_signoff_fails(self, tmp_path: Path) -> None:
        """The RED of backlog 5: one approval out of three is not a sign-off."""
        report = self._run(tmp_path, "FIN, JU (light), PO", _record("financas"))
        assert not report.ok
        assert any(
            "missing an 'approved' sign-off" in f.message and "'juridico'" in f.message
            for f in report.findings
        )

    def test_approval_at_stale_version_does_not_count_for_the_role(self, tmp_path: Path) -> None:
        body = _record("financas") + _record("po") + _record("juridico", version="v0.9.0")
        report = self._run(tmp_path, "FIN, JU, PO", body)
        assert any("'juridico'" in f.message and "missing" in f.message for f in report.findings)

    def test_role_outside_tracker_set_fails(self, tmp_path: Path) -> None:
        body = _record("financas") + _record("dpo")
        report = self._run(tmp_path, "FIN", body)
        assert any("not assigned" in f.message and "'dpo'" in f.message for f in report.findings)

    def test_dono_is_a_valid_role_when_the_tracker_assigns_it(self, tmp_path: Path) -> None:
        report = self._run(tmp_path, "FIN, DONO", _record("financas") + _record("dono"))
        assert report.ok, [f.message for f in report.findings]

    def test_final_contract_without_tracker_row_fails(self, tmp_path: Path) -> None:
        report = self._run(tmp_path, "FIN", _record("financas"), contract_id="SP-OP-OUTRO-001")
        assert any("has no row in docs/sme-dispatch/tracker.md" in f.message for f in report.findings)

    def test_unknown_abbreviation_fails_closed(self, tmp_path: Path) -> None:
        report = self._run(tmp_path, "FIN, ATU", _record("financas"))
        assert any("unknown role abbreviation" in f.message and "ATU" in f.message for f in report.findings)

    def test_missing_tracker_fails_closed_for_final_with_signoff(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        _write_contract(contracts_dir, "SP-OP-PAGTO-001", status="FINAL", version="1.0.0")
        _write_signoff(contracts_dir, "SP-OP-PAGTO-001", _record("financas"))
        report = Report()
        validate_signoffs(contracts_dir, tmp_path / "nao-existe.md", report)
        assert any("cannot read the role set" in f.message for f in report.findings)

    def test_real_tracker_parses_every_contract_without_problem(self) -> None:
        """Non-vacuity: the versioned tracker yields a known role set for each of its rows."""
        from maezo.platform.validation.signoff import parse_tracker_roles

        repo = Path(__file__).resolve().parents[3]
        roles = parse_tracker_roles((repo / "docs/sme-dispatch/tracker.md").read_text(encoding="utf-8"))
        assert len(roles) >= 16
        assert all(entry.problem is None and entry.roles for entry in roles.values())
        assert roles["SP-OP-PAGTO-001"].roles == {"financas", "juridico", "po"}
        assert roles["SP-OP-CRED-001"].roles == {"medico-auditor", "juridico", "regulatorio", "po"}
