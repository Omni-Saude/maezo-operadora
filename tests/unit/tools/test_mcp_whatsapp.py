"""Unit tests for maezo.tools.mcp_whatsapp (ADR-0022)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# WhatsApp Server: send_message, verify_webhook
# ---------------------------------------------------------------------------


def test_whatsapp_tools_registered() -> None:
    """WhatsApp server should expose exactly 2 tools: send_message, verify_webhook."""
    from maezo.tools.mcp_whatsapp import WhatsAppServer

    server = WhatsAppServer()

    tools = server.list_tools()

    assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}: {tools}"
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"send_message", "verify_webhook"}


def test_whatsapp_settings_defaults() -> None:
    """WhatsAppSettings should have default token/secret/verify_token as empty strings
    (must be configured via env vars in production)."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    settings = WhatsAppSettings()

    assert settings.whatsapp_token == ""
    assert settings.whatsapp_app_secret == ""
    assert settings.whatsapp_verify_token == ""
    assert settings.base_url == "https://graph.facebook.com/v18.0"


def test_whatsapp_verify_webhook_valid() -> None:
    """verify_webhook with correct challenge should return the challenge string."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    settings = WhatsAppSettings(whatsapp_verify_token="my_verify_token")
    server = WhatsAppServer(settings=settings)

    result = server.verify_webhook("my_verify_token", "challenge_abc")

    assert result == "challenge_abc"


def test_whatsapp_verify_webhook_invalid() -> None:
    """verify_webhook with wrong token should raise ValueError."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    settings = WhatsAppSettings(whatsapp_verify_token="my_verify_token")
    server = WhatsAppServer(settings=settings)

    with pytest.raises(ValueError, match="Invalid verify token"):
        server.verify_webhook("wrong_token", "challenge_abc")


@pytest.mark.asyncio
async def test_whatsapp_send_message() -> None:
    """send_message should POST to the WhatsApp Cloud API messages endpoint."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    settings = WhatsAppSettings(
        whatsapp_token="test_token",
        whatsapp_verify_token="verify_token",
    )
    server = WhatsAppServer(settings=settings)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "messaging_product": "whatsapp",
            "messages": [{"id": "wamid.test123"}],
        }
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.send_message("5511999999999", "Ola, Maezo!")

    assert result["messaging_product"] == "whatsapp"
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert "/messages" in call_args[0][0]
    # Verify Authorization header
    headers = call_args[1].get("headers", {})
    assert headers.get("Authorization") == "Bearer test_token"
