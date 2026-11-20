"""E-SF-05r (parte c), EP-CORE-00b — a raiz de composição relê os registros A1/A2 a cada chamada.

Pelo `create_app` real: sem arquivo as duas rotas ficam no 503 de corpo fixo; com um registro
válido só a superfície admitida liga; o registro é relido a cada chamada (trocar ou apagar o
arquivo muda a resposta seguinte); a raiz de produção não monta resolvedor e as rotas seguem 503.
O teste nomeado pelo plano (`test_composition_root_without_record_file_keeps_both_routes_closed`)
mora em `tests/unit/test_commercial_admission_a1.py` e usa `resolver_app` daqui.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway, PublicCatalogGateway
from maezo.gateway.commercial_admission import compose_commercial_gateway, compose_public_catalog_gateway
from maezo.gateway.commercial_legitimacy_guard import LegitimacyScreenShadow
from maezo.gateway.commercial_record_resolver import CommercialRecordResolver
from maezo.portal.api.app import create_app
from maezo.portal.api.commercial import COMMERCIAL_PATH, PUBLIC_CATALOG_PATH
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.store import LocalTestIdentityStore
from tests.unit.gateway.test_commercial_legitimacy_guard import NOW, _Store
from tests.unit.test_commercial_admission_a1 import a1_record
from tests.unit.test_commercial_admission_a2 import a2_record, commercial_kwargs
from tests.unit.test_commercial_c6 import SYN_PRESENTIAL_FACT, PublicScope, PublicSource, public_body
from tests.unit.test_commercial_http import UnitCaller, wire_command
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

AMH_ORIGIN = "https://amh.invalid"
HEADERS = {"Authorization": "Bearer SYN-SALES-CREDENTIAL", "X-Commercial-Connection": "SYN-SALES-CONNECTION"}
UNAVAILABLE_COMMERCIAL = {
    "schema_version": "omni-commercial.proposed.v1",
    "status": "unavailable",
    "operation_ref": None,
    "reason_code": "binding_unqualified",
    "data": None,
}
UNAVAILABLE_CATALOG = {
    "schemaVersion": "sales-catalog.proposed.v1",
    "status": "unavailable",
    "reason": "source_unavailable",
}
PRODUCTION = Path(__file__).resolve().parents[3] / "src" / "maezo" / "portal" / "api" / "production.py"


def settings(**changes) -> PortalSettings:
    values = dict(
        tenant="synthetic",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SYN",
        cognito_origin="https://auth.invalid",
        client_id="synthetichuman",
        machine_client_id="syntheticmachine",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://maezo.invalid",
        mode="local-test",
    )
    values.update(changes)
    return PortalSettings(**values)


def resolver(
    schema_bytes, tmp_path, calls, composed, *, catalog=True, sales=True
) -> CommercialRecordResolver:
    def compose_public(admission):
        composed.append("A1")

        def transport(request):
            calls.append(request)
            return httpx.Response(200, json=public_body())

        return compose_public_catalog_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=PublicScope(),
            source_authority=PublicSource(CommercialWire(schema_bytes)),
            source_admission=admission,
            presential_fact=SYN_PRESENTIAL_FACT,
            transport=httpx.MockTransport(transport),
        )

    def compose_commercial(admission):
        composed.append("A2")
        return compose_commercial_gateway(
            **commercial_kwargs(schema_bytes, calls, admitted=False), sales_admission=admission
        )

    return CommercialRecordResolver(
        catalog_record_path=(tmp_path / "A1-ADMISSION-RECORD.json") if catalog else None,
        sales_record_path=(tmp_path / "A2-ADMISSION-RECORD.json") if sales else None,
        compose_public=compose_public,
        compose_commercial=compose_commercial,
    )


async def call_both(app) -> tuple[httpx.Response, httpx.Response]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        posted = await client.post(COMMERCIAL_PATH, headers=HEADERS, json=wire_command())
        catalog = await client.get(PUBLIC_CATALOG_PATH, headers=HEADERS)
    return posted, catalog


def no_identity_io(request):
    raise AssertionError("commercial route invoked customer identity IO")


async def resolver_app(schema_bytes, tmp_path, *, catalog=True, sales=True):
    calls: list[httpx.Request] = []
    composed: list[str] = []
    oidc = httpx.AsyncClient(transport=httpx.MockTransport(no_identity_io))
    app = create_app(
        settings=settings(),
        store=LocalTestIdentityStore("synthetic"),
        oidc_client=oidc,
        commercial_record_resolver=resolver(
            schema_bytes, tmp_path, calls, composed, catalog=catalog, sales=sales
        ),
    )
    return app, calls, composed, oidc


@pytest.mark.asyncio
async def test_a1_record_binds_only_the_public_catalog(schema_bytes, tmp_path) -> None:
    (tmp_path / "A1-ADMISSION-RECORD.json").write_bytes(a1_record())
    app, calls, composed, oidc = await resolver_app(schema_bytes, tmp_path)
    async with oidc:
        posted, catalog = await call_both(app)
    assert posted.status_code == 503 and posted.json() == UNAVAILABLE_COMMERCIAL
    assert catalog.status_code == 200 and catalog.json() == public_body()
    assert composed == ["A1"] and len(calls) == 1


@pytest.mark.asyncio
async def test_a2_record_binds_only_the_sales_session(schema_bytes, tmp_path) -> None:
    (tmp_path / "A2-ADMISSION-RECORD.json").write_bytes(a2_record())
    app, calls, composed, oidc = await resolver_app(schema_bytes, tmp_path)
    async with oidc:
        posted, catalog = await call_both(app)
    assert catalog.status_code == 503 and catalog.json() == UNAVAILABLE_CATALOG
    # A sessão ligou: o comando passou pela admissão e chegou à fonte (uma chamada).
    assert posted.status_code == 200 and posted.json()["status"] == "succeeded"
    assert len(calls) == 1
    assert composed == ["A2"]


@pytest.mark.asyncio
async def test_record_is_reread_on_every_call(schema_bytes, tmp_path) -> None:
    path = tmp_path / "A1-ADMISSION-RECORD.json"
    path.write_bytes(a1_record())
    app, _, composed, oidc = await resolver_app(schema_bytes, tmp_path, sales=False)
    divergent = json.loads(a1_record())
    divergent["contract_pin"]["sha256"] = "e" * 64
    async with oidc:
        assert (await call_both(app))[1].status_code == 200
        path.write_bytes(json.dumps(divergent).encode())
        assert (await call_both(app))[1].json() == UNAVAILABLE_CATALOG
        path.unlink()
        assert (await call_both(app))[1].json() == UNAVAILABLE_CATALOG
        path.write_bytes(a1_record())
        assert (await call_both(app))[1].status_code == 200
    # Composto só nas chamadas com registro válido: nada fica em cache entre chamadas.
    assert composed == ["A1", "A1"]


def test_composition_failure_keeps_the_surface_unbound(tmp_path) -> None:
    (tmp_path / "A1-ADMISSION-RECORD.json").write_bytes(a1_record())
    (tmp_path / "A2-ADMISSION-RECORD.json").write_bytes(a2_record())

    def explode(admission):
        raise ValueError("commercial_catalog_admission_invalid")

    broken = CommercialRecordResolver(
        catalog_record_path=tmp_path / "A1-ADMISSION-RECORD.json",
        sales_record_path=tmp_path / "A2-ADMISSION-RECORD.json",
        compose_public=explode,
        compose_commercial=explode,
    )
    assert broken.public_catalog_gateway() is None and broken.commercial_gateway() is None
    wrong_type = CommercialRecordResolver(
        catalog_record_path=tmp_path / "A1-ADMISSION-RECORD.json",
        sales_record_path=tmp_path / "A2-ADMISSION-RECORD.json",
        compose_public=lambda admission: object(),
        compose_commercial=lambda admission: object(),
    )
    assert wrong_type.public_catalog_gateway() is None and wrong_type.commercial_gateway() is None


def test_resolver_never_coexists_with_a_precomposed_gateway(schema_bytes, tmp_path) -> None:
    for kwargs in (
        {"commercial_gateway": compose_commercial_gateway(schema_bytes=schema_bytes)},
        {"public_catalog_gateway": compose_public_catalog_gateway(schema_bytes=schema_bytes)},
    ):
        with pytest.raises(ValueError, match="commercial_composition_invalid"):
            create_app(
                settings=settings(),
                store=LocalTestIdentityStore("synthetic"),
                commercial_record_resolver=resolver(schema_bytes, tmp_path, [], []),
                **kwargs,
            )


def test_legitimacy_flag_without_guard_is_a_boot_refusal_with_a_resolver(schema_bytes, tmp_path) -> None:
    with pytest.raises(ValueError, match="legitimacy_screen_guard_unbound"):
        create_app(
            settings=settings(collective_legitimacy_screen=True),
            store=LocalTestIdentityStore("synthetic"),
            commercial_record_resolver=resolver(schema_bytes, tmp_path, [], []),
        )


def _shadow_factory(inner):
    return LegitimacyScreenShadow(
        store=_Store(),
        tenant="tenant-a",
        codec=inner.codec,
        admission=inner.admission,
        source=inner.source,
        clock=lambda: NOW,
        timeout_seconds=inner.timeout_seconds,
    )


@pytest.mark.asyncio
async def test_resolved_gateway_passes_through_the_legitimacy_posture(schema_bytes, tmp_path) -> None:
    # Flag ligada com a fábrica da shadow: o gateway resolvido por chamada é EXATAMENTE a shadow,
    # como o injetado. Sem a aplicação da postura em create_app, sairia a classe base.
    (tmp_path / "A2-ADMISSION-RECORD.json").write_bytes(a2_record())
    app = create_app(
        settings=settings(collective_legitimacy_screen=True),
        store=LocalTestIdentityStore("synthetic"),
        legitimacy_screen_guard_factory=_shadow_factory,
        commercial_record_resolver=resolver(schema_bytes, tmp_path, [], []),
    )
    assert type(app.state.commercial_record_resolver.commercial_gateway()) is LegitimacyScreenShadow
    # Flag desligada (o estado entregue): o mesmo gateway composto, sem invólucro.
    plain = create_app(
        settings=settings(),
        store=LocalTestIdentityStore("synthetic"),
        commercial_record_resolver=resolver(schema_bytes, tmp_path, [], []),
    )
    assert type(plain.state.commercial_record_resolver.commercial_gateway()) is CommercialGateway


@pytest.mark.asyncio
async def test_invalid_posture_keeps_the_resolved_surface_unbound_with_the_fixed_body(
    schema_bytes, tmp_path
) -> None:
    # Fábrica que devolve outra postura: com gateway injetado é recusa de boot; com resolvedor,
    # a superfície fica sem ligação e a rota responde o 503 de corpo fixo (nunca erro genérico).
    (tmp_path / "A2-ADMISSION-RECORD.json").write_bytes(a2_record())
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_identity_io)) as oidc:
        app = create_app(
            settings=settings(collective_legitimacy_screen=True),
            store=LocalTestIdentityStore("synthetic"),
            oidc_client=oidc,
            legitimacy_screen_guard_factory=lambda inner: inner,
            commercial_record_resolver=resolver(schema_bytes, tmp_path, [], []),
        )
        posted, _ = await call_both(app)
    assert posted.status_code == 503 and posted.json() == UNAVAILABLE_COMMERCIAL


def test_production_root_builds_no_resolver_and_both_routes_stay_503() -> None:
    tree = ast.parse(PRODUCTION.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "create_app"
    ]
    assert len(calls) == 1
    assert [kw.arg for kw in calls[0].keywords] == []  # nenhum gateway, nenhum resolvedor
    assert "CommercialRecordResolver" not in PRODUCTION.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_factory_without_resolver_keeps_both_routes_503() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_identity_io)) as oidc:
        app = create_app(settings=settings(), store=LocalTestIdentityStore("synthetic"), oidc_client=oidc)
        posted, catalog = await call_both(app)
    assert posted.status_code == 503 and posted.json() == UNAVAILABLE_COMMERCIAL
    assert catalog.status_code == 503 and catalog.json() == UNAVAILABLE_CATALOG
    assert not isinstance(app.state.public_catalog_gateway, PublicCatalogGateway)
