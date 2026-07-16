"""Unit tests for maezo.platform.validation.crossref — BPMN<->DMN cross-reference
and orphan-DMN checks."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.crossref import DecisionRef, check, load_orphan_allowlist
from maezo.platform.validation.result import Report


class TestBrokenReference:
    def test_decision_ref_with_no_matching_dmn_fails(self, tmp_path: Path) -> None:
        bpmn_path = tmp_path / "p.bpmn"
        refs = [DecisionRef("does_not_exist", bpmn_path)]
        report = Report()
        check(refs, defined_ids={}, allowlist_path=tmp_path / "orphans-allowlist.yaml", report=report)
        assert not report.ok
        assert "broken or corrupted cross-reference" in report.findings[0].message
        assert report.findings[0].path == bpmn_path

    def test_matching_reference_passes(self, tmp_path: Path) -> None:
        bpmn_path = tmp_path / "p.bpmn"
        dmn_path = tmp_path / "d.dmn"
        refs = [DecisionRef("real_decision", bpmn_path)]
        report = Report()
        check(
            refs,
            defined_ids={"real_decision": dmn_path},
            allowlist_path=tmp_path / "orphans-allowlist.yaml",
            report=report,
        )
        assert report.ok, [f.message for f in report.findings]


class TestOrphanDetection:
    def test_unlisted_orphan_fails(self, tmp_path: Path) -> None:
        dmn_path = tmp_path / "d.dmn"
        report = Report()
        check(
            [],
            defined_ids={"orphan_decision": dmn_path},
            allowlist_path=tmp_path / "orphans-allowlist.yaml",
            report=report,
        )
        assert not report.ok
        assert "not listed in orphans-allowlist.yaml" in report.findings[0].message

    def test_allowlisted_orphan_passes(self, tmp_path: Path) -> None:
        dmn_path = tmp_path / "d.dmn"
        allowlist_path = tmp_path / "orphans-allowlist.yaml"
        allowlist_path.write_text("version: 1\norphans:\n  orphan_decision: pending wiring\n")
        report = Report()
        check([], defined_ids={"orphan_decision": dmn_path}, allowlist_path=allowlist_path, report=report)
        assert report.ok, [f.message for f in report.findings]

    def test_stale_allowlist_entry_is_a_notice_not_an_error(self, tmp_path: Path) -> None:
        bpmn_path = tmp_path / "p.bpmn"
        dmn_path = tmp_path / "d.dmn"
        allowlist_path = tmp_path / "orphans-allowlist.yaml"
        allowlist_path.write_text("version: 1\norphans:\n  now_wired: was orphaned before\n")
        refs = [DecisionRef("now_wired", bpmn_path)]
        report = Report()
        check(refs, defined_ids={"now_wired": dmn_path}, allowlist_path=allowlist_path, report=report)
        assert report.ok, [f.message for f in report.findings]
        assert any("no longer orphaned" in n for n in report.notices)


class TestAllowlistLoading:
    def test_missing_allowlist_file_returns_empty_dict(self, tmp_path: Path) -> None:
        report = Report()
        result = load_orphan_allowlist(tmp_path / "nope.yaml", report)
        assert result == {}
        assert report.ok

    def test_malformed_allowlist_yaml_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "orphans-allowlist.yaml"
        path.write_text("orphans: [this is not\n  a mapping: :: broken")
        report = Report()
        result = load_orphan_allowlist(path, report)
        assert result is None
        assert not report.ok

    def test_orphans_not_a_mapping_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "orphans-allowlist.yaml"
        path.write_text("orphans:\n  - just_a_list\n")
        report = Report()
        result = load_orphan_allowlist(path, report)
        assert result is None
        assert not report.ok

    def test_entry_without_justification_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "orphans-allowlist.yaml"
        path.write_text("orphans:\n  some_id: ''\n")
        report = Report()
        result = load_orphan_allowlist(path, report)
        assert result == {}
        assert not report.ok

    def test_real_allowlist_file_parses(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "spec" / "processes" / "dmn" / "orphans-allowlist.yaml"
        report = Report()
        result = load_orphan_allowlist(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None
        assert len(result) >= 1
