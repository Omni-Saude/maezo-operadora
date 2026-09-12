"""C9 closed receipt proof and current command/read role boundary controls."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from maezo.gateway.human.assignment_receipt import NativeAssignmentReceiptAuthority
from maezo.gateway.human.assignment_transport import NativeAssignmentClient
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.models import Scope
from maezo.gateway.human.receipt import PublicAssignmentReceipt, parse_public_receipt


def pending():
    return dict(
        schema_version="human-public-assignment-receipt.v1",
        tenant="tenant",
        task_id="task",
        command_id="command",
        payload_digest="a" * 64,
        principal_ref="actor",
        workload_ref="command-workload",
        operation="reassign",
        status="pending",
        audit_intent_ref="intent",
        audit_intent_hash="b" * 64,
        audit_result_ref=None,
        engine_receipt_ref=None,
        engine_recorded_at=None,
        consumed_task_revision=None,
        resulting_task_revision=None,
        technical_code=None,
        command_schema="human-assignment.v2",
        binding_ref="binding",
        binding_version="1",
        binding_digest="c" * 64,
        policy_ref="policy",
        policy_version="1",
        policy_digest="d" * 64,
        source_revision="9007199254740993",
        generation_digest="e" * 64,
        target_ref="target",
        target_membership_revision="9007199254740995",
        prior_assignee_ref=None,
        resulting_assignee_ref=None,
        assignment_disposition=None,
    )


def test_pending_receipt_preserves_command_pins_without_asserting_effect():
    raw = pending()
    result = parse_public_receipt(raw)
    assert isinstance(result, PublicAssignmentReceipt)
    assert result.model_dump() == raw
    assert all(f.is_required() for f in PublicAssignmentReceipt.model_fields.values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("prior_assignee_ref", "actor"),
        ("resulting_assignee_ref", "target"),
        ("assignment_disposition", "changed"),
        ("engine_receipt_ref", "fake"),
        ("audit_result_ref", "f" * 64),
    ],
)
def test_pending_cannot_invent_effect_or_audited_result(field, value):
    with pytest.raises(ValidationError):
        PublicAssignmentReceipt.model_validate(pending() | {field: value})


def test_conflict_retains_pins_and_requires_result_but_has_no_engine_effect():
    raw = pending() | dict(status="conflict", audit_result_ref="f" * 64, technical_code="REVISION_CONFLICT")
    assert parse_public_receipt(raw).model_dump() == raw
    for bad in ({"audit_result_ref": None}, {"technical_code": None}, {"resulting_task_revision": "4"}):
        with pytest.raises(ValidationError):
            parse_public_receipt(raw | bad)


@pytest.mark.parametrize(
    "operation,before,after,disposition",
    [
        ("claim", None, "actor", "changed"),
        ("release", "actor", None, "changed"),
        ("reassign", "target", "target", "unchanged"),
        ("reassign", None, "target", "changed"),
    ],
)
def test_committed_requires_authentic_proof_shape_and_exact_operation_result(
    operation, before, after, disposition
):
    raw = pending() | dict(
        operation=operation,
        status="committed",
        audit_result_ref="f" * 64,
        engine_receipt_ref="engine-receipt",
        engine_recorded_at=datetime.now(UTC),
        consumed_task_revision="3",
        resulting_task_revision="4",
        prior_assignee_ref=before,
        resulting_assignee_ref=after,
        assignment_disposition=disposition,
    )
    if operation != "reassign":
        raw.update(target_ref=None, target_membership_revision=None)
    assert parse_public_receipt(raw).assignment_disposition == disposition
    for bad in (
        {"engine_receipt_ref": None},
        {"resulting_assignee_ref": "wrong"},
        {"assignment_disposition": "changed" if disposition == "unchanged" else "unchanged"},
    ):
        with pytest.raises(ValidationError):
            parse_public_receipt(raw | bad)


def test_private_read_workload_is_separate_from_command_resource_scope():
    command_scope = Scope(tenant="tenant", environment="test", workload_ref="command-workload")
    read_scope = command_scope.model_copy(update={"workload_ref": "read-workload"})
    transport = SimpleNamespace(purpose="human-assignment-read", scope=read_scope)
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    client = NativeAssignmentClient(
        transport=transport,
        source_engine=engine,
        command_scope=command_scope,
        engine_name="engine",
        database_incarnation="incarnation",
    )
    receipt = NativeAssignmentReceiptAuthority(
        transport=transport, source_engine=engine, command_scope=command_scope
    )
    assert client.scope == receipt.scope == command_scope
    assert client._transport.scope == receipt._transport.scope == read_scope
    for cls, extra in (
        (NativeAssignmentClient, dict(engine_name="engine", database_incarnation="incarnation")),
        (NativeAssignmentReceiptAuthority, {}),
    ):
        with pytest.raises(GatewayRefusalError):
            cls(transport=transport, source_engine=engine, command_scope=read_scope, **extra)


async def test_receipt_actor_mismatch_refuses_before_storage_or_native_io():
    from tests.unit.portal.test_assignment_composition import fixture

    from maezo.gateway.human.receipt import ReceiptIdentity

    _, bound, _ = await fixture()
    provider = object.__new__(NativeAssignmentReceiptAuthority)
    provider.scope = bound.scope
    identity = ReceiptIdentity(
        tenant=bound.scope.tenant,
        task_id="task-1",
        command_id="command",
        payload_digest="a" * 64,
        principal_ref="someone-else",
        workload_ref=bound.scope.workload_ref,
    )
    with pytest.raises(GatewayRefusalError):
        await provider.current_authority(bound.principal, identity)
