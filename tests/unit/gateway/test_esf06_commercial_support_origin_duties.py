"""E-SF-06 — origin-operator duties on the A5 support plane (EP-CORE-05b).

Plan file name `test_commercial_support_origin_duties.py`; renamed with the `esf06_`
prefix of the shared `tests/unit/gateway/` tree (EXECUTION-LOG §4).

DEC-CORE CORE-04 item 7 / CORE-05 item 3: the minimum CR-F5 vocabulary includes the RN 438
origin duties (art. 14 §4º and art. 16 §ú, plus art. 8º-A §1º from RN 585/2023), available
to the beneficiary on any channel and independent of A7. Publishing case kinds is an owner
act (A5 signatories), so this repository holds the REQUEST (`minimum_vocabulary_request`
in `source-readiness.json` CR-F5) and the publication lint; no member is invented, and the
closed state (nothing published, the cases route refusing an unpublished kind) is pinned.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

import tests.unit.test_commercial_admission_a5 as a5
import tests.unit.test_commercial_admission_a7 as a7
from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial_admission import SupportGatedCodec
from maezo.gateway.commercial_portability_admission import PortabilityPayloadError
from maezo.gateway.intake.models import IntakeError
from maezo.portal.api.cases import _external_query
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_c6 import DeliveryOrigins, encode
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

ROOT = Path(__file__).resolve().parents[3]
READINESS = ROOT / "docs" / "design" / "v21-capabilities" / "source-readiness.json"
ORIGIN_DUTIES = ("origin_plan_information", "origin_payment_and_permanence_declarations")


def cr_f5() -> dict[str, Any]:
    document = json.loads(READINESS.read_text(encoding="utf-8"))
    (entry,) = [item for item in document["family_requests"] if item["id"] == "CR-F5"]
    return entry


def assert_origin_duties_published(publication: dict[str, Any] | None, request: dict[str, Any]) -> None:
    """Publication lint: every requested origin duty maps to a published caseKind member."""
    required = [duty["duty_id"] for duty in request["origin_duty_case_kinds"]]
    members = set((publication or {}).get("case_kinds", ()))
    coverage = (publication or {}).get("origin_duty_coverage", {})
    missing = [duty for duty in required if coverage.get(duty) not in members]
    assert not missing, f"CR-F5 vocabulary publishes no case kind for: {', '.join(missing)}"


def full_publication() -> dict[str, Any]:
    return {
        "vocabulary_ref": "SYN-VOCAB",
        "revision": 1,
        "case_kinds": ["SYN-KIND-ORIGIN-INFO", "SYN-KIND-ORIGIN-DECLARATIONS"],
        "origin_duty_coverage": {
            "origin_plan_information": "SYN-KIND-ORIGIN-INFO",
            "origin_payment_and_permanence_declarations": "SYN-KIND-ORIGIN-DECLARATIONS",
        },
    }


@pytest.mark.parametrize("dropped", ORIGIN_DUTIES)
def test_origin_declaration_case_kinds_are_published(dropped):
    # Lint de publicação (estado real: não publicado — ver test_origin_duty_kinds_stay_unpublished_until_the_owner_act).  # noqa: E501
    # The request names exactly the two RN 438 origin duties with their verbatim anchors
    # ("por meio de quaisquer de seus canais de atendimento")…
    request = cr_f5()["minimum_vocabulary_request"]
    assert tuple(duty["duty_id"] for duty in request["origin_duty_case_kinds"]) == ORIGIN_DUTIES
    for duty in request["origin_duty_case_kinds"]:
        assert duty["anchors"]
        for anchor in duty["anchors"]:
            assert anchor["norm"].startswith("RN 438/2018")
            assert "quaisquer de seus canais de atendimento" in anchor["verbatim"]
    assert_origin_duties_published(full_publication(), request)
    # Injected violation: a published vocabulary without one origin kind fails, naming it.
    publication = full_publication()
    del publication["origin_duty_coverage"][dropped]
    with pytest.raises(AssertionError, match=dropped):
        assert_origin_duties_published(publication, request)
    # …and a coverage pointing at a kind that is not a published member is no coverage.
    publication = full_publication()
    publication["case_kinds"].remove(publication["origin_duty_coverage"][dropped])
    with pytest.raises(AssertionError, match=dropped):
        assert_origin_duties_published(publication, request)
    # Removing the duty from the request itself is caught by the exact tuple above.
    shrunk = deepcopy(request)
    shrunk["origin_duty_case_kinds"] = [
        d for d in shrunk["origin_duty_case_kinds"] if d["duty_id"] != dropped
    ]
    assert tuple(d["duty_id"] for d in shrunk["origin_duty_case_kinds"]) != ORIGIN_DUTIES


def test_origin_duty_kinds_stay_unpublished_until_the_owner_act():
    # Closed state (GAP: owner publication): CR-F5 has no publication, so the lint lists
    # both duties as missing and the native cases route refuses an unpublished kind.
    entry = cr_f5()
    request = entry["minimum_vocabulary_request"]
    assert request["status"] == "requested_not_published"
    assert request["published_vocabulary"] is None
    assert entry["contract_publication"] is None and entry["published_for_new_scope"] is False
    with pytest.raises(AssertionError) as missing:
        assert_origin_duties_published(request["published_vocabulary"], request)
    assert all(duty in str(missing.value) for duty in ORIGIN_DUTIES)
    for duty in ORIGIN_DUTIES:
        with pytest.raises(IntakeError):
            _external_query((("kind", duty),), False)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["eligible", "remainingWaitingDays", "protocolRef"])
async def test_support_handoff_carrying_portability_verdict_fields_refuses(schema_bytes, field):
    # Injected violation: a `support.handoff` payload carrying a portability verdict
    # (`eligible: true`, a waiting-period count, a protocol). The A7 structural guard runs
    # on the support payload first: typed `PortabilityPayloadError` as the cause of the
    # codec's `denied/invalid_request`, before any source step, even with A5 admitted.
    command = a5.support_command("support.handoff")
    command["payload"][field] = (
        True if field == "eligible" else ("SYN-PROTOCOL" if field == "protocolRef" else 3)
    )
    codec = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=False)
    with pytest.raises(CommercialRefusalError) as err:
        codec.parse_command(encode(command))
    assert (err.value.status, err.value.reason_code) == ("denied", "invalid_request")
    assert isinstance(err.value.__cause__, PortabilityPayloadError)
    assert err.value.__cause__.reason_code == "portability_verdict_fields_prohibited"
    calls: list[httpx.Request] = []
    app = a5.build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, support_admitted=True
    )
    refused = await a5.post_command(app, command)
    assert refused.json()["reason_code"] == "invalid_request"
    assert calls == []
    # Control: the clean handoff payload still parses.
    assert codec.parse_command(encode(a5.support_command("support.handoff"))).command == "support.handoff"


def test_repin_a7_clean_orientation_payload_passes():
    # Repin of `test_a_clean_orientation_payload_passes_without_any_record` (plan a7:543).
    a7.test_a_clean_orientation_payload_passes_without_any_record()


def test_repin_a7_none_valued_verdict_field_is_a_verdict_field():
    # Repin of `test_a_none_valued_verdict_field_is_still_a_verdict_field` (plan a7:590).
    a7.test_a_none_valued_verdict_field_is_still_a_verdict_field()


def test_repin_a7_lgpd_dsr_collision_untouched():
    # Repin of `test_the_lgpd_dsr_collision_is_untouched_and_never_consumed_as_substrate`
    # (plan a7:500): LGPD art. 18 V data portability is never the RN 438 right.
    a7.test_the_lgpd_dsr_collision_is_untouched_and_never_consumed_as_substrate()
