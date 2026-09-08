"""PEC-V1/V2: pytest real, fontes sintéticas privadas e coleção independente.

Não executa fixtures de serviços. A autorização de companions é distinta do
resultado RED de domínio; nenhum segredo real é lido.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import pytest_execution_evidence as core

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "scripts/ci/pytest_execution_evidence.py"
BOOTSTRAP = """
import importlib.util, pathlib, sys, pytest
spec = importlib.util.spec_from_file_location('core_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
plugin = module.EvidencePlugin(pathlib.Path(sys.argv[3]), root)
raise SystemExit(pytest.main(sys.argv[4:], plugins=[plugin]))
"""


def prepare(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pytest.ini").write_text("[pytest]\nasyncio_mode=auto\n")
    source = root / "test_subject.py"
    source.write_text("from __future__ import annotations\nimport pytest\n" + body)
    return source


def invoke(
    root: Path,
    label: str,
    *,
    collect: bool = False,
    selection: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    bootstrap: str = BOOTSTRAP,
) -> tuple[int, Path, dict[str, Any]]:
    output, xml = root / f"{label}.json", root / f"{label}.xml"
    args = [
        sys.executable,
        "-I",
        "-c",
        bootstrap,
        str(PLUGIN),
        str(root),
        str(output),
        "--disable-plugin-autoload",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "no:cacheprovider",
        "-c",
        str(root / "pytest.ini"),
        "--confcutdir",
        str(root),
        "-q",
        f"--junitxml={xml}",
    ]
    if collect:
        args.append("--collect-only")
    args.extend(selection or [str(root / "test_subject.py")])
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PYTHON", "MAEZO_CHAOS"))}
    env.update(extra_env or {})
    run = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=30, check=False)
    # Raw pytest XML/stdout are producer-private. Tests inspect them in tmp_path;
    # neither is forwarded into failure messages or published evidence here.
    return run.returncode, xml, json.loads(output.read_text())


def independent_execution(root: Path) -> tuple[int, Path, dict[str, Any], list[dict[str, Any]]]:
    collected_rc, _, collected = invoke(root, "collect", collect=True)
    assert collected_rc == 0
    rc, xml, actual = invoke(root, "run")
    return rc, xml, actual, collected["collection"]


def execute_registered_companion(root: Path, body: str) -> tuple[int, Path, dict[str, Any]]:
    """Trust fixture explícita para o controle positivo autoral anterior.

    Somente este bootstrap de teste substitui o catálogo fechado por UMA
    declaração sintética autorizada. EvidencePlugin não possui parâmetro/env de
    registro; um teste comum não recebe autoridade por conter mutation_active.
    A guarda, declaração pinada e callable passam pelo código real do núcleo.
    """
    prepare(root, body)
    registration = """
import ast, hashlib
definition = next(n for n in ast.parse((root/'test_subject.py').read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == 'test_mutation')
module._COMPANION_ROOT = root
lines = (root/'test_subject.py').read_text().splitlines(keepends=True)
source = ''.join(lines[min(d.lineno for d in definition.decorator_list)-1:definition.end_lineno])
module._COMPANIONS = {'test_subject.py::test_mutation': (
    'example', 'broken_subject', hashlib.sha256(source.encode()).hexdigest())}
module._GUARD_SHA256 = hashlib.sha256((root/'tests/integration/chaos/mutations.py').read_bytes()).hexdigest()
"""
    bootstrap = BOOTSTRAP.replace(
        "plugin = module.EvidencePlugin", registration + "\nplugin = module.EvidencePlugin"
    )
    extra = {}
    if "MAEZO_CHAOS_MUTATE" in os.environ:
        extra["MAEZO_CHAOS_MUTATE"] = os.environ["MAEZO_CHAOS_MUTATE"]
    return invoke(root, "registered", extra_env=extra, bootstrap=bootstrap)


def copied_helper(root: Path) -> str:
    target = root / "tests/integration/chaos/mutations.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((REPO / "tests/integration/chaos/mutations.py").read_bytes())
    return (
        "import importlib.util\nfrom pathlib import Path\n"
        "spec=importlib.util.spec_from_file_location('copied_mutations', "
        "Path(__file__).parent/'tests/integration/chaos/mutations.py')\n"
        "mutations=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(mutations)\n"
    )


@pytest.mark.parametrize("token", ["invented", "a1_a2"])
def test_real_helper_and_inert_reference_do_not_authorize_an_omission(tmp_path: Path, token: str) -> None:
    body = copied_helper(tmp_path) + (
        "def test_control(): assert True\n"
        f"@pytest.mark.skipif(not mutations.mutation_active({token!r}), reason='PG unavailable')\n"
        "def test_not_a_companion():\n"
        "    if False: mutations.broken_start_process_always_start\n"
        "    pytest.skip('PG unavailable')\n"
    )
    prepare(tmp_path, body)
    rc, xml, actual, expected = independent_execution(tmp_path)
    assert rc == 0
    result = core.validate_execution(xml, actual, expected, rc)
    assert result["return_code"] != 0
    assert result["case_counts"].get("inactive_companion", 0) == 0
    assert result["case_counts"]["passed"] == 1
    active_env = {"MAEZO_CHAOS_MUTATE": token}
    _, _, active_expected = invoke(tmp_path, "collect-active", collect=True, extra_env=active_env)
    active_rc, active_xml, active = invoke(tmp_path, "run-active", extra_env=active_env)
    assert (
        core.validate_execution(active_xml, active, active_expected["collection"], active_rc)["return_code"]
        != 0
    )


def credential(label: str) -> str:
    return "_".join(("PEC", "PUBLIC", "PROBE", label))


def secret_parameters(root: Path) -> tuple[str, str]:
    first, second = credential("FIRST"), credential("SECOND")
    ids = [f"postgresql://synthetic:{secret}@127.0.0.1:9/probe" for secret in (first, second)]
    prepare(
        root,
        f"@pytest.mark.parametrize('value', [1, 2], ids={ids!r})\n"
        "def test_parameter(value): assert value > 0\n",
    )
    return first, second


def test_plugin_and_validator_keep_credentials_private_and_cases_distinct(tmp_path: Path) -> None:
    sentinels = secret_parameters(tmp_path)
    rc, xml, actual, expected = independent_execution(tmp_path)
    result = core.validate_execution(xml, actual, expected, rc)
    assert result["return_code"] == 0
    unsafe_json = any(secret in json.dumps(actual) for secret in sentinels)
    unsafe_cases = any(secret in json.dumps(result) for secret in sentinels)
    assert not unsafe_json
    assert not unsafe_cases
    assert len({item["nodeid_sha256"] for item in expected}) == 2
    assert len({item["nodeid"] for item in expected}) == 2
    assert len({case["junit_identity_sha256"] for case in result["cases"]}) == 2


@pytest.mark.parametrize("mutation", ["substitute", "duplicate", "omit", "phase", "fingerprint"])
def test_private_identity_mismatches_are_rejected_before_public_projection(
    tmp_path: Path, mutation: str
) -> None:
    sentinels = secret_parameters(tmp_path)
    rc, xml, actual, expected = independent_execution(tmp_path)
    tree = ET.parse(xml)
    suite = tree.getroot().find("testsuite")
    assert suite is not None
    first, second = suite.findall("testcase")
    if mutation == "substitute":
        first.set("name", first.attrib["name"].replace(sentinels[0], credential("THIRD")))
    elif mutation == "duplicate":
        second.attrib.update(first.attrib)
    elif mutation == "omit":
        suite.remove(second)
        suite.set("tests", "1")
    elif mutation == "phase":
        actual["reports"] = [report for report in actual["reports"] if report["phase"] != "call"]
    else:
        actual = copy.deepcopy(actual)
        actual["reports"][0]["nodeid_sha256"] = "0" * 64
    tree.write(xml)
    result = core.validate_execution(xml, actual, expected, rc)
    assert result["return_code"] != 0
    assert not any(secret in json.dumps(result) for secret in (*sentinels, credential("THIRD")))


def test_old_synthetic_companion_example_requires_explicit_authority(tmp_path: Path) -> None:
    target = tmp_path / "tests/integration/chaos/mutations.py"
    target.parent.mkdir(parents=True)
    target.write_text(
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
    prepare(tmp_path, body)
    rc, xml, actual, expected = independent_execution(tmp_path)
    assert core.validate_execution(xml, actual, expected, rc)["return_code"] != 0


def test_companion_catalog_matches_all_real_opt_in_declarations() -> None:
    discovered = {}
    for path in (REPO / "tests/integration").rglob("test_*.py"):
        for definition in ast.parse(path.read_text()).body:
            if not isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in definition.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                calls = [
                    node
                    for node in ast.walk(decorator)
                    if isinstance(node, ast.Call)
                    and (
                        (isinstance(node.func, ast.Attribute) and node.func.attr == "mutation_active")
                        or (isinstance(node.func, ast.Name) and node.func.id == "mutation_active")
                    )
                ]
                for call in calls:
                    assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
                    nodeid = path.relative_to(REPO).as_posix() + "::" + definition.name
                    discovered[nodeid] = (
                        call.args[0].value,
                        hashlib.sha256(
                            "".join(
                                path.read_text().splitlines(keepends=True)[
                                    min(d.lineno for d in definition.decorator_list)
                                    - 1 : definition.end_lineno
                                ]
                            ).encode()
                        ).hexdigest(),
                    )
    assert discovered == {nodeid: (token, pin) for nodeid, (token, _, pin) in core._COMPANIONS.items()}
    assert len(discovered) == 6


@pytest.mark.parametrize("change", ["control", "origin", "inert", "token", "callable", "helper", "name"])
def test_exact_canonical_declaration_is_required_even_with_real_helper(tmp_path: Path, change: str) -> None:
    nodeid = next(iter(core._COMPANIONS))
    relative, name = nodeid.split("::")
    token, broken, _ = core._COMPANIONS[nodeid]
    source = REPO / relative
    definition = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    lines = source.read_text().splitlines(keepends=True)
    body = "".join(lines[min(d.lineno for d in definition.decorator_list) - 1 : definition.end_lineno])
    if change == "inert":
        definition.body = ast.parse(f"if False: mutations.{broken}\npytest.skip('PG unavailable')").body
        body = ast.unparse(definition) + "\n"
    elif change == "token":
        body = body.replace(f'"{token}"', '"invented"')
    elif change == "callable":
        body = body.replace(broken, "broken_start_process_always_start")
    elif change == "name":
        body = body.replace(name, "test_invented")
    prepare(tmp_path, "def test_control(): assert True\n")
    copied_helper(tmp_path)
    helper = tmp_path / "tests/integration/chaos/mutations.py"
    if change == "helper":
        helper.write_text(helper.read_text() + "# source drift\n")
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from __future__ import annotations\nimport pytest, importlib.util\n"
        f"spec=importlib.util.spec_from_file_location('canonical_copy', {str(helper)!r})\n"
        "mutations=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(mutations)\n" + body
    )
    # A trusted bootstrap models the authenticated checkout root only. Production
    # declaration pins/tokens/helper digest are untouched, never derived from input.
    bootstrap = (
        BOOTSTRAP
        if change == "origin"
        else BOOTSTRAP.replace(
            "plugin = module.EvidencePlugin", "module._COMPANION_ROOT = root\nplugin = module.EvidencePlugin"
        )
    )
    selection = [str(tmp_path / "test_subject.py"), str(target)]
    collect_rc, _, collected = invoke(
        tmp_path, "collect", collect=True, selection=selection, bootstrap=bootstrap
    )
    assert collect_rc == 0
    rc, xml, actual = invoke(tmp_path, "run", selection=selection, bootstrap=bootstrap)
    assert rc == 0
    result = core.validate_execution(xml, actual, collected["collection"], rc)
    if change == "control":
        assert result["return_code"] == 0
        assert result["case_counts"] == {"passed": 1, "inactive_companion": 1}
        assert result["inactive_companions_require_separate_RED"] == 1
    else:
        assert result["return_code"] != 0
        assert result["case_counts"].get("inactive_companion", 0) == 0


@pytest.mark.parametrize("style", ["json", "repr"])
def test_quoted_and_nested_credential_reasons_stay_private(tmp_path: Path, style: str) -> None:
    secret = credential("REASON")
    structured = {"nested": {"password": secret, "api_key": secret + " with space"}}
    reason = json.dumps(structured) if style == "json" else "ValidationError input_value=" + repr(structured)
    prepare(tmp_path, f"@pytest.mark.xfail(strict=True, reason={reason!r})\ndef test_gap(): assert False\n")
    rc, xml, actual, expected = independent_execution(tmp_path)
    result = core.validate_execution(xml, actual, expected, rc)
    assert result["return_code"] == 0
    assert result["case_counts"] == {"xfailed_executed": 1}
    unsafe_json = secret in json.dumps(actual)
    unsafe_result = secret in json.dumps(result)
    assert not unsafe_json
    assert not unsafe_result
    public = core.public_junit_identity("test_module", reason)
    unsafe_public = secret in json.dumps(public)
    assert not unsafe_public
    assert (
        public["junit_identity_sha256"]
        != core.public_junit_identity("test_module", reason + "x")["junit_identity_sha256"]
    )
