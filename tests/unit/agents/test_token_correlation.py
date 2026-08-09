"""Proves the Tier-2 item 4 fix: `agent_id`/`tenant_id` correlation ids actually flow from a real
agent graph's `.generate()` call site into the seam, not just exist as unused optional kwargs on
`InferenceProvider.generate`'s signature.

Before this fix, `BaseInferenceProvider.generate`/`InferenceProvider.generate` already declared
`agent_id: str | None = None, tenant_id: str | None = None` (pre-built extension point, T8) but
every in-repo agent call site (`agents/*/graph.py`) called `self._llm.generate(prompt, phi=True)`
without ever passing them — so `llm_token_usage` log events always carried `agent_id=None,
tenant_id=None` (recon-c §ITEM 4). This test drives two structurally different real graphs
(Helena — a classifier-family agent; Andre — a dossier/DMN-routing agent) through their public
node methods with a capturing fake inference double and asserts BOTH ids arrive at `generate()`
with the right values: the literal per-agent id, and the tenant id carried on `state["tenant_id"]`
(never hardcoded/shared — two different tenant ids across the two calls prove it's read from
state, not a constant)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from maezo.agents.andre.graph import AndreGraph, AndreState
from maezo.agents.helena.graph import HelenaGraph, HelenaState
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _CapturingInference:
    """Records every kwarg a real call site passes to `generate()` (never a real LLM SDK call,
    never network) — the assertion surface for this file. `phi`/`agent_id`/`tenant_id` are
    recorded verbatim so a test can pin exactly what a given call site threaded through."""

    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self._responses: list[str] = list(responses) if responses else []
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append({"prompt": prompt, "phi": phi, "agent_id": agent_id, "tenant_id": tenant_id})
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppSender:
    """Minimal double for Helena's required `whatsapp` seam — never exercised by this file's
    assertions, only needed to satisfy `HelenaGraph.__init__`."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


def _classify_json(**overrides: Any) -> str:
    base = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(overrides)
    return json.dumps(base)


async def test_helena_classify_passes_agent_id_and_state_tenant_id_to_generate() -> None:
    """Helena's `classify` node reaches `_classify_llm` -> `self._llm.generate(...)`
    (helena/graph.py) — must pass `agent_id="helena"` (the literal `source_agent_id`,
    helena/graph.py:645) and `tenant_id=state["tenant_id"]` (never hardcoded)."""
    inference = _CapturingInference([_classify_json()])
    graph = HelenaGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_FakeWhatsAppSender(),
    )
    state: HelenaState = {
        "tenant_id": "tenant-helena-canary",
        "conversation_id": "wa:tenant-helena-canary:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-123",
        "message_body": "ola",
    }

    await graph.classify(state)

    assert len(inference.calls) == 1
    call = inference.calls[0]
    assert call["phi"] is True
    assert call["agent_id"] == "helena"
    assert call["tenant_id"] == "tenant-helena-canary"


async def test_andre_auto_route_passes_agent_id_and_state_tenant_id_to_generate() -> None:
    """Andre's `auto_route` node calls `_build_dossier` -> `self._llm.generate(...)`
    (andre/graph.py) — must pass `agent_id="andre"` (the literal `source_agent_id`,
    andre/graph.py:1164) and `tenant_id=state["tenant_id"]` (never hardcoded)."""
    inference = _CapturingInference(["narrativa sintetica"])
    graph = AndreGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    state: AndreState = {"tenant_id": "tenant-andre-canary", "route": "auto_route"}  # type: ignore[typeddict-item]

    await graph.auto_route(state)

    assert len(inference.calls) == 1
    call = inference.calls[0]
    assert call["phi"] is True
    assert call["agent_id"] == "andre"
    assert call["tenant_id"] == "tenant-andre-canary"


async def test_tenant_id_is_read_from_state_not_a_shared_constant() -> None:
    """Cross-agent proof that `tenant_id` is genuinely per-call state, not accidentally a shared
    default: two different agents, two different tenant ids, each correlates to its OWN call —
    guards against a regression that threads one agent's tenant id into another's call, or a
    module-level constant standing in for `state.get('tenant_id')`."""
    helena_inference = _CapturingInference([_classify_json()])
    helena_graph = HelenaGraph(
        inference=helena_inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_FakeWhatsAppSender(),
    )
    helena_state: HelenaState = {
        "tenant_id": "tenant-alpha",
        "conversation_id": "wa:tenant-alpha:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-123",
        "message_body": "ola",
    }

    andre_inference = _CapturingInference(["narrativa sintetica"])
    andre_graph = AndreGraph(
        inference=andre_inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    andre_state: AndreState = {"tenant_id": "tenant-beta", "route": "auto_route"}  # type: ignore[typeddict-item]

    await helena_graph.classify(helena_state)
    await andre_graph.auto_route(andre_state)

    assert helena_inference.calls[0]["agent_id"] == "helena"
    assert helena_inference.calls[0]["tenant_id"] == "tenant-alpha"
    assert andre_inference.calls[0]["agent_id"] == "andre"
    assert andre_inference.calls[0]["tenant_id"] == "tenant-beta"
