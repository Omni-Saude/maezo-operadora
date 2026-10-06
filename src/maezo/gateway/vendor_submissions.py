"""OP16 `channel.submission.submit` — the vendor channel submission machine.

Registry OP16 (`OP-REGISTRY-VENDOR-V1.md` L74-85): the machine stages a channel's formalization
envelope and PROVES back a receipt; it never asserts acceptance or enrollment (C-10), never
answers by notice (PW1-C untouched) and never decides who answers — existing authorities answer
through native case/document-request objects whose kind the vendor audience declares in
`gateway/external_cases/models.py#KINDS`.

Fail-closed by construction, in the posture of the VW1-P0 vendor plane:
- Identity first (`AUTHORITY_UNPROVEN`): the session must carry a published vendor membership
  whose single vendor binding names the commanded channel. The channel never vouches for itself.
- Channel eligibility next: the migration-0019 vendor channel store is the ONLY accredited
  source; an absent store or row is UNKNOWN (`SOURCE_UNAVAILABLE`), never zero; a revoked row
  is `CHANNEL_STATUS_INELIGIBLE`.
- PHI firewall (`PHI_IN_COMMERCIAL_INPUT`): any payload class outside the one declared class is
  classified, audited and refused with ZERO downstream effect — no instance, no store write.
  The health-declaration class is NOT declared (VW0-D16, RATIFY-LATER DPO): it cannot be
  submitted here, by construction.
- Idempotency (NO-DUPLICATE-INSTANCE): business key `tenant + channel_submission +
  submission_ref + business_revision`. Re-send of the same key with the same business content
  returns the SAME active instance; the same key with mutated content is `CONTRACT_MISMATCH`;
  a revision older than the active one is `STALE_REVISION`. Superseded revisions remain as
  history rows and are never deleted.
- Audit (`AUDIT_UNAVAILABLE`): every decision — accept, replay and refusal — is emitted to the
  audit trail BEFORE any store write; an audit trail that cannot answer stops the machine with
  zero downstream effect. The refusal names follow `CapabilityRefusalReason` (PW2-A, imported
  read-only) plus the two OP16-specific members.

The formalization answer needs a vendor formalization SOURCE, which does not exist in this
wave: the internal core publish (VW1-P1, PR #669) landed the capability-cluster publication
ledger, but that seam is internal to the cluster and its export is a VW2 decision, so this
machine does not consume it — `VendorSubmissionMachine.formalization_response` refuses
`SOURCE_UNAVAILABLE` by construction. That is honest inertia, never a stub that answers.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, NoReturn, Protocol

from maezo.gateway.capabilities.models import CONTRACT_STATE, CONTRACT_STATE_PUBLISHED
from maezo.gateway.human.vendor_membership_administration import Closed, VendorChannelState
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.intake import DecimalRevision, ResourceRef
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest
from maezo.portal.contracts.vendor_submissions import (
    DECLARED_PAYLOAD_CLASS,
    ChannelSubmissionCommand,
    ChannelSubmissionReceipt,
    ChannelSubmissionStatus,
    Lifecycle,
    VendorSubmissionRefusalCode,
)

if TYPE_CHECKING:
    from maezo.gateway.audit import AuditRecord

#: Registry OP16 refusal set, verbatim. `CONTRACT_MISMATCH`/`SOURCE_UNAVAILABLE`/
#: `STALE_REVISION`/`AUTHORITY_UNPROVEN`/`AUDIT_UNAVAILABLE` mirror `CapabilityRefusalReason`;
#: the two OP16 members are new and live ONLY in this namespace (the central enum is PW2-A).


class VendorSubmissionRefusal(StrEnum):
    AUTHORITY_UNPROVEN = "AUTHORITY_UNPROVEN"
    CHANNEL_STATUS_INELIGIBLE = "CHANNEL_STATUS_INELIGIBLE"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    STALE_REVISION = "STALE_REVISION"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"


class VendorSubmissionError(PermissionError):
    def __init__(self, reason: VendorSubmissionRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


def require(condition: bool, reason: VendorSubmissionRefusal) -> None:
    if not condition:
        raise VendorSubmissionError(reason)


class SubmissionAbsentError(Exception):
    """The caller's own submission_ref has no instance here.

    Deliberately NOT a registry refusal: "absent" is a read outcome, not a semantic refusal —
    and it must be indistinguishable from "belongs to another channel" on the wire (the route
    answers 404 `resource_unavailable` for both).
    """


#: The only payload class this machine stages. Anything else — declared or not — is clinical or
#: unknown commercial input and hits the firewall (VW0-D16: the health-declaration schema is
#: RATIFY-LATER DPO and is NOT declared here by anyone).
DECLARED_PAYLOAD_CLASSES = frozenset({DECLARED_PAYLOAD_CLASS})

#: The registry OP16 operation name in the business key `tenant + channel_submission +
#: submission_ref + business_revision`; carried explicitly so the stored key names itself.
OPERATION_NAME = "channel_submission"

ACTION = "vendor_submission"


def command_business_bytes(command: ChannelSubmissionCommand) -> bytes:
    """Canonical bytes of the BUSINESS content of one submission attempt.

    `command_id` is attempt identity and is excluded: a retry with a fresh `command_id` and
    identical business content must replay the active instance, not trip the digest guard.
    """
    return json.dumps(
        {
            "channel_ref": command.channel_ref,
            "submission_ref": command.submission_ref,
            "business_revision": command.business_revision,
            "contract_ref": command.contract_ref,
            "payload": command.payload.model_dump(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def command_business_digest(command: ChannelSubmissionCommand) -> str:
    return hashlib.sha256(command_business_bytes(command)).hexdigest()


def submission_id_for(digest: str) -> ResourceRef:
    """Deterministic instance identity derived from the business digest.

    The SAME business key always yields the SAME `submission_id`, so a replay can be proven to
    return the same instance without trusting the caller to echo one.
    """
    return "vsub" + digest[:28]


def _audit_record(
    *, tenant: str, command: ChannelSubmissionCommand, decision: str, reason: str
) -> AuditRecord:
    from maezo.gateway.audit import AuditRecord

    return AuditRecord(
        agent_id=ACTION,
        tenant_id=tenant,
        agent_version="op16.v1",
        action=ACTION,
        decision=decision,
        details={
            "operation": OPERATION_NAME,
            "channel_ref": command.channel_ref,
            "submission_ref": command.submission_ref,
            "business_revision": command.business_revision,
            "contract_ref": command.contract_ref,
            "reason": reason,
        },
    )


class VendorChannelStore(Protocol):
    """Read-only view of the migration-0019 store; the absence of a row is UNKNOWN, not zero.

    Same shape as `vendor_membership_administration.VendorChannelStore` — redeclared as this
    module's seam so the machine depends on the view, not on the administration module.
    """

    async def channel(self, tenant: str, channel_ref: str) -> VendorChannelState | None: ...


class SubmissionAuditTrail(Protocol):
    """The audit seam; `PostgresAuditSink.emit` satisfies it. Failure refuses, never skips."""

    async def emit(self, record: AuditRecord) -> str: ...


class SubmissionStore(Protocol):
    """The durable instance store; migration `0020_vendor_channel_submissions` holds the DDL.

    `find` returns every stored instance of the submission_ref (superseded revisions included);
    `record` commits one instance and MUST enforce the business-key uniqueness (PRIMARY KEY
    `(tenant, operation, submission_ref, business_revision)` in the migration) — a second
    instance for the same key is a storage-level refusal, never an overwrite.
    """

    async def find(self, tenant: str, submission_ref: str) -> tuple[StoredSubmission, ...]: ...

    async def record(self, submission: StoredSubmission) -> None: ...


class StoredSubmission(Closed):
    """One committed submission instance; lifecycle fields mirror the receipt contract."""

    tenant: OpaqueRef
    submission_id: ResourceRef
    channel_ref: ResourceRef
    submission_ref: ResourceRef
    business_revision: DecimalRevision
    contract_ref: ResourceRef
    payload_class: str
    command_digest: Sha256Digest
    lifecycle: Lifecycle
    refusal_code: VendorSubmissionRefusalCode | None = None
    case_ref: ResourceRef | None = None
    document_request_ref: ResourceRef | None = None
    created_at: datetime

    def receipt(self, command_id: ResourceRef) -> ChannelSubmissionReceipt:
        return ChannelSubmissionReceipt(
            command_id=command_id,
            channel_ref=self.channel_ref,
            submission_ref=self.submission_ref,
            business_revision=self.business_revision,
            submission_id=self.submission_id,
            lifecycle=self.lifecycle,
            refusal_code=self.refusal_code,
            case_ref=self.case_ref,
            document_request_ref=self.document_request_ref,
        )

    def status(self) -> ChannelSubmissionStatus:
        return ChannelSubmissionStatus(
            submission_id=self.submission_id,
            channel_ref=self.channel_ref,
            submission_ref=self.submission_ref,
            business_revision=self.business_revision,
            contract_ref=self.contract_ref,
            lifecycle=self.lifecycle,
            refusal_code=self.refusal_code,
            case_ref=self.case_ref,
            document_request_ref=self.document_request_ref,
        )


def _proven_channel_ref(record: MembershipRecord) -> str | None:
    """The single vendor binding of a PUBLISHED vendor membership, or None.

    A revoked record is not a published membership — the session resolver refuses it before
    the machine would ever see it; refusing here too is defense in depth, not a second truth.
    """
    if record.audience != "vendor" or record.revoked:
        return None
    bindings = [b for b in record.subject_bindings if b.kind == "vendor"]
    if len(bindings) != 1 or len(record.subject_bindings) != 1:
        return None
    return bindings[0].resource_ref


class VendorSubmissionMachine:
    """Stages channel submissions; answers with receipts; decides nothing about acceptance."""

    def __init__(
        self,
        *,
        channels: VendorChannelStore | None,
        store: SubmissionStore | None,
        audit: SubmissionAuditTrail | None,
    ) -> None:
        self.channels, self.store, self.audit = channels, store, audit

    async def submit(
        self,
        tenant: str,
        record: MembershipRecord,
        command: ChannelSubmissionCommand,
    ) -> ChannelSubmissionReceipt:
        # Authority first: a vendor membership whose ONE binding names the commanded channel.
        channel_ref = _proven_channel_ref(record)
        require(
            record.tenant == tenant and channel_ref is not None and channel_ref == command.channel_ref,
            VendorSubmissionRefusal.AUTHORITY_UNPROVEN,
        )
        # The PHI firewall decides BEFORE any source is consulted and BEFORE any effect: a
        # clinical or unknown class is classified, audited and refused with zero downstream
        # effect — and never learns anything about channel state.
        if command.payload.content_class not in DECLARED_PAYLOAD_CLASSES:
            await self._emit(tenant, command, decision="DENY", reason="PHI_IN_COMMERCIAL_INPUT")
            raise VendorSubmissionError(VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT)
        # Channel eligibility from the ONLY accredited source; absent = unknown, never zero.
        require(self.channels is not None, VendorSubmissionRefusal.SOURCE_UNAVAILABLE)
        assert self.channels is not None
        channel = await self.channels.channel(tenant, command.channel_ref)
        require(channel is not None, VendorSubmissionRefusal.SOURCE_UNAVAILABLE)
        assert channel is not None
        require(channel.tenant == tenant, VendorSubmissionRefusal.CONTRACT_MISMATCH)
        require(channel.status == "active", VendorSubmissionRefusal.CHANNEL_STATUS_INELIGIBLE)
        # Idempotency over the named business key — NO-DUPLICATE-INSTANCE.
        require(self.store is not None, VendorSubmissionRefusal.SOURCE_UNAVAILABLE)
        assert self.store is not None
        digest_value = command_business_digest(command)
        stored = await self.store.find(tenant, command.submission_ref)
        for instance in stored:
            if instance.business_revision != command.business_revision:
                continue
            require(
                instance.command_digest == digest_value and instance.channel_ref == command.channel_ref,
                VendorSubmissionRefusal.CONTRACT_MISMATCH,
            )
            # The audit chain's decision vocabulary is the PEP's closed set; the replay is an
            # ALLOW whose reason names the idempotent outcome — never a new decision value.
            await self._emit(tenant, command, decision="ALLOW", reason="replayed_active_instance")
            return instance.receipt(command.command_id)
        if any(
            int(instance.business_revision) > int(command.business_revision)
            for instance in stored
            if instance.channel_ref == command.channel_ref
        ):
            await self._emit(tenant, command, decision="DENY", reason="STALE_REVISION")
            raise VendorSubmissionError(VendorSubmissionRefusal.STALE_REVISION)
        # Audit BEFORE any write: an audit trail that cannot answer stops the machine cold.
        await self._emit(tenant, command, decision="ALLOW", reason="received")
        instance = StoredSubmission(
            tenant=OpaqueRef(tenant),
            submission_id=submission_id_for(digest_value),
            channel_ref=command.channel_ref,
            submission_ref=command.submission_ref,
            business_revision=command.business_revision,
            contract_ref=command.contract_ref,
            payload_class=command.payload.content_class,
            command_digest=digest_value,
            lifecycle="received",
            refusal_code=None,
            case_ref=None,
            document_request_ref=None,
            created_at=datetime.now(UTC),
        )
        try:
            await self.store.record(instance)
        except Exception:
            raise VendorSubmissionError(VendorSubmissionRefusal.SOURCE_UNAVAILABLE) from None
        return instance.receipt(command.command_id)

    async def read(
        self, tenant: str, record: MembershipRecord, submission_ref: str
    ) -> ChannelSubmissionStatus:
        """The caller's own instance state; a foreign or absent submission is indistinguishable."""
        channel_ref = _proven_channel_ref(record)
        require(
            record.tenant == tenant and channel_ref is not None,
            VendorSubmissionRefusal.AUTHORITY_UNPROVEN,
        )
        require(self.store is not None, VendorSubmissionRefusal.SOURCE_UNAVAILABLE)
        assert self.store is not None
        stored = await self.store.find(tenant, submission_ref)
        own = [instance for instance in stored if instance.channel_ref == channel_ref]
        if not own:
            raise SubmissionAbsentError(submission_ref)
        return max(own, key=lambda instance: int(instance.business_revision)).status()

    async def formalization_response(
        self, tenant: str, record: MembershipRecord, submission_ref: str
    ) -> NoReturn:
        """The operadora's formalization answer over one submission — NOT ANSWERED in this wave.

        By construction: no vendor formalization source exists at all. The internal core
        publish (VW1-P1, PR #669) landed the capability-cluster publication ledger, but that
        seam is internal to the cluster — its export is a VW2 decision — so this machine does
        NOT consume it and refuses `SOURCE_UNAVAILABLE` unconditionally. When the vendor
        formalization source is wired, the tripwire test in this package forces that wave to
        replace this body; the branch below is the ONLY place a published core would ever be
        consulted.
        """
        channel_ref = _proven_channel_ref(record)
        require(
            record.tenant == tenant and channel_ref is not None,
            VendorSubmissionRefusal.AUTHORITY_UNPROVEN,
        )
        if CONTRACT_STATE != CONTRACT_STATE_PUBLISHED:
            raise VendorSubmissionError(VendorSubmissionRefusal.SOURCE_UNAVAILABLE)
        raise VendorSubmissionError(VendorSubmissionRefusal.SOURCE_UNAVAILABLE)

    async def _emit(
        self, tenant: str, command: ChannelSubmissionCommand, *, decision: str, reason: str
    ) -> None:
        require(self.audit is not None, VendorSubmissionRefusal.AUDIT_UNAVAILABLE)
        assert self.audit is not None
        try:
            await self.audit.emit(
                _audit_record(tenant=tenant, command=command, decision=decision, reason=reason)
            )
        except VendorSubmissionError:
            raise
        except Exception:
            raise VendorSubmissionError(VendorSubmissionRefusal.AUDIT_UNAVAILABLE) from None
