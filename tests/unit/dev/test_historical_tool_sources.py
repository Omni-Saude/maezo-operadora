"""Offline source provenance and inert-tool/owned-execution lifetime fences."""

from __future__ import annotations

import ast
import gc
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "tool_sources_test", ROOT / "scripts/dev/historical_tool_sources.py"
)
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


def git(repo, *args):
    return (
        subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=repo
        )
        .decode()
        .strip()
    )


@pytest.fixture(scope="module")
def originals():
    capsule = s.ToolSourceCapsule(ROOT)
    return {relative: (capsule.root / relative).read_bytes() for relative in s.TOOL_SOURCES}


@pytest.fixture
def repository(tmp_path, monkeypatch, originals):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    for relative, data in originals.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed tools")
    commit = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(s, "TOOL_SOURCES", {p: (commit, v[1]) for p, v in s.TOOL_SOURCES.items()})
    return repo


def repin_commits(repo, monkeypatch, commit=None):
    commit = commit or git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(s, "TOOL_SOURCES", {p: (commit, v[1]) for p, v in s.TOOL_SOURCES.items()})


def test_original_closure_matches_operational_capsule_exactly():
    values = {}
    for node in ast.parse((ROOT / "scripts/ci/ledger_history_proofs.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in {"BASE", "PRODUCER", "TOOL_SOURCES"}:
                values[key] = eval(
                    compile(ast.Expression(node.value), "literal-source-pins", "eval"), {}, values
                )
    assert values["TOOL_SOURCES"] == s.TOOL_SOURCES


def test_frozen_helper_and_dependencies_have_real_byteexact_filenames(repository, originals):
    capsule = s.ToolSourceCapsule(repository)
    h = capsule.load("scripts/dev/run_historical_unit_recipe.py", "frozen_helper_fence")
    for module, relative in [
        (h, "scripts/dev/run_historical_unit_recipe.py"),
        (h.runner, "scripts/dev/run_engine_integration.py"),
        (h.checker, "scripts/ci/check_evidence_ledger_hashes.py"),
    ]:
        assert Path(module.__file__) == capsule.root / relative
        assert Path(module.__file__).read_bytes() == originals[relative]
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == s.TOOL_SOURCES[relative][1]
    assert {
        p: v[1] for p, v in s.TOOL_SOURCES.items() if p != "scripts/dev/run_historical_unit_recipe.py"
    } == h.PINS


def test_current_working_files_never_substitute_approved_git_bytes(repository):
    for relative in s.TOOL_SOURCES:
        (repository / relative).write_text("raise RuntimeError('unreviewed current source')\n")
    capsule = s.ToolSourceCapsule(repository)
    h = capsule.load("scripts/dev/run_historical_unit_recipe.py", "current_substitution_fence")
    assert h.runner is not None


@pytest.mark.parametrize("mutation", ["nonancestor", "unavailable", "corrupt", "missing", "symlink"])
def test_git_sources_fail_closed(repository, monkeypatch, mutation):
    relative = "scripts/dev/run_engine_integration.py"
    path = repository / relative
    if mutation == "nonancestor":
        # The detached commit has identical reviewed bytes but is not in HEAD's ancestry.
        commit = git(repository, "commit-tree", "HEAD^{tree}", "-m", "disconnected reviewed bytes")
        repin_commits(repository, monkeypatch, commit)
    elif mutation == "unavailable":
        repin_commits(repository, monkeypatch, "f" * 40)
    else:
        path.unlink()
        if mutation == "corrupt":
            path.write_text("raise RuntimeError('unreviewed replacement')\n")
        elif mutation == "symlink":
            path.symlink_to("run_historical_unit_recipe.py")
        git(repository, "add", "-A")
        git(repository, "commit", "-qm", mutation)
        repin_commits(repository, monkeypatch)
    with pytest.raises(s.ToolSourceRefusedError):
        s.ToolSourceCapsule(repository)


@pytest.mark.parametrize("mutation", ["missing", "corrupt", "symlink", "fifo", "directory"])
def test_dependency_substitution_before_import_is_refused(repository, mutation):
    import os

    capsule = s.ToolSourceCapsule(repository)
    path = capsule.root / "scripts/dev/run_engine_integration.py"
    path.unlink()
    if mutation == "corrupt":
        path.write_text("raise RuntimeError('must never execute')\n")
    elif mutation == "symlink":
        path.symlink_to(repository / "scripts/dev/run_engine_integration.py")
    elif mutation == "fifo":
        os.mkfifo(path)
    elif mutation == "directory":
        path.mkdir()
    with pytest.raises(s.ToolSourceRefusedError):
        capsule.load("scripts/dev/run_historical_unit_recipe.py", "must_never_import")
    assert "must_never_import" not in sys.modules


def test_unapproved_relative_has_no_generic_source_escape(repository):
    capsule = s.ToolSourceCapsule(repository)
    with pytest.raises(s.ToolSourceRefusedError, match="unapproved"):
        capsule.load("../unreviewed.py", "unreviewed")


def test_descendant_module_retains_inert_sources_after_parent_gc(repository):
    capsule = s.ToolSourceCapsule(repository)
    h = capsule.load("scripts/dev/run_historical_unit_recipe.py", "gc_parent_helper")
    runner = h.runner
    path = Path(runner.__file__)
    sys.modules.pop("gc_parent_helper")
    del h, capsule
    gc.collect()
    assert path.is_file()
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == s.TOOL_SOURCES["scripts/dev/run_engine_integration.py"][1]
    )


def test_inert_capsule_finalization_does_not_dispose_owned_execution(repository, tmp_path):
    # Tool capsule API cannot accept an execution cleanup path. Only its private,
    # internally minted TemporaryDirectory is eligible for implicit collection.
    capsule = s.ToolSourceCapsule(repository)
    inert_path = capsule.root
    execution = tmp_path / "owned-execution-source"
    execution.mkdir()
    (execution / "docker-compose.yml").write_text("retained execution fixture\n")
    del capsule
    gc.collect()
    assert not inert_path.exists()
    assert (execution / "docker-compose.yml").read_text() == "retained execution fixture\n"


@pytest.mark.parametrize("script", ["unit", "migration"])
def test_isolated_cli_help_runs_without_loading_current_canonical_sources(script):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / f"scripts/dev/run_historical_{script}_recipe.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--original-packet" in result.stdout


@pytest.mark.parametrize("mutation", ["replace", "graft", "shallow"])
def test_noncanonical_ancestry_metadata_refused(repository, mutation):
    commit = git(repository, "rev-parse", "HEAD")
    if mutation == "replace":
        other = git(repository, "commit-tree", "HEAD^{tree}", "-m", "replacement")
        git(repository, "replace", commit, other)
    elif mutation == "graft":
        (repository / ".git/info/grafts").write_text(commit + "\n")
    else:
        (repository / ".git/shallow").write_text(commit + "\n")
    with pytest.raises(s.ToolSourceRefusedError, match="ancestry"):
        s.ToolSourceCapsule(repository)


@pytest.mark.parametrize("kind", ["unit", "migration"])
@pytest.mark.parametrize("mutation", ["corrupt", "missing", "symlink", "fifo"])
def test_loader_itself_cannot_be_substituted_before_ancestry(tmp_path, kind, mutation):
    scripts = tmp_path / "scripts/dev"
    scripts.mkdir(parents=True)
    entrypoint = scripts / f"run_historical_{kind}_recipe.py"
    entrypoint.write_bytes((ROOT / entrypoint.relative_to(tmp_path)).read_bytes())
    source = scripts / "historical_tool_sources.py"
    if mutation == "corrupt":
        source.write_text("raise RuntimeError('UNREVIEWED_LOADER_EXECUTED')\n")
    elif mutation == "symlink":
        source.symlink_to(ROOT / "scripts/dev/historical_tool_sources.py")
    elif mutation == "fifo":
        import os

        os.mkfifo(source)
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(entrypoint), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "UNREVIEWED_LOADER_EXECUTED" not in result.stderr
    assert "usage:" not in result.stdout
    assert any(
        error in result.stderr
        for error in ("loader pin mismatch", "FileNotFoundError", "OSError", "nonregular")
    )


def test_read_access_time_does_not_count_as_source_mutation(repository):
    import os

    capsule = s.ToolSourceCapsule(repository)
    relative = "scripts/dev/run_engine_integration.py"
    path = capsule.root / relative
    os.utime(path, ns=(1, path.stat().st_mtime_ns))
    assert hashlib.sha256(capsule._read(relative)).hexdigest() == s.TOOL_SOURCES[relative][1]
