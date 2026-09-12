"""Governed receipt OpenAPI describes the actual closed serialized wire."""

import copy
import json
from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from tests.unit.portal.test_assignment_receipt import pending
from tests.unit.portal.test_openapi_export import _load_exporter

from maezo.gateway.human.receipt import PublicAssignmentReceipt, PublicReceipt


def wire(operation, status, unchanged=False):
    value = pending() | {"operation": operation, "status": status}
    if operation != "reassign":
        value.update(target_ref=None, target_membership_revision=None)
    if status == "conflict":
        value.update(audit_result_ref="f" * 64, technical_code="REVISION_CONFLICT")
    if status == "committed":
        after = {"claim": "actor", "release": None, "reassign": "target"}[operation]
        before = after if unchanged else "previous"
        value.update(
            audit_result_ref="f" * 64,
            engine_receipt_ref="engine-receipt",
            engine_recorded_at=datetime(2026, 9, 10, tzinfo=UTC),
            consumed_task_revision="9007199254740993",
            resulting_task_revision="9007199254740994",
            prior_assignee_ref=before,
            resulting_assignee_ref=after,
            assignment_disposition="unchanged" if unchanged else "changed",
        )
    return PublicAssignmentReceipt.model_validate(value)


@pytest.mark.parametrize("operation", ["claim", "release", "reassign"])
@pytest.mark.parametrize(
    "status,unchanged", [("pending", False), ("conflict", False), ("committed", False), ("committed", True)]
)
def test_schema_matches_every_supported_governed_receipt_wire(operation, status, unchanged):
    receipt = wire(operation, status, unchanged)
    schema = PublicAssignmentReceipt.model_json_schema(mode="serialization")
    payload = json.loads(receipt.model_dump_json())
    assert (
        set(schema["properties"])
        == set(schema["required"])
        == set(payload)
        == set(PublicAssignmentReceipt.model_fields)
    )
    assert schema["additionalProperties"] is False
    Draft202012Validator(schema).validate(payload)
    # Existing wrap serializer's governed path is identity; null fields remain
    # present, revisions remain exact strings and operation is never omitted.
    assert payload == receipt.model_dump(mode="json")
    assert payload["operation"] == operation
    for key in payload:
        invalid = payload.copy()
        invalid.pop(key)
        assert not Draft202012Validator(schema).is_valid(invalid), key
    assert not Draft202012Validator(schema).is_valid(payload | {"undeclared": True})
    assert not Draft202012Validator(schema).is_valid(payload | {"source_revision": 9007199254740993})
    assert not Draft202012Validator(schema).is_valid(payload | {"generation_digest": "not-a-digest"})


def test_schema_generation_does_not_change_runtime_core_or_legacy_omission():
    original = copy.deepcopy(PublicAssignmentReceipt.__pydantic_core_schema__)
    serialization = PublicAssignmentReceipt.model_json_schema(mode="serialization")
    validation = PublicAssignmentReceipt.model_json_schema(mode="validation")
    assert serialization == validation
    assert original == PublicAssignmentReceipt.__pydantic_core_schema__
    legacy = {
        k: v
        for k, v in pending().items()
        if k in PublicReceipt.model_fields and k not in {"schema_version", "operation"}
    }
    receipt = PublicReceipt.model_validate(legacy)
    assert "operation" not in json.loads(receipt.model_dump_json())
    assert "operation" in wire("claim", "pending").model_dump()


def test_real_exporter_retains_receipt_route_union_and_exact_governed_fields():
    schema = _load_exporter().build_schema()
    governed = schema["components"]["schemas"]["PublicAssignmentReceipt"]
    assert governed == PublicAssignmentReceipt.model_json_schema(mode="serialization")
    for suffix in ("", "/receipt"):
        response = schema["paths"]["/api/v1/portal/commands/{command_id}" + suffix]["get"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]
        assert {item["$ref"].rsplit("/", 1)[-1] for item in response["anyOf"]} == {
            "DecisionReceiptResponse",
            "AssignmentReceiptResponse",
            "PublicAssignmentReceipt",
        }
