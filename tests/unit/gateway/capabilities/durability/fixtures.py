"""Synthetic UNIT/local-PG storage inputs; never a qualified source, policy or provider.

These explicit proof doubles exercise how the journal consumes gates. Their refs
do not grant access, mint receipts or prove administrative journey acceptance.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from maezo.gateway.capabilities.admission import (
    AdmissionBinding,
    VerifiedAuthority,
    VerifiedCurrentness,
    VerifiedSourceResult,
    result_digest,
)
from maezo.gateway.capabilities.durability.models import (
    CommandDescriptor,
    DispatchEvidence,
    JournalBinding,
    OutboxDescriptor,
    OutboxTechnicalKind,
    VerifiedInboxObservation,
    VerifiedResultObservation,
    WaitDescriptor,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CapabilityEnvelope,
    ExternalWaitIntent,
    ProviderReservationReceipt,
    ReservationCommand,
    request_digest,
)
from maezo.portal.engine.profile import canonicalize


def binding(tenant="unit_tenant", **changes):
    return JournalBinding(
        **dict(
            environment_ref="unit-environment",
            tenant_ref=tenant,
            legal_entity_ref="unit-entity",
            journey_ref="unit-journey",
            principal_ref="lucas",
            task_ref="unit-task",
            data_policy_ref="unit-protected-metadata-policy",
        )
        | changes
    )


def admission(b):
    return AdmissionBinding(
        principal_ref=b.principal_ref,
        task_ref=b.task_ref,
        tenant_ref=b.tenant_ref,
        legal_entity_ref=b.legal_entity_ref,
        purpose_ref="unit-purpose",
        operation_name="reservation.command",
        schema_version=CANDIDATE_SCHEMA_VERSION,
        contract_revision="unit-contract",
        source_authority_ref="unit-authority",
        policy_revision="unit-policy",
        data_classification="unit-protected-refs",
        autonomy_action="scheduling",
        security_zone="general",
    )


def descriptor(b, key="unit-literal-key", **changes):
    env = CapabilityEnvelope(
        schema_version=CANDIDATE_SCHEMA_VERSION,
        operation_name="reservation.command",
        tenant_ref=b.tenant_ref,
        legal_entity_ref=b.legal_entity_ref,
        journey_ref=b.journey_ref,
        correlation_ref="unit-correlation",
        causation_ref="unit-cause",
        idempotency_key=key,
        expected_business_revision="source:rev/opaque-Z",
        source_authority_ref="unit-authority",
        policy_revision="unit-policy",
        data_classification="unit-protected-refs",
    )
    req = ReservationCommand(
        provider_ref="unit-provider",
        operation="hold",
        offer_or_booking_ref="unit-offer",
        expected_provider_revision="source:rev/opaque-Z",
        customer_commitment_ref="unit-commitment",
    )
    values = dict(
        envelope=env,
        request_ref="unit-protected-request",
        request_sha256=request_digest(env, req),
        admission_binding_sha256=hashlib.sha256(
            canonicalize(admission(b).model_dump(mode="json"))
        ).hexdigest(),
        predecessor_command_refs=(),
    )
    return CommandDescriptor(**values | changes)


def dispatch_evidence(b, d):
    now = datetime.now(UTC)
    authority = VerifiedAuthority(
        binding=admission(b),
        request_sha256=d.request_sha256,
        authorization_ref="unit-authenticated-authority",
        signed_task_ref="unit-signed-task",
        agent_card_sha256="a" * 64,
        source_contract_publication_ref="unit-publication",
        policy_ratification_ref="unit-ratification",
        currentness_ref="unit-currentness",
        enforcement="enforcing",
        decision="allow",
        verified_at=now,
        valid_until=now + timedelta(minutes=5),
    )
    current = VerifiedCurrentness(
        binding=authority.binding,
        request_sha256=d.request_sha256,
        authorization_ref=authority.authorization_ref,
        currentness_ref=authority.currentness_ref,
        checked_at=now,
        valid_until=authority.valid_until,
    )
    return DispatchEvidence(
        authority=authority,
        currentness=current,
        audit_receipt_sha256="b" * 64,
        audit_intent_ref="unit-durable-audit-intent",
    )


def observation(b, d, revision="source:opaque-A", status="pending", witness=None, digest=None):
    now = datetime.now(UTC)
    result = ProviderReservationReceipt(reservation_status=status, provider_revision=revision)
    sha = digest or result_digest(result)
    source = VerifiedSourceResult(
        binding=admission(b),
        request_sha256=d.request_sha256,
        result_sha256=sha,
        authorization_ref="unit-read-authority",
        source_receipt_ref="unit-receipt/" + revision,
        source_revision_ref=revision,
        currentness_ref="unit-currentness",
        checked_at=now,
        valid_until=now + timedelta(minutes=5),
    )
    return VerifiedResultObservation(
        source_result=source,
        result_ref="unit-protected-result/" + revision,
        result_sha256=sha,
        observer_binding_ref="unit-source-verifier",
        provenance_ref="unit-provenance/" + revision,
        source_revision_order_witness_ref=witness,
    )


def inbox(handle, obs, event="unit-event", **changes):
    return VerifiedInboxObservation(
        **dict(
            event_ref=event,
            source_authority_ref="unit-authority",
            producer_ref="unit-producer",
            source_contract_revision_ref="unit-contract",
            event_sha256="e" * 64,
            handle=handle,
            correlation_ref="unit-correlation",
            observation=obs,
        )
        | changes
    )


def wait(handle, ref="unit-wait", wakeup=None):
    return WaitDescriptor(
        handle=handle,
        operational_wakeup_at=wakeup,
        intent=ExternalWaitIntent(
            wait_ref=ref,
            expected_producer_ref="unit-producer",
            correlation_ref="unit-correlation",
            authoritative_deadline_ref="unit-source-deadline",
        ),
    )


def outbox(handle, ref="unit-outbox", **changes):
    return OutboxDescriptor(
        **dict(
            outbox_ref=ref,
            command_ref=handle.command_ref,
            kind=OutboxTechnicalKind.RETURN_INTENT,
            target_binding_ref="unit-registered-target",
            payload_ref="unit-protected-continuation",
            payload_sha256="c" * 64,
            causation_ref="unit-cause",
            dedupe_identity_ref="unit-return-identity/" + ref,
        )
        | changes
    )


class SyntheticProofs:
    """Test-only authorization/ordering oracles, explicitly not a provider implementation."""

    def __init__(self):
        self.allowed = True
        self.denied = set()
        self.calls = []
        self.order = set()
        self.revoke_after_result_checks = None
        self.result_checks = 0

    def ok(self, method):
        self.calls.append(method)
        return self.allowed and method not in self.denied

    async def authorize(self, b, method):
        return self.ok("authorize:" + method)

    async def verify_command(self, b, d):
        return self.ok("command")

    async def verify_dispatch(self, b, d, evidence):
        return self.ok("dispatch")

    async def verify_result(self, b, d, dispatch, previous, candidate):
        self.result_checks += 1
        if self.revoke_after_result_checks == self.result_checks:
            self.allowed = False
        if not self.ok("result"):
            return False
        if (
            previous
            and previous.source_result.source_revision_ref != candidate.source_result.source_revision_ref
        ):
            return (
                previous.source_result.source_revision_ref,
                candidate.source_result.source_revision_ref,
                candidate.source_revision_order_witness_ref,
            ) in self.order
        return True

    async def verify_inbox(self, b, d, obs):
        return (
            self.ok("inbox")
            and obs.producer_ref == "unit-producer"
            and obs.source_contract_revision_ref == "unit-contract"
        )

    async def verify_wait(self, b, d, obs):
        return (
            self.ok("wait")
            and d.intent.expected_producer_ref == "unit-producer"
            and (obs is None or obs.producer_ref == d.intent.expected_producer_ref)
        )

    async def verify_outbox(self, b, d, worker, evidence):
        return (
            self.ok("outbox")
            and d.target_binding_ref == "unit-registered-target"
            and (worker is None or worker.startswith("unit-worker"))
            and (evidence is None or evidence.verifier_binding_ref == "unit-ack-verifier")
        )

    async def verify_clock(self, b, observed):
        return self.ok("clock") and observed <= datetime.now(UTC)
