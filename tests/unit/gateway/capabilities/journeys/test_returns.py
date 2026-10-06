"""Return mechanics with explicit UNIT journal/sender/evidence doubles only."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from maezo.gateway.capabilities.admission import AdmissionBinding, VerifiedAuthority, VerifiedCurrentness
from maezo.gateway.capabilities.durability.models import (
    JournalCallResult,
    JournalCallTechnicalStatus,
    OutboxAckEvidence,
    OutboxSnapshot,
    OutboxTechnicalState,
)
from maezo.gateway.capabilities.journeys.returns import JourneyReplyBoundary
from maezo.gateway.capabilities.models import CapabilityRefusalReason, DeliveryEvidenceReceipt

from .helpers import NOW, binding, outbox


class UnitReplyJournal:
    def __init__(self) -> None:
        self.snapshot = OutboxSnapshot(
            descriptor=outbox(0, "unit-command-0"),
            journal_revision=1,
            technical_state=OutboxTechnicalState.RECORDED,
        )
        self.calls: list[str] = []
        self.uncertain = False
        self.old_worker = False
        self.expired = False
        self.wrong_payload = False
        self.fail_fence = False

    async def claim_outbox(self, b: Any, ref: str, worker: str, lease: Any, revision: int) -> Any:
        self.calls.append("claim")
        assert revision == self.snapshot.journal_revision
        self.snapshot = self.snapshot.model_copy(
            update=dict(
                technical_state=OutboxTechnicalState.CLAIMED,
                claim_ref="unit-claim",
                worker_ref="old-worker" if self.old_worker else worker,
                lease_until=NOW - timedelta(seconds=1) if self.expired else lease,
                fence_version=1,
                journal_revision=revision + 1,
            )
        )
        return JournalCallResult[OutboxSnapshot](
            technical_status=JournalCallTechnicalStatus.RECORDED, snapshot=self.snapshot
        )

    async def begin_outbox_delivery(self, b: Any, ref: str, claim: str, revision: int) -> Any:
        self.calls.append("fence")
        if self.fail_fence:
            return JournalCallResult[OutboxSnapshot](technical_status=JournalCallTechnicalStatus.UNCERTAIN)
        self.snapshot = self.snapshot.model_copy(
            update=dict(
                technical_state=OutboxTechnicalState.DELIVERY_FENCED,
                delivery_ref="unit-delivery",
                journal_revision=revision + 1,
            )
        )
        result = self.snapshot
        if self.wrong_payload:
            result = result.model_copy(
                update={"descriptor": result.descriptor.model_copy(update={"payload_sha256": "f" * 64})}
            )
        return JournalCallResult[OutboxSnapshot](
            technical_status=JournalCallTechnicalStatus.RECORDED, snapshot=result
        )

    async def record_outbox_ack(self, b: Any, ref: str, delivery: str, evidence: Any, revision: int) -> Any:
        self.calls.append("ack")
        assert revision == self.snapshot.journal_revision and delivery == self.snapshot.delivery_ref
        self.snapshot = self.snapshot.model_copy(
            update=dict(
                technical_state=OutboxTechnicalState.ACK_RECORDED,
                acknowledgement_ref=evidence.acknowledgement_ref,
                acknowledgement_sha256=evidence.acknowledgement_sha256,
                journal_revision=revision + 1,
            )
        )
        return JournalCallResult[OutboxSnapshot](
            technical_status=JournalCallTechnicalStatus.RECORDED, snapshot=self.snapshot
        )

    async def mark_outbox_uncertain(self, b: Any, ref: str, delivery: str, revision: int) -> Any:
        self.calls.append("uncertain")
        self.uncertain = True
        return JournalCallResult[OutboxSnapshot](technical_status=JournalCallTechnicalStatus.UNCERTAIN)


class UnitReplyAuthority:
    """UNIT evidence registry; constructed evidence absent registry is refused."""

    def __init__(self) -> None:
        self.revoked = False
        self.revalidate_count = 0
        self.revoke_after = 100
        self.ack_proof = True
        self.receipt_proof = True
        b = binding().journal_binding()
        admission = AdmissionBinding(
            principal_ref=b.principal_ref,
            task_ref=b.task_ref,
            tenant_ref=b.tenant_ref,
            legal_entity_ref=b.legal_entity_ref,
            purpose_ref="unit-purpose",
            operation_name="notice.prepare_or_send",
            schema_version="v21-capabilities.proposed.v1",
            contract_revision="unit-contract",
            source_authority_ref="unit-source",
            policy_revision="unit-policy",
            data_classification="unit-administrative",
            autonomy_action="unit-action",
            security_zone="general",
        )
        self.authority = VerifiedAuthority(
            binding=admission,
            request_sha256="b" * 64,
            authorization_ref="unit-private-authorization",
            signed_task_ref="unit-signed-task",
            agent_card_sha256="a" * 64,
            source_contract_publication_ref="unit-contract-publication",
            policy_ratification_ref="unit-policy-ratification",
            currentness_ref="unit-currentness",
            enforcement="enforcing",
            decision="allow",
            verified_at=NOW,
            valid_until=NOW + timedelta(minutes=1),
        )
        self.current = VerifiedCurrentness(
            binding=admission,
            request_sha256="b" * 64,
            authorization_ref="unit-private-authorization",
            currentness_ref="unit-currentness",
            checked_at=NOW,
            valid_until=NOW + timedelta(minutes=1),
        )

    async def authorize(self, b: Any, snapshot: Any) -> Any:
        if self.revoked:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return self.authority, self.current

    async def revalidate(self, b: Any, snapshot: Any, authority: Any, currentness: Any) -> Any:
        self.revalidate_count += 1
        if self.revalidate_count >= self.revoke_after:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        assert authority == self.authority
        return authority, self.current

    async def verify_ack(self, b: Any, snapshot: Any, evidence: Any) -> bool:
        return self.ack_proof and evidence.acknowledgement_ref == "unit-target-authenticated-ack"

    async def verify_delivery(self, b: Any, snapshot: Any, receipt: Any) -> bool:
        return self.receipt_proof and receipt.provider_delivery_receipt_ref == "unit-source-delivery-receipt"


class UnitReplyTarget:
    def __init__(self, *, ack: bool = True, receipt: bool = False) -> None:
        self.calls = 0
        self.ack = ack
        self.receipt = receipt
        self.protected_receipts: list[Any] = []
        self.bad_ack = False
        self.fail = False

    async def deliver(self, b: Any, snapshot: Any, authority: Any, currentness: Any) -> Any:
        self.calls += 1
        if self.fail:
            raise TimeoutError("SYNTHETIC_PRIVATE_DELIVERY_BODY_90210")
        ack = (
            OutboxAckEvidence(
                outbox_ref=snapshot.descriptor.outbox_ref,
                delivery_ref=snapshot.delivery_ref,
                target_binding_ref=snapshot.descriptor.target_binding_ref,
                payload_sha256="f" * 64 if self.bad_ack else snapshot.descriptor.payload_sha256,
                acknowledgement_ref="unit-target-authenticated-ack",
                acknowledgement_sha256="c" * 64,
                verifier_binding_ref="unit-ack-verifier",
                observed_at=NOW,
            )
            if self.ack
            else None
        )
        receipt = (
            DeliveryEvidenceReceipt(
                delivery_status="delivered",
                provider_delivery_receipt_ref="unit-source-delivery-receipt",
                attempt_revision="unit-opaque-delivery-revision",
            )
            if self.receipt
            else None
        )
        if receipt is not None:
            self.protected_receipts.append(receipt)  # UNIT custody, never a product provider claim.
        return ack, receipt


def setup() -> tuple[Any, ...]:
    journal = UnitReplyJournal()
    target = UnitReplyTarget()
    authority = UnitReplyAuthority()
    boundary = JourneyReplyBoundary(journal=journal, reply=target, authority=authority, clock=lambda: NOW)
    return journal, target, authority, boundary


async def deliver(boundary: Any) -> Any:
    return await boundary.deliver(
        binding().journal_binding(),
        outbox_ref="unit-outbox-0",
        worker_ref="unit-registered-worker",
        lease_until=NOW + timedelta(seconds=20),
        expected_journal_revision=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["old_worker", "expired", "wrong_payload", "fail_fence", "revoked"])
async def test_unacknowledged_or_unbound_fence_refuses_before_transport(fault: str) -> None:
    journal, target, authority, boundary = setup()
    if fault == "revoked":
        authority.revoked = True
    else:
        setattr(journal, fault, True)
    result = await deliver(boundary)
    assert isinstance(result, CapabilityRefusalReason)
    assert target.calls == 0 and "ack" not in journal.calls


@pytest.mark.asyncio
async def test_verified_transport_ack_is_never_source_delivery_completion() -> None:
    journal, target, _, boundary = setup()
    ack, receipt = await deliver(boundary)
    assert ack is not None and receipt is None
    assert journal.calls == ["claim", "fence", "ack"]
    assert target.calls == 1 and not journal.uncertain


@pytest.mark.asyncio
async def test_source_receipt_without_ack_preserved_and_no_ack_fabricated() -> None:
    journal, target, _, boundary = setup()
    target.ack = False
    target.receipt = True
    ack, receipt = await deliver(boundary)
    assert ack is None and receipt in target.protected_receipts
    assert journal.uncertain and "ack" not in journal.calls


@pytest.mark.asyncio
async def test_no_evidence_tuple_is_uncertain_not_delivered_or_acked() -> None:
    journal, target, _, boundary = setup()
    target.ack = False
    assert await deliver(boundary) == (None, None)
    assert journal.uncertain and "ack" not in journal.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["wrong_ack", "unverified_ack", "unverified_receipt", "post_transport_revocation", "timeout"]
)
async def test_tuple_presence_or_fields_never_replace_independent_evidence(fault: str, caplog: Any) -> None:
    journal, target, authority, boundary = setup()
    if fault == "wrong_ack":
        target.bad_ack = True
    elif fault == "unverified_ack":
        authority.ack_proof = False
    elif fault == "unverified_receipt":
        target.receipt = True
        authority.receipt_proof = False
    elif fault == "post_transport_revocation":
        authority.revoke_after = 2
    else:
        target.fail = True
    assert isinstance(await deliver(boundary), CapabilityRefusalReason)
    assert journal.uncertain and "ack" not in journal.calls
    assert target.calls == 1
    assert "SYNTHETIC_PRIVATE_DELIVERY_BODY_90210" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ["shadow", "wrong_principal", "future_verification"])
async def test_constructed_authority_fields_cannot_bypass_closed_reparse(forgery: str) -> None:
    journal, target, authority, boundary = setup()
    if forgery == "shadow":
        authority.authority = authority.authority.model_copy(update={"enforcement": "shadow"})
    elif forgery == "wrong_principal":
        authority.authority = authority.authority.model_copy(
            update={
                "binding": authority.authority.binding.model_copy(
                    update={"principal_ref": "foreign-principal"}
                )
            }
        )
    else:
        authority.authority = authority.authority.model_copy(
            update={"verified_at": NOW + timedelta(seconds=1)}
        )
    assert isinstance(await deliver(boundary), CapabilityRefusalReason)
    assert target.calls == 0 and "ack" not in journal.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_at", [3, 4, 5])
async def test_every_post_receipt_recheck_preserves_original_private_authority(switch_at: int) -> None:
    journal, target, authority, boundary = setup()
    target.receipt = True
    original, original_current = authority.authority, authority.current
    rebound = original.model_copy(
        update={"authorization_ref": "unit-rebound-private-authority", "request_sha256": "f" * 64}
    )
    rebound_current = original_current.model_copy(
        update={"authorization_ref": rebound.authorization_ref, "request_sha256": rebound.request_sha256}
    )

    async def revalidate(b, snapshot, admitted, current):
        authority.revalidate_count += 1
        return (
            (rebound, rebound_current)
            if authority.revalidate_count >= switch_at
            else (original, original_current)
        )

    authority.revalidate = revalidate
    result = await deliver(boundary)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert target.calls == 1
    if switch_at < 5:
        assert "ack" not in journal.calls and journal.uncertain
    else:
        # A valid historical ACK survives a later disclosure failure; no reset/downgrade.
        assert "ack" in journal.calls and not journal.uncertain
    assert not isinstance(result, tuple)


@pytest.mark.asyncio
async def test_refresh_cannot_extend_original_currentness_validity_ceiling() -> None:
    journal, target, authority, boundary = setup()
    target.receipt = True
    ticks = [NOW]
    boundary.clock = lambda: ticks[0]
    authority.current = authority.current.model_copy(update={"valid_until": NOW + timedelta(seconds=1)})
    original = authority.authority

    async def revalidate(b, snapshot, admitted, current):
        authority.revalidate_count += 1
        return original, authority.current.model_copy(update={"valid_until": NOW + timedelta(seconds=10)})

    original_verify = authority.verify_delivery

    async def verify_receipt(*args):
        valid = await original_verify(*args)
        ticks[0] = NOW + timedelta(seconds=2)
        return valid

    authority.revalidate, authority.verify_delivery = revalidate, verify_receipt
    result = await deliver(boundary)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert target.calls == 1 and "ack" not in journal.calls and journal.uncertain
