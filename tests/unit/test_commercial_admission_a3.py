"""A3 (CR-F3 aceite/matrícula) registered-acceptance admission — negative tests (D-16).

Executable regression for the planned negatives of admission A3 (GAP-CATALOG-A1-A10 §2):
(a) `accepted` without a source receipt stays refused by the C6-3 authority checks —
    repinned here on the acceptance surface this package touches;
(b) without an admitted A3 record no acceptance/enrollment effect executes: the A2-bound
    session composition refuses `acceptance.record` with the shipped fail-closed refusal
    before any source step (zero network, no effect phase), and a record citing a
    divergent contract sha is refused loudly and binds nothing — absence is the shipped
    state, never a default;
(c) the reconcile-only double-submit path stays source-owned custody: with A3 unadmitted
    acceptance execution never dispatches at all (asserted here); once admitted, the
    holding check is the codec's C6-3 terms authority, not the admission.
Plus the admission invariants around them: the A3 record alone binds nothing (no session,
no catalog); the A1 and A2 records never chain into the acceptance surface on the same
app; the admitted scope is exactly the contract command `acceptance.record` (OP03/OP04 are
51-interface-matrix vocabulary, not C6-4 commands); refused/deferred owner decisions never
bind; expired records, foreign admission ids, foreign owner roles and records for other
admission shapes are refused; the record structure is closed; the loader re-validates on
every read; and composition refuses an admission whose pin does not match the wired schema
bytes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import maezo.adapters.amh.commercial as wire_module
from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.capabilities.vendor_journey.binding import CHANNEL_FORBIDDEN_SEAT_OPERATIONS
from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    AcceptanceRecordAdmissionError,
    parse_acceptance_record_admission,
    read_acceptance_record_admission,
)
from maezo.gateway.commercial_admission import compose_commercial_gateway, compose_public_catalog_gateway
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256
from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_router
from maezo.ports.commercial import CommercialRefusalError, TermsReviewAuthority, scope_digest
from tests.unit.agents.test_administrative_journeys import observation
from tests.unit.gateway.capabilities.journeys.helpers import Step, turn
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_parts
from tests.unit.test_commercial_admission_a2 import (
    ADMISSION_REQUIRED_BODY,
    SOURCE_UNAVAILABLE_BODY,
    UNAVAILABLE_BODY,
    a2_admission,
    catalog_kwargs,
    get_public,
)
from tests.unit.test_commercial_c6 import (
    CurrentResultSource,
    DeliveryOrigins,
    continuation_journey,
    journey_request,
    parse_continuation,
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


def a3_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A3",
            "decision": "admitted",
            "owners": ["cliente", "issuer"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.2 (efeitos)",
            "admitted_commands": ["acceptance.record"],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A3_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a3-object.json",
                "object_sha256": "e" * 64,
                "origin_git_sha": "f" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a3_admission():
    return parse_acceptance_record_admission(a3_record(), now=datetime.now(UTC))


def acceptance_command(decision="accept"):
    # A schema-valid C6-4 `acceptance.record` wire command (scope interop/sales.contract).
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "command": "acceptance.record",
        "command_ref": "SYN-ACCEPT",
        "expected_revision": None,
        "payload": {
            "quote_ref": "SYN-QUOTE",
            "source_revision": 1,
            "party_linkage_ref": "SYN-LINKAGE",
            "terms_digest": "a" * 64,
            "decision": decision,
            "terms_review_ref": "SYN-REVIEW",
        },
    }


class AcceptanceScopeAuthority(UnitScope):
    """Session scope that would grant the acceptance operation — isolates the A3 gate.

    With this authority the only possible refusal of `acceptance.record` on an unadmitted
    A3 is the admission gate itself (the scope can no longer be the refuser), which is
    what the negative below pins by exact reason code and effect phases.
    """

    def __init__(self):
        super().__init__()
        self.scope = replace(
            self.scope,
            allowed_operations=frozenset({"operator.get", "operation.get", "acceptance.record"}),
        )


def counting_post_transport(calls):
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=wire_result())

    return httpx.MockTransport(transport)


def build_app(schema_bytes, calls, *, sales_admitted, catalog_admitted, acceptance_admitted):
    scope_authority = AcceptanceScopeAuthority()
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=UnitSourceAuthority(scope_authority.scope),
        transport=counting_post_transport(calls),
    )
    if sales_admitted:
        kwargs["sales_admission"] = a2_admission()
    if acceptance_admitted:
        kwargs["acceptance_admission"] = a3_admission()
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
            content=json.dumps(command).encode(),
        )


def test_frozen_contract_pin_lineage(schema_bytes):
    # The A3 admission pins exactly the same frozen C6-4 contract bytes as A1, A2 and the
    # codec candidate pin: one lineage, five agreeing sources.
    digest = hashlib.sha256(schema_bytes).hexdigest()
    assert digest == A3_CONTRACT_SHA256
    assert digest == A2_CONTRACT_SHA256
    assert digest == A1_CONTRACT_SHA256
    assert digest == wire_module.CANDIDATE_SCHEMA_SHA256


@pytest.mark.asyncio
async def test_default_and_acceptance_only_compositions_never_bind(schema_bytes):
    # The crux (b): the A3 record is not a session key and not a source key — with the
    # full authority ports present and only A3 admitted, the Sales gateway stays unbound
    # (503 `binding_unqualified` on every command) and the catalog stays closed. No
    # network, no default.
    gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    assert gateway.admission is None and gateway.source is None
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=False, catalog_admitted=False, acceptance_admitted=True
    )
    assert app.state.commercial_gateway.admission is None
    assert app.state.commercial_gateway.source is None
    posted = await post_command(app, acceptance_command())
    assert posted.status_code == 503
    assert posted.json() == UNAVAILABLE_BODY
    assert calls == []


@pytest.mark.asyncio
async def test_without_a3_no_acceptance_effect_executes(schema_bytes):
    # Planned negative (b) on a bound A2 session: `acceptance.record` refuses the shipped
    # fail-closed refusal BEFORE any source step — zero network, and the effect phases
    # never run (the scope authority only saw the pre-body phase) — while the same bound
    # session still serves the non-acceptance command exactly as shipped.
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=False
    )
    authority = app.state.commercial_gateway.admission._authority
    refused = await post_command(app, acceptance_command())
    assert refused.status_code == 503
    body = refused.json()
    assert body["status"] == "unavailable" and body["reason_code"] == "binding_unqualified"
    assert calls == []
    assert authority.phases == ["before_body"]  # admit ran; no effect phase was reached
    served_phases = len(authority.phases)
    served = await post_command(app, wire_command())
    assert served.status_code == 200 and served.json() == wire_result()
    # The served command walked the full phase ladder on the very same authority.
    assert authority.phases[served_phases:] == [
        "before_body",
        "before_source",
        "before_effect",
        "before_disclosure",
    ]
    assert len(calls) == 1  # only the served command reached the wire


@pytest.mark.asyncio
async def test_admitted_a3_unlocks_only_the_acceptance_surface(schema_bytes):
    # Controlled positive: with A3 admitted the same command passes the A3 admission gate.
    # An `accept` is then held by the RN 413 art. 4º disclosure precondition (E-SF-36): the
    # pinned C6-4 payload carries no inciso content, so it refuses `terms_review_required`
    # once the scope is current, before any source step. A `decline` (never blocked by the
    # disclosure) reaches the codec's own C6-3 terms authority (`terms_mismatch`) — the
    # issuer runtime stays absent and nothing simulates it. The catalog (A1) surface on the
    # same app stays closed, and the non-acceptance command is unchanged.
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=True
    )
    authority = app.state.commercial_gateway.admission._authority
    accepted = await post_command(app, acceptance_command())
    assert accepted.status_code == 403
    assert accepted.json()["reason_code"] == "terms_review_required"
    assert authority.phases == ["before_body", "before_source"]
    declined = await post_command(app, acceptance_command("decline"))
    assert declined.status_code == 403
    assert declined.json()["reason_code"] == "terms_mismatch"
    assert authority.phases == ["before_body", "before_source"] * 2
    assert calls == []  # the refusal is local: no source dispatch, no fabrication
    served = await post_command(app, wire_command())
    assert served.status_code == 200 and served.json() == wire_result()
    catalog = await get_public(app)
    assert catalog.status_code == 503
    assert catalog.json() == SOURCE_UNAVAILABLE_BODY
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a1_record_does_not_chain_into_the_acceptance_surface(schema_bytes):
    # Third independence leg: the A1 catalog record admitted, Sales unbound (no A2) — the
    # catalog serves while both the session command and the acceptance effect stay 503.
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=False, catalog_admitted=True, acceptance_admitted=False
    )
    catalog = await get_public(app)
    assert catalog.status_code == 200
    posted = await post_command(app, wire_command())
    assert posted.status_code == 503 and posted.json() == UNAVAILABLE_BODY
    accepted = await post_command(app, acceptance_command())
    assert accepted.status_code == 503
    assert accepted.json()["reason_code"] == "binding_unqualified"
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.asyncio
async def test_pre_body_session_admission_is_unchanged_by_the_acceptance_gate(schema_bytes):
    # The acceptance gate lives in the effect phases: the route's pre-body session
    # admission (exact `AuthenticatedSalesAdmission` type check, header count guard)
    # behaves identically with the gate installed.
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=True
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.post(
            COMMERCIAL_PATH,
            headers=[("Authorization", CREDENTIAL), ("Content-Type", "application/json")],
            content=json.dumps(acceptance_command()).encode(),
        )
    assert result.status_code == 403
    assert result.json() == ADMISSION_REQUIRED_BODY
    assert calls == []


def test_record_citing_a_divergent_contract_sha_is_refused(schema_bytes):
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(
            a3_record(contract_pin={"contract_id": "x", "revision": "C6-4", "sha256": "0" * 64}),
            now=datetime.now(UTC),
        )
    assert err.value.reason_code == "admission_pin_divergent"


def test_divergent_record_on_disk_is_refused_and_never_cached(tmp_path):
    path = tmp_path / "A3-ADMISSION-RECORD.json"
    path.write_bytes(a3_record())
    first = read_acceptance_record_admission(path, now=datetime.now(UTC))
    assert first.admission_id == "A3"
    divergent = json.loads(path.read_text())
    for value in divergent.values():
        if isinstance(value, dict) and "sha256" in value:
            value["sha256"] = "0" * 64
    path.write_bytes(json.dumps(divergent).encode())
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        read_acceptance_record_admission(path, now=datetime.now(UTC))
    assert err.value.reason_code == "admission_pin_divergent"
    # Absence is not a refusal: it is the shipped state, and nothing is defaulted.
    with pytest.raises(FileNotFoundError):
        read_acceptance_record_admission(tmp_path / "absent.json", now=datetime.now(UTC))


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision):
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(a3_record(decision=decision), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "changes,expected",
    [
        # The admitted scope is exactly the one contract command: extra commands, the A1
        # surface, a non-existent command, an empty list and an OP-matrix id all refuse.
        ({"admitted_commands": ["acceptance.record", "contract.issue"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["catalog.get"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["offer.quote"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["op03.enrollment.request"]}, "admission_scope_invalid"),
        ({"admitted_commands": []}, "admission_scope_invalid"),
        # A3 admits the effect dimension only — never the session transport.
        ({"admitted_scope": "POST /api/v1/sales/commercial"}, "admission_record_invalid"),
        # The unlock literal is transcribed exactly; near misses are not this admission.
        ({"unlocks": "P6.2"}, "admission_record_invalid"),
        ({"unlocks": "P6.2 (sessão)"}, "admission_record_invalid"),
        ({"unlocks": "P6.1"}, "admission_record_invalid"),
        ({"unlocks": "P6.3"}, "admission_record_invalid"),
        # Foreign admission ids never bind an A3 composition.
        ({"admission_id": "A1"}, "admission_record_invalid"),
        ({"admission_id": "A2"}, "admission_record_invalid"),
        ({"admission_id": "A10"}, "admission_record_invalid"),
    ],
)
def test_scope_beyond_acceptance_record_is_refused(changes, expected):
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(a3_record(**changes), now=datetime.now(UTC))
    assert err.value.reason_code == expected


def test_expired_record_does_not_bind():
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(
            a3_record(valid_until="2020-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_expired"
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(
            a3_record(decided_at="2099-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["produto"],  # A1's owner catalog never satisfies A3
        ["identity", "channel", "security", "DPO"],  # A2's owner catalog never satisfies A3
        ["cliente", "cliente"],
        ["issuer", "engenharia"],
        ["cliente", "DPO"],
        ["cliente", "issuer", "produto"],
        "cliente",
    ],
)
def test_owner_roles_are_a_closed_catalog(owners):
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(a3_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema="omni-commercial-admission-record.v2"),
        lambda r: r.update(runtime_flag=True),
        lambda r: r.pop("contract_pin"),
        lambda r: r.update(admitted_scope="POST /api/v1/sales/commercial"),  # A2's field, not A3's
        lambda r: r.pop("admitted_commands"),
        lambda r: r["evidence"].update(object_sha256="zz"),
        lambda r: r["evidence"].update(origin_git_sha="short"),
        lambda r: r.update(extra="field"),
    ],
)
def test_record_structure_is_closed(mutation):
    record = json.loads(a3_record())
    mutation(record)
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(json.dumps(record).encode(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_record_that_is_not_json_is_refused():
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(b"{not json", now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_naive_timestamps_are_never_admitted():
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(a3_record(valid_until="2099-01-01T00:00:00"), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_acceptance_record_admission(a3_record(), now=datetime(2026, 1, 1))


def test_composition_refuses_an_admission_off_the_wired_contract(schema_bytes):
    calls = []
    scope_authority = AcceptanceScopeAuthority()
    with pytest.raises(ValueError, match="commercial_acceptance_admission_invalid"):
        compose_commercial_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=scope_authority,
            source_authority=UnitSourceAuthority(scope_authority.scope),
            sales_admission=a2_admission(),
            acceptance_admission=SimpleNamespace(contract_sha256="0" * 64),
            transport=counting_post_transport(calls),
        )
    assert calls == []


def test_accepted_without_a_source_receipt_is_never_admitted(schema_bytes):
    # Planned negative (a), repinned on the surface this package touches: the C6-3
    # acceptance authority checks refuse a journey whose commercial acceptance is the
    # OP03 customer receipt itself, or whose quote/linkage diverged — `accepted` without
    # a source receipt is a fabrication, and no admission record changes that.
    value = continuation_journey()
    value["commercial_acceptance"] = deepcopy(value["acceptance"])
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    with pytest.raises(CommercialRefusalError):
        parse_continuation(codec, value, False)
    diverged = continuation_journey()
    diverged["commercial_acceptance"]["quote_ref"] = "FOREIGN"
    with pytest.raises(CommercialRefusalError):
        parse_continuation(codec, diverged, False)


# E-SF-02 (EP-CORE-00b-SIGN): the A3 signatories are the A-INDEX set, spelled out here
# rather than imported so a widened or shrunk constant in the loader cannot hide a defect.
A3_REQUIRED_SIGNATORIES = ("cliente", "issuer")


@pytest.mark.parametrize(
    "owners",
    [
        list(subset)
        for size in range(1, len(A3_REQUIRED_SIGNATORIES))
        for subset in itertools.combinations(A3_REQUIRED_SIGNATORIES, size)
    ],
)
def test_a3_record_signed_by_strict_subset_of_owner_roles_refuses(owners):
    # A record `admitted` by only part of the required signatories never parses: the
    # refusal is typed and happens in the parser, before any binding exists.
    with pytest.raises(AcceptanceRecordAdmissionError) as err:
        parse_acceptance_record_admission(a3_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize("owners", list(itertools.permutations(A3_REQUIRED_SIGNATORIES)))
def test_a3_record_signed_by_every_required_role_is_admitted(owners):
    admission = parse_acceptance_record_admission(a3_record(owners=list(owners)), now=datetime.now(UTC))
    assert set(admission.owners) == set(A3_REQUIRED_SIGNATORIES)


def journey_snapshot_transport(calls, journey_value):
    def transport(request):
        calls.append(request)
        snapshot = {
            "kind": "journey_snapshot",
            "journeys": [journey_value],
            "next_cursor": None,
            "plan_choices": [],
        }
        return httpx.Response(200, json={**wire_result(), "data": snapshot})

    return httpx.MockTransport(transport)


def build_admitted_journey_app(schema_bytes, calls, journey_value):
    # A2 and A3 admitted, the scope grants `journey.get`, and the source answers the
    # continuation snapshot given by the test — the bound route is the only refuser.
    scope_authority = AcceptanceScopeAuthority()
    scope_authority.scope = replace(
        scope_authority.scope,
        allowed_operations=scope_authority.scope.allowed_operations | {"journey.get"},
        granted_scopes=frozenset({"interop/sales.journey.read"}),
        resource_grant_ref="SYN-RESOURCE-GRANT",
        journey_linkage_ref="SYN-LINKAGE",
    )
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        acceptance_admission=a3_admission(),
        transport=journey_snapshot_transport(calls, journey_value),
    )
    app.include_router(commercial_router)
    return app


@pytest.mark.asyncio
async def test_admitted_route_serves_a_continuation_with_a_source_receipt(schema_bytes):
    # Control for the negative below: the same admitted route serves the continuation
    # when the source carries its commercial acceptance receipt.
    calls = []
    app = build_admitted_journey_app(schema_bytes, calls, continuation_journey())
    served = await post_command(app, journey_request())
    assert served.status_code == 200
    journey_out = served.json()["data"]["journeys"][0]
    assert journey_out["commercial_acceptance"]["acceptance_receipt_ref"] == "SYN-COMMERCIAL-RECEIPT"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, "", "   "])
async def test_accepted_without_source_receipt_refused_even_when_a3_admitted(schema_bytes, receipt):
    # TB-E-07-b ("clique não é prova"): with A2 and A3 admitted and composed into the
    # bound gateway, a source continuation that reports `accepted` without an
    # authenticated commercial `acceptance_receipt_ref` is refused on the HTTP route by
    # the C6-3 checks (the control above serves the same journey with its receipt) —
    # the admission binds the codec-checked path, it never stands in
    # for the source receipt. Nothing of the fabricated acceptance reaches the caller and
    # no effect command follows the single source read.
    calls = []
    value = continuation_journey()
    value["commercial_acceptance"]["acceptance_receipt_ref"] = receipt
    app = build_admitted_journey_app(schema_bytes, calls, value)
    refused = await post_command(app, journey_request())
    # The read already ran at the source, so the post-dispatch custody discipline answers
    # `uncertain/reconciliation_required` with no data — never `succeeded`, never a
    # re-dispatch.
    assert refused.status_code == 202
    body = refused.json()
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None
    assert b"accepted" not in refused.content and b"SYN-QUOTE" not in refused.content
    assert len(calls) == 1
    assert json.loads(calls[0].content)["command"] == "journey.get"
    # The generic post-dispatch answer is tied to this very journey: the C6-3 codec
    # refuses it on its own.
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, False)


# E-SF-03 (EP-REG-16a): the A3 fail-closed regressions repinned under the plan's names.
# Each is a thin repin of an existing pin (named in the comment) on the same helpers.


@pytest.mark.asyncio
async def test_a3_absent_record_refuses_effect(schema_bytes):
    # (a) Repin of `test_without_a3_no_acceptance_effect_executes`: with A2 bound, the
    # scope granting `acceptance.record` and the A3 record absent, the composition's
    # typed fail-closed refusal answers before any source step — zero network, no
    # effect phase — for a first and a repeated submission alike.
    calls = []
    app = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=False
    )
    authority = app.state.commercial_gateway.admission._authority
    for _ in range(2):
        refused = await post_command(app, acceptance_command())
        assert refused.status_code == 503
        assert refused.json()["status"] == "unavailable"
        assert refused.json()["reason_code"] == "binding_unqualified"
    assert calls == []
    assert authority.phases == ["before_body", "before_body"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minted",
    ["op03_receipt_as_source_receipt", "receipt_off_the_source_quote", "maezo_local_receipt_off_the_terms"],
)
async def test_acceptance_without_source_receipt_refused(schema_bytes, minted):
    # (b) Repin of `test_accepted_without_a_source_receipt_is_never_admitted` on the bound
    # HTTP route (helpers of TB-E-07-b, which covers the null/blank receipt): an
    # `accepted` whose commercial receipt is minted outside the source — the OP03
    # customer receipt passed off as the source acceptance, or a receipt not bound to
    # the source quote — is refused by the C6-3 checks even with A2+A3 admitted.
    # A locally minted receipt VALUE ("MAEZO-LOCAL-RECEIPT") is an opaque ref: by itself
    # it is not distinguishable from a source receipt, and neither is it with a divergent
    # `terms_review_ref` (the C6-3 continuation checks do not key on that field; probed).
    # Its authenticity is the source's result authority, not the codec. The variant is
    # therefore pinned combined with the binding the codec does check: the minted receipt
    # cites terms that are not the source quote's (`terms_digest` divergent).
    calls = []
    value = continuation_journey()
    if minted == "op03_receipt_as_source_receipt":
        value["commercial_acceptance"] = deepcopy(value["acceptance"])
    elif minted == "receipt_off_the_source_quote":
        value["commercial_acceptance"]["quote_ref"] = "SYN-LOCAL-QUOTE"
    else:
        value["commercial_acceptance"].update(
            acceptance_receipt_ref="MAEZO-LOCAL-RECEIPT",
            terms_review_ref="MAEZO-LOCAL-REVIEW",
            terms_digest="c" * 64,
        )
    app = build_admitted_journey_app(schema_bytes, calls, value)
    refused = await post_command(app, journey_request())
    assert refused.status_code == 202
    body = refused.json()
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None
    assert b"accepted" not in refused.content and b"MAEZO-LOCAL-RECEIPT" not in refused.content
    assert len(calls) == 1 and json.loads(calls[0].content)["command"] == "journey.get"
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, False)


class PresentedTermsSource(UnitSourceAuthority):
    """Source authority holding a current presented terms review for the acceptance."""

    async def prepare(self, command, scope):
        prepared = await super().prepare(command, scope)
        return replace(
            prepared,
            terms_review=TermsReviewAuthority(
                selection=command.terms_selection,
                scope_digest=scope_digest(scope),
                terms_review_ref="SYN-REVIEW",
                review_state="presented",
                presentation_receipt_ref="SYN-PRESENTATION",
                presentation_valid_until=scope.valid_until,
                currentness_ref="SYN-TERMS-CURRENT",
                document_validity_ref="SYN-DOCUMENT-VALID",
                valid_until=scope.valid_until,
            ),
        )


@pytest.mark.asyncio
async def test_acceptance_double_submit_is_forwarded_once_per_post_without_gateway_resend(schema_bytes):
    # (c) Repin of `test_uncertainty_preserves_original_identity_without_post_retry` on the
    # admitted acceptance surface. The first `acceptance.record` loses its response after
    # dispatch: the answer is `uncertain/reconciliation_required` with no data and the
    # gateway never resends. The gateway keeps no state between POSTs: the second POST
    # with the same `command_ref` (double click / reload) IS forwarded to the source, once
    # and byte-identical. "Zero redispatch" of that second POST therefore depends on the
    # source's dedupe by `command_ref` (here simulated by the source answering
    # reconcile-only) — an open GAP (DEC-REG REG-16, "Decidido dentro do registro" item
    # (3)), not a Maezo guarantee. What this pins is the Maezo side: one wire request per
    # caller POST, no gateway resend, and the answer is disclosed as `uncertain`. The
    # submission is a `decline`: an `accept` never reaches the source on the pinned C6-4
    # wire (E-SF-36 disclosure precondition), and the decision value is not what this pins.
    calls = []

    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic dropped response")
        return httpx.Response(
            200,
            json={
                **wire_result(),
                "status": "uncertain",
                "reason_code": "reconciliation_required",
                "data": None,
            },
        )

    scope_authority = AcceptanceScopeAuthority()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=PresentedTermsSource(scope_authority.scope),
        sales_admission=a2_admission(),
        acceptance_admission=a3_admission(),
        transport=httpx.MockTransport(transport),
    )
    app.include_router(commercial_router)
    for submitted in (1, 2):
        answered = await post_command(app, acceptance_command("decline"))
        assert answered.status_code == 202
        body = answered.json()
        assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
        assert body["data"] is None
        assert len(calls) == submitted  # one wire request per caller POST, no resend
    assert calls[0].content == calls[1].content
    assert json.loads(calls[1].content)["command_ref"] == "SYN-ACCEPT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cursor", "operation"),
    [("C2_acceptance", "acceptance.record"), ("C3_enrollment", "enrollment.request")],
)
async def test_vendor_channel_op03_op04_purpose_denied(cursor, operation):
    # (d) Repin of `test_channel_seats_are_refused_with_zero_downstream_effect`
    # (tests/unit/gateway/capabilities/vendor_journey/test_composition_e2e.py): the
    # vendor channel holds no OP03 acceptance seat nor OP04 enrollment route — the
    # binding fence (`VendorCompositionGuard`) refuses `purpose_denied` and no effect
    # beyond OP01/OP02 ever runs.
    steps = [
        Step("C1_need", "access.resolve"),
        Step("C1_options", "offer.compose"),
        Step("C_recovery", variant="wait"),
        Step(cursor, operation),
    ]
    b, journal, _, oracle, executor, driver = vendor_parts(steps)
    first = await driver.accept_turn(turn(b, journal.revision))
    assert first.technical_refusal is None
    await driver.accept_observation(observation(b, journal, "unit-vendor-seat-event"), journal.revision)
    result = await driver.resume("unit-vendor-seat-event", journal.revision)
    assert result.technical_refusal == CapabilityRefusalReason.PURPOSE_DENIED
    assert result.source_completion_receipt_ref is None
    assert [a.envelope.operation_name for a in executor.effects] == ["access.resolve", "offer.compose"]
    assert operation in CHANNEL_FORBIDDEN_SEAT_OPERATIONS
    assert oracle.domain_calls == []
