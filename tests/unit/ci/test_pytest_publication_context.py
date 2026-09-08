"""V-CIG-06/07: contexto privado atravessa todas as publicações pytest reais."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.unit.ci.test_pytest_output_privacy import WRAPPER, invoke, prepare

VALUES = ["private unlabelled detail 27681", 'private "quoted" \\ part\nálpha', "1"]


def run_observed(root: Path, fault: str = "") -> subprocess.CompletedProcess[str]:
    driver = f"""
import sys,json
from pathlib import Path
sys.path.insert(0,{str(WRAPPER.parent)!r})
import run_live_pytest as w
original=w.validate_execution
def validate(xml,evidence,*args):
    for name in ['execution.json','result.xml','validation.json']:
        assert not Path(name).exists(),name
    staged=list(Path('.').glob('.execution.json.pytest-*/execution.json'))
    assert staged
    staged.sort(key=lambda path:path.stat().st_mtime_ns,reverse=True)
    assert staged[0].stat().st_mode & 0o777==0o600
    assert staged[0].parent.stat().st_mode & 0o777==0o700
    Path('observed.json').write_text(json.dumps({{'evidence':evidence,'xml':xml.read_text()}}))
    if {fault!r}=='validator':raise ValueError('synthetic private failure')
    if {fault!r}=='partial':xml.write_text('<testsuites>')
    return original(xml,evidence,*args)
w.validate_execution=validate
if {fault!r} in ('context','narrative'):
    def refuse(*args):raise ValueError('synthetic private failure')
    if {fault!r}=='context':w._publication_context=refuse
    else:w._project_narratives=refuse
if {fault!r}=='projection':
    def refuse(*args):raise OSError('synthetic private failure')
    w._publish_sanitized_junit=refuse
if {fault!r}=='jsonio':
    write=w._write_json
    def refuse(path,payload):
        if path.name=='execution.json':raise OSError('synthetic private failure')
        return write(path,payload)
    w._write_json=refuse
raise SystemExit(w.main(sys.argv[1:]))
"""
    args = [
        "--root",
        str(root),
        "run",
        "--expected",
        "collection.json",
        "--evidence",
        "execution.json",
        "--junit",
        "result.xml",
        "--validation",
        "validation.json",
        "--",
        "test_probe.py",
        "-q",
        "-ra",
        "-c",
        "pytest.ini",
        "--confcutdir",
        str(root),
    ]
    return subprocess.run(
        [sys.executable, "-c", driver, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("origin", ["property", "parameter"])
@pytest.mark.parametrize("action", ["skip", "xfail", "failure", "pass"])
@pytest.mark.parametrize("secret", VALUES, ids=["literal", "escaped", "short"])
def test_context_reaches_all_public_narratives(tmp_path: Path, origin: str, action: str, secret: str) -> None:
    ending = {
        "skip": "pytest.skip(VALUE)",
        "xfail": "pytest.xfail(VALUE)",
        "failure": 'assert value == "expected"',
        "pass": "assert True",
    }[action]
    decorator = '@pytest.mark.parametrize("value", [VALUE], ids=[VALUE])' if origin == "parameter" else ""
    parameters = "value, record_property" if origin == "parameter" else "record_property"
    body = "" if origin == "parameter" else '    value = VALUE\n    record_property("password", VALUE)\n'
    prepare(
        tmp_path,
        f"import pytest\nVALUE={secret!r}\n{decorator}\ndef test_subject({parameters}):\n"
        + body
        + '    record_property("build_label", "useful public diagnostic")\n    '
        + ending
        + "\n",
    )
    assert invoke(tmp_path, "collect").returncode == 0
    for name in ["execution.json", "result.xml", "validation.json"]:
        (tmp_path / name).write_text("STALE_GREEN")
    result = run_observed(tmp_path)
    assert result.returncode == (0 if action == "pass" else 1)
    observed = json.loads((tmp_path / "observed.json").read_text())
    execution = json.loads((tmp_path / "execution.json").read_text())
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert execution["collection"] == observed["evidence"]["collection"]
    assert execution["pytest_exitstatus"] == observed["evidence"]["pytest_exitstatus"]
    for raw, safe in zip(observed["evidence"]["reports"], execution["reports"], strict=True):
        for key in raw.keys() - {"skip_reason", "wasxfail"}:
            assert safe[key] == raw[key]
        for key in ["skip_reason", "wasxfail"]:
            if raw[key] and secret in raw[key]:
                assert secret not in safe[key]
    tree = ET.parse(tmp_path / "result.xml")
    case = tree.find(".//testcase")
    assert case is not None
    assert case.get("junit_identity_sha256") == execution["collection"][0]["junit_identity_sha256"]
    assert validation["cases"][0]["junit_identity_sha256"] == case.get("junit_identity_sha256")
    suite = tree.find(".//testsuite")
    label = tree.find('.//property[@name="build_label"]')
    assert suite is not None and label is not None
    assert suite.get("tests") == "1"
    assert label.get("value") == "useful public diagnostic"
    text = "".join(
        p.read_text()
        for p in [tmp_path / "result.xml", tmp_path / "execution.json", tmp_path / "validation.json"]
    )
    assert "STALE_GREEN" not in text
    if secret != "1":
        assert all(
            form not in text + result.stdout + result.stderr
            for form in {secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]}
        )
    if action in {"skip", "xfail"}:
        assert secret not in validation["cases"][0]["skip_reason"]
    if action == "failure":
        failure = tree.find(".//failure")
        assert failure is not None
        assert "expected" in "".join(failure.itertext())


@pytest.mark.parametrize("fault", ["validator", "partial", "projection", "jsonio", "context", "narrative"])
def test_failed_context_or_publication_does_not_leave_public_json(tmp_path: Path, fault: str) -> None:
    prepare(
        tmp_path,
        "import pytest\ndef test_ok(record_property):\n"
        "    record_property('password', 'private-reason')\n"
        "    pytest.skip('private-reason')\n",
    )
    assert invoke(tmp_path, "collect").returncode == 0
    for name in ["execution.json", "result.xml", "validation.json"]:
        (tmp_path / name).write_text("STALE_GREEN")
    result = run_observed(tmp_path, fault)
    assert result.returncode != 0
    assert not (tmp_path / "execution.json").exists() and not (tmp_path / "result.xml").exists()
    assert "synthetic private failure" not in result.stdout + result.stderr
    assert json.loads((tmp_path / "validation.json").read_text())["return_code"] != 0
    assert not (tmp_path / ".result.xml.private").exists()
    # Same public paths recover through a fresh original execution.
    assert run_observed(tmp_path).returncode == 1
    assert "private-reason" not in (tmp_path / "validation.json").read_text()


def test_collection_stages_json_and_refuses_publication_io(tmp_path: Path) -> None:
    prepare(tmp_path, "def test_ok(): assert True\n")
    driver = f"""
import sys
from pathlib import Path
sys.path.insert(0,{str(WRAPPER.parent)!r})
import run_live_pytest as w
original=w.pytest.main
def observed(*args,**kwargs):
    rc=original(*args,**kwargs)
    assert not Path('collection.json').exists()
    output=kwargs['plugins'][0].output
    assert output.stat().st_mode & 0o777==0o600
    assert output.parent.stat().st_mode & 0o777==0o700
    return rc
w.pytest.main=observed
def refuse(*args):raise OSError('private publication failure')
w._write_json=refuse
raise SystemExit(w.main(sys.argv[1:]))
"""
    (tmp_path / "collection.json").write_text("STALE_GREEN")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            driver,
            "--root",
            str(tmp_path),
            "collect",
            "--output",
            "collection.json",
            "--",
            "test_probe.py",
            "-q",
            "-c",
            "pytest.ini",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 3
    assert "private publication failure" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "collection.json").exists()
    assert invoke(tmp_path, "collect").returncode == 0
