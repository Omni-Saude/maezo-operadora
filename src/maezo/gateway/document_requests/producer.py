"""PHI worker orchestration with sealed commands and receipt-only uncertain recovery.

Contract: E04 occurrence/inbox B1/B2/B1-R1 sections 3–6. Authority sources are
mandatory qualified deployment inputs; this module does not manufacture policy.
"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import Field
from sqlalchemy import text

from maezo.gateway.communications.models import alive
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.auth_profile import DocumentPolicy, InputPublication, PublicationReceipt
from maezo.gateway.human.auth_publisher import AuthInputPublisher, AuthPublicationSnapshot
from maezo.gateway.human.read_profile import ArtifactPin, digest, parse_model, wire
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.gateway.native_fetch.adapter import AcquiredInputs
from maezo.gateway.native_fetch.models import decode, sha
from maezo.portal.engine.profile import canonicalize

from .admission import SystemPublisher, lock_key, one
from .completion import CompletionClient, CompletionOutcome, PreparedCompletion
from .content import AuthenticatedBodyReceipt, PhiRequestContent
from .models import (
    NOTICE,
    DeliveryEntry,
    DeliveryRecord,
    ExistingDelivery,
    NewDelivery,
    PolicyAssessmentVersion,
    Private,
    ProducerContext,
    RequestBodyCommand,
    RequestDeliveryCommand,
    RequestIdentity,
    RequestInboxReceipt,
    SystemAccess,
    SystemGrant,
    SystemProducer,
    SystemRecipient,
    assessment,
    child_id,
    recipient_digest,
    require,
    selector,
)
from .postgres import RequestInboxStore
from .transport import ProducerAcquisition, ProducerContextClient, ProducerObservation


class NoticePlan(Private):
    """Source-selected crosswalk and historical commands, not a permission token."""

    template: ArtifactPin
    recipients: tuple[SystemRecipient, ...] = Field(min_length=1, max_length=256)
    prior_commands: tuple[RequestDeliveryCommand, ...] = Field(max_length=256)


class DocumentRequestPolicySource(ABC):
    @abstractmethod
    async def policy(
        self, observation: ProducerObservation, original: InputPublication | None
    ) -> AuthPublicationSnapshot:
        """Restore the exact original durable source freeze if already sealed."""
        raise NotImplementedError

    @abstractmethod
    async def notice(
        self, observation: ProducerObservation, policy: DocumentPolicy, receipt: PublicationReceipt
    ) -> NoticePlan:
        """Read the qualified template/crosswalk and original historical commands.

        Actual system grants independently revalidate this selection. No raw
        required-code to document-type or recipient relationship inference.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class JournalEntry:
    payload: dict[str, Any]
    state: str


class ProducerJournal:
    """Actual protected DB journal; immutable payloads, single send claim per stage."""

    def __init__(
        self, protected: PostgresAuthDispatchStore, producer: SystemProducer, current: Callable[[], None]
    ) -> None:
        require(protected.tenant == producer.tenant, "unavailable")
        self.protected, self.producer, self.current = protected, producer, current

    def _params(self, command: str, kind: str) -> dict[str, Any]:
        require(kind != "phi-body" and len(kind) <= 160)
        return dict(
            tenant=self.producer.tenant,
            environment=self.producer.environment,
            sender=self.producer.identity_digest,
            command=command,
            kind=kind,
        )

    def _decode(self, row: Any, p: dict[str, Any]) -> JournalEntry:
        payload = self.protected.unseal(
            "document-request-worker", digest(p), row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"])
        )
        require(type(payload) is dict and digest(payload) == row["payload_digest"], "conflict")
        return JournalEntry(payload, row["state"])

    async def read(self, command: str, kind: str) -> JournalEntry | None:
        self.current()
        p = self._params(command, kind)
        async with transaction(self.protected.engine, self.protected.seconds) as c:
            row = await one(
                c,
                "SELECT * FROM portal_document_request.producer_journal WHERE tenant=:tenant "
                "AND environment=:environment AND sender_identity_digest=:sender AND "
                "command_id=:command AND kind=:kind",
                p,
            )
            result = None if row is None else self._decode(row, p)
            self.current()
        self.current()
        return result

    async def seal(
        self, command: str, kind: str, payload: dict[str, Any], *, select_once: bool = False
    ) -> JournalEntry:
        self.current()
        p = self._params(command, kind)
        require(len(canonicalize(wire(payload))) <= 262144, "unavailable")
        nonce, ciphertext = self.protected.seal("document-request-worker", digest(p), payload)
        async with transaction(self.protected.engine, self.protected.seconds) as c:
            await lock_key(c, p)
            row = await one(
                c,
                "SELECT * FROM portal_document_request.producer_journal WHERE tenant=:tenant "
                "AND environment=:environment AND sender_identity_digest=:sender AND "
                "command_id=:command AND kind=:kind FOR UPDATE",
                p,
            )
            if row is None:
                await c.execute(
                    text(
                        "INSERT INTO portal_document_request.producer_journal "
                        "(tenant,environment,sender_identity_digest,command_id,kind,key_id,"
                        "nonce,ciphertext,payload_digest,state) "
                        "VALUES "
                        "(:tenant,:environment,:sender,:command,:kind,:key,:nonce,:ciphertext,"
                        ":digest,'sealed')"
                    ),
                    dict(
                        p,
                        key=self.protected.key_id,
                        nonce=nonce,
                        ciphertext=ciphertext,
                        digest=digest(payload),
                    ),
                )
                result = JournalEntry(wire(payload), "sealed")
            else:
                result = self._decode(row, p)
                require(select_once or result.payload == wire(payload), "conflict")
            self.current()
        self.current()
        return result

    async def claim_send(self, command: str, kind: str) -> bool:
        """False includes uncertainty: only the committed sealed→possible winner sends."""
        self.current()
        p = self._params(command, kind)
        async with transaction(self.protected.engine, self.protected.seconds) as c:
            row = await one(
                c,
                "UPDATE portal_document_request.producer_journal SET state='possibly_sent' "
                "WHERE tenant=:tenant AND environment=:environment AND sender_identity_digest=:sender "
                "AND command_id=:command AND kind=:kind AND state='sealed' RETURNING payload_digest",
                p,
            )
            won = row is not None
            self.current()
        self.current()
        return won

    async def acknowledge(self, command: str, kind: str, receipt: object) -> None:
        # Receipt commits first. Losing its acknowledgement cannot permit a send.
        await self.seal(command, kind + "-receipt", {"receipt": wire(receipt)})
        self.current()
        p = self._params(command, kind)
        async with transaction(self.protected.engine, self.protected.seconds) as c:
            row = await one(
                c,
                "UPDATE portal_document_request.producer_journal SET state='acknowledged' "
                "WHERE tenant=:tenant AND environment=:environment AND sender_identity_digest=:sender "
                "AND command_id=:command AND kind=:kind AND state IN "
                "('possibly_sent','acknowledged') RETURNING payload_digest",
                p,
            )
            require(row is not None, "conflict")
            self.current()
        self.current()


@dataclass(frozen=True, slots=True, repr=False)
class ProducerResult:
    inbox: RequestInboxReceipt
    completion: CompletionOutcome


@dataclass(frozen=True, slots=True, repr=False)
class VersionPins:
    version: PolicyAssessmentVersion

    def __getattr__(self, key: str) -> Any:
        if key == "assessment_version_digest":
            return digest(self.version)
        return getattr(self.version, key)


class DocumentRequestProducer:
    def __init__(
        self,
        *,
        producer: SystemProducer,
        context: ProducerContextClient,
        policy_source: DocumentRequestPolicySource,
        policy_publisher: AuthInputPublisher,
        phi_publisher: SystemPublisher,
        inbox_publisher: SystemPublisher,
        content: PhiRequestContent,
        inbox: RequestInboxStore,
        journal: ProducerJournal,
        completion: CompletionClient,
    ) -> None:
        require(isinstance(policy_source, DocumentRequestPolicySource), "unavailable")
        require(
            producer == journal.producer
            and content.admission.scope == inbox.admission.scope
            and producer.tenant == content.admission.scope.tenant
            and producer.environment == content.admission.scope.environment,
            "unavailable",
        )
        self.producer, self.context, self.source, self.policy_publisher = (
            producer,
            context,
            policy_source,
            policy_publisher,
        )
        self.phi_publisher, self.inbox_publisher = phi_publisher, inbox_publisher
        self.content, self.inbox, self.journal, self.completion = content, inbox, journal, completion

    def _original_producer(self, value: object) -> None:
        original = parse_model(SystemProducer, value)
        # Current installation revision/receipt may change; immutable sender lineage cannot.
        require(
            all(
                getattr(original, k) == getattr(self.producer, k)
                for k in ("producer_ref", "issuer", "subject", "tenant", "environment", "identity_digest")
            ),
            "conflict",
        )

    def _access(
        self,
        operation: Any,
        command_id: str,
        payload_digest: str,
        context_digest: str,
        request: RequestIdentity,
        version: VersionPins | RequestDeliveryCommand,
    ) -> SystemAccess:
        return SystemAccess(
            scope=request.communication_scope(),
            producer=self.producer,
            operation=operation,
            request=request,
            context_query_digest=context_digest,
            command_id=command_id,
            request_digest=payload_digest,
            **{
                k: getattr(version, k)
                for k in (
                    "request_revision",
                    "policy_ref",
                    "policy_digest",
                    "policy_publication_ref",
                    "policy_publication_digest",
                    "assessment_version_digest",
                )
            },
        )

    async def _grant(self, access: SystemAccess, *, phi: bool) -> SystemGrant:
        publisher = self.phi_publisher if phi else self.inbox_publisher
        admission = self.content.admission if phi else self.inbox.admission
        # The source journals/verifies the exact publication before its own send.
        receipt = await publisher.publish(access)
        await self.journal.seal(
            access.command_id,
            "authority-" + digest(receipt),
            {"access": wire(access), "receipt": wire(receipt)},
        )
        return await admission.authorize(access)

    def _command_access(self, operation: Any, command: RequestDeliveryCommand) -> SystemAccess:
        return self._access(
            operation,
            command.command_id,
            digest(command),
            command.context_query_digest,
            command.request,
            command,
        )

    async def _read_inbox(self, command: RequestDeliveryCommand) -> tuple[RequestInboxReceipt, SystemGrant]:
        grant = await self._grant(self._command_access("read_request_receipt", command), phi=False)
        return await self.inbox.read_receipt(grant, command.sender_identity_digest, grant.ceiling()), grant

    async def run(self, inputs: AcquiredInputs) -> ProducerResult:
        """Only authentic selected native inputs enter new work; restart uses recover()."""
        acquired = ProducerAcquisition(inputs)
        observed = await self.context.observe(acquired)
        observed.current()
        seed = await self.journal.seal(
            observed.context.request_ref,
            "occurrence",
            {
                "outer_command_id": uuid4().hex,
                "query": base64.b64encode(observed.query).decode(),
                "result": base64.b64encode(observed.result).decode(),
                "request": wire(observed.context.identity()),
                "producer": wire(self.producer),
            },
            select_once=True,
        )
        require(
            seed.payload["request"] == wire(observed.context.identity()),
            "conflict",
        )
        self._original_producer(seed.payload["producer"])
        outer = seed.payload["outer_command_id"]
        original_query = decode(base64.b64decode(seed.payload["query"], validate=True))
        snapshot_binding = decode(acquired.snapshot)
        same_acquisition = (
            original_query["fetch_command"] == decode(acquired.expected.binding)
            and original_query["resource_ref"] == decode(acquired.row)["id"]
            and original_query["resource_acquisition"]
            == {
                "acquisition_ref": snapshot_binding["acquisition_ref"],
                "lease_revision": snapshot_binding["lease_revision"],
            }
        )
        delivery = await self.journal.read(outer, "delivery")
        if delivery is not None:
            return await self._recover(observed.context.request_ref, acquired if same_acquisition else None)
        existing_plan = await self.journal.read(outer, "plan")
        if existing_plan is not None:
            return await self._deliver(outer, existing_plan.payload, acquired if same_acquisition else None)
        require(same_acquisition, "uncertain")
        original = await self.journal.read(outer, "policy")
        snapshot = await self.source.policy(
            observed,
            None if original is None else parse_model(InputPublication, original.payload["publication"]),
        )
        require(snapshot.publication.kind == "document_policy" and snapshot.publication.state == "active")
        policy = snapshot.publication.payload
        require(isinstance(policy, DocumentPolicy) and policy.submitted_response_digest is None)
        assert isinstance(policy, DocumentPolicy)
        await self.journal.seal(outer, "policy", {"publication": wire(snapshot.publication)})
        receipt = await self.policy_publisher.publish(snapshot)
        await self.journal.seal(outer, "policy-receipt", {"receipt": wire(receipt)})
        attached = await self.context.observe(acquired)
        require(
            attached.context.identity() == observed.context.identity()
            and attached.context.policy_ref == policy.assessment_ref
            and attached.context.policy_digest == digest(policy)
            and attached.context.request_revision == str(policy.request_revision),
            "conflict",
        )
        version = assessment(attached.context.identity(), policy, receipt)
        prior = await self.journal.read(outer, "plan")
        if prior is None:
            plan = await self.source.notice(attached, policy, receipt)
            ids = [r.identity_digest for r in plan.recipients]
            require(
                ids == sorted(set(ids))
                and len({r.principal_ref for r in plan.recipients}) == len(ids)
                and {r.principal_ref for r in plan.recipients} == set(policy.recipient_principal_refs)
            )
            prior = await self.journal.seal(
                outer,
                "plan",
                {
                    "plan": wire(plan),
                    "version": wire(version),
                    "context": wire(attached.context),
                    "context_query_digest": sha(attached.query),
                    "query": base64.b64encode(attached.query).decode(),
                    "result": base64.b64encode(attached.result).decode(),
                },
                select_once=True,
            )
        # A subsequent live query never rebinds any sealed body to newer assessment bytes.
        return await self._deliver(outer, prior.payload, acquired)

    async def _deliver(
        self, outer: str, payload: dict[str, Any], acquired: ProducerAcquisition | None
    ) -> ProducerResult:
        plan = parse_model(NoticePlan, payload["plan"])
        context = parse_model(ProducerContext, payload["context"])
        version = parse_model(PolicyAssessmentVersion, payload["version"])
        require(version.request_identity_digest == digest(context.identity()))
        sealed_query = base64.b64decode(payload["query"], validate=True)
        sealed_result = decode(base64.b64decode(payload["result"], validate=True))
        require(
            sha(sealed_query) == payload["context_query_digest"] == sealed_result["query_digest"]
            and sealed_result["context"] == wire(context),
            "conflict",
        )
        ids = [r.identity_digest for r in plan.recipients]
        require(ids == sorted(set(ids)))
        historical: dict[str, DeliveryRecord] = {}
        for old_command in plan.prior_commands:
            require(old_command.request == context.identity())
            old_receipt, _ = await self._read_inbox(old_command)
            for record in old_receipt.deliveries:
                if record.recipient_identity_digest in ids:
                    require(record.recipient_identity_digest not in historical, "conflict")
                    historical[record.recipient_identity_digest] = record
        entries: list[DeliveryEntry] = []
        proofs: list[AuthenticatedBodyReceipt] = []
        # Access pins have the same names as a delivery command but exclude its evolving entries.
        pins = VersionPins(version)
        for recipient in plan.recipients:
            recipient_id = recipient.identity_digest
            if recipient_id in historical:
                entries.append(
                    ExistingDelivery(
                        mode="existing",
                        recipient_identity_digest=recipient_id,
                        delivery=historical[recipient_id],
                    )
                )
                continue
            body = RequestBodyCommand(
                schema="auth-document-request-body.v1",
                command_id=child_id(
                    "body", context.identity(), self.producer.identity_digest, outer, recipient_id
                ),
                message_command_id=child_id(
                    "message", context.identity(), self.producer.identity_digest, outer, recipient_id
                ),
                outer_command_id=outer,
                sender_identity_digest=self.producer.identity_digest,
                request=context.identity(),
                request_revision=version.request_revision,
                assessment_version_digest=digest(version),
                recipient_set_digest=recipient_digest(plan.recipients),
                recipient_identity_digest=recipient_id,
                template=plan.template,
                body=NOTICE,
            )
            await self.journal.seal(outer, "body-" + recipient_id, {"command": wire(body)})
            selected = selector(body)
            entry = await self.journal.read(outer, "body-" + recipient_id)
            require(entry is not None)
            assert entry is not None
            operation = "preserve_request_body" if entry.state == "sealed" else "read_request_body_receipt"
            access = self._access(
                operation, body.command_id, digest(body), payload["context_query_digest"], body.request, pins
            )
            grant = await self._grant(access, phi=True)
            mutation = entry.state == "sealed" and await self.journal.claim_send(
                outer, "body-" + recipient_id
            )
            if not mutation and operation == "preserve_request_body":
                access = self._access(
                    "read_request_body_receipt",
                    body.command_id,
                    digest(body),
                    payload["context_query_digest"],
                    body.request,
                    pins,
                )
                grant = await self._grant(access, phi=True)
            if mutation:
                proof = await self.content.preserve_proven(grant, body, grant.ceiling())
            else:
                proof = await self.content.recover_proven(grant, selected, grant.ceiling())
            await self.journal.acknowledge(outer, "body-" + recipient_id, proof.receipt)
            proofs.append(proof)
            entries.append(
                NewDelivery(
                    mode="new",
                    recipient_identity_digest=recipient_id,
                    body_command_id=body.command_id,
                    message_command_id=body.message_command_id,
                    body_ref=proof.receipt.body_ref,
                    body_request_digest=digest(body),
                )
            )
        command = RequestDeliveryCommand(
            schema="auth-document-request-delivery.v1",
            command_id=outer,
            sender_identity_digest=self.producer.identity_digest,
            request=context.identity(),
            request_revision=version.request_revision,
            policy_ref=version.policy_ref,
            policy_digest=version.policy_digest,
            policy_publication_ref=version.policy_publication_ref,
            policy_publication_digest=version.policy_publication_digest,
            assessment_version_digest=digest(version),
            context_query_digest=payload["context_query_digest"],
            recipient_set_digest=recipient_digest(plan.recipients),
            deliveries=tuple(entries),
        )
        await self.journal.seal(outer, "delivery", {"command": wire(command)})
        mutation = await self.journal.claim_send(outer, "delivery")
        if mutation:
            grant = await self._grant(self._command_access("publish_request_inbox", command), phi=False)
            receipt = await self.inbox.publish(grant, command, version, tuple(proofs), grant.ceiling())
        else:
            receipt, grant = await self._read_inbox(command)
        await self.journal.acknowledge(outer, "delivery", receipt)
        return await self._finish(outer, receipt, grant, acquired)

    async def _finish(
        self,
        outer: str,
        receipt: RequestInboxReceipt,
        grant: SystemGrant,
        acquired: ProducerAcquisition | None,
    ) -> ProducerResult:
        try:
            return await self._complete(outer, receipt, grant, acquired)
        except Exception:
            # Actual inbox remains independently provable while native disposition is uncertain.
            # Cancellation is not swallowed; no exception text or replacement command escapes.
            self.journal.current()
            alive(grant.ceiling())
            return ProducerResult(receipt, CompletionOutcome("uncertain", None))

    async def _complete(
        self,
        outer: str,
        receipt: RequestInboxReceipt,
        grant: SystemGrant,
        acquired: ProducerAcquisition | None,
    ) -> ProducerResult:
        stored = await self.journal.read(outer, "completion")
        if stored is None:
            if acquired is None:
                return ProducerResult(receipt, CompletionOutcome("not_prepared", None))
            prepared = self.completion.prepare(
                acquired, base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
            )
            stored = await self.journal.seal(
                outer,
                "completion",
                {
                    "body": base64.b64encode(prepared.body).decode(),
                    "binding": base64.b64encode(prepared.binding).decode(),
                },
                select_once=True,
            )
        prepared = PreparedCompletion(
            base64.b64decode(stored.payload["body"], validate=True),
            base64.b64decode(stored.payload["binding"], validate=True),
        )
        mutation = await self.journal.claim_send(outer, "completion")

        # An expired acquisition may still recover its receipt. It never gets a new completion identity.
        def current_receipt() -> None:
            alive(grant.ceiling())

        result = await self.completion.exchange(prepared, recovery=not mutation, before_send=current_receipt)
        if result.receipt is not None:
            await self.journal.acknowledge(outer, "completion", decode(result.receipt))
        return ProducerResult(receipt, result)

    async def recover(self, request_ref: str) -> ProducerResult:
        """Protected original lineage; no fresh fetch or acquisition replacement."""
        return await self._recover(request_ref, None)

    async def _recover(self, request_ref: str, acquired: ProducerAcquisition | None) -> ProducerResult:
        seed = await self.journal.read(request_ref, "occurrence")
        require(seed is not None, "uncertain")
        assert seed is not None
        self._original_producer(seed.payload["producer"])
        outer = seed.payload["outer_command_id"]
        stored = await self.journal.read(outer, "delivery")
        if stored is None or stored.state == "sealed":
            plan = await self.journal.read(outer, "plan")
            require(plan is not None, "uncertain")
            assert plan is not None
            return await self._deliver(outer, plan.payload, acquired)
        command = parse_model(RequestDeliveryCommand, stored.payload["command"])
        require(
            wire(command.request) == seed.payload["request"]
            and command.sender_identity_digest == self.producer.identity_digest
        )
        receipt, grant = await self._read_inbox(command)
        await self.journal.acknowledge(outer, "delivery", receipt)
        return await self._finish(outer, receipt, grant, acquired)
