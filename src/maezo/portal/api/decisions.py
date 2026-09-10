"""ADR0049 human decisions: strict HTTP framing, actual gateway, safe closed errors.

No engine REST proxy, client authority object, inferred completion or free-variable map.
No clinical conditional is duplicated here: the canonical form validators run on input.
"""

import json
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from starlette.routing import Match

from maezo.gateway.human.decision import PendingDecisionAdmission
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.gateway import HumanGateway
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.decisions import (
    DecisionContextResponse,
    DecisionErrorCode,
    DecisionReceiptResponse,
    DecisionSubmission,
    PortalDecisionError,
    revision_from_decimal,
)
from maezo.portal.contracts.models import OpaqueRef

DecisionServiceFactory = Callable[[HumanSessionResolver], HumanGateway]
_PREFIX = "/api/v1/portal"
_COMMAND_PATH = _PREFIX + "/commands/{command_id}"
_RECEIPT_PATH = _COMMAND_PATH + "/receipt"
_MAX_BODY = 65536  # Bounded transport allocation, not a clinical field/business limit.
_STATUS: dict[DecisionErrorCode, int] = {
    "invalid_request": 400,
    "invalid_decision": 422,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "revision_conflict": 409,
    "authority_unavailable": 503,
    "task_unavailable": 503,
    "form_projection_unavailable": 503,
    "form_contract_unavailable": 503,
    "admission_unavailable": 503,
    "credential_scope_mismatch": 503,
    "production_capabilities_unavailable": 503,
    "dependency_unavailable": 503,
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalDecisionError} for status in _STATUS.values()
}


class DecisionRequestError(ValueError):
    pass


def decision_error(code: DecisionErrorCode) -> JSONResponse:
    return JSONResponse(PortalDecisionError(code=code).model_dump(), status_code=_STATUS[code])


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise DecisionRequestError()
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise DecisionRequestError()


async def _body(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > _MAX_BODY:
            raise DecisionRequestError()
        data.extend(chunk)
    return bytes(data)


class DecisionRoute(APIRoute):
    """Validate bounded raw framing BEFORE FastAPI/Pydantic parses or logs any input."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                if self.path in (_COMMAND_PATH, _RECEIPT_PATH):
                    _receipt_query(request)
                elif request.scope.get("query_string", b""):
                    raise DecisionRequestError()
                data = await _body(request)
                if request.method == "POST":
                    if request.headers.getlist("content-type") != ["application/json"]:
                        raise DecisionRequestError()
                    if any(len(request.headers.getlist(name)) != 1 for name in ("origin", "x-csrf-token")):
                        return decision_error("authentication_unavailable")
                    parsed = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite
                    )
                    if type(parsed) is not dict:
                        raise DecisionRequestError()
                elif data:
                    raise DecisionRequestError()
            except Exception:
                return decision_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": data, "more_body": False}

            return await original(Request(request.scope, receive=replay))

        return checked


decision_router = APIRouter(route_class=DecisionRoute)


def is_decision_request(request: Request) -> bool:
    # Match only this router's real paths, including partial (wrong-method) matches.
    return any(route.matches(request.scope)[0] != Match.NONE for route in decision_router.routes)


def _reference(value: str) -> str:
    try:
        return TypeAdapter(OpaqueRef).validate_python(value)
    except Exception:
        raise DecisionRequestError() from None


def _secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise GatewayRefusalError("authentication_unavailable") from None


def _service(request: Request) -> HumanGateway:
    factory: DecisionServiceFactory | None = request.app.state.decision_service_factory
    if factory is None:
        raise GatewayRefusalError("production_capabilities_unavailable")
    resolver = request.app.state.human_session_resolver
    service = factory(resolver)
    if not isinstance(service, HumanGateway) or service._resolver is not resolver:
        raise GatewayRefusalError("production_capabilities_unavailable")
    return service


@decision_router.get(
    _PREFIX + "/tasks/{task_id}/decision-context",
    response_model=DecisionContextResponse,
    responses=_ERRORS,
)
async def decision_context(request: Request, task_id: str) -> Response:
    try:
        task_id = _reference(task_id)
        secret = _secret(request)
        result = await _service(request).read_decision_context(session_secret=secret, task_id=task_id)
        value = DecisionContextResponse.from_context(result)
        content = value.model_dump_json()
        if value.valid_until <= datetime.now(UTC):
            return decision_error("authority_unavailable")
        return Response(content=content, media_type="application/json")
    except DecisionRequestError:
        return decision_error("invalid_request")
    except GatewayRefusalError as exc:
        return decision_error(exc.code)
    except Exception:
        return decision_error("dependency_unavailable")


@decision_router.post(
    _PREFIX + "/tasks/{task_id}/decisions",
    response_model=PendingDecisionAdmission,
    status_code=202,
    responses=_ERRORS,
)
async def submit_decision(request: Request, task_id: str, body: DecisionSubmission) -> Response:
    try:
        task_id = _reference(task_id)
        if task_id != body.decision.task_id:
            return decision_error("invalid_request")
        secret = _secret(request)
        result = await _service(request).submit_decision(
            session_secret=secret,
            csrf_token=request.headers["x-csrf-token"],
            origin=request.headers["origin"],
            decision=body.decision.to_decision(),
            expected_authority_revision=revision_from_decimal(body.expected_authority_revision),
            expected_binding_digest=body.expected_binding_digest,
        )
        # Gateway verifies real adapter acknowledgement; this endpoint never creates one.
        return Response(content=result.model_dump_json(), media_type="application/json", status_code=202)
    except DecisionRequestError:
        return decision_error("invalid_request")
    except GatewayRefusalError as exc:
        return decision_error(exc.code)
    except Exception:
        return decision_error("dependency_unavailable")


def _receipt_query(request: Request) -> str:
    try:
        raw = request.scope.get("query_string", b"")
        if len(raw) > 4096 or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
            raise DecisionRequestError()
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
            errors="strict",
        )
        if len(pairs) != 1 or pairs[0][0] != "task_id":
            raise DecisionRequestError()
        return _reference(pairs[0][1])
    except Exception:
        raise DecisionRequestError() from None


async def _receipt(request: Request, command_id: str, task_id: str) -> Response:
    try:
        command_id, task_id = _reference(command_id), _reference(task_id)
        if _receipt_query(request) != task_id:
            raise DecisionRequestError()
        secret = _secret(request)
        gateway = _service(request)
        result = await gateway.read_receipt(session_secret=secret, task_id=task_id, command_id=command_id)
        if result.schema_version != "human-public-receipt.v2" or result.operation != "decision":
            return decision_error("operation_forbidden")
        value = DecisionReceiptResponse.model_validate(result.model_dump())
        content = value.model_dump_json()
        # Rendering may take time. Re-run the actual current receipt/resource/session
        # boundary after rendering; an evolving result is refreshed, never guessed.
        current = await gateway.read_receipt(session_secret=secret, task_id=task_id, command_id=command_id)
        if current.model_dump() != result.model_dump():
            return decision_error("revision_conflict")
        return Response(content=content, media_type="application/json")
    except DecisionRequestError:
        return decision_error("invalid_request")
    except GatewayRefusalError as exc:
        return decision_error(exc.code)
    except Exception:
        return decision_error("dependency_unavailable")


@decision_router.get(_COMMAND_PATH, response_model=DecisionReceiptResponse, responses=_ERRORS)
async def command_status(request: Request, command_id: str, task_id: str) -> Response:
    return await _receipt(request, command_id, task_id)


@decision_router.get(_RECEIPT_PATH, response_model=DecisionReceiptResponse, responses=_ERRORS)
async def command_receipt(request: Request, command_id: str, task_id: str) -> Response:
    return await _receipt(request, command_id, task_id)
