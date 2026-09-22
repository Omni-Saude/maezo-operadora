"""Employee task routes: two reads, plus the INTERIM completion registered in DL-0049.

Server DI, opaque cookie and closed safe errors throughout. The completion route is the only
write in this module and it is gated twice, independently: `PortalSettings.direct_completion`
(off by default) decides whether the route answers at all, and the read composition decides
whether a `DirectTaskCompletion` capability exists behind it (absent by default). Neither gate
knows about the other; both must be on. See `gateway/human/completion.py`.
"""

import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from starlette.routing import Match

from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.gateway import HumanGateway
from maezo.gateway.human.queue import ReadRefusalError
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.completions import (
    CompletionErrorCode,
    PortalCompletionError,
    TaskCompletionResponse,
    TaskCompletionSubmission,
)
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


# ---------------------------------------------------------------------------------------
# INTERIM completion (DL-0049). Its own router, so the read surface is unchanged.
# ---------------------------------------------------------------------------------------
_COMPLETION_PATH = _PREFIX + "/{task_id}/completion"
_COMPLETION_MAX_BODY = 65536  # Bounded transport allocation, not a clinical/business limit.
_COMPLETION_STATUS: dict[CompletionErrorCode, int] = {
    "invalid_request": 400,
    "invalid_completion": 422,
    "session_unavailable": 401,
    "employee_access_required": 403,
    "resource_unavailable": 404,
    "revision_conflict": 409,
    # 501, not 404: the gate being off is a DECLARED deployment state (DL-0049), and dressing
    # it as "not found" would make an operator debug an authorization problem that is not one.
    "completion_unavailable": 501,
    "completion_dependency_unavailable": 503,
}
_COMPLETION_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalCompletionError} for status in _COMPLETION_STATUS.values()
}
#: Refusal codes of the existing gateway, mapped onto this route's taxonomy. Nothing is
#: invented: `_read_session` raises the first two, `_authorized` the next three, and the
#: interim completion path the last two. An unknown code degrades to 503, never to success.
_FROM_READ_REFUSAL: dict[str, CompletionErrorCode] = {
    "invalid_request": "invalid_request",
    "session_unavailable": "session_unavailable",
    "employee_access_required": "employee_access_required",
    "resource_unavailable": "resource_unavailable",
    "refresh_required": "revision_conflict",
    "read_dependency_unavailable": "completion_dependency_unavailable",
}
_FROM_GATEWAY_REFUSAL: dict[str, CompletionErrorCode] = {
    "authentication_unavailable": "session_unavailable",
    # The mandate asks for 403 when the colleague's group does not match. That is a deliberate
    # divergence from the GET taxonomy, which folds `operation_forbidden` into 404 to avoid an
    # existence oracle (`HumanGateway._read_authorized`). It only applies to a task id the
    # caller already holds, and it is what makes the isolation test readable on the test page.
    "operation_forbidden": "employee_access_required",
    "task_unavailable": "resource_unavailable",
    "revision_conflict": "revision_conflict",
    "authority_unavailable": "completion_dependency_unavailable",
    "admission_unavailable": "completion_dependency_unavailable",
    "production_capabilities_unavailable": "completion_dependency_unavailable",
}


@dataclass(frozen=True, slots=True)
class CompletionPolicy:
    """Deployment policy for the interim route, resolved once in `create_app`.

    `enabled` is `MAEZO_PORTAL_DIRECT_COMPLETION` and defaults to false. `origins` is the
    portal's own `public_origin` plus `MAEZO_PORTAL_CORS_ORIGINS`, which defaults to empty —
    so out of the box only a same-origin caller can reach the route. Where those two settings
    may be turned on is mechanized by `scripts/ci/check_portal_direct_completion.py`.
    """

    enabled: bool
    origins: frozenset[str]


def completion_error(code: CompletionErrorCode) -> JSONResponse:
    return JSONResponse(
        PortalCompletionError(code=code).model_dump(by_alias=True),
        status_code=_COMPLETION_STATUS[code],
    )


class CompletionRoute(APIRoute):
    """Validate bounded raw framing BEFORE FastAPI/Pydantic parses or logs any input.

    The same posture as `decisions.py::DecisionRoute`, with this route's own taxonomy: no query
    string, one exact `application/json`, a bounded body, and exactly one `origin` plus one
    `x-csrf-token` header. The CSRF token is compared against the server-side session inside
    the gateway; the header's PRESENCE is checked here so the handler can read it unambiguously.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            data = bytearray()
            try:
                if request.scope.get("query_string", b""):
                    raise ValueError("query")
                # The reference is part of the PATH, so it is checked before the body: a
                # malformed task id is a bad request, not a rejected completion, and reporting
                # it as 422 would send a caller hunting through its form for a routing bug.
                TypeAdapter(OpaqueRef).validate_python(request.path_params.get("task_id"))
                async for chunk in request.stream():
                    if len(data) + len(chunk) > _COMPLETION_MAX_BODY:
                        raise ValueError("body")
                    data.extend(chunk)
                if request.headers.getlist("content-type") != ["application/json"]:
                    raise ValueError("content type")
                if any(len(request.headers.getlist(name)) != 1 for name in ("origin", "x-csrf-token")):
                    return completion_error("session_unavailable")
            except Exception:
                return completion_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": bytes(data), "more_body": False}

            return await original(Request(request.scope, receive=replay))

        return checked


completion_router = APIRouter(route_class=CompletionRoute)


def is_task_completion(request: Request) -> bool:
    """Match only this router's real paths, including partial (wrong-method) matches.

    Callers MUST consult this before `is_task_read`: the completion path lives under the read
    prefix, so the read predicate also matches it and would answer with the read error schema.
    """
    return any(route.matches(request.scope)[0] != Match.NONE for route in completion_router.routes)


def _policy(request: Request) -> CompletionPolicy:
    policy = getattr(request.app.state, "completion_policy", None)
    return policy if isinstance(policy, CompletionPolicy) else CompletionPolicy(False, frozenset())


@completion_router.post(
    _COMPLETION_PATH,
    response_model=TaskCompletionResponse,
    status_code=200,
    responses=_COMPLETION_ERRORS,
)
async def complete_task(request: Request, task_id: str, body: TaskCompletionSubmission) -> Response:
    """INTERIM (DL-0049): close an ESCALATION task from the portal.

    This handler owns no authorization logic. It resolves the same gateway the two GETs use
    (`task_read_service_factory`) and calls `HumanGateway.complete_task`, which proves the
    `staff` audience, role and candidate group coinciding in one membership, the candidate
    group against the task, and re-resolves the session after the remote reads. What lives
    here is transport: the declared gate, the origin allowlist, the closed body, the error map.
    """
    policy = _policy(request)
    if not policy.enabled:
        # Before any cookie is read and any dependency is touched: the gate is deployment
        # configuration, so refusing it must not depend on having a session.
        return completion_error("completion_unavailable")
    try:
        try:
            task_id = TypeAdapter(OpaqueRef).validate_python(task_id)
        except Exception:
            return completion_error("invalid_request")
        # The browser's Origin is authorization-adjacent, never authorization: it only says
        # which page may TALK to this route. `public_origin` is always allowed; anything else
        # must have been named in `MAEZO_PORTAL_CORS_ORIGINS`, which ships empty.
        if request.headers["origin"] not in policy.origins:
            return completion_error("session_unavailable")
        secret = _secret(request)
        service = _service(request)
        result = await service.complete_task(
            session_secret=secret,
            csrf_token=request.headers["x-csrf-token"],
            task_id=task_id,
            resultado=body.resultado,
            notas_resolucao=body.notas_resolucao,
        )
        snapshot = result.snapshot
        value = TaskCompletionResponse(
            task_id=snapshot.task_id,
            process_definition_key=snapshot.process_definition_key,
            process_definition_version=str(snapshot.process_definition_version),
            task_definition_key=snapshot.task_definition_key,
            form_key=snapshot.form_key,
            resultado=result.resultado,
            consumed_task_revision=str(result.consumed_task_revision),
            authority_revision=str(result.authority_revision),
            evidence_revision=str(snapshot.evidence_revision),
            evidence_digest=snapshot.evidence_digest,
            completed_at=result.completed_at,
            audit_intent_ref=result.audit_intent_ref,
            audit_result_ref=result.audit_result_ref,
        )
        return Response(
            content=value.model_dump_json(by_alias=True),
            media_type="application/json",
            status_code=200,
        )
    except ReadRefusalError as exc:
        return completion_error(_FROM_READ_REFUSAL.get(exc.code, "completion_dependency_unavailable"))
    except GatewayRefusalError as exc:
        return completion_error(_FROM_GATEWAY_REFUSAL.get(exc.code, "completion_dependency_unavailable"))
    except Exception:
        # Never leak an upstream narrative, and never report a completion we cannot prove.
        return completion_error("completion_dependency_unavailable")
