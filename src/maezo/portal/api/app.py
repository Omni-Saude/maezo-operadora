"""Minimal human BFF bootstrap. No task mutations until the enforcing human gateway exists."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from maezo.gateway.portal_identity import build_human_identity_adapters
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import SessionDTO
from maezo.portal.api.session import HumanSessionResolver, HumanSessionService
from maezo.portal.api.store import IdentityStore
from maezo.portal.api.tasks import ReadServiceFactory, is_task_read, read_error, task_router

_SESSION = "__Host-maezo-session"
_BROWSER = "__Host-maezo-login"
_PREFIX = "/api/v1/portal"
_LOG = logging.getLogger("maezo.portal.security")
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def _refused(status: int = 401) -> JSONResponse:
    return JSONResponse({"erro": "Não foi possível validar a sessão."}, status_code=status)


class PrivacyBoundary:
    """Remove query from the server-owned scope before access logging; isolate raw query.

    Entrypoint also disables access logs. Proxies/load balancers must independently omit queries,
    cookies and authorization headers. No exception object or request is emitted to telemetry.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        private_scope = dict(scope)
        scope["query_string"] = b""
        started = False

        async def safe_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = list(message["headers"]) + [
                    (k.lower().encode(), v.encode()) for k, v in _HEADERS.items()
                ]
            await send(message)

        try:
            await self.app(private_scope, receive, safe_send)
        except Exception:
            _LOG.warning("portal_dependency_failure")
            if not started:
                await _refused(503)(scope, receive, safe_send)


def _query(request: Request, allowed: set[str]) -> dict[str, str]:
    raw = request.scope.get("query_string", b"")
    if len(raw) > 4096:
        raise AuthenticationError()
    try:
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
            errors="strict",
        )
    except (ValueError, UnicodeError):
        raise AuthenticationError() from None
    if len({k for k, _ in pairs}) != len(pairs) or any(k not in allowed for k, _ in pairs):
        raise AuthenticationError()
    return dict(pairs)


def _cookie(request: Request, name: str) -> str:
    # Reject ambiguity rather than trusting a parser's first/last cookie selection.
    values = [
        part.strip().split("=", 1)[1]
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
        if part.strip().startswith(name + "=")
    ]
    if len(values) != 1:
        raise AuthenticationError()
    return values[0]


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/")


def _delete_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, secure=True, httponly=True, samesite="lax", path="/")


def create_app(
    settings: PortalSettings | None = None,
    *,
    store: IdentityStore | None = None,
    oidc_client: httpx.AsyncClient | None = None,
    task_read_service_factory: ReadServiceFactory | None = None,
) -> FastAPI:
    """Production factory has no in-memory fallback and no default or agent credentials."""
    try:
        config = settings or PortalSettings()  # type: ignore[call-arg]
    except ValueError:
        raise ValueError("Configuração do portal indisponível.") from None
    adapters = build_human_identity_adapters(config, store=store, oidc_client=oidc_client)
    store = adapters.store
    resolver = HumanSessionResolver(config, store)
    service = HumanSessionService(resolver, adapters.authenticator)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            try:
                await store.purge_expired(datetime.now(UTC))
            except Exception:
                raise RuntimeError("Persistência de identidade indisponível.") from None
            yield
        finally:
            try:
                await service.authenticator.close()
                await store.close()
            except Exception:
                raise RuntimeError("Encerramento da identidade indisponível.") from None

    app = FastAPI(
        title="Portal Maezo",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{_PREFIX}/openapi.json",
        lifespan=lifespan,
        # TLS offload leaves an HTTP scope: slash redirects would forward code/state
        # to plaintext. Reject noncanonical paths; never infer authority from proxy headers.
        redirect_slashes=False,
    )
    app.state.human_session_resolver = resolver
    app.state.task_read_service_factory = task_read_service_factory
    app.include_router(task_router)

    @app.middleware("http")
    async def deployment_host(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            len(request.headers.getlist("host")) != 1
            or request.headers["host"] != urlsplit(config.public_origin).netloc
        ):
            return read_error("invalid_request") if is_task_read(request) else _refused(400)
        return await call_next(request)

    @app.exception_handler(AuthenticationError)
    async def auth_refused(request: Request, exc: AuthenticationError) -> Response:
        return _refused()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        return read_error("invalid_request") if is_task_read(request) else _refused(400)

    @app.get(f"{_PREFIX}/auth/login")
    async def login(request: Request) -> Response:
        query = _query(request, {"return_to"})
        url, browser = await service.start(query.get("return_to", "/"))
        response = RedirectResponse(url, status_code=303)
        _set_cookie(response, _BROWSER, browser, config.transaction_seconds)
        return response

    @app.get(f"{_PREFIX}/auth/callback")
    async def callback(request: Request) -> Response:
        query = _query(request, {"state", "code"})
        old_secret = _cookie(request, _SESSION) if _SESSION in request.cookies else None
        secret, path = await service.finish(
            query.get("state", ""), query.get("code", ""), _cookie(request, _BROWSER), old_secret
        )
        response = RedirectResponse(path, status_code=303)
        _delete_cookie(response, _BROWSER)
        _set_cookie(response, _SESSION, secret, config.session_seconds)
        return response

    @app.get(f"{_PREFIX}/session", response_model=SessionDTO)
    async def session(request: Request) -> SessionDTO:
        _query(request, set())
        return (await resolver.resolve(_cookie(request, _SESSION))).projection()

    @app.post(f"{_PREFIX}/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        _query(request, set())
        if len(request.headers.getlist("origin")) != 1 or len(request.headers.getlist("x-csrf-token")) != 1:
            raise AuthenticationError()
        await service.logout(
            _cookie(request, _SESSION), request.headers["x-csrf-token"], request.headers["origin"]
        )
        response = Response(status_code=204)
        _delete_cookie(response, _SESSION)
        _delete_cookie(response, _BROWSER)
        return response

    app.add_middleware(PrivacyBoundary)
    return app
