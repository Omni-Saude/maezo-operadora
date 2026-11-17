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
from typing import Any, Final, Literal

import httpx

from maezo.adapters.amh.commercial import MAX_BODY_BYTES, AmhCommercialAdapter, CommercialWire
from maezo.adapters.amh.commercial_fences import (
    CommercialFenceError,
    CommercialFenceRefusal,
    sla_claim_marker,
)
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
from maezo.gateway.commercial_portability_admission import (
    PortabilityPayloadError,
    refuse_portability_verdict_payload,
)
from maezo.gateway.commercial_sales_admission import SalesSessionAdmission
from maezo.gateway.commercial_support_admission import (
    A5_ADMITTED_COMMANDS,
    SupportHandoffAdmission,
)
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

# The A1 catalog dimension of the Sales POST path (EP-CORE-00a; DEC-CORE CORE-00 §(4)(b):
# "uma sessão A2 sem registro A1 recusa `journey.get` com `plan_choices` não vazio e recusa
# `offer.quote`"). Two legs, both closed while the registered A1 record
# (`commercial_catalog_admission`) is absent (dossiê A1: "antes de qualquer consumo REAL"):
# - command leg: the commands derived from the source catalog — the C6-4
#   `OfferQuotePayload` binds `catalog_ref`/`catalog_version`/`product_ref` ("existing
#   offer.quote uses the exact source catalog/version/product/profile",
#   `x-journey-formal-entry.selection`), pinned equal to the contract-derived set by test —
#   refuse before any source step (`CatalogUnadmittedSalesAdmission` or `also_refused`);
# - disclosure leg: source-qualified `plan_choices` (catalog/version/product selectors) in
#   a `journey.get` result never cross the disclosure boundary (`CatalogGatedCodec`).
A1_CATALOG_DERIVED_COMMANDS: Final = frozenset({"offer.quote"})


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
    identically to the shipped state outside the acceptance surface. The composition may
    inject `also_refused` — the A1 catalog-derived commands
    (`A1_CATALOG_DERIVED_COMMANDS`) when the A1 record is absent — so one gate instance
    refuses the exact union of the absent records' command sets.
    """

    refused_commands: frozenset[str] = A3_ADMITTED_COMMANDS

    def __init__(
        self,
        *,
        caller: SalesCallerVerifier,
        authority: SalesScopeAuthority,
        also_refused: frozenset[str] = frozenset(),
    ) -> None:
        # `also_refused` carries the other closed dimensions (the A1 catalog-derived
        # commands) so one gate instance refuses the exact union of absent records.
        super().__init__(caller=caller, authority=authority)
        self.refused_commands = type(self).refused_commands | also_refused

    async def current(
        self,
        scope: CommercialScope,
        *,
        phase: Literal["before_body", "before_source", "before_effect", "before_disclosure"],
        command: CommercialCommand | None = None,
    ) -> CommercialScope:
        if command is not None and command.command in self.refused_commands:
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        return await super().current(scope, phase=phase, command=command)


class SupportUnadmittedSalesAdmission(AcceptanceUnadmittedSalesAdmission):
    """A2-bound session whose support/work dimension is not admitted.

    The A5 record (`commercial_support_admission`) is the only registered key the
    support/work command dimension (`support.handoff` plus the `work.*` queue family,
    the x-workflow `sales_operations` commands) and the disclosure of a source-asserted
    case projection bind through; while it is absent this admission refuses those
    commands with the shipped fail-closed refusal before any source step. Only the
    refused set differs from the acceptance gate — the acceptance dimension delegates
    unchanged, so an A3-admitted session without A5 keeps exactly its A3 behavior outside
    the support/work surface.
    """

    refused_commands = A5_ADMITTED_COMMANDS


class AcceptanceSupportUnadmittedSalesAdmission(SupportUnadmittedSalesAdmission):
    """A2-bound session with both non-admitted effect dimensions (A3 and A5) closed.

    Neither record is present, so the acceptance surface and the support/work surface
    both refuse the shipped fail-closed refusal before any source step — the union, not
    a choice.
    """

    refused_commands = A3_ADMITTED_COMMANDS | A5_ADMITTED_COMMANDS


class CatalogUnadmittedSalesAdmission(AcceptanceUnadmittedSalesAdmission):
    """A2-bound session whose catalog source (A1) is not admitted (EP-CORE-00a).

    The A1 record (`commercial_catalog_admission`) is the only registered key the
    catalog source binds through; while it is absent the catalog-derived commands
    (`A1_CATALOG_DERIVED_COMMANDS`) refuse the shipped fail-closed refusal before any
    source step, exactly like the acceptance gate. Installed when A3 and A5 are both
    admitted; otherwise the A1 set rides on the A3/A5 gate as `also_refused`. This is the
    command leg only: the disclosure leg (non-empty `plan_choices` in a `journey.get`
    result) is `CatalogGatedCodec`, installed by the composition under the same condition.
    """

    refused_commands = A1_CATALOG_DERIVED_COMMANDS


class CoverageGatedCodec(CommercialCodec):
    """Sales codec whose coverage read dimension stays admission-gated.

    The frozen contract carries the nominal coverage projection inside the journey
    snapshot (`CustomerJourneySnapshot.coverage`, shape `CustomerCoverageRead`) — a READ,
    never a command — and the SAME arm inside the recovery envelope
    (`RecoveredCustomerJourneySnapshot`, the `operation.get` original_result for
    `original_command == "journey.get"`, keyed only when that original result
    `succeeded`: a recovery whose original result did not succeed carries `data: null` —
    no coverage arm exists in it — and passes byte-exact, never an error or a refusal).
    Without the registered A4 record
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
            and isinstance(value["data"].get("original_result"), dict)
            and value["data"]["original_result"].get("status") == "succeeded"
        ):
            journeys = value["data"]["original_result"]["data"]["journeys"]
        else:
            return result
        if any(journey["coverage"]["coverageState"] != "unknown" for journey in journeys):
            raise CommercialRefusalError("denied", "purpose_denied")
        return result


class CatalogGatedCodec(CommercialCodec):
    """Sales codec whose catalog-choice disclosure stays A1-gated (EP-CORE-00a).

    A fresh `journey.get` result carries source-qualified `plan_choices`
    (`JourneyPlanChoice`: `catalog_ref`/`catalog_version`/`product_ref`) — catalog facts
    the A2 session record does not bind. Without the registered A1 record
    (`commercial_catalog_admission`) a non-empty `plan_choices` does not cross the
    disclosure boundary: this gate refuses it with the shipped
    `unavailable/binding_unqualified` before any byte is disclosed. The check runs on the
    parsed source result, i.e. after dispatch, so the gateway's post-dispatch custody
    discipline turns it into the effective HTTP answer `202 uncertain/
    reconciliation_required` with `data: null` — the read ran at the source, nothing of it
    is disclosed, nothing is re-dispatched. The recovery carrier (`operation.get`,
    `original_command == "journey.get"`) is closed by the C6-4 codec itself, which strips
    recovery to `[]` ("Before any persistence/recovery, plan_choices is []"). An
    empty `plan_choices` and every other dimension pass byte-exact; with the A1 record the
    gate is inert.
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
        if command.command != "journey.get":
            return result
        if value["data"].get("plan_choices"):
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        return result


@dataclass(frozen=True, slots=True)
class _SupportCarrier:
    """One disclosure carrier of support case arms plus the refs its custody is checked against."""

    arms: tuple[dict[str, Any], ...]
    labels: tuple[str, ...]
    # `support.handoff` carriers carry the source receipt; journey snapshots carry none.
    receipt_bound: bool
    receipt_ref: str | None
    # Refs a protocol must never be derived from: the caller's command refs (the current
    # one, the recovered target and the snapshot's pending operations) …
    command_refs: frozenset[str]
    # … and the OP03 acceptance receipts of the same journey (support is never acceptance).
    acceptance_refs: frozenset[str]


def _refuse_support_handoff_verdict_fields(body: bytes) -> None:
    """EP-CORE-05b: a `support.handoff` payload never carries a portability-of-waiting-period verdict.

    The structural guard of A7 (`refuse_portability_verdict_payload`, admission-independent)
    runs on the support payload before the codec, so the typed `PortabilityPayloadError`
    is the refusal's cause; the wire answer stays the codec's own `denied/invalid_request`.
    Bodies that are not a JSON object naming `support.handoff` are left to the codec.
    """
    if type(body) is not bytes or len(body) > MAX_BODY_BYTES:
        return
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return
    if not isinstance(value, dict) or value.get("command") != "support.handoff":
        return
    try:
        refuse_portability_verdict_payload(value.get("payload"))
    except PortabilityPayloadError as exc:
        raise CommercialRefusalError("denied", "invalid_request") from exc


class SupportGatedCodec(CommercialCodec):
    """Sales codec whose support case-projection dimension stays admission-gated.

    The frozen contract carries a source-asserted support case projection
    (`CustomerSupportRead` `status: "projection"` — `caseKind`/`caseStatus`/`caseRef`/
    `vocabularyRef`/`protocolRef`) on four disclosure carriers: the `support.handoff`
    result's `case_read`, the journey snapshot's `support` arm (`journey.get`), the SAME
    arm inside the recovery envelope (`operation.get` with `original_command ==
    "journey.get"`), and the `case_read` arm of a recovered `support.handoff` original
    result (both recovery carriers keyed only when the recovered original result
    `succeeded`: a failed original carries `data: null` — no case arm exists in it — and
    passes byte-exact, never an error or a refusal). The published case vocabulary those
    fields name does not exist (CR-F5 `BROKER_CONSUMER_ABSENT_FROM_AMH_ADAPTER_PACKAGE`; the
    dossier's `DeclaredCaseKind`/`DeclaredCaseStatus` members are empty until publication),
    and the contract only allows a case resolution to be displayed "under its current
    published semantics and receipt" (`x-customer-journey.support_readback`). Without the
    registered A5 record (`commercial_support_admission`) a projection arm does not cross
    the disclosure boundary on ANY carrier: this gate refuses it with the shipped
    `denied/purpose_denied` refusal before one byte of it is disclosed, and the gateway's
    post-dispatch custody discipline answers `uncertain/reconciliation_required` — the
    read executed at the source, the unadmitted fact is never disclosed, and nothing is
    re-dispatched. The `refused` renderings (bounded outcome enum), the journey's
    `support_topics` list and every other dimension stay exactly as shipped — the gate
    never synthesizes, upgrades or rewrites a case fact. With the registered A5 record the
    admission gate is inert and the source-owned projection passes unchanged.

    Three custody checks hold with or without the A5 record (E-SF-06), each refused with
    the same `denied/purpose_denied` before disclosure:
    - protocol only with the OP07 receipt (EP-CORE-04a; DEC-CORE CORE-04 item 2: "nunca
      minta protocolo"): on a `support.handoff` carrier a non-null `protocolRef` without
      `source_receipt_ref` is refused, and on every carrier a `protocolRef` equal to a
      caller command ref (current, recovered target or pending operation) is refused;
    - resolution never via OP03 (`support_readback`: "support is never acceptance"): a
      projection whose `protocolRef`/`caseRef` is an acceptance receipt of the same journey
      is refused;
    - no prazo before Q1/DEC-18 (EP-REG-01a): a case label or support topic label stating a
      numeric deadline is refused (`unratified_sla_claim`, `commercial_fences`).
    """

    def __init__(self, inner: CommercialCodec, *, gated: bool) -> None:
        self._inner = inner
        self._gated = gated

    @property
    def schema_digest(self) -> str:
        return self._inner.schema_digest

    def parse_command(self, body: bytes) -> CommercialCommand:
        _refuse_support_handoff_verdict_fields(body)
        return self._inner.parse_command(body)

    def parse_public_result(self, body: bytes) -> bytes:
        return self._inner.parse_public_result(body)

    def refusal(
        self, status: CommercialStatus, reason_code: str, operation_ref: str | None = None
    ) -> OperationalResult:
        return self._inner.refusal(status, reason_code, operation_ref)

    @staticmethod
    def _journey_carriers(data: dict[str, Any], command_refs: frozenset[str]) -> list[_SupportCarrier]:
        carriers = []
        for journey in data["journeys"]:
            pending = {
                ref
                for operation in journey["pending_operations"]
                for ref in (operation["command_ref"], operation["operation_ref"])
            }
            acceptance = journey["acceptance"]
            commercial = journey["commercial_acceptance"]
            acceptance_refs = {
                ref
                for ref in (
                    acceptance.get("acceptanceRef"),
                    commercial["acceptance_receipt_ref"] if commercial else None,
                )
                if ref is not None
            }
            arm = journey["support"]
            carriers.append(
                _SupportCarrier(
                    arms=(arm,),
                    labels=(
                        *(topic["label"] for topic in journey["support_topics"]),
                        *(arm[key] for key in ("caseKindLabel", "caseStatusLabel") if key in arm),
                    ),
                    receipt_bound=False,
                    receipt_ref=None,
                    command_refs=command_refs | pending,
                    acceptance_refs=frozenset(acceptance_refs),
                )
            )
        return carriers

    @staticmethod
    def _handoff_carrier(data: dict[str, Any], command_refs: frozenset[str]) -> _SupportCarrier:
        arm = data["case_read"]
        return _SupportCarrier(
            arms=(arm,),
            labels=tuple(arm[key] for key in ("caseKindLabel", "caseStatusLabel") if key in arm),
            receipt_bound=True,
            receipt_ref=data["source_receipt_ref"],
            command_refs=command_refs,
            acceptance_refs=frozenset(),
        )

    def _carriers(self, value: dict[str, Any], command: CommercialCommand) -> list[_SupportCarrier]:
        refs = frozenset({command.command_ref})
        if command.command == "journey.get":
            return self._journey_carriers(value["data"], refs)
        if command.command == "support.handoff":
            return [self._handoff_carrier(value["data"], refs)]
        if (
            command.command == "operation.get"
            and isinstance(value["data"].get("original_result"), dict)
            and value["data"]["original_result"].get("status") == "succeeded"
        ):
            original = value["data"]["original_result"]["data"]
            target = value["data"].get("target_command_ref")
            refs = refs | ({target} if isinstance(target, str) else set())
            original_command = value["data"].get("original_command")
            if original_command == "journey.get":
                return self._journey_carriers(original, refs)
            if original_command == "support.handoff":
                return [self._handoff_carrier(original, refs)]
        return []

    @staticmethod
    def _custody_violated(carrier: _SupportCarrier) -> bool:
        for arm in carrier.arms:
            if arm["status"] != "projection":
                continue
            protocol = arm["protocolRef"]
            if protocol is not None and (
                (carrier.receipt_bound and carrier.receipt_ref is None) or protocol in carrier.command_refs
            ):
                return True
            if {protocol, arm["caseRef"]} & carrier.acceptance_refs:
                return True
        return False

    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult:
        result = self._inner.parse_result(body, command)
        if result.status != "succeeded":
            return result
        carriers = self._carriers(json.loads(result.wire_bytes), command)
        if self._gated and any(arm["status"] == "projection" for carrier in carriers for arm in carrier.arms):
            raise CommercialRefusalError("denied", "purpose_denied")
        if any(self._custody_violated(carrier) for carrier in carriers):
            raise CommercialRefusalError("denied", "purpose_denied")
        if any(sla_claim_marker(label) for carrier in carriers for label in carrier.labels):
            raise CommercialRefusalError("denied", "purpose_denied") from CommercialFenceError(
                CommercialFenceRefusal.UNRATIFIED_SLA_CLAIM
            )
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
    catalog_admission: CatalogSourceAdmission | None = None,
    acceptance_admission: AcceptanceRecordAdmission | None = None,
    coverage_admission: CoverageReadAdmission | None = None,
    support_admission: SupportHandoffAdmission | None = None,
    timeout_seconds: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CommercialGateway:
    """Actual fixed POST caller composition; missing authority or admission keeps it unbound.

    Authority ports alone never bind the Sales session: binding additionally requires
    the registered A2 admission (`commercial_sales_admission`), sha-pinned to the frozen
    contract these schema bytes must be. The catalog-derived commands
    (`A1_CATALOG_DERIVED_COMMANDS`) are their own admission: without the registered A1
    record (`commercial_catalog_admission`, equally sha-pinned) a bound session refuses
    them with the shipped fail-closed refusal before any source step, and the codec keeps a
    non-empty `plan_choices` of a `journey.get` result from being disclosed
    (`CatalogGatedCodec`, effective answer `uncertain` after dispatch) — the A2 record
    never stands in for the catalog source. The acceptance/enrollment effect dimension is
    its own admission: without the registered A3 record (`commercial_acceptance_admission`,
    equally sha-pinned) a bound session still refuses `acceptance.record` with the shipped
    fail-closed refusal before any source step — the A2 record never opens an effect.
    The coverage read dimension is likewise its own admission: without the registered A4
    record (`commercial_coverage_admission`, equally sha-pinned) the codec keeps the
    journey snapshot's coverage arm gated, so a source-asserted coverage fact is never
    disclosed — `unknown` renderings and every other dimension are unchanged. The
    support/work dimension (`support.handoff` + `work.*`, and the disclosure of a
    source-asserted case projection on any of its carriers) is its own admission too:
    without the registered A5 record (`commercial_support_admission`, equally sha-pinned)
    a bound session refuses those commands before any source step and the codec keeps the
    case-projection arms gated — `refused` renderings and every other dimension are
    unchanged. Each closed dimension is independent: no admission re-grids another's
    surface. Schema bytes are exact source-owned candidate bytes; a hash never
    establishes live publication. No process-global mutation or environment activation
    exists.
    """
    wire = CommercialWire(
        schema_bytes,
        hosted_origins=hosted_origins,
        issuer_document_origins=issuer_document_origins,
        delivery_origins=delivery_origins,
    )
    codec: CommercialCodec = CoverageGatedCodec(wire, gated=coverage_admission is None)
    codec = SupportGatedCodec(codec, gated=support_admission is None)
    codec = CatalogGatedCodec(codec, gated=catalog_admission is None)
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
    if catalog_admission is not None and catalog_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_catalog_admission_invalid")
    if acceptance_admission is not None and acceptance_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_acceptance_admission_invalid")
    if coverage_admission is not None and coverage_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_coverage_admission_invalid")
    if support_admission is not None and support_admission.contract_sha256 != wire.schema_digest:
        raise ValueError("commercial_support_admission_invalid")
    executor = HttpCommercialExecutor(
        origin=amh_origin, authority=source_authority, timeout_seconds=timeout_seconds, transport=transport
    )
    catalog_refused = A1_CATALOG_DERIVED_COMMANDS if catalog_admission is None else frozenset()
    gate_cls: type[AcceptanceUnadmittedSalesAdmission] | None = None
    if acceptance_admission is None and support_admission is None:
        gate_cls = AcceptanceSupportUnadmittedSalesAdmission
    elif acceptance_admission is None:
        gate_cls = AcceptanceUnadmittedSalesAdmission
    elif support_admission is None:
        gate_cls = SupportUnadmittedSalesAdmission
    elif catalog_admission is None:
        gate_cls = CatalogUnadmittedSalesAdmission
    admission: AuthenticatedSalesAdmission = (
        AuthenticatedSalesAdmission(caller=caller, authority=scope_authority)
        if gate_cls is None
        else gate_cls(caller=caller, authority=scope_authority, also_refused=catalog_refused)
    )
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
