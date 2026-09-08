"""Fail a live CI lane when its JUnit report is partial or contains an ordinary skip.

Pytest returns zero when tests skip because Postgres, Kafka, or CIB Seven is unreachable. That is
useful for local discovery, but a CI lane which has just started and probed those services must not
turn the same condition into green. Only explicit ``MAEZO_CHAOS_MUTATE`` companion skips and
strict xfails remain accepted and visible.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_MUTATION_REASON_TOKEN = "MAEZO_CHAOS_MUTATE="


@dataclass(frozen=True)
class JunitSummary:
    total: int
    collected: int | None
    executed: int
    failures: int
    errors: int
    mutation_skipped: tuple[str, ...]
    xfailed: tuple[str, ...]
    unexpected_skips: tuple[str, ...]


def _reason(testcase: ET.Element, skipped: ET.Element) -> str:
    node = "::".join(part for part in (testcase.get("classname", ""), testcase.get("name", "")) if part)
    detail = skipped.get("message", "") or (skipped.text or "").strip() or "skip sem motivo"
    return f"{node}: {detail}" if node else detail


def _collection_count(path: Path) -> int:
    return sum(
        "::" in line and line.split("::", 1)[0].endswith(".py")
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def inspect_junit(path: Path, collection: Path | None = None) -> JunitSummary:
    root = ET.parse(path).getroot()
    testcases = root.findall(".//testcase")
    mutation_skipped: list[str] = []
    xfailed_reasons: list[str] = []
    unexpected_skips: list[str] = []

    for testcase in testcases:
        skipped = testcase.find("skipped")
        if skipped is None:
            continue
        reason = _reason(testcase, skipped)
        if skipped.get("type") == "pytest.xfail":
            xfailed_reasons.append(reason)
        elif _MUTATION_REASON_TOKEN in reason:
            mutation_skipped.append(reason)
        else:
            unexpected_skips.append(reason)

    failures = len(root.findall(".//failure"))
    errors = len(root.findall(".//error"))
    skipped_count = len(mutation_skipped) + len(xfailed_reasons) + len(unexpected_skips)
    return JunitSummary(
        total=len(testcases),
        collected=_collection_count(collection) if collection is not None else None,
        executed=len(testcases) - skipped_count,
        failures=failures,
        errors=errors,
        mutation_skipped=tuple(mutation_skipped),
        xfailed=tuple(xfailed_reasons),
        unexpected_skips=tuple(unexpected_skips),
    )


def validate_junit(path: Path, collection: Path | None = None) -> JunitSummary:
    summary = inspect_junit(path, collection)
    problems: list[str] = []
    if summary.total == 0:
        problems.append("o relatório não contém nenhum caso coletado")
    if summary.executed == 0:
        problems.append("o relatório não contém nenhum caso efetivamente executado")
    if summary.collected is not None and summary.collected == 0:
        problems.append("a evidência de coleta não contém nenhum node id")
    if summary.collected is not None and summary.total != summary.collected:
        problems.append(f"resultado incompleto: collected={summary.collected}, junit_total={summary.total}")
    if summary.failures or summary.errors:
        problems.append(f"failures={summary.failures}, errors={summary.errors}")
    if summary.unexpected_skips:
        problems.append(
            "skip ordinário em lane live; somente companions MAEZO_CHAOS_MUTATE são aceitos:\n  "
            + "\n  ".join(summary.unexpected_skips)
        )
    if problems:
        raise ValueError("; ".join(problems))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument("--collection", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = validate_junit(args.junit, args.collection)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"[check-live-junit] FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "[check-live-junit] PASS: "
        f"collected={summary.collected}, total={summary.total}, executed={summary.executed}, "
        f"mutation_skipped={len(summary.mutation_skipped)}, "
        f"xfailed={len(summary.xfailed)}, failures=0, errors=0"
    )
    for reason in (*summary.mutation_skipped, *summary.xfailed):
        print(f"[check-live-junit] reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
