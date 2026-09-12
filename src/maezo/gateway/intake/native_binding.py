"""Bind one document native command after a qualified exact occurrence observation.

Trace: E04 native contract 3.2/4.3. Native result and source publication verification
precede this typed construction. A binding already persisted by native_store.prepare
cannot be replaced, including after an uncertain send.
"""

from datetime import datetime

from maezo.gateway.human.auth_profile import (
    AuditIntent,
    AuditIntentPayload,
    DocumentContextResult,
    DocumentPolicy,
    HumanDocumentCommand,
    Pin,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest


def bind_document_response(
    *,
    workload_ref: str,
    admitted: AuditIntentPayload,
    admission: AuditIntent,
    context: DocumentContextResult,
    policy: DocumentPolicy,
    pins: tuple[Pin, ...],
    now: datetime,
) -> HumanDocumentCommand:
    occurrence = context.occurrence
    if occurrence.state in ("created", "awaiting_publication_worker"):
        # Caller retains durable admission; no command is created until bound.
        raise AuthUnavailableError()
    binding = occurrence.binding
    if (
        occurrence.state != "bound"
        or binding is None
        or now >= min(context.valid_until, policy.valid_until, binding.timer_deadline)
        or admitted.operation != "auth.documents.respond"
        or admitted.actor != context.actor
        or (admitted.intent_ref, admitted.command_id, admitted.admitted_digest)
        != (admission.intent_ref, admission.admitted_command_id, admission.admitted_digest)
        or policy.resource_kind != "case"
        or (
            policy.resource_ref,
            policy.request_ref,
            policy.request_revision,
            policy.submitted_response_digest,
        )
        != (
            occurrence.case_ref,
            occurrence.request_ref,
            occurrence.request_revision,
            admitted.admitted_digest,
        )
        or context.actor.principal_ref not in policy.recipient_principal_refs
    ):
        raise AuthUnavailableError()
    return HumanDocumentCommand(
        schema="human-auth-documents.v1",
        scope=context.scope,
        workload_ref=workload_ref,
        actor=admitted.actor,
        case_ref=occurrence.case_ref,
        command_id=admitted.command_id,
        admission=admission,
        occurrence=binding,
        input_pins=pins,
        document_refs=policy.effective_document_refs,
        document_set_digest=policy.document_set_digest,
        assessment_ref=policy.assessment_ref,
        assessment_digest=digest(policy),
        documentacao_completa=policy.complete,
    )
