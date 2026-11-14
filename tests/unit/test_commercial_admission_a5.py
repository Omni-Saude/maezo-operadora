"""A5 (CR-F5/F6 suporte) registered-support admission — negative tests (D-16).

Executable regression for the planned negatives of admission A5 (GAP-CATALOG-A1-A10 §2):
(a) without an admitted A5 record no case instance crosses the boundary: the support/work
    command dimension (`support.handoff` + the `work.*` queue family) refuses the shipped
    fail-closed refusal before any source step (zero network, no effect phase), and a
    source-asserted case projection (`CustomerSupportRead` `status: "projection"`) is
    never disclosed on ANY of its three carriers (journey snapshot, recovered journey
    snapshot, recovered `support.handoff` `case_read`) — typed `denied/purpose_denied`
    before one byte, shipped `uncertain/reconciliation_required` custody on the wire, no
    re-dispatch, nothing synthesized in its place; `refused` renderings and
    `support_topics` stay byte-exact;
(b) a record citing a divergent contract sha is refused loudly and binds nothing —
    absence is the shipped state, never a default;
(c) resolution confirmation is never derived from OP03/acceptance vocabulary: the gate
    keys only on the source's own case arm, so a snapshot that proves issued contract +
    accepted acceptance still discloses a `refused` case arm byte-exact and still refuses
    a projection arm — and no OP-matrix id is a C6-4 command (the admitted scope is
    exactly the six contract commands).
Plus the admission invariants around them: the A5 record alone binds nothing (no session,
no catalog); the A1–A4 records never open the support/work dimension on the same app while
their own surfaces are untouched, and the A5 record never opens theirs (both directions);
refused/deferred owner decisions never bind; expired records, foreign admission ids,
foreign owner roles and records for other admission shapes are refused; the record
structure is closed; the loader re-validates on every read; the escalation process key
(`effect_pep`) stays a separate fail-closed surface; and composition refuses an admission
whose pin does not match the wired schema bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import maezo.adapters.amh.commercial as wire_module
import maezo.gateway.commercial_admission as admission_module
import maezo.gateway.commercial_support_admission as support_module
from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    AcceptanceRecordAdmissionError,
    parse_acceptance_record_admission,
)
from maezo.gateway.commercial_admission import (
    AcceptanceSupportUnadmittedSalesAdmission,
    AcceptanceUnadmittedSalesAdmission,
    AuthenticatedSalesAdmission,
    SupportGatedCodec,
    SupportUnadmittedSalesAdmission,
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256
from maezo.gateway.commercial_coverage_admission import (
    A4_CONTRACT_SHA256,
    CoverageReadAdmissionError,
    parse_coverage_read_admission,
)
from maezo.gateway.commercial_sales_admission import (
    A2_CONTRACT_SHA256,
    SalesSessionAdmissionError,
    parse_sales_session_admission,
)
from maezo.gateway.commercial_support_admission import (
    A5_ADMITTED_COMMANDS,
    A5_CONTRACT_SHA256,
    SupportHandoffAdmissionError,
    parse_support_handoff_admission,
    read_support_handoff_admission,
)
from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_router
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a2 import (
    ADMISSION_REQUIRED_BODY,
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
from tests.unit.test_commercial_admission_a4 import (
    FullScopeAuthority,
    JourneyScopeAuthority,
    a4_admission,
    a4_record,
    journey_with_coverage,
    snapshot_result,
)
from tests.unit.test_commercial_c6 import (
    CurrentResultSource,
    DeliveryOrigins,
    encode,
    envelope,
    journey,
    journey_request,
    outcome,
    recovery,
)
from tests.unit.test_commercial_http import (
    UnitCaller,
    UnitScope,
    wire_command,
    wire_result,
)
from tests.unit.test_commercial_http import (
    schema_bytes as schema_bytes,
)

AMH_ORIGIN = "https://amh.invalid"
CREDENTIAL = "Bearer SYN-SALES-CREDENTIAL"
CONNECTION = "SYN-SALES-CONNECTION"

WORK_QUEUE = {"kind": "work_queue", "items": [], "assignees": [], "next_cursor": None}


def a5_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A5",
            "decision": "admitted",
            "owners": ["case owner", "channel owner"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.4 (suporte)",
            "admitted_commands": sorted(A5_ADMITTED_COMMANDS),
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A5_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a5-object.json",
                "object_sha256": "a" * 64,
                "origin_git_sha": "b" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a5_admission():
    return parse_support_handoff_admission(a5_record(), now=datetime.now(UTC))


def support_command(name):
    # Schema-valid C6-4 wires for the exact six commands of the admitted scope (payload
    # shapes are the codec's own selectors; CAS commands carry expected_revision, which
    # the c6 envelope helper already knows).
    payloads = {
        "work.list": {"states": ["open"], "cursor": None, "limit": 10},
        "work.claim": {"work_ref": "SYN-WORK"},
        "work.assign": {"work_ref": "SYN-WORK", "assignee_ref": "SYN-STAFF"},
        "work.handoff": {"work_ref": "SYN-WORK", "action_ref": "SYN-SOURCE-ACTION"},
        "work.reconcile": {"work_ref": "SYN-WORK"},
        "support.handoff": {"journey_ref": "SYN-JOURNEY", "topic_ref": "SYN-PUBLISHED-TOPIC"},
    }
    return envelope(name, payloads[name])


def case_arm(state):
    if state == "projection":
        return {
            "schemaVersion": "sales-support-case.proposed.v2",
            "status": "projection",
            "vocabularyRef": "SYN-VOCAB",
            "vocabularyRevision": 1,
            "caseKind": "SYN-KIND",
            "caseKindLabel": "Synthetic case kind",
            "caseStatus": "SYN-STATUS",
            "caseStatusLabel": "Synthetic case status",
            "caseRef": "SYN-CASE",
            "protocolRef": None,
        }
    return {
        "schemaVersion": "sales-support-case.proposed.v2",
        "status": "refused",
        "outcome": "SOURCE_UNAVAILABLE",
    }


def journey_with_support(arm):
    value = journey()
    value["support"] = arm
    value["support_topics"] = [
        {
            "topic_ref": "SYN-TOPIC",
            "label": "Synthetic administrative topic",
            "channel_kind": "administrative",
        }
    ]
    return value


def issued_journey_with_support(arm):
    value = journey_with_coverage("unknown", "absent_generation", issued=True)
    value["support"] = arm
    return value


def handoff_result(case_read):
    return {
        "kind": "support_handoff",
        "journey_ref": "SYN-JOURNEY",
        "handoff_ref": "SYN-HANDOFF",
        "source_revision": 1,
        "state": "pending",
        "source_receipt_ref": None,
        "case_read": case_read,
        "external_handoff": None,
    }


class SupportScopeAuthority(UnitScope):
    """Staff scope granting the whole support/work dimension — isolates the A5 gate.

    With this authority every command of the admitted scope passes the gateway's own
    scope checks (staff + sales_operations for `work.*`, journey linkage for
    `support.handoff`), so the only possible refusal of those commands on an unadmitted
    A5 is the admission gate itself, which is what the negatives pin by exact reason
    code and effect phases.
    """

    def __init__(self):
        super().__init__()
        self.scope = replace(
            self.scope,
            purpose_ref="sales_operations",
            actor_kind="staff",
            allowed_operations=frozenset({"operator.get", "operation.get", *A5_ADMITTED_COMMANDS}),
            granted_scopes=frozenset(
                {"interop/sales.work.read", "interop/sales.work.manage", "interop/sales.support"}
            ),
            resource_grant_ref="SYN-RESOURCE-GRANT",
            journey_linkage_ref="SYN-LINKAGE",
        )


class SupportAndJourneyScopeAuthority(SupportScopeAuthority):
    """The support/work dimension plus the journey read — for the cross-dimension tests."""

    def __init__(self):
        super().__init__()
        self.scope = replace(
            self.scope,
            allowed_operations=frozenset(
                {"operator.get", "operation.get", "journey.get", *A5_ADMITTED_COMMANDS}
            ),
            granted_scopes=frozenset(
                {
                    "interop/sales.work.read",
                    "interop/sales.work.manage",
                    "interop/sales.support",
                    "interop/sales.journey.read",
                }
            ),
        )


def journey_with_support_and_coverage(arm, coverage_state=None):
    value = journey_with_support(arm)
    if coverage_state is not None:
        value["coverage"] = {
            "schemaVersion": "customer-coverage.proposed.v1",
            "coverageState": coverage_state,
            "observedAt": "2026-10-01T00:00:00Z",
        }
    return value


def counting_transport(calls, *, handoff_arm=None, journey_value=None):
    def handler(request):
        calls.append(request)
        command = json.loads(request.content)["command"]
        if command == "journey.get":
            snapshot = (
                journey_value if journey_value is not None else journey_with_support(case_arm("refused"))
            )
            return httpx.Response(200, json=outcome(snapshot_result(snapshot)))
        if command == "support.handoff":
            return httpx.Response(200, json=outcome(handoff_result(handoff_arm or case_arm("refused"))))
        if command == "work.list":
            return httpx.Response(200, json=outcome(WORK_QUEUE))
        return httpx.Response(200, json=wire_result())

    return httpx.MockTransport(handler)


def build_app(schema_bytes, calls, *, sales_admitted, catalog_admitted, support_admitted, transport=None):
    scope_authority = SupportScopeAuthority()
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        transport=transport or counting_transport(calls),
    )
    if sales_admitted:
        kwargs["sales_admission"] = a2_admission()
    if support_admitted:
        kwargs["support_admission"] = a5_admission()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.state.public_catalog_gateway = compose_public_catalog_gateway(
        **catalog_kwargs(schema_bytes, calls, admitted=catalog_admitted)
    )
    app.include_router(commercial_router)
    return app


def build_journey_app(schema_bytes, calls, journey_value, *, support_admitted):
    scope_authority = JourneyScopeAuthority()

    def transport(request):
        calls.append(request)
        command = json.loads(request.content)["command"]
        body = outcome(snapshot_result(journey_value)) if command == "journey.get" else wire_result()
        return httpx.Response(200, json=body)

    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        transport=httpx.MockTransport(transport),
    )
    if support_admitted:
        kwargs["support_admission"] = a5_admission()
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
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


def operation_request(codec):
    return codec.parse_command(encode(envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"})))


def recovered_journey_payload(arm):
    return encode(
        outcome(recovery("journey.get", _strip_replay_actions(snapshot_result(journey_with_support(arm)))))
    )


def recovered_handoff_payload(arm):
    return encode(outcome(recovery("support.handoff", _strip_replay_actions(handoff_result(arm)))))


def test_frozen_contract_pin_lineage(schema_bytes):
    # The A5 admission pins exactly the same frozen C6-4 contract bytes as A1–A4 and the
    # codec candidate pin: one lineage, seven agreeing sources.
    digest = hashlib.sha256(schema_bytes).hexdigest()
    assert digest == A5_CONTRACT_SHA256
    assert digest == A4_CONTRACT_SHA256
    assert digest == A3_CONTRACT_SHA256
    assert digest == A2_CONTRACT_SHA256
    assert digest == A1_CONTRACT_SHA256
    assert digest == wire_module.CANDIDATE_SCHEMA_SHA256


def test_admitted_scope_is_exactly_the_contract_support_work_commands(schema_bytes):
    # The transcription is pinned against the live frozen contract: the six commands of
    # the admitted scope exist with exactly the scopes the dossier cites, no OP-matrix id
    # is a command, and there is no "case" command the admission could be read to open.
    schema = json.loads(schema_bytes)
    commands = schema["x-commands"]
    assert set(commands) & A5_ADMITTED_COMMANDS == A5_ADMITTED_COMMANDS
    expected_scopes = {
        "work.list": "interop/sales.work.read",
        "work.claim": "interop/sales.work.manage",
        "work.assign": "interop/sales.work.manage",
        "work.handoff": "interop/sales.work.manage",
        "work.reconcile": "interop/sales.work.manage",
        "support.handoff": "interop/sales.support",
    }
    assert {name: commands[name]["scope"] for name in expected_scopes} == expected_scopes
    assert not any(command.startswith(("case.", "op0", "op1")) for command in commands)


@pytest.mark.asyncio
async def test_default_and_support_only_compositions_never_bind(schema_bytes):
    # The crux (b): the A5 record is not a session key and not a source key — with every
    # authority port present and only A5 admitted, the Sales gateway stays unbound
    # (503 `binding_unqualified` on every command) and the catalog stays closed.
    gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    assert gateway.admission is None and gateway.source is None
    calls = []
    app = build_app(schema_bytes, calls, sales_admitted=False, catalog_admitted=False, support_admitted=True)
    assert app.state.commercial_gateway.admission is None
    assert app.state.commercial_gateway.source is None
    posted = await post_command(app, support_command("support.handoff"))
    assert posted.status_code == 503
    assert posted.json() == UNAVAILABLE_BODY
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(A5_ADMITTED_COMMANDS))
async def test_without_a5_no_support_or_work_command_reaches_the_source(schema_bytes, name):
    # Planned negative (a), command dimension, on a bound A2 session: each of the six
    # commands refuses the exact shipped `unavailable/binding_unqualified` BEFORE any
    # source step (zero network, no effect phase), while the same session still serves
    # the non-scope command through the full phase ladder.
    calls = []
    app = build_app(schema_bytes, calls, sales_admitted=True, catalog_admitted=False, support_admitted=False)
    admission = app.state.commercial_gateway.admission
    assert isinstance(admission, SupportUnadmittedSalesAdmission)
    assert set(A5_ADMITTED_COMMANDS) <= admission.refused_commands
    authority = admission._authority
    refused = await post_command(app, support_command(name))
    assert refused.status_code == 503
    body = refused.json()
    assert body["status"] == "unavailable" and body["reason_code"] == "binding_unqualified"
    assert body["data"] is None
    assert calls == []
    assert authority.phases == ["before_body"]  # admit ran; no effect phase was reached
    served_phases = len(authority.phases)
    served = await post_command(app, wire_command())
    assert served.status_code == 200 and served.json() == wire_result()
    assert authority.phases[served_phases:] == [
        "before_body",
        "before_source",
        "before_effect",
        "before_disclosure",
    ]
    assert len(calls) == 1  # only the served command reached the wire


def test_gate_class_follows_exactly_the_admitted_dimensions(schema_bytes):
    # The composition installs exactly one closed set per absent record: the union when
    # both are absent, each single dimension when only the other is present, and the
    # plain session admission when both records exist. No cross-default exists.
    def admission_class(**admissions):
        scope_authority = SupportScopeAuthority()
        gateway = compose_commercial_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=scope_authority,
            source_authority=CurrentResultSource(scope_authority.scope),
            sales_admission=a2_admission(),
            **admissions,
        )
        return type(gateway.admission)

    assert (
        admission_class(acceptance_admission=a3_admission(), support_admission=a5_admission())
        is AuthenticatedSalesAdmission
    )
    assert admission_class(acceptance_admission=a3_admission()) is SupportUnadmittedSalesAdmission
    assert admission_class(support_admission=a5_admission()) is AcceptanceUnadmittedSalesAdmission
    assert admission_class() is AcceptanceSupportUnadmittedSalesAdmission


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["work.list", "support.handoff"])
async def test_admitted_a5_unlocks_only_the_support_work_dimension(schema_bytes, name):
    # Controlled positive: with A5 admitted the scope commands pass the gate and are
    # served by the real source path (the codec/source custody, not the admission, owns
    # the outcome). On the very same app the A1 catalog stays closed, the A3 acceptance
    # surface stays closed and a non-scope command is unchanged. (The CAS writes share
    # the exact same gate class; their source-owned custody is c6-pinned.)
    calls = []
    app = build_app(
        schema_bytes,
        calls,
        sales_admitted=True,
        catalog_admitted=False,
        support_admitted=True,
        transport=counting_transport(calls, handoff_arm=case_arm("refused")),
    )
    served = await post_command(app, support_command(name))
    assert served.status_code == 200
    assert json.loads(served.content)["status"] == "succeeded"
    operator = await post_command(app, wire_command())
    assert operator.status_code == 200 and operator.json() == wire_result()
    accepted = await post_command(app, acceptance_command())
    assert accepted.status_code == 503
    assert accepted.json()["reason_code"] == "binding_unqualified"  # A3 gate untouched
    catalog = await get_public(app)
    assert catalog.status_code == 503
    assert catalog.json() == SOURCE_UNAVAILABLE_BODY


@pytest.mark.asyncio
async def test_admitted_a5_discloses_the_source_owned_case_projection(schema_bytes):
    # With A5 admitted the source-asserted projection crosses the boundary byte-exact on
    # its own command (the `support.handoff` result's `case_read`) — the gate is inert,
    # nothing is rewritten and no vocabulary is invented.
    calls = []
    arm = case_arm("projection")
    app = build_app(
        schema_bytes,
        calls,
        sales_admitted=True,
        catalog_admitted=False,
        support_admitted=True,
        transport=counting_transport(calls, handoff_arm=arm),
    )
    served = await post_command(app, support_command("support.handoff"))
    assert served.status_code == 200
    assert json.loads(served.content)["data"]["case_read"] == arm
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a1_a2_a3_a4_surfaces_are_unchanged_while_the_support_gate_is_installed(schema_bytes):
    # Independence, one direction: A1–A4 admitted and the A5 gates shipped — the catalog
    # serves, the session serves, the acceptance effect stays codec-held, an admitted A4
    # still discloses its source-asserted coverage fact, and the support/work dimension
    # refuses. No admission re-grids another's surface.
    scope_authority = FullScopeAuthority()
    calls = []
    journey_value = journey_with_support_and_coverage(case_arm("refused"), coverage_state="active")
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        acceptance_admission=a3_admission(),
        coverage_admission=a4_admission(),
        transport=counting_transport(calls, journey_value=journey_value),
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
    disclosed = await post_journey(app)
    assert disclosed.status_code == 200  # A4 admitted: the coverage fact crosses
    journey_body = disclosed.json()["data"]["journeys"][0]
    assert journey_body["coverage"]["coverageState"] == "active"
    assert journey_body["support"] == case_arm("refused")
    refused = await post_command(app, support_command("support.handoff"))
    assert refused.status_code == 503
    assert refused.json()["reason_code"] == "binding_unqualified"  # A5 command gate
    assert len(calls) == 3  # catalog + operator + journey; the refusal never dispatched


@pytest.mark.asyncio
async def test_a5_does_not_open_the_acceptance_or_coverage_dimension(schema_bytes):
    # Independence, the other direction: A2 + A5 admitted, A3/A4 absent — the support
    # command is served, the acceptance effect still refuses with the A3 gate refusal
    # and a source-asserted coverage fact still stays undisclosed behind the A4 gate.
    calls = []
    scope_authority = SupportAndJourneyScopeAuthority()
    kwargs = dict(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        support_admission=a5_admission(),
        transport=counting_transport(
            calls,
            journey_value=journey_with_support_and_coverage(case_arm("refused"), coverage_state="active"),
        ),
    )
    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(**kwargs)
    app.include_router(commercial_router)
    served = await post_command(app, support_command("work.list"))
    assert served.status_code == 200 and json.loads(served.content)["data"] == WORK_QUEUE
    accepted = await post_command(app, acceptance_command())
    assert accepted.status_code == 503
    assert accepted.json()["reason_code"] == "binding_unqualified"
    gated = await post_journey(app)
    assert gated.status_code == 202
    assert gated.json()["status"] == "uncertain"
    assert gated.json()["reason_code"] == "reconciliation_required"
    assert gated.json()["data"] is None
    assert len(calls) == 2  # work.list + the journey read; never a replay


@pytest.mark.asyncio
@pytest.mark.parametrize("support_admitted", [False, True])
@pytest.mark.parametrize("arm_state", ["projection", "refused"])
async def test_journey_case_arm_disclosure_follows_exactly_the_a5_record(
    schema_bytes, support_admitted, arm_state
):
    # The disclosure truth table: a source-asserted projection crosses ONLY with A5
    # admitted; a `refused` rendering crosses in both states byte-exact (never gated,
    # never synthesized into a projection).
    calls = []
    app = build_journey_app(
        schema_bytes, calls, journey_with_support(case_arm(arm_state)), support_admitted=support_admitted
    )
    served = await post_journey(app)
    body = json.loads(served.content)
    if arm_state == "projection":
        assert served.status_code == (200 if support_admitted else 202)
        if support_admitted:
            assert body["data"]["journeys"][0]["support"] == case_arm("projection")
        else:
            assert body["data"] is None  # no case fact disclosed, nothing synthesized
            assert len(calls) == 1  # the read executed; nothing re-dispatched
    else:
        assert served.status_code == 200
        assert body["data"]["journeys"][0]["support"] == case_arm("refused")
        assert len(calls) == 1


def test_gate_refuses_every_carrier_with_the_exact_reason_code(schema_bytes):
    # The typed refusal, pinned at the gate: `denied/purpose_denied` on ALL THREE
    # carriers of the case projection (journey snapshot, recovered journey snapshot,
    # recovered `support.handoff` `case_read`) — "QUALQUER caminho" of disclosure, the
    # A4 repair's lesson applied from birth.
    codec = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    journey_command = codec.parse_command(encode(journey_request()))
    operation = operation_request(codec)
    carriers = [
        (encode(outcome(snapshot_result(journey_with_support(case_arm("projection"))))), journey_command),
        (recovered_journey_payload(case_arm("projection")), operation),
        (recovered_handoff_payload(case_arm("projection")), operation),
    ]
    for payload, command in carriers:
        with pytest.raises(CommercialRefusalError) as err:
            codec.parse_result(payload, command)
        assert err.value.status == "denied" and err.value.reason_code == "purpose_denied"
    ungated = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=False)
    for payload, command in carriers:
        parsed = ungated.parse_result(payload, command)
        assert b"projection" in parsed.wire_bytes  # the gate was the only refuser


def test_refused_rendering_and_topics_stay_byte_exact_under_the_gate(schema_bytes):
    # `refused` renderings (bounded outcome enum) and the journey's `support_topics`
    # are disclosed exactly as shipped — the gate never rewrites or replaces anything.
    codec = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    reference = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    journey_command = codec.parse_command(encode(journey_request()))
    payload = encode(outcome(snapshot_result(journey_with_support(case_arm("refused")))))
    parsed = codec.parse_result(payload, journey_command)
    expected = json.loads(reference.parse_result(payload, journey_command).wire_bytes)
    assert json.loads(parsed.wire_bytes) == expected
    disclosed = json.loads(parsed.wire_bytes)["data"]["journeys"][0]
    assert disclosed["support"] == case_arm("refused")
    assert disclosed["support_topics"]


def test_gate_never_touches_non_support_results(schema_bytes):
    codec = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    reference = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    operator_command = codec.parse_command(encode(wire_command()))
    assert codec.parse_result(encode(wire_result()), operator_command) == reference.parse_result(
        encode(wire_result()), operator_command
    )
    # A recovery whose original command is neither journey nor support read is unchanged.
    operation = operation_request(codec)
    payload = encode(outcome(recovery("operator.get", _strip_replay_actions(wire_result()["data"]))))
    assert codec.parse_result(payload, operation) == reference.parse_result(payload, operation)


@pytest.mark.asyncio
async def test_gateway_refuses_the_recovered_case_fact_before_disclosure(schema_bytes):
    # The wire keeps the shipped custody family on the recovery carrier too: the
    # `operation.get` recovering a `support.handoff` projection dispatches exactly once
    # (the read executed at the source), the case fact is never disclosed, and nothing
    # is re-dispatched.
    calls = []
    scope_authority = JourneyScopeAuthority()

    def transport(request):
        calls.append(request)
        command = json.loads(request.content)["command"]
        body = (
            recovered_handoff_payload(case_arm("projection")) if command == "operation.get" else wire_result()
        )
        return httpx.Response(200, json=json.loads(body))

    app = FastAPI()
    app.state.commercial_gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        amh_origin=AMH_ORIGIN,
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=CurrentResultSource(scope_authority.scope),
        delivery_origins=DeliveryOrigins(),
        sales_admission=a2_admission(),
        transport=httpx.MockTransport(transport),
    )
    app.include_router(commercial_router)
    refused = await post_command(app, envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"}))
    assert refused.status_code == 202
    body = refused.json()
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("arm_state", ["projection", "refused"])
async def test_issued_and_accepted_never_confirm_or_upgrade_a_case(schema_bytes, arm_state):
    # Planned negative (c): resolution confirmation is never derived from OP03/
    # acceptance vocabulary — on a snapshot that proves issued contract + accepted
    # acceptance, the gate keys ONLY on the source's own case arm: a projection arm is
    # still not disclosed without A5 and a `refused` arm is still disclosed byte-exact
    # (never upgraded to a confirmed case).
    calls = []
    app = build_journey_app(
        schema_bytes, calls, issued_journey_with_support(case_arm(arm_state)), support_admitted=False
    )
    served = await post_journey(app)
    disclosed = json.loads(served.content)["data"]
    if arm_state == "projection":
        assert served.status_code == 202 and disclosed is None
    else:
        assert served.status_code == 200
        journey_value = disclosed["journeys"][0]
        assert journey_value["support"] == case_arm("refused")
        assert journey_value["contract"]["state"] == "issued"
        assert journey_value["commercial_acceptance"]["decision"] == "accepted"
    assert len(calls) == 1


def test_escalation_substrate_stays_outside_the_admission_stack():
    # The human-escalation process key stays a separate fail-closed surface this package
    # never imports or wraps (`effect_pep`/`worker_runtime`); the admission stack ships
    # no record and no default.
    for module in (admission_module, support_module):
        imports = [
            line
            for line in Path(module.__file__).read_text().splitlines()
            if line.startswith(("import ", "from "))
        ]
        assert not any("effect_pep" in line or "worker_runtime" in line for line in imports)
    assert not any(hasattr(support_module, name) for name in ("DEFAULT_RECORD", "DEFAULT", "RECORD_PATH"))


def test_record_citing_a_divergent_contract_sha_is_refused(schema_bytes):
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(
            a5_record(contract_pin={"contract_id": "x", "revision": "C6-4", "sha256": "0" * 64}),
            now=datetime.now(UTC),
        )
    assert err.value.reason_code == "admission_pin_divergent"


def test_divergent_record_on_disk_is_refused_and_never_cached(tmp_path):
    path = tmp_path / "A5-ADMISSION-RECORD.json"
    path.write_bytes(a5_record())
    first = read_support_handoff_admission(path, now=datetime.now(UTC))
    assert first.admission_id == "A5"
    divergent = json.loads(path.read_text())
    for value in divergent.values():
        if isinstance(value, dict) and "sha256" in value:
            value["sha256"] = "0" * 64
    path.write_bytes(json.dumps(divergent).encode())
    with pytest.raises(SupportHandoffAdmissionError) as err:
        read_support_handoff_admission(path, now=datetime.now(UTC))
    assert err.value.reason_code == "admission_pin_divergent"
    # Absence is not a refusal: it is the shipped state, and nothing is defaulted.
    with pytest.raises(FileNotFoundError):
        read_support_handoff_admission(tmp_path / "absent.json", now=datetime.now(UTC))


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision):
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(a5_record(decision=decision), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "changes,expected",
    [
        # The admitted scope is exactly the six contract commands: extra commands, a
        # subset, the A1 surface, a non-existent command, an OP-matrix id, an empty list
        # and the other admissions' scope shapes all refuse.
        (
            {"admitted_commands": [*sorted(A5_ADMITTED_COMMANDS), "contract.issue"]},
            "admission_scope_invalid",
        ),
        ({"admitted_commands": ["work.list"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["support.handoff"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["catalog.get"]}, "admission_scope_invalid"),
        ({"admitted_commands": ["op07.case.open_or_update"]}, "admission_scope_invalid"),
        ({"admitted_commands": []}, "admission_scope_invalid"),
        ({"admitted_commands": "support.handoff"}, "admission_scope_invalid"),
        ({"admitted_scope": "POST /api/v1/sales/commercial"}, "admission_record_invalid"),
        ({"admitted_scope": "customer-coverage.proposed.v1"}, "admission_record_invalid"),
        # The unlock literal is transcribed exactly; near misses are not this admission.
        ({"unlocks": "P6.4"}, "admission_record_invalid"),
        ({"unlocks": "P6.4 (honra)"}, "admission_record_invalid"),
        ({"unlocks": "P6.2 (efeitos)"}, "admission_record_invalid"),
        ({"unlocks": "P6.3"}, "admission_record_invalid"),
        # Foreign admission ids never bind an A5 composition.
        ({"admission_id": "A1"}, "admission_record_invalid"),
        ({"admission_id": "A2"}, "admission_record_invalid"),
        ({"admission_id": "A3"}, "admission_record_invalid"),
        ({"admission_id": "A4"}, "admission_record_invalid"),
        ({"admission_id": "A10"}, "admission_record_invalid"),
    ],
)
def test_scope_beyond_the_support_work_commands_is_refused(changes, expected):
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(a5_record(**changes), now=datetime.now(UTC))
    assert err.value.reason_code == expected


def test_expired_record_does_not_bind():
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(
            a5_record(valid_until="2020-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_expired"
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(
            a5_record(decided_at="2099-01-01T00:00:00+00:00"), now=datetime.now(UTC)
        )
    assert err.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["produto"],  # A1's owner catalog never satisfies A5
        ["identity", "channel", "security", "DPO"],  # A2's
        ["cliente", "issuer"],  # A3's
        ["steward AMH", "direito"],  # A4's
        ["case owner", "case owner"],
        ["case owner", "DPO"],
        ["case owner", "channel owner", "human owners", "AMH contract steward"],
        "case owner",
    ],
)
def test_owner_roles_are_a_closed_catalog(owners):
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(a5_record(owners=owners), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema="omni-commercial-admission-record.v2"),
        lambda r: r.update(runtime_flag=True),
        lambda r: r.pop("contract_pin"),
        lambda r: r.update(admitted_scope="POST /api/v1/sales/commercial"),  # A2/A4's field, not A5's
        lambda r: r.pop("admitted_commands"),
        lambda r: r["evidence"].update(object_sha256="zz"),
        lambda r: r["evidence"].update(origin_git_sha="short"),
        lambda r: r.update(extra="field"),
    ],
)
def test_record_structure_is_closed(mutation):
    record = json.loads(a5_record())
    mutation(record)
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(json.dumps(record).encode(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_record_that_is_not_json_is_refused():
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(b"{not json", now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_naive_timestamps_are_never_admitted():
    with pytest.raises(SupportHandoffAdmissionError) as err:
        parse_support_handoff_admission(a5_record(valid_until="2099-01-01T00:00:00"), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_support_handoff_admission(a5_record(), now=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "parser,error",
    [
        (parse_acceptance_record_admission, AcceptanceRecordAdmissionError),
        (parse_coverage_read_admission, CoverageReadAdmissionError),
        (parse_sales_session_admission, SalesSessionAdmissionError),
    ],
)
def test_foreign_admission_parsers_reject_the_a5_shape(parser, error):
    with pytest.raises(error) as err:
        parser(a5_record(), now=datetime.now(UTC))
    assert err.value.reason_code == "admission_record_invalid"


def test_the_a5_parser_rejects_foreign_record_shapes():
    for record in (a2_record(), a3_record(), a4_record()):
        with pytest.raises(SupportHandoffAdmissionError) as err:
            parse_support_handoff_admission(record, now=datetime.now(UTC))
        assert err.value.reason_code == "admission_record_invalid"


def test_composition_refuses_a_support_admission_off_the_wired_contract(schema_bytes):
    calls = []
    scope_authority = SupportScopeAuthority()
    with pytest.raises(ValueError, match="commercial_support_admission_invalid"):
        compose_commercial_gateway(
            schema_bytes=schema_bytes,
            amh_origin=AMH_ORIGIN,
            caller=UnitCaller(),
            scope_authority=scope_authority,
            source_authority=CurrentResultSource(scope_authority.scope),
            sales_admission=a2_admission(),
            support_admission=SimpleNamespace(contract_sha256="0" * 64),
            transport=counting_transport(calls),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_pre_body_session_admission_is_unchanged_by_the_support_gate(schema_bytes):
    # The support gate lives in the effect phases: the route's pre-body session admission
    # (exact `AuthenticatedSalesAdmission` type check, header count guard) behaves
    # identically with the gate installed.
    calls = []
    app = build_app(schema_bytes, calls, sales_admitted=True, catalog_admitted=False, support_admitted=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.post(
            COMMERCIAL_PATH,
            headers=[("Authorization", CREDENTIAL), ("Content-Type", "application/json")],
            content=encode(support_command("support.handoff")),
        )
    assert result.status_code == 403
    assert result.json() == ADMISSION_REQUIRED_BODY
    assert calls == []


def test_fixture_variants_stay_schema_true():
    # Guard for the fixtures above: the projection-arm journey is exactly the c6 journey
    # with the support arm (and topics) swapped; the issued variant is the a4 issued
    # fixture with only the support arm swapped. Nothing else mutated.
    projected = journey_with_support(case_arm("projection"))
    base = journey()
    base["support"] = projected["support"]
    base["support_topics"] = projected["support_topics"]
    assert projected == base
    assert projected["support"]["status"] == "projection"
    issued = issued_journey_with_support(case_arm("projection"))
    assert issued["contract"]["state"] == "issued"
    assert issued["commercial_acceptance"]["decision"] == "accepted"
    assert issued["support"] == case_arm("projection")
    assert issued == journey_with_coverage("unknown", "absent_generation", issued=True) | {
        "support": case_arm("projection")
    }
