"""Artifact custody checks use real ZIPs, without claiming engine admission."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[3]
JAVA = ROOT / "src/maezo/portal/engine/java"
SPEC = importlib.util.spec_from_file_location(
    "native_qualification_artifact", ROOT / "deploy/cibseven/secured/qualification_artifact.py"
)
assert SPEC is not None and SPEC.loader is not None
artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifact)
DEFAULT = (JAVA / "src/main/resources/engine-schemas-v2.json").read_bytes()
QUALIFICATION = (JAVA / "src/qualification/native-v2/resources/engine-schemas-v2.json").read_bytes()


def jar(path: Path, data: bytes, *, bytecode: bytes = b"finite-class-bytes") -> Path:
    with zipfile.ZipFile(path, "w") as out:
        out.writestr(artifact.RESOURCE, data)
        out.writestr("br/com/maezo/Example.class", bytecode)
    return path


def test_default_remains_empty_and_qualification_is_explicit_technical_data() -> None:
    assert artifact.registry(DEFAULT, qualification=False)["schemas"] == []
    rows = artifact.registry(QUALIFICATION, qualification=True)["schemas"]
    assert len(rows) == 10
    definitions = ET.parse(JAVA / "src/qualification/native-v2/native-v2-qualification.bpmn")
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    processes = {p.attrib["id"] for p in definitions.findall("b:process", ns)}
    assert processes == {row["process_key"] for row in rows}
    assert len(processes) == 4
    with pytest.raises(ValueError, match="wrong_registry_profile"):
        artifact.registry(QUALIFICATION, qualification=False)


def test_profile_has_separate_output_and_one_resource_input() -> None:
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    pom = ET.parse(JAVA / "pom.xml")
    profiles = pom.findall("m:profiles/m:profile", ns)
    profile = next(p for p in profiles if p.findtext("m:id", namespaces=ns) == "native-v2-qualification")
    assert profile.find("m:activation", ns) is None
    build = profile.find("m:build", ns)
    assert build is not None
    assert build.findtext("m:directory", namespaces=ns).endswith("/target/native-v2-qualification")
    resources = build.findall("m:resources/m:resource", ns)
    assert len(resources) == 2
    assert resources[0].findtext("m:excludes/m:exclude", namespaces=ns) == artifact.RESOURCE
    assert resources[1].findtext("m:includes/m:include", namespaces=ns) == artifact.RESOURCE
    assert "maezo.nativeQualification.registrySha256" in ET.tostring(profile, encoding="unicode")


def test_actual_zip_has_one_pinned_resource_and_identical_class_bytes(tmp_path: Path) -> None:
    normal = jar(tmp_path / "default.jar", DEFAULT)
    qualified = jar(tmp_path / "qualification.jar", QUALIFICATION)
    evidence = artifact.inspect(
        [qualified], qualification=True, expected_registry_sha256=hashlib.sha256(QUALIFICATION).hexdigest()
    )
    assert evidence["resource_count"] == 1 and evidence["runtime_admission_verified"] is False
    assert artifact.compare_classes(normal, qualified) == 1
    with pytest.raises(ValueError, match="registry_classpath"):
        artifact.inspect([normal, qualified], qualification=True, expected_registry_sha256="a" * 64)
    altered = jar(tmp_path / "altered.jar", QUALIFICATION, bytecode=b"different")
    with pytest.raises(ValueError, match="changed_production_classes"):
        artifact.compare_classes(normal, altered)


@pytest.mark.parametrize("fault", ["duplicate", "shadow_directory", "wrong_digest", "missing", "symlink"])
def test_actual_resource_custody_refuses_ambiguous_or_changed_inputs(tmp_path: Path, fault: str) -> None:
    qualified = jar(tmp_path / "qualification.jar", QUALIFICATION)
    roots = [qualified]
    expected = hashlib.sha256(QUALIFICATION).hexdigest()
    if fault == "duplicate":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(qualified, "a") as out:
                out.writestr(artifact.RESOURCE, QUALIFICATION)
    elif fault == "shadow_directory":
        classes = tmp_path / "classes"
        classes.mkdir()
        (classes / artifact.RESOURCE).write_bytes(QUALIFICATION)
        roots.append(classes)
    elif fault == "wrong_digest":
        expected = "a" * 64
    elif fault == "missing":
        roots = [tmp_path / "absent.jar"]
    else:
        link = tmp_path / "linked.jar"
        link.symlink_to(qualified)
        roots = [link]
    with pytest.raises(ValueError):
        artifact.inspect(roots, qualification=True, expected_registry_sha256=expected)


def test_business_descriptor_cannot_be_called_qualification() -> None:
    data = json.loads(QUALIFICATION)
    data["schemas"][0]["process_key"] = "SP-OP-AUTH-001"
    with pytest.raises(ValueError, match="nonqualification_schema"):
        artifact.registry(json.dumps(data).encode(), qualification=True)
