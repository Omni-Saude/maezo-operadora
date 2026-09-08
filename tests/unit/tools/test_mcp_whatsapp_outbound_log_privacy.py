"""ADR-0006: response IDs belong to the authorized caller, never send diagnostics.

Local HTTP MockTransport only. The registry fake checks the client protocol, not
database durability, lease expiry, engine behavior or delivery by Meta.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import structlog

from maezo.platform.observability import get_metrics_collector, setup_observability
from maezo.tools.mcp_whatsapp import server as server_module
from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings
from tests.support.dedup_fakes import FakeDedupRegistry

_PHONE = "55" + "11" + "9" * 9
_ENCODED = base64.b64encode(("synthetic:" + _PHONE + ":outbound").encode()).decode()
_WAMID = "wamid." + _ENCODED
_TEXT = "sentinela sintetica de narrativa privada outbound"
_TOKEN = "synthetic-token-" + "q" * 24
_KEY = "wa:outbound:amh:hk1_synthetic:1"
_BODY = {"messaging_product": "whatsapp", "messages": [{"id": _WAMID}]}
_NESTED = {"errors": [{"message": _WAMID, "payload": {"detail": _TEXT}}]}
_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def rendered_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    original = structlog.get_config().copy()
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    factory = structlog.PrintLoggerFactory(file=stream)
    monkeypatch.setattr(structlog, "PrintLoggerFactory", lambda: factory)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    monkeypatch.delenv("MAEZO_LOG_LEVEL", raising=False)
    provider = setup_observability(service_name="test-outbound-privacy", otlp_endpoint="")
    monkeypatch.setattr(server_module, "logger", structlog.get_logger(server_module.__name__))
    stream.seek(0)
    stream.truncate()
    try:
        yield stream
    finally:
        provider.shutdown()
        structlog.configure(**original)


def _server(registry: FakeDedupRegistry | None = None) -> WhatsAppServer:
    return WhatsAppServer(
        settings=WhatsAppSettings(
            base_url="https://whatsapp.invalid/v18.0",
            whatsapp_token=_TOKEN,
            phone_number_id="synthetic-business-id",
        ),
        dedup=registry,
    )


def _transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "https://whatsapp.invalid/v18.0/synthetic-business-id/messages"
        assert request.headers["Authorization"] == "Bearer " + _TOKEN
        assert json.loads(request.content) == {
            "messaging_product": "whatsapp",
            "to": _PHONE,
            "type": "text",
            "text": {"body": _TEXT},
        }
        return handler(request)

    def client(*, timeout: float) -> httpx.AsyncClient:
        assert timeout == 30.0
        return _ASYNC_CLIENT(transport=httpx.MockTransport(handle), timeout=timeout)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests


def _assert_private(stream: io.StringIO, caplog: pytest.LogCaptureFixture, event: str) -> None:
    rendered = stream.getvalue()
    assert event in rendered, "the diagnostic must still be emitted"
    combined = rendered + caplog.text
    for sentinel in (_WAMID, _ENCODED, _PHONE, _TEXT, _TOKEN):
        assert sentinel not in combined, "private value escaped to rendered or stdlib logs"
    assert "message_id=" not in rendered


@pytest.mark.parametrize("message_id", [_WAMID, "opaque-synthetic-id"])
async def test_response_id_is_returned_unchanged_without_logging_it(
    monkeypatch: pytest.MonkeyPatch,
    rendered_logs: io.StringIO,
    caplog: pytest.LogCaptureFixture,
    message_id: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    body = {"messages": [{"id": message_id}], "contacts": [{"wa_id": _PHONE}], "detail": _TEXT}
    requests = _transport(monkeypatch, lambda _: httpx.Response(200, json=body))
    result = await _server().send_message(_PHONE, _TEXT)
    assert result == body
    assert len(requests) == 1
    assert any(record.name == "httpx" for record in caplog.records), "stdlib capture must be live"
    _assert_private(rendered_logs, caplog, "whatsapp_message_sent")
    assert message_id not in rendered_logs.getvalue() + caplog.text


@pytest.mark.parametrize("messages", [None, [], [{"id": _WAMID}], _TEXT])
async def test_success_diagnostics_never_inspect_the_response_payload(
    monkeypatch: pytest.MonkeyPatch,
    rendered_logs: io.StringIO,
    caplog: pytest.LogCaptureFixture,
    messages: Any,
) -> None:
    # A diagnostic must not fail an accepted send by traversing optional upstream data.
    body = {"messages": messages, "errors": _NESTED}
    _transport(monkeypatch, lambda _: httpx.Response(200, json=body))
    assert await _server().send_message(_PHONE, _TEXT) == body
    _assert_private(rendered_logs, caplog, "whatsapp_message_sent")


async def test_success_seals_and_suppresses_duplicate_without_exposing_response_id(
    monkeypatch: pytest.MonkeyPatch, rendered_logs: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _transport(monkeypatch, lambda _: httpx.Response(200, json=_BODY))
    registry = FakeDedupRegistry()
    server = _server(registry)
    assert await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY) == _BODY
    assert await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY) == {
        "suppressed_duplicate": True,
        "idempotency_key": _KEY,
    }
    assert len(requests) == 1 and registry.rows == {_KEY: "processed"}
    _assert_private(rendered_logs, caplog, "whatsapp_send_suppressed_duplicate")


class _PrivateFailureRegistry(FakeDedupRegistry):
    def _check(self, operation: str = "") -> None:
        if operation in self.fail_ops:
            try:
                raise ValueError(json.dumps(_NESTED))
            except ValueError as cause:
                raise RuntimeError(_TEXT) from cause


async def test_seal_error_keeps_delivery_and_counter_without_rendering_nested_error(
    monkeypatch: pytest.MonkeyPatch, rendered_logs: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _transport(monkeypatch, lambda _: httpx.Response(200, json=_BODY))
    registry = _PrivateFailureRegistry(fail_ops=frozenset({"mark_processed"}))
    metrics = get_metrics_collector().registry
    metric = "maezo_whatsapp_send_seal_failures_total"
    before = metrics.get_sample_value(metric) or 0
    server = _server(registry)
    assert await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY) == _BODY
    assert (await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY))["suppressed_duplicate"]
    assert len(requests) == 1 and registry.rows == {_KEY: "pending"}
    assert metrics.get_sample_value(metric) == before + 1
    _assert_private(rendered_logs, caplog, "whatsapp_send_claim_seal_failed")


@pytest.mark.parametrize("release_fails", [False, True])
async def test_send_error_remains_original_and_rendered_caller_trace_is_private(
    monkeypatch: pytest.MonkeyPatch,
    rendered_logs: io.StringIO,
    caplog: pytest.LogCaptureFixture,
    release_fails: bool,
) -> None:
    failure = ExceptionGroup(_TEXT, [ValueError(json.dumps(_NESTED)), RuntimeError(_WAMID)])

    def failed_send(_: httpx.Request) -> httpx.Response:
        raise failure

    requests = _transport(monkeypatch, failed_send)
    registry = _PrivateFailureRegistry(fail_ops=frozenset({"release"}) if release_fails else frozenset())
    server = _server(registry)
    with pytest.raises(ExceptionGroup) as raised:
        await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY)
    assert raised.value is failure, "cleanup must preserve the exact transport exception"
    # This is the real configured renderer used by a caller catching the unchanged failure.
    structlog.get_logger().error("outbound_caller_failed", errors=_NESTED, exc_info=raised.value)
    assert len(requests) == 1
    assert registry.rows == ({_KEY: "pending"} if release_fails else {})
    assert "whatsapp_message_sent" not in rendered_logs.getvalue()
    _assert_private(rendered_logs, caplog, "outbound_caller_failed")
    assert "ExceptionGroup" in rendered_logs.getvalue() and "ValueError" in rendered_logs.getvalue()
    if release_fails:
        _assert_private(rendered_logs, caplog, "whatsapp_send_claim_release_failed")
    else:
        recovered = _transport(monkeypatch, lambda _: httpx.Response(200, json=_BODY))
        assert await server.send_message(_PHONE, _TEXT, idempotency_key=_KEY) == _BODY
        assert len(recovered) == 1 and registry.rows == {_KEY: "processed"}
        _assert_private(rendered_logs, caplog, "whatsapp_message_sent")
