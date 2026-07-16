"""Validation CLI — CI gate for BPMN, DMN, policies, agent-definitions.

Entry-point referenced by the Makefile:
    uv run python -m maezo.platform.validation.cli validate spec/processes spec/policies spec/agents

Modes:
- validate: parses and cross-validates BPMN/DMN/policy/agent-definition
  artifacts under the given directories (delegates to the sibling modules
  `bpmn.py`, `dmn.py`, `policy.py`, `agent_def.py`, `crossref.py`).
  FAIL-CLOSED: a missing path, an unrecognized directory, an unparseable
  file, a missing required field, a broken BPMN->DMN cross-reference, or an
  un-allowlisted orphan DMN all make this exit non-zero. There is no
  "greenfield" exemption and no warn-then-pass path — spec/ is the single
  source of truth (per ADR) and this gate exists to keep it honest.
- signoff: NOT YET IMPLEMENTED. See `validate_signoff()` docstring — tracked
  as T2.2. A green `make validate-signoff` today proves nothing.

Each directory passed to `validate` is classified by its *structure*, not by
name, so this gate does not silently go blind if a path is renamed (e.g.
`src/maezo/agents` -> `spec/agents`, T0.3/B14):
  - a "processes" root has a `bpmn/` and/or `dmn/` subdirectory;
  - a "policies" root has an `autonomy/` subdirectory;
  - an "agents" root has subdirectories that each contain an `agent.yaml`.
A directory matching none of these is a fail-closed error, not a silent skip.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import agent_def, bpmn, crossref, dmn, policy
from .result import Report


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="maezo-validate",
        description="Validate BPMN, DMN, policies, and agent definitions for CI gating.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    val = sub.add_parser("validate", help="Validate compliance artifacts in directories")
    val.add_argument(
        "paths",
        nargs="+",
        help="Directories to scan (e.g., spec/processes spec/policies spec/agents)",
    )

    # signoff
    sig = sub.add_parser("signoff", help="Validate human sign-off for promoted content (Track C2)")
    sig.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Fail if any promoted artifact lacks sign-off (default: true)",
    )

    return parser


def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` until a `pyproject.toml` is found; fall back to cwd."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def _is_processes_root(path: Path) -> bool:
    return (path / "bpmn").is_dir() or (path / "dmn").is_dir()


def _is_policies_root(path: Path) -> bool:
    return (path / "autonomy").is_dir()


def _is_agents_root(path: Path) -> bool:
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return any(child.is_dir() and (child / "agent.yaml").is_file() for child in children)


def _validate_processes_root(path: Path, report: Report) -> None:
    bpmn_dir = path / "bpmn"
    dmn_dir = path / "dmn"

    bpmn_files = sorted(bpmn_dir.glob("*.bpmn")) if bpmn_dir.is_dir() else []
    if not bpmn_files:
        report.error(bpmn_dir, "no .bpmn files found")

    dmn_files = sorted(dmn_dir.glob("*.dmn")) if dmn_dir.is_dir() else []
    if not dmn_files:
        report.error(dmn_dir, "no .dmn files found")

    decision_refs: list[crossref.DecisionRef] = []
    for bpmn_path in bpmn_files:
        bpmn_result = bpmn.validate_file(bpmn_path, report)
        if bpmn_result is not None:
            for ref in bpmn_result.decision_refs:
                decision_refs.append(crossref.DecisionRef(ref, bpmn_path))

    defined_ids: dict[str, Path] = {}
    for dmn_path in dmn_files:
        dmn_result = dmn.validate_file(dmn_path, report)
        if dmn_result is not None:
            for did in dmn_result.decision_ids:
                if did in defined_ids:
                    report.error(
                        dmn_path,
                        f"dmn:decision id '{did}' is already declared in {defined_ids[did]} — "
                        "decision ids must be unique across the whole spec/processes/dmn tree "
                        "(a BPMN businessRuleTask resolves decisionRef by id, not by filename)",
                    )
                else:
                    defined_ids[did] = dmn_path

    if bpmn_files or dmn_files:
        allowlist_path = dmn_dir / crossref.ALLOWLIST_FILENAME
        crossref.check(decision_refs, defined_ids, allowlist_path, report)


def _validate_policies_root(path: Path, report: Report) -> None:
    policy.validate_dir(path / "autonomy", report)


def _validate_agents_root(path: Path, report: Report) -> None:
    tools_root = _find_repo_root(path.resolve()) / "src" / "maezo" / "tools"
    agent_def.validate_dir(path, tools_root, report)


def validate_artifacts(paths: Sequence[str]) -> int:
    """Validate BPMN, DMN, policies, and agent-definitions in the given paths.

    Fail-closed throughout: a missing/non-directory path, an unrecognized
    directory structure, an unparseable file, a schema violation, a broken
    BPMN->DMN cross-reference, or an un-allowlisted orphan DMN each add at
    least one error — and any error makes this return 1. There is no path
    left that reports a real problem and still returns 0.

    Args:
        paths: Directory paths to scan.

    Returns:
        0 if every path validated cleanly, 1 otherwise.
    """
    report = Report()

    if not paths:
        report.error(Path("."), "no paths provided to validate — nothing to certify as passing")

    for raw_path in paths:
        p = Path(raw_path)
        if not p.exists():
            report.error(p, "path does not exist")
            continue
        if not p.is_dir():
            report.error(p, "path is not a directory")
            continue

        resolved = p.resolve()
        matched = False
        if _is_processes_root(resolved):
            matched = True
            _validate_processes_root(resolved, report)
        if _is_policies_root(resolved):
            matched = True
            _validate_policies_root(resolved, report)
        if _is_agents_root(resolved):
            matched = True
            _validate_agents_root(resolved, report)
        if not matched:
            report.error(
                p,
                "directory structure not recognized as a processes root (bpmn/ or dmn/ "
                "subdirectory), a policies root (autonomy/ subdirectory), or an agents root "
                "(subdirectories each containing agent.yaml) — cannot validate what cannot be "
                "classified",
            )

    for notice in report.notices:
        print(f"[validate] {notice}")
    for finding in report.findings:
        print(finding.render())

    if report.ok:
        print(f"[validate] OK — 0 errors, {len(report.notices)} notice(s).")
        return 0
    print(f"[validate] FAILED — {len(report.findings)} error(s), {len(report.notices)} notice(s).")
    return 1


def validate_signoff(strict: bool = True) -> int:
    """NOT YET IMPLEMENTED — placeholder only. Tracked as T2.2.

    Unlike `validate_artifacts()` (this module), this function is
    deliberately NOT a real gate: it always returns 0 regardless of the
    actual state of the repo. A green `make validate-signoff` today proves
    nothing about whether promoted/FINAL content actually has human
    sign-off — do not treat it as evidence of that. T2.2 will replace this
    with a real check of `*.signoff.yaml` metadata against promoted
    artifacts, wired to docs/review-queue.md.

    Args:
        strict: Accepted for CLI compatibility; has no effect yet.

    Returns:
        0 always (stub — see docstring above).
    """
    print(
        "[signoff] NOT YET IMPLEMENTED (T2.2) — this call always returns 0 and validates "
        "nothing; see validate_signoff() in src/maezo/platform/validation/cli.py before relying "
        "on it as a real gate.",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the validation CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate_artifacts(args.paths)
    elif args.command == "signoff":
        return validate_signoff(strict=args.strict)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
