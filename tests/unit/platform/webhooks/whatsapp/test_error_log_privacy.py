"""Rendered failure logs, not capture_logs: ADR-0006 and R6 privacy successors.

The fake registry proves the caller's claim/seal/release protocol, never lease durability.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import sys
from collections.abc import Iterator
from typing import Any, cast

import pytest
import structlog

from maezo.platform.driver_idempotency import DedupRegistryUnavailableError
from maezo.platform.observability import setup_observability
from maezo.platform.webhooks.whatsapp import app as app_module
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage, InboundNonTextMessage
from tests.support.dedup_fakes import FakeDedupRegistry
from tests.unit.platform.webhooks.whatsapp.test_app_dedup import (
    _envelope,
    _FakeDispatcher,
    _guard,
    _non_text,
    _post,
    _settings,
    _text,
)

# Constructed from low-entropy synthetic data; no real-shaped opaque secret fixture in git.
_PHONE = "55" + "11" + "9" * 9
_B64 = base64.b64encode(("test:" + _PHONE + ":message:" + "a" * 12).encode()).decode()
_WAMID = "wamid." + _B64
_NARRATIVE = "sentinela paciente teste relato clinico"
_RAW_ERROR = json.dumps({"payload": {"message": _WAMID, "nome": _NARRATIVE}})


@pytest.fixture
def rendered_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    original = structlog.get_config().copy()
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    factory = structlog.PrintLoggerFactory(file=stream)
    monkeypatch.setattr(structlog, "PrintLoggerFactory", lambda: factory)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    monkeypatch.delenv("MAEZO_LOG_LEVEL", raising=False)
    provider = setup_observability(service_name="test-error-privacy", otlp_endpoint="")
    monkeypatch.setattr(app_module, "logger", structlog.get_logger(app_module.__name__))
    stream.seek(0)
    stream.truncate()
    try:
        yield stream
    finally:
        provider.shutdown()
        structlog.configure(**original)


def _assert_safe(stream: io.StringIO, event: str, *diagnostics: str) -> None:
    rendered = stream.getvalue()
    assert rendered and event in rendered
    for raw in (_WAMID, _B64, _PHONE, _NARRATIVE):
        assert raw not in rendered, "raw value escaped into a rendered log"
    for diagnostic in diagnostics:
        assert diagnostic in rendered


class _FailingRegistry(FakeDedupRegistry):
    def _check(self, operation: str = "") -> None:
        if operation in self.fail_ops:
            raise DedupRegistryUnavailableError(_RAW_ERROR)


class _OnceFailingDispatcher(_FakeDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def _check(self) -> None:
        if self.fail:
            self.fail = False
            try:
                raise ValueError(_RAW_ERROR)
            except ValueError as cause:
                raise RuntimeError(_RAW_ERROR) from cause

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        self._check()
        return await super().dispatch(message)

    async def acknowledge_non_text(self, message: InboundNonTextMessage) -> dict[str, Any]:
        self._check()
        return await super().acknowledge_non_text(message)


@pytest.mark.parametrize("non_text", [False, True])
async def test_dispatch_trace_privacy_preserves_retry_then_seal_and_dedup(
    rendered_logs: io.StringIO, non_text: bool
) -> None:
    registry = FakeDedupRegistry()
    dispatcher = _OnceFailingDispatcher()
    app = app_module.create_app(
        _settings(), dispatcher=cast(HelenaDispatcher, dispatcher), dedup=_guard(registry)
    )
    payload = _envelope(_non_text(_WAMID) if non_text else _text(_WAMID))
    failed = await _post(app, payload)
    recovered = await _post(app, payload)
    duplicate = await _post(app, payload)
    assert failed.status_code == 500
    assert recovered.status_code == duplicate.status_code == 200
    assert len(dispatcher.acknowledged if non_text else dispatcher.dispatched) == 1
    assert [operation for operation, _ in registry.calls] == [
        "claim",
        "release",
        "claim",
        "mark_processed",
        "claim",
    ]
    _assert_safe(rendered_logs, "whatsapp_dispatch_failed", "RuntimeError", "ValueError", "_check", "app.py")


@pytest.mark.parametrize("operation", ["claim", "mark_processed", "release"])
async def test_registry_error_privacy_preserves_ack_and_recovery(
    rendered_logs: io.StringIO, operation: str
) -> None:
    registry = _FailingRegistry(fail_ops=frozenset({operation}))
    dispatcher = _OnceFailingDispatcher()
    dispatcher.fail = operation == "release"
    app = app_module.create_app(
        _settings(), dispatcher=cast(HelenaDispatcher, dispatcher), dedup=_guard(registry)
    )
    failed = await _post(app, _envelope(_text(_WAMID)))
    assert failed.status_code == (200 if operation == "mark_processed" else 500)
    if operation == "claim":
        assert dispatcher.dispatched == [] and registry.rows == {}
    elif operation == "mark_processed":
        assert len(dispatcher.dispatched) == 1
        assert list(registry.rows.values()) == ["pending"]
    else:
        assert dispatcher.dispatched == []
        assert list(registry.rows.values()) == ["pending"]
    registry.fail_ops = frozenset()
    recovered = await _post(app, _envelope(_text(_WAMID)))
    assert recovered.status_code == 200
    # Failed seal/withdraw keeps its pending claim; retry is suppressed until the real lease expires.
    assert len(dispatcher.dispatched) == (0 if operation == "release" else 1)
    event = {
        "claim": "whatsapp_webhook_dedup_unavailable",
        "mark_processed": "whatsapp_webhook_dedup_seal_failed",
        "release": "whatsapp_webhook_dedup_release_failed",
    }[operation]
    _assert_safe(rendered_logs, event, "DedupRegistryUnavailableError")


def test_exception_group_and_nested_error_fields_are_safe_with_policy_off(rendered_logs: io.StringIO) -> None:
    from maezo.platform.privacy.phi_key_policy import phi_key_policy

    assert not phi_key_policy().scrubbing_enabled
    nested = {"errors": [{"message": _RAW_ERROR, "detail": [_NARRATIVE, {"input": _WAMID}]}]}
    before = copy.deepcopy(nested)
    try:
        raise ExceptionGroup(_NARRATIVE, [ValueError(_RAW_ERROR), RuntimeError(_RAW_ERROR)])
    except ExceptionGroup:
        structlog.get_logger().error("nested_failure", details=nested, exc_info=True, operation="dispatch")
    assert nested == before
    _assert_safe(
        rendered_logs, "nested_failure", "ExceptionGroup", "ValueError", "RuntimeError", "operation=dispatch"
    )


def test_success_logs_keep_routing_and_correlation(rendered_logs: io.StringIO) -> None:
    structlog.get_logger().info(
        "successful_dispatch", tenant="amh", outcome="dispatched", count=2, details={"status": "ok"}
    )
    _assert_safe(rendered_logs, "successful_dispatch", "tenant=amh", "outcome=dispatched", "count=2", "ok")
