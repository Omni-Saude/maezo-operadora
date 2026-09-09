"""Explicit current all-PASS consumer; the legacy hash CLI is unchanged."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.ci import check_evidence_ledger_hashes as legacy
from scripts.ci.ledger_current_execution import (
    CurrentRunner,
    Occurrence,
    canonical,
    git,
    regular_bytes,
    source_inventory,
)

SCHEMA = "maezo-ledger-current-accounting/v1"
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def selected_occurrences(root: Path, base: str) -> tuple[Occurrence, ...]:
    """Keep Git hunk physical positions; never deduplicate identical row bytes/tasks."""
    git(root, "merge-base", "--is-ancestor", base, "HEAD")
    text = regular_bytes(root / legacy.DEFAULT_LEDGER_PATH).decode()
    actual = text.splitlines()
    diff = git(root, "diff", "--unified=0", base, "HEAD", "--", legacy.DEFAULT_LEDGER_PATH).decode()
    selected: list[Occurrence] = []
    cursor: int | None = None
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if match:
            cursor = int(match.group(1))
        elif cursor is not None and line.startswith("+"):
            raw = line[1:]
            if actual[cursor - 1] != raw:
                raise ValueError("PHYSICAL_OCCURRENCE_DRIFT")
            selection = legacy.select_rows([raw])
            if selection.declared:
                selected.append(Occurrence(cursor, raw))
            cursor += 1
        elif cursor is not None and line.startswith(" "):
            cursor += 1
    return tuple(selected)


def run_integrated(root: Path, base: str, output: Path, *, allow_live: bool = False) -> dict[str, Any]:
    """Schedule exact physical current obligations; operational history is unresolved."""
    from scripts.ci.ledger_current_relations import plan_current_relations

    root = root.resolve()
    before = source_inventory(root)
    base = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    scope = plan_current_relations(root, base)
    runner = CurrentRunner(root, output, base=base, allow_live=allow_live)
    rows: list[dict[str, Any]] = []
    executed: list[str] = []
    for requirement in scope.requirements:
        physical = requirement.occurrence
        occurrence = Occurrence(physical.line, physical.raw_line)
        verdict = runner.run(occurrence)
        consumed = runner.consume(verdict, occurrence)
        if verdict.run_id in runner._executed:
            executed.append(physical.identity)
        history_required = bool(requirement.required_relations)
        rows.append(
            {
                "occurrence": physical.identity,
                "line": physical.line,
                "row_sha256": physical.row_sha256,
                "task": physical.task_id,
                "path": physical.test_path,
                "original": asdict(physical),
                "current_expectation": asdict(requirement.current),
                "required_relations": list(requirement.required_relations),
                "history": "REQUIRED_NOT_COMPOSED" if history_required else "NOT_REQUIRED",
                "current": asdict(verdict),
                "current_consumed": consumed,
                "status": "UNRESOLVED"
                if history_required or (verdict.status == "ACCEPTED" and not consumed)
                else verdict.status,
            }
        )
    stable = source_inventory(root) == before and plan_current_relations(root, base) == scope
    current_accepted = bool(rows) and stable and all(row["current_consumed"] for row in rows)
    return {
        "schema": SCHEMA,
        "candidate": before["commit"],
        "tree": before["tree"],
        "base": base,
        "selected_count": len(scope.selected),
        "selected_occurrence_identities": list(scope.selected),
        "required_occurrence_identities": [req.occurrence.identity for req in scope.requirements],
        "required_relation_identities": list(scope.required_relations),
        "executed_occurrence_identities": executed,
        "execution_count": len(executed),
        "attempt_count": len(rows),
        "relations": [
            {
                "identity": edge.identity,
                "kind": edge.claim.kind,
                "claim": asdict(edge.claim),
                "historical_claim_verified": edge.claim.historical_claim_verified,
                "status": "UNRESOLVED",
                "reason": "ACTUAL_HISTORY_ADAPTER_NOT_IMPLEMENTED",
            }
            for edge in scope.relations
            if edge.identity in scope.required_relations
        ],
        "rows": rows,
        "source_stable": stable,
        "current_status": "ACCEPTED" if current_accepted else "UNRESOLVED",
        "scope": "EXPLICIT_BASE_RELATION_CLOSURE",
        "global_acceptance": False,
        "status": "ACCEPTED" if current_accepted and not scope.required_relations else "UNRESOLVED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--proof-output", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        result = run_integrated(root, args.base, args.proof_output, allow_live=args.allow_live)
    except (OSError, ValueError, legacy.SupersessionError, subprocess.SubprocessError) as exc:
        result = {"schema": SCHEMA, "status": "UNRESOLVED", "reason": type(exc).__name__}
    args.proof_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = args.proof_output / "accounting.json"
    with target.open("xb") as stream:
        stream.write(canonical(result))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
