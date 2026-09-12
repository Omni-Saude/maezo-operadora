"""New orchestration controls; underlying providers here are explicitly synthetic."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from tests.unit.gateway.intake.test_postgres import inputs
from tests.unit.portal.test_intake_documents import REF, REF2

from maezo.gateway.documents.service import DocumentGrant, DocumentService
from maezo.gateway.external_cases.models import ExternalCaseError, instant
from maezo.gateway.intake.cases import CaseService
from maezo.gateway.intake.models import IntakeError
from maezo.portal.contracts.documents import (
    DocumentResponse,
    DocumentResponseReceipt,
    UploadCompletion,
    UploadReceipt,
)
from maezo.portal.engine.profile import canonicalize


class Resolver:
    def __init__(self):
        self.settings = SimpleNamespace(
            tenant="synthetic-tenant",
            issuer="https://idp.example.test",
            public_origin="https://portal.example.test",
        )
        self.principal = inputs()[0].principal
        self.audience = "provider"
        self.revoked = False

    async def resolve(self, secret):
        if self.revoked:
            raise IntakeError("authentication_unavailable")
        until = datetime.now(UTC) + timedelta(minutes=1)
        return SimpleNamespace(
            principal=self.principal,
            membership=SimpleNamespace(audience=self.audience, reviewed_until=until),
            record=SimpleNamespace(expires_at=until, csrf_token="synthetic-csrf"),
        )


class Lease:
    tenant = "synthetic-tenant"
    issuer = "https://idp.example.test"
    finalized = False

    async def finalize_frozen(self, secret, principal, **values):
        self.finalized = True
        return values["frozen_projection"]


class Native:
    def __init__(self):
        self.extra = False
        self.wrong_case = False

    async def observe(self, **request):
        now = datetime.now(UTC)
        item = dict(
            case_ref=REF2 if self.wrong_case else REF,
            kind="authorization",
            state="active",
            record_revision="1",
            state_observed_at=instant(now),
        )
        if self.extra:
            item["clinical_notes"] = "PRIVATE_CANARY"
        return canonicalize(
            {
                "schema": "portal-external-case-observation.v1",
                "continuity_proof": "synthetic-proof",
                "projection": {
                    "schema": "portal-external-case-detail.v1",
                    "case": item,
                    "allowed_actions": [],
                    "freshness": {
                        "observed_at": instant(now),
                        "valid_until": instant(now + timedelta(seconds=5)),
                    },
                },
            }
        )


@pytest.mark.asyncio
async def test_case_projection_freezes_then_finalizes_exact_bytes():
    lease = Lease()
    service = CaseService(Resolver(), lease, Native())
    raw = await service.read("synthetic", case_ref=REF)
    assert lease.finalized and REF.encode() in raw
    assert b"synthetic-proof" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["extra", "wrong_case"])
async def test_case_projection_never_infers_more_than_w6(change):
    lease, native = Lease(), Native()
    setattr(native, change, True)
    service = CaseService(Resolver(), lease, native)
    with pytest.raises(ExternalCaseError):
        await service.read("synthetic", case_ref=REF)
    assert not lease.finalized


@pytest.mark.asyncio
async def test_staff_cannot_reuse_external_case_authority():
    resolver, lease = Resolver(), Lease()
    resolver.audience = "staff"
    with pytest.raises(ExternalCaseError):
        await CaseService(resolver, lease, Native()).read("synthetic", case_ref=REF)
    assert not lease.finalized


class Authority:
    revoked = False

    async def authorize(self, access):
        if self.revoked:
            raise IntakeError("operation_forbidden")
        return DocumentGrant(
            access=access, authority_digest="a" * 64, valid_until=datetime.now(UTC) + timedelta(minutes=1)
        )


class Provider:
    called = False
    quarantine = False
    wrong_ref = False

    def __init__(self, authority):
        self.authority = authority
        self.revoke_after = False

    async def complete(self, grant, request):
        self.called = True
        if self.revoke_after:
            self.authority.revoked = True
        return UploadReceipt(
            upload_ref=REF2 if self.wrong_ref else REF,
            disposition="quarantined" if self.quarantine else "verified",
            document_ref=None if self.quarantine else REF2,
        )

    async def respond(self, grant, request):
        self.called = True
        assert grant.access.case_ref == REF2
        return DocumentResponseReceipt(
            command_id=request.command_id,
            request_ref=grant.access.resource_ref,
            revision=request.expected_revision,
            disposition="waiting_for_subscription",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["verified", "quarantine", "revoked", "wrong_ref", "bad_csrf"])
async def test_document_completion_preserves_resource_and_current_authority(state):
    authority = Authority()
    provider = Provider(authority)
    provider.quarantine = state == "quarantine"
    provider.revoke_after = state == "revoked"
    provider.wrong_ref = state == "wrong_ref"
    service = DocumentService(Resolver(), authority, provider)
    args = dict(
        operation="complete_upload",
        resource_kind="upload",
        resource_ref=REF,
        body=UploadCompletion(command_id=REF2),
        csrf="bad" if state == "bad_csrf" else "synthetic-csrf",
        origin="https://portal.example.test",
    )
    if state in {"revoked", "wrong_ref", "bad_csrf"}:
        with pytest.raises(IntakeError):
            await service.execute("synthetic", **args)
        if state == "bad_csrf":
            assert not provider.called
    else:
        result = await service.execute("synthetic", **args)
        assert result.disposition == ("quarantined" if state == "quarantine" else "verified")
        assert "documentacao_completa" not in result.model_dump()


@pytest.mark.asyncio
async def test_response_preserves_request_revision_case_and_pending_subscription():
    authority = Authority()
    service = DocumentService(Resolver(), authority, Provider(authority))
    result = await service.execute(
        "synthetic",
        operation="respond",
        resource_kind="request",
        resource_ref=REF,
        case_ref=REF2,
        body=DocumentResponse(command_id=REF2, expected_revision="9", document_refs=(REF2,)),
        csrf="synthetic-csrf",
        origin="https://portal.example.test",
    )
    assert result.disposition == "waiting_for_subscription" and result.correlation_receipt_ref is None
    assert result.revision == "9"
