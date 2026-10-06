"""Frozen record/27-cursor contract and hostile input checks, UNIT only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maezo.gateway.capabilities.journeys import contracts as c
from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK

from .helpers import MEMBERS, Step, UnitJournal, UnitSourceOracle, binding, turn


def document() -> dict:
    root = Path(__file__).resolve().parents[5]
    return json.loads((root / "docs/design/v21-capabilities/journey-orchestration-contract.json").read_text())


def test_all_frozen_fields_and_27_technical_cursors_match_independent_document() -> None:
    doc = document()
    interfaces = doc["interfaces"]
    for name in ("JourneyBinding", "CurrentJourneyTurn", "JourneyDispatchOutcome"):
        assert set(getattr(c, name).model_fields) == set(interfaces[name]["fields"])
    assert set(c.VerifiedJourneyContinuation.model_fields) == set(
        interfaces["VerifiedJourneyContinuation"]["closed_fields"]
    )
    common = set(interfaces["PreparedJourneyAction"]["closed_common_fields"])
    variants = interfaces["PreparedJourneyAction"]["closed_variants"]
    for model, variant in [
        (c.PreparedCapabilityAction, "capability"),
        (c.PreparedDomainHandoffAction, "existing_domain_handoff"),
        (c.PreparedAwaitObservationAction, "await_observation"),
        (c.PreparedCurrentManifestationAction, "current_client_manifestation"),
        (c.PreparedTerminalAction, "verify_source_terminal"),
    ]:
        assert set(model.model_fields) == common | set(variants[variant])
    operations = {o["id"]: o["operation"] for o in doc["operations"]}
    seen = 0
    for journey in doc["journeys"].values():
        stages = STAGES_BY_TASK[journey["task_ref"]]
        assert set(stages) == {s["cursor_ref"] for s in journey["stages"]}
        for expected in journey["stages"]:
            stage = stages[expected["cursor_ref"]]
            assert stage.operations == frozenset(operations[o] for o in expected["operations"])
            assert stage.successor_cursor_refs == frozenset(expected["successor_cursor_refs"])
            seen += 1
    assert seen == 27
    assert all(
        "acceptance.record" not in stage.operations
        for stage in STAGES_BY_TASK["journey.suporte.step"].values()
    )


@pytest.mark.parametrize(
    "field",
    [
        "operation_name",
        "next_cursor",
        "receipt",
        "source_result",
        "raw_message",
        "clinical_text",
        "recipient",
        "config",
    ],
)
def test_current_turn_is_closed_before_any_source_or_checkpoint(field: str) -> None:
    marker = "SYNTHETIC_CLINICAL_BODY_90210"
    value = turn(binding(), 0).model_copy(update={field: marker})
    with pytest.raises(c.JourneyContractError) as error:
        c.parse_turn(value)
    assert marker not in str(error.value)


@pytest.mark.parametrize("revision", [True, -1, "2", 2.0])
def test_local_revision_never_accepts_business_ref_or_bool(revision: object) -> None:
    with pytest.raises(c.JourneyContractError):
        c.parse_turn(turn(binding(), 0).model_copy(update={"expected_journal_revision": revision}))


@pytest.mark.asyncio
async def test_output_planting_nested_poison_and_revoked_membership_reparsed() -> None:
    b = binding("journey.suporte.step")
    journal = UnitJournal(b)
    source = UnitSourceOracle(b, journal, [Step("S1_case", "case.open_or_update")])
    initial = (await journal.observe_journey(b.journal_binding())).snapshot
    action = await source.prepare(b, initial, turn(b, 0))
    with pytest.raises(c.JourneyContractError):
        c.parse_action(action.model_copy(update={"source_result": "planted"}), MEMBERS)
    poisoned = action.request.model_copy(
        update={"evidence_refs": ["SYNTHETIC_CLINICAL_BODY_90210"], "clinical_body": "planted"}
    )
    with pytest.raises(c.JourneyContractError):
        c.parse_action(action.model_copy(update={"request": poisoned}), MEMBERS)
    with pytest.raises(c.JourneyContractError):
        c.parse_action(action, c.DeclaredMemberships())


def test_effect_authority_records_match_admitted_r5_closed_contract_fields() -> None:
    amendment = document()["journey_effect_authority_amendment"]
    assert amendment["revision"] == "R5_DOMAIN_BOUNDARY_THIRD_REPAIR"
    metadata = {
        "JourneyEffectAuthority": {"meaning", "public_input_allowed"},
        "VerifiedJourneyEffectCurrentness": {"meaning", "closed_frozen_strict"},
        "OperationInvocationAuthority": {"provenance", "closed_frozen_strict"},
        "OriginalDomainInvocationAuthority": {
            "provenance",
            "closed_frozen_strict",
            "missing_native_contract_or_authentic_resolution",
        },
        "JourneyInvocationCheckpoint": {
            "issuance_scope",
            "closed_frozen_strict",
            "authenticity",
            "irreversible_boundary_rule",
            "external_atomicity_limit",
        },
    }
    for name, excluded in metadata.items():
        assert set(getattr(c, name).model_fields) == set(amendment["records"][name]) - excluded
