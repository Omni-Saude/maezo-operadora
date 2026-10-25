"""W5 CLNT 5.2 — titular DSR channel over ASGI: DARK by default, `decisao_dsr` refused on sight.

ASGI half of the channel (the engine start itself is the sink's future job through the audited
`start_process_idempotent` chokepoint — NEVER this boundary; the live-engine lane for
SP-OP-LGPD-DSR-001 remains `tests/integration/processes/test_sp_op_lgpd_dsr_001.py`). Real
session/CSRF/cookie semantics over the in-process app with the P0 harness mold; no live Cognito,
Postgres or engine is claimed.

Fail-closed proofs in this module:

- DEFAULT deployment (`lgpd_dsr_channel=False`) refuses 403 before any effect (flag dark);
- a payload carrying `decisao_dsr` (any of the three literals, or a forged one) — or ANY
  reserved decision/identity/routing fact — is refused 403 with ZERO sink calls, session or
  not: the guard `decisao_dsr` is NEVER resolvable by whoever opens the request;
- unbound factory refuses 503; a session without beneficiary vínculo refuses 403;
- the happy path projects ONLY server-owned facts (tenant, principal_ref, canal=portal, server
  date) and never echoes the free-text request into the receipt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from tests.unit.portal.test_human_session import (
    ISSUER,
    ORIGIN,
    PREFIX,
    SUBJECT,
    Harness,
    SignedTestIdP,
    config,
)

from maezo.portal.api.app import create_app
from maezo.portal.api.dsr import DsrService, DsrStartProjection
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.dsr import DsrReceipt
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ROUTE = PREFIX + "/dsr"
#: Synthetic free text (no real PHI by construction) used to prove it never leaks to the wire.
CANARIO = "CANARIO-TEXTO-LIVRE-DSR-123"


def beneficiary_membership(**updates: object) -> MembershipRecord:
    values: dict[str, object] = dict(
        tenant="test-tenant",
        issuer=ISSUER,
        subject=SUBJECT,
        principal_ref="human-internal-1",
        revision=1,
        audience="beneficiary",
        memberships=(MembershipBinding(membership_ref="review-1", roles=("beneficiary",), groups=()),),
        subject_bindings=(SubjectBinding(kind="beneficiary", resource_ref="beneficiary-opaque-1"),),
        reviewed_until=datetime.now(UTC) + timedelta(hours=2),
    )
    values.update(updates)
    return MembershipRecord(**values)


class RecordingSink:
    """Test double for the future engine seam: records projections, emits a staged receipt."""

    def __init__(self) -> None:
        self.projections: list[DsrStartProjection] = []

    async def open_request(self, projection: DsrStartProjection) -> DsrReceipt:
        self.projections.append(projection)
        return DsrReceipt(
            request_ref="dsr-request-00000001",
            tipo_requisicao=projection.tipo_requisicao,
            received_at=datetime.now(UTC),
        )


class DsrApp:
    """The P0 harness + the DSR service behind the same factory-slot convention."""

    def __init__(self, *, flag: bool, bind: bool, beneficiary: bool = True) -> None:
        self.sink = RecordingSink()
        self.identity = LocalTestIdentityStore("test-tenant")
        self.identity.memberships[(ISSUER, SUBJECT)] = (
            beneficiary_membership()
            if beneficiary
            else beneficiary_membership(
                audience="staff",
                memberships=(MembershipBinding(membership_ref="review-1", roles=("staff",), groups=()),),
                subject_bindings=(),
            )
        )
        self.idp = SignedTestIdP()
        self.oidc = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handle))
        factory = (lambda resolver: DsrService(resolver, self.sink)) if bind else None
        self.app = create_app(
            config(lgpd_dsr_channel=flag),
            store=self.identity,
            oidc_client=self.oidc,
            dsr_service_factory=factory,
        )

    async def __aenter__(self) -> DsrApp:
        self._client_cm = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url=ORIGIN)
        self.client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client_cm.__aexit__(*exc)
        await self.oidc.aclose()


async def session_csrf(app: DsrApp) -> str:
    harness = Harness(app.client, app.identity, app.idp, app.app)
    assert (await harness.login()).status_code == 303
    return (await app.client.get(PREFIX + "/session")).json()["csrf_token"]


def body(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": 1,
        "tipo_requisicao": "confirmacao_acesso",
        "detalhes_requisicao": CANARIO,
    }
    values.update(changes)
    return values


async def post(app: DsrApp, csrf: str, payload: dict[str, Any]) -> httpx.Response:
    return await app.client.post(ROUTE, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


@pytest.mark.asyncio
async def test_canal_escuro_por_padrao_recusa_antes_de_qualquer_efeito() -> None:
    """Flags default-off: `lgpd_dsr_channel=False` (the shipped value) refuses typed — the
    service may be bound, the session valid; nothing reaches the sink."""
    async with DsrApp(flag=False, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.sink.projections == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fact", "value"),
    [
        ("decisao_dsr", "APROVAR_ENVIO"),
        ("decisao_dsr", "EXECUTAR_E_ENVIAR"),
        ("decisao_dsr", "NEGAR_FUNDAMENTADO"),
        ("decisao_dsr", ""),
        ("decisao_dsr", "APROVAR_ENVIO_TYPO"),
        ("fundamentacao_legal", "Retention basis"),
        ("identidade_verificada", True),
        ("identidade_confirmada", True),
        ("human_approved", True),
        ("envolve_dados_saude", True),
        ("tenant_id", "forged-tenant"),
        ("titular_pseudo_id", "forged-pseudo"),
        ("canal", "whatsapp"),
        ("data_solicitacao_iso", "2020-01-01"),
    ],
)
async def test_payload_com_fato_reservado_e_recusado_403_zero_efeito(fact: str, value: object) -> None:
    """FAIL-CLOSED (guard `decisao_dsr` RESOLVIDO neste limite): abrir a requisicao nunca carrega
    decisao, identidade ou fato de roteamento — 403 antes da admissao, ZERO efeito no sink,
    independente de sessao."""
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(**{fact: value}))
        assert response.status_code == 403, (fact, value)
        assert response.json() == {"code": "operation_forbidden"}
        assert app.sink.projections == []


@pytest.mark.asyncio
async def test_fato_reservado_e_recusado_ate_sem_sessao() -> None:
    """The reserved-fact guard fires at the framing layer: framing headers present but NO
    session cookie and a garbage CSRF token still get the same 403 — session-independence."""
    async with DsrApp(flag=True, bind=True) as app:
        response = await app.client.post(
            ROUTE,
            json=body(decisao_dsr="APROVAR_ENVIO"),
            headers={"Origin": ORIGIN, "X-CSRF-Token": "not-a-session-token"},
        )
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.sink.projections == []


@pytest.mark.asyncio
async def test_caminho_feliz_recebe_recibo_e_projeta_so_fatos_do_servidor() -> None:
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 202
        receipt = response.json()
        assert receipt["status"] == "received"
        assert receipt["tipo_requisicao"] == "confirmacao_acesso"
        assert receipt["schema_version"] == 1
        # Stage receipt, never a DSR outcome: no decision, no grant, no free-text echo.
        assert set(receipt) == {"schema_version", "request_ref", "tipo_requisicao", "received_at", "status"}
        assert CANARIO not in response.text
        (projection,) = app.sink.projections
        assert projection.tenant_id == "test-tenant"
        assert projection.principal_ref == "human-internal-1"
        assert projection.canal == "portal"
        assert projection.tipo_requisicao == "confirmacao_acesso"
        assert projection.detalhes_requisicao == CANARIO
        assert projection.data_solicitacao_iso == datetime.now(UTC).date().isoformat()
        assert repr(projection) == (
            "DsrStartProjection(tenant_id='test-tenant', principal_ref='human-internal-1', "
            "tipo_requisicao='confirmacao_acesso', data_solicitacao_iso="
            f"'{projection.data_solicitacao_iso}', canal='portal')"
        )
        assert CANARIO not in repr(projection)


@pytest.mark.asyncio
async def test_reenvio_no_mesmo_dia_e_recibo_de_estagio_nao_de_veredito() -> None:
    """The sink receives the SAME projection twice; idempotency per business key is the sink's
    documented contract (DSR-{tenant}-{titular}-{tipo}-{data} returns the active instance)."""
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        first = await post(app, csrf, body())
        retry = await post(app, csrf, body())
        assert first.status_code == retry.status_code == 202
        assert first.json()["request_ref"] == retry.json()["request_ref"]
        assert len(app.sink.projections) == 2
        assert app.sink.projections[0] == app.sink.projections[1]


@pytest.mark.asyncio
async def test_rota_sem_servico_bound_recusa_dependency_unavailable() -> None:
    async with DsrApp(flag=True, bind=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 503
        assert response.json() == {"code": "dependency_unavailable"}


@pytest.mark.asyncio
async def test_sessao_sem_vinculo_beneficiario_e_operation_forbidden() -> None:
    """A DSR is the data subject's right: no beneficiary vínculo, no channel — a login alone
    proves no titular relationship."""
    async with DsrApp(flag=True, bind=True, beneficiary=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.sink.projections == []


@pytest.mark.asyncio
async def test_csrf_ausente_origem_errada_e_query_string_sao_recusados() -> None:
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        naked = await app.client.post(ROUTE, json=body(), headers={"Origin": ORIGIN})
        assert naked.status_code == 401  # single-valued origin/csrf headers are required
        assert naked.json() == {"code": "authentication_unavailable"}
        wrong_origin = await app.client.post(
            ROUTE,
            json=body(),
            headers={"Origin": "https://outro-portal.example", "X-CSRF-Token": csrf},
        )
        assert wrong_origin.status_code == 401
        wrong_token = await post(app, "wrong-token-attempt", body())
        assert wrong_token.status_code == 401
        assert wrong_token.json() == {"code": "authentication_unavailable"}
        with_query = await app.client.post(
            ROUTE + "?extra=1", json=body(), headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert with_query.status_code == 400
        assert with_query.json() == {"code": "invalid_request"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        body(tipo_requisicao="apagar_tudo"),  # outside the closed six-value domain
        body(detalhes_requisicao="   "),  # blank free text
        body(extra="forged"),  # unknown field — closed DTO
        {**body(), "schema_version": 2},
    ],
)
async def test_payloads_invalidos_sao_invalid_request_sem_efeito(payload: dict[str, Any]) -> None:
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, payload)
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.sink.projections == []


@pytest.mark.asyncio
async def test_corpo_nao_json_e_recusado_sem_efeito() -> None:
    async with DsrApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        wrong_type = await app.client.post(
            ROUTE,
            content=b"tipo_requisicao=confirmacao_acesso",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert wrong_type.status_code == 400
        not_object = await app.client.post(
            ROUTE, content=b"[1, 2]", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert not_object.status_code == 400
        assert app.sink.projections == []
