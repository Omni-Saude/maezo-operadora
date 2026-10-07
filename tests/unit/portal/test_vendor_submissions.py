"""OP16 submission route, E2E-local: vendor session in, receipt out, refusals typed.

ASGI half of VW1-P4 (the machine half lives in `tests/unit/gateway/test_vendor_submissions.py`).
Real session/CSRF/cookie semantics over the in-process app with the P0 harness mold; no live
Cognito, Postgres or engine is claimed. Default-off is proved here too: the DEFAULT deployment
never projects the `vendor` capability, and a session without it never reaches the machine.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.unit.gateway.test_vendor_submissions import (
    CHANNEL,
    TENANT,
    AuditTrail,
    ChannelDirectory,
    SubmissionStore,
)
from tests.unit.portal.test_human_session import (
    ISSUER,
    ORIGIN,
    PREFIX,
    SUBJECT,
    Harness,
    SignedTestIdP,
    config,
)
from tests.unit.portal.test_vendor_membership_records import vendor_record

from maezo.gateway.vendor_submissions import VendorSubmissionMachine
from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.api.vendor_submissions import VendorSubmissionService
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

VENDOR_PROFILE = "identity,staff_cases,human,vendor"
ROUTE = PREFIX + "/vendor/submissions"


class VendorApp:
    """The P0 harness + the OP16 machine behind the same factory-slot convention."""

    def __init__(self, *, capabilities: str, bind: bool) -> None:
        self.store = SubmissionStore()
        self.audit = AuditTrail()
        self.channels = ChannelDirectory({CHANNEL: (TENANT, "active")})
        self.identity = LocalTestIdentityStore(TENANT)
        self.identity.memberships[(ISSUER, SUBJECT)] = vendor_record(
            subject=SUBJECT,
            principal_ref="human-internal-1",
            subject_bindings=(SubjectBinding(kind="vendor", resource_ref=CHANNEL),),
        )
        self.idp = SignedTestIdP()
        self.oidc = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handle))
        factory = (
            (
                lambda resolver: VendorSubmissionService(
                    resolver,
                    VendorSubmissionMachine(channels=self.channels, store=self.store, audit=self.audit),
                )
            )
            if bind
            else None
        )
        self.app = create_app(
            config(capabilities=capabilities),
            store=self.identity,
            oidc_client=self.oidc,
            vendor_submission_service_factory=factory,
        )

    async def __aenter__(self) -> VendorApp:
        self._client_cm = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url=ORIGIN)
        self.client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client_cm.__aexit__(*exc)
        await self.oidc.aclose()


async def vendor_session(app: VendorApp) -> str:
    """Log a vendor principal in and return the session CSRF token."""
    harness = Harness(app.client, app.identity, app.idp, app.app)
    assert (await harness.login()).status_code == 303
    return (await app.client.get(PREFIX + "/session")).json()["csrf_token"]


def body(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": 1,
        "command_id": "command-0001-attempt01",
        "channel_ref": CHANNEL,
        "submission_ref": "submission-000001",
        "business_revision": "7",
        "contract_ref": "contract-00000001",
        "payload": {"content_class": "commercial"},
    }
    values.update(changes)
    return values


async def post(app: VendorApp, csrf: str, payload: dict[str, Any]) -> httpx.Response:
    return await app.client.post(ROUTE, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


@pytest.mark.asyncio
async def test_submissao_e2e_recebe_recibo_e_le_o_estado_da_instancia() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        csrf = await vendor_session(app)
        assert (await app.client.get(PREFIX + "/session")).json()["capabilities"] == [
            "identity",
            "staff_cases",
            "human",
            "vendor",
        ]
        response = await post(app, csrf, body())
        assert response.status_code == 202
        receipt = response.json()
        assert receipt["lifecycle"] == "received"
        assert receipt["refusal_code"] is None
        assert receipt["case_ref"] is None  # C-10: staging asserts nothing
        assert app.store.count() == 1
        assert app.audit.decisions() == [("ALLOW", "received")]
        # The response leg is read from the SAME instance by ref — never pushed as a notice.
        read = await app.client.get(ROUTE + "/" + receipt["submission_ref"])
        assert read.status_code == 200
        status = read.json()
        assert status["submission_id"] == receipt["submission_id"]
        assert status["lifecycle"] == "received"
        # A ref this channel never submitted is indistinguishable from an absent one.
        missing = await app.client.get(ROUTE + "/submission-004040")
        assert missing.status_code == 404
        assert missing.json() == {"code": "resource_unavailable"}


@pytest.mark.asyncio
async def test_reenvio_pela_rota_retorna_a_mesma_instancia() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        csrf = await vendor_session(app)
        first = (await post(app, csrf, body())).json()
        retry = (await post(app, csrf, body(command_id="command-0001-attempt02"))).json()
        assert retry["submission_id"] == first["submission_id"]
        assert app.store.count() == 1
        assert app.audit.decisions() == [("ALLOW", "received"), ("ALLOW", "replayed_active_instance")]


@pytest.mark.asyncio
async def test_default_config_recusa_a_rota_vendor_antes_da_maquina() -> None:
    """Flags default-off: no vendor capability projected, so the route refuses typed — the
    machine bound or not, no session is born vendor."""
    async with VendorApp(capabilities="identity", bind=True) as app:
        csrf = await vendor_session(app)
        assert (await app.client.get(PREFIX + "/session")).json()["capabilities"] == ["identity"]
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.store.count() == 0
        assert app.audit.decisions() == []


@pytest.mark.asyncio
async def test_sessao_nao_vendor_com_capability_ligada_e_authority_unproven() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        app.identity.memberships[(ISSUER, SUBJECT)] = vendor_record(
            audience="staff",
            memberships=(MembershipBinding(membership_ref="staff-binding", roles=("staff",), groups=()),),
            subject_bindings=(),
            subject=SUBJECT,
            principal_ref="human-internal-1",
        )
        csrf = await vendor_session(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "AUTHORITY_UNPROVEN"}
        assert app.store.count() == 0


@pytest.mark.asyncio
async def test_payload_clinico_pela_rota_cai_no_firewall_com_zero_efeito() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        csrf = await vendor_session(app)
        response = await post(app, csrf, body(payload={"content_class": "declaracao_saude"}))
        assert response.status_code == 400
        assert response.json() == {"code": "PHI_IN_COMMERCIAL_INPUT"}  # exact wire bytes
        assert app.store.count() == 0  # ZERO downstream effect
        assert app.audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]  # refusal audited


@pytest.mark.asyncio
async def test_rota_sem_maquina_bound_recusa_dependency_unavailable() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=False) as app:
        csrf = await vendor_session(app)
        response = await post(app, csrf, body())
        assert response.status_code == 503
        assert response.json() == {"code": "dependency_unavailable"}


@pytest.mark.asyncio
async def test_csrf_ausente_e_query_string_sao_recusados() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        csrf = await vendor_session(app)
        naked = await app.client.post(ROUTE, json=body(), headers={"Origin": ORIGIN})
        assert naked.status_code == 401  # single-valued origin/csrf headers are required
        assert naked.json() == {"code": "authentication_unavailable"}
        wrong = await post(app, "wrong-token-attempt", body())
        assert wrong.status_code == 401
        assert wrong.json() == {"code": "authentication_unavailable"}
        query = await app.client.get(
            ROUTE + "/submission-000001", params={"extra": "1"}, headers={"X-CSRF-Token": csrf}
        )
        assert query.status_code == 400
        assert query.json() == {"code": "invalid_request"}


@pytest.mark.asyncio
async def test_canal_revogado_pela_rota_e_channel_status_ineligible() -> None:
    async with VendorApp(capabilities=VENDOR_PROFILE, bind=True) as app:
        app.channels.rows[CHANNEL] = (TENANT, "revoked")
        csrf = await vendor_session(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "CHANNEL_STATUS_INELIGIBLE"}
        assert app.store.count() == 0
