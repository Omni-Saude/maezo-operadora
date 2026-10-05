"""Closed candidate state and distinct root checkpoint identities."""

import pytest

from maezo.agents.lucas.administrative.handoff import HANDOFF_SCHEMA, AdministrativeInputError
from maezo.agents.lucas.administrative.state import (
    administrative_checkpoint_config,
    new_administrative_state,
    validate_administrative_input,
)
from maezo.gateway.capabilities.models import CANDIDATE_SCHEMA_VERSION, DeclaredMemberships
from maezo.gateway.pseudonymizer import Pseudonymizer


def state_values() -> dict[str, object]:
    envelope = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "operation_name": "access.resolve",
        "tenant_ref": "unit-tenant-a",
        "legal_entity_ref": "unit-legal-a",
        "journey_ref": "unit-journey-a",
        "correlation_ref": "unit-correlation-a",
        "causation_ref": "unit-causation-a",
        "idempotency_key": "unit-command-a",
        "expected_business_revision": "unit-revision-a",
        "source_authority_ref": "unit-source-a",
        "policy_revision": "unit-policy-a",
        "data_classification": "unit-administrative",
    }
    return {
        "envelope": envelope,
        "payload": {
            "subject_type": "prospect",
            "subject_or_prospect_ref": "unit-prospect-a",
            "operation_scope_ref": "unit-scope-a",
            "purpose_policy_ref": "unit-purpose-a",
            "source_context_refs": [],
        },
        "handoff": {
            "schema_version": HANDOFF_SCHEMA,
            "task_type": "journey.compras.step",
            "tenant_ref": envelope["tenant_ref"],
            "journey_ref": envelope["journey_ref"],
            "message_ref": "hk1_" + "a" * 64,
        },
        "current_message_ref": "hk1_" + "a" * 64,
        "health_priority": False,
        "human_requested": False,
    }


@pytest.mark.parametrize("field", ["outcome", "technical_status", "business_revision", "message_body"])
def test_caller_cannot_plant_outputs_or_free_text(field: str) -> None:
    with pytest.raises(AdministrativeInputError, match="administrative_input_contract_mismatch"):
        new_administrative_state(state_values() | {field: "confidential injected output"})


@pytest.mark.parametrize("field", ["tenant_ref", "journey_ref"])
def test_handoff_cannot_select_other_tenant_or_business_object(field: str) -> None:
    values = state_values()
    handoff = dict(values["handoff"])  # type: ignore[arg-type]
    handoff[field] = "unit-other-object"
    values["handoff"] = handoff
    with pytest.raises(AdministrativeInputError, match="administrative_handoff_object_mismatch"):
        new_administrative_state(values)


@pytest.mark.parametrize("value", [1, "false", None])
def test_priority_flags_never_coerce_truthiness(value: object) -> None:
    with pytest.raises(AdministrativeInputError, match="administrative_priority_contract_mismatch"):
        new_administrative_state(state_values() | {"health_priority": value})


def test_input_repr_does_not_expose_reference_or_payload() -> None:
    turn = new_administrative_state(state_values())["turn"]
    assert "unit-prospect-a" not in repr(turn)
    assert "unit-journey-a" not in repr(turn)


def test_unparsed_clinical_fields_never_enter_checkpointable_state() -> None:
    values = state_values()
    values["payload"] = dict(values["payload"]) | {"message_body": "confidential clinical text"}  # type: ignore[arg-type]
    with pytest.raises(
        AdministrativeInputError, match="administrative_envelope_or_payload_contract_mismatch"
    ):
        new_administrative_state(values)


def test_validated_carrier_is_reconstructed_without_aliasing_caller_dtos() -> None:
    original = new_administrative_state(state_values())["turn"]
    validated = validate_administrative_input({"turn": original})["turn"]
    assert validated is not original
    assert validated.envelope is not original.envelope
    assert validated.payload is not original.payload
    assert validated.handoff is not original.handoff
    object.__setattr__(original.handoff, "message_ref", "clinical body")
    assert validated.handoff.message_ref == "hk1_" + "a" * 64


def test_preparsed_case_request_requires_current_composition_memberships() -> None:
    values = state_values()
    values["envelope"] = dict(values["envelope"]) | {"operation_name": "case.open_or_update"}  # type: ignore[arg-type]
    values["payload"] = {
        "problem_ref": "unit-problem-a",
        "origin_case_or_journey_ref": "unit-journey-a",
        "case_kind": "unit-source-published-kind",
        "requester_authority_ref": "unit-requester-a",
        "evidence_refs": [],
    }
    members = DeclaredMemberships(case_kinds=frozenset({"unit-source-published-kind"}))
    initial = new_administrative_state(values, memberships=members)
    assert (
        validate_administrative_input(initial, memberships=members)["turn"].payload == initial["turn"].payload
    )
    with pytest.raises(
        AdministrativeInputError, match="administrative_envelope_or_payload_contract_mismatch"
    ):
        validate_administrative_input(initial, memberships=DeclaredMemberships())


def test_checkpoint_identity_scopes_both_tenant_and_journey_with_keyed_hash() -> None:
    pseudonymizer = Pseudonymizer(key=b"unit-checkpoint-key")
    configs = [
        administrative_checkpoint_config(
            task_type="journey.compras.step",
            tenant_ref=tenant,
            journey_ref=journey,
            pseudonymizer=pseudonymizer,
        )
        for tenant, journey in [
            ("unit-tenant-a", "unit-journey-a"),
            ("unit-tenant-a", "unit-journey-b"),
            ("unit-tenant-b", "unit-journey-a"),
        ]
    ]
    configs.append(
        administrative_checkpoint_config(
            task_type="journey.suporte.step",
            tenant_ref="unit-tenant-a",
            journey_ref="unit-journey-a",
            pseudonymizer=pseudonymizer,
        )
    )
    threads = [config["configurable"]["thread_id"] for config in configs]
    assert len(set(threads)) == 4
    assert all(thread.startswith("lucas:administrative:hk1_") for thread in threads)
    assert all("unit-journey" not in thread for thread in threads)
    assert all(config["configurable"]["checkpoint_ns"] == "" for config in configs)
