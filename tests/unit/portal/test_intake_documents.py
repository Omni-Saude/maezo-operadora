"""E04 HTTP/contract controls, synthetic adapters only; no runtime acceptance."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from tests.unit.portal.test_human_session import ISSUER, ORIGIN, PREFIX, SUBJECT, Harness, h, membership

from maezo.gateway.intake.models import AdmissionGrant, IntakeError, request_bytes
from maezo.gateway.intake.service import IntakeService
from maezo.portal.contracts.documents import DocumentResponse, DocumentResponseReceipt, UploadReceipt
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeReceipt
from maezo.portal.contracts.models import SubjectBinding

__all__ = ["h"]
REF = "a" * 32
REF2 = "b" * 32


def submission(**changes: object) -> dict[str, object]:
    return dict(
        command_id=REF,
        beneficiary_ref=REF,
        provider_ref=REF2,
        guide_ref=REF,
        codigo_procedimento_tuss="SYNTHETIC",
        categoria_procedimento="consulta",
        carater_atendimento="eletivo",
        valor_estimado_centavos="900719925474099312345",
        document_refs=[],
        **changes,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", REF),
        ("actor", REF),
        ("variables", {}),
        ("documentacao_completa", True),
        ("numero_guia_tiss", "PRIVATE_CANARY"),
        ("cid10", "PRIVATE_CANARY"),
    ],
)
def test_browser_cannot_supply_authority_or_raw_clinical_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuthIntakeSubmission.model_validate_json(
            __import__("json").dumps(dict(submission(), **{field: value}))
        )


@pytest.mark.parametrize("value", [True, 5, 1.2, "01", "-1", "1e3", "1.20"])
def test_money_is_exact_nonnegative_decimal_string(value: object) -> None:
    with pytest.raises(ValidationError):
        AuthIntakeSubmission.model_validate_json(
            __import__("json").dumps(dict(submission(), valor_estimado_centavos=value))
        )


def test_receipts_distinguish_admission_custody_and_effect() -> None:
    with pytest.raises(ValidationError):
        IntakeReceipt(intake_ref=REF, command_id=REF, revision="0", disposition="admitted", case_ref=REF2)
    with pytest.raises(ValidationError):
        IntakeReceipt(intake_ref=REF, command_id=REF, revision="0", disposition="started")
    with pytest.raises(ValidationError):
        UploadReceipt(upload_ref=REF, disposition="quarantined", document_ref=REF2)
    with pytest.raises(ValidationError):
        DocumentResponseReceipt(command_id=REF, request_ref=REF2, revision="0", disposition="correlated")
    with pytest.raises(ValidationError):
        DocumentResponse(command_id=REF, expected_revision="0", document_refs=(REF, REF))


@pytest.mark.asyncio
async def test_missing_composition_is_safe_and_no_cache(h: Harness) -> None:
    # PR-A landing repair: the S5 routers authenticate BEFORE revealing composition state (an
    # anonymous caller gets 401, never a 503 that discloses what is wired). The safety claim
    # is about an authenticated session meeting a missing composition.
    await h.login()
    for path in (
        "/cases",
        "/cases/" + REF,
        "/intakes/" + REF,
        "/cases/" + REF + "/documents",
        "/cases/" + REF + "/document-requests",
        "/documents/" + REF + "/content",
    ):
        response = await h.client.get(PREFIX + path)
        assert response.status_code == 503
        assert response.json() == {"code": "dependency_unavailable"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b'{"command_id":"SECRET","command_id":"OTHER"}',
        b"[]",
        b'{"x":NaN}',
        b'{"raw":"PRIVATE_CANARY"}',
        b"X" * 65537,
    ],
)
async def test_bad_http_payload_never_echoes_private_input(h: Harness, raw: bytes) -> None:
    response = await h.client.post(
        PREFIX + "/intakes/auth",
        content=raw,
        headers={"Content-Type": "application/json", "Origin": ORIGIN, "X-CSRF-Token": "TEST"},
    )
    assert response.status_code == 400
    assert response.json() == {"code": "invalid_request"}
    assert "PRIVATE_CANARY" not in response.text


@pytest.mark.asyncio
async def test_duplicate_origin_and_bad_query_are_rejected(h: Harness) -> None:
    response = await h.client.post(
        PREFIX + "/intakes/auth",
        json=submission(),
        headers=[("origin", ORIGIN), ("origin", ORIGIN), ("x-csrf-token", "TEST")],
    )
    assert response.status_code == 401
    # Query validation is only reachable by an authenticated session (401 precedes 400).
    await h.login()
    for query in ("?limit=01", "?limit=101", "?cursor=a&cursor=b", "?tenant=PRIVATE_CANARY", "?kind=unknown"):
        response = await h.client.get(PREFIX + "/cases" + query)
        assert response.status_code == 400
        assert "PRIVATE_CANARY" not in response.text


class SyntheticAuthority:
    async def admit(self, principal, request):  # type: ignore[no-untyped-def]
        import hashlib

        return AdmissionGrant(
            principal=principal,
            request_digest=hashlib.sha256(request_bytes(request)).hexdigest(),
            guide_identity_ref=REF,
            authority_receipt_ref=REF2,
            authority_digest="a" * 64,
            valid_until=datetime.now(UTC) + timedelta(minutes=1),
        )

    async def read(self, principal, intake_ref):  # type: ignore[no-untyped-def]
        return datetime.now(UTC) + timedelta(minutes=1)


class SyntheticStore:
    count = 0
    after = None

    async def admit(self, grant, request):  # type: ignore[no-untyped-def]
        self.count += 1
        if self.after:
            self.after()
        return IntakeReceipt(
            intake_ref=REF2, command_id=request.command_id, revision="0", disposition="admitted"
        )

    async def read(self, tenant, principal_ref, intake_ref):  # type: ignore[no-untyped-def]
        raise IntakeError("resource_unavailable")


@pytest.mark.asyncio
async def test_current_provider_submission_and_revocation_after_admission(h: Harness) -> None:
    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref=REF2),)
    )
    await h.login()
    csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    store = SyntheticStore()
    h.app.state.intake_service_factory = lambda resolver: IntakeService(resolver, SyntheticAuthority(), store)
    response = await h.client.post(
        PREFIX + "/intakes/auth", json=submission(), headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 202
    assert response.json()["disposition"] == "admitted"
    assert response.json()["case_ref"] is None
    assert store.count == 1
    response = await h.client.post(
        PREFIX + "/intakes/auth", json=submission(), headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 401 and store.count == 1
    store.after = lambda: h.store.memberships.pop((ISSUER, SUBJECT))
    response = await h.client.post(
        PREFIX + "/intakes/auth", json=submission(), headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 401
    assert store.count == 2  # Durable admission can exist despite refused/lost response.


@pytest.mark.asyncio
async def test_staff_and_missing_policy_do_not_receive_provider_submit_power(h: Harness) -> None:
    await h.login()
    csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    store = SyntheticStore()
    h.app.state.intake_service_factory = lambda resolver: IntakeService(resolver, SyntheticAuthority(), store)
    response = await h.client.post(
        PREFIX + "/intakes/auth", json=submission(), headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 401 and store.count == 0


@pytest.mark.asyncio
async def test_openapi_exports_exact_closed_fields(h: Harness) -> None:
    schemas = h.app.openapi()["components"]["schemas"]
    assert set(schemas["CaseSummary"]["properties"]) == {
        "case_ref",
        "kind",
        "state",
        "record_revision",
        "state_observed_at",
    }
    intake = schemas["AuthIntakeSubmission"]
    assert intake["additionalProperties"] is False
    assert set(intake["properties"]) == set(submission()) | {"schema_version"}
