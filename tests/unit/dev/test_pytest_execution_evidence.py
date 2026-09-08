from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.pytest_execution_evidence import validate_execution

PLUGIN = Path(__file__).resolve().parents[3] / "scripts/ci/pytest_execution_evidence.py"
BOOTSTRAP = """
import importlib.util, pathlib, sys, pytest
spec = importlib.util.spec_from_file_location('execution_evidence', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
plugin = module.EvidencePlugin(root / 'execution.json', root)
raise SystemExit(pytest.main(sys.argv[3:], plugins=[plugin]))
"""


def execute(tmp_path: Path, body: str) -> tuple[int, Path, dict[str, Any]]:
    (tmp_path / "test_subject.py").write_text("import pytest\n" + body)
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode=auto\n")
    xml = tmp_path / "junit.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            BOOTSTRAP,
            str(PLUGIN),
            str(tmp_path),
            "--disable-plugin-autoload",
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--confcutdir",
            str(tmp_path),
            str(tmp_path / "test_subject.py"),
            "-q",
            f"--junitxml={xml}",
        ],
        cwd=tmp_path,
        env={k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PYTHON"))},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    evidence = json.loads((tmp_path / "execution.json").read_text())
    return result.returncode, xml, evidence


@pytest.mark.parametrize(
    "body,accepted",
    [
        ("def test_ok(): assert True\n", True),
        ("async def test_async(): assert True\n", True),
        ("@pytest.mark.xfail(strict=True, reason='canonical gap')\ndef test_x(): assert False\n", True),
        ("@pytest.mark.xfail(strict=True, run=False, reason='omitted')\ndef test_x(): assert False\n", False),
        ("@pytest.fixture\ndef f(): pytest.xfail('fixture gap')\ndef test_x(f): assert False\n", False),
        ("@pytest.mark.xfail(strict=False, reason='nonstrict')\ndef test_x(): assert False\n", False),
        ("def test_control(): assert True\n@pytest.mark.xfail(strict=False)\ndef test_x(): pass\n", False),
        ("def test_control(): assert True\n@pytest.mark.xfail(strict=True)\ndef test_x(): pass\n", False),
        (
            "def test_control(): pass\n"
            "@pytest.mark.skip(reason='only runs when MAEZO_CHAOS_MUTATE=invented')\ndef test_x(): pass\n",
            False,
        ),
        (
            "def test_control(): pass\n"
            "@pytest.mark.parametrize('v',['MAEZO_CHAOS_MUTATE=invented'])\n"
            "def test_x(v): pytest.skip('PG unavailable')\n",
            False,
        ),
        ("# no test body\n", False),
    ],
)
def test_real_execution_distinguishes_body_from_omission(tmp_path: Path, body: str, accepted: bool) -> None:
    rc, xml, evidence = execute(tmp_path, body)
    result = validate_execution(xml, evidence, evidence["collection"], rc)
    assert (result["return_code"] == 0) is accepted, result


@pytest.mark.parametrize("mutation", ["header", "identity", "duplicate", "outcome", "phase", "source"])
def test_real_evidence_corruption_is_rejected(tmp_path: Path, mutation: str) -> None:
    rc, xml, evidence = execute(tmp_path, "def test_one(): pass\ndef test_two(): pass\n")
    expected = copy.deepcopy(evidence["collection"])
    tree = ET.parse(xml)
    suite = tree.getroot().find("testsuite")
    assert suite is not None
    case = suite.find("testcase")
    assert case is not None
    if mutation == "header":
        suite.remove(case)
    elif mutation == "identity":
        case.set("name", "test_substituted")
    elif mutation == "duplicate":
        second = suite.findall("testcase")[1]
        second.attrib.update(case.attrib)
    elif mutation == "outcome":
        ET.SubElement(case, "skipped", {"type": "pytest.skip"})
        suite.set("skipped", "1")
    elif mutation == "phase":
        evidence["reports"] = [r for r in evidence["reports"] if r["phase"] != "call"]
    elif mutation == "source":
        evidence["collection"][0]["source_sha256"] = "0" * 64
    tree.write(xml)
    result = validate_execution(xml, evidence, expected, rc)
    assert result["return_code"] != 0
    assert result["errors"]


def test_missing_junit_is_not_a_pass(tmp_path: Path) -> None:
    rc, xml, evidence = execute(tmp_path, "def test_ok(): pass\n")
    xml.unlink()
    assert validate_execution(xml, evidence, evidence["collection"], rc)["return_code"] != 0


def test_companion_requires_canonical_guard_and_red_is_a_separate_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mutation_file = tmp_path / "tests/integration/chaos/mutations.py"
    mutation_file.parent.mkdir(parents=True)
    mutation_file.write_text(
        "import os\n"
        "def mutation_active(token): return os.environ.get('MAEZO_CHAOS_MUTATE') == token\n"
        "def broken_subject(): return False\n"
    )
    body = (
        "import importlib.util\nfrom pathlib import Path\n"
        "spec=importlib.util.spec_from_file_location('fixture_mutations', "
        "Path(__file__).parent/'tests/integration/chaos/mutations.py')\n"
        "mutations=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(mutations)\n"
        "def test_control(): assert True\n"
        "@pytest.mark.skipif(not mutations.mutation_active('example'), reason='canonical opt-in')\n"
        "def test_mutation(): assert mutations.broken_subject()\n"
    )
    monkeypatch.delenv("MAEZO_CHAOS_MUTATE", raising=False)
    rc, xml, evidence = execute(tmp_path, body)
    accepted = validate_execution(xml, evidence, evidence["collection"], rc)
    assert accepted["return_code"] == 0, accepted
    assert accepted["case_counts"] == {"passed": 1, "inactive_companion": 1}
    assert accepted["inactive_companions_require_separate_RED"] == 1
    monkeypatch.setenv("MAEZO_CHAOS_MUTATE", "example")
    red_rc, red_xml, red_evidence = execute(tmp_path, body)
    rejected = validate_execution(red_xml, red_evidence, evidence["collection"], red_rc)
    assert red_rc == 1
    assert rejected["return_code"] == 1
    mutation_call = [
        r for r in red_evidence["reports"] if r["nodeid"].endswith("test_mutation") and r["phase"] == "call"
    ]
    assert mutation_call[0]["outcome"] == "failed"
    assert mutation_call[0]["body_entered"] is True
