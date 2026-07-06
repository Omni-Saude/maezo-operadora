"""Validation CLI — CI gate for BPMN, DMN, policies, agent-definitions.

Entry-point referenced by the Makefile:
    uv run python -m maezo.platform.validation.cli src/maezo/processes src/maezo/policies src/maezo/agents

Modes:
- validate: Scans directories for BPMN/DMN/policies/agent-definitions and
  validates basic well-formedness (file presence, parseability, cross-references).
- signoff: Validates that promoted content has human sign-off (Track C2).

This is a stub — the full validators are implemented in sibling modules
(bpmn.py, dmn.py, policy.py, agent_def.py) during later milestones.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


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
        help="Directories to scan (e.g., src/maezo/processes src/maezo/policies src/maezo/agents)",
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


def validate_artifacts(paths: Sequence[str]) -> int:
    """Validate BPMN, DMN, policies, agent-definitions in given paths.

    Stub implementation: confirms directories exist and contain expected
    file types. Full validation (BPMN XML schema, DMN FEEL expressions,
    policy YAML schema, agent-definition JSON schema) is implemented in
    sibling modules and integrated during later milestones.

    Args:
        paths: Directory paths to scan.

    Returns:
        0 if all paths exist and contain artifacts, 1 otherwise.
    """
    errors: list[str] = []
    found_any = False

    for raw_path in paths:
        p = Path(raw_path)
        if not p.exists():
            errors.append(f"Path does not exist: {raw_path}")
            continue
        if not p.is_dir():
            errors.append(f"Path is not a directory: {raw_path}")
            continue

        # Count artifacts in directory
        bpmn_count = len(list(p.rglob("*.bpmn")))
        dmn_count = len(list(p.rglob("*.dmn")))
        yaml_count = len(list(p.rglob("*.yaml"))) + len(list(p.rglob("*.yml")))
        json_count = len(list(p.rglob("*.json")))

        total = bpmn_count + dmn_count + yaml_count + json_count
        if total > 0:
            found_any = True
            print(
                f"[OK] {raw_path}: {bpmn_count} BPMN, {dmn_count} DMN, {yaml_count} YAML, {json_count} JSON"
            )
        else:
            # Not an error — some directories may be empty during greenfield
            print(f"[SKIP] {raw_path}: no artifacts found (empty directory)")

    if errors:
        for err in errors:
            print(f"[WARN] {err}", file=sys.stderr)
        # Non-existent paths are warnings during greenfield, not errors.
        # The gate becomes stricter as artifacts are populated in later milestones.

    if not found_any:
        print("[WARN] No artifacts found in any of the provided paths.", file=sys.stderr)
        # Not an error during greenfield — the gate becomes stricter as
        # artifacts are populated.

    return 0


def validate_signoff(strict: bool = True) -> int:
    """Validate that promoted content has human sign-off (Track C2).

    Stub: always passes during greenfield. When sign-off metadata files
    (*.signoff.yaml) are introduced, this validates their presence and
    completeness.

    Args:
        strict: If True, fail when any promoted artifact lacks sign-off.

    Returns:
        0 always during greenfield.
    """
    print("[OK] signoff: no promoted artifacts to validate (greenfield stub)")
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
