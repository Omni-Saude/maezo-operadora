"""BPMN artifact validation (`spec/processes/bpmn/*.bpmn`).

Scope (T2.1 — see docs/prompts/V2-COMPLETION-PLAN.md):
- XML well-formed;
- root element is `bpmn:definitions` in the BPMN 2.0 MODEL namespace;
- every `bpmn:process` declares a non-empty `id`;
- every `businessRuleTask` collects its `camunda:decisionRef` — the BPMN side
  of the BPMN -> DMN cross-reference checked in `crossref.py`;
- existing process diagrams have complete per-plane coverage and finite geometry
  (E01); repository tests also require diagrams, unlike DI-less parser snippets.

A file that fails to parse, or whose root is not `bpmn:definitions`, cannot
be reasoned about further — `validate_file` reports the error and returns
`None` rather than guessing at partial content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from xml.etree.ElementTree import Element

from ._loaders import ParseError, load_xml
from .result import Report

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"

# Flow nodes visible in the current SP-OP foundation. Data/label quality and
# connector routing remain modeler review concerns, not execution validation.
_SHAPE_TAGS = frozenset(
    f"{{{BPMN_NS}}}{tag}"
    for tag in (
        "startEvent",
        "endEvent",
        "intermediateCatchEvent",
        "intermediateThrowEvent",
        "boundaryEvent",
        "task",
        "serviceTask",
        "userTask",
        "businessRuleTask",
        "manualTask",
        "scriptTask",
        "sendTask",
        "receiveTask",
        "callActivity",
        "subProcess",
        "exclusiveGateway",
        "inclusiveGateway",
        "parallelGateway",
        "complexGateway",
        "eventBasedGateway",
    )
)


def _visible_elements(scope: Element, collapsed: set[str | None]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for element in scope:
        eid = element.get("id", "")
        if element.tag in _SHAPE_TAGS:
            expected[eid] = "BPMNShape"
        elif element.tag == _q("sequenceFlow"):
            expected[eid] = "BPMNEdge"
        if element.tag == _q("subProcess") and eid not in collapsed:
            expected.update(_visible_elements(element, collapsed))
    return expected


def validate_di(root: Element, path: Path, report: Report) -> None:
    """Check process-plane DI coverage and finite geometry in an existing diagram.

    This is the repository's process-plane convention, not a general BPMN
    modeler: collaboration planes and separately expanded subprocess planes need
    explicit support before adoption. Collapsed subprocess contents are hidden.
    Callers can require diagrams; validate_file also permits DI-less XML snippets.
    """
    planes = root.findall(f".//{{{BPMNDI_NS}}}BPMNPlane")
    if not planes:
        report.error(path, "BPMNDI: no process plane declared")
        return
    processes = {p.get("id", ""): p for p in root.findall(_q("process"))}
    seen: set[str] = set()
    for plane in planes:
        ref = plane.get("bpmnElement", "")
        process = processes.get(ref)
        if process is None:
            report.error(path, f"BPMNDI: plane references unknown process '{ref}'")
            continue
        if ref in seen:
            report.error(path, f"BPMNDI: duplicate plane for process '{ref}'")
        seen.add(ref)
        shapes = plane.findall(f"{{{BPMNDI_NS}}}BPMNShape")
        collapsed = {s.get("bpmnElement") for s in shapes if s.get("isExpanded") != "true"}
        expected = _visible_elements(process, collapsed)
        actual: set[str] = set()
        for item in plane:
            kind = item.tag.removeprefix(f"{{{BPMNDI_NS}}}")
            if kind not in {"BPMNShape", "BPMNEdge"}:
                continue
            eid = item.get("bpmnElement", "")
            if eid in actual:
                report.error(path, f"BPMNDI: duplicate representation for '{eid}'")
            actual.add(eid)
            if expected.get(eid) != kind:
                report.error(path, f"BPMNDI: {kind} '{eid}' is not in process plane '{ref}'")
            geometry = (
                item.findall(f"{{{DC_NS}}}Bounds")
                if kind == "BPMNShape"
                else item.findall(f"{{{DI_NS}}}waypoint")
            )
            if (kind == "BPMNShape" and len(geometry) != 1) or (kind == "BPMNEdge" and len(geometry) < 2):
                report.error(path, f"BPMNDI: '{eid}' has missing bounds/waypoints")
            for point in geometry:
                names = ("x", "y", "width", "height") if kind == "BPMNShape" else ("x", "y")
                try:
                    values = {name: float(point.get(name, "")) for name in names}
                    valid = all(isfinite(v) for v in values.values()) and all(
                        values[name] > 0 for name in names if name in {"width", "height"}
                    )
                except ValueError:
                    valid = False
                if not valid:
                    report.error(path, f"BPMNDI: '{eid}' has invalid geometry")
        for eid in sorted(expected.keys() - actual):
            report.error(path, f"BPMNDI: missing {expected[eid]} for '{eid}' in '{ref}'")
    for ref in sorted(set(processes) - seen):
        report.error(path, f"BPMNDI: missing plane for process '{ref}'")


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

    if root.find(f"{{{BPMNDI_NS}}}BPMNDiagram") is not None:
        validate_di(root, path, report)

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
