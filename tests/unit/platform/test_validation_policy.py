"""Unit tests for maezo.platform.validation.policy — autonomy-policy validation (ADR-0008)."""

from __future__ import annotations

from pathlib import Path

from maezo.platform.validation.policy import validate_dir
from maezo.platform.validation.result import Report

CORE_YAML = """\
version: 1
actions:
  clinical_decision: { level: L0, hard: true }
  triage_and_routing: { level: L3 }
"""

FROZEN_YAML = """\
version: 1
hard_items:
  - action: clinical_decision
    level: L0
"""

TENANT_YAML = """\
version: 1
tenant: test
overrides:
  triage_and_routing:
    params: { foo: 1 }
"""


def _autonomy_dir(
    tmp_path: Path,
    core: str = CORE_YAML,
    frozen: str | None = FROZEN_YAML,
    tenant: str | None = TENANT_YAML,
) -> Path:
    autonomy_dir = tmp_path / "autonomy"
    autonomy_dir.mkdir()
    (autonomy_dir / "L0-core.yaml").write_text(core)
    if frozen is not None:
        (autonomy_dir / "_hard_frozen.yaml").write_text(frozen)
    if tenant is not None:
        (autonomy_dir / "tenants-test.yaml").write_text(tenant)
    return autonomy_dir


class TestHappyPath:
    def test_matching_frozen_set_passes(self, tmp_path: Path) -> None:
        autonomy_dir = _autonomy_dir(tmp_path)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert report.ok, [f.message for f in report.findings]


class TestMissingDirectory:
    def test_missing_directory_is_an_error(self, tmp_path: Path) -> None:
        report = Report()
        validate_dir(tmp_path / "does-not-exist", report)
        assert not report.ok


class TestFrozenSetDrift:
    def test_removed_hard_item_fails(self, tmp_path: Path) -> None:
        core = "version: 1\nactions:\n  triage_and_routing: { level: L3 }\n"
        autonomy_dir = _autonomy_dir(tmp_path, core=core)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
        assert any("no longer present" in f.message for f in report.findings)

    def test_new_unregistered_hard_item_fails(self, tmp_path: Path) -> None:
        core = CORE_YAML + "  fraud_accusation: { level: L0, hard: true }\n"
        autonomy_dir = _autonomy_dir(tmp_path, core=core)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
        assert any("not registered" in f.message for f in report.findings)

    def test_relevel_hard_item_fails(self, tmp_path: Path) -> None:
        core = CORE_YAML.replace("level: L0, hard: true", "level: L1, hard: true")
        autonomy_dir = _autonomy_dir(tmp_path, core=core)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
        assert any("must be level L0" in f.message for f in report.findings)
        assert any("differs from frozen" in f.message for f in report.findings)

    def test_missing_frozen_file_is_an_error(self, tmp_path: Path) -> None:
        autonomy_dir = _autonomy_dir(tmp_path, frozen=None)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
        assert any("_hard_frozen.yaml" in f.message for f in report.findings)


class TestTenantOverrideCannotTouchHard:
    def test_tenant_override_of_hard_action_fails(self, tmp_path: Path) -> None:
        tenant = "version: 1\ntenant: test\noverrides:\n  clinical_decision:\n    level: L1\n"
        autonomy_dir = _autonomy_dir(tmp_path, tenant=tenant)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
        assert any("hard-frozen" in f.message for f in report.findings)


class TestMalformedYaml:
    def test_malformed_core_yaml_is_an_error(self, tmp_path: Path) -> None:
        core = "version: 1\nactions: [this is not\n  a mapping"
        autonomy_dir = _autonomy_dir(tmp_path, core=core)
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok

    def test_malformed_frozen_yaml_is_an_error(self, tmp_path: Path) -> None:
        autonomy_dir = _autonomy_dir(tmp_path, frozen="hard_items: [not\n  valid: :: yaml")
        report = Report()
        validate_dir(autonomy_dir, report)
        assert not report.ok
