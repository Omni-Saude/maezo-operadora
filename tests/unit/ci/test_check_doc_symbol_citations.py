"""Unit tests for scripts/ci/check_doc_symbol_citations.py (R-089).

Belt-and-suspenders structure, same posture as the other `tests/unit/ci/test_check_*.py` fences:
synthetic fixtures prove the RED/GREEN mechanics in isolation (fast, no dependency on the real
`docs/adr/` corpus staying in any particular shape), and one test ties the gate to the REAL tree at
HEAD so a future citation regression is caught by CI, not just by this file's own fixtures.
"""

from __future__ import annotations

import ast
import shutil
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
    `run_gate` needs (it asks Git only about the current index/ignore rules, never history)."""
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "src" / "maezo" / "tools" / "workers").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text(adr_body, encoding="utf-8")
    (root / "src" / "maezo" / "tools" / "workers" / "widget.py").write_text(py_body, encoding="utf-8")
    _init_git_repo(root)
    _git_add_all(root)
    return root


def _make_custom_fixture_repo(tmp_path: Path, *, adr_body: str, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text(adr_body, encoding="utf-8")
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
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
# 4. Bare line citations: file existence IS hard-checked (reparo F4/VER-ADR-BATCH); the LINE
#    NUMBER stays informational only, never checked.
# ===================================================================================================


def test_bare_line_citation_to_a_missing_file_now_fails_the_gate(tmp_path: Path) -> None:
    """Reparo F4/VER-ADR-BATCH (2026-09-06): this used to be
    `test_bare_line_citation_to_a_missing_file_does_not_fail_the_gate` and asserted `result.ok` —
    the gate collected shape 3 but never hard-checked it at all. File existence is now checked
    (the line number itself is not — see the sibling test below for that half of the claim)."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body="see `src/maezo/tools/workers/deleted_long_ago.py:42` for the old approach.\n",
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert result.line_citation_count == 1, result.render()
    assert any("deleted_long_ago.py" in f and "file not found" in f for f in result.failures)


def test_bare_line_citation_with_a_drifted_line_number_still_passes(tmp_path: Path) -> None:
    """The half of shape 3 that stays deliberately unchecked: the FILE exists, so the citation
    passes, even though line 9999 obviously has nothing to do with a 2-line file — proving the
    reparo did not overreach into verifying the line number, only file existence."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body="see `src/maezo/tools/workers/widget.py:9999` for the old approach.\n",
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.line_citation_count == 1


def test_bare_line_citation_with_a_literal_ellipsis_path_is_excluded_by_rule(tmp_path: Path) -> None:
    """`docs/adr/0018-...md` (an elided placeholder in a summary table, per ADR-0040) is not a real
    path — excluded by the structural `is_ellipsis_artifact_path` rule, not by `_DISCLOSED_ROT`."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body=(
            "see `docs/adr/0018-...md:513` for the pattern and "
            "`src/maezo/tools/workers/widget.py:1` for a checked companion.\n"
        ),
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.disclosed == [], result.render()
    assert result.line_citation_count == 2


def test_bare_line_citation_under_a_gitignored_prefix_is_excluded_by_rule(tmp_path: Path) -> None:
    """A concrete missing docs path that Git reports ignored is separated from checked citations."""
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "prompts").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text(
        "see `docs/prompts/SOME-PLAN.md:22` for the plan and "
        "`docs/adr/0001-fixture.md:1` for this checked companion.\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("docs/prompts/\n", encoding="utf-8")
    _init_git_repo(root)
    _git_add_all(root)
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.disclosed == [], result.render()
    assert result.line_citation_count == 2


def test_disclosed_bare_line_citation_is_reported_but_does_not_fail_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shape 3 participates in the SAME `_DISCLOSED_ROT` mechanism as shapes 1-2 — a known,
    disclosed, genuinely-missing path is reported, not silently swallowed."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body="see `src/maezo/tools/workers/long_gone_module.py:1` for the old approach.\n",
        py_body="def still_here():\n    pass\n",
    )
    fake_entry = gate.DisclosedRot(
        doc="0001-fixture.md",
        citation_repr="src/maezo/tools/workers/long_gone_module.py",
        reason="test fixture: known, disclosed, genuinely-missing bare line citation",
    )
    monkeypatch.setattr(gate, "_DISCLOSED_ROT", (fake_entry,))
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert any("long_gone_module.py" in d for d in result.disclosed)


def test_is_ellipsis_artifact_path() -> None:
    assert gate.is_ellipsis_artifact_path("docs/adr/0018-...md") is True
    assert gate.is_ellipsis_artifact_path("src/maezo/tools/workers/widget.py") is False


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


def test_two_disclosed_rot_entries_on_the_same_doc_a_live_one_does_not_mask_a_stale_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reparo F3/VER-ADR-BATCH (2026-09-06, terceiro agente). Regression for the exact bug the
    verifier's M1 mutation exposed: the allowlist-rot self-audit used to ask "did ANY disclosed
    message mention this DOC'S FILENAME?" — a substring match on `entry.doc`, not on the specific
    `(doc, citation_repr)` pair that actually fired. Two `_DISCLOSED_ROT` entries naming the SAME
    doc covered for each other under that check: as long as ONE of them was genuinely disclosed
    this run, the doc's filename showed up in `disclosed`, and a SECOND, bogus entry on that same
    doc — one whose citation was never even present in the text, so it can never legitimately fire
    — passed silently instead of being flagged as stale allowlist rot.

    This fixture puts exactly that shape in front of the gate: one doc, one real unresolved
    citation (`long_gone`, genuinely disclosed) plus one `_DISCLOSED_ROT` entry for a citation that
    plain does not appear anywhere in the doc (`never_cited_anywhere` — cannot possibly have fired).
    Before the reparo this passed GREEN (2 disclosed, the bogus entry along for the ride). After
    the reparo it must fail specifically on the bogus entry, while the real disclosure stays
    reported (not double-penalised)."""
    root = _make_fixture_repo(
        tmp_path,
        adr_body="cites `src/maezo/tools/workers/widget.py::long_gone` — known, disclosed rot.\n",
        py_body="def still_here():\n    pass\n",
    )
    live_entry = gate.DisclosedRot(
        doc="0001-fixture.md",
        citation_repr="src/maezo/tools/workers/widget.py::long_gone",
        reason="test fixture: genuinely disclosed — this citation IS in the doc and unresolved",
    )
    stale_entry = gate.DisclosedRot(
        doc="0001-fixture.md",
        citation_repr="src/maezo/tools/workers/widget.py::never_cited_anywhere",
        reason="test fixture: BOGUS — this citation never appears in the doc, so it can never fire",
    )
    monkeypatch.setattr(gate, "_DISCLOSED_ROT", (live_entry, stale_entry))
    result = gate.run_gate(root, root / "docs" / "adr")

    assert not result.ok, (
        "the stale entry on an already-disclosed doc must fail the gate, not hide behind the "
        f"live entry's disclosure — got: {result.render()}"
    )
    assert any("never_cited_anywhere" in f and "stale allowlist rot" in f for f in result.failures), (
        result.render()
    )
    # The live entry's own disclosure must still be reported, undisturbed by the sibling failure.
    assert any("long_gone" in d for d in result.disclosed), result.render()
    assert not any("long_gone" in f for f in result.failures), result.render()


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
    monkeypatch.setattr(gate, "resolve_symbol_details", lambda *_args, **_kwargs: (False, []))
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, "mutation (resolve_symbol always False) should have turned this RED"
    assert any("WidgetError" in f for f in result.failures)


# ===================================================================================================
# 7. The real corpus at HEAD — ties this gate to CI, not just to fixtures
# ===================================================================================================


def test_the_real_docs_adr_corpus_passes_with_exactly_the_seven_known_disclosed_entries() -> None:
    """R-089's own two stale citations (0026:72, 0028:192) were FIXED FORWARD by reparo
    F2/VER-ADR-BATCH (2026-09-06) instead of staying allowlisted. Reparo F4/VER-ADR-BATCH
    (2026-09-06) then added a file-existence check for bare `path:line` citations, surfacing 6
    more (5 distinct paths) alongside the pre-existing, out-of-scope ADR-0022 rot — 7 total."""
    result = gate.run_gate(_REPO_ROOT, _REPO_ROOT / "docs" / "adr")
    assert result.ok, result.render()
    assert len(result.disclosed) == 7, result.render()
    disclosed_docs = {d.split(":", 1)[0] for d in result.disclosed}
    assert disclosed_docs == {
        "0022-mcp-in-process-boot.md",
        "0024-durable-idempotency-resume-inbound-drivers.md",
        "0025-pep-policy-unification.md",
        "0037-amh-compatibility-boundary-canonical-contracts.md",
        "0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md",
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
    # Every supported module/class binding is present; lexical-scope negatives are pinned below.
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


# ===================================================================================================
# 10. ADR-VERIFY-01 — exact module/path binding with explicit suffix and basename forms
# ===================================================================================================


@pytest.mark.parametrize(
    "citation",
    [
        "`src/maezo/wrong/widget.py::kept`",
        "`from maezo.wrong.widget import kept`",
        "`src/maezo/wrong/widget.py:9`",
    ],
)
def test_qualified_wrong_directory_never_falls_back_to_same_basename(tmp_path: Path, citation: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=citation,
        files={"src/maezo/right/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("wrong/widget.py" in failure for failure in result.failures)


def test_exact_suffix_and_explicit_basename_path_forms_resolve(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=("`src/maezo/gateway/widget.py::kept` `gateway/widget.py::kept` `widget.py::kept`"),
        files={"src/maezo/gateway/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert gate.resolve_path_candidates("src/maezo/wrong/widget.py", ["src/maezo/right/widget.py"]) == []


@pytest.mark.parametrize(
    "citation",
    ["`src/maezo/widget.py::kept`", "`src/maezo/widget.py:1`"],
)
def test_explicit_repository_root_never_resolves_to_an_archived_suffix(tmp_path: Path, citation: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=citation,
        files={"archive/src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("file not found" in failure for failure in result.failures)


def test_non_rooted_suffix_abbreviation_remains_deliberately_supported(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`gateway/widget.py::kept`",
        files={"src/maezo/gateway/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


def test_package_init_is_a_valid_exact_module_binding(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`from maezo.widget import kept`",
        files={"src/maezo/widget/__init__.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


def test_same_name_package_takes_precedence_over_module_file(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`from maezo.widget import gone`",
        files={
            "src/maezo/__init__.py": "",
            "src/maezo/widget.py": "gone = 1\n",
            "src/maezo/widget/__init__.py": "kept = 1\n",
        },
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src'); from maezo.widget import gone",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "ImportError" in proc.stderr

    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("gone" in failure and "name not found" in failure for failure in result.failures)


def test_module_file_is_selected_when_same_name_package_is_absent(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`from maezo.widget import kept`",
        files={"src/maezo/widget.py": "kept = 1\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


# ===================================================================================================
# 11. ADR-VERIFY-02 — lexical Python bindings and static aliases/reexports
# ===================================================================================================


@pytest.mark.parametrize(
    ("citation", "source"),
    [
        ("`from maezo.widget import gone`", "def wrapper():\n    gone = 1\n"),
        ("`src/maezo/widget.py::gone`", "def wrapper():\n    def gone():\n        pass\n"),
        ("`from maezo.widget import gone`", "class Kept:\n    def gone(self):\n        pass\n"),
    ],
)
def test_function_and_class_local_names_do_not_become_module_bindings(
    tmp_path: Path, citation: str, source: str
) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=citation,
        files={"src/maezo/widget.py": source},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()


def test_class_qualified_members_and_static_reexport_aliases_resolve(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=("`src/maezo/widget.py::Kept.gone` `from maezo.widget import public_name, imported_module`"),
        files={
            "src/maezo/widget.py": (
                "from maezo.source import original as public_name\n"
                "import maezo.other as imported_module\n\n"
                "class Kept:\n"
                "    def gone(self):\n"
                "        pass\n"
            )
        },
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


def test_python_symbol_collection_is_exact_about_lexical_scope() -> None:
    symbols = gate.collect_python_symbols(
        "TOP = 1\n"
        "def wrapper():\n"
        "    local = 2\n"
        "    def nested():\n"
        "        pass\n"
        "class Kept:\n"
        "    CLASS = 3\n"
        "    def method(self):\n"
        "        pass\n"
    )
    assert symbols == {"TOP", "wrapper", "Kept", "Kept.CLASS", "Kept.method"}


# ===================================================================================================
# 12. ADR-VERIFY-03 — AST parsing for quoted ImportFrom examples
# ===================================================================================================


def test_parenthesized_multiline_import_and_aliases_are_parsed(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=("```python\nfrom maezo.widget import (\n    kept as local_name,\n    also_kept,\n)\n```\n"),
        files={"src/maezo/widget.py": "def kept():\n    pass\n\ndef also_kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


@pytest.mark.parametrize(
    "quoted_import",
    [
        "`from maezo.widget import check_calendar, trigger_submissions`",
        ("```python\nfrom maezo.widget import (\n    check_calendar,\n    trigger_submissions,\n)\n```"),
    ],
)
def test_original_dead_check_calendar_import_fails_in_plain_and_multiline_forms(
    tmp_path: Path, quoted_import: str
) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=quoted_import,
        files={"src/maezo/widget.py": "def trigger_submissions():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("check_calendar" in failure and "name not found" in failure for failure in result.failures)


@pytest.mark.parametrize("fence", ["~~~", "````", "   ```"])
def test_standard_markdown_fences_cannot_hide_a_dead_import(tmp_path: Path, fence: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=(f"{fence}python\nfrom maezo.widget import gone\n{fence}\n`src/maezo/widget.py::kept`"),
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("gone" in failure and "name not found" in failure for failure in result.failures)


@pytest.mark.parametrize("fence", ["~~~", "````", "   ```"])
def test_standard_markdown_fences_keep_valid_imports_checked(tmp_path: Path, fence: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=f"{fence}python\nfrom maezo.widget import kept\n{fence}\n",
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


@pytest.mark.parametrize(
    "adr_body",
    [
        "````python\n```\nfrom maezo.widget import gone\n````\n",
        "~~~python\n```\nfrom maezo.widget import gone\n~~~~\n",
        "```python\nfrom maezo.widget import gone\n",
        "`````from maezo.widget import gone`````",
    ],
)
def test_fence_delimiters_and_code_spans_follow_the_documented_finite_grammar(
    tmp_path: Path, adr_body: str
) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=adr_body + "\n`src/maezo/widget.py::kept`",
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("gone" in failure and "name not found" in failure for failure in result.failures)


@pytest.mark.parametrize(
    "adr_body",
    [
        "    from maezo.widget import kept\n",
        "> ~~~python\n> from maezo.widget import kept\n> ~~~\n",
    ],
)
def test_unsupported_markdown_code_forms_with_imports_fail_explicitly(tmp_path: Path, adr_body: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=adr_body + "\n`src/maezo/widget.py::kept`",
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("unsupported Markdown" in failure for failure in result.failures)


def test_indentation_inside_a_supported_root_fence_is_not_refused(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="```python\n    from maezo.widget import kept\n```\n",
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


@pytest.mark.parametrize("fence", ["~~~", "````", "   ```"])
def test_real_adr_corpus_standard_fences_cannot_hide_original_dead_import(tmp_path: Path, fence: str) -> None:
    adr_dir = tmp_path / "adr"
    shutil.copytree(_REPO_ROOT / "docs" / "adr", adr_dir)
    adr = adr_dir / "0026-worker-standardization.md"
    original = adr.read_text(encoding="utf-8")
    live = "`from maezo.tools.workers.ans_cron import trigger_submissions`"
    replacement = (
        f"\n{fence}python\n"
        "from maezo.tools.workers.ans_cron import check_calendar, trigger_submissions\n"
        f"{fence}\n"
    )
    assert original.count(live) == 1
    adr.write_text(original.replace(live, replacement), encoding="utf-8")

    result = gate.run_gate(_REPO_ROOT, adr_dir)
    assert not result.ok, result.render()
    assert any("check_calendar" in failure and "name not found" in failure for failure in result.failures)


def test_unparseable_quoted_import_is_an_explicit_failure(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=(
            "`from maezo.widget import (kept` and `src/maezo/widget.py::kept` as a non-vacuous companion"
        ),
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("unsupported or unparseable ImportFrom syntax" in failure for failure in result.failures)


def test_abbreviated_ellipsis_import_is_reported_separately(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=("`from maezo.widget import (..., kept, ...)` and `src/maezo/widget.py::kept`"),
        files={"src/maezo/widget.py": "def kept():\n    pass\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert len(result.excluded) == 1
    assert "literal `...`" in result.excluded[0]


# ===================================================================================================
# 13. ADR-VERIFY-04 — BPMN/DMN symbols are declared XML IDs
# ===================================================================================================


def test_declared_bpmn_and_dmn_ids_resolve(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`spec/process.bpmn::BRT_Kept` `spec/decision.dmn::Decision_Kept`",
        files={
            "spec/process.bpmn": (
                '<definitions xmlns="urn:bpmn"><businessRuleTask id="BRT_Kept"/></definitions>'
            ),
            "spec/decision.dmn": '<definitions xmlns="urn:dmn"><decision id="Decision_Kept"/></definitions>',
        },
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()


@pytest.mark.parametrize(
    "xml",
    [
        '<definitions><!-- deleted BRT_Gone --><task id="replacement"/></definitions>',
        '<definitions><sequenceFlow id="flow" sourceRef="BRT_Gone"/></definitions>',
    ],
)
def test_bpmn_comments_and_reference_attributes_do_not_declare_symbols(tmp_path: Path, xml: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`spec/process.bpmn::BRT_Gone`",
        files={"spec/process.bpmn": xml},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()


def test_malformed_xml_and_unsupported_structured_extension_fail_honestly(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`spec/process.bpmn::BRT_Gone` `config/policy.yaml::rule_id`",
        files={
            "spec/process.bpmn": '<definitions><task id="BRT_Gone"></definitions>',
            "config/policy.yaml": "rule_id: present_only_as_yaml_text\n",
        },
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("malformed XML" in failure for failure in result.failures)
    assert any("unsupported structured-symbol extension" in failure for failure in result.failures)


# ===================================================================================================
# 14. ADR-VERIFY-05, ignore boundaries and non-vacuity
# ===================================================================================================


@pytest.mark.parametrize(
    "adr_body",
    [
        "<!-- anchor: src/maezo/gone.py -->\n`src/maezo/gone.py:4`",
        "`src/maezo/gone.py:4`\n\nunrelated\n\n<!-- anchor: src/maezo/gone.py -->",
    ],
)
def test_anchor_comments_never_suppress_missing_file_checks(tmp_path: Path, adr_body: str) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body=adr_body,
        files={"src/maezo/widget.py": "x = 1\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("gone.py" in failure and "file not found" in failure for failure in result.failures)


def test_anchor_comment_with_existing_file_passes(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="<!-- anchor: src/maezo/widget.py -->\n`src/maezo/widget.py:999`",
        files={"src/maezo/widget.py": "x = 1\n"},
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.line_citation_count == 1


def test_ignored_source_prefix_cannot_hide_a_deleted_tracked_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "src" / "maezo").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text("`src/maezo/widget.py:9`", encoding="utf-8")
    (root / "src" / "maezo" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("src/maezo/\n", encoding="utf-8")
    _init_git_repo(root)
    subprocess.run(["git", "add", "-f", "."], cwd=root, check=True)
    subprocess.run(["git", "rm", "-f", "src/maezo/widget.py"], cwd=root, check=True)
    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert result.ignored == []


def test_bare_tracked_file_deleted_only_from_worktree_fails(tmp_path: Path) -> None:
    root = _make_custom_fixture_repo(
        tmp_path,
        adr_body="`src/maezo/widget.py:1`",
        files={"src/maezo/widget.py": "kept = 1\n"},
    )
    (root / "src" / "maezo" / "widget.py").unlink()

    result = gate.run_gate(root, root / "docs" / "adr")
    assert not result.ok, result.render()
    assert any("missing from worktree" in failure for failure in result.failures)


def test_empty_or_exclusion_only_corpus_never_passes(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "docs" / "adr").mkdir(parents=True)
    _init_git_repo(empty)
    empty_result = gate.run_gate(empty, empty / "docs" / "adr")
    assert not empty_result.ok
    assert any("EMPTY" in failure for failure in empty_result.failures)

    excluded = tmp_path / "excluded"
    (excluded / "docs" / "adr").mkdir(parents=True)
    (excluded / "docs" / "adr" / "0001.md").write_text("`docs/adr/0018-...md:1`", encoding="utf-8")
    _init_git_repo(excluded)
    _git_add_all(excluded)
    excluded_result = gate.run_gate(excluded, excluded / "docs" / "adr")
    assert not excluded_result.ok
    assert any("UNCHECKED" in failure for failure in excluded_result.failures)


# ===================================================================================================
# 15. Finite exceptions and five load-bearing repair controls
# ===================================================================================================


@pytest.mark.parametrize("removed_index", range(7))
def test_each_exact_disclosure_is_load_bearing(monkeypatch: pytest.MonkeyPatch, removed_index: int) -> None:
    frozen = gate._DISCLOSED_ROT
    assert len(frozen) == 7
    monkeypatch.setattr(
        gate,
        "_DISCLOSED_ROT",
        tuple(entry for index, entry in enumerate(frozen) if index != removed_index),
    )
    result = gate.run_gate(_REPO_ROOT, _REPO_ROOT / "docs" / "adr")
    assert not result.ok, "removing any exact disclosure must make its unresolved citation blocking"


def test_disclosure_breakdown_and_bounded_historical_exclusions_are_exact() -> None:
    assert len(gate._DISCLOSED_ROT) == 7
    assert gate._DISCLOSED_ROT[0].citation_repr.endswith("tool_wiring.py::build_tool_invoker")
    assert sum("inbound_driver.py" in entry.citation_repr for entry in gate._DISCLOSED_ROT) == 3
    assert sum(entry.citation_repr == "a2a_assembly.py" for entry in gate._DISCLOSED_ROT) == 1
    assert {entry.citation_repr for entry in gate._DISCLOSED_ROT[-2:]} == {
        "schemas-validate.yml",
        "test_autonomy.py",
    }
    assert len(gate._HISTORICAL_LINE_EXCLUSIONS) == 3


@pytest.mark.parametrize("removed_index", range(3))
def test_each_exact_historical_exclusion_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch, removed_index: int
) -> None:
    frozen = gate._HISTORICAL_LINE_EXCLUSIONS
    monkeypatch.setattr(
        gate,
        "_HISTORICAL_LINE_EXCLUSIONS",
        tuple(entry for index, entry in enumerate(frozen) if index != removed_index),
    )
    result = gate.run_gate(_REPO_ROOT, _REPO_ROOT / "docs" / "adr")
    assert not result.ok, "removing any exact historical exclusion must expose its missing path"
    assert any(frozen[removed_index].citation_repr in failure for failure in result.failures)


def test_real_corpus_reports_nonvacuity_and_all_boundary_counts() -> None:
    result = gate.run_gate(_REPO_ROOT, _REPO_ROOT / "docs" / "adr")
    assert result.ok, result.render()
    assert result.document_count == 51
    assert result.eligible_citation_count > 0
    assert len(result.disclosed) == 7
    assert len(result.ignored) == 2
    assert len(result.excluded) == 3
    assert len(result.historical) == 3


def test_five_old_false_green_mutants_are_observable() -> None:
    tracked = ["src/maezo/right/widget.py"]
    assert gate.resolve_path_candidates("src/maezo/wrong/widget.py", tracked) == []
    assert [path for path in tracked if Path(path).name == "widget.py"] == tracked

    source = "def wrapper():\n    gone = 1\n"
    assert "gone" not in (gate.collect_python_symbols(source) or set())
    assert any(isinstance(node, ast.Name) and node.id == "gone" for node in ast.walk(ast.parse(source)))

    citations, issues, exclusions = gate.inspect_import_citations(
        "0001.md", "```python\nfrom maezo.widget import (kept, gone)\n```"
    )
    assert [citation.names for citation in citations] == [("kept", "gone")]
    assert issues == [] and exclusions == []

    assert gate.collect_declared_xml_ids(
        '<definitions><!-- BRT_Gone --><task id="kept" ref="BRT_Gone"/></definitions>'
    ) == {"kept"}
    assert gate.extract_line_citations("<!-- anchor: src/maezo/gone.py -->\n`src/maezo/gone.py:4`") == [
        ("src/maezo/gone.py", 2)
    ]
