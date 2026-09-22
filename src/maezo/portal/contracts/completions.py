"""INTERIM ESCALATION completion contract (DL-0049): closed outcome, bounded pseudonymized note.

The wire shape of `POST /api/v1/portal/tasks/{task_id}/completion`. It is deliberately NOT a
new clinical or process rule: the outcome set and the "must not be blank" rule are taken from
`models.py::EscalationDecisionInputs`, the canonical form contract this repo already derives
from `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
("Saidas obrigatorias ao completar: resultado in {resolvido_humano, devolvido_agente,
emergencia_acionada}; notas_resolucao (texto livre, pseudonimizado)").

`resultado` has NO default. A body that omits it is refused with 422, never completed with a
guessed outcome.
"""

from typing import Annotated, Literal, Self, get_args

from pydantic import Field, StringConstraints, model_validator

from .models import EscalationDecisionInputs, FormKey, OpaqueRef, Sha256Digest
from .queues import ClosedRead, DecimalRevision, DecimalVersion, UTCDateTime

CompletionOutcome = Literal["resolvido_humano", "devolvido_agente", "emergencia_acionada"]

#: Read from the canonical form contract, not restated: if `EscalationDecisionInputs.resultado`
#: ever changes, this tuple changes with it and the assertion below fails at IMPORT time rather
#: than letting two enums drift in silence.
COMPLETION_OUTCOMES: tuple[str, ...] = get_args(EscalationDecisionInputs.model_fields["resultado"].annotation)

#: Bounded transport allocation for one free-text note, in the spirit of `decisions.py`'s
#: `_MAX_BODY`: a wire limit, NOT a clinical or regulatory bound. The note that actually
#: reaches the engine is additionally capped by `phi_vars.redact_free_text` (500 characters),
#: which is the pseudonymization contract, not this number.
MAX_NOTES = 2000

#: The completion taxonomy. `completion_unavailable` is the DECLARED interim gate being off —
#: it is not "not found" and not "forbidden", because pretending either would hide a
#: deployment decision behind an authorization answer.
CompletionErrorCode = Literal[
    "invalid_request",
    "invalid_completion",
    "session_unavailable",
    "employee_access_required",
    "resource_unavailable",
    "revision_conflict",
    "completion_unavailable",
    "completion_dependency_unavailable",
]


class TaskCompletionSubmission(ClosedRead):
    resultado: CompletionOutcome
    notas_resolucao: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_NOTES)]

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        """Re-validate through the canonical form contract; one rule, in one place."""
        EscalationDecisionInputs.model_validate(
            {
                "kind": "escalation",
                "resultado": self.resultado,
                "notas_resolucao": self.notas_resolucao,
            }
        )
        return self


class TaskCompletionResponse(ClosedRead):
    """The final state of the task, plus the trail refs that let the two paths be compared.

    No note, no narrative and no beneficiary reference travel back: the browser already has the
    text it typed, and the pseudonymized note belongs to the engine's PHI zone. The two audit
    refs are chain-link hashes in the tenant's existing audit chain — the same chain
    `human_command.intent`/`.result` writes to — so "conclusão pelo portal e pelo motor
    produzem rastro idêntico" is something a reader can actually check.
    """

    schema_: Literal["portal-task-completion.v1"] = Field(default="portal-task-completion.v1", alias="schema")
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: DecimalVersion
    task_definition_key: OpaqueRef
    form_key: FormKey
    state: Literal["completed"] = "completed"
    resultado: CompletionOutcome
    consumed_task_revision: DecimalRevision
    authority_revision: DecimalRevision
    evidence_revision: DecimalRevision
    evidence_digest: Sha256Digest
    completed_at: UTCDateTime
    audit_intent_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_result_ref: str = Field(pattern=r"^[0-9a-f]{64}$")


class PortalCompletionError(ClosedRead):
    schema_: Literal["portal-completion-error.v1"] = Field(
        default="portal-completion-error.v1", alias="schema"
    )
    code: CompletionErrorCode


if get_args(CompletionOutcome) != COMPLETION_OUTCOMES:  # pragma: no cover - import-time fence
    raise RuntimeError(
        "completion outcome set drifted from EscalationDecisionInputs: "
        f"{get_args(CompletionOutcome)} != {COMPLETION_OUTCOMES}"
    )
