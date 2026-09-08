"""V-CIG-04/05: pytest e XML reais na fronteira de publicação."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from scripts.ci.pytest_execution_evidence import public_junit_identity

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "scripts/ci/run_live_pytest.py"
OPAQUE = "synthetic unlabelled patient detail 98271"
PASSWORD = "synthetic-property-value-821"
KEY_VALUE = "synthetic-key-value-731"


def invoke(root: Path, phase: str, *, injection: str = "") -> subprocess.CompletedProcess[str]:
    args = ["--root", str(root), phase]
    args += (
        ["--output", "collection.json"]
        if phase == "collect"
        else [
            "--expected",
            "collection.json",
            "--evidence",
            "execution.json",
            "--junit",
            "result.xml",
            "--validation",
            "validation.json",
        ]
    )
    args += ["--", "test_probe.py", "-q", "-ra", "-s", "-c", "pytest.ini", "--confcutdir", str(root)]
    if injection:
        driver = f"""
import sys, os
sys.path.insert(0, {str(WRAPPER.parent)!r})
import run_live_pytest as w
original=w.pytest.main
def invoke(*args, **kwargs):
    rc=original(*args, **kwargs)
    print({OPAQUE!r})
    os.write(2, {OPAQUE.encode()!r})
    raise {injection}({OPAQUE!r})
w.pytest.main=invoke
rc=w.main(sys.argv[1:])
print('STREAM_RESTORED')
raise SystemExit(rc)
"""
        command = [sys.executable, "-c", driver, *args]
    else:
        command = [sys.executable, str(WRAPPER), *args]
    return subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=30, check=False)


def prepare(root: Path, source: str) -> None:
    root.chmod(0o700)
    (root / "pytest.ini").write_text("[pytest]\naddopts =\n")
    (root / "test_probe.py").write_text(source)
    for name in ("pytest.ini", "test_probe.py"):
        (root / name).chmod(0o600)


def private_output(root: Path, phase: str) -> str:
    anchor = "collection" if phase == "collect" else "execution"
    logs = list(root.glob(f".{anchor}.json.pytest-*/output.log"))
    assert len(logs) == 1
    assert logs[0].parent.stat().st_mode & 0o777 == 0o700
    assert logs[0].stat().st_mode & 0o777 == 0o600
    return logs[0].read_text()


def test_collect_run_capture_all_streams_and_project_arbitrary_parameter(tmp_path: Path) -> None:
    prepare(
        tmp_path,
        f"""
import os, sys, subprocess, pytest
print({OPAQUE!r})
print({OPAQUE!r}, file=sys.stderr)
os.write(1, {OPAQUE.encode()!r})
os.write(2, {OPAQUE.encode()!r})
subprocess.run([sys.executable, '-c', {("print(" + repr(OPAQUE) + ")")!r}], check=True)
@pytest.mark.parametrize('value', [1], ids=[{OPAQUE!r}])
def test_ok(value):
    print({OPAQUE!r})
    assert value == 1
""",
    )
    for phase in ("collect", "run"):
        result = invoke(tmp_path, phase)
        assert result.returncode == 0
        assert OPAQUE not in result.stdout + result.stderr
        assert OPAQUE in private_output(tmp_path, phase)
        if phase == "collect":
            collected_stdout = result.stdout
    collection = json.loads((tmp_path / "collection.json").read_text())["collection"]
    projected = public_junit_identity("test_probe", f"test_ok[{OPAQUE}]")
    case = ET.parse(tmp_path / "result.xml").find(".//testcase")
    assert case is not None
    assert case.get("junit_identity_sha256") == projected["junit_identity_sha256"]
    assert collection[0]["junit_identity_sha256"] == projected["junit_identity_sha256"]
    assert collection[0]["nodeid_sha256"] in collected_stdout
    assert case.get("name") == projected["name"]


@pytest.mark.parametrize("phase", ["collect", "run"])
@pytest.mark.parametrize("exception", ["RuntimeError", "KeyboardInterrupt", "SystemExit"])
def test_exception_restores_streams_and_keeps_trace_private(
    tmp_path: Path, phase: str, exception: str
) -> None:
    prepare(tmp_path, "def test_ok(): assert True\n")
    if phase == "run":
        assert invoke(tmp_path, "collect").returncode == 0
    result = invoke(tmp_path, phase, injection=exception)
    assert result.returncode == 3
    assert "STREAM_RESTORED" in result.stdout
    assert OPAQUE not in result.stdout + result.stderr
    raw = private_output(tmp_path, phase)
    assert OPAQUE in raw and exception in raw
    if phase == "collect":
        assert not (tmp_path / "collection.json").exists()
    else:
        assert json.loads((tmp_path / "validation.json").read_text())["return_code"] == 3
        assert not (tmp_path / ".result.xml.private").exists()


@pytest.mark.parametrize("phase", ["collect", "run"])
def test_real_pytest_failure_keeps_rc_and_private_diagnostic(tmp_path: Path, phase: str) -> None:
    prepare(
        tmp_path,
        (
            f"raise RuntimeError({OPAQUE!r})\n"
            if phase == "collect"
            else f"def test_bad():\n    assert False, {OPAQUE!r}\n"
        ),
    )
    if phase == "run":
        assert invoke(tmp_path, "collect").returncode == 0
    result = invoke(tmp_path, phase)
    assert result.returncode == (2 if phase == "collect" else 1)
    assert "FAIL" in result.stderr
    assert OPAQUE not in result.stdout + result.stderr
    assert OPAQUE in private_output(tmp_path, phase)


def test_real_junit_properties_and_non_testcase_names_are_contextual(tmp_path: Path) -> None:
    prepare(
        tmp_path,
        f"""
import pytest
@pytest.mark.parametrize('value', [1], ids=[{OPAQUE!r}])
def test_properties(value, record_property):
    record_property('password', {PASSWORD!r})
    record_property({"api_key=" + KEY_VALUE!r}, 'public')
    record_property('build_label', 'useful public diagnostic')
    assert value == 1
""",
    )
    with (tmp_path / "pytest.ini").open("a") as config:
        config.write(f"junit_suite_name = api_key={KEY_VALUE}\n")
    assert invoke(tmp_path, "collect").returncode == 0
    assert invoke(tmp_path, "run").returncode == 0
    public = (tmp_path / "result.xml").read_text()
    assert PASSWORD not in public and KEY_VALUE not in public and OPAQUE not in public
    properties = {p.get("name"): p.get("value") for p in ET.fromstring(public).iter("property")}
    assert properties["password"] == "<redacted>"
    assert properties["build_label"] == "useful public diagnostic"
    report = json.loads((tmp_path / "validation.json").read_text())
    case = ET.fromstring(public).find(".//testcase")
    assert case is not None
    assert case.get("junit_identity_sha256") == report["cases"][0]["junit_identity_sha256"]


@pytest.mark.parametrize(
    "style,secret",
    [
        ("literal", PASSWORD),
        ("constant", PASSWORD),
        ("literal", 'synthetic "quoted" \\ value\nend'),
        ("literal", "1"),
    ],
    ids=["literal", "constant", "escaped", "short"],
)
@pytest.mark.parametrize("failing", [False, True])
def test_property_secret_repeated_in_real_traceback_is_private(
    tmp_path: Path, style: str, secret: str, failing: bool
) -> None:
    prepare(
        tmp_path,
        f"""
import pytest
PROPERTY_VALUE = {secret!r}
@pytest.mark.parametrize('value', [1], ids=[{OPAQUE!r}])
def test_properties(value, record_property):
    record_property('password', {repr(secret) if style == "literal" else "PROPERTY_VALUE"})
    assert 1 == {0 if failing else 1}
""",
    )
    assert invoke(tmp_path, "collect").returncode == 0
    result = invoke(tmp_path, "run")
    assert result.returncode == (1 if failing else 0)
    public = (tmp_path / "result.xml").read_text()
    if secret != "1":
        assert secret not in public and repr(secret)[1:-1] not in public
        assert secret not in result.stdout + result.stderr
    root = ET.fromstring(public)
    prop = root.find(".//property")
    assert prop is not None and prop.get("value") == "<redacted>"
    suite = root.find("testsuite")
    assert suite is not None and suite.get("tests") == "1"
    assert suite.get("failures") == ("1" if failing else "0")
    report = json.loads((tmp_path / "validation.json").read_text())
    assert report["return_code"] == result.returncode
    case = root.find(".//testcase")
    assert case is not None
    identity = public_junit_identity("test_probe", f"test_properties[{OPAQUE}]")
    assert case.get("junit_identity_sha256") == identity["junit_identity_sha256"]
    assert report["cases"][0]["junit_identity_sha256"] == identity["junit_identity_sha256"]
    if failing:
        failure = root.find(".//failure")
        assert failure is not None
        if secret != "1":
            assert "assert 1 == 0" in "".join(failure.itertext())
            raw = private_output(tmp_path, "run")
            assert secret in raw or repr(secret)[1:-1] in raw or "PROPERTY_VALUE" in raw
        else:
            assert "<redacted>" in "".join(failure.itertext())
