"""Document metadata API; upload/download bytes use the separate same-origin PHI plane."""

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from maezo.gateway.documents.service import DocumentResult, DocumentService, Operation
from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.intakes import ERRORS, PREFIX, ProductRoute, reference, secret
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

DocumentServiceFactory = Callable[[HumanSessionResolver], DocumentService]
document_router = APIRouter(route_class=ProductRoute)


async def _execute(
    request: Request,
    operation: Operation,
    resource_kind: str,
    resource_ref: str,
    *,
    case_ref: str | None = None,
    body: UploadInitiation | UploadCompletion | DocumentResponse | None = None,
) -> Response:
    if request.scope.get("query_string", b""):
        raise IntakeError("invalid_request")
    factory: DocumentServiceFactory | None = request.app.state.document_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, DocumentService) or service.resolver is not resolver:
        raise IntakeError()
    from typing import Literal, cast

    def freeze(result: DocumentResult) -> Response:
        if isinstance(result, str):
            # Protected reverse-proxy route must independently enforce current authority;
            # this opaque path is not a signed URL or bearer grant and no raw bytes enter BFF.
            return RedirectResponse(
                "/api/v1/phi/documents/" + reference(result) + "/content", status_code=307
            )
        return Response(
            content=result.model_dump_json(),
            media_type="application/json",
            status_code=202 if request.method == "POST" else 200,
        )

    return await service.execute_frozen(
        secret(request),
        operation=operation,
        freeze=freeze,
        resource_kind=cast(Literal["intake", "case", "upload", "document", "request"], resource_kind),
        resource_ref=reference(resource_ref),
        case_ref=reference(case_ref) if case_ref else None,
        body=body,
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
    )


@document_router.post(
    PREFIX + "/intakes/{intake_ref}/document-uploads",
    response_model=UploadReceipt,
    status_code=202,
    responses=ERRORS,
)
async def intake_upload(request: Request, intake_ref: str, body: UploadInitiation) -> Response:
    return await _execute(request, "initiate_upload", "intake", intake_ref, body=body)


@document_router.post(
    PREFIX + "/cases/{case_ref}/document-uploads",
    response_model=UploadReceipt,
    status_code=202,
    responses=ERRORS,
)
async def case_upload(request: Request, case_ref: str, body: UploadInitiation) -> Response:
    return await _execute(request, "initiate_upload", "case", case_ref, body=body)


@document_router.post(
    PREFIX + "/document-uploads/{upload_ref}/complete",
    response_model=UploadReceipt,
    status_code=202,
    responses=ERRORS,
)
async def complete_upload(request: Request, upload_ref: str, body: UploadCompletion) -> Response:
    return await _execute(request, "complete_upload", "upload", upload_ref, body=body)


@document_router.get(PREFIX + "/cases/{case_ref}/documents", response_model=DocumentPage, responses=ERRORS)
async def documents(request: Request, case_ref: str) -> Response:
    return await _execute(request, "list_documents", "case", case_ref)


@document_router.get(
    PREFIX + "/cases/{case_ref}/document-requests", response_model=DocumentRequestPage, responses=ERRORS
)
async def requests(request: Request, case_ref: str) -> Response:
    return await _execute(request, "list_requests", "case", case_ref)


@document_router.post(
    PREFIX + "/cases/{case_ref}/document-requests/{request_ref}/responses",
    response_model=DocumentResponseReceipt,
    status_code=202,
    responses=ERRORS,
)
async def respond(request: Request, case_ref: str, request_ref: str, body: DocumentResponse) -> Response:
    return await _execute(request, "respond", "request", request_ref, case_ref=case_ref, body=body)


@document_router.get(PREFIX + "/documents/{document_ref}/content", status_code=307, responses=ERRORS)
async def download(request: Request, document_ref: str) -> Response:
    return await _execute(request, "download", "document", document_ref)
