"""A1 (CR-F2 catalog) registered-source admission — negative tests (D-16).

Executable regression for the planned negatives of admission A1 (GAP-CATALOG-A1-A10 §2):
(a) without an admitted A1 record the default composition and the route stay closed
    (503 source_unavailable) and never read the TEST contract copy;
(b) a record citing a divergent contract sha is refused loudly and binds nothing;
(c) recovery never bootstraps plan_choices (the journey formal entry stays closed).
Plus the admission invariants around them: authority ports without a record never
bind; refused/deferred owner decisions never bind; the admitted scope is exactly
`catalog.get`; expired records and records for other admissions are refused; the record
structure is closed; the loader re-validates on every read; and composition refuses
an admission whose pin does not match the wired schema bytes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import maezo.adapters.amh.commercial as wire_module
from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.gateway.commercial import _METHODS
from maezo.gateway.commercial_admission import (
    A1_CATALOG_DERIVED_COMMANDS,
    AcceptanceSupportUnadmittedSalesAdmission,
    CatalogUnadmittedSalesAdmission,
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.gateway.commercial_catalog_admission import (
    A1_CONTRACT_SHA256,
    CatalogSourceAdmissionError,
    parse_catalog_source_admission,
    read_catalog_source_admission,
)
from maezo.portal.api.commercial import COMMERCIAL_PATH, PUBLIC_CATALOG_PATH, commercial_router
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_c6 import (
    CurrentResultSource,
    PublicScope,
    PublicSource,
    envelope,
    formal_entry_data,
    journey,
    journey_request,
    journey_result,
    parse,
    public_body,
    recovery,
)
from tests.unit.test_commercial_http import (
    UnitCaller,
    UnitScope,
    sales_session_admission,
    wire_command,
    wire_result,
)
from tests.unit.test_commercial_http import (
    schema_bytes as schema_bytes,
)

AMH_ORIGIN = "https://amh.invalid"


def a1_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A1",
            "decision": "admitted",
            "owners": ["produto", "comercial", "legal"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.1",
            "admitted_commands": ["catalog.get"],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A1_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a1-object.json",
                "object_sha256": "a" * 64,
                "origin_git_sha": "b" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a1_admission():
    return parse_catalog_source_admission(a1_record(), now=datetime.now(UTC))


def counting_transport(calls):
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=public_body())

    return httpx.MockTransport(transport)


def rig(schema_bytes, *, admitted):
    calls = []
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=PublicScope(),
        source_authority=PublicSource(CommercialWire(schema_bytes)),
        transport=counting_transport(calls),
    )
    if admitted:
        kwargs["source_admission"] = a1_admission()
    gateway = compose_public_catalog_gateway(**kwargs)
    app = FastAPI()
    app.state.public_catalog_gateway = gateway
    # The effect surface stays in its own shipped state: an unbound commercial POST
    # gateway, so the same app proves what the A1 record unlocks and what it cannot.
    app.state.commercial_gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    app.include_router(commercial_router)
    return app, gateway, calls


async def get_public(app, **headers):
    base = {"Authorization": "Bearer SYN-SALES-CREDENTIAL", "X-Commercial-Connection": "SYN-SALES-CONNECTION"}
    base.update(headers)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.get(PUBLIC_CATALOG_PATH, headers=base)


def test_frozen_contract_pin_lineage(schema_bytes):
    # The A1 admission pins exactly the frozen C6-4 contract bytes (CONTRACT-FREEZE),
    # which are also the codec's candidate pin: one lineage, three agreeing sources.
    digest = hashlib.sha256(schema_bytes).hexdigest()
    assert digest == A1_CONTRACT_SHA256
    assert digest == wire_module.CANDIDATE_SCHEMA_SHA256


@pytest.mark.asyncio
async def test_default_composition_stays_closed_without_any_record(schema_bytes):
    gateway = compose_public_catalog_gateway(schema_bytes=schema_bytes)
    assert gateway.admission is None and gateway.source is None
    app = FastAPI()
    app.state.public_catalog_gateway = gateway
    app.include_router(commercial_router)
    result = await get_public(app)
    assert result.status_code == 503
    assert result.json() == {
        "schemaVersion": "sales-catalog.proposed.v1",
        "status": "unavailable",
        "reason": "source_unavailable",
    }


@pytest.mark.asyncio
async def test_authority_ports_without_a_record_never_read_any_source(schema_bytes):
    # The crux: full trusted authority present, record absent — nothing binds, the
    # route stays closed and the source authority is never exercised (zero calls),
    # so no path can reach the TEST contract copy through the public route.
    app, gateway, calls = rig(schema_bytes, admitted=False)
    assert gateway.admission is None and gateway.source is None
    result = await get_public(app)
    assert result.status_code == 503
    assert result.json() == {
        "schemaVersion": "sales-catalog.proposed.v1",
        "status": "unavailable",
        "reason": "source_unavailable",
    }
    assert calls == []


@pytest.mark.asyncio
async def test_admitted_record_unlocks_only_the_public_catalog_surface(schema_bytes):
    app, gateway, calls = rig(schema_bytes, admitted=True)
    assert gateway.admission is not None and gateway.source is not None
    result = await get_public(app)
    assert result.status_code == 200 and result.json() == public_body()
    assert result.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET" and str(request.url) == f"{AMH_ORIGIN}/interop/sales/v1/public/catalog"
    assert request.content == b"" and "cookie" not in request.headers
    # The A1 record does not unlock the effect surface: the POST commercial gateway
    # on the same app remains in its shipped unbound state.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        posted = await client.post(
            "/api/v1/sales/commercial",
            headers={
                "Authorization": "Bearer SYN-SALES-CREDENTIAL",
                "X-Commercial-Connection": "SYN-SALES-CONNECTION",
                "Content-Type": "application/json",
            },
            content=b"{}",
        )
    assert posted.status_code == 503
    assert posted.json()["status"] == "unavailable" and posted.json()["reason_code"] == "binding_unqualified"
    assert len(calls) == 1  # still only the catalog read


def test_record_citing_a_divergent_contract_sha_is_refused(schema_bytes):
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(
            a1_record(contract_pin={"contract_id": "x", "revision": "C6-4", "sha256": "e" * 64}),
            now=datetime.now(UTC),
        )
    assert err.value.reason_code == "admission_pin_divergent"


def test_divergent_record_on_disk_is_refused_and_never_cached(tmp_path):
    path = tmp_path / "A1-ADMISSION-RECORD.json"
    path.write_bytes(a1_record())
    first = read_catalog_source_admission(path, now=datetime.now(UTC))
    assert first.admission_id == "A1"
    divergent = json.loads(path.read_text())
    for value in divergent.values():
        if isinstance(value, dict) and "sha256" in value:
            value["sha256"] = "e" * 64
    path.write_bytes(json.dumps(divergent).encode())
    with pytest.raises(CatalogSourceAdmissionError) as err:
        read_catalog_source_admission(path, now=datetime.now(UTC))
    assert err.value.reason_code == "admission_pin_divergent"
    # Absence is not a refusal: it is the shipped state, and nothing is defaulted.
    with pytest.raises(FileNotFoundError):
        read_catalog_source_admission(tmp_path / "absent.json", now=datetime.now(UTC))


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision):
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(a1_record(decision=decision), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"admitted_commands": ["catalog.get", "offer.quote"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["acceptance.record"]}, "admission_scope_invalid"),
        ({"admitted_commands": []}, "admission_scope_invalid"),
        ({"unlocks": "P6.2"}, "admission_record_invalid"),
        ({"admission_id": "A2"}, "admission_record_invalid"),
    ],
)
def test_scope_beyond_catalog_get_is_refused(changes, expected):
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(a1_record(**changes), now=datetime.now(UTC))
    assert err.value.reason_code == expected


def test_expired_record_does_not_bind():
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(
            a1_record(valid_until="2020-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_expired"
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(
            a1_record(decided_at="2099-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "owners",
    [[], ["DPO"], ["produto", "produto"], ["produto", "engenharia"], "produto"],
)
def test_owner_roles_are_a_closed_catalog(owners):
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(a1_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema="omni-commercial-admission-record.v2"),
        lambda r: r.update(runtime_flag=True),
        lambda r: r.pop("contract_pin"),
        lambda r: r["evidence"].update(object_sha256="zz"),
        lambda r: r["evidence"].update(origin_git_sha="short"),
    ],
)
def test_record_structure_is_closed(mutation):
    record = json.loads(a1_record())
    mutation(record)
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(json.dumps(record).encode(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_record_that_is_not_json_is_refused():
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(b"{not json", now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_naive_timestamps_are_never_admitted():
    with pytest.raises(CatalogSourceAdmissionError):
        parse_catalog_source_admission(a1_record(valid_until="2099-01-01T00:00:00"), now=datetime.now(UTC))
    with pytest.raises(ValueError):
        parse_catalog_source_admission(a1_record(), now=datetime(2026, 1, 1))


def test_composition_refuses_an_admission_off_the_wired_contract(schema_bytes):
    calls = []
    with pytest.raises(ValueError, match="commercial_catalog_admission_invalid"):
        compose_public_catalog_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=PublicScope(),
            source_authority=PublicSource(CommercialWire(schema_bytes)),
            source_admission=SimpleNamespace(contract_sha256="0" * 64),
            transport=counting_transport(calls),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_recovery_never_bootstraps_plan_choices(schema_bytes):
    # Matrix-probed: with documents isolated (the only remaining C2 variable), a
    # recovered original that still carries plan_choices is REFUSED outright — the
    # C6-4 schema has no bootstrap path through recovery; stripping is exactly what
    # makes the same payload admissible, and it surfaces choices [].
    codec = CommercialWire(schema_bytes)
    journeys = [{**journey(), "documents": []}]
    request = envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"})
    seeded = {**journey_result(), "journeys": journeys, "plan_choices": [{"option_ref": "SYN-OPTION"}]}
    with pytest.raises(CommercialRefusalError):
        parse(codec, request, recovery("journey.get", seeded))
    safe = _strip_replay_actions(recovery("journey.get", seeded))
    parsed = parse(codec, request, safe)
    surfaced = json.loads(parsed.wire_bytes)["data"]["original_result"]["data"]["plan_choices"]
    assert surfaced == []
    assert seeded["plan_choices"] != []


# E-SF-02 (EP-CORE-00b-SIGN): the A1 signatories are the A-INDEX set, spelled out here
# rather than imported so a widened or shrunk constant in the loader cannot hide a defect.
A1_REQUIRED_SIGNATORIES = ("produto", "comercial", "legal")


@pytest.mark.parametrize(
    "owners",
    [
        list(subset)
        for size in range(1, len(A1_REQUIRED_SIGNATORIES))
        for subset in itertools.combinations(A1_REQUIRED_SIGNATORIES, size)
    ],
)
def test_a1_record_signed_by_strict_subset_of_owner_roles_refuses(owners):
    # A record `admitted` by only part of the required signatories never parses: the
    # refusal is typed and happens in the parser, before any binding exists.
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(a1_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize("owners", list(itertools.permutations(A1_REQUIRED_SIGNATORIES)))
def test_a1_record_signed_by_every_required_role_is_admitted(owners):
    admission = parse_catalog_source_admission(a1_record(owners=list(owners)), now=datetime.now(UTC))
    assert set(admission.owners) == set(A1_REQUIRED_SIGNATORIES)


@pytest.mark.parametrize("owners", [["comercial"], ["produto", "comercial"], ["comercial", "legal"]])
def test_a1_record_missing_the_legal_signatory_is_refused(owners):
    # EP-CORE-01a (subsumed by E-SF-02): the catalog record is a produto+comercial+legal
    # act; a record without the legal leg (or with any other leg missing) never binds.
    with pytest.raises(CatalogSourceAdmissionError) as err:
        parse_catalog_source_admission(a1_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


# E-SF-03 (EP-CORE-00a): the A1 gate on the private Sales POST path. A bound A2 session
# must not consume the catalog source while no A1 record exists — the A2 record never
# stands in for A1 (dossiê A1: "antes de qualquer consumo REAL").
SALES_HEADERS = {
    "Authorization": "Bearer SYN-SALES-CREDENTIAL",
    "X-Commercial-Connection": "SYN-SALES-CONNECTION",
    "Content-Type": "application/json",
}


def quote_command():
    # A schema-valid C6-4 `offer.quote` (`OfferQuotePayload`, scope interop/sales.contract).
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "command": "offer.quote",
        "command_ref": "SYN-QUOTE-CMD",
        "expected_revision": None,
        "payload": {
            "catalog_ref": "SYN-CATALOG",
            "catalog_version": 1,
            "product_ref": "SYN-PRODUCT",
            "profile_ref": "SYN-PROFILE",
        },
    }


def quote_result():
    return {
        **wire_result(),
        "data": {
            "kind": "quote",
            "quote_ref": "SYN-QUOTE",
            "source_revision": 7,
            "catalog_ref": "SYN-CATALOG",
            "catalog_version": 1,
            "product_ref": "SYN-PRODUCT",
            "network_version": 1,
            "terms_digest": "b" * 64,
            "money": {"amount_minor": 12345, "currency": "BRL"},
            "valid_until": "2099-01-01T00:00:00Z",
            "contractable": True,
        },
    }


class QuoteSource(CurrentResultSource):
    """Source authority that would let `offer.quote` through — isolates the A1 gate."""

    async def prepare(self, command, scope):
        prepared = await super().prepare(command, scope)
        return replace(prepared, registry_readiness_ref="SYN-REGISTRY", ready_methods=frozenset(_METHODS))


def quote_rig(schema_bytes, calls, *, catalog_admitted, effects_admitted=False):
    scope_authority = UnitScope()
    scope_authority.scope = replace(
        scope_authority.scope,
        allowed_operations=scope_authority.scope.allowed_operations | {"offer.quote"},
    )

    def transport(request):
        calls.append(request)
        quoted = json.loads(request.content)["command"] == "offer.quote"
        return httpx.Response(200, json=quote_result() if quoted else wire_result())

    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=QuoteSource(scope_authority.scope),
        sales_admission=sales_session_admission(),
        transport=httpx.MockTransport(transport),
    )
    if catalog_admitted:
        kwargs["catalog_admission"] = a1_admission()
    if effects_admitted:
        # A3 and A5 admitted: the A1 command leg is the only gate left
        # (`CatalogUnadmittedSalesAdmission` branch of the composition).
        from tests.unit.test_commercial_admission_a3 import a3_admission
        from tests.unit.test_commercial_admission_a5 import a5_admission

        kwargs["acceptance_admission"] = a3_admission()
        kwargs["support_admission"] = a5_admission()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.include_router(commercial_router)
    return app, scope_authority


async def post_sales(app, command):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.post(COMMERCIAL_PATH, headers=SALES_HEADERS, content=json.dumps(command).encode())


def test_catalog_derived_set_is_the_contract_quote_binding(schema_bytes):
    # The gated set is read off the frozen contract, not chosen here: the Sales commands
    # whose payload binds the source catalog/version/product triple.
    contract = json.loads(schema_bytes)
    derived = {
        name
        for name, spec in contract["x-commands"].items()
        if {"catalog_ref", "catalog_version", "product_ref"}
        <= set(contract["$defs"][spec["payload_schema"].rsplit("/", 1)[1]].get("properties", {}))
    }
    assert derived == set(A1_CATALOG_DERIVED_COMMANDS) == {"offer.quote"}


@pytest.mark.asyncio
@pytest.mark.parametrize("effects_admitted", [False, True])
async def test_a2_session_without_a1_record_refuses_catalog_derived_commands(schema_bytes, effects_admitted):
    # Violation injected: a valid A2 session composed with `catalog_admission=None` and a
    # valid `offer.quote` whose scope and source would both let it through. The gate
    # refuses `unavailable/binding_unqualified` (503) before any source step: zero bytes
    # reach the executor transport and no effect phase runs.
    calls = []
    app, scope_authority = quote_rig(
        schema_bytes, calls, catalog_admitted=False, effects_admitted=effects_admitted
    )
    expected = (
        CatalogUnadmittedSalesAdmission if effects_admitted else AcceptanceSupportUnadmittedSalesAdmission
    )
    assert type(app.state.commercial_gateway.admission) is expected
    refused = await post_sales(app, quote_command())
    assert refused.status_code == 503
    body = refused.json()
    assert body["status"] == "unavailable" and body["reason_code"] == "binding_unqualified"
    assert body["data"] is None
    assert calls == []
    assert scope_authority.phases == ["before_body"]
    # The same bound session keeps serving the non-catalog command exactly as shipped.
    served = await post_sales(app, wire_command())
    assert served.status_code == 200 and served.json() == wire_result()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a1_and_a2_admitted_quote_reaches_the_source_unchanged(schema_bytes):
    # Control: with A1 and A2 the gate is not the refuser — the quote walks the full
    # phase ladder and the source answer is disclosed byte-for-byte as before.
    calls = []
    app, scope_authority = quote_rig(schema_bytes, calls, catalog_admitted=True)
    served = await post_sales(app, quote_command())
    assert served.status_code == 200 and served.json() == quote_result()
    assert len(calls) == 1 and json.loads(calls[0].content) == quote_command()
    assert scope_authority.phases == ["before_body", "before_source", "before_effect", "before_disclosure"]


def test_commercial_composition_refuses_an_a1_admission_off_the_wired_contract(schema_bytes):
    scope_authority = UnitScope()
    with pytest.raises(ValueError, match="commercial_catalog_admission_invalid"):
        compose_commercial_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=scope_authority,
            source_authority=QuoteSource(scope_authority.scope),
            sales_admission=sales_session_admission(),
            catalog_admission=SimpleNamespace(contract_sha256="0" * 64),
        )


def plan_choices_rig(schema_bytes, calls, *, catalog_admitted):
    # A2 bound, scope grants `journey.get`, and the source answers the formal-entry
    # snapshot with one source-qualified plan choice (catalog/version/product).
    scope_authority = UnitScope()
    scope_authority.scope = replace(
        scope_authority.scope,
        allowed_operations=frozenset({"operator.get", "journey.get"}),
        granted_scopes=frozenset({"interop/sales.journey.read"}),
        resource_grant_ref="SYN-RESOURCE-GRANT",
        journey_linkage_ref="SYN-LINKAGE",
    )

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={**wire_result(), "data": formal_entry_data()})

    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        sales_admission=sales_session_admission(),
        transport=httpx.MockTransport(transport),
    )
    if catalog_admitted:
        kwargs["catalog_admission"] = a1_admission()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.include_router(commercial_router)
    return app


@pytest.mark.asyncio
async def test_a2_session_without_a1_record_refuses_plan_choices_disclosure(schema_bytes):
    # Disclosure leg of EP-CORE-00a (DEC-CORE CORE-00 §(4)(b)): with A2 and no A1 record a
    # `journey.get` whose source result carries a non-empty `plan_choices` never discloses
    # it. The gate refuses after the read ran at the source, so the effective answer is
    # the post-dispatch custody `202 uncertain/reconciliation_required` with no data —
    # not `succeeded`, no catalog byte, one source read and no re-dispatch.
    calls = []
    app = plan_choices_rig(schema_bytes, calls, catalog_admitted=False)
    refused = await post_sales(app, journey_request())
    assert refused.status_code == 202
    body = refused.json()
    assert body["status"] != "succeeded"
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None
    assert b"SYN-CATALOG" not in refused.content and b"plan_choices" not in refused.content
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a1_and_a2_admitted_journey_discloses_plan_choices(schema_bytes):
    # Control: with A1 and A2 the same source snapshot is served with its plan choice.
    calls = []
    app = plan_choices_rig(schema_bytes, calls, catalog_admitted=True)
    served = await post_sales(app, journey_request())
    assert served.status_code == 200
    assert served.json()["data"]["plan_choices"] == formal_entry_data()["plan_choices"]
    assert b"SYN-CATALOG" in served.content
    assert len(calls) == 1


# E-SF-04 / TB-E-24-a — wire fence over the pinned C6-4 (read-only): no commercial slot for
# a score, segment or health value in any successor of the quote command or its results.


def test_offer_quote_payload_has_no_score_slot(schema_bytes):
    from maezo.adapters.amh.commercial_fences import (
        OFFER_QUOTE_KEYS,
        offer_quote_slot_violations,
        result_score_slots,
    )

    document = json.loads(schema_bytes)
    payload = document["$defs"]["OfferQuotePayload"]
    assert set(payload["properties"]) == OFFER_QUOTE_KEYS
    assert offer_quote_slot_violations(document) == ()
    results = ("quoteResultData", "journey_snapshotResultData", "RecoveredJourneySnapshotResultData")
    assert result_score_slots(document, results) == ()
    # Injected violation: a successor adds `risk_score` to the quote payload / opens it.
    successor = json.loads(schema_bytes)
    successor["$defs"]["OfferQuotePayload"]["properties"]["risk_score"] = {"type": "number"}
    assert offer_quote_slot_violations(successor) == ("risk_score",)
    opened = json.loads(schema_bytes)
    opened["$defs"]["OfferQuotePayload"]["additionalProperties"] = True
    assert offer_quote_slot_violations(opened) == ("additionalProperties",)
    scored = json.loads(schema_bytes)
    scored["$defs"]["quoteResultData"]["properties"]["propensity"] = {"type": "number"}
    assert result_score_slots(scored, results) == ("quoteResultData.propensity",)
