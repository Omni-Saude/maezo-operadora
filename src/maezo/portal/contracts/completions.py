"""INTERIM ESCALATION completion contract (DL-0049): closed outcome, bounded pseudonymized note.

The wire shape of `POST /api/v1/portal/tasks/{task_id}/completion`. It is deliberately NOT a
new clinical or process rule: the outcome set and the "must not be blank" rule are taken from
`models.py::EscalationDecisionInputs`, the canonical form contract this repo already derives
from `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
("Saidas obrigatorias ao completar: resultado in {resolvido_humano, devolvido_agente,
emergencia_acionada}; notas_resolucao (texto livre, pseudonimizado)").

`resultado` has NO default. A body that omits it is refused with 422, never completed with a
guessed outcome.

DL-0111 (SP-OP-CANCEL-001 v0.2.0) adds ONE more closed body on the same interim route:
`CancelExecucaoCompletionSubmission` (`execucao_cadastro` in EXECUTADO/IMPEDIDO, no free text), for the
registry execution task (`cancel_execucao`). The two bodies are disjoint (`extra=forbid`): an
escalation body can never be read as the cancel one, and the gateway refuses a body whose form is
not the task's own form.
"""

from typing import Annotated, Literal, Self, get_args

from pydantic import Field, StringConstraints, model_validator

from .models import CancelExecucaoInputs, EscalationDecisionInputs, FormKey, OpaqueRef, Sha256Digest
from .queues import ClosedRead, DecimalRevision, DecimalVersion, UTCDateTime

CompletionOutcome = Literal["resolvido_humano", "devolvido_agente", "emergencia_acionada"]

#: Read from the canonical form contract, not restated: if `EscalationDecisionInputs.resultado`
#: ever changes, this tuple changes with it and the assertion below fails at IMPORT time rather
#: than letting two enums drift in silence.
COMPLETION_OUTCOMES: tuple[str, ...] = get_args(EscalationDecisionInputs.model_fields["resultado"].annotation)

#: DL-0111: the closed outcome of the CANCEL registry execution task, read from its form contract.
CancelExecucaoOutcome = Literal["EXECUTADO", "IMPEDIDO"]
CANCEL_EXECUCAO_OUTCOMES: tuple[str, ...] = get_args(
    CancelExecucaoInputs.model_fields["execucao_cadastro"].annotation
)

#: THE limit of one free-text note, in characters — a transport/product bound, NOT a clinical or
#: regulatory one. ONE number, three places (P3/P4, 16/10/2026): the wire schema (`maxLength` of
#: `notas_resolucao`, what the browser counts against), and the note AFTER the scrub
#: (`gateway/human/completion.py::pseudonymize_notes` refuses a scrubbed note longer than this with
#: `notes_too_long`, because the `[REDACTED_*]` tokens make the text longer). Nothing cuts the
#: note: before 16/10/2026 `redact_free_text` cut it at 500 characters plus `...[TRUNCATED]`.
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
    # P3 (16/10/2026): the note fits the wire limit but NOT after the scrub (the `[REDACTED_*]`
    # tokens are longer than what they replace). Refused before the audit intent and the engine.
    "notes_too_long",
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


class CancelExecucaoCompletionSubmission(ClosedRead):
    """DL-0111: "cancelamento executado no sistema" (or impeded). One closed field, no note."""

    execucao_cadastro: CancelExecucaoOutcome

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        CancelExecucaoInputs.model_validate(
            {"kind": "cancel_execucao", "execucao_cadastro": self.execucao_cadastro}
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
    #: The closed outcome of the completed form: an ESCALATION `resultado` or, since DL-0111, the
    #: CANCEL `execucao_cadastro` (EXECUTADO/IMPEDIDO). `form_key` says which.
    resultado: CompletionOutcome | CancelExecucaoOutcome
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


if get_args(CancelExecucaoOutcome) != CANCEL_EXECUCAO_OUTCOMES:  # pragma: no cover - import-time fence
    raise RuntimeError(
        "cancel execution outcome set drifted from CancelExecucaoInputs: "
        f"{get_args(CancelExecucaoOutcome)} != {CANCEL_EXECUCAO_OUTCOMES}"
    )

if get_args(CompletionOutcome) != COMPLETION_OUTCOMES:  # pragma: no cover - import-time fence
    raise RuntimeError(
        "completion outcome set drifted from EscalationDecisionInputs: "
        f"{get_args(CompletionOutcome)} != {COMPLETION_OUTCOMES}"
    )
