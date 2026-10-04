"""Contract conformance for the PROPOSED internal candidate; no source acceptance."""

import json
from pathlib import Path

import pytest

from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CONTRACT_STATE,
    REQUEST_MODELS,
    RESULT_MODELS,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityOutcome,
    CapabilityRefusalReason,
    DeclaredMemberships,
    parse_envelope,
    parse_request,
    parse_result,
    request_digest,
)


def envelope(operation: str = "acceptance.record") -> CapabilityEnvelope:
    return parse_envelope(
        {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "operation_name": operation,
            "tenant_ref": "unit-tenant",
            "legal_entity_ref": "unit-entity",
            "journey_ref": "unit-journey",
            "correlation_ref": "unit-correlation",
            "causation_ref": "unit-causation",
            "idempotency_key": "unit-command-key",
            "expected_business_revision": "unit-revision",
            "source_authority_ref": "unit-authority",
            "policy_revision": "unit-policy",
            "data_classification": "unit-classification",
        }
    )


def acceptance() -> dict[str, str]:
    return {
        "offer_ref": "unit-offer",
        "offer_version": "unit-version",
        "decision": "accept",
        "terms_evidence_ref": "unit-terms",
        "customer_authority_proof_ref": "unit-customer-authority",
    }


def test_field_partition_matches_reviewable_w0_contract() -> None:
    root = Path(__file__).resolve().parents[4]
    document = json.loads((root / "docs/design/v21-capabilities/contracts.json").read_text())
    assert CONTRACT_STATE == "PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED"
    assert document["common_envelope"]["published"] is False
    assert document["common_envelope"]["candidate_interface_only"] is True
    assert document["common_envelope"]["schema_version_value"] == CANDIDATE_SCHEMA_VERSION
    assert set(CapabilityEnvelope.model_fields) == set(document["common_envelope"]["field_names"])
    assert set(REQUEST_MODELS) == {item["operation"] for item in document["operations"]}
    for item in document["operations"]:
        request, result = REQUEST_MODELS[item["operation"]], RESULT_MODELS[item["operation"]]
        assert request.__name__ == item["request"]
        assert result.__name__ == item["response"]
        assert set(request.model_fields) == set(item["request_fields_proposed"])
        assert set(result.model_fields) == set(item["response_fields_proposed"])


@pytest.mark.parametrize("field,value", [("doctor_verdict", True), ("receipt_ref", "planted")])
def test_request_rejects_output_and_unknown_fields(field: str, value: object) -> None:
    with pytest.raises(CapabilityContractError) as caught:
        parse_request("acceptance.record", acceptance() | {field: value})
    assert caught.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert "planted" not in str(caught.value)


@pytest.mark.parametrize("value", [False, 0, "", " ", None])
def test_opaque_reference_never_coerces_or_fabricates(value: object) -> None:
    with pytest.raises(CapabilityContractError):
        parse_request("acceptance.record", acceptance() | {"offer_ref": value})


def test_unknown_operation_is_closed_and_safe() -> None:
    with pytest.raises(CapabilityContractError) as caught:
        parse_request("fake-clinical-id", {})
    assert caught.value.reason == CapabilityRefusalReason.UNKNOWN_OPERATION
    assert "fake-clinical-id" not in str(caught.value)


def test_duplicate_json_members_and_model_copy_poison_are_rejected() -> None:
    with pytest.raises(CapabilityContractError):
        parse_request("acceptance.record", b'{"offer_ref":"a","offer_ref":"b"}')
    request = parse_request("acceptance.record", acceptance())
    with pytest.raises(CapabilityContractError):
        parse_request("acceptance.record", request.model_copy(update={"receipt_ref": "planted"}))
    with pytest.raises(CapabilityContractError):
        parse_request("acceptance.record", request.model_copy(update={"decision": "authorize_clinical"}))


def test_memberships_are_unpublished_by_default_and_cannot_be_planted() -> None:
    payload = {
        "problem_ref": "unit-problem",
        "origin_case_or_journey_ref": "unit-case",
        "case_kind": "unit-confirmation",
        "requester_authority_ref": "unit-requester",
        "evidence_refs": ["unit-resolution-receipt", "unit-current-manifestation"],
    }
    with pytest.raises(CapabilityContractError):
        parse_request("case.open_or_update", payload)
    memberships = DeclaredMemberships(case_kinds=frozenset({"unit-confirmation"}))
    result = parse_request("case.open_or_update", payload, memberships=memberships)
    assert result.model_dump(mode="json")["evidence_refs"] == payload["evidence_refs"]
    assert isinstance(result.evidence_refs, tuple)
    with pytest.raises(CapabilityContractError):
        parse_request("case.open_or_update", payload | {"action": "confirm"}, memberships=memberships)
    with pytest.raises(CapabilityContractError):
        parse_request("case.open_or_update", payload | {"case_kind": "unpublished"}, memberships=memberships)
    with pytest.raises(CapabilityContractError):
        DeclaredMemberships(case_kinds={"mutable"})


def test_result_shape_does_not_accept_http_ack_or_unknown_case_status() -> None:
    with pytest.raises(CapabilityContractError):
        parse_result("acceptance.record", {"http_status": "200"})
    with pytest.raises(CapabilityContractError):
        parse_result(
            "case.open_or_update",
            {
                "case_ref": "unit-case",
                "case_status": "closed_without_confirmation",
                "authority_receipt_ref": "unit-receipt",
                "business_revision": "unit-next-revision",
            },
        )


def test_wait_timeout_and_notice_uncertainty_remain_exact_domain_states() -> None:
    waiting = parse_result(
        "external_wait.settle",
        {"wait_status": "timed_out", "settlement_revision": "unit-revision", "owner_case_ref": "unit-case"},
    )
    delivery = parse_result(
        "notice.prepare_or_send", {"delivery_status": "unknown", "attempt_revision": "unit-revision"}
    )
    assert waiting.wait_status == "timed_out"
    assert delivery.delivery_status == "unknown"
    assert delivery.provider_delivery_receipt_ref is None


def test_exact_true_feedback_and_aware_receipt_instant() -> None:
    feedback = {
        "feedback_observation_ref": "unit-feedback",
        "feedback_kind": "no_response",
        "recorded_at": "2026-10-04T10:00:00Z",
        "does_not_authorize_clinical_or_financial_change": True,
    }
    assert parse_result("feedback.record", feedback).feedback_kind == "no_response"
    for forged in (False, 1, "true"):
        with pytest.raises(CapabilityContractError):
            parse_result(
                "feedback.record", feedback | {"does_not_authorize_clinical_or_financial_change": forged}
            )
    with pytest.raises(CapabilityContractError):
        parse_result("feedback.record", feedback | {"recorded_at": "2026-10-04T10:00:00"})


def test_command_digest_binds_every_envelope_field_and_input() -> None:
    request = parse_request("acceptance.record", acceptance())
    original = request_digest(envelope(), request)
    assert original == request_digest(envelope(), parse_request("acceptance.record", acceptance()))
    for field in ("tenant_ref", "journey_ref", "expected_business_revision", "source_authority_ref"):
        assert original != request_digest(envelope().model_copy(update={field: "changed"}), request)
    assert original != request_digest(
        envelope(), parse_request("acceptance.record", acceptance() | {"decision": "decline"})
    )


def test_outcome_never_accepts_empty_or_result_and_refusal() -> None:
    with pytest.raises(ValueError):
        CapabilityOutcome()
    with pytest.raises(ValueError):
        CapabilityOutcome(
            result=parse_request("acceptance.record", acceptance()),
            refusal=CapabilityRefusalReason.SOURCE_UNAVAILABLE,
        )
