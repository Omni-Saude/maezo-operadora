"""Exact codec and canonical model validation; no schema-inferred clinical rules."""

import pytest
from pydantic import ValidationError
from tests.unit.gateway.human.test_classified_decision import configured

from maezo.portal.contracts.decisions import BrowserTaskDecision, DecisionSubmission, revision_from_decimal
from maezo.portal.contracts.models import TaskDecision


@pytest.mark.parametrize("bad", [None, True, 1, 1.0, "", "01", "-1", "+1", "1.0", "1e2", "１", "1\n", " 1"])
def test_revision_codec_has_no_coercion_or_noncanonical_spelling(bad):
    with pytest.raises(ValueError):
        revision_from_decimal(bad)


@pytest.mark.parametrize("value", ["0", "1", "9" * 5000])
def test_arbitrary_precision_codec_roundtrips_without_python_digit_limit(value):
    from maezo.gateway.human.projection import decimal_revision

    assert decimal_revision(revision_from_decimal(value)) == value


async def test_browser_codec_fields_track_actual_source_contract_and_preserve_unicode():
    _, decision, *_ = await configured()
    wire = BrowserTaskDecision.from_decision(decision)
    assert set(BrowserTaskDecision.model_fields) == set(TaskDecision.model_fields)
    assert wire.to_decision() == decision
    assert wire.to_decision().inputs.model_dump() == decision.inputs.model_dump()


@pytest.mark.parametrize(
    "field",
    [
        "process_definition_version",
        "form_version",
        "expected_task_revision",
        "expected_evidence_revision",
        "expected_membership_revision",
    ],
)
async def test_every_revision_field_handles_more_than_4300_digits(field):
    _, decision, *_ = await configured()
    wire = BrowserTaskDecision.from_decision(decision).model_dump()
    wire[field] = "9" * 5000
    validated = BrowserTaskDecision.model_validate(wire)
    backend = validated.to_decision()
    assert getattr(backend, field) == revision_from_decimal(wire[field])
    assert BrowserTaskDecision.from_decision(backend).model_dump() == wire


@pytest.mark.parametrize("field", ["process_definition_version", "form_version"])
async def test_positive_version_never_accepts_zero(field):
    _, decision, *_ = await configured()
    wire = BrowserTaskDecision.from_decision(decision).model_dump()
    wire[field] = "0"
    with pytest.raises(ValidationError):
        BrowserTaskDecision.model_validate(wire)


async def test_schema_version_is_explicit_and_forged_actor_fields_are_forbidden():
    _, decision, *_ = await configured()
    good = dict(
        schema_version="portal-decision-submission.v1",
        decision=BrowserTaskDecision.from_decision(decision),
        expected_authority_revision="7",
        expected_binding_digest="c" * 64,
    )
    for change in ({"actor_id": "forged"}, {"human_approved": True}, {"tenant": "other"}):
        with pytest.raises(ValidationError):
            DecisionSubmission(**{**good, **change})
