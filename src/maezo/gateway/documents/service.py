"""Typed authorization/custody orchestration for human document exchange.

PHI storage, screening/policy and current resource authority are mandatory concrete
deployment providers. This service is no storage or native-effect substitute.
Response admission cannot claim correlation unless the effect provider returns an
authenticated native receipt. Raw bytes never enter this General-zone service.
"""

import secrets
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator

from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.documents import (
    DocumentPage,
    DocumentRequestPage,
    DocumentResponse,
    DocumentResponseReceipt,
    UploadCompletion,
    UploadInitiation,
    UploadReceipt,
)
from maezo.portal.contracts.intake import Closed, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal, Sha256Digest

Operation = Literal[
    "initiate_upload", "complete_upload", "list_documents", "list_requests", "download", "respond"
]


class DocumentAccess(Closed):
    principal: HumanPrincipal = Field(repr=False)
    operation: Operation
    resource_kind: Literal["intake", "case", "upload", "document", "request"]
    resource_ref: ResourceRef
    case_ref: ResourceRef | None = None


class DocumentGrant(Closed):
    access: DocumentAccess
    authority_digest: Sha256Digest
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("aware deadline required")
        return value


class DocumentAuthority(Protocol):
    async def authorize(self, access: DocumentAccess) -> DocumentGrant:
        """Verify exact action, fields, resource, consent and membership currently."""
        ...


class ProtectedDocumentProvider(Protocol):
    async def initiate(self, grant: DocumentGrant, request: UploadInitiation) -> UploadReceipt: ...
    async def complete(self, grant: DocumentGrant, request: UploadCompletion) -> UploadReceipt:
        """Verify stored bytes, policy and screening; never trust browser completion."""
        ...

    async def documents(self, grant: DocumentGrant) -> DocumentPage: ...
    async def requests(self, grant: DocumentGrant) -> DocumentRequestPage: ...
    async def download(self, grant: DocumentGrant) -> str:
        """Authorize protected delivery and return the same document_ref, never bytes/URL."""
        ...

    async def respond(self, grant: DocumentGrant, request: DocumentResponse) -> DocumentResponseReceipt:
        """Durable exact-request admission with custody, no broad message correlation."""
        ...


class DocumentService:
    def __init__(
        self,
        resolver: HumanSessionResolver,
        authority: DocumentAuthority,
        provider: ProtectedDocumentProvider,
    ) -> None:
        self.resolver, self.authority, self.provider = resolver, authority, provider

    async def execute(
        self,
        secret: str,
        *,
        operation: Operation,
        resource_kind: Literal["intake", "case", "upload", "document", "request"],
        resource_ref: str,
        case_ref: str | None = None,
        body: UploadInitiation | UploadCompletion | DocumentResponse | None = None,
        csrf: str | None = None,
        origin: str | None = None,
    ) -> UploadReceipt | DocumentPage | DocumentRequestPage | DocumentResponseReceipt | str:
        session = await self.resolver.resolve(secret)
        mutation = operation in {"initiate_upload", "complete_upload", "respond"}
        if mutation and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise IntakeError("authentication_unavailable")
        access = DocumentAccess(
            principal=session.principal,
            operation=operation,
            resource_kind=resource_kind,
            resource_ref=resource_ref,
            case_ref=case_ref,
        )
        grant = await self.authority.authorize(access)
        if grant.access != access or datetime.now(UTC) >= grant.valid_until:
            raise IntakeError("operation_forbidden")
        current = await self.resolver.resolve(secret)
        if current.principal != session.principal or datetime.now(UTC) >= grant.valid_until:
            raise IntakeError("operation_forbidden")
        result: UploadReceipt | DocumentPage | DocumentRequestPage | DocumentResponseReceipt | str
        if (
            operation == "initiate_upload"
            and isinstance(body, UploadInitiation)
            and resource_kind in {"case", "intake"}
        ):
            result = await self.provider.initiate(grant, body)
        elif (
            operation == "complete_upload"
            and isinstance(body, UploadCompletion)
            and resource_kind == "upload"
        ):
            result = await self.provider.complete(grant, body)
            if result.upload_ref != resource_ref:
                raise IntakeError("conflict")
        elif operation == "list_documents" and body is None and resource_kind == "case":
            result = await self.provider.documents(grant)
            if result.case_ref != resource_ref:
                raise IntakeError("conflict")
        elif operation == "list_requests" and body is None and resource_kind == "case":
            result = await self.provider.requests(grant)
            if result.case_ref != resource_ref:
                raise IntakeError("conflict")
        elif operation == "download" and body is None and resource_kind == "document":
            result = await self.provider.download(grant)
            if result != resource_ref:
                raise IntakeError("conflict")
        elif (
            operation == "respond"
            and isinstance(body, DocumentResponse)
            and resource_kind == "request"
            and case_ref is not None
        ):
            result = await self.provider.respond(grant, body)
            if result.request_ref != resource_ref or result.command_id != body.command_id:
                raise IntakeError("conflict")
        else:
            raise IntakeError("invalid_request")
        current = await self.resolver.resolve(secret)
        final = await self.authority.authorize(access)
        if (
            current.principal != session.principal
            or final.access != access
            or final.authority_digest != grant.authority_digest
            or datetime.now(UTC) >= min(grant.valid_until, final.valid_until)
        ):
            raise IntakeError("operation_forbidden")
        return result
