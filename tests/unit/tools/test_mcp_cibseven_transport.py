"""Unit tests for `maezo.tools.mcp_cibseven.transport` (ADR-0001, T1.11).

`CibSevenHttpTransport`'s wire-format assertions mirror `dmn_transport.py`'s own test style
(mocked `httpx.AsyncClient`, never a real engine — the real engine is exercised in
`tests/integration/`, ADR-0011). `FakeCibSevenTransport`/`start_process_idempotent` are exercised
directly (no mocking needed — they're pure Python).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    CibSevenHttpTransport,
    FakeCibSevenTransport,
    ProcessInstance,
    ProcessNotFoundError,
    start_process_idempotent,
)


def _mock_response(json_body: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body)
    if status_code >= 300:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=resp)
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# FakeCibSevenTransport — never imported by production code
# ---------------------------------------------------------------------------


async def test_fake_find_active_instance_none_when_unseeded() -> None:
    fake = FakeCibSevenTransport()
    assert await fake.find_active_instance("ESC-amh-1") is None


async def test_fake_start_process_instance_creates_active() -> None:
    fake = FakeCibSevenTransport()
    inst = await fake.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {"a": 1})
    assert inst.instance_id == "fake-ESC-amh-1"
    assert inst.state == "ACTIVE"
    assert inst.already_existed is False
    assert await fake.find_active_instance("ESC-amh-1") == inst


async def test_fake_get_process_status_raises_when_not_found() -> None:
    fake = FakeCibSevenTransport()
    with pytest.raises(ProcessNotFoundError):
        await fake.get_process_status("nope")


async def test_fake_correlate_message_records_calls() -> None:
    fake = FakeCibSevenTransport()
    await fake.correlate_message("msg.auth.docs_received", "AUTH-amh-1", {"x": 1})
    assert fake.correlate_calls == [
        {
            "message_name": "msg.auth.docs_received",
            "business_key": "AUTH-amh-1",
            "variables": {"x": 1},
            "correlation_keys": {},
            "all_matching": False,
        }
    ]


# ---------------------------------------------------------------------------
# start_process_idempotent — the ONE call site agent graphs should use
# ---------------------------------------------------------------------------


async def test_start_process_idempotent_starts_when_no_active_instance() -> None:
    fake = FakeCibSevenTransport()
    inst = await start_process_idempotent(
        fake, process_key="SP-OP-ESCALATION-001", business_key="ESC-amh-1", variables={}
    )
    assert inst.already_existed is False
    assert inst.instance_id == "fake-ESC-amh-1"


async def test_start_process_idempotent_returns_existing_never_double_starts() -> None:
    fake = FakeCibSevenTransport()
    fake.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-ESCALATION-001",
            business_key="ESC-amh-1",
            state="ACTIVE",
            already_existed=True,
        )
    )
    inst = await start_process_idempotent(
        fake, process_key="SP-OP-ESCALATION-001", business_key="ESC-amh-1", variables={"x": 1}
    )
    assert inst.instance_id == "existing-1"
    assert inst.already_existed is True


# ---------------------------------------------------------------------------
# CibSevenHttpTransport — wire format (mocked httpx client, never a real engine here)
# ---------------------------------------------------------------------------


async def test_http_find_active_instance_queries_business_key_and_active_true() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response([{"id": "proc-1", "processDefinitionKey": "SP-OP-ESCALATION-001"}])
    )

    inst = await transport.find_active_instance("ESC-amh-1")

    assert inst is not None
    assert inst.instance_id == "proc-1"
    assert inst.already_existed is True
    call_args = transport._client.get.call_args
    assert call_args[0][0] == "/process-instance"
    assert call_args[1]["params"] == {"businessKey": "ESC-amh-1", "active": "true"}


async def test_http_find_active_instance_returns_none_when_empty() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(return_value=_mock_response([]))  # type: ignore[method-assign]
    assert await transport.find_active_instance("ESC-amh-1") is None


async def test_http_start_process_instance_posts_business_key_and_typed_variables() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response({"id": "proc-2", "state": "ACTIVE"})
    )

    inst = await transport.start_process_instance(
        "SP-OP-ESCALATION-001", "ESC-amh-1", {"severidade": "grave", "count": 3, "flag": True}
    )

    assert inst.instance_id == "proc-2"
    assert inst.already_existed is False
    call_args = transport._client.post.call_args
    assert call_args[0][0] == "/process-definition/key/SP-OP-ESCALATION-001/start"
    payload = call_args[1]["json"]
    assert payload["businessKey"] == "ESC-amh-1"
    assert payload["variables"]["severidade"] == {"value": "grave", "type": "String"}
    assert payload["variables"]["count"] == {"value": 3, "type": "Integer"}
    assert payload["variables"]["flag"] == {"value": True, "type": "Boolean"}


async def test_http_start_process_instance_long_typed_above_int32() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(return_value=_mock_response({"id": "proc-3"}))  # type: ignore[method-assign]

    huge = 5_000_000_000  # exceeds java.lang.Integer max
    await transport.start_process_instance("SP-OP-PAGTO-001", "PAG-amh-1", {"valor_pagamento_cents": huge})

    payload = transport._client.post.call_args[1]["json"]
    assert payload["variables"]["valor_pagamento_cents"] == {"value": huge, "type": "Long"}


async def test_http_start_process_instance_raises_cibseven_error_on_http_failure() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response({"error": "boom"}, status_code=500)
    )
    with pytest.raises(CibSevenError):
        await transport.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {})


async def test_http_start_process_instance_raises_cibseven_error_on_unreachable() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))  # type: ignore[method-assign]
    with pytest.raises(CibSevenError):
        await transport.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {})


async def test_http_correlate_message_posts_message_endpoint() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(return_value=_mock_response({}))  # type: ignore[method-assign]

    await transport.correlate_message("msg.auth.docs_received", "AUTH-amh-1", {"a": 1})

    call_args = transport._client.post.call_args
    assert call_args[0][0] == "/message"
    payload = call_args[1]["json"]
    assert payload["messageName"] == "msg.auth.docs_received"
    assert payload["businessKey"] == "AUTH-amh-1"


async def test_http_close_closes_client() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.aclose = AsyncMock()  # type: ignore[method-assign]
    await transport.close()
    transport._client.aclose.assert_awaited_once()
