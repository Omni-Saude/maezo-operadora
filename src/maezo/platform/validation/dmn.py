"""DMN artifact validation (`spec/processes/dmn/*.dmn`).

Scope (T2.1 — see docs/prompts/V2-COMPLETION-PLAN.md):
- XML well-formed;
- root element is `dmn:definitions` in a recognized OMG DMN namespace (the
  repo's DMNs use the 1.3-era `https://www.omg.org/spec/DMN/20191111/MODEL/`
  namespace; the older `http://www.omg.org/spec/DMN/...` prefix is also
  accepted since Camunda/CIB Seven tooling emits both across versions);
- every `dmn:decision` collects its `id` — the DMN side of the BPMN -> DMN
  cross-reference checked in `crossref.py`. Note: a decision's `id` is the
  join key BPMN resolves against, and is **not** required to match the
  `.dmn` file's name (e.g. `reembolso_coverage.dmn` declares the decision id
  `reembolso_admissibility`) — validators must never assume filename == id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._loaders import ParseError, load_xml, local_name
from .result import Report

DMN_NS_PREFIXES = ("https://www.omg.org/spec/DMN/", "http://www.omg.org/spec/DMN/")


def _is_dmn_definitions(tag: str) -> bool:
    if "}" not in tag:
        return False
    ns, _, local = tag.partition("}")
    ns = ns.lstrip("{")
    return local == "definitions" and any(ns.startswith(prefix) for prefix in DMN_NS_PREFIXES)


@dataclass(slots=True)
class DmnFile:
    """Facts collected from one successfully-parsed `.dmn` file."""

    path: Path
    decision_ids: list[str] = field(default_factory=list)


def validate_file(path: Path, report: Report) -> DmnFile | None:
    """Validate one `.dmn` file, accumulating findings into `report`.

    Returns `None` if the file could not be parsed at all, or its root is not
    a recognized `dmn:definitions` element.
    """
    try:
        root = load_xml(path)
    except ParseError as exc:
        report.error(path, str(exc))
        return None

    if not _is_dmn_definitions(root.tag):
        report.error(
            path,
            "root element is not dmn:definitions in a recognized DMN namespace "
            f"(expected a namespace starting with one of {DMN_NS_PREFIXES}) — got '{root.tag}'",
        )
        return None

    result = DmnFile(path=path)

    decisions = [el for el in root.iter() if local_name(el.tag) == "decision"]
    if not decisions:
        report.error(path, "no dmn:decision element declared")

    seen: set[str] = set()
    for decision in decisions:
        did = (decision.get("id") or "").strip()
        if not did:
            report.error(path, "dmn:decision is missing a non-empty id")
            continue
        if did in seen:
            report.error(path, f"duplicate dmn:decision id within this file: '{did}'")
            continue
        seen.add(did)
        result.decision_ids.append(did)

    return result
