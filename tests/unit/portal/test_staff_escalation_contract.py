"""D-M / T-M3: `staff_escalation.v1` repassada sem invencao; mascara no list, guia inteira no detail."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway.human.read_profile import parse_model, wire
from maezo.gateway.staff_cases.models import FIELDS
from maezo.gateway.staff_cases.service import NativeStaffPage
from maezo.portal.contracts.staff_cases import StaffDetail, StaffEscalation, StaffPage
from maezo.portal.engine.profile import ProfileError

AT = "2026-09-24T12:00:00.000000Z"
ACK = "2026-09-24T12:30:00.000000Z"
DUE = "2026-09-25T12:00:00.000000Z"
REF = "AUTH-00000000000000000000000000000001"
FRESH = {"observed_at": AT, "source_observed_at": AT, "valid_until": DUE, "refresh_after_seconds": 10}
FIELD_NAMES = ["guide_number", "reason_code", "priority", "ack_due_at", "resolution_due_at"]


def esc(**over: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "escalation_state": "resolved",
        "guide_number": "123456789012",
        "reason_code": "sla_vencido",
        "priority": "P3",
        "ack_due_at": ACK,
        "resolution_due_at": DUE,
    }
    return value | over


UNRESOLVED = esc(
    escalation_state="unresolved", reason_code=None, priority=None, ack_due_at=None, resolution_due_at=None
)


def summary(escalation: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "case_ref": REF,
        "kind": "authorization",
        "state": "active",
        "record_revision": "1",
        "state_observed_at": AT,
    }
    if escalation is not None:
        value["escalation"] = escalation
    return value | extra


def page(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "portal-staff-case-page.v1",
        "items": list(items),
        "next_cursor": None,
        "freshness": FRESH,
    }


def detail(case: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "portal-staff-case-detail.v1",
        "case": case,
        "identity": identity,
        "active_tasks": [],
        "next_task_cursor": None,
        "tasks_complete": True,
        "freshness": FRESH,
        "outcome": None,
    }


@pytest.fixture
def staff_identity() -> dict[str, Any]:
    return {
        "upstream_resource_key": "AUTH-tenant-123456789012",
        "case_ref": REF,
        "process_instance_ref": "pi-1",
        "process_definition_id": "pd-1",
        "process_definition_key": "authorization",
        "process_definition_version": "1",
        "process_definition_digest": "0" * 64,
        "kind": "authorization",
    }


def test_projection_fields_are_exactly_dm2() -> None:
    assert FIELDS["staff_escalation.v1"] == set(FIELD_NAMES)


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_each_field_is_passed_through_unchanged(field: str) -> None:
    parsed = StaffEscalation.model_validate(esc())
    assert getattr(parsed, field) == esc()[field]


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_resolved_requires_every_field(field: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(esc(**{field: None}))


def test_unresolved_keeps_the_case_with_null_escalation_fields() -> None:
    parsed = StaffEscalation.model_validate(UNRESOLVED)
    assert (parsed.reason_code, parsed.priority, parsed.ack_due_at, parsed.resolution_due_at) == (None,) * 4
    assert StaffEscalation.model_validate(UNRESOLVED | {"guide_number": None}).guide_number is None
    listed = StaffPage.model_validate_json(
        json.dumps(page(summary(UNRESOLVED | {"guide_number": "***9012"})))
    )
    assert listed.items[0].case_ref == REF


@pytest.mark.parametrize("field", ["reason_code", "priority", "ack_due_at", "resolution_due_at"])
def test_unresolved_refuses_invented_values(field: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(UNRESOLVED | {field: esc()[field]})


def test_list_accepts_only_the_masked_guide() -> None:
    masked = StaffPage.model_validate_json(json.dumps(page(summary(esc(guide_number="***9012")))))
    escalation = masked.items[0].escalation
    assert escalation is not None and escalation.guide_number == "***9012"
    with pytest.raises(ValidationError):
        StaffPage.model_validate_json(json.dumps(page(summary(esc()))))


@pytest.mark.parametrize(
    "guide", ["**9012", "***012", "***90123", "12***9012", "*** 9012", "-1234", "123456789012345678901"]
)
def test_malformed_mask_or_guide_is_refused(guide: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(esc(guide_number=guide))


@pytest.mark.parametrize("key", ["motivo", "motivo_fallback", "motivo_categoria"])
def test_free_text_reason_keys_are_refused(key: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(esc() | {key: "paciente relatou dor"})


@pytest.mark.parametrize("text", ["Paciente com dor", "sla vencido", "SLA_VENCIDO", "a" * 65, ""])
def test_reason_code_is_a_code_never_free_text(text: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(esc(reason_code=text))


def test_escalation_is_optional_for_an_engine_before_tm2() -> None:
    parsed = StaffPage.model_validate_json(json.dumps(page(summary())))
    assert parsed.items[0].escalation is None
    # Ausente nao vira `null` no repasse: o registro nativo antigo continua canonico.
    assert "escalation" not in parsed.model_dump_json(by_alias=True)


@pytest.mark.parametrize(
    "escalation", [esc(guide_number="***9012"), UNRESOLVED | {"guide_number": "***9012"}, None]
)
def test_native_record_is_passed_through_to_the_public_page_unchanged(
    escalation: dict[str, Any] | None,
) -> None:
    # O mesmo caminho do `StaffCaseService.list`: parse fechado do engine -> wire -> DTO publico.
    native = page(summary(escalation)) | {"freshness": FRESH | {"refresh_after_seconds": "10"}}
    public = wire(parse_model(NativeStaffPage, native))
    public["freshness"]["refresh_after_seconds"] = 10  # a unica conversao do servico (cadencia numerica)
    out = json.loads(StaffPage.model_validate_json(json.dumps(public)).model_dump_json(by_alias=True))
    assert out["items"][0].get("escalation") == escalation


def test_native_record_with_explicit_null_or_extra_member_is_refused() -> None:
    native = page(summary()) | {"freshness": FRESH | {"refresh_after_seconds": "10"}}
    for extra in ({"escalation": None}, {"motivo": "texto livre"}):
        record = native | {"items": [native["items"][0] | extra]}
        with pytest.raises(ProfileError):
            parse_model(NativeStaffPage, record)


def test_detail_carries_the_full_guide_and_refuses_the_mask(staff_identity: dict[str, Any]) -> None:
    full = StaffDetail.model_validate_json(json.dumps(detail(summary(esc()), staff_identity)))
    escalation = full.case.escalation
    assert escalation is not None and escalation.guide_number == "123456789012"
    with pytest.raises(ValidationError):
        StaffDetail.model_validate_json(
            json.dumps(detail(summary(esc(guide_number="***9012")), staff_identity))
        )


@pytest.mark.parametrize("priority", ["P1", "P2", "P3", "P10"])
def test_priority_accepts_the_dmn_output(priority: str) -> None:
    assert StaffEscalation.model_validate(esc(priority=priority)).priority == priority


@pytest.mark.parametrize("priority", ["alta", "p1", "P", "P123", "P1 "])
def test_priority_refuses_anything_but_the_dmn_code(priority: str) -> None:
    with pytest.raises(ValidationError):
        StaffEscalation.model_validate(esc(priority=priority))


def test_full_guide_follows_the_engine_pattern_including_the_synthetic_one() -> None:
    assert StaffEscalation.model_validate(esc(guide_number="SYN-C1GUIA1")).guide_number == "SYN-C1GUIA1"
