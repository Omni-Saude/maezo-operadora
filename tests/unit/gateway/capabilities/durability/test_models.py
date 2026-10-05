"""Closed-model negative controls; no source authority or PostgreSQL proof."""

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from maezo.gateway.capabilities.durability import models
from maezo.gateway.capabilities.durability.models import (
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
    FenceVersion,
    JournalCallResult,
    JournalCallTechnicalStatus,
    JournalRefusalReason,
    JourneySnapshot,
    LocalRevision,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from tests.unit.gateway.capabilities.durability.fixtures import binding, descriptor, observation


def test_closed_interface_matches_frozen_source_contract():
    root = Path(__file__).resolve().parents[5]
    contract = json.loads((root / "docs/design/v21-capabilities/durability-contract.json").read_text())
    assert len(contract["records"]) == 17
    assert len(contract["protocol_methods"]) == 16
    for name, value in contract["records"].items():
        model = getattr(models, name)
        assert set(model.model_fields) == set(value["fields"])
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["frozen"] is True
    for method in contract["protocol_methods"]:
        signature = inspect.signature(getattr(DurabilityJournalPort, method["name"]))
        assert list(signature.parameters) == ["self", *method["closed_arguments"]]
        assert all(p.default is inspect.Parameter.empty for p in signature.parameters.values())


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1"])
def test_local_revision_is_strict_and_never_source_ref(value):
    with pytest.raises(ValidationError):
        TypeAdapter(LocalRevision).validate_python(value)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1"])
def test_fence_is_strict_positive_coordination_only(value):
    with pytest.raises(ValidationError):
        TypeAdapter(FenceVersion).validate_python(value)


def test_opaque_source_revision_preserved_without_cast():
    d = descriptor(binding())
    assert d.envelope.expected_business_revision == "source:rev/opaque-Z"
    obs = observation(binding(), d, revision="source:opaque-Z-before-A")
    assert obs.source_result.source_revision_ref == "source:opaque-Z-before-A"
    with pytest.raises(ValidationError):
        type(obs).model_validate(obs.model_copy(update={"result_sha256": "not-a-digest"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"phone": "synthetic-not-admitted"},
        {"journal_revision": True},
        {"recorded_at": datetime(2026, 1, 1)},
        {"technical_state": "COMPLETED"},
        {"dispatch_ref": "planted"},
    ],
)
def test_snapshot_rejects_shape_coercion_naive_clock_business_state_and_planted_fence(changes):
    b = binding()
    base = dict(
        handle=CommandHandle(binding=b, command_ref="unit-command", request_sha256="a" * 64),
        journal_revision=0,
        technical_state=CommandTechnicalState.RECORDED,
        recorded_at=datetime.now(UTC),
        last_observed_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        CommandSnapshot(**base | changes)


def test_unknown_commit_cannot_return_dispatch_or_disclosure_snapshot():
    snapshot = JourneySnapshot(
        binding=binding(), journal_revision=0, command_refs=(), wait_refs=(), outbox_refs=()
    )
    with pytest.raises(ValidationError):
        JournalCallResult[JourneySnapshot](
            technical_status=JournalCallTechnicalStatus.UNCERTAIN,
            refusal_reason=JournalRefusalReason.COMMIT_UNCERTAIN,
            snapshot=snapshot,
        )
    with pytest.raises(ValidationError):
        JournalCallResult[JourneySnapshot](technical_status=JournalCallTechnicalStatus.RECORDED)
