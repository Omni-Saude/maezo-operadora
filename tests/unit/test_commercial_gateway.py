"""Source-independent UNIT doubles; no engine, live admission or financial effects."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256, CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialPreparation,
    CommercialPublication,
    CommercialRefusalError,
    CommercialScope,
    CommercialSource,
    CommercialStatus,
    IssuedContractAuthority,
    OperationalResult,
    SalesAdmission,
    SalesTransport,
    TermsReviewAuthority,
    TermsSelection,
    command_digest,
    scope_digest,
)

NOW = datetime(2026, 10, 22, tzinfo=UTC)
PHASE = Literal["before_body", "before_source", "before_effect", "before_disclosure"]


def scope() -> CommercialScope:
    return CommercialScope(
        admission_ref="admission",
        workload_ref="sales-bff",
        tenant_ref="tenant-a",
        legal_entity_ref="entity-a",
        purpose_ref="sales_billing",
        lifecycle_ref="lifecycle",
        identity_receipt_ref="identity",
        source_grant_ref="binding",
        source_binding_revision="1",
        authority_revision="1",
        correlation_ref="correlation",
        publication_ref="publication",
        allowed_operations=frozenset(
            {
                "receivable.ensure",
                "payment.create",
                "payment.reconcile",
                "mandate.revoke",
                "operation.get",
                "acceptance.record",
            }
        ),
        valid_from=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(minutes=5),
        audience="account",
        session_ref="session",
        actor_ref="customer",
        actor_kind="customer",
        account_ref="account",
        representation_ref="representation",
    )


class UnitCodec(CommercialCodec):
    """Synthetic envelope only; real schema conformance is tested from external current C2 bytes."""

    schema_digest = CANDIDATE_SCHEMA_SHA256

    def parse_command(self, body: bytes) -> CommercialCommand:
        name = body.decode()
        if name not in {
            "receivable.ensure",
            "payment.create",
            "payment.reconcile",
            "mandate.revoke",
            "operation.get",
            "acceptance.record",
        }:
            raise CommercialRefusalError("denied", "invalid_request")
        return CommercialCommand(
            command=name,
            command_ref="command",
            expected_revision=1,
            wire_bytes=body,  # type: ignore[arg-type]
            terms_selection=TermsSelection(
                quote_ref="quote",
                source_revision=1,
                terms_digest="b" * 64,
                terms_review_ref="review",
                decision="accept",
            )
            if name == "acceptance.record"
            else None,
        )

    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult:
        value = json.loads(body)
        return OperationalResult(
            status=value["status"],
            operation_ref=value["operation_ref"],
            reason_code=value["reason_code"],
            wire_bytes=body,
        )

    def refusal(
        self,
        status: CommercialStatus,
        reason_code: str,
        operation_ref: str | None = None,
    ) -> OperationalResult:
        body = json.dumps(
            {"status": status, "operation_ref": operation_ref, "reason_code": reason_code}
        ).encode()
        return self.parse_result(body, self.parse_command(b"receivable.ensure"))


class UnitAdmission(SalesAdmission):
    def __init__(self, admitted: CommercialScope) -> None:
        self.admitted = admitted
        self.calls: list[str] = []
        self.revoke_at: str | None = None
        self.changed: CommercialScope | None = None
        self.deny_recovery_owner = False

    async def acquire(self, transport: SalesTransport) -> CommercialScope:
        self.calls.append("acquire")
        return self.admitted

    async def current(
        self,
        value: CommercialScope,
        *,
        phase: PHASE,
        command: CommercialCommand | None = None,
    ) -> CommercialScope:
        self.calls.append(phase)
        if (
            command is not None
            and command.command == "operation.get"
            and phase == "before_source"
            and self.deny_recovery_owner
        ):
            raise CommercialRefusalError("denied", "actor_denied")
        if phase == self.revoke_at:
            raise CommercialRefusalError("denied", "authority_revoked")
        return self.changed or value


class UnitSource(CommercialSource):
    def __init__(self, value: CommercialScope) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None
        self.executed_commands: list[str] = []
        self.cancel = False
        self.missing_contract = False
        self.missing_proceed = False
        self.missing_method = False
        self.stale = False
        self.different_command = False
        self.different_scope = False
        self.different_tenant = False
        self.proof = CommercialPublication(
            publication_ref=value.publication_ref,
            source_contract_digest="a" * 64,
            shared_contract_digest=CANDIDATE_SCHEMA_SHA256,
            source_binding_ref=value.source_grant_ref,
            source_binding_revision=value.source_binding_revision,
            tenant_ref=value.tenant_ref,
            legal_entity_ref=value.legal_entity_ref,
            currentness_ref="source-current",
            valid_until=NOW + timedelta(minutes=5),
        )

    async def admit(self, value: CommercialScope) -> CommercialPublication:
        self.calls.append("publication")
        return replace(self.proof, tenant_ref="tenant-b") if self.different_tenant else self.proof

    async def prepare(self, command: CommercialCommand, value: CommercialScope) -> CommercialPreparation:
        self.calls.append("prepare")
        issued = IssuedContractAuthority(
            contract_ref="contract",
            issuer_receipt_ref="issued-source-receipt",
            proceed_authority_ref="" if self.missing_proceed else "proceed",
            payer_ref="payer-distinct-from-customer",
            source_revision_ref="source-revision",
            signed_receipt_verification_ref="source-signature-verification",
            valid_until=NOW + timedelta(minutes=5),
        )
        return CommercialPreparation(
            operation_ref="operation-source",
            command_digest="wrong" if self.different_command else command_digest(command),
            scope_digest="wrong" if self.different_scope else scope_digest(value),
            source_evidence_ref="verified-source-evidence",
            publication=self.proof,
            valid_until=NOW - timedelta(seconds=1) if self.stale else NOW + timedelta(minutes=5),
            registry_readiness_ref="registry-current",
            ready_methods=frozenset({"pix", "card"} if self.missing_method else {"pix", "card", "boleto"}),
            issued_contract=None if self.missing_contract else issued,
            terms_review=TermsReviewAuthority(
                selection=command.terms_selection,
                scope_digest=scope_digest(value),
                terms_review_ref="review",
                review_state="presented",
                presentation_receipt_ref="receipt",
                presentation_valid_until=NOW + timedelta(minutes=5),
                currentness_ref="terms-current",
                document_validity_ref="document-current",
                valid_until=NOW + timedelta(minutes=5),
            )
            if command.terms_selection is not None
            else None,
        )

    async def execute(
        self,
        command: CommercialCommand,
        value: CommercialScope,
        preparation: CommercialPreparation,
    ) -> OperationalResult:
        self.calls.append("execute")
        self.executed_commands.append(command.command)
        if self.fail:
            raise self.fail
        if self.cancel:
            raise asyncio.CancelledError
        return UnitCodec().refusal("succeeded", "synthetic-source-result", preparation.operation_ref)


def composed() -> tuple[CommercialGateway, UnitAdmission, UnitSource]:
    value = scope()
    admission = UnitAdmission(value)
    source = UnitSource(value)
    gateway = CommercialGateway(codec=UnitCodec(), admission=admission, source=source, clock=lambda: NOW)
    return gateway, admission, source


@pytest.mark.asyncio
async def test_unbound_admission_has_no_source_io() -> None:
    gateway = CommercialGateway(codec=UnitCodec())
    with pytest.raises(CommercialRefusalError, match="unavailable"):
        await gateway.admit(
            SalesTransport(connection_ref=None, method="POST", path="/api/v1/sales/commercial")
        )


@pytest.mark.asyncio
async def test_source_independent_positive_orders_current_authority_and_source_proof() -> None:
    gateway, admission, source = composed()
    value = await gateway.admit(
        SalesTransport(connection_ref="server-connection", method="POST", path="fixed")
    )
    result = await gateway.execute(UnitCodec().parse_command(b"receivable.ensure"), value)
    assert result.status == "succeeded"
    assert admission.calls == [
        "acquire",
        "before_body",
        "before_source",
        "before_effect",
        "before_disclosure",
    ]
    assert source.calls.count("execute") == 1
    assert source.calls.index("prepare") < source.calls.index("execute")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_source", "before_effect"])
async def test_revocation_before_effect_prevents_dispatch(phase: str) -> None:
    gateway, admission, source = composed()
    admission.revoke_at = phase
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert (result.status, result.reason_code) == ("denied", "authority_revoked")
    assert "execute" not in source.calls


@pytest.mark.asyncio
async def test_revocation_before_disclosure_never_discloses_source_result_or_retries() -> None:
    gateway, admission, source = composed()
    admission.revoke_at = "before_disclosure"
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert (result.status, result.reason_code) == ("uncertain", "reconciliation_required")
    assert source.calls.count("execute") == 1
    assert b"synthetic-source-result" not in result.wire_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing,reason",
    [
        ("missing_contract", "contract_not_issued"),
        ("missing_proceed", "proceed_authority_missing"),
        ("missing_method", "payment_methods_unavailable"),
        ("stale", "source_stale"),
        ("different_command", "source_stale"),
        ("different_scope", "source_stale"),
        ("different_tenant", "binding_unqualified"),
    ],
)
async def test_source_qualification_is_bound_to_current_command_scope_and_receipts(
    missing: str, reason: str
) -> None:
    gateway, _, source = composed()
    setattr(source, missing, True)
    result = await gateway.execute(UnitCodec().parse_command(b"receivable.ensure"), scope())
    assert result.reason_code == reason
    assert "execute" not in source.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"valid_until": NOW},
        {"actor_kind": "service"},
        {"actor_kind": "vendor"},
        {"session_ref": None},
        {"account_ref": None},
        {"identity_receipt_ref": ""},
        {"represented_party_ref": "party", "representation_ref": None},
        {"allowed_operations": frozenset({"mandate.get"})},
    ],
)
async def test_unqualified_current_scope_never_reaches_source(change: dict[str, object]) -> None:
    gateway, _, source = composed()
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), replace(scope(), **change))
    assert result.status == "denied"
    assert source.calls == []


@pytest.mark.asyncio
async def test_changed_current_authority_revision_is_not_silently_renewed() -> None:
    gateway, admission, source = composed()
    admission.changed = replace(scope(), authority_revision="2")
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert result.reason_code == "authority_revoked"
    assert source.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_ambiguous_or_cancelled_effect_remains_uncertain_without_retry(cancel: bool) -> None:
    gateway, _, source = composed()
    source.cancel = cancel
    source.fail = None if cancel else TimeoutError("foreign sensitive body")
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert result.status == "uncertain"
    assert source.calls.count("execute") == 1
    assert b"foreign" not in result.wire_bytes


@pytest.mark.asyncio
async def test_reconciliation_and_revocation_require_current_authority_too() -> None:
    for name in (b"payment.reconcile", b"mandate.revoke"):
        gateway, admission, source = composed()
        admission.revoke_at = "before_source"
        result = await gateway.execute(UnitCodec().parse_command(name), scope())
        assert result.status == "denied"
        assert not source.calls


def test_candidate_hash_is_not_a_permissive_schema_fallback() -> None:
    with pytest.raises(ValueError, match="commercial_candidate_invalid"):
        CommercialWire(b'{"published":true,"additionalProperties":true}')


@pytest.mark.asyncio
async def test_operation_recovery_checks_original_owner_before_any_source_lookup() -> None:
    gateway, admission, source = composed()
    admission.deny_recovery_owner = True
    result = await gateway.execute(UnitCodec().parse_command(b"operation.get"), scope())
    assert (result.status, result.reason_code) == ("denied", "actor_denied")
    assert source.calls == []


@pytest.mark.asyncio
async def test_operation_recovery_dispatches_only_recovery_not_original_effect() -> None:
    gateway, _, source = composed()
    result = await gateway.execute(UnitCodec().parse_command(b"operation.get"), scope())
    assert result.status == "succeeded"
    assert source.executed_commands == ["operation.get"]


@pytest.mark.asyncio
async def test_acceptance_record_cannot_bypass_current_source_authority() -> None:
    gateway, admission, source = composed()
    admission.revoke_at = "before_effect"
    result = await gateway.execute(UnitCodec().parse_command(b"acceptance.record"), scope())
    assert (result.status, result.reason_code) == ("denied", "authority_revoked")
    assert "execute" not in source.calls


@pytest.mark.asyncio
async def test_post_dispatch_uncertainty_has_source_operation_reference() -> None:
    gateway, _, source = composed()
    source.fail = TimeoutError()
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert (result.status, result.operation_ref) == ("uncertain", "operation-source")
