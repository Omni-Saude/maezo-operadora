"""Current-authorized W6 external case list/detail. Staff dossier remains separate."""

import re
from collections.abc import Callable
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import Response

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.intake.cases import CaseService
from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.intakes import ERRORS, PREFIX, ProductRoute, intake_error, reference, secret
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.cases import CaseDetail, CasePage

CaseServiceFactory = Callable[[HumanSessionResolver], CaseService]
case_router = APIRouter(route_class=ProductRoute)


def _query(request: Request, detail: bool) -> dict[str, str]:
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
        if (
            (detail and pairs)
            or len({key for key, _ in pairs}) != len(pairs)
            or any(key not in {"kind", "limit", "cursor"} or not value for key, value in pairs)
        ):
            raise ValueError
        result = dict(pairs)
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


async def _read(request: Request, case_ref: str | None = None) -> Response:
    query = _query(request, case_ref is not None)
    if case_ref is not None:
        case_ref = reference(case_ref)
    factory: CaseServiceFactory | None = request.app.state.case_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, CaseService) or service.resolver is not resolver:
        raise IntakeError()
    try:
        raw = await service.read(secret(request), case_ref=case_ref, **query)
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


@case_router.get(PREFIX + "/cases/{case_ref}", response_model=CaseDetail, responses=ERRORS)
async def detail_case(request: Request, case_ref: str) -> Response:
    return await _read(request, case_ref)
