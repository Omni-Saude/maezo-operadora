"""D6 unit tests. No engine or PostgreSQL execution is represented here."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import command, snapshot
from tests.unit.portal.test_human_session import membership

from maezo.gateway.human.models import AuthorizedAssignment, Scope
from maezo.gateway.human.projection import (
    EvidenceReference,
    ProjectionError,
    decimal_revision,
    project_assignment,
    verify_engine_receipt,
)
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize, strict_loads


def assignment(**changes):
    scope = Scope(tenant="tenant_d6", environment="test", workload_ref="human-gateway")
    m = membership()
    p = HumanPrincipal(
        schema_version=1,
        principal_ref=m.principal_ref,
        issuer=m.issuer,
        subject=m.subject,
        tenant=scope.tenant,
        membership_revision=m.revision,
        memberships=m.memberships,
        session_ref="session-1",
        authenticated_at=datetime.now(UTC),
        subject_bindings=m.subject_bindings,
    )
    values = dict(
        scope=scope,
        workload_ref=scope.workload_ref,
        principal=p,
        snapshot=snapshot(),
        authority_revision=7,
        command=command(),
    )
    values.update(changes)
    return AuthorizedAssignment.model_validate(values)


def evidence(value=None, **changes):
    value = value or assignment()
    values = dict(
        tenant=value.scope.tenant,
        task_id=value.snapshot.task_id,
        evidence_ref="classified-evidence-1",
        revision=value.snapshot.evidence_revision,
        digest=value.snapshot.evidence_digest,
        valid_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    values.update(changes)
    return EvidenceReference(**values)


def wire():
    value = assignment()
    return project_assignment(value, evidence(value))


def receipt_payload(c=None, **changes):
    c = c or wire()
    value = dict(
        schema="human-engine-receipt.v1",
        status="committed",
        tenant=c.tenant,
        task_id=c.task_id,
        command_id=c.command_id,
        operation=c.operation,
        payload_digest=c.digest,
        principal_ref=c.principal_ref,
        workload_ref=c.workload_ref,
        audit_intent_ref=c.audit_intent_ref,
        consumed_task_revision=c.task_revision,
        resulting_task_revision="3",
        engine_receipt_ref="receipt-1",
        recorded_at="1788890000",
    )
    value.update(changes)
    return canonicalize(value)


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None])
def test_revision_rejects_coercion(value):
    with pytest.raises(ProjectionError):
        decimal_revision(value)


def test_revision_arbitrary_precision_without_global_interpreter_change():
    import sys

    limit = sys.get_int_max_str_digits()
    huge = 10**5000 + 123
    assert decimal_revision(huge) == "1" + "0" * 4997 + "123"
    assert sys.get_int_max_str_digits() == limit


def test_projection_is_minimized_canonical_and_stable():
    c = wire()
    value = strict_loads(c.canonical)
    assert c == wire()
    assert c.operation == "claim" and c.outcome is None
    assert c.evidence_ref == "classified-evidence-1"
    assert value["task_revision"] == "2"
    assert not {"memberships", "session_ref", "allowed_inputs", "variables"} & value.keys()


def test_complete_projection_preserves_six_5001_digit_revisions_within_wire_cap():
    huge = 10**5000 + 123
    s = snapshot(
        task_revision=huge, evidence_revision=huge, process_definition_version=huge, form_version=huge
    )
    a = assignment()
    c = command(s, expected_membership_revision=huge, expected_authority_revision=huge)
    a = assignment(
        snapshot=s,
        command=c,
        authority_revision=huge,
        principal=a.principal.model_copy(update={"membership_revision": huge}),
    )
    projected = project_assignment(a, evidence(a))
    for name in (
        "task_revision",
        "evidence_revision",
        "membership_revision",
        "authority_revision",
        "process_definition_version",
        "form_version",
    ):
        assert getattr(projected, name) == "1" + "0" * 4997 + "123"
    assert len(projected.canonical) < 65536


def test_release_is_typed_and_cannot_release_another_principal_assignment():
    a = assignment()
    s = snapshot(assignee_ref=a.principal.principal_ref)
    a = assignment(snapshot=s, command=command(s, operation="release"))
    assert project_assignment(a, evidence(a)).operation == "release"
    other = s.model_copy(update={"assignee_ref": "other-human"})
    with pytest.raises(ProjectionError):
        project_assignment(a.model_copy(update={"snapshot": other}), evidence(a))


@pytest.mark.parametrize(
    "changes",
    [
        dict(tenant="foreign"),
        dict(task_id="foreign"),
        dict(revision=4),
        dict(digest="f" * 64),
        dict(valid_until=datetime(2000, 1, 1, tzinfo=UTC)),
    ],
)
def test_projection_requires_exact_current_evidence(changes):
    a = assignment()
    with pytest.raises(ProjectionError):
        project_assignment(a, evidence(a, **changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "foreign"),
        ("task_id", "foreign"),
        ("command_id", "foreign"),
        ("payload_digest", "f" * 64),
        ("principal_ref", "foreign"),
        ("workload_ref", "foreign"),
        ("audit_intent_ref", "foreign"),
        ("consumed_task_revision", "4"),
        ("resulting_task_revision", "02"),
        ("resulting_task_revision", None),
        ("operation", "decision"),
        ("status", "pending"),
        ("recorded_at", "99999999999999999999"),
    ],
)
def test_receipt_requires_exact_identity_and_real_wire_schema(field, value):
    with pytest.raises(ProjectionError):
        verify_engine_receipt(receipt_payload(**{field: value}), wire())


def test_receipt_no_invented_engine_commit_id_and_huge_revision():
    c = wire()
    result = verify_engine_receipt(receipt_payload(resulting_task_revision="9" * 5000), c)
    assert result.resulting_task_revision == "9" * 5000
    assert result.engine_receipt_ref == "receipt-1"
    assert "engine_commit_ref" not in result.model_dump()


def test_receipt_rejects_unknown_fields_numeric_tokens_duplicates():
    for raw in [
        receipt_payload(extra="PRIVATE"),
        receipt_payload().replace(b'"3"', b"3"),
        receipt_payload().replace(b"{", b'{"status":"committed",', 1),
    ]:
        with pytest.raises(ProjectionError):
            verify_engine_receipt(raw, wire())
