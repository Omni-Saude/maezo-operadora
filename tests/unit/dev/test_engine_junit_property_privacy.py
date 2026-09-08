"""EIR-SD-04: propriedade JUnit real preserva privacidade e identidade validada."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scripts.dev import run_engine_integration as runner

from tests.unit.dev.test_engine_runner_final_repair import authenticate_fixture

VALUE = "synthetic property content 9271"
KEY_VALUE = "synthetic-key-content-8317"
PARAMETER = "synthetic arbitrary parameter 731"


@pytest.mark.parametrize(
    "style,secret",
    [
        ("literal", VALUE),
        ("constant", VALUE),
        ("literal", 'synthetic "quoted" \\ value\nend'),
        ("literal", "1"),
    ],
    ids=["literal", "constant", "escaped", "short"],
)
@pytest.mark.parametrize("failing", [False, True])
def test_real_runner_property_context_preserves_validated_identity_and_rc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: bool, style: str, secret: str
) -> None:
    tmp_path.chmod(0o700)
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["integration"]\n')
    (tmp_path / "test_subject.py").write_text(f"""
import pytest
pytestmark = pytest.mark.integration
PROPERTY_VALUE = {secret!r}
PROPERTY_KEY = {"api_key=" + KEY_VALUE!r}
@pytest.mark.parametrize('value', [1], ids=[{PARAMETER!r}])
def test_properties(value, record_property):
    record_property('password', {repr(secret) if style == "literal" else "PROPERTY_VALUE"})
    record_property(PROPERTY_KEY, 'public')
    record_property('build_label', 'useful public diagnostic')
    assert 1 == {0 if failing else 1}
""")
    for name in ("pyproject.toml", "test_subject.py"):
        (tmp_path / name).chmod(0o600)
    authenticate_fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    api = runner._evidence_api(tmp_path)
    load_api = runner._evidence_api
    seen: dict[str, object] = {}

    def observe_api(checkout: Path):
        loaded = load_api(checkout)
        validate = loaded.validate_execution

        def observe_original(*args, **kwargs):
            assert not (tmp_path / "out/pytest.log").exists()
            private_log = args[0].parent / "pytest.log"
            assert private_log.stat().st_mode & 0o777 == 0o600
            assert private_log.parent.stat().st_mode & 0o777 == 0o700
            seen["raw"] = args[0].read_text()
            return validate(*args, **kwargs)

        loaded.validate_execution = observe_original
        return loaded

    monkeypatch.setattr(runner, "_evidence_api", observe_api)
    result = runner._run_pytest(
        tmp_path, "core", "test_subject.py", runner._runtime_env(), tmp_path / "out", 1
    )
    assert result["return_code"] == (1 if failing else 0)
    raw = str(seen["raw"])
    assert secret in ET.fromstring(raw).find(".//property").get("value")
    assert KEY_VALUE in raw and PARAMETER in raw
    public = (tmp_path / "out/junit.xml").read_text()
    log = (tmp_path / "out/pytest.log").read_text()
    if secret != "1":
        assert secret not in public + log and repr(secret)[1:-1] not in public + log
    assert KEY_VALUE not in public
    root = ET.fromstring(public)
    properties = {p.get("name"): p.get("value") for p in root.iter("property")}
    assert properties["password"] == "<redacted>"
    assert properties["build_label"] == "useful public diagnostic"
    suite = root.find("testsuite")
    assert suite is not None and suite.get("tests") == "1"
    assert suite.get("failures") == ("1" if failing else "0")
    if failing:
        failure = root.find(".//failure")
        assert failure is not None
        if secret != "1":
            assert "assert 1 == 0" in "".join(failure.itertext())
            assert "assert 1 == 0" in log
        else:
            assert "<redacted>" in "".join(failure.itertext())
    case = root.find(".//testcase")
    assert case is not None and PARAMETER not in case.get("name", "")
    identity = api.public_junit_identity("test_subject", f"test_properties[{PARAMETER}]")
    assert case.get("maezo_identity_sha256") == identity["junit_identity_sha256"]
    collection = json.loads((tmp_path / "out/pytest-execution.json").read_text())["collection"]
    assert collection[0]["junit_identity_sha256"] == case.get("maezo_identity_sha256")
    assert result["cases"][0]["junit_identity_sha256"] == case.get("maezo_identity_sha256")


@pytest.mark.parametrize("fault", ["missing", "partial", "validator", "interrupt"])
def test_pytest_log_never_publishes_early_or_without_xml_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    tmp_path.chmod(0o700)
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["integration"]\n')
    (tmp_path / "test_subject.py").write_text(f"""
import pytest
pytestmark = pytest.mark.integration
def test_secret(record_property):
    record_property('password', {VALUE!r})
    assert 1 == 0
""")
    authenticate_fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "pytest.log").write_text("STALE_PUBLIC_LOG")
    run_actual = runner._run
    load_api = runner._evidence_api
    raw_directories: list[Path] = []

    def intervene(command, **kwargs):
        completed = run_actual(command, **kwargs)
        if any(arg.startswith("--junitxml=") for arg in command):
            assert not (destination / "pytest.log").exists()
            raw_xml = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
            raw_directories.append(raw_xml.parent)
            assert VALUE in (raw_xml.parent / "pytest.log").read_text()
            if fault == "missing":
                raw_xml.unlink()
            elif fault == "partial":
                raw_xml.write_text("<testsuites>")
            elif fault == "interrupt":
                raise KeyboardInterrupt("synthetic interruption after real pytest")
        return completed

    def api_with_fault(checkout):
        api = load_api(checkout)
        if fault == "validator":

            def fail_validation(*args, **kwargs):
                raise ValueError("synthetic validator failure after real pytest")

            api.validate_execution = fail_validation
        return api

    monkeypatch.setattr(runner, "_run", intervene)
    monkeypatch.setattr(runner, "_evidence_api", api_with_fault)
    if fault in {"validator", "interrupt"}:
        with pytest.raises(runner.RunnerError if fault == "validator" else KeyboardInterrupt):
            runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), destination, 1)
    else:
        result = runner._run_pytest(
            tmp_path, "core", "test_subject.py", runner._runtime_env(), destination, 1
        )
        assert result["return_code"] != 0
    log = (destination / "pytest.log").read_text()
    assert VALUE not in log and "STALE_PUBLIC_LOG" not in log
    assert raw_directories and all(not path.exists() for path in raw_directories)
    if fault in {"missing", "partial"}:
        assert "retido" in log and "sha256=" in log
    else:
        assert "assert 1 == 0" in log and "<redacted>" in log
