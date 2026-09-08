"""R-199: ambiguous YAML must block both build consumers, without ratifying anything."""

import shutil
from pathlib import Path

import pytest
import yaml

from maezo.platform.validation import phi_completeness as fence
from maezo.platform.validation._loaders import load_yaml
from maezo.platform.validation.cli import validate_artifacts
from maezo.platform.validation.policy import (
    PHI_DISPOSITIONS_FILENAME,
    PhiDispositionsError,
    load_phi_dispositions,
    validate_phi_dispositions_dir,
)
from maezo.platform.validation.result import Report

_MANIFEST = Path(__file__).resolve().parents[3] / "spec/policies/phi" / PHI_DISPOSITIONS_FILENAME


def _write(tmp_path: Path, text: str) -> Path:
    target = tmp_path / PHI_DISPOSITIONS_FILENAME
    target.write_text(text, encoding="utf-8")
    return target


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("status: DRAFT", "status: RATIFICADO\nstatus: DRAFT"),
        ("status: DRAFT", "status: DRAFT\nstatus: RATIFICADO"),
        ("status: DRAFT", "status: DRAFT\n'status': DRAFT"),
        ("status: DRAFT", 'status: DRAFT\n"statu\\x73": DRAFT'),
        ("ratificado: false", "ratificado: true\n  ratificado: false"),
        ("ratificado: false", "ratificado: false\n  ratificado: true"),
        ("revisor: null", "revisor: TEST-ONLY\n  revisor: null"),
        ("disposicao: null", "disposicao: LISTAR_EM_CONJUNTO_PHI\n    disposicao: null"),
    ],
)
def test_duplicate_manifest_fields_refused(tmp_path: Path, original: str, replacement: str) -> None:
    source = _MANIFEST.read_text(encoding="utf-8")
    assert original in source
    indent = ""
    if original.startswith(("ratificado:", "revisor:")):
        indent = "  "
    elif original.startswith("disposicao:"):
        indent = "    "
    target = _write(tmp_path, source.replace("\n" + indent + original, "\n" + indent + replacement, 1))
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key"):
        load_phi_dispositions(target)


@pytest.mark.parametrize(
    "extra",
    [
        "extra: {1: first, true: second}\n",
        "extra: [{0xA: first, 10: second}]\n",
        "extra: {null: first, ~: second}\n",
        "extra: {=: first, '=': second}\n",
        "extra: {&key foo: first, *key: second}\n",
        "extra: {<<: &base {a: 1, a: 2}}\n",
        "extra: {<<: {a: 1}, <<: {b: 2}}\n",
    ],
)
def test_resolved_nested_duplicates_refused(tmp_path: Path, extra: str) -> None:
    target = _write(tmp_path, _MANIFEST.read_text(encoding="utf-8") + extra)
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key"):
        load_phi_dispositions(target)


@pytest.mark.parametrize("field", ["ratificacao", "disposicoes"])
def test_duplicate_root_collections_refused(tmp_path: Path, field: str) -> None:
    source = _MANIFEST.read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    target = _write(tmp_path, source + yaml.safe_dump({field: data[field]}))
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key"):
        load_phi_dispositions(target)


def test_duplicate_inside_disposition_signature_refused(tmp_path: Path) -> None:
    source = _MANIFEST.read_text(encoding="utf-8")
    original = "      revisor: null"
    assert original in source
    target = _write(tmp_path, source.replace(original, original + "\n" + original, 1))
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key"):
        load_phi_dispositions(target)


def test_duplicate_diagnostic_does_not_echo_key_or_value(tmp_path: Path) -> None:
    extra = "extra: {PRIVATE-KEY: PRIVATE-VALUE, PRIVATE-KEY: PRIVATE-VALUE}\n"
    target = _write(tmp_path, _MANIFEST.read_text(encoding="utf-8") + extra)
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key at line") as caught:
        load_phi_dispositions(target)
    assert "PRIVATE" not in str(caught.value)


def test_valid_aliases_merges_and_empty_signatures_preserved(tmp_path: Path) -> None:
    source = _MANIFEST.read_text(encoding="utf-8")
    # YAML merge inheritance permits an explicit override; it is not two explicit keys.
    extra = (
        "base: &base {a: 1}\n"
        "alias: *base\n"
        "override: &override {<<: *base, a: 2}\n"
        "nested: {<<: *override, b: 3}\n"
        "sequence: {<<: [*override, *base]}\n"
        "value_key: {=: unchanged}\n"
    )
    target = _write(tmp_path, source + extra)
    assert load_phi_dispositions(target) == load_phi_dispositions(_MANIFEST)
    parsed = load_yaml(target, reject_duplicate_keys=True)
    assert parsed == yaml.safe_load(source + extra)
    assert isinstance(parsed, dict)
    assert parsed["override"] == {"a": 2}
    manifest = load_phi_dispositions(target)
    assert manifest.status == "DRAFT"
    assert manifest.ratificadas == {}
    assert all(
        item.disposicao is None and item.revisor is None and item.ratificado_em is None
        for item in manifest.itens
    )


def test_shared_loader_default_is_unchanged(tmp_path: Path) -> None:
    target = _write(tmp_path, "key: first\nkey: second\n")
    assert load_yaml(target) == {"key": "second"}
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {"key": "second"}


@pytest.mark.parametrize("consumer", ["directory", "completeness", "cli"])
def test_duplicate_refusal_reaches_build_consumers(tmp_path: Path, consumer: str) -> None:
    policies = tmp_path / "policies"
    shutil.copytree(_MANIFEST.parent.parent, policies)
    root = policies / "phi"
    target = _write(root, "status: RATIFICADO\n" + _MANIFEST.read_text(encoding="utf-8"))
    if consumer == "cli":
        assert validate_artifacts([str(policies)]) == 1
        return
    report = Report()
    if consumer == "directory":
        validate_phi_dispositions_dir(root, report)
    else:
        fence.check_sweep(fence.Sweep((), ()), report, manifest_path=target)
    assert not report.ok
    assert len(report.findings) == 1
    assert "duplicate YAML key" in report.findings[0].message


def test_recovery_after_duplicate_refusal(tmp_path: Path) -> None:
    source = _MANIFEST.read_text(encoding="utf-8")
    target = _write(tmp_path, "status: DRAFT\n" + source)
    with pytest.raises(PhiDispositionsError, match="duplicate YAML key"):
        load_phi_dispositions(target)
    target.write_text(source, encoding="utf-8")
    assert load_phi_dispositions(target) == load_phi_dispositions(_MANIFEST)
