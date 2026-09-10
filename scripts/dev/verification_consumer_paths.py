"""Shared path, Git-revision and fresh-output guards for maintained verification consumers."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VerificationRefusedError(RuntimeError):
    """A consumer input cannot support the requested exact-source verification."""


@dataclass(frozen=True)
class VerifiedInputs:
    source: Path
    evidence: Path
    output: Path
    head: str
    tree: str


def _existing_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise VerificationRefusedError(f"{label} must not be a symlink")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise VerificationRefusedError(f"{label} is unavailable") from exc
    if not resolved.is_dir():
        raise VerificationRefusedError(f"{label} must be a directory")
    return resolved


def git(source: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(source), *args],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            env={
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationRefusedError("Git source inspection failed") from exc
    if result.returncode:
        raise VerificationRefusedError("Git source inspection failed")
    return result.stdout


def verify_source(source: Path, expected_head: str) -> tuple[str, str]:
    top = Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
    if top != source:
        raise VerificationRefusedError("source must be the Git worktree root")
    head = git(source, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if head != expected_head:
        raise VerificationRefusedError("source is not the required Git revision")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise VerificationRefusedError("source worktree is not clean")
    tree = git(source, "rev-parse", "HEAD^{tree}").decode().strip()
    return head, tree


def validate_inputs(*, source: Path, evidence: Path, output: Path, expected_head: str) -> VerifiedInputs:
    checked_source = _existing_directory(source, "source")
    checked_evidence = _existing_directory(evidence, "evidence")
    if output.is_symlink() or output.exists():
        raise VerificationRefusedError("output already exists")
    try:
        parent = output.expanduser().parent.resolve(strict=True)
    except OSError as exc:
        raise VerificationRefusedError("output parent is unavailable") from exc
    if not parent.is_dir() or output.name in {"", ".", ".."}:
        raise VerificationRefusedError("output path is invalid")
    checked_output = parent / output.name
    if checked_output.is_relative_to(checked_source) or checked_output.is_relative_to(checked_evidence):
        raise VerificationRefusedError("output must be outside source and evidence")
    head, tree = verify_source(checked_source, expected_head)
    return VerifiedInputs(checked_source, checked_evidence, checked_output, head, tree)


def write_json_exclusive(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise VerificationRefusedError("fresh output could not be created") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_output_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise VerificationRefusedError("fresh output directory could not be created") from exc
    return path
