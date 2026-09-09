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
