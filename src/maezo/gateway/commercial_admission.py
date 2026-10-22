"""Authenticated M2M metadata composition before body intake. No default providers.

Headers are routing/authentication input, never scope or source authority. The
verifier must authenticate the bearer and bind the opaque connection to that
caller before a scope resolver can observe the connection.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

from maezo.adapters.amh.commercial import AmhCommercialAdapter, CommercialWire
from maezo.adapters.amh.commercial_http import AmhCommercialAuthority, HttpCommercialExecutor
from maezo.gateway.commercial import CommercialGateway
from maezo.ports.commercial import (
    CommercialCommand,
    CommercialRefusalError,
    CommercialScope,
    IssuerDocumentOrigins,
    SalesAdmission,
    SalesTransport,
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class VerifiedSalesConnection:
    connection_ref: str
    authenticated_caller_ref: str
    valid_until: datetime


class SalesCallerVerifier(ABC):
    @abstractmethod
    async def authenticate_and_resolve(self, credential: str, connection_ref: str) -> VerifiedSalesConnection:
        """Authenticate credential then verify current connection ownership/source binding."""
        ...


class SalesScopeAuthority(ABC):
    @abstractmethod
    async def acquire(self, connection_ref: str, authenticated_caller_ref: str) -> CommercialScope:
        """Resolve current verified identity/representation/lifecycle/source for caller."""
        ...

    @abstractmethod
    async def current(
        self,
        scope: CommercialScope,
        *,
        phase: Literal["before_body", "before_source", "before_effect", "before_disclosure"],
        command: CommercialCommand | None = None,
    ) -> CommercialScope: ...


class AuthenticatedSalesAdmission(SalesAdmission):
    def __init__(self, *, caller: SalesCallerVerifier, authority: SalesScopeAuthority) -> None:
        self._caller = caller
        self._authority = authority

    async def transport(self, authorization: str | None, connection_ref: str | None) -> SalesTransport:
        if (
            not isinstance(authorization, str)
            or not re.fullmatch(r"Bearer [A-Za-z0-9._~+/\-]{1,8192}={0,2}", authorization)
            or not isinstance(connection_ref, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", connection_ref)
        ):
            raise CommercialRefusalError("denied", "admission_required")
        verified = await self._caller.authenticate_and_resolve(authorization[7:], connection_ref)
        if (
            not isinstance(verified, VerifiedSalesConnection)
            or verified.connection_ref != connection_ref
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", verified.authenticated_caller_ref)
            or verified.valid_until.utcoffset() is None
            or verified.valid_until <= datetime.now(UTC)
        ):
            raise CommercialRefusalError("denied", "admission_required")
        return SalesTransport(
            connection_ref=verified.connection_ref,
            authenticated_caller_ref=verified.authenticated_caller_ref,
            method="POST",
            path="/api/v1/sales/commercial",
        )

    async def acquire(self, transport: SalesTransport) -> CommercialScope:
        if (
            not transport.connection_ref
            or not transport.authenticated_caller_ref
            or transport.method != "POST"
            or transport.path != "/api/v1/sales/commercial"
        ):
            raise CommercialRefusalError("denied", "admission_required")
        return await self._authority.acquire(transport.connection_ref, transport.authenticated_caller_ref)

    async def current(
        self,
        scope: CommercialScope,
        *,
        phase: Literal["before_body", "before_source", "before_effect", "before_disclosure"],
        command: CommercialCommand | None = None,
    ) -> CommercialScope:
        return await self._authority.current(scope, phase=phase, command=command)


def compose_commercial_gateway(
    *,
    schema_bytes: bytes,
    hosted_origins: dict[str, frozenset[str]] | None = None,
    issuer_document_origins: IssuerDocumentOrigins | None = None,
    amh_origin: str | None = None,
    caller: SalesCallerVerifier | None = None,
    scope_authority: SalesScopeAuthority | None = None,
    source_authority: AmhCommercialAuthority | None = None,
    timeout_seconds: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CommercialGateway:
    """Server deployment factory, closed unless all verified authority ports exist.

    Schema bytes are exact source-owned candidate bytes; a hash never establishes
    live publication. No process-global mutation or environment activation exists.
    """
    wire = CommercialWire(
        schema_bytes, hosted_origins=hosted_origins, issuer_document_origins=issuer_document_origins
    )
    if caller is None or scope_authority is None or source_authority is None or amh_origin is None:
        return CommercialGateway(codec=wire, timeout_seconds=timeout_seconds)
    executor = HttpCommercialExecutor(
        origin=amh_origin, authority=source_authority, timeout_seconds=timeout_seconds, transport=transport
    )
    return CommercialGateway(
        codec=wire,
        admission=AuthenticatedSalesAdmission(caller=caller, authority=scope_authority),
        source=AmhCommercialAdapter(wire=wire, executor=executor),
        timeout_seconds=timeout_seconds,
    )
