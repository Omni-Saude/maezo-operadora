"""Bounded JSON intake HTTP boundary with existing cookie/CSRF session semantics."""

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from starlette.routing import Match

from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import IntakeService
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.decisions import _body, _nonfinite, _pairs
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeReceipt, PortalIntakeError, ResourceRef

IntakeServiceFactory = Callable[[HumanSessionResolver], IntakeService]
PREFIX = "/api/v1/portal"
STATUS = {
    "invalid_request": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "resource_unavailable": 404,
    "conflict": 409,
    "dependency_unavailable": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": PortalIntakeError} for status in STATUS.values()}


def intake_error(code: str = "dependency_unavailable") -> JSONResponse:
    if code not in STATUS:
        code = "dependency_unavailable"
    return JSONResponse({"code": code}, status_code=STATUS[code])


def reference(value: str) -> str:
    try:
        return TypeAdapter(ResourceRef).validate_python(value)
    except Exception:
        raise IntakeError("invalid_request") from None


def secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise IntakeError("authentication_unavailable") from None


class ProductRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                data = await _body(request)
                if request.method == "POST":
                    if request.scope.get("query_string", b"") or request.headers.getlist("content-type") != [
                        "application/json"
                    ]:
                        raise ValueError
                    if any(len(request.headers.getlist(name)) != 1 for name in ("origin", "x-csrf-token")):
                        return intake_error("authentication_unavailable")
                    parsed = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite
                    )
                    if type(parsed) is not dict:
                        raise ValueError
                elif data:
                    raise ValueError
            except Exception:
                return intake_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": data, "more_body": False}

            try:
                return await original(Request(request.scope, receive=replay))
            except IntakeError as exc:
                return intake_error(exc.code)
            except RequestValidationError:
                return intake_error("invalid_request")
            except AuthenticationError:
                return intake_error("authentication_unavailable")
            except Exception:
                return intake_error()

        return checked


intake_router = APIRouter(route_class=ProductRoute)


def service(request: Request) -> IntakeService:
    factory: IntakeServiceFactory | None = request.app.state.intake_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    result = factory(resolver)
    if not isinstance(result, IntakeService) or result.resolver is not resolver:
        raise IntakeError()
    return result


@intake_router.post(PREFIX + "/intakes/auth", response_model=IntakeReceipt, status_code=202, responses=ERRORS)
async def submit_intake(request: Request, body: AuthIntakeSubmission) -> Response:
    return await service(request).submit_frozen(
        secret(request),
        request.headers["x-csrf-token"],
        request.headers["origin"],
        body,
        freeze=lambda result: Response(
            content=result.model_dump_json(), status_code=202, media_type="application/json"
        ),
    )


@intake_router.get(PREFIX + "/intakes/{intake_ref}", response_model=IntakeReceipt, responses=ERRORS)
async def read_intake(request: Request, intake_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    return await service(request).read_frozen(
        secret(request),
        reference(intake_ref),
        freeze=lambda result: Response(content=result.model_dump_json(), media_type="application/json"),
    )


def is_product_request(request: Request) -> bool:
    from maezo.portal.api.cases import case_router
    from maezo.portal.api.documents import document_router

    return any(
        route.matches(request.scope)[0] != Match.NONE
        for router in (intake_router, case_router, document_router)
        for route in router.routes
    )
