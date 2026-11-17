"""E-SF-20 (EP-REG-08a, maezo side) — the REG-08 waiting posture on the commercial catalog.

Guard of the plan row: `test_collective_path_blocked_pending_ratification`. The frozen C6-4
wire carries the contract type only as `modality` of a public catalog product, so the hold
sits on the self-service catalog read (every quote selector of the funnel comes from it): a
collective product offered as `usable` is refused `denied/purpose_denied` before disclosure.
The fallback is the only variant built: the OP21 enforcing posture, the `legitimacy_unproven`
token and the vendor enforcing posture (E-SF-20r) stay closed, and the
`collective_legitimacy_screen` flag stays OFF (owner act, CA-11).
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_admission import compose_public_catalog_gateway
from maezo.gateway.commercial_collective_hold import (
    COLLECTIVE_HOLD_REFUSAL,
    COLLECTIVE_HOLD_REFUSAL_TEXT,
    COLLECTIVE_MODALITIES,
    SELF_SERVICE_OFFER_STATE,
    CollectiveHoldCatalogCodec,
    collective_self_service_offers,
)
from maezo.gateway.commercial_legitimacy_guard import (
    LEGITIMACY_UNPROVEN,
    LegitimacyScreenEnforcing,
    collective_legitimacy_shadow,
)
from maezo.portal.api.commercial import PUBLIC_CATALOG_PATH
from maezo.portal.api.config import PortalSettings
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_c6 import public_body, public_rig

_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT = _ROOT / "tests" / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
_HOLD_MODULE = _ROOT / "src" / "maezo" / "gateway" / "commercial_collective_hold.py"
_BASELINE_RUBRIC = "Legitimidade da pessoa jurídica contratante de beneficiário em plano coletivo"
_HEADERS = {
    "Authorization": "Bearer SYN-SALES-CREDENTIAL",
    "X-Commercial-Connection": "SYN-SALES-CONNECTION",
}


@pytest.fixture
def schema_bytes() -> bytes:
    return _CONTRACT.read_bytes()


def _contract() -> dict[str, Any]:
    return json.loads(_CONTRACT.read_text())


def _catalog(*products: tuple[str, str]) -> dict[str, Any]:
    """The shared synthetic catalog with one product per (modality, offerState) pair."""
    body = public_body()
    template = body["products"][0]
    body["products"] = []
    for index, (modality, offer_state) in enumerate(products):
        product = deepcopy(template)
        product["optionRef"] = f"SYN-OPTION-{index}"
        product["modality"] = modality
        product["offerState"] = offer_state
        body["products"].append(product)
    return body


async def _read(app: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.get(PUBLIC_CATALOG_PATH, headers=_HEADERS)


# ----------------------------------------------------------------------------------
# The plan-row guard.
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("modality", sorted(COLLECTIVE_MODALITIES))
async def test_collective_path_blocked_pending_ratification(schema_bytes: bytes, modality: str) -> None:
    # Injected violation: the source publishes a collective product as a self-service offer.
    body = _catalog(("individual_familiar", "usable"), (modality, SELF_SERVICE_OFFER_STATE))
    app, _, _, source, calls = public_rig(schema_bytes, body=body)
    response = await _read(app)
    # Refused before disclosure: nothing of the catalog reaches the buyer surface, not even
    # the PF product that sits next to the collective one.
    assert response.status_code != 200
    assert "SYN-OPTION" not in response.text and modality not in response.text
    # A read: one fixed GET, no command, no effect at the source.
    assert len(calls) == 1 and calls[0].method == "GET" and calls[0].content == b""
    assert source.phases == ["before_io", "before_disclosure"]
    # The refusal is typed and belongs to the frozen C6-4 refusal vocabulary.
    codec = CollectiveHoldCatalogCodec(CommercialWire(schema_bytes))
    raw = httpx.Response(200, json=body).content
    with pytest.raises(CommercialRefusalError) as refused:
        codec.parse_public_result(raw)
    assert (refused.value.status, refused.value.reason_code) == ("denied", "purpose_denied")
    assert collective_self_service_offers(body) == ("SYN-OPTION-1",)


@pytest.mark.asyncio
async def test_pf_only_catalog_is_byte_identical_to_the_shipped_read(schema_bytes: bytes) -> None:
    app, _, _, _, _ = public_rig(schema_bytes)
    response = await _read(app)
    assert response.status_code == 200 and response.json() == public_body()
    raw = httpx.Response(200, json=public_body()).content
    wire = CommercialWire(schema_bytes)
    assert CollectiveHoldCatalogCodec(wire).parse_public_result(raw) == wire.parse_public_result(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("offer_state", ["unavailable", "review_required"])
@pytest.mark.parametrize("modality", sorted(COLLECTIVE_MODALITIES))
async def test_collective_product_is_information_never_a_self_service_offer(
    schema_bytes: bytes, modality: str, offer_state: str
) -> None:
    # The public price of a collective product may be seen (SC-08); the product is not
    # contractable on its own: `review_required` is the human route, `unavailable` is closed.
    body = _catalog(("individual_familiar", "usable"), (modality, offer_state))
    app, _, _, _, _ = public_rig(schema_bytes, body=body)
    response = await _read(app)
    assert response.status_code == 200 and response.json() == body


def test_every_composition_carries_the_hold(schema_bytes: bytes) -> None:
    unbound = compose_public_catalog_gateway(schema_bytes=schema_bytes)
    _, bound, _, _, _ = public_rig(schema_bytes)
    for gateway in (unbound, bound):
        assert type(gateway.codec) is CollectiveHoldCatalogCodec
        assert gateway.codec.schema_digest == CommercialWire(schema_bytes).schema_digest


@pytest.mark.parametrize(
    "catalog",
    [None, [], {"products": None}, {"products": [None]}, {"products": [{"modality": "adesao"}]}],
)
def test_an_unreadable_catalog_never_counts_as_pf_only(catalog: object) -> None:
    with pytest.raises(ValueError, match="collective_hold_catalog_unreadable"):
        collective_self_service_offers(catalog)


# ----------------------------------------------------------------------------------
# The contract facts the hold rests on (why it sits on the catalog read).
# ----------------------------------------------------------------------------------


def test_the_collective_modalities_are_the_frozen_c6_4_catalog_values() -> None:
    product = _contract()["$defs"]["PublicCatalogProduct"]["properties"]
    assert set(product["modality"]["enum"]) == COLLECTIVE_MODALITIES | {"individual_familiar"}
    assert SELF_SERVICE_OFFER_STATE in product["offerState"]["enum"]


def test_no_command_payload_or_quote_carries_the_contract_type() -> None:
    # GAP pin: the admission cannot tell a collective `offer.terms` from a PF one on this wire.
    defs = _contract()["$defs"]
    assert set(defs["OfferTermsPayload"]["properties"]) == {"quote_ref", "source_revision", "terms_digest"}
    assert set(defs["OfferQuotePayload"]["properties"]) == {
        "catalog_ref",
        "catalog_version",
        "product_ref",
        "profile_ref",
    }
    for name in ("OfferTermsPayload", "OfferQuotePayload", "quoteResultData", "JourneyPlanChoice"):
        assert "modality" not in json.dumps(defs[name]), name


def test_the_hold_refusal_is_in_the_frozen_vocabulary_and_the_ratified_token_is_not() -> None:
    reason_codes: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "reason_code" and isinstance(value, dict) and "enum" in value:
                    reason_codes.update(value["enum"])
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(_contract())
    assert COLLECTIVE_HOLD_REFUSAL[1] in reason_codes
    # Closed state of E-SF-20r: the enforcing token is not wire vocabulary here.
    assert LEGITIMACY_UNPROVEN not in reason_codes


# ----------------------------------------------------------------------------------
# Gates closed: the screen stays dormant, the enforcing posture stays unreachable.
# ----------------------------------------------------------------------------------


def test_collective_legitimacy_screen_flag_ships_off() -> None:
    assert PortalSettings.model_fields["collective_legitimacy_screen"].default is False


def test_the_flag_port_still_refuses_the_enforcing_posture(schema_bytes: bytes) -> None:
    inner = CommercialGateway(codec=CommercialWire(schema_bytes))
    # OFF (shipped): the object passes through untouched, no guard exists in the composition.
    assert collective_legitimacy_shadow(inner, enabled=False) is inner

    def enforcing(wrapped: CommercialGateway) -> Any:
        return LegitimacyScreenEnforcing.__new__(LegitimacyScreenEnforcing)

    # Even ON, the enforcing posture (E-SF-20r) is never mountable through the flag.
    with pytest.raises(ValueError, match="legitimacy_screen_posture_invalid"):
        collective_legitimacy_shadow(inner, enabled=True, guard_factory=enforcing)


def test_the_hold_never_reads_the_screen_store_nor_mounts_a_guard() -> None:
    tree = ast.parse(_HOLD_MODULE.read_text())
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imported == {"__future__", "json", "typing", "maezo.ports.commercial"}


# ----------------------------------------------------------------------------------
# The buyer-facing refusal copy (ADDENDUM-01 §6 / SC-08; REG-08 (iii)).
# ----------------------------------------------------------------------------------


def test_refusal_text_names_the_criterion_and_offers_the_pf_path() -> None:
    assert set(COLLECTIVE_HOLD_REFUSAL_TEXT) == {
        "titulo",
        "criterio",
        "mensagem",
        "caminho_pf",
        "rota_humana",
    }
    # The criterion is the art. 23-A rubric as pinned verbatim in NORMATIVE-BASELINE-v2.
    assert COLLECTIVE_HOLD_REFUSAL_TEXT["criterio"] == _BASELINE_RUBRIC
    # The two alternative paths, verbatim from SC-08 ("sempre na mesma tela").
    assert COLLECTIVE_HOLD_REFUSAL_TEXT["caminho_pf"] == "Contratar como individual/familiar"
    assert COLLECTIVE_HOLD_REFUSAL_TEXT["rota_humana"] == "Falar com especialista"
    assert "legitimidade da pessoa jurídica contratante" in COLLECTIVE_HOLD_REFUSAL_TEXT["mensagem"]


@pytest.mark.parametrize(
    "forbidden",
    ["corretor", "intermedi", "canal", "vendedor", "parceiro", "administradora", "em análise", "RN 557"],
)
def test_refusal_text_names_no_sales_channel_figure(forbidden: str) -> None:
    text = " ".join(COLLECTIVE_HOLD_REFUSAL_TEXT.values()).lower()
    assert forbidden.lower() not in text
