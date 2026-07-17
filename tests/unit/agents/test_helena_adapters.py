"""Unit tests for `maezo.agents.helena.adapters.WhatsAppServerSender`."""

from __future__ import annotations

from unittest.mock import AsyncMock

from maezo.agents.helena.adapters import WhatsAppServerSender
from maezo.tools.mcp_whatsapp.server import WhatsAppServer


async def test_whatsapp_server_sender_delegates_to_send_message() -> None:
    server = WhatsAppServer()
    server.send_message = AsyncMock(return_value={"messages": [{"id": "wamid.1"}]})  # type: ignore[method-assign]

    sender = WhatsAppServerSender(server)
    result = await sender.send("some-hash", "ola")

    server.send_message.assert_awaited_once_with("some-hash", "ola")
    assert result == {"messages": [{"id": "wamid.1"}]}
