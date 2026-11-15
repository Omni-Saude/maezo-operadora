"""Unit tests for maezo.platform.validation.dmn — DMN artifact validation."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.dmn import deployment_resource_name, is_rco_dmn, list_dmn_files, validate_file
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


class TestRcoFolder:
    """ADR-0075 §(b): the rco K1 folder — `rco_` id prefix, explicit scan, resource name."""

    def _rco(self, tmp_path: Path, decision_id: str) -> Path:
        (tmp_path / "dmn" / "rco").mkdir(parents=True, exist_ok=True)
        path = tmp_path / "dmn" / "rco" / f"{decision_id}.dmn"
        path.write_text(VALID_DMN.replace('id="some_decision"', f'id="{decision_id}"'))
        return path

    def test_prefixed_decision_under_rco_passes(self, tmp_path: Path) -> None:
        report = Report()
        result = validate_file(self._rco(tmp_path, "rco_carencia"), report)
        assert report.ok, [f.message for f in report.findings]
        assert result is not None and result.decision_ids == ["rco_carencia"]

    def test_unprefixed_decision_under_rco_is_an_error(self, tmp_path: Path) -> None:
        path = self._rco(tmp_path, "carencia_check")
        report = Report()
        validate_file(path, report)
        assert not report.ok
        assert [f.path for f in report.findings] == [path]
        assert "must start with 'rco_'" in report.findings[0].message

    def test_top_level_maezo_decision_needs_no_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "dmn").mkdir()
        path = tmp_path / "dmn" / "carencia_check.dmn"
        path.write_text(VALID_DMN.replace('id="some_decision"', 'id="carencia_check"'))
        report = Report()
        validate_file(path, report)
        assert report.ok, [f.message for f in report.findings]

    def test_list_dmn_files_is_top_level_then_rco_and_nothing_deeper(self, tmp_path: Path) -> None:
        dmn_dir = tmp_path / "dmn"
        (dmn_dir / "rco" / "solta").mkdir(parents=True)
        (dmn_dir / "outra").mkdir()
        for rel in ("b.dmn", "a.dmn", "rco/rco_z.dmn", "rco/rco_y.dmn", "rco/solta/x.dmn", "outra/w.dmn"):
            (dmn_dir / rel).write_text("<x/>")
        (dmn_dir / "orphans-allowlist.yaml").write_text("version: 1\n")
        names = [p.relative_to(dmn_dir).as_posix() for p in list_dmn_files(dmn_dir)]
        assert names == ["a.dmn", "b.dmn", "rco/rco_y.dmn", "rco/rco_z.dmn"]
        assert list_dmn_files(tmp_path / "absent") == []

    def test_resource_name_is_relative_path_only_for_rco(self, tmp_path: Path) -> None:
        assert deployment_resource_name(tmp_path / "dmn" / "rco" / "rco_a.dmn") == "rco/rco_a.dmn"
        assert deployment_resource_name(tmp_path / "dmn" / "auth_sla.dmn") == "auth_sla.dmn"
        assert deployment_resource_name(tmp_path / "bpmn" / "SP-X.bpmn") == "SP-X.bpmn"
        assert is_rco_dmn(tmp_path / "dmn" / "rco" / "rco_a.dmn")
        assert not is_rco_dmn(tmp_path / "dmn" / "rco" / "solta" / "rco_a.dmn")
        assert not is_rco_dmn(tmp_path / "x" / "rco" / "rco_a.dmn")
