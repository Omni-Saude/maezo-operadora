"""Unit tests for maezo.platform.validation.bpmn — BPMN artifact validation."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.bpmn import validate_file
from maezo.platform.validation.result import Report

VALID_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  id="Definitions_1">
  <bpmn:process id="Process_1" isExecutable="true">
    <bpmn:startEvent id="Start_1"/>
    <bpmn:businessRuleTask id="BRT_1" camunda:decisionRef="some_decision"/>
    <bpmn:endEvent id="End_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


def _write(tmp_path: Path, content: str, name: str = "test.bpmn") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


class TestValidateFileHappyPath:
    def test_valid_bpmn_parses_cleanly(self, tmp_path: Path) -> None:
        path = _write(tmp_path, VALID_BPMN)
        report = Report()
        result = validate_file(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None
        assert result.process_ids == ["Process_1"]
        assert result.decision_refs == {"some_decision"}


class TestMalformedXml:
    def test_truncated_xml_is_an_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "<bpmn:definitions><unclosed>")
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok
        assert "malformed XML" in report.findings[0].message

    def test_unescaped_angle_bracket_is_an_error(self, tmp_path: Path) -> None:
        """Regression for the real defect found in spec/processes/dmn/adequacao_gap.dmn
        (an unescaped `<` inside element text breaks the XML parser)."""
        content = VALID_BPMN.replace(
            '<bpmn:startEvent id="Start_1"/>',
            '<bpmn:startEvent id="Start_1">'
            "<bpmn:documentation>threshold a < b</bpmn:documentation>"
            "</bpmn:startEvent>",
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok


class TestNamespace:
    def test_missing_bpmn_namespace_is_an_error(self, tmp_path: Path) -> None:
        content = '<?xml version="1.0"?><definitions id="x"><process id="p"/></definitions>'
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert result is None
        assert not report.ok
        assert "bpmn:definitions" in report.findings[0].message


class TestProcessId:
    def test_process_missing_id_is_an_error(self, tmp_path: Path) -> None:
        content = VALID_BPMN.replace('id="Process_1"', "")
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert not report.ok
        assert result is not None
        assert result.process_ids == []

    def test_no_process_element_is_an_error(self, tmp_path: Path) -> None:
        content = (
            '<?xml version="1.0"?>'
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
            'id="Definitions_1"/>'
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert not report.ok
        assert result is not None
        assert result.process_ids == []


class TestDecisionRefCollection:
    def test_business_rule_task_without_decision_ref_is_an_error(self, tmp_path: Path) -> None:
        content = VALID_BPMN.replace('camunda:decisionRef="some_decision"', "")
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert not report.ok
        assert result is not None
        assert result.decision_refs == set()

    def test_multiple_decision_refs_collected(self, tmp_path: Path) -> None:
        content = VALID_BPMN.replace(
            '<bpmn:endEvent id="End_1"/>',
            '<bpmn:businessRuleTask id="BRT_2" camunda:decisionRef="other_decision"/>'
            '<bpmn:endEvent id="End_1"/>',
        )
        path = _write(tmp_path, content)
        report = Report()
        result = validate_file(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None
        assert result.decision_refs == {"some_decision", "other_decision"}
