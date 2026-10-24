"""Authenticated M2M metadata composition before body intake. No default providers.

Headers are routing/authentication input, never scope or source authority. The
verifier must authenticate the bearer and bind the opaque connection to that
caller before a scope resolver can observe the connection.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

from maezo.adapters.amh.commercial import AmhCommercialAdapter, CommercialWire
from maezo.adapters.amh.commercial_http import (
    AmhCommercialAuthority,
    AmhPublicCatalogAuthority,
    HttpCommercialExecutor,
    HttpPublicCatalogSource,
)
from maezo.gateway.commercial import CommercialGateway, PublicCatalogGateway
from maezo.gateway.commercial_acceptance_admission import A3_ADMITTED_COMMANDS, AcceptanceRecordAdmission
from maezo.gateway.commercial_catalog_admission import CatalogSourceAdmission
from maezo.gateway.commercial_coverage_admission import CoverageReadAdmission
from maezo.gateway.commercial_sales_admission import SalesSessionAdmission
from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialDeliveryOrigins,
    CommercialRefusalError,
    CommercialScope,
    CommercialStatus,
    IssuerDocumentOrigins,
    OperationalResult,
    PublicCatalogAdmission,
    PublicCatalogScope,
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


class AcceptanceUnadmittedSalesAdmission(AuthenticatedSalesAdmission):
    """A2-bound session whose acceptance/enrollment effect dimension is not admitted.

    The A3 record (`commercial_acceptance_admission`) is the only registered key the
    `acceptance.record` effect binds through; while it is absent this admission refuses
    that command with the composition's shipped fail-closed refusal — before any source
    step, so no OP03/OP04 effect can execute. It subclasses the A2 session admission
    because the route's pre-body session admission must keep its exact type; every other
    command and phase delegates unchanged, so an A2-bound gateway without A3 behaves
    identically to the shipped state outside the acceptance surface.
    """

    async def current(
        self,
        scope: CommercialScope,
        *,
        phase: Literal["before_body", "before_source", "before_effect", "before_disclosure"],
        command: CommercialCommand | None = None,
    ) -> CommercialScope:
        if command is not None and command.command in A3_ADMITTED_COMMANDS:
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        return await super().current(scope, phase=phase, command=command)


class CoverageGatedCodec(CommercialCodec):
    """Sales codec whose coverage read dimension stays admission-gated.

    The frozen contract carries the nominal coverage projection inside the journey
    snapshot (`CustomerJourneySnapshot.coverage`, shape `CustomerCoverageRead`) — a READ,
    never a command — and the SAME arm inside the recovery envelope
    (`RecoveredCustomerJourneySnapshot`, the `operation.get` original_result for
    `original_command == "journey.get"`). Without the registered A4 record
    (`commercial_coverage_admission`) a source-asserted coverage fact (`coverageState`
    `active`/`revoked`) does not cross the disclosure boundary on EITHER carrier: this
    gate refuses it with the shipped `purpose_denied` refusal before any byte of it is
    disclosed, and the gateway's post-dispatch custody discipline answers
    `uncertain/reconciliation_required` — the read executed at the source, the unadmitted
    fact is never disclosed, and nothing is re-dispatched. The snapshot's other
    dimensions (acceptance/contract/enrollment/documents/support) and every `unknown`
    coverage rendering stay exactly as shipped. The gate only ever keys on the source's
    own coverage arm: issued/accepted/paid facts in the same snapshot never upgrade it,
    and no coverage fact is synthesized, upgraded or written — the structural
    `issued ≠ coverage_active` custody (contract :454, dossier A4). With the registered
    A4 record the gate is inert and the source-owned fact passes unchanged.
    """

    def __init__(self, inner: CommercialCodec, *, gated: bool) -> None:
        self._inner = inner
        self._gated = gated

    @property
    def schema_digest(self) -> str:
        return self._inner.schema_digest

    def parse_command(self, body: bytes) -> CommercialCommand:
        return self._inner.parse_command(body)

    def parse_public_result(self, body: bytes) -> bytes:
        return self._inner.parse_public_result(body)

    def refusal(
        self, status: CommercialStatus, reason_code: str, operation_ref: str | None = None
    ) -> OperationalResult:
        return self._inner.refusal(status, reason_code, operation_ref)

    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult:
        result = self._inner.parse_result(body, command)
        if not self._gated or result.status != "succeeded":
            return result
        value = json.loads(result.wire_bytes)
        if command.command == "journey.get":
            journeys = value["data"]["journeys"]
        elif (
            command.command == "operation.get"
            and value["data"].get("original_command") == "journey.get"
            and value["data"].get("original_result") is not None
        ):
            journeys = value["data"]["original_result"]["data"]["journeys"]
        else:
            return result
        if any(journey["coverage"]["coverageState"] != "unknown" for journey in journeys):
            raise CommercialRefusalError("denied", "purpose_denied")
        return result


def compose_commercial_gateway(
    *,
    schema_bytes: bytes,
    hosted_origins: dict[str, frozenset[str]] | None = None,
    issuer_document_origins: IssuerDocumentOrigins | None = None,
    delivery_origins: CommercialDeliveryOrigins | None = None,
    amh_origin: str | None = None,
    caller: SalesCallerVerifier | None = None,
    scope_authority: SalesScopeAuthority | None = None,
    source_authority: AmhCommercialAuthority | None = None,
    sales_admission: SalesSessionAdmission | None = None,
    acceptance_admission: AcceptanceRecordAdmission | None = None,
    coverage_admission: CoverageReadAdmission | None = None,
    timeout_seconds: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CommercialGateway:
    """Actual fixed POST caller composition; missing authority or admission keeps it unbound.

    Authority ports alone never bind the Sales session: binding additionally requires
    the registered A2 admission (`commercial_sales_admission`), sha-pinned to the frozen
    contract these schema bytes must be. The acceptance/enrollment effect dimension is
    its own admission: without the registered A3 record (`commercial_acceptance_admission`,
    equally sha-pinned) a bound session still refuses `acceptance.record` with the shipped
    fail-closed refusal before any source step — the A2 record never opens an effect.
    The coverage read dimension is likewise its own admission: without the registered A4
    record (`commercial_coverage_admission`, equally sha-pinned) the codec keeps the
    journey snapshot's coverage arm gated, so a source-asserted coverage fact is never
    disclosed — `unknown` renderings and every other dimension are unchanged. Schema
    bytes are exact source-owned candidate bytes; a hash never establishes live
    publication. No process-global mutation or environment activation exists.
    """
    wire = CommercialWire(
        schema_bytes,
        hosted_origins=hosted_origins,
        issuer_document_origins=issuer_document_origins,
        delivery_origins=delivery_origins,
    )
    codec: CommercialCodec = CoverageGatedCodec(wire, gated=coverage_admission is None)
    if (
        caller is None
        or scope_authority is None
        or source_authority is None
        or amh_origin is None
        or sales_admission is None
    ):
        return CommercialGateway(codec=codec, timeout_seconds=timeout_seconds)
    if sales_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_sales_admission_invalid")
    if acceptance_admission is not None and acceptance_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_acceptance_admission_invalid")
    if coverage_admission is not None and coverage_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_coverage_admission_invalid")
    executor = HttpCommercialExecutor(
        origin=amh_origin, authority=source_authority, timeout_seconds=timeout_seconds, transport=transport
    )
    admission: AuthenticatedSalesAdmission = AuthenticatedSalesAdmission(
        caller=caller, authority=scope_authority
    )
    if acceptance_admission is None:
        admission = AcceptanceUnadmittedSalesAdmission(caller=caller, authority=scope_authority)
    return CommercialGateway(
        codec=codec,
        admission=admission,
        source=AmhCommercialAdapter(wire=wire, executor=executor),
        timeout_seconds=timeout_seconds,
    )


class PublicCatalogScopeAuthority(ABC):
    """Separate machine grant; never an account/customer SalesScopeAuthority."""

    @abstractmethod
    async def acquire(self, connection_ref: str, authenticated_caller_ref: str) -> PublicCatalogScope: ...

    @abstractmethod
    async def current(self, scope: PublicCatalogScope, *, phase: str) -> PublicCatalogScope: ...


class AuthenticatedPublicCatalogAdmission(PublicCatalogAdmission):
    def __init__(self, *, caller: SalesCallerVerifier, authority: PublicCatalogScopeAuthority) -> None:
        self._caller, self._authority = caller, authority

    async def transport(self, authorization: str | None, connection_ref: str | None) -> SalesTransport:
        if (
            not isinstance(authorization, str)
            or not re.fullmatch(r"Bearer [A-Za-z0-9._~+/\-]{1,8192}={0,2}", authorization)
            or not isinstance(connection_ref, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", connection_ref)
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")
        verified = await self._caller.authenticate_and_resolve(authorization[7:], connection_ref)
        if (
            not isinstance(verified, VerifiedSalesConnection)
            or verified.connection_ref != connection_ref
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", verified.authenticated_caller_ref)
            or verified.valid_until.utcoffset() is None
            or verified.valid_until <= datetime.now(UTC)
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")
        return SalesTransport(
            connection_ref=verified.connection_ref,
            authenticated_caller_ref=verified.authenticated_caller_ref,
            method="GET",
            path="/api/v1/sales/public/catalog",
        )

    async def acquire(self, transport: SalesTransport) -> PublicCatalogScope:
        if (
            not transport.connection_ref
            or not transport.authenticated_caller_ref
            or transport.method != "GET"
            or transport.path != "/api/v1/sales/public/catalog"
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")
        return await self._authority.acquire(transport.connection_ref, transport.authenticated_caller_ref)

    async def current(self, scope: PublicCatalogScope, *, phase: str) -> PublicCatalogScope:
        return await self._authority.current(scope, phase=phase)


def compose_public_catalog_gateway(
    *,
    schema_bytes: bytes,
    amh_origin: str | None = None,
    caller: SalesCallerVerifier | None = None,
    scope_authority: PublicCatalogScopeAuthority | None = None,
    source_authority: AmhPublicCatalogAuthority | None = None,
    source_admission: CatalogSourceAdmission | None = None,
    timeout_seconds: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PublicCatalogGateway:
    """Actual fixed GET caller composition; missing authority or admission keeps it unbound.

    Authority ports alone never bind the public source: binding additionally requires
    the registered A1 admission (`commercial_catalog_admission`), sha-pinned to the frozen
    contract these schema bytes must be. No process-global mutation or environment
    activation exists.
    """
    wire = CommercialWire(schema_bytes)
    if (
        caller is None
        or scope_authority is None
        or source_authority is None
        or amh_origin is None
        or source_admission is None
    ):
        return PublicCatalogGateway(codec=wire, timeout_seconds=timeout_seconds)
    if source_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_catalog_admission_invalid")
    return PublicCatalogGateway(
        codec=wire,
        admission=AuthenticatedPublicCatalogAdmission(caller=caller, authority=scope_authority),
        source=HttpPublicCatalogSource(
            origin=amh_origin,
            authority=source_authority,
            timeout_seconds=timeout_seconds,
            transport=transport,
        ),
        timeout_seconds=timeout_seconds,
    )
