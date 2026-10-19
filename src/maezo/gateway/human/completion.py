"""INTERIM direct ESCALATION completion — closed types, ports and the note scrub (DL-0049).

WHAT THIS IS, SAID PLAINLY. `docs/decisions-log.md` DL-0049 records the shortcut this
module implements: ADR-0049 D5/D6/D7 says a human command reaches the engine as a signed,
durable, deduplicated envelope dispatched by `HumanCommandRelay` over the outbox. That relay
is declared NOT ESTABLISHED in `src/maezo/portal/engine/README.md`, so the portal today shows
the queue and cannot close the case. This module is the *interim* path that closes it, behind
a gate that is OFF by default and fenced to `dev-sa-east-1` by
`scripts/ci/check_portal_direct_completion.py`. It is not the destination; issue #427's relay
is. Read DL-0049 before extending anything here.

Nothing in this module is a capability: no key, no connection, no HTTP client and no audit
sink. It holds the closed value types, the two abstract ports the gateway calls, and the one
free-text scrub the BPMN contract requires. The concrete adapters live in `completion_engine.py`
and are installed only by an explicit composition.

DL-0111 (SP-OP-CANCEL-001 v0.2.0, dev only, same gate): the SAME interim path also closes the CANCEL
registry execution task (`cancel_execucao`: one closed variable `execucao_cadastro`, no note). The
set of forms is CLOSED (`COMPLETION_FORMS`): variable names live in server code, one outcome variable
plus at most one pseudonymized note per form, and a body can only close a task of its own form.
"""

from __future__ import annotations

import hashlib
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from maezo.portal.contracts.completions import (
    CANCEL_EXECUCAO_OUTCOMES,
    COMPLETION_OUTCOMES,
    MAX_NOTES,
    CancelExecucaoOutcome,
    CompletionOutcome,
)
from maezo.portal.contracts.models import TaskSnapshot
from maezo.tools.workers.phi_vars import redact_free_text

from .models import Scope

#: The closed outcome set is NOT restated here. `portal/contracts/completions.py` derives it
#: from `EscalationDecisionInputs`, which this repo derives from
#: `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`:
#: "Saidas obrigatorias ao completar: resultado in {resolvido_humano, devolvido_agente,
#: emergencia_acionada}; notas_resolucao (texto livre, pseudonimizado)."
#: There is no default: a completion without an explicit outcome is refused, never guessed.
__all__ = [
    "CANCEL_EXECUCAO_FORM_KEY",
    "COMPLETION_FORMS",
    "AnyCompletionOutcome",
    "CompletionForm",
    "COMPLETION_FORM_KEY",
    "COMPLETION_OUTCOMES",
    "COMPLETION_VARIABLES",
    "INTERIM_DECISION_REF",
    "MAX_NOTES",
    "NotesTooLongError",
    "CompletedTask",
    "CompletionAck",
    "CompletionAuditEntry",
    "CompletionAuditSink",
    "CompletionOutcome",
    "DirectTaskCompletion",
    "PseudonymizedNotes",
    "pseudonymize_notes",
]

#: The only form contract whose outputs are these two variables
#: (`portal/contracts/models.py::_INPUTS_BY_FORM`). A task with any other form key is refused:
#: this path completes ESCALATION human tasks, not "whatever the engine will accept".
COMPLETION_FORM_KEY = "escalation"
COMPLETION_VARIABLES: tuple[str, str] = ("resultado", "notas_resolucao")

#: DL-0111: the CANCEL v0.2.0 registry execution form (`UT_ExecutarCancelamentoCadastro` and its
#: SLA coordination). One closed outcome variable, NO note: nothing free-text reaches the engine.
CANCEL_EXECUCAO_FORM_KEY = "cancel_execucao"

#: The outcome of any form this path may close.
AnyCompletionOutcome = CompletionOutcome | CancelExecucaoOutcome


@dataclass(frozen=True, slots=True)
class CompletionForm:
    """One form the interim path may close: the outcome variable, its closed values and the
    optional pseudonymized note variable. Server-owned: nothing here comes from the browser."""

    form_key: str
    outcome_variable: str
    outcomes: tuple[str, ...]
    note_variable: str | None

    @property
    def variables(self) -> tuple[str, ...]:
        return (self.outcome_variable,) + ((self.note_variable,) if self.note_variable else ())


#: The CLOSED set of forms the interim completion may close (DL-0049, extended by DL-0111).
COMPLETION_FORMS: Mapping[str, CompletionForm] = MappingProxyType(
    {
        COMPLETION_FORM_KEY: CompletionForm(
            COMPLETION_FORM_KEY, "resultado", COMPLETION_OUTCOMES, "notas_resolucao"
        ),
        CANCEL_EXECUCAO_FORM_KEY: CompletionForm(
            CANCEL_EXECUCAO_FORM_KEY, "execucao_cadastro", CANCEL_EXECUCAO_OUTCOMES, None
        ),
    }
)

#: The decision-log row that authorises this module's existence. Quoted in the audit trail and
#: in the endpoint's refusal path so the shortcut is one `grep` away from its own registration.
INTERIM_DECISION_REF = "DL-0049"


@dataclass(frozen=True, slots=True, repr=False)
class PseudonymizedNotes:
    """`notas_resolucao` after `redact_free_text`, carried as its own type.

    A MARKER, not a capability: this dataclass is constructible by anyone, exactly like every
    other frozen dataclass here. What it buys is that the transport's parameter is typed, and
    that `grep -rn "PseudonymizedNotes("` names every site that claims a scrub happened —
    today only `pseudonymize_notes` below. `digest` is what reaches the audit chain; the text
    itself only ever travels to the engine, which is inside the PHI boundary.
    """

    text: str = field(repr=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class NotesTooLongError(Exception):
    """The note, AFTER the scrub, is longer than `MAX_NOTES`. Closed: carries no text, no length.

    The scrub REPLACES identifiers with class tokens (`[REDACTED_PHONE]` is 16 characters, the
    phone it replaces may be 10), so a note under the wire limit can come out over it. Refusing
    is the only honest answer: cutting a human's note in the middle changes what it says (the
    `...[TRUNCATED]` cut this replaces sent the beneficiary half an instruction).
    """

    code: Literal["notes_too_long"] = "notes_too_long"

    def __init__(self) -> None:
        super().__init__(self.code)


def pseudonymize_notes(raw: str) -> PseudonymizedNotes:
    """Run the SAME free-text net `_start_escalation` runs, before the note leaves the BFF.

    `maezo.agents.helena.graph::_start_escalation` scrubs `resumo_contexto` with
    `phi_vars.redact_free_text` on the way INTO this process; SP-OP-ESCALATION-001 requires
    `notas_resolucao` to be "pseudonimizado" on the way out of it. Same function and same
    identifier families — not a second, drifting policy.

    NEVER CUTS (P3, 16/10/2026). The 500-character cap of `redact_free_text` is for error messages;
    applied here it cut the attendant's note to 500 characters plus `...[TRUNCATED]`, and that is
    what the beneficiary received. The note is scrubbed WHOLE and, if the scrubbed text is longer
    than `MAX_NOTES` (the same number the wire schema carries as `maxLength`), the completion is
    refused with `NotesTooLongError` — before the audit `intent` link and before any engine call
    (`HumanGateway.complete_task` calls this right after the session/CSRF check).

    NOT a PHI classifier. It removes e-mail, CPF/CNPJ, BR phone and long digit runs; clinical
    narrative in prose survives by design, which is why this note stays inside the PHI zone
    and only its digest reaches the audit chain (`phi_vars.redact_free_text` docstring).
    """
    # `max_chars=sys.maxsize`: the cap is disabled on purpose; the limit is checked below, whole.
    text = redact_free_text(raw, max_chars=sys.maxsize)
    if len(text) > MAX_NOTES:
        raise NotesTooLongError()
    return PseudonymizedNotes(text)


@dataclass(frozen=True, slots=True)
class CompletionAck:
    """What the engine's completion answered — a technical record, never a clinical verdict.

    `consumed_task_revision` is the revision this BFF read and completed against; the engine
    REST completion carries no optimistic fence of its own, so this field records the local
    expectation, it does not prove the engine enforced it. That missing fence is the debt
    DL-0049 registers and the durable relay closes.
    """

    task_id: str
    resultado: AnyCompletionOutcome
    consumed_task_revision: int
    completed_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class CompletedTask:
    """The gateway's answer: the state that was closed, and the trail that proves who closed it.

    `audit_intent_ref` is the chain link committed BEFORE the engine call (and the claim that
    excluded a concurrent completion of the same revision); `audit_result_ref` is the link
    committed after it. Two hashes, in the same chain `human_command.*` writes to, so the test
    page can compare a portal completion against an engine completion row for row.
    """

    snapshot: TaskSnapshot = field(repr=False)
    resultado: AnyCompletionOutcome
    authority_revision: int
    consumed_task_revision: int
    completed_at: datetime
    audit_intent_ref: str
    audit_result_ref: str


#: O desfecho do elo de `result`. `completed` e' o motor confirmando; os dois de recusa sao a
#: revalidacao imediatamente antes do efeito barrando, com NADA enviado (DL-0049 review, P1).
#: Sao DOIS e nao um porque quem audita precisa separar governanca (alguem perdeu o direito no
#: meio do caminho) de relogio (o prazo da tarefa ou da projecao venceu).
CompletionRefusal = Literal["refused_membership_revoked", "refused_deadline_lapsed"]
CompletionPhaseOutcome = Literal["completed"] | CompletionRefusal


@dataclass(frozen=True, slots=True, repr=False)
class CompletionAuditEntry:
    """One link of the human completion trail: WHO closed it, on WHAT basis, at WHICH revision.

    Pseudonymous by construction. `principal_ref` and `session_ref` are the server-side opaque
    references the portal already uses in `SessionDTO`/`PendingAdmission` — never the Cognito
    subject, never an e-mail. `notes_digest` replaces the narrative: the audit chain is in the
    General zone and `AuditRecord.details` must carry no PHI (`gateway/audit.py::hash_input`).
    """

    scope: Scope
    phase: Literal["intent", "result"]
    principal_ref: str
    session_ref: str
    membership_revision: int
    snapshot: TaskSnapshot = field(repr=False)
    authority_revision: int
    resultado: AnyCompletionOutcome
    #: `None` for a form without a note (DL-0111 `cancel_execucao`).
    notes_digest: str | None
    #: `None` no elo de intent. No elo de result: `"completed"` quando o motor confirmou, ou um
    #: dos dois de RECUSA quando a revalidacao imediatamente antes do efeito barrou e NADA foi
    #: enviado (DL-0049 review, P1). Eles existem para a claim de intent nao ficar pendurada:
    #: sem elo de result, a unica leitura possivel de um intent solitario e' 'o efeito e'
    #: incerto' — e aqui ele e' CERTO, nao aconteceu. E sao DOIS, nao um: `revoked` e' alguem
    #: perdendo o direito no meio do caminho, `lapsed` e' o prazo da tarefa/projecao vencendo;
    #: quem audita precisa distinguir governanca de relogio, e um log nao sobrevive ao mes.
    outcome: CompletionPhaseOutcome | None = None

    @property
    def dedup_key(self) -> str:
        """The durable claim that makes this an effect gate, not only a double-audit guard.

        Keyed on task AND the consumed revision (`audit_emit_dedup` is `PRIMARY KEY (tenant,
        dedup_key)`, so the tenant is already in the key). Two browsers racing the same
        completion of the same revision produce ONE claim; the loser observes `deduped=True`
        and is refused with a revision conflict instead of completing twice. See
        `gateway/audit.py::EmitOnceOutcome`.
        """
        return f"portal-direct-completion:{self.phase}:{self.snapshot.task_id}:{self.snapshot.task_revision}"


class CompletionAuditSink(ABC):
    """Append one completion link to the tenant's existing hash chain, or raise.

    MUST be the chain `PostgresAuditSink` owns — no second algorithm, no parallel table and no
    in-memory fallback in production. MUST report the durable dedup claim, because the intent
    link is what stands between two callers and two completions.
    """

    scope: Scope

    @abstractmethod
    async def record(self, entry: CompletionAuditEntry) -> tuple[str, bool]:
        """Return `(record_hash, deduped)`; `deduped=True` means someone else already claimed."""
        raise NotImplementedError


class DirectTaskCompletion(ABC):
    """The interim engine completion capability. Absent by default; absence refuses.

    An implementation MUST NOT sign with, borrow from or share the read/command credential
    partitions (`gateway/human/README.md`: read and publication signing capabilities "never
    borrow their signers"). It MUST treat a non-answer as UNCERTAIN, never as rollback.
    """

    scope: Scope

    @abstractmethod
    async def complete(
        self,
        *,
        task_id: str,
        resultado: AnyCompletionOutcome,
        notes: PseudonymizedNotes | None,
        expected_task_revision: int,
        form_key: str = COMPLETION_FORM_KEY,
    ) -> CompletionAck:
        raise NotImplementedError

    async def close(self) -> None:
        """Release whatever this capability owns. The application lifespan calls it once."""
        return None
