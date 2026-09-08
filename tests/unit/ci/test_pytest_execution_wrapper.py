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
    published_keys = [(case.get("classname"), case.get("name")) for case in published.findall(".//testcase")]
    validated_keys = [(case["classname"], case["name"]) for case in report["cases"]]
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


def test_published_junit_redacts_failure_text_without_changing_identity(tmp_path: Path) -> None:
    root = _prepare(
        tmp_path,
        """def test_failure():
    raise RuntimeError("password=synthetic-secret")
""",
    )

    result, report = _run(root)

    assert result.returncode != 0
    assert report["return_code"] != 0
    published = (root / "result.xml").read_text(encoding="utf-8")
    assert "synthetic-secret" not in published
    assert "&lt;redacted&gt;" in published
    case = ET.fromstring(published).find(".//testcase")
    assert case is not None
    assert case.get("name") == "test_failure"
    assert not (root / ".result.xml.private").exists()
