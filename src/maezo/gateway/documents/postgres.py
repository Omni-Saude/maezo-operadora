"""Concrete `ProtectedDocumentProvider` over one dedicated PHI PostgreSQL plane.

Custody state machine (no bypass): `initiate` opens a slot that holds references only;
`complete` verifies the bytes really are there, re-reads the published policy head that
authorized them, mints the `document_ref` and publishes the ONLY custody row this plane
may ever write — `screening_result='pending'`, `custody_state='available'`, provenance
columns NULL. Every other screening result is copied verbatim from a VERIFIED published
`document_custody` head, so a quarantined or rejected document never becomes `verified`,
never satisfies a request and never re-enters the General zone. Screening itself is
decided engine-side and published; nothing here infers it.

Response admission follows `PostgresAuthDispatchStore.admit_response` exactly: one row
per `command_id`, replayed with the SAME digest, `conflict` on any reinterpretation,
payload sealed under the response's own associated data. `correlated` is deliberately
NOT produced here — a committed response is not proof of a bound engine subscription;
that disposition belongs to the native effect plane, which reads the exact admission
this store records.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.documents.authority import case_policy_heads
from maezo.gateway.documents.storage import (
    RESPONSE_AAD,
    DocumentScope,
    PhiDocumentKeys,
    bounded_document,
    content_digest,
    now,
    principal_digest,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.auth_profile import DocumentCustody, DocumentPolicy
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest, wire
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.native_authority import NativeAuthReader
from maezo.portal.contracts.documents import (
    DocumentPage,
    DocumentRequestPage,
    DocumentRequestSummary,
    DocumentResponse,
    DocumentResponseReceipt,
    DocumentSummary,
    UploadCompletion,
    UploadInitiation,
    UploadReceipt,
)
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize

from .service import DocumentGrant, ProtectedDocumentProvider

_UPLOADED = "portal-document-upload.v1"
_RESPONDED = "portal-document-response.v1"


def _forbidden() -> IntakeError:
    return IntakeError("operation_forbidden")


def _refuse(code: Literal["conflict", "denied", "invalid", "unavailable"]) -> ExternalCaseError:
    """The store's own refusal, raised INSIDE a transaction.

    `_transaction` converts any other exception into `uncertain`, so a specific refusal
    has to travel in the store's closed vocabulary; the boundary below translates it
    into the document plane's `IntakeError` codes once the transaction has closed.
    """
    return ExternalCaseError(code)


_FROM_STORE: dict[str, IntakeError] = {
    "conflict": IntakeError("conflict"),
    "denied": IntakeError("operation_forbidden"),
    "invalid": IntakeError("invalid_request"),
    "unavailable": IntakeError(),
    "uncertain": IntakeError(),
}


def _translated(code: str) -> IntakeError:
    error = _FROM_STORE.get(code)
    return IntakeError(error.code) if error is not None else IntakeError()


class PostgresProtectedDocumentProvider:
    """The protocol's only production implementer; no in-memory or stub sibling exists."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        scope: DocumentScope,
        keys: PhiDocumentKeys,
        heads: NativeAuthReader,
        seconds: float = 5,
        clock: Callable[[], datetime] = now,
    ) -> None:
        if keys.scope != scope or scope.tenant != heads.binding.scope.tenant:
            raise _forbidden()
        self.engine, self.scope, self.keys, self.heads, self.seconds, self.clock = (
            engine,
            scope,
            keys,
            heads,
            seconds,
            clock,
        )

    # --- shared helpers ----------------------------------------------------------

    def _creator(self, grant: DocumentGrant, operation: str) -> str:
        access = grant.access
        if access.operation != operation or access.principal.tenant != self.scope.tenant:
            raise _forbidden()
        return principal_digest(access.principal)

    def _alive(self, *deadlines: datetime) -> datetime:
        if not deadlines or any(deadline.tzinfo is None for deadline in deadlines):
            raise IntakeError()
        ceiling = min(deadlines)
        if self.clock() >= ceiling:
            raise _forbidden()
        return ceiling

    async def _snapshot(self) -> tuple[AsyncConnection, Any, datetime]:
        """One REPEATABLE READ READ ONLY reader snapshot, so heads never tear."""
        manager = self.heads.engine.connect()
        connection = await manager.__aenter__()
        try:
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            _installed, until = await self.heads.qualified(connection)
        except BaseException:
            await manager.__aexit__(None, None, None)
            raise
        return manager, connection, until

    async def _policy_head(
        self, principal_ref: str, *, resource_kind: str, resource_ref: str, policy_ref: str
    ) -> DocumentPolicy:
        """Re-read the one policy head this upload is bound to; the browser names nothing."""
        manager, connection, until = await self._snapshot()
        try:
            if self.clock() >= until:
                raise _forbidden()
            try:
                payload, _row, deadline = await self.heads.head(connection, "document_policy", policy_ref)
            except AuthUnavailableError:
                raise _forbidden() from None
            if (
                type(payload) is not DocumentPolicy
                or (payload.resource_kind, payload.resource_ref) != (resource_kind, resource_ref)
                or payload.complete
                # `recipient_principal_refs` names PRINCIPAL refs (auth_profile), so the
                # recipient check uses the ref, never the identity digest the rows store.
                or principal_ref not in payload.recipient_principal_refs
                or self.clock() >= min(payload.valid_until, deadline, until)
            ):
                raise _forbidden()
            return payload
        finally:
            await manager.__aexit__(None, None, None)

    async def _custody_head(
        self, connection: AsyncConnection, document_ref: str
    ) -> tuple[DocumentCustody, datetime] | None:
        """The verified published custody head, or None when none is currently verifiable.

        Omission is the fail-closed direction for a projection: a document whose head
        cannot be verified is reported as NOT verified, never as verified.
        """
        try:
            payload, _row, deadline = await self.heads.head(connection, "document_custody", document_ref)
        except AuthUnavailableError:
            return None
        if type(payload) is not DocumentCustody or payload.document.document_ref != document_ref:
            return None
        return payload, deadline

    async def _published_custody(self, document_ref: str) -> tuple[DocumentCustody, datetime] | None:
        manager, connection, until = await self._snapshot()
        try:
            if self.clock() >= until:
                raise IntakeError()
            return await self._custody_head(connection, document_ref)
        finally:
            await manager.__aexit__(None, None, None)

    @staticmethod
    def _receipt(upload_ref: str, custody: DocumentCustody | None, document_ref: str | None) -> UploadReceipt:
        """The closed receipt vocabulary, derived only from a custody state."""
        if custody is None or custody.screening_result == "pending":
            return UploadReceipt(upload_ref=upload_ref, disposition="screening", document_ref=None)
        if custody.custody_state != "available":
            # A revoked or deleted document can no longer back an upload.
            return UploadReceipt(upload_ref=upload_ref, disposition="rejected", document_ref=None)
        if custody.screening_result == "clean":
            return UploadReceipt(upload_ref=upload_ref, disposition="verified", document_ref=document_ref)
        return UploadReceipt(
            upload_ref=upload_ref,
            disposition="quarantined" if custody.screening_result == "quarantined" else "rejected",
            document_ref=None,
        )

    def _values(self, **extra: Any) -> dict[str, Any]:
        return {"tenant": self.scope.tenant, "environment": self.scope.environment, **extra}

    @asynccontextmanager
    async def _store_transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with transaction(self.engine, self.seconds) as connection:
                yield connection
        except ExternalCaseError as error:
            # The store's closed vocabulary, translated once at this plane's boundary.
            raise _translated(error.code) from None

    async def _upload_row(self, connection: AsyncConnection, upload_ref: str) -> Any:
        return (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM portal_document.upload WHERE tenant=:tenant AND "
                        "environment=:environment AND upload_ref=:upload"
                    ),
                    self._values(upload=upload_ref),
                )
            )
            .mappings()
            .one_or_none()
        )

    # --- protocol ----------------------------------------------------------------

    async def store(self, upload_ref: str, principal: HumanPrincipal, raw: bytes) -> str:
        """Seal the delivered bytes onto the PHI plane; the browser cannot complete an
        empty slot, and a stored upload can never be silently replaced.

        This is the one write of the PHI plane and it is reachable only from the PHI
        application: the General BFF never sees `raw`. The row's creator, the pending
        state and the published policy are re-verified here, so a stored object is
        always the creator's own bytes under a policy that is still open.
        """
        creator = principal_digest(principal)
        if principal.tenant != self.scope.tenant:
            raise _forbidden()
        raw = bounded_document(raw)
        digest_hex = content_digest(raw)
        async with self._store_transaction() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {"lock": canonicalize([self.scope.tenant, "document-store", upload_ref]).decode()},
            )
            upload = await self._upload_row(connection, upload_ref)
            if upload is None:
                raise IntakeError("resource_unavailable")
            if upload["creator_identity_digest"] != creator or upload["state"] != "awaiting_content":
                raise _refuse("denied")
            await self._policy_head(
                principal.principal_ref,
                resource_kind=upload["resource_kind"],
                resource_ref=upload["resource_ref"],
                policy_ref=upload["policy_ref"],
            )
            stored = (
                (
                    await connection.execute(
                        text(
                            "SELECT content_sha256 FROM portal_document.object WHERE tenant=:tenant "
                            "AND environment=:environment AND upload_ref=:upload"
                        ),
                        self._values(upload=upload_ref),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if stored is not None:
                if stored["content_sha256"] != digest_hex:
                    raise IntakeError("conflict")
                return digest_hex
            nonce, ciphertext = self.keys.seal(
                upload["key_id"],
                raw,
                {
                    "tenant": self.scope.tenant,
                    "environment": self.scope.environment,
                    "resource_kind": upload["resource_kind"],
                    "resource_ref": upload["resource_ref"],
                    "upload_ref": upload_ref,
                    "document_type_ref": upload["document_type_ref"],
                    "command_id": upload["command_id"],
                    "content_sha256": digest_hex,
                    "key_id": upload["key_id"],
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO portal_document.object (tenant,environment,upload_ref,key_id,nonce,"
                    "ciphertext,content_sha256) VALUES (:tenant,:environment,:upload,:key,:nonce,"
                    ":ciphertext,:digest)"
                ),
                self._values(
                    upload=upload_ref,
                    key=upload["key_id"],
                    nonce=nonce,
                    ciphertext=ciphertext,
                    digest=digest_hex,
                ),
            )
        return digest_hex

    async def initiate(self, grant: DocumentGrant, request: UploadInitiation) -> UploadReceipt:
        creator = self._creator(grant, "initiate_upload")
        if grant.access.resource_kind not in {"intake", "case"}:
            raise _forbidden()
        policy = await self._policy_head(
            grant.access.principal.principal_ref,
            resource_kind=grant.access.resource_kind,
            resource_ref=grant.access.resource_ref,
            policy_ref=request.policy_ref,
        )
        if request.document_type_ref not in policy.required_codes:
            raise _forbidden()
        policy_digest = digest(policy)
        admitted = digest(
            {"schema": _UPLOADED, "access": grant.access, "request": request, "policy": policy_digest}
        )
        upload_ref, key_id = uuid4().hex, self.keys.active_key_id
        valid_until = self._alive(grant.valid_until, policy.valid_until, self.keys.valid_until)
        async with self._store_transaction() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {
                    "lock": canonicalize(
                        [self.scope.tenant, "document-upload", creator, request.command_id]
                    ).decode()
                },
            )
            existing = await self._by_command(connection, creator, request.command_id)
            if existing is not None:
                # The same command IS the same upload, never a second slot; any other
                # reinterpretation of the command is a conflict, not a new admission.
                if (existing["resource_ref"], existing["admitted_digest"]) != (
                    grant.access.resource_ref,
                    admitted,
                ):
                    raise _refuse("conflict")
                return UploadReceipt(upload_ref=existing["upload_ref"], disposition="awaiting_content")
            await connection.execute(
                text(
                    "INSERT INTO portal_document.upload (tenant,environment,upload_ref,resource_kind,"
                    "resource_ref,policy_ref,policy_digest,document_type_ref,creator_identity_digest,"
                    "command_id,admitted_digest,state,document_ref,key_id,valid_until) VALUES "
                    "(:tenant,:environment,:upload,:rkind,:rref,:policy,:pdigest,:dtype,:creator,"
                    ":command,:admitted,'awaiting_content',NULL,:key,:until)"
                ),
                self._values(
                    upload=upload_ref,
                    rkind=grant.access.resource_kind,
                    rref=grant.access.resource_ref,
                    policy=request.policy_ref,
                    pdigest=policy_digest,
                    dtype=request.document_type_ref,
                    creator=creator,
                    command=request.command_id,
                    admitted=admitted,
                    key=key_id,
                    until=valid_until,
                ),
            )
        return UploadReceipt(upload_ref=upload_ref, disposition="awaiting_content")

    async def _by_command(self, connection: AsyncConnection, creator: str, command_id: str) -> Any:
        return (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM portal_document.upload WHERE tenant=:tenant AND "
                        "environment=:environment AND creator_identity_digest=:creator AND "
                        "command_id=:command"
                    ),
                    self._values(creator=creator, command=command_id),
                )
            )
            .mappings()
            .one_or_none()
        )

    async def complete(self, grant: DocumentGrant, request: UploadCompletion) -> UploadReceipt:
        creator = self._creator(grant, "complete_upload")
        upload_ref = grant.access.resource_ref
        document_ref: str | None = None
        async with self._store_transaction() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {"lock": canonicalize([self.scope.tenant, "document-complete", upload_ref]).decode()},
            )
            upload = await self._upload_row(connection, upload_ref)
            if upload is None:
                raise _refuse("unavailable")
            if upload["creator_identity_digest"] != creator:
                raise _refuse("denied")
            if upload["command_id"] != request.command_id:
                raise _refuse("conflict")
            if upload["state"] == "awaiting_content":
                stored = (
                    (
                        await connection.execute(
                            text(
                                "SELECT content_sha256 FROM portal_document.object WHERE tenant=:tenant "
                                "AND environment=:environment AND upload_ref=:upload"
                            ),
                            self._values(upload=upload_ref),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if stored is None:
                    # The bytes were never delivered to the PHI plane; a browser cannot
                    # complete an empty slot.
                    raise _refuse("conflict")
                policy = await self._policy_head(
                    grant.access.principal.principal_ref,
                    resource_kind=upload["resource_kind"],
                    resource_ref=upload["resource_ref"],
                    policy_ref=upload["policy_ref"],
                )
                if digest(policy) != upload["policy_digest"]:
                    # The published policy moved since initiation; re-admission required.
                    raise _refuse("conflict")
                document_ref = uuid4().hex
                await connection.execute(
                    text(
                        "INSERT INTO portal_document.document (tenant,environment,document_ref,"
                        "upload_ref,resource_kind,resource_ref,document_type_ref,content_sha256,"
                        "creator_identity_digest,custody_revision,storage_version_ref,policy_ref,"
                        "policy_digest,completed_at) VALUES (:tenant,:environment,:document,:upload,"
                        ":rkind,:rref,:dtype,:content,:creator,0,:storage,:policy,:pdigest,:at)"
                    ),
                    self._values(
                        document=document_ref,
                        upload=upload_ref,
                        rkind=upload["resource_kind"],
                        rref=upload["resource_ref"],
                        dtype=upload["document_type_ref"],
                        content=stored["content_sha256"],
                        creator=creator,
                        storage=upload_ref,
                        policy=upload["policy_ref"],
                        pdigest=upload["policy_digest"],
                        at=self.clock(),
                    ),
                )
                await connection.execute(
                    text(
                        "INSERT INTO portal_document.custody (tenant,environment,document_ref,"
                        "resource_kind,resource_ref,creator_identity_digest,screening_ref,"
                        "screening_revision,screening_result,custody_state,head_generation,"
                        "publication_digest,valid_until) VALUES (:tenant,:environment,:document,"
                        ":rkind,:rref,:creator,NULL,0,'pending','available',NULL,NULL,:until)"
                    ),
                    self._values(
                        document=document_ref,
                        rkind=upload["resource_kind"],
                        rref=upload["resource_ref"],
                        creator=creator,
                        until=upload["valid_until"],
                    ),
                )
                await connection.execute(
                    text(
                        "UPDATE portal_document.upload SET state='screening', document_ref=:document "
                        "WHERE tenant=:tenant AND environment=:environment AND upload_ref=:upload"
                    ),
                    self._values(document=document_ref, upload=upload_ref),
                )
            else:
                # Idempotent replay: the completion already happened, so the answer is the
                # CURRENT truth about that ONE document — no second row, no new effect.
                document_ref = upload["document_ref"]
        self._alive(grant.valid_until, upload["valid_until"])
        found = await self._published_custody(document_ref)
        return self._receipt(upload_ref, None if found is None else found[0], document_ref)

    async def documents(self, grant: DocumentGrant) -> DocumentPage:
        self._creator(grant, "list_documents")
        case_ref = grant.access.resource_ref
        async with self._store_transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT document_ref,document_type_ref FROM portal_document.document WHERE "
                            "tenant=:tenant AND environment=:environment AND resource_kind='case' AND "
                            "resource_ref=:case ORDER BY document_ref"
                        ),
                        self._values(case=case_ref),
                    )
                )
                .mappings()
                .all()
            )
        manager, reader, until = await self._snapshot()
        try:
            if self.clock() >= until:
                raise IntakeError()
            verified: list[DocumentSummary] = []
            for row in rows:
                found = await self._custody_head(reader, row["document_ref"])
                if found is None:
                    continue
                custody, deadline = found
                if (
                    custody.screening_result == "clean"
                    and custody.custody_state == "available"
                    and custody.resource_ref == case_ref
                    and self.clock() < min(custody.valid_until, deadline)
                ):
                    verified.append(
                        DocumentSummary(
                            document_ref=row["document_ref"],
                            document_type_ref=row["document_type_ref"],
                            # A listing can only ever say "verified": anything else is
                            # omitted above, never softened into a lesser disposition.
                            disposition="verified",
                        )
                    )
        finally:
            await manager.__aexit__(None, None, None)
        return DocumentPage(case_ref=case_ref, documents=tuple(verified))

    async def requests(self, grant: DocumentGrant) -> DocumentRequestPage:
        self._creator(grant, "list_requests")
        case_ref = grant.access.resource_ref
        manager, reader, until = await self._snapshot()
        try:
            if self.clock() >= until:
                raise IntakeError()
            heads = await case_policy_heads(reader, case_ref)
        finally:
            await manager.__aexit__(None, None, None)
        summaries = []
        for policy_ref, policy, deadline in heads:
            if policy.request_ref is None:  # intake policies never answer a case request
                raise IntakeError()
            summaries.append(
                DocumentRequestSummary(
                    request_ref=policy.request_ref,
                    revision=str(policy.request_revision),
                    disposition=(
                        "answered" if policy.complete else "expired" if self.clock() >= deadline else "open"
                    ),
                    policy_ref=policy_ref,
                    requested_document_type_refs=policy.missing_codes or policy.required_codes,
                )
            )
        return DocumentRequestPage(case_ref=case_ref, requests=tuple(summaries))

    async def download(self, grant: DocumentGrant) -> str:
        """Return the SAME opaque document_ref; the bytes live behind the PHI route."""
        self._creator(grant, "download")
        document_ref = grant.access.resource_ref
        found = await self._published_custody(document_ref)
        custody = None if found is None else found[0]
        if custody is None or custody.screening_result != "clean" or custody.custody_state != "available":
            raise _forbidden()
        return document_ref

    async def respond(self, grant: DocumentGrant, request: DocumentResponse) -> DocumentResponseReceipt:
        self._creator(grant, "respond")
        case_ref, request_ref = grant.access.case_ref, grant.access.resource_ref
        admitted = digest({"schema": _RESPONDED, "access": grant.access, "request": request})
        response_ref, key_id = uuid4().hex, self.keys.active_key_id
        manager, reader, until = await self._snapshot()
        try:
            if self.clock() >= until:
                raise IntakeError()
            deadlines: list[datetime] = []
            for document_ref in request.document_refs:
                found = await self._custody_head(reader, document_ref)
                if found is None:
                    # A quarantined, revoked, pending or foreign-case document never
                    # satisfies a request; no subset is admitted instead.
                    raise _forbidden()
                custody, deadline = found
                if (
                    custody.screening_result != "clean"
                    or custody.custody_state != "available"
                    or custody.resource_kind != "case"
                    or custody.resource_ref != case_ref
                    or self.clock() >= deadline
                ):
                    raise _forbidden()
                deadlines.append(min(custody.valid_until, deadline))
        finally:
            await manager.__aexit__(None, None, None)
        valid_until = self._alive(grant.valid_until, *deadlines)
        async with self._store_transaction() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {"lock": canonicalize([self.scope.tenant, "document-response", request.command_id]).decode()},
            )
            stored = set(
                (
                    await connection.execute(
                        text(
                            "SELECT document_ref FROM portal_document.document WHERE tenant=:tenant AND "
                            "environment=:environment AND resource_kind='case' AND resource_ref=:case"
                        ),
                        self._values(case=case_ref),
                    )
                )
                .scalars()
                .all()
            )
            if any(ref not in stored for ref in request.document_refs):
                raise _refuse("denied")
            prior = (
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM portal_document.response WHERE tenant=:tenant AND "
                            "environment=:environment AND command_id=:command"
                        ),
                        self._values(command=request.command_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                if (
                    prior["principal_ref"],
                    prior["case_ref"],
                    prior["request_ref"],
                    prior["admitted_digest"],
                ) != (grant.access.principal.principal_ref, case_ref, request_ref, admitted):
                    raise _refuse("conflict")
                response_ref = prior["response_ref"]
            else:
                # Every field of RESPONSE_AAD is bound, so a value that ever drifts from
                # the sealed set is a typing error, not a silently weaker binding.
                aad: dict[str, str] = dict.fromkeys(RESPONSE_AAD, "")
                aad.update(
                    tenant=self.scope.tenant,
                    environment=self.scope.environment,
                    case_ref=case_ref or "",
                    request_ref=request_ref,
                    response_ref=response_ref,
                    command_id=request.command_id,
                    admitted_digest=admitted,
                    key_id=key_id,
                )
                if set(aad) != set(RESPONSE_AAD) or "" in aad.values():
                    raise _refuse("invalid")
                nonce, ciphertext = self.keys.seal(
                    key_id,
                    canonicalize(wire({"schema": _RESPONDED, "access": grant.access, "request": request})),
                    aad,
                )
                await connection.execute(
                    text(
                        "INSERT INTO portal_document.response (tenant,environment,response_ref,command_id,"
                        "case_ref,request_ref,request_revision,principal_ref,admitted_digest,key_id,nonce,"
                        "ciphertext,valid_until) VALUES (:tenant,:environment,:response,:command,:case,"
                        ":request,:revision,:principal,:admitted,:key,:nonce,:ciphertext,:until)"
                    ),
                    self._values(
                        response=response_ref,
                        command=request.command_id,
                        case=case_ref,
                        request=request_ref,
                        revision=int(request.expected_revision),
                        principal=grant.access.principal.principal_ref,
                        admitted=admitted,
                        key=key_id,
                        nonce=nonce,
                        ciphertext=ciphertext,
                        until=valid_until,
                    ),
                )
        return DocumentResponseReceipt(
            command_id=request.command_id,
            request_ref=request_ref,
            revision=request.expected_revision,
            disposition="admitted",
            correlation_receipt_ref=None,
        )

    # --- PHI plane ---------------------------------------------------------------

    async def open(self, document_ref: str) -> bytes:
        """Decrypt stored bytes for the PHI application; never a General-zone path.

        The caller has already authorized this exact document against the published
        heads; this method still refuses anything the durable row cannot corroborate.
        """
        async with self._store_transaction() as connection:
            document = (
                (
                    await connection.execute(
                        text(
                            "SELECT d.upload_ref,d.content_sha256,d.resource_kind,d.resource_ref,"
                            "d.document_type_ref,u.command_id,u.key_id,o.nonce,o.ciphertext FROM "
                            "portal_document.document d JOIN portal_document.upload u ON "
                            "u.tenant=d.tenant AND u.environment=d.environment AND "
                            "u.upload_ref=d.upload_ref JOIN portal_document.object o ON "
                            "o.tenant=d.tenant AND o.environment=d.environment AND "
                            "o.upload_ref=d.upload_ref WHERE d.tenant=:tenant AND "
                            "d.environment=:environment AND d.document_ref=:document"
                        ),
                        self._values(document=document_ref),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document is None:
                raise _refuse("unavailable")
            raw = bounded_document(
                self.keys.unseal(
                    document["key_id"],
                    document["nonce"],
                    document["ciphertext"],
                    {
                        "tenant": self.scope.tenant,
                        "environment": self.scope.environment,
                        "resource_kind": document["resource_kind"],
                        "resource_ref": document["resource_ref"],
                        "upload_ref": document["upload_ref"],
                        "document_type_ref": document["document_type_ref"],
                        "command_id": document["command_id"],
                        "content_sha256": document["content_sha256"],
                        "key_id": document["key_id"],
                    },
                )
            )
        if content_digest(raw) != document["content_sha256"]:
            raise IntakeError()
        return raw


def _conformance(provider: PostgresProtectedDocumentProvider) -> ProtectedDocumentProvider:
    """Static proof for mypy that the production class satisfies the protocol."""
    return provider
