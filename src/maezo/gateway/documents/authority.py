"""The production `DocumentAuthority`: published AUTH input heads, nothing else.

`DocumentAuthority` (`gateway/documents/service.py`) had no concrete implementer
anywhere in `src/`, so `document_service_factory` could never be bound. This is that
implementer, built on the same sanctioned source as `PublishedIntakeAuthority`
(`gateway/intake/published_authority.py`): the installed, publisher-verified
`mzo_auth_input_head` rows read through `NativeAuthReader`. It decides nothing — the
published `resource_authority`, `document_policy` and `document_custody` heads ARE the
contract; a Python rule here would re-decide a ratified contract and put a business
rule in code.

Fail-closed everywhere. Exactly-one semantics: two heads covering one request are an
inconsistent installation, never extra power. A quarantined, rejected or revoked
document never yields a grant, because the custody head itself refuses; absence of a
published row is a refusal, never an allowance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from maezo.gateway.human.auth_profile import (
    DocumentCustody,
    DocumentPolicy,
    InputKind,
    ResourceAuthority,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.intake.links import actor_matches
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.native_authority import NativeAuthReader
from maezo.portal.contracts.models import HumanPrincipal

from .service import DocumentAccess, DocumentAuthority, DocumentGrant


def _forbidden() -> IntakeError:
    # The authority refuses in the document plane's own closed vocabulary: a missing or
    # conflicting published row is `operation_forbidden`, never a disclosure of WHICH
    # row was missing. An unreadable source is `dependency_unavailable`.
    return IntakeError("operation_forbidden")


def _unavailable() -> IntakeError:
    return IntakeError()


def _covers(authority: ResourceAuthority, principal: HumanPrincipal, now: datetime) -> bool:
    """The published contract's own validity — identical to the intake plane's floor."""
    return (
        actor_matches(authority.actor, principal, None)
        and authority.state == "active"
        and authority.consent_state != "revoked"
        and not (authority.legal_basis == "consent" and authority.consent_state != "valid")
        and authority.valid_from <= now < authority.valid_until
    )


async def _candidate_heads(
    reader: NativeAuthReader, kind: InputKind, where: dict[str, Any]
) -> list[tuple[str, Any, datetime]]:
    """Every verified head of `kind` matching a candidate filter, with its deadline.

    The candidate SQL is a filter only: each candidate is re-read through `head`, the
    single sanctioned verification (publisher purpose, payload digest, version join,
    source lifetime). A candidate that fails verification is omitted, which is the
    fail-closed direction — an unverifiable row can only ever remove a grant.
    """
    async with reader.engine.connect() as db:
        return await _candidate_heads_in(db, reader, kind, where)


async def _candidate_heads_in(
    db: AsyncConnection, reader: NativeAuthReader, kind: InputKind, where: dict[str, Any]
) -> list[tuple[str, Any, datetime]]:
    binding = reader.binding
    rows = (
        (
            await db.execute(
                text(
                    "SELECT resource_ FROM "
                    + reader.table("mzo_auth_input_head")
                    + " WHERE tenant_=:tenant AND kind_=:kind AND state_='active' AND "
                    + where["clause"]
                ),
                {"tenant": binding.scope.tenant, "kind": kind, **where["params"]},
            )
        )
        .scalars()
        .all()
    )
    heads: list[tuple[str, Any, datetime]] = []
    for ref in sorted(set(rows)):
        try:
            payload, _row, deadline = await reader.head(db, kind, ref)
        except AuthUnavailableError:
            # An unverifiable candidate is omitted: it can only ever remove a grant.
            continue
        heads.append((ref, payload, deadline))
    return heads


async def case_policy_heads(
    reader: NativeAuthReader, case_ref: str
) -> list[tuple[str, DocumentPolicy, datetime]]:
    """Every verified `document_policy` head published for one case, with deadlines.

    Shared by the authority and the provider so a request listing and the admission that
    authorized an upload can never disagree about what the case's policy says. A `case`
    policy without its required request binding is an installation fault, refused
    outright rather than listed as if it were a request.
    """
    heads = await _candidate_heads(
        reader,
        "document_policy",
        {
            "clause": "payload_::jsonb->>'resource_kind'='case' AND payload_::jsonb->>'resource_ref'=:case",
            "params": {"case": case_ref},
        },
    )
    result: list[tuple[str, DocumentPolicy, datetime]] = []
    for ref, payload, deadline in heads:
        if type(payload) is not DocumentPolicy:
            continue
        if payload.resource_ref != case_ref or payload.request_ref is None:
            raise _unavailable()
        result.append((ref, payload, deadline))
    return result


def _principal_of(principal: HumanPrincipal, tenant: str) -> str:
    if type(principal) is not HumanPrincipal or principal.tenant != tenant:
        raise _forbidden()
    return principal.principal_ref


class PublishedDocumentAuthority:
    """`DocumentAuthority` over the published AUTH heads of one installed tenant."""

    def __init__(self, reader: NativeAuthReader) -> None:
        if type(reader) is not NativeAuthReader:
            raise _unavailable()
        self.reader = reader

    @property
    def tenant(self) -> str:
        return self.reader.binding.scope.tenant

    @staticmethod
    def _one(matches: list[tuple[Any, datetime]]) -> tuple[Any, datetime]:
        if len(matches) != 1:
            # Ambiguity is refused, never resolved by preference: two heads over one
            # request mean the installation is inconsistent, not that the principal has
            # more power.
            raise _forbidden()
        return matches[0]

    async def _context(
        self,
        db: AsyncConnection,
        principal: HumanPrincipal,
        principal_ref: str,
        resource_kind: str,
        resource_ref: str,
        now: datetime,
    ) -> tuple[ResourceAuthority, datetime]:
        heads = await _candidate_heads_in(
            db,
            self.reader,
            "resource_authority",
            {
                "clause": "payload_::jsonb->>'action'='auth.document_context.read' "
                "AND payload_::jsonb->>'resource_kind'=:rkind "
                "AND payload_::jsonb->>'resource_ref'=:rref "
                "AND payload_::jsonb->'actor'->>'principal_ref'=:principal",
                "params": {"rkind": resource_kind, "rref": resource_ref, "principal": principal_ref},
            },
        )
        authority, deadline = self._one(
            [
                (payload, deadline)
                for _ref, payload, deadline in heads
                if type(payload) is ResourceAuthority
                # The action is re-checked here, not only in the candidate filter, so a
                # wider filter can never turn two different grants into an ambiguity.
                and payload.action == "auth.document_context.read"
                and (payload.resource_kind, payload.resource_ref) == (resource_kind, resource_ref)
                and _covers(payload, principal, now)
            ]
        )
        return authority, deadline

    async def _policy(
        self,
        db: AsyncConnection,
        principal_ref: str,
        now: datetime,
        *,
        resource_kind: str | None = None,
        resource_ref: str | None = None,
    ) -> tuple[DocumentPolicy, datetime]:
        """The one open policy head naming this principal a recipient.

        `resource_kind`/`resource_ref` scope the candidate filter when the caller knows
        the resource (an `initiate_upload` names it on the wire). A `complete_upload`
        names only the opaque `upload` ref, whose resource is provider state — there the
        filter is the recipient alone, and the provider re-binds the row to the very same
        head, so an upload of another resource still cannot pass.
        """
        clause = (
            "EXISTS (SELECT 1 FROM jsonb_array_elements_text("
            "payload_::jsonb->'recipient_principal_refs') AS r WHERE r=:principal)"
        )
        params: dict[str, Any] = {"principal": principal_ref}
        if resource_kind is not None:
            clause += (
                " AND payload_::jsonb->>'resource_kind'=:rkind AND payload_::jsonb->>'resource_ref'=:rref"
            )
            params.update(rkind=resource_kind, rref=resource_ref)
        heads = await _candidate_heads_in(
            db, self.reader, "document_policy", {"clause": clause, "params": params}
        )
        return self._one(
            [
                (payload, deadline)
                for _ref, payload, deadline in heads
                if type(payload) is DocumentPolicy
                and principal_ref in payload.recipient_principal_refs
                and not payload.complete
                and (
                    resource_kind is None
                    or (payload.resource_kind, payload.resource_ref) == (resource_kind, resource_ref)
                )
                and now < min(payload.valid_until, deadline)
            ]
        )

    async def _custody(
        self, db: AsyncConnection, principal_ref: str, document_ref: str, now: datetime
    ) -> tuple[DocumentCustody, datetime]:
        """The verified custody head of one document, if it is currently deliverable."""
        try:
            custody, _row, deadline = await self.reader.head(db, "document_custody", document_ref)
        except AuthUnavailableError:
            # A document whose custody head cannot be verified is not downloadable.
            raise _unavailable() from None
        if (
            type(custody) is not DocumentCustody
            or custody.document.document_ref != document_ref
            or custody.custody_state != "available"
            or custody.screening_result != "clean"
            or now >= min(custody.valid_until, deadline)
        ):
            raise _forbidden()
        return custody, deadline

    async def authorize(self, access: DocumentAccess) -> DocumentGrant:
        principal_ref = _principal_of(access.principal, self.tenant)
        now = datetime.now(UTC)
        async with self.reader.engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            try:
                _installed, until = await self.reader.qualified(db)
            except AuthUnavailableError:
                raise _unavailable() from None
            heads: list[Any] = []
            if access.operation == "initiate_upload":
                # An upload is authorized by the published document policy that names
                # this principal a recipient of this exact open resource.
                policy, deadline = await self._policy(
                    db,
                    principal_ref,
                    now,
                    resource_kind=access.resource_kind,
                    resource_ref=access.resource_ref,
                )
                heads.append(policy)
                until = min(until, deadline, policy.valid_until)
            elif access.operation == "complete_upload":
                # The upload ref is opaque provider state; the principal-scoped policy is
                # the published fact this operation can be held to, and the provider
                # binds the row to that same head before any byte is read.
                policy, deadline = await self._policy(db, principal_ref, now)
                heads.append(policy)
                until = min(until, deadline, policy.valid_until)
            elif access.operation in {"list_documents", "list_requests"}:
                authority, deadline = await self._context(
                    db, access.principal, principal_ref, access.resource_kind, access.resource_ref, now
                )
                heads.append(authority)
                until = min(until, deadline, authority.valid_until)
            elif access.operation == "download":
                custody, custody_deadline = await self._custody(db, principal_ref, access.resource_ref, now)
                authority, deadline = await self._context(
                    db, access.principal, principal_ref, custody.resource_kind, custody.resource_ref, now
                )
                heads.extend([custody, authority])
                until = min(until, custody_deadline, deadline, custody.valid_until, authority.valid_until)
            elif access.operation == "respond":
                heads_ref = await _candidate_heads_in(
                    db,
                    self.reader,
                    "resource_authority",
                    {
                        "clause": "payload_::jsonb->>'action'='auth.documents.respond' "
                        "AND payload_::jsonb->>'resource_kind'='case' "
                        "AND payload_::jsonb->>'resource_ref'=:case "
                        "AND payload_::jsonb->>'request_ref'=:request "
                        "AND payload_::jsonb->'actor'->>'principal_ref'=:principal",
                        "params": {
                            "case": access.case_ref,
                            "request": access.resource_ref,
                            "principal": principal_ref,
                        },
                    },
                )
                authority, deadline = self._one(
                    [
                        (payload, deadline)
                        for _ref, payload, deadline in heads_ref
                        if type(payload) is ResourceAuthority
                        and (payload.resource_kind, payload.resource_ref, payload.request_ref)
                        == ("case", access.case_ref, access.resource_ref)
                        and _covers(payload, access.principal, now)
                    ]
                )
                heads.append(authority)
                until = min(until, deadline, authority.valid_until)
            else:  # pragma: no cover - `Operation` is a closed Literal
                raise _forbidden()
        if now >= until:
            raise _forbidden()
        return DocumentGrant(
            access=access,
            authority_digest=digest({"schema": "portal-document-grant.v1", "heads": heads, "access": access}),
            valid_until=until,
        )


def _conformance(authority: PublishedDocumentAuthority) -> DocumentAuthority:
    """Static proof for mypy that the production class satisfies `DocumentAuthority`."""
    return authority
