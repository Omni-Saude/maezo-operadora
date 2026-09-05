"""Outbound leg of the `wamid` guard — `WhatsAppServer.send_message` (gap `WEBHOOK-WAMID-DEDUP`,
owner decision R-071: "mais idempotencia na saida `send`, tratadas como uma entrega so").

The inbound claim cannot cover the whole delivery: it is WITHDRAWN when a turn fails, and a turn
can fail AFTER the beneficiary was already answered (a checkpoint write that raises after
`respond`). Meta's redelivery would then legitimately re-run the turn — and legitimately re-send
the reply — if the send itself were not idempotent too. That is the window these tests pin.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maezo.platform.driver_idempotency import DedupRegistryUnavailableError
from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings
from tests.support.dedup_fakes import FakeDedupRegistry

_KEY = "wa:outbound:amh:hk1_deadbeef:1"


def _settings() -> WhatsAppSettings:
    return WhatsAppSettings(whatsapp_token="tok-" + "q" * 28, phone_number_id="1234567890")


def _mock_httpx_client(*, fail: bool = False) -> AsyncMock:
    mock_response = MagicMock()
    if fail:
        mock_response.raise_for_status = MagicMock(side_effect=RuntimeError("cloud api 503"))
    else:
        mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"messaging_product": "whatsapp", "messages": [{"id": "wamid.out1"}]}
    )
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=mock_response)
    return client


@pytest.mark.asyncio
async def test_the_same_idempotency_key_sends_exactly_once() -> None:
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        first = await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)
        second = await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert client.post.await_count == 1, "the repeat must not reach the Cloud API at all"
    assert first["messages"][0]["id"] == "wamid.out1"
    assert second == {"suppressed_duplicate": True, "idempotency_key": _KEY}


@pytest.mark.asyncio
async def test_the_suppressed_return_value_is_not_a_fabricated_cloud_api_response() -> None:
    """A synthesised `{"messages": [{"id": ...}]}` would hand the caller an id no message has —
    the fabricated-success pattern this repo's gates exist to prevent."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)
        suppressed = await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert "messages" not in suppressed
    assert suppressed["suppressed_duplicate"] is True


@pytest.mark.asyncio
async def test_a_failed_send_withdraws_its_claim_so_a_retry_can_really_send() -> None:
    """Otherwise a transient Cloud API failure would be laundered into permanent silence: the key
    would stay claimed and every retry would be suppressed as a "duplicate" of a message that was
    never delivered."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)

    with (
        patch("httpx.AsyncClient", return_value=_mock_httpx_client(fail=True)),
        pytest.raises(RuntimeError),
    ):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert registry.rows == {}, "the failed send must leave no claim behind"

    client = _mock_httpx_client()
    with patch("httpx.AsyncClient", return_value=client):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_a_successful_send_seals_its_claim() -> None:
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)

    with patch("httpx.AsyncClient", return_value=_mock_httpx_client()):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert registry.rows == {_KEY: "processed"}


@pytest.mark.asyncio
async def test_an_unreachable_registry_refuses_the_send_instead_of_risking_a_duplicate() -> None:
    registry = FakeDedupRegistry(unavailable=True)
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with (
        patch("httpx.AsyncClient", return_value=client),
        pytest.raises(DedupRegistryUnavailableError),
    ):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert client.post.await_count == 0


@pytest.mark.asyncio
async def test_distinct_keys_are_independent_sends() -> None:
    """Non-vacuity for every suppression test above: the guard suppresses BY KEY, not by any send
    that happens to follow another."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        await server.send_message("5511999999999", "um", idempotency_key=f"{_KEY}:a")
        await server.send_message("5511999999999", "dois", idempotency_key=f"{_KEY}:b")

    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_a_send_without_a_key_is_byte_identical_to_the_pre_dedup_path() -> None:
    """Every existing caller passes no key; none of them may change behaviour or touch the store."""
    registry = FakeDedupRegistry()
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        result: dict[str, Any] = await server.send_message("5511999999999", "Ola!")

    assert client.post.await_count == 1
    assert result["messages"][0]["id"] == "wamid.out1"
    assert registry.calls == []


@pytest.mark.asyncio
async def test_a_key_without_a_registry_is_announced_never_silently_ignored() -> None:
    import structlog

    server = WhatsAppServer(settings=_settings())
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client), structlog.testing.capture_logs() as logs:
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert client.post.await_count == 1, "the message must still be sent — refusing would be worse"
    assert any(entry["event"] == "whatsapp_send_idempotency_key_ignored" for entry in logs)
