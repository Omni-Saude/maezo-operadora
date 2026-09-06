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
        adr_body="see `docs/adr/0018-...md:513` for the pattern shared across the listed ADRs.\n",
        py_body="def still_here():\n    pass\n",
    )
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.disclosed == [], result.render()
    assert result.line_citation_count == 1


def test_bare_line_citation_under_a_gitignored_prefix_is_excluded_by_rule(tmp_path: Path) -> None:
    """A citation under a directory this repo's OWN `.gitignore` excludes can never be confirmed
    via `git ls-files` — excluded structurally (`gitignored_directory_prefixes`), not allowlisted,
    since the gate cannot tell "gitignored but present on disk" from "genuinely gone" either way."""
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "prompts").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-fixture.md").write_text(
        "see `docs/prompts/SOME-PLAN.md:22` for the plan.\n", encoding="utf-8"
    )
    (root / ".gitignore").write_text("docs/prompts/\n", encoding="utf-8")
    _init_git_repo(root)
    _git_add_all(root)
    result = gate.run_gate(root, root / "docs" / "adr")
    assert result.ok, result.render()
    assert result.disclosed == [], result.render()
    assert result.line_citation_count == 1


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


def test_gitignored_directory_prefixes_and_membership(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "# comment, ignored\ndocs/prompts/\nnot-a-dir-pattern.txt\nbuild/\n", encoding="utf-8"
    )
    prefixes = gate.gitignored_directory_prefixes(tmp_path)
    assert prefixes == ("docs/prompts/", "build/")
    assert gate.is_under_gitignored_prefix("docs/prompts/PLAN.md", prefixes) is True
    assert gate.is_under_gitignored_prefix("docs/adr/0001.md", prefixes) is False


def test_gitignored_directory_prefixes_missing_gitignore_returns_empty(tmp_path: Path) -> None:
    assert gate.gitignored_directory_prefixes(tmp_path / "no-such-dir") == ()


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
    assert any(
        "never_cited_anywhere" in f and "stale allowlist rot" in f for f in result.failures
    ), result.render()
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
    monkeypatch.setattr(gate, "resolve_symbol", lambda *_args, **_kwargs: False)
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
