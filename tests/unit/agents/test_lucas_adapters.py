"""Unit tests for `maezo.agents.lucas.adapters.WhatsAppServerSender` (LUC-08).

LUC-08 (gap `IDEMPOTENCY-KEY-MISSING`): Lucas's outbound WhatsApp seam carried no idempotency
key, so an engine re-delivery of the same turn could resend the informational message or the
escalation ACK. These tests prove two things a symbol grep cannot: (1) the adapter FORWARDS the
key it is given — an adapter that accepted `idempotency_key` and dropped it on the floor would be
the forbidden accepted-but-ignored workaround the brief names explicitly; (2) the key reaches a
component that ACTUALLY dedupes — the REAL `WhatsAppServer.send_message` claiming it against a
fake `DedupRegistry` (the same double `tests/unit/tools/test_mcp_whatsapp_idempotency.py` uses
for the `WEBHOOK-WAMID-DEDUP` gap), never a mock that only proves the call shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from maezo.agents.lucas.adapters import WhatsAppServerSender
from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings
from tests.support.dedup_fakes import FakeDedupRegistry

_KEY = "ESC-amh-wa:amh:deadbeef:respond_member"


def _settings() -> WhatsAppSettings:
    return WhatsAppSettings(whatsapp_token="tok-" + "q" * 28, phone_number_id="1234567890")


def _mock_httpx_client() -> AsyncMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"messaging_product": "whatsapp", "messages": [{"id": "wamid.out1"}]}
    )
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=mock_response)
    return client


async def test_whatsapp_server_sender_forwards_idempotency_key_to_send_message() -> None:
    """The adapter is the LOWEST layer in Lucas's own package that owns the send — it must
    FORWARD the key unchanged, never swallow it (LUC-08 build step 2)."""
    server = WhatsAppServer()
    server.send_message = AsyncMock(return_value={"messages": [{"id": "wamid.1"}]})  # type: ignore[method-assign]

    sender = WhatsAppServerSender(server)
    result = await sender.send("some-hash", "ola", idempotency_key=_KEY)

    server.send_message.assert_awaited_once_with("some-hash", "ola", idempotency_key=_KEY)
    assert result == {"messages": [{"id": "wamid.1"}]}


async def test_the_key_reaches_a_component_that_actually_dedupes() -> None:
    """LUC-08 non-negotiable: 'the key MUST reach a component that actually dedupes — a
    parameter that is accepted and ignored is forbidden'. Proven end to end through the REAL
    adapter and the REAL `WhatsAppServer`, against a fake durable store: a replayed send with the
    SAME key must not reach the Cloud API a second time."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    sender = WhatsAppServerSender(server)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        first = await sender.send("deadbeef", "Aqui esta a 2a via do seu boleto.", idempotency_key=_KEY)
        second = await sender.send("deadbeef", "Aqui esta a 2a via do seu boleto.", idempotency_key=_KEY)

    assert client.post.await_count == 1, "the replay must not reach the Cloud API at all"
    assert "messages" in first
    assert second == {"suppressed_duplicate": True, "idempotency_key": _KEY}
