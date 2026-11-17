"""E-SF-06 — EP-REG-02b: no automated personalization without a human review route.

LGPD art. 20 (NORMATIVE-BASELINE-v2): "O titular dos dados tem direito a solicitar revisão,
por pessoa natural, de decisões tomadas unicamente com base em tratamento automatizado de
dados pessoais que afetem seus interesses, inclusive de decisões destinadas a definir o seu
perfil pessoal, profissional, de consumo e de crédito ou os aspectos de sua personalidade."

Closed state: the commercial plane has no personalized offer. `offer.quote` binds exactly
the source catalog/version/product/profile the buyer selected (E-SF-04 TB-E-24-a pins the
four keys), and no C6-4 result carries a personalization slot. The render block on the buyer
surface is DEC-11 (omni); here the guard is the contract lint plus the codec refusing an
undeclared slot. GAP: the `review_route` shape (link to the A5 human queue
`SP-OP-ESCALATION-001`) is not published; a successor that adds personalization must add it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a4 import snapshot_result
from tests.unit.test_commercial_c6 import DeliveryOrigins, encode, journey, journey_request, outcome
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

#: Automated personalization markers (art. 20: decisions defining a consumption profile).
PERSONALIZATION = re.compile(r"personali[sz]|recommend|recomend", re.IGNORECASE)


def personalized_without_review(document: Mapping[str, Any]) -> list[str]:
    """Personalization-marked properties of an object that does not require `review_route`."""
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                marked = [name for name in properties if PERSONALIZATION.search(name)]
                if marked and "review_route" not in (node.get("required") or ()):
                    hits.extend(f"{path}.{name}" for name in marked)
            for key, item in node.items():
                walk(item, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(document["$defs"], "$defs")
    return sorted(hits)


def test_personalized_offer_requires_human_review_route(schema_bytes):
    document = json.loads(schema_bytes)
    # Closed state: nothing personalized exists at the pin.
    assert not personalized_without_review(document)
    assert not [name for name in document["$defs"] if PERSONALIZATION.search(name)]
    # Injected violation (successor): a personalized offer slot without `review_route`.
    tampered = deepcopy(document)
    quote = tampered["$defs"]["OfferQuotePayload"]
    quote["properties"]["personalized_offer_ref"] = {"$ref": "#/$defs/Ref"}
    assert personalized_without_review(tampered) == ["$defs/OfferQuotePayload.personalized_offer_ref"]
    # The same slot travelling with a required human review route passes the lint.
    quote["properties"]["review_route"] = {"$ref": "#/$defs/Ref"}
    quote["required"] = [*quote["required"], "review_route"]
    assert not personalized_without_review(tampered)


@pytest.mark.parametrize("slot", ["personalized_offer", "recommended_plan_ref"])
def test_source_result_with_a_personalization_slot_is_never_disclosed(schema_bytes, slot):
    # Render blocked on this side: a journey snapshot carrying a personalization slot is an
    # unknown key, refused typed by the codec before any byte is disclosed.
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    command = codec.parse_command(encode(journey_request()))
    value = journey()
    value[slot] = {"product_ref": "SYN-PRODUCT"}
    with pytest.raises(CommercialRefusalError) as err:
        codec.parse_result(encode(outcome(snapshot_result(value))), command)
    assert (err.value.status, err.value.reason_code) == ("unavailable", "source_unavailable")
