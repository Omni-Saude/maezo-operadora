"""Read-only employee routes; server DI, opaque cookie and closed safe errors."""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import TypeAdapter

from maezo.gateway.human.gateway import HumanGateway
from maezo.gateway.human.queue import ReadRefusalError
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.models import OpaqueRef
from maezo.portal.contracts.queues import (
    PortalReadError,
    ReadErrorCode,
    TaskQueuePage,
    TaskQueueRequest,
    TaskReadResponse,
)

ReadServiceFactory = Callable[[HumanSessionResolver], HumanGateway]
_PREFIX = "/api/v1/portal/tasks"
_STATUS: dict[ReadErrorCode, int] = {
    "invalid_request": 400,
    "session_unavailable": 401,
    "employee_access_required": 403,
    "resource_unavailable": 404,
    "refresh_required": 409,
    "read_dependency_unavailable": 503,
}
task_router = APIRouter()
_ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": PortalReadError} for status in _STATUS.values()}


def is_task_read(request: Request) -> bool:
    return request.url.path == _PREFIX or request.url.path.startswith(_PREFIX + "/")


def read_error(code: ReadErrorCode) -> JSONResponse:
    return JSONResponse(PortalReadError(code=code).model_dump(by_alias=True), status_code=_STATUS[code])


def _render(value: TaskQueuePage | TaskReadResponse) -> Response:
    # Called INSIDE the gateway's synchronous finalization; guard follows all construction.
    return Response(content=value.model_dump_json(by_alias=True), media_type="application/json")


async def _request(request: Request, *, detail: bool) -> TaskQueueRequest | None:
    async for chunk in request.stream():
        if chunk:
            raise ReadRefusalError("invalid_request")
    try:
        raw = request.scope.get("query_string", b"")
        if len(raw) > 4096 or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError("query encoding")
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=3,
            errors="strict",
        )
        if detail:
            if pairs:
                raise ValueError("detail query")
            return None
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate query")
        data = dict(pairs)
        if any(key not in {"queue", "limit", "cursor"} or not value for key, value in pairs):
            raise ValueError("unknown or blank query")
        limit = data.get("limit", "25")
        if not re.fullmatch(r"[1-9][0-9]{0,2}", limit):
            raise ValueError("noncanonical limit")
        return TaskQueueRequest.model_validate(
            {"queue": data.get("queue"), "limit": int(limit), "cursor": data.get("cursor")}
        )
    except Exception:
        raise ReadRefusalError("invalid_request") from None


def _service(request: Request) -> HumanGateway:
    factory: ReadServiceFactory | None = request.app.state.task_read_service_factory
    if factory is None:
        raise ReadRefusalError("read_dependency_unavailable")
    resolver = request.app.state.human_session_resolver
    service = factory(resolver)
    if not isinstance(service, HumanGateway) or service._resolver is not resolver:
        raise ReadRefusalError("read_dependency_unavailable")
    return service


def _secret(request: Request) -> str:
    # The existing cookie helper remains the single ambiguity policy.
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise ReadRefusalError("session_unavailable") from None


@task_router.get(_PREFIX, response_model=TaskQueuePage, responses=_ERRORS)
async def list_tasks(request: Request) -> Response:
    try:
        query = await _request(request, detail=False)
        if query is None:
            raise ReadRefusalError("invalid_request")
        secret = _secret(request)
        service = _service(request)
        return await service.list_tasks(session_secret=secret, request=query, render=_render)
    except ReadRefusalError as exc:
        return read_error(exc.code)
    except Exception:
        return read_error("read_dependency_unavailable")


@task_router.get(_PREFIX + "/{task_id}", response_model=TaskReadResponse, responses=_ERRORS)
async def read_task(request: Request, task_id: str) -> Response:
    try:
        await _request(request, detail=True)
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            raise ReadRefusalError("invalid_request") from None
        secret = _secret(request)
        service = _service(request)
        return await service.read_task_envelope(session_secret=secret, task_id=task_id, render=_render)
    except ReadRefusalError as exc:
        return read_error(exc.code)
    except Exception:
        return read_error("read_dependency_unavailable")
