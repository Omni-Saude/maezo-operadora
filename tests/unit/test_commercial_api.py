"""Commercial UNIT ingress privacy: unbound transport never consumes subject data."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI, Request

from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_command, commercial_router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"cookie", b"__Host-maezo-session=browser-claims-admin")],
        [(b"authorization", b"Bearer arbitrary-m2m"), (b"x-role", b"customer")],
    ],
)
async def test_unbound_route_refuses_before_receive_even_with_browser_authority_claims(
    headers: list[tuple[bytes, bytes]],
) -> None:
    app = FastAPI()
    app.include_router(commercial_router)
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        received = True
        raise AssertionError("Subject data must not be consumed before admission")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": COMMERCIAL_PATH,
            "headers": headers,
            "query_string": b"",
            "app": app,
        },
        receive=receive,
    )
    result = await commercial_command(request)
    assert result.status_code == 503
    assert json.loads(bytes(result.body)) == {
        "schema_version": "omni-commercial.proposed.v1",
        "status": "unavailable",
        "operation_ref": None,
        "reason_code": "binding_unqualified",
        "data": None,
    }
    assert result.headers["cache-control"] == "no-store"
    assert not received


def test_only_one_exact_command_route_is_registered() -> None:
    routes = [(route.path, route.methods) for route in commercial_router.routes if route.methods == {"POST"}]
    assert routes == [(COMMERCIAL_PATH, {"POST"})]
