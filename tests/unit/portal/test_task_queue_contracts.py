"""Closed public read boundary, synthetic unit values only."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.unit.gateway.human.test_gateway import snapshot

from maezo.portal.contracts.queues import (
    DecimalRevision,
    DecimalVersion,
    PortalReadError,
    PublicTaskSnapshot,
    QueueFreshness,
    TaskQueueItem,
    TaskQueuePage,
    TaskQueueRequest,
)


@pytest.mark.parametrize("value", [True, 1, -1, "-1", "01", "1.0", "", " 1"])
def test_revision_refuses_coercion(value):
    with pytest.raises(ValidationError):
        TypeAdapter(DecimalRevision).validate_python(value)


def test_decimal_precision_and_positive_version():
    large = str(2**70 + 1)
    assert TypeAdapter(DecimalRevision).validate_python(large) == large
    with pytest.raises(ValidationError):
        TypeAdapter(DecimalVersion).validate_python("0")


@pytest.mark.parametrize("extra", [{"tenant": "private"}, {"limit": True}, {"limit": "25"}, {"cursor": ""}])
def test_closed_request(extra):
    with pytest.raises(ValidationError):
        TaskQueueRequest(queue="team", **extra)


@pytest.mark.parametrize(
    "field", ["task_revision", "evidence_revision", "process_definition_version", "form_version"]
)
def test_snapshot_decimal_projection_lossless_and_immutable(field):
    internal = snapshot(**{field: 2**70 + 3})
    projected = PublicTaskSnapshot.from_snapshot(internal)
    assert getattr(projected, field) == str(2**70 + 3)
    assert projected.allowed_actions == ()
    assert internal.allowed_actions == ("claim", "release", "decision")
    assert getattr(internal, field) == 2**70 + 3
    with pytest.raises(ValidationError):
        projected.task_id = "replacement"
    assert PublicTaskSnapshot.model_validate_json(projected.model_dump_json()) == projected


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowed_actions", ("decision",)),
        ("task_revision", 2),
        ("task_revision", "-1"),
        ("form_version", "0"),
        ("allowed_inputs", ("unreviewed",)),
        ("business_key", "private"),
        ("task_definition_key", "unknown"),
        ("form_key", "escalation"),
    ],
)
def test_public_detail_refuses_unapproved_field_action_and_binding(field, value):
    data = PublicTaskSnapshot.from_snapshot(snapshot()).model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        PublicTaskSnapshot.model_validate(data)


@pytest.mark.parametrize("value", ["task\n", "a b", "a/b", "a?b", "a#b", "a\x00b", "a" * 513, 123])
def test_queue_ref_is_strict(value):
    with pytest.raises(ValidationError):
        TaskQueueItem(
            task_id=value,
            process_definition_key="SP-OP-AUTH-001",
            task_definition_key="UT_AnaliseMedicoAuditor",
            task_revision="0",
            ownership="unassigned",
            engine_due_at=None,
            snapshot_at=datetime.now(UTC),
        )


def test_public_models_are_closed_and_deeply_immutable():
    now = datetime.now(UTC)
    item = TaskQueueItem(
        task_id="task-1",
        process_definition_key="SP-OP-AUTH-001",
        task_definition_key="UT_AnaliseMedicoAuditor",
        task_revision="0",
        ownership="unassigned",
        engine_due_at=None,
        snapshot_at=now,
    )
    fresh = QueueFreshness(observed_at=now, source_observed_at=now, valid_until=now + timedelta(seconds=1))
    page = TaskQueuePage(queue="team", items=(item,), next_cursor=None, freshness=fresh)
    with pytest.raises(ValidationError):
        page.items[0].ownership = "self"
    with pytest.raises(ValidationError):
        TaskQueuePage.model_validate({**page.model_dump(by_alias=True), "total": 42})
    with pytest.raises(ValidationError):
        PortalReadError(code="invalid_request", message="private")
    with pytest.raises(ValidationError):
        QueueFreshness(observed_at=now, source_observed_at=now, valid_until=now)
