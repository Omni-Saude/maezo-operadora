"""Unit tests for maezo.platform.validation.dmn — DMN artifact validation."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.dmn import validate_file
from maezo.platform.validation.result import Report

VALID_DMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs_1" name="Test">
  <decision id="some_decision" name="Some Decision">
    <decisionTable id="dt_1" hitPolicy="FIRST">
      <input id="in_1">
        <inputExpression id="ie_1" typeRef="boolean"><text>x</text></inputExpression>
      </input>
      <output id="out_1" typeRef="string"/>
    </decisionTable>
  </decision>
</definitions>
"""


def _write(tmp_path: Path, content: str, name: str = "test.dmn") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


class TestValidateFileHappyPath:
    def test_valid_dmn_parses_cleanly(self, tmp_path: Path) -> None:
        path = _write(tmp_path, VALID_DMN)
        report = Report()
        result = validate_file(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None
        assert result.decision_ids == ["some_decision"]

    def test_legacy_namespace_accepted(self, tmp_path: Path) -> None:
        content = VALID_DMN.replace(
            "https://www.omg.org/spec/DMN/20191111/MODEL/",
            "http://www.omg.org/spec/DMN/20180521/MODEL/",
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None


class TestMalformedXml:
    def test_truncated_xml_is_an_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "<definitions><unclosed>")
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok
        assert "malformed XML" in report.findings[0].message

    def test_real_defect_unescaped_angle_bracket_in_description(self, tmp_path: Path) -> None:
        """Regression test for the real defect found+fixed in
        spec/processes/dmn/adequacao_gap.dmn: an unescaped `<` in a
        <description> text node breaks the XML parser outright."""
        content = VALID_DMN.replace(
            '<decision id="some_decision" name="Some Decision">',
            '<decision id="some_decision" name="Some Decision">'
            "<description>threshold (<= 30min)</description>",
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok


class TestNamespace:
    def test_wrong_namespace_is_an_error(self, tmp_path: Path) -> None:
        content = VALID_DMN.replace(
            "https://www.omg.org/spec/DMN/20191111/MODEL/", "http://example.com/not-dmn"
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok
        assert "dmn:definitions" in report.findings[0].message


class TestDecisionIdCollection:
    def test_missing_decision_id_is_an_error(self, tmp_path: Path) -> None:
        content = VALID_DMN.replace('id="some_decision" name="Some Decision"', 'name="Some Decision"')
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert not report.ok
        assert result is not None
        assert result.decision_ids == []

    def test_no_decision_element_is_an_error(self, tmp_path: Path) -> None:
        content = (
            '<?xml version="1.0"?>'
            '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs_1"/>'
        )
        path = _write(tmp_path, content)
        report = Report()
        validate_file(path, report)
        assert not report.ok

    def test_duplicate_decision_id_in_same_file_is_an_error(self, tmp_path: Path) -> None:
        extra_decision = (
            '<decision id="some_decision" name="Dup">'
            '<decisionTable id="dt_2" hitPolicy="FIRST">'
            '<input id="in_2"><inputExpression id="ie_2" typeRef="boolean">'
            "<text>y</text></inputExpression></input>"
            '<output id="out_2" typeRef="string"/>'
            "</decisionTable></decision>"
        )
        content = VALID_DMN.replace("</definitions>", extra_decision + "</definitions>")
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert not report.ok
        assert result is not None
        assert result.decision_ids == ["some_decision"]  # only the first occurrence is kept
