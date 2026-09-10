"""Current-authorized metadata inbox/history; no text or key enters this service."""

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from maezo.gateway.external_cases.models import ExternalCaseError, instant
from maezo.gateway.human.gateway import HumanGateway
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.communications import (
    CommunicationPage,
    CommunicationSubmission,
    CommunicationSummary,
    HistoryEntry,
    HistoryPage,
)
from maezo.portal.contracts.intake import Closed

from .models import (
    HISTORY_FIELDS,
    MESSAGE_FIELDS,
    CommunicationAccess,
    CommunicationAuthority,
    CommunicationGrant,
    CommunicationScope,
    Operation,
    alive,
    fingerprint,
    now,
)
from .postgres import PostgresCommunicationStore

PAGE_FIELDS = frozenset({"case_ref", "items", "next_cursor", "observed_at", "valid_until"})


def database_instant(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ExternalCaseError("unavailable")
    return instant(value)


class CommunicationSessionBoundary:
    """Shared exact currentness checks; original ceilings survive every refresh/I/O."""

    def __init__(
        self, resolver: HumanSessionResolver, authority: CommunicationAuthority, scope: CommunicationScope
    ) -> None:
        if resolver.settings.tenant != scope.tenant or authority.scope != scope:
            raise ExternalCaseError("unavailable")
        self.resolver, self.authority, self.scope = resolver, authority, scope

    async def session(
        self, secret: str, *, mutation: bool = False, csrf: str | None = None, origin: str | None = None
    ) -> ResolvedHumanSession:
        session = await self.resolver.resolve(secret)
        if session.principal.tenant != self.scope.tenant or session.membership.audience not in {
            "staff",
            "beneficiary",
            "provider",
        }:
            raise AuthenticationError()
        alive(session.record.expires_at, session.membership.reviewed_until)
        if mutation and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise AuthenticationError()
        return session

    async def grant(
        self,
        session: ResolvedHumanSession,
        *,
        operation: Operation,
        case_ref: str,
        resource_ref: str | None = None,
        request_digest: str | None = None,
    ) -> CommunicationGrant:
        access = CommunicationAccess(
            scope=self.scope,
            principal=session.principal,
            operation=operation,
            case_ref=case_ref,
            resource_ref=resource_ref,
            request_digest=request_digest or fingerprint({}),
        )
        grant = await self.authority.authorize(access)
        if grant.access != access:
            raise ExternalCaseError("denied")
        alive(session.record.expires_at, session.membership.reviewed_until, grant.ceiling())
        return grant

    async def finish[T](
        self,
        secret: str,
        initial: ResolvedHumanSession,
        grants: list[CommunicationGrant],
        value: Closed,
        *deadlines: datetime,
        freeze: Callable[[bytes], T],
    ) -> T:
        ceilings = [initial.record.expires_at, initial.membership.reviewed_until, *deadlines]
        for grant in grants:
            ceilings.append(grant.ceiling())
            current = await self.authority.authorize(grant.access)
            if (
                current.access != grant.access
                or current.authority_digest != grant.authority_digest
                or current.permitted_fields != grant.permitted_fields
                or [
                    (r.identity_digest, r.audience, r.source_revision, r.policy_digest)
                    for r in current.intended_recipients
                ]
                != [
                    (r.identity_digest, r.audience, r.source_revision, r.policy_digest)
                    for r in grant.intended_recipients
                ]
            ):
                raise ExternalCaseError("conflict")
            ceilings.append(current.ceiling())
            alive(*ceilings)
        final = await self.session(secret)
        if final.principal != initial.principal or final.membership.audience != initial.membership.audience:
            raise ExternalCaseError("conflict")
        ceilings.extend((final.record.expires_at, final.membership.reviewed_until))
        if isinstance(value, (CommunicationPage, HistoryPage)):
            # A renewed source response may shorten validity without changing its
            # policy revision. The browser must see that smaller original/current ceiling.
            value = value.model_copy(update={"valid_until": instant(alive(*ceilings))})
        # No await or reconstruction after the final serialized-byte validity check.
        raw = value.model_dump_json().encode("utf-8")
        result = freeze(raw)
        alive(*ceilings)
        return result


class CommunicationService(CommunicationSessionBoundary):
    def __init__(
        self,
        resolver: HumanSessionResolver,
        authority: CommunicationAuthority,
        store: PostgresCommunicationStore,
    ) -> None:
        super().__init__(resolver, authority, store.scope)
        self.store = store

    async def publish[T](
        self,
        secret: str,
        *,
        csrf: str,
        origin: str,
        case_ref: str,
        body: CommunicationSubmission,
        freeze: Callable[[bytes], T],
    ) -> T:
        session = await self.session(secret, mutation=True, csrf=csrf, origin=origin)
        grant = await self.grant(
            session,
            operation="publish",
            case_ref=case_ref,
            resource_ref=body.body_ref,
            request_digest=fingerprint(body.model_dump(mode="json")),
        )
        ceiling = alive(session.record.expires_at, session.membership.reviewed_until, grant.ceiling())
        receipt = await self.store.publish(
            grant, body, sender_kind=session.membership.audience, deadline=ceiling
        )
        return await self.finish(secret, session, [grant], receipt, ceiling, freeze=freeze)

    async def page[T](
        self,
        secret: str,
        *,
        operation: Literal["list_messages", "list_history"],
        case_ref: str,
        freeze: Callable[[bytes], T],
        cursor: str | None = None,
        limit: int = 25,
    ) -> T:
        session = await self.session(secret)
        observed = now()
        grant = await self.grant(session, operation=operation, case_ref=case_ref)
        required = PAGE_FIELDS | ({"history_scope"} if operation == "list_history" else set())
        if not required.issubset(grant.permitted_fields):
            raise ExternalCaseError("denied")
        ceiling = alive(session.record.expires_at, session.membership.reviewed_until, grant.ceiling())
        rows, next_cursor, page_until = await self.store.candidates(
            grant,
            operation=operation,
            audience=session.membership.audience,
            cursor=cursor,
            limit=limit,
            deadline=ceiling,
        )
        grants = [grant]
        messages: list[CommunicationSummary] = []
        history: list[HistoryEntry] = []
        for row in rows:
            resource = row["communication_ref"] if operation == "list_messages" else row["event_ref"]
            try:
                row_grant = await self.grant(
                    session,
                    operation="read_message" if operation == "list_messages" else "read_history",
                    case_ref=case_ref,
                    resource_ref=resource,
                )
            except ExternalCaseError as exc:
                if exc.code == "denied":
                    continue  # Known resource denial only; unknown policy state fails the page.
                raise
            grants.append(row_grant)
            permitted = set(row_grant.permitted_fields)
            if operation == "list_messages":
                if not (MESSAGE_FIELDS - {"body_ref"}).issubset(permitted):
                    continue
                data = {k: row[k] for k in permitted & MESSAGE_FIELDS if k in row}
                for name in ("authored_at", "inbox_available_at"):
                    if name in data:
                        data[name] = database_instant(data[name])
                if "delivery_state" in permitted:
                    data["delivery_state"] = "inbox_available"
                messages.append(CommunicationSummary.model_validate(data))
            else:
                if not {"event_ref", "sequence", "occurred_at", "kind"}.issubset(permitted):
                    continue
                data = {k: row[k] for k in permitted & HISTORY_FIELDS if k in row}
                if "occurred_at" in data:
                    data["occurred_at"] = database_instant(data["occurred_at"])
                if "sequence" in data:
                    if type(data["sequence"]) is not int or data["sequence"] < 0:
                        raise ExternalCaseError("unavailable")
                    data["sequence"] = str(data["sequence"])
                history.append(HistoryEntry.model_validate(data))
        ceiling = alive(ceiling, page_until, *(g.ceiling() for g in grants))
        page: CommunicationPage | HistoryPage
        common: dict[str, Any] = dict(
            case_ref=case_ref,
            next_cursor=next_cursor,
            observed_at=instant(observed),
            valid_until=instant(ceiling),
        )
        page = (
            CommunicationPage(items=tuple(messages), **common)
            if operation == "list_messages"
            else HistoryPage(items=tuple(history), **common)
        )
        return await self.finish(secret, session, grants, page, ceiling, freeze=freeze)

    async def index_existing_receipt(
        self, secret: str, *, case_ref: str, task_id: str, command_id: str, gateway: HumanGateway
    ) -> str:
        """Server-only index. Existing gateway obtains current authenticated receipt.

        No browser supplied receipt/engine state is accepted. The independent index
        grant must verify this task/receipt belongs to the requested case.
        """
        session = await self.session(secret)
        if (
            session.membership.audience != "staff"
            or not isinstance(gateway, HumanGateway)
            or gateway._resolver is not self.resolver
        ):
            raise ExternalCaseError("denied")
        receipt = await gateway.read_receipt(session_secret=secret, task_id=task_id, command_id=command_id)
        if (
            receipt.status != "committed"
            or receipt.engine_receipt_ref is None
            or receipt.tenant != self.scope.tenant
            or receipt.task_id != task_id
            or receipt.command_id != command_id
            or receipt.principal_ref != session.principal.principal_ref
        ):
            raise ExternalCaseError("denied")
        digest = fingerprint(receipt.model_dump(mode="json"))
        grant = await self.grant(
            session,
            operation="index_receipt",
            case_ref=case_ref,
            resource_ref=task_id,
            request_digest=fingerprint(
                {
                    "command_ref": command_id,
                    "receipt_ref": receipt.engine_receipt_ref,
                    "receipt_digest": digest,
                }
            ),
        )
        ceiling = alive(session.record.expires_at, session.membership.reviewed_until, grant.ceiling())
        event = await self.store.index_receipt(
            grant,
            command_ref=command_id,
            receipt_ref=receipt.engine_receipt_ref,
            receipt_digest=digest,
            deadline=ceiling,
        )
        # Reuse the complete final-I/O/currentness path without publishing receipt bytes.
        value = HistoryEntry(event_ref=event)
        await self.finish(secret, session, [grant], value, ceiling, freeze=lambda raw: raw)
        return event
