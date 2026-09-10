"""Private Uvicorn h11 transport identity derived only from the accepted TLS socket."""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

_EXTENSION = "maezo.identity-peer.v1"


@dataclass(frozen=True, slots=True)
class IdentityPeer:
    certificate_sha256: str
    spki_sha256: str
    not_before: datetime
    not_after: datetime

    def current(self, now: datetime | None = None) -> None:
        value = now or datetime.now(UTC)
        if value.tzinfo is None or not self.not_before <= value < self.not_after:
            raise PermissionError("private peer unavailable")


class _PeerApplication:
    def __init__(self, app: ASGIApp, peer: IdentityPeer) -> None:
        self.app, self.peer = app, peer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extensions = scope.get("extensions", {})
        if not isinstance(extensions, dict) or _EXTENSION in extensions:
            await _refuse(scope, receive, send)
            return
        try:
            self.peer.current()
        except Exception:
            await _refuse(scope, receive, send)
            return
        protected = dict(scope)
        protected["extensions"] = dict(extensions, **{_EXTENSION: self.peer})
        await self.app(protected, receive, send)


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"cache-control", b"no-store"), (b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def identity_peer(scope: Scope) -> IdentityPeer:
    extensions = scope.get("extensions")
    value = extensions.get(_EXTENSION) if isinstance(extensions, dict) else None
    if type(value) is not IdentityPeer:
        raise PermissionError("private peer unavailable")
    value.current()
    return value


class IdentityPeerH11Protocol(H11Protocol):
    """Per-connection app wrapper; it never changes Config.loaded_app globally."""

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        try:
            tls = transport.get_extra_info("ssl_object")
            context = getattr(tls, "context", None)
            certificate_der = tls.getpeercert(binary_form=True) if tls is not None else None
            if (
                tls is None
                or context is None
                or context.verify_mode != ssl.CERT_REQUIRED
                or not certificate_der
            ):
                raise PermissionError
            certificate = x509.load_der_x509_certificate(certificate_der)
            spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            peer = IdentityPeer(
                certificate_sha256=hashlib.sha256(certificate_der).hexdigest(),
                spki_sha256=hashlib.sha256(spki).hexdigest(),
                not_before=certificate.not_valid_before_utc,
                not_after=certificate.not_valid_after_utc,
            )
            peer.current()
            self.app = _PeerApplication(self.app, peer)
            super().connection_made(transport)
        except Exception:
            transport.close()


def private_uvicorn_options(
    *,
    ssl_keyfile: str,
    ssl_certfile: str,
    ssl_ca_certs: str,
    timeout_keep_alive: int,
) -> dict[str, Any]:
    """Exact private-listener controls consumed by the common process owner."""
    if not 1 <= timeout_keep_alive <= 10:
        raise ValueError("invalid private listener timeout")
    return {
        "http": IdentityPeerH11Protocol,
        "ws": "none",
        "access_log": False,
        "proxy_headers": False,
        "ssl_keyfile": ssl_keyfile,
        "ssl_certfile": ssl_certfile,
        "ssl_ca_certs": ssl_ca_certs,
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "timeout_keep_alive": timeout_keep_alive,
        "limit_concurrency": 32,
        "h11_max_incomplete_event_size": 16384,
    }
