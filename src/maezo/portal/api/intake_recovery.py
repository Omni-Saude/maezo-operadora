"""Exact pointer-only GET recovery routes; ROOT composes the factory/router."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.routing import Match

from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.recovery import IntakeRecoveryService
from maezo.portal.api.intakes import ERRORS, PREFIX, ProductRoute, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.intake_recovery import IntakeCommandObservation, IntakeRecoveryPage

IntakeRecoveryFactory = Callable[[HumanSessionResolver], IntakeRecoveryService]
_PRIVATE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie",
    "Referrer-Policy": "no-referrer",
}


class RecoveryRoute(ProductRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            result = await original(request)
            result.headers.update(_PRIVATE)
            return result

        return checked


intake_recovery_router = APIRouter(route_class=RecoveryRoute)


def service(request: Request) -> IntakeRecoveryService:
    factory: IntakeRecoveryFactory | None = getattr(request.app.state, "intake_recovery_factory", None)
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    result = factory(resolver)
    if not isinstance(result, IntakeRecoveryService) or result.resolver is not resolver:
        raise IntakeError()
    return result


def response(result: IntakeCommandObservation | IntakeRecoveryPage) -> Response:
    return Response(content=result.model_dump_json(), media_type="application/json", headers=_PRIVATE)


@intake_recovery_router.get(
    PREFIX + "/intake-recovery/commands/{command_id}",
    response_model=IntakeCommandObservation,
    responses=ERRORS,
)
async def observe_command(request: Request, command_id: str) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    return await service(request).observe_frozen(secret(request), reference(command_id), freeze=response)


@intake_recovery_router.get(PREFIX + "/intake-recovery", response_model=IntakeRecoveryPage, responses=ERRORS)
async def discover_admissions(request: Request) -> Response:
    query = request.query_params.multi_items()
    if (
        len(request.scope.get("query_string", b"")) > 160
        or len(query) > 1
        or any(k != "cursor" for k, _ in query)
    ):
        raise IntakeError("invalid_request")
    cursor = reference(query[0][1]) if query else None
    return await service(request).discover_frozen(secret(request), cursor, freeze=response)


def is_intake_recovery_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in intake_recovery_router.routes)
