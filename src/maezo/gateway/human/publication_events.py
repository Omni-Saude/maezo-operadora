"""Structured events of the staff publication job (T1.5/H2) — closed names, closed fields, no PHI.

Why this exists (P1 of the ESCALATION portal follow-up, 16/10/2026): the job skipped, IN SILENCE,
every live task whose (process definition, task key) the Q2 admission does not name — after a
redeploy of SP-OP-ESCALATION-001 the new version's tasks never reached the portal and the queue
showed empty with `T15_RESULT ok=true`. Nobody could tell "no work" from "work the job refused".

Two events, each with a FIXED field list (`EVENT_FIELDS`):

* `human_task_skipped` — one per live task the job did not publish, with a closed `reason`
  (`SkipReason`), the task's engine ids and the admitted process definition ids for that task key
  (what an operator compares to tell "another version is live" from "nothing is admitted");
* `human_publication_refused` — the run refused: exception TYPE name and the closed reason of
  `read_credentials.UnavailableReason` (`unclassified` when the refusal did not say).

Every value must look like an engine/catalog identifier (`_IDENTIFIER`); anything else refuses to
render. Nothing here can carry a name, a phone, a note or a row: the fields are ids by construction
and the renderer refuses a field or a value outside that shape. `tools/staff_ops/job.py` re-renders
each line through `forwardable` before printing it to CloudWatch, so the same fence holds there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Final, Literal, get_args

from .read_credentials import UNAVAILABLE_REASONS

SkipReason = Literal[
    "definition_not_admitted",
    "task_id_format_not_admitted",
    "task_changed_during_publication",
]
SKIP_REASONS: Final[frozenset[str]] = frozenset(get_args(SkipReason))

EVENT_TASK_SKIPPED: Final[str] = "human_task_skipped"
EVENT_PUBLICATION_REFUSED: Final[str] = "human_publication_refused"

#: The ONLY fields each event may carry (the PHI fence is this list plus `_IDENTIFIER`).
EVENT_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    EVENT_TASK_SKIPPED: frozenset(
        {
            "event",
            "reason",
            "task_id",
            "task_definition_key",
            "process_definition_id",
            "admitted_process_definition_ids",
        }
    ),
    EVENT_PUBLICATION_REFUSED: frozenset({"event", "error_type", "reason"}),
}

#: Engine/catalog identifier shape: `SP-OP-ESCALATION-001:3:<uuid>`, `UT_TratarEscalonamento`, a
#: UUID, a decimal id, an exception class name. No space, no `@`, no `+`, bounded.
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,255}")
#: How many events `tools/staff_ops/job.py` forwards per run (MAX_TASKS is 1000; the count is in
#: `JobResult.tasks_skipped` regardless).
MAX_FORWARDED_EVENTS: Final[int] = 200


class EventRefusedError(ValueError):
    """An event with a field or a value outside the closed shape. Never rendered."""


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise EventRefusedError("value is not an identifier")
    return value


def render(event: Mapping[str, object]) -> str:
    """One JSON line (sorted keys), or `EventRefusedError`. The only way an event is printed."""
    name = event.get("event")
    allowed = EVENT_FIELDS.get(name) if isinstance(name, str) else None
    if allowed is None or set(event) != allowed:
        raise EventRefusedError("event name or field list outside the closed set")
    reason = event["reason"]
    if name == EVENT_TASK_SKIPPED and reason not in SKIP_REASONS:
        raise EventRefusedError("skip reason outside the closed set")
    if name == EVENT_PUBLICATION_REFUSED and reason not in UNAVAILABLE_REASONS:
        raise EventRefusedError("refusal reason outside the closed set")
    clean: dict[str, object] = {}
    for key, value in event.items():
        if key == "admitted_process_definition_ids":
            if not isinstance(value, list | tuple):
                raise EventRefusedError("admitted ids must be a list")
            clean[key] = sorted(_identifier(v) for v in value)
        else:
            clean[key] = _identifier(value)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def task_skipped(
    *,
    reason: SkipReason,
    task_id: str,
    task_definition_key: str,
    process_definition_id: str,
    admitted_process_definition_ids: Iterable[str],
) -> dict[str, object]:
    return {
        "event": EVENT_TASK_SKIPPED,
        "reason": reason,
        "task_id": task_id,
        "task_definition_key": task_definition_key,
        "process_definition_id": process_definition_id,
        "admitted_process_definition_ids": sorted(admitted_process_definition_ids),
    }


def refusal_reason(exc: BaseException) -> str:
    """The closed reason of a refusal (`unclassified` when it did not say, or said something else)."""
    reason = getattr(exc, "reason", None)
    return reason if isinstance(reason, str) and reason in UNAVAILABLE_REASONS else "unclassified"


def publication_refused(exc: BaseException) -> dict[str, object]:
    return {
        "event": EVENT_PUBLICATION_REFUSED,
        "error_type": type(exc).__name__,
        "reason": refusal_reason(exc),
    }


def forwardable(line: str) -> str | None:
    """`tools/staff_ops/job.py`: the line re-rendered if it is one of OUR events, else `None`."""
    try:
        value = json.loads(line)
        if not isinstance(value, dict):
            return None
        return render(value)
    except (ValueError, TypeError):
        return None
