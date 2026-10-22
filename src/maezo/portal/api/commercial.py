"""Exact commercial POST surface; current source/data admission before body intake."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from maezo.adapters.amh.commercial import MAX_BODY_BYTES, SCHEMA_VERSION
from maezo.gateway.commercial import CommercialGateway, PublicCatalogGateway
from maezo.gateway.commercial_admission import (
    AuthenticatedPublicCatalogAdmission,
    AuthenticatedSalesAdmission,
)
from maezo.ports.commercial import CommercialRefusalError, CommercialStatus, SalesTransport

COMMERCIAL_PATH = "/api/v1/sales/commercial"
_HTTP = {"succeeded": 200, "denied": 403, "conflict": 409, "uncertain": 202, "unavailable": 503}
_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}
commercial_router = APIRouter()


def is_commercial_request(request: Request) -> bool:
    return request.scope.get("path") == COMMERCIAL_PATH


def commercial_error(
    status: CommercialStatus = "unavailable",
    reason_code: str = "binding_unqualified",
    *,
    http_status: int | None = None,
) -> JSONResponse:
    # Callers supply only fixed internal tokens, never request/provider error values.
    return JSONResponse(
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "operation_ref": None,
            "reason_code": reason_code,
            "data": None,
        },
        status_code=http_status or _HTTP[status],
        headers=_HEADERS,
    )


@commercial_router.post(COMMERCIAL_PATH)
async def commercial_command(request: Request) -> Response:
    gateway = getattr(request.app.state, "commercial_gateway", None)
    if not isinstance(gateway, CommercialGateway):
        return commercial_error()
    try:
        if isinstance(gateway.admission, AuthenticatedSalesAdmission):
            # Presence is not authority: authenticate and resolve caller/source binding
            # before gateway admission and before consuming any body bytes.
            async with asyncio.timeout(gateway.timeout_seconds):
                if (
                    len(request.headers.getlist("authorization")) != 1
                    or len(request.headers.getlist("x-commercial-connection")) != 1
                ):
                    return commercial_error("denied", "admission_required")
                transport = await gateway.admission.transport(
                    request.headers.get("authorization"), request.headers.get("x-commercial-connection")
                )
        else:
            # Existing qualified ASGI integrations supply trusted server metadata.
            extensions = request.scope.get("extensions", {})
            connection_ref = extensions.get("sales.commercial.connection_ref")
            if connection_ref is not None and not isinstance(connection_ref, str):
                return commercial_error("denied", "admission_required")
            transport = SalesTransport(connection_ref=connection_ref, method="POST", path=COMMERCIAL_PATH)
        scope = await gateway.admit(transport)
    except CommercialRefusalError as exc:
        # Providers cannot choose arbitrary error text; the codec validates tokens.
        try:
            refusal = gateway.codec.refusal(exc.status, exc.reason_code)
            return Response(
                refusal.wire_bytes,
                status_code=_HTTP[refusal.status],
                media_type="application/json",
                headers=_HEADERS,
            )
        except Exception:
            return commercial_error()
    except Exception:
        return commercial_error()
    try:
        if request.scope.get("query_string", b"") or request.headers.getlist("content-type") != [
            "application/json"
        ]:
            return commercial_error("denied", "invalid_request", http_status=400)
        chunks: list[bytes] = []
        size = 0
        async with asyncio.timeout(gateway.timeout_seconds):
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    return commercial_error("denied", "invalid_request", http_status=400)
                chunks.append(chunk)
        command = gateway.codec.parse_command(b"".join(chunks))
    except Exception:
        return commercial_error("denied", "invalid_request", http_status=400)
    result = await gateway.execute(command, scope)
    return Response(
        result.wire_bytes, status_code=_HTTP[result.status], media_type="application/json", headers=_HEADERS
    )


PUBLIC_CATALOG_PATH = "/api/v1/sales/public/catalog"


def is_public_catalog_request(request: Request) -> bool:
    return request.scope.get("path") == PUBLIC_CATALOG_PATH


def public_catalog_error() -> JSONResponse:
    return JSONResponse(
        {
            "schemaVersion": "sales-catalog.proposed.v1",
            "status": "unavailable",
            "reason": "source_unavailable",
        },
        status_code=503,
        headers=_HEADERS,
    )


@commercial_router.get(PUBLIC_CATALOG_PATH)
async def public_catalog(request: Request) -> Response:
    gateway = getattr(request.app.state, "public_catalog_gateway", None)
    if not isinstance(gateway, PublicCatalogGateway) or not isinstance(
        gateway.admission, AuthenticatedPublicCatalogAdmission
    ):
        return public_catalog_error()
    # Never decode body/query/cookies or accept caller-owned authority overrides.
    if (
        request.scope.get("query_string", b"")
        or request.headers.getlist("transfer-encoding")
        or request.headers.getlist("content-length") not in ([], ["0"])
        or len(request.headers.getlist("authorization")) != 1
        or len(request.headers.getlist("x-commercial-connection")) != 1
        or any(
            key.lower().startswith(("x-tenant", "x-actor", "x-purpose", "x-role", "x-source", "x-operator"))
            for key in request.headers
        )
    ):
        return public_catalog_error()
    try:
        async with asyncio.timeout(gateway.timeout_seconds):
            transport = await gateway.admission.transport(
                request.headers.get("authorization"), request.headers.get("x-commercial-connection")
            )
            # HTTP body framing is rejected above. Never consume a body at this
            # public-data boundary: machine/public grants do not admit subject input.
            body = await gateway.read(transport)
        return Response(body, media_type="application/json", status_code=200, headers=_HEADERS)
    except Exception:
        return public_catalog_error()
