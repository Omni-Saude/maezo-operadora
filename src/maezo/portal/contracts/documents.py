"""Document exchange DTOs; custody, response correlation and completeness are distinct."""

from typing import Literal, Self

from pydantic import field_validator, model_validator

from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef


class UploadInitiation(Closed):
    command_id: ResourceRef
    policy_ref: ResourceRef
    document_type_ref: ResourceRef


class UploadReceipt(Closed):
    upload_ref: ResourceRef
    disposition: Literal["awaiting_content", "screening", "verified", "quarantined", "rejected"]
    document_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def verified_only(self) -> Self:
        if (self.disposition == "verified") != (self.document_ref is not None):
            raise ValueError("verified custody required")
        return self


class UploadCompletion(Closed):
    command_id: ResourceRef


class DocumentResponse(Closed):
    command_id: ResourceRef
    expected_revision: DecimalRevision
    document_refs: tuple[ResourceRef, ...]

    @field_validator("document_refs", mode="before")
    @classmethod
    def http_array(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if not self.document_refs or len(set(self.document_refs)) != len(self.document_refs):
            raise ValueError("distinct verified documents required")
        return self


class DocumentResponseReceipt(Closed):
    command_id: ResourceRef
    request_ref: ResourceRef
    revision: DecimalRevision
    disposition: Literal[
        "admitted", "waiting_for_subscription", "reconciling", "correlated", "conflict", "rejected"
    ]
    correlation_receipt_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def correlated_only(self) -> Self:
        if (self.disposition == "correlated") != (self.correlation_receipt_ref is not None):
            raise ValueError("correlation evidence required")
        return self


class DocumentSummary(Closed):
    document_ref: ResourceRef
    document_type_ref: ResourceRef
    disposition: Literal["verified"]


class DocumentPage(Closed):
    case_ref: ResourceRef
    documents: tuple[DocumentSummary, ...]


class DocumentRequestSummary(Closed):
    request_ref: ResourceRef
    revision: DecimalRevision
    disposition: Literal["open", "answered", "expired", "replaced"]
    policy_ref: ResourceRef
    requested_document_type_refs: tuple[ResourceRef, ...]


class DocumentRequestPage(Closed):
    case_ref: ResourceRef
    requests: tuple[DocumentRequestSummary, ...]
