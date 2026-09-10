"""Concrete durable metadata inbox/history with no raw content or encryption key.

Injected engine must have independently qualified metadata-only database privileges.
Publication and inbox availability are one commit, never an external delivery claim.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.portal.contracts.communications import CommunicationReceipt, CommunicationSubmission

from .models import CommunicationGrant, CommunicationScope, alive, fingerprint, identity


async def case_lock(connection: Any, scope: CommunicationScope, case_ref: str) -> None:
    # All event writers and readers use this same per-case boundary, so a sequence
    # allocated by an uncommitted writer cannot later appear behind a page cursor.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": fingerprint({"scope": scope.model_dump(), "case_ref": case_ref})},
    )


class PostgresCommunicationStore:
    def __init__(self, engine: AsyncEngine, *, scope: CommunicationScope, seconds: float = 5) -> None:
        self.engine, self.scope, self.seconds = engine, scope, seconds

    def _params(self, grant: CommunicationGrant) -> dict[str, Any]:
        if grant.access.scope != self.scope or grant.access.principal.tenant != self.scope.tenant:
            raise ExternalCaseError("denied")
        return dict(
            **self.scope.model_dump(), case=grant.access.case_ref, identity=identity(grant.access.principal)
        )

    async def publish(
        self,
        grant: CommunicationGrant,
        body: CommunicationSubmission,
        *,
        sender_kind: str,
        deadline: datetime,
    ) -> CommunicationReceipt:
        params = self._params(grant)
        ceiling = alive(deadline, grant.ceiling())
        digest = fingerprint(body.model_dump(mode="json"))
        if (
            grant.access.operation != "publish"
            or grant.access.request_digest != digest
            or sender_kind not in {"staff", "beneficiary", "provider"}
        ):
            raise ExternalCaseError("denied")
        recipients_digest = fingerprint(
            [
                {k: v for k, v in r.model_dump(mode="json").items() if k != "valid_until"}
                for r in sorted(grant.intended_recipients, key=lambda r: r.identity_digest)
            ]
        )
        params.update(
            command=body.command_id,
            body=body.body_ref,
            recipient_set=body.recipient_set_ref,
            request_digest=digest,
            recipients_digest=recipients_digest,
            communication=uuid4().hex,
            sender_kind=sender_kind,
            authority_ref=grant.authority_receipt_ref,
            authority_digest=grant.authority_digest,
        )
        result = None
        async with transaction(self.engine, self.seconds) as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {
                    "lock": fingerprint(
                        {
                            "scope": self.scope.model_dump(),
                            "identity": params["identity"],
                            "command": body.command_id,
                            "purpose": "message",
                        }
                    )
                },
            )
            await case_lock(connection, self.scope, grant.access.case_ref)
            alive(ceiling)
            previous = (
                (
                    await connection.execute(
                        text(
                            "SELECT communication_ref,case_ref,request_digest,recipients_digest,"
                            "authority_digest "
                            "FROM portal_communication.message WHERE tenant=:tenant AND "
                            "environment=:environment "
                            "AND sender_identity_digest=:identity AND command_id=:command"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                if (
                    previous["case_ref"] != params["case"]
                    or previous["request_digest"] != digest
                    or previous["recipients_digest"] != recipients_digest
                    or previous["authority_digest"] != grant.authority_digest
                ):
                    raise ExternalCaseError("conflict")
                result = CommunicationReceipt(
                    communication_ref=previous["communication_ref"], command_id=body.command_id
                )
            else:
                custody = (
                    (
                        await connection.execute(
                            text(
                                "SELECT authored_at FROM portal_communication.content_metadata "
                                "WHERE tenant=:tenant AND environment=:environment AND case_ref=:case "
                                "AND sender_identity_digest=:identity AND body_ref=:body"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if custody is None:
                    raise ExternalCaseError("denied")
                params["authored_at"] = custody["authored_at"]
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.message "
                        "(tenant,environment,case_ref,sender_identity_digest,sender_kind,command_id,"
                        "communication_ref,"
                        "body_ref,request_digest,recipient_set_ref,recipients_digest,authority_receipt_ref,"
                        "authority_digest,authored_at) "
                        "VALUES (:tenant,:environment,:case,:identity,:sender_kind,:command,:communication,"
                        ":body,:request_digest,:recipient_set,:recipients_digest,:authority_ref,"
                        ":authority_digest,:authored_at)"
                    ),
                    params,
                )
                for recipient in grant.intended_recipients:
                    target = dict(
                        params,
                        recipient=recipient.identity_digest,
                        audience=recipient.audience,
                        source_revision=recipient.source_revision,
                        policy_digest=recipient.policy_digest,
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO portal_communication.intended_recipient "
                            "(tenant,environment,communication_ref,recipient_identity_digest,audience,"
                            "source_revision,policy_digest) "
                            "VALUES (:tenant,:environment,:communication,:recipient,:audience,"
                            ":source_revision,:policy_digest)"
                        ),
                        target,
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO portal_communication.inbox "
                            "(tenant,environment,communication_ref,recipient_identity_digest) "
                            "VALUES (:tenant,:environment,:communication,:recipient)"
                        ),
                        target,
                    )
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.history "
                        "(tenant,environment,case_ref,event_ref,kind,communication_ref) "
                        "VALUES (:tenant,:environment,:case,:event,'communication_available',:communication)"
                    ),
                    dict(params, event=uuid4().hex),
                )
                result = CommunicationReceipt(
                    communication_ref=params["communication"], command_id=body.command_id
                )
            alive(ceiling)
        alive(ceiling)
        if result is None:
            raise ExternalCaseError("uncertain")
        return result

    async def candidates(
        self,
        grant: CommunicationGrant,
        *,
        operation: Literal["list_messages", "list_history"],
        audience: str,
        cursor: str | None,
        limit: int,
        deadline: datetime,
    ) -> tuple[list[dict[str, Any]], str | None, datetime]:
        params = self._params(grant)
        ceiling = alive(deadline, grant.ceiling())
        if grant.access.operation != operation or not 1 <= limit <= 100:
            raise ExternalCaseError("invalid")
        params.update(
            audience=audience,
            page_limit=limit,
            fetch_limit=limit + 1,
            after=0,
            membership=str(grant.access.principal.membership_revision),
            operation=operation,
            authority_digest=grant.authority_digest,
        )
        next_cursor = None
        async with transaction(self.engine, self.seconds) as connection:
            await case_lock(connection, self.scope, grant.access.case_ref)
            if cursor is not None:
                entry = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM portal_communication.cursor "
                                "WHERE tenant=:tenant AND environment=:environment AND cursor_ref=:cursor"
                            ),
                            dict(params, cursor=cursor),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if entry is None or any(
                    entry[column] != params[key]
                    for column, key in (
                        ("case_ref", "case"),
                        ("identity_digest", "identity"),
                        ("membership_revision", "membership"),
                        ("operation", "operation"),
                        ("authority_digest", "authority_digest"),
                        ("page_limit", "page_limit"),
                    )
                ):
                    raise ExternalCaseError("conflict")
                ceiling = alive(ceiling, entry["valid_until"])
                params["after"] = entry["after_sequence"]
            if operation == "list_messages":
                query = """SELECT m.communication_ref,m.sequence,m.sender_kind,m.authored_at,
                    i.available_at AS inbox_available_at,m.body_ref
                    FROM portal_communication.message m JOIN portal_communication.inbox i
                    ON i.tenant=m.tenant AND i.environment=m.environment AND
                    i.communication_ref=m.communication_ref
                    JOIN portal_communication.intended_recipient r ON r.tenant=i.tenant
                    AND r.environment=i.environment
                    AND r.communication_ref=i.communication_ref AND
                    r.recipient_identity_digest=i.recipient_identity_digest
                    WHERE m.tenant=:tenant AND m.environment=:environment AND m.case_ref=:case
                    AND i.recipient_identity_digest=:identity AND r.audience=:audience AND m.sequence>:after
                    ORDER BY m.sequence,m.communication_ref LIMIT :fetch_limit"""
            else:
                query = """SELECT
                h.event_ref,h.sequence,h.occurred_at,h.kind,h.communication_ref,h.command_ref,h.receipt_ref
                    FROM portal_communication.history h WHERE h.tenant=:tenant AND h.environment=:environment
                    AND h.case_ref=:case AND h.sequence>:after AND (h.kind='command_receipt_indexed' OR EXISTS
                    (SELECT 1 FROM portal_communication.inbox i JOIN portal_communication.intended_recipient r
                    ON r.tenant=i.tenant AND r.environment=i.environment AND
                    r.communication_ref=i.communication_ref
                    AND r.recipient_identity_digest=i.recipient_identity_digest
                    WHERE i.tenant=h.tenant AND i.environment=h.environment AND
                    i.communication_ref=h.communication_ref
                    AND i.recipient_identity_digest=:identity AND r.audience=:audience))
                    ORDER BY h.sequence,h.event_ref LIMIT :fetch_limit"""
            rows = [dict(row) for row in (await connection.execute(text(query), params)).mappings().all()]
            if len(rows) > limit:
                rows = rows[:limit]
                next_cursor = uuid4().hex
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.cursor "
                        "(tenant,environment,cursor_ref,case_ref,identity_digest,membership_revision,"
                        "operation,after_sequence,"
                        "page_limit,authority_digest,valid_until) VALUES (:tenant,:environment,:cursor,"
                        ":case,:identity,"
                        ":membership,:operation,:after,:page_limit,:authority_digest,:valid_until)"
                    ),
                    dict(params, cursor=next_cursor, after=rows[-1]["sequence"], valid_until=ceiling),
                )
            alive(ceiling)
        alive(ceiling)
        return rows, next_cursor, ceiling

    async def index_receipt(
        self,
        grant: CommunicationGrant,
        *,
        command_ref: str,
        receipt_ref: str,
        receipt_digest: str,
        deadline: datetime,
    ) -> str:
        params = self._params(grant)
        ceiling = alive(deadline, grant.ceiling())
        if grant.access.operation != "index_receipt" or grant.access.request_digest != fingerprint(
            {"command_ref": command_ref, "receipt_ref": receipt_ref, "receipt_digest": receipt_digest}
        ):
            raise ExternalCaseError("denied")
        params.update(command=command_ref, receipt=receipt_ref, digest=receipt_digest, event=uuid4().hex)
        async with transaction(self.engine, self.seconds) as connection:
            await case_lock(connection, self.scope, grant.access.case_ref)
            previous = (
                (
                    await connection.execute(
                        text(
                            "SELECT event_ref,receipt_digest FROM portal_communication.history "
                            "WHERE tenant=:tenant AND environment=:environment AND case_ref=:case "
                            "AND kind='command_receipt_indexed' AND command_ref=:command AND "
                            "receipt_ref=:receipt"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                if previous["receipt_digest"] != receipt_digest:
                    raise ExternalCaseError("conflict")
                event_ref = previous["event_ref"]
            else:
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.history "
                        "(tenant,environment,case_ref,event_ref,kind,command_ref,receipt_ref,receipt_digest) "
                        "VALUES (:tenant,:environment,:case,:event,'command_receipt_indexed',:command,"
                        ":receipt,:digest)"
                    ),
                    params,
                )
                event_ref = params["event"]
            alive(ceiling)
        alive(ceiling)
        return str(event_ref)
