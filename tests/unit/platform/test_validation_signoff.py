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
    "| 1 | SP-OP-ESCALATION-001 | **FINAL (v1.0.0)** | MA, REG (retro-verification) |\n"
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
    path.write_text(text)
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
