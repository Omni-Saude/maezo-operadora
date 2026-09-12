"""Separate PHI application: never registered/mounted by the General portal factory."""

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response

from maezo.gateway.communications.content import PhiCommunicationService
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.app import PrivacyBoundary
from maezo.portal.api.communications import communication_error
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.intakes import ERRORS, ProductRoute, intake_error, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import IdentityStore
from maezo.portal.contracts.communication_content import (
    CommunicationContent,
    CommunicationContentReceipt,
    CommunicationContentSubmission,
)

PhiCommunicationServiceFactory = Callable[[HumanSessionResolver], PhiCommunicationService]
phi_communication_router = APIRouter(route_class=ProductRoute)
phi_communication_read_router = APIRouter(route_class=ProductRoute)


def _service(request: Request) -> PhiCommunicationService:
    factory: PhiCommunicationServiceFactory = request.app.state.phi_communication_service_factory
    resolver = request.app.state.human_session_resolver
    value = factory(resolver)
    if not isinstance(value, PhiCommunicationService) or value.resolver is not resolver:
        raise IntakeError()
    return value


@phi_communication_router.post(
    "/api/v1/phi/cases/{case_ref}/communication-content",
    response_model=CommunicationContentReceipt,
    responses=ERRORS,
)
async def preserve_content(request: Request, case_ref: str, body: CommunicationContentSubmission) -> Response:
    try:
        return await _service(request).preserve(
            secret(request),
            csrf=request.headers["x-csrf-token"],
            origin=request.headers["origin"],
            case_ref=reference(case_ref),
            body=body,
            freeze=_render,
        )
    except ExternalCaseError as error:
        return communication_error(error)


@phi_communication_read_router.get(
    "/api/v1/phi/cases/{case_ref}/communications/{communication_ref}/content",
    response_model=CommunicationContent,
    responses=ERRORS,
)
async def read_content(request: Request, case_ref: str, communication_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    try:
        return await _service(request).read_content(
            secret(request),
            case_ref=reference(case_ref),
            communication_ref=reference(communication_ref),
            freeze=_render,
        )
    except ExternalCaseError as error:
        return communication_error(error)


def create_phi_communication_app(
    settings: PortalSettings,
    *,
    identity_store: IdentityStore,
    content_service_factory: PhiCommunicationServiceFactory,
    read_only: bool = False,
) -> FastAPI:
    """Explicit PHI composition; no key, authority, store or General-zone default.

    Deployment must route these same-origin paths to PHI and separately qualify
    TLS/roles/keys/zone/auth providers. Resource ownership/cleanup belongs to the
    composition root; this factory opens no engine, database or network connection.
    """
    resolver = HumanSessionResolver(settings, identity_store)
    app = FastAPI(
        title="Portal Maezo PHI communications",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/phi/openapi.json",
        redirect_slashes=False,
    )
    app.state.human_session_resolver = resolver
    app.state.phi_communication_service_factory = content_service_factory
    app.include_router(phi_communication_read_router)
    if not read_only:
        app.include_router(phi_communication_router)

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


def _render(raw: bytes) -> Response:
    return Response(content=raw, media_type="application/json")
