"""Qualified return boundary: durable delivery fence, real ACK and source receipt.

No sender, target, custody, authority, clock lease or provider is installed.
Transport ACK and a source delivery receipt remain independently verified facts.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from maezo.gateway.capabilities.admission import VerifiedAuthority, VerifiedCurrentness
from maezo.gateway.capabilities.durability.models import (
    JournalBinding,
    JournalCallTechnicalStatus,
    OutboxAckEvidence,
    OutboxSnapshot,
    OutboxTechnicalState,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from maezo.gateway.capabilities.models import (
    CapabilityRefusalReason,
    DeliveryEvidenceReceipt,
    parse_result,
)

type ReplyEvidence = tuple[OutboxAckEvidence | None, DeliveryEvidenceReceipt | None]
type ReplyAuthority = tuple[VerifiedAuthority, VerifiedCurrentness]


class QualifiedReplyPort(Protocol):
    """Registered target resolves protected content/recipient/channel; no caller wire.

    The actual source delivery receipt must already be preserved by qualified
    custody before return, including when there is no transport ACK. The port
    must enforce original idempotency and current sender/recipient/window at its
    own commit. It never retries an uncertain transport attempt automatically.
    """

    async def deliver(
        self,
        binding: JournalBinding,
        snapshot: OutboxSnapshot,
        authority: VerifiedAuthority,
        currentness: VerifiedCurrentness,
    ) -> ReplyEvidence | CapabilityRefusalReason: ...


class ReplyAuthorityPort(Protocol):
    """Qualified OP09 admission and independent evidence/currentness verifier.

    Bind acknowledged journal fence/worker/target/payload to original command,
    admission/request digest and same private authorization. Revalidation after
    each await uses that same private lease, never a second one-shot phase call.
    A typed object or HTTP/Kafka offset alone cannot satisfy either verifier.
    """

    async def authorize(
        self, binding: JournalBinding, snapshot: OutboxSnapshot
    ) -> ReplyAuthority | CapabilityRefusalReason: ...

    async def revalidate(
        self,
        binding: JournalBinding,
        snapshot: OutboxSnapshot,
        authority: VerifiedAuthority,
        currentness: VerifiedCurrentness,
    ) -> ReplyAuthority | CapabilityRefusalReason: ...

    async def verify_ack(
        self, binding: JournalBinding, snapshot: OutboxSnapshot, evidence: OutboxAckEvidence
    ) -> bool: ...

    async def verify_delivery(
        self, binding: JournalBinding, snapshot: OutboxSnapshot, receipt: DeliveryEvidenceReceipt
    ) -> bool: ...


class JourneyReplyBoundary:
    def __init__(
        self,
        *,
        journal: DurabilityJournalPort | None = None,
        reply: QualifiedReplyPort | None = None,
        authority: ReplyAuthorityPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.journal, self.reply, self.authority = journal, reply, authority
        self.clock = clock or (lambda: datetime.now(UTC))

    def _scope(self, binding: JournalBinding, snapshot: OutboxSnapshot, proofs: ReplyAuthority) -> bool:
        authority, currentness = proofs
        try:
            if type(authority) is not VerifiedAuthority or type(currentness) is not VerifiedCurrentness:
                return False
            authority = VerifiedAuthority.model_validate(dict(authority.__dict__))
            currentness = VerifiedCurrentness.model_validate(dict(currentness.__dict__))
        except ValidationError:
            return False
        now = self.clock()
        if now.utcoffset() is None:
            return False
        if snapshot.technical_state != OutboxTechnicalState.DELIVERY_FENCED:
            return False
        if not all((snapshot.claim_ref, snapshot.worker_ref, snapshot.fence_version, snapshot.delivery_ref)):
            return False
        if snapshot.lease_until is None or snapshot.lease_until <= now:
            return False
        if authority.binding != currentness.binding or authority.request_sha256 != currentness.request_sha256:
            return False
        if (authority.authorization_ref, authority.currentness_ref) != (
            currentness.authorization_ref,
            currentness.currentness_ref,
        ):
            return False
        if authority.valid_until <= now or currentness.valid_until <= now:
            return False
        if authority.verified_at > now or currentness.checked_at > now:
            return False
        if authority.binding.operation_name != "notice.prepare_or_send":
            return False
        return all(
            getattr(binding, field) == getattr(authority.binding, field)
            for field in ("principal_ref", "task_ref", "tenant_ref", "legal_entity_ref")
        )

    async def _revalidate_original(
        self,
        binding: JournalBinding,
        snapshot: OutboxSnapshot,
        authorized: ReplyAuthority,
        previous: ReplyAuthority,
        ceiling: datetime,
    ) -> tuple[ReplyAuthority, datetime] | CapabilityRefusalReason:
        """Keep one private authorization/request and all observed validity ceilings."""
        assert self.authority is not None
        # Keep the anchor out of external ports; frozen DTOs still permit unsafe object revival/mutation.
        port_authority = VerifiedAuthority.model_validate(dict(authorized[0].__dict__))
        port_currentness = VerifiedCurrentness.model_validate(dict(previous[1].__dict__))
        updated = await self.authority.revalidate(binding, snapshot, port_authority, port_currentness)
        if (
            isinstance(updated, CapabilityRefusalReason)
            or updated[0] != authorized[0]
            or not self._scope(binding, snapshot, authorized)
            or not self._scope(binding, snapshot, updated)
        ):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        original_current = authorized[1]
        current = updated[1]
        if (
            current.binding != original_current.binding
            or current.authorization_ref != original_current.authorization_ref
            or current.request_sha256 != original_current.request_sha256
            or current.currentness_ref != original_current.currentness_ref
            or current.checked_at < previous[1].checked_at
        ):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        ceiling = min(ceiling, current.valid_until)
        now = self.clock()
        if now.utcoffset() is None or now >= ceiling:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return updated, ceiling

    async def deliver(
        self,
        binding: JournalBinding,
        *,
        outbox_ref: str,
        worker_ref: str,
        lease_until: datetime,
        expected_journal_revision: int,
    ) -> ReplyEvidence | CapabilityRefusalReason:
        if self.journal is None or self.reply is None or self.authority is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        fenced: OutboxSnapshot | None = None
        ack_recorded = False
        try:
            claimed = await self.journal.claim_outbox(
                binding, outbox_ref, worker_ref, lease_until, expected_journal_revision
            )
            if (
                claimed.technical_status
                not in {JournalCallTechnicalStatus.RECORDED, JournalCallTechnicalStatus.UNCHANGED}
                or claimed.snapshot is None
            ):
                return CapabilityRefusalReason.SOURCE_UNAVAILABLE
            claim = claimed.snapshot
            if (
                claim.technical_state != OutboxTechnicalState.CLAIMED
                or claim.claim_ref is None
                or claim.worker_ref != worker_ref
            ):
                return CapabilityRefusalReason.CONTRACT_MISMATCH
            result = await self.journal.begin_outbox_delivery(
                binding, outbox_ref, claim.claim_ref, claim.journal_revision
            )
            if result.technical_status != JournalCallTechnicalStatus.RECORDED or result.snapshot is None:
                return CapabilityRefusalReason.SOURCE_UNAVAILABLE
            fenced = OutboxSnapshot.model_validate(dict(result.snapshot.__dict__))
            if (
                fenced.descriptor != claim.descriptor
                or fenced.claim_ref != claim.claim_ref
                or fenced.worker_ref != worker_ref
                or fenced.fence_version != claim.fence_version
            ):
                return CapabilityRefusalReason.CONTRACT_MISMATCH
            authorized = await self.authority.authorize(binding, fenced)
            if isinstance(authorized, CapabilityRefusalReason) or not self._scope(
                binding, fenced, authorized
            ):
                return CapabilityRefusalReason.AUTHORITY_UNPROVEN
            # Separate copies anchor originally admitted pins even if a port mutates its input DTO.
            authorized = (
                VerifiedAuthority.model_validate(dict(authorized[0].__dict__)),
                VerifiedCurrentness.model_validate(dict(authorized[1].__dict__)),
            )
            ceiling = min(authorized[0].valid_until, authorized[1].valid_until)
            checked = await self._revalidate_original(binding, fenced, authorized, authorized, ceiling)
            if isinstance(checked, CapabilityRefusalReason):
                return checked
            current, ceiling = checked
            delivered = await self.reply.deliver(binding, fenced, *current)
            if isinstance(delivered, CapabilityRefusalReason):
                return delivered
            checked = await self._revalidate_original(binding, fenced, authorized, current, ceiling)
            if isinstance(checked, CapabilityRefusalReason):
                return checked
            current, ceiling = checked
            if type(delivered) is not tuple or len(delivered) != 2:
                return CapabilityRefusalReason.CONTRACT_MISMATCH
            ack, receipt = delivered
            if receipt is not None:
                parsed = parse_result("notice.prepare_or_send", receipt)
                if (
                    not isinstance(parsed, DeliveryEvidenceReceipt)
                    or await self.authority.verify_delivery(binding, fenced, parsed) is not True
                ):
                    return CapabilityRefusalReason.AUTHORITY_UNPROVEN
                receipt = parsed
                checked = await self._revalidate_original(binding, fenced, authorized, current, ceiling)
                if isinstance(checked, CapabilityRefusalReason):
                    return checked
                current, ceiling = checked
            if ack is not None:
                ack = OutboxAckEvidence.model_validate(dict(ack.__dict__))
                if (ack.outbox_ref, ack.delivery_ref, ack.target_binding_ref, ack.payload_sha256) != (
                    fenced.descriptor.outbox_ref,
                    fenced.delivery_ref,
                    fenced.descriptor.target_binding_ref,
                    fenced.descriptor.payload_sha256,
                ) or await self.authority.verify_ack(binding, fenced, ack) is not True:
                    return CapabilityRefusalReason.AUTHORITY_UNPROVEN
                checked = await self._revalidate_original(binding, fenced, authorized, current, ceiling)
                if isinstance(checked, CapabilityRefusalReason):
                    return checked
                current, ceiling = checked
                assert fenced.delivery_ref is not None
                recorded = await self.journal.record_outbox_ack(
                    binding, outbox_ref, fenced.delivery_ref, ack, fenced.journal_revision
                )
                if recorded.technical_status not in {
                    JournalCallTechnicalStatus.RECORDED,
                    JournalCallTechnicalStatus.UNCHANGED,
                }:
                    return CapabilityRefusalReason.SOURCE_UNAVAILABLE
                ack_recorded = True
                checked = await self._revalidate_original(binding, fenced, authorized, current, ceiling)
                if isinstance(checked, CapabilityRefusalReason):
                    return checked
                return ack, receipt
            return None, receipt
        except Exception:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        finally:
            if not ack_recorded and fenced is not None and fenced.delivery_ref is not None:
                # No ACK or failed post-fence step remains reconciliation-only.
                # A failed/duplicate CAS never resets the acknowledged delivery fence.
                with suppress(Exception):
                    await self.journal.mark_outbox_uncertain(
                        binding, outbox_ref, fenced.delivery_ref, fenced.journal_revision
                    )
