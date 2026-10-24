"""A4 (GAP-COVERAGE vigência) registered-coverage admission — negative tests (D-16).

Executable regression for the planned negatives of admission A4 (GAP-CATALOG-A1-A10 §2):
(a) the structural invariant `issued ≠ coverage_active`: no path derives a coverage fact
    from issuance/acceptance/payment/click — the gate keys only on the source's own
    journey-snapshot coverage arm, a snapshot with issued contract + accepted acceptance
    + `unknown` coverage is disclosed exactly as sent (never upgraded), and a
    source-asserted `active`/`revoked` arm is never disclosed without the registered A4
    record even when the same snapshot proves issuance (typed `denied/purpose_denied` at
    the gate, shipped `uncertain/reconciliation_required` custody on the wire, one single
    dispatch, no re-dispatch, nothing synthesized in its place);
(b) without an admitted A4 record the coverage dimension stays closed with zero side
    effect, a record citing a divergent contract sha is refused loudly and binds nothing,
    and no coverage command exists in the frozen contract to express a write — absence is
    the fail-closed state, never a default;
(c) containment: the read-only clinical port (`adapters/amh/subject_context.py`) stays
    outside the commercial coverage scope (never imported by the admission stack), and
    the A4 record shape is rejected by the A1/A2/A3 parsers and vice versa.
Plus the admission invariants around them: the A4 record alone binds nothing (no session,
no catalog); the A1/A2/A3 records never open the coverage dimension on the same app while
their own surfaces are untouched; the admitted scope is exactly the nominal READ
projection `customer-coverage.proposed.v1` (a projection, never a command or transport);
refused/deferred owner decisions never bind; expired records, foreign admission ids,
foreign owner roles and records for other admission shapes are refused; the record
structure is closed; the loader re-validates on every read; and composition refuses an
admission whose pin does not match the wired schema bytes.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import maezo.adapters.amh.commercial as wire_module
import maezo.gateway.commercial_admission as admission_module
import maezo.gateway.commercial_coverage_admission as coverage_module
from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    AcceptanceRecordAdmissionError,
    parse_acceptance_record_admission,
)
from maezo.gateway.commercial_admission import (
    CoverageGatedCodec,
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256
from maezo.gateway.commercial_coverage_admission import (
    A4_CONTRACT_SHA256,
    CoverageReadAdmissionError,
    parse_coverage_read_admission,
    read_coverage_read_admission,
)
from maezo.gateway.commercial_sales_admission import (
    A2_CONTRACT_SHA256,
    SalesSessionAdmissionError,
    parse_sales_session_admission,
)
from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_router
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a2 import (
    SOURCE_UNAVAILABLE_BODY,
    UNAVAILABLE_BODY,
    a2_admission,
    a2_record,
    catalog_kwargs,
    get_public,
)
from tests.unit.test_commercial_admission_a3 import (
    a3_admission,
    a3_record,
    acceptance_command,
)
from tests.unit.test_commercial_c6 import (
    CurrentResultSource,
    DeliveryOrigins,
    continuation_contract,
    continuation_journey,
    encode,
    journey,
    journey_request,
    outcome,
)
from tests.unit.test_commercial_http import (
    UnitCaller,
    UnitScope,
    UnitSourceAuthority,
    wire_command,
    wire_result,
)
from tests.unit.test_commercial_http import (
    schema_bytes as schema_bytes,
)

AMH_ORIGIN = "https://amh.invalid"
CREDENTIAL = "Bearer SYN-SALES-CREDENTIAL"
CONNECTION = "SYN-SALES-CONNECTION"


def a4_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A4",
            "decision": "admitted",
            "owners": ["steward AMH", "direito"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.3",
            "admitted_scope": "customer-coverage.proposed.v1",
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A4_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a4-object.json",
                "object_sha256": "b" * 64,
                "origin_git_sha": "1" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a4_admission():
    return parse_coverage_read_admission(a4_record(), now=datetime.now(UTC))


def coverage_arm(state, reason=None):
    arm = {
        "schemaVersion": "customer-coverage.proposed.v1",
        "coverageState": state,
        "observedAt": "2026-10-01T00:00:00Z",
    }
    if reason is not None:
        arm["unknownReason"] = reason
    return arm


def journey_with_coverage(state, reason=None, *, issued=False):
    value = continuation_journey() if issued else journey()
    value["coverage"] = coverage_arm(state, reason)
    if issued:
        value["contract"] = continuation_contract(value)
    return value


def snapshot_result(journey_value):
    return {"kind": "journey_snapshot", "journeys": [journey_value], "next_cursor": None, "plan_choices": []}


def issued_journey_result(state, reason=None):
    return snapshot_result(journey_with_coverage(state, reason, issued=True))


class JourneyScopeAuthority(UnitScope):
    """Scope granting the journey read — isolates the A4 coverage gate.

    With this authority the only possible refusal of a source-asserted coverage fact is
    the admission gate itself (the scope granted the read), which is what the negative
    below pins by exact reason code.
    """

    def __init__(self):
        super().__init__()
        self.scope = replace(
            self.scope,
            allowed_operations=frozenset({"operator.get", "operation.get", "journey.get"}),
            granted_scopes=frozenset({"interop/sales.journey.read"}),
            resource_grant_ref="SYN-RESOURCE-GRANT",
            journey_linkage_ref="SYN-LINKAGE",
        )


class FullScopeAuthority(JourneyScopeAuthority):
    """Journey scope that would also grant the acceptance operation — isolates no gate."""

    def __init__(self):
        super().__init__()
        self.scope = replace(
            self.scope,
            allowed_operations=frozenset(
                {"operator.get", "operation.get", "acceptance.record", "journey.get"}
            ),
        )


def counting_transport(calls, journeys):
    def handler(request):
        calls.append(request)
        command = json.loads(request.content)["command"]
        body = outcome(snapshot_result(journeys)) if command == "journey.get" else wire_result()
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def build_app(schema_bytes, calls, journeys, *, sales_admitted, catalog_admitted, coverage_admitted):
    scope_authority = JourneyScopeAuthority()
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        transport=counting_transport(calls, journeys),
    )
    if sales_admitted:
        kwargs["sales_admission"] = a2_admission()
    if coverage_admitted:
        kwargs["coverage_admission"] = a4_admission()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.state.public_catalog_gateway = compose_public_catalog_gateway(
        **catalog_kwargs(schema_bytes, calls, admitted=catalog_admitted)
    )
    app.include_router(commercial_router)
    return app


async def post_command(app, command, **headers):
    base = {"Authorization": CREDENTIAL, "X-Commercial-Connection": CONNECTION}
    base.update(headers)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        return await client.post(
            COMMERCIAL_PATH,
            headers={**base, "Content-Type": "application/json"},
            content=encode(command),
        )


async def post_journey(app):
    return await post_command(app, journey_request())


def test_frozen_contract_pin_lineage(schema_bytes):
    # The A4 admission pins exactly the same frozen C6-4 contract bytes as A1, A2, A3 and
    # the codec candidate pin: one lineage, six agreeing sources.
    digest = hashlib.sha256(schema_bytes).hexdigest()
    assert digest == A4_CONTRACT_SHA256
    assert digest == A3_CONTRACT_SHA256
    assert digest == A2_CONTRACT_SHA256
    assert digest == A1_CONTRACT_SHA256
    assert digest == wire_module.CANDIDATE_SCHEMA_SHA256


@pytest.mark.asyncio
async def test_default_and_coverage_only_compositions_never_bind(schema_bytes):
    # The crux (b): the A4 record is not a session key — with every authority port present
    # and only A4 admitted, the Sales gateway stays unbound (503 `binding_unqualified`) and
    # the catalog stays closed. No network, no default.
    gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    assert gateway.admission is None and gateway.source is None
    calls = []
    app = build_app(
        schema_bytes,
        calls,
        issued_journey_result("active"),
        sales_admitted=False,
        catalog_admitted=False,
        coverage_admitted=True,
    )
    assert app.state.commercial_gateway.admission is None
    assert app.state.commercial_gateway.source is None
    posted = await post_journey(app)
    assert posted.status_code == 503
    assert posted.json() == UNAVAILABLE_BODY
    assert calls == []


@pytest.mark.asyncio
async def test_without_a4_no_source_asserted_coverage_fact_is_disclosed(schema_bytes):
    # Planned negative (a) on a bound A2 session: the journey read itself executes at the
    # source (one dispatch), but the gate refuses the source-asserted `active` arm with the
    # exact shipped `purpose_denied` refusal before disclosure — the wire answers the
    # post-dispatch custody family, discloses no coverage fact, synthesizes nothing in its
    # place, and never re-dispatches. The same session still serves the non-journey command
    # exactly as shipped.
    calls = []
    app = build_app(
        schema_bytes,
        calls,
        journey_with_coverage("active"),
        sales_admitted=True,
        catalog_admitted=False,
        coverage_admitted=False,
    )
    refused = await post_journey(app)
    assert refused.status_code == 202
    body = refused.json()
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None  # no coverage fact disclosed, no synthesized replacement
    assert len(calls) == 1  # exactly one dispatch: the read itself, never a replay
    served = await post_command(app, wire_command())
    assert served.status_code == 200 and served.json() == wire_result()
    assert len(calls) == 2  # only the served command added a dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "revoked"])
async def test_revoked_is_as_gated_as_active_without_a4(schema_bytes, state):
    calls = []
    app = build_app(
        schema_bytes,
        calls,
        journey_with_coverage(state),
        sales_admitted=True,
        catalog_admitted=False,
        coverage_admitted=False,
    )
    refused = await post_journey(app)
    assert refused.status_code == 202
    assert refused.json()["data"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_issued_and_accepted_never_upgrade_an_unknown_coverage(schema_bytes):
    # The invariant half: a snapshot that proves issuance and acceptance but carries the
    # source's own `unknown` rendering is disclosed byte-exact as shipped — maezo never
    # upgrades `unknown` to `active` and never derives coverage from issued facts.
    calls = []
    arm = coverage_arm("unknown", "absent_generation")
    app = build_app(
        schema_bytes,
        calls,
        journey_with_coverage("unknown", "absent_generation", issued=True),
        sales_admitted=True,
        catalog_admitted=False,
        coverage_admitted=False,
    )
    served = await post_journey(app)
    assert served.status_code == 200
    disclosed = served.json()["data"]["journeys"][0]["coverage"]
    assert disclosed == arm  # exactly the source's own unknown fact, never upgraded
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_asserted_coverage_stays_gated_even_with_issued_contract(schema_bytes):
    # `issued ≠ coverage_active`: the same snapshot proves issued contract + accepted
    # acceptance + payment refs, and the coverage fact is STILL not disclosed without A4 —
    # the gate keys only on the source's own coverage arm, never on issuance.
    calls = []
    app = build_app(
        schema_bytes,
        calls,
        issued_journey_result("active"),
        sales_admitted=True,
        catalog_admitted=False,
        coverage_admitted=False,
    )
    refused = await post_journey(app)
    assert refused.status_code == 202
    assert refused.json()["data"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "revoked"])
async def test_admitted_a4_unlocks_only_the_coverage_read_dimension(schema_bytes, state):
    # Controlled positive: with A4 admitted the source-owned fact crosses the boundary
    # byte-exact; on the very same app the A1 catalog surface stays closed and the
    # non-journey command is unchanged — A4 unlocks the coverage read dimension and
    # nothing else.
    calls = []
    arm = coverage_arm(state)
    app = build_app(
        schema_bytes,
        calls,
        journey_with_coverage(state),
        sales_admitted=True,
        catalog_admitted=False,
        coverage_admitted=True,
    )
    served = await post_journey(app)
    assert served.status_code == 200
    assert served.json()["data"]["journeys"][0]["coverage"] == arm
    operator = await post_command(app, wire_command())
    assert operator.status_code == 200 and operator.json() == wire_result()
    catalog = await get_public(app)
    assert catalog.status_code == 503
    assert catalog.json() == SOURCE_UNAVAILABLE_BODY
    assert len(calls) == 2  # one journey, one operator — the closed catalog never dispatched


@pytest.mark.asyncio
async def test_a1_a2_a3_surfaces_are_unchanged_while_the_coverage_gate_is_installed(schema_bytes):
    # Independence, one direction: A1 admitted + A2 admitted + A3 admitted and the coverage
    # gate shipped — the catalog serves, the session serves, the acceptance effect stays
    # codec-held, and a source-asserted coverage fact stays gated. No admission re-grids
    # another's surface.
    scope_authority = FullScopeAuthority()
    calls = []
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        acceptance_admission=a3_admission(),
        transport=counting_transport(calls, journey_with_coverage("active")),
    )
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.state.public_catalog_gateway = compose_public_catalog_gateway(
        **catalog_kwargs(schema_bytes, calls, admitted=True)
    )
    app.include_router(commercial_router)
    catalog = await get_public(app)
    assert catalog.status_code == 200
    served = await post_command(app, wire_command())
    assert served.status_code == 200
    held = await post_command(app, acceptance_command())
    assert held.status_code == 403
    assert held.json()["reason_code"] == "terms_mismatch"
    gated = await post_journey(app)
    assert gated.status_code == 202 and gated.json()["data"] is None
    assert len(calls) == 3


def test_gate_refuses_with_the_exact_reason_code(schema_bytes):
    # The typed refusal, pinned at the gate: `denied/purpose_denied` — the dossier's own
    # admission reason. With the record admitted the gate is inert.
    codec = CoverageGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    command = codec.parse_command(encode(journey_request()))
    with pytest.raises(CommercialRefusalError) as err:
        codec.parse_result(encode(outcome(snapshot_result(journey_with_coverage("active")))), command)
    assert err.value.status == "denied" and err.value.reason_code == "purpose_denied"
    ungated = CoverageGatedCodec(
        CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=False
    )
    parsed = ungated.parse_result(encode(outcome(snapshot_result(journey_with_coverage("active")))), command)
    assert json.loads(parsed.wire_bytes)["data"]["journeys"][0]["coverage"]["coverageState"] == "active"


def test_gate_never_touches_non_journey_results_or_unknown_renderings(schema_bytes):
    codec = CoverageGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    reference = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    operator_command = codec.parse_command(encode(wire_command()))
    parsed = codec.parse_result(encode(wire_result()), operator_command)
    assert parsed == reference.parse_result(encode(wire_result()), operator_command)
    journey_command = codec.parse_command(encode(journey_request()))
    unknown = codec.parse_result(
        encode(outcome(snapshot_result(journey_with_coverage("unknown", "source_unavailable")))),
        journey_command,
    )
    assert (
        json.loads(unknown.wire_bytes)["data"]["journeys"][0]["coverage"]["unknownReason"]
        == "source_unavailable"
    )


def test_no_coverage_write_exists_in_the_frozen_contract(schema_bytes):
    # The admission cannot create a coverage write: the frozen contract's 42 commands name
    # none, and the codec refuses to even express one on the wire.
    schema = json.loads(schema_bytes)
    commands = set(schema["x-commands"])
    assert len(commands) == 42
    assert not any("coverage" in command for command in commands)
    codec = CoverageGatedCodec(CommercialWire(schema_bytes), gated=False)
    fabricated = {**json.loads(encode(wire_command())), "command": "coverage.activate"}
    with pytest.raises(CommercialRefusalError):
        codec.parse_command(encode(fabricated))


def test_clinical_port_stays_outside_the_commercial_coverage_scope():
    # Dossier A4 §Identidade: the read-only clinical coverage read never becomes the
    # commercial projection — the admission stack never imports or wraps it.
    for module in (admission_module, coverage_module):
        imports = [
            line
            for line in Path(module.__file__).read_text().splitlines()
            if line.startswith(("import ", "from "))
        ]
        assert not any("subject_context" in line for line in imports)
    from maezo.adapters.amh import subject_context

    clinical = Path(subject_context.__file__).read_text()
    # ... and the port itself stays the purpose-bound, consent-bound read it always was.
    assert "purpose_of_use" in clinical and "consent_decision_ref" in clinical


def test_record_citing_a_divergent_contract_sha_is_refused(schema_bytes):
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(
            a4_record(contract_pin={"contract_id": "x", "revision": "C6-4", "sha256": "0" * 64}),
            now=datetime.now(UTC),
        )
    assert err.value.reason_code == "admission_pin_divergent"


def test_divergent_record_on_disk_is_refused_and_never_cached(tmp_path):
    path = tmp_path / "A4-ADMISSION-RECORD.json"
    path.write_bytes(a4_record())
    first = read_coverage_read_admission(path, now=datetime.now(UTC))
    assert first.admission_id == "A4"
    divergent = json.loads(path.read_text())
    for value in divergent.values():
        if isinstance(value, dict) and "sha256" in value:
            value["sha256"] = "0" * 64
    path.write_bytes(json.dumps(divergent).encode())
    with pytest.raises(CoverageReadAdmissionError) as err:
        read_coverage_read_admission(path, now=datetime.now(UTC))
    assert err.value.reason_code == "admission_pin_divergent"
    # Absence is not a refusal: it is the shipped state, and nothing is defaulted.
    with pytest.raises(FileNotFoundError):
        read_coverage_read_admission(tmp_path / "absent.json", now=datetime.now(UTC))


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision):
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(a4_record(decision=decision), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "changes,expected",
    [
        # The admitted scope is exactly the nominal READ projection: commands, transports
        # and the other admissions' scopes all refuse.
        ({"admitted_scope": "journey.get"}, "admission_scope_invalid"),
        ({"admitted_scope": "coverage.activate"}, "admission_scope_invalid"),
        ({"admitted_scope": "CustomerCoverageRead"}, "admission_scope_invalid"),
        ({"admitted_scope": "GET /api/v1/sales/public/catalog"}, "admission_scope_invalid"),
        ({"admitted_scope": "POST /api/v1/sales/commercial"}, "admission_scope_invalid"),
        ({"admitted_scope": "acceptance.record"}, "admission_scope_invalid"),
        ({"admitted_scope": ["customer-coverage.proposed.v1"]}, "admission_scope_invalid"),
        ({"admitted_scope": ""}, "admission_scope_invalid"),
        # The unlock literal is transcribed exactly; near misses are not this admission.
        ({"unlocks": "P6.1"}, "admission_record_invalid"),
        ({"unlocks": "P6.2"}, "admission_record_invalid"),
        ({"unlocks": "P6.2 (efeitos)"}, "admission_record_invalid"),
        ({"unlocks": "P6.3 (cobertura)"}, "admission_record_invalid"),
        # Foreign admission ids never bind an A4 dimension.
        ({"admission_id": "A1"}, "admission_record_invalid"),
        ({"admission_id": "A2"}, "admission_record_invalid"),
        ({"admission_id": "A3"}, "admission_record_invalid"),
        ({"admission_id": "A10"}, "admission_record_invalid"),
    ],
)
def test_scope_beyond_the_coverage_projection_is_refused(changes, expected):
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(a4_record(**changes), now=datetime.now(UTC))
    assert err.value.reason_code == expected


def test_expired_record_does_not_bind():
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(
            a4_record(valid_until="2020-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_expired"
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(
            a4_record(decided_at="2099-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["produto"],  # A1's owner catalog never satisfies A4
        ["identity", "channel", "security", "DPO"],  # A2's owner catalog never satisfies A4
        ["cliente", "issuer"],  # A3's owner catalog never satisfies A4
        ["steward AMH", "steward AMH"],
        ["direito", "issuer"],
        ["steward AMH", "direito", "DPO"],
        "steward AMH",
    ],
)
def test_owner_roles_are_a_closed_catalog(owners):
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(a4_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema="omni-commercial-admission-record.v2"),
        lambda r: r.update(runtime_flag=True),
        lambda r: r.pop("contract_pin"),
        lambda r: r.update(admitted_commands=["acceptance.record"]),  # A3's field, not A4's
        lambda r: r.pop("admitted_scope"),
        lambda r: r["evidence"].update(object_sha256="zz"),
        lambda r: r["evidence"].update(origin_git_sha="short"),
        lambda r: r.update(extra="field"),
    ],
)
def test_record_structure_is_closed(mutation):
    record = json.loads(a4_record())
    mutation(record)
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(json.dumps(record).encode(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_record_that_is_not_json_is_refused():
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(b"{not json", now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_naive_timestamps_are_never_admitted():
    with pytest.raises(CoverageReadAdmissionError) as err:
        parse_coverage_read_admission(a4_record(valid_until="2099-01-01T00:00:00"), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_coverage_read_admission(a4_record(), now=datetime(2026, 1, 1))


@pytest.mark.parametrize("parser", [parse_acceptance_record_admission, parse_sales_session_admission])
def test_foreign_admission_parsers_reject_the_a4_shape(parser):
    with pytest.raises((AcceptanceRecordAdmissionError, SalesSessionAdmissionError)) as err:
        parser(a4_record(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_the_a4_parser_rejects_foreign_record_shapes():
    for record in (a2_record(), a3_record()):
        with pytest.raises(CoverageReadAdmissionError) as err:
            parse_coverage_read_admission(record, now=datetime.now(UTC))
        assert err.value.reason_code == "admission_record_invalid"


def test_composition_refuses_a_coverage_admission_off_the_wired_contract(schema_bytes):
    calls = []
    scope_authority = JourneyScopeAuthority()
    with pytest.raises(ValueError, match="commercial_coverage_admission_invalid"):
        compose_commercial_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=scope_authority,
            source_authority=UnitSourceAuthority(scope_authority.scope),
            sales_admission=a2_admission(),
            coverage_admission=SimpleNamespace(contract_sha256="0" * 64),
            transport=counting_transport(calls, journey_with_coverage("active")),
        )
    assert calls == []


def test_fixture_variants_stay_schema_true():
    # Guard for the fixtures above: the issued journey variant is exactly the c6
    # continuation fixture with the coverage arm swapped — nothing else mutated.
    value = deepcopy(journey_with_coverage("active", issued=True))
    assert value["coverage"]["coverageState"] == "active"
    assert value["contract"]["state"] == "issued"
    assert value["commercial_acceptance"]["decision"] == "accepted"
