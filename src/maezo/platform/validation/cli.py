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
- signoff: parses `**Status:**` out of every `docs/processes/contracts/*.md`
  contract and requires a valid, current-version, human-approved
  `docs/processes/contracts/signoffs/<ID>.signoff.yaml` for every FINAL one
  (delegates to the sibling module `signoff.py`). FAIL-CLOSED: a missing,
  malformed, mismatched-version, or `needs-changes` signoff each make this
  exit non-zero — see `signoff.py` for the full rule and its one narrow,
  visible, auditable grandfather exception.

Each directory passed to `validate` is classified by its *structure*, not by
name, so this gate does not silently go blind if a path is renamed (e.g.
`src/maezo/agents` -> `spec/agents`, T0.3/B14):
  - a "processes" root has a `bpmn/` and/or `dmn/` subdirectory;
  - a "policies" root has an `autonomy/` subdirectory (and, since R-199, is also
    where the CODEOWNED `phi/` dispositions manifest is validated);
  - an "agents" root has subdirectories that each contain an `agent.yaml`.
A directory matching none of these is a fail-closed error, not a silent skip.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import agent_def, bpmn, crossref, dmn, perspective, policy, signoff
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

    # ADR-0040 perspective fence (Tier A: raw BPMN/DMN text; Tier B: parsed spec YAML). Every hit
    # is a BLOCKING `report.error` — the module has no severity a caller could downgrade and no
    # per-file exception mechanism. Wired HERE, and only from PR-4, because the fence could not be
    # switched on while the two chains were still dirty: it exists (PR-1) before it gates (PR-4).
    perspective.check_processes(bpmn_files, dmn_files, sorted(dmn_dir.glob("*.yaml")), report)


def _validate_policies_root(path: Path, report: Report) -> None:
    policy.validate_dir(path / "autonomy", report)
    # R-199: the CODEOWNED PHI dispositions manifest. SCHEMA ONLY. The code<->manifest
    # closure deliberately lives on the completeness fence's side (its `check_sweep`), so
    # this gate still does not import or run that module — see the test that pins it.
    policy.validate_phi_dispositions_dir(path / policy.PHI_DISPOSITIONS_DIRNAME, report)
    perspective.check_policies_root(path, report)


def _validate_agents_root(path: Path, report: Report) -> None:
    tools_root = _find_repo_root(path.resolve()) / "src" / "maezo" / "tools"
    agent_def.validate_dir(path, tools_root, report)
    perspective.check_agents_root(path, report)


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
    """Real, fail-closed sign-off gate for promoted (FINAL) process contracts (T2.2).

    Replaces the pre-T2.2 stub that always returned 0 regardless of repo state
    (defect B2/B8, signoff side). Delegates to `signoff.validate_signoffs()`,
    which parses every contract's `**Status:**` line under
    `docs/processes/contracts/*.md` and requires a valid, current-version,
    human-`approved` `docs/processes/contracts/signoffs/<ID>.signoff.yaml` for
    every FINAL one. The sole exception is the narrow, visible, auditable
    grandfather list at
    `docs/processes/contracts/signoffs/retro-verification-pending.yaml`,
    cross-checked against `docs/sme-dispatch/tracker.md` on every run — see
    `signoff.py` for the exact rules.

    Args:
        strict: Accepted for CLI/Makefile interface compatibility. Sign-off
            enforcement is fail-closed unconditionally — there is no
            non-strict mode that lets a FINAL contract through without a
            valid signoff or a checked grandfather entry, so this flag has no
            effect on the outcome.

    Returns:
        0 if every FINAL contract has a valid signoff (or a verified
        grandfather exception), 1 otherwise.
    """
    del strict  # accepted for interface compatibility only; no lenient mode exists

    repo_root = _find_repo_root(Path.cwd())
    contracts_dir = repo_root / "docs" / "processes" / "contracts"
    tracker_path = repo_root / "docs" / "sme-dispatch" / "tracker.md"

    report = Report()
    signoff.validate_signoffs(contracts_dir, tracker_path, report)

    for notice in report.notices:
        print(f"[signoff] {notice}")
    for finding in report.findings:
        print(finding.render())

    if report.ok:
        print(f"[signoff] OK — 0 errors, {len(report.notices)} notice(s).")
        return 0
    print(f"[signoff] FAILED — {len(report.findings)} error(s), {len(report.notices)} notice(s).")
    return 1


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
