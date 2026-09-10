"""Separate private identity-owner application; never mount on the public portal."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from maezo.gateway.communications.identity_owner import (
    MAX_BODY,
    IdentityMismatchOwner,
    IdentityMismatchRevocation,
)
from maezo.gateway.communications.production_config import PhiProductionError
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.identity_peer import identity_peer


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def create_identity_owner_app(owner: IdentityMismatchOwner) -> FastAPI:
    """Construct the one-route private app around the already bootstrapped store."""
    if type(owner) is not IdentityMismatchOwner:
        raise ValueError("identity owner unavailable")
    app = FastAPI(
        title="Maezo private identity owner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )

    @app.post("/internal/v1/identity/revoke-mismatched-session")
    async def revoke(request: Request) -> Response:
        try:
            peer = identity_peer(request.scope)
            if request.scope.get("query_string", b""):
                raise ValueError
            raw = await request.body()
            if not 0 < len(raw) <= MAX_BODY:
                raise ValueError
            parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            if type(parsed) is not dict:
                raise ValueError
            command = IdentityMismatchRevocation.model_validate_json(raw)
            result = await owner.revoke(
                command,
                peer_spki_sha256=peer.spki_sha256,
                purpose="identity-mismatch-revocation.v1",
            )
            peer.current()
            return Response(
                content=result.model_dump_json(by_alias=True),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except (AuthenticationError, PermissionError, UnicodeError, ValueError, ValidationError):
            return Response(status_code=401, headers={"Cache-Control": "no-store"})
        except PhiProductionError:
            return Response(status_code=503, headers={"Cache-Control": "no-store"})

    @app.exception_handler(Exception)
    async def unavailable(request: Request, exc: Exception) -> Response:
        return JSONResponse(
            {"erro": "Não foi possível validar a solicitação privada."},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    return app
