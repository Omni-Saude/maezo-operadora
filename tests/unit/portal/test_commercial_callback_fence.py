"""E-SF-03 (EP-CORE-11b): no inbound commercial callback route is mounted.

Decision A (pull-first) of the CORE-11 record (to be registered as ADR-0078): in v1 the
Maezo learns about asynchronous commercial state by pulling (`operation.get` /
`journey.get`, `work.reconcile`), never through a new callback endpoint. C6-4
`x-workflow.callbacks`: "no new external callback endpoint/schema is introduced. Any
future source callback requires its own qualification and cannot bypass authenticated
source readback." The guard reads the routes the real `create_app` mounts and fails,
listing the route, when anything beyond the closed commercial list appears under the
commercial prefix.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from tests.unit.portal.test_human_session import ORIGIN, config

from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore

COMMERCIAL_PREFIX = "/api/v1/sales"
#: The closed commercial route list (DEC CORE-11, EP-CORE-11b): the fixed Sales POST and
#: the public catalog GET. Spelled out, not imported, so a new route cannot hide behind it.
COMMERCIAL_ROUTES = frozenset(
    {
        ("POST", "/api/v1/sales/commercial"),
        ("GET", "/api/v1/sales/public/catalog"),
    }
)


def _walk(routes: list[Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    # FastAPI >= 0.139 keeps an included router as `_IncludedRouter` (no `.path`), so a flat
    # scan of `app.routes` would see nothing and pass vacuously; descend into it instead.
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _walk(original.routes, prefix + route.include_context.prefix)
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in getattr(route, "methods", None) or {"*"}:
            yield method, prefix + path


def _mounted() -> tuple[Any, set[tuple[str, str]]]:
    app = create_app(config(), store=LocalTestIdentityStore("test-tenant"))
    return app, set(_walk(app.routes))


def test_no_inbound_commercial_callback_route_is_mounted() -> None:
    app, mounted = _mounted()
    commercial = {
        (m, p) for m, p in mounted if p == COMMERCIAL_PREFIX or p.startswith(COMMERCIAL_PREFIX + "/")
    }
    extra = sorted(commercial - COMMERCIAL_ROUTES)
    assert not extra, f"commercial route outside the closed list (CORE-11 decision A): {extra}"
    # Non-vacuous: the walker sees the real mount — both closed-list routes are present and
    # every documented path of the app was found by the walk.
    assert commercial == COMMERCIAL_ROUTES
    assert set(app.openapi()["paths"]) <= {p for _, p in mounted}
    # No callback/webhook ingress anywhere names a commercial surface; the only callback
    # route is the staff OIDC login return.
    hooks = sorted((m, p) for m, p in mounted if "callback" in p.lower() or "webhook" in p.lower())
    assert hooks == [("GET", "/api/v1/portal/auth/callback")], hooks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/sales/commercial/callback",
        "/api/v1/sales/commercial/notifications",
        "/api/v1/sales/webhooks/commercial",
    ],
)
async def test_commercial_callback_probe_is_not_served(path: str) -> None:
    # Behavioural twin of the route walk: an external notification POST under the
    # commercial prefix finds no route at all.
    app, _ = _mounted()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(path, json={"event": "SYN-EVENT"})
    assert response.status_code == 404
