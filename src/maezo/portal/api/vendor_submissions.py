"""Vendor channel submission boundary (OP16); a receipt is a stage, never a verdict.

The route is gated TWICE, both server-side: the deployment must project the `vendor` capability
(config; default deployments project none and the route answers `operation_forbidden` — the
capability string itself grants nothing) and the resolved session must carry a published vendor
membership whose single binding names the commanded channel (`AUTHORITY_UNPROVEN` otherwise).
The formalization answer is NOT a field on this boundary: it lives in native case objects
produced by existing authorities — this module never imports the communications plane (PW1-C).
"""

import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.gateway.vendor_submissions import (
    SubmissionAbsentError,
    VendorSubmissionError,
    VendorSubmissionMachine,
)
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.intakes import PREFIX, ProductRoute, reference, secret
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.vendor_submissions import (
    ChannelSubmissionCommand,
    ChannelSubmissionReceipt,
    ChannelSubmissionStatus,
    PortalVendorSubmissionError,
)

VendorSubmissionServiceFactory = Callable[[HumanSessionResolver], "VendorSubmissionService"]
vendor_router = APIRouter(route_class=ProductRoute)

#: Transport codes follow the intake lane (`PortalIntakeError`); the SCREAMING codes are the
#: registry OP16 refusals, quoted exactly as the registry spells them.
STATUS: dict[str, int] = {
    "invalid_request": 400,
    "PHI_IN_COMMERCIAL_INPUT": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "AUTHORITY_UNPROVEN": 403,
    "CHANNEL_STATUS_INELIGIBLE": 403,
    "resource_unavailable": 404,
    "CONTRACT_MISMATCH": 409,
    "STALE_REVISION": 409,
    "SOURCE_UNAVAILABLE": 503,
    "AUDIT_UNAVAILABLE": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalVendorSubmissionError} for status in STATUS.values()
}


def submission_error(code: str) -> JSONResponse:
    return JSONResponse({"code": code}, status_code=STATUS.get(code, 503))


class VendorSubmissionService:
    """Session resolution + the one capability gate + the OP16 machine; no authority here."""

    def __init__(self, resolver: HumanSessionResolver, machine: VendorSubmissionMachine) -> None:
        self.resolver, self.machine = resolver, machine

    def _vendor_enabled(self) -> bool:
        return "vendor" in self.resolver.settings.capabilities.split(",")

    async def _authorize(
        self, session_secret: str, *, csrf: str | None = None, origin: str | None = None
    ) -> ResolvedHumanSession:
        session = await self.resolver.resolve(session_secret)
        if datetime.now(UTC) >= session_ceiling(session):
            raise AuthenticationError()
        if not self._vendor_enabled():
            # The area is not enabled in THIS deployment; the session proves nothing about it.
            raise IntakeError("operation_forbidden")
        if (csrf is not None or origin is not None) and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise IntakeError("authentication_unavailable")
        current = await self.resolver.resolve(session_secret)
        if current.principal != session.principal or datetime.now(UTC) >= session_ceiling(current):
            raise AuthenticationError()
        return current

    async def submit(
        self, session_secret: str, *, csrf: str | None, origin: str | None, command: ChannelSubmissionCommand
    ) -> ChannelSubmissionReceipt:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        return await self.machine.submit(session.principal.tenant, session.membership, command)

    async def read(self, session_secret: str, submission_ref: str) -> ChannelSubmissionStatus:
        session = await self._authorize(session_secret)
        return await self.machine.read(
            session.principal.tenant, session.membership, reference(submission_ref)
        )


def _service(request: Request) -> VendorSubmissionService:
    factory: VendorSubmissionServiceFactory | None = request.app.state.vendor_submission_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, VendorSubmissionService) or service.resolver is not resolver:
        raise IntakeError()
    return service


async def _execute(
    request: Request, call: Callable[[VendorSubmissionService, str], Awaitable[Any]]
) -> Response:
    session_secret = secret(request)
    try:
        result = await call(_service(request), session_secret)
    except VendorSubmissionError as exc:
        return submission_error(exc.reason.value)
    except SubmissionAbsentError:
        # An absent ref and a ref owned by another channel are indistinguishable on the wire.
        return submission_error("resource_unavailable")
    return Response(
        content=result.model_dump_json(),
        media_type="application/json",
        status_code=202 if request.method == "POST" else 200,
    )


@vendor_router.post(
    PREFIX + "/vendor/submissions",
    response_model=ChannelSubmissionReceipt,
    status_code=202,
    responses=ERRORS,
)
async def submit(request: Request, body: ChannelSubmissionCommand) -> Response:
    return await _execute(
        request,
        lambda service, session_secret: service.submit(
            session_secret,
            csrf=request.headers.get("x-csrf-token"),
            origin=request.headers.get("origin"),
            command=body,
        ),
    )


@vendor_router.get(
    PREFIX + "/vendor/submissions/{submission_ref}",
    response_model=ChannelSubmissionStatus,
    responses=ERRORS,
)
async def read(request: Request, submission_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    return await _execute(
        request, lambda service, session_secret: service.read(session_secret, submission_ref)
    )
