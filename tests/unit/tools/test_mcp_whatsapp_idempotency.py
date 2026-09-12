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
from maezo.platform.observability import get_metrics_collector
from maezo.tools.mcp_whatsapp.server import (
    WhatsAppIdempotencyUnsupportedError,
    WhatsAppServer,
    WhatsAppSettings,
)
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
async def test_a_key_without_a_registry_is_refused_never_accepted_and_ignored() -> None:
    """§Delta W4-HYGIENE F2 — this assertion is INVERTED from what it pinned before.

    It used to pin "the message must still be sent — refusing would be worse", which held while
    the only caller with a key was Helena's live path (whose root always wires the registry).
    LUC-08 made Lucas's graph supply a key UNCONDITIONALLY, and his root could be built without
    a DSN: the old branch then answered a request for once-only delivery with a 2xx and no
    guarantee, i.e. a parameter accepted and ignored. The refusal is now the contract, the log
    line is still there, and — the half a log line alone never proved — the Cloud API is not
    reached at all.
    """
    import structlog

    server = WhatsAppServer(settings=_settings())
    client = _mock_httpx_client()

    with (
        patch("httpx.AsyncClient", return_value=client),
        structlog.testing.capture_logs() as logs,
        pytest.raises(WhatsAppIdempotencyUnsupportedError),
    ):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert client.post.await_count == 0, "the send must not reach the Cloud API unprotected"
    assert any(entry["event"] == "whatsapp_send_idempotency_key_unsupported" for entry in logs)


@pytest.mark.asyncio
async def test_a_registry_less_server_still_sends_when_no_key_is_supplied() -> None:
    """The other half of the same §Delta: the refusal is scoped to a caller that ASKED for
    once-only delivery. Helena's and Fernando's Protocols pass no key, so their unprotected send
    is untouched — a refusal there would be a regression, not a hardening."""
    server = WhatsAppServer(settings=_settings())
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        result = await server.send_message("5511999999999", "Ola!")

    assert client.post.await_count == 1
    assert result["messages"][0]["id"] == "wamid.out1"


def _seal_failures_total() -> float:
    value = get_metrics_collector().registry.get_sample_value("maezo_whatsapp_send_seal_failures_total")
    return float(value or 0.0)


@pytest.mark.asyncio
async def test_a_seal_failure_after_a_2xx_neither_raises_nor_withdraws_the_claim() -> None:
    """VER-A2-WEBHOOK MINOR-2. The window between the Cloud API's 2xx and the seal.

    With the seal outside the send's failure domain, a registry blip HERE raised out of
    `send_message`, failed the Helena turn, and made `webhooks/whatsapp/app.py::_withdraw_claim`
    withdraw the INBOUND claim — so Meta re-delivered a message the beneficiary had already
    received. The effect ALREADY happened, so nothing downstream may be told it did not: the
    failure is logged and counted, and the send returns normally.
    """
    import structlog

    registry = FakeDedupRegistry(fail_ops=frozenset({"mark_processed"}))
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    before = _seal_failures_total()

    with (
        patch("httpx.AsyncClient", return_value=_mock_httpx_client()),
        structlog.testing.capture_logs() as logs,
    ):
        result = await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert result["messages"][0]["id"] == "wamid.out1", "the delivered message is reported as sent"
    assert registry.rows == {_KEY: "pending"}, "the claim is NOT withdrawn — the message went out"
    assert _seal_failures_total() == before + 1
    assert any(entry["event"] == "whatsapp_send_claim_seal_failed" for entry in logs)


@pytest.mark.asyncio
async def test_an_unsealed_claim_still_suppresses_a_repeat_within_the_lease() -> None:
    """The residual of MINOR-2's choice, pinned rather than assumed: an unsealed row stays
    `pending`, and a `pending` row still refuses a second claim — so duplicate protection for that
    key degrades from the full TTL to one in-flight lease, it does not vanish. (The lease EXPIRY
    itself is a property of the real store: only
    `tests/integration/platform/test_wamid_dedup_live_pg.py` can prove it.)"""
    registry = FakeDedupRegistry(fail_ops=frozenset({"mark_processed"}))
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()

    with patch("httpx.AsyncClient", return_value=client):
        await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)
        repeat = await server.send_message("5511999999999", "Ola!", idempotency_key=_KEY)

    assert client.post.await_count == 1
    assert repeat == {"suppressed_duplicate": True, "idempotency_key": _KEY}


# R19: an outbound provider id can embed the counterpart phone. Construct only
# synthetic data; inspect actual emitted log dictionaries, never a logger stub.
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unguarded", "guarded", "seal_failure", "duplicate"])
async def test_outbound_success_logs_omit_provider_id_preserving_delivery(mode):
    import base64
    import json

    import structlog.testing

    phone = "5511000000000"
    encoded = base64.b64encode((phone + ":synthetic-outbound").encode()).decode()
    provider_id = "wamid." + encoded
    response = {"messaging_product": "whatsapp", "messages": [{"id": provider_id}]}
    registry = (
        None
        if mode == "unguarded"
        else FakeDedupRegistry(
            fail_ops=frozenset({"mark_processed"}) if mode == "seal_failure" else frozenset()
        )
    )
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()
    client.post.return_value.json.return_value = response
    key = None if registry is None else _KEY
    with patch("httpx.AsyncClient", return_value=client), structlog.testing.capture_logs() as logs:
        returned = await server.send_message(phone, "synthetic body", idempotency_key=key)
        if mode == "duplicate":
            repeated = await server.send_message(phone, "synthetic body", idempotency_key=key)
            assert repeated == {"suppressed_duplicate": True, "idempotency_key": _KEY}

    assert returned is response and returned["messages"][0]["id"] == provider_id
    client.post.assert_awaited_once()
    sent = client.post.call_args.kwargs
    assert sent["json"] == {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": "synthetic body"},
    }
    assert sent["headers"]["Authorization"] == "Bearer " + _settings().whatsapp_token
    if registry is not None:
        assert registry.rows == {_KEY: "pending" if mode == "seal_failure" else "processed"}
    success = [entry for entry in logs if entry["event"] == "whatsapp_message_sent"]
    assert len(success) == 1
    rendered = json.dumps(logs)
    assert all(value not in rendered for value in (provider_id, encoded, phone, "synthetic body"))
    assert "message_id" not in success[0]
    if mode == "seal_failure":
        assert any(entry["event"] == "whatsapp_send_claim_seal_failed" for entry in logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("release_fails", [False, True])
async def test_outbound_failed_post_logs_no_provider_id_and_preserves_retry_state(release_fails):
    import json

    import structlog.testing

    # The failure object remains the caller's; this fix changes only this server's log.
    provider_error = RuntimeError("wamid.synthetic-outbound-error")
    registry = FakeDedupRegistry(fail_ops=frozenset({"release"}) if release_fails else frozenset())
    server = WhatsAppServer(settings=_settings(), dedup=registry)
    client = _mock_httpx_client()
    client.post.side_effect = provider_error
    with (
        patch("httpx.AsyncClient", return_value=client),
        structlog.testing.capture_logs() as logs,
        pytest.raises(RuntimeError) as caught,
    ):
        await server.send_message("5511000000000", "synthetic body", idempotency_key=_KEY)
    assert caught.value is provider_error
    assert not any(entry["event"] == "whatsapp_message_sent" for entry in logs)
    assert "wamid.synthetic-outbound-error" not in json.dumps(logs)
    assert registry.rows == ({_KEY: "pending"} if release_fails else {})
    if not release_fails:
        client.post.side_effect = None
        with patch("httpx.AsyncClient", return_value=client), structlog.testing.capture_logs():
            result = await server.send_message("5511000000000", "synthetic body", idempotency_key=_KEY)
        assert result["messages"][0]["id"] == "wamid.out1"
        assert client.post.await_count == 2
