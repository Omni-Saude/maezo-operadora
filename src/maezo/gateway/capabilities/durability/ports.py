"""Frozen internal DUR0 interface and independently qualified proof dependencies.

No implementations or grants of data/source/transport authority are supplied.
Proof methods must authenticate/authorize real protected material, fail closed
on unavailable/revoked/erased evidence, and remain current across awaited work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    CommandDescriptor,
    CommandHandle,
    CommandSnapshot,
    DispatchEvidence,
    DispatchToken,
    JournalBinding,
    JournalCallResult,
    JourneySnapshot,
    OutboxAckEvidence,
    OutboxDescriptor,
    OutboxSnapshot,
    PreDispatchRefusal,
    RecoverySnapshot,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
    WaitSnapshot,
)


class DurabilityJournalPort(Protocol):
    async def record_command(
        self, binding: JournalBinding, descriptor: CommandDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def observe_command(
        self, binding: JournalBinding, handle: CommandHandle
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def observe_journey(self, binding: JournalBinding) -> JournalCallResult[JourneySnapshot]: ...

    async def begin_dispatch(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        evidence: DispatchEvidence,
        expected_journal_revision: int,
    ) -> JournalCallResult[DispatchToken]: ...

    async def record_pre_dispatch_refusal(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        refusal: PreDispatchRefusal,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def mark_uncertain(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        dispatch_ref: str,
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def record_verified_result(
        self,
        binding: JournalBinding,
        handle: CommandHandle,
        dispatch_ref: str,
        observation: VerifiedResultObservation,
        outbox_intents: tuple[OutboxDescriptor, ...],
        wait_intents: tuple[WaitDescriptor, ...],
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def ingest_verified_observation(
        self,
        binding: JournalBinding,
        observation: VerifiedInboxObservation,
        wait_ref: str | None,
        outbox_intents: tuple[OutboxDescriptor, ...],
        wait_intents: tuple[WaitDescriptor, ...],
        expected_journal_revision: int,
    ) -> JournalCallResult[CommandSnapshot]: ...

    async def record_wait(
        self, binding: JournalBinding, descriptor: WaitDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[WaitSnapshot]: ...

    async def mark_wait_elapsed(
        self, binding: JournalBinding, wait_ref: str, observed_at: datetime, expected_journal_revision: int
    ) -> JournalCallResult[WaitSnapshot]: ...

    async def enqueue_outbox(
        self, binding: JournalBinding, descriptor: OutboxDescriptor, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]: ...

    async def claim_outbox(
        self,
        binding: JournalBinding,
        outbox_ref: str,
        worker_ref: str,
        lease_until: datetime,
        expected_journal_revision: int,
    ) -> JournalCallResult[OutboxSnapshot]: ...

    async def begin_outbox_delivery(
        self, binding: JournalBinding, outbox_ref: str, claim_ref: str, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]: ...

    async def record_outbox_ack(
        self,
        binding: JournalBinding,
        outbox_ref: str,
        delivery_ref: str,
        evidence: OutboxAckEvidence,
        expected_journal_revision: int,
    ) -> JournalCallResult[OutboxSnapshot]: ...

    async def mark_outbox_uncertain(
        self, binding: JournalBinding, outbox_ref: str, delivery_ref: str, expected_journal_revision: int
    ) -> JournalCallResult[OutboxSnapshot]: ...

    async def recover(
        self, binding: JournalBinding, limit: int, cursor_ref: str | None
    ) -> JournalCallResult[RecoverySnapshot]: ...


class JournalProofPort(Protocol):
    """Owner-qualified current proofs; construction/presence is insufficient.

    Each method must return literal True only on current authenticated evidence.
    authorize covers metadata privacy, custody, retention, erasure, signed task and
    scope for this exact call; verify_result covers source proof and a replacement
    ordering witness bound to both heads, never lexical/local ordering. verify_wait
    authenticates the original producer/correlation and published settlement mapping.
    verify_outbox binds registered target/payload/worker and authenticated ACK.
    Revalidation occurs in the tenant TX after I/O before commit; no fake defaults.
    """

    async def authorize(self, binding: JournalBinding, method: str) -> bool: ...

    async def verify_command(self, binding: JournalBinding, descriptor: CommandDescriptor) -> bool: ...

    async def verify_dispatch(
        self, binding: JournalBinding, descriptor: CommandDescriptor, evidence: DispatchEvidence
    ) -> bool: ...

    async def verify_result(
        self,
        binding: JournalBinding,
        descriptor: CommandDescriptor,
        dispatch_ref: str,
        previous: VerifiedResultObservation | None,
        candidate: VerifiedResultObservation,
    ) -> bool: ...

    async def verify_inbox(
        self, binding: JournalBinding, descriptor: CommandDescriptor, observation: VerifiedInboxObservation
    ) -> bool: ...

    async def verify_wait(
        self,
        binding: JournalBinding,
        descriptor: WaitDescriptor,
        observation: VerifiedInboxObservation | None,
    ) -> bool: ...

    async def verify_outbox(
        self,
        binding: JournalBinding,
        descriptor: OutboxDescriptor,
        worker_ref: str | None,
        evidence: OutboxAckEvidence | None,
    ) -> bool: ...

    async def verify_clock(self, binding: JournalBinding, observed_at: datetime) -> bool: ...
