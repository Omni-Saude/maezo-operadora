"""Generic `WhatsAppSender` adapter over `maezo.tools.mcp_whatsapp.server.WhatsAppServer`.

This is Lucas's OWN copy of the SAME thin adapter shape as `agents/helena/adapters.py`
(`WhatsAppServerSender`) — deliberately duplicated, not imported, per ADR-0004's federated
Agent-Definition independence stance (mirrors the v1 donor's `contracts.py` rationale:
"independence > DRY between agents"). It satisfies `graph.WhatsAppSender` structurally; the
`to` parameter is passed straight through to `WhatsAppServer.send_message` unchanged, so a
caller passing a phone-number HASH (Lucas's graph only ever has a hash, never a raw number —
ADR-0006) sends to the hash literal, not a real recipient. Real per-turn resolution from hash
back to a raw number (if/when Lucas gets a live inbound dispatch driver, mirroring
`platform/webhooks/whatsapp/dispatch.py`) is that future driver's job, not this adapter's — see
that module's docstring for the same labeled boundary Helena already discloses (no persistent,
reversible hash->phone vault exists in v2 yet). Today this adapter is used by
`runtime/agent_runtime/service.py`'s readiness-check wiring, which only proves Lucas's graph
BUILDS/COMPILES, never invokes a node.

OUTBOUND IDEMPOTENCY (LUC-08, gap `IDEMPOTENCY-KEY-MISSING`). This is the LOWEST layer of
Lucas's own package that owns the send, so it is where `graph.WhatsAppSender`'s now-required
`idempotency_key` gets threaded through to a component that ACTUALLY dedupes — an adapter that
accepted the key and dropped it would be exactly the forbidden accepted-but-ignored workaround.
The key is forwarded UNCHANGED to `WhatsAppServer.send_message(..., idempotency_key=...)`, which
claims it against the SAME durable `driver_idempotency` table (ADR-0024, R-073) Helena's live
dispatcher already uses for its own outbound leg — no new table, no in-memory-only dedupe here.
Whether a `dedup` registry is actually wired into the `WhatsAppServer` this adapter wraps is a
COMPOSITION-ROOT decision (`send_message`'s own docstring: "Ignored — with a loud log line,
never silently — when no registry is wired"), not this adapter's: today the only construction
site (`gateway/tool_registry.py::build_whatsapp_seam`, `adapter="lucas"`) builds a bare
`WhatsAppServer()` for the SAME readiness-only reason named above (no live turn ever reaches
`.send()` yet — `docs/processes/contracts/SP-OP-CANCEL-001.md` "Canal de entrada" section
verifies this), so wiring a registry there has no live effect to prove today and is left for the
driver that eventually calls this adapter for a real turn.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_whatsapp.server import WhatsAppServer


class WhatsAppServerSender:
    """Adapts `WhatsAppServer.send_message(to, text, idempotency_key=...)` to the
    `graph.WhatsAppSender` Protocol — see the module docstring's OUTBOUND IDEMPOTENCY section for
    why `idempotency_key` is forwarded, never swallowed."""

    def __init__(self, server: WhatsAppServer) -> None:
        self._server = server

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        return await self._server.send_message(to_hash, text, idempotency_key=idempotency_key)
