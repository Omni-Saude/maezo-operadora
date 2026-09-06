"""Unit tests for scripts/ci/check_doc_symbol_citations.py (R-089).

Belt-and-suspenders structure, same posture as the other `tests/unit/ci/test_check_*.py` fences:
synthetic fixtures prove the RED/GREEN mechanics in isolation (fast, no dependency on the real
`docs/adr/` corpus staying in any particular shape), and one test ties the gate to the REAL tree at
HEAD so a future citation regression is caught by CI, not just by this file's own fixtures.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.ci import check_doc_symbol_citations as gate

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _make_fixture_repo(tmp_path: Path, *, adr_body: str, py_body: str) -> Path:
    """A throwaway git repo with one ADR file citing into one Python module — the minimal shape
    `run_gate` needs (it only ever calls `git ls-files`, never touches history)."""
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "src" / "maezo" / "tools" / "workers").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text(adr_body, encoding="utf-8")
    (root / "src" / "maezo" / "tools" / "workers" / "widget.py").write_text(py_body, encoding="utf-8")
    _init_git_repo(root)
    _git_add_all(root)
    return root


# ===================================================================================================
# 1. RED on a citation to a missing file
# ===================================================================================================


def test_symbol_citation_to_a_missing_file_fails(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/nonexistent.py::whatever` as evidence.\n",
        py_body="def whatever():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok
    assert any("nonexistent.py" in f and "file not found" in f for f in result.failures)


# ===================================================================================================
# 2. RED on a missing symbol
# ===================================================================================================


def test_symbol_citation_to_a_missing_symbol_fails(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::gone_function` as evidence.\n",
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok
    assert any("gone_function" in f and "symbol not found" in f for f in result.failures)


def test_import_citation_to_a_missing_name_fails(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body=("example: `from maezo.tools.workers.widget import gone_name, still_here`\n"),
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok
    assert any("gone_name" in f and "name not found" in f for f in result.failures)


# ===================================================================================================
# 3. GREEN — resolvable citations of both hard-checked shapes
# ===================================================================================================


def test_symbol_and_import_citations_that_resolve_pass(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body=(
            "cites `src/maezo/tools/workers/widget.py::WidgetError` and "
            "`from maezo.tools.workers.widget import still_here, WidgetError`\n"
        ),
        py_body="class WidgetError(Exception):\n    pass\n\n\ndef still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.failures == []


def test_method_citation_resolves(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::Widget.run` as the entry point.\n",
        py_body="class Widget:\n    def run(self) -> None:\n        pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


# ===================================================================================================
# 4. Bare line citations are informational only — never a hard failure, even to a missing file
# ===================================================================================================


def test_bare_line_citation_to_a_missing_file_does_not_fail_the_gate(tmp_path: Path) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body="see `src/maezo/tools/workers/deleted_long_ago.py:42` for the old approach.\n",
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.line_citation_count == 1


# ===================================================================================================
# 5. Disclosed rot: reported, not blocking — and actively re-checked (no silent staleness)
# ===================================================================================================


def test_disclosed_rot_is_reported_but_does_not_fail_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::long_gone` — known, disclosed rot.\n",
        py_body="def still_here():\n    pass\n",
    )
    fake_entry = gate.DisclosedRot(
        doc="0001-fixture.md",
        citation_repr="src/maezo/tools/workers/widget.py::long_gone",
        reason="test fixture: deliberately unresolved and disclosed",
    )
    monkeypatch.setattr(gate, "_DISCLOSED_ROT", (fake_entry,))
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert any("long_gone" in d for d in result.disclosed)


def test_a_disclosed_rot_entry_that_now_resolves_fails_as_stale_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The allowlist-rot check: an entry claiming a citation is unresolved, when the citation now
    resolves cleanly, must fail loudly — the maintainer has to prune it, not let it silently mask a
    future regression under the same name."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::still_here` — this one is FIXED now.\n",
        py_body="def still_here():\n    pass\n",
    )
    fake_entry = gate.DisclosedRot(
        doc="0001-fixture.md",
        citation_repr="src/maezo/tools/workers/widget.py::still_here",
        reason="test fixture: stale entry — the citation was fixed and this allowlist row was not pruned",
    )
    monkeypatch.setattr(gate, "_DISCLOSED_ROT", (fake_entry,))
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok
    assert any("stale allowlist rot" in f for f in result.failures)


# ===================================================================================================
# 6. Mutation proof — a broken resolver must turn a passing citation RED
# ===================================================================================================


def test_mutation_disabling_symbol_resolution_turns_a_passing_citation_red(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Proves `test_symbol_and_import_citations_that_resolve_pass` is load-bearing: if
    `resolve_symbol` regressed to always-False (the mutation), the GREEN case above would go RED.
    """
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::WidgetError` as evidence.\n",
        py_body="class WidgetError(Exception):\n    pass\n",
    )
    monkeypatch.setattr(gate, "resolve_symbol", lambda *_args, **_kwargs: False)
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, "mutation (resolve_symbol always False) should have turned this RED"
    assert any("WidgetError" in f for f in result.failures)


# ===================================================================================================
# 7. The real corpus at HEAD — ties this gate to CI, not just to fixtures
# ===================================================================================================


def test_the_real_docs_adr_corpus_passes_with_exactly_the_two_known_disclosed_entries() -> None:
    result = gate.run_gate(_REPO_ROOT, _REPO_ROOT / "docs" / "adr")
    assert result.ok, result.render()
    assert len(result.disclosed) == 2, result.render()
    disclosed_docs = {d.split(":", 1)[0] for d in result.disclosed}
    assert disclosed_docs == {
        "0022-mcp-in-process-boot.md",
        "0026-worker-standardization.md",
    }, result.render()


# ===================================================================================================
# 8. Path/import resolution helpers, isolated
# ===================================================================================================


def test_resolve_path_candidates_matches_bare_basename_and_suffix() -> None:
    tracked = ["src/maezo/gateway/pep.py", "src/maezo/tools/workers/ans_cron.py"]
    assert gate.resolve_path_candidates("pep.py", tracked) == ["src/maezo/gateway/pep.py"]
    assert gate.resolve_path_candidates("gateway/pep.py", tracked) == ["src/maezo/gateway/pep.py"]
    assert gate.resolve_path_candidates("src/maezo/gateway/pep.py", tracked) == ["src/maezo/gateway/pep.py"]
    assert gate.resolve_path_candidates("does_not_exist.py", tracked) == []


def test_module_to_path_uses_the_src_package_root() -> None:
    assert gate.module_to_path("maezo.tools.workers.ans_cron") == "src/maezo/tools/workers/ans_cron.py"


def test_collect_python_symbols_covers_functions_classes_methods_and_imports() -> None:
    source = (
        "from maezo.x import reexported\n"
        "\n"
        "MODULE_CONST = 1\n"
        "\n"
        "\n"
        "def top_level():\n"
        "    pass\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    CLASS_CONST = 2\n"
        "\n"
        "    def method(self):\n"
        "        pass\n"
    )
    symbols = gate.collect_python_symbols(source)
    # The collector is deliberately permissive (module docstring: "a false positive... does no
    # harm here") — it also re-adds unqualified names while recursing into the class body. Assert
    # every symbol a citation could legitimately use is PRESENT; do not assert exact equality.
    assert symbols is not None
    assert {
        "reexported",
        "MODULE_CONST",
        "top_level",
        "Widget",
        "Widget.CLASS_CONST",
        "Widget.method",
    }.issubset(symbols)


def test_collect_python_symbols_returns_none_on_syntax_error() -> None:
    assert gate.collect_python_symbols("def broken(:\n") is None


# ===================================================================================================
# 9. Shallow-clone proof — the gate uses `git ls-files` only, never history
# ===================================================================================================


def test_runs_unchanged_in_a_shallow_clone(tmp_path: Path) -> None:
    """`git ls-files` needs no history — clone the real repo with `--depth 1` and run the gate's
    CLI entry point against it exactly as `make check-doc-symbol-citations` would, proving it does
    not shell out to anything (like `git merge-base origin/main`) that a shallow clone lacks."""
    shallow = tmp_path / "shallow-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--no-local", f"file://{_REPO_ROOT}", str(shallow)],
        check=True,
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        [sys.executable, "scripts/ci/check_doc_symbol_citations.py"],
        cwd=shallow,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout
