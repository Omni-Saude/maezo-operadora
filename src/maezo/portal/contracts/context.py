"""INTERIM escalation-context read contract (DL-0050): what the attendant needs to open a case.

The wire shape of `GET /api/v1/portal/tasks/{task_id}/context`. Until now the attendant who opened
a `SP-OP-ESCALATION-001` task saw only technical metadata (`PublicTaskSnapshot`): no reason, no
severity, no priority, no deadline and no idea how long the case had been waiting. This contract
carries exactly those facts, and nothing that identifies a person.

WHAT IS AND IS NOT HERE
-----------------------
Every value is either a closed vocabulary token the process already computed (`motivo_categoria`,
`severidade`, and the `escalation_routing` DMN outputs `prioridade`/`grupo_atendimento`), a
timestamp, or the agent's own hand-off summary. There is no telephone, no name, no conversation
and no beneficiary reference: none of them exists in the process, by design (ADR-0006/ADR-0061).

The summary (`resumo_contexto`) is the one free-text field. The repository classifies it as PHI
(`tools/workers/phi_vars.py::PHI_PROCESS_VARS`), so this contract bounds it and the server
scrubs it again on the way out; it is rendered only, never stored by the browser.

Codes (`motivo_categoria`, `severidade`, `prioridade`, `grupo_atendimento`) are opaque tokens whose
LABELS live in the web client, exactly like `staff_cases.py::StaffEscalation.reason_code`: a new
DMN value therefore reaches the screen as its own code until someone writes its label, and never
invalidates the response.
"""

from typing import Annotated, Literal, get_args

from pydantic import Field, StringConstraints

from .models import OpaqueRef
from .queues import ClosedRead, ReadErrorCode, UTCDateTime

#: Bounded transport allocation for the summary, not a clinical bound. The server-side scrub
#: (`phi_vars.redact_free_text`) already caps what the agent produced at 500 characters.
MAX_SUMMARY = 1000

_Code = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")]
_Group = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
_Priority = Annotated[str, StringConstraints(strict=True, pattern=r"^P[0-9]{1,2}$")]
_Summary = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_SUMMARY)]

#: Which human line the task belongs to. `atendimento` is `UT_TratarEscalonamento` (the routed
#: group, with the two SLA clocks); `supervisao` is `UT_SupervisorAssume` (the last line, entered
#: only after the resolution clock broke, and which carries no clock of its own).
ContextStage = Literal["atendimento", "supervisao"]

#: The read taxonomy plus ONE declared deployment state. `context_unavailable` is the interim
#: gate being off (DL-0050 shares DL-0049's gate) — not "not found" and not "forbidden".
ContextErrorCode = Literal[
    "invalid_request",
    "session_unavailable",
    "employee_access_required",
    "resource_unavailable",
    "refresh_required",
    "read_dependency_unavailable",
    "context_unavailable",
]


class TaskContextResponse(ClosedRead):
    schema_: Literal["portal-task-context.v1"] = Field(default="portal-task-context.v1", alias="schema")
    task_id: OpaqueRef
    etapa: ContextStage
    motivo_categoria: _Code | None
    severidade: _Code | None
    prioridade: _Priority | None
    grupo_atendimento: _Group | None
    #: When the escalation process started. Absent when the engine did not answer with a time.
    aberto_em: UTCDateTime | None
    #: Deadlines of the two SLA clocks, present only in the `atendimento` stage and only when the
    #: routing decision named a valid ISO-8601 duration. Never invented from a default.
    ack_vence_em: UTCDateTime | None
    resolucao_vence_em: UTCDateTime | None
    #: The agent's hand-off summary, already scrubbed. `None` when the agent produced none.
    resumo_contexto: _Summary | None
    observed_at: UTCDateTime


class PortalContextError(ClosedRead):
    schema_: Literal["portal-context-error.v1"] = Field(default="portal-context-error.v1", alias="schema")
    code: ContextErrorCode


_EXPECTED = set(get_args(ReadErrorCode)) | {"context_unavailable"}
if set(get_args(ContextErrorCode)) != _EXPECTED:  # pragma: no cover - import-time fence
    raise AssertionError("ContextErrorCode drifted from ReadErrorCode")
