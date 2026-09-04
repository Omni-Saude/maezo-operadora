"""The agent-memory declaration fence (CC-07, Agent Fleet Audit).

WHY THIS FILE EXISTS. `mcp_memory.server.MemoryServer` fails closed on both of its tools
(GAP-DU-01-a): `store_episodic` always raises `EpisodicMemoryUnavailableError`
(`REASON_EPISODIC_SCHEMA_DRIFT`) and `recall_semantic` always raises
`SemanticMemoryUnavailableError` (`REASON_SEMANTIC_SEARCH_NOT_WIRED`) — there is no migration
this module can honestly write to and no embedding path wired (see `mcp_memory/server.py`'s
module docstring). Despite that, every `spec/agents/*/agent.yaml` (10 real agents +
`_template`) used to declare `memory: {episodic: true, semantic: true}` — a documented
capability with NO live side, CC-07 of the fleet audit. This is exactly the "declared but
structurally unreachable" defect class `test_alert_metrics_fence.py` polices for
alerts/metrics: a capability flag that reads as "on" but is provably always refused underneath
is worse than an honest "off", because a caller has no way to tell the two apart from the
`agent.yaml` alone.

The fix is a documentation-only desdeclaration (`episodic: false` / `semantic: false`, both
commented "reabilitar quando GAP-DU-01-b ligar pgvector (ADR proposto 0043 — memoria
ativar-ou-aposentar)") — reactivation is gated on GAP-DU-01-b
(an owner decision: wire a real pgvector/embedding path onto `agent_memory`, or retire the
server outright). This fence makes the honest state a structural invariant instead of a
one-time cleanup: it fails the moment either side of the pair drifts.

WHAT THIS FENCE ASSERTS, and what it deliberately does not:
  1. `MemoryServer.store_episodic`/`recall_semantic` refuse UNCONDITIONALLY today (the premise
     the desdeclaration depends on) — proven live, not asserted from a comment. If a future
     change wires a real backing store, THIS assertion breaks first, which is the signal that
     every `agent.yaml`'s `memory: {episodic: true, semantic: true}` may become true again.
  2. No `spec/agents/*/agent.yaml` (including `_template`, the seed every new agent copies)
     declares `memory.episodic: true` or `memory.semantic: true` while (1) holds.
  3. Every such file's `memory:` block is exactly `{episodic: false, semantic: false}` — not
     merely "not true" (e.g. accidentally omitted, which `AgentDefinition.memory` would treat
     as `{}`, `.get("episodic")` returning `None`, itself falsy but silently different from a
     deliberate `false`).
It does NOT assert anything about `tools:`/`autonomy_actions:` still naming
`mcp-memory.read_write`/`read_write_memory` — that tool-id declaration is a SEPARATE, deliberate
exception the `effect-chokepoint-fence` gate depends on staying declared by at least one agent
(`scripts/ci/check_effect_chokepoint_fence.py`'s `_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS` /
"the exception has gone stale" check, mirrored in `src/maezo/gateway/effect_classes.py`'s
"KNOWN GAP, DISCLOSED" docstring) — removing it from every agent would trade one drift for a
different, gate-breaking one. Only the `memory:` capability flags are this fence's concern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from maezo.tools.mcp_memory.server import (
    EpisodicMemoryUnavailableError,
    MemoryServer,
    SemanticMemoryUnavailableError,
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_ROOT: Final[Path] = _REPO_ROOT / "spec" / "agents"


def _agent_yaml_paths() -> list[Path]:
    paths = sorted(_AGENTS_ROOT.glob("*/agent.yaml"))
    assert paths, f"no agent.yaml found under {_AGENTS_ROOT} — glob is broken, not the fixture"
    return paths


# ---------------------------------------------------------------------------
# 1) The premise: MemoryServer refuses unconditionally, live (not from a comment).
# ---------------------------------------------------------------------------


async def test_memory_server_store_episodic_refuses_unconditionally() -> None:
    """`store_episodic` must refuse for ANY structurally-valid input — the desdeclaration below
    is honest only as long as there is no path through this method that succeeds."""
    server = MemoryServer()
    with pytest.raises(EpisodicMemoryUnavailableError):
        await server.store_episodic("agent-1", {"type": "decision", "payload": {}})


async def test_memory_server_recall_semantic_refuses_unconditionally() -> None:
    """`recall_semantic` must refuse for ANY query — same rationale as the test above."""
    server = MemoryServer()
    with pytest.raises(SemanticMemoryUnavailableError):
        await server.recall_semantic("autorizacao similar")


# ---------------------------------------------------------------------------
# 2) The consequence: no agent.yaml may claim a live memory side while (1) holds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _agent_yaml_paths(), ids=lambda p: p.parent.name)
def test_agent_yaml_does_not_declare_live_memory(path: Path) -> None:
    """CC-07: `memory.episodic`/`memory.semantic` must both be `false` while `MemoryServer`
    refuses fail-closed (proven by the two tests above) — reactivation is GAP-DU-01-b."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    memory = data.get("memory") or {}
    assert memory.get("episodic") is not True, (
        f"{path}: memory.episodic=true but MemoryServer.store_episodic always refuses "
        "(GAP-DU-01-a) — desdeclare until GAP-DU-01-b wires a real backing store"
    )
    assert memory.get("semantic") is not True, (
        f"{path}: memory.semantic=true but MemoryServer.recall_semantic always refuses "
        "(GAP-DU-01-a) — desdeclare until GAP-DU-01-b wires a real backing store"
    )


@pytest.mark.parametrize("path", _agent_yaml_paths(), ids=lambda p: p.parent.name)
def test_agent_yaml_memory_block_is_the_explicit_honest_pair(path: Path) -> None:
    """Stronger than "not true": every `memory:` block is exactly `{episodic, semantic}` both
    `False` — catches a silently-omitted key (which reads as `None`, not a deliberate `false`)
    as well as a stray third key that would need its own review."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    memory = data.get("memory") or {}
    assert memory == {"episodic": False, "semantic": False}, (
        f"{path}: memory block is {memory!r}, expected the explicit desdeclared pair "
        "{'episodic': False, 'semantic': False} (CC-07)"
    )
