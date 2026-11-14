"""C6 CI repair: actual fixed HTTP adapters, synthetic transport, no REAL/engine IO."""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import maezo.adapters.amh.commercial_http as transport_module
from maezo.adapters.amh.commercial import MAX_RESULT_BYTES, CommercialWire
from maezo.ports.commercial import CommercialRefusalError, PaymentChoiceAuthority, PaymentChoiceSelection
from tests.unit.test_commercial_c6 import PublicScope, PublicSource
from tests.unit.test_commercial_http import UnitSourceAuthority, qualified_scope, wire_command, wire_result
from tests.unit.test_commercial_http import schema_bytes as schema_bytes


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, events: list[str], *, fail: bool = False) -> None:
        self.body, self.events, self.fail = body, events, fail

    async def __aiter__(self):
        self.events.append("read")
        if self.fail:
            raise httpx.ReadTimeout("synthetic read failure")
        yield self.body

    async def aclose(self) -> None:
        self.events.append("response_closed")


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response, events: list[str]) -> None:
        self.response, self.events = response, events
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.events.append("dispatch")
        return self.response

    async def aclose(self) -> None:
        self.events.append("transport_closed")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["private", "public"])
@pytest.mark.parametrize("outcome", ["success", "redirect", "oversize", "read_error", "revoked", "encoded"])
async def test_actual_adapters_use_bounded_no_retry_transport_and_close_before_disclosure(
    monkeypatch, schema_bytes, kind, outcome
):
    events: list[str] = []
    body = json.dumps(wire_result()).encode() if kind == "private" else b'{"synthetic":true}'
    if outcome == "oversize":
        body = b" " * (MAX_RESULT_BYTES + 1)
    headers = {"Content-Type": "application/json", "Set-Cookie": "foreign=cookie"}
    if outcome == "encoded":
        headers["Content-Encoding"] = "gzip"
        body = gzip.compress(body)
    response = httpx.Response(
        302 if outcome == "redirect" else 200,
        headers=headers,
        stream=ObservedStream(body, events, fail=outcome == "read_error"),
    )
    transport = ObservedTransport(response, events)
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return transport

    def forbidden_client(*args, **kwargs):
        raise AssertionError("C6 transport must use the existing AMH public transport pattern")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
    monkeypatch.setenv("HTTPS_PROXY", "http://foreign.invalid:1")
    if kind == "private":
        scope = qualified_scope()
        authority = UnitSourceAuthority(scope)
        command = CommercialWire(schema_bytes).parse_command(json.dumps(wire_command()).encode())
        preparation = await authority.prepare(command, scope)
        adapter = transport_module.HttpCommercialExecutor(origin="https://amh.invalid", authority=authority)

        async def perform():
            return await adapter.execute(command, scope, preparation)

    else:
        scope = PublicScope().value
        authority = PublicSource(None)
        adapter = transport_module.HttpPublicCatalogSource(origin="https://amh.invalid", authority=authority)

        async def perform():
            return await adapter.read(scope)

    original_current = authority.current

    async def current(*args, phase):
        events.append(phase)
        if phase == "before_disclosure":
            assert events[-3:-1] == ["response_closed", "transport_closed"]
            if outcome == "revoked":
                raise CommercialRefusalError("unavailable", "source_unavailable")
        await original_current(*args, phase=phase)

    monkeypatch.setattr(authority, "current", current)
    if outcome == "success":
        assert await perform() == body
    else:
        with pytest.raises(httpx.ReadTimeout if outcome == "read_error" else CommercialRefusalError):
            await perform()
    assert options == [{"retries": 0, "trust_env": False}]
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == ("POST" if kind == "private" else "GET")
    assert str(request.url) == "https://amh.invalid/interop/sales/v1/" + (
        "commercial" if kind == "private" else "public/catalog"
    )
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 5.0)
    assert request.headers["authorization"].startswith("Bearer SYN-")
    assert request.headers["accept-encoding"] == "identity"
    if outcome == "encoded":
        assert "read" not in events  # Refuse representation before any decompression/stream read.
    assert "cookie" not in request.headers
    assert events.count("response_closed") == events.count("transport_closed") == 1
    assert ("before_disclosure" in events) == (outcome in {"success", "revoked"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,deadline",
    [
        ("private", "scope"),
        ("private", "preparation"),
        ("private", "connection"),
        ("private", "payment"),
        ("public", "scope"),
        ("public", "connection"),
    ],
)
async def test_expiry_during_transport_entry_refuses_before_any_dispatch(
    monkeypatch, schema_bytes, kind, deadline
):
    instant = datetime.now(UTC)
    expired = instant + timedelta(seconds=10)

    class Clock:
        value = instant

        @classmethod
        def now(cls, tz):
            return cls.value

    events = []

    class EntryTransport(ObservedTransport):
        async def __aenter__(self):
            Clock.value = expired
            return self

    transport = EntryTransport(httpx.Response(200, json=wire_result()), events)
    if kind == "private":
        scope = qualified_scope()
        if deadline == "scope":
            scope = replace(scope, valid_until=expired)
        authority = UnitSourceAuthority(scope)
        command = CommercialWire(schema_bytes).parse_command(json.dumps(wire_command()).encode())
        preparation = await authority.prepare(command, scope)
        if deadline == "preparation":
            preparation = replace(preparation, valid_until=expired)
        if deadline == "payment":
            selection = PaymentChoiceSelection(
                choice_ref="SYN-CHOICE",
                scope_kind="contract",
                scope_ref="SYN-CONTRACT",
                provider="stripe",
                method="card",
                operation="payment.create",
                use="one_time",
            )
            choice = PaymentChoiceAuthority(
                selection=selection,
                scope_digest=preparation.scope_digest,
                command_digest=preparation.command_digest,
                capability_ref="SYN-CAP",
                source_revision=1,
                account_binding_ref="SYN-ACCOUNT",
                contractual_use_ref="SYN-USE",
                currentness_ref="SYN-CURRENT",
                valid_until=expired,
            )
            preparation = replace(preparation, payment_choice=choice)
        connection = await authority.connection(command, scope, preparation)
        if deadline == "connection":
            connection = replace(connection, valid_until=expired)

        async def resolved_connection(*args):
            return connection

        monkeypatch.setattr(authority, "connection", resolved_connection)
        adapter = transport_module.HttpCommercialExecutor(
            origin="https://amh.invalid", authority=authority, transport=transport
        )

        async def perform():
            return await adapter.execute(command, scope, preparation)

    else:
        scope = PublicScope().value
        if deadline == "scope":
            scope = replace(scope, valid_until=expired)
        authority = PublicSource(None)
        connection = await authority.connection(scope)
        if deadline == "connection":
            connection = replace(connection, valid_until=expired)

        async def resolved_connection(*args):
            return connection

        monkeypatch.setattr(authority, "connection", resolved_connection)
        adapter = transport_module.HttpPublicCatalogSource(
            origin="https://amh.invalid", authority=authority, transport=transport
        )

        async def perform():
            return await adapter.read(scope)

    monkeypatch.setattr(transport_module, "datetime", Clock)
    with pytest.raises(CommercialRefusalError):
        await perform()
    assert transport.requests == []
    assert events == ["transport_closed"]
    assert authority.phases == ["before_io"]
