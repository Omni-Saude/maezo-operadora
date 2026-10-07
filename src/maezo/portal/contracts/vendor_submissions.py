"""OP16 `channel.submission.submit` — the vendor channel submission wire contract.

Registry OP16 (`OP-REGISTRY-VENDOR-V1.md` L74-85, namespace OP13+): the channel submits a
formalization envelope and receives a receipt; the answer itself is NEVER carried here. The
response leg is the native case/document-request plane (`external_cases` kind declared for the
vendor audience; existing authorities answer) — never a notice (PW1-C files stay untouched) and
never an assertion of enrollment/acceptance (C-10: a submission stage is a stage, not a verdict).

The health-declaration document leg (NB-12a) does NOT land here: VW0-D16 (RATIFY-LATER, DPO)
blocks it, so the closed payload envelope declares exactly one content class (`commercial`) and
carries NO free text, NO attachments and NO clinical slot at all — an undeclared class is
classified and refused by the firewall (`PHI_IN_COMMERCIAL_INPUT`), never shape-dropped, which
is why the class token is pattern-bound on the wire instead of being a closed literal.

Refusal names are the registry OP16 set, verbatim. Wire error envelopes mix two conventions on
purpose: lowercase transport codes follow the intake lane (`PortalIntakeError`); SCREAMING codes
are registry refusals quoted exactly as the registry spells them.

Idempotency (NO-DUPLICATE-INSTANCE): business key `tenant + channel_submission + submission_ref
+ business_revision`. One active instance per key; a re-send of the same key returns the SAME
active instance and never a second response trail. `command_id` is attempt identity only: it is
excluded from the business digest, so a retry with a fresh `command_id` and identical business
content replays; the same key with mutated business content is a `CONTRACT_MISMATCH`, never a
new instance.
"""

from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef

#: The closed submission lifecycle; each stage names its registry OP16 event.
Lifecycle = Literal["received", "documents_pending", "responded", "refused"]

SUBMISSION_LIFECYCLE_EVENTS: dict[Lifecycle, str] = {
    "received": "submission.received",
    "documents_pending": "submission.documents_pending",
    "responded": "submission.responded",
    "refused": "submission.refused",
}

#: Registry OP16 refusals, verbatim. The receipt refuses ONLY with these.
VendorSubmissionRefusalCode = Literal[
    "AUTHORITY_UNPROVEN",
    "CHANNEL_STATUS_INELIGIBLE",
    "CONTRACT_MISMATCH",
    "SOURCE_UNAVAILABLE",
    "PHI_IN_COMMERCIAL_INPUT",
    "STALE_REVISION",
    "AUDIT_UNAVAILABLE",
]

#: The only payload class this wave declares. The health-declaration class is NOT declared:
#: VW0-D16 (RATIFY-LATER DPO) owns that schema, and nobody invents it here.
DECLARED_PAYLOAD_CLASS = "commercial"


class ChannelSubmissionPayload(Closed):
    """The closed submission envelope: a declared class token and nothing else.

    `content_class` is a bounded DECLARATION slot, deliberately not a `Literal`: the firewall
    must be able to NAME an undeclared (clinical or unknown) class in a typed, audited refusal
    instead of dropping it as a shape error. Everything beyond the token — form content,
    attachments, clinical statements — is structurally absent from this contract.
    """

    content_class: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class ChannelSubmissionCommand(Closed):
    schema_version: Literal[1] = 1
    command_id: ResourceRef
    channel_ref: ResourceRef
    submission_ref: ResourceRef
    business_revision: DecimalRevision
    contract_ref: ResourceRef
    payload: ChannelSubmissionPayload


class ChannelSubmissionReceipt(Closed):
    """What the machine PROVES back: stage, instance identity, refusal — never a verdict.

    `case_ref`/`document_request_ref` bind the response leg to NATIVE objects only: `responded`
    is unconstructible without a native case object an existing authority produced, and
    `documents_pending` without a native document request. `received` binds nothing — staging a
    submission creates no case and asserts no acceptance (C-10).
    """

    schema_version: Literal[1] = 1
    command_id: ResourceRef
    channel_ref: ResourceRef
    submission_ref: ResourceRef
    business_revision: DecimalRevision
    submission_id: ResourceRef
    lifecycle: Lifecycle
    refusal_code: VendorSubmissionRefusalCode | None = None
    case_ref: ResourceRef | None = None
    document_request_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def stage_evidence(self) -> Self:
        refused = self.lifecycle == "refused"
        if refused != (self.refusal_code is not None):
            raise ValueError("refusal evidence required")
        if self.lifecycle == "responded" and self.case_ref is None:
            raise ValueError("native case evidence required")
        if self.lifecycle == "documents_pending" and self.document_request_ref is None:
            raise ValueError("native document request evidence required")
        if self.lifecycle in ("received", "refused") and (
            self.case_ref is not None or self.document_request_ref is not None
        ):
            raise ValueError("unproven native binding")
        return self


class ChannelSubmissionStatus(Closed):
    """Read projection of one stored instance; the formalization answer is not a field here."""

    schema_version: Literal[1] = 1
    submission_id: ResourceRef
    channel_ref: ResourceRef
    submission_ref: ResourceRef
    business_revision: DecimalRevision
    contract_ref: ResourceRef
    lifecycle: Lifecycle
    refusal_code: VendorSubmissionRefusalCode | None = None
    case_ref: ResourceRef | None = None
    document_request_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def stage_evidence(self) -> Self:
        refused = self.lifecycle == "refused"
        if refused != (self.refusal_code is not None):
            raise ValueError("refusal evidence required")
        return self


class PortalVendorSubmissionError(Closed):
    """Closed wire error; codes are transport (lowercase) or registry refusals (verbatim)."""

    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "resource_unavailable",
        "AUTHORITY_UNPROVEN",
        "CHANNEL_STATUS_INELIGIBLE",
        "CONTRACT_MISMATCH",
        "SOURCE_UNAVAILABLE",
        "PHI_IN_COMMERCIAL_INPUT",
        "STALE_REVISION",
        "AUDIT_UNAVAILABLE",
    ]
