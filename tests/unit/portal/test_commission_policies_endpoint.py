"""D-36 — internal commission ADJUSTMENT-STRUCTURE drafts over ASGI: DARK, drafts-only.

ASGI half of the D-36 surface (the A8 dimension stays closed: the frozen C6-4 contract has
ZERO commission commands and the registered decision is `admitted_out_of_scope` — this
boundary never calculates, publishes or settles anything). Real session/CSRF/cookie
semantics over the in-process app with the P0 harness mold; no live Cognito, Postgres or
engine is claimed.

Fail-closed proofs in this module:

- DEFAULT deployment (`staff_commission_policies=False`) refuses 403 before any effect
  (flag dark); flag ON with no bound store refuses 503; a NON-STAFF session refuses 403;
- the contract stub payload `commission-policy.proposed.v1` — WELL-FORMED or mutated — is
  refused 403 `commission_policy_fabricated` with ZERO store calls and no session: test
  data never matches the stub as an acceptable shape, the stub dialect never enters a
  maezo-internal draft (family separation), and the A8 structural guard runs on sight;
- a payload carrying a server-owned field (`status` "ATIVO" included) is refused 403
  `field_not_client_owned`: `DRAFT` is the only state and nobody negotiates it;
- activation/calculation/liquidation are ABSENT BY CONSTRUCTION: exactly four operations,
  GET/POST only; `PATCH`/`PUT`/`DELETE` are 405; `.../ativar`, `.../calcular`,
  `.../liquidar` are 404; no commission-action path exists in the OpenAPI surface;
- the happy path stores STRUCTURE only: all value slots nullable (a draft with zero values
  is stored), multiple simultaneous adjustments per target, precedence recorded, audit
  columns server-stamped (`DRAFT` receipt, never a verdict).
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
from tests.unit.portal.test_human_session import membership as staff_membership

from maezo.portal.api.app import create_app
from maezo.portal.api.commission_policies import CommissionPolicyService
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.commission_policies import CommissionAdjustmentDraft
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ROUTE = PREFIX + "/commission-adjustments"
#: The closed portal stub `commission-policy.proposed.v1`, transcribed VERBATIM — this test
#: proves the surface refuses it (the stub is the CONTRACT's placeholder, never an internal
#: draft structure; refusal is the only acceptable outcome for it here).
CONTRACT_STUB_PAYLOAD: dict[str, Any] = {
    "schemaVersion": "commission-policy.proposed.v1",
    "status": "PROPOSED_STUB_NO_ADMITTED_SOURCE",
    "policyRef": "portal-proposal-0001",
    "sourceAuthorityRef": "external-owners-trio",
    "calculationBaseRef": "not-established",
    "attributionPolicyRef": "not-established",
    "generatingFactRefs": [],
    "ratification": "REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO",
    "monetaryContent": "ABSENT_UNTIL_RATIFIED",
}


class RecordingStore:
    """Test double of the draft seam: an in-memory tenant dict; drafts only, by construction."""

    def __init__(self) -> None:
        self.drafts: dict[str, CommissionAdjustmentDraft] = {}
        self.calls: list[str] = []

    async def create(self, draft: CommissionAdjustmentDraft) -> None:
        self.calls.append("create")
        self.drafts[draft.policy_ref] = draft

    async def replace(
        self, policy_ref: str, *, expected_revision: str, draft: CommissionAdjustmentDraft
    ) -> CommissionAdjustmentDraft | None:
        self.calls.append("replace")
        current = self.drafts.get(policy_ref)
        if current is None or current.revision != expected_revision:
            return None
        self.drafts[policy_ref] = draft
        return draft

    async def read(self, policy_ref: str) -> CommissionAdjustmentDraft | None:
        self.calls.append("read")
        return self.drafts.get(policy_ref)

    async def list(self) -> tuple[CommissionAdjustmentDraft, ...]:
        self.calls.append("list")
        return tuple(sorted(self.drafts.values(), key=lambda d: (d.precedencia, d.dimensao, d.policy_ref)))


class CommissionApp:
    """The P0 harness + the D-36 service behind the same factory-slot convention."""

    def __init__(self, *, flag: bool, bind: bool, staff: bool = True) -> None:
        self.store = RecordingStore()
        self.identity = LocalTestIdentityStore("test-tenant")
        if staff:
            self.identity.memberships[(ISSUER, SUBJECT)] = staff_membership()
        else:
            self.identity.memberships[(ISSUER, SUBJECT)] = beneficiary_membership()
        self.idp = SignedTestIdP()
        self.oidc = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handle))
        factory = (lambda resolver: CommissionPolicyService(resolver, self.store)) if bind else None
        self.app = create_app(
            config(staff_commission_policies=flag),
            store=self.identity,
            oidc_client=self.oidc,
            commission_policy_service_factory=factory,
        )

    async def __aenter__(self) -> CommissionApp:
        self._client_cm = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url=ORIGIN)
        self.client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client_cm.__aexit__(*exc)
        await self.oidc.aclose()


def beneficiary_membership() -> MembershipRecord:
    """A NON-staff identity: the same human, beneficiary audience — never this surface."""
    return MembershipRecord(
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


async def session_csrf(app: CommissionApp) -> str:
    harness = Harness(app.client, app.identity, app.idp, app.app)
    assert (await harness.login()).status_code == 303
    return (await app.client.get(PREFIX + "/session")).json()["csrf_token"]


def body(**changes: Any) -> dict[str, Any]:
    """A valid percentual draft for a product — every value slot nullable, here all absent."""
    values: dict[str, Any] = {
        "schema_version": 1,
        "dimensao": "produto",
        "dimensao_ref": "SKU-0001",
        "ajuste_tipo": "percentual",
        "precedencia": 1,
    }
    values.update(changes)
    return values


async def post(
    app: CommissionApp, csrf: str, payload: dict[str, Any], *, path: str = ROUTE
) -> httpx.Response:
    return await app.client.post(path, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


# ---------------------------------------------------------------------------
# DARK by default and fail-closed gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_desligada_recusa_403_antes_de_qualquer_efeito() -> None:
    """D-36 ships DARK: default flag OFF refuses typed — session valid, service bound, and
    the store records ZERO calls. Nothing is drafted on a deployment that did not opt in."""
    async with CommissionApp(flag=False, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.store.calls == []
        read = await app.client.get(ROUTE)
        assert read.status_code == 403
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_flag_ligada_sem_store_bound_recusa_503() -> None:
    async with CommissionApp(flag=True, bind=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 503
        assert response.json() == {"code": "dependency_unavailable"}


@pytest.mark.asyncio
async def test_sessao_nao_staff_recusa_403() -> None:
    """An internal management surface: the SAME human with a beneficiary session never
    reaches the store — with the flag ON or OFF."""
    async with CommissionApp(flag=True, bind=True, staff=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_mutacao_sem_origem_e_csrf_recusa_401() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        await session_csrf(app)
        response = await app.client.post(ROUTE, json=body())
        assert response.status_code == 401
        assert response.json() == {"code": "authentication_unavailable"}
        assert app.store.calls == []


# ---------------------------------------------------------------------------
# The contract stub family is refused ON SIGHT — never a draft, never a session matter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_do_contrato_bem_formado_e_recusado_403_zero_efeito() -> None:
    """The EXACT closed portal stub `commission-policy.proposed.v1` (correct nine fields,
    correct four consts) is refused 403 `commission_policy_fabricated` — family separation:
    the maezo-internal draft surface never speaks the contract stub dialect, well-formed or
    not, with or without a session."""
    async with CommissionApp(flag=True, bind=True) as app:
        response = await app.client.post(
            ROUTE,
            json=CONTRACT_STUB_PAYLOAD,
            headers={"Origin": ORIGIN, "X-CSRF-Token": "not-a-session-token"},
        )
        assert response.status_code == 403
        assert response.json() == {"code": "commission_policy_fabricated"}
        assert app.store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"monetaryContent": {"percentual": 5}},
        {"ratification": "RATIFIED_BY_CODE"},
        {"status": "RATIFIED"},
        {"policyRef": None},
    ],
)
async def test_stub_mutado_e_recusado_pela_guarda_estrutural_a8(mutation: dict[str, Any]) -> None:
    """A mutated stub is fabricated ratification/monetary content: the admission-independent
    A8 guard (`refuse_commission_policy_payload`) refuses before admission, zero effect."""
    payload = {**CONTRACT_STUB_PAYLOAD, **mutation}
    async with CommissionApp(flag=True, bind=True) as app:
        response = await app.client.post(
            ROUTE, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": "junk"}
        )
        assert response.status_code == 403
        assert response.json() == {"code": "commission_policy_fabricated"}
        assert app.store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stray", "expected"),
    [
        # Non-const values are FABRICATED monetary content/ratification: the A8 structural
        # guard refuses (its own reason), before any shape or session consideration.
        ({"monetaryContent": {"valor": 100}}, "commission_policy_fabricated"),
        ({"monetaryContent": "RATIFIED"}, "commission_policy_fabricated"),
        ({"ratification": "RATIFIED_BY_CODE"}, "commission_policy_fabricated"),
        # The stub's CONST values fabricate nothing (the A8 guard passes them) — but the
        # NAMES themselves are reserved on this surface: the stub dialect never enters an
        # internal draft, const or not.
        ({"monetaryContent": "ABSENT_UNTIL_RATIFIED"}, "field_not_client_owned"),
        (
            {"ratification": "REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO"},
            "field_not_client_owned",
        ),
        ({"schemaVersion": "1"}, "field_not_client_owned"),
    ],
)
async def test_nome_transcrito_do_stub_em_payload_alienigena_e_recusado(
    stray: dict[str, Any], expected: str
) -> None:
    """A NON-family payload carrying a stub-transcribed name never reaches the DTO: refused
    403 on sight, with the accurate reason — A8 fabrication for forged values, the reserved
    field guard for the names themselves."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(**stray))
        assert response.status_code == 403
        assert response.json() == {"code": expected}
        assert app.store.calls == []


# ---------------------------------------------------------------------------
# Server-owned fields: `DRAFT` is the only state and nobody negotiates it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fact", "value"),
    [
        ("status", "ATIVO"),
        ("status", "DRAFT"),
        ("status", None),
        ("policy_ref", "cap-forged00000000000000000000000000"),
        ("criado_por", "someone-else"),
        ("criado_em", "2020-01-01T00:00:00Z"),
        ("atualizado_por", "someone-else"),
        ("atualizado_em", "2020-01-01T00:00:00Z"),
        ("tenant", "other-tenant"),
    ],
)
async def test_campo_do_servidor_e_recusado_403_antes_de_tudo(fact: str, value: object) -> None:
    """FAIL-CLOSED: a draft never carries its own state, identity or audit — 403 before the
    closed DTO, ZERO store calls, session or not. `revision` is deliberately NOT here: it is
    the replace command's echoed expectation, refused by the CREATE shape (400 below)."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(**{fact: value}))
        assert response.status_code == 403, (fact, value)
        assert response.json() == {"code": "field_not_client_owned"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_revisao_nao_e_input_de_criacao_recusa_400() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(revision="99"))
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_campo_do_servidor_e_recusado_ate_sem_sessao() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        response = await app.client.post(
            ROUTE, json=body(status="ATIVO"), headers={"Origin": ORIGIN, "X-CSRF-Token": "junk"}
        )
        assert response.status_code == 403
        assert response.json() == {"code": "field_not_client_owned"}
        assert app.store.calls == []


# ---------------------------------------------------------------------------
# Activation / calculation / liquidation are ABSENT BY CONSTRUCTION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ativacao_calculo_liquidacao_nao_existem_por_construcao() -> None:
    """The router carries EXACTLY the four draft operations, GET/POST only. Every activation
    or calculation verb is a 405 (method absent) and every action path is a 404 (route
    absent) — not disabled: never shipped."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        created = await post(app, csrf, body())
        policy_ref = created.json()["policy_ref"]
        for method in ("patch", "put", "delete"):
            attempt = await app.client.request(
                method.upper(),
                f"{ROUTE}/{policy_ref}",
                json=body(),
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            )
            assert attempt.status_code == 405, method
        for action in ("ativar", "calcular", "liquidar", "publicar", "settle", "activate"):
            # POSTing an action path is 404: no such route EXISTS (a GET of the same segment
            # is just the item-read route with a malformed ref — covered by the 400 test).
            attempt = await post(app, csrf, body(), path=f"{ROUTE}/{policy_ref}/{action}")
            assert attempt.status_code == 404, action
        assert app.store.calls == ["create"]


@pytest.mark.asyncio
async def test_openapi_da_superficie_tem_exatos_quatro_operacoes_get_post() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        openapi = (await app.client.get(PREFIX + "/openapi.json")).json()
        mine = {
            path: set(methods)
            for path, methods in openapi["paths"].items()
            if "commission-adjustment" in path
        }
        assert mine == {
            ROUTE: {"get", "post"},
            ROUTE + "/{policy_ref}": {"get", "post"},
        }
        forbidden = {"put", "patch", "delete"}
        assert all(not (methods & forbidden) for methods in mine.values())
        # No commission action verb anywhere on THIS surface's served paths.
        assert not any(
            word in path
            for path in mine
            for word in ("ativar", "calcular", "liquidar", "settle", "activate", "payout", "rank")
        )


@pytest.mark.asyncio
async def test_query_string_e_corpo_em_get_sao_recusados() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        assert (await app.client.get(ROUTE + "?dimensao=produto")).status_code == 400
        read = await app.client.request("GET", ROUTE, json=body(), headers={"Origin": ORIGIN})
        assert read.status_code == 400
        assert (await post(app, csrf, body())).status_code == 202
        with_query = await app.client.post(
            ROUTE + "?x=1", json=body(), headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert with_query.status_code == 400


# ---------------------------------------------------------------------------
# Positive draft flows: structure in, DRAFT out, multiple simultaneous adjustments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_criar_rascunho_estrutura_sem_valor_nenhum_e_202_draft() -> None:
    """D-36's core property: the draft stores STRUCTURE — every value slot absent — and the
    receipt is a DRAFT with server-owned identity and audit, never a verdict or a rate."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 202
        receipt = response.json()
        assert set(receipt) == {
            "schema_version",
            "policy_ref",
            "revision",
            "dimensao",
            "dimensao_ref",
            "ajuste_tipo",
            "percentual",
            "valor",
            "faixa_min",
            "faixa_max",
            "moeda",
            "vigencia_inicio",
            "vigencia_fim",
            "precedencia",
            "status",
            "criado_por",
            "criado_em",
            "atualizado_por",
            "atualizado_em",
        }
        assert receipt["status"] == "DRAFT"
        assert receipt["revision"] == "1"
        assert receipt["policy_ref"].startswith("cap-")
        assert receipt["dimensao"] == "produto"
        assert receipt["dimensao_ref"] == "SKU-0001"
        assert receipt["ajuste_tipo"] == "percentual"
        assert receipt["percentual"] is None
        assert receipt["valor"] is None
        assert receipt["faixa_min"] is None
        assert receipt["faixa_max"] is None
        assert receipt["moeda"] is None
        assert receipt["precedencia"] == 1
        assert receipt["criado_por"] == "human-internal-1"
        assert receipt["atualizado_por"] == "human-internal-1"
        assert len(app.store.drafts) == 1


@pytest.mark.asyncio
async def test_tres_dimensoes_e_multiplos_ajustes_simultaneos_por_alvo() -> None:
    """Multiple simultaneous adjustments per target across the three dimensions, precedence
    declared per draft; the list returns them server-ordered (precedencia, dimensao, ref)."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        assert (await post(app, csrf, body(precedencia=2))).status_code == 202
        assert (
            await post(app, csrf, body(dimensao="canal", dimensao_ref="CANAL-APP", precedencia=1))
        ).status_code == 202
        assert (
            await post(
                app,
                csrf,
                body(dimensao="corretor", dimensao_ref="CORRETOR-77", ajuste_tipo="valor", precedencia=3),
            )
        ).status_code == 202
        # SAME product, second simultaneous adjustment — D-36's point, no uniqueness invented.
        assert (await post(app, csrf, body(ajuste_tipo="faixa", precedencia=2))).status_code == 202
        page = (await app.client.get(ROUTE, headers={"Origin": ORIGIN})).json()
        assert page["count"] == 4
        order = [(d["precedencia"], d["dimensao"], d["ajuste_tipo"]) for d in page["drafts"]]
        # The two same-key drafts (both produto, precedencia 2) order by server ref — the
        # deterministic property is the (precedencia, dimensao) ordering itself.
        assert sorted(order) == sorted(
            [
                (1, "canal", "percentual"),
                (2, "produto", "percentual"),
                (2, "produto", "faixa"),
                (3, "corretor", "valor"),
            ]
        )
        precedences = [d["precedencia"] for d in page["drafts"]]
        assert precedences == sorted(precedences)
        assert all(d["status"] == "DRAFT" for d in page["drafts"])


@pytest.mark.asyncio
async def test_valores_podem_nascer_com_o_rascunho_mas_nada_e_calculado() -> None:
    """A draft MAY carry values (future data, present tense) — the receipt mirrors them
    verbatim and derives NOTHING: no total, no effective rate, no currency conversion."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        negative = await post(
            app,
            csrf,
            body(percentual="-2.5"),
        )
        assert negative.status_code == 202
        assert negative.json()["percentual"] == "-2.5"
        monetary = await post(
            app,
            csrf,
            body(
                ajuste_tipo="valor",
                dimensao="corretor",
                dimensao_ref="CORRETOR-77",
                valor="1050",
                moeda="BRL",
                vigencia_inicio="2026-11-01T00:00:00Z",
                vigencia_fim="2027-10-31T23:59:59Z",
            ),
        )
        assert monetary.status_code == 202
        receipt = monetary.json()
        assert receipt["valor"] == "1050"
        assert receipt["moeda"] == "BRL"
        assert receipt["vigencia_inicio"] is not None
        assert receipt["status"] == "DRAFT"
        faixa = await post(
            app,
            csrf,
            body(ajuste_tipo="faixa", faixa_min="500", faixa_max="9000", moeda="BRL", precedencia=7),
        )
        assert faixa.status_code == 202
        assert [faixa.json()["faixa_min"], faixa.json()["faixa_max"]] == ["500", "9000"]


@pytest.mark.asyncio
async def test_ler_um_rascunho_e_recursos_ausentes() -> None:
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        created = await post(app, csrf, body())
        policy_ref = created.json()["policy_ref"]
        read = await app.client.get(f"{ROUTE}/{policy_ref}", headers={"Origin": ORIGIN})
        assert read.status_code == 200
        assert read.json()["policy_ref"] == policy_ref
        missing = await app.client.get(f"{ROUTE}/cap-{'0' * 32}", headers={"Origin": ORIGIN})
        assert missing.status_code == 404
        assert missing.json() == {"code": "resource_unavailable"}
        malformed = await app.client.get(f"{ROUTE}/curto", headers={"Origin": ORIGIN})
        assert malformed.status_code == 400
        assert malformed.json() == {"code": "invalid_request"}


@pytest.mark.asyncio
async def test_substituicao_de_rascunho_bump_de_revisao_e_conflito_stale() -> None:
    """Full-shape replace (POST-only house posture): revision bumps, creation audit is
    immutable history, and a stale revision is a surfaced 409 — never a silent last-write."""
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        created = (await post(app, csrf, body())).json()
        ref = created["policy_ref"]
        replaced = await post(
            app,
            csrf,
            body(dimensao_ref="SKU-0002", percentual="1.5", revision=created["revision"]),
            path=f"{ROUTE}/{ref}",
        )
        assert replaced.status_code == 202
        receipt = replaced.json()
        assert receipt["revision"] == "2"
        assert receipt["dimensao_ref"] == "SKU-0002"
        assert receipt["percentual"] == "1.5"
        assert receipt["criado_por"] == created["criado_por"]
        assert receipt["atualizado_por"] == "human-internal-1"
        stale = await post(
            app, csrf, body(percentual="9", revision=created["revision"]), path=f"{ROUTE}/{ref}"
        )
        assert stale.status_code == 409
        assert stale.json() == {"code": "stale_revision"}
        unknown = await post(app, csrf, body(revision="1"), path=f"{ROUTE}/cap-{'0' * 32}")
        assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# Structural validators: kind/slot, vigência, faixa — structure, never business rates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"ajuste_tipo": "percentual", "valor": "100"},
        {"ajuste_tipo": "percentual", "faixa_min": "1", "faixa_max": "2"},
        {"ajuste_tipo": "percentual", "moeda": "BRL"},
        {"ajuste_tipo": "valor", "percentual": "5"},
        {"ajuste_tipo": "faixa", "percentual": "5"},
        {"ajuste_tipo": "faixa", "faixa_min": "9000", "faixa_max": "500"},
        {
            "ajuste_tipo": "valor",
            "vigencia_inicio": "2027-01-01T00:00:00Z",
            "vigencia_fim": "2026-01-01T00:00:00Z",
        },
        {
            "ajuste_tipo": "valor",
            "vigencia_inicio": "2026-01-01T00:00:00Z",
            "vigencia_fim": "2026-01-01T00:00:00Z",
        },
        {"dimensao": "regiao"},
        {"dimensao_ref": "nome do corretor"},
        {"dimensao_ref": ""},
        {"ajuste_tipo": "multiplicador"},
        {"percentual": "5%"},
        {"valor": "10.50"},
        {"moeda": "brl"},
        {"precedencia": 0},
        {"precedencia": "1"},
        {"schema_version": 2},
    ],
)
async def test_estrutura_invalida_e_recusada_400_zero_efeito(invalid: dict[str, Any]) -> None:
    payload = body(**invalid)
    if invalid.get("ajuste_tipo") == "valor" or invalid.get("ajuste_tipo") == "faixa":
        payload = body(dimensao="corretor", dimensao_ref="CORRETOR-77", **invalid)
    async with CommissionApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, payload)
        assert response.status_code == 400, invalid
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []
