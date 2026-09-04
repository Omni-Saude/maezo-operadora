"""The agent.yaml tool/action wiring fence (CC-05, Agent Fleet Audit).

WHY THIS FILE EXISTS. `spec/agents/<agent>/agent.yaml` is the single source of truth for what
an agent DOES (AGENTS.md) — `tools:`/`autonomy_actions:` are supposed to be the allowlist a
node in `graph.py` actually exercises. Because every v2 `agent.yaml` was ported from the v1
donor's superset, five agents kept declaring ids no node in their v2 graph ever calls: helena
and rafael both declared `mcp-cibseven.get_process_status`/`query_process_status`; gustavo also
kept `mcp-cibseven.correlate_process_message`/`correlate_process_message`
(HEL-01/RAF-03/GUS-02, Agent Fleet Audit). A declared-but-unwired tool is a documented capability
+ attack surface + autonomy grant that nothing exercises — worse than not declaring it, because a
reader has no way to tell "wired" from "aspirational" apart from actually reading every node.

THE FIX (CC-05): `mcp-cibseven.get_process_status`/`query_process_status` were REMOVED from
`spec/agents/{helena,rafael}/agent.yaml` (both the `tools:` id and the paired `autonomy_actions:`
id), each with a `# ... removido: ...` comment naming the finding. gustavo's two dead
`mcp-cibseven.*` declarations (`get_process_status` AND `correlate_process_message`) were
DELIBERATELY KEPT instead of removed: `tests/unit/gateway/test_effect_enforcement.py::
test_every_catalogued_tool_id_is_declared_by_some_agent` asserts `declared >= effect_classes.
CATALOGUED_TOOL_IDS` — the catalogue (`src/maezo/gateway/effect_classes.py`, OWNER-GATED/`src`,
out of this WP's charter) still lists both ids as real operations, and gustavo was the LAST
agent.yaml declaring either (helena/rafael/andre/carolina/valentina all either never declared
them or had already dropped them before this WP). Removing them from gustavo too would have
flipped that catalogue completeness gate RED — a real, pre-existing test this WP is not allowed
to break, not a fence this WP owns. So gustavo's `tools:`/`autonomy_actions:` keep both ids with
an OWNER-GATED comment (distinct from andre/carolina/valentina's plain "not declared yet" note —
gustavo's IS declared, on purpose, only to hold the catalogue's completeness invariant). This is
the same shape as the `mcp-memory.read_write` exception below, for the same reason: a `src/`-side
completeness gate outranks per-agent dead-code hygiene when the two collide, and the collision is
recorded rather than silently resolved. `mcp-memory.read_write`/`read_write_memory` is the OTHER
deliberate exception left standing everywhere it already was: `scripts/ci/
check_effect_chokepoint_fence.py`'s §8.5 item 2 hard-fails if NO agent.yaml declares
`mcp-memory.read_write` (it is the sole disclosed tool-id exception on that fence) — removing it
fleet-wide would trade a "dead tool" drift for a "the CI fence's own disclosed exception has gone
stale" drift. `test_agent_memory_declaration_fence.py` (CC-07) already fences the SEPARATE
`memory: {episodic, semantic}` capability flags; this file does not duplicate that.

WHAT THIS FENCE ASSERTS. For every real `spec/agents/*/agent.yaml` (10, `_template` excluded —
it is a contract skeleton, not a shipped agent):
  1. Every declared `tools[]` id is either (a) exercised by a live call in that agent's own
     `graph.py`/`adapters.py`/`delegation.py` (resolved through `_TOOL_WIRE_ALIASES` below,
     because several agents legitimately call the tool through an indirection —
     `start_process_idempotent()` for `mcp-cibseven.start_process`, `WhatsAppSender.send()` for
     `mcp-whatsapp.send_message` — a literal-method-name grep alone gives false negatives), or
     (b) named in the CLOSED inventory `_DECLARED_NOT_WIRED_TOOLS` below with a one-line reason.
  2. `_DECLARED_NOT_WIRED_TOOLS` cannot silently grow or shrink: every `(agent, tool)` key in it
     must still be BOTH declared in that agent's yaml AND unwired in that agent's code (adoption
     of a dead tool, or reintroducing a tool without deleting its inventory entry, fails loudly);
     and no declared+unwired pair may exist that ISN'T in the inventory (planting a new dead tool
     without classifying it fails loudly — the CC-05 "sonda").
  3. Same two checks for `autonomy_actions[]`, restricted to the subset that has a natural 1:1
     tool binding (`_ACTION_TOOL_MAP`) — a `query_decision_engine` action is exactly as wired as
     its `mcp-dmn.evaluate` tool. Actions with no such binding (`informational_response`,
     `reminders_nudges`, `ans_official_submission`, `nip_response` — process/business outcomes
     gated by BPMN/DMN, not a single tool call) are out of this fence's scope by design; they are
     still named in `_ACTION_NOT_TOOL_SHAPED` so the exclusion is itself a checked, closed list
     rather than "everything else silently passes."

WHAT THIS FENCE DELIBERATELY DOES NOT ASSERT (adjacent findings seen, NOT this WP's scope):
  - `beatriz` declares `mcp-fhir.read_patient` but her graph calls the injected
    `PatientSummaryReader.read_patient_summary` instead (already disclosed,
    `agents/beatriz/graph.py:82`); `carolina` declares `mcp-fhir.read_patient_summary` but reuses
    rafael's generic `FhirServerReader.read_patient` (already disclosed,
    `agents/carolina/graph.py:107-109`). Both are pre-existing NAME-SHAPE drift between the
    declared id and the wired method, not "no node calls anything" — they are recorded in
    `_DECLARED_NOT_WIRED_TOOLS` so this fence does not misreport them as dead, but fixing the
    name mismatch itself is out of CC-05's charter (HEL-01/RAF-03/GUS-02/LUC-01/BEA-03 only name
    the `mcp-cibseven.*` and `mcp-memory.read_write` ids).
  - `helena` also declares `mcp-fhir.read_patient_summary`/`read_coverage`/`search_coverage`,
    none of which any node calls (GAP-TRIAGE-5, already disclosed at `graph.py:64-66`). Also
    recorded, also out of CC-05's charter (that finding is HEL-01's FHIR sibling, tracked
    separately by GAP-TRIAGE-5, not by HEL-01/RAF-03/GUS-02).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_ROOT: Final[Path] = _REPO_ROOT / "spec" / "agents"
_SRC_AGENTS_ROOT: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"
_TEMPLATE_DIR_NAME: Final[str] = "_template"

# tool id -> substrings whose presence anywhere in the agent's own graph.py/adapters.py/
# delegation.py counts as "wired". Several are indirections, not the literal action name:
#   - mcp-cibseven.start_process is called via the shared start_process_idempotent() chokepoint
#     (tools/mcp_cibseven/transport.py), never a bare `.start_process(`.
#   - mcp-whatsapp.send_message is called via the `WhatsAppSender.send()` Protocol seam (every
#     agent.yaml comments "alias registrado de send_text"/similar) — never `.send_message(`.
# A tool with an empty alias tuple can never be found wired by this probe (mcp-memory.read_write:
# no agent's graph calls the memory server today — that absence is exactly CC-07's premise).
_TOOL_WIRE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "mcp-dmn.evaluate": ("_dmn.evaluate(",),
    "mcp-cibseven.start_process": ("start_process_idempotent(",),
    "mcp-cibseven.get_process_status": ("get_process_status(",),
    "mcp-cibseven.correlate_process_message": ("correlate_process_message(",),
    "mcp-whatsapp.send_message": ("_whatsapp.send(",),
    "mcp-fhir.read_patient": ("read_patient(",),
    "mcp-fhir.read_patient_summary": ("read_patient_summary(",),
    "mcp-fhir.read_coverage": ("read_coverage(",),
    "mcp-fhir.search_coverage": ("search_coverage(",),
    "mcp-memory.read_write": (),
}

# Closed inventory: every (agent, tool) pair currently declared in that agent's agent.yaml that
# this probe cannot find wired, with why. Both directions are checked (see test bodies below) —
# this is not an allowlist you can silently add to; a mismatch in either direction fails.
_DECLARED_NOT_WIRED_TOOLS: Final[dict[tuple[str, str], str]] = {
    # mcp-memory.read_write: universal disclosed exception. scripts/ci/check_effect_chokepoint_
    # fence.py §8.5 item 2 requires >=1 agent.yaml to keep declaring it; no v2 graph writes
    # memory yet (CC-07 premise, test_agent_memory_declaration_fence.py). Kept in all 10.
    ("andre", "mcp-memory.read_write"): "excecao disclosed do fence (§8.5 item 2); sem no vivo",
    ("beatriz", "mcp-memory.read_write"): (
        "excecao disclosed do fence (§8.5 item 2); sem no vivo (CC-05/BEA-03)"
    ),
    ("carolina", "mcp-memory.read_write"): "excecao disclosed do fence (§8.5 item 2); sem no vivo",
    ("fernando", "mcp-memory.read_write"): "excecao disclosed do fence (§8.5 item 2); sem no vivo",
    ("gustavo", "mcp-memory.read_write"): (
        "excecao disclosed do fence (§8.5 item 2); sem no vivo (CC-05/GUS-02)"
    ),
    ("helena", "mcp-memory.read_write"): (
        "excecao disclosed do fence (§8.5 item 2); sem no vivo (CC-05/HEL-01)"
    ),
    ("lucas", "mcp-memory.read_write"): (
        "excecao disclosed do fence (§8.5 item 2); sem no vivo (CC-05/LUC-01)"
    ),
    ("marina", "mcp-memory.read_write"): "excecao disclosed do fence (§8.5 item 2); sem no vivo",
    ("rafael", "mcp-memory.read_write"): (
        "excecao disclosed do fence (§8.5 item 2); sem no vivo (CC-05/RAF-03b)"
    ),
    ("valentina", "mcp-memory.read_write"): "excecao disclosed do fence (§8.5 item 2); sem no vivo",
    # gustavo: kept ON PURPOSE (not "not yet wired", but "must stay declared somewhere") — see
    # test_effect_enforcement.py::test_every_catalogued_tool_id_is_declared_by_some_agent, which
    # asserts every effect_classes.CATALOGUED_TOOL_IDS entry is declared by >=1 agent.yaml.
    # gustavo was the last declarer of both ids after helena/rafael's CC-05 cleanup; removing
    # them here too would flip that catalogue-completeness gate RED. OWNER-GATED: the catalogue
    # itself lives in src/maezo/gateway/effect_classes.py, out of this WP's charter.
    ("gustavo", "mcp-cibseven.get_process_status"): (
        "OWNER-GATED: unico agent.yaml que ainda declara o id, exigido por "
        "test_every_catalogued_tool_id_is_declared_by_some_agent; sem no vivo (CC-05/GUS-02)"
    ),
    ("gustavo", "mcp-cibseven.correlate_process_message"): (
        "OWNER-GATED: unico agent.yaml que ainda declara o id, exigido por "
        "test_every_catalogued_tool_id_is_declared_by_some_agent; sem no vivo (CC-05/GUS-02)"
    ),
    # Pre-existing declared-id vs wired-method NAME-SHAPE drift (disclosed in each graph's own
    # docstring long before this WP) — adjacent to, but out of, CC-05's charter.
    ("beatriz", "mcp-fhir.read_patient"): (
        "grafo chama PatientSummaryReader.read_patient_summary "
        "(shape drift disclosed graph.py:82); fora do escopo BEA-03"
    ),
    ("carolina", "mcp-fhir.read_patient_summary"): (
        "grafo reusa FhirServerReader.read_patient "
        "(shape drift disclosed graph.py:107-109); fora do escopo CC-05"
    ),
    ("helena", "mcp-fhir.read_patient_summary"): (
        "nao wireado (GAP-TRIAGE-5, disclosed graph.py:64-66); fora do escopo HEL-01"
    ),
    ("helena", "mcp-fhir.read_coverage"): (
        "nao wireado (GAP-TRIAGE-5, disclosed graph.py:64-66); fora do escopo HEL-01"
    ),
    ("helena", "mcp-fhir.search_coverage"): (
        "nao wireado (GAP-TRIAGE-5, disclosed graph.py:64-66); fora do escopo HEL-01"
    ),
}

# autonomy_action -> the single tool id whose wiring status it inherits. Only actions with a
# natural 1:1 tool binding are covered — see _ACTION_NOT_TOOL_SHAPED for the rest.
_ACTION_TOOL_MAP: Final[dict[str, str]] = {
    "query_decision_engine": "mcp-dmn.evaluate",
    "start_compliance_process": "mcp-cibseven.start_process",
    "query_process_status": "mcp-cibseven.get_process_status",
    "correlate_process_message": "mcp-cibseven.correlate_process_message",
    "read_write_memory": "mcp-memory.read_write",
    # "read_phi_data" is resolved specially in _action_tool(): its bound tool is whichever
    # mcp-fhir.* id the agent declares, not a fixed name — see that function.
    "send_beneficiary_message": "mcp-whatsapp.send_message",
}

# Actions with no 1:1 tool call — process/business outcomes gated by BPMN/DMN human tasks, not a
# single client method this probe can grep for. Closed on purpose: an action id that is neither
# here nor in _ACTION_TOOL_MAP fails the test (forces classification, see test bodies).
_ACTION_NOT_TOOL_SHAPED: Final[frozenset[str]] = frozenset(
    {
        "informational_response",
        "reminders_nudges",
        "ans_official_submission",
        "nip_response",
    }
)


def _real_agent_dirs() -> list[Path]:
    dirs = sorted(
        p
        for p in _AGENTS_ROOT.iterdir()
        if p.is_dir() and p.name != _TEMPLATE_DIR_NAME and (p / "agent.yaml").exists()
    )
    assert dirs, f"no agent directories found under {_AGENTS_ROOT} — glob is broken, not the fixture"
    return dirs


def _agent_source_text(agent_dir: Path) -> str:
    """Concatenated text of every hand-authored module in one agent's RUNTIME package
    (`src/maezo/agents/<id>/graph.py`/`adapters.py`/`delegation.py` — never `__init__.py`, which
    only re-exports). `agent_dir` is the `spec/agents/<id>` contract directory; the code that
    actually wires tools lives under `src/maezo/agents/<id>`, a sibling tree keyed by the same
    agent id."""
    src_dir = _SRC_AGENTS_ROOT / agent_dir.name
    assert src_dir.is_dir(), f"no src/maezo/agents/{agent_dir.name}/ — spec/code id mismatch"
    text = ""
    for py_file in sorted(src_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        text += py_file.read_text(encoding="utf-8")
    return text


def _load_agent_yaml(agent_dir: Path) -> dict:
    data = yaml.safe_load((agent_dir / "agent.yaml").read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict)
    return data


def _tool_is_wired(tool: str, source_text: str) -> bool:
    aliases = _TOOL_WIRE_ALIASES.get(tool)
    if aliases is None:
        # Unknown tool id (not in our alias table at all) — the probe cannot judge it silently.
        # Treated as "not wired" so it MUST be classified in _DECLARED_NOT_WIRED_TOOLS, which
        # itself fails the test if there is no matching entry (see test below).
        return False
    return any(alias in source_text for alias in aliases)


# ---------------------------------------------------------------------------
# 1) tools[]: every declared id is wired OR in the closed, exact inventory.
# ---------------------------------------------------------------------------


def test_every_declared_tool_is_wired_or_classified() -> None:
    unclassified: list[str] = []
    for agent_dir in _real_agent_dirs():
        agent = agent_dir.name
        data = _load_agent_yaml(agent_dir)
        source_text = _agent_source_text(agent_dir)
        for tool in data.get("tools") or []:
            if _tool_is_wired(tool, source_text):
                continue
            if (agent, tool) not in _DECLARED_NOT_WIRED_TOOLS:
                unclassified.append(
                    f"{agent}: {tool!r} is declared, unwired, and NOT in _DECLARED_NOT_WIRED_TOOLS"
                )
    assert not unclassified, (
        "agent.yaml declares a tool no node exercises, with no recorded reason (CC-05 sonda — "
        "either wire the tool, remove the declaration, or add a classified entry):\n"
        + "\n".join(unclassified)
    )


def test_declared_not_wired_tools_inventory_has_no_stale_entries() -> None:
    """Every inventory key must still be BOTH declared AND unwired — a pair that got wired (good
    news) or removed from the yaml (also good news) must be deleted from the inventory, or this
    fence stops proving what it claims to prove."""
    stale: list[str] = []
    agent_dirs = {p.name: p for p in _real_agent_dirs()}
    for (agent, tool), _reason in _DECLARED_NOT_WIRED_TOOLS.items():
        agent_dir = agent_dirs.get(agent)
        if agent_dir is None:
            stale.append(f"{agent}: agent directory does not exist anymore")
            continue
        data = _load_agent_yaml(agent_dir)
        declared = tool in (data.get("tools") or [])
        if not declared:
            stale.append(
                f"{agent}: {tool!r} is no longer declared in agent.yaml — remove the inventory entry"
            )
            continue
        if _tool_is_wired(tool, _agent_source_text(agent_dir)):
            stale.append(f"{agent}: {tool!r} is now wired by a live node — remove the inventory entry")
    assert not stale, "stale _DECLARED_NOT_WIRED_TOOLS entries:\n" + "\n".join(stale)


# ---------------------------------------------------------------------------
# 2) autonomy_actions[]: same two checks, for the 1:1-tool-shaped subset only.
# ---------------------------------------------------------------------------


def _action_tool(agent_yaml: dict, action: str) -> str | None:
    """The tool id an action's wiring is judged against, or None if the action has no natural
    1:1 tool (informational_response et al. — see _ACTION_NOT_TOOL_SHAPED)."""
    if action == "read_phi_data":
        for tool in agent_yaml.get("tools") or []:
            if tool.startswith("mcp-fhir."):
                return tool
        return None  # no mcp-fhir.* tool declared at all — nothing to judge wiring against
    return _ACTION_TOOL_MAP.get(action)


def test_every_declared_autonomy_action_is_wired_or_classified() -> None:
    unclassified: list[str] = []
    for agent_dir in _real_agent_dirs():
        agent = agent_dir.name
        data = _load_agent_yaml(agent_dir)
        source_text = _agent_source_text(agent_dir)
        for action in data.get("autonomy_actions") or []:
            if action in _ACTION_NOT_TOOL_SHAPED:
                continue
            tool = _action_tool(data, action)
            if tool is None:
                if action not in _ACTION_TOOL_MAP:
                    unclassified.append(
                        f"{agent}: autonomy_action {action!r} has no _ACTION_TOOL_MAP entry and is "
                        "not in _ACTION_NOT_TOOL_SHAPED — classify it"
                    )
                continue
            if _tool_is_wired(tool, source_text):
                continue
            if (agent, tool) not in _DECLARED_NOT_WIRED_TOOLS:
                unclassified.append(
                    f"{agent}: autonomy_action {action!r} maps to unwired tool {tool!r}, which is "
                    "NOT in _DECLARED_NOT_WIRED_TOOLS"
                )
    assert not unclassified, (
        "agent.yaml declares an autonomy_action whose bound tool no node exercises, with no "
        "recorded reason (CC-05 sonda):\n" + "\n".join(unclassified)
    )


def test_action_tool_map_and_not_tool_shaped_are_disjoint_and_cover_all_declared_actions() -> None:
    """Every autonomy_action actually declared anywhere must be classified as either 1:1-tool-
    shaped (_ACTION_TOOL_MAP, or the special-cased read_phi_data) or explicitly not tool-shaped
    (_ACTION_NOT_TOOL_SHAPED) — no third, silent category."""
    overlap = _ACTION_NOT_TOOL_SHAPED & set(_ACTION_TOOL_MAP)
    assert not overlap, f"actions classified both ways: {sorted(overlap)}"

    declared_actions: set[str] = set()
    for agent_dir in _real_agent_dirs():
        data = _load_agent_yaml(agent_dir)
        declared_actions.update(data.get("autonomy_actions") or [])

    known = _ACTION_NOT_TOOL_SHAPED | set(_ACTION_TOOL_MAP) | {"read_phi_data"}
    unknown = declared_actions - known
    assert not unknown, f"autonomy_action(s) declared but never classified: {sorted(unknown)}"


@pytest.mark.parametrize("agent_dir", _real_agent_dirs(), ids=lambda p: p.name)
def test_agent_yaml_is_readable_and_non_empty(agent_dir: Path) -> None:
    """Sanity gate for the fixtures above: a broken/empty agent.yaml should fail loudly here,
    not silently make every other test in this file vacuously pass."""
    data = _load_agent_yaml(agent_dir)
    assert data.get("id") == agent_dir.name
