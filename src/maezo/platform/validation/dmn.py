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
  `reembolso_admissibility`) — validators must never assume filename == id;
- a `.dmn` directly under `spec/processes/dmn/rco/` (the rco "K1" rules package,
  ADR-0075 §(b)) must declare decision ids prefixed `rco_` — the engine keys a
  decision globally per tenant, so an unprefixed K1 decision would silently become
  a new VERSION of a Maezo decision with the same id.

`list_dmn_files` is the one definition of "which `.dmn` files exist" shared by the
deploy, the validation gate and the repo's fences: the top-level Maezo tables plus
an EXPLICIT second scan of `rco/` — never a general `rglob`, so a stray subfolder
stays out of every scan point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._loaders import ParseError, load_xml, local_name
from .result import Report

DMN_NS_PREFIXES = ("https://www.omg.org/spec/DMN/", "http://www.omg.org/spec/DMN/")

#: Subfolder of `spec/processes/dmn/` that receives the rco K1 package (ADR-0075 §(b)).
#: Written only by the pin update (`config/integrations/rco/rules.lock.json`).
RCO_SUBDIR = "rco"
#: Mandatory decision-id prefix for every decision under `RCO_SUBDIR` (ADR-0075 §(b)).
RCO_DECISION_ID_PREFIX = "rco_"


def list_dmn_files(dmn_dir: Path) -> list[Path]:
    """Every deployable `.dmn` under `dmn_dir`: top-level (Maezo) first, then `rco/` (K1).

    Each group is sorted. Only files DIRECTLY inside `dmn_dir` and `dmn_dir/rco` are
    returned — the second scan is explicit, so any other subfolder (or a folder nested
    inside `rco/`) is invisible here, exactly as it was before `rco/` existed.
    """
    if not dmn_dir.is_dir():
        return []
    rco_dir = dmn_dir / RCO_SUBDIR
    rco_files = sorted(rco_dir.glob("*.dmn")) if rco_dir.is_dir() else []
    return [*sorted(dmn_dir.glob("*.dmn")), *rco_files]


def is_rco_dmn(path: Path) -> bool:
    """True when `path` sits directly in a `dmn/rco/` folder (the K1 package)."""
    return path.parent.name == RCO_SUBDIR and path.parent.parent.name == "dmn"


def deployment_resource_name(path: Path) -> str:
    """Engine resource name for a BPMN/DMN artifact.

    Top-level artifacts keep their bare file name (unchanged, so duplicate filtering
    of the existing deployments is not disturbed). A K1 table is named by its path
    relative to `dmn/` — `rco/<file>.dmn` — so it can never collide with a Maezo table
    of the same file name (ADR-0075 §(b)).
    """
    if is_rco_dmn(path):
        return f"{RCO_SUBDIR}/{path.name}"
    return path.name


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
        if is_rco_dmn(path) and not did.startswith(RCO_DECISION_ID_PREFIX):
            report.error(
                path,
                f"dmn:decision id '{did}' under spec/processes/dmn/{RCO_SUBDIR}/ must start with "
                f"'{RCO_DECISION_ID_PREFIX}' (ADR-0075 §(b)): the engine keys decisions globally per "
                "tenant, so an unprefixed K1 decision would become a new version of a Maezo decision",
            )
        result.decision_ids.append(did)

    return result
