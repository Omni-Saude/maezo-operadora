"""REG-1.1 — OP21 `legitimacy.screen` over ASGI: DARK, checklist FECHADO, zero bloqueio.

ASGI half of the OP21 staff surface (desenho `OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` sha256
`5639f41d…`, §5 DECISÃO 4; substrato #1022). Real session/CSRF/cookie semantics over the
in-process app with the P0 harness mold; no live Cognito, Postgres or engine is claimed.

Fail-closed proofs in this module:

- DEFAULT deployment (`collective_legitimacy_screen=False`) refuses 403 before any effect
  (flag dark, ZERO store calls); flag ON with no bound store refuses 503; a NON-STAFF
  session refuses 403; a mutation without origin/CSRF refuses 401;
- the checklist is CLOSED: a field outside the five parecer §5 criteria is a 400, a
  server-owned fact (state, art. 39 signal, criteria/anchor materialization, audit) is a
  403 on sight — there is NO slot to type an anchor, a verdict or a free-text observation;
- the manifest renders EXACTLY the five §5 criteria with their VERBATIM labels and per-
  article RN 557/2022 anchors; the penalty citation on screen is the SHORT canonical form
  "RN 489/2022, art. 23-A"; the imprecise forms ("RN 659/2025, art. 23-A", "RN 557/659")
  occur NOWHERE (cerca de citação, parecer §1/§4);
- ZERO channel characterization in any displayed string (parecer §3 — recomendação
  técnica): the fence walks every UI string of the manifest, the served checklist response
  and the surface's OpenAPI slice;
- NO blocking path exists by construction: `would_block` is `False` and `art39_signaled`
  is `False` on every receipt this surface mints (the posture owns both, PRs 3–4), and the
  commercial path answers byte-identically with the flag OFF and ON;
- the art. 16 exemption is DERIVED from the inciso (never typed): incisos I/II refuse the
  criterion; incisos III–VI require it ("em verificação" is `pending`, never silence).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
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

from maezo.gateway.commercial_legitimacy_screen import (
    LegitimacyScreenRecord,
    LegitimacyScreenStoreError,
)
from maezo.portal.api.app import create_app
from maezo.portal.api.legitimacy_screen import (
    _CAMPO_ANCORAS,
    CHECKLIST,
    LegitimacyScreenService,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.legitimacy_screens import INCISOS_ISENTOS_ART16
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

ROUTE = PREFIX + "/legitimacy-screens"
#: As DUAS formas de citação imprecisas (parecer §1/§4) — nenhuma pode existir em string
#: de UI desta superfície, nem no manifest, nem na resposta, nem no schema servido.
PROHIBIDAS_CITACAO = ("RN 659/2025, art. 23-A", "RN 557/659")
#: Tokens de caracterização do CANAL (parecer §3: não caracterizar juridicamente o papel do
#: canal na tela nem em textos exibidos) — nenhum pode aparecer em string de UI.
TOKENS_DE_CANAL = ("corretor", "corretora", "corretagem", "intermedi", "canal")

_SHA = "b" * 64


class RecordingStore:
    """Test double of the substrate port (#1022): in-memory, with the SAME semantics.

    `save` is idempotent on the instance key and refuses a second instance of the same
    opportunity+revision under another `screen_ref` (`contract_mismatch` — the store-level
    refusal the boundary translates to `screen_conflict`).
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, int], LegitimacyScreenRecord] = {}
        self.opportunities: dict[tuple[str, str, int], str] = {}
        self.calls: list[str] = []

    async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        self.calls.append("save")
        key = (record.tenant, record.screen_ref, record.screen_revision)
        binding = (record.tenant, record.opportunity_ref, record.screen_revision)
        owner = self.opportunities.get(binding)
        if owner is not None and owner != record.screen_ref:
            raise LegitimacyScreenStoreError("contract_mismatch")
        existing = self.rows.get(key)
        if existing is not None:
            return existing
        self.rows[key] = record
        self.opportunities[binding] = record.screen_ref
        return record

    async def read(self, tenant: str, screen_ref: str, screen_revision: int) -> LegitimacyScreenRecord | None:
        self.calls.append("read")
        return self.rows.get((tenant, screen_ref, screen_revision))

    async def latest_for_opportunity(
        self, tenant: str, opportunity_ref: str
    ) -> LegitimacyScreenRecord | None:
        self.calls.append("latest_for_opportunity")
        return self._latest(lambda r: r.opportunity_ref == opportunity_ref)

    async def latest_for_legal_entity(
        self, tenant: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord | None:
        self.calls.append("latest_for_legal_entity")
        return self._latest(lambda r: r.legal_entity_ref == legal_entity_ref)

    def _latest(self, predicate: Any) -> LegitimacyScreenRecord | None:
        vivos = [r for (t, _, _), r in self.rows.items() if t == "test-tenant" and predicate(r)]
        if not vivos:
            return None
        return max(vivos, key=lambda r: (r.screen_revision, r.screen_ref))


class LegitimacyApp:
    """The P0 harness + the OP21 service behind the same factory-slot convention."""

    def __init__(self, *, flag: bool, bind: bool, staff: bool = True) -> None:
        self.store = RecordingStore()
        self.identity = LocalTestIdentityStore("test-tenant")
        if staff:
            self.identity.memberships[(ISSUER, SUBJECT)] = staff_membership()
        else:
            self.identity.memberships[(ISSUER, SUBJECT)] = beneficiary_membership()
        self.idp = SignedTestIdP()
        self.oidc = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handle))
        factory = (lambda resolver: LegitimacyScreenService(resolver, self.store)) if bind else None
        self.app = create_app(
            config(collective_legitimacy_screen=flag),
            store=self.identity,
            oidc_client=self.oidc,
            legitimacy_screen_service_factory=factory,
        )

    async def __aenter__(self) -> LegitimacyApp:
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


async def session_csrf(app: LegitimacyApp) -> str:
    harness = Harness(app.client, app.identity, app.idp, app.app)
    assert (await harness.login()).status_code == 303
    return (await app.client.get(PREFIX + "/session")).json()["csrf_token"]


def body(**changes: Any) -> dict[str, Any]:
    """A valid EMPRESARIAL checklist: kind/form declared, the art. 5º criterion proven."""
    values: dict[str, Any] = {
        "schema_version": 1,
        "legal_entity_ref": "pj-contratante-1",
        "opportunity_ref": "opportunity-1",
        "contract_kind": "empresarial",
        "contracting_form": "direta",
        "vinculo_beneficiario_contratante": {
            "result": "proven",
            "evidence_refs": [
                {"kind": "operadora", "locator": "custodia/2026/vinculo.pdf", "document_sha256": _SHA}
            ],
        },
    }
    values.update(changes)
    return values


async def post(
    app: LegitimacyApp, csrf: str, payload: dict[str, Any], *, path: str = ROUTE
) -> httpx.Response:
    return await app.client.post(path, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


def _strings(obj: object) -> Iterator[str]:
    """Every string of a closed model / tuple / already-parsed JSON tree."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, BaseModel):
        for value in obj.__dict__.values():
            yield from _strings(value)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _strings(value)
    elif isinstance(obj, (tuple, list)):
        for value in obj:
            yield from _strings(value)


# ---------------------------------------------------------------------------
# DARK by default and fail-closed gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_desligada_recusa_403_antes_de_qualquer_efeito() -> None:
    """OP21 ships DARK: default flag OFF refuses typed — session valid, service bound, and
    the store records ZERO calls on every verb of the surface."""
    async with LegitimacyApp(flag=False, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.store.calls == []
        for path in (
            ROUTE + "/checklist",
            ROUTE + "/contratante/pj-contratante-1",
            ROUTE + "/scr-" + "a" * 32 + "/1",
        ):
            read = await app.client.get(path)
            assert read.status_code == 403, path
            assert read.json() == {"code": "operation_forbidden"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_flag_ligada_sem_store_bound_recusa_503() -> None:
    async with LegitimacyApp(flag=True, bind=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 503
        assert response.json() == {"code": "dependency_unavailable"}


@pytest.mark.asyncio
async def test_sessao_nao_staff_recusa_403() -> None:
    """An internal staff surface: the SAME human with a beneficiary session never reaches
    the store — with the flag ON or OFF."""
    async with LegitimacyApp(flag=True, bind=True, staff=False) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 403
        assert response.json() == {"code": "operation_forbidden"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_mutacao_sem_origem_e_csrf_recusa_401() -> None:
    async with LegitimacyApp(flag=True, bind=True) as app:
        await session_csrf(app)
        response = await app.client.post(ROUTE, json=body())
        assert response.status_code == 401
        assert response.json() == {"code": "authentication_unavailable"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_get_com_query_string_ou_corpo_sao_recusados() -> None:
    async with LegitimacyApp(flag=True, bind=True) as app:
        await session_csrf(app)
        assert (await app.client.get(ROUTE + "/checklist?critério=1")).status_code == 400
        read = await app.client.request("GET", ROUTE + "/checklist", json=body(), headers={"Origin": ORIGIN})
        assert read.status_code == 400


# ---------------------------------------------------------------------------
# Server-owned facts and the CLOSED checklist shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fact", "value"),
    [
        ("tenant", "other-tenant"),
        ("operation", "legitimacy_screen"),
        ("screen_ref", "scr-forged0000000000000000000000000000"),
        ("status", "valid"),
        ("would_block", True),
        ("art39_signaled", True),
        ("art39_anchor", "RN 557/2022, art. 39"),
        (
            "criteria",
            [{"criterion_id": "forjado", "result": "proven", "norm_anchor": "RN 557/2022, art. 5º"}],
        ),
        ("norm_anchor", "RN 659/2025, art. 23-A"),
        ("created_by", "someone-else"),
        ("created_at", "2020-01-01T00:00:00Z"),
        ("updated_at", "2020-01-01T00:00:00Z"),
    ],
)
async def test_campo_do_servidor_e_recusado_403_antes_de_tudo(fact: str, value: object) -> None:
    """FAIL-CLOSED: a screen never carries its own identity, state, art. 39 signal,
    criteria/anchor materialization or audit — 403 before the closed DTO, ZERO store calls,
    session or not. Notably `norm_anchor` is NOT an input: the anchor belongs to the
    criterion (parecer §4) and a forged imprecise citation dies right here."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(**{fact: value}))
        assert response.status_code == 403, (fact, value)
        assert response.json() == {"code": "field_not_client_owned"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_campo_do_servidor_e_recusado_ate_sem_sessao() -> None:
    async with LegitimacyApp(flag=True, bind=True) as app:
        response = await app.client.post(
            ROUTE, json=body(status="valid"), headers={"Origin": ORIGIN, "X-CSRF-Token": "junk"}
        )
        assert response.status_code == 403
        assert response.json() == {"code": "field_not_client_owned"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_screen_revision_nao_e_input_de_criacao_recusa_400() -> None:
    """Molde D-36 `revision`: the echoed expectation is the REVALIDATION path's input; on
    create the shape simply has no such field and it dies in the closed DTO."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(screen_revision="1"))
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stray",
    [
        {"observacao": "texto livre do operador"},
        {"criterio_extra": {"result": "proven"}},
        {"documentos_art12": {"result": "proven"}},
        {"contract_kind_extra": "qualquer"},
        {"schema_version": 2},
    ],
)
async def test_campo_fora_do_checklist_fechado_e_recusa_400(stray: dict[str, Any]) -> None:
    """The five parecer §5 criteria are the WHOLE input surface: no extra field, no free
    text, no seventh criterion (desenho §5 — additionalProperties recusado)."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(**stray))
        assert response.status_code == 400, stray
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_verificacao_sem_criterio_declarado_e_recusada_400() -> None:
    """The substrate requires at least one criterion — a screen that only names the PJ is
    not a verification (substrato #1022: criteria min_length=1)."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(vinculo_beneficiario_contratante=None))
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


# ---------------------------------------------------------------------------
# The art. 16 exemption is DERIVED from the inciso — never typed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("inciso", ["inciso_i", "inciso_ii"])
async def test_inciso_isento_do_art16_dispensa_o_criterio_temporal(inciso: str) -> None:
    """Parecer §5 verbatim — "art. 16, salvo conselhos, entidades de classe e sindicatos":
    for incisos I/II the temporal criterion is structurally EXEMPT, the receipt records the
    lista criterion alone, and the derived status stays honest."""
    payload = body(
        contract_kind="adesao",
        contracting_form="administradora",
        vinculo_beneficiario_contratante=None,
        contratante_lista_art15=inciso,
    )
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, payload)
        assert response.status_code == 201
        receipt = response.json()
        assert [c["criterion_id"] for c in receipt["criteria"]] == ["adesao_lista"]
        assert receipt["criteria"][0]["norm_anchor"] == "RN 557/2022, art. 15"
        assert receipt["status"] == "valid"


@pytest.mark.asyncio
@pytest.mark.parametrize("inciso", ["inciso_i", "inciso_ii"])
async def test_inciso_isento_nao_declara_o_criterio_temporal_recusa_400(inciso: str) -> None:
    """The exemption is DERIVED from the inciso (desenho §5), nunca digitada: typing the
    criterion for an exempt inciso is a refusal — there is no state where it means anything."""
    payload = body(
        contract_kind="adesao",
        contracting_form="administradora",
        vinculo_beneficiario_contratante=None,
        contratante_lista_art15=inciso,
        constituida_um_ano={"result": "proven"},
    )
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, payload)
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("inciso", ["inciso_iii", "inciso_iv", "inciso_v", "inciso_vi"])
async def test_inciso_aplicavel_exige_o_criterio_temporal_recusa_400(inciso: str) -> None:
    """Applicable (incisos III–VI) ⇒ declared: "em verificação" is result="pending", never a
    silent absence of the ONLY temporal criterion of art. 16."""
    payload = body(
        contract_kind="adesao",
        contracting_form="administradora",
        vinculo_beneficiario_contratante=None,
        contratante_lista_art15=inciso,
    )
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, payload)
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []
        pendente = await post(app, csrf, {**payload, "constituida_um_ano": {"result": "pending"}})
        assert pendente.status_code == 201
        assert pendente.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_criterio_temporal_sem_inciso_e_orfao_recusa_400() -> None:
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(constituida_um_ano={"result": "proven"}))
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}
        assert app.store.calls == []


@pytest.mark.asyncio
async def test_reconfirmacao_e_par_exclusivo_com_o_criterio_e_exige_fuso() -> None:
    """art. 10: the due instant pairs with the declared criterion and is timezone-aware."""
    due = "2027-10-26T12:00:00Z"
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        orfao = await post(app, csrf, body(reconfirmation_due_at=due))
        assert orfao.status_code == 400
        ingenuo = await post(
            app,
            csrf,
            body(reconfirmacao_anual={"result": "pending"}, reconfirmation_due_at="2027-10-26T12:00:00"),
        )
        assert ingenuo.status_code == 400
        assert app.store.calls == []
        par = await post(
            app,
            csrf,
            body(
                contract_kind="empresario_individual",
                vinculo_beneficiario_contratante=None,
                documentos_art9={"result": "proven"},
                reconfirmacao_anual={"result": "pending"},
                reconfirmation_due_at=due,
            ),
        )
        assert par.status_code == 201
        assert par.json()["reconfirmation_due_at"].startswith("2027-10-26T12:00:00")
        assert par.json()["status"] == "pending"


# ---------------------------------------------------------------------------
# Positive flow: the receipt is the SUBSTRATE model, anchors from the closed table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_criar_tela_e_201_com_recibo_do_substrato() -> None:
    """The receipt is EXACTLY the substrate record (#1022) — no new model on the wire;
    anchors come from the closed per-criterion table, never from the payload."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        assert response.status_code == 201
        receipt = response.json()
        assert set(receipt) == set(LegitimacyScreenRecord.model_fields)
        assert receipt["operation"] == "legitimacy_screen"
        assert receipt["tenant"] == "test-tenant"
        assert receipt["screen_ref"].startswith("scr-")
        assert receipt["screen_revision"] == 1
        assert receipt["legal_entity_ref"] == "pj-contratante-1"
        assert receipt["opportunity_ref"] == "opportunity-1"
        assert receipt["contract_kind"] == "empresarial"
        assert receipt["contracting_form"] == "direta"
        assert receipt["status"] == "valid"
        assert receipt["would_block"] is False
        assert receipt["art39_signaled"] is False
        assert receipt["art39_anchor"] is None
        assert receipt["created_by"] == "human-internal-1"
        assert [(c["criterion_id"], c["result"], c["norm_anchor"]) for c in receipt["criteria"]] == [
            ("vinculo_beneficiario_contratante", "proven", "RN 557/2022, art. 5º")
        ]
        evidence = receipt["criteria"][0]["evidence_refs"][0]
        assert evidence == {
            "kind": "operadora",
            "locator": "custodia/2026/vinculo.pdf",
            "document_sha256": _SHA,
        }
        assert len(app.store.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("criterio", "esperado"),
    [
        ("proven", "valid"),
        ("pending", "pending"),
        ("not_proven", "invalid"),
    ],
)
async def test_consolidacao_do_status_e_a_projecao_dos_resultados(criterio: str, esperado: str) -> None:
    """`status` is the PROJECTION of the declared results onto the substrate pin — a label,
    never a commercial consequence (would_block stays False in every row this surface mints)."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body(vinculo_beneficiario_contratante={"result": criterio}))
        assert response.status_code == 201
        assert response.json()["status"] == esperado
        assert response.json()["would_block"] is False


@pytest.mark.asyncio
async def test_nao_comprovado_nao_e_suavizado_por_pendente() -> None:
    """The most severe result wins: one not_proven criterion is `invalid` even beside a
    pending one — PENDENTE never reads as approval, and invalid never softens."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(
            app,
            csrf,
            body(
                contract_kind="adesao",
                contracting_form="administradora",
                vinculo_beneficiario_contratante=None,
                contratante_lista_art15="inciso_iii",
                constituida_um_ano={"result": "pending"},
                documentos_art9={"result": "not_proven"},
            ),
        )
        assert response.status_code == 201
        receipt = response.json()
        assert receipt["status"] == "invalid"
        assert [c["criterion_id"] for c in receipt["criteria"]] == [
            "adesao_lista",
            "constituida_um_ano",
            "documentos_art9",
        ]
        assert [c["norm_anchor"] for c in receipt["criteria"]] == [
            "RN 557/2022, art. 15",
            "RN 557/2022, art. 16",
            "RN 557/2022, arts. 9º a 14",
        ]


@pytest.mark.asyncio
async def test_toda_ancora_persistida_vem_da_tabela_fechada_da_contratacao() -> None:
    """Parecer §4: every persisted anchor is the criterion's OWN RN 557/2022 article — a
    forged or foreign anchor cannot exist, because no input slot carries one."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        response = await post(app, csrf, body())
        anchors = {c["norm_anchor"] for c in response.json()["criteria"]}
        assert anchors <= set(_CAMPO_ANCORAS.values())
        assert all(anchor.startswith("RN 557/2022") for anchor in anchors)


# ---------------------------------------------------------------------------
# Revalidation is a NEW revision — never an update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidacao_cria_nova_revisao_e_stale_e_409() -> None:
    """Renovação/reajuste = nova `screen_revision` (desenho §3): the echoed expectation must
    match the current revision; the previous line stays immutable."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        primeira = (await post(app, csrf, body())).json()
        ref = primeira["screen_ref"]
        segunda = await post(
            app,
            csrf,
            body(
                vinculo_beneficiario_contratante={"result": "pending"},
                screen_revision=primeira["screen_revision"],
            ),
            path=f"{ROUTE}/{ref}",
        )
        assert segunda.status_code == 201
        assert segunda.json()["screen_revision"] == 2
        assert segunda.json()["status"] == "pending"
        anterior = await app.client.get(f"{ROUTE}/{ref}/1")
        assert anterior.status_code == 200
        assert anterior.json()["status"] == "valid"
        stale = await post(
            app,
            csrf,
            body(screen_revision=primeira["screen_revision"]),
            path=f"{ROUTE}/{ref}",
        )
        assert stale.status_code == 409
        assert stale.json() == {"code": "stale_revision"}
        assert app.store.rows[("test-tenant", ref, 2)].created_by == "human-internal-1"


@pytest.mark.asyncio
async def test_revalidar_ref_desconhecido_ou_alheio_e_404() -> None:
    """An absent ref and a ref that is no longer the PJ's current screen are both 404 — the
    revalidation axis is the PJ's latest receipt (molde D-36: indistinguishable)."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        ref = (await post(app, csrf, body())).json()["screen_ref"]
        revalidada = await post(app, csrf, body(screen_revision=1), path=f"{ROUTE}/scr-{'f' * 32}")
        assert revalidada.status_code == 404
        trocada = await post(
            app, csrf, body(legal_entity_ref="pj-outra", screen_revision=1), path=f"{ROUTE}/{ref}"
        )
        assert trocada.status_code == 404
        assert app.store.calls[-2:] == ["latest_for_legal_entity", "latest_for_legal_entity"]


@pytest.mark.asyncio
async def test_segunda_instancia_da_mesma_oportunidade_e_screen_conflict_409() -> None:
    """The substrate's opportunity UNIQUE (same opportunity + revision under another
    screen_ref) surfaces as 409 `screen_conflict` — a loud refusal, never a second trail."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        assert (await post(app, csrf, body())).status_code == 201
        conflito = await post(app, csrf, body(legal_entity_ref="pj-outra"))
        assert conflito.status_code == 409
        assert conflito.json() == {"code": "screen_conflict"}
        # A NEW revision of the SAME opportunity is the house path (revalidation), not a
        # second instance — proven by the revalidation test above.


@pytest.mark.asyncio
async def test_leitura_por_revisao_e_ultima_da_contratante() -> None:
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        primeira = (await post(app, csrf, body())).json()
        ref = primeira["screen_ref"]
        read = await app.client.get(f"{ROUTE}/{ref}/1")
        assert read.status_code == 200
        assert read.json()["screen_ref"] == ref
        ausente = await app.client.get(f"{ROUTE}/{ref}/9")
        assert ausente.status_code == 404
        assert ausente.json() == {"code": "resource_unavailable"}
        malformado = await app.client.get(f"{ROUTE}/curto/1")
        assert malformado.status_code == 400
        segunda = (
            await post(
                app,
                csrf,
                body(vinculo_beneficiario_contratante={"result": "not_proven"}, screen_revision=1),
                path=f"{ROUTE}/{ref}",
            )
        ).json()
        ultima = await app.client.get(f"{ROUTE}/contratante/pj-contratante-1")
        assert ultima.status_code == 200
        assert ultima.json()["screen_revision"] == segunda["screen_revision"]
        assert ultima.json()["status"] == "invalid"


# ---------------------------------------------------------------------------
# The checklist manifest: the FIVE §5 criteria, VERBATIM, and the citation fences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_tem_exatamente_os_cinco_criterios_do_parecer() -> None:
    """Parecer §5 verbatim — labels are the parecer's own words, in its order; the fifth
    criterion (art. 29 §1º) is STRUCTURAL: displayed, never an input field."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        await session_csrf(app)
        manifest = (await app.client.get(ROUTE + "/checklist")).json()
    assert manifest["schema_version"] == 1
    assert manifest["norma_contratacao"] == "RN 557/2022"
    assert manifest["penalidade_anchor"] == "RN 489/2022, art. 23-A"
    assert manifest["escopo"] == "integral"
    criterios = [(c["criterion_id"], c["rotulo"], c["norm_anchor"]) for c in manifest["criterios"]]
    assert criterios == [
        (
            "contratacao_tipo_forma",
            "tipo de contratação (empresarial ou adesão) e forma (direta, com administradora "
            "ou com administradora coestipulante — art. 29)",
            "RN 557/2022, art. 29",
        ),
        (
            "vinculo_beneficiario_contratante",
            "empresarial: vínculo do beneficiário com a contratante (art. 5º)",
            "RN 557/2022, art. 5º",
        ),
        (
            "adesao_lista_constituicao",
            "adesão: contratante na lista do art. 15 e constituída há pelo menos um ano "
            "(art. 16, salvo conselhos, entidades de classe e sindicatos)",
            "RN 557/2022, art. 15",
        ),
        (
            "empresario_documentos_reconfirmacao",
            "empresário individual: documentos do art. 9º e reconfirmação anual (art. 10)",
            "RN 557/2022, arts. 9º a 14",
        ),
        (
            "vedacao_inclusao_sem_pj",
            "nenhuma inclusão de beneficiário sem a pessoa jurídica legitimada (art. 29, § 1º)",
            "RN 557/2022, art. 29, § 1º",
        ),
    ]
    vedacao = manifest["criterios"][4]
    assert vedacao["estrutural"] is True
    assert vedacao["campos"] == []
    um_ano = manifest["criterios"][2]["campos"][1]
    assert um_ano["campo_id"] == "constituida_um_ano"
    assert um_ano["norm_anchor"] == "RN 557/2022, art. 16"
    assert "incisos I e II" in um_ano["condicao_estrutural"]
    resultados = {opcao["valor"] for opcao in manifest["resultados"]}
    assert resultados == {"proven", "not_proven", "not_applicable", "pending"}
    incisos = {opcao["valor"] for opcao in manifest["criterios"][2]["opcoes"]}
    assert incisos == {"inciso_i", "inciso_ii", "inciso_iii", "inciso_iv", "inciso_v", "inciso_vi"}


@pytest.mark.asyncio
async def test_constante_da_isencao_do_contrato_e_a_que_o_manifest_descreve() -> None:
    """The contract's exemption set (the ONLY place the derivation lives) is exactly the
    incisos I/II the manifest's `condicao_estrutural` describes."""
    assert INCISOS_ISENTOS_ART16 == ("inciso_i", "inciso_ii")


@pytest.mark.asyncio
async def test_cerca_de_citacao_imprecisa_sobre_toda_string_da_superficie() -> None:
    """Parecer §1/§4: "RN 659/2025, art. 23-A" e "RN 557/659" occur NOWHERE — not in the
    manifest constant, not in the served checklist, not in the served OpenAPI slice, not in
    any UI string this module ships."""
    textos: list[str] = list(_strings(CHECKLIST))
    textos.extend(_CAMPO_ANCORAS.keys())
    textos.extend(_CAMPO_ANCORAS.values())
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        servido = (await app.client.get(ROUTE + "/checklist")).json()
        textos.extend(_strings(servido))
        receipt = (await post(app, csrf, body())).json()
        textos.extend(_strings(receipt))
        openapi = (await app.client.get(PREFIX + "/openapi.json")).json()
        slice_legitimacy = {
            path: methods for path, methods in openapi["paths"].items() if "legitimacy-screens" in path
        }
        textos.extend(_strings(slice_legitimacy))
        schemas_legitimacy = {
            nome: schema
            for nome, schema in openapi["components"]["schemas"].items()
            if nome.startswith("Legitimacy")
        }
        textos.extend(_strings(schemas_legitimacy))
    textos.extend(_strings(LegitimacyScreenRecord.model_fields))
    for proibida in PROHIBIDAS_CITACAO:
        assert not any(proibida in texto for texto in textos), proibida
    # E a forma CURTA é a que a tela carrega — a forma canônica longa é de REGISTRO
    # (parecer §1), não de UI.
    assert "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)" not in textos
    assert CHECKLIST.penalidade_anchor == "RN 489/2022, art. 23-A"


@pytest.mark.asyncio
async def test_zero_caracterizacao_do_canal_em_texto_exibido() -> None:
    """Parecer §3 (recomendação técnica): no displayed string describes, qualifies or
    characterizes the channel's legal role — the screen is an instrument of the operadora
    and the administradora de benefícios, and no label says otherwise."""
    textos: list[str] = list(_strings(CHECKLIST))
    async with LegitimacyApp(flag=True, bind=True) as app:
        await session_csrf(app)
        servido = (await app.client.get(ROUTE + "/checklist")).json()
        textos.extend(_strings(servido))
    for token in TOKENS_DE_CANAL:
        assert not any(token in texto.lower() for texto in textos), token


# ---------------------------------------------------------------------------
# Route table: registration and reading ONLY — no blocking verb exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_superficie_tem_exatas_cinco_operacoes_get_post() -> None:
    """Exactly the five operations of the registration surface. Every blocking verb is a
    405 (method absent) and every action path is a 404 (route absent) — enforcing is a
    successor class, NOT an endpoint (desenho §4)."""
    async with LegitimacyApp(flag=True, bind=True) as app:
        csrf = await session_csrf(app)
        openapi = (await app.client.get(PREFIX + "/openapi.json")).json()
        mine = {
            path: set(methods) for path, methods in openapi["paths"].items() if "legitimacy-screens" in path
        }
        assert mine == {
            ROUTE: {"post"},
            ROUTE + "/{screen_ref}": {"post"},
            ROUTE + "/{screen_ref}/{screen_revision}": {"get"},
            ROUTE + "/checklist": {"get"},
            ROUTE + "/contratante/{legal_entity_ref}": {"get"},
        }
        ref = (await post(app, csrf, body())).json()["screen_ref"]
        for method in ("patch", "put", "delete"):
            attempt = await app.client.request(
                method.upper(),
                f"{ROUTE}/{ref}",
                json=body(),
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            )
            assert attempt.status_code == 405, method
        for action in ("ativar", "bloquear", "enforce", "aprovar", "liberar", "autorizar"):
            # The ONLY 3-segment shape is the GET read route: POST there is a 405 (method
            # absent), never an action endpoint; GET of the action word dies in the int
            # revision parse (400). No verb exists — enforcing is a successor CLASS, not a
            # route (desenho §4).
            attempt = await post(app, csrf, body(), path=f"{ROUTE}/{ref}/{action}")
            assert attempt.status_code == 405, action
            leitura = await app.client.get(f"{ROUTE}/{ref}/{action}")
            assert leitura.status_code == 400, action
            # The 2-segment POST shape is the revalidation of a screen INSTANCE: an action
            # word there is just a malformed ref (400), never a verb.
            curto = await post(app, csrf, body(), path=f"{ROUTE}/{action}")
            assert curto.status_code == 400, action
        for path in mine:
            assert not any(word in path for word in ("ativar", "bloquear", "enforce", "aprovar", "liberar"))


# ---------------------------------------------------------------------------
# Flag OFF × ON: the commercial path is byte-identical (the stub-OFF proof of §7.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caminho_comercial_byte_identico_com_flag_off_e_on() -> None:
    """The flag adds ZERO surface to the sale path: the same commercial POST answers
    byte-identically (status, headers and body) with the flag OFF and ON — no composition
    path reads it outside the staff gate (the OFF×shadow byte-exact proof on a BOUND
    gateway is PR 3's, prova 3 of the design §7)."""
    payloads: list[bytes] = []
    for flag in (False, True):
        async with LegitimacyApp(flag=flag, bind=True) as app:
            assert app.app.state.commercial_gateway is None
            response = await app.client.post("/api/v1/sales/commercial", content=b"{}")
            assert response.status_code == 503
            payloads.append(response.content)
    assert payloads[0] == payloads[1]
    assert json.loads(payloads[0])["reason_code"] == "binding_unqualified"
