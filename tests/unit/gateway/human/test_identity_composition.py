"""Offline construction tests: actual production factory, no DB/network effects."""

import ssl

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.unit.portal.test_human_session import config

from maezo.gateway import portal_identity as composition
from maezo.gateway.oidc import AuthenticationError, CognitoAuthenticator
from maezo.portal.api.app import create_app
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.store import LocalTestIdentityStore


@pytest.mark.asyncio
async def test_actual_production_factory_owns_safe_adapters_and_disposal(monkeypatch):
    settings = config(mode="production", database_url="postgresql+asyncpg://portal_identity@db.test/portal")
    engines, clients, purges, closes = [], [], [], []
    engine_factory = composition.create_async_engine
    client_factory = httpx.AsyncClient

    def database(url, **kwargs):
        assert url.drivername == "postgresql+asyncpg" and url.host == "db.test"
        assert kwargs["hide_parameters"] is True and kwargs["echo"] is False
        context = kwargs["connect_args"]["ssl"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        result = engine_factory(url, **kwargs)
        engines.append(result)
        return result

    def oidc(**kwargs):
        assert kwargs == dict(verify=True, trust_env=False, follow_redirects=False, timeout=10.0)
        result = client_factory(**kwargs)
        clients.append(result)
        return result

    async def purge(self, now):
        purges.append(self.tenant)

    original_close = PostgresIdentityStore.close

    async def close(self):
        closes.append(self.tenant)
        await original_close(self)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setattr(composition, "create_async_engine", database)
    monkeypatch.setattr(httpx, "AsyncClient", oidc)
    monkeypatch.setattr(PostgresIdentityStore, "purge_expired", purge)
    monkeypatch.setattr(PostgresIdentityStore, "close", close)
    app = create_app(settings)
    assert len(engines) == len(clients) == 1
    assert isinstance(engines[0], AsyncEngine)
    assert isinstance(app.state.human_session_resolver.store, PostgresIdentityStore)
    assert not clients[0].is_closed
    async with app.router.lifespan_context(app):
        assert purges == [settings.tenant]
    assert closes == [settings.tenant] and clients[0].is_closed


@pytest.mark.parametrize("override", ["store", "oidc_client"])
def test_gateway_factory_itself_refuses_production_injection_before_construction(monkeypatch, override):
    settings = config(mode="production", database_url="postgresql+asyncpg://portal_identity@db.test/portal")
    effects = []
    monkeypatch.setattr(composition, "create_async_engine", lambda *a, **kw: effects.append("db"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: effects.append("http"))
    with pytest.raises(ValueError, match="production dependency overrides prohibited"):
        composition.build_human_identity_adapters(settings, **{override: object()})
    assert effects == []


@pytest.mark.parametrize(
    "dsn", ["sqlite+aiosqlite:///portal", "postgresql://db.test/portal", "postgresql+asyncpg:///portal"]
)
def test_database_driver_or_missing_host_refuses_before_effect(monkeypatch, dsn):
    effects = []
    monkeypatch.setattr(composition, "create_async_engine", lambda *a, **kw: effects.append("db"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: effects.append("http"))
    with pytest.raises(ValueError, match="dedicated PostgreSQL"):
        composition.build_human_identity_adapters(config(mode="production", database_url=dsn))
    assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,target,data",
    [
        ("POST", "jwks", {}),
        ("GET", "token", None),
        ("GET", "jwks", {}),
        ("POST", "https://foreign.example/oauth2/token", {}),
        ("POST", "https://engine.example/engine-rest/message", {}),
    ],
)
async def test_oidc_operation_endpoint_pair_refuses_before_http(method, target, data):
    settings = config()
    urls = {"jwks": settings.jwks_url, "token": settings.cognito_origin + "/oauth2/token"}
    effects = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: effects.append(r))) as client:
        auth = CognitoAuthenticator(settings, client)
        with pytest.raises(AuthenticationError):
            await auth._json(method, urls.get(target, target), data)
    assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not-json", b" " * 65537])
async def test_oidc_bounded_json_refuses_without_exposing_response(body):
    settings = config()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(AuthenticationError) as error:
            await CognitoAuthenticator(settings, client)._json("GET", settings.jwks_url)
    assert str(error.value) == ""


@pytest.mark.asyncio
async def test_gateway_local_override_keeps_same_store_and_identity_adapter():
    settings = config()
    store = LocalTestIdentityStore(settings.tenant)
    async with httpx.AsyncClient() as client:
        adapters = composition.build_human_identity_adapters(settings, store=store, oidc_client=client)
        assert adapters.store is store
        assert isinstance(adapters.authenticator, CognitoAuthenticator)
        assert adapters.authenticator._client is client
