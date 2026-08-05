"""`ConsentDecisionSource` — consent decision intake + point-read for the payer core (MZO-030).

**Two halves, one port.** Consent reaches the payer core two ways and both are needed:

1. As a STREAM of canonical consent events (same 28-field envelope as every other boundary
   event), so the core can keep a durable local projection and react to a revocation without
   polling. Ack/nack/quarantine semantics are identical to `maezo.ports.work_items` — a consent
   event that fails the pinned contract is quarantined under a closed reason token, never
   silently dropped.
2. As a POINT READ (`latest_decision`) for the moment a protected operation needs to know whether
   it may proceed for a given subject and purpose. Without this, every consumer would invent its
   own projection query and the consent gate would stop being one auditable seam.

**Scope semantics are NOT modelled here — deliberately.** The canonical consent record carries a
`scope` alongside `purpose`, and the mapping from a scope to what it authorises is owned by the
contract manifest's consent-scope-mapping authority, not by this repo. `ConsentDecision`
therefore exposes NO scope field and NO scope interpretation: it answers only the question a
payer-core consumer actually asks — "is there an authorising decision for this subject and this
purpose, which decision reference is it, and at which revision". Adding a scope vocabulary here
would be inventing contract semantics this repo does not own (ADR-0037 XRD-04) and pre-empting
the DPO/Legal gate that DL-0040 records as still OPEN for identity/consent values (MZO-020).

**`consent_decision_ref` is opaque.** It is the token a protected read hands back as its
authorising reference (see `maezo.ports.clinical_context` /
`maezo.ports.population_features`). This module defines no format, no prefix and no parsing for
it — see `maezo.ports.envelope` for the full DL-0040 rationale.

**`purpose_of_use` is an opaque `str`, not a local enum.** The purpose vocabulary is contract-
owner-published; a local StrEnum here would silently fork it on the first value the owner adds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from maezo.ports.envelope import CanonicalEnvelope
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailureReason, PortResult


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalConsentEvent(CanonicalEnvelope):
    """A canonical consent event: the 28 pinned envelope fields + an OPAQUE payload mapping.

    Same posture as `CanonicalWorkItem.payload` — the consent record's body (purpose, scope,
    revision, decision, timestamps) is contract-owner-shaped and is NOT re-declared here. The
    ADAPTER projects it into `ConsentDecision` for the point-read half of this port.
    """

    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsentDelivery:
    """One delivery from a `ConsentDecisionSource` stream: the event plus its settle handle."""

    delivery_ref: str
    consent_event: CanonicalConsentEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsentDecision:
    """The MINIMAL projection a payer-core consumer needs to gate a protected operation.

    - `consent_decision_ref` — opaque authorising reference; the caller carries it into the
      protected read it is about to perform, and into the audit record of that read.
    - `portable_subject_ref` / `purpose_of_use` — echo of what was asked, so a decision object
      cannot be applied to a different subject or purpose by accident.
    - `granted` — the only decision bit the core needs. A revoked decision and an expired
      decision are both `granted=False`; a subject with NO decision at all is `NOT_FOUND`, which
      is a materially different fail-closed outcome and therefore a refusal, not a value.
    - `consent_revision` — the contract's monotonic revision. Present because out-of-order
      delivery is real (XRD-10 mandates a business-revision guard on the consuming side); a
      consumer holding revision N must be able to reject a late revision N-1.
    - `decided_at` — when the decision was taken, for the audit trail of the gated operation.

    NO scope, NO expiry policy, NO derived "is_valid_now()" — see the module docstring: those are
    contract-owner / DPO-gated semantics, not payer-core inventions.
    """

    consent_decision_ref: str
    portable_subject_ref: str
    purpose_of_use: str
    granted: bool
    consent_revision: int
    decided_at: datetime


@runtime_checkable
class ConsentDecisionSource(Protocol):
    """Consent intake + point-read seam. Structural (`typing.Protocol`), never an ABC."""

    def stream(self) -> AsyncIterator[ConsentDelivery]:
        """Yield consent deliveries. Same rationale as `WorkItemSource.stream` (no deadline on a
        long-lived stream; deadlines live on the request/response calls)."""
        ...

    async def ack(
        self,
        delivery_ref: str,
        *,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        """Settle a consent delivery as absorbed. Idempotent per `delivery_ref`; must not report a
        success it did not durably observe (DL-0038)."""
        ...

    async def nack(
        self,
        delivery_ref: str,
        *,
        reason: PortFailureReason,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        """Route a consent delivery to quarantine under a closed, opaque, non-PHI reason token."""
        ...

    async def latest_decision(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ConsentDecision]:
        """Latest known consent decision for this subject and this purpose.

        `purpose_of_use` is REQUIRED and keyword-only: there is no "just tell me the consent"
        read, because a decision is only meaningful against the purpose it authorises.

        Fail-closed refusals: `NOT_FOUND` when no decision exists for the subject at all,
        `PURPOSE_DENIED` when decisions exist but none covers this purpose, `TIMEOUT` /
        `UPSTREAM_UNAVAILABLE` when the projection cannot be consulted. A refusal NEVER degrades
        into an optimistic "assume granted"; a `granted=False` decision is a successful read of a
        real decision and is materially different from a refusal.

        Note the asymmetry with the other protected reads in this package: this call takes NO
        `consent_decision_ref`, because it is the call that PRODUCES one.
        """
        ...
