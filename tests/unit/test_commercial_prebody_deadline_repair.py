"""Mounted ASGI route UNIT: subject admission must survive the last publication IO.

All authority/source objects are synthetic test doubles. The actual C6-4 codec,
CommercialGateway and commercial router execute without a server or real source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_router
from maezo.ports.commercial import (
    CommercialCommand,
    CommercialPreparation,
    CommercialPublication,
    CommercialResultAuthority,
    CommercialScope,
    OperationalResult,
    command_digest,
    scope_digest,
)
from tests.unit.test_commercial_gateway import NOW, UnitAdmission, UnitSource, scope
from tests.unit.test_commercial_http import schema_bytes as schema_bytes
from tests.unit.test_commercial_http import wire_command, wire_result


class ReceiveProbeWire(CommercialWire):
    def __init__(self, schema: bytes) -> None:
        super().__init__(schema)
        self.parsed = 0

    def parse_command(self, body: bytes) -> CommercialCommand:
        self.parsed += 1
        return super().parse_command(body)


class PublicationClockSource(UnitSource):
    def __init__(
        self,
        value: CommercialScope,
        codec: ReceiveProbeWire,
        tick: list[datetime],
        elapsed: timedelta,
    ) -> None:
        super().__init__(value)
        self.codec, self.tick, self.elapsed = codec, tick, elapsed
        self.publication_error: Exception | None = None

    async def admit(self, value: CommercialScope) -> CommercialPublication:
        proof = await super().admit(value)
        if self.calls.count("publication") == 1:
            # The legitimate publication provider consumes time while its own
            # independently longer proof remains valid. No malformed scope/proof.
            await asyncio.sleep(0)
            self.tick[0] += self.elapsed
            if self.publication_error is not None:
                raise self.publication_error
        return proof

    async def execute(
        self,
        command: CommercialCommand,
        value: CommercialScope,
        preparation: CommercialPreparation,
    ) -> OperationalResult:
        self.calls.append("execute")
        self.executed_commands.append(command.command)
        result = wire_result()
        result["operation_ref"] = preparation.operation_ref
        return self.codec.parse_result(json.dumps(result).encode(), command)

    async def result_authority(
        self,
        command: CommercialCommand,
        value: CommercialScope,
        result: OperationalResult,
    ) -> CommercialResultAuthority:
        self.calls.append("result-authority")
        return CommercialResultAuthority(
            command_digest=command_digest(command),
            scope_digest=scope_digest(value),
            result_digest=hashlib.sha256(result.wire_bytes).hexdigest(),
            resource_grant_ref="SYN-RESULT-GRANT",
            currentness_ref="SYN-CURRENT-RESULT",
            valid_until=NOW + timedelta(minutes=5),
        )


async def request_route(gateway: CommercialGateway) -> tuple[int, dict[str, Any], int, dict[str, str]]:
    app = FastAPI()
    app.state.commercial_gateway = gateway
    app.include_router(commercial_router)
    received = 0
    messages: list[dict[str, Any]] = []
    body = json.dumps(wire_command()).encode()

    async def receive() -> dict[str, Any]:
        nonlocal received
        received += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": COMMERCIAL_PATH,
            "raw_path": COMMERCIAL_PATH.encode(),
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "extensions": {"sales.commercial.connection_ref": "SYN-SERVER-CONNECTION"},
            "server": ("synthetic.test", 443),
            "client": ("127.0.0.1", 1),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(raw), received, {k.decode(): v.decode() for k, v in start["headers"]}


def composed(schema: bytes, elapsed: timedelta) -> tuple[Any, ...]:
    value = replace(
        scope(), allowed_operations=frozenset({"operator.get"}), valid_until=NOW + timedelta(seconds=1)
    )
    tick = [NOW]
    codec = ReceiveProbeWire(schema)
    admission = UnitAdmission(value)
    source = PublicationClockSource(value, codec, tick, elapsed)
    gateway = CommercialGateway(codec=codec, admission=admission, source=source, clock=lambda: tick[0])
    return gateway, codec, admission, source, value, tick


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "elapsed", [timedelta(seconds=1), timedelta(seconds=2)], ids=["at-deadline", "past-deadline"]
)
async def test_expiry_during_final_publication_refuses_before_subject_receive(
    schema_bytes: bytes, elapsed: timedelta
) -> None:
    gateway, codec, admission, source, value, tick = composed(schema_bytes, elapsed)
    status, result, received, headers = await request_route(gateway)
    assert status == 403
    assert value.valid_until <= tick[0] < source.proof.valid_until
    assert received == 0
    assert codec.parsed == 0
    assert source.calls == ["publication"]
    assert admission.calls == ["acquire", "before_body"]
    assert source.executed_commands == []
    assert result["data"] is None and result["operation_ref"] is None
    assert result["reason_code"] == "admission_required"
    assert headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_still_valid_scope_allows_real_router_parse_and_one_synthetic_source_call(
    schema_bytes: bytes,
) -> None:
    gateway, codec, admission, source, value, tick = composed(schema_bytes, timedelta(milliseconds=500))
    status, result, received, headers = await request_route(gateway)
    assert tick[0] < value.valid_until < source.proof.valid_until
    assert status == 200 and result["status"] == "succeeded"
    assert received == 1 and codec.parsed >= 1
    assert source.calls.count("prepare") == source.calls.count("execute") == 1
    assert source.executed_commands == ["operator.get"]
    assert admission.calls[0:2] == ["acquire", "before_body"]
    assert headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_initial_scope_expiry_and_publication_expiry_keep_early_refusal(schema_bytes: bytes) -> None:
    gateway, codec, admission, source, _, tick = composed(schema_bytes, timedelta(0))
    tick[0] = NOW + timedelta(seconds=1)
    status, _, received, _ = await request_route(gateway)
    assert status == 403 and received == codec.parsed == 0
    assert source.calls == [] and admission.calls == ["acquire"]
    gateway, codec, admission, source, _, _ = composed(schema_bytes, timedelta(milliseconds=500))
    source.proof = replace(source.proof, valid_until=NOW + timedelta(milliseconds=250))
    status, result, received, _ = await request_route(gateway)
    assert status == 503 and received == codec.parsed == 0
    assert result["reason_code"] == "binding_unqualified"
    assert source.calls == ["publication"] and source.executed_commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [RuntimeError("SYN-PRIVATE-ERROR"), TimeoutError("SYN-PRIVATE-ERROR")])
async def test_publication_io_exceptions_remain_bounded_without_body_intake(
    schema_bytes: bytes, exception: Exception
) -> None:
    gateway, codec, admission, source, _, _ = composed(schema_bytes, timedelta(milliseconds=500))
    source.publication_error = exception
    status, result, received, _ = await request_route(gateway)
    assert status == 503 and result["reason_code"] == "binding_unqualified"
    assert received == codec.parsed == 0
    assert source.calls == ["publication"] and source.executed_commands == []
    assert admission.calls == ["acquire", "before_body"]
    assert "SYN-PRIVATE-ERROR" not in json.dumps(result)
