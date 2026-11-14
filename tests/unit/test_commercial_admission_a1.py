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
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import maezo.adapters.amh.commercial as wire_module
from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.gateway.commercial_admission import (
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.gateway.commercial_catalog_admission import (
    A1_CONTRACT_SHA256,
    CatalogSourceAdmissionError,
    parse_catalog_source_admission,
    read_catalog_source_admission,
)
from maezo.portal.api.commercial import PUBLIC_CATALOG_PATH, commercial_router
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_c6 import (
    PublicScope,
    PublicSource,
    envelope,
    journey,
    journey_result,
    parse,
    public_body,
    recovery,
)
from tests.unit.test_commercial_http import UnitCaller
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
