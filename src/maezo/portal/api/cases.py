"""Audience-dispatched case reads; exact staff detail does not widen external grants."""

import re
from collections.abc import Callable
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import Response

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.intake.cases import CaseService
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.staff_cases.models import StaffCaseError
from maezo.gateway.staff_cases.service import StaffCaseService
from maezo.portal.api.intakes import ERRORS, PREFIX, ProductRoute, intake_error, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.cases import CaseDetail, CasePage
from maezo.portal.contracts.staff_cases import StaffDetail

CaseServiceFactory = Callable[[HumanSessionResolver], CaseService]
StaffCaseServiceFactory = Callable[[HumanSessionResolver], StaffCaseService]
case_router = APIRouter(route_class=ProductRoute)


def _pairs(request: Request) -> tuple[tuple[str, str], ...]:
    raw = request.scope.get("query_string", b"")
    try:
        if len(raw) > 4096 or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=3,
            errors="strict",
        )
        if len({key for key, _ in pairs}) != len(pairs) or any(
            key not in {"kind", "limit", "cursor", "task_limit", "task_cursor"} or not value
            for key, value in pairs
        ):
            raise ValueError
        return tuple(pairs)
    except Exception:
        raise IntakeError("invalid_request") from None


def _external_query(pairs: tuple[tuple[str, str], ...], detail: bool) -> dict[str, str]:
    try:
        if detail and pairs:
            raise ValueError
        result = dict(pairs)
        if any(key not in {"kind", "limit", "cursor"} for key in result):
            raise ValueError
        if (
            not re.fullmatch(r"[1-9][0-9]{0,2}", result.get("limit", "25"))
            or int(result.get("limit", "25")) > 100
        ):
            raise ValueError
        if "kind" in result and result["kind"] not in {"authorization", "reimbursement", "account"}:
            raise ValueError
        if "cursor" in result and not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", result["cursor"]):
            raise ValueError
        return result
    except Exception:
        raise IntakeError("invalid_request") from None


def _staff_query(pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    try:
        result = dict(pairs)
        if any(key not in {"task_limit", "task_cursor"} for key in result):
            raise ValueError
        if (
            not re.fullmatch(r"[1-9][0-9]{0,2}", result.get("task_limit", "25"))
            or int(result.get("task_limit", "25")) > 100
        ):
            raise ValueError
        if "task_cursor" in result and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", result["task_cursor"]
        ):
            raise ValueError
        return result
    except Exception:
        raise IntakeError("invalid_request") from None


async def _read(request: Request, case_ref: str | None = None) -> Response:
    pairs = _pairs(request)
    if case_ref is not None:
        case_ref = reference(case_ref)
    resolver = request.app.state.human_session_resolver
    session_secret = secret(request)
    resolved = await resolver.resolve(session_secret)
    if resolved.membership.audience == "staff":
        if case_ref is None:
            raise IntakeError()
        query = _staff_query(pairs)
        factory: StaffCaseServiceFactory | None = request.app.state.staff_case_service_factory
        if factory is None:
            raise IntakeError()
        service = factory(resolver)
        if not isinstance(service, StaffCaseService) or service.resolver is not resolver:
            raise IntakeError()
        try:
            raw = await service.read(session_secret, case_ref=case_ref, **query)
            return Response(content=raw, media_type="application/json")
        except StaffCaseError as exc:
            return intake_error(
                {"invalid": "invalid_request", "denied": "resource_unavailable", "conflict": "conflict"}.get(
                    exc.code, "dependency_unavailable"
                )
            )
    query = _external_query(pairs, case_ref is not None)
    factory = request.app.state.case_service_factory
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, CaseService) or service.resolver is not resolver:
        raise IntakeError()
    try:
        raw = await service.read(session_secret, case_ref=case_ref, **query)
        return Response(content=raw, media_type="application/json")
    except ExternalCaseError as exc:
        return intake_error(
            {"invalid": "invalid_request", "denied": "operation_forbidden", "conflict": "conflict"}.get(
                exc.code, "dependency_unavailable"
            )
        )


@case_router.get(PREFIX + "/cases", response_model=CasePage, responses=ERRORS)
async def list_cases(request: Request) -> Response:
    return await _read(request)


@case_router.get(
    PREFIX + "/cases/{case_ref}",
    response_model=CaseDetail | StaffDetail,
    responses=ERRORS,
    # Match the audience-specific manual parser; do not add pre-auth coercion.
    openapi_extra={
        "parameters": [
            {
                "name": "task_limit",
                "in": "query",
                "required": False,
                "description": "Staff only. External case reads reject query parameters.",
                "schema": {"type": "string", "pattern": "^(?:[1-9][0-9]?|100)$", "default": "25"},
            },
            {
                "name": "task_cursor",
                "in": "query",
                "required": False,
                "description": (
                    "Staff only. Reserved for qualified task pagination; non-null cursors "
                    "currently return dependency_unavailable (503)."
                ),
                "schema": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$"},
            },
        ]
    },
)
async def detail_case(request: Request, case_ref: str) -> Response:
    return await _read(request, case_ref)
