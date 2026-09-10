"""Path, revision and output-custody checks for maintained verification consumers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    validate_inputs,
    write_json_exclusive,
)


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(["/usr/bin/git", "-C", str(repository), *args], text=True).strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "verification@example.invalid")
    _git(path, "config", "user.name", "Verification Fixture")
    (path / "tracked.txt").write_text("exact\n")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-q", "-m", "fixture")
    return path, _git(path, "rev-parse", "HEAD")


def test_explicit_source_can_be_relocated_without_changing_revision(tmp_path: Path) -> None:
    source, head = _repository(tmp_path / "source-a")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    first = validate_inputs(
        source=source, evidence=evidence, output=outputs / "first.json", expected_head=head
    )
    relocated = tmp_path / "source-b"
    source.rename(relocated)
    second = validate_inputs(
        source=relocated, evidence=evidence, output=outputs / "second.json", expected_head=head
    )

    assert first.head == second.head == head
    assert first.source != second.source
    assert second.source == relocated.resolve()


def test_wrong_revision_and_dirty_source_are_refused_before_output(tmp_path: Path) -> None:
    source, previous = _repository(tmp_path / "source")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (source / "tracked.txt").write_text("new revision\n")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-q", "-m", "second")

    wrong_output = outputs / "wrong.json"
    with pytest.raises(VerificationRefusedError, match="required Git revision"):
        validate_inputs(source=source, evidence=evidence, output=wrong_output, expected_head=previous)
    assert not wrong_output.exists()

    current = _git(source, "rev-parse", "HEAD")
    (source / "untracked.txt").write_text("dirty\n")
    dirty_output = outputs / "dirty.json"
    with pytest.raises(VerificationRefusedError, match="not clean"):
        validate_inputs(source=source, evidence=evidence, output=dirty_output, expected_head=current)
    assert not dirty_output.exists()


def test_existing_or_evidence_local_output_is_refused_without_overwrite(tmp_path: Path) -> None:
    source, head = _repository(tmp_path / "source")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output = tmp_path / "result.json"
    output.write_bytes(b"preserve-me\n")

    with pytest.raises(VerificationRefusedError, match="already exists"):
        validate_inputs(source=source, evidence=evidence, output=output, expected_head=head)
    with pytest.raises(VerificationRefusedError, match="fresh output"):
        write_json_exclusive(output, {"replacement": True})
    assert output.read_bytes() == b"preserve-me\n"

    with pytest.raises(VerificationRefusedError, match="outside source and evidence"):
        validate_inputs(
            source=source,
            evidence=evidence,
            output=evidence / "new-result.json",
            expected_head=head,
        )
