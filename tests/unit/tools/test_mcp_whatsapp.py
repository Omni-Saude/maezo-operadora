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
    assert settings.phone_number_id == ""
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
        # ADAPTED (achado 9.4): the send path now addresses the URL by phone-number id.
        # Before the fix this test passed while the URL read `/v18.0/test_token/messages`.
        phone_number_id="1234567890",
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


# ---------------------------------------------------------------------------
# Auditoria 09 — achados 9.3 (secret in the exception message) and 9.4 (bearer
# token in the URL path). Each test below FAILS on the pre-fix module.
# ---------------------------------------------------------------------------

#: Deliberately OPAQUE — no dictionary words. A sample secret spelling out "verify"
#: or "token" collides with the generic refusal text ("Invalid verify token") and the
#: substring sweep below then reports a coincidence as a leak. High entropy is what
#: makes "no 4+-char window of the secret appears in the message" a real assertion.
_SECRET = "Zx9Qw7Lp4Rt2Vb8Nm5Kd3Hs6"


def _all_substrings(value: str, min_length: int = 4) -> list[str]:
    return [
        value[start : start + size]
        for size in range(min_length, len(value) + 1)
        for start in range(0, len(value) - size + 1)
    ]


def test_verify_webhook_refusal_leaks_no_part_of_the_configured_token() -> None:
    """9.3: the refusal reaches structlog and the caller's HTTP body — it must carry
    neither the configured verify token nor ANY substring of it.

    Pre-fix this raised `ValueError(f"Invalid verify token (expected {token!r})")`,
    i.e. the whole secret, verbatim, in the message.
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(settings=WhatsAppSettings(whatsapp_verify_token=_SECRET))

    with pytest.raises(ValueError) as excinfo:
        server.verify_webhook("wrong_token", "challenge_abc")

    message = str(excinfo.value)
    assert _SECRET not in message
    leaked = [chunk for chunk in _all_substrings(_SECRET) if chunk in message]
    assert leaked == [], f"refusal message leaks token substrings: {leaked}"


def test_verify_webhook_refuses_when_no_token_is_configured() -> None:
    """Fail-closed: an UNSET verify token must refuse everything.

    Pre-fix, default settings (`whatsapp_verify_token == ""`) made `verify_token=""`
    compare equal — an unconfigured server handed out the challenge to any caller.
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(settings=WhatsAppSettings())

    with pytest.raises(ValueError):
        server.verify_webhook("", "challenge_abc")


def test_verify_webhook_refuses_a_non_ascii_token_without_raising_typeerror() -> None:
    """`hmac.compare_digest` refuses non-ASCII `str`; the comparison is done on bytes."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(settings=WhatsAppSettings(whatsapp_verify_token=_SECRET))

    with pytest.raises(ValueError):
        server.verify_webhook("café", "challenge_abc")


def _mock_httpx_client() -> AsyncMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"messaging_product": "whatsapp", "messages": [{"id": "wamid.test123"}]}
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_send_message_url_carries_the_phone_number_id_and_never_the_token() -> None:
    """9.4: `POST {base_url}/{phone_number_id}/messages`, token in the header only.

    Pre-fix the URL was `{base_url}/{whatsapp_token}/messages` — the bearer credential
    in the path, where every access log, proxy and `httpx.HTTPStatusError` copies it.
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    token = "EAAGm7Jq2Xd5Rb9Tn4Ws8Vk6"
    settings = WhatsAppSettings(whatsapp_token=token, phone_number_id="1234567890")
    server = WhatsAppServer(settings=settings)
    mock_client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=mock_client):
        await server.send_message("5511999999999", "Ola, Maezo!")

    url = mock_client.post.call_args[0][0]
    assert url == "https://graph.facebook.com/v18.0/1234567890/messages"
    assert token not in url
    leaked = [chunk for chunk in _all_substrings(token) if chunk in url]
    assert leaked == [], f"URL leaks token substrings: {leaked}"
    headers = mock_client.post.call_args[1]["headers"]
    assert headers["Authorization"] == f"Bearer {token}"


@pytest.mark.asyncio
async def test_send_message_refuses_when_phone_number_id_is_unconfigured() -> None:
    """Fail-closed: a missing `phone_number_id` REFUSES; it never falls back to the token.

    The fallback is the whole of 9.4: the pre-fix build had no `phone_number_id` at all,
    so the token was structurally forced into the path. No HTTP call may be attempted.
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    token = "EAAGm7Jq2Xd5Rb9Tn4Ws8Vk6"
    server = WhatsAppServer(settings=WhatsAppSettings(whatsapp_token=token))
    mock_client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(ValueError) as excinfo:
        await server.send_message("5511999999999", "Ola, Maezo!")

    mock_client.post.assert_not_awaited()
    message = str(excinfo.value)
    assert token not in message
    assert [chunk for chunk in _all_substrings(token) if chunk in message] == []


@pytest.mark.asyncio
async def test_send_message_refuses_when_the_token_is_unconfigured() -> None:
    """Fail-closed on the other credential: no `Authorization: Bearer ` with an empty token."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    server = WhatsAppServer(settings=WhatsAppSettings(phone_number_id="1234567890"))
    mock_client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(ValueError):
        await server.send_message("5511999999999", "Ola, Maezo!")

    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_never_logs_the_raw_recipient() -> None:
    """ADR-0006: on the live path `to` is the RAW phone number (`_ScopedWhatsAppSender`
    passes `raw_to`), so no log line may carry it — the I-3 claim in
    `docs/design/wave1-effect-chokepoint.md`. Pre-fix: `logger.info(..., to=to)`."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    settings = WhatsAppSettings(whatsapp_token="tok", phone_number_id="1234567890")
    server = WhatsAppServer(settings=settings)
    mock_client = _mock_httpx_client()
    raw_number = "5511999999999"

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("maezo.tools.mcp_whatsapp.server.logger") as mock_logger,
    ):
        await server.send_message(raw_number, "Ola, Maezo!")

    emitted = repr(mock_logger.info.call_args_list)
    assert raw_number not in emitted, f"raw recipient reached a log line: {emitted}"


@pytest.mark.asyncio
async def test_send_message_survives_a_response_with_an_empty_messages_list() -> None:
    """`data.get("messages", [{}])[0]` raised IndexError when the key was present but
    empty — a crash AFTER the message was already delivered."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    settings = WhatsAppSettings(whatsapp_token="tok", phone_number_id="1234567890")
    server = WhatsAppServer(settings=settings)
    mock_client = _mock_httpx_client()
    mock_client.post.return_value.json = MagicMock(
        return_value={"messaging_product": "whatsapp", "messages": []}
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await server.send_message("5511999999999", "Ola, Maezo!")

    assert result["messages"] == []
