"""Pre-import admission controls; no engine or live test module is imported."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.ci import check_evidence_ledger_hashes as gate
from scripts.ci import ledger_history_proofs as history

ROOT = Path(__file__).resolve().parents[3]
LIVE = "tests/unit/a2a/test_atomic_admission_live_pg.py"


def row(path: str) -> gate.DeclaredRow:
    digest = gate.compute_recipe_hash([f"{path}::test_case PASSED"]).removeprefix("sha256:")
    return gate.DeclaredRow("ADMISSION", path, digest)


def install_capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def capture(root: Path, path: str, python: str) -> gate.PytestCapture:
        calls.append(path)
        return gate.PytestCapture(True, f"{path}::test_case PASSED\n", 0, "controlled capture")

    monkeypatch.setattr(gate, "capture_pytest_recipe", capture)
    return calls


def test_actual_integration_marked_unit_path_refused_before_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_capture(monkeypatch)
    result = gate.verify_row(ROOT, row(LIVE), sys.executable)
    assert not result.ok, "integration-marked unit path acquired offline hash credit"
    assert calls == [], "the real live test would have been imported/executed"
    assert "allow-live" in result.message


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytestmark = pytest.mark.integration\n",
        "import pytest as p\npytestmark = [p.mark.integration]\n",
        "from pytest import mark as m\n@m.integration\ndef test_case(): pass\n",
        "import pytest\n@pytest.mark.integration\nclass TestRenamed: pass\n",
    ],
)
def test_marked_renamed_paths_refuse_without_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    path = "tests/unit/arbitrary/test_renamed.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source + "raise RuntimeError('must never import')\n")
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytestmark = getattr(pytest.mark, 'inte' + 'gration')\n",
        "from somewhere import marks\npytestmark = marks()\n",
        "from somewhere import decorate\n@decorate\ndef test_case(): pass\n",
        "import pytest\nm = pytest.mark\npytestmark = getattr(m, 'integration')\n",
        "exec('pytestmark = integration')\n",
        "from somewhere import *\n",
        "def pytest_collection_modifyitems(items): pass\n",
        "import pytest\npytestmark: object = choose_marks()\n",
        "import pytest\npytestmark = []\npytestmark.append(choose_mark())\n",
        "from somewhere import pytestmark\n",
        "from somewhere import LiveBase\nclass TestDerived(LiveBase): pass\n",
        "import pytest\n@pytest.mark.parametrize('x', [pytest.param(1, marks=choose())])\n"
        "def test_case(x): pass\n",
        "def test_case(x=choose()): pass\n",
        "import pytest as p\nfrom elsewhere import p\n@p.mark.unit\ndef test_case(): pass\n",
        "import pytest as p\np: object = replacement\n@p.mark.unit\ndef test_case(): pass\n",
        "import pytest as p\np, other = replacements\n@p.mark.unit\ndef test_case(): pass\n",
    ],
)
def test_dynamic_classification_never_grants_offline_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    path = "tests/unit/test_dynamic.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source)
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


def test_explicit_live_assertion_and_ordinary_offline_are_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_capture(monkeypatch)
    assert gate.verify_row(ROOT, row(LIVE), sys.executable, allow_live=True).ok
    path = "tests/unit/test_ordinary.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("def test_case():\n    assert True\n")
    assert gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == [LIVE, path]


@pytest.mark.parametrize("allow_live", [False, True])
@pytest.mark.parametrize("kind", ["leaf", "parent", "unsafe", "fifo"])
def test_unsafe_and_special_paths_refuse_even_with_live_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_live: bool, kind: str
) -> None:
    import os

    path = "tests/unit/test_case.py"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_case.py").write_text("def test_case(): pass\n")
    (tmp_path / "tests").mkdir()
    if kind == "parent":
        (tmp_path / "tests/unit").symlink_to(outside, target_is_directory=True)
    else:
        (tmp_path / "tests/unit").mkdir()
        if kind == "leaf":
            (tmp_path / path).symlink_to(outside / "test_case.py")
        elif kind == "fifo":
            os.mkfifo(tmp_path / path)
        else:
            path = "tests/../outside/test_case.py"
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(tmp_path, row(path), sys.executable, allow_live=allow_live).ok
    assert calls == []


def test_integration_subtree_still_refuses_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(ROOT, row("tests/integration/missing.py"), sys.executable).ok
    assert calls == []


def test_frozen_producer_recipe_closure_is_preserved() -> None:
    original = subprocess.check_output(
        ["git", "show", f"{history.BASE}:scripts/ci/check_evidence_ledger_hashes.py"], cwd=ROOT
    )
    assert history.recipe_closure((ROOT / "scripts/ci/check_evidence_ledger_hashes.py").read_bytes()) == (
        history.recipe_closure(original)
    )


def test_static_nonlive_pytest_metadata_is_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = "tests/unit/test_static.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(
        "import pytest as p\n"
        "pytestmark = [p.mark.unit]\n"
        "@p.fixture(scope='module')\ndef value(): return 1\n"
        "@p.mark.parametrize('x', [p.param(1, marks=p.mark.unit)])\n"
        "def test_case(x): pass\n"
    )
    calls = install_capture(monkeypatch)
    assert gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == [path]


@pytest.mark.parametrize("historical_live", [True, False])
@pytest.mark.parametrize("tamper", [True, False])
def test_historical_classification_uses_bound_blob_not_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_live: bool, tamper: bool
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Admission control")
    git("config", "user.email", "admission@example.invalid")
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    offline = "def test_case(): pass\n"
    live = "import pytest\npytestmark = pytest.mark.integration\n" + offline
    bound = live if historical_live else offline
    target.write_text(bound)
    (tmp_path / "uv.lock").write_text("locked\n")
    git("add", ".")
    git("commit", "-qm", "bound source")
    commit = git("rev-parse", "HEAD")
    target.write_text(offline if historical_live else live)
    git("add", ".")
    git("commit", "-qm", "opposite current marker")
    claim = gate.SupersessionClaim(
        "ADMISSION",
        commit,
        "a" * 64,
        "0" * 64 if tamper else hashlib.sha256(bound.encode()).hexdigest(),
        hashlib.sha256(b"locked\n").hexdigest(),
    )
    edge = gate.SupersessionEdge(row(path), row(path), claim)
    calls: list[str] = []

    def capture(*args: object, **kwargs: object) -> gate.PytestCapture:
        calls.append("historical")
        return gate.PytestCapture(True, f"{path}::test_case PASSED\n", 0, "bound history")

    monkeypatch.setattr(gate, "_capture_historical_recipe", capture)
    result = gate.verify_historical_row(tmp_path, edge, sys.executable)
    assert result.ok is (not historical_live and not tamper)
    assert calls == ([] if historical_live or tamper else ["historical"])


@pytest.mark.parametrize(
    "source,helper",
    [
        ("import pytest\npytestmark=pytest.mark.__getattr__('integration')\n", ""),
        ("import pytest\nm=pytest.mark\npytestmark=m.__getattr__('integration')\n", ""),
        (("import pytest\nm=pytest.mark\nreflect=m.__getattr__\npytestmark=reflect('integration')\n"), ""),
        ("import pytest\nm=pytest.mark\npytestmark=getattr(m, 'integration')\n", ""),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.integration)]\n",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "import pytest\nCASES=[pytest.param(1, marks=choose())]\n",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES + [2])\ndef test_case(x): pass\n"
            ),
            ("import pytest\nCASES=[pytest.param(1, marks=pytest.mark.__getattr__('integration'))]\n"),
        ),
        (
            (
                "import pytest\nmark=pytest.mark.unit\nCASES=[pytest.param(1, marks=mark)"
                "]\nmark=choose()\n@pytest.mark.parametrize('x', CASES)\ndef test_case(x):"
                " pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nCASES=[1]\nCASES.append(choose())\n@pytest.mark.parametriz"
                "e('x', CASES)\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nCASES=[1]\nif flag: CASES=[choose()]\n@pytest.mark.paramet"
                "rize('x', CASES)\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nCASES=[1]\nCASES[0]=choose()\n@pytest.mark.parametrize('x'"
                ", CASES)\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "from helper import CASES\n",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "from second import CASES\n",
        ),
        (
            (
                "import pytest\n@pytest.mark.parametrize('x', [pytest.param(1, **OPTIONS"
                ")])\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            "import pytest\nfrom helper import decorate\n@decorate\ndef test_case(): pass\n",
            "def decorate(f): return f\n",
        ),
        ("import pytest\nclass Base: pytestmark=choose()\nclass TestCase(Base): pass\n", ""),
        ("import pytest\nif flag: pytestmark=choose()\n", ""),
        ("import pytest\nfrom helper import case\ntest_case=case\n", ""),
        (
            (
                "import pytest\n@pytest.fixture(autouse=True)\ndef mark(request): request"
                ".applymarker('integration')\n"
            ),
            "",
        ),
        ("import pytest\npytest_plugins=['helper']\n", ""),
        ("def pytest_generate_tests(metafunc): pass\n", ""),
        ("import pytest as p\np.mark=replacement\n@p.mark.unit\ndef test_case(): pass\n", ""),
        (
            (
                "import pytest\nimport json\njson.loads=produce\n@pytest.mark.parametrize("
                "'x', json.loads('[1]'))\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nimport json\n@pytest.mark.parametrize('x', json.loads('[1"
                "]', parse_int=produce))\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nfrom pathlib import Path\nPath=choose\ndef cases(): return"
                " sorted(p.name for p in Path('.').iterdir())\n@pytest.mark.parametrize("
                "'x', cases())\ndef test_case(x): pass\n"
            ),
            "",
        ),
    ],
)
def test_third_repair_metadata_closure_refuses_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, helper: str
) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source)
    if helper:
        (tmp_path / "helper.py").write_text(helper)
    calls = install_capture(monkeypatch)
    result = gate.verify_row(tmp_path, row(path), sys.executable)
    assert not result.ok
    assert calls == []


@pytest.mark.parametrize(
    "source,helper",
    [
        ("from pathlib import Path\nROOT=Path(__file__).parent\ndef test_case(): pass\n", ""),
        (
            (
                "import pytest\nfrom dataclasses import dataclass\n@dataclass\nclass Helpe"
                "r: value: int\ndef test_case(): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nfrom contextlib import asynccontextmanager\n@asynccontext"
                "manager\nasync def helper(): yield 1\ndef test_case(): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nCASES=[1, 2]\n@pytest.mark.parametrize('x', CASES + [3])\n"
                "def test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "CASES=[1, 2]\n",
        ),
        (
            (
                "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
                "ASES)\ndef test_case(x): pass\n"
            ),
            "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.unit)]\n",
        ),
        (
            (
                "import pytest\nfrom helper import cases\n@pytest.mark.parametrize('a,b',"
                " cases())\ndef test_case(a,b): pass\n"
            ),
            "def cases(): return [(a,b) for a,b in arbitrary_data()]\n",
        ),
        (
            (
                "import pytest\nVALUES=[('a', lambda x:x)]\n@pytest.mark.parametrize('a,b"
                "', VALUES)\ndef test_case(a,b): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nMODELS={'a': arbitrary_model}\n@pytest.mark.parametrize('"
                "x', list(MODELS))\ndef test_case(x): pass\n"
            ),
            "",
        ),
        (
            (
                "import pytest\nfrom datetime import timedelta\n@pytest.mark.parametrize("
                "'x', [timedelta(seconds=1)])\ndef test_case(x): pass\n"
            ),
            "",
        ),
    ],
)
def test_third_repair_ordinary_roles_retain_offline_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, helper: str
) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source)
    if helper:
        (tmp_path / "helper.py").write_text(helper)
    calls = install_capture(monkeypatch)
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == "OFFLINE", admission.reason
    assert gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == [path]
    assert dict(admission.dependencies)[path] == hashlib.sha256(source.encode()).hexdigest()
    if helper:
        assert dict(admission.dependencies)["helper.py"] == hashlib.sha256(helper.encode()).hexdigest()


@pytest.mark.parametrize(
    "extra,body",
    [
        ("tests/conftest.py", "def pytest_collection_modifyitems(items): pass\n"),
        ("tests/unit/conftest.py", "pytest_plugins=['helper']\n"),
        ("tests/__init__.py", "pytestmark=unknown\n"),
        ("pyproject.toml", '[tool.pytest.ini_options]\npython_functions=["*"]\n'),
        ("pytest.ini", "[pytest]\naddopts=-p helper\n"),
        ("json.py", "def loads(x): return dangerous()\n"),
        ("tests/unit/pytest.py", "mark=replacement\n"),
    ],
)
def test_third_repair_collector_config_and_import_shadow_changes_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str, body: str
) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(
        "import pytest\nimport json\n@pytest.mark.parametrize('x', json.loads('[1"
        "]'))\ndef test_case(x): pass\n"
    )
    (tmp_path / extra).write_text(body)
    calls = install_capture(monkeypatch)
    assert gate.classify_test_admission(tmp_path, path).status == "UNKNOWN"
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


@pytest.mark.parametrize("special", ["symlink", "fifo"])
@pytest.mark.parametrize("allow_live", [False, True])
def test_third_repair_unsafe_helper_refuses_even_with_live_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, special: str, allow_live: bool
) -> None:
    import os

    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(
        "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
        "ASES)\ndef test_case(x): pass\n"
    )
    if special == "symlink":
        (tmp_path / "outside.py").write_text("CASES=[1]\n")
        (tmp_path / "helper.py").symlink_to(tmp_path / "outside.py")
    else:
        os.mkfifo(tmp_path / "helper.py")
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(tmp_path, row(path), sys.executable, allow_live=allow_live).ok
    assert calls == []


@pytest.mark.parametrize("historical_live", [True, False])
def test_third_repair_historical_helper_uses_own_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_live: bool
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Admission closure")
    git("config", "user.email", "admission@example.invalid")
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    source = (
        "import pytest\nfrom helper import CASES\n@pytest.mark.parametrize('x', C"
        "ASES)\ndef test_case(x): pass\n"
    )
    target.write_text(source)
    live = "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.integration)]\n"
    offline = "CASES=[1]\n"
    (tmp_path / "helper.py").write_text(live if historical_live else offline)
    (tmp_path / "uv.lock").write_text("locked\n")
    git("add", ".")
    git("commit", "-qm", "bound helper")
    commit = git("rev-parse", "HEAD")
    (tmp_path / "helper.py").write_text(offline if historical_live else live)
    git("add", ".")
    git("commit", "-qm", "opposite helper")
    claim = gate.SupersessionClaim(
        "ADMISSION",
        commit,
        "a" * 64,
        hashlib.sha256(source.encode()).hexdigest(),
        hashlib.sha256(b"locked\n").hexdigest(),
    )
    edge = gate.SupersessionEdge(row(path), row(path), claim)
    calls: list[str] = []

    def capture(*args: object, **kwargs: object) -> gate.PytestCapture:
        calls.append("historical")
        return gate.PytestCapture(True, f"{path}::test_case PASSED\n", 0, "bound helper")

    monkeypatch.setattr(gate, "_capture_historical_recipe", capture)
    admission = gate.classify_test_admission(
        tmp_path, path, commit, expected_source_sha256=claim.source_test_sha256
    )
    assert admission.status == ("LIVE" if historical_live else "OFFLINE")
    assert (
        dict(admission.dependencies)["helper.py"]
        == hashlib.sha256((live if historical_live else offline).encode()).hexdigest()
    )
    result = gate.verify_historical_row(tmp_path, edge, sys.executable)
    assert result.ok is not historical_live
    assert calls == ([] if historical_live else ["historical"])


def test_third_repair_current_source_drift_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("def test_case(): pass\n")
    original = gate._read_admission_source
    reads = 0

    def drifting(root: Path, relative: str) -> bytes:
        nonlocal reads
        data = original(root, relative)
        if relative == path:
            reads += 1
            if reads == 2:
                target.write_text("import pytest\npytestmark=pytest.mark.integration\n")
        return data

    calls = install_capture(monkeypatch)
    monkeypatch.setattr(gate, "_read_admission_source", drifting)
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


@pytest.mark.parametrize("special_name", ["__getattr__", "__getattribute__", "__dir__"])
def test_reflected_module_metadata_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, special_name: str
) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(f"def {special_name}(name): return choose_mark()\ndef test_case(): pass\n")
    calls = install_capture(monkeypatch)
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


def test_relative_import_and_package_metadata_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(
        "import pytest\nfrom .helper import CASES\n"
        "@pytest.mark.parametrize('x', CASES)\ndef test_case(x): pass\n"
    )
    helper = tmp_path / "tests/unit/helper.py"
    helper.write_text("CASES=[1]\n")
    package = tmp_path / "tests/unit/__init__.py"
    package.write_text("")
    calls = install_capture(monkeypatch)
    initial = gate.classify_test_admission(tmp_path, path)
    assert initial.status == "OFFLINE", initial.reason
    assert {"tests/unit/helper.py", "tests/unit/__init__.py"}.issubset(dict(initial.dependencies))
    package.write_text("def pytest_collection_modifyitems(items): pass\n")
    assert not gate.verify_row(tmp_path, row(path), sys.executable).ok
    assert calls == []


def test_historical_unbound_source_never_falls_back_to_current(tmp_path: Path) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("def test_case(): pass\n")
    assert gate.classify_test_admission(tmp_path, path, "HEAD").status == "UNKNOWN"
    assert gate.classify_test_admission(tmp_path, path, "0" * 40).status == "UNKNOWN"
    assert gate.classify_test_admission(tmp_path, path, expected_source_sha256="0" * 64).status == "UNKNOWN"


def test_live_admission_binds_config_and_lock(tmp_path: Path) -> None:
    path = "tests/unit/test_case.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("import pytest\npytestmark=pytest.mark.integration\ndef test_case(): pass\n")
    (tmp_path / "uv.lock").write_text("bound lock\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == "LIVE"
    assert {path, "uv.lock", "pyproject.toml"}.issubset(dict(admission.dependencies))


def write_admission_fixture(root: Path, files: dict[str, str]) -> str:
    path = "tests/unit/test_case.py"
    for name, source in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return path


@pytest.mark.parametrize("placement", ["test", "conftest", "package", "helper"])
@pytest.mark.parametrize("metadata", ["CASES", "ALIAS", "expanded", "factory_alias"])
@pytest.mark.parametrize("kind", ["offline", "live", "unknown"])
def test_fixture_parameter_authority_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placement: str, metadata: str, kind: str
) -> None:
    cases = {
        "offline": "CASES=[1, 2]\n",
        "live": "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.integration)]\n",
        "unknown": "import pytest\nCASES=[pytest.param(1, marks=choose())]\n",
    }[kind]
    fixture = "import pytest as p\nfrom reexport import CASES\nALIAS=CASES\n"
    fixture += {
        "CASES": "@p.fixture(params=CASES)\n",
        "ALIAS": "@p.yield_fixture(None, params=ALIAS)\n",
        "expanded": "OPTIONS={'params': CASES}\n@p.fixture(**OPTIONS)\n",
        "factory_alias": "decorator=p.fixture(params=CASES)\n@decorator\n",
    }[metadata]
    fixture += "def value(request): return request.param\n"
    path = "tests/unit/test_case.py"
    files = {
        path: "def test_case(value): pass\n",
        "parameters.py": cases,
        "reexport.py": "from parameters import CASES\n",
    }
    destination = {
        "test": path,
        "conftest": "conftest.py",
        "package": "tests/unit/__init__.py",
        "helper": "helper.py",
    }[placement]
    files[destination] = fixture + (files[path] if placement == "test" else "")
    if placement == "helper":
        files[path] = (
            "import pytest\n"
            "from helper import VALUE\n"
            "@pytest.mark.parametrize('x', VALUE)\n"
            "def test_case(x): pass\n"
        )
        files[destination] += "VALUE=[1]\n"
    write_admission_fixture(tmp_path, files)
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == {"offline": "OFFLINE", "live": "LIVE", "unknown": "UNKNOWN"}[kind], (
        admission.reason
    )
    for name in {path, destination, "parameters.py", "reexport.py"}:
        assert dict(admission.dependencies)[name] == hashlib.sha256(files[name].encode()).hexdigest()
    calls = install_capture(monkeypatch)
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED").ok is (kind == "offline")
    assert calls == ([path] if kind == "offline" else [])
    calls.clear()
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok is (kind != "unknown")
    assert calls == ([] if kind == "unknown" else [path])


@pytest.mark.parametrize(
    "decoration",
    [
        "@pytest.fixture(params=choose())",
        "@pytest.fixture(**choose())",
        "@pytest.fixture(**OPTIONS)",
        "@pytest.fixture(params=CASES)",
        "@pytest.fixture(None, 'function', CASES)",
        "@pytest.fixture(**{'params': [pytest.param(1, **OPTIONS)]})",
        "@pytest.fixture(**{name: [1]})",
        "@pytest.fixture(*ARGS, params=[1])",
        "@pytest.fixture(params=[1], **{'params': [2]})",
    ],
)
def test_unbound_fixture_metadata_refuses_even_live_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decoration: str
) -> None:
    path = write_admission_fixture(
        tmp_path,
        {
            "tests/unit/test_case.py": "import pytest\n"
            + decoration
            + "\ndef value(request): return request.param\ndef test_case(value): pass\n"
        },
    )
    calls = install_capture(monkeypatch)
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == "UNKNOWN", admission.reason
    assert not gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok
    assert calls == []


@pytest.mark.parametrize(
    "decoration",
    [
        "@pytest.fixture",
        "@pytest.fixture()",
        "@pytest.fixture(None, params=None)",
        "@pytest.yield_fixture(params=[1])",
        "@pytest.fixture(fixture_function=None, params=[1])",
        "@pytest.fixture(**{'scope':'module', **{'params':[1]}})",
    ],
)
def test_bound_fixture_factory_forms_retain_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decoration: str
) -> None:
    path = write_admission_fixture(
        tmp_path,
        {
            "tests/unit/test_case.py": "import pytest\n"
            + decoration
            + "\ndef value(request): return request.param\ndef test_case(value): pass\n"
        },
    )
    calls = install_capture(monkeypatch)
    result = gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED")
    assert result.ok, result.message
    assert calls == [path]


@pytest.mark.parametrize(
    "expression,method",
    [
        ("Value()+0", "__add__"),
        ("0+Value()", "__radd__"),
        ("-Value()", "__neg__"),
        ("+Value()", "__pos__"),
        ("~Value()", "__invert__"),
        ("Value()*2", "__mul__"),
        ("2*Value()", "__rmul__"),
        ("Value()-0", "__sub__"),
        ("1/Value()", "__rtruediv__"),
        ("Value()**2", "__pow__"),
        ("Value()|0", "__or__"),
        ("Value()@0", "__matmul__"),
        ("Value<<1", "__lshift__"),
    ],
)
@pytest.mark.parametrize("imported", [False, True])
def test_user_operator_dispatch_cannot_acquire_offline_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str, method: str, imported: bool
) -> None:
    declaration = (
        f"import pytest\n"
        f"class Value:\n"
        f"    def {method}(self, *args):\n"
        f"        return pytest.param(1, marks=pytest.mark.__getattr__('integration'))\n"
    )
    source = "import pytest\nfrom helper import Value\n" if imported else declaration
    source += f"@pytest.mark.parametrize('x', [{expression}])\ndef test_case(x): pass\n"
    files = {"tests/unit/test_case.py": source}
    if imported:
        files["helper.py"] = declaration
    path = write_admission_fixture(tmp_path, files)
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == "UNKNOWN", admission.reason
    if imported:
        assert dict(admission.dependencies)["helper.py"] == hashlib.sha256(declaration.encode()).hexdigest()
    calls = install_capture(monkeypatch)
    for allow_live in (False, True):
        assert not gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=allow_live).ok
    assert calls == []


@pytest.mark.parametrize(
    "expression",
    [
        "1+2",
        "1-2",
        "2*3",
        "1/2",
        "7//2",
        "7%2",
        "2**-1",
        "1<<2",
        "3&1",
        "-1",
        "+1.5",
        "~1",
        "-(1+2)",
        "1+2j",
        "'a'+'b'",
        "b'a'*2",
        "2*'a'",
        "[1]+[2]",
        "(1,)+(2,)",
        "[object()]*2",
        "NAMED+1",
        "int('2')+1",
        "not Value()",
        "(Value()+0,)",
    ],
)
def test_primitive_operator_and_tuple_value_neighbors_stay_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    source = (
        "import pytest\n"
        "from helper import NAMED\n"
        "class Value:\n"
        "    def __add__(self, other): return unknown()\n"
    )
    source += f"@pytest.mark.parametrize('x', [{expression}])\ndef test_case(x): pass\n"
    path = write_admission_fixture(tmp_path, {"tests/unit/test_case.py": source, "helper.py": "NAMED=2\n"})
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == "OFFLINE", admission.reason
    calls = install_capture(monkeypatch)
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED").ok
    assert calls == [path]


@pytest.mark.parametrize("historical_bad", [False, True])
@pytest.mark.parametrize("kind", ["fixture", "operator"])
def test_fixture_operator_historical_dependencies_use_exact_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historical_bad: bool, kind: str
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Admission repair")
    git("config", "user.email", "admission@example.invalid")
    source = "import pytest\nfrom helper import CASES\n"
    source += (
        "@pytest.fixture(params=CASES)\n"
        "def value(request): return request.param\n"
        "def test_case(value): pass\n"
        if kind == "fixture"
        else "@pytest.mark.parametrize('x', [CASES+0])\ndef test_case(x): pass\n"
    )
    good = "CASES=[1]\n" if kind == "fixture" else "CASES=1\n"
    bad = (
        "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.integration)]\n"
        if kind == "fixture"
        else (
            "import pytest\n"
            "class Value:\n"
            "    def __add__(self, other):\n"
            "        return pytest.param(1, marks=pytest.mark.__getattr__('integration'))\n"
            "CASES=Value()\n"
        )
    )
    path = write_admission_fixture(
        tmp_path,
        {
            "tests/unit/test_case.py": source,
            "helper.py": bad if historical_bad else good,
            "uv.lock": "locked\n",
        },
    )
    git("add", ".")
    git("commit", "-qm", "historical metadata")
    commit = git("rev-parse", "HEAD")
    (tmp_path / "helper.py").write_text(good if historical_bad else bad)
    git("add", ".")
    git("commit", "-qm", "opposite metadata")
    digest = hashlib.sha256(source.encode()).hexdigest()
    historical = gate.classify_test_admission(tmp_path, path, commit, expected_source_sha256=digest)
    current = gate.classify_test_admission(tmp_path, path, expected_source_sha256=digest)
    bad_status = "LIVE" if kind == "fixture" else "UNKNOWN"
    assert historical.status == (bad_status if historical_bad else "OFFLINE")
    assert current.status == ("OFFLINE" if historical_bad else bad_status)
    assert (
        dict(historical.dependencies)["helper.py"]
        == hashlib.sha256((bad if historical_bad else good).encode()).hexdigest()
    )
    assert (
        dict(current.dependencies)["helper.py"]
        == hashlib.sha256((good if historical_bad else bad).encode()).hexdigest()
    )
    calls = []

    def capture(*args: object, **kwargs: object) -> gate.PytestCapture:
        calls.append("historical")
        return gate.PytestCapture(True, f"{path}::test_case PASSED\n", 0, "synthetic only")

    monkeypatch.setattr(gate, "_capture_historical_recipe", capture)
    claim = gate.SupersessionClaim(
        "ADMISSION", commit, "a" * 64, digest, hashlib.sha256(b"locked\n").hexdigest()
    )
    edge = gate.SupersessionEdge(row(path), row(path), claim)
    assert gate.verify_historical_row(tmp_path, edge, "NEVER_EXECUTED").ok is not historical_bad
    assert calls == ([] if historical_bad else ["historical"])
    calls.clear()
    broken = gate.SupersessionClaim("ADMISSION", commit, "a" * 64, "0" * 64, claim.source_lock_sha256)
    assert not gate.verify_historical_row(
        tmp_path, gate.SupersessionEdge(row(path), row(path), broken), "NEVER_EXECUTED", allow_live=True
    ).ok
    assert calls == []


@pytest.mark.parametrize("kind", ["missing", "changed", "removed"])
def test_fixture_dependency_failure_then_fresh_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = write_admission_fixture(
        tmp_path,
        {
            "tests/unit/test_case.py": "import pytest\n"
            "from helper import CASES\n"
            "@pytest.fixture(params=CASES)\n"
            "def value(request): return request.param\n"
            "def test_case(value): pass\n"
        },
    )
    helper = tmp_path / "helper.py"
    if kind != "missing":
        helper.write_text("CASES=[1]\n")
    original = gate._read_admission_source
    reads = 0

    def drifting(root: Path, relative: str) -> bytes:
        nonlocal reads
        data = original(root, relative)
        if relative == "helper.py":
            reads += 1
            if reads == 1:
                if kind == "changed":
                    helper.write_text("CASES=[2]\n")
                elif kind == "removed":
                    helper.unlink()
        return data

    calls = install_capture(monkeypatch)
    monkeypatch.setattr(gate, "_read_admission_source", drifting)
    assert not gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok
    assert calls == []
    monkeypatch.setattr(gate, "_read_admission_source", original)
    helper.write_text("CASES=[1]\n")
    recovered = gate.classify_test_admission(tmp_path, path)
    assert recovered.status == "OFFLINE"
    assert dict(recovered.dependencies)["helper.py"] == hashlib.sha256(helper.read_bytes()).hexdigest()
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED").ok
    assert calls == [path]


@pytest.mark.parametrize("expression", ["str(Value())+'a'", "bytes(Value())+b'a'", "f'{Value()}'+ 'a'"])
def test_string_conversion_subclass_cannot_hide_operator_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    source = (
        "import pytest\n"
        "class Text(str):\n"
        "    def __add__(self, other):\n"
        "        return pytest.param(1, marks=pytest.mark.__getattr__('integration'))\n"
        "class Raw(bytes):\n"
        "    def __add__(self, other):\n"
        "        return pytest.param(1, marks=pytest.mark.__getattr__('integration'))\n"
        "class Value:\n"
        "    def __str__(self): return Text('x')\n"
        "    def __bytes__(self): return Raw(b'x')\n"
        "    def __format__(self, spec): return Text('x')\n"
        f"@pytest.mark.parametrize('x', [{expression}])\n"
        "def test_case(x): pass\n"
    )
    path = write_admission_fixture(tmp_path, {"tests/unit/test_case.py": source})
    calls = install_capture(monkeypatch)
    assert gate.classify_test_admission(tmp_path, path).status == "UNKNOWN"
    assert not gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok
    assert calls == []


@pytest.mark.parametrize("metadata", ["[1]", "[pytest.param(1, marks=choose())]"])
def test_imported_fixture_option_mapping_keeps_owner_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str
) -> None:
    files = {
        "tests/unit/test_case.py": (
            "import pytest\nfrom reexport import OPTIONS\n"
            "@pytest.fixture(**OPTIONS)\ndef value(request): return request.param\n"
            "def test_case(value): pass\n"
        ),
        "reexport.py": "from helper import OPTIONS\n",
        "helper.py": f"import pytest\nOPTIONS={{'params': {metadata}}}\n",
    }
    path = write_admission_fixture(tmp_path, files)
    admission = gate.classify_test_admission(tmp_path, path)
    assert admission.status == ("OFFLINE" if metadata == "[1]" else "UNKNOWN")
    for name, source in files.items():
        assert dict(admission.dependencies)[name] == hashlib.sha256(source.encode()).hexdigest()
    calls = install_capture(monkeypatch)
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok is (metadata == "[1]")
    assert calls == ([path] if metadata == "[1]" else [])


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_historical_fixture_dependency_failure_does_not_use_current_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Admission dependency")
    git("config", "user.email", "admission@example.invalid")
    source = (
        "import pytest\nfrom helper import CASES\n@pytest.fixture(params=CASES)\n"
        "def value(request): return request.param\ndef test_case(value): pass\n"
    )
    files = {"tests/unit/test_case.py": source, "uv.lock": "locked\n"}
    if failure == "changed":
        files["helper.py"] = "CASES=[1]\n"
    path = write_admission_fixture(tmp_path, files)
    git("add", ".")
    git("commit", "-qm", "historical dependency")
    commit = git("rev-parse", "HEAD")
    (tmp_path / "helper.py").write_text("CASES=[1]\n")
    original = gate._source_blob
    reads = 0

    def drifting(root: Path, source_commit: str, relative: str) -> bytes:
        nonlocal reads
        data = original(root, source_commit, relative)
        if relative == "helper.py":
            reads += 1
            if reads > 1:
                return b"CASES=[2]\n"
        return data

    monkeypatch.setattr(gate, "_source_blob", drifting)
    calls = []

    def capture(*args: object, **kwargs: object) -> gate.PytestCapture:
        calls.append("historical")
        return gate.PytestCapture(True, f"{path}::test_case PASSED\n", 0, "synthetic only")

    monkeypatch.setattr(gate, "_capture_historical_recipe", capture)
    claim = gate.SupersessionClaim(
        "ADMISSION",
        commit,
        "a" * 64,
        hashlib.sha256(source.encode()).hexdigest(),
        hashlib.sha256(b"locked\n").hexdigest(),
    )
    edge = gate.SupersessionEdge(row(path), row(path), claim)
    assert not gate.verify_historical_row(tmp_path, edge, "NEVER_EXECUTED", allow_live=True).ok
    assert calls == []
    monkeypatch.setattr(gate, "_source_blob", original)
    if failure == "changed":
        assert gate.verify_historical_row(tmp_path, edge, "NEVER_EXECUTED").ok
        assert calls == ["historical"]
    else:
        assert gate.classify_test_admission(tmp_path, path).status == "OFFLINE"
        assert not gate.verify_historical_row(tmp_path, edge, "NEVER_EXECUTED").ok
        assert calls == []


def test_fixture_operator_repair_retains_frozen_public_admission_api() -> None:
    import dataclasses
    import inspect

    from scripts.ci.pytest_metadata_admission import TestAdmission

    assert [field.name for field in dataclasses.fields(TestAdmission)] == [
        "status",
        "reason",
        "source_sha256",
        "dependencies",
    ]
    assert TestAdmission.__dataclass_params__.frozen
    signature = inspect.signature(gate.classify_test_admission)
    assert list(signature.parameters) == [
        "repo_root",
        "test_path",
        "source_commit",
        "expected_source_sha256",
    ]
    assert signature.parameters["source_commit"].default is None
    assert signature.parameters["expected_source_sha256"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("reexport", [False, True])
@pytest.mark.parametrize("placement", ["test", "conftest"])
@pytest.mark.parametrize("kind", ["offline", "live", "unknown", "missing"])
def test_imported_fixture_decorator_alias_retains_metadata_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reexport: bool, placement: str, kind: str
) -> None:
    path = "tests/unit/test_case.py"
    fixture = "from helper import DECORATOR\n@DECORATOR\ndef value(request): return request.param\n"
    files = {path: "def test_case(value): pass\n"}
    destination = path if placement == "test" else "conftest.py"
    files[destination] = fixture + (files[path] if placement == "test" else "")
    if kind != "missing":
        helper = "import pytest\nfrom parameters import CASES\nDECORATOR=pytest.fixture(params=CASES)\n"
        files["helper.py"] = "from reexport import DECORATOR\n" if reexport else helper
        if reexport:
            files["reexport.py"] = helper
        files["parameters.py"] = {
            "offline": "CASES=[1]\n",
            "live": "import pytest\nCASES=[pytest.param(1, marks=pytest.mark.integration)]\n",
            "unknown": "import pytest\nCASES=[pytest.param(1, marks=choose())]\n",
        }[kind]
    write_admission_fixture(tmp_path, files)
    admission = gate.classify_test_admission(tmp_path, path)
    assert (
        admission.status
        == {"offline": "OFFLINE", "live": "LIVE", "unknown": "UNKNOWN", "missing": "UNKNOWN"}[kind]
    ), admission.reason
    for name, source in files.items():
        assert dict(admission.dependencies)[name] == hashlib.sha256(source.encode()).hexdigest()
    calls = install_capture(monkeypatch)
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED").ok is (kind == "offline")
    assert calls == ([path] if kind == "offline" else [])
    calls.clear()
    assert gate.verify_row(tmp_path, row(path), "NEVER_EXECUTED", allow_live=True).ok is (
        kind in {"offline", "live"}
    )
    assert calls == ([path] if kind in {"offline", "live"} else [])
