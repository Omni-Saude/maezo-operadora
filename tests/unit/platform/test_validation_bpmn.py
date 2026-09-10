"""Unit tests for maezo.platform.validation.bpmn — BPMN artifact validation."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from maezo.platform.validation.bpmn import BPMNDI_NS, DC_NS, DI_NS, validate_di, validate_file
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


_ARTIFACTS = Path(__file__).resolve().parents[3] / "spec/processes/bpmn"
_INAD = _ARTIFACTS / "SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn"


def test_repository_di_has_a_complete_plane_for_every_process() -> None:
    """Includes all five timer-started ANS definitions and expanded subprocesses."""
    paths = sorted(_ARTIFACTS.glob("*.bpmn"))
    assert paths, "repository BPMN inventory must not be empty"
    for path in paths:
        report = Report()
        validate_di(ET.parse(path).getroot(), path, report)
        assert report.ok, [(f.path, f.message) for f in report.findings]


@pytest.mark.parametrize(
    "element_id",
    [
        "BE_SuspensaoNaoHumano",
        "End_SuspensaoBloqueadaNaoHumano",
        "Flow_SuspensaoNaoHumano_End",
    ],
)
def test_missing_guard_representation_is_reported(element_id: str) -> None:
    """Reproduce each original blind spot independently, without altering execution."""
    root = ET.parse(_INAD).getroot()
    plane = root.find(f".//{{{BPMNDI_NS}}}BPMNPlane")
    assert plane is not None
    item = next(child for child in plane if child.get("bpmnElement") == element_id)
    plane.remove(item)
    report = Report()
    validate_di(root, _INAD, report)
    assert not report.ok
    assert len(report.findings) == 1
    assert element_id in report.findings[0].message


def test_other_process_plane_cannot_satisfy_missing_coverage() -> None:
    path = _ARTIFACTS / "SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"
    root = ET.parse(path).getroot()
    planes = root.findall(f".//{{{BPMNDI_NS}}}BPMNPlane")
    shape = planes[0].find(f"{{{BPMNDI_NS}}}BPMNShape")
    assert shape is not None
    planes[0].remove(shape)
    planes[1].append(shape)
    report = Report()
    validate_di(root, path, report)
    assert any("not in process plane" in f.message for f in report.findings)
    assert any("missing BPMNShape" in f.message for f in report.findings)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", ""])
def test_shape_size_must_be_finite_and_positive(value: str) -> None:
    root = ET.parse(_INAD).getroot()
    bounds = root.find(f".//{{{DC_NS}}}Bounds")
    assert bounds is not None
    bounds.set("width", value)
    report = Report()
    validate_di(root, _INAD, report)
    assert any("invalid geometry" in f.message for f in report.findings)


def test_required_di_does_not_accept_no_di() -> None:
    report = Report()
    validate_di(ET.fromstring(VALID_BPMN), Path("snippet.bpmn"), report)
    assert not report.ok
    assert "no process plane" in report.findings[0].message


def test_edge_requires_two_finite_waypoints() -> None:
    root = ET.parse(_INAD).getroot()
    edge = root.find(f".//{{{BPMNDI_NS}}}BPMNEdge")
    assert edge is not None
    for point in list(edge)[1:]:
        edge.remove(point)
    point = edge.find(f"{{{DI_NS}}}waypoint")
    assert point is not None
    point.set("x", "NaN")
    report = Report()
    validate_di(root, _INAD, report)
    assert any("missing bounds/waypoints" in f.message for f in report.findings)
    assert any("invalid geometry" in f.message for f in report.findings)
