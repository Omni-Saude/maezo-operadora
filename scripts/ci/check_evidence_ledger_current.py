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
    HistoryRunner,
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
    """Consume independent fresh current and history handles for the exact closure."""
    from scripts.ci.ledger_current_relations import plan_current_relations

    root = root.resolve()
    before = source_inventory(root)
    base = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    scope = plan_current_relations(root, base)
    runner = CurrentRunner(root, output, base=base, allow_live=allow_live)
    history_runner = HistoryRunner(root, output / "history", base=base)
    histories: list[dict[str, Any]] = []
    history_handles = []
    for edge in scope.relations:
        if edge.identity not in scope.required_relations:
            continue
        history = history_runner.run(edge.identity)
        history_handles.append(history)
        histories.append(
            {
                **asdict(history),
                "identity": edge.identity,
                "kind": edge.claim.kind,
                "claim": asdict(edge.claim),
                "successor": asdict(edge.successor),
                "terminal": asdict(edge.terminal),
                "consumed": False,
            }
        )
    by_relation = {item["identity"]: item for item in histories}
    rows: list[dict[str, Any]] = []
    executed: list[str] = []
    current_handles = []
    for requirement in scope.requirements:
        physical = requirement.occurrence
        occurrence = Occurrence(physical.line, physical.raw_line)
        verdict = runner.run(occurrence)
        current_handles.append((verdict, occurrence))
        consumed = False
        if verdict.run_id in runner._executed:
            executed.append(physical.identity)
        history_required = bool(requirement.required_relations)
        history_consumed = all(
            by_relation[identity]["consumed"] for identity in requirement.required_relations
        )
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
                "history": ("VERIFIED" if history_consumed else "UNRESOLVED")
                if history_required
                else "NOT_REQUIRED",
                "current": asdict(verdict),
                "current_consumed": consumed,
                "status": "UNRESOLVED"
                if not history_consumed or (verdict.status == "ACCEPTED" and not consumed)
                else verdict.status,
            }
        )
    # No child launches after consumption begins: each check covers all earlier
    # executions, including a later child attempting to alter an earlier packet.
    for item, history_handle in zip(histories, history_handles, strict=True):
        item["consumed"] = history_runner.consume(history_handle, item["identity"])
    for item, (handle, occurrence) in zip(rows, current_handles, strict=True):
        item["current_consumed"] = runner.consume(handle, occurrence)
        history_ok = all(by_relation[identity]["consumed"] for identity in item["required_relations"])
        item["history"] = (
            ("VERIFIED" if history_ok else "UNRESOLVED") if item["required_relations"] else "NOT_REQUIRED"
        )
        if history_ok and item["required_relations"]:
            # Discharging an invalid declaration never labels that original
            # historical claim as verified.
            item["history"] = next(
                status
                for status in (
                    "CORRECTED_WITH_INVALID_HISTORY",
                    "VERIFIED_HISTORY_OWN_LOCK",
                    "VALID_HISTORICAL_EQUALITY",
                )
                if any(by_relation[identity]["status"] == status for identity in item["required_relations"])
            )
        item["status"] = (
            "UNRESOLVED"
            if not history_ok or (handle.status == "ACCEPTED" and not item["current_consumed"])
            else handle.status
        )
    stable = source_inventory(root) == before and plan_current_relations(root, base) == scope
    current_accepted = bool(rows) and stable and all(row["current_consumed"] for row in rows)
    history_accepted = set(by_relation) == set(scope.required_relations) and all(
        item["consumed"] for item in histories
    )
    invalid_count = sum(
        item["status"] == "CORRECTED_WITH_INVALID_HISTORY" and item["consumed"] for item in histories
    )
    own_lock_count = sum(
        item["status"] == "VERIFIED_HISTORY_OWN_LOCK" and item["consumed"] for item in histories
    )
    accepted_status = (
        "ACCEPTED_WITH_INVALID_HISTORY"
        if invalid_count
        else "ACCEPTED_WITH_VERIFIED_HISTORY"
        if own_lock_count
        else "ACCEPTED"
    )
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
        "relations": histories,
        "history_execution_count": sum(item["execution_count"] for item in histories)
        if all(item["execution_count"] is not None for item in histories)
        else None,
        "history_attempt_count": len(histories),
        "invalid_history_count": invalid_count,
        "verified_own_lock_count": own_lock_count,
        "rows": rows,
        "source_stable": stable,
        "current_status": "ACCEPTED" if current_accepted else "UNRESOLVED",
        "scope": "EXPLICIT_BASE_RELATION_CLOSURE",
        "global_acceptance": False,
        "status": accepted_status if current_accepted and history_accepted else "UNRESOLVED",
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
    return (
        0
        if result["status"] in {"ACCEPTED", "ACCEPTED_WITH_INVALID_HISTORY", "ACCEPTED_WITH_VERIFIED_HISTORY"}
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
