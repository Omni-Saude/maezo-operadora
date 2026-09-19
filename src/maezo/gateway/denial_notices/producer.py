"""The one owner of `operadora.auth.send_denial_notice` when the portal decides.

## Why a second producer exists at all

`SendDenialNoticeWorker` (`tools/workers/auth.py`) reads the three ANS grounding
fields out of process variables and refuses the send when any is blank. That guard is
correct and stays. What changed underneath it is where the grounding lives: since the
portal's decision plane, a NEGAR's clinical text is preserved in PHI custody and the
engine receives only references and digests (`ClassifiedDecision.variables`). So a
portal NEGAR reaches that worker with all three fields absent — *by design, for PHI
reasons* — and every one of them ends at `End_FundamentacaoIncompletaBloqueada`. Two
correct components, one dead journey.

This producer is the owner for that path. It enforces the same rule against the
evidence that actually exists:

* **Completeness** — the denial must be backed by a complete human-decision basis in
  custody. The grounding itself was validated where it lives, at submission time, by
  the browser contract (`AuthDecisionInputs` refuses a NEGAR without all three
  fields) and then sealed by `content_digest` over exactly those inputs. A denial
  whose basis is absent or malformed is not transmissible and raises the modeled
  `ERR_AUTH_DENIAL_INCOMPLETE`, unchanged, to the same boundary.
* **Human accountability** — `auditor_id` must be present AND be the very principal
  the classified decision was projected for. A denial cannot be attributed to a
  principal that did not make it, and a workload reference can never be an auditor.

## What it deliberately does NOT do

It does not resolve the PHI body. `HumanDecisionCustody.resolve` authorizes only the
deciding principal, and `EngineBackedPhiDecisionAuthorization` grants nothing once the
task is no longer current — so no post-completion consumer may read that text today.
Composing the *notice* from references is honest; fabricating a PHI egress, or
relaxing that authorization to obtain one, would not be. The remaining half of row j
(the PHI body and its delivery to provider and beneficiary) needs a ratified PHI
consumer permission, and is reported as such rather than implemented here.

It also transmits nothing: the AUTH contract states the real secure channel is Fase 1.
This composes the record and returns it; no key it emits claims otherwise.
"""

from __future__ import annotations

import hashlib

from maezo.portal.engine.profile import canonicalize

from .models import (
    BASIS_VARIABLES,
    DenialIncompleteError,
    DenialNotHumanError,
    DenialNoticeRecord,
    HumanDecisionBasis,
    blank,
)

#: The single external-task topic this producer owns.
TOPIC = "operadora.auth.send_denial_notice"
PROCESS = "SP-OP-AUTH-001"
#: `ST_PublishNegada`, the only outgoing flow of `ST_EnviarNegativaFormal`.
EVENT = "agents.events.auth.completed"


def denial_record_ref(basis: HumanDecisionBasis) -> str:
    """A stable, non-PHI reference to this exact denial record.

    Derived from the basis alone, so the same decision always names the same record
    and two different decisions never collide. It is a digest of references, never of
    clinical text — there is nothing here to attack with a dictionary.
    """
    return hashlib.sha256(
        canonicalize({"schema": "maezo.auth-denial-record.v1", **basis.model_dump(mode="json")})
    ).hexdigest()


def read_basis(process_vars: dict[str, object]) -> HumanDecisionBasis:
    """Read the human-decision basis out of engine variables, or refuse.

    Every missing or malformed reference is reported by NAME (never by value) so an
    operator can see what the engine failed to carry, and the refusal is the modeled
    incomplete-grounding error, because an unresolvable basis means the formal
    grounding cannot be shown to exist.
    """
    missing = tuple(name for name in BASIS_VARIABLES if blank(process_vars.get(name)))
    if missing:
        raise DenialIncompleteError(missing)
    try:
        return HumanDecisionBasis(
            custody_ref=str(process_vars["human_decision_custody_ref"]),
            content_digest=str(process_vars["human_decision_content_digest"]),
            request_digest=str(process_vars["human_decision_request_digest"]),
            binding_digest=str(process_vars["human_decision_binding_digest"]),
            principal_ref=str(process_vars["human_decision_principal_ref"]),
            workload_ref=str(process_vars["human_decision_workload_ref"]),
            command_ref=str(process_vars["human_decision_command_ref"]),
            audit_intent_ref=str(process_vars["human_decision_audit_intent_ref"]),
        )
    except Exception:
        # A basis that does not typecheck is a basis that cannot be resolved in PHI.
        raise DenialIncompleteError(BASIS_VARIABLES) from None


class DenialNoticeProducer:
    """Compose one formal-denial record from an admitted human decision.

    Pure: no I/O, no engine call, no clock. The transport that feeds it is the
    external-task harness, and the exclusivity of this topic is enforced by whoever
    installs it (`runtime/worker_runtime/denial_notices.py`).
    """

    topic = TOPIC
    process_key = PROCESS

    def compose(self, process_vars: dict[str, object]) -> DenialNoticeRecord:
        """Guards in the same order the BPMN models them: completeness, then provenance.

        Completeness first, unconditionally: an ungrounded denial must be barred in
        ANY circumstance, including one where the provenance check would also have
        failed — the impossibility of establishing completeness IS incompleteness.
        """
        basis = read_basis(process_vars)

        auditor = process_vars.get("auditor_id")
        auditor_id = auditor.strip() if isinstance(auditor, str) else ""
        if not auditor_id or auditor_id != basis.principal_ref:
            # The host returns this as a record: the BPMN declares no boundary for
            # ERR_DENIAL_NOT_HUMAN, so it can never become a `bpmnError`.
            raise DenialNotHumanError("denial lacks human accountability")

        return DenialNoticeRecord(
            notice_type="denial",
            event=EVENT,
            # Resolved provenance, not a decoration: this line is reachable only with
            # an auditor principal that matches the admitted classified decision.
            human_approved=True,
            auditor_id=auditor_id,
            denial_record_ref=denial_record_ref(basis),
            human_decision_custody_ref=basis.custody_ref,
        )


__all__ = [
    "EVENT",
    "PROCESS",
    "TOPIC",
    "DenialNoticeProducer",
    "denial_record_ref",
    "read_basis",
]
