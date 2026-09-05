"""The `wamid` dedup at the receiver's edge — gap `WEBHOOK-WAMID-DEDUP` (owner decisions R-071 /
R-072).

What each block pins, in one line:

* a Meta REDELIVERY of the same wamid runs no second Helena turn (the landmine ADR-0024:7 names);
* a FAILED turn withdraws its claim, so the dedup never converts a transient failure into
  permanent silent loss;
* an UNREACHABLE registry dispatches NOTHING (fail closed) instead of guessing;
* ack-then-queue answers Meta BEFORE the turn runs, and is off by default.

The mixed-batch half of the same handler (gap `WHATSAPP-MIXED-BATCH-RETRY-TRADEOFF`, R-100) lives
in `test_app_mixed_batch.py`, which reuses this file's helpers.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.observability import get_metrics_collector
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.dispatch import InboundMessage, InboundNonTextMessage
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from tests.support.dedup_fakes import FakeDedupRegistry

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
TENANT = "amh"


def _settings(**overrides: object) -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(
        app_secret=APP_SECRET, verify_token=VERIFY_TOKEN, tenant_id=TENANT, **overrides
    )


def _sign(payload: bytes) -> str:
    return f"sha256={hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()}"


async def _post(app: FastAPI, payload: bytes) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})


def _envelope(*messages: dict[str, Any]) -> bytes:
    return json.dumps({"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}).encode()


def _text(msg_id: str = "wamid.1", body: str = "oi") -> dict[str, Any]:
    return {"from": "5511999999999", "id": msg_id, "type": "text", "text": {"body": body}}


def _non_text(msg_id: str = "wamid.9", message_type: str = "image") -> dict[str, Any]:
    return {"from": "5511999999999", "id": msg_id, "type": message_type}


def _counter(status: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_webhook_requests_total", {"tenant": TENANT, "status": status}
    )
    return float(value or 0.0)


def _guard(registry: FakeDedupRegistry) -> WhatsAppDedupGuard:
    return WhatsAppDedupGuard(
        registry=registry,
        pseudonymizer=Pseudonymizer.from_settings(
            phi_hmac_key="unit-test-key-not-a-secret", production=False, tenant_id=TENANT
        ),
        tenant=TENANT,
    )


class _FakeDispatcher:
    """Records what actually reached Helena. `fail_times` fails the first N dispatches."""

    def __init__(self, *, fail_times: int = 0, fail_ack: bool = False) -> None:
        self.dispatched: list[InboundMessage] = []
        self.acknowledged: list[InboundNonTextMessage] = []
        self._fail_times = fail_times
        self._fail_ack = fail_ack
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("dispatch boom")
        self.dispatched.append(message)
        return {"ok": True}

    async def acknowledge_non_text(self, message: InboundNonTextMessage) -> dict[str, Any]:
        if self._fail_ack:
            raise ValueError("WhatsApp phone_number_id is not configured — refusing to send")
        self.acknowledged.append(message)
        return {"ok": True}


# ---------------------------------------------------------------------------
# R-071 — the redelivery landmine
# ---------------------------------------------------------------------------


async def test_a_redelivered_wamid_never_runs_a_second_helena_turn() -> None:
    """THE gap. Meta re-delivers on any non-2xx and on timeout; before this change the second
    delivery ran a complete second turn and the beneficiary got a second reply."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope(_text())
    before = _counter("duplicate")

    first = await _post(app, payload)
    second = await _post(app, payload)

    assert first.status_code == 200
    assert first.json() == {"status": "ok", "dispatched": 1, "failed": 0}
    # The redelivery is ACKNOWLEDGED (200 — Meta must stop retrying) but runs nothing.
    assert second.status_code == 200
    assert second.json() == {"status": "ok", "dispatched": 0, "failed": 0, "duplicates": 1}
    assert len(dispatcher.dispatched) == 1
    assert _counter("duplicate") == before + 1


async def test_a_redelivered_non_text_message_is_not_acknowledged_twice() -> None:
    """The same protection for the fixed non-text reply: a duplicate courtesy message is still a
    duplicate message to a beneficiary."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope(_non_text())

    await _post(app, payload)
    await _post(app, payload)

    assert len(dispatcher.acknowledged) == 1


async def test_a_first_delivery_seals_its_claim() -> None:
    """A sealed claim is what keeps suppressing the redelivery after the in-flight lease ends."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    guard = _guard(registry)
    app = create_app(_settings(), dispatcher=dispatcher, dedup=guard)  # type: ignore[arg-type]

    await _post(app, _envelope(_text()))

    assert registry.rows == {guard.inbound_key("wamid.1"): "processed"}


async def test_a_failed_turn_withdraws_its_claim_so_the_redelivery_really_retries() -> None:
    """Without the withdrawal, the dedup would be WORSE than no dedup: the first (failed) delivery
    would claim the key and Meta's retry — the only second chance the beneficiary has — would be
    suppressed as a duplicate of a turn that answered nobody."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher(fail_times=1)
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope(_text())

    failed = await _post(app, payload)
    retried = await _post(app, payload)

    assert failed.status_code == 500
    assert failed.json()["status"] == "dispatch_failed"
    assert retried.status_code == 200
    assert len(dispatcher.dispatched) == 1, "the retry must really run, not be deduped away"


async def test_an_unreachable_registry_dispatches_nothing_and_says_so() -> None:
    """FAIL CLOSED. Dispatching on the word of a registry that cannot answer would re-open the
    duplicate-reply landmine exactly when the platform is already degraded; a 500 makes Meta
    re-deliver, which is safe precisely because nothing ran."""
    registry = FakeDedupRegistry(unavailable=True)
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    before = _counter("dedup_unavailable")

    response = await _post(app, _envelope(_text()))

    assert response.status_code == 500
    assert response.json()["status"] == "dedup_unavailable"
    assert dispatcher.dispatched == []
    assert dispatcher.acknowledged == []
    assert _counter("dedup_unavailable") == before + 1


async def test_a_message_without_a_wamid_is_dispatched_but_loudly_announced() -> None:
    """Meta always sends `id`. A message without one cannot be keyed at all — dropping it would be
    worse than dispatching it, so it is dispatched and the missing protection is ANNOUNCED, never
    silently assumed away."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope({"from": "5511999999999", "type": "text", "text": {"body": "oi"}})

    with structlog.testing.capture_logs() as logs:
        response = await _post(app, payload)

    assert response.status_code == 200
    assert len(dispatcher.dispatched) == 1
    assert registry.calls == [], "no key could be derived, so nothing may be claimed"
    assert any(entry["event"] == "whatsapp_webhook_message_without_wamid" for entry in logs)


async def test_the_suppression_log_carries_the_pseudonym_and_never_the_wamid() -> None:
    """PHI: a wamid base64-embeds the counterpart phone number (see `test_dedup_keys.py`), so the
    duplicate line logs the keyed pseudonym instead."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    wamid = "wamid.HBgNNTUxMTk5OTk5OTk5ORUCABIYFDNBMEE1NkY3RUUxQjA5RkQyNkE5AA=="
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope(_text(msg_id=wamid))

    await _post(app, payload)
    with structlog.testing.capture_logs() as logs:
        await _post(app, payload)

    suppressed = [e for e in logs if e["event"] == "whatsapp_webhook_duplicate_suppressed"]
    assert len(suppressed) == 1
    assert wamid not in json.dumps(suppressed[0])
    assert "5511999999999" not in json.dumps(suppressed[0])


# ---------------------------------------------------------------------------
# R-072 — ack-then-queue
# ---------------------------------------------------------------------------


def test_ack_then_queue_is_off_by_default() -> None:
    """The target form is adopted, but the default stays the synchronous dispatch: this build has
    no re-drive consumer, so a turn lost after the ack is lost for good (settings.py says so)."""
    assert _settings().ack_then_queue is False


async def test_ack_then_queue_answers_meta_before_the_turn_finishes() -> None:
    """The whole point of R-072: the ack no longer waits for an LLM/engine round trip, which is
    what closes the timeout window that CAUSES Meta's retry."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    gate = asyncio.Event()
    dispatcher.gate = gate
    guard = _guard(registry)
    app = create_app(  # type: ignore[arg-type]
        _settings(ack_then_queue=True), dispatcher=dispatcher, dedup=guard
    )
    before = _counter("queued")

    response = await _post(app, _envelope(_text()))

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "queued": 1}
    assert dispatcher.dispatched == [], "the turn must NOT have completed before the ack"
    assert registry.rows[guard.inbound_key("wamid.1")] == "pending"
    assert _counter("queued") == before + 1

    # ...and the queued turn really runs afterwards, sealing its claim.
    gate.set()
    for _ in range(100):
        await asyncio.sleep(0)
        if dispatcher.dispatched:
            break
    assert len(dispatcher.dispatched) == 1
    assert registry.rows[guard.inbound_key("wamid.1")] == "processed"


async def test_ack_then_queue_still_suppresses_a_redelivery() -> None:
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(  # type: ignore[arg-type]
        _settings(ack_then_queue=True), dispatcher=dispatcher, dedup=_guard(registry)
    )
    payload = _envelope(_text())

    await _post(app, payload)
    second = await _post(app, payload)

    assert second.json() == {"status": "duplicate", "queued": 0, "duplicates": 1}


async def test_ack_then_queue_refuses_without_a_durable_registry() -> None:
    """ "Ack then queue" without the durable claim would be "ack then hope": nothing would record
    that a turn is owed. Refused, never degraded silently."""
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(ack_then_queue=True), dispatcher=dispatcher)  # type: ignore[arg-type]

    response = await _post(app, _envelope(_text()))

    assert response.status_code == 500
    assert response.json()["status"] == "dedup_unavailable"
    assert dispatcher.dispatched == []


@pytest.mark.parametrize("flag", [True, False])
async def test_both_flag_states_stay_wired(flag: bool) -> None:
    """Both states are exercised: the flag is a rollback lever, so neither branch may rot."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(  # type: ignore[arg-type]
        _settings(ack_then_queue=flag), dispatcher=dispatcher, dedup=_guard(registry)
    )

    response = await _post(app, _envelope(_text()))

    assert response.status_code == 200
    assert ("queued" in response.json()) is flag
