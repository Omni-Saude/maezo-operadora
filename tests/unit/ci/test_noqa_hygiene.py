"""INERT-NOQA-SWEEP: no `# noqa: <RULE>` in the tree may reference a rule prefix ruff's own
`pyproject.toml` `[tool.ruff.lint] select` does not enable.

Why this matters (found by the round-6 sweep, 2026-09-06): a `# noqa: <CODE>` for a rule prefix
outside `select` is not merely decorative — ruff never evaluates that code at all (it isn't
selected), so the suppression is dead FOR THAT CODE. It is not, however, a blanket E501 shield:
ruff excludes the `# noqa` directive comment itself from the width it measures for E501
(line-too-long, which IS enabled), so an inert directive only ever hides the *comment's own*
length — a CODE portion that is itself over the limit is still flagged even with an inert `# noqa`
attached (verified: a 136-char code portion followed by `# noqa: S104` still raises
`E501 Line too long (136 > 110)`). The corollary is what actually bit this sweep:
`src/maezo/platform/health.py`'s `build_health_server` def line was 166 characters wide with a
92-character code portion (well under 110) and a 74-character `# noqa: S104 - ...` directive
comment that ruff was not counting — `ruff check` passed at both base and tip for that reason, not
because the noqa hid an over-long code line. Downgrading the directive to a plain comment (this
sweep's edit shape for a dead suppression) made the *comment* start counting toward the width,
newly exposing `E501` on the same physical line. So an inert noqa is not inert to a reader
auditing the codebase for suppressions, and removing or downgrading one can newly expose E501 on
a long trailing comment — never the reverse.

This fence reads the enabled prefixes from `pyproject.toml` itself (never hard-coded — the whole
point is to track `select` if it ever changes) and fails on ANY future `# noqa: <CODE>` whose
rule-letter prefix (e.g. "BLE" of "BLE001", "S" of "S104") is not currently enabled.
"""

from __future__ import annotations

import io
import re
import subprocess
import tokenize
import tomllib
from pathlib import Path

import pytest

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Matches a suppression directive with an explicit, comma-separated rule-code list anywhere on a
# line (never a bare marker with no codes at all — this repo has none of those today; a bare one
# suppresses everything and is a different, worse anti-pattern this fence does not (yet) police).
# NOTE: written without the literal marker word immediately followed by ":" in this comment on
# purpose — ruff's own scanner for that exact directive shape does not distinguish prose from the
# real thing and would otherwise warn "Invalid directive" about this very sentence.
_NOQA_DIRECTIVE_RE = re.compile(
    r"#\s*noqa\s*:\s*(?P<codes>[A-Za-z][A-Za-z0-9]*(?:\s*,\s*[A-Za-z][A-Za-z0-9]*)*)"
)

# A rule code's PREFIX is its leading run of letters (e.g. "BLE" of "BLE001", "ASYNC" of
# "ASYNC220", "E" of "E501") — ruff's `select` entries are exactly these letter prefixes.
_RULE_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def enabled_rule_prefixes(pyproject_path: Path) -> frozenset[str]:
    """Pure: the `[tool.ruff.lint] select` list from `pyproject.toml`, as a frozenset of rule
    prefixes. Read from disk every time — never hard-coded — so this fence tracks `select` if it
    is ever widened or narrowed, instead of silently drifting from the real configuration."""
    with open(pyproject_path, "rb") as fh:
        data = tomllib.load(fh)
    select = data["tool"]["ruff"]["lint"]["select"]
    return frozenset(select)


def rule_prefix(code: str) -> str:
    """Pure: the letter-prefix of a rule code, e.g. `rule_prefix("BLE001") == "BLE"`."""
    match = _RULE_PREFIX_RE.match(code)
    return match.group(1) if match else code


def iter_comment_tokens(text: str) -> list[tuple[int, str]]:
    """Pure: `(1-indexed line number, comment text)` for every REAL `#` comment token in `text`,
    via `tokenize` — never a plain per-line substring search. This is the fix for a real
    false-positive this fence hit on its OWN source: a docstring that explains the `# noqa: CODE`
    shape, or a Python string literal building fixture content for another test (e.g.
    `"x = 1  # noqa: BLE001\\n"`, a target FILE's contents, not a comment in THIS file), both
    contain the literal text `# noqa: ...` without being an actual comment — `tokenize` classifies
    string/docstring content as `STRING`, never `COMMENT`, so only a genuine `#`-comment reaches
    the caller. A file that fails to tokenize (should not happen for tracked, lint-clean `*.py`
    files) is skipped, same fail-soft posture as an unreadable file."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []


def find_inert_noqa_references(
    paths: list[Path], enabled_prefixes: frozenset[str]
) -> list[tuple[Path, int, str]]:
    """Pure(ish) I/O: scan every file in `paths` for a `# noqa: CODE` directive whose code's
    prefix is not in `enabled_prefixes`. Returns `(path, 1-indexed line number, code)` for every
    such reference, in file order — one entry PER inert code, so a mixed line (some codes
    enabled, some not) reports only the inert ones, matching how the sweep's own repair kept
    enabled codes and dropped inert ones rather than treating the whole line as one unit. Only
    REAL comment tokens are inspected (`iter_comment_tokens`) — a docstring or string literal that
    merely CONTAINS the text `# noqa: ...` is never a false positive."""
    findings: list[tuple[Path, int, str]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, comment in iter_comment_tokens(text):
            match = _NOQA_DIRECTIVE_RE.search(comment)
            if match is None:
                continue
            codes = [c.strip() for c in match.group("codes").split(",")]
            for code in codes:
                if rule_prefix(code) not in enabled_prefixes:
                    findings.append((path, line_no, code))
    return findings


class TestEnabledRulePrefixes:
    def test_reads_the_real_pyproject_select_list(self) -> None:
        prefixes = enabled_rule_prefixes(_REPO_ROOT / "pyproject.toml")
        # Sanity floor, not a hard pin: today's real select is a strict superset of this — if a
        # future PR narrows `select` below this floor, THAT change should be reviewed deliberately
        # (this test failing is the signal), not silently accepted by a test that hard-codes the
        # full list and therefore never notices either direction of drift.
        assert {"E", "F", "B"} <= prefixes


class TestRulePrefix:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [("BLE001", "BLE"), ("S104", "S"), ("ASYNC220", "ASYNC"), ("E501", "E"), ("N801", "N")],
    )
    def test_extracts_the_letter_prefix(self, code: str, expected: str) -> None:
        assert rule_prefix(code) == expected


class TestFindInertNoqaReferencesFixture:
    """Fixture-based: never mutates the real tree — proves the detector itself works before the
    real-tree test below relies on it finding nothing."""

    def test_inert_code_is_reported(self, tmp_path: Path) -> None:
        target = tmp_path / "example.py"
        target.write_text(
            "def f():\n    x = 1  # noqa: BLE001 - hypothetical\n    return x\n",
            encoding="utf-8",
        )
        findings = find_inert_noqa_references([target], frozenset({"E", "F"}))
        assert findings == [(target, 2, "BLE001")]

    def test_enabled_code_is_not_reported(self, tmp_path: Path) -> None:
        target = tmp_path / "example.py"
        target.write_text("import os  # noqa: E402\n", encoding="utf-8")
        findings = find_inert_noqa_references([target], frozenset({"E"}))
        assert findings == []

    def test_mixed_line_reports_only_the_inert_code(self, tmp_path: Path) -> None:
        target = tmp_path / "example.py"
        target.write_text(
            "proc = f()  # noqa: S603, ASYNC220 - mixed line\n",
            encoding="utf-8",
        )
        findings = find_inert_noqa_references([target], frozenset({"ASYNC"}))
        assert findings == [(target, 1, "S603")]

    def test_no_noqa_at_all_is_not_reported(self, tmp_path: Path) -> None:
        target = tmp_path / "example.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert find_inert_noqa_references([target], frozenset()) == []


class TestRealTreeHasNoInertNoqa:
    """The acceptance bar (INERT-NOQA-SWEEP): every `# noqa: CODE` in the real tree, tracked by
    git (`git ls-files`, never a hand-maintained inventory — a new file is covered automatically),
    references a rule prefix `pyproject.toml`'s `[tool.ruff.lint] select` actually enables."""

    def test_no_tracked_python_file_carries_an_inert_noqa(self) -> None:
        enabled_prefixes = enabled_rule_prefixes(_REPO_ROOT / "pyproject.toml")
        tracked = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        paths = [_REPO_ROOT / rel for rel in tracked]

        findings = find_inert_noqa_references(paths, enabled_prefixes)

        assert findings == [], (
            f"{len(findings)} inert `# noqa` reference(s) found (rule prefix not in ruff's "
            f"enabled select {sorted(enabled_prefixes)}): "
            + "; ".join(f"{p.relative_to(_REPO_ROOT)}:{line}:{code}" for p, line, code in findings[:20])
            + (" ..." if len(findings) > 20 else "")
        )
