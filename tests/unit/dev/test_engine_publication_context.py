"""EIR-XML-01: valores privados conhecidos chegam a todas as narrativas públicas."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

from tests.unit.dev.test_engine_runner_final_repair import authenticate_fixture

VALUES = ["private unlabelled detail 27681", 'private "quoted" \\ part\nálpha', "1"]


def fixture(root: Path, secret: str, origin: str, action: str) -> None:
    root.chmod(0o700)
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    ending = {
        "skip": "pytest.skip(VALUE)",
        "xfail": "pytest.xfail(VALUE)",
        "failure": 'assert value == "expected"',
        "pass": "assert True",
    }[action]
    decorator = '@pytest.mark.parametrize("value", [VALUE], ids=[VALUE])' if origin == "parameter" else ""
    parameters = "value, record_property" if origin == "parameter" else "record_property"
    body = "" if origin == "parameter" else '    value=VALUE\n    record_property("password", VALUE)\n'
    (root / "test_subject.py").write_text(
        "import pytest\npytestmark=pytest.mark.integration\n"
        + f"VALUE={secret!r}\n{decorator}\ndef test_subject({parameters}):\n"
        + body
        + '    record_property("build_label", "useful public diagnostic")\n    '
        + ending
        + "\n"
    )
    authenticate_fixture(root)


@pytest.mark.parametrize("origin", ["property", "parameter"])
@pytest.mark.parametrize("action", ["skip", "xfail", "failure", "pass"])
@pytest.mark.parametrize("secret", VALUES, ids=["literal", "escaped", "short"])
def test_all_public_narratives_share_private_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str, action: str, secret: str
) -> None:
    fixture(tmp_path, secret, origin, action)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    out.mkdir()
    for name in ["pytest.log", "junit.xml", "pytest-execution.json", "suite-results.json"]:
        (out / name).write_text("STALE_GREEN")
    load_api = runner._evidence_api
    observed: dict[str, Any] = {}

    def observe_api(checkout: Path) -> Any:
        api = load_api(checkout)
        validate = api.validate_execution

        def observe(xml: Path, evidence: dict[str, Any], *args: Any) -> Any:
            for name in ["pytest.log", "junit.xml", "pytest-execution.json", "suite-results.json"]:
                assert not (out / name).exists()
            assert xml.parent.stat().st_mode & 0o777 == 0o700
            for name in ["junit.xml", "execution.json", "pytest.log"]:
                assert (xml.parent / name).stat().st_mode & 0o777 == 0o600
            observed["evidence"] = evidence
            observed["private"] = xml.parent
            observed["validation"] = validate(xml, evidence, *args)
            return observed["validation"]

        api.validate_execution = observe
        return api

    monkeypatch.setattr(runner, "_evidence_api", observe_api)
    result = runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)
    assert result["return_code"] == (0 if action == "pass" else 1)
    assert not observed["private"].exists()
    execution = json.loads((out / "pytest-execution.json").read_text())
    assert execution["collection"] == observed["evidence"]["collection"]
    assert execution["pytest_exitstatus"] == observed["evidence"]["pytest_exitstatus"]
    for raw, safe in zip(observed["evidence"]["reports"], execution["reports"], strict=True):
        for key in raw.keys() - {"skip_reason", "wasxfail"}:
            assert safe[key] == raw[key]
        for key in ["skip_reason", "wasxfail"]:
            if raw[key] and secret in raw[key]:
                assert secret not in safe[key]
    tree = ET.parse(out / "junit.xml")
    case = tree.find(".//testcase")
    assert case is not None
    assert case.get("maezo_identity_sha256") == execution["collection"][0]["junit_identity_sha256"]
    assert result["cases"][0]["junit_identity_sha256"] == case.get("maezo_identity_sha256")
    suite = tree.find(".//testsuite")
    label = tree.find('.//property[@name="build_label"]')
    assert suite is not None and label is not None
    assert suite.get("tests") == "1"
    assert label.get("value") == "useful public diagnostic"
    for key in ["case_counts", "phase_counts", "return_code"]:
        assert result.get(key) == observed["validation"].get(key)
    text = "".join(
        (out / name).read_text()
        for name in ["pytest.log", "junit.xml", "pytest-execution.json", "suite-results.json"]
    )
    assert "STALE_GREEN" not in text
    if secret != "1":
        assert all(form not in text for form in {secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]})
    if action in {"skip", "xfail"}:
        assert secret not in result["cases"][0]["skip_reason"]
    if action == "failure":
        failure = tree.find(".//failure")
        assert failure is not None
        assert "expected" in "".join(failure.itertext())


@pytest.mark.parametrize("fault", ["partial", "missing", "validator", "projection", "jsonio", "suiteio"])
def test_context_failure_and_publication_io_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    fixture(tmp_path, VALUES[0], "property", "skip")
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    out.mkdir()
    original = runner._run
    api_original = runner._evidence_api
    private: list[Path] = []

    def run(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if any(arg.startswith("--junitxml=") for arg in args[0]):
            raw = Path(next(arg.split("=", 1)[1] for arg in args[0] if arg.startswith("--junitxml=")))
            private.append(raw.parent)
            if fault == "missing":
                raw.unlink()
            elif fault == "partial":
                raw.write_text("<testsuites>")
        return result

    def load(checkout: Path) -> Any:
        api = api_original(checkout)
        if fault == "validator":

            def refuse(*args: Any) -> Any:
                raise ValueError("synthetic private failure")

            api.validate_execution = refuse
        return api

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_run", run)
        patch.setattr(runner, "_evidence_api", load)
        if fault == "projection":

            def refuse_xml(*args: Any, **kwargs: Any) -> None:
                raise OSError("synthetic private failure")

            patch.setattr(runner, "_publish_xml", refuse_xml)
        if fault in {"jsonio", "suiteio"}:
            write = runner._json_write

            def refuse_json(path: Path, payload: object) -> None:
                if path.name == ("pytest-execution.json" if fault == "jsonio" else "suite-results.json"):
                    raise OSError("synthetic private failure")
                write(path, payload)

            patch.setattr(runner, "_json_write", refuse_json)
        if fault in {"validator", "projection", "jsonio", "suiteio"}:
            with pytest.raises(runner.RunnerError if fault == "validator" else OSError):
                runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)
            assert not (out / "suite-results.json").exists()
        else:
            assert (
                runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)[
                    "return_code"
                ]
                == 1
            )
        assert all(not path.exists() for path in private)
        assert VALUES[0] not in "".join(p.read_text() for p in out.iterdir() if p.is_file())
    # Recovery uses a fresh execution and retains the correctly non-green skip.
    assert (
        runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)["return_code"]
        == 1
    )
    assert "<redacted>" in (out / "suite-results.json").read_text()


def test_collection_failure_removes_all_previous_public_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture(tmp_path, VALUES[0], "property", "pass")
    with (tmp_path / "test_subject.py").open("a") as stream:
        stream.write('\nraise RuntimeError("synthetic collection failure")\n')
    authenticate_fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    out.mkdir()
    names = ["suite-results.json", "pytest-execution.json", "junit.xml", "pytest.log"]
    for name in names:
        (out / name).write_text("STALE_GREEN")
    with pytest.raises(runner.RunnerError, match="coleta pytest falhou"):
        runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)
    assert all(not (out / name).exists() for name in names)
