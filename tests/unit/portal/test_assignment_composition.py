"""Focal E03 actor binding, closed codec and explicitly absent legacy bundle controls."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tests.unit.gateway.human.test_gateway import SCOPE, SECRET, command, setup, snapshot

from maezo.gateway.human.assignment_transport import NativeAssignmentClient
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.gateway import HumanGateway
from maezo.gateway.human.models import (
    AuthorizedGovernedAssignment,
    GovernedAssignmentCommand,
    GovernedAssignmentContext,
    GovernedAssignmentReadContext,
)
from maezo.gateway.human.ports import BoundGovernedAssignmentPorts
from maezo.gateway.human.projection import (
    EvidenceReference,
    ProjectionError,
    project_governed_assignment,
    restore_command,
)
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.contracts.assignments import AssignmentContextResponse, GovernedBrowserAssignment


async def fixture():
    parts = await setup(snap=snapshot(allowed_actions=("claim", "reassign")))
    old, _, tasks, authorities, _ = parts
    principal = (await old._resolver.resolve(SECRET)).principal
    v1 = await old.read_assignment_context(session_secret=SECRET, task_id="task-1")
    raw = AssignmentContextResponse.from_context(v1).model_dump()
    raw.update(
        schema_version="portal-assignment-context.v2",
        allowed_operations=("claim", "reassign"),
        binding_ref="binding",
        binding_version="1",
        binding_digest="a" * 64,
        policy_ref="policy",
        policy_version="1",
        policy_digest="b" * 64,
        source_revision=str(2**53 + 1),
        generation_digest="c" * 64,
    )
    context = GovernedAssignmentContext.model_validate(raw)
    bound = GovernedAssignmentReadContext(principal=principal, scope=SCOPE, task=tasks.task, context=context)
    c = GovernedAssignmentCommand.model_validate(
        command(tasks.task.snapshot).model_dump()
        | dict(
            schema_version=2,
            expected_assignee_ref=None,
            expected_binding_ref=context.binding_ref,
            expected_binding_version=1,
            expected_binding_digest=context.binding_digest,
            expected_policy_ref=context.policy_ref,
            expected_policy_version=1,
            expected_policy_digest=context.policy_digest,
            expected_source_revision=2**53 + 1,
            expected_generation_digest=context.generation_digest,
            target_ref=None,
            expected_target_membership_revision=None,
        )
    )
    authority = authorities.authority.model_copy(update={"permitted_operations": ("claim",)})
    authorized = AuthorizedGovernedAssignment(
        scope=SCOPE,
        principal=principal,
        read_context=bound,
        authority=authority,
        command=c,
        valid_until=context.valid_until,
    )
    return parts, bound, authorized


async def test_private_projection_preserves_actual_evidence_reference_and_large_source_revision():
    _, bound, a = await fixture()
    evidence = EvidenceReference(
        tenant=SCOPE.tenant,
        task_id="task-1",
        evidence_ref="actual-row-reference",
        revision=3,
        digest="e" * 64,
        valid_until=bound.context.valid_until,
    )
    projected = project_governed_assignment(a, evidence)
    assert projected.evidence_ref == "actual-row-reference"
    assert projected.source_revision == str(2**53 + 1)
    assert restore_command(projected.canonical).canonical == projected.canonical
    assert "evidence_ref" not in wire(bound.context)


@pytest.mark.parametrize(
    "change",
    [
        {"tenant": "other"},
        {"task_id": "other"},
        {"revision": 4},
        {"digest": "0" * 64},
        {"valid_until": datetime.now(UTC)},
    ],
)
async def test_reference_binding_or_deadline_mismatch_refuses_projection(change):
    _, bound, a = await fixture()
    evidence = EvidenceReference(
        tenant=SCOPE.tenant,
        task_id="task-1",
        evidence_ref="actual-row-reference",
        revision=3,
        digest="e" * 64,
        valid_until=bound.context.valid_until,
    )
    with pytest.raises(ProjectionError):
        project_governed_assignment(a, evidence.model_copy(update=change))


async def test_full_actor_context_binding_is_checked_before_native_io_even_same_revision():
    _, bound, _ = await fixture()
    client = object.__new__(NativeAssignmentClient)
    client.scope = SCOPE
    changed = bound.principal.model_copy(update={"principal_ref": "different-principal"})
    with pytest.raises(GatewayRefusalError):
        await client.current_authority(changed, bound, "claim", None, None)


async def test_governed_only_bundle_does_not_require_fake_legacy_ports():
    parts, bound, _ = await fixture()
    old = parts[0]

    class Context:
        scope = SCOPE

        async def read_context(self, principal, task_id):
            assert principal == bound.principal and task_id == "task-1"
            return bound

    context = Context()
    gateway = HumanGateway(
        resolver=old._resolver,
        scope=SCOPE,
        ports=None,
        credentials=old._credentials,
        governed_assignment_ports=BoundGovernedAssignmentPorts(context, context, context, context),
    )
    result = await gateway.read_governed_assignment_context(session_secret=SECRET, task_id="task-1")
    assert result.schema_version == "portal-assignment-context.v2"
    for method in (gateway.read_assignment_context, gateway.read_decision_context):
        with pytest.raises(GatewayRefusalError) as error:
            await method(session_secret=SECRET, task_id="task-1")
        assert error.value.code == "production_capabilities_unavailable"


async def test_v2_public_numeric_expectations_stay_strings_but_version_is_strict_integer():
    _, _, a = await fixture()
    value = wire(a.command)
    value["schema_version"] = 2
    dto = GovernedBrowserAssignment.model_validate_json(__import__("json").dumps(value))
    assert dto.to_command() == a.command
    assert GovernedBrowserAssignment.from_command(a.command) == dto
    value["schema_version"] = "2"
    with pytest.raises(ValidationError):
        GovernedBrowserAssignment.model_validate_json(__import__("json").dumps(value))
    assert parse_model(GovernedAssignmentCommand, wire(a.command)) == a.command


@pytest.mark.parametrize("operation", ["context", "candidates", "authority"])
async def test_complete_private_query_result_and_full_actor_binding(operation):
    """Actual client codec with explicit synthetic wire/source collaborators, not engine integration."""
    import hashlib
    from types import SimpleNamespace

    from maezo.portal.engine.profile import canonicalize

    _, bound, authorized = await fixture()
    queries = []

    class SyntheticTransport:
        scope = SimpleNamespace(workload_ref="assignment-reader")

        async def query(self, request):
            queries.append(request)
            assert set(request) == {
                "schema",
                "operation",
                "tenant",
                "workload_ref",
                "principal_ref",
                "principal_issuer",
                "principal_subject",
                "task_id",
                "expected_context",
                "requested_operation",
                "target_ref",
                "target_membership_revision",
            }
            assert "membership_revision" not in request
            assert request["principal_ref"] == bound.principal.principal_ref
            assert request["expected_context"] == (wire(bound.context) if operation == "authority" else None)
            return dict(
                schema="human-assignment-query-result.v1",
                request_digest=hashlib.sha256(canonicalize(request)).hexdigest(),
                context=wire(bound.context),
                candidates=[] if operation == "candidates" else None,
                authority=wire(authorized.authority) if operation == "authority" else None,
                task=wire(bound.task),
                evidence=wire(
                    EvidenceReference(
                        tenant=SCOPE.tenant,
                        task_id="task-1",
                        evidence_ref="actual-row-reference",
                        revision=bound.task.snapshot.evidence_revision,
                        digest=bound.task.snapshot.evidence_digest,
                        valid_until=bound.context.valid_until,
                    )
                ),
                valid_until=wire(bound.context)["valid_until"],
            )

    class SyntheticSourceClient(NativeAssignmentClient):
        async def _active(self, expected=None):
            if expected is not None:
                assert expected == bound.context

    client = object.__new__(SyntheticSourceClient)
    client.scope = SCOPE
    client._transport = SyntheticTransport()
    result = await client._query(
        bound.principal,
        "task-1",
        operation,
        bound if operation != "context" else None,
        "claim" if operation == "authority" else None,
    )
    assert result[0] == bound
    assert (result[2] is not None) == (operation == "authority")
    assert len(queries) == 1
    changed = bound.principal.model_copy(
        update={"membership_revision": bound.principal.membership_revision + 1}
    )
    with pytest.raises(GatewayRefusalError):
        await client._query(
            changed,
            "task-1",
            operation,
            bound if operation != "context" else None,
            "claim" if operation == "authority" else None,
        )
    # Later operations reject a changed initiating principal before source/transport I/O.
    assert len(queries) == (2 if operation == "context" else 1)
