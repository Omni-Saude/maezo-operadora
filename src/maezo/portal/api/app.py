"""Human BFF bootstrap with separately composed enforcing read and decision gateways."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from maezo.gateway.human.assignment_composition import AssignmentRuntime
from maezo.gateway.human.engine_reads import EngineReadComposition
from maezo.gateway.portal_identity import build_human_identity_adapters
from maezo.gateway.staff_cases.composition import StaffCaseRuntime
from maezo.portal.api import spa
from maezo.portal.api.auth import AuthenticationError, HumanAuthenticator
from maezo.portal.api.cases import CaseServiceFactory, StaffCaseServiceFactory, case_router
from maezo.portal.api.communications import (
    CommunicationServiceFactory,
    communication_router,
    is_communication_request,
)
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.decisions import (
    DecisionServiceFactory,
    decision_error,
    decision_router,
    is_decision_request,
)
from maezo.portal.api.documents import DocumentServiceFactory, document_router
from maezo.portal.api.intake_recovery import (
    IntakeRecoveryFactory,
    intake_recovery_router,
    is_intake_recovery_request,
)
from maezo.portal.api.intakes import IntakeServiceFactory, intake_error, intake_router, is_product_request
from maezo.portal.api.records import LogoutDTO, SessionDTO
from maezo.portal.api.session import HumanSessionResolver, HumanSessionService
from maezo.portal.api.store import IdentityStore
from maezo.portal.api.tasks import (
    CompletionPolicy,
    ReadServiceFactory,
    completion_error,
    completion_router,
    is_task_completion,
    is_task_read,
    read_error,
    task_router,
)

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

    def __init__(self, app: ASGIApp, *, web_bundle: bool = False) -> None:
        self.app = app
        # Non-API paths answered by `spa.WebBundle` carry their own CSP/cache headers; the API's
        # `default-src 'none'` would forbid the bundle's own scripts if appended on top.
        self.web_bundle = web_bundle

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        private_scope = dict(scope)
        scope["query_string"] = b""
        started = False
        own_headers = self.web_bundle and not spa.is_api_path(scope.get("path", ""))

        async def safe_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if own_headers:
                    await send(message)
                    return
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


#: As UNICAS rotas que a pagina de demonstracao do canal de teste chama de outra origem
#: (`platform/testchannel/paginas/escalonamento.html`): ler a sessao, listar a fila, ler uma
#: tarefa e concluir. O login nao entra — ele e' um redirect que o navegador segue numa aba, e
#: redirect nao e' requisicao cross-origin com credencial.
CROSS_ORIGIN_PATHS: Final[tuple[str, ...]] = (
    "/api/v1/portal/session",
    "/api/v1/portal/tasks",
)


class PathScopedCORS:
    """`CORSMiddleware` aplicado SO' num prefixo de caminho.

    DL-0049 review (rodaquino, 21/09/2026), P2: o middleware global dava a origem do canal
    acesso CREDENCIADO a todo o BFF — inclusive rotas que nao verificam `Origin` por conta
    propria, porque nunca precisaram (sao same-origin por desenho). O canal precisa de quatro
    caminhos; conceder o resto era alcance que ninguem pediu.

    Por que envolver em vez de reimplementar: preflight, `Vary`, credencial e a lista exata de
    metodos/cabecalhos sao detalhes que o Starlette ja acerta. Aqui so' se decide QUANDO ele
    entra. Fora do prefixo a requisicao segue para o app sem passar por ele, entao nenhuma
    resposta de outra rota pode ganhar `Access-Control-Allow-*` — nem no caminho felizmente
    nem num erro.
    """

    def __init__(self, app: ASGIApp, /, **options: Any) -> None:
        # `paths` vem por `**options` e nao como keyword-only proprio: o protocolo de middleware
        # do Starlette (`_MiddlewareFactory`) exige exatamente `(app, **kwargs)`, e um parametro
        # nomeado antes do `**` quebra a compatibilidade estrutural (medido com mypy).
        self._app = app
        self._paths: tuple[str, ...] = options.pop("paths")
        self._cors = CORSMiddleware(app, **options)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        caminho = scope.get("path", "")
        if any(caminho == p or caminho.startswith(p + "/") for p in self._paths):
            await self._cors(scope, receive, send)
            return
        await self._app(scope, receive, send)


def create_app(
    settings: PortalSettings | None = None,
    *,
    store: IdentityStore | None = None,
    oidc_client: httpx.AsyncClient | None = None,
    authenticator: HumanAuthenticator | None = None,
    task_read_service_factory: ReadServiceFactory | None = None,
    engine_read_composition: EngineReadComposition | None = None,
    decision_service_factory: DecisionServiceFactory | None = None,
    assignment_runtime: AssignmentRuntime | None = None,
    case_service_factory: CaseServiceFactory | None = None,
    staff_case_service_factory: StaffCaseServiceFactory | None = None,
    staff_case_runtime: StaffCaseRuntime | None = None,
    intake_service_factory: IntakeServiceFactory | None = None,
    intake_recovery_factory: IntakeRecoveryFactory | None = None,
    document_service_factory: DocumentServiceFactory | None = None,
    communication_service_factory: CommunicationServiceFactory | None = None,
    web_root: Path | None = None,
) -> FastAPI:
    """Production factory has no in-memory fallback and no default or agent credentials."""
    try:
        config = settings or PortalSettings()  # type: ignore[call-arg]
    except ValueError:
        raise ValueError("Configuração do portal indisponível.") from None
    if engine_read_composition is not None:
        if task_read_service_factory is not None:
            raise ValueError("Composição de leitura ambígua.")
        task_read_service_factory = engine_read_composition.build
    if assignment_runtime is not None:
        if decision_service_factory is not None or assignment_runtime.scope.tenant != config.tenant:
            raise ValueError("Composição de atribuição ambígua.")
        decision_service_factory = assignment_runtime.build
    if staff_case_runtime is not None:
        if (
            staff_case_service_factory is not None
            or staff_case_runtime.sessions.tenant != config.tenant
            or staff_case_runtime.sessions.issuer != config.issuer
        ):
            raise ValueError("Composição de casos de colaboradores ambígua.")
        staff_case_service_factory = staff_case_runtime.service
    adapters = build_human_identity_adapters(
        config, store=store, oidc_client=oidc_client, authenticator=authenticator
    )
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
            if assignment_runtime is not None:
                await assignment_runtime.start()
            yield
        finally:
            try:
                if assignment_runtime is not None:
                    await assignment_runtime.close()
            finally:
                try:
                    if engine_read_composition is not None:
                        await engine_read_composition.close()
                finally:
                    try:
                        if staff_case_runtime is not None:
                            await staff_case_runtime.close()
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
    # INTERIM (DL-0049). Both halves ship off: `direct_completion` is false unless the
    # deployment sets it, and `cors_origins` is empty, so the only caller the route would ever
    # accept is the portal's own origin. `scripts/ci/check_portal_direct_completion.py` is what
    # keeps either of them from being turned on outside `dev-sa-east-1`.
    cross_origins = config.allowed_cross_origins()
    app.state.completion_policy = CompletionPolicy(
        enabled=config.direct_completion,
        origins=frozenset((config.public_origin, *cross_origins)),
    )
    app.include_router(completion_router)
    app.state.decision_service_factory = decision_service_factory
    app.include_router(decision_router)
    app.state.case_service_factory = case_service_factory
    app.state.staff_case_service_factory = staff_case_service_factory
    app.state.intake_service_factory = intake_service_factory
    app.state.intake_recovery_factory = intake_recovery_factory
    app.state.document_service_factory = document_service_factory
    app.include_router(case_router)
    app.include_router(intake_router)
    app.include_router(intake_recovery_router)
    app.include_router(document_router)
    app.state.communication_service_factory = communication_service_factory
    app.include_router(communication_router)

    # Added BEFORE `deployment_host` on purpose. `add_middleware` inserts at position 0, so
    # the LAST call is the outermost: registering CORS first leaves it INSIDE the host check,
    # and a preflight for a foreign Host is refused by that check instead of being answered.
    # Nothing is installed when the allowlist is empty, which is the shipped state — there is
    # no code path in a default deployment that can emit an `Access-Control-Allow-*` header.
    if cross_origins:
        app.add_middleware(
            PathScopedCORS,
            paths=CROSS_ORIGIN_PATHS,
            allow_origins=list(cross_origins),  # exact strings; never a regex or an echo
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["content-type", "x-csrf-token"],
            max_age=600,
        )

    @app.middleware("http")
    async def deployment_host(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            len(request.headers.getlist("host")) != 1
            or request.headers["host"] != urlsplit(config.public_origin).netloc
        ):
            if (
                is_product_request(request)
                or is_communication_request(request)
                or is_intake_recovery_request(request)
            ):
                return intake_error("invalid_request")
            if is_decision_request(request):
                return decision_error("invalid_request")
            # Completion first: its path lives under the read prefix, so `is_task_read` also
            # matches it and would answer with the read schema.
            if is_task_completion(request):
                return completion_error("invalid_request")
            return read_error("invalid_request") if is_task_read(request) else _refused(400)
        return await call_next(request)

    @app.exception_handler(AuthenticationError)
    async def auth_refused(request: Request, exc: AuthenticationError) -> Response:
        return _refused()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        if (
            is_product_request(request)
            or is_communication_request(request)
            or is_intake_recovery_request(request)
        ):
            return intake_error("invalid_request")
        if is_decision_request(request):
            return decision_error("invalid_decision")
        # 422 with this route's own schema: a body whose `resultado` is absent or outside the
        # BPMN's three values is a rejected completion, not a malformed read.
        if is_task_completion(request):
            return completion_error("invalid_completion")
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
        capabilities = cast(
            'tuple[Literal["identity", "staff_cases", "human"], ...]', tuple(config.capabilities.split(","))
        )
        return (await resolver.resolve(_cookie(request, _SESSION))).projection(capabilities)

    # Config-only, never derived from the request: ends the Cognito Hosted UI session too.
    idp_logout_url = f"{config.cognito_origin}/logout?" + urlencode(
        {"client_id": config.client_id, "logout_uri": f"{config.public_origin}/"}
    )

    @app.post(f"{_PREFIX}/auth/logout", response_model=LogoutDTO)
    async def logout(request: Request) -> Response:
        _query(request, set())
        if len(request.headers.getlist("origin")) != 1 or len(request.headers.getlist("x-csrf-token")) != 1:
            raise AuthenticationError()
        await service.logout(
            _cookie(request, _SESSION), request.headers["x-csrf-token"], request.headers["origin"]
        )
        response = JSONResponse(LogoutDTO(idp_logout_url=idp_logout_url).model_dump(mode="json"))
        _delete_cookie(response, _SESSION)
        _delete_cookie(response, _BROWSER)
        return response

    # Registered LAST so every API route above wins; the catch-all refuses `/api/*` itself.
    bundle = spa.load_bundle(web_root, production=config.mode == "production")
    if bundle is not None:
        spa.install(app, bundle)
    app.add_middleware(PrivacyBoundary, web_bundle=bundle is not None)
    return app
