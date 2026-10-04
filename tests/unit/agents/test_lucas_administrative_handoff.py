"""Candidate handoff mechanics; no engine, source or conduct ratification."""

import pytest

from maezo.agents.lucas.administrative.handoff import (
    HANDOFF_SCHEMA,
    AdministrativeInputError,
    handoff_priority,
    parse_handoff,
)


def handoff_values() -> dict[str, object]:
    return {
        "schema_version": HANDOFF_SCHEMA,
        "task_type": "journey.compras.step",
        "tenant_ref": "unit-tenant-a",
        "journey_ref": "unit-journey-a",
        "message_ref": "hk1_" + "a" * 64,
    }


@pytest.mark.parametrize("field", ["message_body", "sintoma_codigo", "result", "permission", "raw_to"])
def test_handoff_rejects_clinical_output_and_authority_fields(field: str) -> None:
    values = handoff_values() | {field: "untrusted confidential value"}
    with pytest.raises(AdministrativeInputError, match="administrative_handoff_contract_mismatch") as error:
        parse_handoff(values)
    assert "confidential" not in str(error.value)


@pytest.mark.parametrize(
    ("health", "human", "expected"),
    [
        (True, True, "health"),
        (True, False, "health"),
        (False, True, "human"),
        (False, False, "administrative"),
    ],
)
def test_priority_always_preserves_health_then_human(health: bool, human: bool, expected: str) -> None:
    assert (
        handoff_priority(
            parse_handoff(handoff_values()),
            current_message_ref="hk1_" + "a" * 64,
            health_priority=health,
            human_requested=human,
        )
        == expected
    )


def test_previous_delivery_handoff_cannot_be_reused() -> None:
    with pytest.raises(AdministrativeInputError, match="administrative_handoff_stale_message"):
        handoff_priority(
            parse_handoff(handoff_values()),
            current_message_ref="hk1_" + "b" * 64,
            health_priority=False,
            human_requested=False,
        )


@pytest.mark.parametrize("task", ["journey.unknown.step", "authorization.analyze", "JOURNEY.COMPRAS.STEP"])
def test_only_exact_candidate_task_identifiers_exist(task: str) -> None:
    with pytest.raises(AdministrativeInputError):
        parse_handoff(handoff_values() | {"task_type": task})


@pytest.mark.parametrize("message", ["5511999999999", "a" * 64, "hk1_" + "a" * 63, ""])
def test_reference_requires_current_keyed_delivery_scheme(message: str) -> None:
    with pytest.raises(AdministrativeInputError):
        parse_handoff(handoff_values() | {"message_ref": message})
