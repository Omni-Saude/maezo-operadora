"""Fixed-route private mTLS Q2 client; no browser-selectable URL or signing oracle."""

from __future__ import annotations

import base64
import hashlib
import secrets
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel

from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.contracts.queues import ReadErrorCode
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import AuthoritativeTask, CurrentTaskAuthority
from .queue import CatalogExpectation, CatalogTrustAnchor, ReadRefusalError
from .read_credentials import ReadCredentialPartition, unavailable
from .read_profile import (
    VALUE_TYPES,
    NativeContinuity,
    Operation,
    digest,
    instant,
    parse_model,
    wire,
)


@dataclass(frozen=True, slots=True, repr=False)
class ReadResult:
    value: BaseModel = field(repr=False)
    source_observed_at: datetime
    valid_until: datetime
    request_digest: str


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Per-request HTTP client must not close its application-owned connection pool."""

    def __init__(self, pool: httpx.AsyncBaseTransport) -> None:
        self._pool = pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._pool.handle_async_request(request)

    async def aclose(self) -> None:
        return None


class PortalReadClient:
    def __init__(
        self,
        *,
        origin: str,
        tls_context: ssl.SSLContext,
        server_spki_sha256: str,
        partition: ReadCredentialPartition,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not tls_context.check_hostname
            or tls_context.verify_mode != ssl.CERT_REQUIRED
            or timeout_seconds <= 0
        ):
            raise unavailable()
        if len(server_spki_sha256) != 64 or any(c not in "0123456789abcdef" for c in server_spki_sha256):
            raise unavailable()
        self._origin, self._server_pin = origin.rstrip("/"), server_spki_sha256
        self.partition = partition
        self.read_context_id = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        self._owns_transport = transport is None
        self._transport = transport or httpx.AsyncHTTPTransport(verify=tls_context, trust_env=False)
        self._http = httpx.AsyncClient(
            verify=tls_context,
            transport=_BorrowedTransport(self._transport),
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
        )
        self._closed = False

    def _peer(self, response: httpx.Response) -> None:
        stream = response.extensions.get("network_stream")
        tls = stream.get_extra_info("ssl_object") if stream is not None else None
        if tls is None:
            raise unavailable()
        cert = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
        raw = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        if hashlib.sha256(raw).hexdigest() != self._server_pin:
            raise unavailable()

    async def _send(self, operation: Operation, extra: dict[str, Any]) -> ReadResult:
        try:
            if self._closed:
                raise unavailable()
            await self.partition.prepare()
            now = self.partition.guard()
            signing, admission = self.partition.signing, self.partition.admission
            assert signing is not None and admission is not None
            a = admission.record
            request = {
                "schema": "portal-engine-read.v1",
                "operation": operation,
                "scope": wire(a.scope),
                "engine_name": a.engine_name,
                "database_incarnation": a.database_incarnation,
                "read_deployment_ref": a.read_deployment_ref,
                "read_deployment_digest": a.read_deployment_digest,
                "request_id": base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii"),
                "read_context_id": self.read_context_id,
                **extra,
            }
            request_digest = digest(request)
            until = min(
                signing.not_after, a.valid_until, now + timedelta(seconds=signing.max_envelope_seconds)
            )
            issued, expires = int(now.timestamp()), int(until.timestamp())
            if expires <= issued:
                raise unavailable()
            outer = {
                "schema": "portal-read-envelope.v1",
                "purpose": "portal-task-read",
                "algorithm": "Ed25519",
                "audience": signing.audience,
                "issuer": signing.requester.issuer,
                "tenant": a.scope.tenant,
                "key_id": signing.requester.key_id,
                "issued_at": str(issued),
                "expires_at": str(expires),
                "digest": request_digest,
                "request": request,
            }
            outer["signature"] = (
                base64.urlsafe_b64encode(signing.sign(canonicalize(outer), self.partition.guard()))
                .rstrip(b"=")
                .decode("ascii")
            )
            raw = canonicalize(outer)
            if len(raw) > 65536:
                raise unavailable()
            self.partition.guard()
            async with self._http.stream(
                "POST",
                self._origin + "/maezo-human-read/v1/" + operation,
                content=raw,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                self._peer(response)
                if "set-cookie" in response.headers:
                    raise unavailable()
                self.partition.guard()
                if (
                    response.headers.get("content-type", "").split(";")[0] != "application/json"
                    or response.headers.get("cache-control") != "no-store"
                ):
                    raise unavailable()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 65536:
                        raise unavailable()
                    self.partition.guard()
                value = strict_loads(bytes(chunks))
                if response.status_code != 200:
                    codes = {
                        400: "INVALID_REQUEST",
                        403: "READ_AUTHENTICATION_DENIED",
                        404: "RESOURCE_UNAVAILABLE",
                        409: "READ_REVISION_CONFLICT",
                        503: "READ_DEPENDENCY_UNAVAILABLE",
                    }
                    if value != {
                        "schema": "portal-engine-read-error.v1",
                        "code": codes.get(response.status_code),
                    }:
                        raise unavailable()
                    code: ReadErrorCode = (
                        "resource_unavailable"
                        if response.status_code == 404
                        else "refresh_required"
                        if response.status_code == 409
                        else "read_dependency_unavailable"
                    )
                    raise ReadRefusalError(code)
            self.partition.guard()
            fields = {
                "schema",
                "request_digest",
                "scope",
                "engine_name",
                "database_incarnation",
                "read_deployment_ref",
                "read_deployment_digest",
                "operation",
                "source_observed_at",
                "valid_until",
                "value",
            }
            if (
                type(value) is not dict
                or value.keys() != fields
                or value["schema"] != "portal-engine-read-result.v1"
                or value["request_digest"] != request_digest
                or any(
                    value[k] != request[k]
                    for k in (
                        "scope",
                        "engine_name",
                        "database_incarnation",
                        "read_deployment_ref",
                        "read_deployment_digest",
                        "operation",
                    )
                )
            ):
                raise unavailable()
            observed, valid_until = instant(value["source_observed_at"]), instant(value["valid_until"])
            if (
                not now <= observed <= self.partition.guard() < valid_until
                or valid_until.timestamp() > expires
            ):
                raise unavailable()
            result = ReadResult(
                parse_model(VALUE_TYPES[operation], value["value"]), observed, valid_until, request_digest
            )
            return result
        except BaseException as exc:
            self.partition.close()
            self._closed = True
            if isinstance(exc, ReadRefusalError) or not isinstance(exc, Exception):
                raise
            raise unavailable() from None

    async def catalog(self, anchor: CatalogTrustAnchor) -> ReadResult:
        return await self._send("catalog", {"anchor": wire(anchor)})

    async def discover(
        self,
        principal: HumanPrincipal,
        expectation: CatalogExpectation,
        queue: str,
        limit: int,
        after_task_id: str | None,
    ) -> ReadResult:
        return await self._send(
            "discover",
            {
                "principal": wire(principal),
                "expectation": wire(expectation),
                "queue": queue,
                "limit": str(limit),
                "after_task_id": after_task_id,
            },
        )

    async def task(self, anchor: CatalogTrustAnchor, task_id: str) -> ReadResult:
        return await self._send("task", {"anchor": wire(anchor), "task_id": task_id})

    async def authority(
        self,
        anchor: CatalogTrustAnchor,
        principal: HumanPrincipal,
        task: AuthoritativeTask,
        continuity: NativeContinuity,
    ) -> ReadResult:
        return await self._send(
            "authority",
            {
                "anchor": wire(anchor),
                "principal": wire(principal),
                "task": wire(task),
                "task_continuity": wire(continuity),
            },
        )

    async def disclosure(
        self,
        anchor: CatalogTrustAnchor,
        principal: HumanPrincipal,
        task: AuthoritativeTask,
        continuity: NativeContinuity,
        authority: CurrentTaskAuthority,
        authority_continuity: NativeContinuity,
    ) -> ReadResult:
        return await self._send(
            "disclosure",
            {
                "anchor": wire(anchor),
                "principal": wire(principal),
                "task": wire(task),
                "task_continuity": wire(continuity),
                "authority": wire(authority),
                "authority_continuity": wire(authority_continuity),
            },
        )

    async def close(self) -> None:
        self._closed = True
        self.partition.close()
        await self._http.aclose()
        if self._owns_transport:
            await self._transport.aclose()
