"""Unit tests for `maezo.platform.webhooks.whatsapp.app` (T1.6 signature verification; T1.11
real dispatch to a `HelenaDispatcher` double, ASGI in-process transport, no engine/network)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dispatch import InboundMessage
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"


def _settings(**overrides: object) -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(
        app_secret=APP_SECRET, verify_token=VERIFY_TOKEN, tenant_id="amh", **overrides
    )


def _sign(payload: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def app() -> FastAPI:
    return create_app(_settings())


async def test_healthz_ok(app: FastAPI) -> None:
    async with await _client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200


async def test_readyz_ok(app: FastAPI) -> None:
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


async def test_healthz_503_when_not_live() -> None:
    app = create_app(_settings(), is_live=lambda: False)
    async with await _client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 503


async def test_get_webhook_verification_success(app: FastAPI) -> None:
    async with await _client(app) as client:
        resp = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.challenge": "chal123", "hub.verify_token": VERIFY_TOKEN},
        )
    assert resp.status_code == 200
    assert resp.text == "chal123"


async def test_get_webhook_verification_wrong_token(app: FastAPI) -> None:
    async with await _client(app) as client:
        resp = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.challenge": "chal123", "hub.verify_token": "wrong"},
        )
    assert resp.status_code == 403


async def test_get_webhook_verification_wrong_mode(app: FastAPI) -> None:
    async with await _client(app) as client:
        resp = await client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.challenge": "chal123",
                "hub.verify_token": VERIFY_TOKEN,
            },
        )
    assert resp.status_code == 403


async def test_post_webhook_invalid_signature(app: FastAPI) -> None:
    payload = b'{"entry":[]}'
    async with await _client(app) as client:
        resp = await client.post(
            "/webhook", content=payload, headers={"X-Hub-Signature-256": "sha256=deadbeef"}
        )
    assert resp.status_code == 401
    assert resp.json()["status"] == "invalid_signature"


async def test_post_webhook_missing_signature_header(app: FastAPI) -> None:
    payload = b'{"entry":[]}'
    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload)
    assert resp.status_code == 401


async def test_post_webhook_valid_signature_parse_error(app: FastAPI) -> None:
    payload = b"not-json"
    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})
    assert resp.status_code == 400
    assert resp.json()["status"] == "parse_error"


async def test_post_webhook_valid_signature_no_messages_acks_200(app: FastAPI) -> None:
    """A signature-verified payload with no actual message (e.g. a delivery-status callback, or
    this fixture's empty `messages` list) is acked — nothing to dispatch, nothing fabricated."""
    payload = b'{"entry":[{"changes":[{"value":{"messages":[]}}]}]}'
    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 0}


async def test_post_webhook_valid_signature_message_no_dispatcher_returns_501(app: FastAPI) -> None:
    """T1.11: a real message with NO dispatcher configured for this replica (e.g. dependency
    bring-up failed) still returns the explicit, honest 501 — never a fabricated dispatch."""
    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload = json.dumps(body).encode()
    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})
    assert resp.status_code == 501
    assert resp.json()["status"] == "not_implemented"


class _FakeDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispatched: list[InboundMessage] = []
        self._fail = fail

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("dispatch boom")
        self.dispatched.append(message)
        return {"ok": True}


def _text_message_payload(
    *, from_number: str = "5511999999999", body: str = "oi", msg_id: str = "wamid.1"
) -> bytes:
    envelope = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": from_number, "id": msg_id, "type": "text", "text": {"body": body}}
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(envelope).encode()


async def test_post_webhook_dispatches_real_message_to_dispatcher() -> None:
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    payload = _text_message_payload(body="estou com dor no peito")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 1, "failed": 0}
    assert len(dispatcher.dispatched) == 1
    assert dispatcher.dispatched[0].text == "estou com dor no peito"
    assert dispatcher.dispatched[0].from_number == "5511999999999"


async def test_post_webhook_dispatch_failure_returns_500_never_fabricates_success() -> None:
    dispatcher = _FakeDispatcher(fail=True)
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    payload = _text_message_payload()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 500
    assert resp.json()["status"] == "dispatch_failed"


async def test_post_webhook_non_text_message_skipped_acks_200() -> None:
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    envelope = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "5511999999999", "type": "image"}]}}]}]
    }
    payload = json.dumps(envelope).encode()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 0}
    assert dispatcher.dispatched == []


async def test_get_webhook_non_ascii_token_is_403_not_an_unhandled_500(app: FastAPI) -> None:
    """`hub.verify_token` is attacker-controlled, and `hmac.compare_digest` REFUSES a
    non-ASCII `str` with a TypeError. Comparing the raw query param turned
    `?hub.verify_token=café` into an unhandled 500; the comparison is now on UTF-8 bytes,
    so every input takes the same fail-closed 403 path."""
    async with await _client(app) as client:
        response = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.challenge": "chal123", "hub.verify_token": "café"},
        )
    assert response.status_code == 403
    assert VERIFY_TOKEN not in response.text
