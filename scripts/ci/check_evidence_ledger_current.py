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
from scripts.ci import ledger_invalid_declarations as history
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
    """Observe ordinary current rows; leave uncomposed history obligations explicit."""
    before = source_inventory(root)
    base = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    text = regular_bytes(root / legacy.DEFAULT_LEDGER_PATH).decode()
    supersession = legacy.build_supersession_plan(root, text)
    operational = history.build_plan(root, text, supersession, legacy.DEFAULT_LEDGER_PATH)
    occurrences = selected_occurrences(root, base)
    required = set(supersession.by_target_row_sha256) | set(supersession.by_successor_row_sha256)
    for relation in operational.relations:
        required.add(legacy.row_sha256(relation.target))
        required.add(legacy.row_sha256(relation.correction))
    runner = CurrentRunner(root, output, allow_live=allow_live)
    rows: list[dict[str, Any]] = []
    for occurrence in occurrences:
        record: dict[str, Any] = {
            "occurrence": occurrence.identity,
            "line": occurrence.line,
            "row_sha256": legacy.row_sha256(occurrence.row),
            "task": occurrence.row.task_id,
            "path": occurrence.row.test_path,
        }
        if legacy.row_sha256(occurrence.row) in required:
            record.update(status="UNRESOLVED", history="REQUIRED_NOT_COMPOSED", current="NOT_RUN")
        else:
            verdict = runner.run(occurrence)
            accepted = runner.consume(verdict, occurrence)
            record.update(
                status="ACCEPTED" if accepted else verdict.status,
                history="NOT_REQUIRED",
                current=asdict(verdict),
            )
            if verdict.status == "ACCEPTED" and not accepted:
                record["status"] = "UNRESOLVED"
        rows.append(record)
    stable = source_inventory(root) == before
    return {
        "schema": SCHEMA,
        "candidate": before["commit"],
        "tree": before["tree"],
        "base": base,
        "selected_count": len(occurrences),
        "rows": rows,
        "source_stable": stable,
        "status": "ACCEPTED"
        if rows and stable and all(row["status"] == "ACCEPTED" for row in rows)
        else "UNRESOLVED",
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
