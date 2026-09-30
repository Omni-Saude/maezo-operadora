"""INTERIM escalation-context read — closed types, the port and the pure parsers (DL-0050).

WHY THIS EXISTS. The attendant who opens a `SP-OP-ESCALATION-001` task in the portal saw only
technical metadata: no reason, no severity, no priority, no deadline, no age. The signed read
model that feeds the task detail carries one typed piece of read-only evidence (payment
admissibility) and the engine plugin refuses evidence on any other form; extending it means a
Java plugin change, new column grants on the engine tables and regenerated signed vectors.

DL-0050 takes the same shortcut DL-0049 already took for completion, for the same reason and
behind the same gate: read the handful of facts straight over the engine's REST surface, from a
fixed server-owned client. It is fenced to `dev-sa-east-1` by
`scripts/ci/check_portal_direct_completion.py` and it is not the destination — the destination
is the durable, signed read model (issue #427's family of work). Read DL-0049 and DL-0050 before
extending anything here.

Nothing in this module is a capability: no key, no connection and no HTTP client. It holds the
closed value type, the abstract port the gateway calls and the pure functions that turn engine
strings into safe values. The concrete adapter is `escalation_context_engine.py`.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from maezo.portal.contracts.context import MAX_SUMMARY
from maezo.tools.workers.phi_vars import redact_free_text

from .models import Scope

__all__ = [
    "DECISION_REF",
    "ESCALATION_PROCESS_KEY",
    "ROUTING_DECISION_KEY",
    "SUMMARY_VARIABLE",
    "TASK_STAGES",
    "EscalationContext",
    "EscalationContextSource",
    "Stage",
    "clean_group",
    "clean_priority",
    "clean_summary",
    "clean_token",
    "parse_engine_datetime",
    "parse_iso_duration",
]

#: The decision-log row that authorises this module's existence, quoted where a reader would
#: otherwise have to guess why the BFF reads the engine directly.
DECISION_REF: Final = "DL-0050"

#: The only process this reader answers for. A task whose process instance is anything else is
#: refused: this is the escalation attendant's context, not "whatever the engine will return".
ESCALATION_PROCESS_KEY: Final = "SP-OP-ESCALATION-001"

#: `<decision id="escalation_routing">` in `spec/processes/dmn/escalation_routing.dmn`. Its four
#: output `name`s are the variables read back below.
ROUTING_DECISION_KEY: Final = "escalation_routing"

#: The process-input variable holding the agent's hand-off summary
#: (`docs/processes/contracts/SP-OP-ESCALATION-001.md`, `resumo_contexto`).
SUMMARY_VARIABLE: Final = "resumo_contexto"

Stage = Literal["atendimento", "supervisao"]

#: The two human lines of the process. The mapping is closed on purpose: a task definition key
#: that is not one of these is not an escalation attendant task and is refused.
TASK_STAGES: Final[dict[str, Stage]] = {
    "UT_TratarEscalonamento": "atendimento",
    "UT_SupervisorAssume": "supervisao",
}


@dataclass(frozen=True, slots=True, repr=False)
class EscalationContext:
    """What the attendant is shown. `repr=False`: the summary never lands in a log line."""

    etapa: Stage
    motivo_categoria: str | None
    severidade: str | None
    prioridade: str | None
    grupo_atendimento: str | None
    aberto_em: datetime | None
    ack_vence_em: datetime | None
    resolucao_vence_em: datetime | None
    resumo_contexto: str | None = field(repr=False)

    def __repr__(self) -> str:  # pragma: no cover - deliberately reveals nothing
        return f"EscalationContext(etapa={self.etapa!r})"


class EscalationContextSource(ABC):
    """The port `HumanGateway.read_escalation_context` calls after it authorized the task."""

    scope: Scope

    @abstractmethod
    async def read(self, *, task_id: str) -> EscalationContext:
        """Read the context of `task_id`, a task the caller was ALREADY authorized to read.

        Authorization is the gateway's job and happens first; this port never decides who may
        see a task. Any failure must surface as a `GatewayRefusalError` with a static code —
        never an upstream string.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


# --- pure parsers ---------------------------------------------------------------------------

_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_GROUP = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PRIORITY = re.compile(r"^P[0-9]{1,2}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DURATION = re.compile(r"^P(?:([0-9]{1,4})D)?(?:T(?:([0-9]{1,4})H)?(?:([0-9]{1,4})M)?(?:([0-9]{1,4})S)?)?$")


def clean_token(value: object) -> str | None:
    """A closed-vocabulary code, or `None`. A value outside the shape is dropped, not repaired."""
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def clean_group(value: object) -> str | None:
    return value if isinstance(value, str) and _GROUP.fullmatch(value) else None


def clean_priority(value: object) -> str | None:
    return value if isinstance(value, str) and _PRIORITY.fullmatch(value) else None


def clean_summary(value: object) -> str | None:
    """The agent's summary, scrubbed AGAIN on the way out and bounded.

    `_start_escalation` already ran `phi_vars.redact_free_text` on the way INTO the process. It
    runs here a second time on purpose: this string is about to be rendered to a person, and
    defense in depth costs one regex pass. Control characters go first — they mean nothing in a
    message and are the classic vehicle for text that disguises itself on screen.

    NOT a PHI classifier (see `redact_free_text`): clinical narrative in prose survives by
    design. That is why the field is bounded, never stored by the browser and named in the
    PR that introduced it.
    """
    if not isinstance(value, str):
        return None
    text = redact_free_text(_CONTROL.sub("", value)).strip()
    return text[:MAX_SUMMARY] or None


def parse_iso_duration(value: object) -> timedelta | None:
    """`PT5M`, `PT4H`, `P1D`, `PT1H30M` -> `timedelta`. Anything else -> `None`.

    Covers exactly the shapes the routing DMN emits and the engine's own timer parser accepts
    for the day/hour/minute/second family. Months and years are refused: they have no fixed
    length, and guessing one would print a deadline nobody agreed to.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value)
    if match is None or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_engine_datetime(value: object) -> datetime | None:
    """The engine REST date shape (`2026-09-28T21:31:33.000+0000`) -> aware UTC, else `None`."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return None if parsed.tzinfo is None else parsed.astimezone(UTC)
