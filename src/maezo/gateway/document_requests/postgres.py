"""Actual existing communication tables, immutable bridge versions, one inbox transaction."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from maezo.gateway.communications.models import CommunicationScope, alive
from maezo.gateway.communications.postgres import case_lock
from maezo.gateway.human.read_profile import digest, wire

from .admission import SystemAdmission, lock_key, one
from .content import AuthenticatedBodyReceipt
from .models import (
    DeliveryRecord,
    ExistingDelivery,
    NewDelivery,
    PolicyAssessmentVersion,
    RequestDeliveryCommand,
    RequestInboxReceipt,
    SystemGrant,
    packed,
    parsed,
    recipient_digest,
    require,
)


class RequestInboxStore:
    def __init__(self, admission: SystemAdmission) -> None:
        self.admission = admission

    async def _version(
        self, c: AsyncConnection, command: RequestDeliveryCommand, version: PolicyAssessmentVersion
    ) -> None:
        require(
            digest(version) == command.assessment_version_digest
            and version.request_identity_digest == digest(command.request)
            and all(
                getattr(command, k) == getattr(version, k)
                for k in (
                    "request_revision",
                    "policy_ref",
                    "policy_digest",
                    "policy_publication_ref",
                    "policy_publication_digest",
                )
            )
        )
        p = dict(
            **command.request.communication_scope().model_dump(),
            request=command.request.request_ref,
            request_digest=digest(command.request),
            identity=packed(command.request),
            case=command.request.case_ref,
            revision=command.request_revision,
            stable=version.stable_definition_digest,
            version=digest(version),
            payload=packed(version),
            publication=version.policy_publication_ref,
            native_scope=digest(command.request.scope),
        )
        await c.execute(
            text(
                "INSERT INTO portal_document_request.request "
                "(tenant,environment,request_ref,case_ref,identity_digest,identity_payload) "
                "VALUES (:tenant,:environment,:request,:case,:request_digest,:identity) ON CONFLICT "
                "DO NOTHING"
            ),
            p,
        )
        request = await one(
            c,
            "SELECT identity_digest,identity_payload FROM portal_document_request.request "
            "WHERE tenant=:tenant AND environment=:environment AND request_ref=:request FOR UPDATE",
            p,
        )
        require(
            request is not None
            and request["identity_digest"] == p["request_digest"]
            and bytes(request["identity_payload"]) == p["identity"],
            "conflict",
        )
        await c.execute(
            text(
                "INSERT INTO portal_document_request.request_definition_revision "
                "(tenant,environment,request_ref,request_revision,stable_definition_digest) "
                "VALUES (:tenant,:environment,:request,:revision,:stable) ON CONFLICT DO NOTHING"
            ),
            p,
        )
        stable = await one(
            c,
            "SELECT stable_definition_digest FROM portal_document_request.request_definition_revision "
            "WHERE tenant=:tenant AND environment=:environment AND request_ref=:request AND "
            "request_revision=:revision",
            p,
        )
        require(stable is not None and stable["stable_definition_digest"] == p["stable"], "conflict")
        await c.execute(
            text(
                "INSERT INTO portal_document_request.assessment_version "
                "(tenant,environment,request_ref,request_revision,native_scope_digest,"
                "assessment_version_digest,publication_ref,payload) "
                "VALUES "
                "(:tenant,:environment,:request,:revision,:native_scope,:version,:publication,"
                ":payload) ON CONFLICT DO NOTHING"
            ),
            p,
        )
        actual = await one(
            c,
            "SELECT payload FROM portal_document_request.assessment_version "
            "WHERE tenant=:tenant AND environment=:environment AND request_ref=:request AND "
            "assessment_version_digest=:version",
            p,
        )
        require(actual is not None and bytes(actual["payload"]) == p["payload"], "conflict")

    async def _delivery(
        self, c: AsyncConnection, scope: CommunicationScope, request: str, recipient: str
    ) -> DeliveryRecord | None:
        row = await one(
            c,
            "SELECT "
            "d.record,m.sender_identity_digest,m.sender_kind,m.command_id,m.communication_ref,m.body_ref,"
            "m.request_digest,m.recipients_digest,m.recipient_set_ref,m.authority_receipt_ref,"
            "m.authority_digest,m.inbox_available_at,"
            "r.audience,r.source_revision,r.policy_digest,c.authority_receipt_ref AS body_authority_ref,"
            "c.authority_digest AS body_authority_digest FROM portal_document_request.delivery d "
            "JOIN portal_communication.message m ON m.tenant=d.tenant AND m.environment=d.environment "
            "AND m.communication_ref=d.communication_ref AND m.body_ref=d.body_ref "
            "JOIN portal_document_request.request request ON request.tenant=d.tenant AND "
            "request.environment=d.environment "
            "AND request.request_ref=d.request_ref AND request.case_ref=m.case_ref "
            "JOIN portal_communication.content_metadata c ON c.tenant=m.tenant AND "
            "c.environment=m.environment "
            "AND c.case_ref=m.case_ref AND c.sender_identity_digest=m.sender_identity_digest AND "
            "c.body_ref=m.body_ref "
            "JOIN portal_communication.intended_recipient r ON r.tenant=m.tenant AND "
            "r.environment=m.environment "
            "AND r.communication_ref=m.communication_ref AND "
            "r.recipient_identity_digest=d.recipient_identity_digest "
            "JOIN portal_communication.inbox i ON i.tenant=r.tenant AND i.environment=r.environment "
            "AND i.communication_ref=r.communication_ref AND "
            "i.recipient_identity_digest=r.recipient_identity_digest "
            "JOIN portal_communication.history h ON h.tenant=m.tenant AND h.environment=m.environment "
            "AND h.case_ref=m.case_ref AND h.communication_ref=m.communication_ref AND "
            "h.kind='communication_available' "
            "WHERE d.tenant=:tenant AND d.environment=:environment AND d.request_ref=:request "
            "AND d.recipient_identity_digest=:recipient AND d.channel='portal'",
            dict(**scope.model_dump(), request=request, recipient=recipient),
        )
        if row is None:
            return None
        record = parsed(DeliveryRecord, bytes(row["record"]))
        singleton = digest(
            [
                dict(
                    identity_digest=recipient,
                    audience=row["audience"],
                    source_revision=row["source_revision"],
                    policy_digest=row["policy_digest"],
                )
            ]
        )
        child = dict(
            schema="auth-document-request-message.v1",
            sender_identity_digest=record.sender_identity_digest,
            origin_command_id=record.origin_command_id,
            origin_command_digest=record.origin_command_digest,
            message_command_id=record.message_command_id,
            body_command_id=record.body_command_id,
            body_ref=record.body_ref,
            body_request_digest=record.body_request_digest,
            recipient_identity_digest=recipient,
            recipient_set_digest=singleton,
            assessment_version_digest=record.notice_assessment_version_digest,
        )
        require(
            record.recipient_identity_digest == recipient
            and row["sender_kind"] == "system"
            and row["sender_identity_digest"] == record.sender_identity_digest
            and row["command_id"] == row["recipient_set_ref"] == record.message_command_id
            and row["communication_ref"] == record.communication_ref
            and row["body_ref"] == record.body_ref
            and row["request_digest"] == record.message_request_digest == digest(child)
            and row["recipients_digest"] == singleton
            and row["authority_receipt_ref"] == record.message_authority_receipt_ref
            and row["authority_digest"] == record.message_authority_digest
            and row["body_authority_ref"] == record.body_authority_receipt_ref
            and row["body_authority_digest"] == record.body_authority_digest
            and row["inbox_available_at"] == record.inbox_available_at,
            "conflict",
        )
        return record

    async def publish(
        self,
        grant: SystemGrant,
        command: RequestDeliveryCommand,
        version: PolicyAssessmentVersion,
        proofs: tuple[AuthenticatedBodyReceipt, ...],
        deadline: datetime,
    ) -> RequestInboxReceipt:
        a = grant.access
        require(
            a.operation == "publish_request_inbox"
            and a.command_id == command.command_id
            and a.request_digest == digest(command)
            and a.producer.identity_digest == command.sender_identity_digest
            and grant.body_bindings == command.deliveries
            and recipient_digest(grant.recipients) == command.recipient_set_digest
        )
        require(
            all(
                getattr(a, k) == getattr(command, k)
                for k in (
                    "request",
                    "request_revision",
                    "policy_ref",
                    "policy_digest",
                    "policy_publication_ref",
                    "policy_publication_digest",
                    "assessment_version_digest",
                    "context_query_digest",
                )
            )
        )
        require(all(type(p) is AuthenticatedBodyReceipt for p in proofs), "unavailable")
        receipts = {p.receipt.recipient_identity_digest: p.receipt for p in proofs}
        require(
            len(receipts) == len(proofs)
            and set(receipts)
            == {e.recipient_identity_digest for e in command.deliveries if isinstance(e, NewDelivery)}
        )
        p = dict(
            **a.scope.model_dump(),
            sender=command.sender_identity_digest,
            command=command.command_id,
            digest=digest(command),
            request=command.request.request_ref,
            case=command.request.case_ref,
        )
        ceiling = alive(deadline, grant.ceiling())
        async with self.admission.acquire(grant, ceiling) as c:
            await lock_key(
                c, {"scope": a.scope.model_dump(), "request": p["request"], "purpose": "request-delivery"}
            )
            await case_lock(c, a.scope, p["case"])
            prior = await one(
                c,
                "SELECT request_digest,payload,receipt FROM portal_document_request.command "
                "WHERE tenant=:tenant AND environment=:environment AND "
                "producer_identity_digest=:sender AND command_id=:command FOR UPDATE",
                p,
            )
            if prior is not None:
                require(
                    prior["request_digest"] == p["digest"] and bytes(prior["payload"]) == packed(command),
                    "conflict",
                )
                result = parsed(RequestInboxReceipt, bytes(prior["receipt"]))
                require(
                    result.command_id == command.command_id
                    and result.command_digest == digest(command)
                    and result.request == command.request
                    and all(
                        getattr(result, k) == getattr(command, k)
                        for k in (
                            "request_revision",
                            "policy_ref",
                            "policy_digest",
                            "assessment_version_digest",
                        )
                    ),
                    "conflict",
                )
                for record in result.deliveries:
                    require(
                        await self._delivery(c, a.scope, p["request"], record.recipient_identity_digest)
                        == record,
                        "conflict",
                    )
            else:
                await self._version(c, command, version)
                deliveries = []
                for entry, recipient in zip(command.deliveries, grant.recipients, strict=True):
                    require(entry.recipient_identity_digest == recipient.identity_digest)
                    old = await self._delivery(c, a.scope, p["request"], recipient.identity_digest)
                    if isinstance(entry, ExistingDelivery):
                        require(old == entry.delivery, "conflict")
                        deliveries.append(entry.delivery)
                        continue
                    require(old is None, "conflict")
                    body = receipts[recipient.identity_digest]
                    require(
                        body.scope == a.scope
                        and body.case_ref == p["case"]
                        and body.sender_identity_digest == p["sender"]
                        and body.outer_command_id == p["command"]
                        and body.request_identity_digest == digest(command.request)
                        and body.template == grant.template
                        and body.request_revision == command.request_revision
                        and body.assessment_version_digest == command.assessment_version_digest
                        and body.recipient_set_digest == command.recipient_set_digest
                        and all(
                            getattr(body, k) == getattr(entry, k)
                            for k in (
                                "recipient_identity_digest",
                                "body_command_id",
                                "message_command_id",
                                "body_ref",
                                "body_request_digest",
                            )
                        )
                    )
                    singleton = digest(
                        [
                            {
                                k: wire(getattr(recipient, k))
                                for k in ("identity_digest", "audience", "source_revision", "policy_digest")
                            }
                        ]
                    )
                    child = dict(
                        schema="auth-document-request-message.v1",
                        sender_identity_digest=p["sender"],
                        origin_command_id=p["command"],
                        origin_command_digest=p["digest"],
                        message_command_id=entry.message_command_id,
                        body_command_id=entry.body_command_id,
                        body_ref=entry.body_ref,
                        body_request_digest=entry.body_request_digest,
                        recipient_identity_digest=recipient.identity_digest,
                        recipient_set_digest=singleton,
                        assessment_version_digest=command.assessment_version_digest,
                    )
                    q = dict(
                        p,
                        body=entry.body_ref,
                        message=entry.message_command_id,
                        communication=uuid4().hex,
                        message_digest=digest(child),
                        singleton=singleton,
                        authority_ref=grant.authority_receipt_ref,
                        authority_digest=grant.authority_digest,
                        recipient=recipient.identity_digest,
                        audience=recipient.audience,
                        source_revision=recipient.source_revision,
                        policy_digest=recipient.policy_digest,
                    )
                    custody = await one(
                        c,
                        "SELECT authored_at,authority_receipt_ref,authority_digest FROM "
                        "portal_communication.content_metadata "
                        "WHERE tenant=:tenant AND environment=:environment AND case_ref=:case AND "
                        "sender_identity_digest=:sender AND body_ref=:body",
                        q,
                    )
                    require(
                        custody is not None
                        and custody["authority_receipt_ref"] == body.body_authority_receipt_ref
                        and custody["authority_digest"] == body.body_authority_digest
                    )
                    q["authored_at"] = custody["authored_at"]
                    await c.execute(
                        text(
                            "INSERT INTO portal_communication.message "
                            "(tenant,environment,case_ref,sender_identity_digest,sender_kind,"
                            "command_id,communication_ref,"
                            "body_ref,request_digest,recipient_set_ref,recipients_digest,"
                            "authority_receipt_ref,authority_digest,authored_at) "
                            "VALUES "
                            "(:tenant,:environment,:case,:sender,'system',:message,"
                            ":communication,:body,:message_digest,"
                            ":message,:singleton,:authority_ref,:authority_digest,:authored_at)"
                        ),
                        q,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_communication.intended_recipient "
                            "(tenant,environment,communication_ref,recipient_identity_digest,"
                            "audience,source_revision,policy_digest) "
                            "VALUES "
                            "(:tenant,:environment,:communication,:recipient,:audience,"
                            ":source_revision,:policy_digest)"
                        ),
                        q,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_communication.inbox "
                            "(tenant,environment,communication_ref,recipient_identity_digest) "
                            "VALUES (:tenant,:environment,:communication,:recipient)"
                        ),
                        q,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_communication.history "
                            "(tenant,environment,case_ref,event_ref,kind,communication_ref) "
                            "VALUES "
                            "(:tenant,:environment,:case,:event,'communication_available',:communication)"
                        ),
                        dict(q, event=uuid4().hex),
                    )
                    inserted = await one(
                        c,
                        "SELECT inbox_available_at FROM portal_communication.message "
                        "WHERE tenant=:tenant AND environment=:environment AND "
                        "sender_identity_digest=:sender AND command_id=:message",
                        q,
                    )
                    record = DeliveryRecord(
                        recipient_identity_digest=recipient.identity_digest,
                        sender_identity_digest=p["sender"],
                        origin_command_id=p["command"],
                        origin_command_digest=p["digest"],
                        body_command_id=entry.body_command_id,
                        message_command_id=entry.message_command_id,
                        message_request_digest=q["message_digest"],
                        communication_ref=q["communication"],
                        body_ref=entry.body_ref,
                        body_request_digest=entry.body_request_digest,
                        body_authority_receipt_ref=body.body_authority_receipt_ref,
                        body_authority_digest=body.body_authority_digest,
                        message_authority_receipt_ref=grant.authority_receipt_ref,
                        message_authority_digest=grant.authority_digest,
                        notice_request_revision=command.request_revision,
                        notice_policy_digest=command.policy_digest,
                        notice_assessment_version_digest=command.assessment_version_digest,
                        inbox_available_at=inserted["inbox_available_at"],
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_document_request.delivery "
                            "(tenant,environment,request_ref,recipient_identity_digest,channel,"
                            "communication_ref,body_ref,record) "
                            "VALUES "
                            "(:tenant,:environment,:request,:recipient,'portal',:communication,:body,:record)"
                        ),
                        dict(q, record=packed(record)),
                    )
                    deliveries.append(record)
                timestamp = await one(c, "SELECT transaction_timestamp() AS at", {})
                result = RequestInboxReceipt(
                    schema="auth-document-request-inbox-receipt.v1",
                    command_id=p["command"],
                    command_digest=p["digest"],
                    request=command.request,
                    request_revision=command.request_revision,
                    policy_ref=command.policy_ref,
                    policy_digest=command.policy_digest,
                    assessment_version_digest=command.assessment_version_digest,
                    deliveries=tuple(deliveries),
                    committed_at=timestamp["at"],
                )
                await c.execute(
                    text(
                        "INSERT INTO portal_document_request.command "
                        "(tenant,environment,producer_identity_digest,command_id,request_digest,"
                        "payload,receipt) "
                        "VALUES (:tenant,:environment,:sender,:command,:digest,:payload,:receipt)"
                    ),
                    dict(p, payload=packed(command), receipt=packed(result)),
                )
            alive(ceiling)
        alive(ceiling)
        return result

    async def read_receipt(
        self, grant: SystemGrant, original_sender: str, deadline: datetime
    ) -> RequestInboxReceipt:
        a = grant.access
        require(a.operation == "read_request_receipt")
        ceiling = alive(deadline, grant.ceiling())
        async with self.admission.acquire(grant, ceiling) as c:
            await case_lock(c, a.scope, a.request.case_ref)
            row = await one(
                c,
                "SELECT request_digest,payload,receipt FROM portal_document_request.command "
                "WHERE tenant=:tenant AND environment=:environment AND "
                "producer_identity_digest=:sender AND command_id=:command",
                dict(**a.scope.model_dump(), sender=original_sender, command=a.command_id),
            )
            require(row is not None and row["request_digest"] == a.request_digest, "uncertain")
            command = parsed(RequestDeliveryCommand, bytes(row["payload"]))
            require(
                command.sender_identity_digest == original_sender
                and digest(command) == a.request_digest
                and command.command_id == a.command_id
                and all(
                    getattr(command, k) == getattr(a, k)
                    for k in (
                        "request",
                        "request_revision",
                        "policy_ref",
                        "policy_digest",
                        "policy_publication_ref",
                        "policy_publication_digest",
                        "assessment_version_digest",
                        "context_query_digest",
                    )
                )
            )
            result = parsed(RequestInboxReceipt, bytes(row["receipt"]))
            require(
                result.command_id == command.command_id
                and result.request == a.request
                and result.command_digest == a.request_digest
                and result.request_revision == a.request_revision
                and result.policy_ref == a.policy_ref
                and result.policy_digest == a.policy_digest
                and result.assessment_version_digest == a.assessment_version_digest
            )
            expected = tuple(
                ExistingDelivery(
                    mode="existing", recipient_identity_digest=d.recipient_identity_digest, delivery=d
                )
                for d in result.deliveries
            )
            require(expected == grant.body_bindings)
            for delivery in result.deliveries:
                require(
                    await self._delivery(
                        c, a.scope, a.request.request_ref, delivery.recipient_identity_digest
                    )
                    == delivery
                )
            alive(ceiling)
        alive(ceiling)
        return result
