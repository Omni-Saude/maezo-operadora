"""Unit tests for `maezo.platform.webhooks.whatsapp.app` (T1.6) — real signature verification,
explicit 501 for the not-yet-wired downstream (no engine, no Kafka, ASGI in-process transport)."""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest
from fastapi import FastAPI

from maezo.platform.webhooks.whatsapp.app import create_app
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


async def test_post_webhook_valid_signature_returns_501_not_implemented(app: FastAPI) -> None:
    """Charter: explicit 501/queue-less behavior — signature-verified, but this build does not
    fabricate a Kafka publish with no consumer (T1.11 wires the downstream)."""
    payload = b'{"entry":[{"changes":[{"value":{"messages":[]}}]}]}'
    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})
    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert "T1.11" in body["detail"]
