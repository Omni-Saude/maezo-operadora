"""BPMN artifact validation (`spec/processes/bpmn/*.bpmn`).

Scope (T2.1 — see docs/prompts/V2-COMPLETION-PLAN.md):
- XML well-formed;
- root element is `bpmn:definitions` in the BPMN 2.0 MODEL namespace;
- every `bpmn:process` declares a non-empty `id`;
- every `businessRuleTask` collects its `camunda:decisionRef` — the BPMN side
  of the BPMN -> DMN cross-reference checked in `crossref.py`.

A file that fails to parse, or whose root is not `bpmn:definitions`, cannot
be reasoned about further — `validate_file` reports the error and returns
`None` rather than guessing at partial content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._loaders import ParseError, load_xml
from .result import Report

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"


def _q(tag: str) -> str:
    return f"{{{BPMN_NS}}}{tag}"


def _camunda(attr: str) -> str:
    return f"{{{CAMUNDA_NS}}}{attr}"


@dataclass(slots=True)
class BpmnFile:
    """Facts collected from one successfully-parsed `.bpmn` file."""

    path: Path
    process_ids: list[str] = field(default_factory=list)
    decision_refs: set[str] = field(default_factory=set)


def validate_file(path: Path, report: Report) -> BpmnFile | None:
    """Validate one `.bpmn` file, accumulating findings into `report`.

    Returns the collected `BpmnFile` facts (process ids, decisionRefs) even
    when non-fatal errors were reported (e.g. a process missing an id still
    yields its businessRuleTask decisionRefs) — but returns `None` if the
    file could not be parsed at all, or its root is not `bpmn:definitions`.
    """
    try:
        root = load_xml(path)
    except ParseError as exc:
        report.error(path, str(exc))
        return None

    if root.tag != _q("definitions"):
        report.error(
            path,
            "root element is not bpmn:definitions in the BPMN 2.0 MODEL namespace "
            f"(expected '{BPMN_NS}') — got '{root.tag}'",
        )
        return None

    result = BpmnFile(path=path)

    processes = root.findall(_q("process"))
    if not processes:
        report.error(path, "no bpmn:process element declared")

    for process in processes:
        pid = (process.get("id") or "").strip()
        if not pid:
            report.error(path, "bpmn:process is missing a non-empty id")
        else:
            result.process_ids.append(pid)

    # camunda:decisionRef is the BPMN->DMN join key (see crossref.py). Collected
    # from anywhere in the document (root.iter), not just top-level process
    # children, so a businessRuleTask nested in a subProcess is still caught.
    for task in root.iter(_q("businessRuleTask")):
        ref = (task.get(_camunda("decisionRef")) or "").strip()
        if not ref:
            tid = task.get("id", "<no-id>")
            report.error(path, f"businessRuleTask '{tid}' has no camunda:decisionRef")
        else:
            result.decision_refs.add(ref)

    return result
