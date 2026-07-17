"""Generic `WhatsAppSender` adapter over `maezo.tools.mcp_whatsapp.server.WhatsAppServer`.

This is the STRUCTURAL adapter (satisfies `graph.WhatsAppSender`) used wherever a real,
constructible-but-not-necessarily-invoked sender is needed — e.g. `agent_runtime`'s readiness
check, which only proves Helena's graph BUILDS/COMPILES, never invokes a node.

For an actual live WhatsApp turn, the webhook dispatch driver
(`maezo.platform.webhooks.whatsapp.dispatch`) uses its OWN per-turn scoped sender instead: this
adapter's `to` parameter is passed straight through to `WhatsAppServer.send_message` unchanged,
so a caller passing a phone-number HASH (Helena's graph only ever has a hash, never a raw
number — ADR-0006) would send to the hash literal, not a real recipient. That per-turn resolution
from hash back to the raw number captured for the current inbound request is the dispatch
driver's job, not this adapter's — see that module's docstring for the labeled boundary
(no persistent, reversible hash->phone vault exists in v2 yet).
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
