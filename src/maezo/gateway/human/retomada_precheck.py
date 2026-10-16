"""P5 (16/10/2026): the 'devolvido_agente' note checked IN THE PORTAL against the SAME output fence the
Helena applies before sending it to the beneficiary — so the attendant hears it now, not never.

What was wrong. On `devolvido_agente` the note travels to the engine, the BPMN publishes
`process_completed`, `agent_resume` reads the note from the engine history and the Helena composes
the message (`compor_mensagem_de_retomada`) and runs her output fence on it
(`motivo_de_recusa_da_retomada`). A note the fence bars ("entraremos em contato", "nao e nada
grave", an unconfirmed app name) is NOT sent: the beneficiary receives the placeholder and the
attendant, who already saw "Tarefa concluida", is never told. (The team alert for that case is P6,
`agent_resume.NotifyTeamAlerter`.)

What this module does. The SAME two functions, imported — never a copy of the fence — run on the
SAME text the Helena will compose: the note AFTER `pseudonymize_notes` (what the engine stores and
`agent_resume` relays), inside the approved template. A barred note refuses the completion with
the closed code `resumption_refused` and the closed GROUP of the rule (`ResumptionRefusalGroup`),
before the audit `intent` link and before any engine call. The PATTERN that matched and the TEXT
never leave this module: the group is a rule family, the pattern would teach how to dodge it.

Scope, declared. Only the escalation form (`notas_resolucao`) and only `devolvido_agente` (the two
other outcomes close the case; their note never reaches the beneficiary). The portal does not know
which agent opened the escalation (`agent_id` is not among the three variables DL-0050 lets the
portal read), so the check is the Helena's for every `devolvido_agente`: today the Helena is the
only agent with a resume port for SP-OP-ESCALATION-001 (`agent_resume`, "hoje so' a Helena tem porta
de retomada"); the Lucas is out of this scope.

Architecture, measured (not assumed): nothing in the repository forbids `gateway/human` from
importing `maezo.agents.helena` (no import contract, no fence test). The cost is real and declared:
`maezo.agents.helena.graph` drags `maezo.runtime` (langgraph and the LLM SDK module, never a
credential) into the portal process, which loaded none of them before. See DL-0117.
"""

from __future__ import annotations

from typing import Final, get_args

from maezo.agents.helena.graph import compor_mensagem_de_retomada, motivo_de_recusa_da_retomada
from maezo.agents.helena.prompts import (
    RECUSA_CANAL_NAO_CONFIRMADO,
    RECUSA_NEGATIVA_CLINICA,
    RECUSA_PROMESSA_DE_CAPACIDADE,
    RECUSA_PROMESSA_SEM_START,
)
from maezo.portal.contracts.completions import ResumptionRefusalGroup

__all__ = ["RESULTADO_QUE_RETOMA", "ResumptionRefusedError", "check_resumption_note"]

#: The only outcome whose note the agent sends to the beneficiary (`agent_resume`, "SO'
#: `resultado == "devolvido_agente"` acorda o agente").
RESULTADO_QUE_RETOMA: Final[str] = "devolvido_agente"

#: Every group the Helena's resume fence can return (`start_aconteceu=False`, authorization fence
#: off), plus `outra_regra` for a group added to the Helena later and not yet named here: the note
#: is still REFUSED (fail closed), the label is just generic until someone names it.
_HELENA_GROUPS: Final[frozenset[str]] = frozenset(
    {
        RECUSA_NEGATIVA_CLINICA,
        RECUSA_PROMESSA_DE_CAPACIDADE,
        RECUSA_PROMESSA_SEM_START,
        RECUSA_CANAL_NAO_CONFIRMADO,
    }
)
_OTHER: Final[str] = "outra_regra"
if set(get_args(ResumptionRefusalGroup)) != _HELENA_GROUPS | {_OTHER}:  # pragma: no cover - import fence
    raise RuntimeError("ResumptionRefusalGroup drifted from the Helena resume fence groups")


class ResumptionRefusedError(Exception):
    """The note would be barred by the Helena's output fence. Carries the GROUP only."""

    code: Final = "resumption_refused"

    def __init__(self, group: ResumptionRefusalGroup) -> None:
        self.group: ResumptionRefusalGroup = group
        super().__init__(self.code)


def check_resumption_note(resultado: str, pseudonymized_note: str) -> None:
    """Refuse (`ResumptionRefusedError`) a `devolvido_agente` note the Helena would not send.

    `pseudonymized_note` MUST be the text after `pseudonymize_notes`: it is what the engine stores
    and what the Helena receives, so the verdict here is the verdict there.
    """
    if resultado != RESULTADO_QUE_RETOMA:
        return
    recusa = motivo_de_recusa_da_retomada(compor_mensagem_de_retomada(pseudonymized_note))
    if recusa is None:
        return
    grupo, _padrao = recusa  # the pattern stays here: never logged, never returned
    group: ResumptionRefusalGroup = grupo if grupo in _HELENA_GROUPS else _OTHER  # type: ignore[assignment]
    raise ResumptionRefusedError(group)
