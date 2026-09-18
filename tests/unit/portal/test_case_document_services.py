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
    """Motor sintetico. `state`/`outcome` sao estado do motor, jamais inferidos pelo servico."""

    def __init__(self):
        self.extra = False
        self.wrong_case = False
        self.extra_outcome = False
        self.state = "active"
        # None = a chave `outcome` vai nula no fio (caso em andamento).
        self.outcome: dict[str, object] | None = None
        self.omit_outcome = False

    async def observe(self, **request):
        now = datetime.now(UTC)
        item = dict(
            case_ref=REF2 if self.wrong_case else REF,
            kind="authorization",
            state=self.state,
            record_revision="1",
            state_observed_at=instant(now),
        )
        if self.extra:
            item["clinical_notes"] = "PRIVATE_CANARY"
        outcome = self.outcome
        if self.extra_outcome and outcome is not None:
            outcome = dict(outcome, justificativa_clinica="PRIVATE_CANARY")
        projection: dict[str, object] = {
            "schema": "portal-external-case-detail.v1",
            "case": item,
            "allowed_actions": [],
            "freshness": {
                "observed_at": instant(now),
                "valid_until": instant(now + timedelta(seconds=5)),
            },
            "outcome": outcome,
        }
        if self.omit_outcome:
            del projection["outcome"]
        return canonicalize(
            {
                "schema": "portal-external-case-observation.v1",
                "continuity_proof": "synthetic-proof",
                "projection": projection,
            }
        )


APROVADA = {
    "phase": "decisao_executada",
    "desfecho": "aprovada_auditor",
    "authorization_ref": "b" * 64,
}


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
async def test_case_outcome_projects_engine_desfecho_without_the_tiss_number():
    lease, native = Lease(), Native()
    native.state, native.outcome = "ended", APROVADA
    raw = await (CaseService(Resolver(), lease, native)).read("synthetic", case_ref=REF)
    assert lease.finalized
    assert b'"desfecho":"aprovada_auditor"' in raw and b'"phase":"decisao_executada"' in raw
    # `numero_autorizacao = AUTH-{tenant}-{guia}-{uuid8}` (`tools/workers/auth.py:1336`)
    # nao cruza a fronteira externa: o grant de divulgacao de recibo esta diferido.
    assert b"AUTH-" not in raw and ("b" * 64).encode() in raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "outcome", "omit", "extra_outcome"),
    [
        # Fecha por ausencia: encerrado sem registro de decisao nao vira projecao.
        ("ended", None, False, False),
        # Desfecho sem encerramento seria inferencia de estado do motor.
        ("active", APROVADA, False, False),
        # Chave obrigatoria no fio: produtor que a omite e recusado, nao defaultado.
        ("active", None, True, False),
        # Nenhum campo alem de phase/desfecho/authorization_ref atravessa.
        ("ended", APROVADA, False, True),
        # BPMN :546 nao declara `numero_autorizacao` para a negativa.
        ("ended", {**APROVADA, "desfecho": "negada_auditor"}, False, False),
        # BPMN :505 declara `numero_autorizacao` para a aprovacao do auditor.
        ("ended", {**APROVADA, "authorization_ref": None}, False, False),
        # Vocabulario fechado: so os cinco `event_desfecho` do BPMN.
        ("ended", {**APROVADA, "desfecho": "aprovada_por_omissao"}, False, False),
        # `comunicado_entregue` pertence ao WP-J1-07 completo, ainda nao provavel.
        ("ended", {**APROVADA, "phase": "comunicado_entregue"}, False, False),
        # Referencia opaca: o composto TISS nao satisfaz o formato hexadecimal.
        ("ended", {**APROVADA, "authorization_ref": "AUTH-tenant-12345-abcdef12"}, False, False),
    ],
)
async def test_case_outcome_fails_closed_against_every_inconsistent_engine_record(
    state, outcome, omit, extra_outcome
):
    lease, native = Lease(), Native()
    native.state, native.outcome = state, outcome
    native.omit_outcome, native.extra_outcome = omit, extra_outcome
    with pytest.raises(ExternalCaseError):
        await (CaseService(Resolver(), lease, native)).read("synthetic", case_ref=REF)
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
