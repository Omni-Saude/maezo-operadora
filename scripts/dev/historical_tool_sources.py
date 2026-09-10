"""Immutable historical tooling, separate from lease-owned execution checkouts.

This inert capsule contains only three reviewed Python sources, never a Git
checkout, environment, Compose file or execution source. Its temporary-directory
finalizer may remove these inert copies at GC/interpreter exit. Loaded modules
retain their capsule so their real __file__ bytes remain available for receipts.
Migration OwnedExecutionCheckout has a different, explicit lease-bound lifetime;
this module must never receive or dispose of its paths.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

# Same immutable closure as scripts/ci/ledger_history_proofs.py:TOOL_SOURCES.
TOOL_SOURCES = {
    "scripts/dev/run_historical_unit_recipe.py": (
        "babecc190d4fc6987165f6f0899c886d2d0a4e71",
        "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c",
    ),
    "scripts/ci/check_evidence_ledger_hashes.py": (
        "7189bcb0b3532a48401bf86f376cf37e876adabf",
        "8ac5085b95b6d4e3cbaee4decfa1315306ae3747eb77a54a57024096c38bfcac",
    ),
    "scripts/dev/run_engine_integration.py": (
        "7189bcb0b3532a48401bf86f376cf37e876adabf",
        "02810ce8ecda0c7d609c1f58a3b3da8816a1cc5190c37e746593bcd39f3b80d3",
    ),
}
MAX_SOURCE_BYTES = 1024 * 1024


class ToolSourceRefusedError(ValueError):
    """The complete approved source closure could not be established."""


def _git(repository: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args],
            cwd=repository,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            env={
                "PATH": os.defpath,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolSourceRefusedError("original tooling Git source unavailable") from exc
    if result.returncode or len(result.stdout) > MAX_SOURCE_BYTES:
        raise ToolSourceRefusedError("original tooling Git source unavailable or nonancestor")
    return result.stdout


class ToolSourceCapsule:
    """One fixed, byte-pinned closure from exact ancestors of the supplied HEAD."""

    def __init__(self, repository: Path):
        repository = repository.resolve()
        head = _git(repository, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        if _git(repository, "rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ToolSourceRefusedError("shallow tooling ancestry")
        if _git(repository, "for-each-ref", "--format=%(refname)", "refs/replace").strip():
            raise ToolSourceRefusedError("replacement tooling ancestry")
        graft = Path(_git(repository, "rev-parse", "--git-path", "info/grafts").decode().strip())
        if not graft.is_absolute():
            graft = repository / graft
        if graft.exists() or graft.is_symlink():
            raise ToolSourceRefusedError("grafted tooling ancestry")
        payloads = {}
        for relative, (commit, expected) in TOOL_SOURCES.items():
            if _git(repository, "cat-file", "-t", commit).strip() != b"commit":
                raise ToolSourceRefusedError("tooling source is not a commit")
            _git(repository, "merge-base", "--is-ancestor", commit, head)
            entry = _git(repository, "ls-tree", commit, "--", relative).split()
            if len(entry) != 4 or entry[0] not in {b"100644", b"100755"} or entry[1] != b"blob":
                raise ToolSourceRefusedError("missing or nonregular tooling dependency")
            oid = entry[2].decode("ascii")
            if int(_git(repository, "cat-file", "-s", oid)) > MAX_SOURCE_BYTES:
                raise ToolSourceRefusedError("oversized tooling dependency")
            data = _git(repository, "cat-file", "blob", oid)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ToolSourceRefusedError("original tooling source pin mismatch")
            payloads[relative] = data
        # No local source fallback and no tool execution before the WHOLE closure
        # passes its pins. The temporary path can only be minted here.
        self._temporary = tempfile.TemporaryDirectory(prefix="maezo-inert-historical-tools-")
        self.root = Path(self._temporary.name).resolve()
        for relative, data in payloads.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o600)

    def _read(self, relative: str) -> bytes:
        if relative not in TOOL_SOURCES:
            raise ToolSourceRefusedError("unapproved tooling dependency")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in Path(relative).parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            source = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(source)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_SOURCE_BYTES:
                    raise ToolSourceRefusedError("nonregular tooling capsule source")
                with os.fdopen(os.dup(source), "rb") as stream:
                    data = stream.read(MAX_SOURCE_BYTES + 1)
                after = os.stat(Path(relative).name, dir_fd=fd, follow_symlinks=False)
                # Reading may update atime; identity/content metadata must not.
                if any(
                    getattr(current, field) != getattr(info, field)
                    for current in (os.fstat(source), after)
                    for field in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_uid",
                        "st_nlink",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                ):
                    raise ToolSourceRefusedError("tooling capsule source changed during read")
            finally:
                os.close(source)
        finally:
            os.close(fd)
        if hashlib.sha256(data).hexdigest() != TOOL_SOURCES[relative][1]:
            raise ToolSourceRefusedError("tooling capsule source pin mismatch")
        return data

    def load(self, relative: str, name: str) -> ModuleType:
        try:
            payloads = {path: self._read(path) for path in TOOL_SOURCES}
        except OSError as exc:
            raise ToolSourceRefusedError("missing or substituted tooling capsule source") from exc
        if relative not in payloads:
            raise ToolSourceRefusedError("unapproved tooling dependency")
        path = self.root / relative
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        module._historical_tool_capsule = self
        sys.modules[name] = module
        try:
            exec(compile(payloads[relative], str(path), "exec"), module.__dict__)
            if payloads != {source: self._read(source) for source in TOOL_SOURCES}:
                raise ToolSourceRefusedError("tooling capsule source changed during import")
        except BaseException:
            sys.modules.pop(name, None)
            raise
        # The frozen helper imports its two descendants internally. A caller may
        # retain one after dropping the helper, so each module owns inert sources.
        for value in module.__dict__.copy().values():
            if (
                isinstance(value, ModuleType)
                and getattr(value, "__file__", None)
                and Path(value.__file__).is_relative_to(self.root)
            ):
                value._historical_tool_capsule = self
        return module
