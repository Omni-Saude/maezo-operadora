"""General-zone communication metadata only. No content DTO, keys or PHI routes."""

import re
from collections.abc import Callable
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.routing import Match

from maezo.gateway.communications.service import CommunicationService
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.intakes import ERRORS, PREFIX, ProductRoute, intake_error, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.communications import (
    CommunicationPage,
    CommunicationReceipt,
    CommunicationSubmission,
    HistoryPage,
)

CommunicationServiceFactory = Callable[[HumanSessionResolver], CommunicationService]
communication_router = APIRouter(route_class=ProductRoute)


def communication_error(error: ExternalCaseError) -> Response:
    return intake_error(
        {"invalid": "invalid_request", "conflict": "conflict", "denied": "operation_forbidden"}.get(
            error.code, "dependency_unavailable"
        )
    )


def _query(request: Request) -> tuple[str | None, int]:
    raw = request.scope.get("query_string", b"")
    try:
        if len(raw) > 4096 or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
            errors="strict",
        )
        if len({key for key, _ in pairs}) != len(pairs) or any(
            key not in {"cursor", "limit"} or not value for key, value in pairs
        ):
            raise ValueError
        data = dict(pairs)
        if (
            not re.fullmatch(r"[1-9][0-9]{0,2}", data.get("limit", "25"))
            or int(data.get("limit", "25")) > 100
        ):
            raise ValueError
        cursor = reference(data["cursor"]) if "cursor" in data else None
        return cursor, int(data.get("limit", "25"))
    except Exception:
        raise IntakeError("invalid_request") from None


def _service(request: Request) -> CommunicationService:
    factory: CommunicationServiceFactory | None = request.app.state.communication_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    value = factory(resolver)
    if not isinstance(value, CommunicationService) or value.resolver is not resolver:
        raise IntakeError()
    return value


@communication_router.post(
    PREFIX + "/cases/{case_ref}/communications", response_model=CommunicationReceipt, responses=ERRORS
)
async def publish_communication(request: Request, case_ref: str, body: CommunicationSubmission) -> Response:
    try:
        return await _service(request).publish(
            secret(request),
            csrf=request.headers["x-csrf-token"],
            origin=request.headers["origin"],
            case_ref=reference(case_ref),
            body=body,
            freeze=_render,
        )
    except ExternalCaseError as error:
        return communication_error(error)


async def _page(request: Request, case_ref: str, *, history: bool) -> Response:
    cursor, limit = _query(request)
    try:
        return await _service(request).page(
            secret(request),
            operation="list_history" if history else "list_messages",
            case_ref=reference(case_ref),
            cursor=cursor,
            limit=limit,
            freeze=_render,
        )
    except ExternalCaseError as error:
        return communication_error(error)


@communication_router.get(
    PREFIX + "/cases/{case_ref}/communications", response_model=CommunicationPage, responses=ERRORS
)
async def list_communications(request: Request, case_ref: str) -> Response:
    return await _page(request, case_ref, history=False)


@communication_router.get(PREFIX + "/cases/{case_ref}/history", response_model=HistoryPage, responses=ERRORS)
async def case_history(request: Request, case_ref: str) -> Response:
    return await _page(request, case_ref, history=True)


def is_communication_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in communication_router.routes)


def _render(raw: bytes) -> Response:
    return Response(content=raw, media_type="application/json")
