"""Static regression pin for F-E01 (CA_2 v4) — the U4 parallel-join deadlock class.

F-E01 (JOURNEYS-COMPLETENESS-2026-10): in ``CA_2_UTILIZACAO_v3.bpmn`` the interruptive
timer boundary ``U4_B_Pesquisa`` AND its host ``U4_T_Pesquisa`` both fed the parallel
gateway ``U4_GW_Join`` directly (3 inbounds, only 2 tokenizable) — the canonical
rule-3b deadlock (bpmn-camunda-authoring §3b): each execution of that parallel branch
produces a token via task completion OR via the boundary, never both, so a join with
N+1 inbounds waits forever and every U4 instance hangs before liquidação. v4 fixed it
by inserting the exclusive escape gateway ``U4_GW_EscapePesquisa``; this file pins the
fix so no future edit can silently reintroduce the pattern.

What is asserted (static ElementTree only — no engine, no new dependencies):

(a) ``U4_GW_Join`` has exactly 2 inbound sequenceFlows, and the declared
    ``<bpmn:incoming>`` children match the actual inbound flow set exactly (the
    ghost-declaration drift class is invisible to ref-checking alone).

(b) File-wide rule-3b sweep: for every INTERRUPTIVE boundary event B attached to task T
    and every parallel join J (in-degree >= 2): if both B and T can reach J through
    sequenceFlows, then an exclusiveGateway escape X must separate them from J — i.e.
    there is an exclusiveGateway whose removal makes J unreachable from BOTH B and T
    (both branch outcomes converge at X and exactly one flow leaves toward the join).
    This is the canonical rule-3b convergence property, not a literal "boundary must
    never reach a join": in the FIXED v4 topology both U4_T_Pesquisa and U4_B_Pesquisa
    still reach U4_GW_Join, but only through the escape, which is what makes the join
    receive exactly 2 tokens per branch execution.

(c) U4-specific pin: the escape ``U4_GW_EscapePesquisa`` exists, is an exclusiveGateway,
    and is that separating gateway for the (U4_B_Pesquisa, U4_T_Pesquisa, U4_GW_Join)
    triple.

Evidence anchors: FINDINGS.md F-E01 · COMPARISON-V3-V4.md §1 "IA-01 FIXED" ·
INVENTORY-A-V4-BPMN.md IA-01 entry (escape verified 2-in/1-out, join back to canonical
2 inbounds). Any failure here means the v4 deadlock fix regressed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
NS = {"bpmn": BPMN_NS}

#: File under pin. Tracked on main; a missing file is itself a regression.
BPMN_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
)

JOIN_ID = "U4_GW_Join"
ESCAPE_ID = "U4_GW_EscapePesquisa"
HOST_ID = "U4_T_Pesquisa"
BOUNDARY_ID = "U4_B_Pesquisa"


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _load() -> ET.Element:
    assert BPMN_PATH.is_file(), f"missing BPMN under pin: {BPMN_PATH}"
    return ET.parse(BPMN_PATH).getroot()


def _sequence_flow_index(root: ET.Element) -> dict[str, list[tuple[str, str]]]:
    """sourceRef -> [(flowId, targetRef), ...] over every process in the file."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for flow in root.iter(f"{{{BPMN_NS}}}sequenceFlow"):
        adj.setdefault(flow.get("sourceRef", ""), []).append((flow.get("id", ""), flow.get("targetRef", "")))
    return adj


def _reachable(adj: dict[str, list[tuple[str, str]]], start: str, skip: str = "") -> set[str]:
    """Nodes reachable from ``start`` via sequenceFlows. ``skip`` removes one node from
    the graph (cut-vertex probe for the escape-gateway property)."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for _, target in adj.get(node, []):
            if target == skip:
                continue
            stack.append(target)
    return seen


def _inbound_flows(root: ET.Element, node_id: str) -> set[str]:
    return {
        flow.get("id", "")
        for flow in root.iter(f"{{{BPMN_NS}}}sequenceFlow")
        if flow.get("targetRef") == node_id
    }


def _parallel_joins(root: ET.Element) -> dict[str, int]:
    """parallelGateway id -> inbound count (joins only: in-degree >= 2)."""
    inbound = {}
    for flow in root.iter(f"{{{BPMN_NS}}}sequenceFlow"):
        inbound.setdefault(flow.get("targetRef", ""), 0)
        inbound[flow.get("targetRef", "")] += 1
    return {
        gw.get("id", ""): inbound.get(gw.get("id", ""), 0)
        for gw in root.iter(f"{{{BPMN_NS}}}parallelGateway")
        if inbound.get(gw.get("id", ""), 0) >= 2
    }


def _interruptive_boundaries(root: ET.Element) -> dict[str, str]:
    """boundaryEvent id -> host id, for interrupting boundaries only.

    ``cancelActivity`` defaults to true per the BPMN spec; v4 made the two interruptive
    timers explicit (CORP-2.2), so both spellings must be honored here.
    """
    out: dict[str, str] = {}
    for boundary in root.iter(f"{{{BPMN_NS}}}boundaryEvent"):
        cancel = boundary.get("cancelActivity", "true")
        if cancel.strip().lower() == "false":
            continue
        host = boundary.get("attachedToRef")
        if host:
            out[boundary.get("id", "")] = host
    return out


def test_u4_join_has_exactly_two_inbound_sequence_flows() -> None:
    """(a) F-E01 pin: U4_GW_Join keeps the canonical 2 inbounds — no third branch, no
    direct boundary feed, and declared <incoming> children match the real flows."""
    root = _load()
    inbound = _inbound_flows(root, JOIN_ID)
    assert len(inbound) == 2, (
        f"{JOIN_ID} must have exactly 2 inbound sequenceFlows (canonical rule-3b "
        f"convergence), found {len(inbound)}: {sorted(inbound)}"
    )
    join = next(gw for gw in root.iter(f"{{{BPMN_NS}}}parallelGateway") if gw.get("id") == JOIN_ID)
    declared = {el.text for el in join.findall(f"{{{BPMN_NS}}}incoming")}
    assert declared == inbound, (
        f"{JOIN_ID} declared <incoming> {sorted(declared)} drifts from actual inbound "
        f"flows {sorted(inbound)} (ghost-declaration class)"
    )


def test_no_interruptive_boundary_feeds_a_parallel_join_without_an_escape() -> None:
    """(b) File-wide rule-3b sweep — the F-E01 deadlock pattern must stay absent."""
    root = _load()
    adj = _sequence_flow_index(root)
    joins = _parallel_joins(root)
    boundaries = _interruptive_boundaries(root)
    escapes: dict[str, set[str]] = {}
    for boundary_id, host_id in boundaries.items():
        for join_id in joins:
            if join_id not in _reachable(adj, boundary_id) or join_id not in _reachable(adj, host_id):
                continue
            separating = {
                gateway.get("id", "")
                for gateway in root.iter(f"{{{BPMN_NS}}}exclusiveGateway")
                if join_id not in _reachable(adj, boundary_id, skip=gateway.get("id", ""))
                and join_id not in _reachable(adj, host_id, skip=gateway.get("id", ""))
            }
            escapes[(boundary_id, host_id, join_id)] = separating
            assert separating, (
                f"rule-3b deadlock: interruptive boundary {boundary_id} (host {host_id}) and "
                f"the host both reach parallel join {join_id} with NO exclusiveGateway escape "
                f"converging the two outcomes (F-E01 pattern); boundary can reach join via: "
                f"{sorted(_reachable(adj, boundary_id) & {join_id})}"
            )
    assert (BOUNDARY_ID, HOST_ID, JOIN_ID) in escapes, (
        f"the pinned F-E01 triple ({BOUNDARY_ID}/{HOST_ID}/{JOIN_ID}) is expected to be "
        "exercised by this sweep; if the U4 topology changed, re-pin deliberately"
    )


def test_u4_escape_gateway_is_the_pinned_convergence_point() -> None:
    """(c) U4-specific pin: the v4 escape gateway exists, is exclusive, and is the
    separating convergence point for the (boundary, host) -> join pair."""
    root = _load()
    escape = [gw for gw in root.iter(f"{{{BPMN_NS}}}exclusiveGateway") if gw.get("id") == ESCAPE_ID]
    assert escape, f"{ESCAPE_ID} (the v4 F-E01 fix) is missing"
    adj = _sequence_flow_index(root)
    assert JOIN_ID not in _reachable(adj, BOUNDARY_ID, skip=ESCAPE_ID), (
        f"without {ESCAPE_ID} the boundary {BOUNDARY_ID} still reaches {JOIN_ID} directly"
    )
    assert JOIN_ID not in _reachable(adj, HOST_ID, skip=ESCAPE_ID), (
        f"without {ESCAPE_ID} the host {HOST_ID} still reaches {JOIN_ID} directly"
    )
    assert JOIN_ID in _reachable(adj, ESCAPE_ID), f"{ESCAPE_ID} must route toward {JOIN_ID}"
