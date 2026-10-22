"""Candidate Sales orchestration; every source step needs current authority.

No financial computation, eligibility decision, rule, source credential or local
idempotency cache exists here. The AMH source owns atomic effects and receipts.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialPreparation,
    CommercialPublication,
    CommercialRefusalError,
    CommercialResultAuthority,
    CommercialScope,
    CommercialSource,
    OperationalResult,
    PublicCatalogAdmission,
    PublicCatalogProof,
    PublicCatalogScope,
    PublicCatalogSource,
    SalesAdmission,
    SalesTransport,
    command_digest,
    public_scope_digest,
    scope_digest,
)

Phase = Literal["before_body", "before_source", "before_effect", "before_disclosure"]
_REGISTRY_REQUIRED = frozenset({"offer.quote", "contract.issue", "receivable.ensure", "payment.create"})
_CONTRACT_REQUIRED = frozenset({"receivable.ensure", "payment.create"})
_METHODS = frozenset({"pix", "card", "boleto"})
_C6_SCOPES = {
    "work.list": "interop/sales.work.read",
    "work.claim": "interop/sales.work.manage",
    "work.assign": "interop/sales.work.manage",
    "work.handoff": "interop/sales.work.manage",
    "work.reconcile": "interop/sales.work.manage",
    "journey.save": "interop/sales.journey.write",
    "journey.get": "interop/sales.journey.read",
    "support.handoff": "interop/sales.support",
}


def _ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and value.isascii()
        and value.isprintable()
        and not any(c.isspace() for c in value)
    )


def _future(value: datetime, now: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None and value > now


class CommercialGateway:
    """Composition is explicit. Missing authority/source leaves REAL ingress closed."""

    def __init__(
        self,
        *,
        codec: CommercialCodec,
        admission: SalesAdmission | None = None,
        source: CommercialSource | None = None,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(codec, CommercialCodec)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("commercial_composition_invalid")
        self.codec = codec
        self.admission = admission
        self.source = source
        self.timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def _scope(self, scope: CommercialScope) -> None:
        now = self._clock()
        required = (
            scope.admission_ref,
            scope.workload_ref,
            scope.tenant_ref,
            scope.legal_entity_ref,
            scope.purpose_ref,
            scope.lifecycle_ref,
            scope.identity_receipt_ref,
            scope.source_grant_ref,
            scope.source_binding_revision,
            scope.authority_revision,
            scope.correlation_ref,
            scope.publication_ref,
        )
        if (
            not all(_ref(value) for value in required)
            or scope.valid_from.utcoffset() is None
            or scope.valid_from > now
            or not _future(scope.valid_until, now)
            or type(scope.allowed_operations) is not frozenset
            or not scope.allowed_operations
        ):
            raise CommercialRefusalError("denied", "admission_required")
        # Public catalog uses a separate machine scope/gateway, never this actor scope.
        if scope.audience != "account" or scope.actor_kind not in {"customer", "staff"}:
            raise CommercialRefusalError("denied", "actor_denied")
        if not all(_ref(value) for value in (scope.session_ref, scope.actor_ref, scope.account_ref)):
            raise CommercialRefusalError("denied", "identity_unqualified")
        if scope.represented_party_ref is not None and not _ref(scope.representation_ref):
            raise CommercialRefusalError("denied", "representation_denied")
        if type(scope.granted_scopes) is not frozenset:
            raise CommercialRefusalError("denied", "admission_required")
        # Before-body admission of an expanded scope already requires independent
        # current resource/linkage authority. A historical OP purpose is insufficient.
        for operation in scope.allowed_operations:
            self._operation_scope(scope, operation)

    @staticmethod
    def _operation_scope(scope: CommercialScope, operation: str) -> None:
        grant = _C6_SCOPES.get(operation)
        if grant is None:
            return
        if grant not in scope.granted_scopes or not _ref(scope.resource_grant_ref):
            raise CommercialRefusalError("denied", "purpose_denied")
        if operation.startswith("work."):
            if scope.actor_kind != "staff" or scope.purpose_ref != "sales_operations":
                raise CommercialRefusalError("denied", "actor_denied")
        elif not _ref(scope.journey_linkage_ref):
            raise CommercialRefusalError("denied", "representation_denied")

    async def _current(
        self,
        scope: CommercialScope,
        phase: Phase,
        command: CommercialCommand | None = None,
    ) -> CommercialScope:
        if self.admission is None:
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        self._scope(scope)
        current = await self.admission.current(scope, phase=phase, command=command)
        if not isinstance(current, CommercialScope) or current != scope:
            raise CommercialRefusalError("denied", "authority_revoked")
        self._scope(current)
        if command is not None and command.command not in current.allowed_operations:
            raise CommercialRefusalError("denied", "actor_denied")
        return current

    def _publication(self, proof: CommercialPublication, scope: CommercialScope) -> None:
        if (
            not isinstance(proof, CommercialPublication)
            or proof.shared_contract_digest != self.codec.schema_digest
            or proof.publication_ref != scope.publication_ref
            or proof.source_binding_ref != scope.source_grant_ref
            or proof.source_binding_revision != scope.source_binding_revision
            or proof.tenant_ref != scope.tenant_ref
            or proof.legal_entity_ref != scope.legal_entity_ref
            or not all(
                _ref(value)
                for value in (
                    proof.publication_ref,
                    proof.source_contract_digest,
                    proof.shared_contract_digest,
                    proof.currentness_ref,
                )
            )
            or not _future(proof.valid_until, self._clock())
        ):
            raise CommercialRefusalError("unavailable", "binding_unqualified")

    async def admit(self, transport: SalesTransport) -> CommercialScope:
        """Must finish before reading even one byte of a subject request body."""
        if not isinstance(self.admission, SalesAdmission) or not isinstance(self.source, CommercialSource):
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        async with asyncio.timeout(self.timeout_seconds):
            scope = await self.admission.acquire(transport)
            if not isinstance(scope, CommercialScope):
                raise CommercialRefusalError("denied", "admission_required")
            scope = await self._current(scope, "before_body")
            self._publication(await self.source.admit(scope), scope)
            return scope

    def _prepared(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
        prepared: CommercialPreparation,
    ) -> None:
        if not isinstance(prepared, CommercialPreparation):
            raise CommercialRefusalError("unavailable", "source_unavailable")
        self._publication(prepared.publication, scope)
        if (
            prepared.command_digest != command_digest(command)
            or prepared.scope_digest != scope_digest(scope)
            or not _ref(prepared.operation_ref)
            or not _ref(prepared.source_evidence_ref)
            or not _future(prepared.valid_until, self._clock())
        ):
            raise CommercialRefusalError("denied", "source_stale")
        self._terms(command, scope, prepared)
        if command.command in {"payment.create", "mandate.create"}:
            selection, choice = command.payment_selection, prepared.payment_choice
            if (
                selection is None
                or choice is None
                or choice.selection != selection
                or choice.scope_digest != scope_digest(scope)
                or choice.command_digest != command_digest(command)
                or type(choice.source_revision) is not int
                or choice.source_revision < 1
                or not all(
                    _ref(value)
                    for value in (
                        choice.capability_ref,
                        choice.account_binding_ref,
                        choice.contractual_use_ref,
                        choice.currentness_ref,
                    )
                )
                or not _future(choice.valid_until, self._clock())
            ):
                raise CommercialRefusalError("denied", "source_stale")
        if command.command in _REGISTRY_REQUIRED and (
            not _ref(prepared.registry_readiness_ref) or prepared.ready_methods != _METHODS
        ):
            raise CommercialRefusalError("unavailable", "payment_methods_unavailable")
        if command.command in _CONTRACT_REQUIRED:
            issued = prepared.issued_contract
            if (
                issued is None
                or not all(
                    _ref(value)
                    for value in (
                        issued.contract_ref,
                        issued.issuer_receipt_ref,
                        issued.payer_ref,
                        issued.source_revision_ref,
                        issued.signed_receipt_verification_ref,
                    )
                )
                or not _future(issued.valid_until, self._clock())
            ):
                raise CommercialRefusalError("denied", "contract_not_issued")
            if not _ref(issued.proceed_authority_ref):
                raise CommercialRefusalError("denied", "proceed_authority_missing")

    def _terms(
        self, command: CommercialCommand, scope: CommercialScope, prepared: CommercialPreparation
    ) -> None:
        if command.command not in {"offer.terms", "acceptance.record"}:
            return
        selected = command.terms_selection
        proof = prepared.terms_review
        now = self._clock()
        if (
            selected is None
            or proof is None
            or proof.scope_digest != scope_digest(scope)
            or proof.selection.quote_ref != selected.quote_ref
            or proof.selection.source_revision != selected.source_revision
            or proof.selection.terms_digest != selected.terms_digest
            or (selected.terms_review_ref is not None and proof.terms_review_ref != selected.terms_review_ref)
            or not all(
                _ref(value)
                for value in (proof.terms_review_ref, proof.currentness_ref, proof.document_validity_ref)
            )
            or not _future(proof.valid_until, now)
        ):
            raise CommercialRefusalError("denied", "terms_mismatch")
        if proof.review_state == "pending":
            if proof.presentation_receipt_ref is not None or proof.presentation_valid_until is not None:
                raise CommercialRefusalError("denied", "terms_mismatch")
        elif proof.review_state == "presented":
            if (
                not _ref(proof.presentation_receipt_ref)
                or proof.presentation_valid_until is None
                or not _future(proof.presentation_valid_until, now)
            ):
                raise CommercialRefusalError("denied", "terms_review_required")
        else:
            raise CommercialRefusalError("denied", "terms_mismatch")
        if command.command == "acceptance.record" and (
            selected.terms_review_ref is None or selected.decision not in {"accept", "decline"}
        ):
            raise CommercialRefusalError("denied", "terms_review_required")
        if selected.decision == "accept" and proof.review_state != "presented":
            raise CommercialRefusalError("denied", "terms_review_required")

    def _result_authority(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
        result: OperationalResult,
        proof: CommercialResultAuthority,
    ) -> None:
        if (
            not isinstance(proof, CommercialResultAuthority)
            or proof.scope_digest != scope_digest(scope)
            or proof.command_digest != command_digest(command)
            or proof.result_digest != hashlib.sha256(result.wire_bytes).hexdigest()
            or not _ref(proof.resource_grant_ref)
            or not _ref(proof.currentness_ref)
            or (scope.resource_grant_ref is not None and proof.resource_grant_ref != scope.resource_grant_ref)
            or not _future(proof.valid_until, self._clock())
        ):
            raise CommercialRefusalError("denied", "source_stale")

    async def execute(self, command: CommercialCommand, scope: CommercialScope) -> OperationalResult:
        operation_ref: str | None = "commercial-" + uuid.uuid4().hex
        dispatched = False
        try:
            if self.source is None:
                raise CommercialRefusalError("unavailable", "binding_unqualified")
            # Reparse immutable bytes: direct internal construction is not a bypass.
            if self.codec.parse_command(command.wire_bytes) != command:
                raise CommercialRefusalError("denied", "invalid_request")
            async with asyncio.timeout(self.timeout_seconds):
                scope = await self._current(scope, "before_source", command)
                self._publication(await self.source.admit(scope), scope)
                prepared = await self.source.prepare(command, scope)
                self._prepared(command, scope, prepared)
                operation_ref = prepared.operation_ref
                scope = await self._current(scope, "before_effect", command)
                self._prepared(command, scope, prepared)
                if await self.source.admit(scope) != prepared.publication:
                    raise CommercialRefusalError("denied", "source_stale")
                # AMH atomically rechecks proof, source revision, current authority,
                # dedupe and original account binding. No retry on lost response.
                dispatched = True
                received = await self.source.execute(command, scope, prepared)
                result = self.codec.parse_result(received.wire_bytes, command)
                if result != received or result.operation_ref != prepared.operation_ref:
                    raise CommercialRefusalError("uncertain", "reconciliation_required")
                if command.command == "offer.terms" and result.status == "succeeded":
                    presentation = result.terms_presentation
                    proof = prepared.terms_review
                    if (
                        presentation is None
                        or proof is None
                        or presentation.terms_review_ref != proof.terms_review_ref
                        or presentation.review_state != proof.review_state
                        or presentation.presentation_receipt_ref != proof.presentation_receipt_ref
                    ):
                        raise CommercialRefusalError("uncertain", "reconciliation_required")
                await self._current(scope, "before_disclosure", command)
                self._prepared(command, scope, prepared)
                if await self.source.admit(scope) != prepared.publication:
                    raise CommercialRefusalError("uncertain", "reconciliation_required")
                response_proof = None
                if result.status == "succeeded" and result.requires_current_result:
                    self._operation_scope(scope, result.authorization_command or command.command)
                    response_proof = await self.source.result_authority(command, scope, result)
                    self._result_authority(command, scope, result, response_proof)
                # Authority checks may await IO. Revalidate ephemeral hosted URLs
                # and their current issuer-origin binding at actual disclosure.
                if self.codec.parse_result(result.wire_bytes, command) != result:
                    raise CommercialRefusalError("uncertain", "reconciliation_required")
                # The codec's final issuer lookup may consume a lease. These
                # checks are local and do not start another authority IO cycle.
                self._scope(scope)
                self._prepared(command, scope, prepared)
                if response_proof is not None:
                    self._result_authority(command, scope, result, response_proof)
                return result
        except asyncio.CancelledError:
            # Source execution owns durable uncertainty/outbox custody. Cancellation
            # never authorizes replay or a provider/account switch.
            if dispatched:
                return self.codec.refusal("uncertain", "reconciliation_required", operation_ref)
            raise
        except CommercialRefusalError as exc:
            if dispatched:
                return self.codec.refusal("uncertain", "reconciliation_required", operation_ref)
            try:
                return self.codec.refusal(exc.status, exc.reason_code, operation_ref)
            except Exception:
                return self.codec.refusal("unavailable", "source_unavailable", operation_ref)
        except Exception:
            return self.codec.refusal(
                "uncertain" if dispatched else "unavailable",
                "reconciliation_required" if dispatched else "source_unavailable",
                operation_ref,
            )


class PublicCatalogGateway:
    """Separate current server-machine read path; no customer identity or intake."""

    def __init__(
        self,
        *,
        codec: CommercialCodec,
        admission: PublicCatalogAdmission | None = None,
        source: PublicCatalogSource | None = None,
        timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(codec, CommercialCodec)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("commercial_composition_invalid")
        self.codec, self.admission, self.source = codec, admission, source
        self.timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def _scope(self, scope: PublicCatalogScope) -> None:
        if (
            not isinstance(scope, PublicCatalogScope)
            or scope.purpose != "sales_public_catalog"
            or scope.granted_scope != "interop/sales.catalog.public.read"
            or not all(
                _ref(value)
                for value in (
                    scope.workload_ref,
                    scope.tenant_ref,
                    scope.legal_entity_ref,
                    scope.operator_ref,
                    scope.source_grant_ref,
                    scope.source_binding_revision,
                    scope.publication_ref,
                    scope.lifecycle_ref,
                    scope.currentness_ref,
                )
            )
            or not _future(scope.valid_until, self._clock())
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")

    def _proof(self, proof: PublicCatalogProof, scope: PublicCatalogScope, result_digest: str | None) -> None:
        if (
            not isinstance(proof, PublicCatalogProof)
            or proof.scope_digest != public_scope_digest(scope)
            or proof.schema_digest != self.codec.schema_digest
            or proof.publication_ref != scope.publication_ref
            or proof.result_digest != result_digest
            or not _ref(proof.currentness_ref)
            or not _future(proof.valid_until, self._clock())
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")

    async def admit(self, transport: SalesTransport) -> PublicCatalogScope:
        if not isinstance(self.admission, PublicCatalogAdmission) or not isinstance(
            self.source, PublicCatalogSource
        ):
            raise CommercialRefusalError("unavailable", "source_unavailable")
        if transport.method != "GET" or transport.path != "/api/v1/sales/public/catalog":
            raise CommercialRefusalError("unavailable", "source_unavailable")
        async with asyncio.timeout(self.timeout_seconds):
            scope = await self.admission.acquire(transport)
            self._scope(scope)
            if await self.admission.current(scope, phase="before_source") != scope:
                raise CommercialRefusalError("unavailable", "source_unavailable")
            self._scope(scope)
            self._proof(await self.source.authorize(scope, None), scope, None)
            self._scope(scope)
            return scope

    async def read(self, transport: SalesTransport) -> bytes:
        scope = await self.admit(transport)
        if self.admission is None or self.source is None:
            raise CommercialRefusalError("unavailable", "source_unavailable")
        async with asyncio.timeout(self.timeout_seconds):
            raw = await self.source.read(scope)
            self.codec.parse_public_result(raw)
            if await self.admission.current(scope, phase="before_disclosure") != scope:
                raise CommercialRefusalError("unavailable", "source_unavailable")
            body_digest = hashlib.sha256(raw).hexdigest()
            proof = await self.source.authorize(scope, body_digest)
            # All source/authority IO is over: pure proof/DTO deadlines only.
            self._proof(proof, scope, body_digest)
            self._scope(scope)
            return self.codec.parse_public_result(raw)
