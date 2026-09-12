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

from maezo.platform.observability import get_metrics_collector
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dispatch import InboundMessage, InboundNonTextMessage
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
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_ack: bool = False,
        resultado: dict[str, Any] | None = None,
    ) -> None:
        self.dispatched: list[InboundMessage] = []
        self.acknowledged: list[InboundNonTextMessage] = []
        self._fail = fail
        self._fail_ack = fail_ack
        #: Estado final que `dispatch` devolve — o real carrega o grafo inteiro; aqui so' o que
        #: o corpo do ack pode ler (`response_text`, `conversation_id`).
        self.resultado = resultado if resultado is not None else {"ok": True}

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("dispatch boom")
        self.dispatched.append(message)
        return dict(self.resultado)

    async def acknowledge_non_text(self, message: InboundNonTextMessage) -> dict[str, Any]:
        if self._fail_ack:
            # The live shape of this failure today: `WhatsAppServer.send_message` refuses while
            # `WHATSAPP_PHONE_NUMBER_ID` is unprovisioned in Helm (owner-gated).
            raise ValueError("WhatsApp phone_number_id is not configured — refusing to send")
        self.acknowledged.append(message)
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


def _non_text_payload(
    *, from_number: str = "5511999999999", message_type: str = "image", msg_id: str = "wamid.9"
) -> bytes:
    envelope = {
        "entry": [
            {
                "changes": [
                    {"value": {"messages": [{"from": from_number, "id": msg_id, "type": message_type}]}}
                ]
            }
        ]
    }
    return json.dumps(envelope).encode()


def _webhook_requests_total(status: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_webhook_requests_total", {"tenant": "amh", "status": status}
    )
    return float(value or 0.0)


async def test_post_webhook_non_text_message_is_acknowledged_not_dropped() -> None:
    """Gap `WHATSAPP-NON-TEXT-DROPPED`: a voice note / photo used to be swallowed here with
    `{"dispatched": 0}` and no reply of any kind. It now takes the acknowledgement path — one
    fixed message through the same gated seam — and NEVER a Helena turn."""
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    payload = _non_text_payload(message_type="audio")
    before = _webhook_requests_total("non_text_acked")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 0, "failed": 0, "acked": 1}
    assert dispatcher.dispatched == []  # no Helena turn over a bodyless message
    assert dispatcher.acknowledged == [
        InboundNonTextMessage(from_number="5511999999999", message_type="audio", message_id="wamid.9")
    ]
    # The distinct metric status is what tells operators how much inbound volume this channel
    # cannot actually process (label cardinality unchanged: `message_type` is NOT a label).
    assert _webhook_requests_total("non_text_acked") == before + 1


async def test_post_webhook_status_callback_still_acks_without_any_send() -> None:
    """A delivery-status callback is NOT a message: nothing dispatched, nothing acknowledged."""
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    envelope = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    payload = json.dumps(envelope).encode()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 0}
    assert dispatcher.dispatched == []
    assert dispatcher.acknowledged == []


async def test_post_webhook_mixed_batch_dispatches_text_and_acknowledges_non_text() -> None:
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    envelope = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "551199", "id": "wamid.1", "type": "text", "text": {"body": "oi"}},
                                {"from": "551199", "id": "wamid.2", "type": "image"},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload = json.dumps(envelope).encode()
    before_ok = _webhook_requests_total("ok")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 1, "failed": 0, "acked": 1}
    assert len(dispatcher.dispatched) == 1
    assert len(dispatcher.acknowledged) == 1
    # A mixed batch is an ordinary "ok" request — `non_text_acked` means ONLY-non-text.
    assert _webhook_requests_total("ok") == before_ok + 1


async def test_post_webhook_acknowledgement_failure_never_escapes_the_webhook() -> None:
    """A refused ack (today's live shape: no `WHATSAPP_PHONE_NUMBER_ID` in Helm) is counted and
    logged, never raised out of `receive_event` — and, with nothing in the batch succeeding, the
    request answers 500 so Meta retries, exactly as a failed Helena turn does."""
    dispatcher = _FakeDispatcher(fail_ack=True)
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    payload = _non_text_payload()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 500
    assert resp.json() == {"status": "dispatch_failed", "dispatched": 0, "failed": 1, "acked": 0}
    assert dispatcher.acknowledged == []


async def test_post_webhook_partially_failed_batch_is_not_reported_as_total_failure() -> None:
    """One acknowledged message and one failed Helena turn is NOT `dispatch_failed`: something in
    the batch really happened, and a 500 would make Meta re-deliver the message already answered."""
    dispatcher = _FakeDispatcher(fail=True)
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]
    envelope = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "551199", "id": "wamid.1", "type": "text", "text": {"body": "oi"}},
                                {"from": "551199", "id": "wamid.2", "type": "image"},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload = json.dumps(envelope).encode()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "dispatched": 0, "failed": 1, "acked": 1}


async def test_post_webhook_non_text_without_a_dispatcher_returns_501_never_a_silent_drop() -> None:
    """No dispatcher on this replica means it can no more ACKNOWLEDGE than it can dispatch. The
    honest answer is the same explicit 501 a text message gets — not a 200 that pretends the
    beneficiary was answered."""
    app = create_app(_settings())
    payload = _non_text_payload()

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.status_code == 501
    assert resp.json()["status"] == "not_implemented"


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


# ---------------------------------------------------------------------------------------------
# `devolve_turno` (12/09/2026): o corpo do ack carrega o turno, e SO' sob o portao.
# ---------------------------------------------------------------------------------------------
# O texto que a Helena redige nunca foi lido por ninguem: ele vai para o envio do WhatsApp e morre
# num 401 de credencial de preenchimento em dev. Sem ver o texto nao ha' como avaliar se ele presta
# nem conferir se vaza orientacao clinica. Estes testes fixam as DUAS metades: com o portao o corpo
# ganha os campos; sem ele fica exatamente como a Meta sempre recebeu.


_TURNO = {
    "ok": True,
    "response_text": "Ola! Sou a Helena, navegadora de saude. Como posso ajudar?",
    "conversation_id": "wa:amh:hk1_0123456789abcdef",
}


async def test_ack_carrega_o_turno_quando_o_portao_esta_ligado() -> None:
    dispatcher = _FakeDispatcher(resultado=_TURNO)
    app = create_app(_settings(devolve_turno=True), dispatcher=dispatcher)
    payload = _text_message_payload(body="oi")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    corpo = resp.json()
    assert resp.status_code == 200
    assert corpo["dispatched"] == 1
    assert corpo["resposta"] == _TURNO["response_text"]
    assert corpo["conversation_id"] == _TURNO["conversation_id"]


async def test_ack_fica_intacto_quando_o_portao_esta_desligado() -> None:
    """O default. O corpo e' o que a Meta recebe em producao — nenhum campo novo aparece nele."""
    dispatcher = _FakeDispatcher(resultado=_TURNO)
    app = create_app(_settings(), dispatcher=dispatcher)
    payload = _text_message_payload(body="oi")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    corpo = resp.json()
    assert corpo == {"status": "ok", "dispatched": 1, "failed": 0}
    assert "resposta" not in corpo
    assert "conversation_id" not in corpo


async def test_turno_sem_texto_devolve_string_vazia_e_nao_inventa_nada() -> None:
    """Um turno cujo `response_text` veio vazio (HEL-07) nao vira texto fabricado no corpo."""
    dispatcher = _FakeDispatcher(resultado={"response_text": "", "conversation_id": "wa:amh:hk1_x"})
    app = create_app(_settings(devolve_turno=True), dispatcher=dispatcher)
    payload = _text_message_payload(body="oi")

    async with await _client(app) as client:
        resp = await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": _sign(payload)})

    assert resp.json()["resposta"] == ""
