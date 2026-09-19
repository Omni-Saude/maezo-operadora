"""Separate PHI document application: never registered or mounted by the General factory.

The General portal answers `GET /documents/{ref}/content` with a 307 to this
same-origin path (`portal/api/documents.py`), never with bytes: the redirect route
proves the authority and hands back the SAME opaque reference, and the bytes only ever
leave the platform here, where the current authority and the custody state are
re-checked against the published heads before a single byte is decrypted. No query
string, no signed URL, no bearer grant, no filename — an upload carries none, so none
is invented (a name would be metadata PHI this plane has no contract to hold).

Key/database residency, roles/TLS and retention are independently qualified deployment
inputs; this factory opens no engine, database or network connection itself.
"""

import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response

from maezo.gateway.documents.authority import PublishedDocumentAuthority
from maezo.gateway.documents.postgres import PostgresProtectedDocumentProvider
from maezo.gateway.documents.service import DocumentAccess, DocumentGrant
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.portal.api.app import PrivacyBoundary
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.intakes import ERRORS, ProductRoute, intake_error, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import IdentityStore
from maezo.portal.contracts.models import HumanPrincipal

PhiDocumentServiceFactory = Callable[[HumanSessionResolver], "PhiDocumentService"]
phi_document_router = APIRouter(route_class=ProductRoute)
# The storage route carries the document bytes, not JSON, so it deliberately does not
# use `ProductRoute`; it enforces the same session/CSRF/origin discipline itself.
phi_document_write_router = APIRouter()

_CONTENT_TYPE = "application/octet-stream"


class PhiDocumentService:
    """Session boundary + custody re-check for the one route that serves document bytes."""

    def __init__(
        self,
        resolver: HumanSessionResolver,
        authority: PublishedDocumentAuthority,
        store: PostgresProtectedDocumentProvider,
    ) -> None:
        if (
            type(authority) is not PublishedDocumentAuthority
            or type(store) is not PostgresProtectedDocumentProvider
            or authority.tenant != store.scope.tenant
            or resolver.settings.tenant != authority.tenant
        ):
            # A resolver of another tenant wired to this plane is a composition fault,
            # not a weaker permission.
            raise IntakeError()
        self.resolver, self.authority, self.store = resolver, authority, store

    async def _grant(self, principal: HumanPrincipal, document_ref: str) -> DocumentGrant:
        return await self.authority.authorize(
            DocumentAccess(
                principal=principal,
                operation="download",
                resource_kind="document",
                resource_ref=document_ref,
            )
        )

    async def store_content(
        self,
        secret_value: str,
        *,
        csrf: str,
        origin: str,
        upload_ref: str,
        raw: bytes,
    ) -> None:
        """The creator's own bytes for a pending upload; a mutation, so CSRF/origin bind."""
        session = await self.resolver.resolve(secret_value)
        if (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
            or session.principal.tenant != self.authority.tenant
        ):
            raise IntakeError("authentication_unavailable")
        ceiling = session_ceiling(session)
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("authentication_unavailable")
        await self.store.store(upload_ref, session.principal, raw)
        current = await self.resolver.resolve(secret_value)
        if current.principal != session.principal or datetime.now(UTC) >= min(
            ceiling, session_ceiling(current)
        ):
            raise IntakeError("operation_forbidden")

    async def read_content[T](
        self, secret_value: str, *, document_ref: str, freeze: Callable[[bytes], T]
    ) -> T:
        session = await self.resolver.resolve(secret_value)
        if session.principal.tenant != self.authority.tenant:
            raise IntakeError("operation_forbidden")
        before = await self._grant(session.principal, document_ref)
        if before.access.resource_ref != document_ref:
            raise IntakeError("operation_forbidden")
        raw = await self.store.open(document_ref)
        # The bytes are re-authorized against the same published heads AFTER the read:
        # a revocation or a re-screening that happened during the decrypt refuses here.
        current = await self.resolver.resolve(secret_value)
        if current.principal != session.principal:
            raise IntakeError("operation_forbidden")
        after = await self._grant(current.principal, document_ref)
        if after.access != before.access or after.authority_digest != before.authority_digest:
            raise IntakeError("operation_forbidden")
        ceiling = min(session_ceiling(session), session_ceiling(current), after.valid_until)
        if datetime.now(UTC) >= ceiling:
            raise IntakeError("operation_forbidden")
        return freeze(raw)


def _service(request: Request) -> PhiDocumentService:
    factory: PhiDocumentServiceFactory | None = request.app.state.phi_document_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    value = factory(resolver)
    if not isinstance(value, PhiDocumentService) or value.resolver is not resolver:
        raise IntakeError()
    return value


@phi_document_router.get(
    "/api/v1/phi/documents/{document_ref}/content",
    responses=ERRORS,
)
async def read_content(request: Request, document_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    try:
        return await _service(request).read_content(
            secret(request),
            document_ref=reference(document_ref),
            freeze=lambda raw: Response(content=raw, media_type=_CONTENT_TYPE),
        )
    except AuthUnavailableError:
        return intake_error("operation_forbidden")


@phi_document_write_router.put(
    "/api/v1/phi/documents/{upload_ref}/content",
    status_code=204,
    responses=ERRORS,
)
async def store_content(request: Request, upload_ref: str) -> Response:
    """The one PHI write: the creator's own bytes, before completion, never re-written.

    Deliberately a plain route — `ProductRoute` speaks JSON, and this body is the
    document itself. The same session/CSRF/origin checks are enforced here explicitly,
    the body is size-bounded, and nothing about the document is echoed back.
    """
    if request.scope.get("query_string", b""):
        return intake_error("invalid_request")
    headers = request.headers
    if (
        headers.getlist("content-type") != ["application/octet-stream"]
        or len(headers.getlist("origin")) != 1
        or len(headers.getlist("x-csrf-token")) != 1
    ):
        return intake_error("authentication_unavailable")
    raw = await request.body()
    try:
        await _service(request).store_content(
            secret(request),
            csrf=headers["x-csrf-token"],
            origin=headers["origin"],
            upload_ref=reference(upload_ref),
            raw=raw,
        )
    except AuthUnavailableError:
        return intake_error("operation_forbidden")
    except IntakeError as error:
        return intake_error(error.code)
    return Response(status_code=204)


def create_phi_document_app(
    settings: PortalSettings,
    *,
    identity_store: IdentityStore,
    document_service_factory: PhiDocumentServiceFactory,
    read_only: bool = False,
) -> FastAPI:
    """Explicit PHI composition; no key, authority, store or General-zone default.

    Deployment must route these same-origin paths to PHI and separately qualify
    TLS/roles/keys/zone/auth providers. `read_only` is the deployment saying this
    process never accepts document bytes; the storage route is then not mounted at all,
    rather than mounted and refused.
    """
    resolver = HumanSessionResolver(settings, identity_store)
    app = FastAPI(
        title="Portal Maezo PHI documents",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/phi/openapi.json",
        redirect_slashes=False,
    )
    app.state.human_session_resolver = resolver
    app.state.phi_document_service_factory = document_service_factory
    app.include_router(phi_document_router)
    if not read_only:
        app.include_router(phi_document_write_router)

    @app.middleware("http")
    async def deployment_host(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            len(request.headers.getlist("host")) != 1
            or request.headers["host"] != urlsplit(settings.public_origin).netloc
        ):
            return intake_error("invalid_request")
        return await call_next(request)

    app.add_middleware(PrivacyBoundary)
    return app
