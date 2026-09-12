"""R-199: refuse ambiguous PHI signature manifests without changing YAML consumers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from maezo.platform.validation._loaders import load_yaml
from maezo.platform.validation.cli import validate_artifacts
from maezo.platform.validation.policy import (
    PHI_DISPOSITIONS_FILENAME,
    PhiDispositionsError,
    load_phi_dispositions,
    validate_phi_dispositions_dir,
)
from maezo.platform.validation.result import Report

_POLICIES = Path(__file__).resolve().parents[3] / "spec" / "policies"
_MANIFEST = _POLICIES / "phi" / PHI_DISPOSITIONS_FILENAME


def _plain_manifest() -> str:
    return yaml.safe_dump(yaml.safe_load(_MANIFEST.read_text()), sort_keys=False, allow_unicode=True)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / PHI_DISPOSITIONS_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("version: 0\n", ""),
        ("status: RATIFICADO\n", ""),
        ('"\\x76ersion": 0\n', ""),
        ("", "extra: {chave: primeiro, chave: segundo}\n"),
        ("", "extra: {true: primeiro, 1: segundo}\n"),
        ("", "extra: {null: primeiro, ~: segundo}\n"),
        ("", "extra: {1: primeiro, 0x1: segundo}\n"),
        ("", "extra: &a {chave: primeiro, chave: segundo}\nalias: *a\n"),
        ("", "extra: {<<: &a {chave: primeiro, chave: segundo}}\n"),
        ("", "extra: {<<: {chave: primeiro}, <<: {outra: segundo}}\n"),
        ("", "extra: [{interno: {chave: primeiro, chave: segundo}}]\n"),
    ],
)
def test_actual_manifest_loader_refuses_duplicate_mapping_keys(
    tmp_path: Path, prefix: str, suffix: str
) -> None:
    path = _write(tmp_path, prefix + _plain_manifest() + suffix)
    with pytest.raises(PhiDispositionsError, match="duplicate YAML mapping key"):
        load_phi_dispositions(path)


@pytest.mark.parametrize("field", ["ratificado", "revisor", "ratificado_em", "disposicao", "nome"])
def test_nested_signature_and_disposition_keys_cannot_be_overridden(tmp_path: Path, field: str) -> None:
    lines = _plain_manifest().splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().removeprefix("- ").startswith(field + ":"):
            # Repeat the SAME bytes: even equal duplicate values are ambiguous source.
            lines.insert(index + 1, line.replace("- ", "  ", 1) if line.startswith("- ") else line)
            break
    else:
        pytest.fail(f"the shipped manifest has no {field} field")
    with pytest.raises(PhiDispositionsError, match="duplicate YAML mapping key"):
        load_phi_dispositions(_write(tmp_path, "".join(lines)))


def test_build_validator_reports_duplicate_as_blocking_finding(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 0\n" + _plain_manifest())
    report = Report()
    validate_phi_dispositions_dir(tmp_path, report)
    assert not report.ok
    assert len(report.findings) == 1
    assert report.findings[0].path == path
    assert "duplicate YAML mapping key" in report.findings[0].message


def test_actual_artifact_cli_refuses_duplicate_manifest(tmp_path: Path) -> None:
    policies = tmp_path / "policies"
    shutil.copytree(_POLICIES, policies)
    _write(policies / "phi", "version: 0\n" + _plain_manifest())
    assert validate_artifacts([str(policies)]) == 1


def test_duplicate_diagnostic_does_not_echo_key_or_value(tmp_path: Path) -> None:
    marker = "SYNTHETIC_PRIVATE_TEXT_789"
    path = _write(tmp_path, _plain_manifest() + f"extra: {{{marker}: primeiro, {marker}: segundo}}\n")
    with pytest.raises(PhiDispositionsError) as caught:
        load_phi_dispositions(path)
    assert "duplicate YAML mapping key at line" in str(caught.value)
    assert marker not in str(caught.value)
    assert "primeiro" not in str(caught.value)
    assert "segundo" not in str(caught.value)


@pytest.mark.parametrize(
    "extra",
    [
        "extra: &a {chave: original}\nalias: *a\n",
        "extra: &a {chave: original}\nalias: {<<: *a, chave: override}\n",
        "extra: {<<: [{chave: primeiro}, {chave: segundo}], outra: valor}\n",
        "extra: &a {chave: original}\none: {<<: *a}\ntwo: {<<: *a}\n",
        "extra: &a {self: *a}\n",
        "extra: {true: booleano, 'true': texto}\n",
    ],
)
def test_standard_anchors_aliases_and_merge_precedence_remain_compatible(tmp_path: Path, extra: str) -> None:
    expected = load_phi_dispositions(_MANIFEST)
    actual = load_phi_dispositions(_write(tmp_path, _plain_manifest() + extra))
    assert actual == expected
    assert not actual.ratificado
    assert actual.ratificadas == {}


def test_explicit_valid_manifest_value_can_override_merged_default(tmp_path: Path) -> None:
    path = _write(tmp_path, "<<: {version: 0, status: RATIFICADO}\n" + _plain_manifest())
    assert load_phi_dispositions(path) == load_phi_dispositions(_MANIFEST)


def test_other_shared_loader_consumers_keep_existing_duplicate_semantics(tmp_path: Path) -> None:
    path = _write(tmp_path, "key: first\nkey: last\n")
    assert load_yaml(path) == {"key": "last"}


def test_refusal_does_not_poison_next_load_or_change_shipped_signature_bytes(tmp_path: Path) -> None:
    original = _MANIFEST.read_bytes()
    with pytest.raises(PhiDispositionsError):
        load_phi_dispositions(_write(tmp_path, "version: 0\n" + _plain_manifest()))
    assert load_phi_dispositions(_MANIFEST).ratificadas == {}
    assert _MANIFEST.read_bytes() == original
