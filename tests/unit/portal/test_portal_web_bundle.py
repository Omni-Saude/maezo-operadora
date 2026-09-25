"""The BFF serves the portal SPA on its own origin without ever answering for the API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from tests.unit.portal.test_human_session import ISSUER, ORIGIN, PREFIX, SUBJECT, config, membership

from maezo.portal.api import spa
from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore

pytestmark = pytest.mark.asyncio

INDEX = (
    "<!doctype html><html><body><div id=root></div>"
    "<script type=module src=/assets/index-abc123.js></script></body></html>"
)
SECRET = "outside-the-bundle"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / ".env").write_text(SECRET, encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return root


async def _client(web_root: Path | None, **updates: object) -> AsyncIterator[httpx.AsyncClient]:
    store = LocalTestIdentityStore("test-tenant")
    store.memberships[(ISSUER, SUBJECT)] = membership()
    app = create_app(config(**updates), store=store, web_root=web_root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        yield client


@pytest.fixture
async def c(bundle: Path) -> AsyncIterator[httpx.AsyncClient]:
    async for client in _client(bundle):
        yield client


def _spa_headers(response: httpx.Response) -> None:
    csp = response.headers.get_list("content-security-policy")
    assert csp == [spa.CSP]
    assert "unsafe-inline" not in csp[0] and "unsafe-eval" not in csp[0]
    assert "frame-ancestors 'none'" in csp[0]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


async def test_root_serves_index_no_store_with_strict_headers(c: httpx.AsyncClient) -> None:
    response = await c.get("/")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers.get_list("cache-control") == ["no-store"]
    _spa_headers(response)


async def test_client_route_falls_back_to_index(c: httpx.AsyncClient) -> None:
    for path in ("/tarefas", "/casos/123/detalhe", "/index.html"):
        response = await c.get(path)
        assert response.status_code == 200, path
        assert response.text == INDEX
        assert response.headers.get_list("cache-control") == ["no-store"]


async def test_hashed_asset_is_immutable(c: httpx.AsyncClient) -> None:
    response = await c.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == spa.IMMUTABLE
    _spa_headers(response)
    head = await c.head("/assets/index-abc123.js")
    assert head.status_code == 200


async def test_missing_asset_is_404_not_html(c: httpx.AsyncClient) -> None:
    for path in ("/assets/gone-1.js", "/assets/sem-extensao", "/logo.png"):
        response = await c.get(path)
        assert response.status_code == 404, path
        assert INDEX not in response.text
        _spa_headers(response)


async def test_api_is_untouched(c: httpx.AsyncClient) -> None:
    for path in (PREFIX + "/does-not-exist", "/api", "/api/", "/api/v2/anything"):
        response = await c.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Not Found"}
        assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert INDEX not in response.text
    login = await c.get(PREFIX + "/auth/login")
    assert login.status_code == 303
    assert login.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert (await c.get(PREFIX + "/session")).status_code == 401
    assert (await c.post("/tarefas")).status_code in (404, 405)


async def test_openapi_does_not_list_the_catch_all(c: httpx.AsyncClient) -> None:
    paths = (await c.get(PREFIX + "/openapi.json")).json()["paths"]
    assert all(p.startswith(PREFIX) for p in paths)


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/..%5csecret.txt",
        "/.env",
        "/assets/%00.js",
    ],
)
async def test_path_traversal_is_refused(c: httpx.AsyncClient, path: str) -> None:
    response = await c.get(path)
    assert SECRET not in response.text
    assert response.status_code in (400, 404) or response.text == INDEX


async def test_resolver_refuses_escape_directly(bundle: Path) -> None:
    web = spa.WebBundle(bundle)
    for relative in ("../secret.txt", "assets/../../secret.txt", "..\\secret.txt", ".env", "a\x00b"):
        assert web._resolve(relative) is None, relative
    assert web.respond("/../secret.txt").status_code == 404
    link = bundle / "assets" / "link.js"
    try:
        link.symlink_to(bundle.parent / "secret.txt")
    except OSError:
        return  # symlinks not permitted on this host
    assert web._resolve("assets/link.js") is None


async def test_without_bundle_non_api_is_404_and_api_headers_unchanged(tmp_path: Path) -> None:
    async for client in _client(None):
        response = await client.get("/")
        assert response.status_code == 404
        assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


async def test_missing_bundle_in_production_logs_and_serves_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="maezo.portal.web")
    assert spa.load_bundle(tmp_path / "absent", production=True) is None
    assert any("portal_web_bundle_missing" in r.getMessage() for r in caplog.records)
    (tmp_path / "empty").mkdir()
    assert spa.load_bundle(tmp_path / "empty", production=True) is None
    assert spa.load_bundle(None, production=False) is None
