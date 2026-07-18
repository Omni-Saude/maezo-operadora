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
"""

from __future__ import annotations

from typing import Any

from maezo.tools.mcp_whatsapp.server import WhatsAppServer


class WhatsAppServerSender:
    """Adapts `WhatsAppServer.send_message(to, text)` to the `graph.WhatsAppSender` Protocol."""

    def __init__(self, server: WhatsAppServer) -> None:
        self._server = server

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return await self._server.send_message(to_hash, text)
