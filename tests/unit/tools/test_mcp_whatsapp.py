"""Unit tests for maezo.tools.mcp_whatsapp (ADR-0022)."""

import os
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


def test_whatsapp_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """WhatsAppSettings should have default token/secret/verify_token as empty strings
    (must be configured via env vars in production).

    The env is cleared first: these defaults are what the fail-closed guards in
    `send_message`/`verify_webhook` are keyed on, so an ambient `WHATSAPP_*` in the
    developer's shell must not decide whether this passes (`_clear_whatsapp_env` is
    defined with the env-binding tests at the bottom of this file).
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)

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
# token in the URL path). All but ONE of the tests below FAIL on the pre-fix
# module and are therefore counter-proofs of a real defect; the exception is
# `test_verify_webhook_refuses_a_non_ascii_token_without_raising_typeerror`,
# which PASSES pre-fix (main compared plain `str` with `!=`, which refuses
# "café" happily) — it is a REGRESSION GUARD for the new `compare_digest`
# code, not evidence of a pre-existing leak. Stated here because the first
# version of this file's evidence claimed all of them were counter-proofs.
# ---------------------------------------------------------------------------

#: Deliberately OPAQUE — no dictionary words. A sample secret spelling out "verify"
#: or "token" collides with the generic refusal text ("Invalid verify token") and the
#: substring sweep below then reports a coincidence as a leak. Opaque (not a
#: dictionary word, no overlap with any surrounding literal) is what makes "no
#: 4+-char window of the secret appears in the message" a real assertion; the value
#: is built low-entropy on purpose (a short opaque prefix + one repeated char) so a
#: secrets scanner never mistakes this synthetic test fixture for a real credential.
_SECRET = "seg-" + "q" * 24


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
    """REGRESSION GUARD, not a counter-proof: this test PASSES against HEAD 35cffd3.

    Main compared plain `str` with `!=`, which refuses "café" with the very ValueError
    asserted here. `hmac.compare_digest`, however, raises TypeError on a non-ASCII `str`,
    so the 9.3 fix would have turned an attacker-controlled `hub.verify_token` into an
    unhandled crash had it not encoded to UTF-8 first. This pins that encoding.
    """
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

    token = "tok-" + "x" * 24
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

    token = "tok-" + "x" * 24
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


# ---------------------------------------------------------------------------
# Env-var binding (achado 9.5, gatekeeper MAJOR-1) and secret rendering
# (gatekeeper MINOR-4). `env_prefix="WHATSAPP_"` over fields already NAMED
# `whatsapp_*` resolved to `WHATSAPP_WHATSAPP_TOKEN` / `_APP_SECRET` /
# `_VERIFY_TOKEN` — spellings that appear NOWHERE in this repo (Helm
# `deployment-webhook-receiver.yaml:38,47,52`, `docker-compose.yml:252-253`
# and `.env.example` all inject the SHORT names), so all three credentials
# read "" in every environment and the live send path could never
# authenticate. These tests pin the env NAMES, not just the behaviour.
# ---------------------------------------------------------------------------

_CANONICAL_ENV = {
    "WHATSAPP_TOKEN": "canonical-token",
    "WHATSAPP_APP_SECRET": "canonical-secret",
    "WHATSAPP_VERIFY_TOKEN": "canonical-verify",
    "WHATSAPP_PHONE_NUMBER_ID": "5550001111",
}

_DOUBLED_ENV = {
    "WHATSAPP_WHATSAPP_TOKEN": "doubled-token",
    "WHATSAPP_WHATSAPP_APP_SECRET": "doubled-secret",
    "WHATSAPP_WHATSAPP_VERIFY_TOKEN": "doubled-verify",
}


def _clear_whatsapp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every WHATSAPP_* name the ambient shell may carry — the assertions below are
    about which name the settings READ, so an inherited value would make them lie."""
    for name in list(os.environ):
        if name.startswith("WHATSAPP_"):
            monkeypatch.delenv(name, raising=False)


def test_settings_read_the_canonical_whatsapp_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The short names every injector actually sets must land on the fields."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)
    for name, value in _CANONICAL_ENV.items():
        monkeypatch.setenv(name, value)

    settings = WhatsAppSettings()

    assert settings.whatsapp_token == "canonical-token"
    assert settings.whatsapp_app_secret == "canonical-secret"
    assert settings.whatsapp_verify_token == "canonical-verify"
    assert settings.phone_number_id == "5550001111"


def test_settings_do_not_read_the_doubled_whatsapp_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The doubled spelling must be INERT — nothing in the repo injects it, and a
    settings class that still honoured it would let the defect return silently.

    (`populate_by_name=True` on top of `env_prefix` re-admits exactly these names
    through the env source — probed. That is why the fix uses `AliasChoices` with
    the field name instead of `populate_by_name`.)
    """
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)
    for name, value in _DOUBLED_ENV.items():
        monkeypatch.setenv(name, value)

    settings = WhatsAppSettings()

    assert settings.whatsapp_token == ""
    assert settings.whatsapp_app_secret == ""
    assert settings.whatsapp_verify_token == ""


def test_settings_canonical_env_wins_when_both_spellings_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover doubled var in some environment must not shadow the real credential."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_TOKEN", "canonical-token")
    monkeypatch.setenv("WHATSAPP_WHATSAPP_TOKEN", "doubled-token")

    assert WhatsAppSettings().whatsapp_token == "canonical-token"


def test_settings_still_construct_by_field_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construction by FIELD name is the whole test suite's idiom and every call site's
    injection point (`WhatsAppServer(settings=...)`); the alias must not break it."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)

    settings = WhatsAppSettings(
        whatsapp_token="by-name-token",
        whatsapp_app_secret="by-name-secret",
        whatsapp_verify_token="by-name-verify",
        phone_number_id="1234567890",
    )

    assert settings.whatsapp_token == "by-name-token"
    assert settings.whatsapp_app_secret == "by-name-secret"
    assert settings.whatsapp_verify_token == "by-name-verify"
    assert settings.phone_number_id == "1234567890"


def test_settings_never_render_the_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repr`/`str`/`model_dump`/`model_dump_json` used to print all three secrets
    verbatim (plain `str` fields). Nothing in `src/` dumps this object today — this
    test is what keeps a future `logger.info(..., settings=settings)` from leaking."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

    _clear_whatsapp_env(monkeypatch)
    settings = WhatsAppSettings(
        whatsapp_token=_SECRET,
        whatsapp_app_secret=_SECRET,
        whatsapp_verify_token=_SECRET,
        phone_number_id="1234567890",
    )

    renderings = {
        "repr": repr(settings),
        "str": str(settings),
        "model_dump": repr(settings.model_dump()),
        "model_dump_json": settings.model_dump_json(),
    }

    for label, rendered in renderings.items():
        assert _SECRET not in rendered, f"{label} leaks the credential: {rendered}"
        leaked = [chunk for chunk in _all_substrings(_SECRET) if chunk in rendered]
        assert leaked == [], f"{label} leaks credential substrings: {leaked}"
    # The non-secret fields are still legible — this is redaction, not blindness.
    assert "1234567890" in renderings["repr"]
