"""Vendor-audience notice composition (OP09 prepare half). Composition only, never delivery.

Fence status (BRIEF-VW1-P3 / WAVES §2.5): `models.py`, `content.py`, `provider_notice.py`,
`production.py` and the portal communications surface are PW1-C/PW1-D molds — read here,
never edited; this file is a NEW vendor-side module, the declared coordination point of the
vendor DAG. The `vendor` audience literal (models.py:77) is consumed, not re-decided.

Fail-closed by construction:

- Distribution is NOT wired: no route, no worker, no DMN, no sender. The composed draft, its
  canonical payload and its digest are the whole effect (`distribution="not_wired"`); the F6
  delivery channels distribute events when a qualified lane exists — not in this wave.
- Client disclosure (OP18 leg (b)) refuses by construction with `RATIFICATION_MISSING`
  (VW0-D18: the BR legal trigger for corretagem disclosure is RATIFY-LATER, owner = jurídico).
  NB-4 (pontual-de-venda × periódico-público) is REGISTERED, not decided — the two disclosure
  plans must be separated BEFORE any wire exists; this module wires nothing.
- Registry §3 PHI firewall (Q7): the notice subject has NO free-text field — a closed
  contratacional vocabulary only (refs, closed literals, revisions). Commercial input carrying
  a field of clinical class or UNKNOWN class is refused typed (`PHI_IN_COMMERCIAL_INPUT`)
  before anything is composed: zero egress. The definitive field-class taxonomy and the
  k-anon floor are DPO RATIFY-LATER; they block the package, never this shape.
- G-CADE: the payload never carries rates, market or competitor comparisons — there is no
  field that could. G-CDC art. 30: only `authorized_content_ref` (an approved content
  artifact) is bound, so what is veiculado stays the ratified artifact, never prose here.

The refusal vocabulary is local because `CapabilityRefusalReason` lives in the PW2-A frozen
file; extending it requires its own ROOT dispatch. Grant bindings mirror the molds: the
subject fingerprint must equal the grant `request_digest`, the notice fields must be within
`permitted_fields`, and every intended recipient must be audience `vendor` — a notice for any
other audience never composes.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, NoReturn

from pydantic import Field

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest
from maezo.portal.engine.profile import canonicalize

from .models import CommunicationGrant, alive, fingerprint

NOTICE_SCHEMA_VERSION: Literal["vendor-notice.v1"] = "vendor-notice.v1"
NOTICE_OPERATION = "notice.prepare_or_send"
SUBMISSION_FORMAL_RESPONSE = "submission_formal_response"


class VendorNoticeRefusalReason(StrEnum):
    """Vendor-layer typed refusals. Registered reasons only — never free diagnostic text."""

    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    RATIFICATION_MISSING = "RATIFICATION_MISSING"


class VendorNoticeRefusalError(ValueError):
    """Bounded outward refusal; never carries the refused payload or its text."""

    def __init__(self, reason: VendorNoticeRefusalReason, *, decision_ref: str | None = None) -> None:
        self.reason = reason
        self.decision_ref = decision_ref
        super().__init__(reason.value)


class VendorNoticeSubject(Closed):
    """The notice body itself: closed contratacional vocabulary, zero free text (F6).

    Every field is class contratacional (registry §3): it exists in the contractual
    relationship and reveals no health condition of any person. `kind` is closed to the one
    wired consumer — the formal response notice of a channel submission — consumed BY
    REFERENCE through the opaque `case_ref`; the case object belongs to the OP16 submission
    package and is never imported here (never a second response track). `authorized_content_ref`
    names the approved content artifact (G-9656/G-CDC): the veiculado is the ratified
    artifact, never prose carried in this payload.
    """

    schema_version: Literal["vendor-notice.v1"] = NOTICE_SCHEMA_VERSION
    notice_ref: ResourceRef
    notice_class: Literal["mandatory", "facultative"]
    kind: Literal["submission_formal_response"]
    case_ref: ResourceRef
    business_revision: DecimalRevision
    authorized_content_ref: OpaqueRef


NOTICE_FIELDS = frozenset(VendorNoticeSubject.model_fields)


class VendorNoticeDraft(Closed):
    """Deterministic composition result. No wall-clock field exists anywhere in it.

    `canonical_payload` is the RFC 8785 subset serialization of the bound notice;
    `payload_sha256` addresses those bytes; `idempotency_key` follows the OP09 axes
    `tenant + operation + domain-object-reference + business-revision`. `distribution` stays
    `not_wired` until a qualified delivery lane adopts the draft — the value is part of the
    frozen shape so an accidentally wired sender cannot reuse this type silently.
    """

    schema_version: Literal["vendor-notice.v1"] = NOTICE_SCHEMA_VERSION
    notice_ref: ResourceRef
    case_ref: ResourceRef
    business_revision: DecimalRevision
    audience: Literal["vendor"]
    recipient_count: int = Field(ge=1)
    canonical_payload: str = Field(min_length=1)
    payload_sha256: Sha256Digest
    idempotency_key: Sha256Digest
    distribution: Literal["not_wired"] = "not_wired"


class DisclosurePreparationCommand(Closed):
    """Registry OP18 request axes (idempotency tuple only) — refused by construction today.

    Shaped so the ratification gate lands in exactly one place when jurídico ratifies the
    leg-(b) trigger; carrying the axes keeps the future `disclosure.prepared` event keyed by
    `tenant + disclosure_prepare + commercial_event_ref + artifact_version` without any
    content field that could smuggle PHI into a commercial input.
    """

    schema_version: Literal["vendor-disclosure.v1"] = "vendor-disclosure.v1"
    tenant: OpaqueRef
    commercial_event_ref: ResourceRef
    artifact_version: DecimalRevision


def screen_commercial_input(fields: Mapping[str, object]) -> None:
    """Registry §3 layer 2 (hostile admission): refuse clinical or UNKNOWN field classes.

    The declared subject fields are the closed contratacional vocabulary of this vehicle;
    any other key in commercial input is an UNKNOWN class (a clinical-class key collapses
    into the same refusal until the DPO taxonomy is ratified) and refuses typed BEFORE any
    composition — zero downstream effect, zero egress.
    """
    if set(fields) - NOTICE_FIELDS:
        raise VendorNoticeRefusalError(VendorNoticeRefusalReason.PHI_IN_COMMERCIAL_INPUT)


def prepare_client_disclosure(*, command: DisclosurePreparationCommand) -> NoReturn:
    """OP18 leg (b), disclosure of remuneration to the client: refuse by construction.

    VW0-D18: the BR legal trigger for corretagem disclosure is RATIFY-LATER (owner jurídico);
    until it is ratified this leg always refuses `RATIFICATION_MISSING` and composes nothing.
    NB-4 (pontual-de-venda × periódico-público) is registered, NOT decided — the separation
    of the two disclosure plans must happen BEFORE any wire, and no wire exists here. Leg (a)
    (relato da operadora ao regulador, RN 518/2022 3.1 e-f) is a separate duty and is NOT
    composed by this vehicle.
    """
    raise VendorNoticeRefusalError(VendorNoticeRefusalReason.RATIFICATION_MISSING, decision_ref="VW0-D18")


def compose_vendor_notice(*, grant: CommunicationGrant, subject: VendorNoticeSubject) -> VendorNoticeDraft:
    """Compose the vendor notice draft deterministically; compose, never send.

    Mold-faithful grant bindings (content.py/service.py read as molds): the live grant
    ceiling is enforced, the operation must be `publish`, the subject fingerprint must equal
    the grant `request_digest`, every intended recipient must be audience `vendor` for the
    granted case, and the emitted fields must be within `permitted_fields`. Any mismatch is
    `denied` — the same bounded classification the molds raise, with no payload echo.
    """
    subject_payload = subject.model_dump(mode="json")
    screen_commercial_input(subject_payload)
    alive(grant.ceiling())
    if (
        grant.access.operation != "publish"
        or grant.access.request_digest != fingerprint(subject_payload)
        or grant.access.case_ref != subject.case_ref
        or any(recipient.audience != "vendor" for recipient in grant.intended_recipients)
        or not grant.intended_recipients
        or not NOTICE_FIELDS.issubset(grant.permitted_fields)
    ):
        raise ExternalCaseError("denied")
    canonical = canonicalize(
        {
            "schema_version": NOTICE_SCHEMA_VERSION,
            "kind": subject.kind,
            "notice_class": subject.notice_class,
            "notice_ref": subject.notice_ref,
            "case_ref": subject.case_ref,
            "business_revision": subject.business_revision,
            "authorized_content_ref": subject.authorized_content_ref,
            "tenant": grant.access.scope.tenant,
            "environment": grant.access.scope.environment,
            "recipient_identity_digests": [r.identity_digest for r in grant.intended_recipients],
            "authority_digest": grant.authority_digest,
        }
    )
    return VendorNoticeDraft(
        notice_ref=subject.notice_ref,
        case_ref=subject.case_ref,
        business_revision=subject.business_revision,
        audience="vendor",
        recipient_count=len(grant.intended_recipients),
        canonical_payload=canonical.decode("utf-8"),
        payload_sha256=fingerprint(subject_payload),
        idempotency_key=fingerprint(
            {
                "tenant": grant.access.scope.tenant,
                "operation": NOTICE_OPERATION,
                "domain_object_ref": subject.case_ref,
                "business_revision": subject.business_revision,
            }
        ),
    )
