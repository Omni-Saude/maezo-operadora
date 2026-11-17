"""E-SF-36 (EP-REG-34) — the disclosure checklist wired into the bound Sales admission.

`require_precontract_disclosure` (`maezo.gateway.commercial_admission`) runs on every bound
Sales session, once the scope is current and before any source step. An `acceptance.record`
that is not an explicit `decline` must carry the RN 413 art. 4º I–IX/§1º content in its own
payload. The pinned C6-4 `AcceptanceRecordPayload` is closed and has none of those slots
(`test_checklist_slots_are_candidates_absent_from_the_pinned_wire`), so on that wire every
`accept` refuses `denied/terms_review_required` — with or without the A3 record — and a
`decline` is never blocked. Nothing here states the disclosure as complete.

The pinned `tests/candidate_artifacts/commercial/contract-c6-4.json` is only read.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_admission import (
    DISCLOSURE_GATED_COMMANDS,
    AcceptanceSupportUnadmittedSalesAdmission,
    AcceptanceUnadmittedSalesAdmission,
    AuthenticatedSalesAdmission,
    CatalogUnadmittedSalesAdmission,
    SupportUnadmittedSalesAdmission,
    require_precontract_disclosure,
)
from maezo.gateway.commercial_disclosure_checklist import (
    DISCLOSURE_TERMS_FIELDS,
    GUIA_ART18_SLOT,
    PUBLIC_PRICE_FIELD,
    RN413_ART4_CHECKLIST,
    DisclosureChecklistError,
    DisclosureRefusal,
)
from maezo.ports.commercial import CommercialRefusalError, SalesAdmission
from tests.unit.test_commercial_admission_a3 import (
    AcceptanceScopeAuthority,
    acceptance_command,
    build_app,
    post_command,
)
from tests.unit.test_commercial_http import UnitCaller, wire_command
from tests.unit.test_commercial_http import schema_bytes as schema_bytes


def _parsed(schema_bytes: bytes, command: dict[str, object]):
    return CommercialWire(schema_bytes).parse_command(json.dumps(command).encode())


def _with_payload(command, extra: dict[str, object]):
    """A successor-shaped command: the pinned payload plus disclosure slots.

    The pinned C6-4 schema refuses these keys at parse (closed payload), so the successor
    shape is built by replacing the immutable bytes of an already parsed command — it only
    exercises the gate's reading of the payload, never the wire.
    """
    envelope = json.loads(command.wire_bytes)
    envelope["payload"] = {**envelope["payload"], **extra}
    return replace(command, wire_bytes=json.dumps(envelope).encode())


def _full_disclosure() -> dict[str, object]:
    terms: dict[str, object] = {
        item.terms_field: f"Texto sintético de {item.dispositivo}" for item in RN413_ART4_CHECKLIST
    }
    terms[PUBLIC_PRICE_FIELD] = 45_990
    return terms


def _refusal(exc: pytest.ExceptionInfo[CommercialRefusalError]) -> tuple[str, str]:
    return exc.value.status, exc.value.reason_code


# --- the gate ------------------------------------------------------------------------------


def test_accept_on_the_pinned_wire_refuses_before_decision(schema_bytes) -> None:
    command = _parsed(schema_bytes, acceptance_command())
    assert command.terms_selection is not None and command.terms_selection.decision == "accept"
    with pytest.raises(CommercialRefusalError) as exc:
        require_precontract_disclosure(command)
    assert _refusal(exc) == ("denied", "terms_review_required")
    cause = exc.value.__cause__
    assert isinstance(cause, DisclosureChecklistError)
    assert cause.reason is DisclosureRefusal.DISCLOSURE_INCOMPLETO


def test_decline_is_never_blocked(schema_bytes) -> None:
    assert require_precontract_disclosure(_parsed(schema_bytes, acceptance_command("decline"))) is None


def test_only_the_acceptance_decision_is_gated(schema_bytes) -> None:
    assert frozenset({"acceptance.record"}) == DISCLOSURE_GATED_COMMANDS
    assert require_precontract_disclosure(_parsed(schema_bytes, wire_command())) is None


@pytest.mark.parametrize("item", RN413_ART4_CHECKLIST, ids=lambda item: item.terms_field)
def test_successor_payload_opens_only_with_every_item_present(schema_bytes, item) -> None:
    command = _parsed(schema_bytes, acceptance_command())
    assert require_precontract_disclosure(_with_payload(command, _full_disclosure())) is None
    missing = _full_disclosure()
    del missing[item.terms_field]
    with pytest.raises(CommercialRefusalError) as exc:
        require_precontract_disclosure(_with_payload(command, missing))
    assert _refusal(exc) == ("denied", "terms_review_required")


@pytest.mark.parametrize(
    "extra",
    [
        {"terms.rn413_art4_vii_breakdown": {"administracao": 1_000}},
        {"terms.comparativo_operadoras": "texto"},
    ],
)
def test_terms_content_outside_the_slots_is_never_dropped(schema_bytes, extra) -> None:
    # The checklist shape is closed: extra `terms.*` content reaches it and refuses, so a
    # price-formation breakdown cannot ride along a complete I–IX/§1º payload.
    command = _with_payload(_parsed(schema_bytes, acceptance_command()), {**_full_disclosure(), **extra})
    with pytest.raises(CommercialRefusalError) as exc:
        require_precontract_disclosure(command)
    assert _refusal(exc) == ("denied", "terms_review_required")


def test_decline_exemption_needs_payload_and_selection_to_agree(schema_bytes) -> None:
    accept = _parsed(schema_bytes, acceptance_command())
    decline = _parsed(schema_bytes, acceptance_command("decline"))
    # Selection says decline, wire says accept: treated as accept and refused.
    with pytest.raises(CommercialRefusalError):
        require_precontract_disclosure(replace(accept, terms_selection=decline.terms_selection))
    # Wire says decline, selection says accept (or is absent): refused as well.
    with pytest.raises(CommercialRefusalError):
        require_precontract_disclosure(replace(decline, terms_selection=accept.terms_selection))
    with pytest.raises(CommercialRefusalError):
        require_precontract_disclosure(replace(decline, terms_selection=None))


def test_filled_guia_slot_refuses_as_pending_source(schema_bytes) -> None:
    assert GUIA_ART18_SLOT.terms_field in DISCLOSURE_TERMS_FIELDS
    command = _with_payload(
        _parsed(schema_bytes, acceptance_command()),
        {**_full_disclosure(), GUIA_ART18_SLOT.terms_field: "itens do Guia"},
    )
    with pytest.raises(CommercialRefusalError) as exc:
        require_precontract_disclosure(command)
    assert _refusal(exc) == ("denied", "terms_review_required")
    cause = exc.value.__cause__
    assert isinstance(cause, DisclosureChecklistError)
    assert cause.reason is DisclosureRefusal.FONTE_NORMATIVA_PENDENTE


@pytest.mark.parametrize("payload", [None, [], "texto"])
def test_unreadable_payload_refuses(schema_bytes, payload) -> None:
    command = _parsed(schema_bytes, acceptance_command())
    envelope = json.loads(command.wire_bytes)
    envelope["payload"] = payload
    with pytest.raises(CommercialRefusalError) as exc:
        require_precontract_disclosure(replace(command, wire_bytes=json.dumps(envelope).encode()))
    assert _refusal(exc) == ("denied", "terms_review_required")


# --- the wiring ----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate_cls",
    [
        AuthenticatedSalesAdmission,
        SupportUnadmittedSalesAdmission,
        CatalogUnadmittedSalesAdmission,
    ],
)
async def test_every_a3_admitted_session_runs_the_gate(schema_bytes, gate_cls) -> None:
    # The session admissions a composition installs when A3 is admitted. Each one reaches
    # the base `current`, so the accept refuses after the scope is current.
    authority = AcceptanceScopeAuthority()
    admission = gate_cls(caller=UnitCaller(), authority=authority)
    accept = _parsed(schema_bytes, acceptance_command())
    with pytest.raises(CommercialRefusalError) as exc:
        await admission.current(authority.scope, phase="before_source", command=accept)
    assert _refusal(exc) == ("denied", "terms_review_required")
    assert authority.phases == ["before_source"]
    decline = _parsed(schema_bytes, acceptance_command("decline"))
    assert await admission.current(authority.scope, phase="before_source", command=decline) is authority.scope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate_cls", [AcceptanceUnadmittedSalesAdmission, AcceptanceSupportUnadmittedSalesAdmission]
)
async def test_without_a3_the_a3_gate_still_answers_first(schema_bytes, gate_cls) -> None:
    # The shipped A3-absent refusal is unchanged: it answers before the disclosure gate.
    authority = AcceptanceScopeAuthority()
    admission = gate_cls(caller=UnitCaller(), authority=authority)
    with pytest.raises(CommercialRefusalError) as exc:
        await admission.current(
            authority.scope, phase="before_source", command=_parsed(schema_bytes, acceptance_command())
        )
    assert _refusal(exc) == ("unavailable", "binding_unqualified")
    assert authority.phases == []


@pytest.mark.asyncio
async def test_bound_route_refuses_accept_with_zero_source_dispatch(schema_bytes) -> None:
    calls: list[object] = []
    app = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=True
    )
    for _ in range(2):
        refused = await post_command(app, acceptance_command())
        assert refused.status_code == 403
        assert refused.json()["status"] == "denied"
        assert refused.json()["reason_code"] == "terms_review_required"
        assert b"rn413" not in refused.content and b"incompleto" not in refused.content
    assert calls == []


class _ForeignAdmission(SalesAdmission):
    """An admission that is NOT the bound Sales one: it never runs the disclosure gate."""

    def __init__(self, scope) -> None:
        self.scope = scope
        self.phases: list[str] = []

    async def acquire(self, transport):
        return self.scope

    async def current(self, scope, *, phase, command=None):
        self.phases.append(phase)
        return scope


@pytest.mark.asyncio
async def test_gateway_runs_the_gate_whatever_the_admission(schema_bytes) -> None:
    # Defense in depth: `CommercialGateway.execute` calls the gate after the before_source
    # currentness, so a gateway composed with another admission still refuses the accept
    # with no source step.
    calls: list[object] = []
    bound = build_app(
        schema_bytes, calls, sales_admitted=True, catalog_admitted=False, acceptance_admitted=True
    ).state.commercial_gateway
    foreign = _ForeignAdmission(AcceptanceScopeAuthority().scope)
    gateway = CommercialGateway(codec=bound.codec, admission=foreign, source=bound.source)
    accept = _parsed(schema_bytes, acceptance_command())
    result = await gateway.execute(accept, foreign.scope)
    assert (result.status, result.reason_code) == ("denied", "terms_review_required")
    assert foreign.phases == ["before_source"]
    assert calls == []
