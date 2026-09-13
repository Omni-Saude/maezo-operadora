"""The human basis of a formal denial, as the engine carries it (WP-J1-06, row j).

ADR-0006 keeps clinical text out of the General zone, so a portal NEGAR never puts
`justificativa_clinica`, `cid10_referencia` or `fundamentacao_dut` into process
variables: the browser contract requires all three (`portal/contracts/models.py`
`AuthDecisionInputs`, NEGAR branch), the gateway preserves them in PHI custody
(`gateway/human/decision_custody.py`), and the classified projection writes only
references and digests to the engine (`ClassifiedDecision.variables`).

What the engine therefore holds for a denial is this basis: nine server-authored
values, every one of them written inside the same transaction that completed the
auditor's User Task. None of them is PHI and none of them is browser-authored —
which is precisely why they can be verified by a worker and the clinical text cannot.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import model_validator

from maezo.gateway.required_text import is_blank
from maezo.portal.contracts.intake import Closed
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

#: Written by `AtomicHumanCommand` from the admitted, signed `ClassifiedDecision`.
BASIS_VARIABLES: tuple[str, ...] = (
    "human_decision_custody_ref",
    "human_decision_content_digest",
    "human_decision_request_digest",
    "human_decision_binding_digest",
    "human_decision_principal_ref",
    "human_decision_workload_ref",
    "human_decision_command_ref",
    "human_decision_audit_intent_ref",
)


class DenialNoticeError(RuntimeError):
    """Base: a denial notice is never composed on uncertain ground."""


class DenialIncompleteError(DenialNoticeError):
    """The grounding is not in custody, so the formal denial is NOT transmissible.

    Carried to the engine as the modeled `ERR_AUTH_DENIAL_INCOMPLETE`, caught by
    `BE_NegativaIncompleta` and ended at `End_FundamentacaoIncompletaBloqueada` — the
    neutral terminal where nothing reached the beneficiary. The rule it enforces is
    unchanged (RN 395 art. 10: no formal denial without complete grounding); only its
    evidence moved, from clinical strings in engine variables to the custody the
    gateway already holds.
    """

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("denial grounding not in custody")


class DenialNotHumanError(DenialNoticeError):
    """A denial without human accountability (ADR-0007/L0 hard).

    Returned as a record, never raised to the engine: the BPMN declares no boundary
    for `ERR_DENIAL_NOT_HUMAN`, so it cannot become a `bpmnError`.
    """


#: The single emptiness rule (`gateway/required_text.py`), shared with the grounding
#: guard of `SendDenialNoticeWorker`: a reference the PHI plane could not resolve is
#: missing, and invisible code points do not make one present.
blank = is_blank


class HumanDecisionBasis(Closed):
    """The engine-visible, non-PHI basis of one admitted human decision.

    KNOWN PROPERTY (V14 MINOR-7), recorded so nobody mistakes what this proves.
    These eight references are what `AtomicHumanCommand` writes for EVERY admitted
    human decision — APROVAR and NEGAR alike. Nothing in them says "this decision was
    a denial", and nothing here lets the worker verify that the sealed
    `content_digest` covers three non-blank clinical fields. So the guard proves *a
    complete human decision exists in custody*, not *a denial with complete grounding
    exists* — strictly weaker evidence than the generic worker's, which reads the
    clinical text directly.

    The chain still holds end to end: `AuthDecisionInputs` refuses a NEGAR without the
    three fields, `content_digest` seals exactly those inputs, and the worker gates on
    `decisao_auditor == "NEGAR"` before composing anything. Closing the gap needs a
    discriminator written into the basis by the engine-side writer (PR-C's
    `ClassifiedDecision`), which does not exist yet — so the day it appears,
    `test_the_basis_still_carries_no_denial_discriminator` fails and forces this guard
    to consume it, rather than the gap quietly becoming permanent.
    """

    custody_ref: OpaqueRef
    content_digest: Sha256Digest
    request_digest: Sha256Digest
    binding_digest: Sha256Digest
    principal_ref: OpaqueRef
    workload_ref: OpaqueRef
    command_ref: OpaqueRef
    audit_intent_ref: OpaqueRef

    @model_validator(mode="after")
    def separated(self) -> Self:
        # `ClassifiedDecision` refuses a projection whose principal is the workload
        # (`decision.py`, `_closed_projection`): a service account cannot be the
        # auditor. Re-assert it here so a forged variable set cannot pass either.
        if self.principal_ref == self.workload_ref:
            raise ValueError("decision principal may not be the workload")
        return self


class DenialNoticeRecord(Closed):
    """The composed formal-denial record. Carries references, never clinical text."""

    notice_type: str
    event: str
    human_approved: bool
    auditor_id: OpaqueRef
    denial_record_ref: Sha256Digest
    human_decision_custody_ref: OpaqueRef

    def variables(self) -> dict[str, Any]:
        """The engine-bound projection of this record: every key a verifiable fact.

        `status` is deliberately absent. This composition transmits nothing — the real
        secure channel to the provider is Fase 1 per the AUTH contract — and a `status`
        key would name an act nothing here performs.
        """
        return {
            "notice_type": self.notice_type,
            "error_code": None,
            "event": self.event,
            "human_approved": self.human_approved,
            "auditor_id": self.auditor_id,
            "denial_record_ref": self.denial_record_ref,
            "human_decision_custody_ref": self.human_decision_custody_ref,
        }


__all__ = [
    "BASIS_VARIABLES",
    "DenialIncompleteError",
    "DenialNotHumanError",
    "DenialNoticeError",
    "DenialNoticeRecord",
    "HumanDecisionBasis",
    "blank",
]
