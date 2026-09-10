from datetime import timedelta

import pytest
from tests.unit.gateway.intake.native.test_wire_transport import HASH, NOW, command, source

from maezo.gateway.human.auth_profile import (
    AuditIntentPayload,
    DocumentContextResult,
    DocumentOccurrence,
    DocumentPolicy,
    OccurrenceBinding,
    Pin,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import ArtifactPin, digest
from maezo.gateway.intake.native_binding import bind_document_response


def inputs():
    c = command()
    binding = OccurrenceBinding(
        request_ref="request",
        generation=2,
        request_revision=3,
        binding_revision=1,
        process_instance_id="instance",
        definition=c.definition,
        scope_execution_id="scope-execution",
        subscription_id="subscription",
        subscription_revision=1,
        subscription_execution_id="subscription-execution",
        execution_revision=1,
        timer_job_id="timer",
        timer_deadline=NOW + timedelta(seconds=60),
    )
    occurrence = DocumentOccurrence(
        request_ref="request",
        scope=c.scope,
        case_ref="case",
        process_instance_id="instance",
        definition=c.definition,
        generation=2,
        request_revision=3,
        binding_revision=1,
        creator_execution_id="creator",
        producer_external_task_id="producer",
        publication_external_task_id="publisher",
        state="bound",
        created_at=NOW,
        policy_ref="policy",
        policy_digest=HASH,
        binding=binding,
        successor_ref=None,
        terminal_command_id=None,
    )
    context = DocumentContextResult(
        schema="human-auth-document-context.v1",
        scope=c.scope,
        query_id="query",
        query_digest=HASH,
        actor=c.actor,
        occurrence=occurrence,
        input_pins=(),
        valid_until=NOW + timedelta(seconds=45),
    )
    admitted = AuditIntentPayload(
        intent_ref=c.admission.intent_ref,
        intake_or_response_ref="response",
        command_id=c.command_id,
        actor=c.actor,
        admitted_digest=c.admission.admitted_digest,
        operation="auth.documents.respond",
        state="committed",
        admitted_at=NOW,
    )
    policy = DocumentPolicy(
        assessment_ref="assessment",
        resource_kind="case",
        resource_ref="case",
        request_ref="request",
        request_revision=3,
        policy=ArtifactPin(artifact_ref="policy", digest=HASH),
        policy_revision=1,
        recipient_principal_refs=(c.actor.principal_ref,),
        required_codes=(),
        missing_codes=(),
        submitted_response_digest=c.admission.admitted_digest,
        effective_document_refs=(),
        document_set_digest=digest(()),
        complete=True,
        source=source(),
        valid_until=NOW + timedelta(seconds=30),
    )
    pins = tuple(
        Pin(kind=k, resource_ref=r, head_generation=1, source=source(), payload_digest=d)
        for k, r, d in [
            ("actor", c.actor.principal_ref, HASH),
            ("document_policy", "assessment", digest(policy)),
            ("resource_authority", "authority", HASH),
        ]
    )
    return dict(
        workload_ref=c.workload_ref,
        admitted=admitted,
        admission=c.admission,
        context=context,
        policy=policy,
        pins=pins,
        now=NOW,
    )


def test_exact_bound_response_preserves_generation_and_assessed_complete_set():
    values = inputs()
    result = bind_document_response(**values)
    assert result.occurrence.generation == 2 and result.occurrence.request_revision == 3
    assert result.document_refs == values["policy"].effective_document_refs
    assert result.admission.admitted_digest == values["policy"].submitted_response_digest


@pytest.mark.parametrize(
    "state", ["created", "awaiting_publication_worker", "consumed", "expired", "cancelled", "replaced"]
)
def test_no_effect_command_before_wait_or_after_terminal_occurrence(state):
    values = inputs()
    context = values["context"]
    values["context"] = context.model_copy(
        update={"occurrence": context.occurrence.model_copy(update={"state": state})}
    )
    with pytest.raises(AuthUnavailableError):
        bind_document_response(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("submitted_response_digest", None),
        ("submitted_response_digest", "b" * 64),
        ("request_revision", 4),
        ("request_ref", "new-request"),
        ("recipient_principal_refs", ("other",)),
    ],
)
def test_wrong_assessment_or_pre_response_policy_cannot_correlate(field, value):
    values = inputs()
    values["policy"] = values["policy"].model_copy(update={field: value})
    with pytest.raises(AuthUnavailableError):
        bind_document_response(**values)


def test_request_only_policy_is_valid_shape_but_not_effect_authority():
    values = inputs()
    policy = DocumentPolicy.model_validate(
        values["policy"].model_dump() | {"submitted_response_digest": None}
    )
    values["policy"] = policy
    with pytest.raises(AuthUnavailableError):
        bind_document_response(**values)


def test_final_context_or_policy_deadline_blocks_binding():
    values = inputs()
    values["now"] = NOW + timedelta(seconds=30)
    with pytest.raises(AuthUnavailableError):
        bind_document_response(**values)
