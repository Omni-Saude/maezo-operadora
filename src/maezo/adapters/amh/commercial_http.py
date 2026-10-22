"""Concrete own-endpoint transport; external publication/authority remain unbound.

REQ-C2-02, ADR-0037 XRD-03/04/05/09. No Tasy wire is invented here.
"""

from __future__ import annotations

import asyncio
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from maezo.adapters.amh.commercial import (
    AMH_COMMAND_PATH,
    MAX_RESULT_BYTES,
    GovernedCommercialExecutor,
    _json,
)
from maezo.ports.commercial import (
    CommercialCommand,
    CommercialPreparation,
    CommercialPublication,
    CommercialRefusalError,
    CommercialResultAuthority,
    CommercialScope,
    OperationalResult,
    PublicCatalogProof,
    PublicCatalogScope,
    PublicCatalogSource,
    command_digest,
    public_scope_digest,
    scope_digest,
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AmhConnection:
    """Authority resolves this server-side against current authenticated caller/source."""

    connection_ref: str
    operation_ref: str
    command_digest: str
    scope_digest: str
    valid_until: datetime


class AmhCommercialAuthority(ABC):
    """Actual verified authority ports; default deployment installs none.

    Publication and preparation must resolve source evidence independently, never
    construct it from requested refs. Connection must be bound to the same current
    caller, source scope, command and original operation on the AMH receiver.
    """

    @abstractmethod
    async def publication(self, scope: CommercialScope) -> CommercialPublication: ...

    @abstractmethod
    async def prepare(self, command: CommercialCommand, scope: CommercialScope) -> CommercialPreparation: ...

    @abstractmethod
    async def connection(
        self, command: CommercialCommand, scope: CommercialScope, preparation: CommercialPreparation
    ) -> AmhConnection: ...

    @abstractmethod
    async def current(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
        preparation: CommercialPreparation,
        connection: AmhConnection,
        *,
        phase: str,
    ) -> None: ...

    @abstractmethod
    async def credential(self, connection: AmhConnection) -> str: ...

    async def result_authority(
        self, command: CommercialCommand, scope: CommercialScope, result: OperationalResult
    ) -> CommercialResultAuthority:
        raise CommercialRefusalError("unavailable", "source_unavailable")


class HttpCommercialExecutor(GovernedCommercialExecutor):
    """One authenticated POST, bounded response/time; never retries ambiguous effects."""

    def __init__(
        self,
        *,
        origin: str,
        authority: AmhCommercialAuthority,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
            or not isinstance(authority, AmhCommercialAuthority)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("commercial_composition_invalid")
        self._url = origin.rstrip("/") + AMH_COMMAND_PATH
        self._authority = authority
        self._timeout = timeout_seconds
        self._transport = transport

    async def publication(self, scope: CommercialScope) -> CommercialPublication:
        return await self._authority.publication(scope)

    async def prepare(self, command: CommercialCommand, scope: CommercialScope) -> CommercialPreparation:
        return await self._authority.prepare(command, scope)

    async def execute(
        self, command: CommercialCommand, scope: CommercialScope, preparation: CommercialPreparation
    ) -> bytes:
        async with asyncio.timeout(self._timeout):
            connection = await self._authority.connection(command, scope, preparation)
            if (
                not isinstance(connection, AmhConnection)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", connection.connection_ref)
                or connection.operation_ref != preparation.operation_ref
                or connection.command_digest != command_digest(command)
                or connection.scope_digest != scope_digest(scope)
                or connection.valid_until.utcoffset() is None
                or connection.valid_until <= datetime.now(UTC)
            ):
                raise CommercialRefusalError("denied", "source_stale")
            credential = await self._authority.credential(connection)
            if not isinstance(credential, str) or not re.fullmatch(
                r"[A-Za-z0-9._~+/\-]{1,8192}={0,2}", credential
            ):
                raise CommercialRefusalError("unavailable", "binding_unqualified")
            await self._authority.current(command, scope, preparation, connection, phase="before_io")
            deadlines = [connection.valid_until, preparation.valid_until, scope.valid_until]
            if preparation.payment_choice is not None:
                deadlines.append(preparation.payment_choice.valid_until)
            if any(value.utcoffset() is None or value <= datetime.now(UTC) for value in deadlines):
                raise CommercialRefusalError("denied", "source_stale")
            # Own client has no browser cookie jar, env proxies, redirects or retry transport.
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False, trust_env=False, transport=self._transport
                ) as client,
                client.stream(
                    "POST",
                    self._url,
                    headers={
                        "Authorization": f"Bearer {credential}",
                        "X-Commercial-Connection": connection.connection_ref,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    content=command.wire_bytes,
                ) as response,
            ):
                if (
                    response.status_code not in {200, 202, 400, 403, 409, 503}
                    or response.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                ):
                    raise CommercialRefusalError("uncertain", "reconciliation_required")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESULT_BYTES:
                        raise CommercialRefusalError("uncertain", "reconciliation_required")
                    chunks.append(chunk)
                body = b"".join(chunks)
                value = _json(body, MAX_RESULT_BYTES)
                expected = {
                    "succeeded": 200,
                    "denied": 400 if response.status_code == 400 else 403,
                    "conflict": 409,
                    "uncertain": 202,
                    "unavailable": 503,
                }
                status = value.get("status")
                if (
                    not isinstance(status, str)
                    or expected.get(status) != response.status_code
                    or value.get("operation_ref") != preparation.operation_ref
                ):
                    raise CommercialRefusalError("uncertain", "reconciliation_required")
            await self._authority.current(command, scope, preparation, connection, phase="before_disclosure")
            return body

    async def result_authority(
        self, command: CommercialCommand, scope: CommercialScope, result: OperationalResult
    ) -> CommercialResultAuthority:
        return await self._authority.result_authority(command, scope, result)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class PublicAmhConnection:
    connection_ref: str
    scope_digest: str
    valid_until: datetime


class AmhPublicCatalogAuthority(ABC):
    """Current machine publication and exact public field-policy qualification."""

    @abstractmethod
    async def authorize(self, scope: PublicCatalogScope, result_digest: str | None) -> PublicCatalogProof: ...

    @abstractmethod
    async def connection(self, scope: PublicCatalogScope) -> PublicAmhConnection: ...

    @abstractmethod
    async def credential(self, connection: PublicAmhConnection) -> str: ...

    @abstractmethod
    async def current(
        self, scope: PublicCatalogScope, connection: PublicAmhConnection, *, phase: str
    ) -> None: ...


class HttpPublicCatalogSource(PublicCatalogSource):
    """Fixed AMH GET; machine metadata only, no browser input or retry."""

    def __init__(
        self,
        *,
        origin: str,
        authority: AmhPublicCatalogAuthority,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
            or not isinstance(authority, AmhPublicCatalogAuthority)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("commercial_composition_invalid")
        self._url = origin.rstrip("/") + "/interop/sales/v1/public/catalog"
        self._authority, self._timeout, self._transport = authority, timeout_seconds, transport

    async def authorize(self, scope: PublicCatalogScope, result_digest: str | None) -> PublicCatalogProof:
        return await self._authority.authorize(scope, result_digest)

    @staticmethod
    def _connection(scope: PublicCatalogScope, connection: PublicAmhConnection) -> None:
        if (
            not isinstance(connection, PublicAmhConnection)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", connection.connection_ref)
            or connection.scope_digest != public_scope_digest(scope)
            or connection.valid_until.utcoffset() is None
            or connection.valid_until <= datetime.now(UTC)
            or scope.valid_until.utcoffset() is None
            or scope.valid_until <= datetime.now(UTC)
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")

    async def read(self, scope: PublicCatalogScope) -> bytes:
        async with asyncio.timeout(self._timeout):
            connection = await self._authority.connection(scope)
            self._connection(scope, connection)
            credential = await self._authority.credential(connection)
            if not isinstance(credential, str) or not re.fullmatch(
                r"[A-Za-z0-9._~+/\-]{1,8192}={0,2}", credential
            ):
                raise CommercialRefusalError("unavailable", "source_unavailable")
            await self._authority.current(scope, connection, phase="before_io")
            self._connection(scope, connection)
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False, trust_env=False, transport=self._transport
                ) as client,
                client.stream(
                    "GET",
                    self._url,
                    headers={
                        "Authorization": f"Bearer {credential}",
                        "X-Commercial-Connection": connection.connection_ref,
                        "Accept": "application/json",
                    },
                ) as response,
            ):
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                ):
                    raise CommercialRefusalError("unavailable", "source_unavailable")
                size, chunks = 0, []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESULT_BYTES:
                        raise CommercialRefusalError("unavailable", "source_unavailable")
                    chunks.append(chunk)
                body = b"".join(chunks)
            await self._authority.current(scope, connection, phase="before_disclosure")
            self._connection(scope, connection)
            return body
