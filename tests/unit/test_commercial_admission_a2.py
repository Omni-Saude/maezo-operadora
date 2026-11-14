"""A2 (CR-F1 auth Sales) registered-session admission — negative tests (D-16).

Executable regression for the planned negatives of admission A2 (GAP-CATALOG-A1-A10 §2):
(a) without an admitted A2 record the Sales POST path stays closed with zero effect —
    503 `binding_unqualified` even with the full trusted authority ports present, and
    missing/partial headers refuse 403 `admission_required` before one body byte
    (`commercial_admission` transport guard);
(b) a record citing a divergent contract sha is refused loudly and binds nothing;
(c) portal/pin side: absence keeps the session admission blocked — cookie/role/env-flag
    change nothing, because binding is a registered record, never a toggle.
Plus the admission invariants around them: authority ports without a record never
install a concrete verifier (the unbound factory exposes no effect); the admitted
scope is exactly the single fixed Sales transport `POST /api/v1/sales/commercial` and
never a contract command (effects keep their own A3+ admissions); the A1 record and
the A2 record unlock disjoint surfaces on the same app; refused/deferred owner
decisions never bind; expired records, foreign admission ids, foreign owner roles and
records for other admission shapes are refused; the record structure is closed; the
loader re-validates on every read; and composition refuses an admission whose pin
does not match the wired schema bytes.
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
from maezo.adapters.amh.commercial import SCHEMA_VERSION, CommercialWire
from maezo.gateway.commercial_admission import (
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256
from maezo.gateway.commercial_sales_admission import (
    A2_CONTRACT_SHA256,
    SalesSessionAdmissionError,
    parse_sales_session_admission,
    read_sales_session_admission,
)
from maezo.portal.api.commercial import (
    COMMERCIAL_PATH,
    PUBLIC_CATALOG_PATH,
    commercial_command,
    commercial_router,
)
from tests.unit.test_commercial_c6 import (
    PublicScope,
    PublicSource,
    catalog_admission,
    public_body,
)
from tests.unit.test_commercial_http import (
    UnitCaller,
    UnitScope,
    UnitSourceAuthority,
    make_request,
    wire_command,
    wire_result,
)
from tests.unit.test_commercial_http import (
    schema_bytes as schema_bytes,
)

AMH_ORIGIN = "https://amh.invalid"
CREDENTIAL = "Bearer SYN-SALES-CREDENTIAL"
CONNECTION = "SYN-SALES-CONNECTION"
UNAVAILABLE_BODY = {
    "schema_version": SCHEMA_VERSION,
    "status": "unavailable",
    "operation_ref": None,
    "reason_code": "binding_unqualified",
    "data": None,
}
ADMISSION_REQUIRED_BODY = {
    "schema_version": SCHEMA_VERSION,
    "status": "denied",
    "operation_ref": None,
    "reason_code": "admission_required",
    "data": None,
}
SOURCE_UNAVAILABLE_BODY = {
    "schemaVersion": "sales-catalog.proposed.v1",
    "status": "unavailable",
    "reason": "source_unavailable",
}


def a2_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A2",
            "decision": "admitted",
            "owners": ["identity", "channel", "security", "DPO"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.2 (sessão)",
            "admitted_scope": "POST /api/v1/sales/commercial",
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A2_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a2-object.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a2_admission():
    return parse_sales_session_admission(a2_record(), now=datetime.now(UTC))


def counting_transport(calls):
    def transport(request):
        calls.append(request)
        body = public_body() if request.method == "GET" else wire_result()
        return httpx.Response(200, json=body)

    return httpx.MockTransport(transport)


def commercial_kwargs(schema_bytes, calls, *, admitted):
    scope_authority = UnitScope()
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=UnitSourceAuthority(scope_authority.scope),
        transport=counting_transport(calls),
    )
    if admitted:
        kwargs["sales_admission"] = a2_admission()
    return kwargs


def catalog_kwargs(schema_bytes, calls, *, admitted):
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=PublicScope(),
        source_authority=PublicSource(CommercialWire(schema_bytes)),
        transport=counting_transport(calls),
    )
    if admitted:
        kwargs["source_admission"] = catalog_admission()
    return kwargs


def mounted_app(schema_bytes, *, sales_admitted, catalog_admitted):
    calls = []
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(
        **commercial_kwargs(schema_bytes, calls, admitted=sales_admitted)
    )
    app.state.public_catalog_gateway = compose_public_catalog_gateway(
        **catalog_kwargs(schema_bytes, calls, admitted=catalog_admitted)
    )
    app.include_router(commercial_router)
    return app, calls


async def post_commercial(app, **headers):
    base = {"Authorization": CREDENTIAL, "X-Commercial-Connection": CONNECTION}
    base.update(headers)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.post(
            COMMERCIAL_PATH,
            headers={**base, "Content-Type": "application/json"},
            content=json.dumps(wire_command()).encode(),
        )


async def get_public(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.get(
            PUBLIC_CATALOG_PATH, headers={"Authorization": CREDENTIAL, "X-Commercial-Connection": CONNECTION}
        )


def test_frozen_contract_pin_lineage(schema_bytes):
    # The A2 admission pins exactly the same frozen C6-4 contract bytes as A1 and the
    # codec candidate pin: one lineage, four agreeing sources.
    digest = hashlib.sha256(schema_bytes).hexdigest()
    assert digest == A2_CONTRACT_SHA256
    assert digest == A1_CONTRACT_SHA256
    assert digest == wire_module.CANDIDATE_SCHEMA_SHA256


@pytest.mark.asyncio
async def test_default_composition_refuses_before_body_with_exact_body(schema_bytes):
    gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    assert gateway.admission is None and gateway.source is None
    app = FastAPI()
    app.state.commercial_gateway = gateway
    app.include_router(commercial_router)
    result = await post_commercial(app)
    assert result.status_code == 503
    assert result.json() == UNAVAILABLE_BODY


@pytest.mark.asyncio
async def test_full_authority_without_a_record_never_binds_any_effect(schema_bytes):
    # The crux: every trusted authority port present, record absent — the factory
    # exposes no effect at all (no admission, no source installed), the route answers
    # 503 `binding_unqualified` with full valid session headers and the network is
    # never touched. No composition exists that installs a concrete verifier without
    # the registered A2 record.
    app, calls = mounted_app(schema_bytes, sales_admitted=False, catalog_admitted=False)
    gateway = app.state.commercial_gateway
    assert gateway.admission is None and gateway.source is None
    result = await post_commercial(app)
    assert result.status_code == 503
    assert result.json() == UNAVAILABLE_BODY
    assert calls == []


@pytest.mark.asyncio
async def test_admitted_record_unlocks_only_the_sales_session_surface(schema_bytes):
    app, calls = mounted_app(schema_bytes, sales_admitted=True, catalog_admitted=False)
    gateway = app.state.commercial_gateway
    assert gateway.admission is not None and gateway.source is not None
    result = await post_commercial(app)
    assert result.status_code == 200 and result.json() == wire_result()
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST" and str(request.url) == f"{AMH_ORIGIN}/interop/sales/v1/commercial"
    assert request.headers["authorization"] == "Bearer SYN-MAEZO-CREDENTIAL"
    assert "cookie" not in request.headers
    # The A2 record never unlocks the public catalog: on the same app, with the full
    # catalog authority ports present but no A1 record, the catalog route stays 503.
    catalog = await get_public(app)
    assert catalog.status_code == 503
    assert catalog.json() == SOURCE_UNAVAILABLE_BODY
    assert len(calls) == 1  # still only the sales POST


@pytest.mark.asyncio
async def test_a1_record_does_not_unlock_the_sales_session(schema_bytes):
    # Mirror independence: with the A1 catalog record admitted and the full Sales
    # authority ports present, the catalog serves 200 while the Sales POST stays 503 —
    # no record chains into another admission's surface.
    app, calls = mounted_app(schema_bytes, sales_admitted=False, catalog_admitted=True)
    catalog = await get_public(app)
    assert catalog.status_code == 200 and catalog.json() == public_body()
    posted = await post_commercial(app)
    assert posted.status_code == 503
    assert posted.json() == UNAVAILABLE_BODY
    assert len(calls) == 1 and calls[0].method == "GET"


def test_record_citing_a_divergent_contract_sha_is_refused(schema_bytes):
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(
            a2_record(contract_pin={"contract_id": "x", "revision": "C6-4", "sha256": "e" * 64}),
            now=datetime.now(UTC),
        )
    assert err.value.reason_code == "admission_pin_divergent"


def test_divergent_record_on_disk_is_refused_and_never_cached(tmp_path):
    path = tmp_path / "A2-ADMISSION-RECORD.json"
    path.write_bytes(a2_record())
    first = read_sales_session_admission(path, now=datetime.now(UTC))
    assert first.admission_id == "A2"
    divergent = json.loads(path.read_text())
    for value in divergent.values():
        if isinstance(value, dict) and "sha256" in value:
            value["sha256"] = "e" * 64
    path.write_bytes(json.dumps(divergent).encode())
    with pytest.raises(SalesSessionAdmissionError) as err:
        read_sales_session_admission(path, now=datetime.now(UTC))
    assert err.value.reason_code == "admission_pin_divergent"
    # Absence is not a refusal: it is the shipped state, and nothing is defaulted.
    with pytest.raises(FileNotFoundError):
        read_sales_session_admission(tmp_path / "absent.json", now=datetime.now(UTC))


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision):
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(a2_record(decision=decision), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "changes,expected",
    [
        # The admitted scope is exactly the fixed Sales transport: the A1 surface, the
        # AMH-side transport, a list shape and the empty string all refuse.
        ({"admitted_scope": "GET /api/v1/sales/public/catalog"}, "admission_scope_invalid"),
        ({"admitted_scope": "POST /interop/sales/v1/commercial"}, "admission_scope_invalid"),
        ({"admitted_scope": ["POST /api/v1/sales/commercial"]}, "admission_scope_invalid"),
        ({"admitted_scope": ""}, "admission_scope_invalid"),
        # A2 admits the session dimension and no contract command.
        ({"admitted_scope": "catalog.get"}, "admission_scope_invalid"),
        ({"admitted_scope": "acceptance.record"}, "admission_scope_invalid"),
        # The unlock literal is transcribed exactly; near misses are not this admission.
        ({"unlocks": "P6.1"}, "admission_record_invalid"),
        ({"unlocks": "P6.2"}, "admission_record_invalid"),
        ({"unlocks": "P6.2 (sessao)"}, "admission_record_invalid"),
        # Foreign admission ids never bind an A2 composition.
        ({"admission_id": "A1"}, "admission_record_invalid"),
        ({"admission_id": "A3"}, "admission_record_invalid"),
        ({"admission_id": "A10"}, "admission_record_invalid"),
    ],
)
def test_scope_beyond_the_fixed_sales_transport_is_refused(changes, expected):
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(a2_record(**changes), now=datetime.now(UTC))
    assert err.value.reason_code == expected


def test_expired_record_does_not_bind():
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(
            a2_record(valid_until="2020-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_expired"
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(
            a2_record(decided_at="2099-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["produto"],  # A1's owner catalog never satisfies A2
        ["identity", "identity"],
        ["identity", "engenharia"],
        "identity",
        ["identity", "channel", "security", "DPO", "produto"],
    ],
)
def test_owner_roles_are_a_closed_catalog(owners):
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(a2_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema="omni-commercial-admission-record.v2"),
        lambda r: r.update(runtime_flag=True),
        lambda r: r.pop("contract_pin"),
        lambda r: r.update(admitted_commands=["catalog.get"]),  # A1's field, not A2's
        lambda r: r.pop("admitted_scope"),
        lambda r: r["evidence"].update(object_sha256="zz"),
        lambda r: r["evidence"].update(origin_git_sha="short"),
    ],
)
def test_record_structure_is_closed(mutation):
    record = json.loads(a2_record())
    mutation(record)
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(json.dumps(record).encode(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_record_that_is_not_json_is_refused():
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(b"{not json", now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_naive_timestamps_are_never_admitted():
    with pytest.raises(SalesSessionAdmissionError) as err:
        parse_sales_session_admission(a2_record(valid_until="2099-01-01T00:00:00"), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_sales_session_admission(a2_record(), now=datetime(2026, 1, 1))


def test_composition_refuses_an_admission_off_the_wired_contract(schema_bytes):
    calls = []
    with pytest.raises(ValueError, match="commercial_sales_admission_invalid"):
        compose_commercial_gateway(
            **commercial_kwargs(schema_bytes, calls, admitted=False),
            sales_admission=SimpleNamespace(contract_sha256="0" * 64),
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sent_headers",
    [
        [],
        [("X-Commercial-Connection", CONNECTION)],
        [("Authorization", CREDENTIAL)],
        [
            ("Authorization", CREDENTIAL),
            ("X-Commercial-Connection", CONNECTION),
            ("Authorization", CREDENTIAL),
        ],
        [
            ("Authorization", CREDENTIAL),
            ("X-Commercial-Connection", CONNECTION),
            ("X-Commercial-Connection", CONNECTION),
        ],
    ],
)
async def test_missing_partial_or_duplicated_headers_refuse_before_any_effect(schema_bytes, sent_headers):
    # The planned negative (a): without exactly one pair of complete session headers
    # the bound gateway still refuses 403 `denied/admission_required` before one body
    # byte — duplicated headers are refused by the same pre-body count guard.
    app, calls = mounted_app(schema_bytes, sales_admitted=True, catalog_admitted=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.post(
            COMMERCIAL_PATH, headers=[*sent_headers, ("Content-Type", "application/json")], content=b"{}"
        )
    assert result.status_code == 403
    assert result.json() == ADMISSION_REQUIRED_BODY
    assert calls == []  # zero effect: no source call, no disclosure


@pytest.mark.asyncio
async def test_malformed_bearer_refuses_before_one_body_byte(schema_bytes):
    app, calls = mounted_app(schema_bytes, sales_admitted=True, catalog_admitted=False)
    reads = []

    async def forbidden():
        reads.append(True)
        raise AssertionError("body was read before session qualification")

    request = make_request(
        app.state.commercial_gateway, authorization=b"Bearer browser-claim", receive=forbidden
    )
    response = await commercial_command(request)
    assert response.status_code == 403
    assert json.loads(bytes(response.body)) == ADMISSION_REQUIRED_BODY
    assert reads == [] and calls == []
