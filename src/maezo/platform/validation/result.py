"""Shared result type for artifact validators.

A `Finding` is a single ERROR pinned to a file (or directory) path — every
`Finding` blocks the CI gate; there is no "warning" severity here that a
caller could quietly downgrade into a pass. `Report.notice()` exists only for
purely informational, non-blocking observations (e.g. a stale allowlist
entry) and is never a substitute for reporting a real problem as an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Finding:
    """A single validation error, pinned to the artifact that caused it."""

    path: Path
    message: str

    def render(self) -> str:
        return f"  [ERROR] {self.path}: {self.message}"


@dataclass(slots=True)
class Report:
    """Accumulates findings (blocking) and notices (non-blocking) for one run."""

    findings: list[Finding] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def error(self, path: Path, message: str) -> None:
        """Record a blocking error. Any call to this makes `ok` False."""
        self.findings.append(Finding(path, message))

    def notice(self, message: str) -> None:
        """Record a non-blocking, informational observation."""
        self.notices.append(message)

    @property
    def ok(self) -> bool:
        """True iff no error was ever recorded. Notices never affect this."""
        return not self.findings
