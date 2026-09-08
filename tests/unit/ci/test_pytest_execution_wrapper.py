"""Provas reais do wrapper que liga a evidência de execução às lanes de serviço."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "run_live_pytest.py"


def _invoke(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root), *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _collect(root: Path) -> subprocess.CompletedProcess[str]:
    return _invoke(
        root,
        "collect",
        "--output",
        "collection.json",
        "--",
        "test_probe.py",
        "-q",
        "-c",
        "pytest.ini",
        "--confcutdir",
        str(root),
    )


def _run(root: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = _invoke(
        root,
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
    )
    return result, json.loads((root / "validation.json").read_text())


def _prepare(tmp_path: Path, source: str) -> Path:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(source, encoding="utf-8")
    collected = _collect(tmp_path)
    assert collected.returncode == 0, collected.stdout
    return tmp_path


def test_pass_and_strict_xfail_with_body_entry_are_verified(tmp_path: Path) -> None:
    root = _prepare(
        tmp_path,
        """import pytest

def test_control():
    assert True

@pytest.mark.xfail(strict=True, reason="known synthetic failure")
def test_expected_failure():
    assert False
""",
    )

    result, report = _run(root)

    assert result.returncode == 0, result.stdout
    assert report["return_code"] == 0
    assert report["case_counts"]["passed"] == 1
    assert report["case_counts"]["xfailed_executed"] == 1
    published = ET.parse(root / "result.xml").getroot()
    published_keys = [
        (
            case.get("classname"),
            case.get("name"),
            case.get("junit_identity_sha256"),
        )
        for case in published.findall(".//testcase")
    ]
    validated_keys = [
        (case["classname"], case["name"], case["junit_identity_sha256"]) for case in report["cases"]
    ]
    assert published_keys == validated_keys
    assert not (root / ".result.xml.private").exists()


def test_execution_cannot_replace_a_collected_identity(tmp_path: Path) -> None:
    root = _prepare(
        tmp_path,
        """def test_a():
    assert True

def test_b():
    assert True
""",
    )
    (root / "test_probe.py").write_text(
        """def test_a():
    assert True

def test_intruder():
    assert True
""",
        encoding="utf-8",
    )

    result, report = _run(root)

    assert result.returncode != 0
    assert report["return_code"] != 0
    assert any("diverge" in error for error in report["errors"])


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """import pytest
def test_control(): pass
def test_pg():
    pytest.skip("Postgres unreachable; MAEZO_CHAOS_MUTATE=invented")
""",
            id="token-inventado-no-motivo",
        ),
        pytest.param(
            """import pytest
def test_control(): pass
@pytest.mark.parametrize("value", ["x"], ids=["MAEZO_CHAOS_MUTATE=invented"])
def test_pg(value):
    pytest.skip("Postgres unreachable")
""",
            id="token-inventado-no-parametro",
        ),
        pytest.param(
            """import pytest
def test_control(): pass
@pytest.mark.xfail(strict=True, run=False, reason="known synthetic failure")
def test_never_runs():
    assert False
""",
            id="xfail-run-false",
        ),
        pytest.param(
            """import pytest
def test_control(): pass
@pytest.fixture
def unavailable():
    pytest.xfail("known synthetic failure")
def test_never_calls(unavailable):
    assert False
""",
            id="xfail-no-setup",
        ),
        pytest.param(
            """import pytest
def test_control(): pass
@pytest.mark.xfail(strict=False, reason="known synthetic failure")
def test_xpass():
    assert True
""",
            id="xpass-nao-estrito",
        ),
    ],
)
def test_labels_without_verified_execution_never_authorize_a_pass(tmp_path: Path, source: str) -> None:
    root = _prepare(tmp_path, source)

    result, report = _run(root)

    assert result.returncode != 0
    assert report["return_code"] != 0
    assert any("não verificado" in error for error in report["errors"])


def test_safe_artifacts_redact_structured_secrets_and_keep_distinct_identities(
    tmp_path: Path,
) -> None:
    sentinels = (
        "synthetic secret with spaces",
        "synthetic api key with spaces",
        "first uri password",
        "second uri password",
    )
    root = _prepare(
        tmp_path,
        """import pytest

REASON = 'payload={"password": "synthetic secret with spaces", "api_key": "synthetic api key with spaces"}'

@pytest.mark.xfail(strict=True, reason=REASON)
@pytest.mark.parametrize(
    "value",
    [1, 2],
    ids=[
        "postgresql://synthetic:first uri password@127.0.0.1:9/probe",
        "postgresql://synthetic:second uri password@127.0.0.1:9/probe",
    ],
)
def test_private(value):
    raise AssertionError(REASON)
""",
    )

    result, report = _run(root)

    assert result.returncode == 0, result.stdout
    assert report["return_code"] == 0
    assert report["case_counts"] == {"xfailed_executed": 2}
    artifacts = [
        root / "collection.json",
        root / "execution.json",
        root / "validation.json",
        root / "result.xml",
    ]
    published = "\n".join(path.read_text(encoding="utf-8") for path in artifacts)
    assert all(secret not in published for secret in sentinels)
    cases = ET.parse(root / "result.xml").getroot().findall(".//testcase")
    assert len(cases) == 2
    assert len({case.get("name") for case in cases}) == 2
    assert len({case.get("junit_identity_sha256") for case in cases}) == 2
    assert all(case.get("name", "").startswith("test_private[parameters:") for case in cases)
    assert not (root / ".result.xml.private").exists()


def test_failed_collection_cannot_reuse_stale_public_artifacts(tmp_path: Path) -> None:
    root = _prepare(tmp_path, "def test_control(): pass\n")
    stale_paths = [root / name for name in ("execution.json", "result.xml", "validation.json")]
    for path in stale_paths:
        path.write_text("STALE-PASS", encoding="utf-8")
    (root / "test_probe.py").write_text(
        "raise KeyboardInterrupt(\"payload={'password': 'interrupted secret with spaces'}\")\n",
        encoding="utf-8",
    )

    result, report = _run(root)

    assert result.returncode != 0
    assert report["return_code"] != 0
    assert all("STALE-PASS" not in path.read_text(encoding="utf-8") for path in stale_paths)
    assert all(
        "interrupted secret with spaces" not in path.read_text(encoding="utf-8") for path in stale_paths
    )
    assert not (root / ".result.xml.private").exists()
    assert not (root / ".result.xml.publishing").exists()
    assert not (root / ".validation.json.tmp").exists()
