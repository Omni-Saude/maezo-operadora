"""E-SF-05r (parte a) — cercas da vitrine pública na rota real, com recusa antes da divulgação.

Testes nomeados pelo plano: `test_public_catalog_never_usable_with_unknown_ans_registry`
(TB-E-01-a), `test_online_showcase_requires_presential_fact` (EP-REG-G1a) e
`test_online_eligibility_not_hardcoded` (EP-REG-06a). Mais a perna fechada do preço público
(`test_public_price_never_numeric_while_second_leg_closed`).
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial_public_fences import (
    PresentialChannelFact,
    PublicOfferFenceCodec,
    public_offer_fence_violations,
)
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_c6 import (
    PUBLIC_CATALOG_PATH,
    SYN_PRESENTIAL_FACT,
    numeric_public_body,
    public_body,
    public_rig,
)

SRC = Path(__file__).resolve().parents[3] / "src" / "maezo"
_CONTRACT = Path(__file__).resolve().parents[2] / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
HEADERS = {
    "Authorization": "Bearer SYN-SALES-CREDENTIAL",
    "X-Commercial-Connection": "SYN-SALES-CONNECTION",
}


@pytest.fixture
def schema_bytes() -> bytes:
    return _CONTRACT.read_bytes()


async def get_catalog(app) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.get(PUBLIC_CATALOG_PATH, headers=HEADERS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registry",
    [
        {"dataState": "unknown"},
        {"dataState": "known", "productRegistryCode": "SYN-ANS-PRODUCT"},
        {"dataState": "known", "operatorRegistryCode": "SYN-ANS-OPERATOR"},
    ],
)
async def test_public_catalog_never_usable_with_unknown_ans_registry(schema_bytes, registry) -> None:
    body = public_body()
    body["products"][0]["ansRegistry"] = registry
    assert body["products"][0]["offerState"] == "usable"
    app, _, _, source, calls = public_rig(schema_bytes, body=body)
    response = await get_catalog(app)
    # Recusa tipada do catálogo inteiro, antes da divulgação: o produto nunca sai contratável.
    assert response.status_code == 503 and response.json()["reason"] == "source_unavailable"
    assert "SYN-OPTION" not in response.text
    assert len(calls) == 1 and source.digests[-1] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offer_state", ["unavailable", "review_required"])
async def test_unknown_ans_registry_is_information_when_not_usable(schema_bytes, offer_state) -> None:
    body = public_body()
    body["products"][0]["ansRegistry"] = {"dataState": "unknown"}
    body["products"][0]["offerState"] = offer_state
    app, _, _, _, _ = public_rig(schema_bytes, body=body)
    response = await get_catalog(app)
    assert response.status_code == 200 and response.json() == body


@pytest.mark.asyncio
async def test_public_price_never_numeric_while_second_leg_closed(schema_bytes) -> None:
    body = numeric_public_body()
    body["products"][0]["offerState"] = "unavailable"  # nem como informação o número sai
    app, _, _, source, _ = public_rig(schema_bytes, body=body)
    response = await get_catalog(app)
    assert response.status_code == 503 and "12345" not in response.text
    assert source.digests[-1] is None


@pytest.mark.asyncio
async def test_online_showcase_requires_presential_fact(schema_bytes) -> None:
    from maezo.gateway import commercial_admission

    # Mesmo catálogo admitido e válido da 1ª perna: sem o fato do dono, nada é divulgado.
    app, gateway, _, source, calls = public_rig(schema_bytes)
    assert (await get_catalog(app)).status_code == 200
    gateway.codec._presential_fact = None
    response = await get_catalog(app)
    assert response.status_code == 503 and "SYN-OPTION" not in response.text
    assert len(calls) == 2 and source.digests[-1] is None
    # O padrão da composição é o fato ausente.
    unbound = commercial_admission.compose_public_catalog_gateway(schema_bytes=schema_bytes)
    assert isinstance(unbound.codec, PublicOfferFenceCodec) and unbound.codec._presential_fact is None
    with pytest.raises(CommercialRefusalError):
        unbound.codec.parse_public_result(httpx.Response(200, json=public_body()).content)


def test_presential_fact_carries_no_product() -> None:
    # Ato único do dono: nunca lista de produtos (que seria filtro local).
    assert set(PresentialChannelFact.__dataclass_fields__) == {"owner_act_ref"}
    for bad in ("", " ", "x" * 200, "SYN OPTION"):
        with pytest.raises(ValueError, match="presential_channel_fact_invalid"):
            PresentialChannelFact(owner_act_ref=bad)


def test_presential_fact_is_never_built_in_src() -> None:
    builders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name == "PresentialChannelFact":
                    builders.append(path.relative_to(SRC).as_posix())
    assert builders == []


def test_online_eligibility_not_hardcoded(schema_bytes) -> None:
    # (1) A vitrine devolve os bytes exatos da fonte: nada é removido, reordenado ou reescrito.
    wire = CommercialWire(schema_bytes)
    codec = PublicOfferFenceCodec(wire, presential_fact=SYN_PRESENTIAL_FACT)
    body = public_body()
    second = deepcopy(body["products"][0])
    second["optionRef"] = "SYN-OPTION-2"
    body["products"].append(second)
    raw = httpx.Response(200, json=body).content
    assert codec.parse_public_result(raw) == wire.parse_public_result(raw)
    # (2) Tirar a marcação na fonte (o produto deixa de vir) é o único jeito de sair da vitrine.
    body["products"].pop()
    raw = httpx.Response(200, json=body).content
    assert json.loads(codec.parse_public_result(raw))["products"] == public_body()["products"]
    # (3) Nenhuma referência de produto fixa no código das cercas.
    tree = ast.parse((SRC / "gateway" / "commercial_public_fences.py").read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    constants = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]
    assert constants, "a varredura tem de ver as constantes do módulo"
    assert not [c for c in constants if any(k in c for k in ("optionRef", "productRef", "SYN-"))]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda p: p.__setitem__("ansRegistry", {"dataState": "unknown"}), ("ans_registry_unknown",)),
        (
            lambda p: p.__setitem__("price", numeric_public_body()["products"][0]["price"]),
            ("public_price_numeric",),
        ),
        (lambda p: None, ()),
    ],
)
def test_fence_violations_are_named(mutate, expected) -> None:
    body = public_body()
    mutate(body["products"][0])
    assert public_offer_fence_violations(body) == expected


@pytest.mark.parametrize("catalog", [None, [], {"products": None}, {"products": [1]}, {"products": [{}]}])
def test_unreadable_catalog_fails_closed(catalog) -> None:
    with pytest.raises(ValueError, match="public_fence_catalog_unreadable"):
        public_offer_fence_violations(catalog)
