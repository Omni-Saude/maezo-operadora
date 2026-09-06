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
COMPOSITION-ROOT decision, not this adapter's. CORRECTED (§Delta W4-HYGIENE F2) — the previous
version of this paragraph said the only construction site built a BARE `WhatsAppServer()` and
that wiring a registry there was left to a future driver. That is no longer true, and the
"accepted and ignored" fallback it relied on no longer exists either:

  * `gateway/tool_registry.py::build_agent_seams` — the root that builds this adapter for the
    agent runtime — now passes `dedup=` (`_outbound_dedup_registry`), constructed from the SAME
    `settings.database_url` it already uses for the `PostgresAuditSink` and pointing at the SAME
    `driver_idempotency` store `platform/webhooks/service.py` gives Helena's live client.
  * a settings surface WITHOUT a DSN still yields a registry-less server; on such an instance
    `WhatsAppServer.send_message` now REFUSES a keyed send
    (`WhatsAppIdempotencyUnsupportedError`) rather than delivering it unprotected, and the
    refusal lands in Lucas's best-effort `except Exception`, which records `mensagem_enviada=
    False` — a disclosed non-delivery, never a silent unprotected duplicate.

What has NOT changed is reachability: Lucas still has no live invocation path
(`docs/processes/contracts/SP-OP-CANCEL-001.md` "Canal de entrada"; gap 11.7 in
`spec/agents/lucas/agent.yaml`), so none of the above has a production effect today. It is wired
so the day the driver lands the seam is already correct (CC-02), not because a turn runs now.
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
