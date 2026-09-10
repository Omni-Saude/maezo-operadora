"""Focused I1 deadline controls with synthetic identity, authority and durable ports."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.responses import Response

import maezo.gateway.documents.service as documents
import maezo.gateway.intake.service as intake
from maezo.gateway.intake.models import AdmissionGrant, IntakeError, request_bytes
from maezo.portal.contracts.documents import DocumentPage
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeReceipt
from maezo.portal.contracts.models import HumanPrincipal

START = datetime(2026, 9, 10, tzinfo=UTC)
REF = "a" * 32
ORIGIN = "https://portal.example.test"
PRINCIPAL = HumanPrincipal(
    schema_version=1,
    principal_ref="synthetic-principal",
    issuer="https://idp.example.test",
    subject="synthetic-subject",
    tenant="synthetic-tenant",
    membership_revision=1,
    memberships=(),
    session_ref="synthetic-session",
    authenticated_at=START,
    subject_bindings=(),
)
REQUEST = AuthIntakeSubmission(
    command_id=REF,
    beneficiary_ref=REF,
    provider_ref=REF,
    guide_ref=REF,
    codigo_procedimento_tuss="synthetic",
    categoria_procedimento="consulta",
    carater_atendimento="eletivo",
    valor_estimado_centavos="1",
    document_refs=(),
)
RECEIPT = IntakeReceipt(intake_ref=REF, command_id=REF, revision="0", disposition="admitted")


class Clock:
    value = START

    @classmethod
    def now(cls, tz=None):
        return cls.value


class Boundary:
    def __init__(self, ceiling, phase):
        self.phase = phase
        self.settings = SimpleNamespace(public_origin=ORIGIN)
        self.short = START + timedelta(seconds=1)
        self.long = START + timedelta(seconds=60)
        self.session = SimpleNamespace(
            principal=PRINCIPAL,
            record=SimpleNamespace(
                expires_at=self.short if ceiling == "session" else self.long, csrf_token="synthetic"
            ),
            membership=SimpleNamespace(
                audience="provider", reviewed_until=self.short if ceiling == "membership" else self.long
            ),
        )
        self.resolve_calls = self.authority_calls = self.provider_calls = 0
        self.durable = False

    def expire(self, phase):
        if self.phase == phase:
            Clock.value = START + timedelta(seconds=2)

    async def resolve(self, secret):
        self.resolve_calls += 1
        self.expire(
            "initial_resolver"
            if self.resolve_calls == 1
            else "before_provider"
            if self.resolve_calls == 2
            else "final_resolver"
        )
        return self.session

    async def authorize(self, access):
        self.authority_calls += 1
        self.expire("first_authority" if self.authority_calls == 1 else "final_authority")
        return documents.DocumentGrant(access=access, authority_digest="a" * 64, valid_until=self.long)

    async def admit(self, principal, request):
        self.expire("first_authority")
        return AdmissionGrant(
            principal=principal,
            request_digest=hashlib.sha256(request_bytes(request)).hexdigest(),
            guide_identity_ref=REF,
            authority_receipt_ref=REF,
            authority_digest="a" * 64,
            valid_until=self.long,
        )

    async def read(self, principal, ref):
        self.authority_calls += 1
        self.expire("first_authority" if self.authority_calls == 1 else "final_authority")
        return self.long

    async def documents(self, grant):
        self.provider_calls += 1
        assert grant.valid_until == self.short
        self.expire("provider")
        return DocumentPage(case_ref=REF, documents=())

    def freeze(self, value):
        response = Response(content=value.model_dump_json(), media_type="application/json")
        self.expire("serialization")
        return response


class Store:
    def __init__(self, boundary):
        self.boundary = boundary

    async def admit(self, grant, request):
        b = self.boundary
        b.provider_calls += 1
        assert grant.valid_until == b.short
        b.durable = True
        b.expire("provider")
        return RECEIPT

    async def read(self, *args):
        self.boundary.provider_calls += 1
        self.boundary.expire("provider")
        return RECEIPT


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", ["session", "membership"])
@pytest.mark.parametrize(
    "operation,phase",
    [
        ("read", "final_authority"),
        ("documents", "final_authority"),
        ("submit", "final_resolver"),
        ("read", "first_authority"),
        ("documents", "before_provider"),
        ("submit", "before_provider"),
        ("read", "provider"),
        ("documents", "provider"),
        ("submit", "provider"),
        ("read", "serialization"),
        ("documents", "serialization"),
        ("submit", "serialization"),
        ("read", "initial_resolver"),
        ("documents", "initial_resolver"),
        ("submit", "initial_resolver"),
    ],
)
async def test_identity_expiry_refuses_frozen_output_without_repeating_effect(
    monkeypatch, ceiling, operation, phase
):
    monkeypatch.setattr(intake, "datetime", Clock)
    monkeypatch.setattr(documents, "datetime", Clock)
    Clock.value = START
    b = Boundary(ceiling, phase)
    service = intake.IntakeService(b, b, Store(b))
    with pytest.raises(IntakeError):
        if operation == "read":
            await service.read_frozen("synthetic", REF, freeze=b.freeze)
        elif operation == "submit":
            await service.submit_frozen("synthetic", "synthetic", ORIGIN, REQUEST, freeze=b.freeze)
        else:
            await documents.DocumentService(b, b, b).execute_frozen(
                "synthetic",
                operation="list_documents",
                resource_kind="case",
                resource_ref=REF,
                freeze=b.freeze,
            )
    assert Clock.value >= b.short
    assert b.provider_calls <= 1
    if phase in {"initial_resolver", "before_provider", "first_authority"}:
        assert b.provider_calls == 0
    if operation == "submit" and phase in {"provider", "final_resolver", "serialization"}:
        assert b.durable and b.provider_calls == 1  # Refusal does not undo/repeat acknowledged admission.


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "documents", "submit"])
async def test_current_response_freezes_once_and_preserves_admission(monkeypatch, operation):
    monkeypatch.setattr(intake, "datetime", Clock)
    monkeypatch.setattr(documents, "datetime", Clock)
    Clock.value = START
    b = Boundary("session", "never")
    service = intake.IntakeService(b, b, Store(b))
    if operation == "read":
        result = await service.read_frozen("synthetic", REF, freeze=b.freeze)
    elif operation == "submit":
        result = await service.submit_frozen("synthetic", "synthetic", ORIGIN, REQUEST, freeze=b.freeze)
    else:
        result = await documents.DocumentService(b, b, b).execute_frozen(
            "synthetic",
            operation="list_documents",
            resource_kind="case",
            resource_ref=REF,
            freeze=b.freeze,
        )
    assert isinstance(result, Response) and b.provider_calls == 1
    assert b.durable == (operation == "submit")
