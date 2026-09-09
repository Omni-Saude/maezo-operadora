"""ICV2-R1/R2/R3 synthetic boundaries; never grant operational v2 credit."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_hashes as v1
from scripts.ci import ledger_invalid_declarations as invalid

from tests.unit.ci.test_ledger_invalid_declarations import (
    CURRENT_HASH,
    TEST,
    Fixture,
    git,
    row,
)
from tests.unit.ci.test_ledger_invalid_declarations import (
    history as history,
)


@pytest.mark.parametrize("path", [".python-version", "config/review.yaml", "pyproject.toml"])
@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
def test_config_only_diff_closes_relation_without_execution(
    history: Fixture, path: str, operation: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if operation != "add":
        history.write(path, b"# first config\n")
        git(history.root, "add", ".")
        git(history.root, "commit", "-qm", "initial config")
    base = git(history.root, "rev-parse", "HEAD")
    if operation == "delete":
        (history.root / path).unlink()
    else:
        history.write(path, b"# changed config\n")
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "config-only change")
    plan = history.plan()
    results = invalid.selected_unresolved(history.root, plan, [], base)
    assert len(results) == 1
    assert results[0].status == "UNRESOLVED"
    assert results[0].historical_claim_verified is False
    assert v1.main(["--base", base], repo_root=history.root) == 1
    output = capsys.readouterr().out
    assert "0 historical verified" in output and "0 executions" in output


@pytest.mark.parametrize("contradictory", [False, True])
def test_cited_prior_metadata_equals_row_and_evolved_current(history: Fixture, contradictory: bool) -> None:
    prior_row = row("PRIOR", CURRENT_HASH)
    history.publish(extra=prior_row + "\n")
    prior = copy.deepcopy(history.record["target"])
    prior.update(
        task_id="PRIOR",
        row_sha256=invalid.digest(prior_row.encode()),
        declared_recipe_sha256=CURRENT_HASH,
        source_commit=git(history.root, "rev-parse", "HEAD"),
        ledger_sha256=invalid.digest((history.root / "docs/evidence-ledger.md").read_bytes()),
        test_path="tests/unit/test_unrelated.py" if contradictory else TEST,
        test_sha256=invalid.digest((history.root / TEST).read_bytes()),
    )
    history.record["current"]["prior_correction"] = [prior]
    history.record["current"]["result_count"] = 3
    history.write(TEST, b"def test_evolved():\n    assert 2 == 2\n")
    history.publish(extra=prior_row + "\n")
    if contradictory:
        with pytest.raises(invalid.InvalidDeclarationError, match="IC_R1_CITED_ROW_PATH_MISMATCH"):
            history.plan()
    else:
        plan = history.plan()
        result = invalid.selected_unresolved(history.root, plan, [plan.relations[0].correction], None)
        assert len(result) == 1 and result[0].status == "UNRESOLVED"


def test_regular_current_file_control(history: Fixture) -> None:
    expected = (history.root / TEST).read_bytes()
    assert invalid.FrozenGit(history.root).current(TEST, limit=len(expected)) == expected


def test_frozen_resolve_boundary_never_reads_external(
    history: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve the reviewer's real swap timing and read instrumentation."""
    frozen = invalid.FrozenGit(history.root)
    target = history.root / TEST
    expected = target.read_bytes()
    outside = tmp_path / "owned-external-sentinel"
    outside.write_bytes(expected)
    resolve, read = Path.resolve, Path.read_bytes
    reads: list[Path] = []

    def swap(path: Path, *args: Any, **kwargs: Any) -> Path:
        result = resolve(path, *args, **kwargs)
        if path == target and not path.is_symlink():
            target.unlink()
            target.symlink_to(outside)
        return result

    def readback(path: Path) -> bytes:
        if path == target and path.is_symlink():
            reads.append(outside)
            data = read(path)
            target.unlink()
            target.write_bytes(expected)
            return data
        return read(path)

    monkeypatch.setattr(Path, "resolve", swap)
    monkeypatch.setattr(Path, "read_bytes", readback)
    try:
        assert frozen.current(TEST) == expected
        assert not reads
    finally:
        if target.is_symlink():
            target.unlink()
            target.write_bytes(expected)
    assert git(history.root, "status", "--porcelain") == ""


@pytest.mark.parametrize("replacement", ["symlink", "fifo", "oversized", "hardlink"])
def test_open_boundary_substitution_refused_before_read(
    history: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replacement: str
) -> None:
    frozen = invalid.FrozenGit(history.root)
    target = history.root / TEST
    expected = target.read_bytes()
    outside = tmp_path / "owned-external-sentinel"
    outside.write_bytes(expected)
    original_open, original_read = os.open, os.read
    swapped = False
    forbidden_reads: list[int] = []
    opened: set[int] = set()

    def swap(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if str(path) == target.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            target.unlink()
            if replacement == "symlink":
                target.symlink_to(outside)
            elif replacement == "fifo":
                os.mkfifo(target)
            elif replacement == "hardlink":
                os.link(outside, target)
            else:
                target.write_bytes(b"x" * (len(expected) + 1))
        fd = original_open(path, flags, *args, **kwargs)
        if str(path) == target.name and kwargs.get("dir_fd") is not None:
            opened.add(fd)
        return fd

    def tracked_read(fd: int, size: int) -> bytes:
        if fd in opened:
            forbidden_reads.append(fd)
        return original_read(fd, size)

    monkeypatch.setattr(os, "open", swap)
    monkeypatch.setattr(os, "read", tracked_read)
    try:
        with pytest.raises(invalid.InvalidDeclarationError, match="IC_WORKTREE"):
            frozen.current(TEST, limit=len(expected))
        assert swapped
        assert not forbidden_reads
    finally:
        if swapped:
            target.unlink()
            target.write_bytes(expected)
    assert git(history.root, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "path",
    [
        "uv.toml",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "setup.py",
        "conftest.py",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        ".env",
        ".env.example",
        "alembic.ini",
        "Makefile",
    ],
)
def test_supported_root_config_input_selects_relation(
    history: Fixture, path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(history.root, "rev-parse", "HEAD")
    history.write(path, b"# bound configuration\n")
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "root config addition")
    results = invalid.selected_unresolved(history.root, history.plan(), [], base)
    assert len(results) == 1 and results[0].status == "UNRESOLVED"
    assert results[0].historical_claim_verified is False
    assert v1.main(["--base", base], repo_root=history.root) == 1
    output = capsys.readouterr().out
    assert "0 historical verified" in output and "0 executions" in output


@pytest.mark.parametrize(
    "when", ["before-directory-open", "after-directory-open", "after-leaf-open", "after-leaf-read"]
)
def test_descriptor_path_replacement_refused(
    history: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, when: str
) -> None:
    frozen = invalid.FrozenGit(history.root)
    target = history.root / TEST
    expected = target.read_bytes()
    outside = tmp_path / "owned-external"
    outside.mkdir()
    (outside / target.name).write_bytes(expected)
    original_open, original_read = os.open, os.read
    opened: int | None = None
    swapped = False
    saved = tmp_path / "saved-directory"

    def swap() -> None:
        nonlocal swapped
        swapped = True
        if when in {"before-directory-open", "after-directory-open"}:
            target.parent.rename(saved)
            target.parent.symlink_to(outside, target_is_directory=True)
        else:
            target.unlink()
            target.symlink_to(outside / target.name)

    def open_hook(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal opened
        if str(path) == "unit" and kwargs.get("dir_fd") is not None and when == "before-directory-open":
            swap()
        fd = original_open(path, flags, *args, **kwargs)
        if str(path) == "unit" and kwargs.get("dir_fd") is not None and when == "after-directory-open":
            swap()
        if str(path) == target.name and kwargs.get("dir_fd") is not None:
            opened = fd
            if when == "after-leaf-open":
                swap()
        return fd

    def read_hook(fd: int, size: int) -> bytes:
        data = original_read(fd, size)
        if fd == opened and when == "after-leaf-read" and not swapped:
            swap()
        return data

    monkeypatch.setattr(os, "open", open_hook)
    monkeypatch.setattr(os, "read", read_hook)
    try:
        with pytest.raises(invalid.InvalidDeclarationError, match="IC_WORKTREE"):
            frozen.current(TEST)
        assert swapped
    finally:
        if swapped and when in {"before-directory-open", "after-directory-open"}:
            target.parent.unlink()
            saved.rename(target.parent)
        elif swapped:
            target.unlink()
            target.write_bytes(expected)
    assert git(history.root, "status", "--porcelain") == ""


def test_read_bound_survives_growth_after_fstat(history: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = invalid.FrozenGit(history.root)
    target = history.root / TEST
    expected = target.read_bytes()
    limit = len(expected)
    original_open, original_read = os.open, os.read
    opened: int | None = None
    consumed = 0

    def open_hook(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal opened
        fd = original_open(path, flags, *args, **kwargs)
        if str(path) == target.name and kwargs.get("dir_fd") is not None:
            opened = fd
        return fd

    def read_hook(fd: int, size: int) -> bytes:
        nonlocal consumed
        if fd == opened:
            if consumed == 0:
                target.write_bytes(expected + b"x" * 200_000)
            assert size <= limit - consumed + 1
        data = original_read(fd, size)
        if fd == opened:
            consumed += len(data)
        return data

    monkeypatch.setattr(os, "open", open_hook)
    monkeypatch.setattr(os, "read", read_hook)
    try:
        with pytest.raises(invalid.InvalidDeclarationError, match="IC_WORKTREE_SIZE"):
            frozen.current(TEST, limit=limit)
        assert consumed == limit + 1
    finally:
        target.write_bytes(expected)


def test_documentation_only_cli_retains_no_selection(
    history: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(history.root, "rev-parse", "HEAD")
    history.write("docs/explanation.md", b"# unrelated documentation\n")
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "documentation only")
    assert invalid.selected_unresolved(history.root, history.plan(), [], base) == ()
    assert v1.main(["--base", base], repo_root=history.root) == 0
    assert "PASS: 0 rows verified" in capsys.readouterr().out


@pytest.mark.parametrize("replacement", ["fifo", "oversized", "hardlink"])
def test_preexisting_nonregular_or_oversized_input_never_read(
    history: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replacement: str
) -> None:
    frozen = invalid.FrozenGit(history.root)
    target = history.root / TEST
    expected = target.read_bytes()
    outside = tmp_path / "owned-hardlink-source"
    outside.write_bytes(expected)
    target.unlink()
    if replacement == "fifo":
        os.mkfifo(target)
    elif replacement == "hardlink":
        os.link(outside, target)
    else:
        target.write_bytes(expected + b"x")
    info = target.stat()
    original_read = os.read
    reads: list[int] = []

    def read_hook(fd: int, size: int) -> bytes:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) == (info.st_dev, info.st_ino):
            reads.append(size)
        return original_read(fd, size)

    monkeypatch.setattr(os, "read", read_hook)
    try:
        with pytest.raises(invalid.InvalidDeclarationError, match="IC_WORKTREE_REGULAR"):
            frozen.current(TEST, limit=len(expected))
        assert not reads
    finally:
        target.unlink()
        target.write_bytes(expected)
