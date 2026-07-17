"""Unit tests for `maezo.platform.health` — the generic health/readiness/metrics ASGI app."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from prometheus_client import CollectorRegistry, Counter

from maezo.platform import health as health_module
from maezo.platform.health import CheckResult, create_health_app


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_healthz_ok_when_live() -> None:
    app = create_health_app(readiness_checks=[])
    async with await _client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_healthz_503_when_not_live() -> None:
    app = create_health_app(readiness_checks=[], is_live=lambda: False)
    async with await _client(app) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "shutting_down"}


async def test_readyz_200_with_no_checks() -> None:
    app = create_health_app(readiness_checks=[])
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True, "checks": []}


async def test_readyz_200_when_all_healthy() -> None:
    async def ok() -> CheckResult:
        return CheckResult(name="ok", healthy=True)

    app = create_health_app(readiness_checks=[ok])
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


async def test_readyz_503_when_one_unhealthy() -> None:
    async def ok() -> CheckResult:
        return CheckResult(name="ok", healthy=True)

    async def bad() -> CheckResult:
        return CheckResult(name="bad", healthy=False, detail="nope")

    app = create_health_app(readiness_checks=[ok, bad])
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    names = {c["name"] for c in body["checks"]}
    assert names == {"ok", "bad"}


async def test_readyz_check_raising_becomes_unhealthy_never_500() -> None:
    async def boom() -> CheckResult:
        raise RuntimeError("kaboom")

    app = create_health_app(readiness_checks=[boom])
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    check = resp.json()["checks"][0]
    assert check["healthy"] is False
    assert "kaboom" in check["detail"]


async def test_readyz_check_timeout_becomes_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "_READINESS_CHECK_TIMEOUT_SECONDS", 0.01)

    async def hangs() -> CheckResult:
        await asyncio.sleep(1.0)
        return CheckResult(name="hangs", healthy=True)

    app = create_health_app(readiness_checks=[hangs])
    async with await _client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    check = resp.json()["checks"][0]
    assert check["healthy"] is False
    assert "timeout" in check["detail"]


async def test_metrics_exposes_prometheus_text() -> None:
    registry = CollectorRegistry()
    counter = Counter("test_metric_total", "a test counter", registry=registry)
    counter.inc()

    app = create_health_app(readiness_checks=[], registry=registry)
    async with await _client(app) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert b"test_metric_total 1.0" in resp.content


def test_build_health_server_returns_uvicorn_server() -> None:
    from maezo.platform.health import build_health_server

    app = create_health_app(readiness_checks=[])
    server = build_health_server(app, port=18123)
    assert server.config.port == 18123
